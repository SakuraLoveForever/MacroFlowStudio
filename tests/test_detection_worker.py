from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from macroflow.execution.detection_worker import DetectionWorker


class DetectionWorkerTests(unittest.TestCase):
    def test_coalesces_pending_requests_and_reports_monotonic_metadata(self):
        first_started = threading.Event()
        release_first = threading.Event()
        calls = []

        def evaluate(run_id, config_version):
            request = (run_id, config_version)
            calls.append(request)
            if request == (1, 1):
                first_started.set()
                self.assertTrue(release_first.wait(2))
            return {"request": request}

        worker = DetectionWorker(evaluate)
        try:
            submitted_first = worker.submit(1, 1)
            self.assertTrue(first_started.wait(2))
            worker.submit(1, 2)
            worker.submit(1, 3)
            release_first.set()

            results = []
            deadline = time.monotonic() + 2
            while len(results) < 2 and time.monotonic() < deadline:
                result = worker.poll()
                if result is not None:
                    results.append(result)
                else:
                    time.sleep(0.01)

            self.assertEqual(calls, [(1, 1), (1, 3)])
            self.assertEqual([result.hit for result in results], [
                {"request": (1, 1)}, {"request": (1, 3)},
            ])
            self.assertEqual(results[0].run_id, 1)
            self.assertEqual(results[1].config_version, 3)
            self.assertGreaterEqual(results[0].completed_at, submitted_first)
            self.assertLessEqual(results[0].submitted_at, results[0].completed_at)
            self.assertLessEqual(results[1].submitted_at, results[1].completed_at)
        finally:
            worker.close()

    def test_captures_evaluator_exception_without_leaving_worker_stuck(self):
        worker = DetectionWorker(
            lambda _run_id, _config_version: (_ for _ in ()).throw(ValueError("bad guard")),
        )
        try:
            worker.submit(4, 9)
            deadline = time.monotonic() + 2
            result = None
            while result is None and time.monotonic() < deadline:
                result = worker.poll()
                if result is None:
                    time.sleep(0.01)
            self.assertIsNotNone(result)
            self.assertIsNone(result.hit)
            self.assertIsInstance(result.error, ValueError)
            self.assertEqual((result.run_id, result.config_version), (4, 9))
        finally:
            worker.close()

    def test_close_prevents_new_requests_and_joins_daemon_thread(self):
        worker = DetectionWorker(lambda _request: None)
        worker.close()
        self.assertFalse(worker.submit(1, 1))
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
