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
    if parent is not None:
        parent.winfo_children.return_value.append(result)
    return result


class PickerWindowRestoreTests(unittest.TestCase):
    def test_collects_nested_windows_in_parent_first_order(self):
        root = window("root")
        manager = window("manager", root)
        form = window("form", manager)
        owner = window("action", form)
        notice = window("notice", root)

        self.assertEqual(base.app_windows(form), [root, manager, form, owner, notice])

    def test_process_selection_restores_parents_before_the_action_dialog(self):
        for event in ("click", "cancel"):
            with self.subTest(event=event):
                root = window("root", state="zoomed")
                manager = window("manager", root)
                form = window("form", manager)
                owner = window("action", form)
                notice = window("notice", root, state="withdrawn")
                restored = []
                for name, item in (("root", root), ("manager", manager),
                                   ("form", form), ("action", owner), ("notice", notice)):
                    item.deiconify.side_effect = lambda name=name: restored.append(name)
                    item.withdraw.side_effect = lambda item=item: setattr(item.state, "return_value", "withdrawn")
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

                self.assertEqual(restored, ["root", "manager", "form", "action"])
                root.state.assert_called_with("zoomed")
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
        picker = screen_pickers.ScreenProcessPicker(
            owner, form, Mock(), hidden_windows=base.app_windows(form),
        )
        picker.start()
        self.assertEqual(picker.hidden_states, ["normal", "normal"])

    def test_minimized_window_is_not_shown_by_the_native_restore(self):
        main = window("main")
        minimized = window("minimized", main, state="iconic")
        picker = screen_pickers.ScreenProcessPicker(
            None, main, Mock(), hidden_windows=[minimized],
        )
        picker.start()
        with patch.object(screen_pickers, "show_window_no_activate") as show:
            picker.close()
        minimized.state.assert_called_with("iconic")
        # Only the main window should be shown; SW_SHOWNOACTIVATE would restore
        # the minimized sibling to normal size.
        self.assertEqual(show.call_count, 1)


if __name__ == "__main__":
    unittest.main()
