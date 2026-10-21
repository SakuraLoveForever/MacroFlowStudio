"""Mixed sequence checks with all native input mocked; no windows are created."""
import unittest
from unittest.mock import Mock, call, patch

from macroflow.core.models import key_sequence_parameters
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from macroflow.ui.dialogs.actions import KeySequenceDialog
from tests.helpers.core import FakeVar
from tests.helpers.patches import package_patch


class KeySequenceMouseTests(unittest.TestCase):
    def test_mixed_sequence_order_counts_and_gaps(self):
        player, events = MacroPlayer(), []
        player._wait = lambda ms: events.append(('wait', ms))
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key', side_effect=lambda *args: events.append(('key', *args))), \
             package_patch('player', 'send_button', side_effect=lambda *args: events.append(('button', *args))), \
             package_patch('player', 'send_scroll', side_effect=lambda *args: events.append(('scroll', *args))), \
             package_patch('player', 'send_move_absolute') as move:
            player._execute_action(dict(type='key_sequence', keys='a[左键][右键][中键][滚轮上:2][滚轮下:3]B', interval_enabled=True, interval_ms=75), None)
        operations = [('key', 65), ('button', 'left'), ('button', 'right'), ('button', 'middle'),
                      ('scroll', 1), ('scroll', 1), ('scroll', -1), ('scroll', -1), ('scroll', -1), ('key', 66)]
        expected = []
        for index, (kind, value) in enumerate(operations):
            if index:
                expected.append(('wait', 75))
            if kind == 'scroll':
                expected.append(('scroll', 0, value))
            else:
                expected.extend([(kind, value, True), ('wait', 30), (kind, value, False)])
        self.assertEqual(events, expected)
        move.assert_not_called()
        self.assertFalse(player._held_buttons)

    def test_malformed_token_rejected_before_any_input(self):
        for keys in ('a[滚轮上:0]', 'a[滚轮下:-1]', '[滚轮上:1.5]', '[滚轮下:２]', '[滚轮上]', '[左键', '[未知]', 'a[左键]junk!'):
            with self.subTest(keys=keys), package_patch('player', 'send_key') as key, \
                 package_patch('player', 'send_button') as button, package_patch('player', 'send_scroll') as scroll:
                with self.assertRaises(ValueError):
                    MacroPlayer()._execute_action(dict(type='key_sequence', keys=keys), None)
                key.assert_not_called()
                button.assert_not_called()
                scroll.assert_not_called()

    def test_stop_during_mouse_hold_releases_button(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_button') as send:
            with self.assertRaises(PlaybackStopped):
                player._execute_action(dict(type='key_sequence', keys='[右键]'), None)
        self.assertEqual(send.call_args_list, [call('right', True), call('right', False)])
        self.assertFalse(player._held_buttons)

    def test_stop_between_scroll_notches(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_scroll') as send:
            with self.assertRaises(PlaybackStopped):
                player._execute_action(dict(type='key_sequence', keys='[滚轮下:1000000]', interval_enabled=True), None)
        self.assertEqual(send.call_args_list, [call(0, -1)])

    def test_insert_scroll_validates_count_and_inserts_at_caret(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog.mouse_action = FakeVar('滚轮上')
        dialog.mouse_count = FakeVar('3')
        dialog.keys_entry = Mock()
        dialog.insert_mouse_action()
        dialog.keys_entry.insert.assert_called_once_with('insert', '[滚轮上:3]')
        for value in ('0', '-1', '1.5', '２', ''):
            dialog.mouse_count = FakeVar(value)
            dialog.keys_entry.reset_mock()
            with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
                dialog.insert_mouse_action()
            notice.assert_called_once()
            dialog.keys_entry.insert.assert_not_called()

    def test_dialog_saves_mixed_sequence(self):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog._source = dict(action_id='stable')
        dialog.keys = FakeVar('a[中键][滚轮下:4]')
        dialog.interval = FakeVar('25')
        dialog.interval_enabled = FakeVar(True)
        dialog.cycle_interval_enabled = FakeVar(False)
        dialog.cycle_interval = FakeVar('20')
        dialog.destroy = Mock()
        with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
            dialog.save()
        notice.assert_not_called()
        self.assertEqual(dialog.result['keys'], 'a[中键][滚轮下:4]')
        self.assertEqual(key_sequence_parameters(dialog.result), ('a[中键][滚轮下:4]', 25, 0))
        self.assertEqual(dialog.result['action_id'], 'stable')
