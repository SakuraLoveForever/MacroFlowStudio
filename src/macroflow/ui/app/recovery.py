"""Automatic recovery of full workflows; F12 always cancels the handoff."""
from __future__ import annotations

import os
import threading
from pathlib import Path
import uuid

from macroflow.core.image_match import capture_bgr
from macroflow.core.models import Workflow
from macroflow.execution.recovery import WorkflowSupervisor
from .startup import instance_command, spawn_new_instance


class RecoveryMixin:
    def _recovery_lock(self):
        return self.__dict__.setdefault("_recovery_lifecycle_lock", threading.RLock())

    def _emergency_stop_from_hook(self) -> None:
        # F12 must disarm the independent watchdog even if Tk is unresponsive.
        self._cancel_workflow_recovery()
        self.workflow_stop.set()
        self.player.stop()
        self._ui(self.stop_all)

    def _workflow_heartbeat(self) -> None:
        recovery = getattr(self, "_workflow_recovery", None)
        # Other callbacks (e.g. OCR detection) must not mask a stuck player.
        if recovery is not None and threading.current_thread() is self.worker:
            recovery.heartbeat()

    def _finish_workflow_recovery(self) -> None:
        recovery = getattr(self, "_workflow_recovery", None)
        if recovery is not None:
            recovery.finish()

    def _cancel_workflow_recovery(self, *, manual=True) -> None:
        with self._recovery_lock():
            if manual:
                self._recovery_cancel_generation = getattr(self, "_recovery_cancel_generation", 0) + 1
            recovery = getattr(self, "_workflow_recovery", None)
            if recovery is not None:
                recovery.cancel()
                self._workflow_recovery = None

    def _arm_workflow_recovery(self, *, waiting_screen=False) -> None:
        self._cancel_workflow_recovery(manual=False)
        detection = getattr(self, "_detection_worker", None)
        path = self.session_log_path.with_name(f"workflow_recovery_{os.getpid()}_{uuid.uuid4().hex}.json")
        recovery = WorkflowSupervisor(
            path, instance_command(), worker=lambda: self.worker,
            detector=lambda: detection, capture=capture_bgr,
            paused=lambda: self.player.paused,
            snapshot=lambda: self.workflow.to_dict(),
            stop=lambda reason: self._stop_for_workflow_recovery(recovery, reason),
            ready=lambda monitor: self._ui(self._restart_recovered_workflow, monitor),
            waiting_screen=waiting_screen,
        )
        self._workflow_recovery = recovery
        try:
            recovery.start()
            spawn_new_instance(
                [*instance_command(), "--watchdog", str(path), str(os.getpid())],
                background=True,
            )
        except Exception as exc:
            recovery.cancel()
            self._workflow_recovery = None
            self._log(f"无法启用工作流自动恢复：{exc}")
            return
        self._pulse_workflow_recovery(recovery)

    def _pulse_workflow_recovery(self, recovery) -> None:
        if getattr(self, "_workflow_recovery", None) is not recovery \
                or recovery.cancelled.is_set() or getattr(self, "exiting", False) \
                or recovery.phase == "finished":
            return
        recovery.ui_heartbeat()
        self.root.after(1000, self._pulse_workflow_recovery, recovery)

    def _stop_for_workflow_recovery(self, recovery, reason: str) -> None:
        with self._recovery_lock():
            if getattr(self, "_workflow_recovery", None) is not recovery or recovery.cancelled.is_set():
                return
            self.workflow_restart_requested = False
            self.workflow_stop.set()
            self.player.stop()
        hotkey = getattr(self, "hotkey_player", None)
        if hotkey is not None:
            hotkey.stop()
        self._ui(self._show_workflow_recovery_wait, recovery, reason)
        self._ui(self._log, f"工作流自动恢复：{reason}；已请求停止，等待旧线程退出及屏幕恢复。")

    def _show_workflow_recovery_wait(self, recovery, reason: str) -> None:
        if getattr(self, "_workflow_recovery", None) is not recovery or recovery.cancelled.is_set():
            return
        self._leave_focus_mode()
        self._finish_execution_visibility()
        self._set_status("自动恢复：等待旧线程退出及屏幕恢复 · F12 取消", "warning")

    def _request_workflow_recovery(self, reason: str) -> bool:
        recovery = getattr(self, "_workflow_recovery", None)
        if recovery is None or recovery.cancelled.is_set():
            return False
        recovery.request(reason)
        return True

    def _restart_recovered_workflow(self, recovery) -> None:
        with self._recovery_lock():
            if getattr(self, "_workflow_recovery", None) is not recovery \
                    or recovery.cancelled.is_set() or getattr(self, "exiting", False):
                return
            if self.worker is not None and self.worker.is_alive():
                return
            generation = getattr(self, "_recovery_cancel_generation", 0)
            self._cancel_workflow_recovery(manual=False)
        self._clear_global_detect_cooldowns()
        self._log("屏幕已恢复，旧工作线程已退出：从工作流第 1 步重新运行，保留各行剩余次数。")
        if generation != getattr(self, "_recovery_cancel_generation", 0):
            return
        self._automatic_restarting = True
        self._automatic_recovery_generation = generation
        try:
            self.run_workflow(suppress_start_sound=True)
        finally:
            self._automatic_restarting = False
            self._automatic_recovery_generation = None

    def _restore_recovery_workflow(self, path: Path) -> None:
        import json

        generation = getattr(self, "_recovery_cancel_generation", 0)
        if generation:
            self._log("软件启动期间已收到停止指令，取消工作流自动恢复。")
            return
        workflow = Workflow.from_dict(json.loads(path.read_text(encoding="utf-8")))
        self.workflow = workflow
        self.workflow_name_var.set(workflow.name)
        self.workflow_start_var.set(workflow.start_at)
        self.workflow_test_mode_var.set(False)
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        path.unlink()
        self._log("独立看门狗已重启软件：等待屏幕恢复后，从工作流第 1 步继续自动运行；F12 可取消。")
        with self._recovery_lock():
            if generation == getattr(self, "_recovery_cancel_generation", 0):
                self._arm_workflow_recovery(waiting_screen=True)
