import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import unittest
from macroflow.core.timed_detection import sustained_detection

class TimedDetectionTests(unittest.TestCase):
    def test_present_resets_and_triggers_once_until_state_changes(self):
        state = {}
        for detected, now, expected in [(True, 0, False), (True, 1, False),
                (False, 1.5, False), (True, 2, False), (True, 4, True),
                (True, 8, False), (False, 9, False), (True, 10, False),
                (True, 12, True)]:
            self.assertEqual(sustained_detection(state, detected, 'present', 2000, now), expected)

    def test_absent_and_invalid_observations_reset_timer(self):
        state = {}
        for detected, now, expected in [(False, 0, False), (None, 3, False),
                (False, 4, False), (False, 6, True), (True, 7, False),
                (False, 8, False), (False, 10, True)]:
            self.assertEqual(sustained_detection(state, detected, 'absent', 2000, now), expected)

    def test_configuration_change_restarts_timing(self):
        state = {}
        self.assertFalse(sustained_detection(state, True, 'present', 2000, 0))
        self.assertFalse(sustained_detection(state, True, 'present', 3000, 2))
        self.assertTrue(sustained_detection(state, True, 'present', 3000, 5))


class TimedModuleIntegrationTests(unittest.TestCase):
    def test_player_absence_resets_on_match_and_executes_only_custom_steps(self):
        from unittest.mock import Mock, patch
        from macroflow.execution.player import MacroPlayer
        from tests.helpers.patches import package_patch
        player = MacroPlayer()
        player._wait = Mock()
        player._run_action_sequence = Mock()
        player._module_result_route = Mock(return_value=None)
        steps = [{"type": "delay", "ms": 1}]
        obj = {"name": "恢复", "timed_detection": True, "timed_condition": "absent",
               "timed_duration_ms": 2000, "timed_actions": steps, "interval_ms": 1000,
               "template": "images/test.png", "region": [0, 0, 20, 20],
               "after_action": "click_match"}
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'find_template', side_effect=[None, {}, {"center_x": 1}, None, None, None]), \
             patch('macroflow.execution.player.image.time.perf_counter', side_effect=[0, 0, 1, 2, 3, 4, 5]):
            player._execute_image({"module_ref": True, "module_key": "module:test", "region_mode": "template"}, None)
        player._run_action_sequence.assert_called_once_with(steps, None, script_stack=None, depth=1)
        player._module_result_route.assert_called_once()

    def test_guard_triggers_once_and_rearms_after_opposite_state(self):
        from unittest.mock import Mock
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        steps = [{"type": "delay", "ms": 10}]
        guard = helper._make_guard("images/test.png", timed_detection=True,
                   timed_condition="absent", timed_duration_ms=2000, timed_actions=steps)
        app._guard_image_detect = Mock(return_value=(False, None))
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 0))
        hit = app._evaluate_one_guard(guard, None, None, 2)
        self.assertEqual(hit["actions"], steps)
        self.assertNotIn("click", hit)
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 4))
        app._guard_image_detect.return_value = (True, {"center_x": 10})
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 5))
        app._guard_image_detect.return_value = (False, None)
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 6))
        self.assertIsNotNone(app._evaluate_one_guard(guard, None, None, 8))

    def test_form_saves_timed_steps(self):
        from tests.test_module_objects import TemplateRegionTests
        from tests.helpers.patches import package_patch
        import tempfile
        import os
        handle, image = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        self.addCleanup(Path(image).unlink)
        form = TemplateRegionTests._form(self, image=image, region="0,0,20,20")
        form.timed_detection_var.get.return_value = True
        form.timed_segment = [{"type": "delay", "ms": 10}]
        with package_patch('dialogs', 'registered_module_object', return_value=None):
            form.save()
        self.assertTrue(form.result[2]["timed_detection"])
        self.assertEqual(form.result[2]["timed_duration_ms"], 30000)
        self.assertEqual(form.result[2]["timed_actions"][0]["ms"], 10)

    def test_guard_detection_error_does_not_count_as_absence(self):
        from unittest.mock import Mock
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        guard = helper._make_guard("images/test.png", timed_detection=True,
                   timed_condition="absent", timed_duration_ms=2000,
                   timed_actions=[{"type": "delay", "ms": 1}])
        app._guard_image_detect = Mock(return_value=(False, None))
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 0))
        guard["warned_find_error"] = True
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 3))
        guard["warned_find_error"] = False
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 4))
        self.assertIsNotNone(app._evaluate_one_guard(guard, None, None, 6))

    def test_player_present_text_executes_steps_and_stop_remains_interruptible(self):
        from unittest.mock import Mock, patch
        from macroflow.execution.player import MacroPlayer
        from macroflow.execution.player.control import PlaybackStopped
        from tests.helpers.patches import package_patch
        player = MacroPlayer()
        player._wait = Mock()
        player._run_action_sequence = Mock()
        player._module_result_route = Mock()
        obj = {"name": "等待文字", "recognize": "text", "expected_text": "大厅",
               "timed_condition": "present", "timed_duration_ms": 1000,
               "timed_actions": [{"type": "delay", "ms": 10}]}
        with package_patch('player', 'recognize_region_with_boxes', return_value=("大厅", [])), \
             patch('macroflow.execution.player.image.time.perf_counter', side_effect=[0, 1]):
            player._execute_timed_detection({}, obj, Path("unused.png"), (0, 0, 20, 20), None, None, 0)
        player._run_action_sequence.assert_called_once()
        player.stop_event.set()
        with self.assertRaises(PlaybackStopped):
            player._execute_timed_detection({}, obj, Path("unused.png"), None, None, None, 0)

    def test_timed_configuration_normalization(self):
        from macroflow.core.storage import _normalize_object
        obj = _normalize_object({"timed_detection": True, "timed_condition": "present",
                                 "timed_duration_ms": "2500", "timed_actions": []})
        self.assertTrue(obj["timed_detection"])
        self.assertEqual(obj["timed_duration_ms"], 2500)
        self.assertFalse(_normalize_object({"recognize": "none", "timed_detection": True})["timed_detection"])

    def test_discarded_timed_hit_can_be_delivered_again(self):
        from unittest.mock import Mock
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        guard = helper._make_guard("images/test.png", timed_detection=True,
                   timed_condition="present", timed_duration_ms=2000,
                   timed_actions=[{"type": "delay", "ms": 1}])
        app.global_guards[guard["key"]] = guard
        app._guard_image_detect = Mock(return_value=(True, {"center_x": 10}))
        self.assertIsNone(app._evaluate_one_guard(guard, None, None, 0))
        hit = app._evaluate_one_guard(guard, None, None, 2)
        app._rollback_detection_hit(hit)
        self.assertIsNotNone(app._evaluate_one_guard(guard, None, None, 4))

    def test_config_invalidation_rolls_back_pending_timed_hits(self):
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        guard = helper._make_guard("images/test.png", timed_state={"since": 0, "fired": True})
        app.global_guards[guard["key"]] = guard
        app._pending_global_guard_hits = [{"kind": "timed", "guard_key": guard["key"]}]
        app._invalidate_detection_config()
        self.assertNotIn("fired", guard["timed_state"])
        self.assertEqual(app._pending_global_guard_hits, [])

    def test_form_rejects_empty_timed_actions_without_ui(self):
        from tests.test_module_objects import TemplateRegionTests
        from tests.helpers.patches import package_patch
        import tempfile
        import os
        handle, image = tempfile.mkstemp(suffix=".png")
        os.close(handle)
        self.addCleanup(Path(image).unlink)
        form = TemplateRegionTests._form(self, image=image, region="0,0,20,20")
        form.timed_detection_var.get.return_value = True
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        form.destroy.assert_not_called()


class ProcessDetectionTests(unittest.TestCase):
    def test_process_module_saves_without_image_or_region(self):
        from tests.test_module_objects import TemplateRegionTests
        from unittest.mock import Mock
        from tests.helpers.patches import package_patch
        form = TemplateRegionTests._form(self, recognize="进程检测")
        form.process_name_var = Mock()
        form.process_name_var.get.return_value = "CustomGame.exe"
        form.timed_segment = [{"type": "delay", "ms": 10}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        self.assertEqual(form.result[2]["recognize"], "process")
        self.assertEqual(form.result[2]["process_name"], "CustomGame.exe")
        self.assertTrue(form.result[2]["timed_detection"])

    def test_player_process_absence_uses_process_list_not_screen(self):
        from unittest.mock import Mock, patch
        from macroflow.execution.player import MacroPlayer
        player = MacroPlayer()
        player._wait = Mock()
        player._run_action_sequence = Mock()
        player._module_result_route = Mock()
        obj = {"recognize": "process", "process_name": "CustomGame.exe",
               "timed_condition": "absent", "timed_duration_ms": 2000,
               "timed_actions": [{"type": "delay", "ms": 1}]}
        with patch('macroflow.execution.player.image.is_process_running', create=True,
                   side_effect=[False, False, True, False, False, False]) as probe, \
             patch('macroflow.execution.player.image.find_template', side_effect=AssertionError("must not capture")), \
             patch('macroflow.execution.player.image.time.perf_counter', side_effect=[0, 1, 2, 3, 4, 5]):
            player._execute_timed_detection({}, obj, Path("unused"), None, None, None, 0)
        self.assertEqual(probe.call_count, 6)
        probe.assert_called_with("CustomGame.exe")
        player._run_action_sequence.assert_called_once()

    def test_global_process_monitor_does_not_capture_screen(self):
        from unittest.mock import patch
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        guard = helper._make_guard("", recognize="process", process_name="CustomGame.exe",
                   timed_detection=True, timed_condition="absent", timed_duration_ms=2000,
                   timed_actions=[{"type": "delay", "ms": 1}])
        app.global_guards[guard["key"]] = guard
        with patch('macroflow.ui.app.guards.is_process_running', create=True, return_value=False), \
             patch('macroflow.ui.app.guards.capture_bgr') as capture, \
             patch('macroflow.ui.app.guards.time.perf_counter', return_value=10):
            self.assertIsNone(app._evaluate_global_guards_sync().hit)
            guard["last_check_time"] = 0
            with patch('macroflow.ui.app.guards.time.perf_counter', return_value=12):
                self.assertEqual(app._evaluate_global_guards_sync().hit["kind"], "timed")
        capture.assert_not_called()

    def test_process_normalization_forces_timed_mode_and_removes_image(self):
        from macroflow.core.storage import _normalize_object
        obj = _normalize_object({"recognize": "process", "process_name": " CustomGame.exe ",
                                 "template": "old.png", "region": [1, 2, 3, 4]})
        self.assertEqual(obj["process_name"], "CustomGame.exe")
        self.assertTrue(obj["timed_detection"])
        self.assertEqual(obj["template"], "")
        self.assertEqual(obj["region"], [])

    def test_process_monitor_survives_other_guard_capture_failure(self):
        from unittest.mock import patch
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        process_guard = helper._make_guard("", key="process", recognize="process", process_name="CustomGame.exe",
                    timed_detection=True, timed_condition="absent", timed_duration_ms=2000,
                    timed_actions=[{"type": "delay", "ms": 1}], timed_state={"config": ("absent", 2000), "since": 0})
        visual_guard = helper._make_guard("missing.png", key="visual")
        app.global_guards = {"process": process_guard, "visual": visual_guard}
        with patch('macroflow.ui.app.guards.capture_bgr', side_effect=OSError("capture unavailable")), \
             patch('macroflow.ui.app.guards.is_process_running', return_value=False) as probe, \
             patch('macroflow.ui.app.guards.time.perf_counter', return_value=3):
            self.assertEqual(app._evaluate_global_guards_sync().hit["kind"], "timed")
        probe.assert_called_once_with("CustomGame.exe")

    def test_native_enumeration_failure_is_not_absence(self):
        from unittest.mock import Mock, patch
        from macroflow.execution.player.base import running_process_names
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = -1
        kernel.GetLastError.return_value = 5
        with patch('macroflow.execution.player.base.ctypes.windll.kernel32', kernel):
            with self.assertRaises(OSError):
                running_process_names()

    def test_enumeration_entry_failures_close_snapshot_and_raise(self):
        from unittest.mock import Mock, patch
        from macroflow.execution.player.base import running_process_names
        for first_succeeds in (False, True):
            with self.subTest(first_succeeds=first_succeeds):
                kernel = Mock()
                kernel.CreateToolhelp32Snapshot.return_value = 123
                kernel.Process32FirstW.return_value = first_succeeds
                kernel.Process32NextW.return_value = False
                kernel.GetLastError.return_value = 5
                with patch('macroflow.execution.player.base.ctypes.windll.kernel32', kernel):
                    with self.assertRaises(OSError):
                        running_process_names()
                kernel.CloseHandle.assert_called_once_with(123)

    def test_process_probe_error_resets_global_absence_timer(self):
        from unittest.mock import patch
        from tests.test_global_detect import GuardTestHelpers
        helper = GuardTestHelpers()
        app = helper._make_guard_app()
        guard = helper._make_guard("", recognize="process", process_name="CustomGame.exe",
                   timed_detection=True, timed_condition="absent", timed_duration_ms=2000,
                   timed_actions=[{"type": "delay", "ms": 1}])
        with patch('macroflow.ui.app.guards.is_process_running',
                   side_effect=[False, OSError("enumeration failed"), False, False]):
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 0))
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 3))
            self.assertIsNone(app._evaluate_one_guard(guard, None, None, 4))
            self.assertIsNotNone(app._evaluate_one_guard(guard, None, None, 6))


class ProcessPickerTests(unittest.TestCase):
    def test_process_picker_is_readonly_and_refreshes_on_open(self):
        from unittest.mock import Mock, patch
        from macroflow.ui.dialogs.module_objects import TemplateRegionFormDialog
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        form.process_name_var = Mock()
        form.process_name_var.get.return_value = "saved.exe"
        with patch('macroflow.ui.dialogs.module_objects.ttk.Combobox') as combo, \
             patch('macroflow.ui.dialogs.module_objects.ttk.Button') as button:
            form._process_picker_row(Mock())
        self.assertEqual(combo.call_args.kwargs["state"], "readonly")
        self.assertEqual(combo.call_args.kwargs["postcommand"], form._refresh_process_choices)
        self.assertEqual(button.call_args.kwargs["command"], form._pick_process)

    def test_process_choices_refresh_and_keep_saved_stopped_target(self):
        from unittest.mock import Mock, patch
        from macroflow.ui.dialogs.module_objects import TemplateRegionFormDialog
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        form.process_name_var = Mock()
        form.process_name_var.get.return_value = "saved.exe"
        form.process_name_combo = Mock()
        with patch('macroflow.ui.dialogs.module_objects.running_process_names', create=True,
                   return_value=["game.exe", "python.exe"]):
            form._refresh_process_choices()
        self.assertEqual(form.process_name_combo.configure.call_args.kwargs["values"],
                         ["game.exe", "python.exe", "saved.exe"])
