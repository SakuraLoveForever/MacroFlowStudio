"""Thread-safe batching of callbacks that must run on the Tk thread."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable


@dataclass
class _Update:
    callback: Callable
    args: tuple
    key: str | None = None
    batch_key: str | None = None
    urgent: bool = False


class UIUpdateQueue:
    """Coalesce state updates while preserving ordered log/action updates."""

    def __init__(self, root, interval_ms: int = 50):
        self.root = root
        self.interval_ms = max(1, int(interval_ms))
        self._owner_thread_id = threading.get_ident()
        self._lock = threading.RLock()
        self._updates: list[_Update] = []
        self._scheduled_id = None
        self._closed = False

    def submit(self, callback: Callable, *args, key: str | None = None,
               batch_key: str | None = None, urgent: bool = False) -> bool:
        with self._lock:
            if self._closed:
                return False
            if key is not None:
                for index, update in enumerate(self._updates):
                    if update.key == key:
                        self._updates[index] = _Update(callback, args, key, batch_key, urgent)
                        break
                else:
                    self._updates.append(_Update(callback, args, key, batch_key, urgent))
            elif batch_key is not None and self._updates:
                previous = self._updates[-1]
                if previous.batch_key == batch_key and self._same_callback(previous.callback, callback):
                    self._updates[-1] = _Update(
                        callback, previous.args + args, None, batch_key,
                        previous.urgent or urgent,
                    )
                else:
                    self._updates.append(_Update(callback, args, None, batch_key, urgent))
            else:
                self._updates.append(_Update(callback, args, None, batch_key, urgent))
        # Producer threads must never touch Tk.  ``urgent`` is intentionally
        # only retained as queue priority and is drained by the main-thread pump.
        return True

    def start(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("UIUpdateQueue.start() must run on the Tk thread")
        with self._lock:
            self._schedule_locked()

    def _schedule_locked(self) -> None:
        if self._scheduled_id is not None or self._closed:
            return
        try:
            self._scheduled_id = self.root.after(self.interval_ms, self._scheduled_flush)
        except (RuntimeError, AttributeError):
            self._scheduled_id = None

    def _scheduled_flush(self) -> None:
        with self._lock:
            self._scheduled_id = None
        self.pump()

    def _take_locked(self) -> list[_Update]:
        updates = self._updates
        self._updates = []
        scheduled_id = self._scheduled_id
        self._scheduled_id = None
        if scheduled_id is not None:
            try:
                self.root.after_cancel(scheduled_id)
            except (RuntimeError, AttributeError):
                pass
        return updates

    def flush(self) -> int:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("UIUpdateQueue.flush() must run on the Tk thread")
        with self._lock:
            updates = self._take_locked()
        self._run([update for update in updates if update.urgent])
        self._run([update for update in updates if not update.urgent])
        with self._lock:
            self._schedule_locked()
        return len(updates)

    def pump(self) -> int:
        return self.flush()

    @staticmethod
    def _same_callback(left: Callable, right: Callable) -> bool:
        return left is right or (
            getattr(left, "__self__", None) is getattr(right, "__self__", None)
            and getattr(left, "__func__", None) is getattr(right, "__func__", None)
        )

    @staticmethod
    def _run(updates: list[_Update]) -> None:
        for update in updates:
            if update.batch_key is None:
                update.callback(*update.args)
            else:
                update.callback(list(update.args))

    def close(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise RuntimeError("UIUpdateQueue.close() must run on the Tk thread")
        with self._lock:
            if self._closed:
                return
            updates = self._take_locked()
            self._closed = True
        self._run(updates)
