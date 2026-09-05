import argparse
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path

from scripts import benchmark_playback_timing


class PlaybackTimingBenchmarkTests(unittest.TestCase):
    def test_dropped_events_count_against_requested_event_count(self):
        class FakeTimeline:
            metrics = SimpleNamespace(
                scheduled_count=3,
                sent_count=2,
                snapshot=lambda: {
                    "scheduled_count": 3,
                    "sent_count": 2,
                    "average_lateness_ms": 0.0,
                    "p95_lateness_ms": 0.0,
                    "p99_lateness_ms": 0.0,
                    "max_lateness_ms": 0.0,
                },
            )

            def __init__(self, now, wait):
                self.wait_until_calls = 0

            def start(self):
                pass

            def wait_until(self, offset_ms):
                self.wait_until_calls += 1

        with mock.patch.object(benchmark_playback_timing, "PlaybackTimeline", FakeTimeline):
            with mock.patch.object(
                benchmark_playback_timing.time,
                "perf_counter",
                side_effect=[0.0, 1.0],
            ):
                result = benchmark_playback_timing.run_case(1.0, 3)

        self.assertEqual(result["dropped_event_count"], 1)

    def test_parse_durations_rejects_non_finite_values(self):
        for value in ("nan", "inf", "-inf", "1,nan"):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    benchmark_playback_timing.parse_durations(value)

    def test_events_one_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            benchmark_playback_timing.main(["--durations", "0", "--events", "1", "--json"])

        self.assertEqual(raised.exception.code, 2)

    def test_json_mode_reports_one_complete_result_per_duration(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_playback_timing.py"
        completed = subprocess.run(
            [sys.executable, str(script), "--durations", "0.01,0.02", "--events", "3", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )

        results = [json.loads(line) for line in completed.stdout.splitlines()]

        self.assertEqual([result["duration_s"] for result in results], [0.01, 0.02])
        for result in results:
            self.assertEqual(result["event_count"], 3)
            self.assertEqual(result["dropped_event_count"], 0)
            for field in (
                "drift_ms",
                "average_lateness_ms",
                "p95_lateness_ms",
                "p99_lateness_ms",
                "max_lateness_ms",
            ):
                self.assertIn(field, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
