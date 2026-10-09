"""Recognition click offsets, exercised without opening windows or sending input."""
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from macroflow.execution.player import MacroPlayer
from macroflow.ui.app.main import MacroFlowApp
from tests.helpers.patches import package_patch
from tests import test_module_objects as module_tests
from tests import test_global_detect as guard_tests


OFFSETS = dict(ocr_offset_up=5, ocr_offset_down=15,
               ocr_offset_left=20, ocr_offset_right=5)


class RecognitionClickOffsetTests(unittest.TestCase):
    def test_offset_control_available_for_image_and_text_recognition(self):
        for recognize, after, target, visible in (
            ("模板图片", "点击识别区域", "第二次识别位置", True),
            ("识别文字", "点击识别区域", "第二次识别位置", True),
            ("模板图片", "二次识别后点击", "第一次识别位置", True),
            ("模板图片", "二次识别后点击", "第二次识别位置", True),
            ("模板图片", "二次识别后点击", "自定义框选区域", False),
            ("模板图片", "点击自定义位置", "第二次识别位置", False),
            ("模板图片", "成功后继续", "第二次识别位置", False),
        ):
            with self.subTest(recognize=recognize, after=after, target=target):
                form = module_tests.TemplateRegionTests()._form(recognize=recognize, after_action=after)
                for name in (
                    "row_name", "row_image", "row_region", "detect_section_heading",
                    "row_recognize", "row_expected_text", "row_match_mode", "row_threshold",
                    "row_wait_text_absent", "row_interval", "row_cooldown", "row_recheck_after_3s",
                    "row_fallback_module", "row_fallback_click", "row_blocking",
                    "action_section_heading", "row_after", "row_hold", "row_button",
                    "row_click_count", "row_ocr_offset", "row_click_point", "row_second_template",
                    "row_second_timeout", "row_second_click_target", "row_second_click_region",
                    "segment_section_heading", "row_run_code_after_action", "segment_frame",
                    "timeout_section_heading", "row_run_code_on_timeout", "row_not_found_timeout",
                    "timeout_segment_frame",
                ):
                    setattr(form, name, Mock())
                form.second_click_target_var = Mock()
                form.second_click_target_var.get.return_value = target
                form._set_row = Mock()
                form._resize_for_content = Mock()
                module_tests.TemplateRegionFormDialog._toggle_sections(form)
                calls = [c.args[1] for c in form._set_row.call_args_list
                         if c.args[0] is form.row_ocr_offset]
                self.assertEqual(calls, [visible])

    def test_all_module_recognition_centers_apply_offsets(self):
        for recognize in ("text", "image", ""):
            with self.subTest(recognize=recognize):
                player = MacroPlayer()
                player._click_module_point = Mock()
                player._after_module_success(
                    dict(OFFSETS, recognize=recognize, after_action="click_match"),
                    dict(center_x=120, center_y=80), None, None, 0,
                )
                player._click_module_point.assert_called_once_with(105, 90, "left", 1, None)

    def test_offset_scales_as_vector_without_monitor_origin(self):
        player = MacroPlayer()
        player._source_screen = dict(left=-1920, top=0, width=1920, height=1080)
        player._target_screen = dict(left=100, top=50, width=960, height=540)
        player._click_module_point = Mock()
        player._after_module_success(
            dict(ocr_offset_right=40, ocr_offset_down=20),
            dict(center_x=300, center_y=200), None, None, 0,
        )
        player._click_module_point.assert_called_once_with(320, 210, "left", 1, None)

    def test_fixed_click_ignores_recognition_offsets(self):
        player = MacroPlayer()
        player._click_module_point = Mock()
        player._after_module_success(
            dict(OFFSETS, after_action="click_custom", click_point=[300, 200]),
            dict(center_x=120, center_y=80), None, None, 0,
        )
        player._click_module_point.assert_called_once_with(300, 200, "left", 1, None)

    def test_second_match_offsets_selected_recognition_center_only(self):
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder)/"second.png"
            template.write_bytes(b"test")
            for target, expected in (("first", (105, 90)), ("second", (35, 70)),
                                     ("custom_region", (140, 220))):
                with self.subTest(target=target):
                    player = MacroPlayer()
                    player._click_module_point = Mock()
                    obj = dict(OFFSETS, second_match_template=str(template),
                               second_match_click_target=target,
                               second_match_click_region=[100, 200, 80, 40])
                    second = dict(x=40, y=50, width=20, height=20, center_x=50, center_y=60)
                    with package_patch('player', 'find_template', return_value=second), \
                         package_patch('player', 'registered_template_region', return_value=None), \
                         package_patch('player', 'show_overlay'):
                        player._execute_second_match(obj, None, dict(center_x=120, center_y=80))
                    player._click_module_point.assert_called_once_with(*expected, "left", 1, None)

    def test_global_second_match_preserves_offsets(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        guard = dict(OFFSETS, template=Path("first.png"), module_ref=True,
                     after_action="second_match", match_data=dict(center_x=120, center_y=80),
                     second=dict(template="second.png", click_target="first"))
        hit = app._build_guard_hit(guard, resolve_hwnd=False)
        for key, value in OFFSETS.items():
            self.assertEqual(hit["second"][key], value)

    def test_ordinary_fallback_uses_its_own_offsets(self):
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder) / "main.png"
            template.write_bytes(b"test")
            main = dict(template=str(template), blocking=True, after_action="continue",
                        fallback_module_key="fallback", fallback_on_match="click_continue",
                        ocr_offset_right=999)
            fallback = dict(OFFSETS, template=str(template), name="fallback")
            match = dict(x=40, y=50, width=20, height=20, center_x=50, center_y=60, score=0.9)
            player = MacroPlayer()
            player._wait = Mock()
            player._click_module_point = Mock()
            with package_patch('player', 'registered_module_object',
                               side_effect=lambda key: {"main": main, "fallback": fallback}[key]), \
                 package_patch('player', 'find_template', side_effect=[None, match, match]), \
                 package_patch('player', 'show_overlay'):
                player._execute_image(dict(type="image_match", module_ref=True,
                                           module_key="main", template=str(template)), None)
            player._click_module_point.assert_called_once_with(35, 70, "left", 1, None)

    def test_global_fallback_offsets_survive_deferred_execution(self):
        fixture = guard_tests.GlobalDetectTests()
        app = fixture._make_guard_app()
        app._pending_global_guard_hits = []
        app._guard_config_version = 3
        app._detection_run_id = 7
        app._ui = Mock()
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder) / "main.png"
            template.write_bytes(b"test")
            guard = fixture._make_guard(template, key="g1", fallback_module_key="fallback",
                                        fallback_click=True, ocr_offset_right=999)
            app.global_guards["g1"] = guard
            fallback = dict(OFFSETS, template=str(template), name="fallback")
            match = dict(x=40, y=50, width=20, height=20, center_x=50, center_y=60)
            with package_patch('app', 'capture_bgr', return_value=(None, None)), \
                 package_patch('app', 'registered_module_object', return_value=fallback), \
                 package_patch('app', 'find_template', side_effect=[None, match]):
                evaluation = app._evaluate_global_guards_sync()
            clicks = [event for event in evaluation.deferred_events
                      if event["kind"] == "fallback_click"]
            self.assertEqual(len(clicks), 1)
            self.assertEqual((clicks[0]["match"]["center_x"], clicks[0]["match"]["center_y"]),
                             (35, 70))


if __name__ == "__main__":
    unittest.main()
