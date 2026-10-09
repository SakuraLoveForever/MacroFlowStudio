"""Real-window lookup and picker lifecycle without windows or live input hooks."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from macroflow.input import process_targets
from macroflow.ui.dialogs import screen_pickers


class ProcessTargetReaderTests(unittest.TestCase):
    def reader(self, pid=123):
        reader = process_targets.ProcessTargetReader.__new__(process_targets.ProcessTargetReader)
        reader.desktop = Mock()
        reader.desktop.top_from_point.return_value.process_id.return_value = pid
        return reader

    def test_name_lookup_does_not_require_executable_path_access(self):
        reader = self.reader()
        with patch.object(process_targets.psutil, 'Process') as process:
            process.return_value.name.return_value = 'game.exe'
            process.return_value.exe.side_effect = PermissionError('denied')
            self.assertEqual(reader.at(-50, 25), 'game.exe')
        reader.desktop.top_from_point.assert_called_once_with(-50, 25)
        process.return_value.exe.assert_not_called()

    def test_open_application_requires_the_full_path(self):
        reader = self.reader()
        with patch.object(process_targets.psutil, 'Process') as process:
            process.return_value.exe.return_value = r'C:\Apps\game.exe'
            self.assertEqual(reader.at(10, 20, full_path=True), r'C:\Apps\game.exe')
        process.return_value.name.assert_not_called()

    def test_own_windows_and_desktop_cannot_be_selected(self):
        for pid in (0, os.getpid()):
            with self.subTest(pid=pid), patch.object(process_targets.psutil, 'Process') as process:
                self.assertEqual(self.reader(pid).at(10, 20), '')
                process.assert_not_called()


class PickerLifecycleTests(unittest.TestCase):
    def picker(self):
        picker = screen_pickers.ScreenProcessPicker(Mock(), Mock(), Mock())
        picker.reader = Mock()
        picker.reader.at.return_value = 'game.exe'
        picker.mouse_listener = Mock()
        picker.keyboard_listener = Mock()
        picker.tip_label = Mock()
        picker._position_tip = Mock()
        return picker

    def test_hover_queries_live_window_without_confirming(self):
        picker = self.picker()
        with patch.object(screen_pickers, 'get_cursor_pos', return_value=(-50, 25)):
            picker._poll()
        picker.reader.at.assert_called_once_with(-50, 25, full_path=False)
        self.assertIn('game.exe', picker.tip_label.configure.call_args.kwargs['text'])
        picker.on_result.assert_not_called()

    def test_confirmation_queries_click_coordinates_again(self):
        picker = self.picker()
        picker.close = Mock()
        picker.events.put(('click', 150, 40))
        picker._poll()
        picker.reader.at.assert_called_once_with(150, 40, full_path=False)
        picker.close.assert_called_once()
        picker.on_result.assert_called_once_with('game.exe')

    def test_permission_failure_does_not_confirm_or_reuse_a_previous_target(self):
        picker = self.picker()
        picker.close = Mock()
        picker.reader.at.side_effect = PermissionError('denied')
        picker.events.put(('click', 150, 40))
        with patch.object(screen_pickers, 'get_cursor_pos', return_value=(150, 40)):
            picker._poll()
        picker.close.assert_not_called()
        picker.on_result.assert_not_called()
        self.assertIn('denied', picker.tip_label.configure.call_args.kwargs['text'])

    def test_escape_cancels_without_updating_the_form(self):
        picker = self.picker()
        picker.close = Mock()
        picker.events.put(('cancel', 0, 0))
        picker._poll()
        picker.close.assert_called_once()
        picker.on_result.assert_not_called()

    def test_confirmation_click_is_suppressed_and_queued_on_release(self):
        picker = self.picker()
        data = SimpleNamespace(pt=SimpleNamespace(x=10, y=20))
        picker._mouse_filter(0x201, data)
        self.assertTrue(picker.events.empty())
        picker._mouse_filter(0x202, data)
        self.assertEqual(picker.events.get_nowait(), ('click', 10, 20))
        self.assertEqual(picker.mouse_listener.suppress_event.call_count, 2)
        picker._mouse_filter(0x204, data)  # Right click remains available.
        self.assertEqual(picker.mouse_listener.suppress_event.call_count, 2)

    def test_escape_is_suppressed_but_other_keys_are_not(self):
        picker = self.picker()
        picker._keyboard_filter(0x100, SimpleNamespace(vkCode=27))
        picker._keyboard_filter(0x101, SimpleNamespace(vkCode=27))
        self.assertEqual(picker.events.get_nowait(), ('cancel', 0, 0))
        self.assertEqual(picker.keyboard_listener.suppress_event.call_count, 2)
        picker._keyboard_filter(0x100, SimpleNamespace(vkCode=65))
        self.assertEqual(picker.keyboard_listener.suppress_event.call_count, 2)

    def test_close_stops_both_hooks_and_restores_windows_only_once(self):
        picker = self.picker()
        picker.poll_id = 'timer'
        picker._destroy_overlay = Mock()
        picker._restore_windows = Mock()
        picker.close()
        picker.close()
        picker.mouse_listener.stop.assert_called_once()
        picker.keyboard_listener.stop.assert_called_once()
        picker.main.after_cancel.assert_called_once_with('timer')
        picker._restore_windows.assert_called_once()

    def test_backend_initialization_failure_restores_windows(self):
        picker = self.picker()
        picker.close = Mock()
        with patch.object(screen_pickers, 'ProcessTargetReader', side_effect=OSError('backend failed')), \
             patch.object(screen_pickers, 'show_floating_notice') as notice:
            picker._show_overlay()
        picker.close.assert_called_once()
        notice.assert_called_once()

    def test_dead_listener_cancels_instead_of_leaving_the_app_hidden(self):
        picker = self.picker()
        picker.mouse_listener.is_alive.return_value = False
        picker.close = Mock()
        with patch.object(screen_pickers, 'show_floating_notice') as notice:
            picker._poll()
        picker.close.assert_called_once()
        notice.assert_called_once()
        picker.on_result.assert_not_called()

    def test_tip_does_not_create_a_full_screen_input_curtain(self):
        picker = self.picker()
        picker._poll = Mock()
        with patch.object(screen_pickers, 'ProcessTargetReader'), \
             patch.object(screen_pickers.tk, 'Toplevel') as overlay, \
             patch.object(screen_pickers.tk, 'Label'), \
             patch.object(screen_pickers.mouse, 'Listener'), \
             patch.object(screen_pickers.keyboard, 'Listener'), \
             patch.object(screen_pickers, 'get_cursor_pos', return_value=(10, 20)), \
             patch.object(screen_pickers, 'make_window_no_activate', return_value=True) as no_activate, \
             patch.object(screen_pickers, 'show_window_no_activate'):
            picker._show_overlay()
        no_activate.assert_called_once_with(overlay.return_value.winfo_id.return_value, click_through=True)
        overlay.return_value.grab_set.assert_not_called()
        overlay.return_value.focus_force.assert_not_called()
        picker.mouse_listener.start.assert_called_once()
        picker.keyboard_listener.start.assert_called_once()
