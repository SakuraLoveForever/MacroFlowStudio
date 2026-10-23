import unittest
from unittest.mock import Mock, patch

from macroflow.ui.app.main import MacroFlowApp
from tests.helpers.ui import make_edit_app


class ModuleCacheTests(unittest.TestCase):
    key = 'module:be508de98d4c474b9eb0561f2f658d9f'

    def action(self):
        return {'type': 'image_match', 'module_ref': True, 'module_key': self.key}

    def snapshot(self, name='荣光号'):
        return {self.key: {'name': name, 'category': 'switch', 'template': 'ship.png'}}

    def test_insert_new_module_refreshes_snapshot_once(self):
        app = make_edit_app(actions=[{'type': 'delay', 'ms': 10}])
        with patch('macroflow.ui.app.scripts.module_objects_snapshot', side_effect=[{}, self.snapshot()]) as read:
            app.rebuild_action_tree()
            app._insert_action(self.action())
        detail = app.action_tree.value('1', 'detail')
        self.assertIn('荣光号', detail)
        self.assertNotIn('对象不存在', detail)
        self.assertEqual(read.call_count, 2)

    def test_single_row_refresh_reads_updated_module_name(self):
        app = make_edit_app(actions=[self.action()])
        with patch('macroflow.ui.app.scripts.module_objects_snapshot', side_effect=[{}, self.snapshot()]):
            app.rebuild_action_tree()
            app._refresh_one_action_row(0)
        self.assertIn('荣光号', app.action_tree.value('0', 'detail'))

    def test_manager_close_refreshes_existing_references(self):
        app = make_edit_app(actions=[self.action()])
        app.rebuild_workflow_tree = Mock()
        with patch('macroflow.ui.app.scripts.module_objects_snapshot', side_effect=[{}, self.snapshot()]), \
                patch('macroflow.ui.app.scripts.TemplateRegionManagerDialog'):
            app.rebuild_action_tree()
            app.open_template_region_manager()
        self.assertIn('荣光号', app.action_tree.value('0', 'detail'))


class ActionPaletteTests(unittest.TestCase):
    def test_palette_creates_segmented_categories_and_wires_every_action_button(self):
        from macroflow.ui.app.action_palette import ACTION_CATEGORIES
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        specs = app._script_action_button_specs()
        callbacks = {}
        for _label, command, _style in specs:
            callbacks[command] = Mock()
            setattr(app, command, callbacks[command])
        with patch('macroflow.ui.app.shell.SegmentedControl') as segmented, \
                patch('macroflow.ui.app.shell.FlowFrame') as flow_class, \
                patch('macroflow.ui.app.shell.ttk.Frame'), \
                patch('macroflow.ui.app.shell.ttk.Button') as button:
            app._build_action_palette(Mock())
        options = segmented.call_args.kwargs['options']
        self.assertEqual([label for label, _value in options],
                         [item[0] for item in ACTION_CATEGORIES])
        self.assertEqual(len(flow_class.call_args_list), len(ACTION_CATEGORIES))
        self.assertEqual(flow_class.call_args.kwargs['max_rows'], 1)
        for call in button.call_args_list:
            callback = call.kwargs.get('command')
            if callback is not None:
                callback()
        for callback in callbacks.values():
            callback.assert_called_once()

    def test_categories_cover_every_command_once_and_keep_few_primary_buttons(self):
        from macroflow.ui.app.action_palette import action_category_specs
        specs = MacroFlowApp._script_action_button_specs()
        categories = action_category_specs(specs)
        commands = [command for _label, primary, extra in categories
                    for _text, command, _style in (*primary, *extra)]
        self.assertEqual(set(commands), {item[1] for item in specs})
        self.assertEqual(len(commands), len(set(commands)))
        for _label, primary, extra in categories:
            self.assertTrue(1 <= len(primary) <= 3)
            self.assertTrue(extra)
        for command in commands:
            self.assertTrue(callable(getattr(MacroFlowApp, command)))

    def test_palette_stays_flat_without_notebook_widgets(self):
        import inspect
        from macroflow.ui.app import shell
        source = inspect.getsource(shell.ShellMixin._build_action_palette)
        self.assertIn('SegmentedControl', source)
        self.assertIn('FlowFrame', source)
        self.assertNotIn('ttk.Notebook', source)
