"""Measure real-clock PlaybackTimeline scheduling without starting the app."""

import argparse
import json
import math
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from macroflow.execution.timeline import PlaybackTimeline  # noqa: E402


def parse_durations(value: str) -> list[float]:
    durations = [float(item.strip()) for item in value.split(",") if item.strip()]
    if not durations or any(not math.isfinite(duration) or duration < 0 for duration in durations):
        raise argparse.ArgumentTypeError(
            "durations must be a non-empty comma-separated list of finite, non-negative seconds"
        )
    return durations


def run_case(duration_s: float, event_count: int) -> dict[str, float | int]:
    timeline = PlaybackTimeline(now=time.perf_counter, wait=time.sleep)
    started_at = time.perf_counter()
    timeline.start()
    for index in range(event_count):
        offset_ms = duration_s * 1000 * index / max(1, event_count - 1)
        timeline.wait_until(offset_ms)

    result = timeline.metrics.snapshot()
    result["duration_s"] = duration_s
    result["event_count"] = event_count
    result["drift_ms"] = (time.perf_counter() - started_at) * 1000 - duration_s * 1000
    result["dropped_event_count"] = event_count - timeline.metrics.sent_count
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--durations", type=parse_durations, default=[10.0, 60.0, 600.0])
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--json", action="store_true", help="emit one JSON object per duration")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.events < 2:
        parser.error("events must be at least 2")

    for result in (run_case(duration, args.events) for duration in args.durations):
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            print(
                f"duration={result['duration_s']}s events={result['event_count']} "
                f"drift={result['drift_ms']:.3f}ms "
                f"max_lateness={result['max_lateness_ms']:.3f}ms"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
