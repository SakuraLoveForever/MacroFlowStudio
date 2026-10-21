"""Nested picker window lifecycle without creating a Tk window or input hook."""
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from macroflow.ui.dialogs import base, screen_pickers


def window(name, parent=None, state="normal"):
    result = Mock(spec=tk.Tk if parent is None else tk.Toplevel, name=name)
    result.master = parent
    result.winfo_exists.return_value = True
    result.winfo_children.return_value = []
    result.state.return_value = state
    result.winfo_id.return_value = 123
    result.window_settings = {'-alpha': 1.0, '-disabled': False}

    def attributes(attribute, *values):
        if values:
            result.window_settings[attribute] = values[0]
        return result.window_settings[attribute]

    result.attributes.side_effect = attributes
    if parent is not None:
        parent.winfo_children.return_value.append(result)
    return result


class PickerWindowRestoreTests(unittest.TestCase):
    def test_restoring_a_modal_keeps_its_existing_transient_relationship(self):
        main = window('main')
        owner = window('action', main)
        with patch.object(base, 'show_window_no_activate'):
            self.assertTrue(base.restore_modal_after_overlay(owner, main, 'normal'))
            self.assertTrue(base.restore_modal_after_overlay(owner, main, 'normal'))
        # On Windows, setting wm transient even to the SAME parent calls
        # UpdateWrapper, destroying/recreating the decorated HWND. Restoration
        # must leave the relationship established by ModalDialog intact.
        owner.transient.assert_not_called()
        owner.deiconify.assert_called()
        owner.grab_set.assert_called()

    def test_process_picker_keeps_windows_mapped_on_confirm_cancel_and_failure(self):
        for nested in (False, True):
            for outcome in ('confirm', 'cancel', 'failure'):
                with self.subTest(nested=nested, outcome=outcome):
                    root = window('root')
                    parent = window('module', root) if nested else root
                    owner = window('action', parent)
                    result = Mock()
                    picker = screen_pickers.ScreenProcessPicker(
                        owner, parent, result, hidden_windows=base.app_windows(parent))
                    picker.reader = Mock()
                    picker.reader.at.return_value = 'target.exe'
                    windows = base.app_windows(parent)
                    parent.window_settings.update({'-alpha': 0.75, '-disabled': True})
                    picker.start()
                    for item in windows:
                        item.attributes.assert_called_with('-alpha', 0.0)
                        self.assertTrue(item.window_settings['-disabled'])
                        item.withdraw.assert_not_called()
                    with patch.object(screen_pickers, 'show_window_no_activate') as native_show, \
                         patch.object(screen_pickers, 'restore_modal_after_overlay') as remap, \
                         patch.object(screen_pickers, 'show_floating_notice'):
                        if outcome == 'failure':
                            with patch.object(screen_pickers, 'ProcessTargetReader', side_effect=OSError('failed')):
                                picker._show_overlay()
                        else:
                            picker.events.put(('click' if outcome == 'confirm' else 'cancel', 10, 20))
                            picker._poll()
                        # A cancelled startup callback must not resurrect a tip.
                        with patch.object(screen_pickers.tk, 'Toplevel') as overlay:
                            picker._show_overlay()
                        overlay.assert_not_called()
                    native_show.assert_not_called()
                    remap.assert_not_called()
                    for item in windows:
                        self.assertEqual(item.window_settings, {
                            '-alpha': 0.75 if item is parent else 1.0,
                            '-disabled': item is parent,
                        })
                        item.transient.assert_not_called()
                        item.deiconify.assert_not_called()
                        item.withdraw.assert_not_called()
                    owner.grab_set.assert_called_once()
                    owner.after.assert_not_called()
                    if outcome == 'confirm':
                        result.assert_called_once_with('target.exe')
                    else:
                        result.assert_not_called()

    def test_collects_nested_windows_in_parent_first_order(self):
        root = window("root")
        manager = window("manager", root)
        form = window("form", manager)
        owner = window("action", form)
        notice = window("notice", root)

        self.assertEqual(base.app_windows(form), [root, manager, form, owner, notice])

    def test_process_selection_preserves_hidden_and_minimized_windows(self):
        for event in ("click", "cancel"):
            with self.subTest(event=event):
                root = window("root", state="zoomed")
                manager = window("manager", root)
                form = window("form", manager)
                owner = window("action", form)
                notice = window("notice", root, state="withdrawn")
                minimized = window("minimized", root, state="iconic")
                result = Mock()
                picker = screen_pickers.ScreenProcessPicker(
                    owner, form, result, hidden_windows=base.app_windows(form),
                )
                picker.reader = Mock()
                picker.reader.at.return_value = "game.exe"
                picker.start()
                picker.events.put((event, 10, 20))
                with patch.object(screen_pickers, "show_window_no_activate"), \
                     patch.object(base, "show_window_no_activate"):
                    picker._poll()

                notice.attributes.assert_not_called()
                minimized.attributes.assert_not_called()
                for item in base.app_windows(form):
                    # state() is read only: no normal/zoomed/iconic transitions.
                    self.assertTrue(all(not call.args for call in item.state.call_args_list))
                if event == "click":
                    result.assert_called_once_with("game.exe")
                else:
                    result.assert_not_called()
                owner.grab_set.assert_called_once()

    def test_snapshot_precedes_withdrawing_transient_parents(self):
        root = window("root")
        manager = window("manager", root)
        form = window("form", manager)
        owner = window("action", form)
        # A transient mirrors its parent's withdrawal in Tk.
        root.withdraw.side_effect = lambda: setattr(manager.state, "return_value", "withdrawn")
        picker = screen_pickers.ScreenRegionPicker(
            owner, form, Mock(), hidden_windows=base.app_windows(form),
        )
        picker.start()
        self.assertEqual(picker.hidden_states, ["normal", "normal"])

    def test_minimized_window_is_not_shown_by_the_native_restore(self):
        main = window("main")
        minimized = window("minimized", main, state="iconic")
        picker = screen_pickers.ScreenRegionPicker(
            None, main, Mock(), hidden_windows=[minimized],
        )
        picker.start()
        with patch.object(screen_pickers, "show_window_no_activate") as show:
            picker.close()
        minimized.state.assert_called_with("iconic")
        # Only the main window should be shown; SW_SHOWNOACTIVATE would restore
        # the minimized sibling to normal size.
        self.assertEqual(show.call_count, 1)

    def test_process_picker_start_failure_restores_original_opacity_and_grab(self):
        main = window('main')
        owner = window('action', main)
        main.window_settings['-alpha'] = 0.8
        original_attributes = {item: item.attributes.side_effect for item in (main, owner)}

        def attributes(item, *args):
            if item is main and args == ('-alpha', 0.0):
                raise tk.TclError('hide failed')
            return original_attributes[item](*args)

        for item in (main, owner):
            item.attributes.side_effect = lambda *args, item=item: attributes(item, *args)
        picker = screen_pickers.ScreenProcessPicker(owner, main, Mock())
        with patch.object(screen_pickers, 'show_floating_notice'):
            picker.start()
        self.assertEqual(main.window_settings, {'-alpha': 0.8, '-disabled': False})
        self.assertEqual(owner.window_settings, {'-alpha': 1.0, '-disabled': False})
        main.after.assert_not_called()
        owner.grab_set.assert_called_once()

    def test_process_picker_close_before_tip_cancels_startup_and_restores_once(self):
        main = window('main')
        owner = window('action', main)
        main.after.return_value = 'startup'
        picker = screen_pickers.ScreenProcessPicker(owner, main, Mock())
        picker.start()
        picker.start()
        callback = main.after.call_args.args[1]
        picker.close()
        picker.close()
        main.after.assert_called_once()
        main.after_cancel.assert_called_once_with('startup')
        owner.grab_set.assert_called_once()
        with patch.object(screen_pickers.tk, 'Toplevel') as overlay:
            callback()
        overlay.assert_not_called()

    def test_process_picker_restores_surviving_windows_if_owner_is_destroyed(self):
        main = window('main')
        owner = window('action', main)
        picker = screen_pickers.ScreenProcessPicker(owner, main, Mock())
        picker.start()
        owner.attributes.side_effect = tk.TclError('window destroyed')
        owner.lift.side_effect = tk.TclError('window destroyed')
        picker.close()
        self.assertEqual(main.window_settings, {'-alpha': 1.0, '-disabled': False})
        self.assertEqual(picker.hidden_settings, [])


if __name__ == "__main__":
    unittest.main()
