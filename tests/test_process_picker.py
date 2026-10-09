"""Mouse process selection, verified without creating windows or sending input."""
import unittest
from unittest.mock import Mock, patch

from macroflow.ui.dialogs import actions, screen_pickers, module_objects


class ProcessPickerTests(unittest.TestCase):
    def test_form_uses_shared_picker_and_only_changes_value_on_confirmation(self):
        form = module_objects.TemplateRegionFormDialog.__new__(module_objects.TemplateRegionFormDialog)
        form.master = Mock()
        form.process_name_var = Mock()
        form._ancestors_to_hide = Mock(return_value=[form.master])
        with patch.object(module_objects, 'ScreenProcessPicker') as constructor:
            form._pick_process()
        constructor.return_value.start.assert_called_once()
        form.process_name_var.set.assert_not_called()
        constructor.call_args.args[2]('selected.exe')
        form.process_name_var.set.assert_called_once_with('selected.exe')

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

    def test_close_application_picker_fills_process_name_only_on_confirmation(self):
        form = actions.CloseAppDialog.__new__(actions.CloseAppDialog)
        form.master = Mock()
        form.name = Mock()
        with patch.object(actions, 'ScreenProcessPicker') as constructor, \
             patch.object(actions, 'app_windows', return_value=[form.master]):
            form._pick_process()
        form.name.set.assert_not_called()
        constructor.call_args.args[2]('target.exe')
        form.name.set.assert_called_once_with('target.exe')
        constructor.return_value.start.assert_called_once()
