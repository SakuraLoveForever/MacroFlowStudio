"""模块对象与模板区域登记。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_module_objects.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect
import json
import numpy as np
import os
import tempfile
import tkinter as tk
import tkinter.font as tkfont
import unittest
from unittest.mock import Mock, call, patch
from macroflow.core.models import ACTION_ID_KEY, MacroScript
from macroflow.core.storage import display_path, load_module_objects, load_script, load_template_regions, registered_template_region, save_module_objects, save_script, save_template_regions, script_category_for_path
from macroflow.input.wininput import WindowInfo
from macroflow.ui.app.base import px
from macroflow.ui.app.constants import FONT_FAMILY, FONT_SUBTITLE
from macroflow.ui.app.main import MacroFlowApp
import macroflow.ui.dialogs as dialog_module
from macroflow.ui.dialogs.actions import ClickDialog, RepeatClickDialog, edit_action
from macroflow.ui.dialogs.app_dialogs import RestartWorkflowTargetDialog
from macroflow.ui.dialogs.base import ModalDialog, fit_window_to_content
from macroflow.ui.dialogs.helpers import configure_module_tree_styles, fallback_template_options, module_manager_label, module_manager_selection_colors, module_manager_special_action_summary, module_manager_tag, pinyin_sort_key, registered_template_options, restart_workflow_row_options, segment_action_is_blocking, segment_row_label
from macroflow.ui.dialogs.module_objects import BatchModuleScriptDialog, ModulePickerDialog, ModuleReferenceDelayDialog, TemplateRegionFormDialog, TemplateRegionManagerDialog, prepend_module_to_scripts, remove_module_from_scripts
from macroflow.ui.dialogs.recognition import GlobalDetectDialog
from macroflow.ui.dialogs.segments import module_action_for_key
from tests.helpers.patches import package_patch


class TemplateRegionTests(unittest.TestCase):
    def test_image_inventory_is_a_separate_dialog_not_a_manager_tab(self):
        self.assertEqual(
            TemplateRegionManagerDialog.TAB_KEYS,
            ("all", "switch", "workflow_global", "script_global", "special"),
        )
        self.assertEqual(
            dialog_module.ModuleImageInventoryDialog._category_label("script_global"),
            "脚本全局",
        )
        self.assertEqual(
            dialog_module.ModuleImageInventoryDialog._category_label("special"),
            "特殊",
        )

    def test_module_picker_defaults_to_all_module_categories(self):
        self.assertEqual(
            ModulePickerDialog._allowed_categories(),
            ("switch", "workflow_global", "script_global", "special"),
        )

    def test_live_module_binding_refreshes_saved_action_for_edit(self):
        stale = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:first", "template": "images/stale.png",
            "region_mode": "template", "region": [1, 2, 3, 4],
        }
        with package_patch('dialogs', 'registered_module_object', return_value={'category': 'switch', 'template': 'images/current.png', 'region': [11, 22, 333, 444]}):
            refreshed = dialog_module.action_with_live_module_binding(stale)

        self.assertEqual(refreshed["template"], "images/current.png")
        self.assertEqual(refreshed["region"], [11, 22, 333, 444])
        self.assertEqual(refreshed["module_key"], "module:first")
        self.assertEqual(stale["template"], "images/stale.png")

    def test_module_manager_label_marks_blocking_module(self):
        self.assertEqual(
            module_manager_label(
                "module:blocking", {"name": "退出队伍", "blocking": True},
            ),
            "【阻塞识别】退出队伍",
        )
        self.assertEqual(
            module_manager_label(
                "module:normal", {"name": "结算确定", "blocking": False},
            ),
            "结算确定",
        )

    def test_module_manager_label_and_tag_mark_disabled_module(self):
        obj = {"name": "退出队伍", "blocking": True, "enabled": False}
        self.assertEqual(
            module_manager_label("module:disabled", obj),
            "【阻塞识别】【已禁用】退出队伍",
        )
        self.assertEqual(module_manager_tag(obj), "disabled")

    def test_module_manager_selection_colors_distinguish_enabled_and_disabled(self):
        self.assertEqual(
            module_manager_selection_colors({"enabled": True}),
            ("#FFFFFF", "#1F6B45"),
        )
        self.assertEqual(
            module_manager_selection_colors({"enabled": False}),
            ("#FFFFFF", "#7A3434"),
        )
        self.assertEqual(
            module_manager_selection_colors({
                "enabled": True,
                "run_code_on_timeout": True,
                "on_timeout_actions": [{"type": "end_current_script"}],
            }),
            ("#FFFFFF", "#713C78"),
        )

    def test_module_manager_marks_named_special_actions_in_both_segments(self):
        obj = {
            "name": "结算检测", "enabled": True,
            "run_code_after_action": True,
            "run_code_on_timeout": True,
            "on_success_actions": [
                {"type": "restart_workflow"}, {"type": "delay", "ms": 10},
            ],
            "on_timeout_actions": [
                {"type": "end_current_script"},
                {"type": "jump_current_script_last"},
            ],
        }
        self.assertEqual(
            module_manager_special_action_summary(obj),
            "附加：重新执行工作流；超时：结束当前最里层脚本，继续执行、跳转到当前脚本最后一行",
        )
        self.assertEqual(
            module_manager_label("module:special-code", obj),
            "【特殊代码段】结算检测",
        )
        self.assertEqual(module_manager_tag(obj), "special_action")

    def test_module_manager_hides_special_actions_when_segments_are_not_enabled(self):
        obj = {
            "name": "未启用代码段",
            "run_code_after_action": False,
            "run_code_on_timeout": False,
            "on_success_actions": [{"type": "restart_workflow"}],
            "on_timeout_actions": [{"type": "end_current_script"}],
        }
        self.assertEqual(module_manager_special_action_summary(obj), "")
        self.assertEqual(module_manager_label("module:off", obj), "未启用代码段")
        self.assertEqual(module_manager_tag(obj), "")

    def test_manager_selection_highlight_updates_tree_style(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        tree = Mock()
        dialog.current = "all"
        dialog.trees = {"all": tree}

        dialog._update_selection_highlight({"enabled": False})
        tree.configure.assert_called_once_with(style="ModuleManagerDisabled.Treeview")

        tree.reset_mock()
        dialog._update_selection_highlight({"enabled": True})
        tree.configure.assert_called_once_with(style="ModuleManagerEnabled.Treeview")

        tree.reset_mock()
        dialog._update_selection_highlight({
            "enabled": True,
            "run_code_after_action": True,
            "on_success_actions": [{"type": "restart_workflow"}],
        })
        tree.configure.assert_called_once_with(style="ModuleManagerSpecial.Treeview")

    def test_module_manager_styles_copy_layout_and_keep_dark_readable_colors(self):
        # 行高现在按字体行高算，取字体度量需要一个真实（隐藏的）Tk root。
        root = tk.Tk()
        root.withdraw()
        try:
            style = Mock()
            style.layout.return_value = [("Treeview.treearea", {"sticky": "nswe"})]

            configure_module_tree_styles(style)

            self.assertEqual(style.layout.call_count, 5)
            configured = {item.args[0]: item.kwargs for item in style.configure.call_args_list}
            self.assertEqual(
                set(configured),
                {
                    "ModuleManagerNeutral.Treeview",
                    "ModuleManagerEnabled.Treeview",
                    "ModuleManagerSpecial.Treeview",
                    "ModuleManagerDisabled.Treeview",
                },
            )
            linespace = tkfont.Font(family=FONT_FAMILY, size=FONT_SUBTITLE).metrics("linespace")
            for options in configured.values():
                self.assertEqual(options["background"], "#182129")
                self.assertEqual(options["fieldbackground"], "#182129")
                self.assertEqual(options["foreground"], "#E8EDF2")
                # 行高必须贴着字体行高：写死 42 时每行比文字高出一倍多，
                # 10 行表格凭空多出两百多像素空白，窗口也被一并撑大。
                self.assertEqual(options["rowheight"], linespace + px(8))
        finally:
            root.destroy()

    def test_manager_tree_tags_blocking_modules_in_all_and_category_tabs(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {
            "module:blocking": {
                "name": "退出队伍", "category": "workflow_global",
                "blocking": True, "region": [1, 2, 3, 4],
            },
            "module:normal": {
                "name": "结算确定", "category": "workflow_global",
                "blocking": False, "region": [0, 0, 0, 0],
            },
            "module:special-code": {
                "name": "结算退出", "category": "workflow_global",
                "blocking": False, "region": [0, 0, 0, 0],
                "run_code_after_action": True,
                "run_code_on_timeout": True,
                "on_success_actions": [{"type": "restart_workflow"}],
                "on_timeout_actions": [{"type": "end_current_script"}],
            },
        }
        dialog.sort_direction = "asc"
        for tab_key in ("all", "workflow_global"):
            tree = Mock()
            tree.get_children.return_value = ()
            dialog._reload_tree(tab_key, tree)
            inserted = {item.kwargs["iid"]: item.kwargs for item in tree.insert.call_args_list}
            self.assertEqual(inserted["module:blocking"]["text"], "【阻塞识别】退出队伍")
            self.assertEqual(inserted["module:blocking"]["tags"], ("blocking",))
            self.assertEqual(inserted["module:normal"]["text"], "结算确定")
            self.assertEqual(inserted["module:normal"]["tags"], ())
            self.assertEqual(
                inserted["module:special-code"]["text"],
                "【特殊代码段】结算退出",
            )
            self.assertEqual(
                inserted["module:special-code"]["values"],
                (
                    "未设置区域（全屏）",
                    "附加：重新执行工作流；超时：结束当前最里层脚本，继续执行",
                ),
            )
            self.assertEqual(
                inserted["module:special-code"]["tags"], ("special_action",),
            )

    def test_manager_toggle_selected_enabled_persists_and_reselects(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        obj = {"name": "退出队伍", "category": "workflow_global", "enabled": True}
        dialog.objects = {"module:item": obj}
        dialog.current = "workflow_global"
        tree = Mock()
        tree.selection.return_value = ("module:item",)
        dialog.trees = {"workflow_global": tree}
        dialog._update_action_buttons = Mock()
        with package_patch('dialogs', 'save_module_objects') as save, \
             patch.object(TemplateRegionManagerDialog, "_reload_trees") as reload_trees:
            dialog._toggle_selected_enabled()
        self.assertFalse(obj["enabled"])
        save.assert_called_once_with(dialog.objects)
        reload_trees.assert_called_once_with()
        tree.selection_set.assert_called_once_with("module:item")
        tree.see.assert_called_once_with("module:item")
        dialog._update_action_buttons.assert_called_once_with()

    def test_pinyin_sort_key_orders_chinese_names(self):
        names = ["张三", "阿明", "李四", "白云"]
        self.assertEqual(
            sorted(names, key=pinyin_sort_key),
            ["阿明", "白云", "李四", "张三"],
        )

    def test_manager_sort_direction_reloads_all_trees(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.sort_direction = "asc"
        tree = Mock()
        dialog.trees = {"all": tree}
        dialog._reload_trees = Mock()
        dialog._set_sort_direction("desc")
        self.assertEqual(dialog.sort_direction, "desc")
        dialog._reload_trees.assert_called_once_with()
        heading = tree.heading.call_args
        self.assertEqual(heading.args[0], "#0")
        self.assertEqual(heading.kwargs["text"], "模块名称 ↓")

    def test_clicking_sort_heading_toggles_direction(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.sort_direction = "asc"
        dialog._set_sort_direction = Mock()
        dialog._toggle_sort_direction()
        dialog._set_sort_direction.assert_called_once_with("desc")

    def test_inventory_filter_switches_visible_group_and_reloads(self):
        dialog = dialog_module.ModuleImageInventoryDialog.__new__(
            dialog_module.ModuleImageInventoryDialog,
        )
        dialog.inventory_filter = "all"
        dialog.inventory_filter_buttons = {
            "all": Mock(), "adopted": Mock(), "unused": Mock(),
        }
        dialog._reload_tree = Mock()
        dialog._set_inventory_filter("unused")
        self.assertEqual(dialog.inventory_filter, "unused")
        dialog._reload_tree.assert_called_once_with()
        self.assertEqual(
            dialog.inventory_filter_buttons["unused"].configure.call_args.kwargs["background"],
            "#244D78",
        )

    def test_template_regions_storage_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                save_template_regions({"images/a.png": [10, 20, 300, 400]})
                loaded = load_template_regions()
            self.assertEqual(loaded, {"images/a.png": [10, 20, 300, 400]})

    def test_module_enabled_state_roundtrips_and_defaults_to_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                save_module_objects({
                    "module:disabled": {
                        "name": "停用模块", "category": "switch", "enabled": False,
                    },
                    "module:default": {
                        "name": "默认模块", "category": "switch",
                    },
                })
                loaded = load_module_objects()
            self.assertFalse(loaded["module:disabled"]["enabled"])
            self.assertTrue(loaded["module:default"]["enabled"])
            self.assertEqual(loaded["module:default"]["not_found_timeout_ms"], 5000)

    def test_text_absent_wait_state_roundtrips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                save_module_objects({
                    "module:text": {
                        "name": "等待加载结束", "category": "switch",
                        "recognize": "text", "expected_text": "加载中",
                        "wait_text_absent": True,
                        "ocr_offset_up": 6, "ocr_offset_down": 7,
                        "ocr_offset_left": 8, "ocr_offset_right": 9,
                    },
                })
                loaded = load_module_objects()
            self.assertTrue(loaded["module:text"]["wait_text_absent"])
            self.assertEqual(loaded["module:text"]["ocr_offset_up"], 6)
            self.assertEqual(loaded["module:text"]["ocr_offset_down"], 7)
            self.assertEqual(loaded["module:text"]["ocr_offset_left"], 8)
            self.assertEqual(loaded["module:text"]["ocr_offset_right"], 9)

    def test_number_module_roundtrips_as_read_only_switch_module(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                save_module_objects({
                    "module:number": {
                        "name": "剩余次数", "category": "workflow_global",
                        "recognize": "number", "template": "images/old.png",
                        "region": [10, 20, 80, 30], "after_action": "click_match",
                        "run_code_after_action": True,
                        "on_success_actions": [{"type": "click"}],
                    },
                })
                loaded = load_module_objects()["module:number"]
                template_regions = load_template_regions()
        self.assertEqual(loaded["category"], "switch")
        self.assertEqual(loaded["recognize"], "number")
        self.assertEqual(loaded["template"], "")
        self.assertEqual(loaded["region"], [10, 20, 80, 30])
        self.assertEqual(loaded["after_action"], "continue")
        self.assertFalse(loaded["run_code_after_action"])
        self.assertEqual(loaded["on_success_actions"], [])
        self.assertNotIn("", template_regions)

    def test_template_regions_filters_invalid_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            path.write_text(json.dumps({
                "images/good.png": [1, 2, 3, 4],
                "images/unset.png": [0, 0, 0, 0],
                "images/short.png": [1, 2, 3],
                "images/typed.png": ["x", "y", "w", "h"],
                "images/negative.png": [1, 2, -3, 4],
                "": [1, 2, 3, 4],
            }), encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                loaded = load_template_regions()
            # 未设置区域（全 0）的占位条目合法保留，格式错误的丢弃。
            self.assertEqual(loaded, {
                "images/good.png": [1, 2, 3, 4],
                "images/unset.png": [0, 0, 0, 0],
            })

    def test_unset_template_region_placeholder_survives_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                save_template_regions({"images/new.png": [0, 0, 0, 0]})
                loaded = load_template_regions()
            self.assertEqual(loaded, {"images/new.png": [0, 0, 0, 0]})

    def test_module_objects_migrate_special_detection_back_to_global(self):
        # 1.81 曾把旧全局模块并入特殊；1.82 起特殊分类只放纯动作，检测型条目
        # （有图 / 未标纯动作）加载时惰性迁回「全局模块」，字段全保留；纯动作不动。
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            path.write_text(json.dumps({
                "images/g.png": {"category": "global", "region": [10, 20, 300, 400]},
                "images/s2.png": {"category": "special", "region": [1, 2, 300, 400],
                                  "hold_ms": 2000, "blocking": True},
                "重新执行工作流": {"category": "special", "name": "重新执行工作流",
                              "pure_action": True},
            }), encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                objects = load_module_objects()
            # 旧 global 类别迁移为工作流全局。
            self.assertEqual(objects["images/g.png"]["category"], "workflow_global")
            self.assertEqual(objects["images/g.png"]["region"], [10, 20, 300, 400])
            # 检测型特殊（有图）→ 全局，字段全保留。
            obj = objects["images/s2.png"]
            self.assertEqual(obj["category"], "workflow_global")
            self.assertEqual(obj["region"], [1, 2, 300, 400])
            self.assertEqual(obj["hold_ms"], 2000)
            self.assertTrue(obj["blocking"])
            self.assertFalse(obj.get("pure_action"))
            # 纯动作特殊保持特殊。
            self.assertEqual(objects["重新执行工作流"]["category"], "special")
            self.assertTrue(objects["重新执行工作流"].get("pure_action"))

    def test_module_objects_migrate_legacy_run_actions_to_optional_post_code(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            path.write_text(json.dumps({
                "images/legacy.png": {
                    "category": "switch", "region": [1, 2, 3, 4],
                    "after_action": "run_actions",
                    "on_success_actions": [{"type": "restart_workflow"}],
                },
            }), encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                obj = load_module_objects()["images/legacy.png"]
        self.assertEqual(obj["after_action"], "continue")
        self.assertTrue(obj["run_code_after_action"])
        self.assertEqual(obj["on_success_actions"][0]["type"], "restart_workflow")

    def test_module_objects_normalize_independent_timeout_segment(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            path.write_text(json.dumps({
                "images/timeout.png": {
                    "category": "switch", "region": [1, 2, 3, 4],
                    "run_code_on_timeout": True,
                    "not_found_timeout_ms": "4500",
                    "on_timeout_actions": [{"type": "delay", "ms": 10}],
                },
            }), encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                obj = load_module_objects()["images/timeout.png"]
        self.assertTrue(obj["run_code_on_timeout"])
        self.assertEqual(obj["not_found_timeout_ms"], 4500)
        self.assertEqual(obj["on_timeout_actions"][0]["type"], "delay")
        self.assertEqual(obj["on_success_actions"], [])

    def test_load_module_objects_seeds_default_pure_action_special(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                objects = load_module_objects()
            # 空仓库补种两个固定纯动作，且不落盘。
            self.assertIn("重新执行工作流", objects)
            self.assertIn("结束当前最里层脚本，继续执行", objects)
            self.assertTrue(objects["重新执行工作流"].get("pure_action"))
            self.assertFalse(path.exists())
            # 仓库已有用户纯动作时仍独立补齐固定条目。
            path.write_text(json.dumps({
                "自定特殊": {"category": "special", "name": "自定特殊", "pure_action": True},
            }), encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                loaded = load_module_objects()
            self.assertIn("重新执行工作流", loaded)
            self.assertIn("结束当前最里层脚本，继续执行", loaded)
            self.assertIn("自定特殊", loaded)

    def test_load_template_regions_skips_pure_action_specials(self):
        # 纯动作特殊模块（无图片）不进入模板下拉。
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            path.write_text(json.dumps({
                "images/s.png": {"category": "switch", "region": [1, 2, 3, 4]},
                "重新执行工作流": {"category": "special", "pure_action": True},
            }), encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                regions = load_template_regions()
            self.assertEqual(regions, {"images/s.png": [1, 2, 3, 4]})

    def test_load_template_regions_skips_no_recognition_modules(self):
        with patch("macroflow.core.storage.load_module_objects", return_value={
            "module:direct": {
                "recognize": "none", "template": "", "region": [0, 0, 0, 0],
            },
            "module:image": {
                "template": "images/a.png", "region": [1, 2, 3, 4],
            },
        }):
            self.assertEqual(load_template_regions(), {"images/a.png": [1, 2, 3, 4]})

    def test_registered_template_region_lookup(self):
        key = display_path("images/a.png")
        with patch("macroflow.core.storage.load_template_regions", return_value={key: [10, 20, 300, 400]}):
            self.assertEqual(registered_template_region("images/a.png"), [10, 20, 300, 400])
        with patch("macroflow.core.storage.load_template_regions", return_value={}):
            self.assertIsNone(registered_template_region("images/missing.png"))

    def test_registered_template_options_include_legacy_value(self):
        with package_patch('dialogs', 'load_template_regions', return_value={'images/b.png': [1, 2, 3, 4]}):
            self.assertEqual(registered_template_options(), ["images/b.png"])
            # 编辑旧动作：模板不在注册表时临时加回，保证下拉显示原值。
            self.assertEqual(
                registered_template_options("images/legacy.png"),
                ["images/legacy.png", "images/b.png"],
            )

    def test_fallback_template_options_has_disabled_first(self):
        with package_patch('dialogs', 'load_template_regions', return_value={'images/b.png': [1, 2, 3, 4]}):
            self.assertEqual(
                fallback_template_options("images/legacy.png"),
                ["（不启用）", "images/legacy.png", "images/b.png"],
            )
            self.assertEqual(fallback_template_options(""), ["（不启用）", "images/b.png"])

    def test_open_template_region_manager_shows_and_refreshes(self):
        # 打开管理器（show）后刷新模板下拉；管理器里已删除的模板被清空。
        with package_patch('dialogs', 'TemplateRegionManagerDialog') as manager_class, \
             package_patch('dialogs', 'load_template_regions', return_value={'images/g.png': [1, 2, 3, 4]}):
            dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
            dialog.template = Mock()
            dialog.template.get.return_value = "images/g.png"
            dialog.template_combo = Mock()
            dialog.open_template_region_manager()
        manager_class.assert_called_once_with(dialog)
        manager_class.return_value.show.assert_called_once()
        dialog.template_combo.configure.assert_called_once_with(values=["images/g.png"])

    def test_refresh_template_options_clears_removed_template(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/removed.png"
        dialog.template_combo = Mock()
        with package_patch('dialogs', 'load_template_regions', return_value={'images/g.png': [1, 2, 3, 4]}):
            dialog._refresh_template_options()
        dialog.template.set.assert_called_once_with("")
        dialog.template_combo.configure.assert_called_once_with(values=["images/g.png"])

    def test_editor_page_opens_unified_region_manager(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        with package_patch('app', 'TemplateRegionManagerDialog') as manager_class:
            app.open_template_region_manager()
        manager_class.assert_called_once_with(app.root)
        manager_class.return_value.show.assert_called_once()

    def _object(self, region=(10, 20, 300, 400), category="switch", **overrides):
        obj = {
            "category": category, "region": list(region), "threshold": 0.85,
            "interval_ms": 250, "blocking": False, "hold_ms": 1000,
            "delay_ms": 0, "after_action": "click_match", "click_point": [],
            "button": "left", "second_match_template": "",
            "second_match_region": [], "second_match_timeout_ms": 3000,
            "on_success_actions": [], "run_code_on_timeout": False,
            "not_found_timeout_ms": 5000, "on_timeout_actions": [],
        }
        obj.update(overrides)
        return obj

    def test_segment_blocking_module_has_visible_warning_marker(self):
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:blocking", "template": "images/wait.png",
        }
        with package_patch('dialogs', 'registered_module_object', return_value={'blocking': True}):
            self.assertTrue(segment_action_is_blocking(action))
            self.assertEqual(segment_row_label(action), "【阻塞等待】识图 wait")

    def test_block_action_has_visible_waiting_marker_in_segments(self):
        action = {"type": "block"}

        self.assertTrue(segment_action_is_blocking(action))
        self.assertEqual(segment_row_label(action), "【阻塞等待跳转】阻塞")

    def test_segment_text_absent_module_is_also_marked_blocking(self):
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:text", "template": "",
        }
        with package_patch('dialogs', 'registered_module_object', return_value={'blocking': False, 'recognize': 'text', 'wait_text_absent': True}):
            self.assertTrue(segment_action_is_blocking(action))

    def test_segment_nonblocking_module_has_no_warning_marker(self):
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:normal", "template": "images/next.png",
        }
        with package_patch('dialogs', 'registered_module_object', return_value={'blocking': False}):
            self.assertFalse(segment_action_is_blocking(action))
            self.assertEqual(segment_row_label(action), "识图 next")

    def test_segment_module_reference_displays_module_name_instead_of_internal_id(self):
        module = {"name": "登录确认", "blocking": False}

        with package_patch('dialogs', 'registered_module_object', return_value=module):
            self.assertEqual(segment_row_label({
                "type": "image_match", "module_ref": True,
                "module_key": "module:89971b22b5b44b639a76dc69465dc595",
                "template": "module:89971b22b5b44b639a76dc69465dc595",
            }), "识图 登录确认")
            self.assertEqual(segment_row_label({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:89971b22b5b44b639a76dc69465dc595",
                "template": "module:89971b22b5b44b639a76dc69465dc595",
            }), "全局检测 登录确认")

    def test_reload_segment_list_colors_every_blocking_module(self):
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        form.segment = [
            {"type": "image_match", "module_ref": True,
             "module_key": "module:block", "template": "images/block.png"},
            {"type": "image_match", "module_ref": True,
             "module_key": "module:normal", "template": "images/normal.png"},
        ]
        form.segment_listbox = Mock()

        def lookup(key):
            return {"blocking": key == "module:block"}

        with package_patch('dialogs', 'registered_module_object', side_effect=lookup):
            form._reload_segment_list()

        form.segment_listbox.itemconfigure.assert_called_once_with(
            0, foreground="#F2B84B",
        )

    def test_manager_reload_tree_shows_category_and_region(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        trees = {
            key: Mock() for key in (
                "all", "switch", "workflow_global", "script_global", "special",
            )
        }
        for tree in trees.values():
            tree.get_children.return_value = ["old"]
        dialog.trees = trees
        dialog.objects = {
            "images/a.png": self._object(),
            "images/b.png": self._object(region=(0, 0, 0, 0)),
            "images/g.png": self._object(category="workflow_global"),
            "images/sg.png": self._object(category="script_global"),
            "重新执行工作流": {
                "category": "special", "name": "重新执行工作流", "pure_action": True,
            },
        }
        dialog._reload_trees()
        for tree in trees.values():
            tree.delete.assert_called_once_with("old")
        # 全部页签：switch / global 显示区域，纯动作显示名称与区域 "—"。
        calls = trees["all"].insert.call_args_list
        by_iid = {call.kwargs["iid"]: call.kwargs for call in calls}
        self.assertEqual(by_iid["images/a.png"]["values"], ("10,20,300,400", "—"))
        self.assertEqual(by_iid["images/b.png"]["values"], ("未设置区域（全屏）", "—"))
        self.assertEqual(by_iid["images/g.png"]["values"], ("10,20,300,400", "—"))
        self.assertEqual(by_iid["重新执行工作流"]["text"], "重新执行工作流")
        self.assertEqual(by_iid["重新执行工作流"]["values"], ("—", "固定特殊模块"))
        # 切换 / 全局页签各列所属类别；特殊页签只列纯动作（名称 + 类型）。
        self.assertEqual(
            [call.kwargs["iid"] for call in trees["switch"].insert.call_args_list],
            ["images/a.png", "images/b.png"],
        )
        self.assertEqual(
            [call.kwargs["iid"] for call in trees["workflow_global"].insert.call_args_list],
            ["images/g.png"],
        )
        self.assertEqual(
            [call.kwargs["iid"] for call in trees["script_global"].insert.call_args_list],
            ["images/sg.png"],
        )
        special_calls = trees["special"].insert.call_args_list
        self.assertEqual(
            [call.kwargs["iid"] for call in special_calls], ["重新执行工作流"],
        )
        self.assertEqual(special_calls[0].kwargs["values"], ("特殊",))

    def _form(self, image="", region="", after_action="点击识别区域", recognize="模板图片"):
        """构造表单桩：__new__ 跳过 __init__，用 Mock 变量代替控件。"""
        form = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        form.old_key = ""
        form.segment_depth = 0
        form.images_dir = Path(tempfile.gettempdir())
        form.image_var = Mock()
        form.image_var.get.return_value = image
        form.region_var = Mock()
        form.region_var.get.return_value = region
        form.recognize_var = Mock()
        form.recognize_var.get.return_value = recognize
        form.expected_text_var = Mock()
        form.expected_text_var.get.return_value = ""
        form.match_mode_var = Mock()
        form.match_mode_var.get.return_value = "包含"
        form.wait_text_absent_var = Mock()
        form.wait_text_absent_var.get.return_value = False
        form.ocr_offset_up_var = Mock()
        form.ocr_offset_up_var.get.return_value = "0"
        form.ocr_offset_down_var = Mock()
        form.ocr_offset_down_var.get.return_value = "0"
        form.ocr_offset_left_var = Mock()
        form.ocr_offset_left_var.get.return_value = "0"
        form.ocr_offset_right_var = Mock()
        form.ocr_offset_right_var.get.return_value = "0"
        form.category_var = Mock()
        form.category_var.get.return_value = "切换模块"
        form.threshold_var = Mock()
        form.threshold_var.get.return_value = "0.85"
        form.ignore_background_var = Mock()
        form.ignore_background_var.get.return_value = False
        form.interval_var = Mock()
        form.interval_var.get.return_value = "1000"
        form.cooldown_var = Mock()
        form.cooldown_var.get.return_value = "2000"
        form.start_delay_var = Mock()
        form.start_delay_var.get.return_value = "0"
        form.fallback_module_key_var = Mock()
        form.fallback_module_key_var.get.return_value = ""
        form.fallback_click_var = Mock()
        form.fallback_click_var.get.return_value = False
        form.fallback_on_match_var = Mock()
        form.fallback_on_match_var.get.return_value = "继续识别主模块（不点击）"
        form.blocking_var = Mock()
        form.blocking_var.get.return_value = False
        form.hold_enabled_var = Mock()
        form.hold_enabled_var.get.return_value = False  # 持续延时默认不启用
        form.hold_var = Mock()
        form.hold_var.get.return_value = "1000"
        form.delay_var = Mock()
        form.delay_var.get.return_value = "0"
        form.after_action_var = Mock()
        form.after_action_var.get.return_value = after_action
        form.run_code_after_action_var = Mock()
        form.run_code_after_action_var.get.return_value = False
        form.run_code_on_timeout_var = Mock()
        form.run_code_on_timeout_var.get.return_value = False
        form.not_found_timeout_var = Mock()
        form.not_found_timeout_var.get.return_value = "5000"
        form.button_var = Mock()
        form.button_var.get.return_value = "left"
        form.click_count_var = Mock()
        form.click_count_var.get.return_value = "1"
        form.click_point_var = Mock()
        form.click_point_var.get.return_value = ""
        form.second_template_var = Mock()
        form.second_template_var.get.return_value = ""
        form.second_region_var = Mock()
        form.second_region_var.get.return_value = ""
        form.second_timeout_var = Mock()
        form.second_timeout_var.get.return_value = "3000"
        form.second_click_target_var = Mock()
        form.second_click_target_var.get.return_value = "第二次识别位置"
        form.second_click_region_var = Mock()
        form.second_click_region_var.get.return_value = ""
        form.segment = []
        form.timeout_segment = []
        form.name_var = Mock()
        form.name_var.get.return_value = ""
        form._toggle_sections = Mock()
        form.destroy = Mock()
        return form

    def test_form_capture_saves_image_and_fills_vars(self):
        # "截图新建…"：框选区域截图存为新模板图片，图片与区域两项一起填入。
        form = self._form()
        form.master = Mock()
        with package_patch('dialogs', 'ScreenRegionPicker') as picker_class:
            form._capture()
        on_result = picker_class.call_args[0][2]
        self.assertEqual(picker_class.return_value.start.call_count, 1)
        with tempfile.TemporaryDirectory() as folder:
            images_dir = Path(folder) / "images"
            form.images_dir = images_dir
            screen = np.zeros((40, 50, 3), dtype=np.uint8)
            with package_patch('dialogs', 'capture_bgr', return_value=(screen, (0, 0))):
                on_result([10, 20, 30, 40])
            saved = list(images_dir.glob("template_*.png"))
            self.assertEqual(len(saved), 1)
            key = str(saved[0])
        form.image_var.set.assert_called_once_with(key)
        form.region_var.set.assert_called_once_with("10,20,30,40")

    def test_form_capture_failure_shows_notice(self):
        form = self._form()
        form.master = Mock()
        with package_patch('dialogs', 'ScreenRegionPicker') as picker_class:
            form._capture()
        on_result = picker_class.call_args[0][2]
        with package_patch('dialogs', 'capture_bgr', side_effect=RuntimeError('boom')), \
             package_patch('dialogs', 'show_floating_notice') as notice:
            on_result([10, 20, 30, 40])
        notice.assert_called_once()
        self.assertIn("截图失败", notice.call_args.args[1])
        form.image_var.set.assert_not_called()
        form.region_var.set.assert_not_called()

    def test_form_choose_image_sets_image_var(self):
        form = self._form()
        chosen = r"C:\images\部分\勇士挑战确定.png"
        with patch("tkinter.filedialog.askopenfilename", return_value=chosen):
            form._choose_image()
        form.image_var.set.assert_called_once_with(chosen)
        form.name_var.set.assert_called_once_with("勇士挑战确定")

    def test_form_choose_image_preserves_custom_name(self):
        form = self._form()
        form.name_var.get.return_value = "手动名称"
        with patch("tkinter.filedialog.askopenfilename", return_value=r"C:\images\new.png"):
            form._choose_image()
        form.name_var.set.assert_not_called()

    def test_module_default_name_uses_final_path_stem(self):
        self.assertEqual(
            TemplateRegionFormDialog._default_name_for_image(
                r"images\部分\勇士挑战确定.png"
            ),
            "勇士挑战确定",
        )

    def test_form_pick_region_fills_region_var(self):
        form = self._form()
        form.master = Mock()
        with package_patch('dialogs', 'ScreenRegionPicker') as picker_class:
            form._pick_region()
        on_result = picker_class.call_args[0][2]
        on_result([1, 2, 3, 4])
        form.region_var.set.assert_called_once_with("1,2,3,4")

    def test_form_save_requires_image(self):
        form = self._form(image="", region="10,20,300,400")
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        self.assertIn("缺少模板图片", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_requires_region(self):
        form = self._form(image="images/g.png", region="")
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        self.assertIn("缺少框选区域", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_rejects_malformed_region(self):
        for region in ("1,2,3", "x,y,w,h", "1,2,-3,4"):
            form = self._form(image="images/g.png", region=region)
            with package_patch('dialogs', 'show_floating_notice') as notice:
                form.save()
            notice.assert_called_once()
            form.destroy.assert_not_called()

    def test_form_save_sets_result_and_closes(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[0], "")
        self.assertTrue(form.result[1].startswith("module:"))
        obj = form.result[2]
        self.assertEqual(obj["template"], "images/g.png")
        self.assertEqual(obj["category"], "switch")
        self.assertEqual(obj["region"], [10, 20, 300, 400])
        self.assertEqual(obj["after_action"], "click_match")
        self.assertFalse(obj["run_code_after_action"])
        self.assertEqual(obj["threshold"], 0.85)
        self.assertEqual(obj["interval_ms"], 1000)
        self.assertEqual(obj["cooldown_ms"], 2000)
        self.assertEqual(obj["start_delay_ms"], 0)
        self.assertEqual(obj["fallback_module_key"], "")
        self.assertFalse(obj["fallback_click"])
        self.assertFalse(obj["blocking"])
        self.assertFalse(obj["hold_enabled"])  # 持续延时默认不启用
        self.assertEqual(obj["hold_ms"], 1000)
        self.assertEqual(obj["delay_ms"], 0)
        self.assertEqual(obj["button"], "left")
        self.assertEqual(obj["click_point"], [])
        self.assertEqual(obj["second_match_template"], "")
        self.assertEqual(obj["second_match_region"], [])
        self.assertEqual(obj["second_match_timeout_ms"], 3000)
        self.assertEqual(obj["second_match_click_target"], "second")
        self.assertEqual(obj["second_match_click_region"], [])
        self.assertEqual(obj["on_success_actions"], [])
        self.assertFalse(obj["run_code_on_timeout"])
        self.assertEqual(obj["not_found_timeout_ms"], 5000)
        self.assertEqual(obj["on_timeout_actions"], [])
        form.destroy.assert_called_once()
        notice.assert_not_called()

    def test_form_can_disable_global_hold_delay(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.category_var.get.return_value = "工作流全局模块"
        form.hold_enabled_var.get.return_value = False

        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()

        self.assertFalse(form.result[2]["hold_enabled"])
        self.assertEqual(form.result[2]["hold_ms"], 1000)
        notice.assert_not_called()

    def test_new_module_detection_interval_defaults_to_one_second(self):
        # 以后新建的模块「检测间隔」默认 1 秒：新建表单与新建对象都读这个默认值。
        import macroflow.core.storage as storage_module
        import macroflow.ui.dialogs as dialogs_module

        self.assertEqual(storage_module.DEFAULT_MODULE_INTERVAL_MS, 1000)
        self.assertEqual(dialogs_module.DEFAULT_MODULE_INTERVAL_MS, 1000)
        self.assertIs(
            dialogs_module.DEFAULT_MODULE_INTERVAL_MS,
            storage_module.DEFAULT_MODULE_INTERVAL_MS,
            "表单默认值必须与模块仓库默认值同源，不能各写一份",
        )
        self.assertEqual(storage_module.DEFAULT_MODULE_OBJECT["interval_ms"], 1000)

    def test_new_module_trigger_cooldown_defaults_to_two_seconds(self):
        import macroflow.core.storage as storage_module
        import macroflow.ui.dialogs as dialogs_module

        self.assertEqual(storage_module.DEFAULT_MODULE_TRIGGER_COOLDOWN_MS, 2000)
        self.assertIs(
            dialogs_module.DEFAULT_MODULE_TRIGGER_COOLDOWN_MS,
            storage_module.DEFAULT_MODULE_TRIGGER_COOLDOWN_MS,
        )
        self.assertEqual(storage_module.DEFAULT_MODULE_OBJECT["cooldown_ms"], 2000)

    def test_form_saves_start_delay_for_any_module_category(self):
        for label in ("切换模块", "工作流全局模块", "脚本全局模块"):
            form = self._form(image="images/g.png", region="10,20,300,400")
            form.category_var.get.return_value = label
            form.start_delay_var.get.return_value = "125000"
            with package_patch('dialogs', 'show_floating_notice') as notice:
                form.save()
            self.assertEqual(form.result[2]["start_delay_ms"], 125000, label)
            notice.assert_not_called()

    def test_form_saves_per_module_trigger_cooldown(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.category_var.get.return_value = "工作流全局模块"
        form.cooldown_var.get.return_value = "3750"

        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()

        self.assertEqual(form.result[2]["cooldown_ms"], 3750)
        notice.assert_not_called()

    def test_toggle_sections_shows_start_delay_for_switch_module(self):
        form = self._form(recognize="模板图片")
        form.pure = False
        for attr in (
            "row_name", "row_image", "row_region", "detect_section_heading",
            "row_recognize", "row_expected_text", "row_match_mode", "row_threshold",
            "row_wait_text_absent", "row_ignore_background", "row_interval", "row_cooldown", "row_start_delay",
            "row_fallback_module", "row_fallback_click",
            "row_blocking", "row_delay", "action_section_heading", "row_after",
            "row_hold", "row_button", "row_click_count", "row_ocr_offset", "row_click_point",
            "row_second_template", "row_second_timeout", "row_second_click_target",
            "row_second_click_region", "segment_section_heading",
            "row_run_code_after_action", "segment_frame", "timeout_section_heading",
            "row_run_code_on_timeout", "row_not_found_timeout", "timeout_segment_frame",
        ):
            setattr(form, attr, Mock())
        form.category_var.get.return_value = "切换模块"
        form.after_action_var.get.return_value = "点击识别区域"
        form.second_click_target_var = Mock()
        form.second_click_target_var.get.return_value = "第二次识别位置"
        form.run_code_after_action_var.get.return_value = False
        form.run_code_on_timeout_var.get.return_value = False
        form._set_row = Mock()
        form._resize_for_content = Mock()
        form._toggle_sections = TemplateRegionFormDialog._toggle_sections.__get__(
            form, TemplateRegionFormDialog,
        )

        form._toggle_sections()

        visible = {
            item.args[0] for item in form._set_row.call_args_list if item.args[1]
        }
        self.assertIn(form.row_start_delay, visible)

    def test_restart_target_dialog_save_picks_row_object(self):
        dialog = RestartWorkflowTargetDialog.__new__(RestartWorkflowTargetDialog)
        dialog.row_ids = {"（使用默认跳转行）": 0, "第 2 行 · 脚本a": 2}
        dialog.row_var = Mock()
        dialog.row_var.get.return_value = "第 2 行 · 脚本a"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["restart_workflow_target_row"], 2)
        dialog.destroy.assert_called_once()

    def test_restart_target_dialog_save_use_default(self):
        dialog = RestartWorkflowTargetDialog.__new__(RestartWorkflowTargetDialog)
        dialog.row_ids = {"（使用默认跳转行：第 4 行）": 0}
        dialog.row_var = Mock()
        dialog.row_var.get.return_value = "（使用默认跳转行：第 4 行）"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["restart_workflow_target_row"], 0)
        dialog.destroy.assert_called_once()

    def test_restart_target_dialog_save_custom_row(self):
        dialog = RestartWorkflowTargetDialog.__new__(RestartWorkflowTargetDialog)
        dialog.row_ids = {}
        dialog.row_var = Mock()
        dialog.row_var.get.return_value = "自定义行号…"
        dialog.row_spin_var = Mock()
        dialog.row_spin_var.get.return_value = "7"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result["restart_workflow_target_row"], 7)

    def test_restart_workflow_row_options_label_rows(self):
        labels, mapping = restart_workflow_row_options(
            [{"kind": "script", "script": "scripts/a.json"},
             {"kind": "module", "action": {"module_name": "可领取"}}],
            default_row=2,
        )
        self.assertEqual(mapping[labels[0]], 0)
        self.assertIn("（使用默认跳转行：第 2 行）", labels[0])
        self.assertEqual(mapping["第 1 行 · a"], 1)
        self.assertEqual(mapping["第 2 行 · 模块 可领取"], 2)

    def test_form_saves_fallback_module_and_click_behavior(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.fallback_module_key_var.get.return_value = "module:fallback"
        form.fallback_click_var.get.return_value = True
        form.fallback_on_match_var.get.return_value = "点击备用命中位置，继续识别主模块"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[2]["fallback_module_key"], "module:fallback")
        self.assertTrue(form.result[2]["fallback_click"])
        self.assertEqual(
            form.result[2]["fallback_on_match"], "click_continue",
        )
        notice.assert_not_called()

    def test_form_saves_fallback_exit_option(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.fallback_module_key_var.get.return_value = "module:fallback"
        form.fallback_on_match_var.get.return_value = "点击备用命中位置后退出主模块识别"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[2]["fallback_on_match"], "click_exit")
        self.assertTrue(form.result[2]["fallback_click"])
        notice.assert_not_called()

    def test_form_save_click_custom_requires_point(self):
        form = self._form(
            image="images/g.png", region="10,20,300,400",
            after_action="点击自定义位置",
        )
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        form.destroy.assert_not_called()
        form.click_point_var.get.return_value = "120,340"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[2]["after_action"], "click_custom")
        self.assertEqual(form.result[2]["click_point"], [120, 340])
        form.destroy.assert_called_once()

    def test_form_save_second_match_requires_template(self):
        form = self._form(
            image="images/g.png", region="10,20,300,400",
            after_action="二次识别后点击",
        )
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        self.assertIn("缺少二次识别模板", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_second_match_custom_click_region(self):
        form = self._form(
            image="images/g.png", region="10,20,300,400",
            after_action="二次识别后点击",
        )
        form.second_template_var.get.return_value = "images/second.png"
        form.second_click_target_var.get.return_value = "自定义框选区域"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertIn("缺少自定义点击区域", notice.call_args.args[1])
        form.destroy.assert_not_called()

        form.second_click_region_var.get.return_value = "100,200,80,40"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        obj = form.result[2]
        self.assertEqual(obj["second_match_click_target"], "custom_region")
        self.assertEqual(obj["second_match_click_region"], [100, 200, 80, 40])
        form.destroy.assert_called_once()

    def test_form_save_enabled_post_action_code_requires_segment(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.run_code_after_action_var.get.return_value = True
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        self.assertIn("代码段为空", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_continue_with_post_action_code(self):
        form = self._form(
            image="images/g.png", region="10,20,300,400",
            after_action="成功后继续",
        )
        form.run_code_after_action_var.get.return_value = True
        form.segment = [{"type": "restart_workflow"}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        obj = form.result[2]
        self.assertEqual(obj["after_action"], "continue")
        self.assertTrue(obj["run_code_after_action"])
        self.assertEqual(obj["on_success_actions"][0]["type"], "restart_workflow")
        self.assertTrue(obj["on_success_actions"][0]["action_id"])
        notice.assert_not_called()

    def test_form_save_independent_not_found_timeout_code(self):
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.run_code_on_timeout_var.get.return_value = True
        form.not_found_timeout_var.get.return_value = "4200"
        form.timeout_segment = [{"type": "delay", "ms": 25}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        obj = form.result[2]
        self.assertTrue(obj["run_code_on_timeout"])
        self.assertEqual(obj["not_found_timeout_ms"], 4200)
        self.assertEqual(obj["on_timeout_actions"][0]["type"], "delay")
        self.assertEqual(obj["on_success_actions"], [])
        self.assertTrue(obj["on_timeout_actions"][0]["action_id"])
        notice.assert_not_called()

    def test_add_segment_activate_window_stores_stable_signature(self):
        form = self._form()
        form.segment_listbox = Mock()
        selected = WindowInfo(321, "目标窗口", "GameWnd", r"C:\\Game\\game.exe")
        with patch('macroflow.ui.dialogs.app_dialogs.WindowPicker') as picker:
            picker.return_value.show.return_value = selected
            form._add_segment_activate_window()
        action = form.segment[0]
        self.assertEqual(action["type"], "activate_window")
        self.assertEqual(action["window"]["title"], "目标窗口")
        self.assertEqual(action["window"]["class_name"], "GameWnd")
        self.assertTrue(action["action_id"])

    def test_segment_add_menu_includes_repeat_click_for_timeout_code_block(self):
        form = self._form()
        form.winfo_pointerx = Mock(return_value=10)
        form.winfo_pointery = Mock(return_value=20)
        menu = Mock()
        with patch("tkinter.Menu", return_value=menu):
            form._add_segment_item("timeout_segment", "timeout_segment_listbox")
        commands = {
            item.kwargs.get("label"): item.kwargs.get("command")
            for item in menu.add_command.call_args_list
        }
        self.assertIn("连续点击", commands)
        form._add_segment_dialog = Mock()
        commands["连续点击"]()
        form._add_segment_dialog.assert_called_once_with(
            RepeatClickDialog, "timeout_segment", "timeout_segment_listbox",
        )

    def test_nested_click_picker_hides_every_ancestor_window(self):
        # 幕布必须让开整条窗口链，而且要连不在链上的顶层窗口（悬浮提醒小窗）
        # 一起让开——那种小窗是挂在根窗口下的独立 Toplevel、overrideredirect
        # 且 -topmost，只沿 master 往上找会漏掉它，表现为「幕布没隐藏软件界面」。
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        manager = tk.Toplevel(root)
        manager.withdraw()
        module_form = tk.Toplevel(manager)
        module_form.withdraw()
        # 模拟悬浮提醒小窗：挂在根窗口下、不在任何 master 链上、置顶。
        notice = tk.Toplevel(root)
        notice.withdraw()
        notice.overrideredirect(True)
        notice.attributes("-topmost", True)

        dialog = ClickDialog.__new__(ClickDialog)
        dialog.master = module_form
        dialog._apply_picked_point = Mock()

        hidden = dialog_module.app_windows(module_form)
        for expected in (root, manager, module_form, notice):
            self.assertIn(expected, hidden)
        # 不在链上的置顶小窗正是旧实现漏掉的那个。
        self.assertFalse(
            any(window is notice for window in dialog_module.ancestor_windows(module_form))
        )

        with package_patch('dialogs', 'ScreenPointPicker') as picker:
            dialog.start_pick_position()
        self.assertIs(picker.call_args.args[0], dialog)
        self.assertIs(picker.call_args.args[1], module_form)
        self.assertEqual(picker.call_args.kwargs["hidden_windows"], hidden)
        picker.return_value.start.assert_called_once()

    def test_ctrl_a_selects_all_module_segment_actions(self):
        form = self._form()
        form.segment_listbox = Mock()

        result = form._select_all_segment_items()

        self.assertEqual(result, "break")
        form.segment_listbox.selection_set.assert_called_once_with(0, "end")

    def test_remove_segment_item_deletes_every_selected_action(self):
        form = self._form()
        form.segment = [
            {"type": "delay", "ms": 1},
            {"type": "delay", "ms": 2},
            {"type": "delay", "ms": 3},
        ]
        form.segment_listbox = Mock()
        form.segment_listbox.curselection.return_value = (0, 2)

        form._remove_segment_item()

        self.assertEqual(form.segment, [{"type": "delay", "ms": 2}])

    def test_form_save_pure_action_requires_name(self):
        # 特殊模块纯动作（未选图片）：名称必填。
        form = self._form(image="")
        form.category_var.get.return_value = "特殊模块"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        notice.assert_called_once()
        self.assertIn("缺少名称", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_pure_action_sets_result(self):
        # 特殊模块纯动作保存：名称做 key，对象只有 category/name/pure_action。
        form = self._form(image="")
        form.category_var.get.return_value = "特殊模块"
        form.name_var.get.return_value = "重新执行工作流"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[0], "")
        self.assertEqual(form.result[1], "重新执行工作流")
        self.assertEqual(form.result[2], {
            "category": "special", "name": "重新执行工作流", "pure_action": True,
        })
        form.destroy.assert_called_once()
        notice.assert_not_called()

    def test_form_save_global_with_image_sets_global_category(self):
        # 全局模块（检测型，选了图片）：对象类别为 global，其余字段照常。
        form = self._form(image="images/g.png", region="10,20,300,400")
        form.category_var.get.return_value = "工作流全局模块"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[2]["category"], "workflow_global")
        self.assertEqual(form.result[2]["region"], [10, 20, 300, 400])
        self.assertNotIn("pure_action", form.result[2])
        form.destroy.assert_called_once()
        notice.assert_not_called()

    def test_form_save_text_mode_allows_no_image_or_region(self):
        # 识别文字方式：不需要模板图片，区域可留空（空=全屏），名称缺省"识别文字"。
        form = self._form(recognize="识别文字")
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        module = form.result[2]
        self.assertEqual(module["recognize"], "text")
        self.assertEqual(module["template"], "")
        self.assertEqual(module["region"], [])
        self.assertEqual(module["expected_text"], "")
        self.assertEqual(module["match_mode"], "contains")
        self.assertEqual(module["name"], "识别文字")
        form.destroy.assert_called_once()
        notice.assert_not_called()

    def test_form_save_number_mode_requires_region_and_has_no_click_side_effects(self):
        form = self._form(region="10,20,80,30", recognize="读取数字")
        form.run_code_after_action_var.get.return_value = True
        form.run_code_on_timeout_var.get.return_value = True
        form.segment = [{"type": "click"}]
        form.timeout_segment = [{"type": "click"}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        module = form.result[2]
        self.assertEqual(module["recognize"], "number")
        self.assertEqual(module["name"], "读取数字")
        self.assertEqual(module["template"], "")
        self.assertEqual(module["region"], [10, 20, 80, 30])
        self.assertEqual(module["after_action"], "continue")
        self.assertFalse(module["run_code_after_action"])
        self.assertFalse(module["run_code_on_timeout"])
        self.assertEqual(module["on_success_actions"], [])
        self.assertEqual(module["on_timeout_actions"], [])
        notice.assert_not_called()

    def test_form_save_number_mode_rejects_missing_region(self):
        form = self._form(recognize="读取数字")
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertIn("缺少框选区域", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_number_mode_rejects_global_category(self):
        form = self._form(region="10,20,80,30", recognize="读取数字")
        form.category_var.get.return_value = "工作流全局模块"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertIn("类别不适用", notice.call_args.args[1])
        form.destroy.assert_not_called()

    def test_form_save_no_recognition_mode_runs_directly_without_image_or_region(self):
        form = self._form(recognize="无需识图")
        form.run_code_after_action_var.get.return_value = True
        form.segment = [{"type": "delay", "ms": 25}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        module = form.result[2]
        self.assertEqual(module["recognize"], "none")
        self.assertEqual(module["template"], "")
        self.assertEqual(module["region"], [])
        self.assertEqual(module["after_action"], "continue")
        self.assertTrue(module["run_code_after_action"])
        self.assertFalse(module["run_code_on_timeout"])
        self.assertEqual(module["name"], "无需识图")
        form.destroy.assert_called_once()
        notice.assert_not_called()

    def test_form_allows_no_recognition_for_global_module_timeout(self):
        form = self._form(recognize="无需识图")
        form.category_var.get.return_value = "工作流全局模块"
        form.run_code_on_timeout_var.get.return_value = True
        form.timeout_segment = [{"type": "delay", "ms": 25}]
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        module = form.result[2]
        self.assertEqual(module["recognize"], "none")
        self.assertTrue(module["run_code_on_timeout"])
        self.assertEqual(module["on_timeout_actions"][0]["type"], "delay")
        form.destroy.assert_called_once()
        notice.assert_not_called()

    def test_form_save_text_mode_keeps_expected_text_and_equals(self):
        form = self._form(region="10,20,300,400", recognize="识别文字")
        form.expected_text_var.get.return_value = "体力不足"
        form.match_mode_var.get.return_value = "等于"
        form.wait_text_absent_var.get.return_value = True
        form.ocr_offset_up_var.get.return_value = "8"
        form.ocr_offset_down_var.get.return_value = "2"
        form.ocr_offset_left_var.get.return_value = "4"
        form.ocr_offset_right_var.get.return_value = "12"
        form.name_var.get.return_value = "体力检测"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        module = form.result[2]
        self.assertEqual(module["recognize"], "text")
        self.assertEqual(module["expected_text"], "体力不足")
        self.assertEqual(module["match_mode"], "equals")
        self.assertTrue(module["wait_text_absent"])
        self.assertEqual(module["ocr_offset_up"], 8)
        self.assertEqual(module["ocr_offset_down"], 2)
        self.assertEqual(module["ocr_offset_left"], 4)
        self.assertEqual(module["ocr_offset_right"], 12)
        self.assertEqual(module["region"], [10, 20, 300, 400])
        self.assertEqual(module["name"], "体力检测")
        notice.assert_not_called()

    def test_form_save_template_mode_keeps_wait_until_absent_option(self):
        form = self._form(
            image="images/claim.png", region="10,20,300,400", recognize="模板图片",
        )
        form.wait_text_absent_var.get.return_value = True
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        module = form.result[2]
        self.assertTrue(module["wait_text_absent"])
        self.assertNotEqual(module.get("recognize"), "text")
        notice.assert_not_called()

    def test_form_save_module_click_count_defaults_to_one_and_accepts_custom_value(self):
        form = self._form(
            image="images/claim.png", region="10,20,300,400", recognize="模板图片",
        )
        form.click_count_var.get.return_value = "4"
        with package_patch('dialogs', 'show_floating_notice') as notice:
            form.save()
        self.assertEqual(form.result[2]["click_count"], 4)
        notice.assert_not_called()

    def test_form_drag_offset_converts_two_points_to_four_directions(self):
        form = self._form(recognize="识别文字")
        form._apply_ocr_offset(500, 400, 465, 428)
        form.ocr_offset_left_var.set.assert_called_once_with("35")
        form.ocr_offset_right_var.set.assert_called_once_with("0")
        form.ocr_offset_up_var.set.assert_called_once_with("0")
        form.ocr_offset_down_var.set.assert_called_once_with("28")

    def test_toggle_sections_text_mode_shows_ocr_rows_and_forces_click_match(self):
        form = self._form(recognize="识别文字")
        form.pure = False
        for attr in (
            "row_name", "row_image", "row_region", "detect_section_heading",
            "row_recognize", "row_expected_text", "row_match_mode", "row_threshold",
            "row_wait_text_absent",
            "row_ignore_background", "row_interval", "row_cooldown", "row_start_delay", "row_fallback_module",
            "row_fallback_click", "row_blocking", "row_delay",
            "action_section_heading", "row_after", "row_hold", "row_button",
            "row_click_count",
            "row_ocr_offset",
            "row_click_point", "row_second_template", "row_second_timeout",
            "row_second_click_target", "row_second_click_region",
            "segment_section_heading", "row_run_code_after_action", "segment_frame",
            "timeout_section_heading", "row_run_code_on_timeout",
            "row_not_found_timeout", "timeout_segment_frame",
        ):
            setattr(form, attr, Mock())
        form.after_action_var.get.return_value = "二次识别后点击"
        form.after_action_var.set = Mock()
        form.second_click_target_var = Mock()
        form.second_click_target_var.get.return_value = "第二次识别位置"
        form.run_code_after_action_var.get.return_value = False
        form.run_code_on_timeout_var.get.return_value = False
        form._set_row = Mock()
        form._resize_for_content = Mock()
        form._toggle_sections = TemplateRegionFormDialog._toggle_sections.__get__(
            form, TemplateRegionFormDialog,
        )

        form._toggle_sections()

        # 识别文字方式：二次识别被强制回落到"点击识别区域"。
        form.after_action_var.set.assert_called_once_with("点击识别区域")
        visible = {
            item.args[0] for item in form._set_row.call_args_list if item.args[1]
        }
        hidden = {
            item.args[0] for item in form._set_row.call_args_list if not item.args[1]
        }
        for row in ("row_image", "row_threshold", "row_ignore_background",
                    "row_second_template", "row_second_timeout"):
            self.assertIn(getattr(form, row), hidden)
        for row in (
            "row_expected_text", "row_match_mode", "row_wait_text_absent",
            "row_region", "row_recognize", "row_ocr_offset",
        ):
            self.assertIn(getattr(form, row), visible)

    def test_toggle_sections_template_mode_shows_wait_until_absent(self):
        form = self._form(recognize="模板图片")
        form.pure = False
        for attr in (
            "row_name", "row_image", "row_region", "detect_section_heading",
            "row_recognize", "row_expected_text", "row_match_mode", "row_threshold",
            "row_wait_text_absent", "row_ignore_background", "row_interval", "row_cooldown", "row_start_delay",
            "row_fallback_module", "row_fallback_click",
            "row_blocking", "row_delay", "action_section_heading", "row_after",
            "row_hold", "row_button", "row_click_count", "row_ocr_offset", "row_click_point",
            "row_second_template", "row_second_timeout", "row_second_click_target",
            "row_second_click_region", "segment_section_heading",
            "row_run_code_after_action", "segment_frame", "timeout_section_heading",
            "row_run_code_on_timeout", "row_not_found_timeout", "timeout_segment_frame",
        ):
            setattr(form, attr, Mock())
        form.after_action_var.get.return_value = "点击识别区域"
        form.second_click_target_var = Mock()
        form.second_click_target_var.get.return_value = "第二次识别位置"
        form.run_code_after_action_var.get.return_value = False
        form.run_code_on_timeout_var.get.return_value = False
        form._set_row = Mock()
        form._resize_for_content = Mock()
        form._toggle_sections = TemplateRegionFormDialog._toggle_sections.__get__(
            form, TemplateRegionFormDialog,
        )

        form._toggle_sections()

        visible = {
            item.args[0] for item in form._set_row.call_args_list if item.args[1]
        }
        self.assertIn(form.row_wait_text_absent, visible)

    def test_manager_open_add_persists_new_entry(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {}
        dialog.current = "switch"
        dialog.trees = {"switch": Mock()}
        form = Mock()
        obj = self._object()
        form.show.return_value = ("", "images/g.png", obj)
        with package_patch('dialogs', 'TemplateRegionFormDialog', return_value=form) as form_class, \
             package_patch('dialogs', 'update_module_object', return_value={'images/g.png': obj}) as save, \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            dialog._open_add()
        form_class.assert_called_once_with(dialog, "", object_dict=None, category="switch")
        save.assert_called_once_with("images/g.png", obj, old_key="")
        self.assertEqual(dialog.objects, {"images/g.png": obj})
        dialog.trees["switch"].selection_set.assert_called_once_with("images/g.png")
        dialog.trees["switch"].see.assert_called_once_with("images/g.png")

    def test_manager_open_edit_switches_file_keeps_region(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {"images/a.png": self._object()}
        dialog.current = "switch"
        tree = Mock()
        tree.selection.return_value = ("images/a.png",)
        dialog.trees = {"switch": tree}
        original = dialog.objects["images/a.png"]
        form = Mock()
        obj = self._object()
        form.show.return_value = ("images/a.png", "images/b.png", obj)
        with package_patch('dialogs', 'TemplateRegionFormDialog', return_value=form) as form_class, \
             package_patch('dialogs', 'update_module_object', return_value={'images/b.png': obj}) as save, \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            dialog._open_edit()
        form_class.assert_called_once_with(
            dialog, "images/a.png", object_dict=original, category="switch",
        )
        save.assert_called_once_with("images/b.png", obj, old_key="images/a.png")
        self.assertEqual(dialog.objects, {"images/b.png": obj})
        tree.selection_set.assert_called_once_with("images/b.png")

    def test_manager_open_edit_without_selection_does_nothing(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {"images/a.png": self._object()}
        dialog.current = "switch"
        tree = Mock()
        tree.selection.return_value = ()
        dialog.trees = {"switch": tree}
        with package_patch('dialogs', 'TemplateRegionFormDialog') as form_class, \
             package_patch('dialogs', 'update_module_object') as save, \
             package_patch('dialogs', 'show_floating_notice') as notice:
            dialog._open_edit()
        form_class.assert_not_called()
        self.assertEqual(dialog.objects, {"images/a.png": dialog.objects["images/a.png"]})
        save.assert_not_called()
        notice.assert_called_once()

    def test_manager_form_cancelled_keeps_objects(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        obj = self._object()
        dialog.objects = {"images/a.png": obj}
        dialog.current = "switch"
        dialog.trees = {"switch": Mock()}
        form = Mock()
        form.show.return_value = None
        with package_patch('dialogs', 'TemplateRegionFormDialog', return_value=form), \
             package_patch('dialogs', 'update_module_object') as save:
            dialog._open_form("images/a.png", obj)
        self.assertEqual(dialog.objects, {"images/a.png": obj})
        save.assert_not_called()

    def test_manager_form_edit_same_image_updates_object(self):
        # 图片不变只改对象属性，条目不换 key。
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {"images/a.png": self._object(region=(0, 0, 0, 0))}
        dialog.current = "switch"
        tree = Mock()
        tree.selection_set.side_effect = tk.TclError
        dialog.trees = {"switch": tree}
        form = Mock()
        obj = self._object(region=(10, 20, 300, 400), after_action="continue")
        form.show.return_value = ("images/a.png", "images/a.png", obj)
        with package_patch('dialogs', 'TemplateRegionFormDialog', return_value=form), \
             package_patch('dialogs', 'update_module_object', return_value={'images/a.png': obj}) as save, \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            dialog._open_form("images/a.png", dialog.objects["images/a.png"])
        self.assertEqual(dialog.objects, {"images/a.png": obj})
        save.assert_called_once_with("images/a.png", obj, old_key="images/a.png")
        tree.selection_set.assert_called_once_with("images/a.png")

    def test_manager_special_module_cannot_be_edited(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.current = "all"
        dialog.objects = {
            "重新执行工作流": {
                "category": "special", "name": "重新执行工作流", "pure_action": True,
            },
        }
        tree = Mock()
        tree.selection.return_value = ("重新执行工作流",)
        dialog.trees = {"all": tree}
        with package_patch('dialogs', 'TemplateRegionFormDialog') as form_class, \
             package_patch('dialogs', 'show_floating_notice') as notice:
            dialog._open_edit()
        form_class.assert_not_called()
        notice.assert_called_once()

    def test_manager_remove_selected_persists(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {"images/g.png": self._object()}
        dialog.current = "switch"
        tree = Mock()
        tree.selection.return_value = ("images/g.png",)
        dialog.trees = {"switch": tree}
        dialog._undo_stack = []
        dialog.undo_button = Mock()
        with package_patch('dialogs', 'save_module_objects') as save, \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            dialog._remove_selected()
        self.assertEqual(dialog.objects, {})
        save.assert_called_once_with({})
        # 移除的条目连同完整对象快照进撤销栈，供"撤销移除"恢复。
        self.assertEqual(dialog._undo_stack, [("images/g.png", self._object())])
        dialog.undo_button.configure.assert_called_with(state="normal")

    def test_manager_undo_remove_restores_entry_and_persists(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        obj = self._object()
        dialog.objects = {}
        dialog.current = "switch"
        tree = Mock()
        dialog.trees = {"switch": tree}
        dialog._undo_stack = [("images/g.png", obj)]
        dialog.undo_button = Mock()
        with package_patch('dialogs', 'save_module_objects') as save, \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            dialog._undo_remove()
        self.assertEqual(dialog.objects, {"images/g.png": obj})
        save.assert_called_once_with({"images/g.png": obj})
        self.assertEqual(dialog._undo_stack, [])
        tree.selection_set.assert_called_once_with("images/g.png")
        tree.see.assert_called_once_with("images/g.png")
        dialog.undo_button.configure.assert_called_with(state="disabled")

    def test_manager_undo_remove_empty_stack_does_nothing(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {}
        dialog.current = "switch"
        dialog.trees = {"switch": Mock()}
        dialog._undo_stack = []
        with package_patch('dialogs', 'save_module_objects') as save:
            dialog._undo_remove()
        save.assert_not_called()

    def test_manager_remove_without_selection_does_nothing(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        obj = self._object()
        dialog.objects = {"images/g.png": obj}
        dialog.current = "switch"
        tree = Mock()
        tree.selection.return_value = ()
        dialog.trees = {"switch": tree}
        with package_patch('dialogs', 'save_module_objects') as save:
            dialog._remove_selected()
        self.assertEqual(dialog.objects, {"images/g.png": obj})
        save.assert_not_called()

    def test_manager_copies_script_global_to_independent_workflow_global(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        original = self._object(
            category="script_global", name="同名全局", template="images/shared.png",
            on_success_actions=[{"type": "delay", "ms": 10}],
        )
        dialog.objects = {"module:source": original}
        with patch("uuid.uuid4") as uuid4, \
             package_patch('dialogs', 'save_module_objects') as save, \
             package_patch('dialogs', 'show_floating_notice'), \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            uuid4.return_value.hex = "copied"
            dialog._change_global_module_category(
                "module:source", "workflow_global", copy_object=True,
            )
        self.assertEqual(dialog.objects["module:source"]["category"], "script_global")
        copied = dialog.objects["module:copied"]
        self.assertEqual(copied["category"], "workflow_global")
        self.assertEqual(copied["name"], "同名全局")
        self.assertEqual(copied["template"], "images/shared.png")
        self.assertIsNot(copied["on_success_actions"], original["on_success_actions"])
        save.assert_called_once_with(dialog.objects)

    def test_manager_copy_backfills_name_for_nameless_source(self):
        # 旧对象没有 name 时（图片路径键年代），复制成 module:<uuid> 键后
        # 显示兜底会退化成 uuid——复制时按模板文件名补名。
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        original = self._object(category="workflow_global", template="images/shared.png")
        dialog.objects = {"images/shared.png": original}
        with patch("uuid.uuid4") as uuid4, \
             package_patch('dialogs', 'save_module_objects') as save, \
             package_patch('dialogs', 'show_floating_notice'), \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            uuid4.return_value.hex = "copied"
            dialog._change_global_module_category(
                "images/shared.png", "script_global", copy_object=True,
            )
        copied = dialog.objects["module:copied"]
        self.assertEqual(copied["category"], "script_global")
        self.assertEqual(copied["name"], "shared")
        save.assert_called_once_with(dialog.objects)

    def test_manager_moves_workflow_global_to_script_global_with_same_id(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {
            "module:source": self._object(category="workflow_global", name="全局"),
        }
        with package_patch('dialogs', 'save_module_objects') as save, \
             package_patch('dialogs', 'show_floating_notice'), \
             patch.object(TemplateRegionManagerDialog, "_reload_trees"):
            dialog._change_global_module_category(
                "module:source", "script_global", copy_object=False,
            )
        self.assertEqual(list(dialog.objects), ["module:source"])
        self.assertEqual(dialog.objects["module:source"]["category"], "script_global")
        save.assert_called_once_with(dialog.objects)

    def test_manager_global_context_menu_offers_reverse_category_actions(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.objects = {
            "module:source": self._object(category="script_global", name="全局"),
        }
        tree = Mock()
        tree.identify_row.return_value = "module:source"
        event = Mock(widget=tree, y=30, x_root=100, y_root=120)
        with patch("tkinter.Menu") as menu_class:
            dialog._show_module_context_menu(event)
        labels = [call.kwargs["label"] for call in menu_class.return_value.add_command.call_args_list]
        self.assertEqual(labels, [
            "▶ 测试指定次数…", "改成工作流全局", "复制成工作流全局",
        ])
        tree.selection_set.assert_called_once_with("module:source")
        menu_class.return_value.tk_popup.assert_called_once_with(100, 120)

    def test_manager_context_menu_offers_test_for_every_module_category(self):
        for category in ("switch", "workflow_global", "script_global", "special"):
            with self.subTest(category=category):
                dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
                dialog.objects = {
                    "module:source": self._object(category=category, name="测试模块"),
                }
                tree = Mock()
                tree.identify_row.return_value = "module:source"
                event = Mock(widget=tree, y=30, x_root=100, y_root=120)
                with patch("tkinter.Menu") as menu_class:
                    dialog._show_module_context_menu(event)
                labels = [
                    item.kwargs["label"]
                    for item in menu_class.return_value.add_command.call_args_list
                ]
                self.assertIn("▶ 测试指定次数…", labels)
                tree.selection_set.assert_called_once_with("module:source")

    def test_manager_module_test_asks_for_count_and_runs_selected_module(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.app = Mock()
        dialog.app._ask_repeats.return_value = 4

        dialog._test_module_with_count("module:source")

        dialog.app._ask_repeats.assert_called_once_with(
            "测试模块", "这个模块要独立测试几次？",
        )
        dialog.app.run_module_object_test.assert_called_once_with("module:source", 4)

    def test_manager_module_test_cancel_runs_nothing(self):
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.app = Mock()
        dialog.app._ask_repeats.return_value = None

        dialog._test_module_with_count("module:source")

        dialog.app.run_module_object_test.assert_not_called()

    def test_prepend_global_module_to_selected_scripts(self):
        with tempfile.TemporaryDirectory() as folder:
            normal_path = Path(folder) / "normal.json"
            global_path = Path(folder) / "global.json"
            original = {"type": "delay", "ms": 10}
            save_script(MacroScript(name="normal", actions=[original]), normal_path)
            save_script(MacroScript(name="global", actions=[], is_global=True), global_path)

            added, skipped, errors = prepend_module_to_scripts(
                "images/g.png", "global", [normal_path, global_path],
            )

            self.assertEqual(added, 1)
            self.assertEqual(skipped, [global_path])
            self.assertEqual(errors, [])
            script = load_script(normal_path)
            self.assertEqual(script.actions[0]["type"], "global_detect")
            self.assertEqual(script.actions[0]["template"], "images/g.png")
            self.assertEqual(script.actions[0]["jump_row"], 2)
            self.assertEqual(
                script.actions[0]["jump_action_id"],
                script.actions[1][ACTION_ID_KEY],
            )

    def test_prepend_disabled_module_is_rejected_without_changing_script(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "normal.json"
            save_script(MacroScript(name="normal", actions=[]), path)
            with package_patch('dialogs', 'registered_module_object', return_value={'enabled': False, 'category': 'switch'}):
                added, skipped, errors = prepend_module_to_scripts(
                    "module:disabled", "switch", [path],
                )
            self.assertEqual((added, skipped), (0, []))
            self.assertEqual(errors, [(path, "模块已禁用，不能插入到脚本")])
            self.assertEqual(load_script(path).actions, [])

    def test_prepend_switch_module_only_changes_checked_script(self):
        with tempfile.TemporaryDirectory() as folder:
            selected_path = Path(folder) / "selected.json"
            other_path = Path(folder) / "other.json"
            save_script(MacroScript(name="selected", actions=[]), selected_path)
            save_script(MacroScript(name="other", actions=[]), other_path)

            added, skipped, errors = prepend_module_to_scripts(
                "images/s.png", "switch", [selected_path],
            )

            self.assertEqual((added, skipped, errors), (1, [], []))
            self.assertEqual(load_script(selected_path).actions[0], {
                "type": "image_match", "template": "images/s.png",
                "module_key": "images/s.png",
                "module_ref": True, "module_category": "switch",
                "region_mode": "template", "region": [], "delay_ms": 0,
                "on_found": "continue", "on_timeout": "continue",
                ACTION_ID_KEY: load_script(selected_path).actions[0][ACTION_ID_KEY],
            })
            self.assertEqual(load_script(other_path).actions, [])

    def test_remove_module_from_scripts_removes_all_reference_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            used_path = Path(folder) / "used.json"
            other_path = Path(folder) / "other.json"
            unrelated_path = Path(folder) / "unrelated.json"
            kept = {"type": "delay", "ms": 10, ACTION_ID_KEY: "keep-id"}
            reference = module_action_for_key("images/g.png", "switch")
            another_reference = module_action_for_key("images/g.png", "switch")
            save_script(MacroScript(
                name="used", actions=[kept, reference, another_reference],
            ), used_path)
            save_script(MacroScript(name="other", actions=[kept]), other_path)
            save_script(MacroScript(
                name="unrelated",
                actions=[module_action_for_key("images/s.png", "switch")],
            ), unrelated_path)

            removed, untouched, errors = remove_module_from_scripts(
                "images/g.png", [used_path, other_path, unrelated_path],
            )

            self.assertEqual(removed, 1)
            self.assertEqual(set(untouched), {other_path, unrelated_path})
            self.assertEqual(errors, [])
            script = load_script(used_path)
            self.assertEqual(script.actions, [kept])

    def test_remove_module_rebuilds_action_ids_of_remaining_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.json"
            kept = {"type": "delay", "ms": 10, ACTION_ID_KEY: "keep"}
            save_script(MacroScript(
                name="script",
                actions=[kept, module_action_for_key("images/g.png", "switch")],
            ), path)

            removed, untouched, errors = remove_module_from_scripts("images/g.png", [path])

            self.assertEqual((removed, untouched, errors), (1, [], []))
            remaining = load_script(path).actions
            self.assertEqual(len(remaining), 1)
            self.assertTrue(str(remaining[0].get(ACTION_ID_KEY, "")).strip())

    def test_remove_module_from_scripts_skips_untouched_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            clean_path = Path(folder) / "clean.json"
            broken_path = Path(folder) / "broken.json"
            save_script(MacroScript(name="clean", actions=[]), clean_path)
            broken_path.write_text("{not json", encoding="utf-8")

            removed, untouched, errors = remove_module_from_scripts(
                "images/g.png", [clean_path, broken_path],
            )

            self.assertEqual(removed, 0)
            self.assertEqual(untouched, [clean_path])
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][0], broken_path)

    def test_batch_remove_dialog_counts_usage_and_prechecks_used(self):
        with tempfile.TemporaryDirectory() as folder:
            used_path = Path(folder) / "used.json"
            clean_path = Path(folder) / "clean.json"
            save_script(MacroScript(
                name="used", actions=[module_action_for_key("images/g.png", "switch")],
            ), used_path)
            save_script(MacroScript(name="clean", actions=[]), clean_path)

            dialog = BatchModuleScriptDialog.__new__(BatchModuleScriptDialog)
            dialog.script_paths = [used_path, clean_path]
            counts = dialog._count_module_usage("images/g.png")

            self.assertEqual(counts, {0: 1, 1: 0})

    def test_batch_remove_dialog_path_display_suffixes_usage(self):
        dialog = BatchModuleScriptDialog.__new__(BatchModuleScriptDialog)
        dialog.script_paths = [Path("scripts/关卡/目标.json"), Path("scripts/关卡/其他.json")]
        dialog.mode = "remove"
        dialog.usage_counts = {0: 2, 1: 0}
        used_text = f"scripts{os.sep}关卡{os.sep}目标.json（2 行）"
        clean_text = f"scripts{os.sep}关卡{os.sep}其他.json（未使用）"
        self.assertEqual(dialog._path_display(0), used_text)
        self.assertEqual(dialog._path_display(1), clean_text)
        dialog.mode = "add"
        self.assertEqual(dialog._path_display(0), used_text.split("（")[0])

    def test_batch_script_category_uses_saved_category_and_global_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            switch_path = Path(folder) / "switch.json"
            global_path = Path(folder) / "global.json"
            switch_script = MacroScript(name="switch")
            switch_script.settings["category"] = "switch"
            save_script(switch_script, switch_path)
            save_script(MacroScript(name="global", is_global=True), global_path)
            self.assertEqual(script_category_for_path(switch_path, {}), "switch")
            self.assertEqual(script_category_for_path(global_path, {}), "level")

    def test_batch_category_filter_preserves_cross_category_checks(self):
        dialog = BatchModuleScriptDialog.__new__(BatchModuleScriptDialog)
        dialog.script_paths = [Path("a.json"), Path("b.json"), Path("c.json")]
        dialog.script_categories = ["switch", "level_pack", "switch"]
        dialog.current_filter = "switch"
        dialog.checked = {1}
        dialog.tree = Mock()
        dialog.tree.exists.return_value = False

        dialog._select_all()

        self.assertEqual(dialog.checked, {0, 1, 2})
        dialog.current_filter = "level_pack"
        dialog._clear_all()
        self.assertEqual(dialog.checked, {0, 2})

    def test_batch_set_filter_reloads_without_clearing_checks(self):
        dialog = BatchModuleScriptDialog.__new__(BatchModuleScriptDialog)
        dialog.current_filter = "all"
        dialog.checked = {2}
        dialog.filter_buttons = {
            "all": Mock(), "switch": Mock(), "level_pack": Mock(),
        }
        with patch.object(BatchModuleScriptDialog, "_reload_visible_scripts") as reload:
            dialog._set_filter("level_pack")
        self.assertEqual(dialog.current_filter, "level_pack")
        self.assertEqual(dialog.checked, {2})
        reload.assert_called_once()

    def test_module_picker_switch_choose_returns_module_ref_action(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.category_keys = {"switch": ["images/s.png"], "special": []}
        picker.listboxes = {"switch": Mock(), "special": Mock()}
        picker.listboxes["switch"].curselection.return_value = (0,)
        picker.objects = {"images/s.png": self._object()}
        picker.destroy = Mock()
        picker._choose_category("switch")
        self.assertEqual(picker.result, {
            "type": "image_match", "template": "images/s.png", "module_ref": True,
            "module_key": "images/s.png",
            "module_category": "switch", "region_mode": "template",
            "region": [10, 20, 300, 400], "delay_ms": 0,
            "on_found": "continue", "on_timeout": "continue",
        })
        picker.destroy.assert_called_once()

    def test_module_picker_binds_selected_image_to_its_region(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.category_keys = {"switch": ["module:first"], "special": []}
        picker.listboxes = {"switch": Mock(), "special": Mock()}
        picker.listboxes["switch"].curselection.return_value = (0,)
        picker.objects = {
            "module:first": self._object(
                region=(11, 22, 333, 444),
                template="images/shared.png",
            ),
        }
        picker.destroy = Mock()

        picker._choose_category("switch")

        self.assertEqual(picker.result["module_key"], "module:first")
        self.assertEqual(picker.result["template"], "images/shared.png")
        self.assertEqual(picker.result["region"], [11, 22, 333, 444])
        self.assertEqual(picker.result["region_mode"], "template")
        picker.destroy.assert_called_once()

    def test_module_picker_selection_only_returns_bound_module_payload(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.selection_only = True
        picker.objects = {
            "module:first": self._object(
                region=(11, 22, 333, 444),
                template="images/shared.png",
            ),
        }

        result = picker._action_for_key("module:first", "switch")

        self.assertEqual(result["module_key"], "module:first")
        self.assertEqual(result["template"], "images/shared.png")
        self.assertEqual(result["region"], [11, 22, 333, 444])
        self.assertTrue(result["module_ref"])

    def test_same_image_modules_keep_independent_bound_regions_when_selected(self):
        first = module_action_for_key(
            "module:first", "switch", {
                "template": "images/shared.png", "region": [1, 2, 30, 40],
            },
        )
        second = module_action_for_key(
            "module:second", "switch", {
                "template": "images/shared.png", "region": [5, 6, 70, 80],
            },
        )

        self.assertEqual(first["module_key"], "module:first")
        self.assertEqual(first["region"], [1, 2, 30, 40])
        self.assertEqual(second["module_key"], "module:second")
        self.assertEqual(second["region"], [5, 6, 70, 80])

    def test_module_picker_number_action_defaults_failure_to_continue(self):
        action = module_action_for_key("module:number", "switch", {
            "recognize": "number", "template": "", "name": "剩余次数",
        })
        self.assertEqual(action["module_key"], "module:number")
        self.assertEqual(action["on_found"], "jump")
        self.assertEqual(action["on_timeout"], "continue")
        self.assertNotIn("expected_number", action)

    def test_module_picker_rejects_number_where_row_comparison_is_unavailable(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.allow_number = False
        picker.objects = {"module:number": {"recognize": "number", "category": "switch"}}
        picker.destroy = Mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            picker._choose_key("module:number", "switch")
        notice.assert_called_once()
        picker.destroy.assert_not_called()
        self.assertIsNone(getattr(picker, "result", None))

    def test_module_picker_hides_disabled_modules(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.objects = {
            "module:enabled": self._object(name="可用", enabled=True),
            "module:disabled": self._object(name="停用", enabled=False),
        }
        listbox = Mock()
        empty_label = Mock()
        picker.category_keys = {"switch": []}
        picker.listboxes = {"switch": listbox}
        picker.empty_labels = {"switch": empty_label}

        picker._refresh_category("switch")

        self.assertEqual(picker.category_keys["switch"], ["module:enabled"])
        listbox.insert.assert_called_once_with("end", "可用")
        empty_label.pack_forget.assert_called_once_with()

    def test_module_picker_script_global_choose_returns_global_detect_action(self):
        # 脚本全局模块插入 global_detect 动作，并保留明确类别。
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.category_keys = {"switch": [], "script_global": ["images/g.png"]}
        picker.listboxes = {"switch": Mock(), "script_global": Mock()}
        picker.listboxes["script_global"].curselection.return_value = (0,)
        picker.objects = {"images/g.png": self._object(category="script_global")}
        picker.destroy = Mock()
        picker._choose_category("script_global")
        self.assertEqual(picker.result, {
            "type": "global_detect", "template": "images/g.png", "module_ref": True,
            "module_key": "images/g.png",
            "module_category": "script_global", "region_mode": "template",
            "region": [10, 20, 300, 400], "delay_ms": 0,
        })
        picker.destroy.assert_called_once()

    def test_module_picker_global_multi_select_returns_all_selected_actions(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.multi_select = True
        picker.category_keys = {
            "workflow_global": ["images/g1.png", "images/g2.png", "images/g3.png"],
        }
        picker.listboxes = {"workflow_global": Mock()}
        picker.listboxes["workflow_global"].curselection.return_value = (0, 2)
        picker.destroy = Mock()

        picker._choose_category("workflow_global")

        self.assertEqual(
            [action["template"] for action in picker.result],
            ["images/g1.png", "images/g3.png"],
        )
        self.assertTrue(all(action["module_ref"] for action in picker.result))
        picker.destroy.assert_called_once()

    def test_module_picker_ctrl_a_selects_every_row(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        listbox = Mock()
        picker.listboxes = {"workflow_global": listbox}

        result = picker._select_all_category("workflow_global")

        listbox.selection_set.assert_called_once_with(0, "end")
        self.assertEqual(result, "break")

    def test_module_picker_special_pure_action_returns_restart_workflow(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.objects = {
            "重新执行工作流": {
                "category": "special", "name": "重新执行工作流", "pure_action": True,
            },
        }
        picker.destroy = Mock()
        picker._choose_key("重新执行工作流", "special")
        self.assertEqual(picker.result, {"type": "restart_workflow"})
        picker.destroy.assert_called_once()

    def test_module_picker_special_can_end_current_script(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.destroy = Mock()
        picker._choose_key("结束当前最里层脚本，继续执行", "special")
        self.assertEqual(picker.result, {"type": "end_current_script"})
        picker.destroy.assert_called_once()

    def test_module_picker_choose_without_selection_shows_notice(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.category_keys = {"switch": [], "special": []}
        picker.listboxes = {"switch": Mock(), "special": Mock()}
        picker.listboxes["switch"].curselection.return_value = ()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            picker._choose_category("switch")
        notice.assert_called_once()
        self.assertIsNone(getattr(picker, "result", None))

    def test_module_picker_new_object_stores_repo_and_returns_action(self):
        picker = ModulePickerDialog.__new__(ModulePickerDialog)
        picker.segment_depth = 0
        picker.destroy = Mock()
        form = Mock()
        obj = self._object()
        form.show.return_value = ("", "images/n.png", obj)
        picker.objects = {"images/n.png": obj}
        with package_patch('dialogs', 'TemplateRegionFormDialog', return_value=form) as form_class, \
             package_patch('dialogs', 'update_module_object') as save, \
             patch.object(ModulePickerDialog, "_refresh_lists") as refresh:
            picker._new_object("switch")
        form_class.assert_called_once_with(picker, category="switch", segment_depth=1)
        save.assert_called_once_with("images/n.png", obj, old_key="")
        refresh.assert_called_once()
        self.assertEqual(picker.result["template"], "images/n.png")
        self.assertEqual(picker.result["module_category"], "switch")
        picker.destroy.assert_called_once()

    def test_segment_add_module_ref_uses_nested_picker(self):
        form = self._form()
        form.segment_listbox = Mock()
        with package_patch('dialogs', 'ModulePickerDialog') as picker_class:
            picker_class.return_value.show.return_value = {
                "type": "image_match", "template": "images/m.png", "module_ref": True,
            }
            form._add_segment_module_ref()
        picker_class.assert_called_once_with(form, nested=True, segment_depth=1)
        self.assertEqual(len(form.segment), 1)
        self.assertEqual(form.segment[0]["module_ref"], True)
        self.assertTrue(form.segment[0]["action_id"])

    def test_segment_can_add_end_current_script_directly(self):
        form = self._form()
        form.segment_listbox = Mock()

        form._add_segment_end_current_script()

        self.assertEqual(form.segment[0]["type"], "end_current_script")
        self.assertTrue(form.segment[0]["action_id"])

    def test_segment_can_add_jump_to_current_script_last_action(self):
        form = self._form()
        form.segment_listbox = Mock()

        form._add_segment_jump_current_script_last()

        self.assertEqual(form.segment[0]["type"], "jump_current_script_last")
        self.assertTrue(form.segment[0]["action_id"])

    def test_edit_action_module_ref_opens_reference_result_dialog(self):
        action = {"type": "image_match", "template": "images/m.png", "module_ref": True,
                  "module_category": "switch", "action_id": "abc"}
        with package_patch('dialogs', 'TemplateRegionFormDialog') as form_class, \
             package_patch('dialogs', 'update_module_object') as save, \
             package_patch('dialogs', 'ModuleReferenceDelayDialog') as delay_dialog:
            delay_dialog.return_value.show.return_value = dict(
                action, delay_ms=500, after_delay_ms=800,
            )
            updated = edit_action(None, action)
        self.assertEqual(updated["delay_ms"], 500)
        self.assertEqual(updated["after_delay_ms"], 800)
        self.assertEqual(updated["action_id"], "abc")
        form_class.assert_not_called()
        save.assert_not_called()
        delay_dialog.assert_called_once_with(None, action, actions=None)

    def test_new_blocking_module_reference_defaults_to_timeout_skip_off(self):
        action = module_action_for_key(
            "module:blocking", "switch",
            {"template": "images/blocking.png", "blocking": True},
        )

        self.assertFalse(action.get("blocking_timeout_enabled", False))
        self.assertEqual(action.get("blocking_timeout_ms", 5000), 5000)

    def test_blocking_timeout_editor_uses_switchable_time_unit(self):
        duration_values = []

        def fake_duration_var(value=0):
            duration_values.append(value)
            return Mock()

        widgets = ("Frame", "Button", "Spinbox", "Checkbutton")
        label_mock = Mock(return_value=Mock())
        with patch("macroflow.ui.dialogs.base.ModalDialog.__init__", return_value=None), \
             package_patch('dialogs', 'fit_scrollable_window_to_content'), \
             package_patch('dialogs', 'scrollable_dialog_body', return_value=(Mock(**{'winfo_reqwidth.return_value': 1, 'winfo_reqheight.return_value': 1}), Mock(), Mock(**{'winfo_reqwidth.return_value': 1}))), \
             package_patch('dialogs', 'registered_module_object', return_value={'name': '阻塞模块', 'blocking': True}), \
             package_patch('dialogs', 'image_jump_target_options', return_value=[]), \
             patch("tkinter.BooleanVar", side_effect=lambda **_: Mock()), \
             patch("tkinter.StringVar", side_effect=lambda **_: Mock()), \
             package_patch('dialogs', 'duration_var', side_effect=fake_duration_var), \
             patch("ttkbootstrap.Label", label_mock), \
             patch("macroflow.ui.dialogs.module_objects.ttk", **{
                 **{name: Mock(return_value=Mock()) for name in widgets},
                 "Label": label_mock,
             }):
            ModuleReferenceDelayDialog(object(), {
                "type": "module_ref", "module_ref": True,
                "module_key": "module:blocking",
            })

        self.assertEqual(duration_values, [0, 0, 5000])
        label_texts = [call.kwargs.get("text") for call in label_mock.call_args_list]
        self.assertNotIn("ms", label_texts)

    def test_edit_action_global_module_ref_uses_global_config_dialog(self):
        action = {"type": "global_detect", "template": "images/m.png", "module_ref": True,
                  "module_category": "global", "action_id": "abc"}
        with package_patch('dialogs', 'TemplateRegionFormDialog') as form_class, \
             package_patch('dialogs', 'ModuleReferenceDelayDialog') as delay_dialog, \
             package_patch('dialogs', 'GlobalDetectDialog') as global_dialog:
            global_dialog.return_value.show.return_value = dict(action, jump_enabled=True)
            updated = edit_action(None, action)
        self.assertTrue(updated["jump_enabled"])
        form_class.assert_not_called()
        delay_dialog.assert_not_called()
        global_dialog.assert_called_once_with(None, action, jump=True, actions=None)

    def test_module_reference_dialog_saves_timing_and_result_branches(self):
        dialog = ModuleReferenceDelayDialog.__new__(ModuleReferenceDelayDialog)
        dialog.action = {
            "type": "image_match", "template": "images/m.png", "module_ref": True,
            "threshold": 0.91, "blocking": True, "action_id": "stable",
        }
        dialog.delay = Mock()
        dialog.delay.get.return_value = "600"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "900"
        dialog.blocking_timeout_enabled_var = Mock()
        dialog.blocking_timeout_enabled_var.get.return_value = True
        dialog.blocking_timeout_var = Mock()
        dialog.blocking_timeout_var.get.return_value = "4200"
        dialog.result_routes_enabled = True
        dialog.on_success = Mock()
        dialog.on_success.get.return_value = "jump"
        dialog.on_failure = Mock()
        dialog.on_failure.get.return_value = "end_current_script"
        dialog.success_target = Mock()
        dialog.success_target.get.return_value = "第 3 行"
        dialog.failure_target = Mock()
        dialog.failure_target.get.return_value = "第 2 行"
        dialog.jump_target_ids = {"第 3 行": "success-target", "第 2 行": "failure-target"}
        dialog.destroy = Mock()

        dialog.save()

        self.assertEqual(dialog.result["delay_ms"], 600)
        self.assertEqual(dialog.result["after_delay_ms"], 900)
        self.assertEqual(dialog.result["on_found"], "jump")
        self.assertEqual(dialog.result["found_jump_action_id"], "success-target")
        self.assertEqual(dialog.result["on_timeout"], "end_current_script")
        self.assertEqual(dialog.result["timeout_jump_action_id"], "failure-target")
        self.assertTrue(dialog.result.get("blocking_timeout_enabled"))
        self.assertEqual(dialog.result.get("blocking_timeout_ms"), 4200)
        self.assertEqual(dialog.result["threshold"], 0.91)
        self.assertEqual(dialog.result["action_id"], "stable")
        dialog.destroy.assert_called_once()

    def test_number_module_reference_dialog_saves_comparison_and_existing_routes(self):
        dialog = ModuleReferenceDelayDialog.__new__(ModuleReferenceDelayDialog)
        dialog.action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:number", "action_id": "stable",
        }
        dialog.delay = Mock(**{"get.return_value": "0"})
        dialog.after_delay = Mock(**{"get.return_value": "0"})
        dialog.result_routes_enabled = True
        dialog.number_routes_enabled = True
        dialog.expected_number = Mock(**{"get.return_value": "007"})
        dialog.on_success = Mock(**{"get.return_value": "jump"})
        dialog.on_failure = Mock(**{"get.return_value": "jump"})
        dialog.success_target = Mock(**{"get.return_value": "等于"})
        dialog.failure_target = Mock(**{"get.return_value": "不等于"})
        dialog.jump_target_ids = {"等于": "equal-target", "不等于": "other-target"}
        dialog.destroy = Mock()

        dialog.save()

        self.assertEqual(dialog.result["expected_number"], 7)
        self.assertEqual(dialog.result["found_jump_action_id"], "equal-target")
        self.assertEqual(dialog.result["timeout_jump_action_id"], "other-target")
        dialog.destroy.assert_called_once()

    def test_number_module_reference_dialog_rejects_invalid_comparison(self):
        dialog = ModuleReferenceDelayDialog.__new__(ModuleReferenceDelayDialog)
        dialog.action = {"type": "image_match", "module_ref": True}
        dialog.delay = Mock(**{"get.return_value": "0"})
        dialog.after_delay = Mock(**{"get.return_value": "0"})
        dialog.result_routes_enabled = True
        dialog.number_routes_enabled = True
        dialog.expected_number = Mock(**{"get.return_value": "abc"})
        dialog.destroy = Mock()
        with package_patch('dialogs', 'show_floating_notice') as notice:
            dialog.save()
        self.assertIn("比较数字无效", notice.call_args.args[1])
        dialog.destroy.assert_not_called()

    def test_module_reference_dialog_can_replace_module_and_keep_row_settings(self):
        dialog = ModuleReferenceDelayDialog.__new__(ModuleReferenceDelayDialog)
        dialog.action = {
            "type": "global_detect", "template": "images/old.png",
            "module_ref": True, "module_category": "global",
            "delay_ms": 600, "after_delay_ms": 900,
            "jump_row": 4, "jump_action_id": "target-row",
            "action_id": "stable-action",
        }
        dialog.module_name = Mock()
        replacement = {
            "type": "global_detect", "template": "images/new.png",
            "module_ref": True, "module_category": "script_global",
            "region_mode": "template", "region": [], "delay_ms": 0,
        }
        with package_patch('dialogs', 'ModulePickerDialog') as picker_class, \
             package_patch('dialogs', 'registered_module_object', return_value={'name': '新模块'}):
            picker_class.return_value.show.return_value = replacement
            dialog.replace_reference()

        picker_class.assert_called_once_with(dialog, categories=("script_global",))
        self.assertEqual(dialog.action["template"], "images/new.png")
        self.assertEqual(dialog.action["delay_ms"], 600)
        self.assertEqual(dialog.action["after_delay_ms"], 900)
        self.assertEqual(dialog.action["jump_row"], 4)
        self.assertEqual(dialog.action["jump_action_id"], "target-row")
        self.assertEqual(dialog.action["action_id"], "stable-action")
        dialog.module_name.set.assert_called_once_with("新模块")

    def test_edit_action_restart_workflow_opens_target_dialog(self):
        dialog = Mock()
        dialog.show.return_value = {
            "type": "restart_workflow", "restart_workflow_target_row": 5,
        }
        with package_patch('dialogs', 'RestartWorkflowTargetDialog', return_value=dialog) as dialog_class:
            updated = edit_action(None, {"type": "restart_workflow"})
        dialog_class.assert_called_once()
        self.assertEqual(updated["restart_workflow_target_row"], 5)
        self.assertEqual(updated["type"], "restart_workflow")

    def test_edit_action_restart_workflow_cancel_keeps_original(self):
        dialog = Mock()
        dialog.show.return_value = None
        with package_patch('dialogs', 'RestartWorkflowTargetDialog', return_value=dialog):
            updated = edit_action(None, {"type": "restart_workflow"})
        self.assertIsNone(updated)

    def test_segment_row_label_shows_restart_target_row(self):
        self.assertEqual(
            segment_row_label({"type": "restart_workflow"}),
            "重新执行工作流（默认跳转行）",
        )
        self.assertEqual(
            segment_row_label({"type": "restart_workflow", "restart_workflow_target_row": 4}),
            "重新执行工作流（跳转第 4 行）",
        )

    @staticmethod
    def _work_area(width=1920, height=1080, left=0, top=0):
        return {"left": left, "top": top, "width": width, "height": height}

    def _patch_work_area(self, area):
        """把弹窗定位用的显示器可用区域固定成给定值（不打桩就走真实 Win32）。"""
        patcher = package_patch('dialogs', 'monitor_work_area_for', return_value=area)
        patcher.start()
        self.addCleanup(patcher.stop)
        return area

    def _fit_dialog(self, reqw, reqh, area=None, parent=None):
        """构造一个用于 _fit_window_to_content 的 Mock 对话框。"""
        dialog = TemplateRegionManagerDialog.__new__(TemplateRegionManagerDialog)
        dialog.update_idletasks = Mock()
        dialog.winfo_reqwidth = Mock(return_value=reqw)
        dialog.winfo_reqheight = Mock(return_value=reqh)
        dialog.geometry = Mock()
        dialog.resizable = Mock()
        self._patch_work_area(self._work_area() if area is None else area)
        if parent is None:
            parent = Mock()
            parent.winfo_rootx.return_value = 100
            parent.winfo_rooty.return_value = 100
            parent.winfo_width.return_value = 1700
            parent.winfo_height.return_value = 920
        return dialog, parent

    def test_manager_fit_sizes_window_to_content_request(self):
        # 高 DPI（打包版）下内容需求高度超过固定 470，窗口必须按需求尺寸
        # 自适应，否则底部按钮行被挤出窗口（按钮完全看不见）。
        dialog, parent = self._fit_dialog(700, 620)
        dialog._fit_window_to_content(parent)
        size_call, pos_call = dialog.geometry.call_args_list
        self.assertEqual(size_call.args[0], "700x620")
        # 居中：x = 100 + (1700 - 700)//2 = 600；y = 100 + (920 - 620)//2 = 250
        self.assertEqual(pos_call.args[0], "+600+250")
        dialog.resizable.assert_called_once_with(True, True)

    def test_manager_fit_never_inflates_window_past_content(self):
        # 早先这里有个 1000x500 的兜底最小尺寸：内容只要 620x120 也会被撑到
        # 500 高，多出来的高度被 expand 的表格吃掉，看起来就是一大片空白。
        dialog, parent = self._fit_dialog(620, 120)
        dialog._fit_window_to_content(parent)
        size_call = dialog.geometry.call_args_list[0]
        self.assertEqual(size_call.args[0], "620x120")

    def test_manager_fit_clamps_size_to_monitor_work_area(self):
        # 显示器放不下时不越过可用区域（并给标题栏留余量）；可拉伸兜底保证按钮行可达。
        dialog, parent = self._fit_dialog(640, 900, self._work_area(640, 600))
        dialog._fit_window_to_content(parent)
        size_call = dialog.geometry.call_args_list[0]
        # 宽度 640 - 40 = 600；高度 600 - 40 = 560（px(40) 在 100% 缩放下就是 40）
        self.assertEqual(size_call.args[0], "600x560")
        dialog.resizable.assert_called_once_with(True, True)

    def test_manager_fit_clamps_position_inside_screen(self):
        # 父窗口位于屏幕右下角时，居中位置被限制在屏幕内。
        parent = Mock()
        parent.winfo_rootx.return_value = 1800
        parent.winfo_rooty.return_value = 1000
        parent.winfo_width.return_value = 800
        parent.winfo_height.return_value = 600
        dialog, _ = self._fit_dialog(700, 620)
        dialog._fit_window_to_content(parent)
        pos_call = dialog.geometry.call_args_list[1]
        # x = 1800 + (800-700)//2 = 1850 → 限制到 1920-700-40 = 1180
        # y = 1000 + (600-620)//2 = 990 → 限制到 1080-620-40 = 420
        self.assertEqual(pos_call.args[0], "+1180+420")

    def test_fit_window_to_content_uses_requested_content_size(self):
        # 窗口尺寸只跟内容需求走，调用方不再传最小尺寸。
        dialog, parent = self._fit_dialog(500, 180)
        fit_window_to_content(dialog, parent)
        size_call = dialog.geometry.call_args_list[0]
        self.assertEqual(size_call.args[0], "500x180")
        dialog.resizable.assert_called_once_with(True, True)

    def test_fit_window_to_content_can_align_module_form_to_screen_top(self):
        dialog, parent = self._fit_dialog(680, 900)
        fit_window_to_content(dialog, parent, align_top=True)
        size_call, pos_call = dialog.geometry.call_args_list
        self.assertEqual(size_call.args[0], "680x900")
        self.assertEqual(pos_call.args[0], "+610+0")

    def _declared_dialog(self, declared, reqw, reqh, fitted=False):
        """构造一个用于 ModalDialog._shrink_to_content 的 Mock 对话框。"""
        dialog = ModalDialog.__new__(ModalDialog)
        dialog._declared_size = declared
        if fitted:
            dialog._macroflow_fitted_to_content = True
        dialog.update_idletasks = Mock()
        dialog.winfo_reqwidth = Mock(return_value=reqw)
        dialog.winfo_reqheight = Mock(return_value=reqh)
        dialog.geometry = Mock()
        self._patch_work_area(self._work_area())
        master = Mock()
        master.winfo_rootx.return_value = 100
        master.winfo_rooty.return_value = 100
        master.winfo_width.return_value = 1700
        master.winfo_height.return_value = 920
        dialog.master = master
        return dialog

    def test_modal_dialog_shrinks_declared_size_down_to_content(self):
        # 声明 560x400 但内容只要 540x260：缩到内容尺寸，去掉底部那片空白。
        dialog = self._declared_dialog((560, 400), 540, 260)
        dialog._shrink_to_content()
        size_call, pos_call = dialog.geometry.call_args_list
        self.assertEqual(size_call.args[0], "540x260")
        # 居中：x = 100 + (1700 - 540)//2 = 680；y = 100 + (920 - 260)//2 = 430
        self.assertEqual(pos_call.args[0], "+680+430")

    def test_modal_dialog_never_grows_past_declared_size(self):
        # 内容比声明尺寸大时只缩不放：既有布局不动，高 DPI 下窗口也不会变大。
        dialog = self._declared_dialog((480, 300), 520, 380)
        dialog._shrink_to_content()
        self.assertEqual(dialog.geometry.call_args_list[0].args[0], "480x300")

    def test_modal_dialog_skips_shrink_when_window_was_already_fitted(self):
        # 可滚动表单的尺寸来自 content_*，不是自身 reqsize，不能再缩一次。
        dialog = self._declared_dialog((560, 400), 200, 120, fitted=True)
        dialog._shrink_to_content()
        dialog.geometry.assert_not_called()
        dialog.update_idletasks.assert_not_called()

    def test_fit_window_to_content_marks_window_as_fitted(self):
        dialog, parent = self._fit_dialog(500, 180)
        fit_window_to_content(dialog, parent)
        self.assertTrue(dialog._macroflow_fitted_to_content)

    def test_dialog_placement_keeps_negative_coordinate_monitor(self):
        # 左侧副屏（x 为负）上的弹窗必须留在那块屏上：早先用
        # max(0, min(x, screen_w - width)) 收敛，x 会被顶回 0，模块窗口就
        # 跑到另一块屏（笔记本屏）上去了。
        parent = Mock()
        parent.winfo_rootx.return_value = -1800
        parent.winfo_rooty.return_value = 200
        parent.winfo_width.return_value = 1600
        parent.winfo_height.return_value = 900
        area = self._work_area(1920, 1080, left=-1920, top=181)
        dialog, _ = self._fit_dialog(600, 400, area, parent)
        fit_window_to_content(dialog, parent)
        pos_call = dialog.geometry.call_args_list[1]
        # x = -1800 + (1600-600)//2 = -1300（可用区域内）；y = 200 + (900-400)//2 = 450
        self.assertEqual(pos_call.args[0], "+-1300+450")

    def test_clamp_to_work_area_keeps_window_inside_secondary_monitor(self):
        area = self._work_area(1920, 1080, left=-1920, top=181)
        # 右下越界 → 收回到可用区域内（含标题栏余量 40）
        self.assertEqual(
            dialog_module.clamp_to_work_area(area, 600, 400, 500, 900), (-640, 821),
        )
        # 左上越界 → 贴到该显示器左上角，而不是主屏的 (0, 0)
        self.assertEqual(
            dialog_module.clamp_to_work_area(area, 600, 400, -4000, -50), (-1920, 181),
        )

    def test_monitor_work_area_uses_the_parent_window_center(self):
        widget = Mock()
        widget.winfo_rootx.return_value = 10
        widget.winfo_rooty.return_value = 20
        widget.winfo_width.return_value = 100
        widget.winfo_height.return_value = 80
        area = self._work_area(1260, 840)
        with package_patch('dialogs', 'get_monitor_work_area_for_point', return_value=area) as probe:
            self.assertEqual(dialog_module.monitor_work_area_for(widget), area)
        probe.assert_called_once_with(60, 60)

    def test_monitor_work_area_falls_back_to_primary_when_lookup_fails(self):
        widget = Mock()
        widget.winfo_rootx.return_value = 10
        widget.winfo_rooty.return_value = 20
        widget.winfo_width.return_value = 100
        widget.winfo_height.return_value = 80
        widget.winfo_id.return_value = 0
        primary = self._work_area(1260, 840)
        with package_patch('dialogs', 'get_monitor_work_area_for_point', return_value=None), package_patch('dialogs', 'get_monitor_work_area_for_window', return_value=None), package_patch('dialogs', 'get_primary_screen_rect', return_value=primary):
            self.assertEqual(dialog_module.monitor_work_area_for(widget), primary)

    def test_dialog_placement_never_uses_screen_metrics(self):
        # 回归护栏：弹窗定位只看显示器可用区域，winfo_screenwidth/height 在多屏
        # 下是主屏尺寸，用一次就会把窗口锁在主屏。
        for target in (
            dialog_module.ModalDialog.__init__,
            dialog_module.ModalDialog._shrink_to_content,
            dialog_module.place_window_on_parent,
            dialog_module.fit_window_to_content,
            dialog_module.monitor_work_area_for,
        ):
            self.assertNotIn("winfo_screen", inspect.getsource(target))

    def test_module_form_resize_keeps_raw_pixels(self):
        # winfo_* 返回的已经是当前 DPI 下的像素，再乘一次 px() 会在 200% 缩放下
        # 把窗口放大一倍并顶出屏幕（切换识别方式时窗口突然跳大）。
        dialog = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        dialog.master = Mock()
        dialog.update_idletasks = Mock()
        dialog.winfo_width = Mock(return_value=620)
        dialog.winfo_height = Mock(return_value=400)
        dialog.winfo_x = Mock(return_value=50)
        dialog.winfo_y = Mock(return_value=60)
        dialog.geometry = Mock()
        dialog.body = Mock()
        dialog.body.winfo_reqwidth.return_value = 640
        dialog.body.winfo_reqheight.return_value = 300
        dialog._scrollbar = Mock()
        dialog._scrollbar.winfo_reqwidth.return_value = 20
        self._patch_work_area(self._work_area())
        dialog._resize_for_content()
        self.assertEqual(dialog.geometry.call_args.args[0], "660x400+50+60")

    def test_module_dialog_button_rows_are_packed_before_lists(self):
        # pack 按顺序分配空间：屏幕放不下时最后挂的控件先被裁。按钮行必须排在
        # 列表/页签之前（side="bottom"），否则窗口一被压扁就看不到按钮。
        for dialog_class, later in (
            (dialog_module.TemplateRegionManagerDialog, "self.notebook.pack("),
            (dialog_module.ModuleImageInventoryDialog, "list_frame.pack("),
            (dialog_module.ModuleReferenceDialog, "list_frame.pack("),
            (dialog_module.BatchModuleScriptDialog, "frame.pack("),
            (dialog_module.ModulePickerDialog, "notebook.pack("),
        ):
            source = inspect.getsource(dialog_class.__init__)
            self.assertLess(
                source.index('buttons.pack(side="bottom"'), source.index(later),
                f"{dialog_class.__name__} 的按钮行排在列表之后，会被挤出窗口",
            )

    def test_module_picker_tab_buttons_survive_shrinking(self):
        source = inspect.getsource(dialog_module.ModulePickerDialog._build_category_tab)
        self.assertLess(
            source.index('buttons.pack(side="bottom"'), source.index("list_frame.pack("),
        )

    def test_scrollable_window_sizes_after_a_layout_pass(self):
        # 表单挂在 Canvas 里，grid 的需求尺寸要等一次空闲布局才算出来：布局前
        # 读 body.winfo_reqwidth/reqheight 只会拿到 1×1，窗口会被设成一条缝
        # （用户报告「打开只显示一点点内容」）。
        state = {"laid_out": False}
        window = Mock()

        def mark_laid_out():
            state["laid_out"] = True

        window.update_idletasks.side_effect = mark_laid_out
        window.winfo_reqwidth.return_value = 1
        window.winfo_reqheight.return_value = 1
        window.geometry = Mock()
        window.resizable = Mock()
        body = Mock()
        body.winfo_reqwidth.side_effect = lambda: 620 if state["laid_out"] else 1
        body.winfo_reqheight.side_effect = lambda: 900 if state["laid_out"] else 1
        scrollbar = Mock()
        scrollbar.winfo_reqwidth.return_value = 17
        parent = Mock()
        parent.winfo_rootx.return_value = 100
        parent.winfo_rooty.return_value = 100
        parent.winfo_width.return_value = 1700
        parent.winfo_height.return_value = 920
        self._patch_work_area(self._work_area())

        dialog_module.fit_scrollable_window_to_content(window, parent, body, scrollbar)

        self.assertTrue(state["laid_out"], "读内容尺寸前必须先跑一次布局")
        self.assertEqual(window.geometry.call_args_list[0].args[0], "637x904")

    def test_scrollable_form_dialogs_size_from_live_content(self):
        # 这 6 个可滚动表单都必须走"先布局再定尺寸"的封装，不能再自己提前读
        # winfo_req*（读到 1×1 会让窗口缩成一条缝）。
        for dialog_class in (
            dialog_module.GlobalDetectDialog,
            dialog_module.TemplateRegionFormDialog,
            dialog_module.ModuleReferenceDelayDialog,
            dialog_module.ImageActionDialog,
            dialog_module.OcrActionDialog,
            dialog_module.OcrCompareActionDialog,
        ):
            source = inspect.getsource(dialog_class.__init__)
            self.assertIn(
                "fit_scrollable_window_to_content", source,
                f"{dialog_class.__name__} 未按布局后的内容尺寸定窗口",
            )
            self.assertNotIn(
                "content_width=body.winfo_reqwidth", source,
                f"{dialog_class.__name__} 提前读了尚未布局的内容尺寸",
            )

    def test_module_reference_dialog_keeps_a_scrollbar_for_long_lists(self):
        # 引用位置可能有几十处，列表必须能滚动。
        self.assertIn("ttk.Scrollbar", inspect.getsource(
            dialog_module.ModuleReferenceDialog.__init__,
        ))

    def test_row_recognition_result_covers_parent_monitor_without_fullscreen(self):
        # Tk 的 -fullscreen 在多屏下按主屏铺满，识别结果窗口会跑到另一块屏。
        source = inspect.getsource(dialog_module.RowRecognitionResultDialog.show)
        self.assertNotIn('"-fullscreen"', source)
        self.assertIn("monitor_work_area_for", source)

    def test_deferred_module_form_is_shown_once_after_layout(self):
        dialog = TemplateRegionFormDialog.__new__(TemplateRegionFormDialog)
        dialog._deferred_show = True
        # 该表单在 __init__ 里已按 content_* 定过尺寸，show() 不再走收缩分支。
        dialog._macroflow_fitted_to_content = True
        dialog.deiconify = Mock()
        dialog.update_idletasks = Mock()
        dialog.lift = Mock()
        dialog.focus_force = Mock()
        dialog.wait_window = Mock()
        dialog.result = None
        dialog.show()
        dialog.deiconify.assert_called_once()
        dialog.update_idletasks.assert_called_once()
        dialog.lift.assert_called_once()

if __name__ == '__main__':
    unittest.main()
