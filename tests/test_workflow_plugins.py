"""Workflow process watchdog, tested without opening any windows."""
import json
import threading
import unittest
from unittest.mock import Mock, patch

from macroflow.core.models import Workflow
from macroflow.execution.player import PlaybackStopped
from tests.test_global_detect import GuardTestHelpers


class ProcessAbsencePluginTests(GuardTestHelpers, unittest.TestCase):
    def _app(self, **settings):
        app = self._make_guard_app()
        app.workflow_stop = threading.Event()
        config = {"plugin": "process_absence_restart", "process_name": "Game.exe", **settings}
        step = {"kind": "global_module", "step_id": "watchdog", "config": config}
        app.workflow = Workflow(steps=[step, {"script": "task.json"}])
        app._register_workflow_global_modules([step])
        return app, app.global_guards["workflow:watchdog"]

    def _observe(self, app, guard, now, present=False):
        with patch("macroflow.ui.app.guards.is_process_running", return_value=present):
            return app._evaluate_one_guard(guard, None, None, now)

    def test_default_minute_of_absence_requests_restart_from_first_row(self):
        app, guard = self._app()
        self.assertEqual(guard["recognize"], "process")
        self.assertIsNone(self._observe(app, guard, 100))
        self.assertIsNone(self._observe(app, guard, 159.9))
        hit = self._observe(app, guard, 160)
        self.assertEqual(hit["actions"], [{"type": "restart_workflow", "restart_workflow_target_row": 1}])
        self.assertIsNone(self._observe(app, guard, 200))

    def test_process_reappearance_resets_continuous_absence(self):
        app, guard = self._app(duration_ms=2000)
        self.assertIsNone(self._observe(app, guard, 10))
        self.assertIsNone(self._observe(app, guard, 11.5, True))
        self.assertIsNone(self._observe(app, guard, 12))
        self.assertIsNone(self._observe(app, guard, 13.9))
        self.assertIsNotNone(self._observe(app, guard, 14))

    def test_detection_failure_restarts_timer_instead_of_triggering(self):
        app, guard = self._app(duration_ms=2000)
        self._observe(app, guard, 10)
        with patch("macroflow.ui.app.guards.is_process_running", side_effect=OSError("unavailable")):
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 12))
        self.assertIsNone(self._observe(app, guard, 13))
        self.assertIsNotNone(self._observe(app, guard, 15))

    def test_pause_time_does_not_count_towards_absence(self):
        app, guard = self._app(duration_ms=2000)
        self._observe(app, guard, 10)
        with patch("macroflow.ui.app.guards.time.perf_counter", return_value=11):
            app._pause_workflow_plugins(True)
        with patch("macroflow.ui.app.guards.time.perf_counter", return_value=111):
            app._pause_workflow_plugins(False)
        self.assertIsNone(self._observe(app, guard, 111))
        self.assertIsNotNone(self._observe(app, guard, 112))

    def test_process_plugin_needs_no_capture_or_template(self):
        app, guard = self._app()
        with patch("macroflow.ui.app.guards.capture_bgr", side_effect=AssertionError("unexpected capture")), \
                patch("macroflow.ui.app.guards.is_process_running", return_value=True):
            self.assertIsNone(app._evaluate_global_guards())

    def test_configuration_survives_workflow_json_roundtrip_and_can_be_disabled(self):
        app, guard = self._app(duration_ms=90000)
        restored = Workflow.from_dict(json.loads(json.dumps(app.workflow.to_dict())))
        self.assertEqual(restored.steps[0]["config"]["duration_ms"], 90000)
        self.assertEqual(len(app._workflow_only_steps()), 1)
        app.global_guards.clear()
        restored.steps[0]["enabled"] = False
        app._register_workflow_global_modules(restored.steps[:1])
        self.assertEqual(app.global_guards, {})

    def test_guard_hit_stops_current_playback_and_hands_off_restart(self):
        app, guard = self._app(duration_ms=1000)
        app.workflow_running = True
        app.current_workflow_step_index = 0
        app.workflow.restart_default_row = 5
        queued = []
        app._ui = lambda callback, *args: queued.append((callback, args))
        app.player.on_restart_workflow_request = app._on_restart_workflow_request
        self._observe(app, guard, 10)
        hit = self._observe(app, guard, 11)
        with self.assertRaises(PlaybackStopped):
            app.player.handle_guard_hit(hit)
        self.assertTrue(app.workflow_restart_requested)
        self.assertEqual(app.workflow_restart_target_row, 1)
        self.assertTrue(app.workflow_stop.is_set())
        self.assertTrue(app.player.stop_event.is_set())
        self.assertEqual(app.global_guards, {})
        self.assertIn(app._poll_workflow_stop_for_restart_workflow, [item[0] for item in queued])

    def test_new_run_starts_a_fresh_absence_timer(self):
        app, guard = self._app(duration_ms=1000)
        self._observe(app, guard, 10)
        self.assertIsNotNone(self._observe(app, guard, 11))
        app.global_guards.clear()
        app._register_workflow_global_modules(app._global_module_steps())
        fresh = app.global_guards["workflow:watchdog"]
        self.assertIsNone(self._observe(app, fresh, 20))
        self.assertIsNotNone(self._observe(app, fresh, 21))

    def test_add_and_edit_plugin_preserves_step_identity(self):
        app = self._make_guard_app()
        app.root = Mock()
        app.workflow = Workflow()
        app.global_tree = Mock()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._set_status = Mock()
        first = {"plugin": "process_absence_restart", "process_name": "Game.exe", "duration_ms": 60000}
        edited = {**first, "process_name": "New.exe", "duration_ms": 120000}
        with patch("macroflow.ui.app.workflow.ProcessAbsencePluginDialog") as dialog:
            dialog.return_value.show.return_value = first
            app.add_workflow_plugin()
            original_id = app.workflow.steps[0]["step_id"]
            app.global_tree.selection.return_value = ("0",)
            dialog.return_value.show.return_value = edited
            app.edit_selected_global_module()
        self.assertEqual(len(app.workflow.steps), 1)
        self.assertEqual(app.workflow.steps[0]["step_id"], original_id)
        self.assertEqual(app.workflow.steps[0]["config"], edited)
        self.assertEqual(app._workflow_only_steps(), [])

    def test_cancel_plugin_creation_leaves_workflow_unchanged(self):
        app = self._make_guard_app()
        app.root = Mock()
        app.workflow = Workflow()
        with patch("macroflow.ui.app.workflow.ProcessAbsencePluginDialog") as dialog:
            dialog.return_value.show.return_value = None
            app.add_workflow_plugin()
        self.assertEqual(app.workflow.steps, [])

    def test_invalid_process_or_duration_rejected_at_registration(self):
        for settings in ({"process_name": ""}, {"duration_ms": 0}, {"duration_ms": -1}, {"duration_ms": "bad"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                self._app(**settings)


if __name__ == "__main__":
    unittest.main()
