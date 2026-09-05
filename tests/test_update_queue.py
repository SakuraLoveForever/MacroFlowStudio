import unittest
import threading
from unittest.mock import Mock, patch

from macroflow.ui.dialogs import (
    GridRowConditionClickDialog,
    condition_field_visibility,
    format_grid_column_label,
    parse_named_region,
    test_state_transition,
    format_test_result,
)
from macroflow.ui.update_queue import UIUpdateQueue
from macroflow.ui.app import MacroFlowApp


class FakeRoot:
    def __init__(self):
        self.calls = []
        self.next_id = 0

    def after(self, delay, callback):
        self.next_id += 1
        self.calls.append((self.next_id, delay, callback))
        return self.next_id

    def after_cancel(self, after_id):
        self.calls = [call for call in self.calls if call[0] != after_id]


class UpdateQueueTests(unittest.TestCase):
    def test_status_is_coalesced_but_ordered_events_are_preserved(self):
        root = FakeRoot()
        queue = UIUpdateQueue(root)
        seen = []

        queue.submit(seen.append, "first log")
        queue.submit(seen.append, "recorded action", key="recording")
        queue.submit(seen.append, "old status", key="status")
        queue.submit(seen.append, "new status", key="status")
        self.assertEqual(root.calls, [])
        queue.start()
        self.assertEqual(len(root.calls), 1)
        queue.flush()

        self.assertEqual(seen, ["first log", "recorded action", "new status"])

    def test_batch_events_are_delivered_as_one_callback(self):
        root = FakeRoot()
        queue = UIUpdateQueue(root)
        seen = []
        queue.submit(seen.append, "a", batch_key="log")
        queue.submit(seen.append, "b", batch_key="log")
        queue.flush()
        self.assertEqual(seen, [["a", "b"]])

    def test_urgent_event_flushes_immediately(self):
        root = FakeRoot()
        queue = UIUpdateQueue(root)
        seen = []
        queue.submit(seen.append, "stop", urgent=True)
        self.assertEqual(seen, [])
        queue.pump()
        self.assertEqual(seen, ["stop"])

    def test_submit_from_worker_never_touches_root_or_runs_callback(self):
        root = FakeRoot()
        queue = UIUpdateQueue(root)
        seen = []
        main_thread_id = threading.get_ident()
        worker = threading.Thread(target=lambda: queue.submit(lambda: seen.append(threading.get_ident())))
        worker.start()
        worker.join()
        self.assertEqual(root.calls, [])
        self.assertEqual(seen, [])
        queue.start()
        queue.pump()
        self.assertEqual(seen, [main_thread_id])

    def test_flush_is_safe_after_close(self):
        root = FakeRoot()
        queue = UIUpdateQueue(root)
        queue.close()
        queue.submit(lambda value: self.fail(value), "ignored")
        queue.flush()


class GridDialogPureHelperTests(unittest.TestCase):
    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

        def set(self, value):
            self.value = value

    class FakeButton:
        def __init__(self):
            self.configurations = []

        def configure(self, **kwargs):
            self.configurations.append(kwargs)

    def test_named_region_parsing(self):
        self.assertEqual(parse_named_region({"x": "1", "y": 2, "w": "30", "h": 40}), [1, 2, 30, 40])
        self.assertEqual(parse_named_region("1, 2, 30, 40"), [1, 2, 30, 40])

    def test_columns_are_displayed_from_one(self):
        self.assertEqual(format_grid_column_label(0), "第1列")
        self.assertEqual(format_grid_column_label(3), "第4列")

    def test_condition_visibility_has_no_irrelevant_fields(self):
        self.assertEqual(condition_field_visibility("image"), {"module"})
        self.assertEqual(condition_field_visibility("text"), {"text", "match"})
        self.assertEqual(condition_field_visibility("number"), {"separator", "relation"})

    def test_test_state_transitions_prevent_duplicate_runs(self):
        self.assertEqual(test_state_transition("idle", "start"), "running")
        self.assertEqual(test_state_transition("running", "start"), "running")
        self.assertEqual(test_state_transition("running", "success"), "success")
        self.assertEqual(test_state_transition("success", "cancel"), "idle")

    def test_async_test_result_is_only_rendered_after_completion(self):
        self.assertEqual(format_test_result({"matched_rows": 3}, None, 125), "命中 3 行，耗时 125 ms")
        self.assertEqual(format_test_result(None, "OCR 失败", 80), "测试失败：OCR 失败（耗时 80 ms）")

    def test_dialog_waits_for_async_completion_callback_and_cancel_sets_event(self):
        dialog = GridRowConditionClickDialog.__new__(GridRowConditionClickDialog)
        dialog.test_state = self.FakeVar("未测试")
        dialog.test_cancel_event = __import__("threading").Event()
        dialog.test_button = self.FakeButton()
        dialog.save_button = self.FakeButton()
        dialog.cancel_button = self.FakeButton()
        dialog._build_action = lambda: {"type": "grid_row_condition_click"}
        callbacks = []
        dialog.on_test = lambda action, complete, cancel: callbacks.append((action, complete, cancel))

        dialog._test()
        self.assertEqual(dialog.test_state.get(), "正在测试…")
        self.assertEqual(len(callbacks), 1)
        dialog._test()
        self.assertEqual(len(callbacks), 1)
        dialog._cancel_test_or_close()
        self.assertTrue(dialog.test_cancel_event.is_set())

    def test_log_falls_back_without_queue_on_new_fixture(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._format_log_line = lambda text: f"{text}\n"
        app._write_log_line = Mock()
        app._append_log_line_to_ui = Mock()
        app._log("fallback")
        app._write_log_line.assert_called_once_with("fallback\n")
        app._append_log_line_to_ui.assert_called_once_with("fallback\n")

    def test_edit_grid_syncs_named_fields_before_opening_editor(self):
        dialog = GridRowConditionClickDialog.__new__(GridRowConditionClickDialog)
        dialog.region_vars = {
            name: self.FakeVar(value)
            for name, value in (("x", "1"), ("y", "2"), ("w", "30"), ("h", "40"))
        }
        dialog.grid_region = self.FakeVar("")
        dialog.source_image = self.FakeVar("")
        dialog.state = {}
        dialog._apply_grid = Mock()
        with patch("macroflow.ui.dialogs.GridLayoutEditor") as editor:
            editor.return_value.show = Mock()
            dialog._edit_grid()
        self.assertEqual(dialog.grid_region.get(), "1,2,30,40")
        editor.assert_called_once()

    def test_dialog_keeps_json_contract(self):
        self.assertTrue(hasattr(GridRowConditionClickDialog, "_build_action"))


if __name__ == "__main__":
    unittest.main()
