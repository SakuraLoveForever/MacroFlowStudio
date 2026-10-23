"""Sidebar controls are verified without constructing any Tk window."""
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.ui.app import shell


class SidebarCleanupTests(unittest.TestCase):
    def test_shell_uses_rail_and_page_stack_instead_of_sidebar(self):
        source = inspect.getsource(shell.ShellMixin._build_ui)
        for removed in ("_build_sidebar", "启动与备份", "hotkey_summary_var", "ttk.Notebook"):
            self.assertNotIn(removed, source)
        self.assertIn("PageStack", source)
        self.assertIn("_build_rail", source)

    def test_settings_page_holds_configuration_sections(self):
        source = inspect.getsource(shell.ShellMixin._build_settings_page)
        self.assertIn("_configure_hotkey_scripts", source)
        self.assertIn("hotkey_summary_var", source)
        for removed in ("启动与备份", "专注执行："):
            self.assertNotIn(removed, source)

    def test_plugins_are_fourth_page(self):
        source = inspect.getsource(shell.ShellMixin._build_ui)
        self.assertLess(source.index('"运行日志"'), source.index('"插件"'))
        self.assertIn('build_plugins_page(self.plugins_tab, self)', source)

    def button(self):
        button = object.__new__(shell.SidebarButton)
        button._text = "暂停"
        button._command = Mock()
        button._state = "disabled"
        button._tone = ""
        button._draw = Mock()
        button._pressed = False
        return button

    def test_disabled_button_does_not_invoke_and_state_change_restores_it(self):
        button = self.button()
        button.invoke()
        button._command.assert_not_called()
        with patch.object(shell.tk.Canvas, "configure") as canvas_configure:
            button.configure(text="继续", state="normal", bootstyle="danger")
        canvas_configure.assert_called_once_with(takefocus=True, cursor="hand2")
        self.assertEqual(button._text, "继续")
        self.assertEqual(button._tone, "danger")
        self.assertEqual(button.cget("text"), "继续")
        self.assertEqual(button["state"], "normal")
        button.invoke()
        button._command.assert_called_once_with()
        button._draw.assert_called_once_with()

    def test_disabling_button_removes_it_from_keyboard_navigation(self):
        button = self.button()
        with patch.object(shell.tk.Canvas, "configure") as canvas_configure:
            button.configure(state="disabled")
        canvas_configure.assert_called_once_with(takefocus=False, cursor="arrow")
        self.assertIs(button.cget("command"), button._command)
        button.invoke()
        button._command.assert_not_called()

    def test_mouse_release_requires_a_press_inside_the_button(self):
        button = self.button()
        button._state = "normal"
        button.focus_set = Mock()
        button.winfo_width = Mock(return_value=100)
        button.winfo_height = Mock(return_value=30)
        event = SimpleNamespace(x=10, y=10)
        button._release(event)
        button._command.assert_not_called()
        button._press(event)
        button._release(SimpleNamespace(x=110, y=10))
        button._command.assert_not_called()
        button._press(event)
        button._release(event)
        button._command.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
