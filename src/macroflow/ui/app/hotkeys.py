from __future__ import annotations

from macroflow.ui.dialogs.app_dialogs import HotkeyScriptsDialog


class HotkeysMixin:
    """Studio authoring behavior."""

    def _configure_hotkey_scripts(self):
        dialog = HotkeyScriptsDialog(self.root, list(self.hotkey_scripts))
        self.hotkey_config_open = True
        try:
            result = dialog.show()
        finally:
            self.hotkey_config_open = False
        if result is None:
            return
        self.hotkey_scripts = self._normalize_hotkey_scripts(result)
        self._apply_hotkey_bindings()
        self._refresh_hotkey_summary()
        self._persist_sidebar_settings()
        self._set_status(f"已保存 {len(self.hotkey_scripts)} 个快捷键脚本绑定", "success")
        self._log(
            "快捷键脚本已保存：录制或执行过程中按对应按键即可执行绑定的脚本"
            f"（共 {len(self.hotkey_scripts)} 个）。"
        )
