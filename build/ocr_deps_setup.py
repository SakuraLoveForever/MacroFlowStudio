# -*- coding: utf-8 -*-
"""Build the small, self-contained RapidOCR directory beside the executable."""
from __future__ import annotations

import csv
import shutil
import struct
import sys
import marshal
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / ".deps"
OCR_OUT = ROOT / "dist" / "rapidocr_ocr"
OCR_REQUIREMENTS = ROOT / "requirements-ocr.txt"
MAIN_REQUIREMENTS = ROOT / "requirements.txt"
EXE_PATH = ROOT / "dist" / "MacroFlowStudio.exe"
GENERATED_MARKER = ".macroflow-rapidocr-generated"
OCR_ROOTS = {"rapidocr", "onnxruntime"}
EXE_SHARED_MODULES = {"numpy", "cv2", "pil", "six"}
SHARED_DISTRIBUTION_PROVIDERS = {"opencv-python": "opencv-python-headless"}
NEVER = {"pip", "pyinstaller", "networkx", "hf-xet", "hf_xet"}
IN_EXE_FILES = {"cv2", "ttkbootstrap", "ttkcreator", "pynput", "pystray", "mss"}


def normalize(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


@dataclass
class Distribution:
    name: str
    version: str
    info_path: Path
    requirements: list[str]
    files: list[str]
    top_levels: set[str]


def _read_requirements(path: Path) -> dict[str, str]:
    from packaging.requirements import Requirement

    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue
        requirement = Requirement(raw)
        versions = [item.version for item in requirement.specifier if item.operator == "=="]
        if versions:
            pins[normalize(requirement.name)] = versions[0]
    return pins


def _distributions() -> dict[str, list[Distribution]]:
    results: dict[str, list[Distribution]] = {}
    for info_path in DEPS.glob("*.dist-info"):
        metadata_path = info_path / "METADATA"
        record_path = info_path / "RECORD"
        if not metadata_path.is_file() or not record_path.is_file():
            continue
        metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8", errors="replace"))
        name, version = metadata.get("Name"), metadata.get("Version")
        if not name or not version:
            continue
        files = []
        with record_path.open("r", encoding="utf-8", newline="") as record:
            for row in csv.reader(record):
                if row:
                    files.append(row[0])
        top_file = info_path / "top_level.txt"
        if top_file.is_file():
            top_levels = {
                line.strip() for line in top_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and line.strip() not in {".", ".."}
                and not line.strip().endswith(".libs")
            }
        else:
            top_levels = set()
            for entry in files:
                parts = PurePosixPath(entry.replace("\\", "/")).parts
                if not parts or parts[0].endswith((".dist-info", ".egg-info", ".data")):
                    continue
                root = parts[0]
                if root.endswith(".py"):
                    root = root[:-3]
                elif "." in root and root.lower().endswith((".pyd", ".so")):
                    root = root.split(".", 1)[0]
                if root not in {".", ".."} and not root.endswith(".libs"):
                    top_levels.add(root)
        dist = Distribution(
            name=name,
            version=version,
            info_path=info_path,
            requirements=metadata.get_all("Requires-Dist", []),
            files=files,
            top_levels=top_levels,
        )
        results.setdefault(normalize(name), []).append(dist)
    return results


def _select_distribution(
    name: str, all_dists: dict[str, list[Distribution]], pins: dict[str, str],
) -> Distribution:
    key = normalize(name)
    candidates = all_dists.get(key, [])
    if not candidates:
        raise RuntimeError(f"OCR 运行依赖未安装到 .deps：{name}")
    pinned = pins.get(key)
    if pinned:
        for dist in candidates:
            if dist.version == pinned:
                return dist
        raise RuntimeError(f"OCR 依赖版本错误：{name} 需要 {pinned}，已安装 {', '.join(d.version for d in candidates)}")
    from packaging.version import Version
    return max(candidates, key=lambda item: Version(item.version))


def _exe_modules(exe_path: Path) -> set[str]:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(exe_path))
    pyz_data = archive.extract("PYZ.pyz")
    if isinstance(pyz_data, tuple):
        pyz_data = pyz_data[0]
    toc_offset = struct.unpack("!i", pyz_data[8:12])[0]
    modules = set(dict(marshal.loads(pyz_data[toc_offset:])).keys())
    return {name.split(".", 1)[0].lower() for name in modules} | {
        name.lower() for name in IN_EXE_FILES
    }


def _resolve_closure(
    all_dists: dict[str, list[Distribution]], pins: dict[str, str], exe_modules: set[str],
) -> dict[str, Distribution]:
    from packaging.requirements import Requirement
    from packaging.version import Version

    closure: dict[str, Distribution] = {}
    queue = [name for name in OCR_ROOTS]
    while queue:
        requested = queue.pop()
        key = normalize(requested)
        if key in closure:
            continue
        dist = _select_distribution(requested, all_dists, pins)
        closure[key] = dist
        for raw in dist.requirements:
            requirement = Requirement(raw)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            dep_name = normalize(requirement.name)
            provider = SHARED_DISTRIBUTION_PROVIDERS.get(dep_name, requirement.name)
            provider_dist = _select_distribution(provider, all_dists, pins)
            if requirement.specifier and not requirement.specifier.contains(
                    Version(provider_dist.version), prereleases=True):
                raise RuntimeError(
                    f"{dist.name} requires {requirement}, but {provider}=={provider_dist.version} is installed"
                )
            if dep_name in SHARED_DISTRIBUTION_PROVIDERS:
                if "cv2" not in exe_modules:
                    raise RuntimeError("RapidOCR 需要 OpenCV，但 cv2 未由主程序提供")
                continue
            if dep_name in {"numpy", "pillow", "six"}:
                roots = {root.lower() for root in provider_dist.top_levels}
                if not roots or not roots.issubset(exe_modules | EXE_SHARED_MODULES):
                    raise RuntimeError(
                        f"主程序未提供 OCR 共用依赖 {requirement.name} 的模块：{sorted(roots)}"
                    )
                continue
            queue.append(requirement.name)
    return closure


def _safe_member_path(relative: str) -> Path | None:
    member = PurePosixPath(relative.replace("\\", "/"))
    if member.is_absolute() or ".." in member.parts:
        return None
    return DEPS.joinpath(*member.parts)


def _copy_distribution(dist: Distribution, exe_modules: set[str]) -> set[str]:
    copied = 0
    copied_roots = set()
    for relative in dist.files:
        source = _safe_member_path(relative)
        if source is None or not source.is_file():
            continue
        parts = PurePosixPath(relative.replace("\\", "/")).parts
        if not parts or parts[0].endswith((".dist-info", ".egg-info", ".data")):
            continue
        root = parts[0]
        if root.endswith(".py"):
            root = root[:-3]
        elif "." in root and root.lower().endswith((".pyd", ".so")):
            root = root.split(".", 1)[0]
        if root.lower() in exe_modules:
            continue
        if root.endswith((".pyi", ".lib")) or "__pycache__" in parts:
            continue
        destination = OCR_OUT.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
        copied_roots.add(root.lower())
    if not copied:
        roots = {root.lower() for root in dist.top_levels}
        if not roots or not roots.issubset(exe_modules):
            raise RuntimeError(f"未能从 .deps 复制依赖包文件：{dist.name}=={dist.version}")
    if dist.info_path.is_dir():
        destination = OCR_OUT / dist.info_path.name
        shutil.copytree(dist.info_path, destination, dirs_exist_ok=True)
    for root in dist.top_levels:
        libs_source = DEPS / f"{root}.libs"
        if libs_source.is_dir():
            shutil.copytree(
                libs_source, OCR_OUT / libs_source.name, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyi", "*.lib"),
            )
            copied_roots.add(root.lower())
    return copied_roots


def _tree_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> None:
    if not EXE_PATH.is_file():
        raise RuntimeError(f"构建产物缺失：{EXE_PATH}")
    if not DEPS.is_dir():
        raise RuntimeError(f"源码依赖目录缺失：{DEPS}")
    exe_modules = _exe_modules(EXE_PATH)
    all_dists = _distributions()
    pins = _read_requirements(MAIN_REQUIREMENTS)
    pins.update(_read_requirements(OCR_REQUIREMENTS))
    closure = _resolve_closure(all_dists, pins, exe_modules)

    if OCR_OUT.exists():
        marker = OCR_OUT / GENERATED_MARKER
        if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != "MacroFlow Studio RapidOCR build output":
            raise RuntimeError(f"拒绝清理非本构建脚本创建的 OCR 目录：{OCR_OUT}")
        shutil.rmtree(OCR_OUT)
    OCR_OUT.mkdir(parents=True)
    (OCR_OUT / GENERATED_MARKER).write_text(
        "MacroFlow Studio RapidOCR build output\n", encoding="utf-8",
    )

    copy_names = []
    external_modules = set()
    for key, dist in sorted(closure.items()):
        if key in {"numpy", "pillow", "six", "opencv-python", "opencv-python-headless"}:
            continue
        roots = {root.lower() for root in dist.top_levels}
        if roots and roots.issubset(exe_modules):
            continue
        external_modules.update(_copy_distribution(dist, exe_modules))
        copy_names.append(f"{dist.name}=={dist.version}")

    expected_models = {
        "PP-OCRv6_det_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "PP-OCRv6_rec_small.onnx",
    }
    model_root = OCR_OUT / "rapidocr" / "models"
    missing = sorted(name for name in expected_models if not (model_root / name).is_file())
    if missing:
        raise RuntimeError(f"RapidOCR wheel 缺少本地模型：{', '.join(missing)}")
    rapidocr_license = ROOT / "licenses" / "RapidOCR-LICENSE.txt"
    if not rapidocr_license.is_file():
        raise RuntimeError(f"缺少 RapidOCR 许可证文本：{rapidocr_license}")
    shutil.copy2(rapidocr_license, OCR_OUT / "RapidOCR-LICENSE.txt")
    (OCR_OUT / "THIRD_PARTY_DEPENDENCIES.txt").write_text(
        "\n".join(copy_names) + "\n", encoding="utf-8",
    )
    (OCR_OUT / "OCR_EXTERNAL_MODULES.txt").write_text(
        "\n".join(sorted(external_modules)) + "\n", encoding="utf-8",
    )
    print("RapidOCR 外置依赖：", ", ".join(copy_names))
    print(f"dist/rapidocr_ocr 大小：{_tree_size(OCR_OUT) / (1024 * 1024):.1f} MiB")


if __name__ == "__main__":
    sys.path.insert(0, str(DEPS))
    main()
