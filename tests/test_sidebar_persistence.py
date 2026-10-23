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
from macroflow.ui.save_state import SaveState


class SidebarPersistenceTests(unittest.TestCase):
    def editable_app(self):
        app = self.app()
        app.activation_interval_var = FakeVar('0')
        app.switch_english_input_enabled = False
        app.recorder = None
        app.script = SimpleNamespace(settings={})
        app.saved_activation_signature = None
        app._mark_dirty = Mock()
        app._log = Mock()
        app._set_status = Mock()
        app._sidebar_save_state = SaveState(Mock(), Mock(), app._sidebar_save_values)
        return app

    def test_every_sidebar_option_remains_saveable_until_explicit_save(self):
        app = self.editable_app()
        with patch('macroflow.ui.app.runtime.helpers.save_app_settings'):
            for name in ('sound_enabled', 'mini_window_enabled', 'execution_mini_enabled',
                         'focus_mode_enabled', 'activate_target_enabled',
                         'partial_script_globals', 'partial_workflow_globals', 'activation_enabled'):
                for checked in (True, False):
                    with self.subTest(option=name, checked=checked):
                        getattr(app, name + '_var').set(checked)
                        app._settings_changed()
                        app._sidebar_save_state.refresh()
                        self.assertEqual(app._sidebar_save_state.state, 'normal')
                        self.assertTrue(app.save_sidebar_config())
                        self.assertEqual(app._sidebar_save_state.state, 'disabled')

    def test_background_persistence_does_not_consume_pending_sidebar_edits(self):
        app = self.editable_app()
        app.focus_mode_enabled_var.set(True)
        with patch('macroflow.ui.app.runtime.helpers.save_app_settings'):
            self.assertTrue(app._persist_sidebar_settings())
        app._sidebar_save_state.refresh()
        self.assertEqual(app._sidebar_save_state.state, 'normal')

    def test_other_sidebar_values_and_nested_settings_are_tracked(self):
        for name, value in {
            'repeat_var': 3, 'activation_interval_var': '1.5',
            'floating_notice_position_var': '右下角', 'close_action_var': 'tray',
        }.items():
            with self.subTest(option=name):
                app = self.editable_app()
                getattr(app, name).set(value)
                app._sidebar_save_state.refresh()
                self.assertEqual(app._sidebar_save_state.state, 'normal')
        app = self.editable_app()
        app.switch_english_input_var = FakeVar(True)
        app._toggle_english_input()
        self.assertTrue(app.switch_english_input_enabled)
        self.assertEqual(app._sidebar_save_state.state, 'normal')
        app = self.editable_app()
        app.hotkey_scripts = [{'key': 'F1'}]
        app._sidebar_save_state.saved()
        app.hotkey_scripts[0]['key'] = 'F2'
        app._sidebar_save_state.refresh()
        self.assertEqual(app._sidebar_save_state.state, 'normal')

    def test_failed_save_keeps_retry_enabled(self):
        app = self.editable_app()
        app._notify = Mock()
        app.focus_mode_enabled_var.set(True)
        with patch('macroflow.ui.app.runtime.helpers.save_app_settings', side_effect=OSError('denied')):
            self.assertFalse(app.save_sidebar_config())
        self.assertEqual(app._sidebar_save_state.state, 'normal')

    def app(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        for name in (
            'sound_enabled', 'mini_window_enabled', 'execution_mini_enabled',
            'focus_mode_enabled', 'activate_target_enabled', 'partial_script_globals',
            'partial_workflow_globals', 'activation_enabled',
        ):
            setattr(app, name + '_var', FakeVar(False))
        for name, value in {
            'repeat': 1,
            'floating_notice_position': '顶部居中', 'close_action': 'exit',
            'level_scripts_dir': '',
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
