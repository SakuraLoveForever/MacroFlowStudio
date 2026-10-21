"""Check the client boundary without creating a window or input hooks."""
import json
import subprocess
import sys
import unittest
from pathlib import Path


class RuntimeBoundaryTests(unittest.TestCase):
    def test_runner_import_does_not_load_authoring_modules(self):
        probe = """
import json, sys
from macroflow.ui.app.runner import MacroFlowRunner
print(json.dumps(sorted(sys.modules)))
"""
        result = subprocess.run([sys.executable, '-c', probe], capture_output=True,
                                text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = set(json.loads(result.stdout))
        forbidden = {
            'macroflow.ui.app.main', 'macroflow.ui.app.recording',
            'macroflow.ui.app.scripts', 'macroflow.ui.app.workflow',
            'macroflow.ui.dialogs.actions', 'macroflow.ui.dialogs.module_objects',
            'macroflow.ui.dialogs.recognition', 'macroflow.ui.dialogs.segments',
            'macroflow.input.recorder',
        }
        self.assertEqual(sorted(loaded & forbidden), [])

    def test_clients_use_same_controller_without_runner_inheriting_studio(self):
        from macroflow.ui.app.main import MacroFlowApp
        from macroflow.ui.app.runner import MacroFlowRunner
        self.assertFalse(issubclass(MacroFlowRunner, MacroFlowApp))
        for name in ('run_current_script', 'run_workflow', 'toggle_execution_pause',
                     'stop_all', '_evaluate_global_guards', '_restore_recovery_workflow'):
            self.assertIs(getattr(MacroFlowRunner, name), getattr(MacroFlowApp, name))

    def test_runner_does_not_restore_or_persist_editor_drafts(self):
        from macroflow.ui.app.runner import MacroFlowRunner
        self.assertFalse(hasattr(MacroFlowRunner, '_editor_draft_snapshot'))
        self.assertFalse(hasattr(MacroFlowRunner, 'start_recording'))

    def test_runner_initializes_runtime_variables_without_editor_callbacks(self):
        from contextlib import ExitStack
        from unittest.mock import Mock, patch
        from tests.helpers.core import FakeVar
        from macroflow.core.models import MacroScript, Workflow
        from macroflow.ui.app.runner import MacroFlowRunner
        app = MacroFlowRunner.__new__(MacroFlowRunner)
        app.app_settings = {}
        app.script = MacroScript()
        app.workflow = Workflow()
        app._playback_reference_screen = Mock(return_value=None)
        with ExitStack() as stack:
            for name in ('StringVar', 'IntVar', 'DoubleVar', 'BooleanVar'):
                stack.enter_context(patch('macroflow.ui.app.runtime.shell.tk.' + name,
                                          side_effect=lambda **kw: FakeVar(kw.get('value'))))
            stack.enter_context(patch('macroflow.ui.app.runtime.shell.DurationVar',
                                      side_effect=lambda **kw: FakeVar(kw.get('value'))))
            app._create_variables()
        app._initialize_authoring_state()
        self.assertIsNone(app.recorder)
        self.assertEqual(app.repeat_var.get(), 1)
        self.assertFalse(hasattr(app, 'key_search_var'))
