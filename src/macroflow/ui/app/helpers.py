from __future__ import annotations

from macroflow.core.models import MacroScript, is_global_script
from macroflow.core.storage import display_path
from macroflow.ui.dialogs.actions import GameSetupNoteDialog
from macroflow.ui.dialogs.app_dialogs import ResolutionStylesDialog
import copy
from tkinter import simpledialog

from .base import px, script_category_key
from .constants import SEGMENT_BAR

class HelpersMixin:
    """Studio authoring behavior."""

    def _toggle_english_input(self):
        self.switch_english_input_enabled = bool(self.switch_english_input_var.get())
        self._settings_changed()

    def _sidebar_save_values(self):
        names = ('sound_enabled_var', 'mini_window_enabled_var', 'execution_mini_enabled_var',
                 'focus_mode_enabled_var', 'activate_target_enabled_var',
                 'partial_script_globals_var', 'partial_workflow_globals_var',
                 'floating_notice_position_var', 'close_action_var', 'repeat_var',
                 'activation_enabled_var', 'activation_interval_var')
        values = {name: getattr(self, name).get() for name in names}
        values['switch_english_input_enabled'] = bool(self.switch_english_input_enabled)
        for name in ('saved_window_signature', 'saved_activation_signature',
                     'execution_mini_position', 'resolution_styles', 'hotkey_scripts', '_game_setup_note'):
            values[name] = copy.deepcopy(getattr(self, name, None))
        return values

    def _workflow_save_values(self):
        return {**vars(self.workflow), 'name': self.workflow_name_var.get().strip() or '未命名工作流',
                'start_at': self.workflow_start_var.get().strip()}

    def _workflow_saved(self):
        state = self.__dict__.get('_workflow_save_state')
        if state is not None:
            state.saved()

    def _watch_settings_saves(self, owner):
        from macroflow.ui.save_state import SaveState
        self._sidebar_save_state = SaveState(owner, self.sidebar_save_button, self._sidebar_save_values)
        self._sidebar_save_state.watch(*(self.__dict__[name] for name in self._sidebar_save_values()
                                        if name in self.__dict__ and name.endswith('_var')))

    def _settings_saved(self):
        state = self.__dict__.get('_sidebar_save_state')
        if state is not None:
            state.saved()

    def _collect_sidebar_settings(self) -> dict:
        settings = super()._collect_sidebar_settings()
        settings["switch_english_input_enabled"] = bool(self.switch_english_input_enabled)
        settings["disconnect_monitor_enabled"] = bool(getattr(self, "disconnect_monitor_enabled", False))
        plugins = self.__dict__.get('_interval_plugins')
        if plugins is not None:
            settings['interval_condition_plugins'] = [copy.deepcopy(plugin.config) for plugin in list(plugins.values())]
        elif isinstance(getattr(self, 'app_settings', {}).get('interval_condition_plugins'), list):
            # 插件列表还没初始化时（例如启动早期保存窗口位置），保留磁盘上已有的插件，
            # 不能因为这次保存把插件整个丢掉。
            settings['interval_condition_plugins'] = copy.deepcopy(
                self.app_settings['interval_condition_plugins'])
        settings["editor_draft"] = self._editor_draft_snapshot()
        return settings

    @staticmethod
    def _apply_column_widths(tree, columns, stretch_column: str) -> None:
        for column, text, width, anchor in columns:
            tree.heading(column, text=text)
            tree.column(column, width=px(width), anchor=anchor,
                        stretch=column == stretch_column)

    @staticmethod
    def _selected_row_segment(tree) -> tuple[int, int] | None:
        """列表里选中的那一段（第一行到最后一行），不足两行不算片段。

        片段用两次点击定出来：点一下片段的第一行，再按住 Shift 点最后一行
        （Ctrl 点两头的行同样可以，取的是选中范围的第一行到最后一行）。
        """
        rows = sorted({int(item) for item in tree.selection()})
        if len(rows) < 2:
            return None
        return rows[0], rows[-1]

    def _refresh_row_segment_bar(self, tree, painted_attr: str) -> None:
        """把选中片段画成列表最左边那根实心竖条（只重画变化的行）。"""
        segment = self._selected_row_segment(tree)
        # 未经过完整窗口初始化的测试 fixture 直接按“还没画过”处理。
        painted = getattr(self, painted_attr, None)
        if painted == segment:
            return
        if painted is not None:
            for index in range(painted[0], painted[1] + 1):
                if segment is not None and segment[0] <= index <= segment[1]:
                    continue
                iid = str(index)
                if tree.exists(iid):
                    tree.set(iid, "mark", "")
        if segment is not None:
            for index in range(segment[0], segment[1] + 1):
                iid = str(index)
                if tree.exists(iid):
                    tree.set(iid, "mark", SEGMENT_BAR)
        setattr(self, painted_attr, segment)

    def _reset_row_segment_bar(self, painted_attr: str) -> None:
        """列表要重建：行都换成新的，竖条记号一并作废（重建后按选中重画）。"""
        setattr(self, painted_attr, None)

    def _ask_repeats(self, title: str, prompt: str) -> int | None:
        """问执行次数（默认 1 次、范围 1–999999）；取消返回 None。"""
        return simpledialog.askinteger(
            title, prompt, parent=self.root,
            initialvalue=1, minvalue=1, maxvalue=999999,
        )

    def _editor_draft_snapshot(self) -> dict | None:
        """Capture the complete current editor state without overwriting its file."""
        script = getattr(self, "script", None)
        if not isinstance(script, MacroScript):
            return None
        snapshot = copy.deepcopy(script)
        name_var = getattr(self, "script_name_var", None)
        if name_var is not None:
            name = str(name_var.get()).strip()
            if name:
                snapshot.name = name
        snapshot.settings = self._current_script_settings()
        category_var = getattr(self, "script_category_var", None)
        category_label = category_var.get() if category_var is not None else "关卡"
        snapshot.settings["category"] = script_category_key(category_label)
        snapshot.is_global = is_global_script(snapshot.to_dict())
        return {
            "script": snapshot.to_dict(),
            "script_path": display_path(self.script_path) if self.script_path else "",
            "script_requires_new_file": bool(getattr(self, "script_requires_new_file", False)),
            "dirty": bool(getattr(self, "dirty", False)),
        }

    def save_sidebar_config(self):
        state = self.__dict__.get('_sidebar_save_state')
        if state is not None:
            state.refresh()
            if state.state == 'disabled':
                return True
        self._save_activation_interval()
        if not self._persist_sidebar_settings(show_feedback=True):
            return False
        self._settings_saved()
        return True

    def open_game_setup_note(self):
        """打开「游戏设置说明」：查看/编辑使用本软件前游戏需要设置的参数。"""
        saved = getattr(self, "_game_setup_note", None)
        note = GameSetupNoteDialog(self.root, saved if isinstance(saved, str) else None).show()
        if note is None:
            return
        self._game_setup_note = note
        if self._persist_sidebar_settings(show_feedback=True):
            self._set_status("游戏设置说明已保存", "success")
            self._log("游戏设置说明已保存。")
        else:
            self._set_status("游戏设置说明保存失败", "danger")

    def _configure_resolution_styles(self, parent=None) -> list[dict] | None:
        result = ResolutionStylesDialog(
            parent or self.root, {"resolution_styles": self.resolution_styles},
        ).show()
        if result is None:
            return None
        self.resolution_styles = result
        self._refresh_resolution_styles_summary()
        self._sync_workflow_resolution_ui()
        if self._persist_sidebar_settings(show_feedback=True):
            self._set_status("分辨率样式已保存", "success")
            self._log(f"已保存 {len(result)} 个分辨率样式。")
        return self.resolution_styles

