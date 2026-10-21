"""Optional whole-sequence cycle waits; no GUI or real input."""
import unittest
from unittest.mock import Mock, patch

from macroflow.core.models import key_sequence_parameters
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.app.summaries import action_summary
from macroflow.ui.dialogs.actions import KeySequenceDialog
from tests.helpers.core import FakeVar
from tests.helpers.patches import package_patch


class KeySequenceCycleOptionTests(unittest.TestCase):
    def test_cycle_wait_is_disabled_by_default_and_ignores_unused_value(self):
        for value in (20, "invalid", None):
            with self.subTest(value=value):
                self.assertEqual(key_sequence_parameters(dict(keys="ab", cycle_interval_s=value)),
                                 ("ab", 0, 0))

    def test_enabled_cycle_validates_and_uses_configured_seconds(self):
        self.assertEqual(key_sequence_parameters(dict(keys="ab", cycle_interval_enabled=True,
                                                     cycle_interval_s="2.5")), ("ab", 0, 2.5))
        for value in (0, -1, "invalid", "nan", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                key_sequence_parameters(dict(keys="ab", cycle_interval_enabled=True,
                                             cycle_interval_s=value))

    def test_disabled_cycle_executes_once_without_countdown(self):
        now, starts, countdown = [0.0], [], []
        player = MacroPlayer(on_key_sequence_countdown=countdown.append)
        def wait(ms):
            now[0] += ms / 1000
            if len(starts) == 3:
                raise PlaybackStopped()
        player._wait = wait
        with patch('macroflow.execution.player.core.time.perf_counter', side_effect=lambda: now[0]), \
             package_patch('player', 'send_key', side_effect=lambda vk, down: starts.append(now[0]) if down else None):
            player._execute_action(dict(type="key_sequence", keys="a"), None)
        self.assertEqual(starts, [0.0])
        self.assertEqual(countdown, [])
        self.assertFalse(player._held_keys)

    def test_summary_and_notice_describe_single_execution(self):
        _, detail, _ = action_summary(dict(type="key_sequence", keys="ab", cycle_interval_s="unused"))
        self.assertIn("仅执行一次", detail)
        self.assertNotIn("20 s", detail)
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._ui = Mock()
        for running in (True, False):
            app._key_sequence_state(running, 0)
            self.assertIn("仅执行一次", app._ui.call_args.args[1])
            self.assertNotIn("0 秒", app._ui.call_args.args[1])

    def test_dialog_saves_disabled_mode_without_validating_disabled_entry(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog._source = dict(action_id="stable", cycle_interval_s=12)
        dialog.keys, dialog.interval = FakeVar("ab"), FakeVar("50")
        dialog.interval_enabled = FakeVar(True)
        dialog.cycle_interval_enabled = FakeVar(False)
        dialog.cycle_interval = FakeVar("unused")
        dialog.destroy = Mock()
        dialog.save()
        self.assertFalse(dialog.result["cycle_interval_enabled"])
        self.assertEqual(dialog.result["cycle_interval_s"], 12)
        self.assertEqual(dialog.result["action_id"], "stable")

    def test_cycle_checkbox_controls_entry_state(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog.cycle_interval_entry = Mock()
        for enabled, state in ((False, "disabled"), (True, "normal")):
            dialog.cycle_interval_enabled = FakeVar(enabled)
            dialog._toggle_cycle_interval()
            dialog.cycle_interval_entry.configure.assert_called_with(state=state)

    def test_disabling_cycle_preserves_edited_seconds_for_next_enable(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog._source = dict(cycle_interval_s=20)
        dialog.keys, dialog.interval = FakeVar("ab"), FakeVar("50")
        dialog.interval_enabled = FakeVar(True)
        dialog.cycle_interval_enabled, dialog.cycle_interval = FakeVar(False), FakeVar("7")
        dialog.destroy = Mock()
        dialog.save()
        self.assertFalse(dialog.result["cycle_interval_enabled"])
        self.assertEqual(dialog.result["cycle_interval_s"], 7)

    def test_single_execution_does_not_start_another_round(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=[None, None, None, PlaybackStopped()])
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key'):
            player._execute_action(dict(type="key_sequence", keys="ab"), None)
        player._poll_guards.assert_not_called()
        self.assertFalse(player._held_keys)
