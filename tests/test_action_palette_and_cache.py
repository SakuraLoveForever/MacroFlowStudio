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
    def test_palette_creates_categories_and_wires_every_action_button(self):
        from macroflow.ui.app.action_palette import ACTION_CATEGORIES
        app = MacroFlowApp.__new__(MacroFlowApp)
        specs = app._script_action_button_specs()
        callbacks = {}
        for _label, command, _style in specs:
            callbacks[command] = Mock()
            setattr(app, command, callbacks[command])
        with patch('macroflow.ui.app.shell.ttk.Notebook') as notebook, \
                patch('macroflow.ui.app.shell.ttk.Frame'), \
                patch('macroflow.ui.app.shell.ttk.Button') as button:
            app._build_action_palette(Mock())
        self.assertEqual([call.kwargs['text'] for call in notebook.return_value.add.call_args_list],
                         [item[0] for item in ACTION_CATEGORIES])
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

    def test_width_fills_row_and_only_overflow_is_collapsed(self):
        from macroflow.ui.app.action_palette import action_button_positions
        self.assertEqual(action_button_positions(212, [50, 50, 50, 50], 40, False, gap=4),
                         ([(0, 0), (54, 0), (108, 0), (162, 0)], None))
        self.assertEqual(action_button_positions(211, [50, 50, 50, 50], 40, False, gap=4),
                         ([(0, 0), (54, 0), (108, 0), None], (162, 0)))
        self.assertEqual(action_button_positions(120, [50, 50, 50, 50], 40, True, gap=4),
                         ([(0, 0), (0, 1), (54, 1), (0, 2)], (54, 0)))
        self.assertEqual(action_button_positions(40, [50], 40, False), ([None], (0, 0)))

    def test_expansion_and_resize_relayout_buttons(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.action_palette = Mock()
        tab = Mock(action_expanded=False, action_layout=None)
        tab.winfo_width.return_value = 200
        tab.action_buttons = [Mock() for _ in range(4)]
        for button in (*tab.action_buttons, tab.action_toggle):
            button.winfo_reqwidth.return_value = 50
            button.winfo_reqheight.return_value = 25
        app._layout_action_category(tab)
        tab.action_buttons[-1].place_forget.assert_called_once()
        app._toggle_action_category(tab)
        tab.action_buttons[-1].place.assert_called_once()
        tab.action_toggle.configure.assert_called_with(text='收起 ▴')
        tab.winfo_width.return_value = 1000
        app._layout_action_category(tab)
        tab.action_toggle.place_forget.assert_called_once()
        for button in tab.action_buttons:
            self.assertEqual(button.place.call_args.kwargs['y'], tab.action_buttons[0].place.call_args.kwargs['y'])
