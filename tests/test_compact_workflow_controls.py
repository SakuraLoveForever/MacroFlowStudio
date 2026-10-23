"""Compact workflow controls and script menu, without creating windows."""
from unittest import TestCase
from unittest.mock import Mock, patch

from macroflow.ui.app.main import MacroFlowApp


class CompactWorkflowControlsTests(TestCase):
    def test_selected_source_dispatches_to_existing_add_or_insert_behavior(self):
        for index in range(3):
            with self.subTest(index=index):
                app = MacroFlowApp.__new__(MacroFlowApp)
                app.workflow_add_kind_combo = Mock()
                app.workflow_add_kind_combo.current.return_value = index
                handlers = [Mock(), Mock(), Mock()]
                app.add_current_script_step, app.add_script_step, app.add_workflow_module_step = handlers
                app._add_selected_workflow_step()
                for position, handler in enumerate(handlers):
                    self.assertEqual(handler.call_count, int(position == index))

    def test_script_tools_are_visible_and_insert_choices_default_to_reference(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._script_category_dir = Mock()
        app.open_folder = Mock()
        for name in ('close_script', 'open_new_window', 'import_portable_bundle',
                     'export_portable_bundle', '_configure_script_directories',
                     'open_template_region_manager', '_insert_selected_script'):
            setattr(app, name, Mock())
        with patch('macroflow.ui.app.shell.ttk.Frame'), \
                patch('macroflow.ui.app.shell.ttk.Label'), \
                patch('macroflow.ui.app.shell.ttk.Combobox') as combo, \
                patch('macroflow.ui.app.shell.ttk.Button') as button:
            app._build_script_tools(Mock())
        combo.assert_called_once()
        self.assertEqual(combo.call_args.kwargs['values'], ('引用脚本', '逐行插入', '指定行插入'))
        combo.return_value.current.assert_called_once_with(0)
        combo.return_value.pack.assert_called_once()
        labels = [entry.kwargs['text'] for entry in button.call_args_list]
        self.assertEqual(labels, ['关闭', '新开窗口', '导入包', '导出脚本',
                                  '脚本目录', '目录设置', '模块管理', '插入'])
        self.assertEqual(button.return_value.pack.call_count, len(labels))
        for entry in button.call_args_list:
            entry.kwargs['command']()
        app.close_script.assert_called_once()
        app.open_new_window.assert_called_once()
        app.import_portable_bundle.assert_called_once()
        app.export_portable_bundle.assert_called_once()
        app.open_folder.assert_called_once_with(app._script_category_dir.return_value)
        app._configure_script_directories.assert_called_once()
        app.open_template_region_manager.assert_called_once()
        app._insert_selected_script.assert_called_once()

    def test_script_insert_choice_dispatches_all_three_modes(self):
        for index in range(3):
            with self.subTest(index=index):
                app = MacroFlowApp.__new__(MacroFlowApp)
                app.script_insert_kind_combo = Mock()
                app.script_insert_kind_combo.current.return_value = index
                app._insert_script = Mock()
                app._insert_script_range = Mock()
                app._insert_selected_script()
                if index == 2:
                    app._insert_script_range.assert_called_once_with()
                    app._insert_script.assert_not_called()
                else:
                    app._insert_script.assert_called_once_with(index == 1)
                    app._insert_script_range.assert_not_called()
