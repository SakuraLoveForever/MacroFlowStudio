"""Sidebar save/restart regression checks without creating windows or workers."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.helpers.core import FakeVar, add_src_to_path
from macroflow.core import storage
from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.app.shell import ShellMixin
from macroflow.ui.app import disconnect_monitor as monitor_ui


class SidebarPersistenceTests(unittest.TestCase):
    def app(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        for name in (
            'sound_enabled', 'mini_window_enabled', 'execution_mini_enabled',
            'focus_mode_enabled', 'activate_target_enabled', 'partial_script_globals',
            'partial_workflow_globals', 'activation_enabled', 'timed_backup_enabled',
            'windows_startup_enabled', 'start_minimized_to_tray', 'startup_run_workflow',
        ):
            setattr(app, name + '_var', FakeVar(False))
        for name, value in {
            'repeat': 1, 'backup_interval': '1h',
            'floating_notice_position': '顶部居中', 'close_action': 'exit',
            'startup_workflow_path': '', 'level_scripts_dir': '',
            'level_pack_scripts_dir': '', 'switch_scripts_dir': '', 'direction_scripts_dir': '',
        }.items():
            setattr(app, name + '_var', FakeVar(value))
        app.app_settings = {}
        app.saved_window_signature = None
        app.workflow_path = app.script_path = None
        app._workflow_snapshot = Mock(return_value={})
        app._editor_draft_snapshot = Mock(return_value={})
        app._current_main_window_geometry = Mock(return_value='1000x800')
        return app

    def test_save_and_reload_preserves_checked_and_unchecked_options(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as folder, patch.object(
            storage, 'SETTINGS_PATH', Path(folder) / 'settings.json',
        ):
            for checked in (True, False):
                app.switch_english_input_enabled = checked
                app.disconnect_monitor_enabled = checked
                self.assertTrue(app._persist_sidebar_settings())
                restored = storage.load_app_settings()
                self.assertIs(restored['switch_english_input_enabled'], checked)
                self.assertIs(restored['disconnect_monitor_enabled'], checked)
                self.assertEqual(restored['main_window_geometry'], '1000x800')

    def test_startup_restores_english_option_and_first_launch_defaults_off(self):
        for saved, expected in (({}, False), ({'switch_english_input_enabled': True}, True),
                                ({'switch_english_input_enabled': False}, False)):
            app = SimpleNamespace(app_settings=saved)
            ShellMixin._restore_execution_settings(app)
            self.assertIs(app.switch_english_input_enabled, expected)

    def test_saved_disconnect_option_restores_worker_and_toggle_state(self):
        for saved, expected in (({}, False), ({'disconnect_monitor_enabled': True}, True),
                                ({'disconnect_monitor_enabled': False}, False)):
            app = SimpleNamespace(app_settings=saved, root=Mock(), _ui=Mock(), _log=Mock())
            with patch.object(monitor_ui.tk, 'BooleanVar', side_effect=lambda **kw: FakeVar(kw['value'])), \
                 patch.object(monitor_ui.ttk, 'Checkbutton') as button, \
                 patch.object(monitor_ui, 'Tooltip'), \
                 patch.object(monitor_ui, 'DisconnectMonitor') as factory:
                monitor_ui.add_disconnect_monitor(Mock(), app)
            self.assertIs(app.disconnect_monitor_enabled, expected)
            self.assertEqual(factory.return_value.start.call_count, int(expected))
            variable = button.call_args.kwargs['variable']
            variable.set(not expected)
            button.call_args.kwargs['command']()
            self.assertIs(app.disconnect_monitor_enabled, not expected)
