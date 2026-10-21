"""快捷键循环切换及被动提示的后台测试。"""
import threading
import unittest
from unittest.mock import Mock, patch
from tests.helpers.core import add_src_to_path
from macroflow.ui.app.runtime.hotkeys import RuntimeHotkeysMixin
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from tests.helpers.patches import package_patch


class HotkeyToggleTests(unittest.TestCase):
    def app(self):
        app = RuntimeHotkeysMixin()
        app.exiting = app.hotkey_config_open = app._hotkey_script_running = False
        app._hotkey_script_lock = threading.Lock()
        app._hotkey_cancel = threading.Event()
        app._hotkey_active_binding = None
        app.hotkey_player = Mock()
        app._ui = Mock()
        app._log = Mock()
        app._show_execution_notice = Mock()
        return app

    def test_same_key_stops_active_script_without_starting_another_thread(self):
        app = self.app()
        binding = {"key": "M", "vk": 77, "script": "example.json"}
        with patch('macroflow.ui.app.runtime.hotkeys.threading.Thread') as thread:
            app._trigger_hotkey_script(binding)
            app._trigger_hotkey_script(binding)
        thread.assert_called_once()
        self.assertTrue(app._hotkey_cancel.is_set())
        app.hotkey_player.stop.assert_called_once()

    def test_different_key_does_not_stop_active_script(self):
        app = self.app()
        with patch('macroflow.ui.app.runtime.hotkeys.threading.Thread'):
            app._trigger_hotkey_script({"key": "M", "vk": 77})
            app._trigger_hotkey_script({"key": "N", "vk": 78})
        app.hotkey_player.stop.assert_not_called()

    def test_emergency_stop_cancels_hotkey_before_ui_queue_runs(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._cancel_workflow_recovery = Mock()
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.hotkey_player = Mock()
        app._hotkey_cancel = threading.Event()
        app._ui = Mock()
        app._emergency_stop_from_hook()
        self.assertTrue(app._hotkey_cancel.is_set())
        app.hotkey_player.stop.assert_called_once()

    def test_injected_release_does_not_allow_held_hotkey_to_toggle(self):
        from macroflow.ui.app.runtime.hotkeys import keyboard
        app = self.app()
        app.input_guard = Mock(active=False)
        app._hotkey_pressed = set()
        app._hotkey_vk_map = {77: {"key": "M", "vk": 77}}
        app._trigger_hotkey_script = Mock()
        with patch('macroflow.ui.app.runtime.hotkeys.keyboard.Listener') as listener:
            app._start_hotkeys()
        press = listener.call_args.kwargs["on_press"]
        release = listener.call_args.kwargs["on_release"]
        key = keyboard.KeyCode.from_vk(77)
        press(key)
        release(key, injected=True)
        press(key)
        app._trigger_hotkey_script.assert_called_once()
        release(key)
        press(key)
        self.assertEqual(app._trigger_hotkey_script.call_count, 2)

    def test_start_notice_uses_actual_cycle_and_bottom_left_passive_overlay(self):
        app = self.app()
        app._hotkey_active_binding = {"key": "M", "vk": 77}
        app._hotkey_sequence_state(True, 20)
        args, kwargs = app._ui.call_args
        self.assertIn("M", args[1])
        self.assertIn("开始", args[1])
        self.assertIn("20", args[1])
        self.assertEqual(kwargs, {})
        app._show_key_sequence_notice(args[1])
        self.assertEqual(app._show_execution_notice.call_args.kwargs, {"position": "左下", "passive": True})

    def test_all_sequence_actions_get_start_and_end_notices(self):
        app = self.app()
        for running, word in ((True, "开始"), (False, "结束")):
            app._key_sequence_state(running, 12.5)
            self.assertIn(word, app._ui.call_args.args[1])
            self.assertIn("12.5", app._ui.call_args.args[1])

    def test_countdown_uses_persistent_compact_notice(self):
        app = self.app()
        app._hotkey_active_binding = {"key": "M", "vk": 77}
        app._hotkey_sequence_countdown(19)
        self.assertIn("M", app._ui.call_args.args[1])
        self.assertIn("倒计时 19 秒", app._ui.call_args.args[1])
        self.assertEqual(app._ui.call_args.args[2], 0)

    def test_countdown_tracks_cycle_and_excludes_pause_time(self):
        values = []
        player = MacroPlayer(on_key_sequence_countdown=values.append)
        player._key_sequence_clock = (10.0, 0.0, 20.0)
        with patch('macroflow.execution.player.core.time.perf_counter', return_value=10.0):
            player.wait_while_paused()
            player.wait_while_paused()
        with patch('macroflow.execution.player.core.time.perf_counter', return_value=11.1):
            player.wait_while_paused()
        player._total_pause_seconds = 30
        with patch('macroflow.execution.player.core.time.perf_counter', return_value=41.1):
            player.wait_while_paused()
        self.assertEqual(values, [20, 19])

    def test_countdown_resets_on_next_cycle_and_is_cleared_on_stop(self):
        values = []
        now = [0.0]
        player = MacroPlayer(on_key_sequence_countdown=values.append)
        def wait(ms):
            now[0] += ms / 1000
            player.wait_while_paused()
            if now[0] > 20:
                raise PlaybackStopped()
        player._wait = wait
        with patch('macroflow.execution.player.core.time.perf_counter', side_effect=lambda: now[0]), \
             package_patch('player', 'send_key'):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "keys": "a", "cycle_interval_enabled": True}, None)
        self.assertEqual(values, [20, 0, 20])
        self.assertIsNone(player._key_sequence_clock)

    def test_persistent_countdown_cancels_old_notice_timer(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.state.return_value = "normal"
        app.main_hidden_for_execution = False
        app._app_window_hwnd = Mock(return_value=123)
        app.execution_notice_window = Mock()
        app.execution_notice_label = Mock()
        app.execution_notice_label.winfo_reqwidth.return_value = 220
        app.execution_notice_label.winfo_reqheight.return_value = 36
        app.execution_notice_after_id = "old-timer"
        area = {"left": 0, "top": 0, "width": 1920, "height": 1040}
        with patch('macroflow.ui.app.execution.get_monitor_work_area_for_window', return_value=area), \
             patch('macroflow.ui.app.execution.set_rounded_window'), \
             patch('macroflow.ui.app.execution.make_window_no_activate'):
            app._show_execution_notice("M：倒计时 19 秒", 0, position="左下", passive=True)
        app.execution_notice_window.after_cancel.assert_called_once_with("old-timer")
        app.execution_notice_window.after.assert_not_called()
        self.assertIsNone(app.execution_notice_after_id)

    def test_cancel_survives_player_reset_before_start(self):
        app = self.app()
        app._hotkey_cancel.set()
        with self.assertRaises(PlaybackStopped):
            app._hotkey_playback_heartbeat()
        player = MacroPlayer(on_heartbeat=app._hotkey_playback_heartbeat)
        with package_patch('player', 'send_key') as send:
            player.play([{"type": "key_sequence", "keys": "ab"}])
        send.assert_not_called()

    def test_passive_notice_is_semitransparent_and_forces_bottom_left(self):
        from macroflow.ui.app.main import MacroFlowApp
        from macroflow.ui.app.base import floating_notice_xy
        from macroflow.ui.app.constants import FLOATING_NOTICE_WIDTH, FLOATING_NOTICE_HEIGHT
        from macroflow.ui.dialogs.base import px
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.state.return_value = "normal"
        app.main_hidden_for_execution = False
        app._app_window_hwnd = Mock(return_value=123)
        app.execution_notice_window = Mock()
        app.execution_notice_label = Mock()
        app.execution_notice_label.winfo_reqwidth.return_value = 220
        app.execution_notice_label.winfo_reqheight.return_value = 36
        app.execution_notice_after_id = None
        area = {"left": 0, "top": 0, "width": 1920, "height": 1040}
        with patch('macroflow.ui.app.execution.get_monitor_work_area_for_window', return_value=area), \
             patch('macroflow.ui.app.execution.set_rounded_window') as rounded, \
             patch('macroflow.ui.app.execution.make_window_no_activate') as style:
            app._show_execution_notice("开始 20 秒间隔", 4500, position="左下", passive=True)
            notice = app.execution_notice_window
            self.assertEqual(style.call_args.kwargs, {"click_through": False})
            notice.after_idle.assert_called_once()
            notice.after_idle.call_args.args[0]()
            self.assertEqual(style.call_args.kwargs, {"click_through": True})
        notice = app.execution_notice_window
        notice.attributes.assert_called_with("-alpha", 0.95)
        width, height = 220, 36
        x, y = floating_notice_xy("左下", 1920, 1040, width, height)
        notice.geometry.assert_called_once_with(f"{width}x{height}+{x}+{y}")
        rounded.assert_called_once_with(notice.winfo_id.return_value, px(6))

    def test_notice_uses_explicit_large_white_text_independent_of_ttk_theme(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.state.return_value = "normal"
        app.main_hidden_for_execution = False
        app._app_window_hwnd = Mock(return_value=123)
        app.execution_notice_window = None
        app.execution_notice_after_id = None
        area = {"left": 0, "top": 0, "width": 1920, "height": 1040}
        with patch('macroflow.ui.app.execution.get_monitor_work_area_for_window', return_value=area), \
             patch('macroflow.ui.app.execution.make_window_no_activate'), \
             patch('macroflow.ui.app.execution.set_rounded_window'), \
             patch('macroflow.ui.app.execution.tk.Toplevel'), \
             patch('macroflow.ui.app.execution.tk.Label') as label:
            label.return_value.winfo_reqwidth.return_value = 220
            label.return_value.winfo_reqheight.return_value = 36
            app._show_execution_notice("快捷键 M：开始 20 秒间隔循环", 4500, position="左下", passive=True)
        self.assertEqual(label.call_args.kwargs["foreground"], "#FFFFFF")
        self.assertEqual(label.call_args.kwargs["font"][1:], (12, "bold"))
        self.assertEqual(label.call_args.kwargs["text"], "快捷键 M：开始 20 秒间隔循环")
        label.return_value.pack.assert_called_once_with(fill="both", expand=True)

    def test_sequence_start_callback_runs_before_first_key(self):
        events = []
        player = MacroPlayer(on_key_sequence_state=lambda running, seconds: events.append((running, seconds)))
        player._wait = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key', side_effect=lambda vk, down: events.append((vk, down))):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "keys": "a", "cycle_interval_enabled": True}, None)
        self.assertEqual(events, [(True, 20), (65, True), (65, False), (False, 20)])

    def test_click_through_style_uses_wrapper_and_preserves_no_activate(self):
        from macroflow.input import wininput
        with patch.object(wininput, 'user32') as api:
            api.GetAncestor.return_value = 456
            api.GetWindowLongW.return_value = 0
            wininput.make_window_no_activate(123, click_through=True)
            style = api.SetWindowLongW.call_args.args[2]
            self.assertTrue(style & wininput.WS_EX_TRANSPARENT)
            self.assertTrue(style & wininput.WS_EX_NOACTIVATE)
            self.assertEqual(api.GetAncestor.call_args.args[1], wininput.GA_ROOT)
