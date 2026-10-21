import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from macroflow.core.models import MacroScript, Workflow
from macroflow.core.client_mode import is_runner
from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.app.runner import MacroFlowRunner


class BundleUiTests(unittest.TestCase):
    def app(self, cls=MacroFlowApp):
        app = cls.__new__(cls)
        app.root = Mock()
        app.worker = None
        app.recorder = Mock(running=False)
        app.dirty = False
        app._notify = Mock()
        app._set_status = Mock()
        app._log = Mock()
        app.script = MacroScript(name='test', actions=[{'type': 'delay', 'ms': 1}])
        app.script_path = Path('scripts/test.json')
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = '未保存的名称'
        app._current_script_settings = Mock(return_value={'record_mode': 'auto'})
        app.workflow = Workflow(name='flow')
        app.notebook = Mock()
        app.script_tab = Mock()
        app.workflow_tab = Mock()
        app.load_script_into_editor = Mock()
        app._switch_to_workflow = Mock()
        app.refresh_script_files = Mock()
        return app

    def test_export_current_unsaved_script_snapshot(self):
        app = self.app()
        with patch('macroflow.ui.app.bundles.filedialog.asksaveasfilename', return_value='out.zip'), \
                patch('macroflow.ui.app.bundles.export_bundle') as export:
            app.export_portable_bundle()
        script = export.call_args.kwargs['script']
        self.assertEqual(script.name, '未保存的名称')
        self.assertEqual(script.actions, app.script.actions)
        self.assertIsNot(script, app.script)
        self.assertEqual(app.script.name, 'test')

    def test_import_selects_script_or_workflow_without_running(self):
        for kind in ('script', 'workflow'):
            with self.subTest(kind=kind):
                app = self.app()
                with patch('macroflow.ui.app.bundles.filedialog.askopenfilename', return_value='in.zip'), \
                        patch('macroflow.ui.app.bundles.import_bundle', return_value=(kind, Path('entry.json'))), \
                        patch('macroflow.ui.app.bundles.load_workflow', return_value=app.workflow):
                    app.import_portable_bundle()
                if kind == 'script':
                    app.load_script_into_editor.assert_called_once_with(Path('entry.json'))
                    app._switch_to_workflow.assert_not_called()
                else:
                    app._switch_to_workflow.assert_called_once()
                    app.load_script_into_editor.assert_not_called()
                app._notify.assert_not_called()

    def test_import_is_blocked_while_playing(self):
        app = self.app()
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        with patch('macroflow.ui.app.bundles.filedialog.askopenfilename') as dialog:
            app.import_portable_bundle()
        dialog.assert_not_called()
        app._notify.assert_called_once()

    def test_runner_routing_and_f8_never_record(self):
        self.assertTrue(is_runner(['app', '--runner'], 'python.exe'))
        self.assertTrue(is_runner(['app'], 'C:/dist/MacroFlowRunner.exe'))
        self.assertFalse(is_runner(['app'], 'C:/dist/MacroFlowStudio.exe'))
        app = self.app(MacroFlowRunner)
        app.run_workflow = Mock()
        app.run_current_script = Mock()
        app.toggle_execution_pause = Mock()
        app.runner_kind = 'workflow'
        app._run_runner_selection()
        app.run_workflow.assert_called_once_with()
        app.runner_kind = 'script'
        app._run_runner_selection()
        app.run_current_script.assert_called_once_with()
        app.toggle_record()
        app.toggle_execution_pause.assert_called_once_with()

    def test_runner_view_builds_without_editor_widgets_or_tk_window(self):
        app = self.app(MacroFlowRunner)
        for name in ('repeat_var', 'focus_mode_enabled_var', 'activate_target_enabled_var',
                     'bind_label_var', 'activation_enabled_var', 'activation_interval_var', 'status_var'):
            setattr(app, name, Mock())
        app._build_log_tab = Mock()
        with ExitStack() as patches:
            for name in ('Frame', 'Notebook', 'Button', 'Treeview', 'Label', 'Spinbox',
                         'Checkbutton', 'Entry'):
                patches.enter_context(patch(f'macroflow.ui.app.runner.ttk.{name}'))
            patches.enter_context(patch('macroflow.ui.app.runner.tk.StringVar'))
            app._build_ui()
        self.assertIsNone(app.action_tree)
        self.assertIsNone(app.workflow_tree)
        app._build_log_tab.assert_called_once_with()

    def test_runner_rejects_busy_selection_and_restores_active_project(self):
        app = self.app(MacroFlowRunner)
        app.runner_kind = 'script'
        app.runner_library = Mock()
        app.runner_library.selection.return_value = ['new']
        app.runner_items = {'active': ('script', app.script_path),
                            'new': ('script', Path('scripts/other.json'))}
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        app._select_runner_item()
        app.runner_library.selection_set.assert_called_once_with('active')
        app.load_script_into_editor.assert_not_called()
