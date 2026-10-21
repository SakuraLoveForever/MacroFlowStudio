"""Recorded input controls, tested without constructing a window."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from tests.test_recorded_step_editor import step_dialog
from tests.helpers.core import FakeVar
from macroflow.ui.dialogs import recorded_steps as editor


class RecordedStepControlTests(unittest.TestCase):
    def test_editor_builds_capture_arrow_picker_and_ms_s_picker(self):
        master = tk.Tcl()
        string_var = tk.StringVar
        duration = editor.milliseconds_var
        with patch.object(editor.ModalDialog, '__init__', return_value=None), \
                patch.object(editor.tk, 'StringVar', side_effect=lambda value='', **kw: string_var(master=master, value=value)), \
                patch.object(editor, 'milliseconds_var', side_effect=lambda value, owner: duration(value, master)), \
                patch.object(editor.ttk, 'Frame'), patch.object(editor.ttk, 'Label'), \
                patch.object(editor.ttk, 'Button') as buttons, \
                patch.object(editor.ttk, 'Entry') as entries, \
                patch.object(editor.ttk, 'Combobox') as combos:
            dialog = editor.RecordedStepDialog(Mock(), {
                'type': 'key', 'vk': 87, 'down': True, 'recorded_at_ms': 1969.409},
                delay_ms=1969.409)
        self.assertEqual(dialog.fields['down'].get(), '↓')
        self.assertEqual(dialog.fields['step_delay_ms']._raw(), '1969')
        self.assertEqual([call.kwargs['values'] for call in combos.call_args_list],
                         [('↓', '↑'), ('ms', 's')])
        self.assertTrue(all(call.kwargs['state'] == 'readonly' for call in combos.call_args_list))
        self.assertEqual(entries.call_args_list[0].kwargs['state'], 'readonly')
        self.assertTrue(any(call.kwargs.get('command') == dialog.start_capture
                            for call in buttons.call_args_list))

    def test_arrow_only_states_save_keyboard_and_mouse_direction(self):
        for kind in ('key', 'mouse_button'):
            for symbol, down in (('↓', True), ('↑', False)):
                with self.subTest(kind=kind, symbol=symbol):
                    fields = {'key': 'SPACE', 'button': '鼠标左键', 'down': symbol}
                    dialog = step_dialog(kind, fields)
                    dialog.save()
                    self.assertIs(dialog.result['down'], down)

    def test_default_milliseconds_have_no_decimal_and_seconds_keep_duration(self):
        value = editor.milliseconds_var(1969.409, master=tk.Tcl())
        self.assertEqual(value.unit.get(), 'ms')
        self.assertEqual(value._raw(), '1969')
        value.unit.set('s')
        self.assertEqual(value._raw(), '1.969')
        self.assertEqual(value.get(), '1969')
        value.unit.set('ms')
        self.assertEqual(value._raw(), '1969')

    def capture_dialog(self):
        dialog = editor.RecordedStepDialog.__new__(editor.RecordedStepDialog)
        dialog.capturer = None
        dialog.fields = {'key': FakeVar('W')}
        dialog.capture_button = Mock()
        dialog.capture_hint = FakeVar('')
        dialog.after = lambda delay, callback, *args: callback(*args)
        return dialog

    def test_detected_physical_key_updates_the_field_and_releases_hook(self):
        dialog = self.capture_dialog()
        with patch.object(editor, 'KeyCapturer') as factory:
            factory.return_value.start.return_value = True
            dialog.start_capture()
            factory.call_args.kwargs['on_key'](0x20)
        self.assertEqual(dialog.fields['key'].get(), 'SPACE')
        factory.return_value.stop.assert_called_once()
        self.assertIsNone(dialog.capturer)

    def test_failed_capture_does_not_leave_a_hook_or_disabled_button(self):
        dialog = self.capture_dialog()
        with patch.object(editor, 'KeyCapturer') as factory, \
                patch.object(editor, 'show_floating_notice') as notice:
            factory.return_value.start.return_value = False
            dialog.start_capture()
        self.assertIsNone(dialog.capturer)
        dialog.capture_button.configure.assert_called_with(state='normal')
        notice.assert_called_once()

    def test_closing_editor_stops_key_capture(self):
        dialog = self.capture_dialog()
        hook = dialog.capturer = Mock()
        with patch.object(editor.ModalDialog, 'destroy'):
            dialog.destroy()
        hook.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
