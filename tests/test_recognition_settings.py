"""Recognition settings contracts without constructing application windows."""

import unittest
from unittest.mock import Mock

from tests.helpers.patches import package_patch
from tests import test_module_objects as module_object_tests
from macroflow.ui.dialogs.module_objects import TemplateRegionFormDialog


class RecognitionSettingsTests(unittest.TestCase):
    def test_wait_rules_save_the_matching_runtime_behavior(self):
        for label, blocking, timeout in (
            ("未命中立即跳过", False, 0),
            ("限时等待", False, 5000),
            ("一直等待", True, 5000),
        ):
            with self.subTest(rule=label):
                form = module_object_tests.TemplateRegionTests()._form(
                    image="images/target.png", region="10,20,300,400",
                )
                form.wait_rule_var = Mock()
                form.wait_rule_var.get.return_value = label
                with package_patch('dialogs', 'show_floating_notice') as notice:
                    form.save()
                notice.assert_not_called()
                self.assertEqual(form.result[2]['blocking'], blocking)
                self.assertEqual(form.result[2]['not_found_timeout_ms'], timeout)

    def test_disappearance_condition_saves_for_image_and_text(self):
        for recognize in ("模板图片", "识别文字"):
            with self.subTest(recognize=recognize):
                form = module_object_tests.TemplateRegionTests()._form(
                    image="images/target.png", region="10,20,300,400",
                    recognize=recognize,
                )
                form.target_condition_var = Mock()
                form.target_condition_var.get.return_value = "消失"
                with package_patch('dialogs', 'show_floating_notice') as notice:
                    form.save()
                notice.assert_not_called()
                self.assertTrue(form.result[2]['wait_text_absent'])

    def test_timed_wait_rejects_zero_duration(self):
        form = module_object_tests.TemplateRegionTests()._form(
            image="images/target.png", region="10,20,300,400",
        )
        form.wait_rule_var = Mock()
        form.wait_rule_var.get.return_value = "限时等待"
        form.not_found_timeout_var.get.return_value = "0"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        form.destroy.assert_not_called()

    def test_collapsing_advanced_settings_hides_controls_and_preserves_values(self):
        form = module_object_tests.TemplateRegionTests()._form(
            image="images/target.png", region="10,20,300,400",
        )
        for name in (
            "row_name", "row_image", "row_region", "detect_section_heading",
            "row_recognize", "row_expected_text", "row_match_mode", "row_threshold",
            "row_wait_text_absent", "row_interval", "row_cooldown", "row_recheck_after_3s",
            "row_start_delay", "row_delay",
            "row_fallback_module", "row_fallback_click", "row_blocking",
            "action_section_heading", "row_after", "row_hold", "row_button",
            "row_click_count", "row_ocr_offset", "row_click_point", "row_second_template",
            "row_second_timeout", "row_second_click_target", "row_second_click_region",
            "segment_section_heading", "row_run_code_after_action", "segment_frame",
            "timeout_section_heading", "row_run_code_on_timeout", "row_not_found_timeout",
            "timeout_segment_frame",
        ):
            setattr(form, name, Mock())
        form._set_row = Mock()
        form._resize_for_content = Mock()
        form.advanced_recognition_var.get.return_value = False
        form.fallback_module_key_var.get.return_value = "module:fallback"
        form.hold_enabled_var.get.return_value = True

        TemplateRegionFormDialog._toggle_sections(form)

        final_visibility = {call.args[0]: call.args[1] for call in form._set_row.call_args_list}
        for row in (form.row_interval, form.row_cooldown, form.row_hold,
                    form.row_fallback_module, form.row_fallback_click_settings):
            self.assertFalse(final_visibility[row])
        self.assertTrue(final_visibility[form.row_blocking])
        self.assertTrue(final_visibility[form.row_not_found_timeout])
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        self.assertEqual(form.result[2]['fallback_module_key'], "module:fallback")
        self.assertTrue(form.result[2]['hold_enabled'])

        form.wait_rule_var.get.return_value = "未命中立即跳过"
        form.run_code_on_timeout_var.get.return_value = True
        TemplateRegionFormDialog._toggle_sections(form)
        final_visibility = {call.args[0]: call.args[1] for call in form._set_row.call_args_list}
        self.assertFalse(final_visibility[form.row_not_found_timeout])

        form.category_var.get.return_value = "工作流全局模块"
        form.recognize_var.get.return_value = "无需识图"
        form.advanced_recognition_var.get.return_value = True
        TemplateRegionFormDialog._toggle_sections(form)
        final_visibility = {call.args[0]: call.args[1] for call in form._set_row.call_args_list}
        self.assertTrue(final_visibility[form.row_advanced_recognition])
        self.assertTrue(final_visibility[form.row_cooldown])
