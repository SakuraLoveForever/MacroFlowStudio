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

    def test_menu_omits_header_actions_and_ends_with_three_script_insert_choices(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.script_more_menu_button = Mock()
        app._popup_menu = Mock()
        with patch('macroflow.ui.app.shell.tk.Menu') as menu_class:
            app._show_script_more_menu()
        commands = menu_class.return_value.add_command.call_args_list
        labels = [command.kwargs['label'] for command in commands]
        self.assertNotIn('＋ 新建', labels)
        self.assertNotIn('📂 打开', labels)
        self.assertEqual(labels[-3:], [
            '⇥ 引用脚本（实时读取）', '⇥ 逐行插入脚本', '⇥ 插入脚本指定行…',
        ])
