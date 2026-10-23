import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

class ProcessDetectionTests(unittest.TestCase):
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


    def test_native_enumeration_failure_is_not_absence(self):
        from unittest.mock import Mock, patch
        from macroflow.execution.player.base import running_process_names
        kernel = Mock()
        kernel.CreateToolhelp32Snapshot.return_value = -1
        kernel.GetLastError.return_value = 5
        with patch('macroflow.execution.player.base.ctypes.windll.kernel32', kernel):
            with self.assertRaises(OSError):
                running_process_names()

    def test_process_module_saves_without_image_or_timed_state(self):
        from tests.test_module_objects import TemplateRegionTests
        from tests.helpers.patches import package_patch
        form = TemplateRegionTests()._form(recognize="进程检测")
        form.process_name_var = Mock()
        form.process_name_var.get.return_value = "CustomGame.exe"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        self.assertEqual(form.result[2]["recognize"], "process")
        self.assertEqual(form.result[2]["process_name"], "CustomGame.exe")
        self.assertEqual(form.result[2]["after_action"], "continue")
        self.assertFalse(any(key.startswith('timed_') for key in form.result[2]))

    def test_player_process_detection_routes_current_process_state_without_capture(self):
        from macroflow.execution.player import MacroPlayer
        from macroflow.core.storage import _normalize_object
        obj = _normalize_object({"recognize": "process", "process_name": "Game.exe"})
        for present in (False, True):
            player = MacroPlayer()
            player._after_module_success = Mock(return_value=None)
            player._module_result_route = Mock(return_value=("row", 2))
            with patch('macroflow.execution.player.image.registered_module_object', return_value=obj), \
                    patch('macroflow.execution.player.image.is_process_running', return_value=present) as probe, \
                    patch('macroflow.execution.player.image.find_template') as capture:
                self.assertEqual(player._execute_image({"module_ref": True, "module_key": "module:x"}, None), ("row", 2))
            probe.assert_called_once_with("Game.exe")
            capture.assert_not_called()
            self.assertIs(player._module_result_route.call_args.kwargs['succeeded'], present)
            self.assertEqual(player._after_module_success.call_count, int(present))

    def test_process_normalization_removes_image_without_enabling_timed_state(self):
        from macroflow.core.storage import _normalize_object
        obj = _normalize_object({"recognize": "process", "process_name": " Game.exe ",
                                 "template": "old.png", "region": [1, 2, 3, 4]})
        self.assertEqual(obj['process_name'], 'Game.exe')
        self.assertEqual(obj['template'], '')
        self.assertEqual(obj['region'], [])
        self.assertFalse(any(key.startswith('timed_') for key in obj))

    def test_process_switch_hides_wait_controls_and_limits_success_actions(self):
        from types import SimpleNamespace
        from tests.test_module_objects import TemplateRegionTests
        from macroflow.ui.dialogs.module_objects import TemplateRegionFormDialog
        for category in ("切换模块", "工作流全局模块", "脚本全局模块"):
            form = TemplateRegionTests()._form(recognize="进程检测")
            form.category_var.get.return_value = category
            current = ["二次识别后点击"]
            form.after_action_var = SimpleNamespace(get=lambda: current[0], set=lambda value: current.__setitem__(0, value))
            for name in (
                "row_name", "row_image", "row_region", "detect_section_heading", "row_recognize",
                "row_expected_text", "row_match_mode", "row_threshold", "row_wait_text_absent",
                "row_interval", "row_cooldown", "row_recheck_confirm", "row_blocking",
                "action_section_heading", "row_after", "row_hold", "row_button", "row_click_count",
                "row_ocr_offset", "row_click_point", "row_second_template", "row_second_timeout",
                "row_second_click_target", "row_second_click_region", "segment_section_heading",
                "row_run_code_after_action", "segment_frame", "timeout_section_heading",
                "row_run_code_on_timeout", "row_not_found_timeout", "timeout_segment_frame",
            ):
                setattr(form, name, Mock())
            form.after_action_combo = Mock()
            form._set_row = Mock()
            form._resize_for_content = Mock()
            form.run_code_on_timeout_var.get.return_value = True
            TemplateRegionFormDialog._toggle_sections(form)
            visible = {call.args[0]: call.args[1] for call in form._set_row.call_args_list}
            self.assertEqual(form.after_action_combo.configure.call_args.kwargs['values'], ("点击自定义位置", "成功后继续"))
            self.assertEqual(current[0], "成功后继续")
            self.assertFalse(visible[form.row_wait_text_absent])
            self.assertFalse(visible[form.row_blocking])
            self.assertFalse(visible[form.row_second_template])
            self.assertEqual(visible[form.row_run_code_on_timeout], category != "切换模块")
            self.assertEqual(visible[form.row_not_found_timeout], category != "切换模块")

    def test_single_probe_process_save_clears_unsupported_detection_behavior(self):
        from tests.test_module_objects import TemplateRegionTests
        from tests.helpers.patches import package_patch
        for category in ("切换模块", "工作流全局模块"):
            form = TemplateRegionTests()._form(recognize="进程检测")
            form.category_var.get.return_value = category
            form.process_name_var.get.return_value = "Game.exe"
            form.recheck_enabled_var.get.return_value = True
            form.hold_enabled_var.get.return_value = True
            form.run_code_on_timeout_var.get.return_value = True
            form.timeout_segment = [{"type": "delay", "ms": 1}]
            with package_patch('dialogs', 'show_floating_notice') as notice:
                form.save()
            notice.assert_not_called()
            obj = form.result[2]
            self.assertFalse(obj['recheck_enabled'])
            self.assertFalse(obj['wait_text_absent'])
            self.assertFalse(obj['blocking'])
            self.assertEqual(obj['second_match_template'], '')
            self.assertEqual(obj['run_code_on_timeout'], category != "切换模块")
            self.assertEqual(obj['hold_enabled'], category != "切换模块")
            if category == "切换模块":
                self.assertEqual(obj['not_found_timeout_ms'], 0)
                self.assertEqual(obj['on_timeout_actions'], [])

    def test_normalization_clears_unusable_single_probe_process_settings(self):
        from macroflow.core.storage import _normalize_object
        for category in ("switch", "workflow_global"):
            obj = _normalize_object({"recognize": "process", "process_name": "Game.exe", "category": category,
                                     "hold_enabled": True, "recheck_enabled": True, "run_code_on_timeout": True,
                                     "on_timeout_actions": [{"type": "delay", "ms": 1}],
                                     "second_match_template": "old.png"})
            self.assertFalse(obj['recheck_enabled'])
            self.assertEqual(obj['second_match_template'], '')
            self.assertEqual(obj['run_code_on_timeout'], category == 'workflow_global')
            self.assertEqual(obj['hold_enabled'], category == 'workflow_global')
            if category == 'switch':
                self.assertEqual(obj['on_timeout_actions'], [])
                self.assertEqual(obj['not_found_timeout_ms'], 0)
