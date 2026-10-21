from __future__ import annotations

from macroflow.core.models import DEFAULT_RECORDED_SCREEN
from macroflow.core.storage import BASE_DIR, DIRECTION_SCRIPTS_DIR
from pathlib import Path


class RuntimeScriptsMixin:
    """Shared scripts behavior used by both clients."""

    def _current_script_settings(self, recorded_screen: dict | None = None) -> dict:
        settings = dict(self.script.settings)
        configured = settings.get("activation_window_configured")
        if configured is None:
            configured = bool(self.activation_enabled_var.get() or self.saved_activation_signature)
        settings.update({
            "record_mode": "auto",
            "activation_window_enabled": bool(self.activation_enabled_var.get()),
            "activation_window": (
                dict(self.saved_activation_signature) if self.saved_activation_signature else None
            ),
            "activation_window_configured": bool(configured),
        })
        if recorded_screen:
            settings["recorded_screen"] = dict(recorded_screen)
        else:
            settings.setdefault("recorded_screen", dict(DEFAULT_RECORDED_SCREEN))
        return settings

    def _resolve_scripts_dir(self, var_name: str, default: str) -> Path:
        var = getattr(self, var_name, None)
        value = var.get().strip() if var is not None else default
        path = Path(value)
        return path if path.is_absolute() else BASE_DIR / path

    def _level_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("level_scripts_dir_var", "scripts/关卡")

    def _level_pack_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("level_pack_scripts_dir_var", "scripts/关卡封装")

    def _switch_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("switch_scripts_dir_var", "scripts/切换")

    def _direction_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("direction_scripts_dir_var", DIRECTION_SCRIPTS_DIR)

    def _script_category_dir(self) -> Path:
        var = getattr(self, "script_category_var", None)
        label = var.get() if var is not None else "关卡"
        if label == "关卡封装":
            return self._level_pack_scripts_dir()
        if label == "切换":
            return self._switch_scripts_dir()
        if label == "方向":
            return self._direction_scripts_dir()
        return self._level_scripts_dir()

