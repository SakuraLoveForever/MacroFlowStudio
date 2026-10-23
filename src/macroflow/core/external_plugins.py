"""Local plugins imported from disk and kept out of the repository.

A plugin is a single ``.py`` file in the local ``plugins`` directory:

    PLUGIN = {"name": "显示名称", "description": "一句话说明"}

    def main(ctx):
        ctx.log("hello")

The metadata is read with :mod:`ast` so a broken or untrusted plugin never runs
while the plugin page lists it; execution reuses the existing Python script
runner, which gives plugins ``ctx`` (press/click/log/run_module...).
"""
import ast
import shutil
from dataclasses import dataclass
from pathlib import Path

from .storage import BASE_DIR


PLUGIN_SUFFIX = ".py"
PLUGINS_DIR = BASE_DIR / "plugins"


@dataclass(frozen=True)
class ExternalPlugin:
    path: Path
    name: str
    description: str


def plugins_dir(folder=None):
    directory = Path(folder) if folder is not None else PLUGINS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _metadata(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"插件语法错误：第 {exc.lineno} 行 {exc.msg}") from None
    if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
        raise ValueError("插件需要定义 main(ctx) 入口。")
    name, description = path.stem, ast.get_docstring(tree) or ""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "PLUGIN" for target in node.targets):
            try:
                data = ast.literal_eval(node.value)
            except (SyntaxError, ValueError):
                raise ValueError("PLUGIN 元数据必须是字典字面量。") from None
            if not isinstance(data, dict):
                raise ValueError("PLUGIN 元数据必须是字典字面量。")
            name = str(data.get("name") or name).strip() or name
            description = str(data.get("description") or description)
    description = " ".join(description.split())
    return name, description


def discover_plugins(folder=None):
    """List usable plugins; invalid files are skipped instead of breaking the page."""
    plugins = []
    for path in sorted(plugins_dir(folder).glob(f"*{PLUGIN_SUFFIX}")):
        if not path.is_file():
            continue
        try:
            name, description = _metadata(path)
        except (OSError, ValueError):
            continue
        plugins.append(ExternalPlugin(path, name, description))
    return plugins


def import_plugin(source, folder=None):
    """Copy a plugin file into the local plugin directory and describe it."""
    source = Path(source).expanduser()
    directory = plugins_dir(folder)
    if not source.is_file() or source.suffix.lower() != PLUGIN_SUFFIX:
        raise ValueError("请选择 .py 插件文件。")
    name, description = _metadata(source)
    if source.resolve().parent == directory.resolve():
        return ExternalPlugin(source.resolve(), name, description)
    destination = directory / source.name
    index = 2
    while destination.exists():
        destination = directory / f"{source.stem} ({index}){PLUGIN_SUFFIX}"
        index += 1
    shutil.copy2(source, destination)
    return ExternalPlugin(destination, name, description)


def remove_plugin(plugin, folder=None):
    """Delete one plugin file; only files inside the local plugin directory."""
    directory = plugins_dir(folder).resolve()
    path = Path(plugin.path).resolve()
    if path.parent != directory:
        raise ValueError("只能移除本机插件目录中的插件。")
    path.unlink()