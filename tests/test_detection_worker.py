from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))

import threading
import time
import unittest

from macroflow.execution.detection_worker import DetectionEvaluation, DetectionResult, DetectionWorker


class DetectionWorkerTests(unittest.TestCase):
    def test_submit_coalesces_to_one_pending_request(self):
        started = threading.Event()
        release = threading.Event()
        calls: list[tuple[int, int]] = []

        def evaluate(run_id: int, config_version: int):
            calls.append((run_id, config_version))
            if len(calls) == 1:
                started.set()
                release.wait(2)
            return {"run_id": run_id}

        worker = DetectionWorker(evaluate)
        try:
            worker.submit(1, 10)
            self.assertTrue(started.wait(1))
            worker.submit(1, 11)
            worker.submit(2, 12)
            release.set()

            results = self._poll_until(worker, 2)
            self.assertEqual([(result.run_id, result.config_version) for result in results],
                             [(1, 10), (2, 12)])
            self.assertEqual(calls, [(1, 10), (2, 12)])
        finally:
            worker.close()

    def test_result_contains_metadata_and_hit(self):
        worker = DetectionWorker(lambda run_id, config_version: {"guard_key": "g1"})
        try:
            submitted = time.monotonic()
            worker.submit(7, 8)
            result = self._poll_until(worker, 1)[0]
            self.assertEqual(result.run_id, 7)
            self.assertEqual(result.config_version, 8)
            self.assertEqual(result.hit, {"guard_key": "g1"})
            self.assertIsNone(result.error)
            self.assertGreaterEqual(result.submitted_at, submitted)
            self.assertGreaterEqual(result.completed_at, result.submitted_at)
        finally:
            worker.close()

    def test_evaluator_exception_is_returned_as_error(self):
        worker = DetectionWorker(lambda _run_id, _config_version: 1 / 0)
        try:
            worker.submit(3, 4)
            result = self._poll_until(worker, 1)[0]
            self.assertIsNone(result.hit)
            self.assertIsInstance(result.error, ZeroDivisionError)
        finally:
            worker.close()

    def test_close_stops_daemon_thread_and_rejects_new_requests(self):
        worker = DetectionWorker(lambda _run_id, _config_version: None)
        thread = worker.thread
        worker.close()
        self.assertFalse(thread.is_alive())
        with self.assertRaises(RuntimeError):
            worker.submit(1, 1)

    def test_evaluation_deferred_events_are_returned_as_result_metadata(self):
        worker = DetectionWorker(
            lambda _run_id, _config_version: DetectionEvaluation(
                {"guard_key": "g1"},
                ({"kind": "overlay", "x": 1, "y": 2, "width": 3, "height": 4},),
            ),
        )
        try:
            worker.submit(1, 2)
            result = self._poll_until(worker, 1)[0]
            self.assertEqual(result.hit, {"guard_key": "g1"})
            self.assertEqual(result.deferred_events[0]["kind"], "overlay")
        finally:
            worker.close()

    def test_results_are_bounded_and_close_does_not_wait_for_stuck_evaluator(self):
        started = threading.Event()
        release = threading.Event()

        def evaluate(_run_id, _config_version):
            started.set()
            release.wait(5)
            return "late"

        worker = DetectionWorker(evaluate, close_timeout=0.05)
        worker.submit(1, 1)
        self.assertTrue(started.wait(1))
        began_close = time.monotonic()
        worker.close()
        self.assertLess(time.monotonic() - began_close, 0.5)
        self.assertIsNone(worker.poll())
        release.set()
        worker.thread.join(1)
        self.assertFalse(worker.thread.is_alive())

    def test_result_buffer_keeps_only_latest_two_results(self):
        worker = DetectionWorker(lambda run_id, _config_version: run_id)
        try:
            for run_id in range(3):
                worker._results.append(DetectionResult(run_id, 1, 0.0, 0.0, run_id))
            self.assertLessEqual(len(worker._results), 2)
            self.assertEqual([worker.poll().hit, worker.poll().hit], [1, 2])
        finally:
            worker.close()

    @staticmethod
    def _poll_until(worker: DetectionWorker, count: int):
        results = []
        deadline = time.monotonic() + 2
        while len(results) < count and time.monotonic() < deadline:
            result = worker.poll()
            if result is not None:
                results.append(result)
            else:
                time.sleep(0.005)
        if len(results) != count:
            raise AssertionError(f"expected {count} results, got {len(results)}")
        return results


if __name__ == "__main__":
    unittest.main()
