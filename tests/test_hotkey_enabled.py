import unittest
import importlib
import threading
from unittest.mock import Mock, patch

from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.dialogs.app_dialogs import HotkeyScriptsDialog


class HotkeyEnabledTests(unittest.TestCase):
    def app(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.hotkey_scripts = [
            {'key': 'J', 'vk': 74, 'script': 'left.json', 'enabled': True},
            {'key': 'K', 'vk': 75, 'script': 'right.json', 'enabled': False},
        ]
        app.input_guard = Mock()
        app.recorder = Mock()
        app._hotkey_active_binding = None
        app._stop_hotkey_script = Mock()
        return app

    def test_disabled_state_survives_normalization(self):
        items = self.app().hotkey_scripts
        normalized = MacroFlowApp._normalize_hotkey_scripts(items)
        self.assertEqual(normalized, items)
        self.assertTrue(MacroFlowApp._normalize_hotkey_scripts([
            {'key': 'J', 'vk': 74, 'script': 'left.json'}])[0]['enabled'])

    def test_disabled_keys_are_removed_from_guard_and_recording_filters(self):
        app = self.app()
        app._apply_hotkey_bindings()
        self.assertEqual(set(app._hotkey_vk_map), {74})
        app.input_guard.set_hotkeys.assert_called_once_with({74, 0x77})
        app.recorder.set_filter_vks.assert_called_once_with({74})
        app.hotkey_scripts[1]['enabled'] = True
        app._apply_hotkey_bindings()
        self.assertEqual(set(app._hotkey_vk_map), {74, 75})

    def test_disabling_current_script_stops_it_without_stopping_other_bindings(self):
        app = self.app()
        app._hotkey_active_binding = app.hotkey_scripts[1]
        app._apply_hotkey_bindings()
        app._stop_hotkey_script.assert_called_once()
        app._stop_hotkey_script.reset_mock()
        app._hotkey_active_binding = app.hotkey_scripts[0]
        app._apply_hotkey_bindings()
        app._stop_hotkey_script.assert_not_called()

    def test_disabled_binding_cannot_trigger_a_worker(self):
        app = self.app()
        app.exiting = app.hotkey_config_open = False
        # No lock/player is needed: disabled bindings must return at the entry.
        app._trigger_hotkey_script(app.hotkey_scripts[1])

    def test_toggle_preserves_binding_and_keeps_selection(self):
        dialog = HotkeyScriptsDialog.__new__(HotkeyScriptsDialog)
        dialog.bindings = [dict(item) for item in self.app().hotkey_scripts]
        dialog.tree = Mock()
        dialog.tree.selection.return_value = ('0',)
        dialog.tree.get_children.return_value = ('0', '1')
        dialog.toggle_button = Mock()
        before = dict(dialog.bindings[0])
        dialog._toggle_selected()
        self.assertEqual(dialog.bindings[0], {**before, 'enabled': False})
        dialog.tree.selection_set.assert_called_with('0')
        dialog.toggle_button.configure.assert_called_with(text='启用选中', state='normal')
        dialog._toggle_selected()
        self.assertEqual(dialog.bindings[0], before)
        dialog.toggle_button.configure.assert_called_with(text='禁用选中', state='normal')

    def test_no_selection_disables_toggle_and_does_not_change_bindings(self):
        dialog = HotkeyScriptsDialog.__new__(HotkeyScriptsDialog)
        dialog.bindings = []
        dialog.tree = Mock()
        dialog.tree.selection.return_value = ()
        dialog.toggle_button = Mock()
        dialog._toggle_selected()
        dialog._sync_toggle_button()
        self.assertEqual(dialog.bindings, [])
        dialog.toggle_button.configure.assert_called_with(text='启用/禁用', state='disabled')

    def test_editing_a_disabled_binding_does_not_enable_it(self):
        dialog = HotkeyScriptsDialog.__new__(HotkeyScriptsDialog)
        dialog.bindings = [dict(self.app().hotkey_scripts[1])]
        dialog.grab_set = Mock()
        dialog._render = Mock()
        with patch('macroflow.ui.dialogs.app_dialogs.HotkeyBindingDialog') as editor:
            editor.return_value.show.return_value = {'key': 'K', 'vk': 75, 'script': 'updated.json'}
            dialog._edit_index(0)
        self.assertFalse(dialog.bindings[0]['enabled'])
        self.assertEqual(dialog.bindings[0]['script'], 'updated.json')

    def test_summary_marks_disabled_bindings(self):
        app = self.app()
        app.hotkey_summary_var = Mock()
        app._refresh_hotkey_summary()
        text = app.hotkey_summary_var.set.call_args.args[0]
        self.assertIn('J → left', text)
        self.assertIn('K → right（已禁用）', text)

    def test_saving_disable_stops_the_script_started_by_its_binding(self):
        app = self.app()
        del app._stop_hotkey_script
        app.exiting = app.hotkey_config_open = app._hotkey_script_running = False
        app._hotkey_script_lock = threading.Lock()
        app._hotkey_cancel = threading.Event()
        app.hotkey_player = Mock()
        module = importlib.import_module(MacroFlowApp._trigger_hotkey_script.__module__)
        with patch.object(module.threading, 'Thread'):
            app._trigger_hotkey_script(app.hotkey_scripts[0])
        self.assertEqual(app._hotkey_active_binding['vk'], 74)
        app.hotkey_scripts[0]['enabled'] = False
        app._apply_hotkey_bindings()
        app.hotkey_player.stop.assert_called_once()
