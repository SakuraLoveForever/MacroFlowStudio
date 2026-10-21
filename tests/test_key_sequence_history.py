"""Sequence edit history using a Tcl interpreter, without loading Tk or opening windows."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from macroflow.ui.dialogs.actions import KeySequenceDialog
from tests.helpers.core import FakeVar


class KeySequenceHistoryTests(unittest.TestCase):
    def make_dialog(self, keys='ab'):
        dialog = KeySequenceDialog.__new__(KeySequenceDialog)
        dialog.keys = tk.StringVar(master=tk.Tcl(), value=keys)
        dialog.keys_entry = Mock()
        dialog.undo_button, dialog.redo_button = Mock(), Mock()
        dialog._init_sequence_history()
        return dialog

    def test_manual_edits_can_be_undone_and_redone(self):
        dialog = self.make_dialog()
        dialog.keys.set('abc')
        dialog.keys.set('abcd')
        self.assertEqual(dialog.undo_sequence(), 'break')
        self.assertEqual(dialog.keys.get(), 'abc')
        dialog.undo_sequence()
        self.assertEqual(dialog.keys.get(), 'ab')
        dialog.redo_sequence()
        self.assertEqual(dialog.keys.get(), 'abc')
        dialog.redo_sequence()
        self.assertEqual(dialog.keys.get(), 'abcd')

    def test_new_edit_after_undo_discards_redo(self):
        dialog = self.make_dialog()
        dialog.keys.set('abc')
        dialog.undo_sequence()
        dialog.keys.set('ab[左键]')
        dialog.redo_sequence()
        self.assertEqual(dialog.keys.get(), 'ab[左键]')
        dialog.redo_button.configure.assert_called_with(state='disabled')

    def test_inserted_mouse_token_is_one_undo_step(self):
        dialog = self.make_dialog()
        dialog.mouse_action, dialog.mouse_count = FakeVar('滚轮下'), FakeVar('3')
        dialog.keys_entry.insert.side_effect = lambda index, value: dialog.keys.set('a' + value + 'b')
        dialog.insert_mouse_action()
        self.assertEqual(dialog.keys.get(), 'a[滚轮下:3]b')
        dialog.undo_sequence()
        self.assertEqual(dialog.keys.get(), 'ab')
        dialog.redo_sequence()
        self.assertEqual(dialog.keys.get(), 'a[滚轮下:3]b')

    def test_empty_history_and_duplicate_writes_are_noops(self):
        dialog = self.make_dialog()
        dialog.keys.set('ab')
        dialog.undo_sequence()
        dialog.redo_sequence()
        self.assertEqual(dialog.keys.get(), 'ab')
        dialog.undo_button.configure.assert_called_with(state='disabled')
        dialog.redo_button.configure.assert_called_with(state='disabled')

    def test_save_uses_restored_sequence(self):
        dialog = self.make_dialog()
        dialog._source = dict(action_id='stable')
        dialog.interval, dialog.cycle_interval = FakeVar('50'), FakeVar('20')
        dialog.interval_enabled = FakeVar(True)
        dialog.cycle_interval_enabled = FakeVar(False)
        dialog.destroy = Mock()
        dialog.keys.set('ab[中键]')
        dialog.undo_sequence()
        with patch('macroflow.ui.dialogs.actions.show_floating_notice') as notice:
            dialog.save()
        notice.assert_not_called()
        self.assertEqual(dialog.result['keys'], 'ab')
