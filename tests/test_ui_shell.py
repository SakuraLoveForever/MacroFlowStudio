"""主界面壳层：下拉滚轮、启动可见性、提示音、关闭行为。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_ui_shell.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, call, patch
from macroflow.core.alerts import play_alert
from macroflow.core.models import Workflow
from macroflow.ui.app.base import disable_combobox_wheel_selection
from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.app.startup import spawn_new_instance
from macroflow.ui.dialogs.base import DurationVar
from tests.helpers.core import FakeSettingVar
from tests.helpers.patches import package_patch


class ComboboxWheelTests(unittest.TestCase):
    def test_duration_var_switches_units_without_changing_milliseconds(self):
        master = tk.Tcl()
        value = DurationVar(1500, master=master)
        self.assertEqual(value.get(), "1500")
        value.unit.set("s")
        self.assertEqual(value._raw(), "1.5")
        self.assertEqual(value.get(), "1500")
        value.set("2.25")
        self.assertEqual(value.get(), "2250")
        value.unit.set("ms")
        self.assertEqual(value._raw(), "2250")

    def test_duration_var_switches_minutes_without_changing_milliseconds(self):
        master = tk.Tcl()
        value = DurationVar(90000, master=master)
        value.unit.set("min")
        self.assertEqual(value._raw(), "1.5")
        self.assertEqual(value.get(), "90000")
        value.set("2")
        self.assertEqual(value.get(), "120000")
        value.unit.set("s")
        self.assertEqual(value._raw(), "120")
        value.unit.set("ms")
        self.assertEqual(value._raw(), "120000")

    def test_all_combobox_wheel_sequences_are_blocked(self):
        root = Mock()
        disable_combobox_wheel_selection(root)
        self.assertEqual(
            [call.args[:2] for call in root.bind_class.call_args_list],
            [
                ("TCombobox", "<MouseWheel>"),
                ("TCombobox", "<Button-4>"),
                ("TCombobox", "<Button-5>"),
            ],
        )
        for call in root.bind_class.call_args_list:
            self.assertEqual(call.args[2](Mock()), "break")


class GameSetupNoteTests(unittest.TestCase):
    def test_open_game_setup_note_saves_custom_content(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app._game_setup_note = None
        app._persist_sidebar_settings = Mock(return_value=True)
        app._set_status = Mock()
        app._log = Mock()
        dialog = Mock()
        dialog.show.return_value = "custom game setup note"

        with package_patch('app', 'GameSetupNoteDialog', return_value=dialog):
            app.open_game_setup_note()

        self.assertEqual(app._game_setup_note, "custom game setup note")
        app._persist_sidebar_settings.assert_called_once_with(show_feedback=True)
        app._set_status.assert_called_once_with("游戏设置说明已保存", "success")

    def test_open_game_setup_note_reports_save_failure(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app._game_setup_note = None
        app._persist_sidebar_settings = Mock(return_value=False)
        app._set_status = Mock()
        app._log = Mock()
        dialog = Mock()
        dialog.show.return_value = "custom game setup note"

        with package_patch('app', 'GameSetupNoteDialog', return_value=dialog):
            app.open_game_setup_note()

        self.assertEqual(app._game_setup_note, "custom game setup note")
        app._persist_sidebar_settings.assert_called_once_with(show_feedback=True)
        app._set_status.assert_called_once_with("游戏设置说明保存失败", "danger")


class StartupVisibilityTests(unittest.TestCase):
    def test_new_execution_mini_has_compact_status_rows_and_fixed_pixel_fonts(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.mini_window = None
        app._hide_operation_mini = Mock()
        app._execution_mini_position = Mock(return_value=(100, 200))
        app._bind_operation_mini_drag = Mock()
        app._update_operation_mini = Mock()
        app.mini_context_var = Mock()
        app.mini_elapsed_var = Mock()
        app.mini_count_var = Mock()
        app.mini_window_var = Mock()
        app.mini_event_var = Mock()
        with patch('macroflow.ui.app.guards.tk.Toplevel'), \
             patch('macroflow.ui.app.guards.ttk.Frame'), \
             patch('macroflow.ui.app.guards.ttk.Label'), \
             patch('macroflow.ui.app.guards.tk.Label') as labels, \
             patch('macroflow.ui.app.guards.tk.Text') as events, \
             patch('macroflow.ui.app.guards.make_window_no_activate'), \
             patch('macroflow.ui.app.guards.set_dark_titlebar'), \
             patch('macroflow.ui.app.guards.set_rounded_window'):
            app._show_operation_mini('execution')
        events.assert_not_called()
        self.assertEqual(len(labels.call_args_list), 3)
        self.assertEqual(labels.call_args_list[0].kwargs['height'], 1)
        self.assertEqual(labels.call_args_list[1].kwargs['textvariable'], app.mini_window_var)
        self.assertEqual(labels.call_args_list[2].kwargs['textvariable'], app.mini_event_var)
        self.assertTrue(all(call.kwargs['font'][1] < 0 for call in labels.call_args_list))

    def test_mini_recovers_size_mapping_and_topmost_after_display_change(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_window = Mock()
        app.mini_window.winfo_id.return_value = 123
        app.mini_window.winfo_ismapped.return_value = False
        app._execution_mini_position = Mock(return_value=(100, 200))
        with patch('macroflow.ui.app.guards.get_window_rect', return_value=(100, 200, 840, 176)), \
             patch('macroflow.ui.app.guards.make_window_no_activate') as topmost, \
             patch('macroflow.ui.app.guards.show_window_no_activate') as show, \
             patch('macroflow.ui.app.guards.set_rounded_window'):
            app._reposition_operation_mini()
        app.mini_window.geometry.assert_called_once_with('420x100+100+200')
        topmost.assert_called_once_with(123)
        show.assert_called_once_with(123)

    def test_compact_progress_keeps_workflow_name_and_repeat_fraction(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_mode = 'execution'
        app.mini_count_var = Mock()
        app._set_execution_progress('工作流 2/8 · 每日任务\n共执行 10 次 · 当前第 3/10 次 · F12 停止')
        self.assertEqual(app.mini_count_var.set.call_args.args[0],
                         '工作流 2/8 | 每日任务 | 3/10 次')

    def test_display_rebuild_keeps_current_tab_and_log_messages(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        old_frame = Mock()
        app.root_frame = old_frame
        app.notebook = Mock()
        app.notebook.select.return_value = "old-workflow-tab"
        app.notebook.index.return_value = 1
        app._active_log_view = Mock(return_value="trace")
        app._event_view_buffer = ["event\n"]
        app._trace_view_buffer = ["trace\n"]
        app._flush_log_view = Mock()
        old_action_tree, new_action_tree = Mock(), Mock()
        old_action_tree.selection.return_value = ("2",)
        old_action_tree.yview.return_value = (0.4, 0.7)
        app.action_tree = old_action_tree
        def build_ui():
            app._event_view_buffer = []
            app._trace_view_buffer = []
            app.action_tree = new_action_tree
        app._build_ui = Mock(side_effect=build_ui)
        app.rebuild_action_tree = Mock()
        app.rebuild_workflow_tree = Mock()
        app._sync_activation_ui_from_script = Mock()
        app._refresh_hotkey_summary = Mock()
        app.refresh_script_files = Mock()
        app.refresh_workflow_files = Mock()
        app._show_log_view = Mock()

        app._rebuild_ui_for_display_change()

        old_frame.destroy.assert_called_once_with()
        app._build_ui.assert_called_once_with()
        self.assertEqual(app._event_view_buffer, ["event\n"])
        self.assertEqual(app._trace_view_buffer, ["trace\n"])
        app.notebook.select.assert_called_with(1)
        app._show_log_view.assert_called_once_with("trace")
        new_action_tree.selection_set.assert_called_once_with(("2",))
        new_action_tree.yview_moveto.assert_called_once_with(0.4)

    def test_position_preview_drags_from_its_body_and_saves_on_release(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.execution_mini_position_editor = None
        app.execution_mini_position = []
        app._execution_mini_position = Mock(return_value=(100, 200))
        app._persist_sidebar_settings = Mock()
        preview, body, label = Mock(), Mock(), Mock()
        preview.winfo_id.return_value = 123
        preview.winfo_x.return_value = 120
        preview.winfo_y.return_value = 230
        app._app_window_hwnd = Mock(return_value=123)
        area = {'left': -100, 'top': 20, 'width': 1000, 'height': 800}
        rect = [120, 230, 420, 100]
        def move_preview(_hwnd, x, y):
            rect[:2] = [x, y]
            return True
        with patch("macroflow.ui.app.guards.get_monitor_work_area_for_window", return_value=area), \
             patch("macroflow.ui.app.guards.tk.Toplevel", return_value=preview), \
             patch("macroflow.ui.app.guards.ttk.Frame", return_value=body), \
             patch("macroflow.ui.app.guards.ttk.Label", return_value=label), \
             patch("macroflow.ui.app.guards.get_window_rect", side_effect=lambda _hwnd: tuple(rect)), \
             patch("macroflow.ui.app.guards.move_window_no_activate", side_effect=move_preview) as move:
            app._adjust_execution_mini_position()
            callbacks = {entry.args[0]: entry.args[1] for entry in body.bind.call_args_list}
            callbacks["<ButtonPress-1>"](Mock(x_root=300, y_root=400))
            callbacks["<B1-Motion>"](Mock(x_root=320, y_root=430))
            callbacks["<B1-Motion>"](Mock(x_root=350, y_root=460))
            callbacks["<B1-Motion>"](Mock(x_root=-500, y_root=-500))
            callbacks["<B1-Motion>"](Mock(x_root=2000, y_root=2000))
            callbacks["<ButtonRelease-1>"](Mock())
        self.assertEqual(move.call_args_list, [
            call(123, 140, 260), call(123, 170, 290),
            call(123, -100, 20), call(123, 480, 720),
        ])
        self.assertEqual(app.execution_mini_position, [480, 720])
        app._persist_sidebar_settings.assert_called_once_with()
        preview.destroy.assert_called_once_with()

    def test_running_mini_can_be_dragged_and_remembers_position(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_window = Mock()
        app.mini_window.winfo_id.return_value = 123
        app.execution_mini_position = []
        app._persist_sidebar_settings = Mock()
        app._app_window_hwnd = Mock(return_value=123)
        area = {'left': -100, 'top': 20, 'width': 1000, 'height': 800}
        drag_surface = Mock()
        with patch("macroflow.ui.app.guards.get_monitor_work_area_for_window", return_value=area), \
             patch("macroflow.ui.app.guards.get_window_rect", return_value=(100, 200, 420, 100)), \
             patch("macroflow.ui.app.guards.move_window_no_activate") as move:
            app._bind_operation_mini_drag(drag_surface)
            callbacks = {entry.args[0]: entry.args[1] for entry in drag_surface.bind.call_args_list}
            callbacks["<ButtonPress-1>"](Mock(x_root=110, y_root=210))
            callbacks["<B1-Motion>"](Mock(x_root=130, y_root=240))
            callbacks["<B1-Motion>"](Mock(x_root=-500, y_root=-500))
            self.assertEqual(app.execution_mini_position, [-100, 20])
            callbacks["<B1-Motion>"](Mock(x_root=2000, y_root=2000))
            self.assertEqual(app.execution_mini_position, [480, 720])
            callbacks["<ButtonRelease-1>"](Mock())
        self.assertEqual(move.call_args_list, [
            call(123, 120, 230), call(123, -100, 20), call(123, 480, 720),
        ])
        self.assertEqual(app.execution_mini_position, [480, 720])
        app._persist_sidebar_settings.assert_called_once_with()

    def test_operation_mini_keeps_reference_footprint_after_dpi_change(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        with patch("macroflow.ui.app.guards.px", side_effect=lambda value: value * 2):
            self.assertEqual(app._operation_mini_size(), (420, 100))

    def test_operation_mini_defaults_to_bottom_right_without_margin(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.winfo_id.return_value = 123
        app.execution_mini_position = []
        area = {'left': 0, 'top': 0, 'width': 1000, 'height': 800}
        with package_patch('app', 'get_monitor_work_area_for_window', return_value=area), \
             package_patch('app', 'is_window', return_value=True), \
             patch("macroflow.ui.app.guards.px", side_effect=lambda value: value * 2):
            self.assertEqual(app._execution_mini_position(), (580, 700))

    def test_operation_mini_moves_back_to_corner_after_resolution_change(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_window = Mock()
        app.mini_window.winfo_id.return_value = 123
        app._execution_mini_position = Mock(return_value=(556, 640))
        with patch("macroflow.ui.app.guards.get_window_rect", return_value=(900, 700, 420, 100)), \
             patch("macroflow.ui.app.guards.move_window_no_activate") as move, \
             patch("macroflow.ui.app.guards.make_window_no_activate"):
            app._reposition_operation_mini()
        move.assert_called_once_with(123, 556, 640)

    def test_display_change_resets_mini_to_bottom_right(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_window = Mock()
        app._reposition_operation_mini = Mock()
        app._persist_sidebar_settings = Mock()
        for saved in ([], [24, 72], [2116, 1228]):
            for area in (
                {'left': -1920, 'top': 20, 'width': 1920, 'height': 1040},
                {'left': 0, 'top': 0, 'width': 2560, 'height': 1400},
                {'left': 100, 'top': 20, 'width': 300, 'height': 80},
            ):
                with self.subTest(saved=saved, area=area):
                    app.execution_mini_position = saved
                    app._adapt_execution_mini_position(area)
                    self.assertEqual(app.execution_mini_position, [
                        area['left'] + max(0, area['width'] - 420),
                        area['top'] + max(0, area['height'] - 100),
                    ])
        self.assertEqual(app._reposition_operation_mini.call_count, 9)

    def test_dpi_change_repositions_mini_even_when_fitted_dpi_is_unchanged(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.winfo_id.return_value = 123
        area = {'left': 0, 'top': 0, 'width': 1920, 'height': 1040}
        app._display_work_area = area
        app._display_dpi = 96
        app._ui_scaling_dpi = Mock(return_value=96)
        app._apply_display_dpi = Mock()
        app._adapt_execution_mini_position = Mock()
        with patch('macroflow.ui.app.helpers.get_window_dpi', return_value=144), \
             patch('macroflow.ui.app.helpers.get_monitor_work_area_for_window', return_value=area):
            app._watch_display_dpi()
            app._watch_display_dpi()
        app._adapt_execution_mini_position.assert_called_once_with(area)
        app._apply_display_dpi.assert_not_called()

    def test_execution_mini_position_is_clamped_to_the_app_monitor(self):
        # 多屏下必须按"软件所在显示器"的可用区域收敛，不能用虚拟桌面尺寸，
        # 否则小窗会被推到屏幕外面。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.winfo_id.return_value = 123
        app.execution_mini_position = [900, 700]
        with package_patch('app', 'get_monitor_work_area_for_window', return_value={'left': -1920, 'top': 0, 'width': 1000, 'height': 800}), package_patch('app', 'is_window', return_value=True):
            self.assertEqual(app._execution_mini_position(420, 316), (-1340, 484))

    def test_spawn_new_instance_resets_pyinstaller_extraction_environment(self):
        inherited = {
            "_MEIPASS": "C:/old-mei",
            "_MEIPASS2": "C:/old-mei2",
            "PATH": "C:/Windows",
        }
        with patch.dict("os.environ", inherited, clear=True), \
             patch("subprocess.Popen") as popen, \
             patch("sys.frozen", True, create=True):
            spawn_new_instance(["MacroFlowStudio.exe", "--open-script", "x.json"])
        env = popen.call_args.kwargs["env"]
        self.assertNotIn("_MEIPASS", env)
        self.assertNotIn("_MEIPASS2", env)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(env["PATH"], "C:/Windows")

    def test_startup_explicitly_shows_and_activates_main_window(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.winfo_id.return_value = 123
        app.exiting = False
        app.main_hidden_to_tray = False
        app.main_hidden_for_recording = False
        app.main_hidden_for_execution = False
        app.main_hidden_for_cursor_tracking = False
        with package_patch('app', 'show_window', return_value=True) as show, \
             package_patch('app', 'activate_window', return_value=True) as activate:
            app._ensure_startup_visible()
        app.root.deiconify.assert_called_once()
        app.root.state.assert_called_once_with("normal")
        show.assert_called_once_with(123)
        activate.assert_called_once_with(123)


class AlertTests(unittest.TestCase):
    def test_alert_uses_windows_audio_device(self):
        called = threading.Event()

        def capture_audio(data, _flags):
            self.assertTrue(data.startswith(b"RIFF"))
            called.set()

        with patch("macroflow.core.alerts.winsound.PlaySound", side_effect=capture_audio):
            play_alert("record_start")
            self.assertTrue(called.wait(1.0))


class CloseActionTests(unittest.TestCase):
    """关闭按钮行为：直接退出＝不留后台；隐藏到托盘＝按设置保留。"""

    def _app(self, action: str) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.close_action_var = FakeSettingVar(action)
        app.main_hidden_to_tray = False
        app._persist_workflow_draft = Mock()
        app._quit_app = Mock()
        app._hide_main_to_tray = Mock(return_value=True)
        return app

    def test_exit_action_closes_the_whole_process(self):
        app = self._app("exit")
        app.on_close()
        app._hide_main_to_tray.assert_not_called()
        app._quit_app.assert_called_once_with()

    def test_tray_action_hides_the_window_instead(self):
        app = self._app("tray")
        app.on_close()
        app._hide_main_to_tray.assert_called_once_with()
        app._quit_app.assert_not_called()

    def test_tray_action_falls_back_to_exit_without_a_tray_icon(self):
        # 托盘图标建不出来时不能留下“既无窗口又无图标”的隐藏进程。
        app = self._app("tray")
        app._hide_main_to_tray = Mock(return_value=False)
        app.on_close()
        app._quit_app.assert_called_once_with()

    def test_close_action_is_saved_without_obsolete_proxy_setting(self):
        # 选择要落盘：下次打开软件仍按用户选的关闭行为走。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.close_action_var = FakeSettingVar("tray")
        app.interval_var = FakeSettingVar("100")
        app.repeat_var = FakeSettingVar("1")
        app.backup_interval_var = FakeSettingVar("1h")
        for name in (
            "sound_enabled_var", "mini_window_enabled_var", "execution_mini_enabled_var",
            "focus_mode_enabled_var", "activate_target_enabled_var",
            "partial_script_globals_var", "partial_workflow_globals_var",
            "timed_backup_enabled_var", "windows_startup_enabled_var",
            "start_minimized_to_tray_var", "startup_run_workflow_var",
            "activation_enabled_var",
        ):
            setattr(app, name, FakeSettingVar(False))
        for name, value in (
            ("playback_speed_var", 1.0), ("script_category_var", "关卡"),
            ("floating_notice_position_var", "顶部居中"),
            ("startup_workflow_path_var", ""), ("level_scripts_dir_var", "scripts/关卡"),
            ("level_pack_scripts_dir_var", "scripts/关卡封装"),
            ("switch_scripts_dir_var", "scripts/切换"),
            ("direction_scripts_dir_var", "scripts/方向"),
            ("workflow_name_var", "未命名工作流"), ("workflow_start_var", ""),
        ):
            setattr(app, name, FakeSettingVar(value))
        app.script = None
        app.script_path = None
        app.workflow_path = None
        app.saved_window_signature = None
        app.workflow = Workflow()
        app.app_settings = {"disable_flclash_proxy_before_workflow": True}

        settings = app._collect_sidebar_settings()

        self.assertEqual(settings["close_action"], "tray")
        self.assertNotIn("disable_flclash_proxy_before_workflow", settings)

if __name__ == '__main__':
    unittest.main()
