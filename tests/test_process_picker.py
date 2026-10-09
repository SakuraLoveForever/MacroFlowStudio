"""Mouse process selection, verified without creating windows or sending input."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from macroflow.input.wininput import WindowInfo
from macroflow.ui.dialogs import actions, screen_pickers, module_objects


class ProcessPickerTests(unittest.TestCase):
    def picker(self):
        picker = screen_pickers.ScreenProcessPicker(Mock(), Mock(), Mock())
        picker.windows = [
            WindowInfo(1, 'Front', 'app', r'C:\Apps\front.exe', (-100, 0, 200, 100)),
            WindowInfo(2, 'Behind', 'app', r'C:\Apps\back.exe', (-100, 0, 400, 200)),
        ]
        picker.canvas = Mock()
        picker.tip_id = 1
        picker.close = Mock()
        return picker

    def test_hover_previews_frontmost_process_without_confirming(self):
        picker = self.picker()
        picker._drag_move(SimpleNamespace(x_root=-50, y_root=25))
        self.assertIn('front.exe', picker.canvas.itemconfigure.call_args.kwargs['text'])
        picker.on_result.assert_not_called()
        picker.close.assert_not_called()

    def test_click_recomputes_target_and_confirms_once(self):
        picker = self.picker()
        picker._drag_move(SimpleNamespace(x_root=-50, y_root=25))
        picker._drag_begin(SimpleNamespace(x_root=150, y_root=25))
        picker.on_result.assert_called_once_with('back.exe')
        picker.close.assert_called_once()

    def test_unreadable_front_window_never_selects_window_behind_it(self):
        picker = self.picker()
        picker.windows[0].process_path = ''
        picker._drag_begin(SimpleNamespace(x_root=-50, y_root=25))
        picker.on_result.assert_not_called()
        picker.close.assert_not_called()

    def test_click_outside_windows_does_not_confirm(self):
        picker = self.picker()
        picker._drag_begin(SimpleNamespace(x_root=500, y_root=300))
        picker.on_result.assert_not_called()
        picker.close.assert_not_called()

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

    def test_window_enumeration_failure_restores_hidden_windows(self):
        picker = self.picker()
        with patch.object(screen_pickers, 'enum_windows', side_effect=OSError('denied')), \
             patch.object(screen_pickers, 'show_floating_notice') as notice:
            picker._show_overlay()
        picker.close.assert_called_once()
        notice.assert_called_once()

    def test_application_picker_returns_full_executable_path(self):
        picker = screen_pickers.ScreenProcessPicker(Mock(), Mock(), Mock(), full_path=True)
        picker.windows = [WindowInfo(1, 'App', 'app', r'C:\Program Files\App\app.exe', (0, 0, 100, 100))]
        picker.close = Mock()
        picker._drag_begin(SimpleNamespace(x_root=20, y_root=20))
        picker.on_result.assert_called_once_with(r'C:\Program Files\App\app.exe')

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
