"""One lightweight animation clock shared by the Tk application."""

from __future__ import annotations

import time


def ease_out(progress: float) -> float:
    return 1 - (1 - progress) ** 3


def ease_back(progress: float) -> float:
    overshoot = 1.1
    return 1 + (overshoot + 1) * (progress - 1) ** 3 + overshoot * (progress - 1) ** 2


class UiAnimator:
    def __init__(self, root, *, clock=time.perf_counter):
        self.root = root
        self.clock = clock
        self._animations: dict[object, tuple[float, float, object, object]] = {}
        self._job = None
        self._paused_at = None
        root.bind_all("<FocusIn>", self._resume, add="+")
        root.bind_all("<FocusOut>", self._pause_if_unfocused, add="+")
        root.bind("<Map>", self._resume, add="+")
        root.bind("<Unmap>", self._pause_if_unmapped, add="+")

    def _can_run(self) -> bool:
        try:
            return self.root.state() == "normal" and self.root.focus_displayof() is not None
        except Exception:
            return False

    def animate(self, key, duration_ms: int, callback, easing=ease_out) -> None:
        self._animations[key] = (self.clock(), duration_ms / 1000, callback, easing)
        self._start()

    def _start(self) -> None:
        if not self._animations or self._job is not None:
            return
        if not self._can_run():
            if self._paused_at is None:
                self._paused_at = self.clock()
            return
        if self._paused_at is not None:
            paused = self.clock() - self._paused_at
            self._animations = {
                key: (started + paused, duration, callback, easing)
                for key, (started, duration, callback, easing) in self._animations.items()
            }
            self._paused_at = None
        self._job = self.root.after(16, self._tick)

    def _tick(self) -> None:
        self._job = None
        if not self._can_run():
            self._paused_at = self.clock()
            return
        now = self.clock()
        for key, (started, duration, callback, easing) in tuple(self._animations.items()):
            progress = min(1.0, max(0.0, (now - started) / duration))
            try:
                callback(easing(progress))
            except Exception:
                self._animations.pop(key, None)
                continue
            if progress >= 1:
                self._animations.pop(key, None)
        self._start()

    def _pause_if_unfocused(self, _event=None) -> None:
        if not self._can_run():
            self._pause()

    def _pause_if_unmapped(self, event) -> None:
        if event.widget is self.root:
            self._pause()

    def _pause(self) -> None:
        if self._job is not None:
            self.root.after_cancel(self._job)
            self._job = None
        if self._animations and self._paused_at is None:
            self._paused_at = self.clock()

    def _resume(self, _event=None) -> None:
        self._start()


def animator_for(widget) -> UiAnimator:
    root = widget._root()
    animator = getattr(root, "_macroflow_animator", None)
    if animator is None:
        animator = root._macroflow_animator = UiAnimator(root)
    return animator
