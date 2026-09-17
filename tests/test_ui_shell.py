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

    def test_close_action_is_saved_with_the_sidebar_settings(self):
        # 选择要落盘：下次打开软件仍按用户选的关闭行为走。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.close_action_var = FakeSettingVar("tray")
        app.interval_var = FakeSettingVar("100")
        app.repeat_var = FakeSettingVar("1")
        app.backup_interval_var = FakeSettingVar("1h")
        for name in (
            "sound_enabled_var", "mini_window_enabled_var", "execution_mini_enabled_var",
            "focus_mode_enabled_var", "activate_target_enabled_var",
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
        app.app_settings = {}

        settings = app._collect_sidebar_settings()

        self.assertEqual(settings["close_action"], "tray")

if __name__ == '__main__':
    unittest.main()
