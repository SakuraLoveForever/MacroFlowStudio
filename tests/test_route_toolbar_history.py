"""Route editing history and deletion selection, without opening Tk windows."""
import copy
import unittest
from unittest.mock import Mock, patch

from tests.test_recorded_step_editor import route_dialog
from tests.helpers.ui import make_edit_app


class RouteToolbarHistoryTests(unittest.TestCase):
    def dialog(self):
        dialog = route_dialog()
        dialog.marks_list, dialog.inputs_list = Mock(), Mock()
        dialog.marks_list.curselection.return_value = ()
        dialog.inputs_list.curselection.return_value = ()
        dialog.reload()
        return dialog

    def state(self, dialog):
        return copy.deepcopy((dialog.marks, dialog.inputs, dialog.trajectory, dialog.duration))

    def test_deleted_input_selects_previous_displayed_row(self):
        dialog = self.dialog()
        row = dialog.input_rows.index(('input', 2))
        dialog.inputs_list.curselection.return_value = (row,)
        dialog._remove_input()
        dialog.inputs_list.selection_set.assert_called_with(row - 1)
        dialog.inputs_list.see.assert_called_with(row - 1)

    def test_delete_multiple_rows_including_marks_is_one_undo_step(self):
        dialog = self.dialog()
        original = self.state(dialog)
        rows = (1, 2, 3, 4)
        selected = [dialog.input_rows[row] for row in rows]
        expected_inputs = [step for i, step in enumerate(dialog.inputs) if ('input', i) not in selected]
        expected_marks = [mark for i, mark in enumerate(dialog.marks) if ('mark', i) not in selected]
        dialog.inputs_list.curselection.return_value = rows
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog._remove_input()
        notice.assert_not_called()
        self.assertEqual(dialog.inputs, expected_inputs)
        self.assertEqual(dialog.marks, expected_marks)
        self.assertEqual(len(dialog._undo_stack), 1)
        dialog.inputs_list.selection_set.assert_called_with(0)
        deleted = self.state(dialog)
        dialog._undo()
        self.assertEqual(self.state(dialog), original)
        dialog._redo()
        self.assertEqual(self.state(dialog), deleted)

    def test_delete_noncontiguous_rows_and_all_rows(self):
        for all_rows in (False, True):
            with self.subTest(all_rows=all_rows):
                dialog = self.dialog()
                original_rows = list(dialog.input_rows)
                rows = tuple(range(len(original_rows))) if all_rows else (1, 3, 5)
                selected = [original_rows[row] for row in rows]
                original_inputs = copy.deepcopy(dialog.inputs)
                original_marks = copy.deepcopy(dialog.marks)
                dialog.inputs_list.curselection.return_value = rows
                dialog._remove_input()
                self.assertEqual(dialog.inputs, [step for i, step in enumerate(original_inputs)
                                                if ('input', i) not in selected])
                self.assertEqual(dialog.marks, [mark for i, mark in enumerate(original_marks)
                                               if ('mark', i) not in selected])
                if all_rows:
                    self.assertEqual(dialog.input_rows, [])
                    dialog.inputs_list.selection_set.assert_not_called()

    def test_delete_multiple_correction_points_is_one_undo_step(self):
        dialog = self.dialog()
        original = self.state(dialog)
        dialog.marks_list.curselection.return_value = (0, 1)
        dialog._remove_mark()
        self.assertEqual(dialog.marks, [])
        self.assertEqual(dialog.inputs, original[1])
        self.assertEqual(len(dialog._undo_stack), 1)
        dialog._undo()
        self.assertEqual(self.state(dialog), original)

    def test_delete_without_selection_does_not_create_history(self):
        dialog = self.dialog()
        original = self.state(dialog)
        with patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice') as notice:
            dialog._remove_input()
            dialog._remove_mark()
        self.assertEqual(notice.call_count, 2)
        self.assertEqual(self.state(dialog), original)
        self.assertEqual(dialog._undo_stack, [])

    def test_deleting_first_row_selects_next_and_empty_list_selects_nothing(self):
        dialog = self.dialog()
        dialog.inputs_list.curselection.return_value = (0,)
        dialog._remove_input()
        dialog.inputs_list.selection_set.assert_called_with(0)
        dialog.marks = []
        dialog.inputs = [dialog.inputs[0]]
        dialog.reload()
        dialog.inputs_list.selection_set.reset_mock()
        dialog._remove_input()
        self.assertEqual(dialog.input_rows, [])
        dialog.inputs_list.selection_set.assert_not_called()

    def test_delete_mark_can_be_undone_and_redone_with_selection(self):
        dialog = self.dialog()
        original = self.state(dialog)
        dialog.marks_list.curselection.return_value = (1,)
        dialog._remove_mark()
        dialog.marks_list.selection_set.assert_called_with(0)
        deleted = self.state(dialog)
        dialog._undo()
        self.assertEqual(self.state(dialog), original)
        dialog.marks_list.selection_set.assert_called_with(1)
        dialog._redo()
        self.assertEqual(self.state(dialog), deleted)

    def test_delay_undo_restores_the_entire_timeline_and_new_edit_discards_redo(self):
        dialog = self.dialog()
        original = self.state(dialog)
        dialog.inputs_list.curselection.return_value = (1,)
        with patch('macroflow.ui.dialogs.recorded_steps.DelayInsertDialog') as editor, \
                patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice'):
            editor.return_value.show.return_value = {'duration_ms': 500}
            dialog._insert_delay()
        delayed = self.state(dialog)
        self.assertNotEqual(delayed, original)
        dialog._undo()
        self.assertEqual(self.state(dialog), original)
        dialog._redo()
        self.assertEqual(self.state(dialog), delayed)
        dialog._undo()
        dialog._remove_input()
        deleted = self.state(dialog)
        dialog._redo()
        self.assertEqual(self.state(dialog), deleted)

    def test_add_edit_move_and_script_insert_each_form_one_undo_step(self):
        for operation in ('add_mark', 'edit_mark', 'move_mark', 'edit_input',
                          'edit_script', 'move_input', 'insert_input'):
            with self.subTest(operation=operation):
                dialog = self.dialog()
                dialog.marks_list.curselection.return_value = (1,)
                dialog.inputs_list.curselection.return_value = (3,)
                if operation == 'edit_script':
                    dialog.inputs[2] = dict(dialog.inputs[2], route_script_action=True)
                original = self.state(dialog)
                with patch('macroflow.ui.dialogs.recorded_steps.RouteMarkDialog') as mark_editor, \
                        patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as step_editor, \
                        patch('macroflow.ui.dialogs.actions.edit_action', return_value={'type': 'comment', 'text': 'new'}):
                    mark_editor.return_value.show.return_value = dict(dialog.marks[1], yaw=45)
                    step_editor.return_value.show.return_value = dict(dialog.inputs[2], vk=65)
                    if operation == 'add_mark':
                        dialog._add_mark()
                    elif operation == 'edit_mark':
                        dialog._edit_mark()
                    elif operation == 'move_mark':
                        dialog._move(dialog.marks_list, dialog.marks, -1)
                    elif operation in ('edit_input', 'edit_script'):
                        dialog._edit_input()
                    elif operation == 'move_input':
                        dialog._move_input(-1)
                    else:
                        dialog._place_input({'type': 'comment', 'text': 'new',
                                             'route_script_action': True, 'recorded_at_ms': 1400})
                edited = self.state(dialog)
                self.assertNotEqual(edited, original)
                dialog._undo()
                self.assertEqual(self.state(dialog), original)
                dialog._redo()
                self.assertEqual(self.state(dialog), edited)

    def test_empty_history_and_cancelled_edits_do_not_change_data(self):
        dialog = self.dialog()
        original = self.state(dialog)
        dialog._undo()
        dialog._redo()
        with patch('macroflow.ui.dialogs.recorded_steps.RouteMarkDialog') as editor:
            editor.return_value.show.return_value = None
            dialog._add_mark()
        self.assertEqual(self.state(dialog), original)
        self.assertEqual(dialog._undo_stack, [])

    def test_script_delete_prefers_previous_row_and_first_row_uses_next(self):
        for deleted, expected in [(1, '0'), (0, '0'), (2, '1')]:
            with self.subTest(deleted=deleted):
                app = make_edit_app(actions=[{'type': 'comment', 'text': text} for text in 'ABC'])
                app.rebuild_action_tree()
                app.action_tree.selection_set(str(deleted))
                app.delete_actions()
                self.assertEqual(app.action_tree.selection(), (expected,))
                self.assertEqual(app.action_tree.focus(), expected)
