"""Sequence timing switches: no GUI and no real input."""
import unittest
from unittest.mock import Mock, call, patch

from macroflow.core.models import key_sequence_parameters
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from macroflow.ui.dialogs.actions import KeySequenceDialog
from tests.helpers.core import FakeVar
from tests.helpers.patches import package_patch


class KeySequenceExecutionOptionTests(unittest.TestCase):
    def test_log_description_reports_effective_options(self):
        detail = MacroPlayer()._guard_processing_action_description(dict(type='key_sequence', keys='ab', interval_ms=600))
        self.assertIn('仅执行一次', detail)
        self.assertIn('相邻操作间隔关闭', detail)
        self.assertNotIn('600 ms', detail)

    def test_interval_is_off_by_default_and_ignores_unused_value(self):
        for value in (600, 'unused', None):
            self.assertEqual(key_sequence_parameters(dict(keys='ab', interval_ms=value)), ('ab', 0, 0))

    def test_enabled_interval_uses_default_or_configured_ms(self):
        self.assertEqual(key_sequence_parameters(dict(keys='ab', interval_enabled=True)), ('ab', 500, 0))
        self.assertEqual(key_sequence_parameters(dict(keys='ab', interval_enabled=True, interval_ms=600)), ('ab', 600, 0))

    def test_unchecked_cycle_executes_once_and_returns(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key') as send:
            player._execute_action(dict(type='key_sequence', keys='ab'), None)
        self.assertEqual(send.call_args_list, [call(65, True), call(65, False), call(66, True), call(66, False)])
        self.assertEqual(player._wait.call_args_list, [call(30), call(30)])
        player._poll_guards.assert_not_called()

    def test_explicit_delay_still_runs_without_either_switch(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key'):
            player._execute_action(dict(type='key_sequence', keys='a[延时:800]b', interval_ms=600), None)
        self.assertEqual(player._wait.call_args_list, [call(30), call(800), call(30)])

    def test_enabled_interval_runs_without_enabling_repetition(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key'):
            player._execute_action(dict(type='key_sequence', keys='ab', interval_enabled=True, interval_ms=600), None)
        self.assertEqual(player._wait.call_args_list, [call(30), call(600), call(30)])

    def test_interval_checkbox_controls_entry(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog.interval_entry = Mock()
        for enabled, state in ((False, 'disabled'), (True, 'normal')):
            dialog.interval_enabled = FakeVar(enabled)
            dialog._toggle_interval()
            dialog.interval_entry.configure.assert_called_with(state=state)

    def test_short_cycles_keep_global_detection_running(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=[None, None, PlaybackStopped()])
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key'), self.assertRaises(PlaybackStopped):
            player._execute_action(dict(type='key_sequence', keys='a', cycle_interval_enabled=True,
                                        cycle_interval_s=0.01), None)
        player._poll_guards.assert_called_once()

    def test_disabled_interval_saves_switch_and_retains_edited_value(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog._source = dict(action_id='stable')
        dialog.keys, dialog.interval = FakeVar('ab'), FakeVar('600')
        dialog.interval_enabled = FakeVar(False)
        dialog.cycle_interval_enabled, dialog.cycle_interval = FakeVar(False), FakeVar('20')
        dialog.destroy = Mock()
        with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
            dialog.save()
        notice.assert_not_called()
        self.assertFalse(dialog.result['interval_enabled'])
        self.assertEqual(dialog.result['interval_ms'], 600)
        self.assertEqual(key_sequence_parameters(dialog.result), ('ab', 0, 0))
