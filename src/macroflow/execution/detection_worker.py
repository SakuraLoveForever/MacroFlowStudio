"""Bounded background execution for global screen-detection evaluations."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from collections.abc import Callable


@dataclass(frozen=True)
class DetectionResult:
    run_id: int
    config_version: int
    submitted_at: float
    completed_at: float
    hit: object | None = None
    error: BaseException | None = None

    @property
    def submitted_monotonic(self) -> float:
        return self.submitted_at

    @property
    def completed_monotonic(self) -> float:
        return self.completed_at


class DetectionWorker:
    """Run at most one evaluation and retain only the newest pending request."""

    def __init__(self, evaluator: Callable[[int, int], object | None]):
        self._evaluator = evaluator
        self._condition = threading.Condition()
        self._active = False
        self._pending: tuple[int, int, float] | None = None
        self._results: list[DetectionResult] = []
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="MacroFlowDetection",
            daemon=True,
        )
        self._thread.start()

    def submit(self, run_id: int, config_version: int) -> bool:
        """Start an evaluation or replace the single queued evaluation."""
        submitted_at = time.monotonic()
        with self._condition:
            if self._closed:
                return False
            self._pending = (int(run_id), int(config_version), submitted_at)
            self._condition.notify()
            return True

    def poll(self) -> DetectionResult | None:
        """Return the oldest completed result without blocking."""
        with self._condition:
            if not self._results:
                return None
            return self._results.pop(0)

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self._thread.join()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed and self._pending is None:
                    return
                run_id, config_version, submitted_at = self._pending
                self._pending = None
                self._active = True
            hit = None
            error = None
            try:
                hit = self._evaluator(run_id, config_version)
            except BaseException as exc:  # deliver evaluator failures to poll()
                error = exc
            completed_at = time.monotonic()
            with self._condition:
                self._active = False
                self._results.append(DetectionResult(
                    run_id=run_id,
                    config_version=config_version,
                    submitted_at=submitted_at,
                    completed_at=completed_at,
                    hit=hit,
                    error=error,
                ))
                self._condition.notify_all()
