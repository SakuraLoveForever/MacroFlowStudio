import unittest
from types import SimpleNamespace
from unittest.mock import Mock


class SaveStateTests(unittest.TestCase):
    def state(self, data, **kwargs):
        from macroflow.ui.save_state import SaveState
        owner, button = Mock(), Mock()
        state = SaveState(owner, button, lambda: data, **kwargs)
        return state, owner, button

    def test_only_actual_changes_enable_and_reverting_disables(self):
        data = {'value': 2, 'actions': [{'key': 'W'}]}
        state, _, button = self.state(data)
        button.configure.assert_called_with(state='disabled')
        data['actions'][0]['key'] = 'E'
        state.refresh()
        button.configure.assert_called_with(state='normal')
        data['actions'][0]['key'] = 'W'
        state.refresh()
        button.configure.assert_called_with(state='disabled')

    def test_success_moves_baseline_and_failed_save_keeps_retry_enabled(self):
        data = [2]
        state, _, button = self.state(data)
        data[0] = 8
        state.refresh()
        button.configure.assert_called_with(state='normal')
        # A failing writer does not call saved().
        state.refresh()
        button.configure.assert_called_with(state='normal')
        state.saved()
        button.configure.assert_called_with(state='disabled')

    def test_new_objects_can_be_saved_without_parameter_changes(self):
        state, _, button = self.state({}, new=True)
        button.configure.assert_called_with(state='normal')
        state.saved()
        button.configure.assert_called_with(state='disabled')

    def test_destroy_cancels_polling_and_ignores_child_destroy(self):
        state, owner, _ = self.state({})
        state.destroyed(SimpleNamespace(widget=object()))
        owner.after_cancel.assert_not_called()
        state.destroyed(SimpleNamespace(widget=owner))
        owner.after_cancel.assert_called_once()

    def test_locked_setting_save_stays_disabled_until_value_changes(self):
        from macroflow.ui.app.main import MacroFlowApp
        app, spin, button = Mock(), Mock(), Mock()
        label = ['修改']
        button.cget.side_effect = lambda key: label[0]
        def configure(**options):
            if 'text' in options:
                label[0] = options['text']
        button.configure.side_effect = configure
        spin.get.return_value = '3'
        save = Mock(return_value=True)
        MacroFlowApp._toggle_locked_spinbox(app, spin, button, save)
        button.configure.assert_called_with(state='disabled')
        spin.get.return_value = '4'
        spin._save_state.refresh()
        button.configure.assert_called_with(state='normal')
        MacroFlowApp._toggle_locked_spinbox(app, spin, button, save)
        save.assert_called_once_with()
        self.assertEqual(label[0], '修改')

    def test_variable_changes_refresh_immediately_and_unsubscribe_on_close(self):
        data = [2]
        state, _, button = self.state(data)
        variable = Mock()
        variable.trace_add.return_value = 'token'
        state.watch(variable)
        callback = variable.trace_add.call_args.args[1]
        data[0] = 3
        callback()
        button.configure.assert_called_with(state='normal')
        data[0] = 2
        callback()
        button.configure.assert_called_with(state='disabled')
        state.close()
        variable.trace_remove.assert_called_once_with('write', 'token')

    def test_temporarily_empty_numeric_field_does_not_stop_change_tracking(self):
        import tkinter as tk
        state, _, button = self.state([2])
        state.snapshot = Mock(side_effect=tk.TclError('expected integer'))
        state.refresh()
        self.assertFalse(state.closed)
        button.configure.assert_called_with(state='normal')
        state.snapshot = lambda: [2]
        state.refresh()
        button.configure.assert_called_with(state='disabled')

    def test_enter_on_unchanged_apply_does_not_write_settings(self):
        from macroflow.ui.app.helpers import HelpersMixin
        from macroflow.ui.save_state import SaveState
        app = Mock()
        app._aim_save_state = SaveState(Mock(), Mock(), lambda: 2)
        self.assertTrue(HelpersMixin.apply_aim_follow_interval(app))
        app._persist_sidebar_settings.assert_not_called()
