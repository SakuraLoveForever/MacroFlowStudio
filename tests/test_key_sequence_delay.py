"""Inline sequence delays, tested without windows or native input."""
import unittest
from unittest.mock import Mock, call, patch

from macroflow.core.models import key_sequence_parameters, key_sequence_steps
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.control import PlaybackStopped
from macroflow.ui.dialogs.actions import KeySequenceDialog
from tests.helpers.core import FakeVar
from tests.helpers.patches import package_patch
from tests import test_key_sequence_history


class KeySequenceDelayTests(unittest.TestCase):
    def make_dialog(self, enabled=False, value='500'):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog.insert_delay_enabled = FakeVar(enabled)
        dialog.insert_delay_ms = FakeVar(value)
        dialog.insert_delay_entry, dialog.insert_delay_button = Mock(), Mock()
        dialog.keys_entry = Mock()
        return dialog

    def test_constructor_defaults_to_unchecked_500ms_and_disabled_controls(self):
        import macroflow.ui.dialogs.actions as module
        widgets = {name: Mock() for name in ('Frame', 'Label', 'Entry', 'Button', 'Checkbutton', 'Combobox')}
        with patch.object(module.ModalDialog, '__init__', return_value=None), \
             patch.object(module.tk, 'StringVar', FakeVar), patch.object(module.tk, 'BooleanVar', FakeVar), \
             patch.multiple(module.ttk, **widgets), \
             patch.object(KeySequenceDialog, '_init_sequence_history'):
            dialog = KeySequenceDialog(None)
        self.assertFalse(dialog.insert_delay_enabled.get())
        self.assertFalse(dialog.interval_enabled.get())
        self.assertEqual(dialog.interval.get(), '500')
        self.assertFalse(dialog.cycle_interval_enabled.get())
        self.assertEqual(dialog.insert_delay_ms.get(), '500')
        dialog.insert_delay_entry.configure.assert_called_with(state='disabled')
        dialog.insert_delay_button.configure.assert_called_with(state='disabled')

    def test_unchecked_cannot_insert_and_does_not_validate_unused_value(self):
        dialog = self.make_dialog(value='invalid')
        with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
            dialog.insert_sequence_delay()
        notice.assert_not_called()
        dialog.keys_entry.insert.assert_not_called()

    def test_checked_inserts_default_or_custom_duration_at_caret(self):
        for value in ('500', '1250'):
            with self.subTest(value=value):
                dialog = self.make_dialog(True, value)
                dialog._toggle_insert_delay()
                dialog.insert_delay_entry.configure.assert_called_with(state='normal')
                dialog.insert_delay_button.configure.assert_called_with(state='normal')
                dialog.insert_sequence_delay()
                dialog.keys_entry.insert.assert_called_once_with('insert', f'[延时:{value}]')

    def test_invalid_duration_is_rejected(self):
        for value in ('', '0', '-1', '1.5', '５００', 'nan', '9' * 5000):
            with self.subTest(value=value):
                dialog = self.make_dialog(True, value)
                with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
                    dialog.insert_sequence_delay()
                notice.assert_called_once()
                dialog.keys_entry.insert.assert_not_called()

    def test_parse_multiple_inline_delays(self):
        keys = '[延时:500]a[延时:1200][左键][滚轮下:2][延时:750]'
        self.assertEqual(key_sequence_parameters(dict(keys=keys)), (keys, 0, 0))
        self.assertEqual(list(key_sequence_steps(keys)), [
            dict(type='delay', ms=500), dict(type='key_press', vk=65), dict(type='delay', ms=1200),
            dict(type='click', button='left', pos_mode='current'), dict(type='scroll', dy=-1),
            dict(type='scroll', dy=-1), dict(type='delay', ms=750)])

    def test_playback_uses_exact_inline_delay_without_extra_adjacent_gaps(self):
        player, events = MacroPlayer(), []
        player._wait = lambda ms: events.append(('wait', ms))
        player._poll_guards = Mock(side_effect=PlaybackStopped)
        with package_patch('player', 'send_key', side_effect=lambda *args: events.append(('key', *args))):
            player._execute_action(dict(type='key_sequence', keys='a[延时:500]bc', interval_enabled=True, interval_ms=50), None)
        self.assertEqual(events, [('key', 65, True), ('wait', 30), ('key', 65, False), ('wait', 500),
                                 ('key', 66, True), ('wait', 30), ('key', 66, False), ('wait', 50),
                                 ('key', 67, True), ('wait', 30), ('key', 67, False)])

    def test_stop_in_delay_does_not_send_next_key(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=[None, PlaybackStopped()])
        with package_patch('player', 'send_key') as send:
            with self.assertRaises(PlaybackStopped):
                player._execute_action(dict(type='key_sequence', keys='a[延时:500]b'), None)
        self.assertEqual(send.call_args_list, [call(65, True), call(65, False)])
        self.assertEqual(player._wait.call_args_list, [call(30), call(500)])
        self.assertFalse(player._held_keys)

    def test_bad_saved_token_rejected_before_first_key(self):
        for token in ('[延时:0]', '[延时:-1]', '[延时:1.5]', '[延时:５００]', '[延时:]', '[延时:500', '[延时:' + '9' * 5000 + ']'):
            with self.subTest(token=token[:25]), package_patch('player', 'send_key') as send:
                with self.assertRaises(ValueError):
                    MacroPlayer()._execute_action(dict(type='key_sequence', keys='a' + token), None)
                send.assert_not_called()

    def test_insert_delay_undo_redo_and_save(self):
        dialog = test_key_sequence_history.KeySequenceHistoryTests().make_dialog()
        dialog.insert_delay_enabled, dialog.insert_delay_ms = FakeVar(True), FakeVar('500')
        dialog.keys_entry.insert.side_effect = lambda index, token: dialog.keys.set('a' + token + 'b')
        dialog.insert_sequence_delay()
        self.assertEqual(dialog.keys.get(), 'a[延时:500]b')
        dialog.undo_sequence()
        self.assertEqual(dialog.keys.get(), 'ab')
        dialog.redo_sequence()
        dialog._source = dict(action_id='stable')
        dialog.interval, dialog.cycle_interval = FakeVar('50'), FakeVar('20')
        dialog.interval_enabled = FakeVar(True)
        dialog.cycle_interval_enabled, dialog.destroy = FakeVar(False), Mock()
        with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
            dialog.save()
        notice.assert_not_called()
        self.assertEqual(dialog.result['keys'], 'a[延时:500]b')
