from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class TimingSample:
    planned_offset_ms: float
    actual_offset_ms: float
    lateness_ms: float
    severe: bool


@dataclass
class TimingMetrics:
    scheduled_count: int = 0
    sent_count: int = 0
    severe_lateness_count: int = 0
    rebase_count: int = 0
    explicit_wait_ms: float = 0.0
    recognition_ms: float = 0.0
    stop_cleanup_ms: float = 0.0
    lateness_ms: list[float] = field(default_factory=list)

    def snapshot(self) -> dict[str, float | int]:
        values = sorted(self.lateness_ms)
        return {
            "scheduled_count": self.scheduled_count,
            "sent_count": self.sent_count,
            "severe_lateness_count": self.severe_lateness_count,
            "rebase_count": self.rebase_count,
            "explicit_wait_ms": self.explicit_wait_ms,
            "recognition_ms": self.recognition_ms,
            "stop_cleanup_ms": self.stop_cleanup_ms,
            "average_lateness_ms": sum(values) / len(values) if values else 0.0,
            "p95_lateness_ms": values[max(0, int(len(values) * 0.95) - 1)] if values else 0.0,
            "p99_lateness_ms": values[max(0, int(len(values) * 0.99) - 1)] if values else 0.0,
            "max_lateness_ms": values[-1] if values else 0.0,
        }


class PlaybackTimeline:
    def __init__(
        self,
        now: Callable[[], float],
        wait: Callable[[float], None],
        severe_lateness_ms: float = 100.0,
    ):
        self._now = now
        self._wait = wait
        self.severe_lateness_ms = severe_lateness_ms
        self.metrics = TimingMetrics()
        self._base_wall_time = 0.0
        self._base_offset_ms = 0.0
        self._last_scheduled_offset_ms = 0.0

    def start(self, first_offset_ms: float = 0.0):
        self._base_wall_time = self._now()
        self._base_offset_ms = first_offset_ms
        self._last_scheduled_offset_ms = first_offset_ms

    def wait_until(self, offset_ms: float) -> TimingSample:
        self.metrics.scheduled_count += 1
        self._last_scheduled_offset_ms = offset_ms
        target_duration = (offset_ms - self._base_offset_ms) / 1000.0
        elapsed_duration = self._now() - self._base_wall_time
        remaining = target_duration - elapsed_duration
        if remaining > 0:
            self._wait(remaining)

        actual_offset_ms = self._base_offset_ms + (
            (self._now() - self._base_wall_time) * 1000.0
        )
        lateness_ms = actual_offset_ms - offset_ms
        severe = lateness_ms > self.severe_lateness_ms
        self.metrics.sent_count += 1
        self.metrics.lateness_ms.append(lateness_ms)
        if severe:
            self.metrics.severe_lateness_count += 1
            self.rebase(offset_ms)
        return TimingSample(offset_ms, actual_offset_ms, lateness_ms, severe)

    def mark_boundary(self):
        self._base_wall_time = self._now()
        self._base_offset_ms = self._last_scheduled_offset_ms

    def rebase(self, offset_ms: float | None = None):
        if offset_ms is None:
            offset_ms = self._last_scheduled_offset_ms
        self._base_wall_time = self._now()
        self._base_offset_ms = offset_ms
        self._last_scheduled_offset_ms = offset_ms
        self.metrics.rebase_count += 1
