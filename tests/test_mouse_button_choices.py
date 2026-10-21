"""Mouse button editing and input packets, without windows or real input."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from macroflow.input import wininput
from macroflow.ui.dialogs import recorded_steps as editor
from macroflow.execution.ssjj_recording import validate_recording
from tests.test_recorded_step_editor import step_dialog
from tests.test_ssjj_waypoint_playback import recording


class MouseButtonChoiceTests(unittest.TestCase):
    def test_mouse_editor_uses_readonly_five_button_dropdown(self):
        master = tk.Tcl()
        string_var = tk.StringVar
        with patch.object(editor.ModalDialog, '__init__', return_value=None), \
                patch.object(editor.tk, 'StringVar', side_effect=lambda value='', **kw: string_var(master=master, value=value)), \
                patch.object(editor.ttk, 'Frame'), patch.object(editor.ttk, 'Label'), \
                patch.object(editor.ttk, 'Button'), patch.object(editor.ttk, 'Entry') as entries, \
                patch.object(editor.ttk, 'Combobox') as combos:
            dialog = editor.RecordedStepDialog(Mock(), {'type': 'mouse_button', 'button': 'x2', 'down': True})
        self.assertEqual(dialog.fields['button'].get(), '鼠标前侧键')
        self.assertEqual(combos.call_args_list[0].kwargs['values'],
                         ('鼠标左键', '鼠标右键', '鼠标中键', '鼠标前侧键', '鼠标后侧键'))
        self.assertEqual(combos.call_args_list[0].kwargs['state'], 'readonly')
        entries.assert_not_called()

    def test_all_five_choices_save_and_validate_as_route_inputs(self):
        for name, label in (('left', '鼠标左键'), ('right', '鼠标右键'), ('middle', '鼠标中键'),
                            ('x2', '鼠标前侧键'), ('x1', '鼠标后侧键')):
            for down in (False, True):
                with self.subTest(button=name, down=down):
                    dialog = step_dialog('mouse_button', {'button': label, 'down': '↓' if down else '↑'},
                                         {'type': 'mouse_button', 'recorded_at_ms': 0})
                    dialog.save()
                    self.assertEqual(dialog.result['button'], name)
                    self.assertEqual(dialog.result['down'], down)
                    action = recording()
                    action['inputs'] = [dialog.result]
                    validate_recording(action)

    def test_invalid_button_choice_is_rejected(self):
        dialog = step_dialog('mouse_button', {'button': '任意文字', 'down': '↓'})
        with patch.object(editor, 'show_floating_notice') as notice:
            dialog.save()
        self.assertIsNone(dialog.result)
        notice.assert_called_once()

    def test_side_button_packets_have_correct_button_data_flags_and_input_tag(self):
        for name, data in (('x1', 1), ('x2', 2)):
            for down, flags in ((True, 0x0080), (False, 0x0100)):
                with self.subTest(button=name, down=down), patch.object(wininput, '_send_input') as send:
                    wininput.send_button(name, down)
                    packet = send.call_args.args[0]
                    self.assertEqual(packet.mi.mouseData, data)
                    self.assertEqual(packet.mi.dwFlags, flags)
                    self.assertEqual(packet.mi.dwExtraInfo, wininput.MACROFLOW_INPUT_TAG)

    def test_side_button_rows_display_readable_labels(self):
        from macroflow.ui.dialogs.helpers import recorded_input_row_label
        for name, label in (('x1', '后侧键'), ('x2', '前侧键')):
            self.assertIn(label, recorded_input_row_label({'type': 'mouse_button', 'button': name, 'down': True}))
