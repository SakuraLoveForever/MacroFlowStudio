import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macroflow.execution.timeline import PlaybackTimeline, TimingMetrics


class FakeClock:
    def __init__(self):
        self.value = 100.0
        self.waits = []

    def now(self):
        return self.value

    def wait(self, seconds):
        self.waits.append(seconds)
        self.value += seconds


class PlaybackTimelineTests(unittest.TestCase):
    def test_wait_until_uses_absolute_recorded_offset(self):
        clock = FakeClock()
        timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        timeline.start()

        first = timeline.wait_until(100.0)
        clock.value += 0.003
        second = timeline.wait_until(250.0)

        self.assertEqual(clock.waits, [0.1, 0.147])
        self.assertAlmostEqual(first.lateness_ms, 0.0, places=3)
        self.assertAlmostEqual(second.planned_offset_ms, 250.0, places=3)

    def test_boundary_keeps_future_events_from_bursting_after_explicit_wait(self):
        clock = FakeClock()
        timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        timeline.start()
        timeline.wait_until(100.0)
        clock.value += 0.4
        timeline.mark_boundary()
        timeline.wait_until(150.0)
        self.assertEqual(clock.waits, [0.1, 0.05])

    def test_severe_lateness_rebases_future_target(self):
        clock = FakeClock()
        timeline = PlaybackTimeline(now=clock.now, wait=clock.wait, severe_lateness_ms=20)
        timeline.start()
        timeline.wait_until(10.0)
        clock.value += 0.05
        sample = timeline.wait_until(20.0)
        timeline.wait_until(30.0)
        self.assertTrue(sample.severe)
        self.assertEqual(timeline.metrics.rebase_count, 1)
        self.assertEqual(clock.waits, [0.01, 0.01])

    def test_metrics_snapshot_reports_sorted_lateness_percentiles(self):
        metrics = TimingMetrics(
            scheduled_count=4,
            sent_count=4,
            severe_lateness_count=1,
            rebase_count=1,
            explicit_wait_ms=12.5,
            recognition_ms=3.5,
            stop_cleanup_ms=1.5,
            lateness_ms=[40.0, 10.0, 30.0, 20.0],
        )

        self.assertEqual(
            metrics.snapshot(),
            {
                "scheduled_count": 4,
                "sent_count": 4,
                "severe_lateness_count": 1,
                "rebase_count": 1,
                "explicit_wait_ms": 12.5,
                "recognition_ms": 3.5,
                "stop_cleanup_ms": 1.5,
                "average_lateness_ms": 25.0,
                "p95_lateness_ms": 30.0,
                "p99_lateness_ms": 30.0,
                "max_lateness_ms": 40.0,
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
