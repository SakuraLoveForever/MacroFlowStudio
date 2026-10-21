"""Headless checks for route delays, correction rows and embedded game windows."""
import unittest
from unittest.mock import Mock, patch

from tests.test_recorded_step_editor import route_dialog, step_dialog
from tests import test_ssjj_navigation as navigation


class RouteEditorRegressions(unittest.TestCase):
    def test_letter_editor_uses_a_character_for_the_windows_key_mapping(self):
        from macroflow.ui.dialogs.base import key_to_vk
        import ctypes
        with patch('macroflow.ui.dialogs.base.ctypes.windll.user32.VkKeyScanW', return_value=87) as scan:
            self.assertEqual(key_to_vk('W'), (87, 'W'))
        self.assertIsInstance(scan.call_args.args[0], ctypes.c_wchar)
        self.assertEqual(scan.call_args.args[0].value, 'W')

    def dialog(self):
        dialog = route_dialog()
        dialog.marks_list = Mock()
        dialog.inputs_list = Mock()
        dialog.marks_list.curselection.return_value = ()
        dialog.inputs_list.curselection.return_value = ()
        dialog.reload()
        return dialog

    def test_delay_editor_returns_relative_change_without_saving_editor_metadata(self):
        dialog = step_dialog('key', {'key': 'SPACE', 'down': '松开', 'step_delay_ms': '200'},
                             {'type': 'key', 'vk': 87, 'down': False, 'recorded_at_ms': 900})
        dialog._step_delay_ms = 800
        dialog.save()
        self.assertEqual(dialog.result['recorded_at_ms'], 300)
        self.assertNotIn('step_delay_ms', dialog.result)

    def test_delay_change_moves_following_inputs_marks_and_samples(self):
        dialog = self.dialog()
        row = dialog.input_rows.index(('input', 2))
        dialog.inputs_list.curselection.return_value = (row,)
        with patch('macroflow.ui.dialogs.recorded_steps.RecordedStepDialog') as editor:
            editor.return_value.show.return_value = dict(dialog.inputs[2], recorded_at_ms=300)
            dialog._edit_input()
        self.assertEqual(editor.call_args.kwargs['delay_ms'], 800)
        self.assertEqual([s['recorded_at_ms'] for s in dialog.inputs], [0, 100, 300, 500, 550, 650])
        self.assertEqual(dialog.marks[1]['gap_ms'], 400)
        self.assertEqual(dialog.duration, 700)
        dialog.save()
        self.assertIsNotNone(dialog.result)

    def test_correction_rows_have_distinct_color_and_click_scrolls_to_exact_row(self):
        dialog = self.dialog()
        row = dialog.input_rows.index(('mark', 1))
        self.assertEqual(dialog.input_rows[0], ('mark', 0))
        self.assertEqual(dialog.input_rows[row + 1], ('input', 3))
        self.assertEqual(dialog.inputs_list.itemconfig.call_count, 2)
        color = dialog.inputs_list.itemconfig.call_args.kwargs['foreground']
        from macroflow.ui.dialogs.base import COLOR_TEXT
        self.assertNotEqual(color, COLOR_TEXT)
        dialog.marks_list.curselection.return_value = (1,)
        dialog._jump_to_mark()
        dialog.inputs_list.selection_set.assert_called_with(row)
        dialog.inputs_list.see.assert_called_with(row)

    def test_correction_row_double_click_edits_mark_instead_of_input(self):
        dialog = self.dialog()
        dialog.inputs_list.curselection.return_value = (dialog.input_rows.index(('mark', 1)),)
        with patch('macroflow.ui.dialogs.recorded_steps.RouteMarkDialog') as editor:
            editor.return_value.show.return_value = None
            dialog._edit_input()
        self.assertEqual(editor.call_args.args[1], dialog.marks[1])

    def test_zero_delay_preserves_start_sample_and_does_not_move_earlier_tied_input(self):
        dialog = self.dialog()
        dialog._retime_delay(2, 100, 900, -800)
        self.assertEqual([s['recorded_at_ms'] for s in dialog.inputs], [0, 100, 100, 300, 350, 450])
        dialog.save()
        self.assertIsNotNone(dialog.result)
        dialog = self.dialog()
        start = dict(dialog.trajectory[0])
        dialog._retime_delay(2, 0, 900, -900)
        self.assertEqual(dialog.trajectory[0], start)
        self.assertEqual(dialog.marks[0]['gap_ms'], 0)

    def test_embedded_window_can_be_found_and_its_host_activated(self):
        reader = EmbeddedForegroundRegressions().reader()
        reader.user.GetWindowRect = lambda hwnd, rect: False
        window = navigation.ForegroundTests().window('MicroClient.exe')
        with patch('macroflow.input.wininput.enum_windows', return_value=[window]):
            self.assertEqual(reader.game_window(), window.hwnd)


class EmbeddedForegroundRegressions(unittest.TestCase):
    def reader(self):
        reader = navigation.ForegroundTests().reader({'pid': 7})
        def children(hwnd, callback, data):
            callback(99, data)
            return True
        reader.user.EnumChildWindows = children
        reader.user.IsWindowVisible = lambda hwnd: True
        reader.user.GetWindowThreadProcessId = lambda hwnd, ref: setattr(
            ref._obj, 'value', 42 if hwnd == 99 else 7)
        return reader

    def test_foreground_host_with_visible_battle_child_is_accepted_without_activation(self):
        reader = self.reader()
        reader.activate = Mock(return_value=False)
        reader.require_foreground(activate=False)
        reader.activate.assert_not_called()

    def test_hidden_battle_child_does_not_make_launcher_a_game_window(self):
        reader = self.reader()
        reader.user.IsWindowVisible = lambda hwnd: False
        with self.assertRaisesRegex(RuntimeError, '游戏不在前台'):
            reader.require_foreground(activate=False)


if __name__ == '__main__':
    unittest.main()
