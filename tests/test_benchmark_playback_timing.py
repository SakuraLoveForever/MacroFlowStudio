import json
import subprocess
import sys
import unittest
from pathlib import Path


class PlaybackTimingBenchmarkTests(unittest.TestCase):
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
