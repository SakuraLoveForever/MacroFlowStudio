"""Workflow recovery without windows, input injection, or live screenshots."""
from __future__ import annotations

import tempfile
import threading
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path

add_src_to_path()


class WorkflowRecoveryTests(unittest.TestCase):
    def setUp(self):
        from macroflow.execution.recovery import WorkflowHealth, watchdog_action
        self.Health = WorkflowHealth
        self.action = watchdog_action

    def test_continuous_capture_failure_requests_recovery_after_30_seconds(self):
        health = self.Health(0)
        health.capture(False, 0)
        health.heartbeat(29)
        self.assertIsNone(health.reason(29))
        health.heartbeat(30)
        self.assertIn("截图", health.reason(30))

    def test_successful_capture_resets_failure_duration(self):
        health = self.Health(0)
        health.capture(False, 0)
        health.capture(True, 29)
        health.capture(False, 30)
        health.heartbeat(59)
        self.assertIsNone(health.reason(59))

    def test_long_wait_with_worker_heartbeat_is_healthy(self):
        health = self.Health(0)
        for now in range(1, 600):
            health.heartbeat(now)
            self.assertIsNone(health.reason(now))
        self.assertIn("心跳", health.reason(660))

    def test_watchdog_requests_stop_before_terminating(self):
        state = {"phase": "running", "updated_at": 100, "worker_at": 0, "ui_at": 100}
        self.assertEqual(self.action(state, 100, None), "stop")
        self.assertEqual(self.action(state, 109, 100), "wait")
        self.assertEqual(self.action(state, 110, 100), "restart")

    def test_waiting_for_unlock_does_not_require_worker_heartbeat(self):
        state = {"phase": "waiting_screen", "updated_at": 100, "worker_at": 0, "ui_at": 100}
        self.assertEqual(self.action(state, 100, None), "wait")
        state["updated_at"] = state["ui_at"] = 161
        self.assertEqual(self.action(state, 161, None), "wait")
        self.assertEqual(self.action(state, 161, 100), "wait")

    def test_cancelled_or_finished_runs_never_restart(self):
        for phase in ("cancelled", "finished"):
            self.assertEqual(self.action({"phase": phase}, 1000, 0), "exit")


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        from macroflow.execution.recovery import WorkflowSupervisor
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.worker = Mock()
        self.worker.is_alive.return_value = True
        self.detector = Mock()
        self.detector.thread.is_alive.return_value = True
        self.capture = Mock()
        self.ready = Mock()
        self.stop = Mock()
        self.supervisor = WorkflowSupervisor(
            Path(self.folder.name) / "state.json", ["python"],
            worker=lambda: self.worker, detector=lambda: self.detector,
            capture=self.capture, paused=lambda: False, snapshot=lambda: {"steps": []},
            stop=self.stop, ready=self.ready,
        )

    def test_waits_for_both_old_threads_and_screen_before_restart(self):
        self.supervisor.health.worker_at -= 61
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "recovering")
        self.worker.is_alive.return_value = False
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "recovering")
        self.detector.thread.is_alive.return_value = False
        self.capture.side_effect = OSError("desktop locked")
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "waiting_screen")
        self.capture.side_effect = None
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "waiting_screen")
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "ready")

    def test_cancel_while_waiting_for_screen_prevents_restart(self):
        self.supervisor.phase = "waiting_screen"
        self.supervisor.tick()
        self.supervisor.cancel()
        self.supervisor.tick()
        self.assertTrue(self.supervisor.cancelled.is_set())
        self.ready.assert_not_called()

    def test_successful_completion_disarms_recovery(self):
        self.supervisor.tick()
        self.worker.is_alive.return_value = False
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "finished")
        self.supervisor.request("late failure")
        self.assertEqual(self.supervisor.phase, "finished")

    def test_supervisor_can_be_armed_before_worker_starts(self):
        self.worker.is_alive.return_value = False
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "running")
        self.worker.is_alive.return_value = True
        self.supervisor.tick()
        self.supervisor.health.worker_at -= 61
        self.supervisor.tick()
        self.assertEqual(self.supervisor.phase, "recovering")


class RecoveryIntegrationTests(unittest.TestCase):
    def test_player_updates_heartbeat_during_a_long_pause(self):
        from macroflow.execution.player import MacroPlayer
        beats = []
        player = MacroPlayer()
        player.on_heartbeat = lambda: beats.append(True)
        player.pause()
        thread = threading.Thread(target=player.wait_while_paused)
        thread.start()
        threading.Event().wait(0.25)
        player.resume()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(beats), 2)

    def test_manual_stop_cancels_pending_recovery_first(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._cancel_workflow_recovery = Mock()
        app.recorder = Mock(running=False)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.hotkey_player = None
        for method in ("_refresh_execution_pause_controls", "_clear_global_detect_cooldowns",
                       "_clear_global_guards", "_leave_focus_mode", "_finish_execution_visibility",
                       "_set_status", "_log", "_sound"):
            setattr(app, method, Mock())
        app.worker = None
        app.stop_all()
        app._cancel_workflow_recovery.assert_called_once()
        self.assertTrue(app.workflow_stop.is_set())

    def test_queued_restart_cannot_revive_a_cancelled_workflow(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        recovery = Mock()
        recovery.cancelled = threading.Event()
        recovery.cancelled.set()
        app._workflow_recovery = recovery
        app.run_workflow = Mock()
        app._restart_recovered_workflow(recovery)
        app.run_workflow.assert_not_called()

    def test_restart_preserves_counts_and_skips_initial_schedule(self):
        from macroflow.ui.app.main import MacroFlowApp
        from macroflow.core.models import Workflow
        from macroflow.execution.recovery import WorkflowSupervisor
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"repeats": 0}, {"repeats": 3}])
        app.worker = None
        app.exiting = False
        app._clear_global_detect_cooldowns = lambda: None
        app._log = lambda text: None
        runs = []
        app.run_workflow = lambda **options: runs.append((app._automatic_restarting, options))
        with tempfile.TemporaryDirectory() as folder:
            recovery = WorkflowSupervisor(
                Path(folder) / "state.json", ["python"], worker=lambda: None,
                detector=lambda: None, capture=lambda: None, paused=lambda: False,
                snapshot=app.workflow.to_dict, stop=lambda reason: None, ready=lambda monitor: None,
            )
            app._workflow_recovery = recovery
            app._restart_recovered_workflow(recovery)
        self.assertEqual(runs, [(True, {"suppress_start_sound": True})])
        self.assertEqual([step["repeats"] for step in app.workflow.steps], [0, 3])
        self.assertTrue(recovery.cancelled.is_set())

    def test_f12_during_restart_preparation_prevents_new_run(self):
        from macroflow.ui.app.main import MacroFlowApp
        from macroflow.execution.recovery import WorkflowSupervisor
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app.exiting = False
        app._clear_global_detect_cooldowns = lambda: None
        app._log = lambda text: app._cancel_workflow_recovery()
        app.run_workflow = Mock()
        with tempfile.TemporaryDirectory() as folder:
            recovery = WorkflowSupervisor(
                Path(folder) / "state.json", ["python"], worker=lambda: None,
                detector=lambda: None, capture=lambda: None, paused=lambda: False,
                snapshot=lambda: {}, stop=lambda reason: None, ready=lambda monitor: None,
            )
            app._workflow_recovery = recovery
            app._restart_recovered_workflow(recovery)
        app.run_workflow.assert_not_called()

    def test_tray_quit_cancels_without_waiting_for_ui(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._ui = lambda *args: None  # simulate an unresponsive Tk queue
        recovery = Mock()
        app._workflow_recovery = recovery
        app._tray_exit()
        recovery.cancel.assert_called_once()

    def test_f12_during_startup_restore_prevents_arming(self):
        from macroflow.ui.app.main import MacroFlowApp
        from tests.helpers.core import FakeVar
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_name_var = FakeVar()
        app.workflow_start_var = FakeVar()
        app.workflow_test_mode_var = FakeVar(False)
        app.rebuild_workflow_tree = lambda: None
        app._persist_workflow_draft = app._cancel_workflow_recovery
        app._log = lambda text: None
        app._arm_workflow_recovery = Mock()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "resume.json"
            path.write_text(json.dumps({"name": "w", "steps": []}), encoding="utf-8")
            app._restore_recovery_workflow(path)
        self.assertEqual(app._recovery_cancel_generation, 1)
        app._arm_workflow_recovery.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Windows process-handle watchdog")
class HeadlessWatchdogTests(unittest.TestCase):
    """Use only background Python helpers, never launch the software UI."""

    def test_stuck_helper_exits_before_recovery_payload_is_launched(self):
        from macroflow.execution.recovery import write_json
        entry = Path(__file__).resolve().parents[1] / "src/macroflow/ui/app/__main__.py"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            result = Path(folder) / "result.json"
            target = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                writer = (
                    "import json,sys; from pathlib import Path; "
                    "payload=json.loads(Path(sys.argv[-1]).read_text(encoding='utf-8')); "
                    "Path(sys.argv[1]).write_text(json.dumps(payload),encoding='utf-8')"
                )
                workflow = {"name": "recover", "steps": [{"repeats": 3}]}
                now = time.monotonic()
                write_json(path, {
                    "phase": "recovering", "stop_at": now - 11,
                    "updated_at": now, "worker_at": now, "ui_at": now,
                    "workflow": workflow,
                    "command": [sys.executable, "-c", writer, str(result)],
                })
                watcher = subprocess.run(
                    [sys.executable, str(entry), "--watchdog", str(path), str(target.pid)],
                    capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(watcher.returncode, 0, watcher.stderr.decode(errors="replace"))
                self.assertIsNotNone(target.poll())
                deadline = time.monotonic() + 5
                while not result.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(json.loads(result.read_text()), workflow)
            finally:
                if target.poll() is None:
                    target.terminate()
                target.wait(timeout=5)

    def test_cancelled_watchdog_does_not_terminate_its_target(self):
        from macroflow.execution.recovery import write_json
        entry = Path(__file__).resolve().parents[1] / "src/macroflow/ui/app/__main__.py"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            write_json(path, {"phase": "cancelled"})
            target = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            try:
                watcher = subprocess.run(
                    [sys.executable, str(entry), "--watchdog", str(path), str(target.pid)],
                    capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(watcher.returncode, 0, watcher.stderr.decode(errors="replace"))
                self.assertIsNone(target.poll())
            finally:
                target.terminate()
                target.wait(timeout=5)

    def test_cancel_during_termination_prevents_late_launch(self):
        from macroflow.execution.recovery import run_watchdog, write_json
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            now = time.monotonic()
            write_json(path, {"phase": "recovering", "stop_at": now - 11,
                              "updated_at": now, "worker_at": now, "ui_at": now,
                              "workflow": {}, "command": ["python"]})
            kernel = Mock()
            kernel.OpenProcess.return_value = 1
            kernel.WaitForSingleObject.side_effect = [258, 258, 0]
            def cancel_during_terminate(*args):
                path.with_suffix(".cancel").touch()
                return True
            kernel.TerminateProcess.side_effect = cancel_during_terminate
            with patch("ctypes.WinDLL", return_value=kernel), \
                    patch("time.sleep"), patch("subprocess.Popen") as launch:
                run_watchdog(path, 123)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
