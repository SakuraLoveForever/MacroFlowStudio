import unittest
from unittest.mock import Mock, patch

from tests import test_recorded_step_editor as fixtures


class RouteInsertPositionTests(unittest.TestCase):
    def dialog(self, kind='input', index=0):
        dialog = fixtures.route_dialog()
        dialog.inputs_list, dialog.marks_list = Mock(), Mock()
        dialog.marks_list.curselection.return_value = ()
        dialog.reload()
        dialog.inputs_list.curselection.return_value = (dialog.input_rows.index((kind, index)),)
        return dialog

    def test_key_is_inserted_immediately_below_selected_row_with_500ms_default(self):
        dialog = self.dialog()
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as editor:
            editor.return_value.show.side_effect = lambda: editor.call_args.args[1]
            dialog._insert_step('key')
        self.assertEqual(editor.call_args.kwargs['delay_ms'], 500)
        self.assertEqual([step['recorded_at_ms'] for step in dialog.inputs],
                         [0, 500, 600, 1400, 1600, 1650, 1750])
        self.assertEqual(dialog.input_rows[2], ('input', 1))
        dialog.save()
        self.assertIsNotNone(dialog.result)

    def test_inserting_after_correction_does_not_move_the_selected_mark(self):
        dialog = self.dialog('mark', 1)
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as editor:
            editor.return_value.show.side_effect = lambda: editor.call_args.args[1]
            dialog._insert_step('mouse_button')
        row = dialog.input_rows.index(('mark', 1))
        self.assertEqual(dialog.input_rows[row + 1], ('input', 3))
        self.assertEqual(dialog.marks[1]['gap_ms'], 1000)
        self.assertEqual(dialog.inputs[3]['recorded_at_ms'], 1500)
        dialog.save()
        self.assertIsNotNone(dialog.result)

    def test_delay_is_after_current_row_and_defaults_to_500ms(self):
        dialog = self.dialog()
        with patch('macroflow.ui.dialogs.recorded_steps.DelayInsertDialog') as editor, \
                patch('macroflow.ui.dialogs.recorded_steps.show_floating_notice'):
            editor.return_value.show.return_value = {'duration_ms': 500}
            dialog._insert_delay()
        self.assertNotIn('at_ms', editor.call_args.kwargs)
        self.assertEqual(editor.call_args.kwargs['duration_ms'], 500)
        self.assertEqual(dialog.inputs[0]['recorded_at_ms'], 0)
        self.assertEqual(dialog.inputs[1]['recorded_at_ms'], 600)
        dialog.save()
        self.assertIsNotNone(dialog.result)

    def test_regular_recording_inserts_below_current_and_moves_later_inputs(self):
        dialog = fixtures.RecordedInputInsertTests().dialog([
            {'type': 'key', 'recorded_at_ms': 100},
            {'type': 'key', 'recorded_at_ms': 110}], selection=(0,))
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as editor:
            editor.return_value.show.side_effect = lambda: editor.call_args.args[1]
            dialog._insert_step('key')
        self.assertEqual([step['recorded_at_ms'] for step in dialog.steps], [100, 600, 610])

    def test_no_selection_appends_to_route_end(self):
        dialog = self.dialog()
        dialog.inputs_list.curselection.return_value = ()
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as editor:
            editor.return_value.show.side_effect = lambda: editor.call_args.args[1]
            dialog._insert_step('key')
        self.assertEqual(dialog.inputs[-1]['recorded_at_ms'], 1800)
        dialog.save()
        self.assertIsNotNone(dialog.result)


if __name__ == '__main__':
    unittest.main()
