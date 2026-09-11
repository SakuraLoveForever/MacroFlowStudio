from __future__ import annotations

from typing import Any


RESOLUTION_STYLES_KEY = "resolution_styles"
SUPPORTED_SCALE_PERCENTS = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)
DEFAULT_RESOLUTION_STYLES = [
    {
        "name": "1920×1080", "width": 1920, "height": 1080,
        "refresh_rate": 0, "scale_percent": 100,
    },
    {
        "name": "1280×720", "width": 1280, "height": 720,
        "refresh_rate": 0, "scale_percent": 100,
    },
]


def normalize_resolution_style(raw: Any) -> dict[str, int | str]:
    if not isinstance(raw, dict):
        raise ValueError("分辨率样式必须是对象")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("分辨率样式名称不能为空")
    try:
        width = int(raw.get("width", 0))
        height = int(raw.get("height", 0))
        refresh_rate = int(raw.get("refresh_rate", 0) or 0)
        scale_percent = int(raw.get("scale_percent", 100) or 100)
    except (TypeError, ValueError) as exc:
        raise ValueError("分辨率必须是整数") from exc
    if not 320 <= width <= 16384 or not 200 <= height <= 16384:
        raise ValueError("分辨率超出有效范围")
    if refresh_rate < 0 or refresh_rate > 1000:
        raise ValueError("刷新率必须在 0 到 1000 之间")
    if scale_percent not in SUPPORTED_SCALE_PERCENTS:
        raise ValueError("缩放比例必须是 100%、125%、150%、175%、200%、225%、250%、300%、350%、400%、450% 或 500%")
    return {
        "name": name,
        "width": width,
        "height": height,
        "refresh_rate": refresh_rate,
        "scale_percent": scale_percent,
    }


def normalize_resolution_styles(raw: Any) -> list[dict[str, int | str]]:
    if not isinstance(raw, (list, tuple)):
        return []
    styles: list[dict[str, int | str]] = []
    names: set[str] = set()
    for item in raw:
        try:
            style = normalize_resolution_style(item)
        except ValueError:
            continue
        key = str(style["name"]).casefold()
        if key in names:
            continue
        names.add(key)
        styles.append(style)
    return styles


def resolution_styles_from_settings(settings: dict | None) -> list[dict[str, int | str]]:
    settings = settings if isinstance(settings, dict) else {}
    styles = normalize_resolution_styles(settings.get(RESOLUTION_STYLES_KEY))
    return styles or [dict(style) for style in DEFAULT_RESOLUTION_STYLES]


def resolve_resolution_style(settings: dict | None, name: str) -> dict[str, int | str]:
    requested = str(name or "").strip().casefold()
    for style in resolution_styles_from_settings(settings):
        if str(style["name"]).casefold() == requested:
            return dict(style)
    raise KeyError(f"找不到分辨率样式：{name}")


def build_resolution_action(settings: dict | None, name: str) -> dict[str, int | str]:
    """Build a self-contained action from one configured resolution style."""
    style = resolve_resolution_style(settings, name)
    return {
        "type": "set_resolution",
        "name": style["name"],
        "width": style["width"],
        "height": style["height"],
        "refresh_rate": style["refresh_rate"],
        "scale_percent": style["scale_percent"],
        "delay_ms": 0,
        "after_delay_ms": 0,
    }
