"""Persistent, shared process names for workflow plugins and modules."""
import json
import os
from pathlib import Path
import tempfile

from .storage import BASE_DIR

PROCESS_NAMES_PATH = BASE_DIR / "process_names.json"


def _names(values) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("进程名库格式无效：需要进程名列表。")
    names = {}
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("进程名必须是非空名称。")
        name = raw.strip()
        if not name or any(char in name for char in '<>:"/\\|?*') or any(ord(char) < 32 for char in name):
            raise ValueError("进程名无效，请从运行列表选择或使用鼠标抓取。")
        names.setdefault(name.casefold(), name)
    return sorted(names.values(), key=str.casefold)


def load_process_names() -> list[str]:
    try:
        text = PROCESS_NAMES_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return _names(json.loads(text))


def _save_process_names(names: list[str]) -> None:
    names = _names(names)
    handle, temporary = tempfile.mkstemp(dir=PROCESS_NAMES_PATH.parent, suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(names, output, ensure_ascii=False, indent=2)
        os.replace(temporary, PROCESS_NAMES_PATH)
    finally:
        Path(temporary).unlink(missing_ok=True)


def add_process_name(name: str) -> None:
    _save_process_names(load_process_names() + [name])


def remove_process_names(names: list[str]) -> None:
    removed = {name.casefold() for name in _names(names)}
    _save_process_names([name for name in load_process_names() if name.casefold() not in removed])


def process_name_choices(current: str = "") -> list[str]:
    names = load_process_names()
    return _names(names + ([current] if current else []))
