"""Workflow health monitoring and a headless, independent Windows watchdog."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback

CAPTURE_TIMEOUT = 30.0
HEARTBEAT_TIMEOUT = 60.0
STOP_TIMEOUT = 10.0


class CaptureUnavailable(RuntimeError):
    """A persistent capture failure that a full workflow can recover from."""


class WorkflowHealth:
    def __init__(self, now: float):
        self.worker_at = now
        self.capture_failed_at = None

    def heartbeat(self, now: float) -> None:
        self.worker_at = now

    def capture(self, success: bool, now: float) -> None:
        if success:
            self.capture_failed_at = None
        elif self.capture_failed_at is None:
            self.capture_failed_at = now

    def reason(self, now: float) -> str | None:
        if now - self.worker_at >= HEARTBEAT_TIMEOUT:
            return "工作线程心跳超过 60 秒未更新"
        if self.capture_failed_at is not None and now - self.capture_failed_at >= CAPTURE_TIMEOUT:
            return "屏幕截图连续 30 秒失败"
        return None


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def watchdog_action(state: dict, now: float, stop_requested_at: float | None) -> str:
    if state.get("phase") in {"cancelled", "finished"}:
        return "exit"
    acknowledged = (state.get("phase") in {"waiting_screen", "ready"}
                    and stop_requested_at is not None
                    and min(state["updated_at"], state["ui_at"]) > stop_requested_at)
    stop_at = state.get("stop_at", None if acknowledged else stop_requested_at)
    if stop_at is not None:
        return "restart" if now - stop_at >= STOP_TIMEOUT else "wait"
    clocks = [state["updated_at"], state["ui_at"]]
    if state.get("phase") == "running":
        clocks.append(state["worker_at"])
    return "stop" if now - min(clocks) >= HEARTBEAT_TIMEOUT else "wait"


class WorkflowSupervisor:
    """One supervisor per full workflow; old workers must exit before recovery."""

    def __init__(self, path: Path, command: list[str], *, worker, detector,
                 capture, paused, snapshot, stop, ready, waiting_screen=False):
        self.path = path
        self.request_path = path.with_suffix(".stop")
        self.command = command
        self.worker = worker
        self.detector = detector
        self.capture_screen = capture
        self.paused = paused
        self.snapshot = snapshot
        self.stop = stop
        self.ready = ready
        now = time.monotonic()
        self.health = WorkflowHealth(now)
        self.ui_at = now
        self.phase = "waiting_screen" if waiting_screen else "running"
        self.stop_at = None
        self.cancelled = threading.Event()
        self._write_lock = threading.RLock()
        self._screen_successes = 0
        self.detector_pending_at = None
        self._worker_seen_alive = False
        self.thread = threading.Thread(target=self._run, name="workflow-health", daemon=True)

    def heartbeat(self) -> None:
        self.health.heartbeat(time.monotonic())

    def ui_heartbeat(self) -> None:
        self.ui_at = time.monotonic()

    def _save(self) -> None:
        with self._write_lock:
            state = {
                "phase": "cancelled" if self.cancelled.is_set() else self.phase,
                "updated_at": time.monotonic(), "worker_at": self.health.worker_at,
                "ui_at": self.ui_at, "command": self.command,
                "workflow": self.snapshot(),
            }
            if self.stop_at is not None and self.phase == "recovering":
                state["stop_at"] = self.stop_at
            write_json(self.path, state)

    def cancel(self) -> None:
        self.cancelled.set()
        # This small independent marker also works while the writer owns its lock.
        try:
            self.path.with_suffix(".cancel").touch()
            self._save()
        except OSError:
            # Manual stop must still stop playback when the disk is unavailable.
            pass

    def finish(self) -> None:
        with self._write_lock:
            if self.phase == "running":
                self.phase = "finished"
                self._save()

    def checkpoint(self) -> None:
        self._save()

    def start(self) -> None:
        self._save()
        self.thread.start()

    def _capture(self, now: float) -> bool:
        try:
            self.capture_screen()
        except Exception:
            self.health.capture(False, now)
            return False
        self.health.capture(True, now)
        return True

    def request(self, reason: str) -> None:
        with self._write_lock:
            if self.cancelled.is_set() or self.phase != "running":
                return
            self.phase = "recovering"
            self.stop_at = time.monotonic()
            # Persist before any stop/diagnostic callback that might itself block.
            self._save()
        self.dump_stacks(reason)
        if not self.cancelled.is_set():
            self.stop(reason)

    def tick(self) -> None:
        if self.cancelled.is_set():
            return
        now = time.monotonic()
        worker = self.worker()
        alive = worker is not None and worker.is_alive()
        if self.phase == "running":
            if alive:
                self._worker_seen_alive = True
            if not alive:
                if not self._worker_seen_alive:
                    # The UI arms supervision before starting the worker, so even
                    # an immediate capture exception can request recovery.
                    self._save()
                    return
                self.finish()
                return
            if self.paused():
                self.health.capture(True, now)
            else:
                self._capture(now)
            reason = self.health.reason(now)
            if self.detector_pending_at is not None and now - self.detector_pending_at >= HEARTBEAT_TIMEOUT:
                reason = "全局检测请求超过 60 秒未返回"
            if self.request_path.exists():
                reason = "独立看门狗检测到执行或界面心跳失联"
            if reason:
                self.request(reason)
        if self.phase == "recovering":
            detection = self.detector()
            detection_alive = detection is not None and detection.thread.is_alive()
            if not alive and not detection_alive:
                self.phase = "waiting_screen"
                self._screen_successes = 0
        if self.phase == "waiting_screen":
            self._screen_successes = self._screen_successes + 1 if self._capture(now) else 0
            if self._screen_successes >= 2 and not self.cancelled.is_set():
                self.phase = "ready"
                self._save()
                self.ready(self)
        self._save()

    def dump_stacks(self, reason: str) -> None:
        frames = sys._current_frames()
        stacks = [f"{time.strftime('%Y-%m-%d %H:%M:%S')} 自动恢复：{reason}\n"]
        for thread in threading.enumerate():
            frame = frames.get(thread.ident)
            if frame is not None:
                stacks.append(f"\n线程 {thread.name} ({thread.ident})\n" + "".join(traceback.format_stack(frame)))
        self.path.with_suffix(".diagnostic.log").write_text("".join(stacks), encoding="utf-8")

    def _run(self) -> None:
        while not self.cancelled.wait(1):
            try:
                self.tick()
                if self.phase == "finished":
                    return
            except Exception:
                # The external watchdog will detect a stalled supervisor too.
                self.path.with_suffix(".monitor-error.log").write_text(traceback.format_exc(), encoding="utf-8")
                return


def run_watchdog(path: Path, pid: int) -> None:
    """Hold a handle to the exact process; never kill a reused PID or its games."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000 | 0x0001, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    requested_at = None
    try:
        while True:
            time.sleep(1)
            if path.with_suffix(".cancel").exists():
                return
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            action = watchdog_action(state, time.monotonic(), requested_at)
            if action == "exit":
                return
            if kernel.WaitForSingleObject(handle, 0) == 0:
                # Unexpected process exit during an armed workflow also recovers.
                action = "restart"
            if action == "stop":
                requested_at = time.monotonic()
                path.with_suffix(".stop").write_text("stop", encoding="ascii")
            elif action == "restart":
                # Recheck cancellation at the last possible point before termination.
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("phase") in {"cancelled", "finished"} \
                        or path.with_suffix(".cancel").exists():
                    return
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    if not kernel.TerminateProcess(handle, 1):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if kernel.WaitForSingleObject(handle, 10000) != 0:
                        raise RuntimeError("旧软件进程未退出，取消自动启动")
                # F12/normal completion may have occurred while termination was
                # pending; use the last snapshot only after the old process exits.
                state = json.loads(path.read_text(encoding="utf-8"))
                if state.get("phase") in {"cancelled", "finished"} \
                        or path.with_suffix(".cancel").exists():
                    return
                resume = path.with_suffix(".resume.json")
                write_json(resume, state["workflow"])
                from subprocess import Popen, CREATE_NO_WINDOW
                env = {k: v for k, v in os.environ.items() if k not in {"_MEIPASS", "_MEIPASS2"}}
                if getattr(sys, "frozen", False):
                    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
                Popen([*state["command"], "--recover-workflow", str(resume)],
                      env=env, creationflags=CREATE_NO_WINDOW)
                path.with_suffix(".watchdog.log").write_text(
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')} 旧进程 {pid} 已退出，重启软件并等待屏幕恢复。\n",
                    encoding="utf-8",
                )
                return
    finally:
        kernel.CloseHandle(handle)
