import threading
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.ui.app.execution import ExecutionMixin


class EnglishInputOptionTests(unittest.TestCase):
    def app(self):
        app = ExecutionMixin()
        app._focus_state_lock = threading.RLock()
        app._ui = lambda callback, *args: callback(*args)
        app._log = Mock()
        app.input_guard = Mock()
        app.player = Mock(paused=False)
        app.hotkey_scripts = []
        app._apply_hotkey_bindings = Mock()
        return app

    def test_default_does_not_change_input_method_with_or_without_focus_lock(self):
        for focus in [False, True]:
            app = self.app()
            with patch('macroflow.ui.app.execution.force_english_input') as switch:
                app._enter_focus_mode(123, enabled=focus)
            switch.assert_not_called()
            if focus:
                app.input_guard.block.assert_called_once_with()
            else:
                app.input_guard.start.assert_not_called()

    def test_checked_option_switches_for_both_focus_settings(self):
        for focus in [False, True]:
            app = self.app()
            app.switch_english_input_enabled = True
            with patch('macroflow.ui.app.execution.force_english_input', return_value=True) as switch:
                app._enter_focus_mode(123, enabled=focus)
            switch.assert_called_once_with(123)

    def test_checked_switch_failure_stops_before_input_lock(self):
        app = self.app()
        app.switch_english_input_enabled = True
        with patch('macroflow.ui.app.execution.force_english_input', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '英语输入法'):
                app._enter_focus_mode(123)
        app.input_guard.start.assert_not_called()
