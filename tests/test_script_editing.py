"""脚本编辑：动作列表、保存 / 关闭、引用脚本、录制动作代码段。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_script_editing.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
from pathlib import Path
from macroflow.core.storage import BASE_DIR
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, call, patch
from macroflow.core.models import ACTION_ID_KEY, MacroScript, NEXT_WORKFLOW_STEP_TARGET_ID, RECORDED_INPUT_TYPE, SCRIPT_START_TARGET_ID, Workflow, ensure_action_ids
from macroflow.core.storage import display_path, load_script
from macroflow.ui.app.constants import RECORD_TOOLBAR_BUTTON_LABEL, SEGMENT_BAR
from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.app.summaries import action_summary, key_action_matches, set_matching_key_action_delays
from macroflow.ui.dialogs.actions import edit_action
from macroflow.ui.dialogs.app_dialogs import ScriptRefDialog
from macroflow.ui.dialogs.segments import RecordedInputDialog, SegmentEditorMixin
from tests.helpers.core import FakeSettingVar, FakeTree, FakeVar
from tests.helpers.core import FakeTree, FakeVar  # noqa: E402
from tests.helpers.ui import make_edit_app  # noqa: E402
from macroflow.ui.app.constants import ACTION_TREE_COLUMNS  # noqa: E402
from tests.helpers.patches import package_patch


class ScriptEditingTests(unittest.TestCase):
    def test_script_editor_add_actions_use_one_module_entry(self):
        labels = [label for label, _command, _style in MacroFlowApp._script_action_button_specs()]

        self.assertIn("▤ 模块", labels)
        self.assertNotIn("◈ 脚本全局", labels)
        self.assertNotIn("▤ 识别模块", labels)

    def test_script_editor_exposes_block_action(self):
        specs = MacroFlowApp._script_action_button_specs()

        self.assertIn(("⏸ 阻塞", "add_block", "AccentScriptTool.TButton"), specs)

    def test_script_editor_exposes_scroll_action(self):
        # 工具栏「滚轮」：spec 里的命令名必须能在实例上取到（否则按钮点不动）。
        specs = MacroFlowApp._script_action_button_specs()

        self.assertIn(("↕ 滚轮", "add_scroll", "ScriptTool.TButton"), specs)
        app = MacroFlowApp.__new__(MacroFlowApp)
        self.assertTrue(callable(getattr(app, "add_scroll")))

    def test_script_editor_exposes_record_action(self):
        # 工具栏要有录制入口：spec 里的命令名必须能在实例上取到（否则按钮点不动）。
        specs = MacroFlowApp._script_action_button_specs()
        record_specs = [spec for spec in specs if spec[0] == RECORD_TOOLBAR_BUTTON_LABEL]

        self.assertEqual(len(record_specs), 1)
        _label, command_name, _style = record_specs[0]
        app = MacroFlowApp.__new__(MacroFlowApp)
        self.assertTrue(callable(getattr(app, command_name)))

    def test_toolbar_record_inserts_one_folded_action(self):
        # 工具栏「录制」用的是与侧栏/F8 **同一条录制路径**（悬浮小窗照旧），
        # 只是录制结果折成一条动作插进来：已有脚本内容一个都不能动。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=False)
        app.worker = None
        app.toggle_record = Mock()
        app._insert_action = Mock()
        app._set_status = Mock()
        existing = [{"type": "delay", "ms": 5, "action_id": "keep"}]
        app.script = Mock(actions=list(existing))

        app._toggle_record_from_toolbar()

        # 复用整脚本录制的入口（from_ui=True 与侧栏按钮一致）。
        app.toggle_record.assert_called_once_with(from_ui=True)
        app._insert_action.assert_not_called()
        handler = app._pending_recorded_input
        self.assertTrue(callable(handler))

        handler([
            {"type": "key", "vk": 65, "name": "a", "down": True, "delay_ms": 0},
            {"type": "mouse_button", "button": "left", "down": True, "x": 5, "y": 6, "delay_ms": 120},
        ])

        app._insert_action.assert_called_once()
        inserted = app._insert_action.call_args.args[0]
        self.assertEqual(inserted["type"], RECORDED_INPUT_TYPE)
        self.assertEqual([s["type"] for s in inserted["steps"]], ["key", "mouse_button"])
        self.assertEqual([s["delay_ms"] for s in inserted["steps"]], [0, 120])
        # 每一步都有 id（打开后能选中/编辑），且原有脚本动作没被清空。
        self.assertTrue(all(step.get(ACTION_ID_KEY) for step in inserted["steps"]))
        self.assertEqual(app.script.actions, existing)

    def test_toolbar_record_does_nothing_when_already_recording(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=True)
        app.worker = None
        app.toggle_record = Mock()
        app._notify = Mock()

        app._toggle_record_from_toolbar()

        app.toggle_record.assert_not_called()
        app._notify.assert_called_once()

    def test_recorded_input_stop_does_not_replace_script_actions(self):
        # 停止时必须走 fold 分支：录到的动作交给调用方，不写进脚本动作列表
        # （整脚本录制才会替换 self.script.actions）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock()
        app.recorder.stop.return_value = [{"type": "key", "vk": 65, "down": True}]
        app.recorder.unsupported_buttons = set()
        app.recorder.limit_reached = False
        app.main_hidden_for_recording = False
        app._hide_recording_mini = Mock()
        app.record_button = Mock()
        app._log = Mock()
        app._notify = Mock()
        app._sound = Mock()
        app.script = Mock(actions=[{"type": "delay", "ms": 1, "action_id": "keep"}])
        received: list[list[dict]] = []
        app._pending_recorded_input = received.append
        app._recorded_input_log_label = "测试录制"

        app.stop_recording(discard_recent=False, sound=False)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0]["type"], "key")
        self.assertIsNone(app._pending_recorded_input)
        # 原有脚本动作原样保留。
        self.assertEqual(
            app.script.actions, [{"type": "delay", "ms": 1, "action_id": "keep"}],
        )

    def test_recorded_input_row_summary_counts_steps(self):
        kind, detail, _delay = action_summary({
            "type": RECORDED_INPUT_TYPE,
            "steps": [{"type": "key", "vk": 65}, {"type": "key", "vk": 66}],
        })

        self.assertIn("录制动作", kind)
        self.assertIn("共 2 步", detail)

    def test_recorded_input_steps_get_action_ids(self):
        action = {
            "type": RECORDED_INPUT_TYPE,
            "steps": [{"type": "key", "vk": 65}, {"type": "key", "vk": 66}],
        }

        ensure_action_ids([action])

        self.assertTrue(action.get(ACTION_ID_KEY))
        self.assertTrue(all(step.get(ACTION_ID_KEY) for step in action["steps"]))

    def test_add_block_inserts_special_action(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._insert_action = Mock()

        app.add_block()

        app._insert_action.assert_called_once_with({"type": "block", "delay_ms": 0})

    def test_run_script_worker_logs_and_passes_script_name(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._ui = Mock()
        app._sound = Mock()
        app._finish_execution_visibility = Mock()
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.ocr_engine_ready = True
        app.player = Mock()
        app.player.stop_event = threading.Event()

        app._run_script_worker(
            [{"type": "delay", "delay_ms": 10}], 1, None, None, False, None, False, 0,
            script_name="经典团战",
        )

        texts = [
            str(call.args[1]) if len(call.args) > 1 else ""
            for call in app._ui.call_args_list
        ]
        self.assertIn("开始执行脚本：经典团战，重复 1 次。", texts)
        self.assertEqual(app.player.play.call_args.kwargs["script_name"], "经典团战")

    def test_key_action_search_matches_key_and_state(self):
        self.assertTrue(key_action_matches(
            {"type": "key", "name": "A", "vk": 65, "down": True}, "a", "down",
        ))
        self.assertFalse(key_action_matches(
            {"type": "key", "name": "A", "vk": 65, "down": True}, "a", "up",
        ))
        self.assertTrue(key_action_matches(
            {"type": "key", "name": "A", "vk": 65, "down": False}, "65", "up",
        ))
        self.assertTrue(key_action_matches(
            {"type": "key_press", "name": "ENTER", "vk": 13}, "enter", "press",
        ))
        self.assertFalse(key_action_matches(
            {"type": "key_press", "name": "ENTER", "vk": 13}, "enter", "down",
        ))

    def test_key_action_search_matches_mouse_button_and_state(self):
        self.assertTrue(key_action_matches(
            {"type": "click", "button": "left"}, "左键", "Press",
        ))
        self.assertTrue(key_action_matches(
            {"type": "mouse_button", "button": "right", "down": False}, "right", "抬起",
        ))
        self.assertFalse(key_action_matches(
            {"type": "mouse_button", "button": "right", "down": True}, "右键", "抬起",
        ))

    def test_captured_search_type_does_not_cross_match_keyboard_and_mouse(self):
        key_release = {"type": "key", "name": "E", "vk": 69, "down": False}
        mouse_release = {"type": "mouse_button", "button": "left", "down": False}

        self.assertTrue(key_action_matches(
            key_release, "E", "抬起", query_kind="key",
        ))
        self.assertFalse(key_action_matches(
            mouse_release, "E", "抬起", query_kind="key",
        ))
        self.assertTrue(key_action_matches(
            mouse_release, "left", "抬起", query_kind="mouse",
        ))
        self.assertFalse(key_action_matches(
            key_release, "left", "抬起", query_kind="mouse",
        ))

    def test_captured_search_type_is_used_by_unified_delay_setting(self):
        actions = [
            {"type": "mouse_button", "button": "left", "down": False, "delay_ms": 10},
            {"type": "key", "name": "E", "vk": 69, "down": False, "delay_ms": 20},
        ]

        changed = set_matching_key_action_delays(
            actions, "E", "抬起", 120, query_kind="key",
        )

        self.assertEqual(changed, [1])
        self.assertEqual([action["delay_ms"] for action in actions], [10, 120])

    def test_captured_query_navigation_ignores_other_input_type(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[
            {"type": "mouse_button", "button": "left", "down": False},
            {"type": "key", "name": "E", "vk": 69, "down": False},
        ])
        app.key_search_state_var = Mock()
        app.key_search_state_var.get.return_value = "抬起"
        app.key_search_var = Mock()
        app.key_search_var.get.return_value = "E"
        app.key_search_match_var = Mock()
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app._key_search_query_kind = "key"
        app._selected_action_index = Mock(return_value=None)

        app._search_key_actions(1)

        app.key_search_match_var.set.assert_called_once_with("匹配 1 项")
        app.action_tree.selection_set.assert_called_with("1")

    def test_set_matching_key_action_delays_changes_only_search_matches(self):
        actions = [
            {"type": "key", "name": "A", "vk": 65, "down": True, "delay_ms": 10},
            {"type": "key", "name": "A", "vk": 65, "down": False, "delay_ms": 20},
            {"type": "key_press", "name": "A", "vk": 65, "delay_ms": 30, "hold_ms": 300},
        ]

        changed = set_matching_key_action_delays(actions, "A", "down", 120)

        self.assertEqual(changed, [0])
        self.assertEqual([action["delay_ms"] for action in actions], [120, 20, 30])
        self.assertEqual(actions[2]["hold_ms"], 300)

    def test_module_reference_keeps_script_edit_button_enabled_for_delays(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{
            "type": "image_match", "module_ref": True,
            "template": "images/shared.png",
        }])
        app.edit_action_button = Mock()
        app._selected_action_index = Mock(return_value=0)

        app._update_action_edit_button()

        app.edit_action_button.configure.assert_called_once_with(state="normal")

    def test_add_jump_action_inserts_dialog_result(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.script = MacroScript(actions=[{"type": "delay", "ms": 1}])
        app._insert_action = Mock()
        result = {
            "type": "jump", "jump_action_id": SCRIPT_START_TARGET_ID,
            "jump_row": 1, "delay_ms": 0,
        }
        with package_patch('app', 'JumpActionDialog') as dialog_class:
            dialog_class.return_value.show.return_value = result
            app.add_jump()
        dialog_class.assert_called_once_with(app.root, actions=app.script.actions)
        app._insert_action.assert_called_once_with(result)

    def test_editing_action_preserves_stable_identity(self):
        original = {"type": "key", "action_id": "stable-target", "name": "A"}
        with package_patch('dialogs', 'KeyActionDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "key_press", "name": "B", "vk": 66,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-target")

    def test_editing_text_action_uses_text_dialog(self):
        original = {"type": "text", "action_id": "stable-text", "text": "旧文本", "delay_ms": 0}
        with package_patch('dialogs', 'TextActionDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "text", "text": "新文本", "char_delay_ms": 20, "delay_ms": 1000,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-text")
        self.assertEqual(updated["delay_ms"], 1000)
        self.assertEqual(updated["text"], "新文本")
        dialog_class.assert_called_once()

    def test_editing_repeat_click_action_uses_repeat_click_dialog(self):
        original = {"type": "repeat_click", "action_id": "stable-repeat", "x": 1, "y": 2}
        with package_patch('dialogs', 'RepeatClickDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "repeat_click", "x": 9, "y": 9,
                "count": 3, "interval_ms": 50,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-repeat")
        self.assertEqual(updated["count"], 3)
        dialog_class.assert_called_once()

    def test_editing_open_app_action_uses_open_app_dialog(self):
        original = {"type": "open_app", "action_id": "stable-app", "path": "C:/old/app.exe"}
        with package_patch('dialogs', 'OpenAppDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "open_app", "path": "C:/new/app.exe",
                "delay_ms": 300, "after_delay_ms": 800,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-app")
        self.assertEqual(updated["path"], "C:/new/app.exe")
        dialog_class.assert_called_once()

    def test_script_ref_dialog_saves_repeat_count(self):
        dialog = ScriptRefDialog.__new__(ScriptRefDialog)
        dialog.script = Mock()
        dialog.script.get.return_value = "scripts/ref.json"
        dialog.repeats = Mock()
        dialog.repeats.get.return_value = "3"
        dialog.delay = Mock()
        dialog.delay.get.return_value = "0"
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = "0"
        dialog.destroy = Mock()

        dialog.save()

        self.assertEqual(dialog.result["repeats"], 3)
        dialog.destroy.assert_called_once()

    def test_editing_close_app_action_uses_close_app_dialog(self):
        original = {"type": "close_app", "action_id": "stable-close", "name": "old.exe"}
        with package_patch('dialogs', 'CloseAppDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "close_app", "name": "new.exe",
                "graceful": False, "graceful_wait_ms": 1000,
                "delay_ms": 0, "after_delay_ms": 0,
            }
            updated = edit_action(None, original)
        self.assertEqual(updated["action_id"], "stable-close")
        self.assertEqual(updated["name"], "new.exe")
        dialog_class.assert_called_once()

    def test_editing_jump_action_preserves_identity(self):
        original = {
            "type": "jump", "action_id": "stable-jump",
            "jump_action_id": SCRIPT_START_TARGET_ID,
        }
        with package_patch('dialogs', 'JumpActionDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "jump", "jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
                "jump_row": 3,
            }
            updated = edit_action(None, original, [original])
        self.assertEqual(updated["action_id"], "stable-jump")
        self.assertEqual(updated["jump_action_id"], NEXT_WORKFLOW_STEP_TARGET_ID)

    def test_row_list_condition_click_summary_describes_scan_and_conditions(self):
        kind, detail, _delay = action_summary({
            "type": "row_list_condition_click",
            "row_height": 27,
            "left_condition": {
                "type": "number", "separator": "/", "relation": "not_equal",
            },
            "right_condition": {
                "type": "text", "expected_text": "刚开始", "match_mode": "equals",
            },
            "no_match_action": "retry",
        })

        self.assertEqual(kind, "▤  列表逐行点击")
        self.assertIn("从上到下", detail)
        self.assertIn("行高 27", detail)
        self.assertIn("左:数字/数字（不相等）", detail)
        self.assertIn("右:文字:刚开始（完全相等）", detail)
        self.assertIn("首个匹配即点击", detail)
        self.assertIn("未命中:重试", detail)

    def test_row_list_condition_click_needs_ocr_unless_both_conditions_are_images(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        cases = (
            ("both_images", "image", "image", False),
            ("left_text", "text", "image", True),
            ("right_text", "image", "text", True),
            ("left_number", "number", "image", True),
            ("right_number", "image", "number", True),
        )

        for name, left_type, right_type, expected in cases:
            with self.subTest(name=name):
                self.assertIs(
                    app._script_needs_ocr([{
                        "type": "row_list_condition_click",
                        "left_condition": {"type": left_type},
                        "right_condition": {"type": right_type},
                    }]),
                    expected,
                )

    def test_add_row_list_condition_click_assigns_new_action_identity(self):
        app = make_edit_app()
        app._selected_action_index = Mock(return_value=None)
        result = {
            "type": "row_list_condition_click",
            "left_condition": {"type": "image"},
            "right_condition": {"type": "image"},
        }

        with package_patch('app', 'RowListConditionClickDialog') as dialog_class, \
             package_patch('app', 'new_action_id', return_value='new-row-list-id'):
            dialog_class.return_value.show.return_value = result
            app.add_row_list_condition_click()

        dialog_class.assert_called_once_with(app.root, actions=app.script.actions)
        self.assertEqual(app.script.actions, [{
            "type": "row_list_condition_click",
            "left_condition": {"type": "image"},
            "right_condition": {"type": "image"},
            "action_id": "new-row-list-id",
        }])
        self.assertEqual(app.action_tree.get_children(), ("0",))

    def test_editing_row_list_condition_click_opens_its_dialog_and_preserves_identity(self):
        original = {
            "type": "row_list_condition_click", "action_id": "stable-row-list",
            "left_condition": {"type": "image"},
            "right_condition": {"type": "image"},
        }
        with package_patch('dialogs', 'RowListConditionClickDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "row_list_condition_click",
                "left_condition": {"type": "text", "expected_text": "就绪"},
                "right_condition": {"type": "number", "separator": "/"},
            }
            updated = edit_action(None, original)

        dialog_class.assert_called_once_with(None, original, actions=None)
        self.assertEqual(updated["action_id"], "stable-row-list")
        self.assertEqual(updated["left_condition"]["type"], "text")

    def test_run_script_from_selected_action_uses_first_selected_row(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.selection.return_value = ("4", "2")
        app.run_current_script = Mock()
        app._notify = Mock()

        app.run_script_from_selected_action()

        app.run_current_script.assert_called_once_with(start_index=2)
        app._notify.assert_not_called()

    def test_ctrl_a_selects_all_script_actions(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.get_children.return_value = ("0", "1", "2")

        result = app._select_all_actions()

        self.assertEqual(result, "break")
        app.action_tree.selection_set.assert_called_once_with("0", "1", "2")
        app.action_tree.focus.assert_called_once_with("0")
        app.action_tree.see.assert_called_once_with("0")

    def test_run_script_worker_keeps_detecting_for_global_script(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._ui = Mock()
        app._finish_execution_visibility = Mock()
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.exiting = False
        app._evaluating_guards = False
        app.ocr_engine_ready = True
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app._activate_global_detect_from_config = Mock()
        thread = threading.Thread(
            target=app._run_script_worker,
            args=([{"type": "delay", "delay_ms": 10}], 1, None, None, False, None, False, 0),
            kwargs={"trigger": {"template": "images/g.png", "hold_ms": 500}},
            daemon=True,
        )
        thread.start()
        time.sleep(0.2)
        # 播放已结束但保持"检测中"，未显示"执行完成"。
        app.player.play.assert_called_once()
        activation_call = app._activate_global_detect_from_config.call_args
        self.assertEqual(activation_call.args[0], {"template": "images/g.png", "hold_ms": 500})
        self.assertEqual(
            activation_call.kwargs["standalone_replay"]["actions"],
            [{"type": "delay", "delay_ms": 10}],
        )
        # 语句体参数已存档，供触发后回放。
        self.assertEqual(app.standalone_global_replay["actions"], [{"type": "delay", "delay_ms": 10}])
        texts = [
            str(call.args[1]) if len(call.args) > 1 else ""
            for call in app._ui.call_args_list
        ]
        self.assertTrue(any("全局检测已启用，持续检测中" in text for text in texts))
        self.assertFalse(any("脚本执行完成" in text for text in texts))
        self.assertTrue(thread.is_alive())
        app.player.stop_event.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(app.standalone_global_replay)

    def test_run_script_worker_finishes_normal_script(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._ui = Mock()
        app._sound = Mock()
        app._finish_execution_visibility = Mock()
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.ocr_engine_ready = True
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app._run_script_worker(
            [{"type": "delay", "delay_ms": 10}], 1, None, None, False, None, False, 0,
        )
        app.player.play.assert_called_once()
        self.assertTrue(app._ui.called)
        texts = [
            str(call.args[1]) if len(call.args) > 1 else ""
            for call in app._ui.call_args_list
        ]
        self.assertTrue(any("脚本执行完成" in text for text in texts))

    def test_edit_global_trigger_stores_config_without_type(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript()
        app.root = Mock()
        app._mark_dirty = Mock()
        app._sync_global_script_marker = Mock()
        app._set_status = Mock()
        with package_patch('app', 'GlobalDetectDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "global_detect", "template": "images/g.png",
                "threshold": 0.85, "interval_ms": 500, "hold_ms": 1000,
                "click_point": None, "restart_delay_ms": 0,
            }
            app._edit_global_trigger()
        trigger = app.script.settings["trigger"]
        self.assertNotIn("type", trigger)
        self.assertEqual(trigger["template"], "images/g.png")
        dialog_class.assert_called_once_with(app.root, {}, require_click=False)
        app._mark_dirty.assert_called_once()
        app._sync_global_script_marker.assert_called_once()
        app._set_status.assert_called_once()

    def test_edit_global_trigger_cancel_keeps_previous_config(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript()
        app.script.settings["trigger"] = {"template": "images/old.png"}
        app.root = Mock()
        app._mark_dirty = Mock()
        with package_patch('app', 'GlobalDetectDialog') as dialog_class:
            dialog_class.return_value.show.return_value = None
            app._edit_global_trigger()
        self.assertEqual(app.script.settings["trigger"]["template"], "images/old.png")
        app._mark_dirty.assert_not_called()

    def test_clear_global_trigger_removes_config_and_marks_dirty(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript()
        app.script.settings["trigger"] = {"template": "images/g.png"}
        app._mark_dirty = Mock()
        app._sync_global_script_marker = Mock()
        app._clear_global_trigger()
        self.assertNotIn("trigger", app.script.settings)
        app._mark_dirty.assert_called_once()
        app._sync_global_script_marker.assert_called_once()

    def test_delete_selected_actions_updates_script_and_can_be_undone(self):
        app = make_edit_app(actions=[
            {"type": "comment", "text": "A"},
            {"type": "comment", "text": "B"},
            {"type": "comment", "text": "C"},
        ])
        app.rebuild_action_tree()
        app.action_tree.selection_set("1")
        app.delete_actions()
        self.assertEqual([action["text"] for action in app.script.actions], ["A", "C"])
        self.assertTrue(app.action_undo_stack, "删除必须留下可撤销记录")
        app._mark_dirty.assert_called_once()
        # 行真被删掉、行号跟着重排（不是只改了数据）。
        self.assertEqual(app.action_tree.get_children(), ("0", "1"))
        self.assertEqual(app.action_tree.value("1", "detail"), "C")
        self.assertEqual(app.action_tree.value("1", "index"), 2)
        app._set_status.assert_called_once()

    def test_delete_last_action_selects_new_last_action(self):
        app = make_edit_app(actions=[
            {"type": "comment", "text": "A"},
            {"type": "comment", "text": "B"},
        ])
        app.rebuild_action_tree()
        app.action_tree.selection_set("1")

        app.delete_actions()

        self.assertEqual([action["text"] for action in app.script.actions], ["A"])
        self.assertEqual(app.action_tree.get_children(), ("0",))
        self.assertEqual(app.action_tree.selection(), ("0",))
        self.assertEqual(app.action_tree.focus(), "0")

    def test_insert_script_below_selected_row_stores_reference(self):
        app = make_edit_app(actions=[
            {"type": "comment", "text": "A"},
            {"type": "comment", "text": "B"},
        ])
        app.rebuild_action_tree()
        app.action_tree.selection_set("0")
        inserted = MacroScript(actions=[
            {"type": "comment", "text": "C1"},
            {"type": "comment", "text": "C2"},
        ])
        with patch("tkinter.filedialog.askopenfilenames", return_value=("C:/scripts/C.json",)), \
             package_patch('app', 'load_script', return_value=inserted):
            app._insert_script(False)
        self.assertEqual(len(app.script.actions), 3)
        ref = app.script.actions[1]
        self.assertEqual(ref["type"], "script_ref")
        self.assertEqual(ref["script"], str(Path("C:/scripts/C.json").resolve()))
        self.assertEqual(ref["repeats"], 1)
        self.assertTrue(ref.get("action_id"))
        app._mark_dirty.assert_called_once()
        self.assertEqual(app.action_tree.get_children(), ("0", "1", "2"))
        self.assertEqual(app.action_tree.selection(), ("1",))

    def test_insert_script_reference_accepts_multiple_selected_files_in_order(self):
        app = make_edit_app(actions=[{"type": "comment", "text": "A"}])
        app.rebuild_action_tree()
        app.action_tree.selection_set("0")
        paths = ("C:/scripts/first.json", "C:/scripts/second.json")

        with patch("tkinter.filedialog.askopenfilename", return_value=""), \
             patch("tkinter.filedialog.askopenfilenames", return_value=paths), \
             package_patch('app', 'load_script', return_value=MacroScript()):
            app._insert_script(False)

        self.assertEqual(len(app.script.actions), 3)
        self.assertEqual(
            [action["script"] for action in app.script.actions[1:]],
            [str(Path(paths[0]).resolve()), str(Path(paths[1]).resolve())],
        )
        self.assertEqual([action["type"] for action in app.script.actions[1:]], [
            "script_ref", "script_ref",
        ])
        self.assertEqual(app.action_tree.get_children(), ("0", "1", "2"))
        self.assertEqual(app.action_tree.selection(), ("1", "2"))

    def test_insert_script_expanded_accepts_multiple_selected_files_in_order(self):
        app = make_edit_app()
        app.rebuild_action_tree()
        paths = ("C:/scripts/first.json", "C:/scripts/second.json")
        scripts = [
            MacroScript(actions=[{"type": "comment", "text": "first"}]),
            MacroScript(actions=[{"type": "comment", "text": "second"}]),
        ]

        with patch("tkinter.filedialog.askopenfilename", return_value=""), \
             patch("tkinter.filedialog.askopenfilenames", return_value=paths), \
             package_patch('app', 'load_script', side_effect=scripts * 2):
            app._insert_script(True)

        self.assertEqual(
            [action["text"] for action in app.script.actions], ["first", "second"],
        )
        self.assertEqual(app.action_tree.get_children(), ("0", "1"))
        self.assertEqual(app.action_tree.selection(), ("0", "1"))

    def test_insert_script_into_empty_script_allowed(self):
        app = make_edit_app()
        app.rebuild_action_tree()
        inserted = MacroScript(actions=[{"type": "comment", "text": "C"}])
        with patch("tkinter.filedialog.askopenfilenames", return_value=("C:/scripts/C.json",)), \
             package_patch('app', 'load_script', return_value=inserted):
            app._insert_script(False)
        self.assertEqual(len(app.script.actions), 1)
        self.assertEqual(app.script.actions[0]["type"], "script_ref")
        self.assertEqual(app.script.actions[0]["script"], str(Path("C:/scripts/C.json").resolve()))
        self.assertEqual(app.action_tree.get_children(), ("0",))
        self.assertEqual(app.action_tree.selection(), ("0",))
        app._notify.assert_called_once()

    def test_insert_script_requires_selection_when_actions_exist(self):
        app = make_edit_app(actions=[{"type": "comment", "text": "A"}])
        app.rebuild_action_tree()
        with patch("tkinter.filedialog.askopenfilenames") as picker:
            app._insert_script(False)
        picker.assert_not_called()
        app._notify.assert_called_once()

    def test_insert_action_above_selected_row(self):
        app = make_edit_app(actions=[
            {"type": "comment", "text": "A"},
            {"type": "comment", "text": "B"},
        ])
        app.rebuild_action_tree()
        app.insert_position_var = Mock()
        app.insert_position_var.get.return_value = "above"
        app.action_tree.selection_set("1")
        app._insert_action({"type": "comment", "text": "C"})
        self.assertEqual([action["text"] for action in app.script.actions], ["A", "C", "B"])
        self.assertEqual(app.action_tree.get_children(), ("0", "1", "2"))
        self.assertEqual(app.action_tree.value("1", "detail"), "C")
        self.assertEqual(app.action_tree.value("2", "detail"), "B")
        self.assertEqual(app.action_tree.value("2", "index"), 3)
        self.assertEqual(app.action_tree.selection(), ("1",))

    def test_insert_action_below_selected_row(self):
        app = make_edit_app(actions=[{"type": "comment", "text": "A"}])
        app.rebuild_action_tree()
        app.insert_position_var = Mock()
        app.insert_position_var.get.return_value = "below"
        app.action_tree.selection_set("0")
        app._insert_action({"type": "comment", "text": "C"})
        self.assertEqual([action["text"] for action in app.script.actions], ["A", "C"])
        self.assertEqual(app.action_tree.get_children(), ("0", "1"))
        self.assertEqual(app.action_tree.value("1", "detail"), "C")

    def test_insert_above_with_no_selection_goes_to_top(self):
        app = make_edit_app(actions=[{"type": "comment", "text": "A"}])
        app.rebuild_action_tree()
        app.insert_position_var = Mock()
        app.insert_position_var.get.return_value = "above"
        app._insert_action({"type": "comment", "text": "C"})
        self.assertEqual([action["text"] for action in app.script.actions], ["C", "A"])
        self.assertEqual(app.action_tree.value("0", "detail"), "C")
        self.assertEqual(app.action_tree.value("1", "detail"), "A")

    def test_insert_script_above_selected_row_stores_reference(self):
        app = make_edit_app(actions=[
            {"type": "comment", "text": "A"},
            {"type": "comment", "text": "B"},
        ])
        app.rebuild_action_tree()
        app.root = Mock()
        app.insert_position_var = Mock()
        app.insert_position_var.get.return_value = "above"
        app.action_tree.selection_set("1")
        inserted = MacroScript(actions=[{"type": "comment", "text": "C"}])
        with patch("tkinter.filedialog.askopenfilenames", return_value=("C:/scripts/C.json",)), \
             package_patch('app', 'load_script', return_value=inserted):
            app._insert_script(False)
        self.assertEqual(len(app.script.actions), 3)
        self.assertEqual(app.script.actions[1]["type"], "script_ref")
        self.assertEqual(app.script.actions[1]["script"], str(Path("C:/scripts/C.json").resolve()))
        self.assertEqual(app.action_tree.get_children(), ("0", "1", "2"))

    def test_insert_script_expanded_copies_rows_and_remaps_jump_ids(self):
        app = make_edit_app(actions=[
            {"type": "comment", "text": "A"},
            {"type": "comment", "text": "B"},
        ])
        app.rebuild_action_tree()
        app.action_tree.selection_set("0")
        inserted = MacroScript(actions=[
            {"type": "comment", "text": "C1", "action_id": "src1"},
            {"type": "comment", "text": "C2", "action_id": "src2", "jump_action_id": "src1"},
            {"type": "image_match", "text": "img", "action_id": "src3",
             "timeout_jump_action_id": "src2", "found_jump_action_id": "src3"},
        ])
        with patch("tkinter.filedialog.askopenfilenames", return_value=("C:/scripts/C.json",)), \
             package_patch('app', 'load_script', return_value=inserted):
            app._insert_script(True)
        self.assertEqual(len(app.script.actions), 5)
        inserted_actions = app.script.actions[1:4]
        self.assertEqual([action["text"] for action in inserted_actions], ["C1", "C2", "img"])
        # 行 ID 全部重建，不残留源脚本 ID
        new_ids = {action["action_id"] for action in inserted_actions}
        self.assertEqual(len(new_ids), 3)
        self.assertNotIn("src1", new_ids)
        self.assertNotIn("src2", new_ids)
        self.assertNotIn("src3", new_ids)
        # 跳转引用映射到新 ID：C2 跳 C1、img 超时跳 C2、img 找到后跳自己
        c1_id = inserted_actions[0]["action_id"]
        c2_id = inserted_actions[1]["action_id"]
        img = inserted_actions[2]
        self.assertEqual(img["jump_action_id"] if "jump_action_id" in img else None, None)
        self.assertEqual(inserted_actions[1]["jump_action_id"], c1_id)
        self.assertEqual(img["timeout_jump_action_id"], c2_id)
        self.assertEqual(img["found_jump_action_id"], img["action_id"])
        # 源脚本对象未被修改
        self.assertEqual(inserted.actions[0]["action_id"], "src1")
        # 插入的是逐行动作而非 script_ref
        self.assertNotEqual(app.script.actions[1]["type"], "script_ref")
        app._mark_dirty.assert_called_once()
        app._notify.assert_called_once()

    def test_insert_script_expanded_migrates_legacy_jump_row(self):
        # 旧版脚本的 jump_row（无 action_id）插入后必须迁移为指向插入块内
        # 对应行的 jump_action_id，否则跳转会带着源脚本相对行号错位。
        app = make_edit_app(actions=[{"type": "comment", "text": "主"}])
        app.rebuild_action_tree()
        app.action_tree.selection_set("0")
        app.root = Mock()
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.selection.return_value = ("0",)
        app._mark_dirty = Mock()
        app._notify = Mock()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({
                "name": "Ref",
                "actions": [
                    {"type": "comment", "text": "R1"},
                    {"type": "comment", "text": "R2"},
                    {"type": "jump", "jump_row": 2},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            with patch("tkinter.filedialog.askopenfilenames", return_value=(str(ref),)):
                app._insert_script(True)
        inserted = app.script.actions[1:4]
        self.assertEqual([action.get("text") for action in inserted], ["R1", "R2", None])
        jump = inserted[2]
        self.assertEqual(jump["jump_action_id"], inserted[1][ACTION_ID_KEY])

    def test_insert_script_above_requires_selection_when_actions_exist(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "comment", "text": "A"}])
        app.root = Mock()
        app.insert_position_var = Mock()
        app.insert_position_var.get.return_value = "above"
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.selection.return_value = ()
        app._notify = Mock()
        with patch("tkinter.filedialog.askopenfilenames") as picker:
            app._insert_script(True)
        picker.assert_not_called()
        app._notify.assert_called_once()

    def test_open_new_window_launches_second_instance(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._log = Mock()
        app._set_status = Mock()
        app._notify = Mock()
        with patch("subprocess.Popen") as popen, \
             patch("sys.executable", "C:/Python313/python.exe"), \
             patch("sys.frozen", False, create=True), \
             package_patch('app', '__file__', 'E:/proj/app.py'):
            app.open_new_window()
        popen.assert_called_once()
        args = popen.call_args.args[0]
        self.assertEqual(
            args,
            ["C:/Python313/python.exe", str(Path("E:/proj/app.py")), "--new-script"],
        )
        self.assertEqual(popen.call_args.kwargs["cwd"], str(BASE_DIR))
        app._log.assert_called_once()

    def test_script_category_can_change_back_from_global(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "delay", "ms": 1}])
        app.script.settings["category"] = "global"
        app.script_category_var = Mock()
        app.script_category_var.get.return_value = "关卡"
        app.global_script_marker = Mock()
        app._mark_dirty = Mock()
        app._set_status = Mock()
        app._script_category_changed()
        self.assertEqual(app.script.settings["category"], "level")
        self.assertFalse(app.script.is_global)
        app.global_script_marker.configure.assert_called_with(text="")
        app.script_category_var.set.assert_not_called()

    def test_script_category_not_locked_by_module_row(self):
        # v1.68：普通脚本内嵌全局模块行（global_detect + jump_row）不再强制类别为全局。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[
            {"type": "global_detect", "template": "x.png", "jump_row": 3},
        ])
        app.script.settings["category"] = "level"
        app.script_category_var = Mock()
        app.script_category_var.get.return_value = "关卡"
        app.global_script_marker = Mock()
        app._mark_dirty = Mock()
        app._set_status = Mock()
        app._script_category_changed()
        self.assertEqual(app.script.settings["category"], "level")
        self.assertFalse(app.script.is_global)
        app.script_category_var.set.assert_not_called()
        app.global_script_marker.configure.assert_called_with(text="")

    def test_add_global_detect_inserts_module_row(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "delay", "ms": 1}])
        app.root = Mock()
        app._insert_action = Mock()
        app._notify = Mock()
        with package_patch('app', 'GlobalDetectDialog') as dialog_class:
            dialog_class.return_value.show.return_value = {
                "type": "global_detect", "template": "images/g.png",
                "jump_row": 3, "jump_action_id": "target-a",
                "click_point": None, "restart_delay_ms": 0,
            }
            app.add_global_detect()
        # v1.70：跳转目标从脚本行列表中选择，对话框拿到全部动作。
        dialog_class.assert_called_once_with(
            app.root, jump=True, actions=app.script.actions,
        )
        app._insert_action.assert_called_once()
        action = app._insert_action.call_args.args[0]
        self.assertEqual(action["type"], "global_detect")
        self.assertEqual(action["jump_row"], 3)
        self.assertEqual(action["jump_action_id"], "target-a")
        app._notify.assert_not_called()

    def test_add_global_detect_refuses_for_global_script(self):
        # 全局脚本的触发条件在"触发条件"区块配置，不能添加模块行。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[], settings={"trigger": {"template": "g.png"}})
        app._insert_action = Mock()
        app._notify = Mock()
        app.add_global_detect()
        app._notify.assert_called_once()
        app._insert_action.assert_not_called()

    def test_add_module_inserts_switch_module_ref(self):
        # 切换模块引用：直接插入，无需补跳转行。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "delay", "ms": 1}])
        app.root = Mock()
        app._insert_action = Mock(return_value=0)
        app._default_global_jump = Mock()
        app._notify = Mock()
        action = {
            "type": "image_match", "template": "images/s.png",
            "module_ref": True, "module_category": "switch",
            "region_mode": "template", "region": [], "delay_ms": 0,
        }
        with package_patch('app', 'ModulePickerDialog') as picker_class:
            picker_class.return_value.show.return_value = action
            app.add_module()
        picker_class.assert_called_once_with(
            app.root, actions=app.script.actions, multi_select=True,
        )
        app._insert_action.assert_called_once_with(action)
        app._default_global_jump.assert_not_called()
        app._notify.assert_not_called()

    def test_add_module_inserts_multiple_selected_modules_in_order(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[])
        app.root = Mock()
        app._insert_action = Mock()
        app._notify = Mock()
        first = {
            "type": "image_match", "template": "images/first.png",
            "module_ref": True, "module_category": "switch",
        }
        second = {
            "type": "image_match", "template": "images/second.png",
            "module_ref": True, "module_category": "switch",
        }
        with package_patch('app', 'ModulePickerDialog') as picker_class:
            picker_class.return_value.show.return_value = [first, second]
            app.add_module()

        picker_class.assert_called_once_with(
            app.root, actions=app.script.actions, multi_select=True,
        )
        self.assertEqual(
            app._insert_action.call_args_list,
            [call(first), call(second)],
        )
        app._notify.assert_not_called()

    def test_add_module_global_ref_configures_jump_per_inserted_action(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "delay", "ms": 1, "action_id": "target"}])
        app.root = Mock()
        app._insert_action = Mock(return_value=0)
        app._default_global_jump = Mock()
        app._notify = Mock()
        raw_action = {
            "type": "global_detect", "template": "images/g.png",
            "module_ref": True, "module_category": "script_global",
            "region_mode": "template", "region": [], "delay_ms": 0,
        }
        configured = dict(raw_action, jump_enabled=True, jump_row=2, jump_action_id="target")
        with package_patch('app', 'ModulePickerDialog') as picker_class, \
             package_patch('app', 'GlobalDetectDialog') as dialog_class:
            picker_class.return_value.show.return_value = raw_action
            dialog_class.return_value.show.return_value = configured
            app.add_module()

        dialog_class.assert_called_once_with(
            app.root, raw_action, jump=True, actions=app.script.actions,
        )
        app._insert_action.assert_called_once_with(configured)
        app._default_global_jump.assert_not_called()

    def test_add_number_module_configures_comparison_before_inserting(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "delay", "ms": 1, "action_id": "target"}])
        app.root = Mock()
        app._insert_action = Mock(return_value=1)
        app._default_global_jump = Mock()
        app._notify = Mock()
        raw_action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:number", "template": "",
            "module_category": "switch", "region_mode": "template",
        }
        configured = dict(
            raw_action, expected_number=7, on_found="jump",
            found_jump_action_id="target", on_timeout="jump",
            timeout_jump_action_id="target",
        )
        with package_patch('app', 'ModulePickerDialog') as picker_class, \
             package_patch('app', 'registered_module_object', return_value={'recognize': 'number'}), \
             package_patch('app', 'edit_action', return_value=configured) as edit:
            picker_class.return_value.show.return_value = raw_action
            app.add_module()
        edit.assert_called_once_with(app.root, raw_action, all_actions=app.script.actions)
        app._insert_action.assert_called_once_with(configured)

    def test_add_module_special_insert_does_not_add_global_jump(self):
        # 特殊模块是固定动作，不应被当作全局识别模块自动补跳转。
        app = make_edit_app(actions=[
            {"type": "delay", "ms": 1, "action_id": "a1"},
            {"type": "delay", "ms": 2, "action_id": "a2"},
            {"type": "delay", "ms": 3, "action_id": "a3"},
        ])
        app.rebuild_action_tree()
        app._selected_action_index = Mock(return_value=0)
        action = {
            "type": "global_detect", "template": "images/g.png",
            "module_ref": True, "module_category": "special",
            "region_mode": "template", "region": [], "delay_ms": 0,
        }
        with package_patch('app', 'ModulePickerDialog') as picker_class:
            picker_class.return_value.show.return_value = action
            app.add_module()
        inserted = app.script.actions[1]
        self.assertNotIn("jump_row", inserted)
        self.assertNotIn("jump_action_id", inserted)
        self.assertEqual(inserted["template"], "images/g.png")

    def test_add_module_special_end_insert_does_not_add_global_jump(self):
        # 特殊模块在脚本末尾插入也不应生成识别跳转字段。
        app = make_edit_app(actions=[
            {"type": "delay", "ms": 1, "action_id": "a1"},
            {"type": "delay", "ms": 2, "action_id": "a2"},
        ])
        app.rebuild_action_tree()
        app._selected_action_index = Mock(return_value=1)
        action = {
            "type": "global_detect", "template": "images/g.png",
            "module_ref": True, "module_category": "special",
            "region_mode": "template", "region": [], "delay_ms": 0,
        }
        with package_patch('app', 'ModulePickerDialog') as picker_class:
            picker_class.return_value.show.return_value = action
            app.add_module()
        inserted = app.script.actions[2]
        self.assertNotIn("jump_row", inserted)
        self.assertNotIn("jump_action_id", inserted)

    def test_add_module_refuses_global_module_in_global_script(self):
        # 全局脚本不能插入全局模块引用（触发条件在区块配置）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.script = MacroScript(actions=[], settings={"trigger": {"template": "g.png"}})
        app._insert_action = Mock()
        app._notify = Mock()
        action = {
            "type": "global_detect", "template": "images/g.png",
            "module_ref": True, "module_category": "global",
            "region_mode": "template", "region": [], "delay_ms": 0,
        }
        with package_patch('app', 'ModulePickerDialog') as picker_class:
            picker_class.return_value.show.return_value = action
            app.add_module()
        app._notify.assert_called_once()
        app._insert_action.assert_not_called()

    def test_undo_restores_actions_before_last_edit(self):
        app = make_edit_app(actions=[{"type": "delay", "ms": 10}])
        app.rebuild_action_tree()
        app.action_tree.selection_set("1")

        app._checkpoint_action_edit()
        app.script.actions.append({"type": "delay", "ms": 20})
        app._undo_redo_action_edit(False)

        self.assertEqual([action["ms"] for action in app.script.actions], [10])
        self.assertEqual(app.action_undo_stack, [])
        # 撤销后当前状态（含刚追加的动作）进重做栈，可恢复。
        self.assertEqual(
            [[action["ms"] for action in snapshot] for _kind, snapshot in app.action_redo_stack],
            [[10, 20]],
        )
        app.undo_button.configure.assert_called_with(state="disabled")
        app.redo_button.configure.assert_called_with(state="normal")

    def test_redo_restores_actions_after_undo(self):
        app = make_edit_app(actions=[{"type": "delay", "ms": 10}])
        app.rebuild_action_tree()
        app.action_undo_stack = []
        app.action_redo_stack = [("whole", [{"type": "delay", "ms": 20}])]
        app.action_tree.selection_set("0")

        app._undo_redo_action_edit(True)

        self.assertEqual([action["ms"] for action in app.script.actions], [20])
        self.assertEqual(app.action_redo_stack, [])
        # 重做把当前状态压回撤销栈，可再撤销。
        self.assertEqual(
            [[action["ms"] for action in snapshot] for _kind, snapshot in app.action_undo_stack],
            [[10]],
        )
        app.undo_button.configure.assert_called_with(state="normal")
        app.redo_button.configure.assert_called_with(state="disabled")

    def test_redo_empty_stack_does_nothing(self):
        app = make_edit_app(actions=[{"type": "delay", "ms": 10}])
        app.rebuild_action_tree()
        app.action_redo_stack = []

        app._undo_redo_action_edit(True)

        self.assertEqual([action["ms"] for action in app.script.actions], [10])
        app._mark_dirty.assert_not_called()
        app.redo_button.configure.assert_called_with(state="disabled")

    def test_new_edit_clears_redo_stack(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[{"type": "delay", "ms": 10}])
        app.action_undo_stack = [("whole", [{"type": "delay", "ms": 5}])]
        app.action_redo_stack = [("whole", [{"type": "delay", "ms": 10}])]
        app.undo_button = Mock()
        app.redo_button = Mock()

        app.script.actions.append({"type": "delay", "ms": 30})
        app._checkpoint_action_edit()

        # 撤销后改动作：重做栈作废。
        self.assertEqual(app.action_redo_stack, [])
        self.assertEqual(len(app.action_undo_stack), 2)
        app.redo_button.configure.assert_called_with(state="disabled")

    def test_successful_save_clears_and_disables_undo(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(name="测试", actions=[{"type": "delay", "ms": 10}])
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "测试"
        app.script_path = Path("测试.json")
        app.script_requires_new_file = False
        app.action_undo_stack = [[{"type": "delay", "ms": 5}]]
        app.undo_button = Mock()
        app._current_script_settings = Mock(return_value={})
        app._refresh_coordinate_scale_status = Mock()
        app.refresh_script_files = Mock()
        app._set_status = Mock()
        app._log = Mock()
        app.script_category_var = Mock()
        app.script_category_var.get.return_value = "关卡"
        app.workflow_tree = None
        for name in ("_level_scripts_dir", "_level_pack_scripts_dir",
                     "_switch_scripts_dir", "_global_scripts_dir"):
            setattr(app, name, lambda: Path("."))

        with package_patch('app', 'save_script', return_value=Path('测试.json')):
            app.save_current_script()

        self.assertEqual(app.action_undo_stack, [])
        app.undo_button.configure.assert_called_with(state="disabled")

    def _save_app(self, folder: Path, category: str) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._notify = Mock()
        app._log = Mock()
        app._set_status = Mock()
        app._clear_action_undo = Mock()
        app.refresh_script_files = Mock()
        app.script = MacroScript(name="A", actions=[])
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "A"
        app.script_category_var = Mock()
        app.script_category_var.get.return_value = category
        app.script_requires_new_file = False
        app.dirty = False
        app.action_undo_stack = []
        app.workflow_tree = None
        app._current_script_settings = Mock(return_value={})
        app._refresh_coordinate_scale_status = Mock()
        level_dir = folder / "level"
        level_pack_dir = folder / "level_pack"
        level_dir.mkdir(exist_ok=True)
        level_pack_dir.mkdir(exist_ok=True)
        app._level_scripts_dir = lambda: level_dir
        app._level_pack_scripts_dir = lambda: level_pack_dir
        app._switch_scripts_dir = lambda: level_dir
        return app

    def test_save_after_category_change_moves_file_to_new_category_dir(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            app = self._save_app(Path(folder), "关卡封装")
            level_dir = Path(folder) / "level"
            level_pack_dir = Path(folder) / "level_pack"
            original = level_dir / "A.json"
            original.write_text("{}", encoding="utf-8")
            app.script_path = original
            with package_patch('app', 'save_script', return_value=level_pack_dir / 'A.json') as save:
                result = app.save_current_script()
        self.assertEqual(result, level_pack_dir / "A.json")
        self.assertFalse(original.exists())
        app._set_status.assert_called_once_with(
            "已保存并移动到 level_pack/A.json", "success")

    def test_save_same_category_keeps_file_in_place(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            app = self._save_app(Path(folder), "关卡")
            level_dir = Path(folder) / "level"
            original = level_dir / "A.json"
            original.write_text("{}", encoding="utf-8")
            app.script_path = original
            with package_patch('app', 'save_script', return_value=original):
                app.save_current_script()
            self.assertTrue(original.exists())
            app._set_status.assert_called_once_with("已保存 A.json", "success")

    def test_save_after_category_change_with_name_collision_uses_new_name(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            app = self._save_app(Path(folder), "关卡封装")
            level_dir = Path(folder) / "level"
            level_pack_dir = Path(folder) / "level_pack"
            original = level_dir / "A.json"
            original.write_text("{}", encoding="utf-8")
            conflict = level_pack_dir / "A.json"
            conflict.write_text("另一个脚本", encoding="utf-8")
            app.script_path = original
            with package_patch('app', 'save_script', return_value=level_pack_dir / 'A (2).json'):
                result = app.save_current_script()
            self.assertEqual(result, level_pack_dir / "A (2).json")
            self.assertFalse(original.exists())
            self.assertTrue(conflict.exists())

    def test_save_move_failure_keeps_original_file(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            app = self._save_app(Path(folder), "关卡封装")
            level_dir = Path(folder) / "level"
            original = level_dir / "A.json"
            original.write_text("{}", encoding="utf-8")
            app.script_path = original
            with package_patch('app', 'save_script', side_effect=RuntimeError('磁盘已满')):
                result = app.save_current_script()
            self.assertIsNone(result)
            self.assertTrue(original.exists())
            app._notify.assert_called_once_with("保存失败", "磁盘已满")

    def test_save_after_rename_removes_old_file(self):
        # 改名保存会留下孤儿旧文件（工作流/引用仍指向陈旧内容）——修复：
        # 与类别变化分支一致，保存成功后删除旧文件。
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            app = self._save_app(Path(folder), "关卡")
            level_dir = Path(folder) / "level"
            old = level_dir / "A.json"
            old.write_text("{}", encoding="utf-8")
            app.script_path = old
            app.script_name_var.get.return_value = "B"
            app.dirty = False
            with package_patch('app', 'save_script', side_effect=lambda _script, path: path) as save:
                result = app.save_current_script()
            self.assertEqual(result, level_dir / "B.json")
            self.assertFalse(old.exists())
            self.assertTrue(save.called)

    def test_save_after_direction_rename_updates_hotkey_bindings(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            app = self._save_app(Path(folder), "方向")
            direction_dir = Path(folder) / "direction"
            direction_dir.mkdir()
            app._direction_scripts_dir = lambda: direction_dir
            old = direction_dir / "A.json"
            old.write_text("{}", encoding="utf-8")
            app.script_path = old
            app.script_name_var.get.return_value = "B"
            app.hotkey_scripts = [{
                "key": "J", "vk": 74, "script": display_path(old),
            }]
            app._apply_hotkey_bindings = Mock()
            app._refresh_hotkey_summary = Mock()
            app._persist_sidebar_settings = Mock(return_value=True)

            with package_patch('app', 'save_script', side_effect=lambda _script, path: path):
                result = app.save_current_script()

            new = direction_dir / "B.json"
            self.assertEqual(result, new)
            self.assertEqual(app.hotkey_scripts[0]["script"], display_path(new))
            app._apply_hotkey_bindings.assert_called_once_with()
            app._refresh_hotkey_summary.assert_called_once_with()
            app._persist_sidebar_settings.assert_called_once_with()

    def test_save_current_script_blocked_during_recording(self):
        # 录制中的动作在 recorder.actions 里，编辑器列表为空：保存会写出
        # 永远为空内容的“已保存”文件——必须拦截。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock()
        app.recorder.running = True
        app._notify = Mock()
        self.assertIsNone(app.save_current_script())
        app._notify.assert_called_once()

    def test_copy_contiguous_actions_inserts_after_selection(self):
        app = make_edit_app(actions=[
            {"type": "key", "vk": 65, "meta": {"name": "A"}},
            {"type": "delay", "ms": 100},
            {"type": "key", "vk": 66},
        ])
        app.rebuild_action_tree()
        app.action_tree.selection_set("0", "1")

        app.copy_selected_actions_down()

        self.assertEqual([action["type"] for action in app.script.actions], [
            "key", "delay", "key", "delay", "key",
        ])
        self.assertEqual(app.script.actions[2]["vk"], 65)
        self.assertIsNot(app.script.actions[2], app.script.actions[0])
        self.assertIsNot(app.script.actions[2]["meta"], app.script.actions[0]["meta"])
        self.assertEqual(app.action_tree.selection(), ("2", "3",))

    def test_copy_rejects_non_contiguous_selection(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script = MacroScript(actions=[
            {"type": "delay", "ms": 1},
            {"type": "delay", "ms": 2},
            {"type": "delay", "ms": 3},
        ])
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.selection.return_value = ("0", "2")
        app.root = Mock()
        app._notify = Mock()
        app._mark_dirty = Mock()

        app.copy_selected_actions_down()

        self.assertEqual(len(app.script.actions), 3)
        app._mark_dirty.assert_not_called()
        app._notify.assert_called_once()

    @staticmethod
    def _move_app(selected, count: int = 6) -> MacroFlowApp:
        app = make_edit_app(actions=[
            {"type": "delay", "ms": index} for index in range(count)
        ])
        app.rebuild_action_tree()
        app.action_tree.selection_set(*selected)
        return app

    def test_move_action_moves_contiguous_block_up(self):
        # 连续多选：整块上移一行，块内顺序不变（不是只挪动第一行）。
        app = self._move_app(("4", "2", "3"))

        app.move_action(-1)

        self.assertEqual([action["ms"] for action in app.script.actions], [0, 2, 3, 4, 1, 5])
        self.assertEqual(app.action_tree.selection(), ("1", "2", "3",))
        app._mark_dirty.assert_called_once()
        app._notify.assert_not_called()
        app._set_status.assert_called_once()

    def test_move_action_moves_contiguous_block_down(self):
        app = self._move_app(("1", "2"))

        app.move_action(1)

        self.assertEqual([action["ms"] for action in app.script.actions], [0, 3, 1, 2, 4, 5])
        self.assertEqual(app.action_tree.selection(), ("2", "3",))

    def test_move_action_single_row_swaps_with_neighbour(self):
        app = self._move_app(("1",))

        app.move_action(-1)

        self.assertEqual([action["ms"] for action in app.script.actions], [1, 0, 2, 3, 4, 5])
        self.assertEqual(app.action_tree.selection(), ("0",))
        app._set_status.assert_not_called()

    def test_move_action_rejects_non_contiguous_selection(self):
        app = self._move_app(("0", "2"))

        app.move_action(1)

        self.assertEqual([action["ms"] for action in app.script.actions], [0, 1, 2, 3, 4, 5])
        app._notify.assert_called_once()
        app._mark_dirty.assert_not_called()

    def test_move_action_stops_at_script_edges(self):
        for selected, offset in ((("0",), -1), (("0", "1"), -1), (("3", "4", "5"), 1)):
            with self.subTest(selected=selected, offset=offset):
                app = self._move_app(selected)

                app.move_action(offset)

                self.assertEqual(
                    [action["ms"] for action in app.script.actions], [0, 1, 2, 3, 4, 5],
                )
                app._mark_dirty.assert_not_called()


class _EditorCategoryVar:
    """set()/get() 桩，模拟类别下拉框的 Tk StringVar（不依赖 Tk 根窗口）。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class CloseScriptTests(unittest.TestCase):
    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._notify = Mock()
        app._log = Mock()
        app._set_status = Mock()
        app._refresh_coordinate_scale_status = Mock()
        app._sync_activation_ui_from_script = Mock()
        app._blank_script_with_activation_draft = Mock(return_value=MacroScript())
        app.script_name_var = Mock()
        app.interval_var = Mock()
        app.script_category_var = Mock()
        app.record_mode_var = Mock()
        # 行刷新走真实实现（FakeTree），所以这里必须有动作列表控件。
        app.action_tree = FakeTree(ACTION_TREE_COLUMNS)
        app.empty_action_hint = Mock()
        app.global_script_marker = Mock()
        app.edit_action_button = Mock()
        app.record_count_var = FakeVar("")
        app.undo_button = Mock()
        app.redo_button = Mock()
        app.undo_open_button = Mock()
        return app

    def test_close_script_snapshots_and_clears_editor(self):
        app = self._app()
        app.script = MacroScript(name="A", actions=[{"type": "delay", "delay_ms": 100}])
        app.script_path = Path("C:/x/A.json")
        app.script_requires_new_file = False
        app.dirty = True
        app.action_undo_stack = [{"type": "delay"}]
        app.undo_open_stack = []
        app.undo_open_button = Mock()
        app.close_script()
        self.assertEqual(len(app.undo_open_stack), 1)
        snap = app.undo_open_stack[0]
        self.assertEqual(snap["script"].name, "A")
        self.assertEqual(len(snap["script"].actions), 1)
        self.assertEqual(snap["script_path"], Path("C:/x/A.json"))
        self.assertTrue(snap["dirty"])
        self.assertIsInstance(app.script, MacroScript)
        self.assertEqual(app.script.name, "未命名脚本")
        self.assertIsNone(app.script_path)
        self.assertFalse(app.dirty)
        app.undo_open_button.configure.assert_called_with(state="normal")

    def test_undo_open_restores_closed_script(self):
        app = self._app()
        app.script = MacroScript(name="A", actions=[{"type": "delay", "delay_ms": 100}])
        app.script_path = Path("C:/x/A.json")
        app.script_requires_new_file = False
        app.dirty = True
        app.action_undo_stack = [{"type": "delay"}]
        app.undo_open_stack = []
        app.undo_open_button = Mock()
        app.close_script()
        app.undo_open_script()
        self.assertEqual(app.script.name, "A")
        self.assertEqual(len(app.script.actions), 1)
        self.assertEqual(app.script_path, Path("C:/x/A.json"))
        self.assertTrue(app.dirty)
        self.assertEqual(app.action_undo_stack, [{"type": "delay"}])
        self.assertEqual(app.undo_open_stack, [])
        app.undo_open_button.configure.assert_called_with(state="disabled")

    def test_close_snapshot_isolated_from_later_edits(self):
        app = self._app()
        app.script = MacroScript(name="A", actions=[{"type": "delay", "delay_ms": 100}])
        app.script_path = None
        app.script_requires_new_file = False
        app.dirty = False
        app.action_undo_stack = []
        app.undo_open_stack = []
        app.close_script()
        app.script.actions.append({"type": "click", "x": 1, "y": 2})
        self.assertEqual(len(app.undo_open_stack[0]["script"].actions), 1)

    def test_new_script_clears_undo_open_stack(self):
        app = self._app()
        app.script = MacroScript()
        app.dirty = False
        app.undo_open_stack = [{"x": 1}]
        app.new_script()
        self.assertEqual(app.undo_open_stack, [])

    def test_opening_script_clears_undo_open_stack(self):
        app = self._app()
        app.script = MacroScript()
        app.dirty = False
        app.undo_open_stack = [{"x": 1}]
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({"name": "Ref", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            app.load_script_into_editor(ref)
        self.assertEqual(app.undo_open_stack, [])
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            path = Path(folder) / "b.json"
            path.write_text(json.dumps({"name": "B", "actions": []}, ensure_ascii=False),
                            encoding="utf-8")
            with package_patch('app', 'load_script', return_value=MacroScript(name='B')):
                app.load_script_into_editor(path)
        self.assertEqual(app.undo_open_stack, [])
        self.assertEqual(app.script.name, "b")

    def test_load_script_into_editor_asks_before_discarding_when_dirty(self):
        # 打开脚本会替换编辑器内容并清空“撤销打开”栈：未保存的修改必须先问清楚
        # （保存后打开 / 放弃修改打开 / 取消），不能只在屏幕上飘一条提示就返回——
        # 文件对话框关了、脚本没换，用户看到的就是“双击打开没反应”。
        app = self._app()
        app.root = Mock()
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "旧脚本"
        app.script = MacroScript(name="旧脚本", actions=[{"type": "delay", "ms": 1}])
        app.dirty = True
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({"name": "Ref", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            with patch("tkinter.messagebox.askyesnocancel",
                       return_value=None) as ask:
                self.assertFalse(app.load_script_into_editor(ref))
            ask.assert_called_once()
        # 取消：编辑器与未保存标记都原样保留，也没有再飘提示。
        self.assertEqual(app.script.name, "旧脚本")
        self.assertTrue(app.dirty)
        app._notify.assert_not_called()

    def test_open_script_loads_after_user_discards_unsaved_edits(self):
        app = self._app()
        app.root = Mock()
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "旧脚本"
        app.script = MacroScript(name="旧脚本", actions=[{"type": "delay", "ms": 1}])
        app.dirty = True
        app.undo_open_stack = [{"x": 1}]
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "目标.json"
            ref.write_text(json.dumps({"name": "目标", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            with patch("tkinter.messagebox.askyesnocancel", return_value=False):
                self.assertTrue(app.load_script_into_editor(ref))
        self.assertEqual(app.script.name, "目标")
        self.assertFalse(app.dirty)
        self.assertEqual(app.undo_open_stack, [])

    def test_open_script_saves_first_when_user_answers_yes(self):
        app = self._app()
        app.root = Mock()
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "旧脚本"
        app.script = MacroScript(name="旧脚本")
        app.dirty = True
        app.save_current_script = Mock(return_value=Path("C:/x/旧脚本.json"))
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "目标.json"
            ref.write_text(json.dumps({"name": "目标", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            with patch("tkinter.messagebox.askyesnocancel", return_value=True):
                self.assertTrue(app.load_script_into_editor(ref))
        app.save_current_script.assert_called_once()
        self.assertEqual(app.script.name, "目标")

    def test_open_script_stays_when_saving_fails(self):
        # 选了“保存后打开”但保存没成功（失败或被取消）时不能继续打开：
        # 否则未保存的修改会被静默丢掉。
        app = self._app()
        app.root = Mock()
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "旧脚本"
        app.script = MacroScript(name="旧脚本", actions=[{"type": "delay", "ms": 1}])
        app.dirty = True
        app.save_current_script = Mock(return_value=None)
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "目标.json"
            ref.write_text(json.dumps({"name": "目标", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            with patch("tkinter.messagebox.askyesnocancel", return_value=True):
                self.assertFalse(app.load_script_into_editor(ref))
        self.assertEqual(app.script.name, "旧脚本")
        self.assertTrue(app.dirty)

    def test_open_script_without_unsaved_edits_never_asks(self):
        app = self._app()
        app.dirty = False
        app.undo_open_stack = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "干净.json"
            ref.write_text(json.dumps({"name": "干净", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            with patch("tkinter.messagebox.askyesnocancel") as ask:
                self.assertTrue(app.load_script_into_editor(ref))
        ask.assert_not_called()
        self.assertEqual(app.script.name, "干净")

    def test_new_script_asks_before_discarding_unsaved_edits(self):
        app = self._app()
        app.root = Mock()
        app.script_name_var = Mock()
        app.script_name_var.get.return_value = "旧脚本"
        app.script = MacroScript(name="旧脚本", actions=[{"type": "delay", "ms": 1}])
        app.dirty = True
        app.undo_open_stack = [{"x": 1}]
        with patch("tkinter.messagebox.askyesnocancel", return_value=None):
            app.new_script()
        self.assertEqual(app.script.name, "旧脚本")
        with patch("tkinter.messagebox.askyesnocancel", return_value=False):
            app.new_script()
        self.assertEqual(app.script.name, "未命名脚本")
        self.assertFalse(app.dirty)
        app._notify.assert_not_called()

    def test_workflow_step_open_delegates_unsaved_edits_to_loader(self):
        # 工作流右键「在当前编辑器打开」以前自己又拦一道、只飘提示；
        # 现在统一交给 load_script_into_editor 问一次。
        app = self._app()
        app.dirty = True
        app.load_script_into_editor = Mock(return_value=True)
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "步骤脚本.json"
            ref.write_text(json.dumps({"name": "步骤脚本", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            app._open_workflow_script_in_editor({"script": str(ref)})
        app.load_script_into_editor.assert_called_once_with(ref)
        app._notify.assert_not_called()

    def test_opening_script_shows_script_category(self):
        # 打开脚本后“类别”下拉框必须显示这个脚本的类别：以脚本所在目录为准
        # （保存时按类别进目录），JSON 里旧的/缺失的 category 不能把它顶掉。
        app = self._app()
        app.undo_open_stack = []
        app.undo_open_button = Mock()
        app.script_category_var = _EditorCategoryVar("关卡")
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            switch_dir = Path(folder) / "切换"
            switch_dir.mkdir()
            # 类别由脚本所在目录决定：把“切换”目录指向临时目录。
            app.app_settings = {"switch_scripts_dir": str(switch_dir)}
            for name, settings in (
                ("无类别.json", {}),
                ("类别过期.json", {"category": "level"}),
            ):
                path = switch_dir / name
                path.write_text(
                    json.dumps(
                        {"name": name, "actions": [], "settings": settings},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                app.dirty = False
                app.script_category_var.set("关卡")
                app.load_script_into_editor(path)
                self.assertEqual(app.script_category_var.get(), "切换")
            # 不在任何脚本目录里的脚本仍按自己保存的类别显示。
            outside = Path(folder) / "外部.json"
            outside.write_text(
                json.dumps(
                    {"name": "外部", "actions": [], "settings": {"category": "direction"}},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            app.dirty = False
            app.script_category_var.set("关卡")
            app.load_script_into_editor(outside)
            self.assertEqual(app.script_category_var.get(), "方向")

    def test_restore_editor_draft_shows_script_category_from_directory(self):
        app = self._app()
        app.app_settings = {}
        app.dirty = False
        app.undo_open_stack = []
        app.undo_open_button = Mock()
        app.script_category_var = _EditorCategoryVar("关卡")
        draft = {
            "script": MacroScript(name="切换脚本").to_dict(),
            "script_path": str(BASE_DIR / "scripts" / "切换" / "切换脚本.json"),
            "script_requires_new_file": False,
            "dirty": False,
        }
        self.assertTrue(app._restore_editor_draft(draft))
        self.assertEqual(app.script_category_var.get(), "切换")

    def test_opening_script_uses_current_filename_when_json_name_is_stale(self):
        app = self._app()
        app.script = MacroScript(name="旧名称")
        app.dirty = False
        app.undo_open_stack = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            path = Path(folder) / "修改后的名称.json"
            path.write_text(json.dumps({"name": "旧名称", "actions": []}, ensure_ascii=False),
                            encoding="utf-8")
            app.load_script_into_editor(path)
        self.assertEqual(app.script.name, "修改后的名称")
        app.script_name_var.set.assert_called_with("修改后的名称")


class ScriptRefWindowTests(unittest.TestCase):
    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._notify = Mock()
        app._log = Mock()
        app._set_status = Mock()
        app.load_script_into_editor = Mock()
        return app

    def test_open_referenced_script_launches_new_window_with_flag(self):
        app = self._app()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({"name": "Ref", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            with patch("subprocess.Popen") as popen:
                app.open_referenced_script_in_new_window(
                    {"type": "script_ref", "script": str(ref)})
            popen.assert_called_once()
            args = popen.call_args.args[0]
            self.assertIn("--open-script", args)
            self.assertEqual(args[args.index("--open-script") + 1], str(ref))
            self.assertEqual(popen.call_args.kwargs["cwd"], str(BASE_DIR))
        app._set_status.assert_called_once()

    def test_open_referenced_script_missing_file_notifies_without_launch(self):
        app = self._app()
        with patch("subprocess.Popen") as popen:
            app.open_referenced_script_in_new_window(
                {"type": "script_ref", "script": "C:/no_such_dir/ref.json"})
        popen.assert_not_called()
        app._notify.assert_called_once_with("引用脚本不存在", "找不到文件：C:/no_such_dir/ref.json")

    def test_open_referenced_script_empty_path_notifies(self):
        app = self._app()
        with patch("subprocess.Popen") as popen:
            app.open_referenced_script_in_new_window({"type": "script_ref", "script": "  "})
        popen.assert_not_called()
        app._notify.assert_called_once_with("引用脚本无效", "该引用动作没有脚本路径。")

    def test_context_menu_offered_on_script_ref_rows_with_open_window_item(self):
        app = self._app()
        app.root = Mock()
        app.script = MacroScript(actions=[
            {"type": "script_ref", "script": "scripts/ref.json"},
            {"type": "comment", "text": "备注"},
        ])
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.identify_row.return_value = "0"
        event = Mock()
        event.y, event.x_root, event.y_root = 20, 100, 120
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(event)
        menu_class.assert_called_once()
        menu = menu_class.return_value
        labels = [call.kwargs["label"] for call in menu.add_command.call_args_list]
        self.assertEqual(labels, [
            "▶ 从此行开始运行",
            "▶ 单独执行此动作…",
            "⇪ 在新窗口打开引用的脚本",
        ])
        menu.add_separator.assert_called_once()
        menu.tk_popup.assert_called_once_with(100, 120)
        menu.grab_release.assert_called_once()

    def test_context_menu_offered_on_every_action_row(self):
        app = self._app()
        app.root = Mock()
        app.script = MacroScript(actions=[
            {"type": "comment", "text": "备注"},
            {"type": "delay", "ms": 100},
        ])
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.identify_row.return_value = "1"
        event = Mock()
        event.y, event.x_root, event.y_root = 20, 100, 120
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(event)
        menu = menu_class.return_value
        labels = [call.kwargs["label"] for call in menu.add_command.call_args_list]
        self.assertEqual(labels, ["▶ 从此行开始运行", "▶ 单独执行此动作…"])
        # 普通动作没有“在新窗口打开引用的脚本”，也不画分隔线。
        menu.add_separator.assert_not_called()

    def test_context_menu_selects_the_right_clicked_row(self):
        app = self._app()
        app.root = Mock()
        app.script = MacroScript(actions=[
            {"type": "comment", "text": "备注"},
            {"type": "delay", "ms": 100},
        ])
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.identify_row.return_value = "1"
        app.run_current_script = Mock()
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(Mock())
        app.action_tree.selection_set.assert_called_with("1")
        command = menu_class.return_value.add_command.call_args_list[0].kwargs["command"]
        command()
        app.run_current_script.assert_called_once_with(start_index=1)

    def test_context_menu_single_action_asks_for_count(self):
        app = self._app()
        app.root = Mock()
        app.script = MacroScript(actions=[{"type": "comment", "text": "备注"}])
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.identify_row.return_value = "0"
        app.run_single_action_with_count = Mock()
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(Mock())
        command = menu_class.return_value.add_command.call_args_list[1].kwargs["command"]
        command()
        app.run_single_action_with_count.assert_called_once_with(0)

    def test_context_menu_skipped_outside_rows(self):
        app = self._app()
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ()
        app.action_tree.identify_row.return_value = ""
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(Mock())
        menu_class.assert_not_called()

    def test_context_menu_offers_segment_item_for_a_selected_range(self):
        app = self._app()
        app.root = Mock()
        app.script = MacroScript(actions=[
            {"type": "comment", "text": "一"},
            {"type": "delay", "ms": 10},
            {"type": "comment", "text": "三"},
            {"type": "comment", "text": "四"},
        ])
        app.action_tree = Mock()
        app.action_tree.identify_row.return_value = "2"
        # 右键落在已选中的一段里：保留这段选中，片段就是它。
        app.action_tree.selection.return_value = ("1", "2", "3")
        app.run_action_segment = Mock()
        event = Mock()
        event.y, event.x_root, event.y_root = 20, 100, 120
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(event)
        app.action_tree.selection_set.assert_not_called()
        menu = menu_class.return_value
        labels = [call.kwargs["label"] for call in menu.add_command.call_args_list]
        self.assertEqual(labels, [
            "▶ 从此行开始运行",
            "▶ 单独执行此动作…",
            "▶ 循环执行片段…",
        ])
        command = menu.add_command.call_args_list[2].kwargs["command"]
        command()
        app.run_action_segment.assert_called_once_with()

    def test_context_menu_keeps_the_range_when_right_clicking_inside_it(self):
        app = self._app()
        app.root = Mock()
        app.script = MacroScript(actions=[
            {"type": "comment", "text": "一"},
            {"type": "delay", "ms": 10},
        ])
        app.action_tree = Mock()
        app.action_tree.identify_row.return_value = "0"
        app.action_tree.selection.return_value = ("0", "1")
        with patch("tkinter.Menu") as menu_class:
            app._show_action_context_menu(Mock())
        app.action_tree.selection_set.assert_not_called()
        labels = [call.kwargs["label"] for call in menu_class.return_value.add_command.call_args_list]
        self.assertIn("▶ 循环执行片段…", labels)

    def test_run_action_segment_prompts_with_default_one(self):
        app = self._app()
        app.root = Mock()
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ("1", "3")
        app.run_current_script = Mock()
        with patch("tkinter.simpledialog.askinteger", return_value=5) as ask:
            app.run_action_segment()
        self.assertEqual(ask.call_args.kwargs["initialvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["minvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["maxvalue"], 999999)
        self.assertIs(ask.call_args.kwargs["parent"], app.root)
        app.run_current_script.assert_called_once_with(segment=(1, 3), segment_repeats=5)

    def test_run_action_segment_without_a_range_notifies(self):
        app = self._app()
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ("1",)
        app.run_current_script = Mock()
        with patch("tkinter.simpledialog.askinteger") as ask:
            app.run_action_segment()
        app._notify.assert_called_once()
        ask.assert_not_called()
        app.run_current_script.assert_not_called()

    def test_run_action_segment_cancel_runs_nothing(self):
        app = self._app()
        app.root = Mock()
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ("0", "2")
        app.run_current_script = Mock()
        with patch("tkinter.simpledialog.askinteger", return_value=None):
            app.run_action_segment()
        app.run_current_script.assert_not_called()

    def test_action_tree_segment_bar_uses_the_left_column(self):
        from tkinter import ttk

        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.withdraw()
        tree = ttk.Treeview(
            root, columns=("mark", "index", "kind", "detail", "delay"),
            show="headings", selectmode="extended",
        )
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.action_tree = tree
        app.action_segment_painted = None
        app.script = MacroScript(actions=[
            {"type": "comment", "text": "一"},
            {"type": "delay", "ms": 10},
            {"type": "click", "x": 1, "y": 2},
        ])
        app.empty_action_hint = Mock()
        app.record_count_var = Mock()
        app._sync_global_script_marker = Mock()
        app._update_action_edit_button = Mock()

        app.rebuild_action_tree()

        self.assertEqual([tree.set(str(i), "index") for i in range(3)], ["1", "2", "3"])
        self.assertEqual([tree.set(str(i), "mark") for i in range(3)], ["", "", ""])

        tree.selection_set("0", "1")
        app._refresh_action_segment_bar()

        self.assertEqual(
            [tree.set(str(i), "mark") for i in range(3)], [SEGMENT_BAR, SEGMENT_BAR, ""],
        )

    def test_segment_bar_paints_and_clears_the_selected_range(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.action_segment_painted = None
        app.action_tree = Mock()
        app.action_tree.selection.return_value = ("1", "3")

        app._refresh_action_segment_bar()

        self.assertEqual(
            [call.args[0] for call in app.action_tree.set.call_args_list], ["1", "2", "3"],
        )
        self.assertEqual(
            {call.args[2] for call in app.action_tree.set.call_args_list}, {SEGMENT_BAR},
        )
        app.action_tree.set.reset_mock()

        # 又只选了一行：片段没了，原来画过的三行要擦干净。
        app.action_tree.selection.return_value = ("1",)
        app._refresh_action_segment_bar()
        self.assertEqual(
            [(call.args[0], call.args[2]) for call in app.action_tree.set.call_args_list],
            [("1", ""), ("2", ""), ("3", "")],
        )

    def test_run_single_action_prompts_with_default_one(self):
        app = self._app()
        app.root = Mock()
        app.run_current_script = Mock()
        with patch("tkinter.simpledialog.askinteger", return_value=7) as ask:
            app.run_single_action_with_count(3)
        self.assertEqual(ask.call_args.kwargs["initialvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["minvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["maxvalue"], 999999)
        self.assertIs(ask.call_args.kwargs["parent"], app.root)
        app.run_current_script.assert_called_once_with(start_index=3, single_action_repeats=7)

    def test_run_single_action_cancel_runs_nothing(self):
        app = self._app()
        app.root = Mock()
        app.run_current_script = Mock()
        with patch("tkinter.simpledialog.askinteger", return_value=None):
            app.run_single_action_with_count(0)
        app.run_current_script.assert_not_called()

    def test_workflow_context_menu_offers_open_script_items(self):
        app = self._app()
        app.root = Mock()
        app.workflow = Workflow(steps=[{"script": "scripts/关卡/a.json"}])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.workflow_tree.identify_row.return_value = "0"
        event = Mock()
        event.y, event.x_root, event.y_root = 20, 100, 120
        with patch("tkinter.Menu") as menu_class:
            app._show_workflow_context_menu(event)
        app.workflow_tree.selection_set.assert_called_once_with("0")
        menu_class.assert_called_once()
        menu = menu_class.return_value
        self.assertEqual(menu.add_command.call_count, 3)
        labels = [call.kwargs["label"] for call in menu.add_command.call_args_list]
        self.assertEqual(labels, [
            "▶ 单独执行此步骤…",
            "⇪ 在新窗口打开脚本",
            "✎ 在当前编辑器打开",
        ])
        menu.tk_popup.assert_called_once_with(100, 120)
        menu.grab_release.assert_called_once()

    def test_workflow_context_menu_run_item_asks_for_count(self):
        app = self._app()
        app.root = Mock()
        app.workflow = Workflow(steps=[{"script": "scripts/关卡/a.json"}])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.workflow_tree.identify_row.return_value = "0"
        app.run_workflow_step_with_count = Mock()
        event = Mock()
        event.y, event.x_root, event.y_root = 20, 100, 120
        with patch("tkinter.Menu") as menu_class:
            app._show_workflow_context_menu(event)
        command = menu_class.return_value.add_command.call_args_list[0].kwargs["command"]
        command()
        app.run_workflow_step_with_count.assert_called_once_with(
            {"script": "scripts/关卡/a.json"})

    def test_run_workflow_step_prompts_with_default_one(self):
        app = self._app()
        app.root = Mock()
        app.run_referenced_script_alone = Mock()
        step = {"script": "scripts/关卡/a.json", "repeats": 4}
        with patch("tkinter.simpledialog.askinteger", return_value=7) as ask:
            app.run_workflow_step_with_count(step)
        self.assertEqual(ask.call_args.kwargs["initialvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["minvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["maxvalue"], 999999)
        self.assertIs(ask.call_args.kwargs["parent"], app.root)
        # 指定的次数只作用于这一次单独执行，行里保存的剩余次数不动。
        self.assertEqual(step["repeats"], 4)
        app.run_referenced_script_alone.assert_called_once_with(step, 7)

    def test_run_workflow_step_cancel_runs_nothing(self):
        app = self._app()
        app.root = Mock()
        app.run_referenced_script_alone = Mock()
        with patch("tkinter.simpledialog.askinteger", return_value=None):
            app.run_workflow_step_with_count({"script": "scripts/关卡/a.json"})
        app.run_referenced_script_alone.assert_not_called()

    def test_workflow_context_menu_offers_segment_item_for_a_selected_range(self):
        app = self._app()
        app.root = Mock()
        app.workflow = Workflow(steps=[
            {"script": "scripts/关卡/a.json"},
            {"script": "scripts/关卡/b.json"},
            {"script": "scripts/关卡/c.json"},
        ])
        app.workflow_tree = Mock()
        # 右键落在已选中的一段里：保留这段选中，片段 = 第 1~3 行。
        app.workflow_tree.selection.return_value = ("0", "1", "2")
        app.workflow_tree.identify_row.return_value = "1"
        app.run_workflow_segment = Mock()
        event = Mock()
        event.y, event.x_root, event.y_root = 20, 100, 120
        with patch("tkinter.Menu") as menu_class:
            app._show_workflow_context_menu(event)
        app.workflow_tree.selection_set.assert_not_called()
        menu = menu_class.return_value
        labels = [call.kwargs["label"] for call in menu.add_command.call_args_list]
        self.assertEqual(labels, [
            "▶ 单独执行此步骤…",
            "▶ 循环执行片段…",
            "⇪ 在新窗口打开脚本",
            "✎ 在当前编辑器打开",
        ])
        command = menu.add_command.call_args_list[1].kwargs["command"]
        command()
        app.run_workflow_segment.assert_called_once_with()

    def test_run_workflow_segment_prompts_with_default_one(self):
        app = self._app()
        app.root = Mock()
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ("2", "4")
        app.run_workflow = Mock()
        with patch("tkinter.simpledialog.askinteger", return_value=6) as ask:
            app.run_workflow_segment()
        self.assertEqual(ask.call_args.kwargs["initialvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["minvalue"], 1)
        self.assertEqual(ask.call_args.kwargs["maxvalue"], 999999)
        self.assertIs(ask.call_args.kwargs["parent"], app.root)
        app.run_workflow.assert_called_once_with(segment=(2, 4), segment_repeats=6)

    def test_run_workflow_segment_without_a_range_notifies(self):
        app = self._app()
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.run_workflow = Mock()
        with patch("tkinter.simpledialog.askinteger") as ask:
            app.run_workflow_segment()
        app._notify.assert_called_once()
        ask.assert_not_called()
        app.run_workflow.assert_not_called()

    def test_workflow_context_menu_skipped_for_row_without_script(self):
        app = self._app()
        app.root = Mock()
        app.workflow = Workflow(steps=[{"kind": "global_module", "module": "m"}])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.workflow_tree.identify_row.return_value = "0"
        with patch("tkinter.Menu") as menu_class:
            app._show_workflow_context_menu(Mock())
        menu_class.assert_not_called()

    def test_workflow_context_menu_skipped_outside_row(self):
        app = self._app()
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.workflow_tree.identify_row.return_value = ""
        with patch("tkinter.Menu") as menu_class:
            app._show_workflow_context_menu(Mock())
        menu_class.assert_not_called()

    def test_open_workflow_script_in_editor_loads_file(self):
        app = self._app()
        app.dirty = False
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({"name": "Ref", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            app._open_workflow_script_in_editor({"script": str(ref)})
        app.load_script_into_editor.assert_called_once_with(ref)

    def test_open_workflow_script_in_editor_asks_when_dirty(self):
        # 有未保存修改时不再自己拦一道（只飘提示等于“没反应”），
        # 统一交给 load_script_into_editor 问保存 / 放弃 / 取消。
        app = self._app()
        app.dirty = True
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({"name": "Ref", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            app._open_workflow_script_in_editor({"script": str(ref)})
        app.load_script_into_editor.assert_called_once_with(ref)
        app._notify.assert_not_called()

    def test_open_workflow_script_in_editor_missing_file_notifies(self):
        app = self._app()
        app.dirty = False
        app._open_workflow_script_in_editor({"script": "C:/no_such_dir/ref.json"})
        app.load_script_into_editor.assert_not_called()
        app._notify.assert_called_once_with("脚本不存在", "找不到文件：C:/no_such_dir/ref.json")

    def _app_for_referenced_script_alone(self) -> MacroFlowApp:
        app = self._app()
        app.recorder = Mock()
        app.recorder.running = False
        app.worker = Mock()
        app.worker.is_alive.return_value = False
        app._bound_hwnd = Mock(return_value=123)
        for name in ("focus_mode_enabled_var", "activate_target_enabled_var"):
            variable = Mock()
            variable.get.return_value = False
            setattr(app, name, variable)
        app.workflow_stop = Mock()
        # 全局守卫线程是真实后台线程：单测里不能真起（执行线程被 mock 掉后没人
        # 收尾，遗留的检测线程会让整轮测试在收尾阶段被 Tcl 掐断）。
        for name in ("_begin_detection_run", "_ensure_detection_worker",
                     "_shutdown_detection_worker"):
            setattr(app, name, Mock())
        for name in ("_sound", "_hide_main_for_execution", "_show_execution_mini",
                     "_append_mini_step", "_set_execution_progress",
                     "_run_script_worker"):
            setattr(app, name, Mock())
        return app

    def _write_test_script(self, actions) -> Path:
        folder = tempfile.TemporaryDirectory(dir=BASE_DIR)
        self.addCleanup(folder.cleanup)
        ref = Path(folder.name) / "ref.json"
        ref.write_text(json.dumps({"name": "Ref", "actions": actions}, ensure_ascii=False),
                       encoding="utf-8")
        return ref

    def test_run_referenced_script_alone_starts_worker_once(self):
        app = self._app_for_referenced_script_alone()
        ref = self._write_test_script([{"type": "click", "x": 1, "y": 2, "delay_ms": 0}])
        with patch("threading.Thread") as thread_class:
            app.run_referenced_script_alone({"script": str(ref)})
        thread_class.assert_called_once()
        self.assertEqual(thread_class.call_args.kwargs["target"], app._run_script_worker)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(worker_args[1], 1)
        self.assertEqual(worker_args[0], list(load_script(ref).actions))
        self.assertEqual(thread_class.call_args.kwargs["kwargs"],
                         {"trigger": {}, "script_name": "Ref"})
        thread_class.return_value.start.assert_called_once()
        app._notify.assert_not_called()

    def test_run_referenced_script_alone_runs_requested_repeats(self):
        app = self._app_for_referenced_script_alone()
        ref = self._write_test_script([{"type": "click", "x": 1, "y": 2, "delay_ms": 0}])
        with patch("threading.Thread") as thread_class:
            app.run_referenced_script_alone({"script": str(ref)}, 5)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(worker_args[1], 5)
        self.assertIn("共执行 5 次", app._set_execution_progress.call_args.args[0])
        self.assertIn("共 5 次", app._append_mini_step.call_args.args[0])

    def test_run_referenced_script_alone_clamps_repeats_to_at_least_one(self):
        app = self._app_for_referenced_script_alone()
        ref = self._write_test_script([{"type": "click", "x": 1, "y": 2, "delay_ms": 0}])
        with patch("threading.Thread") as thread_class:
            app.run_referenced_script_alone({"script": str(ref)}, 0)
        self.assertEqual(thread_class.call_args.kwargs["args"][1], 1)

    def test_run_referenced_script_alone_missing_file_notifies(self):
        app = self._app_for_referenced_script_alone()
        app.run_referenced_script_alone({"script": "C:/no_such_dir/ref.json"})
        app._notify.assert_called_once_with("脚本不存在", "找不到文件：C:/no_such_dir/ref.json")
        app.worker.start.assert_not_called()

    def test_run_referenced_script_alone_invalid_script_notifies(self):
        app = self._app_for_referenced_script_alone()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "bad.json"
            ref.write_text("not json", encoding="utf-8")
            app.run_referenced_script_alone({"script": str(ref)})
        self.assertEqual(app._notify.call_args.args[0], "无法加载脚本")

    def test_run_referenced_script_alone_without_actions_notifies(self):
        app = self._app_for_referenced_script_alone()
        ref = self._write_test_script([])
        app.run_referenced_script_alone({"script": str(ref)})
        app._notify.assert_called_once()
        app.worker.start.assert_not_called()

    def test_run_referenced_script_alone_blocked_while_worker_running(self):
        app = self._app_for_referenced_script_alone()
        app.worker.is_alive.return_value = True
        ref = self._write_test_script([{"type": "click", "x": 1, "y": 2, "delay_ms": 0}])
        app.run_referenced_script_alone({"script": str(ref)})
        app._notify.assert_called_once_with("正在运行", "已有脚本或工作流正在执行。")
        app.worker.start.assert_not_called()

    def test_run_referenced_script_alone_skips_missing_activation_window_but_runs(self):
        app = self._app_for_referenced_script_alone()
        app._execution_activation_hwnd = Mock(
            side_effect=RuntimeError("脚本的前置窗口当前未打开。"))
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({
                "name": "Ref",
                "settings": {
                    "activation_window_enabled": True,
                    "activation_window": {"title": "游戏窗口"},
                },
                "actions": [{"type": "click", "x": 1, "y": 2, "delay_ms": 0}],
            }, ensure_ascii=False), encoding="utf-8")
            with patch("threading.Thread") as thread_class:
                app.run_referenced_script_alone({"script": str(ref)})
        app._execution_activation_hwnd.assert_called_once_with(
            123, True, {"title": "游戏窗口", "class_name": "", "process_path": ""})
        app._notify.assert_not_called()
        app._log.assert_called_once_with("前置窗口未打开，已跳过前置窗口，继续执行脚本。")
        thread_class.assert_called_once()
        thread_class.return_value.start.assert_called_once()

    def test_load_startup_script_loads_existing_file(self):
        app = self._app()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref = Path(folder) / "ref.json"
            ref.write_text(json.dumps({"name": "Ref", "actions": []}, ensure_ascii=False),
                           encoding="utf-8")
            app._load_startup_script(ref)
        app.load_script_into_editor.assert_called_once_with(ref)
        app._notify.assert_not_called()

    def test_load_startup_script_missing_file_notifies(self):
        app = self._app()
        missing = Path(BASE_DIR) / "no_such_ref.json"
        app._load_startup_script(missing)
        app.load_script_into_editor.assert_not_called()
        app._notify.assert_called_once()


class SingleActionRunTests(unittest.TestCase):
    """右键「单独执行此动作…」：把单动作模式与次数交给执行线程。"""

    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=False)
        app.worker = Mock()
        app.worker.is_alive.return_value = False
        app.script = MacroScript(
            actions=[{"type": "comment", "text": "一"}, {"type": "delay", "ms": 10},
                     {"type": "click", "x": 1, "y": 2}, {"type": "comment", "text": "四"}],
            settings={"trigger": {}},
        )
        app.repeat_var = Mock()
        app.repeat_var.get.return_value = 3
        app._begin_detection_run = Mock()
        app._ensure_detection_worker = Mock()
        app._bound_hwnd = Mock(return_value=123)
        app._activation_settings_from_script = Mock(return_value=(False, None))
        for name in ("focus_mode_enabled_var", "activate_target_enabled_var"):
            variable = Mock()
            variable.get.return_value = False
            setattr(app, name, variable)
        app.workflow_stop = Mock()
        for name in ("_sound", "_hide_main_for_execution", "_show_execution_mini",
                     "_append_mini_step", "_set_execution_progress", "_notify", "_log"):
            setattr(app, name, Mock())
        return app

    def test_single_action_run_passes_row_repeats_and_flag_to_worker(self):
        app = self._app()
        with patch("threading.Thread") as thread_class:
            app._run_current_script_impl(start_index=2, single_action_repeats=5)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(worker_args[0], list(app.script.actions))
        self.assertEqual(worker_args[1], 5)
        self.assertEqual(worker_args[7], 2)
        self.assertIs(thread_class.call_args.kwargs["kwargs"]["single_action"], True)
        self.assertIn("单独执行第 3/4 行", app._set_execution_progress.call_args.args[0])
        self.assertIn("共 5 次", app._set_execution_progress.call_args.args[0])
        self.assertIn("单独执行第 3/4 行动作，共 5 次", app._append_mini_step.call_args.args[0])
        thread_class.return_value.start.assert_called_once()
        app._notify.assert_not_called()

    def test_segment_run_passes_the_range_and_round_count_to_worker(self):
        app = self._app()
        with patch("threading.Thread") as thread_class:
            app._run_current_script_impl(segment=(1, 2), segment_repeats=4)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(worker_args[0], list(app.script.actions), "整份动作列表照旧交给播放器")
        self.assertEqual(worker_args[1], 4, "次数是片段的轮数")
        self.assertEqual(worker_args[7], 1, "从片段首行起跑")
        kwargs = thread_class.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["segment_end"], 2)
        self.assertIs(kwargs["single_action"], False, "片段与单独执行互斥")
        progress = app._set_execution_progress.call_args.args[0]
        self.assertIn("循环执行片段 第 2-3/4 行", progress)
        self.assertIn("共 4 次", progress)
        self.assertIn(
            "循环执行片段 第 2-3/4 行，共 4 次。",
            app._append_mini_step.call_args.args[0],
        )

    def test_segment_run_clamps_the_range_to_the_action_list(self):
        app = self._app()
        app.script = MacroScript(
            actions=[{"type": "comment", "text": "一"}, {"type": "delay", "ms": 1}],
            settings={"trigger": {}},
        )
        with patch("threading.Thread") as thread_class:
            app._run_current_script_impl(segment=(0, 9), segment_repeats=2)
        self.assertEqual(thread_class.call_args.kwargs["args"][7], 0)
        self.assertEqual(thread_class.call_args.kwargs["kwargs"]["segment_end"], 1)

    def test_normal_run_keeps_toolbar_repeat_count(self):
        app = self._app()
        with patch("threading.Thread") as thread_class:
            app._run_current_script_impl(start_index=1)
        worker_args = thread_class.call_args.kwargs["args"]
        self.assertEqual(worker_args[1], 3)
        self.assertIs(thread_class.call_args.kwargs["kwargs"]["single_action"], False)
        self.assertIn("从第 2/4 行开始 · 共执行 3 次",
                      app._set_execution_progress.call_args.args[0])

    def test_single_action_run_clamps_repeats_to_at_least_one(self):
        app = self._app()
        with patch("threading.Thread") as thread_class:
            app._run_current_script_impl(start_index=0, single_action_repeats=0)
        self.assertEqual(thread_class.call_args.kwargs["args"][1], 1)

    def test_single_action_run_blocked_while_worker_running(self):
        app = self._app()
        app.worker.is_alive.return_value = True
        with patch("threading.Thread") as thread_class:
            app._run_current_script_impl(start_index=0, single_action_repeats=1)
        app._notify.assert_called_once_with("正在运行", "已有脚本或工作流正在执行。")
        thread_class.assert_not_called()

    def test_entrypoint_forwards_single_action_repeats(self):
        app = self._app()
        app._run_current_script_impl = Mock()
        app.run_current_script(start_index=1, single_action_repeats=4)
        app._run_current_script_impl.assert_called_once_with(
            1, 4, segment=None, segment_repeats=1)


class LastScriptRestoreTests(unittest.TestCase):
    """启动时恢复上次关闭时脚本编辑页正在编辑的脚本。"""

    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.app_settings = {}
        app._log = Mock()
        app.load_script_into_editor = Mock()
        return app

    def test_restores_recorded_script_at_startup(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            path = Path(folder) / "a.json"
            path.write_text("{}", encoding="utf-8")
            app = self._app()
            app.app_settings["last_script_path"] = str(path)
            app._load_last_script()
            app.load_script_into_editor.assert_called_once_with(path)

    def test_restores_relative_recorded_script(self):
        # 设置里存的是相对程序目录的路径（display_path 产出），启动时按
        # BASE_DIR 解析回绝对路径再打开。
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            path = Path(folder) / "b.json"
            path.write_text("{}", encoding="utf-8")
            app = self._app()
            app.app_settings["last_script_path"] = display_path(path)
            app._load_last_script()
            app.load_script_into_editor.assert_called_once_with(path)

    def test_skips_missing_recorded_script(self):
        # 脚本已被删除/移动时不能弹“打开失败”，静默跳过并记日志。
        app = self._app()
        app.app_settings["last_script_path"] = "scripts/关卡/不存在的脚本.json"
        app._load_last_script()
        app.load_script_into_editor.assert_not_called()
        self.assertTrue(any("已不存在" in call.args[0] for call in app._log.call_args_list))

    def test_skips_empty_record(self):
        # 编辑器没有脚本（新建/关闭/录制分离）时记录为空，启动不恢复。
        app = self._app()
        app._load_last_script()
        app.load_script_into_editor.assert_not_called()

    def test_sidebar_settings_record_editor_script_path(self):
        # 每次持久化侧栏设置（含关闭应用）都要带上脚本编辑页当前打开的脚本。
        app = self._app()
        app.interval_var = FakeSettingVar("100")
        app.repeat_var = FakeSettingVar("1")
        app.backup_interval_var = FakeSettingVar("1h")
        app.sound_enabled_var = FakeSettingVar(True)
        app.mini_window_enabled_var = FakeSettingVar(True)
        app.execution_mini_enabled_var = FakeSettingVar(True)
        app.execution_mini_position = []
        app.playback_speed_var = FakeSettingVar(1.0)
        app.close_action_var = FakeSettingVar("exit")
        app.focus_mode_enabled_var = FakeSettingVar(False)
        app.activate_target_enabled_var = FakeSettingVar(True)
        app.floating_notice_position_var = FakeSettingVar("顶部居中")
        app.saved_window_signature = None
        app.activation_draft_enabled = False
        app.activation_enabled_var = FakeSettingVar(False)
        app.activation_draft_signature = None
        app._workflow_snapshot = Mock(return_value={})
        app.workflow_path = None
        app.timed_backup_enabled_var = FakeSettingVar(False)
        app.windows_startup_enabled_var = FakeSettingVar(False)
        app.start_minimized_to_tray_var = FakeSettingVar(False)
        app.startup_run_workflow_var = FakeSettingVar(False)
        app.startup_workflow_path_var = FakeSettingVar("")
        app.level_scripts_dir_var = FakeSettingVar("scripts/关卡")
        app.level_pack_scripts_dir_var = FakeSettingVar("scripts/关卡封装")
        app.switch_scripts_dir_var = FakeSettingVar("scripts/切换")
        app.direction_scripts_dir_var = FakeSettingVar("scripts/方向")

        app.script_path = Path("C:/x/A.json")
        self.assertEqual(
            app._collect_sidebar_settings()["last_script_path"],
            display_path(app.script_path),
        )
        app.script_path = None
        self.assertEqual(app._collect_sidebar_settings()["last_script_path"], "")

    def test_sidebar_settings_capture_window_geometry_and_editor_draft(self):
        app = self._app()
        app.root = Mock()
        app.root.geometry.return_value = "1600x900+40+50"
        app.interval_var = FakeSettingVar("125")
        app.repeat_var = FakeSettingVar("1")
        app.backup_interval_var = FakeSettingVar("1h")
        app.sound_enabled_var = FakeSettingVar(True)
        app.mini_window_enabled_var = FakeSettingVar(True)
        app.execution_mini_enabled_var = FakeSettingVar(True)
        app.execution_mini_position = []
        app.playback_speed_var = FakeSettingVar(1.0)
        app.close_action_var = FakeSettingVar("exit")
        app.focus_mode_enabled_var = FakeSettingVar(False)
        app.activate_target_enabled_var = FakeSettingVar(True)
        app.floating_notice_position_var = FakeSettingVar("顶部居中")
        app.saved_window_signature = None
        app.activation_draft_enabled = True
        app.activation_enabled_var = FakeSettingVar(True)
        app.saved_activation_signature = {
            "title": "前置窗口", "class_name": "Front", "process_path": "C:/Game/front.exe",
        }
        app.activation_draft_signature = dict(app.saved_activation_signature)
        app._workflow_snapshot = Mock(return_value={})
        app.workflow_path = None
        app.timed_backup_enabled_var = FakeSettingVar(False)
        app.windows_startup_enabled_var = FakeSettingVar(False)
        app.start_minimized_to_tray_var = FakeSettingVar(False)
        app.startup_run_workflow_var = FakeSettingVar(False)
        app.startup_workflow_path_var = FakeSettingVar("")
        app.level_scripts_dir_var = FakeSettingVar("scripts/关卡")
        app.level_pack_scripts_dir_var = FakeSettingVar("scripts/关卡封装")
        app.switch_scripts_dir_var = FakeSettingVar("scripts/切换")
        app.direction_scripts_dir_var = FakeSettingVar("scripts/方向")
        app.script = MacroScript(
            name="录制脚本",
            actions=[{"type": "key_press", "vk": 74, "name": "J"}],
            settings={"recorded_screen": {"width": 1920, "height": 1080}},
        )
        app.script_name_var = FakeSettingVar("录制脚本")
        app.script_category_var = FakeSettingVar("关卡")
        app.script_path = Path("scripts/关卡/录制脚本.json")
        app.script_requires_new_file = False
        app.dirty = True

        settings = app._collect_sidebar_settings()

        self.assertEqual(settings["main_window_geometry"], "1600x900+40+50")
        self.assertEqual(settings["editor_draft"]["script"]["actions"], app.script.actions)
        self.assertTrue(settings["editor_draft"]["dirty"])
        self.assertEqual(
            settings["editor_draft"]["script"]["settings"]["activation_window"]["title"],
            "前置窗口",
        )

    def test_restore_editor_draft_restores_unsaved_actions_and_prewindow(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script_name_var = FakeSettingVar("")
        app.record_mode_var = FakeSettingVar("")
        app.interval_var = FakeSettingVar(20)
        app.script_category_var = FakeSettingVar("")
        app._update_undo_open_button = Mock()
        app._refresh_coordinate_scale_status = Mock()
        app.action_tree = FakeTree(ACTION_TREE_COLUMNS)
        app.record_count_var = FakeVar("")
        app.empty_action_hint = Mock()
        app.global_script_marker = Mock()
        app.edit_action_button = Mock()
        app.undo_button = Mock()
        app.redo_button = Mock()
        app._sync_activation_ui_from_script = Mock()
        app._set_status = Mock()
        app._log = Mock()
        app.app_settings = {}
        path = BASE_DIR / "scripts" / "关卡" / "录制脚本.json"
        draft = {
            "script": MacroScript(
                name="录制脚本",
                actions=[{"type": "mouse_button", "button": "right", "down": True}],
                settings={
                    "move_interval_ms": 125,
                    "activation_window_enabled": True,
                    "activation_window": {
                        "title": "前置窗口", "class_name": "Front", "process_path": "C:/Game/front.exe",
                    },
                },
            ).to_dict(),
            "script_path": display_path(path),
            "script_requires_new_file": False,
            "dirty": True,
        }

        app._restore_editor_draft(draft)

        self.assertEqual(app.script.name, "录制脚本")
        self.assertEqual(app.script.actions[0]["button"], "right")
        self.assertEqual(app.script_path, path)
        self.assertTrue(app.dirty)
        self.assertEqual(app.interval_var.get(), 125)
        self.assertEqual(app.script_category_var.get(), "关卡")
        app._sync_activation_ui_from_script.assert_called_once_with()

    def test_restore_last_editor_state_prefers_editor_draft(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        draft = {"script": MacroScript(name="未保存").to_dict()}
        app.app_settings = {"editor_draft": draft}
        app._restore_editor_draft = Mock()
        app._load_last_script = Mock()

        app._restore_last_editor_state()

        app._restore_editor_draft.assert_called_once_with(draft)
        app._load_last_script.assert_not_called()

    def test_restore_main_window_geometry_uses_saved_value(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.app_settings = {"main_window_geometry": "1600x900+40+50"}
        app.root = Mock()

        app._restore_main_window_geometry()

        app.root.geometry.assert_called_once_with("1600x900+40+50")

    def test_restore_main_window_geometry_rehomes_offscreen_saved_value(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.app_settings = {"main_window_geometry": "1902x1039+-1919+182"}
        app.root = Mock()

        with package_patch('app', 'get_virtual_screen_rect', return_value={'left': 0, 'top': 0, 'width': 1920, 'height': 1080}):
            app._restore_main_window_geometry()

        app.root.geometry.assert_called_once_with("1902x1039+9+20")


class RecordedInputDialogTests(unittest.TestCase):
    """双击「录制动作」那一行打开的内容窗口：列出每一步、可删可改、确定写回。"""

    def _dialog(self, steps=None):
        root = tk.Tk()
        root.attributes("-alpha", 0.0)
        self.addCleanup(root.destroy)
        dialog = RecordedInputDialog(root, steps)
        self.addCleanup(dialog.destroy)
        return dialog

    def test_lists_folded_steps_with_delays(self):
        dialog = self._dialog([
            {"type": "key", "vk": 65, "name": "a", "down": True},
            {"type": "mouse_button", "button": "left", "down": True, "x": 1, "y": 2, "delay_ms": 120},
        ])

        self.assertEqual(dialog.listbox.size(), 2)
        self.assertEqual(dialog.summary_var.get(), "共 2 步")
        self.assertIn("键盘按下：a", dialog.listbox.get(0))
        self.assertIn("延时 120 ms", dialog.listbox.get(1))

    def test_remove_and_accept_returns_remaining_steps(self):
        dialog = self._dialog([
            {"type": "key", "vk": 65, "name": "a", "down": True},
            {"type": "key", "vk": 65, "name": "a", "down": False},
        ])
        dialog.listbox.selection_set(0)

        dialog._remove_selected()
        dialog._accept()

        self.assertEqual(len(dialog.result), 1)
        self.assertFalse(dialog.result[0]["down"])
        self.assertTrue(dialog.result[0].get(ACTION_ID_KEY))

    def test_move_reorders_steps(self):
        dialog = self._dialog([
            {"type": "delay", "ms": 1},
            {"type": "delay", "ms": 2},
        ])
        dialog.listbox.selection_set(1)

        dialog._move_selected(-1)

        self.assertEqual([step["ms"] for step in dialog.steps], [2, 1])


class SegmentRecordInsertTests(unittest.TestCase):
    """「录制动作…」走主应用的录制路径，停止后把整段动作按顺序追加到当前代码段。"""

    class _Form(SegmentEditorMixin, tk.Frame):
        segment_depth = 1

        def __init__(self, master):
            super().__init__(master)
            self.segment = []

    class _FakeApp:
        """只保留代码段录制用到的那一个入口，记录收到的回调与标签。"""

        def __init__(self):
            self.calls = []

        def _start_action_recording(self, handler, label):
            self.calls.append((handler, label))
            return True

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.addCleanup(self.root.destroy)
        self.form = self._Form(self.root)
        self.form.segment_listbox = tk.Listbox(self.form)
        self.addCleanup(self.form.segment_listbox.destroy)
        self.app = self._FakeApp()
        # _app_via_parent 沿父链找主应用：挂在根窗口上。
        self.root._macroflow_app = self.app

    def test_recording_uses_the_shared_app_entry(self):
        # 代码段录制必须复用主应用的录制入口（悬浮小窗、F8 停止都来自它），
        # 而不是自己另开一个录制窗口。
        self.form._record_segment_actions()

        self.assertEqual(len(self.app.calls), 1)
        _handler, label = self.app.calls[0]
        self.assertEqual(label, "代码段录制动作")

    def test_recorded_actions_are_appended_in_order(self):
        recorded = [
            {"type": "key", "vk": 65, "name": "a", "down": True},
            {"type": "key", "vk": 65, "name": "a", "down": False},
        ]
        self.form._record_segment_actions()
        handler = self.app.calls[0][0]

        handler(recorded)

        self.assertEqual([item["type"] for item in self.form.segment], ["key", "key"])
        self.assertEqual(self.form.segment_listbox.size(), 2)
        # 每条都要有动作 id（与其它插入路径一致，编辑/引用都按 id 定位）。
        self.assertTrue(all(item.get(ACTION_ID_KEY) for item in self.form.segment))

    def test_existing_segment_rows_are_kept(self):
        self.form.segment.append({"type": "delay", "ms": 7, "action_id": "keep"})
        self.form._record_segment_actions()

        self.app.calls[0][0]([{"type": "key", "vk": 66, "name": "b", "down": True}])

        self.assertEqual([item["type"] for item in self.form.segment], ["delay", "key"])
        self.assertEqual(self.form.segment[0]["action_id"], "keep")

    def test_no_recorded_steps_adds_nothing(self):
        self.form._record_segment_actions()

        self.app.calls[0][0]([])

        self.assertEqual(self.form.segment, [])


class SegmentBlockMoveTests(unittest.TestCase):
    """代码段列表的「上移 / 下移」：连续多选整块移动，块内顺序不变。"""

    class _Form(SegmentEditorMixin, tk.Frame):
        def __init__(self, master):
            super().__init__(master)
            self.segment = []

    def setUp(self):
        self.root = tk.Tk()
        self.root.attributes("-alpha", 0.0)
        self.addCleanup(self.root.destroy)
        self.form = self._Form(self.root)
        self.form.segment_listbox = tk.Listbox(self.form)
        self.addCleanup(self.form.segment_listbox.destroy)
        self.form.segment = [{"type": "delay", "ms": index} for index in range(5)]
        self.form._reload_segment_list()

    def test_contiguous_selection_moves_as_a_block(self):
        self.form.segment_listbox.selection_set(1, 2)

        self.form._move_segment_item(-1)

        # 整块 [1,2] 移到最前：原来在它上面的第 1 行被让到整块之后。
        self.assertEqual([item["ms"] for item in self.form.segment], [1, 2, 0, 3, 4])
        self.assertEqual(tuple(self.form.segment_listbox.curselection()), (0, 1))

    def test_block_moves_down_and_keeps_selection(self):
        self.form.segment_listbox.selection_set(1, 3)

        self.form._move_segment_item(1)

        self.assertEqual([item["ms"] for item in self.form.segment], [0, 4, 1, 2, 3])
        self.assertEqual(tuple(self.form.segment_listbox.curselection()), (2, 3, 4))

    def test_block_stops_at_list_edges(self):
        self.form.segment_listbox.selection_set(0, 1)
        self.form._move_segment_item(-1)
        self.assertEqual([item["ms"] for item in self.form.segment], [0, 1, 2, 3, 4])

        self.form.segment_listbox.selection_clear(0, "end")
        self.form.segment_listbox.selection_set(3, 4)
        self.form._move_segment_item(1)
        self.assertEqual([item["ms"] for item in self.form.segment], [0, 1, 2, 3, 4])

    def test_non_contiguous_selection_is_rejected(self):
        self.form.segment_listbox.selection_set(0)
        self.form.segment_listbox.selection_set(2)
        with package_patch('dialogs', 'show_floating_notice') as notice:
            self.form._move_segment_item(-1)

        self.assertEqual([item["ms"] for item in self.form.segment], [0, 1, 2, 3, 4])
        notice.assert_called_once()

    def test_no_selection_does_nothing(self):
        self.form.segment_listbox.selection_clear(0, "end")

        self.form._move_segment_item(-1)

        self.assertEqual([item["ms"] for item in self.form.segment], [0, 1, 2, 3, 4])

if __name__ == '__main__':
    unittest.main()
