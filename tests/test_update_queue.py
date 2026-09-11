import unittest
import threading
from unittest.mock import Mock, patch

from macroflow.ui.dialogs import (
    condition_field_visibility,
    parse_named_region,
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


class DialogPureHelperTests(unittest.TestCase):
    def test_named_region_parsing(self):
        self.assertEqual(parse_named_region({"x": "1", "y": 2, "w": "30", "h": 40}), [1, 2, 30, 40])
        self.assertEqual(parse_named_region("1, 2, 30, 40"), [1, 2, 30, 40])

    def test_condition_visibility_has_no_irrelevant_fields(self):
        self.assertEqual(condition_field_visibility("image"), {"module"})
        self.assertEqual(condition_field_visibility("text"), {"text", "match"})
        self.assertEqual(condition_field_visibility("number"), {"separator", "relation"})

    def test_log_falls_back_without_queue_on_new_fixture(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._format_log_line = lambda text: f"{text}\n"
        app._write_log_line = Mock()
        app._append_log_line_to_ui = Mock()
        app._log("fallback")
        app._write_log_line.assert_called_once_with("fallback\n")
        app._append_log_line_to_ui.assert_called_once_with("fallback\n")


if __name__ == "__main__":
    unittest.main()
