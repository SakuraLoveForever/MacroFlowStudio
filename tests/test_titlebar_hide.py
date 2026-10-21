"""标题栏隐藏按钮，使用控件与 Win32 替身，不创建窗口。"""
import unittest
from unittest.mock import Mock, patch
from tests.helpers.core import add_src_to_path


class TitlebarHideTests(unittest.TestCase):
    def make_button(self):
        from macroflow.ui.app.titlebar import TitlebarHideButton
        app = Mock()
        app.root.state.return_value = "normal"
        app._hide_main_to_tray.return_value = True
        with patch('macroflow.ui.app.titlebar.tk.Toplevel'), \
             patch('macroflow.ui.app.titlebar.tk.Button'):
            button = TitlebarHideButton(app)
        button.window.reset_mock()
        return app, button

    def test_click_hides_to_tray_without_closing_or_stopping(self):
        app, button = self.make_button()
        button.hide()
        app._persist_workflow_draft.assert_called_once()
        app._hide_main_to_tray.assert_called_once_with()
        app._quit_app.assert_not_called()
        app.stop_all.assert_not_called()

    def test_tray_failure_keeps_window_reachable_and_shows_notice(self):
        app, button = self.make_button()
        app._hide_main_to_tray.return_value = False
        button.hide()
        app._notify.assert_called_once()
        app._quit_app.assert_not_called()

    def test_button_tracks_native_caption_and_does_not_take_focus(self):
        app, button = self.make_button()
        with patch('macroflow.ui.app.titlebar.titlebar_hide_button_rect', return_value=(-150, 40, 46, 30)), \
             patch('macroflow.ui.app.titlebar.set_window_owner') as owner, \
             patch('macroflow.ui.app.titlebar.make_window_no_activate') as no_activate, \
             patch('macroflow.ui.app.titlebar.move_window_no_activate') as move:
            button.sync()
        button.window.geometry.assert_called_with("46x30")
        owner.assert_called_once_with(button.window.winfo_id.return_value, app.root.winfo_id.return_value)
        no_activate.assert_called_once_with(button.window.winfo_id.return_value, topmost=False)
        move.assert_called_once_with(button.window.winfo_id.return_value, -150, 40)

    def test_button_disappears_when_main_window_is_hidden_or_minimized(self):
        for state in ("withdrawn", "iconic"):
            app, button = self.make_button()
            app.root.state.return_value = state
            button.sync()
            button.window.withdraw.assert_called_once()

    def test_native_button_position_uses_tk_wrapper_and_dwm_bounds(self):
        from macroflow.input import wininput
        def outer_rect(hwnd, pointer):
            pointer._obj.left, pointer._obj.top = -1000, 100
            pointer._obj.right, pointer._obj.bottom = 200, 900
            return True
        def caption_rect(hwnd, attribute, pointer, size):
            pointer._obj.left, pointer._obj.top = 1050, 8
            pointer._obj.right, pointer._obj.bottom = 1188, 38
            return 0
        with patch.object(wininput, 'user32') as api, patch.object(wininput, 'dwmapi') as dwm:
            api.GetAncestor.return_value = 456
            api.GetWindowRect.side_effect = outer_rect
            dwm.DwmGetWindowAttribute.side_effect = caption_rect
            self.assertEqual(wininput.titlebar_hide_button_rect(123, 46), (4, 108, 46, 30))

    def test_titlebar_style_preserves_normal_window_stacking(self):
        from macroflow.input import wininput
        with patch.object(wininput, 'user32') as api:
            api.GetAncestor.return_value = 456
            api.GetWindowLongW.return_value = 0
            wininput.make_window_no_activate(123, topmost=False)
            args = api.SetWindowPos.call_args.args
            self.assertTrue(args[-1] & wininput.SWP_NOZORDER)
            self.assertTrue(args[-1] & wininput.SWP_NOACTIVATE)
            self.assertEqual(args[1].value, None)

    def test_popup_owner_uses_native_wrappers(self):
        from macroflow.input import wininput
        with patch.object(wininput, 'user32') as api:
            api.GetAncestor.side_effect = [456, 789]
            wininput.set_window_owner(123, 234)
            api.SetWindowLongPtrW.assert_called_once_with(456, -8, 789)
