"""Bounded background execution for global guard detection."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from threading import Condition, Thread
from typing import Callable


@dataclass(frozen=True)
class DetectionResult:
    run_id: int
    config_version: int
    submitted_at: float
    completed_at: float
    hit: object | None = None
    error: BaseException | None = None
    deferred_events: tuple[dict, ...] = ()


@dataclass(frozen=True)
class DetectionEvaluation:
    """Pure evaluator output; side effects are consumed by the player thread."""

    hit: object | None = None
    deferred_events: tuple[dict, ...] = ()


@dataclass(frozen=True)
class _DetectionRequest:
    run_id: int
    config_version: int
    submitted_at: float


class DetectionWorker:
    """Run at most one detection request plus one replaceable pending request."""

    def __init__(self, evaluator: Callable[[int, int], object | None], *, close_timeout: float = 0.2):
        self._evaluator = evaluator
        self._condition = Condition()
        self._active: _DetectionRequest | None = None
        self._pending: _DetectionRequest | None = None
        self._results: deque[DetectionResult] = deque(maxlen=2)
        self._closed = False
        self._close_timeout = max(0.0, float(close_timeout))
        self.thread = Thread(
            target=self._run,
            name="macroflow-detection-worker",
            daemon=True,
        )
        self.thread.start()

    def submit(self, run_id: int, config_version: int) -> None:
        request = _DetectionRequest(run_id, config_version, time.monotonic())
        with self._condition:
            if self._closed:
                raise RuntimeError("detection worker is closed")
            if self._active is None:
                self._active = request
            else:
                self._pending = request
            self._condition.notify()

    def poll(self) -> DetectionResult | None:
        with self._condition:
            if not self._results:
                return None
            return self._results.popleft()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        self.thread.join(self._close_timeout)

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._active is None and not self._closed:
                    self._condition.wait()
                if self._active is None and self._closed:
                    return
                request = self._active
                self._active = None

            hit = None
            deferred_events: tuple[dict, ...] = ()
            error = None
            try:
                evaluation = self._evaluator(request.run_id, request.config_version)
                if isinstance(evaluation, DetectionEvaluation):
                    hit = evaluation.hit
                    deferred_events = tuple(evaluation.deferred_events)
                else:
                    hit = evaluation
            except BaseException as exc:  # worker errors must reach the player thread
                error = exc
            completed_at = time.monotonic()
            with self._condition:
                if self._closed:
                    return
                self._results.append(
                    DetectionResult(
                        request.run_id,
                        request.config_version,
                        request.submitted_at,
                        completed_at,
                        hit,
                        error,
                        deferred_events,
                    )
                )
                if self._pending is not None:
                    self._active = self._pending
                    self._pending = None
                elif self._closed:
                    return
