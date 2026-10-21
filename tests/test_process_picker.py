"""Mouse process selection, verified without creating windows or sending input."""
import unittest
from unittest.mock import Mock, patch

from macroflow.ui.dialogs import actions, screen_pickers, module_objects
from macroflow.ui.dialogs import process_names
from tests.helpers.core import FakeVar


class ProcessPickerTests(unittest.TestCase):
    def test_library_captures_name_only_on_mouse_confirmation(self):
        form = process_names.ProcessLibraryDialog.__new__(process_names.ProcessLibraryDialog)
        form.master = Mock()
        form.name = FakeVar("")
        with patch.object(process_names, 'ScreenProcessPicker') as constructor, \
                patch.object(process_names, 'app_windows', return_value=[form.master]):
            form._pick()
        self.assertEqual(form.name.get(), "")
        constructor.call_args.args[2]('selected.exe')
        self.assertEqual(form.name.get(), 'selected.exe')

    def test_open_application_picker_fills_path_only_on_confirmation(self):
        form = actions.OpenAppDialog.__new__(actions.OpenAppDialog)
        form.master = Mock()
        form.path = Mock()
        with patch.object(actions, 'ScreenProcessPicker') as constructor, \
             patch.object(actions, 'app_windows', return_value=[form.master]):
            form._pick_application()
        self.assertTrue(constructor.call_args.kwargs['full_path'])
        form.path.set.assert_not_called()
        constructor.call_args.args[2](r'C:\Apps\target.exe')
        form.path.set.assert_called_once_with(r'C:\Apps\target.exe')
        constructor.return_value.start.assert_called_once()

    def test_shared_selector_changes_target_only_when_library_confirms(self):
        picker = process_names.ProcessNamePicker.__new__(process_names.ProcessNamePicker)
        picker.variable = FakeVar("original.exe")
        picker.winfo_toplevel = Mock()
        picker.refresh = Mock()
        with patch.object(process_names, 'ProcessLibraryDialog') as dialog:
            dialog.return_value.show.return_value = None
            picker._open_library()
            self.assertEqual(picker.variable.get(), 'original.exe')
            dialog.return_value.show.return_value = 'target.exe'
            picker._open_library()
        self.assertEqual(picker.variable.get(), 'target.exe')
