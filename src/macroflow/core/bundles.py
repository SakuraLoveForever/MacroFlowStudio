"""Portable JSON/ZIP bundles. Parsing never executes imported instructions."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from . import storage

SCRIPT_FIELDS = {"script"}
IMAGE_FIELDS = {"template", "second_match_template", "fallback_template", "image_path"}
MODULE_FIELDS = {"module_key", "fallback_module_key"}
MAX_FILES = 10_000
MAX_BYTES = 512 * 1024 * 1024


def _walk(value, reference):
    if isinstance(value, dict):
        if value.get("type") == "python_script":
            return {key: {alias: reference("module_key", target) for alias, target in item.items()}
                    if key == "module_bindings" else reference("python_file", item) if key == "path" and item
                    else _walk(item, reference) for key, item in value.items()}
        return {key: reference(key, item) if key in SCRIPT_FIELDS | IMAGE_FIELDS | MODULE_FIELDS
                and item else _walk(item, reference) for key, item in value.items()}
    if isinstance(value, list):
        return [_walk(item, reference) for item in value]
    return value


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def export_bundle(destination: Path, *, script=None, workflow=None, script_path=None) -> Path:
    """Export the current object and its transitive dependencies atomically."""
    if (script is None) == (workflow is None):
        raise ValueError("必须选择一个脚本或工作流")
    destination = Path(destination)
    scripts, images, modules, entries = {}, {}, {}, {}
    python_folders = {}
    python_entries = set()
    reserved = set()
    objects = storage.load_module_objects()

    def prepare(value):
        if isinstance(value, list):
            return [prepare(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: prepare(item) for key, item in value.items()}
        if value.get("type") == "python_script":
            definition = storage.load_script(storage.resolve_path(value["path"])).actions[0]
            bindings = {alias: value.get("module_bindings", {}).get(alias, key)
                        for alias, key in definition.get("module_bindings", {}).items()}
            for alias, key in bindings.items():
                if key not in objects:
                    matches = [k for k, obj in objects.items() if obj.get("name") == key]
                    if len(matches) != 1:
                        raise ValueError(f"Python 声明模块不存在或名称不唯一：{key}")
                    bindings[alias] = matches[0]
            value.update(dependencies=definition.get("dependencies", {}), module_bindings=bindings)
        return value

    def module(key):
        if not isinstance(key, str) or key not in objects:
            raise ValueError(f"引用的模块不存在：{key}")
        if key in modules:
            return key
        modules[key] = {}
        modules[key] = _walk(prepare(copy.deepcopy(objects[key])), reference)
        return key

    def image(value):
        path = storage.resolve_path(value).resolve()
        if not path.is_file() or path.suffix.lower() not in storage.MODULE_IMAGE_EXTENSIONS:
            raise ValueError(f"引用的图片不存在或格式不支持：{value}")
        if path not in images:
            name = f"images/{len(images) + 1:04d}_{storage.safe_name(path.name, 'image.png')}"
            images[path] = name
            entries[name] = path.read_bytes()
            # Legacy image-keyed regions are dependencies too.
            for key in objects:
                if Path(key).suffix.lower() in storage.MODULE_IMAGE_EXTENSIONS \
                        and storage.resolve_path(key).resolve() == path:
                    module(key)
        return images[path]

    def script_file(value):
        path = storage.resolve_path(value).resolve()
        if not path.is_file():
            raise ValueError(f"引用的脚本不存在：{value}")
        if path not in scripts:
            name = f"scripts/{len(scripts) + 1:04d}_{storage.safe_name(path.stem, 'script')}.json"
            while name.casefold() in reserved:
                name = name[:-5] + "_.json"
            reserved.add(name.casefold())
            scripts[path] = name
            entries[name] = _json_bytes(_walk(prepare(storage.load_script(path).to_dict()), reference))
        return scripts[path]

    def reference(key, value):
        if not isinstance(value, str):
            raise ValueError(f"引用字段 {key} 必须是文本")
        if key == "python_file":
            path = storage.resolve_path(value).resolve()
            if not path.is_file() or path.suffix.lower() != ".py":
                raise ValueError(f"Python 代码文件不存在：{value}")
            if path.parent not in python_folders:
                folder = f"python/{len(python_folders) + 1:04d}"
                python_folders[path.parent] = folder
                for source in path.parent.glob("*.py"):
                    if not source.resolve().is_relative_to(path.parent):
                        raise ValueError(f"Python 辅助源码不能链接到目录外：{source}")
                    entries[f"{folder}/{source.name}"] = source.read_bytes()
            folder = python_folders[path.parent]
            if path not in python_entries:
                python_entries.add(path)
                definition = storage.load_script(path).actions[0]
                for raw in definition.get("dependencies", {}).get("files", []):
                    source = (path.parent / raw).resolve()
                    if not source.is_relative_to(path.parent) or not source.exists():
                        raise ValueError(f"Python 依赖文件必须存在于源码目录内：{raw}")
                    files = source.rglob("*") if source.is_dir() else [source]
                    for asset in files:
                        if not asset.is_file():
                            continue
                        if asset.resolve() == destination.resolve():
                            continue
                        if not asset.resolve().is_relative_to(path.parent):
                            raise ValueError(f"Python 依赖不能链接到源码目录外：{asset}")
                        data = asset.read_bytes()
                        if asset.suffix.lower() == ".json":
                            value = json.loads(data)
                            if isinstance(value, dict) and "actions" in value:
                                data = _json_bytes(_walk(prepare(value), reference))
                        entries[f"{folder}/{asset.relative_to(path.parent).as_posix()}"] = data
            return f"{python_folders[path.parent]}/{path.name}"
        if key in SCRIPT_FIELDS:
            return script_file(value)
        if key in IMAGE_FIELDS:
            return image(value)
        return module(value)

    kind = "script" if script is not None else "workflow"
    obj = script if script is not None else workflow
    entry = f"{'scripts' if script is not None else 'workflows'}/{storage.safe_name(obj.name, kind)}.json"
    reserved.add(entry.casefold())
    if script is not None and script_path is not None:
        scripts[Path(script_path).resolve()] = entry
    entries[entry] = _json_bytes(_walk(prepare(obj.to_dict()), reference))
    # Image-keyed modules must keep the exact image path as their registry key.
    exported_modules = {}
    for key, value in modules.items():
        image_path = storage.resolve_path(key).resolve()
        exported_modules[images.get(image_path, key)] = value
    entries["modules.json"] = _json_bytes(exported_modules)
    entries["manifest.json"] = _json_bytes({
        "format": "macroflow-bundle", "version": 1, "kind": kind, "entry": entry,
    })
    # References to legacy module keys also need to match the relocated registry.
    key_map = {key: images.get(storage.resolve_path(key).resolve(), key) for key in modules}
    entries["modules.json"] = _json_bytes(_walk(exported_modules, lambda k, v: key_map.get(v, v)))
    for name, data in list(entries.items()):
        if name.endswith(".json") and name not in {"manifest.json", "modules.json"}:
            entries[name] = _json_bytes(_walk(json.loads(data), lambda k, v: key_map.get(v, v)))
    if len(entries) > MAX_FILES or sum(len(data) for data in entries.values()) > MAX_BYTES:
        raise ValueError("导出包文件数量或总大小超过导入限制")
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".zip")
    os.close(handle)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(entries):
                archive.writestr(name, entries[name])
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def _safe_name(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("导入包包含非法路径")
    path = PurePosixPath(name)
    if path.is_absolute() or any(not part or part in {".", ".."} or part.endswith((".", " "))
                                 for part in name.split("/")):
        raise ValueError(f"导入包包含非法路径：{name}")
    return name


def import_bundle(source: Path) -> tuple[str, Path]:
    """Validate first, then install under imports/<id>, preserving existing data."""
    with zipfile.ZipFile(source) as archive:
        files = archive.infolist()
        if len(files) > MAX_FILES or sum(info.file_size for info in files) > MAX_BYTES:
            raise ValueError("导入包过大")
        names = [_safe_name(info.filename) for info in files]
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("导入包包含重名文件")
        for info in files:
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("导入包不允许符号链接")
        entries = {name: archive.read(name) for name in names}
    try:
        manifest = json.loads(entries["manifest.json"])
        modules = json.loads(entries["modules.json"])
    except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("不是有效的 MacroFlow 导入包") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != "macroflow-bundle" \
            or manifest.get("version") != 1 or manifest.get("kind") not in {"script", "workflow"}:
        raise ValueError("不支持的导入包格式或版本")
    kind = manifest["kind"]
    entry = _safe_name(manifest.get("entry"))
    folder = "scripts/" if kind == "script" else "workflows/"
    if entry not in entries or not entry.startswith(folder) or not entry.endswith(".json"):
        raise ValueError("导入包入口不存在或类型错误")
    if not isinstance(modules, dict) or any(not isinstance(v, dict) for v in modules.values()):
        raise ValueError("模块配置格式错误")
    prefix = f"imports/{uuid.uuid4().hex}"
    module_map = {key: f"{prefix}/{key}" if key.startswith("images/")
                  else f"module:{uuid.uuid4().hex}" for key in modules}

    def reference(key, value):
        if not isinstance(value, str):
            raise ValueError(f"引用字段 {key} 必须是文本")
        if key in MODULE_FIELDS:
            if value not in module_map:
                raise ValueError(f"导入包缺少引用的模块：{value}")
            return module_map[value]
        _safe_name(value)
        expected = "python/" if key == "python_file" else "scripts/" if key in SCRIPT_FIELDS else "images/"
        if value not in entries or not value.startswith(expected):
            raise ValueError(f"导入包缺少引用文件：{value}")
        return f"{prefix}/{value}"

    transformed = {}
    for name, data in entries.items():
        if name in {"manifest.json", "modules.json"}:
            continue
        if name.startswith(("scripts/", "workflows/")) and name.endswith(".json"):
            try:
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError("脚本或工作流必须是对象")
                steps_key = "actions" if name.startswith("scripts/") else "steps"
                if not isinstance(value.get(steps_key), list) or any(
                        not isinstance(item, dict) for item in value[steps_key]):
                    raise ValueError(f"{steps_key} 必须是列表")
                transformed[name] = _json_bytes(_walk(value, reference))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"JSON 文件损坏：{name}") from exc
        elif name.startswith("python/"):
            if name.endswith(".json"):
                value = json.loads(data)
                if isinstance(value, dict) and "actions" in value:
                    data = _json_bytes(_walk(value, reference))
            transformed[name] = data
        elif name.startswith("images/") and Path(name).suffix.lower() in storage.MODULE_IMAGE_EXTENSIONS:
            transformed[name] = data
        else:
            raise ValueError(f"导入包包含不支持的文件：{name}")
    relocated = {module_map[key]: _walk(value, reference) for key, value in modules.items()}
    existing = storage.load_module_objects()
    existing.update(relocated)
    root = storage.BASE_DIR / prefix
    # No writes have occurred before this point.
    root.mkdir(parents=True)
    try:
        for name, data in transformed.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        storage.save_module_objects(existing)
    except Exception:
        import shutil
        shutil.rmtree(root)
        raise
    return kind, root / entry
