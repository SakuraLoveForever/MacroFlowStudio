"""No windows are created: reproduce the hidden task-switcher foreground handle."""
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_state import SSJJReader


class HiddenShellForegroundTests(unittest.TestCase):
    def reader(self, windows, foreground=1):
        # hwnd: (pid, visible, width, minimized, ex_style, cloaked)
        states = {1: (7, False, 1920, False, 0, False), **windows}
        api = Mock()
        api.GetForegroundWindow.return_value = foreground
        api.IsWindowVisible.side_effect = lambda hwnd: states[hwnd][1]
        api.IsIconic.side_effect = lambda hwnd: states[hwnd][3]
        api.GetWindowLongPtrW.side_effect = lambda hwnd, index: states[hwnd][4]
        def pid(hwnd, ref):
            ref._obj.value = states[hwnd][0]
            return 10
        api.GetWindowThreadProcessId.side_effect = pid
        def rect(hwnd, ref):
            ref._obj.right, ref._obj.bottom = states[hwnd][2], 1080
            return True
        api.GetWindowRect.side_effect = rect
        order = list(windows)
        api.GetTopWindow.return_value = order[0] if order else 0
        api.GetWindow.side_effect = lambda hwnd, cmd: (
            order[order.index(hwnd) + 1] if order.index(hwnd) + 1 < len(order) else 0)
        api.EnumChildWindows.return_value = True
        def cloaked(hwnd, attribute, ref, size):
            ref._obj.value = states[hwnd.value][5]
            return 0
        self.enterContext(patch('macroflow.input.wininput.dwmapi.DwmGetWindowAttribute',
                                side_effect=cloaked))
        reader = SSJJReader.__new__(SSJJReader)
        reader.pid, reader.user = 42, api
        reader.activate = Mock(return_value=False)
        return reader

    def test_hidden_task_switcher_does_not_hide_the_top_game_window(self):
        reader = self.reader({2: (7, True, 1, False, 0, False),
                              3: (42, True, 1920, False, 0, False)})
        reader.require_foreground(activate=False)
        reader.activate.assert_not_called()

    def test_another_app_above_game_still_blocks_input(self):
        reader = self.reader({2: (88, True, 1920, False, 0, False),
                              3: (42, True, 1920, False, 0, False)})
        self.assertFalse(reader.is_foreground())
        with self.assertRaisesRegex(RuntimeError, '游戏不在前台'):
            reader.require_foreground(activate=False)

    def test_visible_task_switcher_is_not_ignored(self):
        reader = self.reader({1: (7, True, 1920, False, 0, False),
                              3: (42, True, 1920, False, 0, False)})
        self.assertFalse(reader.is_foreground())
        reader.user.GetTopWindow.assert_not_called()

    def test_nonactivating_overlays_minimized_and_cloaked_windows_are_skipped(self):
        reader = self.reader({2: (88, True, 1920, False, 0x08000000, False),
                              3: (88, True, 1920, True, 0, False),
                              4: (88, True, 1920, False, 0, True),
                              5: (42, True, 1920, False, 0, False)})
        reader.require_foreground(activate=False)

    def test_hidden_or_minimized_game_is_never_accepted(self):
        for visible, minimized in ((False, False), (True, True)):
            with self.subTest(visible=visible, minimized=minimized):
                reader = self.reader({3: (42, visible, 1920, minimized, 0, False)})
                self.assertFalse(reader.is_foreground())

    def test_visible_game_uses_native_foreground_without_enumeration(self):
        reader = self.reader({3: (42, True, 1920, False, 0, False)}, foreground=3)
        reader.require_foreground(activate=False)
        reader.user.GetTopWindow.assert_not_called()

    def test_changing_window_stack_cannot_loop_forever(self):
        reader = self.reader({2: (7, False, 1920, False, 0, False)})
        reader.user.GetWindow.side_effect = lambda hwnd, cmd: hwnd
        self.assertFalse(reader.is_foreground())
        self.assertEqual(reader.user.GetWindow.call_count, 1)


if __name__ == '__main__':
    unittest.main()
