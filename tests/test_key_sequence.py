"""组合按键的后台验证，不创建窗口或发送真实输入。"""
import unittest
from unittest.mock import Mock, call, patch

from tests.helpers.core import FakeVar
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from macroflow.ui.app.summaries import action_summary
from tests.helpers.patches import package_patch


class KeySequenceTests(unittest.TestCase):
    def test_sequence_sends_keys_in_order_with_only_between_key_delays(self):
        player = MacroPlayer()
        events = []
        def wait(ms):
            if ms > 1000:
                raise PlaybackStopped()
            events.append(("wait", ms))
        player._wait = wait
        with package_patch('player', 'send_key', side_effect=lambda vk, down: events.append((vk, down))):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "2v3e6e4v4e"}, None)
        expected = []
        for index, key in enumerate("2V3E6E4V4E"):
            if index:
                expected.append(("wait", 50))
            expected.extend([(ord(key), True), ("wait", 30), (ord(key), False)])
        self.assertEqual(events, expected)
        self.assertFalse(player._held_keys)

    def test_interval_can_be_zero_or_custom(self):
        for interval in (0, 125):
            with self.subTest(interval=interval):
                player = MacroPlayer()
                player._wait = Mock(side_effect=[None, None, None, PlaybackStopped()])
                with package_patch('player', 'send_key'):
                    with self.assertRaises(PlaybackStopped):
                        player._execute_action({"type": "key_sequence", "interval_enabled": True, "cycle_interval_enabled": True, "keys": "aB", "interval_ms": interval}, None)
                expected = [call(30), call(interval), call(30)] if interval else [call(30), call(30)]
                self.assertEqual(player._wait.call_args_list[:len(expected)], expected)

    def test_invalid_sequence_is_rejected_before_sending_input(self):
        for keys in ("", "a b", "a+", "中文", "１２", "é"):
            with self.subTest(keys=keys), package_patch('player', 'send_key') as send:
                with self.assertRaises(ValueError):
                    MacroPlayer()._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": keys}, None)
                send.assert_not_called()

    def test_stop_during_hold_releases_key(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key') as send:
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "ab"}, None)
        self.assertEqual(send.call_args_list, [call(65, True), call(65, False)])
        self.assertFalse(player._held_keys)

    def test_stop_between_keys_does_not_send_next_key(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=[None, PlaybackStopped()])
        with package_patch('player', 'send_key') as send:
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "ab"}, None)
        self.assertEqual(send.call_args_list, [call(65, True), call(65, False)])

    def test_invalid_saved_interval_is_rejected(self):
        for interval in (-1, 1.5, None, True):
            with self.subTest(interval=interval), package_patch('player', 'send_key') as send:
                with self.assertRaises(ValueError):
                    MacroPlayer()._execute_action({"type": "key_sequence", "interval_enabled": True, "cycle_interval_enabled": True, "keys": "ab", "interval_ms": interval}, None)
                send.assert_not_called()

    def test_edit_routes_to_sequence_dialog(self):
        from macroflow.ui.dialogs.actions import edit_action
        action = {"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "ab", "action_id": "stable"}
        with patch('macroflow.ui.dialogs.actions.KeySequenceDialog') as dialog:
            dialog.return_value.show.return_value = {"type": "key_sequence", "interval_enabled": True, "cycle_interval_enabled": True, "keys": "cd", "interval_ms": 75}
            edited = edit_action(None, action)
        self.assertEqual(edited["keys"], "cd")
        self.assertEqual(edited["action_id"], "stable")

    def test_summary_shows_sequence_and_interval(self):
        label, detail, _ = action_summary({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "2v3e"})
        self.assertIn("组合按键", label)
        self.assertIn("2v3e", detail)
        self.assertIn("50 ms", detail)
        self.assertIn("20 s", detail)

    def test_cycle_interval_is_measured_from_first_key(self):
        player = MacroPlayer()
        now = [0.0]
        starts = []
        def wait(ms):
            now[0] += ms / 1000
            if now[0] >= 60:
                raise PlaybackStopped()
        player._wait = wait
        with patch('macroflow.execution.player.core.time.perf_counter', side_effect=lambda: now[0]), \
             package_patch('player', 'send_key', side_effect=lambda vk, down: starts.append(now[0]) if down else None):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "ab"}, None)
        self.assertEqual(len(starts), 6)
        for actual, expected in zip(starts, [0.0, 0.08, 20.0, 20.08, 40.0, 40.08]):
            self.assertAlmostEqual(actual, expected)

    def test_slow_sequence_restarts_without_overlapping(self):
        player = MacroPlayer()
        now = [0.0]
        starts = []
        def wait(ms):
            now[0] += ms / 1000
            if len(starts) == 3:
                raise PlaybackStopped()
        player._wait = wait
        with patch('macroflow.execution.player.core.time.perf_counter', side_effect=lambda: now[0]), \
             package_patch('player', 'send_key', side_effect=lambda vk, down: starts.append(now[0]) if down else None):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "a", "cycle_interval_s": 0.01}, None)
        self.assertEqual(starts, [0.0, 0.03, 0.06])

    def test_invalid_cycle_interval_is_rejected(self):
        for cycle in (0, -1, "abc", "nan", "inf", None, True):
            with self.subTest(cycle=cycle), package_patch('player', 'send_key') as send:
                with self.assertRaises(ValueError):
                    MacroPlayer()._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "ab", "cycle_interval_s": cycle}, None)
                send.assert_not_called()

    def test_pause_during_sequence_freezes_cycle_timer(self):
        player = MacroPlayer()
        now = [0.0]
        starts = []
        def wait(ms):
            if len(starts) == 1 and ms == 50:
                player.pause()
                now[0] += 30
                player.resume()
            now[0] += ms / 1000
            if len(starts) >= 3:
                raise PlaybackStopped()
        player._wait = wait
        with patch('macroflow.execution.player.core.time.perf_counter', side_effect=lambda: now[0]), \
             package_patch('player', 'send_key', side_effect=lambda vk, down: starts.append(now[0]) if down else None):
            with self.assertRaises(PlaybackStopped):
                player._execute_action({"type": "key_sequence", "interval_enabled": True, "interval_ms": 50, "cycle_interval_enabled": True, "keys": "ab"}, None)
        self.assertAlmostEqual(starts[2], 50)

    def test_dialog_saves_and_preserves_identity(self):
        from macroflow.ui.dialogs.actions import KeySequenceDialog
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog._source = {"action_id": "stable", "after_delay_ms": 15}
        dialog.keys = FakeVar("2v3e")
        dialog.interval = FakeVar("50")
        dialog.interval_enabled = FakeVar(True)
        dialog.cycle_interval_enabled = FakeVar(True)
        dialog.cycle_interval = FakeVar("20")
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {"type": "key_sequence", "interval_enabled": True, "cycle_interval_enabled": True, "keys": "2v3e", "interval_ms": 50, "cycle_interval_s": 20,
                                        "action_id": "stable", "after_delay_ms": 15})

    def test_dialog_rejects_invalid_values(self):
        from macroflow.ui.dialogs.actions import KeySequenceDialog
        for keys, interval in (("a b", "50"), ("", "50"), ("ab", "-1"), ("ab", "1.5")):
            dialog = KeySequenceDialog.__new__(KeySequenceDialog)
            dialog.keys, dialog.interval = FakeVar(keys), FakeVar(interval)
            dialog.interval_enabled = FakeVar(True)
            dialog.cycle_interval_enabled = FakeVar(True)
            dialog.cycle_interval = FakeVar("20")
            dialog.destroy = Mock()
            with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
                dialog.save()
            notice.assert_called_once()
            dialog.destroy.assert_not_called()
