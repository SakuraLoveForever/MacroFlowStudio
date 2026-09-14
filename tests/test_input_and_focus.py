"""输入注入、按键捕获、强制专注模式、输入锁重启。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_input_and_focus.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.common import *  # noqa: E402,F401,F403


class WinInputTests(unittest.TestCase):
    def test_set_dark_titlebar_requests_rounded_window_corners(self):
        with patch("macroflow.input.wininput.user32.GetAncestor", return_value=123), \
             patch("macroflow.input.wininput.dwmapi.DwmSetWindowAttribute", return_value=0) as set_attr:
            self.assertTrue(set_dark_titlebar(456))

        corner_calls = [
            item for item in set_attr.call_args_list
            if item.args[1] == DWMWA_WINDOW_CORNER_PREFERENCE
        ]
        self.assertEqual(len(corner_calls), 1)
        self.assertEqual(corner_calls[0].args[2]._obj.value, 2)

    def test_resolve_window_signature_matches_exact_signature(self):
        # 标题 + 类名 + 进程路径全匹配时命中对应窗口。
        windows = [
            WindowInfo(10, "大厅", "Launcher", "C:/Game/launcher.exe"),
            WindowInfo(20, "游戏", "GameWindow", "C:/Game/game.exe"),
        ]
        with patch("macroflow.input.wininput.enum_windows", return_value=windows):
            info = resolve_window_signature({
                "title": "游戏", "class_name": "GameWindow",
                "process_path": "C:/Game/game.exe",
            })
        self.assertIsNotNone(info)
        self.assertEqual(info.hwnd, 20)

    def test_resolve_window_signature_falls_back_to_class_and_process(self):
        # 标题带会话状态（"游戏 - 副本 1"）时退化为 类名+进程路径 匹配。
        windows = [
            WindowInfo(10, "游戏 - 副本 1", "GameWindow", "C:/Game/game.exe"),
        ]
        with patch("macroflow.input.wininput.enum_windows", return_value=windows):
            info = resolve_window_signature({
                "title": "游戏", "class_name": "GameWindow",
                "process_path": "C:/Game/game.exe",
            })
        self.assertIsNotNone(info)
        self.assertEqual(info.hwnd, 10)

    def test_resolve_window_signature_missing_window_returns_none(self):
        windows = [WindowInfo(10, "大厅", "Launcher", "C:/Game/launcher.exe")]
        with patch("macroflow.input.wininput.enum_windows", return_value=windows):
            self.assertIsNone(resolve_window_signature({"title": "游戏"}))
            self.assertIsNone(resolve_window_signature({}))
            self.assertIsNone(resolve_window_signature(None))

    def test_show_window_uses_show_without_resizing_normal_window(self):
        with patch("macroflow.input.wininput.is_window", return_value=True), \
             patch("macroflow.input.wininput.user32.IsIconic", return_value=False), \
             patch("macroflow.input.wininput.user32.ShowWindow") as show, \
             patch("macroflow.input.wininput.user32.IsWindowVisible", return_value=True):
            self.assertTrue(show_window(123))
        self.assertEqual(show.call_args.args[1], 5)

    def test_show_window_no_activate_preserves_geometry_and_focus(self):
        with patch("macroflow.input.wininput.is_window", return_value=True), \
             patch("macroflow.input.wininput.user32.ShowWindow") as show, \
             patch("macroflow.input.wininput.user32.SetWindowPos", return_value=True) as position:
            self.assertTrue(show_window_no_activate(123))
        self.assertEqual(show.call_args.args[1], 4)
        flags = position.call_args.args[-1]
        self.assertTrue(flags & 0x0001)  # SWP_NOSIZE
        self.assertTrue(flags & 0x0002)  # SWP_NOMOVE
        self.assertTrue(flags & 0x0004)  # SWP_NOZORDER
        self.assertTrue(flags & 0x0010)  # SWP_NOACTIVATE

    def test_mouse_input_falls_back_when_sendinput_returns_zero_without_error(self):
        with patch("macroflow.input.wininput.user32.SendInput", return_value=0), \
             patch("macroflow.input.wininput.user32.mouse_event") as fallback, \
             patch("macroflow.input.wininput.time.sleep"):
            send_move_relative(12, -7)
        fallback.assert_called_once()
        self.assertEqual(fallback.call_args.args[-1], MACROFLOW_INPUT_TAG)

    def test_sendinput_uses_focus_guard_dispatcher_when_installed(self):
        dispatcher = Mock()
        set_input_dispatcher(dispatcher)
        try:
            with patch("macroflow.input.wininput.user32.SendInput") as send_input:
                send_move_relative(12, -7)
        finally:
            set_input_dispatcher(None)
        dispatcher.assert_called_once()
        packet = dispatcher.call_args.args[0]
        self.assertEqual((packet.mi.dx, packet.mi.dy), (12, -7))
        self.assertEqual(packet.mi.dwExtraInfo, MACROFLOW_INPUT_TAG)
        send_input.assert_not_called()

    def test_activate_window_does_not_restore_non_minimized_window(self):
        with patch("macroflow.input.wininput.is_window", return_value=True), \
             patch("macroflow.input.wininput.user32.IsIconic", return_value=False), \
             patch("macroflow.input.wininput.user32.ShowWindow") as show, \
             patch("macroflow.input.wininput.kernel32.GetCurrentThreadId", return_value=1), \
             patch("macroflow.input.wininput.user32.GetWindowThreadProcessId", return_value=1), \
             patch("macroflow.input.wininput.user32.BringWindowToTop"), \
             patch("macroflow.input.wininput.user32.SetForegroundWindow"), \
             patch("macroflow.input.wininput.user32.SetFocus"), \
             patch("macroflow.input.wininput.user32.GetForegroundWindow", return_value=123), \
             patch("macroflow.input.wininput.time.sleep"):
            self.assertTrue(activate_window(123))
        show.assert_not_called()

    def test_activate_window_restores_only_minimized_window(self):
        with patch("macroflow.input.wininput.is_window", return_value=True), \
             patch("macroflow.input.wininput.user32.IsIconic", return_value=True), \
             patch("macroflow.input.wininput.user32.ShowWindow") as show, \
             patch("macroflow.input.wininput.kernel32.GetCurrentThreadId", return_value=1), \
             patch("macroflow.input.wininput.user32.GetWindowThreadProcessId", return_value=1), \
             patch("macroflow.input.wininput.user32.BringWindowToTop"), \
             patch("macroflow.input.wininput.user32.SetForegroundWindow"), \
             patch("macroflow.input.wininput.user32.SetFocus"), \
             patch("macroflow.input.wininput.user32.GetForegroundWindow", return_value=123), \
             patch("macroflow.input.wininput.time.sleep"):
            self.assertTrue(activate_window(123))
        show.assert_called_once()

    def test_activate_window_skips_set_focus_when_already_foreground(self):
        # 窗口已在前台：不再 SetFocus，避免从窗口内的子渲染表面
        # （Flash/CEF 画布）夺走键盘焦点，游戏不会弹出“点击游戏画面继续操作”。
        with patch("macroflow.input.wininput.is_window", return_value=True), \
             patch("macroflow.input.wininput.user32.IsIconic", return_value=False), \
             patch("macroflow.input.wininput.kernel32.GetCurrentThreadId", return_value=1), \
             patch("macroflow.input.wininput.user32.GetWindowThreadProcessId", return_value=1), \
             patch("macroflow.input.wininput.user32.BringWindowToTop"), \
             patch("macroflow.input.wininput.user32.SetForegroundWindow"), \
             patch("macroflow.input.wininput.user32.SetFocus") as set_focus, \
             patch("macroflow.input.wininput.user32.GetForegroundWindow", return_value=123), \
             patch("macroflow.input.wininput.time.sleep"):
            self.assertTrue(activate_window(123))
        set_focus.assert_not_called()

    def test_activate_window_set_focus_only_when_activation_failed(self):
        # SetForegroundWindow 未把窗口带到前台（前台是别的窗口）：才补 SetFocus。
        with patch("macroflow.input.wininput.is_window", return_value=True), \
             patch("macroflow.input.wininput.user32.IsIconic", return_value=False), \
             patch("macroflow.input.wininput.kernel32.GetCurrentThreadId", return_value=1), \
             patch("macroflow.input.wininput.user32.GetWindowThreadProcessId", return_value=1), \
             patch("macroflow.input.wininput.user32.BringWindowToTop"), \
             patch("macroflow.input.wininput.user32.SetForegroundWindow"), \
             patch("macroflow.input.wininput.user32.SetFocus") as set_focus, \
             patch("macroflow.input.wininput.user32.GetForegroundWindow", return_value=999), \
             patch("macroflow.input.wininput.time.sleep"):
            self.assertFalse(activate_window(123))
        set_focus.assert_called_once()
        self.assertEqual(set_focus.call_args.args[0].value, 123)

    def test_force_english_input_changes_layout_and_closes_ime(self):
        layout = 0x04090409
        with patch("macroflow.input.wininput.user32.LoadKeyboardLayoutW", return_value=layout) as load, \
             patch("macroflow.input.wininput.user32.ActivateKeyboardLayout") as activate, \
             patch("macroflow.input.wininput.user32.PostMessageW", return_value=True) as post, \
             patch("macroflow.input.wininput.user32.GetWindowThreadProcessId", return_value=77), \
             patch("macroflow.input.wininput.user32.GetKeyboardLayout",
                   side_effect=[0x08040804, layout]), \
             patch("macroflow.input.wininput.imm32.ImmGetContext", return_value=88), \
             patch("macroflow.input.wininput.imm32.ImmSetOpenStatus") as close_ime, \
             patch("macroflow.input.wininput.imm32.ImmReleaseContext"), \
             patch("macroflow.input.wininput.time.sleep"):
            self.assertTrue(force_english_input(123))
        load.assert_called_once_with("00000409", 1)
        activate.assert_called_once_with(layout, 0)
        post.assert_called_once()
        close_ime.assert_called_once()

    def test_force_english_input_skips_switch_when_already_english(self):
        with patch("macroflow.input.wininput.user32.LoadKeyboardLayoutW") as load, \
             patch("macroflow.input.wininput.user32.GetWindowThreadProcessId", return_value=77), \
             patch("macroflow.input.wininput.user32.GetKeyboardLayout", return_value=0x04090409):
            self.assertTrue(force_english_input(123))
        load.assert_not_called()

    def test_center_lock_uses_window_position_plus_size(self):
        with patch("macroflow.input.wininput.user32.GetForegroundWindow", return_value=0), \
             patch("macroflow.input.wininput.get_window_rect", return_value=(100, 50, 800, 600)), \
             patch("macroflow.input.wininput.get_cursor_pos", return_value=(500, 350)):
            self.assertTrue(is_cursor_near_window_center(123))


class KeyCaptureTests(unittest.TestCase):
    """KeyCapturer hook + KeyActionDialog capture flow."""

    def test_mouse_capturer_is_available(self):
        self.assertTrue(hasattr(input_guard_module, "MouseCapturer"))

    def test_input_capturer_registers_keyboard_and_mouse_hooks(self):
        events, release, captured = [], threading.Event(), {}

        def fake_set_hook(kind, proc, _inst, _tid):
            captured[kind] = proc
            return ctypes.c_void_p(kind)

        def fake_get_message(*_):
            release.wait(3)
            return 0

        patches = [
            patch("macroflow.input.input_guard.user32.SetWindowsHookExW", side_effect=fake_set_hook),
            patch("macroflow.input.input_guard.user32.GetMessageW", side_effect=fake_get_message),
            patch("macroflow.input.input_guard.user32.PostThreadMessageW"),
            patch("macroflow.input.input_guard.user32.CallNextHookEx", return_value=0),
            patch("macroflow.input.input_guard.user32.UnhookWindowsHookEx"),
        ]
        for patch_ in patches:
            patch_.start()
        capturer = InputCapturer(on_input=lambda kind, value: events.append((kind, value)))
        self.assertTrue(capturer.start(timeout=1.0))
        try:
            self.assertEqual(set(captured), {WH_KEYBOARD_LL, WH_MOUSE_LL})
            data = MSLLHOOKSTRUCT()
            result = captured[WH_MOUSE_LL](0, WM_RBUTTONDOWN, ctypes.addressof(data))
        finally:
            release.set()
            capturer.stop()
            for patch_ in patches:
                patch_.stop()

        self.assertEqual(result, 1)
        self.assertEqual(events, [("mouse", "right")])

    def test_input_capturer_reports_keyboard_input_from_same_capture(self):
        events, release, captured = [], threading.Event(), {}

        def fake_set_hook(kind, proc, _inst, _tid):
            captured[kind] = proc
            return ctypes.c_void_p(kind)

        def fake_get_message(*_):
            release.wait(3)
            return 0

        patches = [
            patch("macroflow.input.input_guard.user32.SetWindowsHookExW", side_effect=fake_set_hook),
            patch("macroflow.input.input_guard.user32.GetMessageW", side_effect=fake_get_message),
            patch("macroflow.input.input_guard.user32.PostThreadMessageW"),
            patch("macroflow.input.input_guard.user32.CallNextHookEx", return_value=0),
            patch("macroflow.input.input_guard.user32.UnhookWindowsHookEx"),
        ]
        for patch_ in patches:
            patch_.start()
        capturer = InputCapturer(on_input=lambda kind, value: events.append((kind, value)))
        self.assertTrue(capturer.start(timeout=1.0))
        try:
            data = KBDLLHOOKSTRUCT()
            data.vkCode = 0x41
            result = captured[WH_KEYBOARD_LL](0, WM_KEYDOWN, ctypes.addressof(data))
        finally:
            release.set()
            capturer.stop()
            for patch_ in patches:
                patch_.stop()

        self.assertEqual(result, 1)
        self.assertEqual(events, [("key", 0x41)])

    def test_search_input_capture_sets_mouse_name_and_searches(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.after.side_effect = lambda _delay, callback, *args: callback(*args)
        app.key_search_var = Mock()
        app.key_search_match_var = Mock()
        app.input_search_capture_button = Mock()
        app._input_search_capturer = None
        app._search_key_actions = Mock()

        with patch_app("InputCapturer") as capturer_class:
            capturer_class.return_value.start.return_value = True
            app.start_input_search_capture()
            on_input = capturer_class.call_args.args[0]
            on_input("mouse", "right")

        app.key_search_var.set.assert_called_once_with("右键")
        app._search_key_actions.assert_called_once_with(1)
        app.input_search_capture_button.configure.assert_any_call(state="disabled")
        app.input_search_capture_button.configure.assert_any_call(state="normal")
        capturer_class.return_value.stop.assert_called_once()

    def test_search_input_capture_sets_key_name_and_searches(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.after.side_effect = lambda _delay, callback, *args: callback(*args)
        app.key_search_var = Mock()
        app.key_search_match_var = Mock()
        app.input_search_capture_button = Mock()
        app._input_search_capturer = None
        app._search_key_actions = Mock()

        with patch_app("InputCapturer") as capturer_class:
            capturer_class.return_value.start.return_value = True
            app.start_input_search_capture()
            on_input = capturer_class.call_args.args[0]
            on_input("key", 0x41)

        app.key_search_var.set.assert_called_once_with("A")
        app._search_key_actions.assert_called_once_with(1)
        app.input_search_capture_button.configure.assert_any_call(state="disabled")
        app.input_search_capture_button.configure.assert_any_call(state="normal")
        capturer_class.return_value.stop.assert_called_once()

    def test_mouse_capturer_captures_next_left_button_down(self):
        events, release, captured = [], threading.Event(), {}

        def fake_set_hook(kind, proc, _inst, _tid):
            captured[kind] = proc
            return ctypes.c_void_p(kind)

        def fake_get_message(*_):
            release.wait(3)
            return 0

        patches = [
            patch("macroflow.input.input_guard.user32.SetWindowsHookExW", side_effect=fake_set_hook),
            patch("macroflow.input.input_guard.user32.GetMessageW", side_effect=fake_get_message),
            patch("macroflow.input.input_guard.user32.PostThreadMessageW"),
            patch("macroflow.input.input_guard.user32.CallNextHookEx", return_value=0),
            patch("macroflow.input.input_guard.user32.UnhookWindowsHookEx"),
        ]
        for patch_ in patches:
            patch_.start()
        capturer = MouseCapturer(on_button=events.append)
        self.assertTrue(capturer.start(timeout=1.0))
        try:
            data = MSLLHOOKSTRUCT()
            result = captured[WH_MOUSE_LL](0, WM_LBUTTONDOWN, ctypes.addressof(data))
        finally:
            release.set()
            capturer.stop()
            for patch_ in patches:
                patch_.stop()

        self.assertEqual(result, 1)
        self.assertEqual(events, ["left"])

    def test_search_input_capture_cancel_stops_without_search(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.after.side_effect = lambda _delay, callback, *args: callback(*args)
        app.key_search_var = Mock()
        app.key_search_match_var = Mock()
        app.input_search_capture_button = Mock()
        app._input_search_capturer = None
        app._search_key_actions = Mock()

        with patch_app("InputCapturer") as capturer_class:
            capturer_class.return_value.start.return_value = True
            app.start_input_search_capture()
            on_cancel = capturer_class.call_args.args[1]
            on_cancel()

        app._search_key_actions.assert_not_called()
        app.key_search_match_var.set.assert_called_with("已取消键鼠检测")
        capturer_class.return_value.stop.assert_called_once()

    def test_key_dialog_can_open_for_new_action_without_existing_action(self):
        widgets = ("Frame", "Label", "Entry", "Button", "Combobox")
        with patch("macroflow.ui.dialogs.ModalDialog.__init__", return_value=None), \
             patch("macroflow.ui.dialogs.tk.StringVar", side_effect=lambda **_: Mock()), \
             patch_dialogs("duration_var", return_value=Mock()), \
             patch.multiple("macroflow.ui.dialogs.ttk", **{
                 name: Mock(return_value=Mock()) for name in widgets
             }):
            dialog = KeyActionDialog(object())

        self.assertIsNone(dialog.capturer)

    def _install_capturer(self, events, release):
        captured = {}

        def fake_set_hook(_kind, proc, _inst, _tid):
            captured["proc"] = proc
            return ctypes.c_void_p(1)

        def fake_get_message(*_):
            release.wait(3)
            return 0

        patch_set = patch("macroflow.input.input_guard.user32.SetWindowsHookExW", side_effect=fake_set_hook)
        patch_get = patch("macroflow.input.input_guard.user32.GetMessageW", side_effect=fake_get_message)
        patch_post = patch("macroflow.input.input_guard.user32.PostThreadMessageW")
        patch_next = patch("macroflow.input.input_guard.user32.CallNextHookEx", return_value=0)
        patch_unhook = patch("macroflow.input.input_guard.user32.UnhookWindowsHookEx")
        patch_set.start()
        patch_get.start()
        post = patch_post.start()
        patch_next.start()
        patch_unhook.start()
        capturer = KeyCapturer(on_key=events.append, on_cancel=lambda: events.append("cancel"))
        self.assertTrue(capturer.start(timeout=1.0))
        return capturer, captured, post, (patch_set, patch_get, patch_post, patch_next, patch_unhook)

    def _press(self, proc, vk, flags=0):
        data = KBDLLHOOKSTRUCT()
        data.vkCode = vk
        data.flags = flags
        return proc(0, WM_KEYDOWN, ctypes.addressof(data))

    def _finish(self, capturer, patches, release):
        release.set()
        capturer.stop()
        for patch_ in patches:
            patch_.stop()

    def test_key_capturer_captures_next_keydown(self):
        events, release = [], threading.Event()
        capturer, captured, post, patches = self._install_capturer(events, release)
        try:
            self.assertEqual(self._press(captured["proc"], 0x41), 1)
            post.assert_called_once()
        finally:
            self._finish(capturer, patches, release)
        self.assertEqual(events, [0x41])

    def test_key_capturer_esc_cancels(self):
        events, release = [], threading.Event()
        capturer, captured, post, patches = self._install_capturer(events, release)
        try:
            self.assertEqual(self._press(captured["proc"], VK_ESCAPE), 1)
            post.assert_called_once()
        finally:
            self._finish(capturer, patches, release)
        self.assertEqual(events, ["cancel"])

    def test_key_capturer_lets_reserved_hotkeys_pass(self):
        events, release = [], threading.Event()
        capturer, captured, post, patches = self._install_capturer(events, release)
        try:
            self.assertEqual(self._press(captured["proc"], VK_F9), 0)
            self.assertEqual(self._press(captured["proc"], 0x7B), 0)  # F12
            post.assert_not_called()
        finally:
            self._finish(capturer, patches, release)
        self.assertEqual(events, [])

    def test_key_capturer_ignores_injected_keys(self):
        events, release = [], threading.Event()
        capturer, captured, post, patches = self._install_capturer(events, release)
        try:
            self.assertEqual(self._press(captured["proc"], 0x41, flags=LLKHF_INJECTED), 0)
            post.assert_not_called()
        finally:
            self._finish(capturer, patches, release)
        self.assertEqual(events, [])

    def test_key_capturer_start_failure_reports_false(self):
        with patch("macroflow.input.input_guard.user32.SetWindowsHookExW", return_value=0) as set_hook, \
             patch("macroflow.input.input_guard.user32.GetMessageW") as get_msg, \
             patch("macroflow.input.input_guard.user32.UnhookWindowsHookEx"):
            capturer = KeyCapturer(on_key=Mock())
            self.assertFalse(capturer.start(timeout=1.0))
        set_hook.assert_called_once()
        get_msg.assert_not_called()

    def test_vk_to_key_name_round_trips_through_key_to_vk(self):
        for name in ("A", "0", "ENTER", "SPACE", "CTRL", "F5", "LEFT", "DELETE", "NUMLOCK"):
            vk, parsed = key_to_vk(name)
            self.assertEqual(parsed, name)
            self.assertEqual(vk_to_key_name(vk), name)
            self.assertEqual(key_to_vk(vk_to_key_name(vk))[0], vk)

    def test_vk_to_key_name_falls_back_to_vk_prefix(self):
        self.assertEqual(vk_to_key_name(0x5D), "VK_0x5d")  # 菜单键不在 VK_NAMES
        self.assertEqual(key_to_vk("VK_0x5d")[0], 0x5D)
        self.assertIn(VK_F9, RESERVED_HOTKEY_VKS)

    def test_key_dialog_start_capture_starts_capturer(self):
        dialog = KeyActionDialog.__new__(KeyActionDialog)
        dialog.after = lambda delay, callback, *args: None
        dialog.capture_button = Mock()
        dialog.capture_hint = Mock()
        dialog.capturer = None
        with patch_dialogs("KeyCapturer") as capturer_class:
            capturer_class.return_value.start.return_value = True
            dialog.start_capture()
        capturer_class.assert_called_once()
        dialog.capture_button.configure.assert_called_with(state="disabled")
        dialog.capture_hint.set.assert_called_with(KEY_HINT_CAPTURING)
        dialog.capturer.start.assert_called_once()

    def test_key_dialog_capture_failure_shows_notice(self):
        dialog = KeyActionDialog.__new__(KeyActionDialog)
        dialog.after = lambda delay, callback, *args: None
        dialog.capture_button = Mock()
        dialog.capture_hint = Mock()
        dialog.capturer = None
        with patch_dialogs("KeyCapturer") as capturer_class, \
             patch_dialogs("show_floating_notice") as notice:
            capturer_class.return_value.start.return_value = False
            dialog.start_capture()
        notice.assert_called_once()
        self.assertIsNone(dialog.capturer)
        dialog.capture_button.configure.assert_called_with(state="normal")

    def test_key_dialog_apply_captured_key_sets_key_and_ends_capture(self):
        dialog = KeyActionDialog.__new__(KeyActionDialog)
        dialog.key = Mock()
        dialog.capture_button = Mock()
        dialog.capture_hint = Mock()
        capturer = Mock()
        dialog.capturer = capturer
        dialog._apply_captured_key(0x41)
        dialog.key.set.assert_called_with("A")
        capturer.stop.assert_called_once()
        self.assertIsNone(dialog.capturer)
        dialog.capture_button.configure.assert_called_with(state="normal")

    def test_key_dialog_cancel_capture_ends_capture(self):
        dialog = KeyActionDialog.__new__(KeyActionDialog)
        dialog.capture_button = Mock()
        dialog.capture_hint = Mock()
        capturer = Mock()
        dialog.capturer = capturer
        dialog._cancel_capture()
        capturer.stop.assert_called_once()
        self.assertIsNone(dialog.capturer)

    def test_key_dialog_destroy_stops_capturer(self):
        dialog = KeyActionDialog.__new__(KeyActionDialog)
        capturer = Mock()
        dialog.capturer = capturer
        with patch("macroflow.ui.dialogs.ModalDialog.destroy", create=True) as base_destroy:
            dialog.destroy()
        capturer.stop.assert_called_once()
        self.assertIsNone(dialog.capturer)
        base_destroy.assert_called_once()


class FocusModeTests(unittest.TestCase):
    def test_disabled_focus_only_switches_english(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.input_guard = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app._log = Mock()
        with patch_app("force_english_input", return_value=True):
            self.assertFalse(app._enter_focus_mode(123, enabled=False))
        app.input_guard.start.assert_not_called()
        app._log.assert_called_once()

    def test_focus_guard_blocks_game_input_and_releases_on_owner_thread(self):
        release = threading.Event()

        def fake_get_message(*_args):
            release.wait(2)
            return 0

        def fake_post(_thread_id, message, *_args):
            if message == 0x0012:  # WM_QUIT
                release.set()
            return True

        guard = FocusInputGuard()
        with patch("macroflow.input.input_guard.user32.SetWindowsHookExW", return_value=1), \
             patch("macroflow.input.input_guard.user32.GetMessageW", side_effect=fake_get_message), \
             patch("macroflow.input.input_guard.user32.PostThreadMessageW", side_effect=fake_post), \
             patch("macroflow.input.input_guard.user32.UnhookWindowsHookEx"), \
             patch("macroflow.input.input_guard.user32.BlockInput", return_value=True) as block_input:
            self.assertTrue(guard.start(timeout=1.0))
            self.assertTrue(guard.block())
            guard.stop()
        self.assertEqual(
            [call.args[0] for call in block_input.call_args_list], [True, False],
        )

    def test_only_f12_and_injected_keyboard_are_allowed(self):
        self.assertTrue(should_block_keyboard(0x41, 0))
        self.assertFalse(should_block_keyboard(VK_F12, 0))
        self.assertTrue(should_block_keyboard(0x41, LLKHF_INJECTED))
        self.assertFalse(should_block_keyboard(
            0x41, LLKHF_INJECTED, MACROFLOW_INPUT_TAG,
        ))

    def test_only_macroflow_injected_mouse_is_allowed(self):
        self.assertTrue(should_block_mouse(0))
        self.assertTrue(should_block_mouse(LLMHF_INJECTED))
        self.assertFalse(should_block_mouse(
            LLMHF_INJECTED, MACROFLOW_INPUT_TAG,
        ))

    def test_focus_mode_switches_english_before_locking_input(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        order = []
        app.hotkey_scripts = []
        app.input_guard = Mock()
        app.input_guard.start.side_effect = lambda: order.append("guard") or True
        app.input_guard.block.side_effect = lambda: order.append("block") or True
        app._ui = Mock()
        app._log = Mock()
        with patch_app("force_english_input", side_effect=lambda _hwnd: order.append("english") or True):
            app._enter_focus_mode(123)
        self.assertEqual(order, ["english", "guard", "block"])

    def test_focus_mode_failure_stops_hook(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.hotkey_scripts = []
        app.input_guard = Mock()
        app.input_guard.start.return_value = True
        app.input_guard.block.return_value = False
        with patch_app("force_english_input", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "管理员身份"):
                app._enter_focus_mode(123)
        app.input_guard.stop.assert_called_once()

    def test_script_worker_activates_prewindow_before_ocr_wait(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app._ui = lambda callback, *args: callback(*args)
        app._set_status = Mock()
        app._log = Mock()
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._clear_global_guards = Mock()
        app._finish_execution_visibility = Mock()
        app._handle_worker_error = Mock()
        app._script_needs_ocr = Mock(return_value=True)
        order = []
        app._activate_execution_window_before_ocr = Mock(
            side_effect=lambda hwnd: order.append(("activate", hwnd)) or True,
        )
        app._ensure_ocr_ready = Mock(
            side_effect=lambda: order.append("ocr") or True,
        )
        app.player.play.side_effect = lambda *args, **kwargs: order.append(
            ("play", kwargs["activation_prepared"]),
        )

        app._run_script_worker(
            [{"type": "text_ocr"}], 1, 123, 456, None, False, True,
        )

        self.assertEqual(order, [("activate", 456), "ocr", ("play", True)])


class InputGuardRestartTests(unittest.TestCase):
    """专注模式守卫的启动/退出竞态：连续两次执行不能被“上一次还没退完”否掉。"""

    def test_start_waits_for_previous_hook_thread_instead_of_failing(self):
        # 上一次执行的钩子线程还在收尾时再执行一次：必须等它退完再启动新会话，
        # 不能直接判失败（否则这次执行被整个取消＝按了执行没反应）。
        guard = FocusInputGuard()

        def fake_run() -> None:
            # 模拟新会话启动成功（不真的装钩子/锁输入）。
            guard.active = True
            guard._ready.set()

        dying = threading.Thread(target=lambda: time.sleep(0.2), daemon=True)
        dying.start()
        guard._thread = dying  # 上一次会话的钩子线程：还没退完
        self.addCleanup(guard.release)
        with patch.object(guard, "_run", fake_run):
            started = time.perf_counter()
            self.assertTrue(guard.start(timeout=2.0))
        elapsed = time.perf_counter() - started
        self.assertFalse(dying.is_alive(), "应该等上一个钩子线程退出后再启动")
        self.assertGreater(elapsed, 0.1, "应当等待上一个钩子线程收尾，而不是立刻启动")
        self.assertIsNot(guard._thread, dying)

    def test_previous_session_teardown_does_not_clear_new_dispatcher(self):
        import macroflow.input.input_guard as guard_module

        guard = FocusInputGuard()
        seen = []
        with patch.object(guard_module.wininput, "set_input_dispatcher",
                          side_effect=seen.append):
            guard._session = 1
            # 上一次会话（启动时 session=0）这时候才跑完收尾。
            guard._release_dispatcher(0)
            self.assertEqual(seen, [])
            # 自己这一会话退出时才撤销分发器。
            guard._release_dispatcher(1)
        self.assertEqual(seen, [None])

    def test_guard_sends_input_only_after_focus_callback(self):
        import macroflow.input.input_guard as guard_module

        order = []
        guard = FocusInputGuard()
        guard._before_input = lambda: order.append("focus")
        guard._thread = threading.current_thread()
        with patch.object(guard_module.wininput, "_send_input_direct",
                          side_effect=lambda _input: order.append("send")):
            guard._dispatch_input(object())
        # 先抢回目标窗口前台，再发这一包输入；顺序反了这一包就发给了别的前台窗口。
        self.assertEqual(order, ["focus", "send"])

    def test_input_actions_restore_target_foreground_first(self):
        # 焦点被抢后，下一个输入动作要先把目标窗口抢回前台：按键发给别的前台
        # 窗口就是“这一次按键没反应”。
        player = MacroPlayer()
        player._ensure_foreground_for_input = Mock()
        with patch_player("send_key") as send_key:
            player._execute_action({"type": "key", "vk": 69, "down": True}, 100)
        player._ensure_foreground_for_input.assert_called_once_with(100)
        send_key.assert_called_once_with(69, True)

    def test_delay_actions_do_not_touch_foreground(self):
        player = MacroPlayer()
        player._ensure_foreground_for_input = Mock()
        player._wait = Mock()
        player._execute_action({"type": "delay", "ms": 0}, 100)
        player._ensure_foreground_for_input.assert_not_called()


class ShutdownLifecycleTests(unittest.TestCase):
    def test_close_always_quits_instead_of_hiding(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._quit_app = Mock()
        app._hide_main_to_tray = Mock()
        app.on_close()
        app._quit_app.assert_called_once()
        app._hide_main_to_tray.assert_not_called()

    def test_packaged_exit_terminates_with_background_thread_alive(self):
        import subprocess
        import sys
        code = "\n".join([
            "from tests.common import MacroFlowApp",
            "import sys, threading",
            "from unittest.mock import Mock",
            "app = MacroFlowApp.__new__(MacroFlowApp)",
            "app.root = Mock()",
            "app.exiting = True",
            "sys.frozen = True",
            "threading.Thread(target=threading.Event().wait, daemon=False).start()",
            "app.run()",
            "raise RuntimeError('packaged process did not exit')",
        ])
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True,
            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_stop_tray_waits_for_detached_tray_thread(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.tray_icon = Mock()
        app.tray_icon._thread = Mock()
        app.tray_icon._thread.is_alive.return_value = True
        icon = app.tray_icon

        app._stop_tray()

        icon.stop.assert_called_once()
        icon._thread.join.assert_called_once()

    def test_tray_creation_is_skipped_after_shutdown_begins(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.tray_icon = None
        app.exiting = True
        app._tray_restore = Mock()
        app._tray_exit = Mock()
        app._ui = Mock()

        with patch("macroflow.ui.app.pystray.Icon") as icon:
            self.assertFalse(app._ensure_tray())

        icon.assert_not_called()

if __name__ == '__main__':
    unittest.main()
