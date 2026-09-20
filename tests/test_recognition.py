"""识图 / OCR / 检测浮层。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_recognition.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import inspect
import numpy as np
import tempfile
import tkinter as tk
import ttkbootstrap
import unittest
from unittest.mock import Mock, call, patch
import macroflow.core.image_match as image_match_module
from macroflow.core.image_match import find_template, find_template_in_image
from macroflow.core.models import ACTION_ID_KEY, NEXT_WORKFLOW_STEP_TARGET_ID, SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL, clone_actions_with_new_ids, ensure_action_ids
from macroflow.core.ocr import extract_ocr_integer, find_expected_match, format_ocr_observation, matches_expected, parse_ocr_number_pair, recognize_image_with_boxes
from macroflow.core.storage import resolve_path
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.base import get_playback_screen_rect, screen_template_scale
from macroflow.ui.app.summaries import action_summary
import macroflow.ui.dialogs as dialog_module
from macroflow.ui.dialogs.actions import ClickDialog, CloseAppDialog, JumpActionDialog, MouseMoveDialog, OpenAppDialog, RepeatClickDialog, ScrollDialog, TextActionDialog, edit_action
from macroflow.ui.dialogs.base import show_floating_notice
from macroflow.ui.dialogs.helpers import image_action_option_defaults, image_click_target_defaults, image_found_jump_target_options, image_jump_target_options, image_timeout_option_defaults, image_timeout_option_label, image_timeout_option_value, segment_row_label
from macroflow.ui.dialogs.module_objects import TemplateRegionFormDialog
from macroflow.ui.dialogs.recognition import ImageActionDialog, MultiConditionClickDialog, OcrActionDialog, OcrCompareActionDialog
from macroflow.ui.dialogs.screen_pickers import ScreenPointPicker
from macroflow.ui.dialogs.segments import SegmentEditorMixin
from tests.helpers.patches import package_patch


class ImageTests(unittest.TestCase):
    def test_image_dialog_capture_creates_private_template_and_region(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.master = Mock()
        dialog.template = Mock()
        dialog.region_mode = Mock()
        dialog.region = Mock()
        dialog.template_combo = Mock()
        dialog._ancestors_to_hide = Mock(return_value=[])
        with package_patch('dialogs', 'ScreenRegionPicker') as picker_class:
            dialog.capture_custom_template()
        picker_class.return_value.start.assert_called_once()
        on_result = picker_class.call_args.args[2]
        with tempfile.TemporaryDirectory() as folder:
            images_dir = Path(folder) / "images"
            screen = np.zeros((40, 50, 3), dtype=np.uint8)
            with package_patch('dialogs', 'load_module_images_dir', return_value=images_dir), \
                 package_patch('dialogs', 'capture_bgr', return_value=(screen, (10, 20))), \
                 package_patch('dialogs', 'registered_template_options', return_value=['captured']) as options:
                on_result([100, 200, 50, 40])
            saved = list(images_dir.glob("recognition_*.png"))
            self.assertEqual(len(saved), 1)
            selected = dialog.template.set.call_args.args[0]
            self.assertEqual(resolve_path(selected), saved[0])
            options.assert_called_once_with(selected)
        dialog.region_mode.set.assert_called_once_with("custom")
        dialog.region.set.assert_called_once_with("100,200,50,40")
        dialog.template_combo.configure.assert_called_once_with(values=["captured"])

    def test_image_dialog_capture_failure_keeps_current_template(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.master = Mock()
        dialog.template = Mock()
        dialog.region_mode = Mock()
        dialog.region = Mock()
        dialog.template_combo = Mock()
        dialog._ancestors_to_hide = Mock(return_value=[])
        with package_patch('dialogs', 'ScreenRegionPicker') as picker_class:
            dialog.capture_custom_template()
        on_result = picker_class.call_args.args[2]
        with package_patch('dialogs', 'capture_bgr', side_effect=RuntimeError('boom')), \
             package_patch('dialogs', 'show_floating_notice') as notice:
            on_result([100, 200, 50, 40])
        self.assertIn("截图失败", notice.call_args.args[1])
        dialog.template.set.assert_not_called()
        dialog.region.set.assert_not_called()

    def test_custom_click_controls_are_disabled_for_match_center(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.click_target_mode = Mock()
        dialog.click_point_entry = Mock()
        dialog.click_point_button = Mock()
        dialog.click_target_mode.get.return_value = "识图区域中心"
        dialog._update_click_point_controls()
        dialog.click_point_entry.configure.assert_called_with(state="disabled")
        dialog.click_point_button.configure.assert_called_with(state="disabled")

        dialog.click_target_mode.get.return_value = "自定义坐标"
        dialog._update_click_point_controls()
        dialog.click_point_entry.configure.assert_called_with(state="normal")
        dialog.click_point_button.configure.assert_called_with(state="normal")

    def test_image_click_target_defaults_to_match_center(self):
        self.assertEqual(image_click_target_defaults({}), ("识图区域中心", [0, 0]))
        self.assertEqual(
            image_click_target_defaults({"click_target": "custom", "click_point": [640, 360]}),
            ("自定义坐标", [640, 360]),
        )

    def test_image_action_module_selection_copies_bound_region(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.module_key = Mock()
        dialog.template = Mock()
        dialog.region_mode = Mock()
        dialog.region = Mock()
        dialog.template_combo = Mock()
        dialog.module_name = Mock()
        with package_patch('dialogs', 'choose_module_binding', return_value={'module_ref': True, 'module_key': 'module:first', 'template': 'images/shared.png', 'region_mode': 'template', 'region': [11, 22, 333, 444]}) as choose:
            dialog.select_image_module()

        choose.assert_called_once_with(dialog, categories=("switch",))
        dialog.module_key.set.assert_called_once_with("module:first")
        dialog.template.set.assert_called_once_with("images/shared.png")
        dialog.region_mode.set.assert_called_once_with("template")
        dialog.region.set.assert_called_once_with("11,22,333,444")

    def test_multi_condition_module_selection_copies_bound_region(self):
        dialog = MultiConditionClickDialog.__new__(MultiConditionClickDialog)
        dialog.condition_module_key = [Mock()]
        dialog.condition_template = [Mock()]
        dialog.condition_region = [Mock()]
        dialog.condition_type = [Mock()]
        dialog.condition_ocr_mode = [Mock()]
        dialog.condition_field_widgets = [{}]
        with package_patch('dialogs', 'choose_module_binding', return_value={'module_ref': True, 'module_key': 'module:first', 'template': 'images/shared.png', 'region_mode': 'template', 'region': [11, 22, 333, 444]}) as choose:
            dialog.select_condition_module(0)

        choose.assert_called_once_with(dialog, categories=("switch",))
        dialog.condition_module_key[0].set.assert_called_once_with("module:first")
        dialog.condition_template[0].set.assert_called_once_with("images/shared.png")
        dialog.condition_region[0].set.assert_called_once_with("11,22,333,444")
        dialog.condition_type[0].set.assert_called_once_with("图片识别")

    def test_image_dialog_saves_fallback_fields(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/x.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.85"
        dialog.timeout = Mock()
        dialog.timeout.get.return_value = "3000"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "250"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.region = Mock()
        dialog.region.get.return_value = "0,0,0,0"
        dialog.on_found = Mock()
        dialog.on_found.get.return_value = "click"
        dialog.found_jump_target = Mock()
        dialog.found_jump_target.get.return_value = ""
        dialog.found_delay = Mock()
        dialog.found_delay.get.return_value = "0"
        dialog.click_target_mode = Mock()
        dialog.click_target_mode.get.return_value = "识图区域中心"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "0,0"
        dialog.on_timeout = Mock()
        dialog.on_timeout.get.return_value = "continue"
        dialog.timeout_jump_target = Mock()
        dialog.timeout_jump_target.get.return_value = ""
        dialog.timeout_delay = Mock()
        dialog.timeout_delay.get.return_value = "0"
        dialog.wait_forever = Mock()
        dialog.wait_forever.get.return_value = True
        dialog.fallback_template = Mock()
        dialog.fallback_template.get.return_value = "images/y.png"
        dialog.fallback_switch_ms = Mock()
        dialog.fallback_switch_ms.get.return_value = "5000"
        dialog.fallback_region_mode = Mock()
        dialog.fallback_region_mode.get.return_value = "custom"
        dialog.fallback_region = Mock()
        dialog.fallback_region.get.return_value = "10,20,30,40"
        dialog.fallback_click = Mock()
        dialog.fallback_click.get.return_value = True
        dialog.fallback_on_match = Mock()
        dialog.fallback_on_match.get.return_value = "回到主模板的检测"
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.show_result_notice = Mock()
        dialog.show_result_notice.get.return_value = True
        dialog.jump_target_ids = {}
        dialog.master = Mock()
        dialog.destroy = Mock()
        with package_patch('dialogs', 'activate_main_after_modal'):
            dialog.save()
        self.assertEqual(dialog.result["fallback_template"], "images/y.png")
        self.assertEqual(dialog.result["fallback_switch_ms"], 5000)
        self.assertEqual(dialog.result["fallback_region_mode"], "custom")
        self.assertEqual(dialog.result["fallback_region"], [10, 20, 30, 40])
        self.assertTrue(dialog.result["wait_forever"])
        self.assertTrue(dialog.result["fallback_click"])
        self.assertEqual(dialog.result["fallback_on_match"], "回到主模板的检测")
        dialog.destroy.assert_called_once()

    def test_image_dialog_saves_fallback_no_click_and_exit(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/x.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.85"
        dialog.timeout = Mock()
        dialog.timeout.get.return_value = "3000"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "250"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.region = Mock()
        dialog.region.get.return_value = "0,0,0,0"
        dialog.on_found = Mock()
        dialog.on_found.get.return_value = "click"
        dialog.found_jump_target = Mock()
        dialog.found_jump_target.get.return_value = ""
        dialog.found_delay = Mock()
        dialog.found_delay.get.return_value = "0"
        dialog.click_target_mode = Mock()
        dialog.click_target_mode.get.return_value = "识图区域中心"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "0,0"
        dialog.on_timeout = Mock()
        dialog.on_timeout.get.return_value = "continue"
        dialog.timeout_jump_target = Mock()
        dialog.timeout_jump_target.get.return_value = ""
        dialog.timeout_delay = Mock()
        dialog.timeout_delay.get.return_value = "0"
        dialog.wait_forever = Mock()
        dialog.wait_forever.get.return_value = True
        dialog.fallback_template = Mock()
        dialog.fallback_template.get.return_value = "images/y.png"
        dialog.fallback_switch_ms = Mock()
        dialog.fallback_switch_ms.get.return_value = "3000"
        dialog.fallback_region_mode = Mock()
        dialog.fallback_region_mode.get.return_value = "screen"
        dialog.fallback_region = Mock()
        dialog.fallback_region.get.return_value = "0,0,0,0"
        dialog.fallback_click = Mock()
        dialog.fallback_click.get.return_value = False
        dialog.fallback_on_match = Mock()
        dialog.fallback_on_match.get.return_value = "直接退出识别"
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.show_result_notice = Mock()
        dialog.show_result_notice.get.return_value = False
        dialog.jump_target_ids = {}
        dialog.master = Mock()
        dialog.destroy = Mock()
        with package_patch('dialogs', 'activate_main_after_modal'):
            dialog.save()
        self.assertFalse(dialog.result["fallback_click"])
        self.assertEqual(dialog.result["fallback_on_match"], "直接退出识别")
        self.assertNotIn("fallback_jump_action_id", dialog.result)
        dialog.destroy.assert_called_once()

    def test_wait_forever_controls_toggle_fallback_state(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.wait_forever = Mock()
        dialog.timeout_entry = Mock()
        dialog.timeout_delay_entry = Mock()
        dialog.timeout_combo = Mock()
        dialog.fallback_combo = Mock()
        dialog.fallback_switch_entry = Mock()
        dialog.fallback_click_button = Mock()
        dialog.fallback_action_combo = Mock()
        dialog._update_timeout_jump_control = Mock()

        dialog.wait_forever.get.return_value = True
        dialog._update_wait_forever_controls()
        dialog.timeout_entry.configure.assert_called_with(state="disabled")
        dialog.timeout_delay_entry.configure.assert_called_with(state="disabled")
        dialog.timeout_combo.configure.assert_called_with(state="disabled")
        dialog.fallback_combo.configure.assert_called_with(state="normal")
        dialog.fallback_switch_entry.configure.assert_called_with(state="normal")
        dialog.fallback_click_button.configure.assert_called_with(state="normal")
        dialog.fallback_action_combo.configure.assert_called_with(state="normal")

        dialog.wait_forever.get.return_value = False
        dialog._update_wait_forever_controls()
        dialog.timeout_entry.configure.assert_called_with(state="normal")
        dialog.timeout_delay_entry.configure.assert_called_with(state="normal")
        dialog.timeout_combo.configure.assert_called_with(state="normal")
        dialog.fallback_combo.configure.assert_called_with(state="disabled")
        dialog.fallback_switch_entry.configure.assert_called_with(state="disabled")
        dialog.fallback_click_button.configure.assert_called_with(state="disabled")
        dialog.fallback_action_combo.configure.assert_called_with(state="disabled")

    def test_image_dialog_saves_template_region_mode_and_disabled_fallback(self):
        # v1.78：主模板已登记 → 引用模板；备用选"（不启用）"→ fallback_template 为空。
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.module_key = Mock()
        dialog.module_key.get.return_value = "module:first"
        dialog.template = Mock()
        dialog.template.get.return_value = "images/x.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.85"
        dialog.timeout = Mock()
        dialog.timeout.get.return_value = "3000"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "250"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.region = Mock()
        dialog.region.get.return_value = "0,0,0,0"
        dialog.on_found = Mock()
        dialog.on_found.get.return_value = "click"
        dialog.found_jump_target = Mock()
        dialog.found_jump_target.get.return_value = ""
        dialog.found_delay = Mock()
        dialog.found_delay.get.return_value = "0"
        dialog.click_target_mode = Mock()
        dialog.click_target_mode.get.return_value = "识图区域中心"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "0,0"
        dialog.on_timeout = Mock()
        dialog.on_timeout.get.return_value = "continue"
        dialog.timeout_jump_target = Mock()
        dialog.timeout_jump_target.get.return_value = ""
        dialog.timeout_delay = Mock()
        dialog.timeout_delay.get.return_value = "0"
        dialog.wait_forever = Mock()
        dialog.wait_forever.get.return_value = False
        dialog.fallback_template = Mock()
        dialog.fallback_template.get.return_value = "（不启用）"
        dialog.fallback_switch_ms = Mock()
        dialog.fallback_switch_ms.get.return_value = "0"
        dialog.fallback_region_mode = Mock()
        dialog.fallback_region_mode.get.return_value = "screen"
        dialog.fallback_region = Mock()
        dialog.fallback_region.get.return_value = "0,0,0,0"
        dialog.fallback_click = Mock()
        dialog.fallback_click.get.return_value = False
        dialog.fallback_on_match = Mock()
        dialog.fallback_on_match.get.return_value = "回到主模板的检测"
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.show_result_notice = Mock()
        dialog.show_result_notice.get.return_value = False
        dialog.jump_target_ids = {}
        dialog.master = Mock()
        dialog.destroy = Mock()
        with package_patch('dialogs', 'registered_module_object', return_value={'category': 'switch', 'template': 'images/x.png', 'region': [10, 20, 30, 40]}), package_patch('dialogs', 'load_template_regions', return_value={'images/x.png': [10, 20, 30, 40]}), package_patch('dialogs', 'activate_main_after_modal'):
            dialog.save()
        result = dialog.result
        self.assertEqual(result["region_mode"], "template")
        self.assertEqual(result["region"], [10, 20, 30, 40])
        self.assertEqual(result["module_key"], "module:first")
        self.assertTrue(result["module_ref"])
        self.assertEqual(result["fallback_template"], "")
        self.assertEqual(result["fallback_region_mode"], "screen")
        dialog.destroy.assert_called_once()

    def test_image_dialog_saves_fallback_template_region_mode(self):
        # v1.78：备用模板已登记 → 备用区域同样引用模板登记表。
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/x.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.85"
        dialog.timeout = Mock()
        dialog.timeout.get.return_value = "3000"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "250"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.region = Mock()
        dialog.region.get.return_value = "0,0,0,0"
        dialog.on_found = Mock()
        dialog.on_found.get.return_value = "click"
        dialog.found_jump_target = Mock()
        dialog.found_jump_target.get.return_value = ""
        dialog.found_delay = Mock()
        dialog.found_delay.get.return_value = "0"
        dialog.click_target_mode = Mock()
        dialog.click_target_mode.get.return_value = "识图区域中心"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "0,0"
        dialog.on_timeout = Mock()
        dialog.on_timeout.get.return_value = "continue"
        dialog.timeout_jump_target = Mock()
        dialog.timeout_jump_target.get.return_value = ""
        dialog.timeout_delay = Mock()
        dialog.timeout_delay.get.return_value = "0"
        dialog.wait_forever = Mock()
        dialog.wait_forever.get.return_value = False
        dialog.fallback_template = Mock()
        dialog.fallback_template.get.return_value = "images/y.png"
        dialog.fallback_switch_ms = Mock()
        dialog.fallback_switch_ms.get.return_value = "5000"
        dialog.fallback_region_mode = Mock()
        dialog.fallback_region_mode.get.return_value = "custom"
        dialog.fallback_region = Mock()
        dialog.fallback_region.get.return_value = "10,20,30,40"
        dialog.fallback_click = Mock()
        dialog.fallback_click.get.return_value = False
        dialog.fallback_on_match = Mock()
        dialog.fallback_on_match.get.return_value = "回到主模板的检测"
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.show_result_notice = Mock()
        dialog.show_result_notice.get.return_value = False
        dialog.jump_target_ids = {}
        dialog.master = Mock()
        dialog.destroy = Mock()
        with package_patch('dialogs', 'load_template_regions', return_value={'images/y.png': [100, 200, 300, 400]}), package_patch('dialogs', 'activate_main_after_modal'):
            dialog.save()
        result = dialog.result
        self.assertEqual(result["region_mode"], "screen")
        self.assertEqual(result["fallback_template"], "images/y.png")
        self.assertEqual(result["fallback_region_mode"], "template")
        self.assertEqual(result["fallback_region"], [])
        dialog.destroy.assert_called_once()

    def test_curtain_click_records_screen_coordinate_without_clicking_through(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.click_point = Mock()
        dialog.click_target_mode = Mock()
        dialog.click_target_mode.get.return_value = "自定义坐标"
        dialog.click_point_entry = Mock()
        dialog.click_point_button = Mock()
        dialog._apply_picked_click_point(777, 444)
        dialog.click_point.set.assert_called_once_with("777,444")
        dialog.click_target_mode.set.assert_called_once_with("自定义坐标")

    def test_module_form_curtain_hides_every_ancestor_window(self):
        # 「模块管理 → 修改模块 → 幕布选取…」时表单的直接父窗口是管理器对话框：
        # 只藏它自己是没用的，应用主窗口还留在幕布上（表现为“没隐藏软件界面”）。
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        manager = tk.Toplevel(root)
        manager.withdraw()
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        form.master = manager
        with package_patch('dialogs', 'ScreenPointPicker') as picker_class:
            form.start_click_point_selection()
        args, kwargs = picker_class.call_args
        self.assertIs(args[0], form)
        self.assertIs(args[1], manager)
        self.assertIn(root, kwargs["hidden_windows"])
        picker_class.return_value.start.assert_called_once()

    def test_image_action_curtain_hides_every_ancestor_window(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        manager = tk.Toplevel(root)
        manager.withdraw()
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.master = manager
        with package_patch('dialogs', 'ScreenPointPicker') as picker_class:
            dialog.start_click_point_selection()
        self.assertIn(root, picker_class.call_args.kwargs["hidden_windows"])

    def test_module_form_curtain_applies_picked_point(self):
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        form.click_point_var = Mock()
        form._apply_picked_click_point(321, 654)
        form.click_point_var.set.assert_called_once_with("321,654")

    def test_dialog_messages_use_root_floating_notice_callback(self):
        root = Mock()
        root._macroflow_notice_callback = Mock()
        parent = Mock()
        parent._root.return_value = root
        show_floating_notice(parent, "参数错误", "请输入有效整数", 3200)
        root._macroflow_notice_callback.assert_called_once_with(
            "参数错误：请输入有效整数", 3200,
        )

    def test_new_image_action_defaults_to_click_and_result_notice(self):
        self.assertEqual(image_action_option_defaults({}), ("click", True))
        self.assertEqual(
            image_action_option_defaults({"on_found": "continue", "show_result_notice": False}),
            ("continue", False),
        )

    def test_new_image_timeout_defaults(self):
        self.assertEqual(
            image_timeout_option_defaults({}),
            ("continue", 3000, 1000, 1, 0),
        )
        self.assertEqual(
            image_timeout_option_defaults({
                "on_timeout": "jump", "timeout_ms": 8000,
                "delay_ms": 250, "timeout_jump_row": 7,
                "timeout_delay_ms": 600,
            }),
            ("jump", 8000, 250, 7, 600),
        )
        self.assertEqual(
            image_timeout_option_defaults({"on_timeout": "end_current_script"})[0],
            "end_current_script",
        )
        self.assertEqual(image_timeout_option_label("end_current_script"), "结束当前脚本")
        self.assertEqual(image_timeout_option_value("结束当前脚本"), "end_current_script")

    def test_image_jump_options_follow_action_ids_not_old_rows(self):
        actions = [
            {"type": "comment", "text": "新插入", ACTION_ID_KEY: "new"},
            {"type": "click", ACTION_ID_KEY: "target"},
        ]
        options = image_jump_target_options(actions)
        self.assertEqual(options[1][1], "target")
        self.assertIn("第 2 行", options[1][0])

    def test_jump_dialog_saves_start_and_end_targets(self):
        dialog = JumpActionDialog.__new__(JumpActionDialog)
        dialog.target = Mock()
        dialog.target_ids = {
            "开头": SCRIPT_START_TARGET_ID,
            "结尾": NEXT_WORKFLOW_STEP_TARGET_ID,
        }
        dialog.target_rows = {"开头": 1, "结尾": 4}
        dialog.target.get.return_value = "结尾"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "jump", "jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
            "jump_row": 4, "workflow_repeat_at_least_2": True, "delay_ms": 0,
        })
        dialog.destroy.assert_called_once()

    def test_jump_dialog_saves_workflow_second_repeat_condition(self):
        dialog = JumpActionDialog.__new__(JumpActionDialog)
        dialog.target = Mock()
        dialog.target.get.return_value = "目标"
        dialog.target_ids = {"目标": "target"}
        dialog.target_rows = {"目标": 3}
        dialog.workflow_repeat_at_least_2 = Mock()
        dialog.workflow_repeat_at_least_2.get.return_value = True
        dialog.destroy = Mock()

        dialog.save()

        self.assertTrue(dialog.result["workflow_repeat_at_least_2"])
        self.assertEqual(dialog.result["jump_action_id"], "target")

    def test_template_match_supports_chinese_file_path(self):
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "测试服务器.png"
            template = np.zeros((16, 18, 3), dtype=np.uint8)
            template[2:14, 3:15] = (20, 180, 240)
            template[6:10, :] = (230, 30, 40)
            encoded_ok, encoded = cv2.imencode(".png", template)
            self.assertTrue(encoded_ok)
            template_path.write_bytes(encoded.tobytes())
            screen = np.zeros((80, 100, 3), dtype=np.uint8)
            screen[25:41, 44:62] = template
            with patch("macroflow.core.image_match.capture_bgr", return_value=(screen, (10, 20))):
                match = find_template(template_path, 0.95)
            self.assertIsNotNone(match)
            self.assertEqual((match["x"], match["y"]), (54, 45))

    def test_template_match(self):
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "template.png"
            template = np.zeros((16, 18, 3), dtype=np.uint8)
            template[2:14, 3:15] = (20, 180, 240)
            template[6:10, :] = (230, 30, 40)
            cv2.imwrite(str(template_path), template)
            screen = np.zeros((80, 100, 3), dtype=np.uint8)
            screen[25:41, 44:62] = template
            with patch("macroflow.core.image_match.capture_bgr", return_value=(screen, (10, 20))):
                match = find_template(template_path, 0.95)
            self.assertIsNotNone(match)
            self.assertEqual((match["x"], match["y"]), (54, 45))

    def test_template_scale_matches_resized_target(self):
        # 执行机截图尺寸与录制机不同（多屏/分辨率/DPI 差异）时，目标在截图
        # 里的像素大小与模板不一致，固定尺寸匹配会失败；按屏幕宽度比缩放
        # 模板后再匹配即可命中，匹配结果坐标仍是截图坐标系。
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "resized.png"
            template = np.zeros((16, 18, 3), dtype=np.uint8)
            template[2:14, 3:15] = (20, 180, 240)
            template[6:10, :] = (230, 30, 40)
            cv2.imwrite(str(template_path), template)
            screen = np.zeros((80, 100, 3), dtype=np.uint8)
            screen[25:41, 44:62] = template
            scaled = cv2.resize(screen, (200, 160), interpolation=cv2.INTER_AREA)
            # 不缩放：40% 面积差导致匹配度跌到阈值以下（用户遇到的现象）。
            self.assertIsNone(find_template_in_image(template_path, scaled, 0.95))
            # 按 2x 缩放模板：命中且位置（44,25）×2 = (88,50) 正确。
            match = find_template_in_image(template_path, scaled, 0.95, scale=2.0)
            self.assertIsNotNone(match)
            self.assertEqual((match["x"], match["y"]), (88, 50))

    def test_screen_template_scale(self):
        self.assertEqual(screen_template_scale(
            {"width": 1920, "height": 1080}, {"width": 3840, "height": 2160}), 2.0)
        self.assertEqual(screen_template_scale(
            {"width": 3840, "height": 2160}, {"width": 1920, "height": 1080}), 0.5)
        self.assertEqual(screen_template_scale(
            {"width": 1920, "height": 1080}, {"width": 1920, "height": 1080}), 1.0)
        self.assertEqual(screen_template_scale(None, {"width": 3840}), 1.0)
        self.assertEqual(screen_template_scale({"width": 0}, {"width": 3840}), 1.0)

    def test_playback_screen_uses_target_window_monitor(self):
        monitor = {"left": -1920, "top": 181, "width": 1920, "height": 1080}
        primary = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with package_patch('player', 'get_monitor_rect_for_window', side_effect=lambda hwnd: monitor if hwnd else None), \
             package_patch('player', 'get_primary_screen_rect', return_value=primary):
            self.assertEqual(get_playback_screen_rect(123), monitor)
            self.assertEqual(get_playback_screen_rect(None), primary)

    def test_image_match_captures_target_monitor_instead_of_virtual_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "target.png"
            template_path.write_bytes(b"template")
            monitor = {"left": -1920, "top": 181, "width": 1920, "height": 1080}
            match = {
                "x": -100, "y": 200, "width": 52, "height": 14,
                "center_x": -74, "center_y": 207, "score": 0.95,
            }
            player = MacroPlayer()
            with package_patch('player', 'get_playback_screen_rect', return_value=monitor), \
                 package_patch('player', 'is_window', return_value=True), \
                 package_patch('player', 'is_window_process_foreground', return_value=True), \
                 package_patch('player', 'find_template', return_value=match) as find, \
                 package_patch('player', 'show_overlay'):
                player.play(
                    [{"type": "image_match", "template": str(template_path),
                      "on_found": "continue"}],
                    hwnd=123,
                    source_screen={"left": 0, "top": 0, "width": 1920, "height": 1080},
                )
            self.assertEqual(find.call_args.args[2], monitor)
            self.assertEqual(find.call_args.kwargs["scale"], 1.0)

    def test_template_match_reuses_existing_full_screenshot_with_region(self):
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "shared.png"
            template = np.zeros((12, 14, 3), dtype=np.uint8)
            template[2:10, 3:11] = (10, 170, 230)
            template[5:8, :] = (220, 20, 30)
            cv2.imwrite(str(template_path), template)
            screen = np.zeros((100, 140, 3), dtype=np.uint8)
            screen[45:57, 70:84] = template
            match = find_template_in_image(
                template_path, screen, 0.95, origin=(10, 20),
                region=(60, 50, 60, 50),
            )
        self.assertIsNotNone(match)
        self.assertEqual((match["x"], match["y"]), (80, 65))

    def test_ignore_background_matches_when_background_changed(self):
        # 模板：深灰底 + 白笔画。截图：金色纹理底 + 相同白笔画（按钮高亮）。
        # 背景颜色变了，普通匹配分数掉到阈值下；忽略背景只按笔画匹配仍高分命中。
        rng = np.random.default_rng(7)
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "button.png"
            template = np.zeros((40, 120, 3), dtype=np.uint8)
            template[:] = (35, 35, 35)
            for y, x0, w in [(8, 10, 100), (18, 10, 100), (28, 10, 60)]:
                template[y:y + 6, x0:x0 + w] = (245, 245, 245)
            template = cv2.GaussianBlur(template, (3, 3), 0)
            cv2.imwrite(str(template_path), template)
            screen = np.zeros((300, 400, 3), dtype=np.uint8)
            for x in range(400):
                for y in range(300):
                    pick = (x // 13 + y // 13) % 3
                    screen[y, x] = (
                        (220, 170, 60) if pick == 0 else
                        (180, 120, 30) if pick == 1 else (120, 70, 15)
                    )
            screen += rng.integers(0, 10, screen.shape).astype(np.uint8)
            # 笔画绘制后做一次模糊模拟抗锯齿（与模板截图一致），纹理部分保持原样。
            stroke_mask = np.zeros((300, 400), dtype=np.uint8)
            for y, x0, w in [(8, 10, 100), (18, 10, 100), (28, 10, 60)]:
                screen[100 + y:106 + y, 50 + x0:50 + x0 + w] = (245, 245, 245)
                stroke_mask[100 + y:106 + y, 50 + x0:50 + x0 + w] = 255
            blurred = cv2.GaussianBlur(screen, (3, 3), 0)
            screen = np.where(stroke_mask[..., None] > 0, blurred, screen)
            self.assertIsNone(find_template_in_image(template_path, screen, 0.92))
            match = find_template_in_image(
                template_path, screen, 0.92, ignore_background=True,
            )
            self.assertIsNotNone(match)
            self.assertEqual((match["x"], match["y"]), (50, 100))
            self.assertGreater(match["score"], 0.95)

    def test_ignore_background_rejects_when_text_absent(self):
        # 没有文字的目标图：忽略背景匹配不应在纯背景上产生假阳性。
        rng = np.random.default_rng(7)
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "button.png"
            template = np.zeros((40, 120, 3), dtype=np.uint8)
            template[:] = (35, 35, 35)
            for y, x0, w in [(8, 10, 100), (18, 10, 100), (28, 10, 60)]:
                template[y:y + 6, x0:x0 + w] = (245, 245, 245)
            template = cv2.GaussianBlur(template, (3, 3), 0)
            cv2.imwrite(str(template_path), template)
            screen = np.zeros((300, 400, 3), dtype=np.uint8)
            for x in range(400):
                for y in range(300):
                    pick = (x // 13 + y // 13) % 3
                    screen[y, x] = (
                        (60, 30, 160) if pick == 0 else
                        (40, 20, 110) if pick == 1 else (25, 12, 70)
                    )
            screen += rng.integers(0, 10, screen.shape).astype(np.uint8)
            self.assertIsNone(
                find_template_in_image(
                    template_path, screen, 0.85, ignore_background=True,
                )
            )

    def test_ignore_background_falls_back_when_background_unidentifiable(self):
        # 模板本身是渐变背景，无法用单一颜色描述 → 忽略背景自动回退普通匹配。
        # 渐变背景在目标截图里原样保留，普通匹配仍能精确命中。
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "button.png"
            template = np.zeros((40, 120, 3), dtype=np.uint8)
            for x in range(120):
                shade = 40 + x * 180 // 120
                template[:, x] = (shade, shade, shade)
            for y, x0, w in [(8, 10, 100), (18, 10, 100), (28, 10, 60)]:
                template[y:y + 6, x0:x0 + w] = (245, 245, 245)
            cv2.imwrite(str(template_path), template)
            screen = np.zeros((300, 400, 3), dtype=np.uint8)
            for x in range(400):
                shade = 40 + x * 180 // 400
                screen[:, x] = (shade, shade, shade)
            screen[100:140, 50:170] = template
            match = find_template_in_image(
                template_path, screen, 0.85, ignore_background=True,
            )
            self.assertIsNotNone(match)
            self.assertEqual((match["x"], match["y"]), (50, 100))

    def test_ensure_action_ids_migrates_found_jump_row(self):
        actions = [
            {"type": "comment", "text": "目标"},
            {"type": "image_match", "on_found": "jump", "found_jump_row": 1},
        ]
        ensure_action_ids(actions)
        self.assertEqual(actions[1]["found_jump_action_id"], actions[0][ACTION_ID_KEY])

    def test_clone_actions_remap_found_jump_target(self):
        actions = [
            {"type": "image_match", "on_found": "jump", "found_jump_action_id": "target"},
            {"type": "comment", "text": "目标", ACTION_ID_KEY: "target"},
        ]
        clones = clone_actions_with_new_ids(actions)
        self.assertNotEqual(clones[0]["found_jump_action_id"], "target")
        self.assertEqual(clones[0]["found_jump_action_id"], clones[1][ACTION_ID_KEY])

    def test_found_jump_options_include_finish_current_script(self):
        actions = [{"type": "comment", "text": "目标", ACTION_ID_KEY: "target"}]
        options = image_found_jump_target_options(actions)
        self.assertEqual(options[0], (
            "结束当前脚本执行",
            NEXT_WORKFLOW_STEP_TARGET_ID,
        ))
        self.assertEqual(options[1][1], "target")

    def test_clone_actions_preserves_next_workflow_target(self):
        actions = [{
            "type": "image_match", "on_found": "jump",
            "found_jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
        }]
        clones = clone_actions_with_new_ids(actions)
        self.assertEqual(
            clones[0]["found_jump_action_id"], NEXT_WORKFLOW_STEP_TARGET_ID,
        )

    def test_found_jump_control_state_follows_on_found(self):
        dialog = ImageActionDialog.__new__(ImageActionDialog)
        dialog.on_found = Mock()
        dialog.found_jump_entry = Mock()
        dialog.on_found.get.return_value = "jump"
        dialog._update_found_jump_control()
        dialog.found_jump_entry.configure.assert_called_with(state="normal")

        dialog.on_found.get.return_value = "continue"
        dialog._update_found_jump_control()
        dialog.found_jump_entry.configure.assert_called_with(state="disabled")

    def test_click_dialog_applies_picked_position(self):
        dialog = ClickDialog.__new__(ClickDialog)
        dialog.x = Mock()
        dialog.y = Mock()
        dialog._apply_picked_point(777, 444)
        dialog.x.set.assert_called_once_with("777")
        dialog.y.set.assert_called_once_with("444")

    def test_click_dialog_saves_values(self):
        dialog = ClickDialog.__new__(ClickDialog)
        dialog.kind = "click"
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.x = Mock()
        dialog.x.get.return_value = "300"
        dialog.y = Mock()
        dialog.y.get.return_value = "400"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "30"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "500"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "click", "button": "left",
            "x": 300, "y": 400, "hold_ms": 30, "delay_ms": 500,
        })
        dialog.destroy.assert_called_once()

    def test_click_dialog_saves_current_pos_mode(self):
        dialog = ClickDialog.__new__(ClickDialog)
        dialog.kind = "click"
        dialog.button = Mock()
        dialog.button.get.return_value = "right"
        dialog.x = Mock()
        dialog.x.get.return_value = "300"
        dialog.y = Mock()
        dialog.y.get.return_value = "400"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "30"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "500"
        dialog.pos_mode = Mock()
        dialog.pos_mode.get.return_value = True
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "click", "button": "right",
            "x": 300, "y": 400, "hold_ms": 30, "delay_ms": 500,
            "pos_mode": "current",
        })

    def test_click_dialog_delay_and_coords_are_editable(self):
        # 回归：X/Y 可手动输入（保留幕布选取按钮）；执行前延时绝不能被
        # _update_pos_mode 误设只读（曾因 _y_entry 槽位被延时框覆盖而只读）。
        root = tk.Tk()
        root.withdraw()
        # 前面的用例可能已经销毁过自己的 ttkbootstrap 根窗口，而 Style 是模块级
        # 单例——它仍指向那个死掉的解释器，弹窗一建 ttk 控件就会报“application
        # has been destroyed”。这里按同一套写法重建 Style 并绑定本用例的根窗口。
        previous_style = ttkbootstrap.style.Style.instance
        ttkbootstrap.style.Style.instance = None
        try:
            dialog = ClickDialog(root, {"type": "click"})
            entries: dict[str, tk.Widget] = {}

            def collect(widget):
                if widget.winfo_class() == "TEntry":
                    name = widget.cget("textvariable")
                    if name:
                        entries[name] = widget
                for child in widget.winfo_children():
                    collect(child)

            collect(dialog)
            for variable, label in ((dialog.x, "X"), (dialog.y, "Y"), (dialog.delay, "延时")):
                entry = entries.get(str(variable))
                self.assertIsNotNone(entry, f"缺少 {label} 输入框")
                self.assertEqual(str(entry.cget("state")), "normal", f"{label} 应可输入")
            dialog.pos_mode.set(True)
            dialog._update_pos_mode()
            self.assertEqual(str(entries[str(dialog.x)].cget("state")), "disabled")
            self.assertEqual(str(entries[str(dialog.y)].cget("state")), "disabled")
            self.assertEqual(str(entries[str(dialog.delay)].cget("state")), "normal")
            dialog.destroy()
        finally:
            ttkbootstrap.style.Style.instance = previous_style
            root.destroy()

    def test_click_summary_shows_current_position(self):
        self.assertEqual(
            action_summary({"type": "click", "button": "left", "pos_mode": "current"})[1],
            "left @ 鼠标当前位置",
        )
        self.assertEqual(
            action_summary({"type": "click", "button": "left", "x": 10, "y": 20})[1],
            "left @ (10, 20)",
        )

    def test_mouse_button_dialog_saves_press_and_release(self):
        for down_label, down_value in (("按下", True), ("松开", False)):
            dialog = ClickDialog.__new__(ClickDialog)
            dialog.kind = "mouse_button"
            dialog.button = Mock()
            dialog.button.get.return_value = "middle"
            dialog.x = Mock()
            dialog.x.get.return_value = "100"
            dialog.y = Mock()
            dialog.y.get.return_value = "200"
            dialog.down = Mock()
            dialog.down.get.return_value = down_label
            dialog.delay = Mock()
            dialog.delay.get.return_value = "0"
            dialog.destroy = Mock()
            dialog.save()
            self.assertEqual(dialog.result, {
                "type": "mouse_button", "button": "middle",
                "down": down_value, "x": 100, "y": 200, "delay_ms": 0,
            })
            dialog.destroy.assert_called_once()

    def test_mouse_button_dialog_saves_delay(self):
        dialog = ClickDialog.__new__(ClickDialog)
        dialog.kind = "mouse_button"
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.x = Mock()
        dialog.x.get.return_value = "10"
        dialog.y = Mock()
        dialog.y.get.return_value = "20"
        dialog.down = Mock()
        dialog.down.get.return_value = "按下"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "800"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["delay_ms"], 800)

    def test_editing_mouse_button_action_uses_click_dialog(self):
        original = {"type": "mouse_button", "action_id": "stable-mb", "down": True}
        with package_patch('dialogs', 'ClickDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "mouse_button", "button": "left", "down": False,
                "x": 1, "y": 2,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-mb")
        self.assertFalse(updated["down"])
        dialog_class.assert_called_once()

    def test_editing_module_row_uses_jump_mode_dialog(self):
        # v1.68：编辑带 jump_row 的全局模块行时用跳转模式对话框。
        # v1.70：跳转目标是行对象，对话框拿到全部动作用于行选择列表。
        original = {"type": "global_detect", "action_id": "stable-g",
                    "template": "images/g.png", "jump_row": 4}
        others = [{"type": "delay", "ms": 1, "action_id": "a"},
                  {"type": "key_press", "name": "B", "action_id": "b"}]
        with package_patch('dialogs', 'GlobalDetectDialog') as dialog_class:
            dialog_class.return_value.show.return_value = dict(original)
            updated = edit_action(None, original, others)
        self.assertEqual(updated["action_id"], "stable-g")
        dialog_class.assert_called_once_with(None, original, jump=True, actions=others)

    def test_editing_plain_global_detect_keeps_default_dialog_mode(self):
        original = {"type": "global_detect", "action_id": "stable-g",
                    "template": "images/g.png"}
        with package_patch('dialogs', 'GlobalDetectDialog') as dialog_class:
            dialog_class.return_value.show.return_value = dict(original)
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-g")
        dialog_class.assert_called_once_with(None, original, jump=False, actions=None)

    def test_repeat_click_dialog_saves_values(self):
        dialog = RepeatClickDialog.__new__(RepeatClickDialog)
        dialog.button = Mock()
        dialog.button.get.return_value = "left"
        dialog.x = Mock()
        dialog.x.get.return_value = "300"
        dialog.y = Mock()
        dialog.y.get.return_value = "400"
        dialog.count = Mock()
        dialog.count.get.return_value = "5"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "80"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "20"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "1000"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "repeat_click", "button": "left",
            "x": 300, "y": 400,
            "count": 5, "interval_ms": 80,
            "hold_ms": 20, "delay_ms": 1000,
        })
        dialog.destroy.assert_called_once()

    def test_repeat_click_dialog_clamps_count_and_interval(self):
        dialog = RepeatClickDialog.__new__(RepeatClickDialog)
        dialog.button = Mock()
        dialog.button.get.return_value = "right"
        dialog.x = Mock()
        dialog.x.get.return_value = "10"
        dialog.y = Mock()
        dialog.y.get.return_value = "20"
        dialog.count = Mock()
        dialog.count.get.return_value = "0"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "-5"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "0"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "-1"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["count"], 1)
        self.assertEqual(dialog.result["interval_ms"], 0)
        self.assertEqual(dialog.result["hold_ms"], 1)
        self.assertEqual(dialog.result["delay_ms"], 0)

    def test_repeat_click_dialog_applies_picked_position(self):
        dialog = RepeatClickDialog.__new__(RepeatClickDialog)
        dialog.x = Mock()
        dialog.y = Mock()
        dialog._apply_picked_point(555, 666)
        dialog.x.set.assert_called_once_with("555")
        dialog.y.set.assert_called_once_with("666")

    def test_scroll_dialog_saves_direction_clicks_and_position(self):
        dialog = ScrollDialog.__new__(ScrollDialog)
        dialog._source = {}
        dialog.direction = Mock()
        dialog.direction.get.return_value = SCROLL_DOWN_LABEL
        dialog.clicks = Mock()
        dialog.clicks.get.return_value = "3"
        dialog.x = Mock()
        dialog.x.get.return_value = "640"
        dialog.y = Mock()
        dialog.y.get.return_value = "360"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "200"
        dialog.destroy = Mock()
        dialog.save()
        # 向下 = Windows 滚轮 delta 负值，格数即 delta 的绝对值。
        self.assertEqual(dialog.result, {
            "type": "scroll", "dx": 0, "dy": -3, "x": 640, "y": 360, "delay_ms": 200,
        })
        dialog.destroy.assert_called_once()

    def test_scroll_dialog_saves_upward_as_positive_delta(self):
        dialog = ScrollDialog.__new__(ScrollDialog)
        dialog._source = {}
        dialog.direction = Mock()
        dialog.direction.get.return_value = SCROLL_UP_LABEL
        dialog.clicks = Mock()
        dialog.clicks.get.return_value = "5"
        dialog.x = Mock()
        dialog.x.get.return_value = "1"
        dialog.y = Mock()
        dialog.y.get.return_value = "2"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["dy"], 5)
        self.assertEqual(dialog.result["dx"], 0)

    def test_scroll_dialog_clamps_clicks_and_delay(self):
        dialog = ScrollDialog.__new__(ScrollDialog)
        dialog._source = {}
        dialog.direction = Mock()
        dialog.direction.get.return_value = SCROLL_DOWN_LABEL
        dialog.clicks = Mock()
        dialog.clicks.get.return_value = "0"
        dialog.x = Mock()
        dialog.x.get.return_value = "10"
        dialog.y = Mock()
        dialog.y.get.return_value = "20"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "-1"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["dy"], -1)
        self.assertEqual(dialog.result["delay_ms"], 0)

    def test_scroll_dialog_applies_picked_position(self):
        dialog = ScrollDialog.__new__(ScrollDialog)
        dialog.x = Mock()
        dialog.y = Mock()
        dialog._apply_picked_point(777, 888)
        dialog.x.set.assert_called_once_with("777")
        dialog.y.set.assert_called_once_with("888")

    def test_scroll_dialog_reads_direction_and_clicks_from_existing_action(self):
        root = tk.Tk()
        root.withdraw()
        # ttkbootstrap 的 Style 单例绑定在第一个 Tk 根窗口上：重建一次绑定到当前根窗口。
        previous_style = ttkbootstrap.style.Style.instance
        ttkbootstrap.style.Style.instance = None
        ttkbootstrap.publisher.Publisher.clear_subscribers()
        ttkbootstrap.style.Style()
        try:
            dialog = ScrollDialog(root, {
                "type": "scroll", "dx": 0, "dy": -4, "x": 111, "y": 222, "delay_ms": 300,
            })
            self.assertEqual(dialog.direction.get(), SCROLL_DOWN_LABEL)
            self.assertEqual(dialog.clicks.get(), "4")
            self.assertEqual(dialog.x.get(), "111")
            self.assertEqual(dialog.y.get(), "222")
            dialog.destroy()
            upward = ScrollDialog(root, {"type": "scroll", "dy": 7})
            self.assertEqual(upward.direction.get(), SCROLL_UP_LABEL)
            self.assertEqual(upward.clicks.get(), "7")
            upward.destroy()
        finally:
            root.destroy()
            ttkbootstrap.publisher.Publisher.clear_subscribers()
            ttkbootstrap.style.Style.instance = previous_style

    def test_scroll_summary_shows_direction_position_and_clicks(self):
        self.assertEqual(
            action_summary({"type": "scroll", "dx": 0, "dy": -3, "x": 640, "y": 360})[:2],
            ("↕  滚轮", "向下 3 格 @ (640, 360)"),
        )
        self.assertEqual(
            action_summary({"type": "scroll", "dx": 0, "dy": 2, "x": 1, "y": 2})[1],
            "向上 2 格 @ (1, 2)",
        )

    def test_edit_action_routes_scroll_to_scroll_dialog(self):
        original = {"type": "scroll", "action_id": "stable-scroll",
                    "dx": 0, "dy": -3, "x": 5, "y": 6}
        with package_patch('dialogs', 'ScrollDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "scroll", "dx": 0, "dy": 9, "x": 7, "y": 8,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-scroll")
        self.assertEqual(updated["dy"], 9)
        dialog_class.assert_called_once()

    def test_segment_add_menu_exposes_scroll(self):
        source = inspect.getsource(SegmentEditorMixin._add_segment_item)
        self.assertIn("滚轮", source)
        self.assertIn("ScrollDialog", source)

    def test_segment_row_label_shows_scroll_direction_and_clicks(self):
        self.assertEqual(
            segment_row_label({"type": "scroll", "dx": 0, "dy": -3, "x": 1, "y": 2}),
            "滚轮 向下 3 格",
        )

    def test_text_action_dialog_saves_values(self):
        dialog = TextActionDialog.__new__(TextActionDialog)
        dialog.text_var = Mock()
        dialog.text_var.get.return_value = "你好"
        dialog.char_delay = Mock()
        dialog.char_delay.get.return_value = "20"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "1000"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "text", "text": "你好", "char_delay_ms": 20, "delay_ms": 1000,
        })
        dialog.destroy.assert_called_once()

    def test_open_app_dialog_saves_values(self):
        dialog = OpenAppDialog.__new__(OpenAppDialog)
        dialog.path = Mock()
        dialog.path.get.return_value = "C:/Tools/游戏.exe"
        dialog.args = Mock()
        dialog.args.get.return_value = " -windowed "
        dialog.delay = Mock()
        dialog.delay.get.return_value = "200"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "500"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "open_app", "path": "C:/Tools/游戏.exe",
            "args": "-windowed",
            "delay_ms": 200, "after_delay_ms": 500,
        })
        dialog.destroy.assert_called_once()

    def test_open_app_dialog_saves_without_args(self):
        dialog = OpenAppDialog.__new__(OpenAppDialog)
        dialog.path = Mock()
        dialog.path.get.return_value = "C:/Tools/游戏.exe"
        dialog.args = Mock()
        dialog.args.get.return_value = ""
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["args"], "")
        dialog.destroy.assert_called_once()

    def test_close_app_dialog_saves_values(self):
        dialog = CloseAppDialog.__new__(CloseAppDialog)
        dialog.name = Mock()
        dialog.name.get.return_value = " clash-verge.exe "
        dialog.graceful = Mock()
        dialog.graceful.get.return_value = True
        dialog.graceful_wait_ms = Mock()
        dialog.graceful_wait_ms.get.return_value = "3000"
        dialog.tree = Mock()
        dialog.tree.get.return_value = False
        dialog.elevated_retry = Mock()
        dialog.elevated_retry.get.return_value = True
        dialog.delay = Mock()
        dialog.delay.get.return_value = "100"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "type": "close_app", "name": "clash-verge.exe",
            "graceful": True, "graceful_wait_ms": 3000,
            "tree": False, "elevated_retry": True,
            "delay_ms": 100, "after_delay_ms": 0,
        })
        dialog.destroy.assert_called_once()

    def test_move_dialog_absolute_pick_records_position(self):
        dialog = MouseMoveDialog.__new__(MouseMoveDialog)
        dialog.mode = Mock()
        dialog.mode.get.return_value = "absolute"
        dialog.x = Mock()
        dialog.y = Mock()
        dialog._apply_picked_point(640, 360)
        dialog.x.set.assert_called_once_with("640")
        dialog.y.set.assert_called_once_with("360")

    def test_move_dialog_relative_measurement_computes_delta(self):
        dialog = MouseMoveDialog.__new__(MouseMoveDialog)
        dialog.mode = Mock()
        dialog.mode.get.return_value = "relative"
        dialog.x = Mock()
        dialog.y = Mock()
        dialog._apply_picked_point(100, 50, 260, 110)
        dialog.x.set.assert_called_once_with("160")
        dialog.y.set.assert_called_once_with("60")

    def test_screen_point_picker_single_click_reports_point(self):
        picker = ScreenPointPicker.__new__(ScreenPointPicker)
        picker.two_points = False
        picker.close = Mock()
        picker.on_result = Mock()
        picker._on_click(Mock(x_root=333, y_root=222))
        picker.close.assert_called_once()
        picker.on_result.assert_called_once_with(333, 222)

    def test_screen_point_picker_two_points_reports_start_and_end(self):
        picker = ScreenPointPicker.__new__(ScreenPointPicker)
        picker.two_points = True
        picker.first_point = None
        picker.canvas = Mock()
        picker.tip_id = 1
        picker.close = Mock()
        picker.on_result = Mock()
        picker._on_click(Mock(x_root=100, y_root=50))
        self.assertEqual(picker.first_point, (100, 50))
        picker.on_result.assert_not_called()
        picker._on_click(Mock(x_root=260, y_root=110))
        picker.on_result.assert_called_once_with(100, 50, 260, 110)


class OcrTests(unittest.TestCase):

    def test_row_offsets_are_locally_corrected_without_accumulating_drift(self):
        stabilize = getattr(image_match_module, "stabilize_row_offsets", None)
        self.assertIsNotNone(stabilize, "缺少逐行局部分隔线校正算法")
        screen = np.full((100, 80, 3), 35, dtype=np.uint8)
        for y in (25, 50, 75):
            screen[y:y + 1, :] = 190

        self.assertEqual(
            stabilize(screen, [0, 26, 52], first_row_bottom=26, tolerance=3),
            [0, 25, 50],
        )

    def test_parse_ocr_number_pair_accepts_full_width_separator_and_spaces(self):
        self.assertEqual(parse_ocr_number_pair("当前 １２ ／ １２", "/"), (12, 12))
        self.assertEqual(parse_ocr_number_pair("挑战 12 / 34", "/"), (12, 34))

    def test_parse_ocr_number_pair_accepts_common_ocr_slash_confusions(self):
        self.assertEqual(parse_ocr_number_pair("15.16", "/"), (15, 16))
        self.assertEqual(parse_ocr_number_pair("17.(12", "/"), (17, 12))
        self.assertEqual(parse_ocr_number_pair("2|12", "/"), (2, 12))

    def test_parse_ocr_number_pair_rejects_missing_or_malformed_side(self):
        self.assertIsNone(parse_ocr_number_pair("12-34", "/"))
        self.assertIsNone(parse_ocr_number_pair("12/", "/"))
        self.assertIsNone(parse_ocr_number_pair("/34", "/"))

    def test_ocr_compare_dialog_saves_custom_regions_and_two_branches(self):
        form = OcrCompareActionDialog.__new__(OcrCompareActionDialog)
        form.master = Mock()
        form.jump_target_ids = {"第 1 行 · 延时": "aid1", "第 2 行 · 点击": "aid2"}
        form.region = Mock(); form.region.get.return_value = "10,20,300,400"
        form.separator = Mock(); form.separator.get.return_value = "/"
        form.click_region = Mock(); form.click_region.get.return_value = "100,200,50,40"
        form.button = Mock(); form.button.get.return_value = "left"
        form.equal_action = Mock(); form.equal_action.get.return_value = "连续点击"
        form.equal_click_count = Mock(); form.equal_click_count.get.return_value = "3"
        form.equal_jump_target = Mock(); form.equal_jump_target.get.return_value = ""
        form.not_equal_action = Mock(); form.not_equal_action.get.return_value = "跳转到目标动作"
        form.not_equal_click_count = Mock(); form.not_equal_click_count.get.return_value = "1"
        form.not_equal_jump_target = Mock(); form.not_equal_jump_target.get.return_value = "第 2 行 · 点击"
        form.timeout = Mock(); form.timeout.get.return_value = "3000"
        form.interval = Mock(); form.interval.get.return_value = "500"
        form.on_timeout = Mock(); form.on_timeout.get.return_value = "继续执行"
        form.timeout_jump_target = Mock(); form.timeout_jump_target.get.return_value = ""
        form.show_result_notice = Mock(); form.show_result_notice.get.return_value = True
        form.destroy = Mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        form.destroy.assert_called_once()
        self.assertEqual(form.result["type"], "ocr_compare")
        self.assertEqual(form.result["region"], [10, 20, 300, 400])
        self.assertEqual(form.result["click_region"], [100, 200, 50, 40])
        self.assertEqual(form.result["equal_action"], "click")
        self.assertEqual(form.result["equal_click_count"], 3)
        self.assertEqual(form.result["not_equal_action"], "jump")
        self.assertEqual(form.result["not_equal_jump_action_id"], "aid2")

    def test_multi_condition_click_dialog_saves_three_selectable_conditions(self):
        form_class = getattr(dialog_module, "MultiConditionClickDialog", None)
        self.assertIsNotNone(form_class, "缺少固定三条件多条件识图点击配置窗口")
        form = form_class.__new__(form_class)
        form.master = Mock()
        form.condition_enabled = [Mock(), Mock(), Mock()]
        form.condition_enabled[0].get.return_value = True
        form.condition_enabled[1].get.return_value = True
        form.condition_enabled[2].get.return_value = False
        form.condition_type = [Mock(), Mock(), Mock()]
        form.condition_type[0].get.return_value = "image"
        form.condition_type[1].get.return_value = "ocr"
        form.condition_type[2].get.return_value = "ocr"
        form.condition_ocr_mode = [Mock(), Mock(), Mock()]
        form.condition_ocr_mode[0].get.return_value = "text"
        form.condition_ocr_mode[1].get.return_value = "text"
        form.condition_ocr_mode[2].get.return_value = "number"
        form.condition_region = [Mock(), Mock(), Mock()]
        form.condition_region[0].get.return_value = "10,20,100,80"
        form.condition_region[1].get.return_value = "200,20,120,40"
        form.condition_region[2].get.return_value = "400,20,120,40"
        form.condition_module_key = [Mock(), Mock(), Mock()]
        form.condition_module_key[0].get.return_value = "module:first"
        form.condition_module_key[1].get.return_value = ""
        form.condition_module_key[2].get.return_value = ""
        form.condition_template = [Mock(), Mock(), Mock()]
        form.condition_template[0].get.return_value = "button.png"
        form.condition_template[1].get.return_value = ""
        form.condition_template[2].get.return_value = ""
        form.condition_threshold = [Mock(), Mock(), Mock()]
        form.condition_threshold[0].get.return_value = "0.9"
        form.condition_threshold[1].get.return_value = "0.85"
        form.condition_threshold[2].get.return_value = "0.85"
        form.condition_expected = [Mock(), Mock(), Mock()]
        form.condition_expected[0].get.return_value = ""
        form.condition_expected[1].get.return_value = "完成"
        form.condition_expected[2].get.return_value = ""
        form.condition_match_mode = [Mock(), Mock(), Mock()]
        form.condition_match_mode[0].get.return_value = "contains"
        form.condition_match_mode[1].get.return_value = "contains"
        form.condition_match_mode[2].get.return_value = "contains"
        form.condition_separator = [Mock(), Mock(), Mock()]
        form.condition_separator[0].get.return_value = "/"
        form.condition_separator[1].get.return_value = "/"
        form.condition_separator[2].get.return_value = "/"
        form.condition_relation = [Mock(), Mock(), Mock()]
        form.condition_relation[0].get.return_value = "equal"
        form.condition_relation[1].get.return_value = "equal"
        form.condition_relation[2].get.return_value = "not_equal"
        form.click_region = Mock(); form.click_region.get.return_value = "600,200,50,40"
        form.button = Mock(); form.button.get.return_value = "left"
        form.click_count = Mock(); form.click_count.get.return_value = "4"
        form.timeout = Mock(); form.timeout.get.return_value = "3000"
        form.interval = Mock(); form.interval.get.return_value = "500"
        form.on_timeout = Mock(); form.on_timeout.get.return_value = "continue"
        form.show_result_notice = Mock(); form.show_result_notice.get.return_value = True
        form.failure_segment_enabled = Mock(); form.failure_segment_enabled.get.return_value = True
        form.failure_segment = [{"type": "notice", "text": "多条件没命中"}]
        form.destroy = Mock()
        with package_patch('dialogs', 'registered_module_object', return_value={'category': 'switch', 'template': 'images/shared.png', 'region': [11, 22, 333, 444]}), package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        form.destroy.assert_called_once()
        self.assertEqual(form.result["type"], "multi_condition_click")
        self.assertEqual([item["enabled"] for item in form.result["conditions"]], [True, True, False])
        self.assertEqual([item["type"] for item in form.result["conditions"]], ["image", "ocr", "ocr"])
        self.assertEqual(form.result["conditions"][0]["template"], "images/shared.png")
        self.assertEqual(form.result["conditions"][0]["module_key"], "module:first")
        self.assertEqual(form.result["conditions"][0]["region"], [11, 22, 333, 444])
        self.assertTrue(form.result["conditions"][0]["module_ref"])
        self.assertEqual(form.result["conditions"][1]["expected_text"], "完成")
        self.assertEqual(form.result["conditions"][1].get("ocr_mode"), "text")
        self.assertEqual(form.result["conditions"][2].get("ocr_mode"), "number")
        self.assertEqual(form.result["conditions"][2]["separator"], "/")
        self.assertEqual(form.result["conditions"][2]["relation"], "not_equal")
        self.assertEqual(form.result["click_region"], [600, 200, 50, 40])
        self.assertTrue(form.result["failure_segment_enabled"])
        self.assertEqual(form.result["failure_actions"][0]["text"], "多条件没命中")

    def test_multi_condition_fields_follow_type_and_ocr_mode(self):
        states = getattr(dialog_module, "multi_condition_field_states", None)
        self.assertIsNotNone(states, "缺少多条件输入控件状态规则")
        self.assertEqual(states("image", "text"), {
            "image": True, "ocr_mode": False, "ocr_text": False,
            "ocr_match": False, "separator": False, "relation": False,
        })
        self.assertEqual(states("ocr", "text"), {
            "image": False, "ocr_mode": True, "ocr_text": True,
            "ocr_match": True, "separator": False, "relation": False,
        })
        self.assertEqual(states("ocr", "number"), {
            "image": False, "ocr_mode": True, "ocr_text": False,
            "ocr_match": False, "separator": True, "relation": True,
        })

    def test_row_list_condition_fields_follow_selected_type(self):
        states = getattr(dialog_module, "row_list_condition_field_states", None)
        self.assertIsNotNone(states, "缺少列表逐行条件输入控件状态规则")
        self.assertEqual(states("image"), {
            "module": True, "text": False, "match_mode": False,
            "separator": False, "relation": False,
        })
        self.assertEqual(states("text"), {
            "module": False, "text": True, "match_mode": True,
            "separator": False, "relation": False,
        })
        self.assertEqual(states("number"), {
            "module": False, "text": False, "match_mode": False,
            "separator": True, "relation": True,
        })

    def test_module_display_name_does_not_show_stable_module_id(self):
        display_name = getattr(dialog_module, "module_display_name", None)
        self.assertIsNotNone(display_name, "缺少模块显示名称解析函数")
        with package_patch('dialogs', 'registered_module_object', return_value={'name': '右侧条件模块', 'template': 'images/right.png'}):
            self.assertEqual(display_name("module:68ce87a9d03541d7a9abc4a817e794d9"), "右侧条件模块")

    def test_row_list_condition_module_selection_displays_name_but_keeps_stable_key(self):
        form_class = getattr(dialog_module, "RowListConditionClickDialog", None)
        self.assertIsNotNone(form_class, "缺少列表逐行条件点击配置窗口")
        form = form_class.__new__(form_class)
        form.condition_field_widgets = {"right": {}}
        form.right_module_key = Mock()
        form.right_module_name = Mock()
        form.right_condition_type = Mock()
        with package_patch('dialogs', 'choose_module_binding', return_value={'module_ref': True, 'module_key': 'module:68ce87a9d03541d7a9abc4a817e794d9'}), package_patch('dialogs', 'registered_module_object', return_value={'name': '右侧条件模块', 'template': 'images/right.png'}):
            form.select_condition_module("right")

        form.right_module_key.set.assert_called_once_with(
            "module:68ce87a9d03541d7a9abc4a817e794d9",
        )
        form.right_module_name.set.assert_called_once_with("右侧条件模块")

    def _row_list_dialog_form(self):
        form_class = getattr(dialog_module, "RowListConditionClickDialog", None)
        self.assertIsNotNone(form_class, "缺少列表逐行条件点击配置窗口")
        form = form_class.__new__(form_class)
        form.master = Mock()
        form.list_region = Mock(); form.list_region.get.return_value = "100,200,160,340"
        form.left_region = Mock(); form.left_region.get.return_value = "100,200,70,26"
        form.right_region = Mock(); form.right_region.get.return_value = "180,200,80,26"
        form.click_region = Mock(); form.click_region.get.return_value = "190,200,60,26"
        form.row_height = Mock(); form.row_height.get.return_value = "26"
        form.source_image = Mock(); form.source_image.get.return_value = ""
        form.left_condition_type = Mock(); form.left_condition_type.get.return_value = "number"
        form.right_condition_type = Mock(); form.right_condition_type.get.return_value = "text"
        form.left_module_key = Mock(); form.left_module_key.get.return_value = ""
        form.right_module_key = Mock(); form.right_module_key.get.return_value = ""
        form.left_expected_text = Mock(); form.left_expected_text.get.return_value = ""
        form.right_expected_text = Mock(); form.right_expected_text.get.return_value = "刚开始"
        form.left_match_mode = Mock(); form.left_match_mode.get.return_value = "contains"
        form.right_match_mode = Mock(); form.right_match_mode.get.return_value = "equals"
        form.left_separator = Mock(); form.left_separator.get.return_value = "/"
        form.right_separator = Mock(); form.right_separator.get.return_value = "/"
        form.left_relation = Mock(); form.left_relation.get.return_value = "not_equal"
        form.right_relation = Mock(); form.right_relation.get.return_value = "equal"
        form.button = Mock(); form.button.get.return_value = "left"
        form.click_count = Mock(); form.click_count.get.return_value = "1"
        form.no_match_action = Mock(); form.no_match_action.get.return_value = "retry"
        form.retry_interval = Mock(); form.retry_interval.get.return_value = "1500"
        form.on_success = Mock(); form.on_success.get.return_value = "继续下一行"
        form.success_target = Mock(); form.success_target.get.return_value = ""
        form.on_failure = Mock(); form.on_failure.get.return_value = "继续下一行"
        form.failure_target = Mock(); form.failure_target.get.return_value = ""
        form.jump_target_ids = {}
        form.result = None
        form.destroy = Mock()
        return form

    def test_row_list_dialog_saves_relative_regions_for_confirmed_screenshot(self):
        form = self._row_list_dialog_form()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        form.destroy.assert_called_once()
        self.assertEqual(form.result, {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 340],
            "left_region": [0, 0, 70, 26],
            "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "row_height": 26,
            "screenshot_path": "",
            "left_condition": {
                "type": "number", "separator": "/", "relation": "not_equal",
            },
            "right_condition": {
                "type": "text", "expected_text": "刚开始", "match_mode": "equals",
            },
            "button": "left",
            "click_count": 1,
            "no_match_action": "retry",
            "retry_interval_ms": 1500,
            "on_found": "continue",
            "found_jump_action_id": "",
            "on_timeout": "continue",
            "timeout_jump_action_id": "",
            "failure_segment_enabled": False,
            "failure_actions": [],
        })

    def test_row_list_test_buttons_route_screen_and_image_sources(self):
        form = self._row_list_dialog_form()
        form.on_test = Mock()

        form.test_recognition("screen")
        form.on_test.assert_called_once()
        self.assertEqual(form.on_test.call_args.kwargs["source"], "screen")

        form.on_test.reset_mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.test_recognition("image")
        form.on_test.assert_not_called()
        notice.assert_called_once()
        self.assertIn("测试图片", notice.call_args.args[1])

        form.source_image.get.return_value = "C:/images/list.png"
        form.test_recognition("image")
        self.assertEqual(form.on_test.call_args.kwargs["source"], "image")
        self.assertEqual(
            form.on_test.call_args.args[0]["screenshot_path"], "C:/images/list.png",
        )

    def test_row_list_dialog_saves_failure_segment(self):
        form = self._row_list_dialog_form()
        form.failure_segment_enabled = Mock(**{"get.return_value": True})
        form.failure_segment = [{"type": "notice", "text": "列表没找到"}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        self.assertTrue(form.result["failure_segment_enabled"])
        self.assertEqual(
            form.result["failure_actions"], [{"type": "notice", "text": "列表没找到"}],
        )

    def test_recognition_dialogs_build_failure_segment_controls(self):
        # 真实构建一次（窗口保持隐藏）：失败代码段开关/入口必须存在。
        root = tk.Tk()
        root.withdraw()
        # ttkbootstrap 的 Style 单例绑定在第一个 Tk 根窗口上；前一个用例销毁根
        # 窗口后单例仍指向已销毁的 root，这里重建一次以绑定到当前根窗口。
        previous_style = ttkbootstrap.style.Style.instance
        ttkbootstrap.style.Style.instance = None
        ttkbootstrap.publisher.Publisher.clear_subscribers()
        ttkbootstrap.style.Style()
        try:
            for dialog_class, kwargs in (
                (dialog_module.RowListConditionClickDialog, {"action": {}, "actions": []}),
                (dialog_module.ImageActionDialog, {"action": {}, "actions": []}),
                (dialog_module.OcrActionDialog, {"action": {}, "actions": []}),
                (dialog_module.OcrCompareActionDialog, {"action": {}, "actions": []}),
                (dialog_module.MultiConditionClickDialog, {"action": {}}),
            ):
                dialog = dialog_class(root, **kwargs)
                try:
                    self.assertTrue(
                        hasattr(dialog, "failure_segment_enabled"),
                        f"{dialog_class.__name__} 未初始化失败代码段",
                    )
                    self.assertTrue(
                        hasattr(dialog, "failure_segment_listbox"),
                        f"{dialog_class.__name__} 缺少失败代码段列表",
                    )
                finally:
                    dialog.destroy()
        finally:
            root.destroy()
            ttkbootstrap.publisher.Publisher.clear_subscribers()
            ttkbootstrap.style.Style.instance = previous_style

    @staticmethod
    def _dialog_descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from OcrTests._dialog_descendants(child)

    def test_module_editor_dialogs_scroll_instead_of_clipping(self):
        # 编辑模块引用 / 全局检测行数多，必须整表可滚动，按钮不能被挤出窗口。
        root = tk.Tk()
        root.withdraw()
        previous_style = ttkbootstrap.style.Style.instance
        ttkbootstrap.style.Style.instance = None
        ttkbootstrap.publisher.Publisher.clear_subscribers()
        ttkbootstrap.style.Style()
        module_obj = {"name": "示例模块", "template": "images/demo.png", "blocking": True}
        cases = (
            (
                dialog_module.ModuleReferenceDelayDialog,
                {"action": {
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:demo", "template": "images/demo.png",
                    "on_found": "continue", "on_timeout": "continue",
                }, "actions": []},
            ),
            (dialog_module.GlobalDetectDialog, {"action": None, "jump": True}),
        )
        try:
            for dialog_class, kwargs in cases:
                with package_patch('dialogs', 'registered_module_object', return_value=module_obj):
                    dialog = dialog_class(root, **kwargs)
                try:
                    dialog.update_idletasks()
                    self.assertIsNotNone(
                        getattr(dialog, "_form_canvas", None),
                        f"{dialog_class.__name__} 未使用可滚动表单",
                    )
                    descendants = list(self._dialog_descendants(dialog))
                    ttk_module = dialog_module.ttk
                    self.assertTrue(
                        any(isinstance(widget, ttk_module.Scrollbar) for widget in descendants),
                        f"{dialog_class.__name__} 缺少滚动条",
                    )
                    self.assertTrue(
                        any(
                            isinstance(widget, ttk_module.Button)
                            and widget.cget("text") == "确定"
                            for widget in descendants
                        ),
                        f"{dialog_class.__name__} 缺少确定按钮",
                    )
                finally:
                    dialog.destroy()
        finally:
            root.destroy()
            ttkbootstrap.publisher.Publisher.clear_subscribers()
            ttkbootstrap.style.Style.instance = previous_style

    def test_recognition_dialogs_expose_row_failure_segment(self):        # 行级失败代码段不能只给“引用模块”用：识别类动作行都要有入口并写盘。
        for name in (
            "ImageActionDialog", "OcrActionDialog", "OcrCompareActionDialog",
            "MultiConditionClickDialog", "RowListConditionClickDialog",
        ):
            dialog_class = getattr(dialog_module, name)
            self.assertTrue(
                hasattr(dialog_class, "_build_failure_segment_controls"),
                f"{name} 缺少“失败后执行代码段”入口",
            )
            source = inspect.getsource(dialog_class.save)
            if "_failure_segment_fields" not in source:
                source = inspect.getsource(dialog_class._build_action)
            self.assertIn(
                "_failure_segment_fields", source, f"{name} 保存时未写入失败代码段",
            )
            form = dialog_class.__new__(dialog_class)
            result = {}
            form._failure_segment_fields(result)
            self.assertEqual(result, {
                "failure_segment_enabled": False, "failure_actions": [],
            })

    def test_row_list_dialog_saves_success_and_failure_result_routes(self):
        form = self._row_list_dialog_form()
        form.on_success.get.return_value = "跳转到行对象"
        form.success_target.get.return_value = "目标行"
        form.on_failure.get.return_value = "结束当前最里层脚本"
        form.jump_target_ids = {"目标行": "action-target"}

        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()

        notice.assert_not_called()
        self.assertEqual(form.result["on_found"], "jump")
        self.assertEqual(form.result["found_jump_action_id"], "action-target")
        self.assertEqual(form.result["on_timeout"], "end_current_script")
        self.assertEqual(form.result["timeout_jump_action_id"], "")

    def test_row_list_dialog_saves_explicit_row_height_and_click_count(self):
        form = self._row_list_dialog_form()
        form.left_region.get.return_value = "100,205,70,20"
        form.right_region.get.return_value = "180,205,80,20"
        form.click_region.get.return_value = "190,200,60,26"
        form.row_height.get.return_value = "27"
        form.click_count.get.return_value = "3"

        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()

        notice.assert_not_called()
        self.assertEqual(form.result["row_height"], 27)
        self.assertEqual(form.result["click_count"], 3)
        self.assertEqual(form.result["left_region"], [0, 5, 70, 20])
        self.assertEqual(form.result["right_region"], [80, 5, 80, 20])

    def test_row_list_dialog_rejects_invalid_click_count(self):
        form = self._row_list_dialog_form()
        form.click_count.get.return_value = "0"

        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()

        notice.assert_called_once()
        self.assertIsNone(form.result)
        form.destroy.assert_not_called()

    def test_row_list_dialog_saves_negative_virtual_desktop_coordinates(self):
        form = self._row_list_dialog_form()
        form.list_region.get.return_value = "-320,-180,160,78"
        form.left_region.get.return_value = "-320,-180,70,26"
        form.right_region.get.return_value = "-240,-180,80,26"
        form.click_region.get.return_value = "-230,-180,60,26"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        self.assertEqual(form.result["list_region"], [-320, -180, 160, 78])
        self.assertEqual(form.result["left_region"], [0, 0, 70, 26])
        self.assertEqual(form.result["right_region"], [80, 0, 80, 26])
        self.assertEqual(form.result["click_region"], [90, 0, 60, 26])

    def test_row_list_coordinate_parser_rejects_empty_components_and_accepts_signed_origin(self):
        with self.assertRaises(ValueError):
            dialog_module.RowListConditionClickDialog._parse_region(
                "100,,200,160,340", "列表区域",
            )
        self.assertEqual(
            dialog_module.RowListConditionClickDialog._parse_region(
                "-320,+180,160,340", "列表区域",
            ),
            [-320, 180, 160, 340],
        )

    def test_row_list_dialog_rejects_click_region_taller_than_list(self):
        form = self._row_list_dialog_form()
        form.click_region.get.return_value = "190,200,60,341"

        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()

        notice.assert_called_once()
        self.assertIsNone(form.result)
        form.destroy.assert_not_called()

    def test_row_list_dialog_saves_image_module_reference_for_row_local_recognition(self):
        form = self._row_list_dialog_form()
        form.left_condition_type.get.return_value = "image"
        form.left_module_key.get.return_value = "module:left"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        self.assertEqual(form.result["left_condition"], {
            "type": "image", "module_ref": True, "module_key": "module:left",
        })

    def test_row_list_dialog_rejects_invalid_values_without_setting_result(self):
        cases = [
            ("subregion_outside_list", lambda form: setattr(form.left_region.get, "return_value", "100,200,161,26")),
            ("empty_number_separator", lambda form: setattr(form.left_separator.get, "return_value", "")),
            ("missing_image_module", lambda form: (
                setattr(form.left_condition_type.get, "return_value", "image"),
                setattr(form.left_module_key.get, "return_value", ""),
            )),
            ("negative_retry_interval", lambda form: setattr(form.retry_interval.get, "return_value", "-1")),
        ]
        for name, invalidate in cases:
            with self.subTest(name=name):
                form = self._row_list_dialog_form()
                invalidate(form)
                with package_patch('dialogs', 'show_floating_notice') as notice:
                    form.save()
                notice.assert_called_once()
                self.assertIsNone(form.result)
                form.destroy.assert_not_called()

    def test_multi_condition_click_dialog_has_scrollable_form(self):
        form_class = getattr(dialog_module, "MultiConditionClickDialog", None)
        self.assertIsNotNone(form_class, "缺少固定三条件多条件识图点击配置窗口")
        init_source = inspect.getsource(form_class.__init__)
        scroll_source = inspect.getsource(form_class._scroll_form)
        self.assertIn("tk.Canvas", init_source)
        self.assertIn("ttk.Scrollbar", init_source)
        self.assertIn("scrollregion", init_source)
        self.assertIn("create_window", init_source)
        self.assertIn("yview_scroll", scroll_source)

    def test_row_list_condition_click_dialog_has_scrollable_form(self):
        form_class = getattr(dialog_module, "RowListConditionClickDialog", None)
        self.assertIsNotNone(form_class, "缺少列表逐行条件点击配置窗口")
        init_source = inspect.getsource(form_class.__init__)
        self.assertIn("tk.Canvas", init_source)
        self.assertIn("ttk.Scrollbar", init_source)
        self.assertIn("scrollregion", init_source)
        self.assertIn("create_window", init_source)
        scroll_form = getattr(form_class, "_scroll_form", None)
        self.assertIsNotNone(scroll_form, "列表逐行条件点击窗口缺少滚轮处理")
        self.assertIn("yview_scroll", inspect.getsource(scroll_form))

    def test_extract_ocr_integer_sorts_boxes_left_to_right(self):
        value, digits = extract_ocr_integer("721", [
            {"text": "7", "x": 130},
            {"text": "1", "x": 10},
            {"text": "2", "x": 70},
        ])
        self.assertEqual((value, digits), (127, "127"))

    def test_extract_ocr_integer_normalizes_full_width_and_leading_zeroes(self):
        value, digits = extract_ocr_integer("unused", [
            {"text": "００", "x": 10}, {"text": "７", "x": 40},
        ])
        self.assertEqual((value, digits), (7, "007"))

    def test_extract_ocr_integer_returns_none_without_digits(self):
        self.assertEqual(extract_ocr_integer("体力", [{"text": "ABC", "x": 1}]), (None, ""))

    def test_ocr_observation_shows_non_target_text_and_match_state(self):
        self.assertEqual(
            format_ocr_observation("可锁取", "可领取", False, "奖励可领取"),
            "奖励可领取 OCR：识别到「可锁取」；期望「可领取」· 未命中",
        )

    def test_recognize_image_with_boxes_returns_absolute_text_coordinates(self):
        engine = Mock()
        engine.predict.return_value = [{
            "rec_texts": ["可领取"],
            "rec_scores": [0.98],
            "rec_polys": [np.array([[5, 4], [45, 4], [45, 24], [5, 24]])],
        }]
        with patch("macroflow.core.ocr._get_engine", return_value=engine):
            text, matches = recognize_image_with_boxes(
                np.zeros((30, 50, 3), dtype=np.uint8), (100, 200),
            )
        self.assertEqual(text, "可领取")
        self.assertEqual(
            (matches[0]["x"], matches[0]["y"],
             matches[0]["center_x"], matches[0]["center_y"]),
            (105, 204, 125, 214),
        )
        self.assertIs(find_expected_match(matches, "可领取", "contains"), matches[0])
    """识别文字：匹配逻辑 + 对话框保存。"""

    def test_matches_expected_contains(self):
        self.assertTrue(matches_expected("体力不足，请补充", "体力不足", "contains"))
        self.assertTrue(matches_expected("背包已满", "背包", "contains"))
        self.assertTrue(matches_expected("ABC", "abc", "contains"))  # 忽略大小写
        self.assertFalse(matches_expected("体力不足", "背包", "contains"))

    def test_matches_expected_equals_strips_and_folds_case(self):
        self.assertTrue(matches_expected("  背包已满  ", "背包已满", "equals"))
        self.assertTrue(matches_expected("MacroFlow", "macroflow", "equals"))
        self.assertFalse(matches_expected("背包已满", "背包已满！", "equals"))
        self.assertFalse(matches_expected("背包已满！", "背包已满", "equals"))

    def test_matches_expected_empty_expected_requires_any_text(self):
        self.assertTrue(matches_expected("任何文字", "", "contains"))
        self.assertTrue(matches_expected("任何文字", "  ", "equals"))
        self.assertFalse(matches_expected("", "", "contains"))
        self.assertFalse(matches_expected("  ", ""))

    def test_matches_expected_default_mode_is_contains(self):
        self.assertTrue(matches_expected("确认购买？", "确认"))
        self.assertFalse(matches_expected("取消", "确认"))

    def test_ocr_dialog_save_builds_action_dict(self):
        form = OcrActionDialog.__new__(OcrActionDialog)
        form.master = Mock()
        form.jump_target_ids = {"第 1 行 · 延时": "aid1", "第 2 行 · 点击": "aid2"}
        form.region_mode = Mock()
        form.region_mode.get.return_value = "自定义区域"
        form.region = Mock()
        form.region.get.return_value = "10,20,300,400"
        form.expected_text = Mock()
        form.expected_text.get.return_value = "体力不足"
        form.match_mode = Mock()
        form.match_mode.get.return_value = "包含"
        form.timeout = Mock()
        form.timeout.get.return_value = "3000"
        form.interval = Mock()
        form.interval.get.return_value = "500"
        form.on_found = Mock()
        form.on_found.get.return_value = "跳转到目标动作"
        form.found_jump_target = Mock()
        form.found_jump_target.get.return_value = "第 1 行 · 延时"
        form.found_delay = Mock()
        form.found_delay.get.return_value = "0"
        form.on_timeout = Mock()
        form.on_timeout.get.return_value = "继续执行"
        form.timeout_delay = Mock()
        form.timeout_delay.get.return_value = "0"
        form.timeout_jump_target = Mock()
        form.timeout_jump_target.get.return_value = "第 2 行 · 点击"
        form.show_result_notice = Mock()
        form.show_result_notice.get.return_value = True
        form.destroy = Mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_not_called()
        form.destroy.assert_called_once()
        self.assertEqual(form.result["type"], "text_ocr")
        self.assertEqual(form.result["region_mode"], "custom")
        self.assertEqual(form.result["region"], [10, 20, 300, 400])
        self.assertEqual(form.result["expected_text"], "体力不足")
        self.assertEqual(form.result["match_mode"], "contains")
        self.assertEqual(form.result["timeout_ms"], 3000)
        self.assertEqual(form.result["interval_ms"], 500)
        self.assertEqual(form.result["on_found"], "jump")
        self.assertEqual(form.result["found_jump_action_id"], "aid1")
        self.assertEqual(form.result["on_timeout"], "continue")
        self.assertEqual(form.result["timeout_jump_action_id"], "aid2")
        self.assertTrue(form.result["show_result_notice"])

    def test_ocr_dialog_save_rejects_jump_without_target(self):
        form = OcrActionDialog.__new__(OcrActionDialog)
        form.master = Mock()
        form.jump_target_ids = {}
        form.region_mode = Mock()
        form.region_mode.get.return_value = "全屏"
        form.region = Mock()
        form.region.get.return_value = ""
        form.expected_text = Mock()
        form.expected_text.get.return_value = ""
        form.match_mode = Mock()
        form.match_mode.get.return_value = "包含"
        form.timeout = Mock()
        form.timeout.get.return_value = "3000"
        form.interval = Mock()
        form.interval.get.return_value = "500"
        form.on_found = Mock()
        form.on_found.get.return_value = "跳转到目标动作"
        form.found_jump_target = Mock()
        form.found_jump_target.get.return_value = "不存在"
        form.found_delay = Mock()
        form.found_delay.get.return_value = "0"
        form.on_timeout = Mock()
        form.on_timeout.get.return_value = "继续执行"
        form.timeout_delay = Mock()
        form.timeout_delay.get.return_value = "0"
        form.timeout_jump_target = Mock()
        form.timeout_jump_target.get.return_value = "不存在"
        form.show_result_notice = Mock()
        form.show_result_notice.get.return_value = False
        form.result = None
        form.destroy = Mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        form.destroy.assert_not_called()
        self.assertIsNone(form.result)

    def test_ocr_dialog_save_rejects_bad_region(self):
        form = OcrActionDialog.__new__(OcrActionDialog)
        form.master = Mock()
        form.jump_target_ids = {}
        form.region_mode = Mock()
        form.region_mode.get.return_value = "全屏"
        form.region = Mock()
        form.region.get.return_value = "1,2,3"
        form.expected_text = Mock()
        form.expected_text.get.return_value = ""
        form.match_mode = Mock()
        form.match_mode.get.return_value = "包含"
        form.timeout = Mock()
        form.timeout.get.return_value = "3000"
        form.interval = Mock()
        form.interval.get.return_value = "500"
        form.on_found = Mock()
        form.on_found.get.return_value = "继续执行"
        form.found_jump_target = Mock()
        form.found_jump_target.get.return_value = ""
        form.found_delay = Mock()
        form.found_delay.get.return_value = "0"
        form.on_timeout = Mock()
        form.on_timeout.get.return_value = "继续执行"
        form.timeout_delay = Mock()
        form.timeout_delay.get.return_value = "0"
        form.timeout_jump_target = Mock()
        form.timeout_jump_target.get.return_value = ""
        form.show_result_notice = Mock()
        form.show_result_notice.get.return_value = False
        form.destroy = Mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        form.destroy.assert_not_called()


class DetectOverlayTests(unittest.TestCase):
    def test_overlay_window_is_excluded_from_screen_capture(self):
        import macroflow.ui.detect_overlay as overlay_module

        fake_user32 = Mock()
        fake_user32.CreateWindowExW.return_value = 123
        fake_user32.GetMessageW.return_value = 0
        previous_hwnd = overlay_module._window_hwnd
        was_ready = overlay_module._ready.is_set()
        overlay_module._window_hwnd = None
        overlay_module._ready.clear()
        try:
            with patch.object(overlay_module, "_user32", fake_user32):
                overlay_module._window_loop()
        finally:
            overlay_module._window_hwnd = previous_hwnd
            if was_ready:
                overlay_module._ready.set()
            else:
                overlay_module._ready.clear()

        fake_user32.SetWindowDisplayAffinity.assert_called_once_with(
            123, 0x00000011,
        )

    def test_countdown_label_is_forwarded_to_the_overlay_window(self):
        import macroflow.ui.detect_overlay as overlay_module

        with patch.object(overlay_module, "_ensure_window", return_value=123), \
             patch.object(overlay_module._user32, "PostMessageW") as post_message:
            overlay_module.show_overlay(50, 60, 100, 80, label="5s")

        self.assertEqual(overlay_module._pending[-1], "5s")
        post_message.assert_called_once_with(
            123, overlay_module.WM_OVERLAY_SHOW, 0, 0,
        )

    def test_countdown_label_does_not_widen_the_match_frame(self):
        import macroflow.ui.detect_overlay as overlay_module

        fake_user32 = Mock()
        fake_user32.GetDpiForSystem.return_value = 96
        fake_user32.GetClientRect.side_effect = lambda _hwnd, pointer: (
            setattr(pointer._obj, "right", 30),
            setattr(pointer._obj, "bottom", 34),
        )
        fake_gdi = Mock()
        previous = overlay_module._pending
        overlay_module._pending = (50, 60, 10, 10, 0xFF, 900, "5s")
        try:
            with patch.object(overlay_module, "_user32", fake_user32), \
                 patch.object(overlay_module, "_gdi32", fake_gdi):
                overlay_module._wnd_proc(1, overlay_module.WM_PAINT, 0, 0)
        finally:
            overlay_module._pending = previous

        # 10 px target + 2 px border on both sides: the label may be wider,
        # but the red frame must still end at x=13 instead of using the label width.
        self.assertEqual(fake_gdi.Rectangle.call_args.args[3], 13)

    def test_show_and_hide_overlay_creates_window(self):
        from macroflow.ui.detect_overlay import hide_overlay, show_overlay
        show_overlay(50, 60, 100, 80)
        show_overlay(60, 70, 120, 90, duration_ms=80)  # 重复调用刷新位置
        hide_overlay()
        show_overlay(70, 80, 10, 10)
        hide_overlay()

    def test_show_overlay_ignores_empty_region(self):
        from macroflow.ui.detect_overlay import hide_overlay, show_overlay
        show_overlay(0, 0, 0, 0)
        show_overlay(10, 10, -5, 5)
        hide_overlay()

    def test_highlight_is_a_hollow_frame_not_a_white_block(self):
        # 回归：Rectangle 会用 DC 当前画刷填充框内。不选空画刷时 GDI 拿默认的白色
        # 画刷把识别到的目标整个填成白块（键色只抠洋红，白块不会被抠掉），表现就是
        # "识别到之后白块挡住目标"。这里断言：画框前先选入空画刷，画完按原样还原。
        import macroflow.ui.detect_overlay as overlay_module

        fake_user32 = Mock()
        fake_user32.GetDpiForSystem.return_value = 96
        fake_gdi = Mock()
        old_pen, old_brush = object(), object()
        fake_gdi.SelectObject.side_effect = [old_pen, old_brush, None, None]
        with patch.object(overlay_module, "_user32", fake_user32), \
             patch.object(overlay_module, "_gdi32", fake_gdi):
            overlay_module._wnd_proc(1, overlay_module.WM_PAINT, 0, 0)

        names = [name for name, _args, _kwargs in fake_gdi.mock_calls]
        self.assertIn("Rectangle", names)
        self.assertLess(names.index("GetStockObject"), names.index("Rectangle"))
        self.assertEqual(
            fake_gdi.GetStockObject.call_args.args[0], overlay_module.NULL_BRUSH,
        )
        # 背景仍先填键色（透明）；选入顺序 = 画笔、空画刷，还原顺序 = 原画刷、原画笔。
        self.assertEqual(fake_user32.FillRect.call_count, 1)
        selected = [call.args[1] for call in fake_gdi.SelectObject.call_args_list]
        self.assertEqual(len(selected), 4)
        self.assertIs(selected[1], fake_gdi.GetStockObject.return_value)
        self.assertIs(selected[2], old_brush)
        self.assertIs(selected[3], old_pen)

if __name__ == '__main__':
    unittest.main()
