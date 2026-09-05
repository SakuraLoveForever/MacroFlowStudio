import unittest

from macroflow.ui.dialogs import (
    GridRowConditionClickDialog,
    condition_field_visibility,
    format_grid_column_label,
    parse_named_region,
    test_state_transition,
)
from macroflow.ui.update_queue import UIUpdateQueue


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
        self.assertEqual(seen, ["stop"])

    def test_flush_is_safe_after_close(self):
        root = FakeRoot()
        queue = UIUpdateQueue(root)
        queue.close()
        queue.submit(lambda value: self.fail(value), "ignored")
        queue.flush()


class GridDialogPureHelperTests(unittest.TestCase):
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

    def test_dialog_keeps_json_contract(self):
        self.assertTrue(hasattr(GridRowConditionClickDialog, "_build_action"))


if __name__ == "__main__":
    unittest.main()
