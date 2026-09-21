"""播放器：动作回放、时间线、截图失败容忍、执行期保持唤醒。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_player.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import json
from mss.exception import ScreenShotError
import numpy as np
from pathlib import Path
from macroflow.core.storage import BASE_DIR
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, call, patch
import macroflow.core.display_power as display_power_module
import macroflow.core.image_match as image_match_module
from macroflow.core.models import ACTION_ID_KEY, MacroScript, NEXT_WORKFLOW_STEP_TARGET_ID, RECORDED_INPUT_STEPS_KEY, SCRIPT_START_TARGET_ID, clone_actions_with_new_ids
from macroflow.core.storage import resolve_path, save_script
from macroflow.execution.player import MacroPlayer
from macroflow.execution.player.base import CLICK_DEDUP_WINDOW_S, CLICK_SOURCE_ACTION, CLICK_SOURCE_GUARD, CLICK_SOURCE_MODULE, GUARD_SETTLE_MS, scale_screen_point
from macroflow.execution.player.control import EndCurrentScriptRequest, GuardJumpRequest, PlaybackStopped
from macroflow.execution.timeline import PlaybackTimeline
from macroflow.input.wininput import WindowInfo
from macroflow.ui.app.main import MacroFlowApp
from macroflow.ui.app.summaries import action_summary
import macroflow.ui.dialogs as dialog_module
from tests.helpers.patches import package_patch


class PlayerTests(unittest.TestCase):
    def test_recorded_actions_use_absolute_targets_when_execution_takes_time(self):
        class FakeClock:
            def __init__(self):
                self.value = 0.0
                self.waits = []

            def now(self):
                return self.value

            def wait(self, seconds):
                self.waits.append(seconds)
                self.value += seconds

        clock = FakeClock()
        player = MacroPlayer()
        player._timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        player._wait = lambda milliseconds: clock.wait(milliseconds / 1000)

        def execute(_action, _hwnd, _stack=None, _depth=0):
            clock.value += 0.003

        player._execute_action = execute
        player._run_action_sequence([
            {"type": "key", "recorded_at_ms": 0.0},
            {"type": "key", "recorded_at_ms": 100.0},
            {"type": "key", "recorded_at_ms": 200.0},
        ], None)

        self.assertEqual(clock.waits, [0.097, 0.097])

    def test_explicit_delay_rebases_the_next_recorded_action(self):
        class FakeClock:
            def __init__(self):
                self.value = 0.0
                self.waits = []

            def now(self):
                return self.value

            def wait(self, seconds):
                self.waits.append(seconds)
                self.value += seconds

        clock = FakeClock()
        player = MacroPlayer()
        player._timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        player._wait = lambda milliseconds: clock.wait(milliseconds / 1000)

        def execute(action, *_args):
            if action["type"] == "delay":
                clock.wait(action["ms"] / 1000)

        player._execute_action = execute

        player._run_action_sequence([
            {"type": "key", "recorded_at_ms": 0.0},
            {"type": "delay", "ms": 50},
            {"type": "key", "recorded_at_ms": 100.0},
        ], None)

        self.assertEqual([wait for wait in clock.waits if wait], [0.05, 0.1])

    def test_nested_recorded_actions_use_child_timeline_and_restore_parent_boundary(self):
        class FakeClock:
            def __init__(self):
                self.value = 0.0
                self.waits = []

            def now(self):
                return self.value

            def wait(self, seconds):
                self.waits.append(seconds)
                self.value += seconds

        clock = FakeClock()
        player = MacroPlayer()
        player._timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        player._wait = lambda milliseconds: clock.wait(milliseconds / 1000)
        timeline_events = []
        mark_boundary = player._timeline.mark_boundary
        start = player._timeline.start

        def record_boundary():
            timeline_events.append(("boundary", round(clock.value, 9)))
            mark_boundary()

        def record_start(offset_ms=0.0):
            timeline_events.append(("start", round(clock.value, 9), offset_ms))
            start(offset_ms)

        player._timeline.mark_boundary = record_boundary
        player._timeline.start = record_start

        def execute(action, hwnd, stack=None, depth=0):
            if action["type"] == "script_ref":
                player._run_action_sequence([
                    {"type": "key", "recorded_at_ms": 0.0},
                    {"type": "key", "recorded_at_ms": 50.0},
                ], hwnd, depth=depth + 1)

        player._execute_action = execute
        player._run_action_sequence([
            {"type": "comment"},
            {"type": "key", "recorded_at_ms": 100.0},
            {"type": "script_ref"},
            {"type": "key", "recorded_at_ms": 200.0},
        ], None)

        self.assertEqual([wait for wait in clock.waits if wait], [0.1, 0.05, 0.1])
        self.assertEqual(
            timeline_events,
            [
                ("start", 0.0, 0.0),
                ("boundary", 0.1),
                ("start", 0.1, 0.0),
                ("boundary", 0.15),
                ("boundary", 0.15),
            ],
        )

    def test_guard_jump_rebases_before_the_target_recorded_action(self):
        class FakeClock:
            def __init__(self):
                self.value = 0.0
                self.waits = []

            def now(self):
                return self.value

            def wait(self, seconds):
                self.waits.append(seconds)
                self.value += seconds

        clock = FakeClock()
        hits = [{"kind": "success"}]
        player = MacroPlayer(on_guard_poll=lambda: hits.pop(0) if hits else None)
        player._timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        player._wait = lambda milliseconds: clock.wait(milliseconds / 1000)

        def handle_guard_hit(_hit):
            clock.value += 0.05
            raise GuardJumpRequest(jump_row=2)

        player.handle_guard_hit = handle_guard_hit
        player._execute_action = Mock(return_value=None)
        player._run_action_sequence([
            {"type": "comment"},
            {"type": "key", "recorded_at_ms": 100.0},
        ], None)

        self.assertEqual(clock.waits, [0.1])
        self.assertEqual(player._timeline.metrics.rebase_count, 1)

    def test_repeat_interval_guard_jump_rebases_timeline(self):
        timing = []
        player = MacroPlayer(on_timing=timing.append)

        def wait(milliseconds):
            if milliseconds:
                raise GuardJumpRequest(jump_row=1)

        player._wait = wait
        player.play([{"type": "comment"}], repeats=2, repeat_interval_ms=25)

        self.assertEqual(timing[0]["rebase_count"], 1)

    def test_thousand_recorded_actions_do_not_accumulate_execution_cost(self):
        class FakeClock:
            def __init__(self):
                self.value = 0.0

            def now(self):
                return self.value

            def wait(self, seconds):
                self.value += seconds

        clock = FakeClock()
        player = MacroPlayer()
        player._timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
        player._wait = lambda milliseconds: clock.wait(milliseconds / 1000)

        def execute(*_args):
            clock.value += 0.003

        player._execute_action = execute
        player._run_action_sequence([
            {"type": "key", "recorded_at_ms": float(index * 10)}
            for index in range(1000)
        ], None)

        self.assertAlmostEqual(clock.value, 9.993, places=9)

    def test_guard_poll_records_recognition_time_without_reordering_actions(self):
        player = MacroPlayer(on_guard_poll=lambda: None)
        player._execute_action = Mock(return_value=None)
        # 一次动作会取 4 次时钟：守卫评估（识别计时）2 次 + 等待/动作耗时 2 次。
        # 识别计时 = 守卫评估前后两次之差（这里 25ms）。
        with patch("macroflow.execution.player.time.perf_counter",
                   side_effect=[10.0, 10.025, 10.025, 10.025, 10.025, 10.025]):
            player._run_action_sequence([{"type": "comment"}], None)

        self.assertEqual(player._execute_action.call_args_list[0].args[0]["type"], "comment")
        self.assertAlmostEqual(player._timeline.metrics.recognition_ms, 25.0, places=6)

    def test_poll_guards_executes_all_hits_from_one_evaluation_in_order(self):
        hits = [
            {"kind": "success", "log_subject": "模块[first]"},
            {"kind": "success", "log_subject": "模块[second]"},
        ]
        player = MacroPlayer(
            on_guard_poll=lambda: hits.pop(0) if hits else None,
        )
        player.handle_guard_hit = Mock()

        player._poll_guards()

        self.assertEqual(
            [call.args[0]["log_subject"] for call in player.handle_guard_hit.call_args_list],
            ["模块[first]", "模块[second]"],
        )

    def test_block_action_can_only_be_released_by_a_jump(self):
        notices = []
        hits = iter([None, {
            "jump_action_id": "after-block",
            "jump_row": 2,
        }])
        player = MacroPlayer(
            on_guard_poll=lambda: next(hits, None),
            on_notice=lambda text, _duration: notices.append(text),
        )

        advanced = player.play([
            {"type": "block", "action_id": "block-row"},
            {"type": "notice", "action_id": "after-block", "text": "已跳过阻塞"},
        ])

        self.assertFalse(advanced)
        self.assertEqual(notices, ["已跳过阻塞"])

    def test_block_action_can_be_released_by_workflow_global_jump(self):
        notices = []
        hits = iter([None, {
            "log_subject": "模块[工作流全局] · 结算完成.png",
            "jump_action_id": "after-block",
            "jump_row": 2,
        }])
        player = MacroPlayer(
            on_guard_poll=lambda: next(hits, None),
            on_notice=lambda text, _duration: notices.append(text),
        )

        player.play([
            {"type": "block", "action_id": "block-row"},
            {"type": "notice", "action_id": "after-block", "text": "工作流全局已跳过"},
        ])

        self.assertEqual(notices, ["工作流全局已跳过"])

    def test_block_action_can_be_released_by_script_global_jump(self):
        notices = []
        hits = iter([None, {
            "log_subject": "模块[脚本全局] · 结算完成.png",
            "scope_action_ids": ("block-row", "after-block"),
            "jump_action_id": "after-block",
            "jump_row": 2,
        }])
        player = MacroPlayer(
            on_guard_poll=lambda: next(hits, None),
            on_notice=lambda text, _duration: notices.append(text),
        )

        player.play([
            {"type": "block", "action_id": "block-row"},
            {"type": "notice", "action_id": "after-block", "text": "脚本全局已跳过"},
        ])

        self.assertEqual(notices, ["脚本全局已跳过"])

    def test_nested_script_global_end_target_advances_only_current_reference_repeat(self):
        notices = []
        nested = MacroScript(
            name="脚本B",
            actions=[{"type": "notice", "text": "B本次完成", "action_id": "b-body"}],
        )
        hits = iter([
            None,  # 外层脚本进入 script_ref 前，不触发 B 的守卫。
            {
                "jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
                "jump_row": 2,
                "scope_action_ids": ["b-body"],
            },
            None, None, None,
        ])
        player = MacroPlayer(
            on_guard_poll=lambda: next(hits, None),
            on_notice=lambda text, _duration: notices.append(text),
        )
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "script_b.json"
            script_path.write_text("{}", encoding="utf-8")
            with package_patch('player', 'resolve_path', return_value=script_path), \
                 package_patch('player', 'load_script', return_value=nested):
                advanced = player.play([
                    {"type": "script_ref", "script": "script_b.json", "repeats": 3},
                    {"type": "notice", "text": "A继续执行"},
                ])

        self.assertFalse(advanced)
        self.assertEqual(notices, ["B本次完成", "B本次完成", "A继续执行"])

    def test_global_guard_end_current_script_repeats_nested_script_invocation(self):
        nested = MacroScript(name="脚本B", actions=[{"type": "comment"}])
        hit = {
            "kind": "success",
            "log_subject": "模块[看到主线模式结束脚本] · 主线模式.png",
            "actions": [{"type": "end_current_script"}],
        }
        hits = iter([None, hit, hit, hit, None])
        player = MacroPlayer(on_guard_poll=lambda: next(hits, None))

        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "script_b.json"
            script_path.write_text("{}", encoding="utf-8")
            with package_patch('player', 'resolve_path', return_value=script_path), \
                 package_patch('player', 'load_script', return_value=nested) as load:
                advanced = player.play([
                    {"type": "script_ref", "script": "script_b.json", "repeats": 3},
                ])

        self.assertFalse(advanced)
        self.assertEqual(load.call_count, 3)

    def test_global_guard_log_includes_active_script_name(self):
        logs = []
        hits = iter([{
            "kind": "success",
            "log_subject": "模块[天降神器关闭] · 天降神器关闭.png",
        }])
        player = MacroPlayer(on_log=logs.append, on_guard_poll=lambda: next(hits, None))

        player.play([{"type": "comment"}], script_name="经典团战")

        self.assertIn(
            "全局检测触发：脚本[经典团战] · 模块[天降神器关闭] · 天降神器关闭.png，开始执行处理段。",
            logs,
        )

    def test_global_guard_logs_each_processing_action_before_execution(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)

        with self.assertRaises(EndCurrentScriptRequest):
            player.handle_guard_hit({
                "kind": "success",
                "log_subject": "模块[看到主线模式结束脚本] · 主线模式.png",
                "actions": [{"type": "end_current_script"}],
            })

        self.assertIn(
            "全局检测处理段动作 1/1：结束当前最里层脚本，继续执行。",
            logs,
        )

    def test_global_guard_end_current_script_only_finishes_current_repeat(self):
        repeat_completions = []
        waits = []
        hit = {
            "kind": "success",
            "log_subject": "模块[看到主线模式结束脚本] · 主线模式.png",
            "actions": [{"type": "end_current_script"}],
        }
        hits = iter([hit, hit, hit])
        player = MacroPlayer(on_guard_poll=lambda: next(hits, None))
        player._wait = lambda milliseconds: waits.append(milliseconds)

        advanced = player.play(
            [{"type": "comment"}],
            repeats=3,
            repeat_interval_ms=25,
            on_repeat_complete=lambda current, total: repeat_completions.append(
                (current, total)
            ),
        )

        self.assertFalse(advanced)
        self.assertEqual(repeat_completions, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(waits.count(25), 2)

    def test_global_guard_jump_log_includes_active_script_name(self):
        logs = []
        hits = iter([{
            "kind": "success",
            "log_subject": "模块[天降神器关闭] · 天降神器关闭.png",
            "jump_action_id": "settlement",
            "jump_row": 2,
        }, None])
        player = MacroPlayer(
            on_log=logs.append,
            on_guard_poll=lambda: next(hits),
        )

        player.play(
            [
                {"type": "comment", "action_id": "before"},
                {"type": "comment", "action_id": "settlement"},
            ],
            script_name="经典团战",
        )

        self.assertIn("脚本[经典团战]：全局检测跳转到第 2 行执行。", logs)

    def test_blocking_module_log_includes_active_script_name(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        module = {
            "name": "竞技结算确定", "template": "unused.png", "region": [1, 2, 30, 40],
            "blocking": True, "interval_ms": 50, "threshold": 0.85,
            "after_action": "click_match",
        }

        def miss_then_stop(*_args, **_kwargs):
            player.stop_event.set()
            return None

        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', side_effect=miss_then_stop):
            player.play([{
                "type": "image_match", "module_ref": True,
                "module_key": "module:settlement", "region_mode": "template", "delay_ms": 0,
            }], script_name="经典团战")

        self.assertIn(
            "脚本[经典团战]：模块 竞技结算确定 开始阻塞等待 unused.png 出现。",
            logs,
        )

    def test_wait_until_absent_module_log_does_not_claim_waiting_to_appear(self):
        # 勾了「等待目标消失」的模块语义相反：看到目标才执行动作，检测不到即完成。
        # 日志不能再说"开始阻塞等待 X 出现"，否则用户会以为它在等图片出现，
        # 而实际它第一帧就判定"已消失"并直接成功（用户就是这么被绕进去的）。
        logs = []
        player = MacroPlayer(on_log=logs.append)
        module = {
            "name": "资讯叉叉", "template": "资讯叉叉.png", "region": [1, 2, 30, 40],
            "blocking": True, "interval_ms": 50, "threshold": 0.85,
            "after_action": "click_match", "wait_text_absent": True,
        }

        def miss_then_stop(*_args, **_kwargs):
            player.stop_event.set()
            return None

        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', side_effect=miss_then_stop):
            player.play([{
                "type": "image_match", "module_ref": True,
                "module_key": "module:news", "region_mode": "template", "delay_ms": 0,
            }], script_name="资讯_精彩活动叉叉")

        joined = "\n".join(logs)
        self.assertIn("等待目标消失", joined)
        self.assertNotIn("开始阻塞等待", joined)

    def test_missing_module_reference_is_routed_as_failure_without_running_stale_template(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        with package_patch('player', 'registered_module_object', return_value=None), package_patch('player', 'find_template', return_value=None) as find:
            result = player._execute_image({
                "type": "image_match", "module_ref": True,
                "module_key": "module:deleted", "template": "images/stale.png",
                "region_mode": "template", "region": [1, 2, 3, 4],
                "timeout_ms": 0, "on_timeout": "continue",
            }, None)

        self.assertIsNone(result)
        self.assertIn("引用的模块已不存在，按识别失败处理：module:deleted", logs)
        find.assert_not_called()

    def test_missing_module_reference_summary_matches_failure_routing(self):
        _kind, detail, _delay = action_summary({
            "type": "image_match", "module_ref": True,
            "module_key": "module:deleted", "template": "images/stale.png",
        }, module_objects={})

        self.assertIn("对象不存在，按识别失败处理", detail)
        self.assertNotIn("按内嵌参数执行", detail)

    def test_recorded_input_actions_keep_each_recorded_delay(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._execute_action = Mock(return_value=None)

        player._run_action_sequence([
            {"type": "key", "delay_ms": 0},
            {"type": "key", "delay_ms": 100},
            {"type": "mouse_button", "delay_ms": 200},
        ], None)

        self.assertEqual(
            [call.args[0] for call in player._wait.call_args_list],
            [0, 100, 200],
        )

    def test_absolute_mouse_button_positions_from_its_own_coordinates(self):
        player = MacroPlayer()
        player._source_screen = {"left": -1920, "top": 0, "width": 3840, "height": 2160}
        player._target_screen = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        player._wait = Mock()
        with package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player._execute_action({
                "type": "mouse_button", "mode": "absolute", "x": -960, "y": 540,
                "button": "left", "down": True,
            }, None)
        move.assert_called_once_with(480, 270)
        button.assert_called_once_with("left", True)

    def test_relative_mouse_button_does_not_teleport_cursor(self):
        player = MacroPlayer()
        with package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player._execute_action({
                "type": "mouse_button", "mode": "relative", "x": 100, "y": 200,
                "button": "right", "down": True,
            }, None)
        move.assert_not_called()
        button.assert_called_once_with("right", True)

    def test_stop_during_mouse_button_hold_releases_button(self):
        player = MacroPlayer()
        player._wait = lambda _milliseconds: player.stop()
        with package_patch('player', 'send_button') as button:
            player.play([
                {"type": "mouse_button", "button": "left", "down": True},
                {"type": "delay", "ms": 1},
            ])
        self.assertEqual(button.call_args_list, [call("left", True), call("left", False)])

    def test_recorded_turn_uses_pulse_duration_including_zero(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._center_cursor_for_turn = Mock()
        with package_patch('player', 'send_move_relative'):
            player._execute_action({
                "type": "turn", "dx": 6, "dy": 0, "steps": 3,
                "pulse_duration_ms": 0, "duration_ms": 10,
            }, None)
        self.assertEqual(
            [item.args[0] for item in player._wait.call_args_list], [0, 0, 0],
        )

        player._wait.reset_mock()
        with package_patch('player', 'send_move_relative'):
            player._execute_action({
                "type": "turn", "dx": 6, "dy": 0, "steps": 3,
                "pulse_duration_ms": 24,
            }, None)
        self.assertEqual(
            [item.args[0] for item in player._wait.call_args_list], [8, 8, 8],
        )

    def test_manual_turn_keeps_explicit_duration(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._center_cursor_for_turn = Mock()
        with package_patch('player', 'send_move_relative'):
            player._execute_action({
                "type": "turn", "dx": 2, "dy": 0, "steps": 2,
                "duration_ms": 14,
            }, None)
        self.assertEqual(
            [item.args[0] for item in player._wait.call_args_list], [7, 7],
        )

    def test_stop_cleanup_metric_measures_from_stop_request_to_release(self):
        timing = []
        player = MacroPlayer(on_timing=timing.append)
        player._wait = lambda _milliseconds: player.stop()
        # 时钟顺序：播放开始 → 每个动作 2 次（等待起点/终点）+ 2 次（动作耗时）
        # → 动作边界 mark_boundary → 收尾。停止请求记在 _wait 里的 stop()，
        # 清理耗时 = 收尾时刻 - 停止请求时刻 = 100.040 - 100.005。
        with patch("macroflow.execution.player.time.perf_counter", side_effect=[
            99.995,                                       # play() 起始
            100.000, 100.005, 100.010, 100.015,           # 动作 1
            100.020, 100.025, 100.030, 100.035,           # 动作 2（_wait 中请求停止）
            100.040,                                      # mark_boundary
            100.040,                                      # play() 收尾 1
            100.040,                                      # play() 收尾 2
        ]), package_patch('player', 'send_button'):
            player.play([
                {"type": "mouse_button", "button": "left", "down": True},
                {"type": "delay", "ms": 1},
            ])
        self.assertAlmostEqual(timing[0]["stop_cleanup_ms"], 35.0)

    def test_release_all_clears_keys_and_buttons_when_a_release_raises(self):
        player = MacroPlayer()
        player._held_keys.add(65)
        player._held_buttons.add("left")
        with package_patch('player', 'send_key', side_effect=RuntimeError('key')) as key, \
             package_patch('player', 'send_button', side_effect=RuntimeError('button')) as button:
            player._release_all(None)
        key.assert_called_once_with(65, False)
        button.assert_called_once_with("left", False)
        self.assertEqual(player._held_keys, set())
        self.assertEqual(player._held_buttons, set())

    def test_playback_speed_scales_waits_without_changing_hold_time(self):
        player = MacroPlayer()
        player.set_playback_speed(1.2)
        player._wait = Mock()
        player._execute_action = Mock(return_value=None)

        player._run_action_sequence([
            {"type": "key_press", "delay_ms": 120, "hold_ms": 300},
            {"type": "delay", "delay_ms": 240},
        ], None)

        self.assertEqual(
            [call.args[0] for call in player._wait.call_args_list],
            [100, 200],
        )

    def test_playback_speed_scales_recorded_timeline(self):
        # 录制脚本的节奏由 recorded_at_ms 的绝对时间轴决定：倍速必须作用在
        # 时间轴上，否则侧栏「Playback speed」对真实录制脚本毫无影响。
        player = MacroPlayer()
        player.set_playback_speed(2.0)
        waits = []
        player._wait = waits.append
        player._execute_action = Mock(return_value=None)

        player.play([
            {"type": "delay", "ms": 0, "recorded_at_ms": 0.0},
            {"type": "delay", "ms": 0, "recorded_at_ms": 1000.0},
        ])

        self.assertEqual(len(waits), 1)
        self.assertAlmostEqual(waits[0], 500, delta=5)

    def test_clone_actions_remaps_ocr_compare_jump_targets(self):
        # 数字比较动作的跳转字段名与识图不同，复制时漏映射会让目标脚本
        # 指向源脚本的行 ID，运行到该分支时报“跳转目标动作已被删除”。
        source = [
            {"type": "ocr_compare", ACTION_ID_KEY: "a",
             "equal_jump_action_id": "b", "not_equal_jump_action_id": "a"},
            {"type": "delay", ACTION_ID_KEY: "b"},
        ]

        clones = clone_actions_with_new_ids(source)

        self.assertEqual(clones[0]["equal_jump_action_id"], clones[1][ACTION_ID_KEY])
        self.assertEqual(clones[0]["not_equal_jump_action_id"], clones[0][ACTION_ID_KEY])
        self.assertTrue({action[ACTION_ID_KEY] for action in clones}.isdisjoint({"a", "b"}))

    def test_execute_action_does_not_recheck_foreground_for_each_action(self):
        player = MacroPlayer()
        player._ensure_foreground_for_input = Mock()

        player._execute_action({"type": "comment", "text": "no-op"}, None)

        player._ensure_foreground_for_input.assert_not_called()

    def test_ensure_foreground_activates_target_when_not_foreground(self):
        # 目标窗口不在前台时激活它。
        player = MacroPlayer()
        player._activate_target = True
        player._relative_target_hwnd = 50
        player._status = Mock()
        with patch.object(player, "_input_target_hwnd", return_value=50), \
             package_patch('player', 'is_window_process_foreground', return_value=False), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player._ensure_foreground_for_input(None)
        activate.assert_called_once_with(50)

    def test_ensure_foreground_skips_when_target_is_foreground(self):
        player = MacroPlayer()
        player._activate_target = True
        player._relative_target_hwnd = 50
        player._status = Mock()
        with patch.object(player, "_input_target_hwnd", return_value=50), \
             package_patch('player', 'is_window_process_foreground', return_value=True), \
             package_patch('player', 'activate_window') as activate:
            player._ensure_foreground_for_input(None)
        activate.assert_not_called()

    def test_restore_target_foreground_activates_target(self):
        player = MacroPlayer()
        player._activate_target = True
        player._status = Mock()
        with patch.object(player, "_input_target_hwnd", return_value=50), \
             package_patch('player', 'is_window_process_foreground', return_value=False), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player._restore_target_foreground(None)
        activate.assert_called_once_with(50)

    def test_long_wait_does_not_recheck_foreground(self):
        # 长等待期间只轮询守卫，不再反复检测或激活目标窗口。
        player = MacroPlayer()
        player.on_guard_poll = Mock(return_value=None)
        player.stop_event = Mock()
        player.stop_event.wait.return_value = False
        player._ensure_foreground_for_input = Mock()
        player._wait(600)
        player._ensure_foreground_for_input.assert_not_called()

    def test_short_wait_skips_foreground_guard(self):
        player = MacroPlayer()
        player.on_guard_poll = Mock(return_value=None)
        player.stop_event = Mock()
        player.stop_event.wait.return_value = False
        player._ensure_foreground_for_input = Mock()
        player._wait(100)
        player._ensure_foreground_for_input.assert_not_called()

    def test_module_failure_runs_row_level_failure_segment(self):
        # 插入的模块失败后先跑脚本行自己的失败代码段，再走失败分支。
        player = MacroPlayer()
        segments = []
        player._run_action_sequence = Mock(
            side_effect=lambda actions, hwnd, **kwargs: segments.append(list(actions)),
        )
        module = {"name": "测试模块", "template": "x.png"}
        action = {
            "type": "image_match", "module_ref": True,
            "on_timeout": "continue",
            "failure_segment_enabled": True,
            "failure_actions": [{"type": "notice", "text": "识别失败"}],
        }

        result = player._module_result_route(action, module, succeeded=False, hwnd=None)

        self.assertIsNone(result)
        player._run_action_sequence.assert_called_once()
        self.assertEqual(segments[0][0]["type"], "notice")

    def test_module_failure_segment_runs_before_failure_jump(self):
        player = MacroPlayer()
        player._run_action_sequence = Mock()
        module = {"name": "测试模块", "template": "x.png"}
        action = {
            "type": "image_match", "module_ref": True,
            "on_timeout": "jump", "timeout_jump_action_id": "fallback-row",
            "failure_segment_enabled": True,
            "failure_actions": [{"type": "delay", "ms": 10}],
        }

        result = player._module_result_route(action, module, succeeded=False, hwnd=None)

        self.assertEqual(result, ("action_id", "fallback-row"))
        player._run_action_sequence.assert_called_once()

    def test_module_success_does_not_run_failure_segment(self):
        player = MacroPlayer()
        player._run_action_sequence = Mock()
        module = {"name": "测试模块", "template": "x.png"}
        action = {
            "type": "image_match", "module_ref": True,
            "on_found": "continue",
            "failure_segment_enabled": True,
            "failure_actions": [{"type": "notice", "text": "识别失败"}],
        }

        result = player._module_result_route(action, module, succeeded=True, hwnd=None)

        self.assertIsNone(result)
        player._run_action_sequence.assert_not_called()

    def test_module_reference_dialog_saves_failure_segment(self):
        dialog_class = dialog_module.ModuleReferenceDelayDialog
        dialog = dialog_class.__new__(dialog_class)
        dialog.action = {"type": "image_match", "module_ref": True}
        dialog.delay = Mock(**{"get.return_value": "0"})
        dialog.after_delay = Mock(**{"get.return_value": "0"})
        dialog.blocking_module = False
        dialog.result_routes_enabled = True
        dialog.number_routes_enabled = False
        dialog.failure_segment_enabled = Mock()
        dialog.failure_segment_enabled.get.return_value = True
        dialog.failure_segment = [{"type": "notice", "text": "识别失败"}]
        dialog.on_success = Mock(**{"get.return_value": "继续下一行"})
        dialog.on_failure = Mock(**{"get.return_value": "继续下一行"})
        dialog.success_target = Mock(**{"get.return_value": ""})
        dialog.failure_target = Mock(**{"get.return_value": ""})
        dialog.jump_target_ids = {}
        dialog.destroy = Mock()

        dialog.save()

        self.assertTrue(dialog.result["failure_segment_enabled"])
        self.assertEqual(dialog.result["failure_actions"][0]["type"], "notice")
        dialog.destroy.assert_called_once()

    def test_row_list_failure_runs_failure_segment_before_branch(self):
        # 列表逐行点击失败：先跑本行失败代码段，再走“失败后”分支。
        player = MacroPlayer()
        segments = []
        player._run_action_sequence = Mock(
            side_effect=lambda actions, hwnd, **kwargs: segments.append(list(actions)),
        )
        action = {
            "type": "row_list_condition_click",
            "on_timeout": "continue",
            "failure_segment_enabled": True,
            "failure_actions": [{"type": "notice", "text": "列表没找到"}],
        }

        result = player._row_list_result_route(action, succeeded=False)

        self.assertIsNone(result)
        player._run_action_sequence.assert_called_once()
        self.assertEqual(segments[0][0]["text"], "列表没找到")

    def test_row_list_executor_forwards_script_stack_to_result_route(self):
        # 行级失败代码段要沿用调用方的脚本栈与嵌套深度，避免段内再引用
        # 脚本时无限递归。
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(return_value=False)
        player._row_list_result_route = Mock(return_value=None)
        stack = {"scripts/self.json"}
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"}, "right_condition": {"type": "number"},
            "no_match_action": "finish",
        }
        screen = np.zeros((26, 160, 3), dtype=np.uint8)

        with package_patch('player', 'capture_bgr', return_value=(screen, (100, 200)), create=True):
            player._execute_row_list_condition_click(action, None, stack, 2)

        kwargs = player._row_list_result_route.call_args.kwargs
        self.assertEqual(kwargs["script_stack"], stack)
        self.assertEqual(kwargs["depth"], 2)

    def test_row_list_success_does_not_run_failure_segment(self):
        player = MacroPlayer()
        player._run_action_sequence = Mock()
        action = {
            "type": "row_list_condition_click",
            "on_found": "continue",
            "failure_segment_enabled": True,
            "failure_actions": [{"type": "notice", "text": "列表没找到"}],
        }

        result = player._row_list_result_route(
            action, succeeded=True, subject="列表逐行点击",
        )

        self.assertIsNone(result)
        player._run_action_sequence.assert_not_called()

    def test_no_recognition_module_executes_directly_without_image_matching(self):
        player = MacroPlayer()
        waits = []
        logs = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        player._trace = lambda text, **_kwargs: logs.append(text)
        module = {
            "name": "直接动作", "recognize": "none", "delay_ms": 120,
            "after_action": "continue", "run_code_after_action": True,
            "on_success_actions": [{"type": "delay", "ms": 25}],
        }
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template') as find, \
             patch.object(player, "_run_action_sequence") as run_segment:
            result = player._execute_image({
                "type": "image_match", "module_ref": True,
                "module_key": "module:direct", "template": "",
            }, None)
        self.assertIsNone(result)
        self.assertEqual(waits, [120])
        find.assert_not_called()
        run_segment.assert_called_once()
        self.assertIn("无需识图", logs[0])

    def test_number_module_reads_region_and_routes_equal_to_success_target(self):
        player = MacroPlayer()
        player._wait = lambda _milliseconds: None
        module = {
            "name": "剩余次数", "recognize": "number", "region": [10, 20, 80, 30],
            "blocking": False, "interval_ms": 50, "not_found_timeout_ms": 1000,
        }
        action = {
            "type": "image_match", "module_ref": True, "module_key": "module:number",
            "region_mode": "template", "expected_number": 127,
            "on_found": "jump", "found_jump_action_id": "equal-target",
            "on_timeout": "jump", "timeout_jump_action_id": "other-target",
        }
        boxes = [
            {"text": "7", "x": 130}, {"text": "1", "x": 10},
            {"text": "2", "x": 70},
        ]
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'recognize_region_with_boxes', return_value=('721', boxes)) as read, \
             package_patch('player', 'find_template') as find:
            result = player._execute_image(action, None)
        self.assertEqual(result, ("action_id", "equal-target"))
        read.assert_called_once_with((10, 20, 80, 30))
        find.assert_not_called()

    def test_number_module_routes_not_equal_immediately_to_failure_target(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        module = {
            "name": "剩余次数", "recognize": "number", "region": [1, 2, 3, 4],
            "blocking": True, "interval_ms": 50, "not_found_timeout_ms": 9999,
        }
        action = {
            "type": "image_match", "module_ref": True, "module_key": "module:number",
            "region_mode": "template", "expected_number": 5,
            "on_found": "jump", "found_jump_action_id": "equal-target",
            "on_timeout": "jump", "timeout_jump_action_id": "other-target",
        }
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'recognize_region_with_boxes', return_value=('4', [{'text': '4', 'x': 1}])) as read:
            result = player._execute_image(action, None)
        self.assertEqual(result, ("action_id", "other-target"))
        read.assert_called_once()
        self.assertIn("模块 剩余次数 比较结果：不相等", logs)
        self.assertFalse(any("执行结果：失败" in text for text in logs))

    def test_number_module_retries_no_digits_then_compares(self):
        player = MacroPlayer()
        player._wait = lambda _milliseconds: None
        module = {
            "name": "层数", "recognize": "number", "region": [1, 2, 30, 40],
            "blocking": True, "interval_ms": 50, "not_found_timeout_ms": 0,
        }
        action = {
            "type": "image_match", "module_ref": True, "module_key": "module:number",
            "region_mode": "template", "expected_number": 7,
            "on_found": "jump", "found_jump_action_id": "equal-target",
            "on_timeout": "jump", "timeout_jump_action_id": "other-target",
        }
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'recognize_region_with_boxes', side_effect=[('加载', []), ('００７', [{'text': '００７', 'x': 1}])]) as read:
            result = player._execute_image(action, None)
        self.assertEqual(result, ("action_id", "equal-target"))
        self.assertEqual(read.call_count, 2)

    def test_number_module_no_digits_timeout_uses_failure_target(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        module = {
            "name": "层数", "recognize": "number", "region": [1, 2, 30, 40],
            "blocking": False, "interval_ms": 50, "not_found_timeout_ms": 0,
        }
        action = {
            "type": "image_match", "module_ref": True, "module_key": "module:number",
            "region_mode": "template", "expected_number": 7,
            "on_found": "jump", "found_jump_action_id": "equal-target",
            "on_timeout": "jump", "timeout_jump_action_id": "other-target",
        }
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'recognize_region_with_boxes', return_value=('加载', [])):
            result = player._execute_image(action, None)
        self.assertEqual(result, ("action_id", "other-target"))
        self.assertIn("模块 层数 读取结果：未读取到数字", logs)
        self.assertFalse(any("执行结果：失败" in text for text in logs))

    def test_number_module_requires_row_comparison_value(self):
        player = MacroPlayer()
        module = {
            "name": "层数", "recognize": "number", "region": [1, 2, 30, 40],
            "blocking": False, "interval_ms": 50, "not_found_timeout_ms": 0,
        }
        with package_patch('player', 'registered_module_object', return_value=module):
            with self.assertRaisesRegex(RuntimeError, "未设置比较数字"):
                player._execute_image({
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:number", "region_mode": "template",
                }, None)

    def setUp(self):
        # 识别成功时 player 会调用检测框提醒；测试中拦截，避免创建真实窗口。
        patcher = package_patch('player', 'show_overlay')
        patcher.start()
        self.addCleanup(patcher.stop)
        self._row_list_capture = package_patch('player', 'capture_bgr', side_effect=lambda region: (np.zeros((region[3], region[2], 3), dtype=np.uint8), (region[0], region[1])))
        self._row_list_capture.start()
        self.addCleanup(self._row_list_capture.stop)

    def test_script_scope_reentered_on_each_repeat(self):
        # 关卡封装"执行 x 次"时，每次重复都要重新进入脚本全局作用域，
        # 让全局模块（如超时计时）从本次重复开始重新注册、重新计时。
        entered = []
        exited = []
        player = MacroPlayer(
            on_script_scope_enter=lambda actions, origin_row=0, last_row=None: entered.append(1) or (),
            on_script_scope_exit=lambda keys: exited.append(1),
        )
        player._status = lambda text: None
        player.play([{"type": "delay", "ms": 0, "delay_ms": 0}], repeats=3)
        self.assertEqual(len(entered), 3)
        self.assertEqual(len(exited), 3)
        # 每次进入时上一次作用域必须已退出，避免监控叠加。
        self.assertEqual(
            entered, exited,
            "每次重复进入前应退出上一次的全局作用域",
        )

    def test_script_scope_single_repeat_enters_and_exits_once(self):
        entered = []
        exited = []
        player = MacroPlayer(
            on_script_scope_enter=lambda actions, origin_row=0, last_row=None: entered.append(1) or (),
            on_script_scope_exit=lambda keys: exited.append(1),
        )
        player._status = lambda text: None
        player.play([{"type": "delay", "ms": 0, "delay_ms": 0}], repeats=1)
        self.assertEqual(len(entered), 1)
        self.assertEqual(len(exited), 1)

    def test_script_scope_enter_receives_the_starting_row(self):
        # 「▶ 从此开始执行」：作用域进入时要把本次播放的起始行交给应用层，
        # 否则起始行之前的全局模块行会被照常启用（表现为“还是从头执行”）。
        rows = []
        player = MacroPlayer(
            on_script_scope_enter=(
                lambda actions, origin_row=0, last_row=None: rows.append(origin_row) or ()
            ),
        )
        player._status = lambda text: None
        actions = [
            {"type": "notice", "text": "一", "duration_ms": 1},
            {"type": "notice", "text": "二", "duration_ms": 1},
            {"type": "notice", "text": "三", "duration_ms": 1},
        ]

        player.play(actions, 1, None, start_index=2)

        self.assertEqual(rows, [2])

    def test_script_scope_exits_before_repeat_completion_and_interval(self):
        events = []
        player = MacroPlayer(
            on_script_scope_enter=(
                lambda _actions, origin_row=0, last_row=None: events.append("enter") or "scope"
            ),
            on_script_scope_exit=lambda token: events.append(("exit", token)),
        )
        player._status = lambda _text: None
        player._wait = lambda milliseconds: (
            events.append(("interval", milliseconds)) if milliseconds else None
        )

        player.play(
            [{"type": "delay", "ms": 0, "delay_ms": 0}],
            repeats=2,
            repeat_interval_ms=25,
            on_repeat_complete=lambda current, total: events.append(
                ("complete", current, total),
            ),
        )

        self.assertEqual(
            events,
            [
                "enter", ("exit", "scope"), ("complete", 1, 2), ("interval", 25),
                "enter", ("exit", "scope"), ("complete", 2, 2),
            ],
        )

    def test_image_timeout_jump_follows_target_action_after_row_insert(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        actions = [
            {
                "type": "image_match", ACTION_ID_KEY: "jump",
                "template": "images/目标.png", "timeout_ms": 0, "delay_ms": 0,
                "on_timeout": "jump", "timeout_jump_action_id": "target",
            },
            {"type": "comment", "text": "后来插入的行", ACTION_ID_KEY: "inserted"},
            {"type": "unknown_must_be_skipped", ACTION_ID_KEY: "skip"},
            {
                "type": "notice", "text": "到达目标动作", "duration_ms": 1000,
                ACTION_ID_KEY: "target",
            },
        ]
        with package_patch('player', 'find_template', return_value=None):
            player.play(actions)
        self.assertEqual(notices, [("到达目标动作", 1000)])

    def test_play_can_start_from_selected_action_index(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))

        player.play([
            {"type": "unknown_must_be_skipped"},
            {"type": "notice", "text": "从第二行开始", "duration_ms": 1000},
        ], start_index=1)

        self.assertEqual(notices, [("从第二行开始", 1000)])

    def test_image_timeout_can_jump_to_one_based_action_row(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        with package_patch('player', 'find_template', return_value=None):
            player.play([
                {
                    "type": "image_match", "template": "images/目标.png",
                    "timeout_ms": 0, "delay_ms": 0,
                    "on_timeout": "jump", "timeout_jump_row": 3,
                },
                {"type": "unknown_must_be_skipped"},
                {"type": "notice", "text": "已跳转", "duration_ms": 1000},
            ])
        self.assertEqual(notices, [("已跳转", 1000)])

    def test_image_found_jump_follows_target_action_after_row_insert(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        actions = [
            {
                "type": "image_match", ACTION_ID_KEY: "jump",
                "template": "images/目标.png", "timeout_ms": 0, "delay_ms": 0,
                "on_found": "jump", "found_jump_action_id": "target",
            },
            {"type": "comment", "text": "后来插入的行", ACTION_ID_KEY: "inserted"},
            {"type": "notice", "text": "找到后跳转成功", "duration_ms": 1000, ACTION_ID_KEY: "target"},
        ]
        with package_patch('player', 'find_template', return_value=match):
            player.play(actions)
        self.assertEqual(notices, [("找到后跳转成功", 1000)])

    def test_image_found_can_jump_to_one_based_action_row(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', return_value=match):
            player.play([
                {
                    "type": "image_match", "template": "images/目标.png",
                    "timeout_ms": 0, "delay_ms": 0,
                    "on_found": "jump", "found_jump_row": 3,
                },
                {"type": "unknown_must_be_skipped"},
                {"type": "notice", "text": "已跳转", "duration_ms": 1000},
            ])
        self.assertEqual(notices, [("已跳转", 1000)])

    def test_image_timeout_waits_before_jump(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'find_template', return_value=None):
            result = player._execute_image({
                "template": "images/目标.png",
                "timeout_ms": 0,
                "on_timeout": "jump",
                "timeout_jump_row": 3,
                "timeout_delay_ms": 500,
                "show_result_notice": False,
            }, None)
        self.assertEqual(result, ("row", 3))
        self.assertIn(500, waits)

    def test_image_timeout_waits_before_continue(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'find_template', return_value=None):
            result = player._execute_image({
                "template": "images/目标.png",
                "timeout_ms": 0,
                "on_timeout": "continue",
                "timeout_delay_ms": 800,
                "show_result_notice": False,
            }, None)
        self.assertIsNone(result)
        self.assertIn(800, waits)

    def test_image_wait_forever_blocks_until_found(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', side_effect=[None, None, match]):
            result = player._execute_image({
                "template": "images/目标.png",
                "timeout_ms": 0,
                "wait_forever": True,
                "on_found": "continue",
                "show_result_notice": False,
                "interval_ms": 100,
            }, None)
        self.assertIsNone(result)
        self.assertEqual(waits, [100, 100, 0])

    def test_image_wait_forever_switches_to_fallback_after_timeout(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                return None if len(calls) < 3 else dict(main_match)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button') as button:
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        self.assertEqual(
            [Path(call).name for call in calls],
            ["main.png", "fallback.png", "main.png"],
        )
        move.assert_called_once_with(200, 300)
        button.assert_called()
        self.assertEqual(button.call_args_list[0].args, ("left", True))

    def test_image_template_region_mode_reads_registered_region(self):
        # v1.78：region_mode="template" → 区域从模板登记表读取并传给 find_template。
        player = MacroPlayer()
        match = {"x": 10, "y": 20, "width": 30, "height": 40,
                 "center_x": 25, "center_y": 40, "score": 0.95}
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            template_png = Path(folder) / "main.png"
            template_png.write_bytes(b"x")
            with package_patch('player', 'find_template', return_value=match) as find, \
                 package_patch('player', 'registered_template_region', return_value=[100, 50, 300, 200]), \
                 package_patch('player', 'send_move_absolute'), package_patch('player', 'send_button'):
                player._execute_image({
                    "template": str(template_png),
                    "region_mode": "template",
                    "timeout_ms": 0,
                    "on_found": "continue",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertEqual(find.call_args.args[0], template_png)
        # 未配置源/目标屏幕时缩放为恒等：区域原样传给识图。
        self.assertEqual(find.call_args.args[2], (100, 50, 300, 200))

    def test_image_template_region_mode_without_region_uses_fullscreen(self):
        # 模板未登记 / 未设置区域：全屏识别并一次性告警。
        player = MacroPlayer()
        statuses = []
        player.on_status = lambda text: statuses.append(text)
        match = {"x": 10, "y": 20, "width": 30, "height": 40,
                 "center_x": 25, "center_y": 40, "score": 0.95}
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            template_png = Path(folder) / "main.png"
            template_png.write_bytes(b"x")
            with package_patch('player', 'find_template', return_value=match) as find, \
                 package_patch('player', 'registered_template_region', return_value=None), \
                 package_patch('player', 'send_move_absolute'), package_patch('player', 'send_button'):
                player._execute_image({
                    "template": str(template_png),
                    "region_mode": "template",
                    "timeout_ms": 0,
                    "on_found": "continue",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(find.call_args.args[2])
        self.assertTrue(any("未设置区域，按全屏识别" in text for text in statuses))

    def test_ocr_hit_continues(self):
        # 识别文字命中：按 found_delay 等待后继续执行。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'recognize_region', return_value='体力不足，请补充'):
            result = player._execute_text_ocr({
                "expected_text": "体力不足",
                "timeout_ms": 0,
                "interval_ms": 500,
                "on_found": "continue",
                "found_delay_ms": 200,
                "on_timeout": "continue",
                "show_result_notice": False,
            }, None)
        self.assertIsNone(result)
        self.assertEqual(waits, [200])

    def test_ocr_compare_equal_clicks_custom_click_region(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._click_module_point = Mock()
        with package_patch('player', 'recognize_region_with_boxes', return_value=('12/12', [])) as recognize:
            try:
                result = player._execute_action({
                    "type": "ocr_compare",
                    "region_mode": "custom",
                    "region": [10, 20, 300, 400],
                    "separator": "/",
                    "click_region": [100, 200, 50, 40],
                    "button": "left",
                    "equal_action": "click",
                    "equal_click_count": 3,
                    "not_equal_action": "continue",
                    "timeout_ms": 0,
                }, None)
            except RuntimeError as exc:
                self.fail(f"识别数字比较动作未实现：{exc}")
        self.assertIsNone(result)
        recognize.assert_called_once_with((10, 20, 300, 400))
        player._click_module_point.assert_called_once_with(125, 220, "left", 3, None)

    def test_ocr_compare_not_equal_jumps_to_selected_action(self):
        player = MacroPlayer()
        player._wait = Mock()
        with package_patch('player', 'recognize_region_with_boxes', return_value=('12/34', [])):
            try:
                result = player._execute_action({
                    "type": "ocr_compare",
                    "region_mode": "custom",
                    "region": [10, 20, 300, 400],
                    "separator": "/",
                    "click_region": [100, 200, 50, 40],
                    "not_equal_action": "jump",
                    "not_equal_jump_action_id": "row-b",
                    "equal_action": "continue",
                    "timeout_ms": 0,
                }, None)
            except RuntimeError as exc:
                self.fail(f"识别数字比较动作未实现：{exc}")
        self.assertEqual(result, ("action_id", "row-b"))

    def test_ocr_compare_invalid_text_uses_timeout_branch(self):
        player = MacroPlayer()
        player._wait = Mock()
        with package_patch('player', 'recognize_region_with_boxes', return_value=('没有有效格式', [])), patch(
            "macroflow.execution.player.time.perf_counter",
            side_effect=[100.0, 101.0],
        ):
            result = player._execute_action({
                "type": "ocr_compare",
                "region_mode": "custom",
                "region": [10, 20, 300, 400],
                "separator": "/",
                "click_region": [100, 200, 50, 40],
                "on_timeout": "jump",
                "timeout_jump_action_id": "timeout-row",
                "timeout_ms": 50,
            }, None)
        self.assertEqual(result, ("action_id", "timeout-row"))

    def test_multi_condition_click_requires_image_ocr_and_number_all_match(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._click_module_point = Mock()
        image_match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        action = {
            "type": "multi_condition_click",
            "conditions": [
                {
                    "enabled": True, "type": "image", "template": "button.png",
                    "threshold": 0.9, "region": [10, 20, 100, 80],
                },
                {
                    "enabled": True, "type": "ocr", "expected_text": "完成",
                    "match_mode": "contains", "region": [200, 20, 120, 40],
                },
                {
                    "enabled": True, "type": "ocr", "ocr_mode": "number", "separator": "/",
                    "relation": "equal", "region": [400, 20, 120, 40],
                },
            ],
            "click_region": [600, 200, 50, 40],
            "button": "left", "click_count": 4,
            "timeout_ms": 0, "interval_ms": 200,
        }
        with package_patch('player', 'find_template', return_value=image_match) as find, package_patch('player', 'recognize_region_with_boxes', side_effect=[('完成', [{'text': '完成'}]), ('8/8', [])]) as recognize:
            try:
                result = player._execute_action(action, None)
            except RuntimeError as exc:
                self.fail(f"多条件识图点击动作未实现：{exc}")
        self.assertIsNone(result)
        find.assert_called_once()
        self.assertEqual(find.call_args.args[2], (10, 20, 100, 80))
        recognize.assert_has_calls([call((200, 20, 120, 40)), call((400, 20, 120, 40))])
        player._click_module_point.assert_called_once_with(625, 220, "left", 4, None)

    def test_multi_condition_image_module_uses_its_live_bound_region(self):
        player = MacroPlayer()
        condition = {
            "enabled": True, "type": "image", "module_ref": True,
            "module_key": "module:first", "template": "images/stale.png",
            "region": [1, 2, 3, 4], "threshold": 0.5,
        }
        module_obj = {
            "template": "images/shared.png", "region": [11, 22, 333, 444],
            "threshold": 0.91, "ignore_background": True,
        }
        with package_patch('player', 'registered_module_object', return_value=module_obj), package_patch('player', 'find_template', return_value={'center_x': 20, 'center_y': 30}) as find:
            matched = player._multi_condition_matches(condition, None)

        self.assertTrue(matched)
        find.assert_called_once_with(
            resolve_path("images/shared.png"), 0.91, (11, 22, 333, 444),
            ignore_background=True, scale=1.0,
        )

    def test_multi_condition_click_does_not_click_when_one_condition_is_missing(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._click_module_point = Mock()
        action = {
            "type": "multi_condition_click",
            "conditions": [
                {
                    "enabled": True, "type": "image", "template": "button.png",
                    "threshold": 0.9, "region": [10, 20, 100, 80],
                },
                {
                    "enabled": True, "type": "ocr", "expected_text": "完成",
                    "match_mode": "contains", "region": [200, 20, 120, 40],
                },
                {"enabled": False, "type": "ocr", "ocr_mode": "number"},
            ],
            "click_region": [600, 200, 50, 40],
            "button": "left", "click_count": 4,
            "timeout_ms": 0, "interval_ms": 200,
        }
        with package_patch('player', 'find_template', return_value=None), package_patch('player', 'recognize_region_with_boxes') as recognize:
            try:
                result = player._execute_action(action, None)
            except RuntimeError as exc:
                self.fail(f"多条件识图点击动作未实现：{exc}")
        self.assertIsNone(result)
        recognize.assert_not_called()
        player._click_module_point.assert_not_called()

    def test_multi_condition_click_number_not_equal_condition(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._click_module_point = Mock()
        action = {
            "type": "multi_condition_click",
            "conditions": [
                {
                    "enabled": True, "type": "ocr", "ocr_mode": "number", "separator": "/",
                    "relation": "not_equal", "region": [400, 20, 120, 40],
                },
                {"enabled": False, "type": "image"},
                {"enabled": False, "type": "ocr"},
            ],
            "click_region": [600, 200, 50, 40],
            "button": "right", "click_count": 2,
            "timeout_ms": 0, "interval_ms": 200,
        }
        with package_patch('player', 'recognize_region_with_boxes', return_value=('5/6', [])):
            try:
                result = player._execute_action(action, None)
            except RuntimeError as exc:
                self.fail(f"多条件数字比较条件未实现：{exc}")
        self.assertIsNone(result)
        player._click_module_point.assert_called_once_with(625, 220, "right", 2, None)

    def test_multi_condition_ocr_number_mode_requires_numeric_pair(self):
        player = MacroPlayer()
        condition = {
            "enabled": True, "type": "ocr", "ocr_mode": "number",
            "separator": "/", "relation": "equal", "region": [10, 20, 120, 40],
        }
        with package_patch('player', 'recognize_region_with_boxes', return_value=('没有数字对', [])):
            self.assertFalse(player._multi_condition_matches(condition, None))

    def test_row_list_clicks_first_matching_row_and_stops_scanning(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(side_effect=[True, False, True, True])
        player._click_module_point = Mock()
        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 78], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "button": "left", "no_match_action": "finish",
        }, None)
        self.assertEqual(player._row_list_condition_matches.call_args_list, [
            call({"type": "number"}, (100, 200, 70, 26)),
            call({"type": "text"}, (180, 200, 80, 26)),
            call({"type": "number"}, (100, 226, 70, 26)),
            call({"type": "text"}, (180, 226, 80, 26)),
        ])
        player._click_module_point.assert_called_once_with(220, 239, "left", 1, None)

    def test_row_list_diagnostic_scans_and_logs_both_conditions_for_every_row(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        player._row_list_condition_matches = Mock(return_value=False)
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 78], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number", "separator": "/", "relation": "not_equal"},
            "right_condition": {"type": "text", "expected_text": "游戏中"},
        }

        with package_patch('player', 'capture_bgr', return_value=(np.zeros((78, 160, 3), dtype=np.uint8), (100, 200))):
            player._diagnose_row_list_condition_click(action, None)

        self.assertEqual(player._row_list_condition_matches.call_count, 6)
        for row_number in (1, 2, 3):
            self.assertTrue(any(f"第{row_number}行结果" in text for text in logs), logs)
        self.assertTrue(any("识别诊断" in text for text in logs), logs)

    def test_row_list_diagnostic_can_send_results_to_a_separate_sink(self):
        logs = []
        results = []
        player = MacroPlayer(on_log=logs.append)
        player._row_list_condition_matches = Mock(return_value=False)
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number", "separator": "/", "relation": "not_equal"},
            "right_condition": {"type": "text", "expected_text": "游戏中"},
        }

        with package_patch('player', 'capture_bgr', return_value=(np.zeros((26, 160, 3), dtype=np.uint8), (100, 200))):
            player._diagnose_row_list_condition_click(action, None, result_sink=results.append)

        self.assertEqual(results, logs)
        self.assertTrue(any("第1行结果" in text for text in results), results)

    def test_row_list_diagnostic_returns_screen_snapshot_and_cells_for_result_window(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(return_value=True)
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 78], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number", "separator": "/", "relation": "not_equal"},
            "right_condition": {"type": "text", "expected_text": "游戏中"},
        }
        snapshot = np.zeros((78, 160, 3), dtype=np.uint8)

        with package_patch('player', 'capture_bgr', return_value=(snapshot, (100, 200))):
            result = player._diagnose_row_list_condition_click(action, None)

        self.assertEqual(result["subject"], "列表逐行")
        self.assertIs(result["image_array"], snapshot)
        self.assertEqual(result["image_origin"], [100, 200])
        self.assertEqual((result["left_column"], result["right_column"], result["click_column"]),
                         (0, 1, 2))
        self.assertEqual(len(result["cells"]), 6)
        self.assertEqual(
            [(cell["row"], cell["column"]) for cell in result["cells"]],
            [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)],
        )
        self.assertTrue(all(cell["matched"] for cell in result["cells"]))
        self.assertEqual(result["cells"][0]["region"], [100, 200, 70, 26])

    def test_row_list_diagnostic_reads_chosen_image_without_capturing_screen(self):
        player = MacroPlayer()
        image = np.zeros((300, 300, 3), dtype=np.uint8)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        image_path = str(Path(temp_dir.name) / "list-test.png")
        Path(image_path).touch()
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 78], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "text", "expected_text": "10/10"},
            "right_condition": {"type": "text", "expected_text": "游戏中"},
        }
        with package_patch('player', 'load_image', return_value=image) as load_image, package_patch('player', 'capture_bgr', side_effect=AssertionError('选图测试不得重新截屏')), package_patch('player', 'recognize_image_with_boxes', side_effect=[('10/10', []), ('游戏中', []), ('3/12', []), ('游戏中', []), ('1/1', []), ('', []), ('', [])]):
            result = player._diagnose_row_list_condition_click(
                action, None, image_path=image_path,
            )

        load_image.assert_called_once_with(Path(image_path))
        self.assertEqual(result["subject"], "列表逐行")
        self.assertEqual(result["image_origin"], [0, 0])
        self.assertEqual(
            [cell["text"] for cell in result["cells"]],
            ["10/10", "游戏中", "3/12", "游戏中", "1/1", "未识别到文字"],
        )
        self.assertEqual(
            [cell["matched"] for cell in result["cells"]],
            [True, True, False, True, False, False],
        )

    def test_row_list_diagnostic_rejects_image_too_small_for_list_region(self):
        player = MacroPlayer()
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        image_path = str(Path(temp_dir.name) / "small.png")
        Path(image_path).touch()
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 78], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "text", "expected_text": "10/10"},
            "right_condition": {"type": "text", "expected_text": "游戏中"},
        }
        with package_patch('player', 'load_image', return_value=np.zeros((78, 160, 3), dtype=np.uint8)):
            with self.assertRaises(RuntimeError) as raised:
                player._diagnose_row_list_condition_click(
                    action, None, image_path=image_path,
                )
        self.assertIn("覆盖不到列表区域", str(raised.exception))

    def test_row_list_image_test_skips_screen_capture_and_window_hiding(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app._bound_hwnd = Mock()
        app._hide_macroflow_windows_for_diagnostic = Mock()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app._finish_row_list_diagnostic = Mock()
        app.player = Mock()
        action = {
            "type": "row_list_condition_click",
            "screenshot_path": "C:/images/list.png",
        }

        with patch("threading.Thread") as thread_class:
            app.test_row_list_condition_click(action, source="image")
            thread_class.call_args.kwargs["target"]()

        app._bound_hwnd.assert_not_called()
        app._hide_macroflow_windows_for_diagnostic.assert_not_called()
        diagnose_kwargs = app.player._diagnose_row_list_condition_click.call_args.kwargs
        self.assertEqual(diagnose_kwargs["image_path"], "C:/images/list.png")

    def test_row_list_image_test_requires_a_chosen_image(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app._notify = Mock()
        app.player = Mock()
        completed = []

        app.test_row_list_condition_click(
            {"type": "row_list_condition_click"},
            on_complete=lambda *args: completed.append(args),
            source="image",
        )

        app.player._diagnose_row_list_condition_click.assert_not_called()
        app._notify.assert_called_once()
        self.assertIn("选择图片", app._notify.call_args.args[1])
        self.assertEqual(completed[0][1], "未选择测试图片")

    def test_row_list_diagnostic_busy_notice_names_row_list_task(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app._row_list_diagnostic_running = True
        app._notify = Mock()

        app.test_row_list_condition_click({"type": "row_list_condition_click"})

        app._notify.assert_called_once()
        self.assertIn("列表逐行识别诊断", app._notify.call_args.args[1])

    def test_row_recognition_result_cell_label_includes_row_column_and_text(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        label_builder = getattr(dialog_class, "cell_label", None)
        actual = label_builder({
            "row": 2, "column": 3, "text": "奖励可领取",
        }) if label_builder else None
        self.assertEqual(
            actual,
            "第2行第3列\n奖励可领取",
        )

    def test_grid_result_scale_fits_selected_image_to_viewport(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        scale_builder = getattr(dialog_class, "display_scale", None)
        actual = scale_builder((1920, 1080), (1150, 700)) if scale_builder else None
        self.assertAlmostEqual(actual, 1150 / 1920)

    def test_grid_result_viewport_falls_back_when_canvas_is_not_laid_out(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        viewport_builder = getattr(dialog_class, "resolve_viewport_size", None)
        actual = viewport_builder((1, 1), (1180, 780)) if viewport_builder else None
        self.assertEqual(actual, (1100, 630))

    def test_grid_result_scale_applies_wheel_zoom_without_changing_aspect_ratio(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        scale_builder = getattr(dialog_class, "display_scale", None)
        actual = scale_builder((1920, 1080), (1150, 700), 2.0) if scale_builder else None
        self.assertAlmostEqual(actual, (1150 / 1920) * 2.0)

    def test_grid_result_wheel_zoom_is_bounded(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        zoom_builder = getattr(dialog_class, "next_zoom", None)
        zoom_in = zoom_builder(1.0, 120) if zoom_builder else None
        zoom_out = zoom_builder(0.25, -120) if zoom_builder else None
        self.assertAlmostEqual(zoom_in, 1.15)
        self.assertEqual(zoom_out, 0.25)

    def test_grid_result_cell_text_does_not_add_row_header_inside_cell(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        text_builder = getattr(dialog_class, "cell_text", None)
        actual = text_builder({
            "row": 2, "column": 3, "text": "奖励可领取",
        }) if text_builder else None
        self.assertEqual(actual, "奖励可领取")

    def test_grid_result_visible_columns_exclude_clicked_column(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        column_builder = getattr(dialog_class, "visible_result_columns", None)
        actual = column_builder({
            "left_column": 0, "right_column": 2, "click_column": 2,
        }) if column_builder else None
        self.assertEqual(actual, [0])

    def test_grid_result_toggle_switches_between_overlay_and_original_image(self):
        dialog_class = getattr(dialog_module, "RowRecognitionResultDialog", None)
        dialog = dialog_class.__new__(dialog_class)
        dialog.overlay_visible = True
        dialog.canvas = Mock()
        dialog.toggle_button = Mock()

        dialog._toggle_display_mode()

        self.assertFalse(dialog.overlay_visible)
        dialog.canvas.itemconfigure.assert_called_once_with("grid-result", state="hidden")
        dialog.toggle_button.configure.assert_called_once_with(text="显示识别结果")

        dialog.canvas.reset_mock()
        dialog.toggle_button.reset_mock()
        dialog._toggle_display_mode()

        self.assertTrue(dialog.overlay_visible)
        dialog.canvas.itemconfigure.assert_called_once_with("grid-result", state="normal")
        dialog.toggle_button.configure.assert_called_once_with(text="显示原图")

    def test_row_list_success_can_end_current_inner_script(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(side_effect=[True, True])
        player._click_module_point = Mock()

        result = player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "on_found": "end_current_script",
            "no_match_action": "finish",
        }, None)

        self.assertEqual(result, ("end_current_script", 0))
        player._click_module_point.assert_called_once()

    def test_row_list_success_can_jump_to_action_id(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(side_effect=[True, True])
        player._click_module_point = Mock()

        result = player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "on_found": "jump", "found_jump_action_id": "action-target",
            "no_match_action": "finish",
        }, None)

        self.assertEqual(result, ("action_id", "action-target"))

    def test_row_list_failure_can_end_current_inner_script(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(return_value=False)
        player._click_module_point = Mock()

        result = player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "on_timeout": "end_current_script",
            "no_match_action": "finish",
        }, None)

        self.assertEqual(result, ("end_current_script", 0))
        player._click_module_point.assert_not_called()

    def test_row_list_text_and_number_conditions(self):
        player = MacroPlayer()
        cases = [
            ({"type": "text", "expected_text": "刚开始", "match_mode": "equals"}, "刚开始", True),
            ({"type": "text", "expected_text": "刚开始", "match_mode": "equals"}, "游戏中", False),
            ({"type": "number", "separator": "/", "relation": "not_equal"}, "11/12", True),
            ({"type": "number", "separator": "/", "relation": "not_equal"}, "12/12", False),
            ({"type": "number", "separator": "/", "relation": "equal"}, "无效", False),
        ]
        for condition, recognized, expected in cases:
            with self.subTest(condition=condition, recognized=recognized), package_patch('player', 'recognize_region_with_boxes', return_value=(recognized, [])) as recognize:
                self.assertEqual(
                    player._row_list_condition_matches(condition, (100, 200, 70, 26)),
                    expected,
                )
                recognize.assert_called_once_with((100, 200, 70, 26))

    def test_row_list_logs_recognized_text_when_condition_misses(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        condition = {"type": "text", "expected_text": "刚开始", "match_mode": "equals"}

        with package_patch('player', 'recognize_region_with_boxes', return_value=('游戏中', [{'text': '游戏中'}])):
            matched = player._row_list_condition_matches(
                condition, (100, 200, 70, 26), "第1行左侧",
            )

        self.assertFalse(matched)
        self.assertTrue(any(
            "第1行左侧" in text
            and "游戏中" in text
            and "刚开始" in text
            and "未命中" in text
            for text in logs
        ), logs)

    def test_row_list_logs_empty_ocr_as_not_recognized(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        with package_patch('player', 'recognize_region_with_boxes', return_value=('', [])):
            matched = player._row_list_condition_matches(
                {"type": "number", "separator": "/", "relation": "not_equal"},
                (100, 200, 70, 26), "第1行左侧",
            )

        self.assertFalse(matched)
        self.assertTrue(any("未识别到文字" in text for text in logs), logs)

    def test_row_list_number_condition_rejects_unknown_relation(self):
        player = MacroPlayer()
        condition = {"type": "number", "separator": "/", "relation": "greater"}

        with package_patch('player', 'recognize_region_with_boxes', return_value=('11/12', [])):
            with self.assertRaisesRegex(RuntimeError, "greater"):
                player._row_list_condition_matches(condition, (100, 200, 70, 26))

    def test_row_list_image_condition_uses_live_module_with_row_region(self):
        player = MacroPlayer()
        condition = {"type": "image", "module_key": "module:first"}
        module = {
            "template": "images/live.png", "threshold": 0.91,
            "ignore_background": True,
        }
        row_region = (100, 200, 70, 26)
        with package_patch('player', 'registered_module_object', return_value=module), package_patch('player', 'find_template', return_value={}) as find:
            self.assertTrue(player._row_list_condition_matches(condition, row_region))
        find.assert_called_once_with(
            resolve_path("images/live.png"), 0.91, row_region,
            ignore_background=True, scale=1.0,
        )
        with package_patch('player', 'registered_module_object', return_value=None):
            with self.assertRaisesRegex(RuntimeError, "module:first"):
                player._row_list_condition_matches(condition, row_region)

    def test_row_list_image_condition_logs_module_name_instead_of_stable_id(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        condition = {
            "type": "image",
            "module_key": "module:68ce87a9d03541d7a9abc4a817e794d9",
        }
        module = {
            "name": "右侧条件模块",
            "template": "images/right.png",
            "threshold": 0.9,
        }
        with package_patch('player', 'registered_module_object', return_value=module), package_patch('player', 'find_template', return_value={}):
            self.assertTrue(
                player._row_list_condition_matches(
                    condition, (100, 200, 70, 26), "第1行右侧",
                )
            )

        self.assertEqual(logs, ["第1行右侧 图片识别：右侧条件模块；命中"])

    def test_row_list_uses_one_list_snapshot_for_ocr_and_image_conditions(self):
        player = MacroPlayer()
        player._click_module_point = Mock()
        screen = np.zeros((26, 160, 3), dtype=np.uint8)
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number", "separator": "/", "relation": "not_equal"},
            "right_condition": {"type": "image", "module_key": "module:game"},
            "no_match_action": "finish",
        }
        module = {
            "name": "游戏中", "template": "images/game.png",
            "threshold": 0.9, "ignore_background": False,
        }

        with package_patch('player', 'capture_bgr', return_value=(screen, (100, 200)), create=True) as capture, package_patch('player', 'recognize_image_with_boxes', return_value=('11/12', [{'text': '11/12', 'score': 0.98}]), create=True), package_patch('player', 'find_template_in_image', return_value={'center_x': 220, 'center_y': 213}, create=True), package_patch('player', 'registered_module_object', return_value=module), package_patch('player', 'recognize_region_with_boxes', side_effect=AssertionError('逐行扫描不得重新截图做 OCR')), package_patch('player', 'find_template', side_effect=AssertionError('逐行扫描不得重新截图识图')):
            player._execute_row_list_condition_click(action, None)

        capture.assert_called_once_with((100, 200, 160, 26))
        player._click_module_point.assert_called_once_with(220, 213, "left", 1, None)

    def test_row_list_retries_empty_ocr_with_enhanced_same_frame_crop(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        player._click_module_point = Mock()
        screen = np.zeros((26, 160, 3), dtype=np.uint8)
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number", "separator": "/", "relation": "not_equal"},
            "right_condition": {"type": "image", "module_key": "module:game"},
            "no_match_action": "finish",
        }

        with package_patch('player', 'capture_bgr', return_value=(screen, (100, 200)), create=True), package_patch('player', 'recognize_image_with_boxes', side_effect=[('', []), ('11/12', [{'text': '11/12', 'score': 0.91}])], create=True) as recognize, package_patch('player', 'find_template_in_image', return_value={'center_x': 220, 'center_y': 213}, create=True), package_patch('player', 'registered_module_object', return_value={'name': '游戏中', 'template': 'images/game.png', 'threshold': 0.9}), package_patch('player', 'recognize_region_with_boxes', side_effect=AssertionError('不得为 OCR 重试重新截图')):
            player._execute_row_list_condition_click(action, None)

        self.assertEqual(recognize.call_count, 2)
        self.assertTrue(any("增强重试" in message for message in logs), logs)
        player._click_module_point.assert_called_once()

    def test_row_list_finishes_without_waiting_or_clicking_when_no_row_matches(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._row_list_condition_matches = Mock(return_value=False)
        player._click_module_point = Mock()
        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 26], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "no_match_action": "finish",
        }, None)
        player._wait.assert_not_called()
        player._click_module_point.assert_not_called()

    def test_row_list_runtime_rejects_row_height_taller_than_list(self):
        player = MacroPlayer()

        with self.assertRaisesRegex(RuntimeError, "行高"):
            player._execute_row_list_condition_click({
                "type": "row_list_condition_click",
                "list_region": [100, 200, 160, 26],
                "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
                "click_region": [90, 0, 60, 27],
                "left_condition": {"type": "number"},
                "right_condition": {"type": "text"},
                "no_match_action": "finish",
            }, None)

    def test_row_list_retries_after_interval_and_clicks_first_matching_rescan_row(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._row_list_condition_matches = Mock(side_effect=[False, False, True, True])
        player._click_module_point = Mock()
        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 52], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "no_match_action": "retry", "retry_interval_ms": 1500,
        }, None)
        player._wait.assert_called_once_with(1500)
        player._click_module_point.assert_called_once_with(220, 213, "left", 1, None)

    def test_row_list_clicks_matching_row_repeatedly(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(side_effect=[True, True])
        player._click_module_point = Mock()

        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 52], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26], "click_count": 4,
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "button": "left", "no_match_action": "finish",
        }, None)

        player._click_module_point.assert_called_once_with(220, 213, "left", 4, None)

    def test_row_list_uses_configured_row_height_as_authoritative_spacing(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(side_effect=[False, True, True])
        player._click_module_point = Mock()

        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 60], "row_height": 30,
            "left_region": [0, 4, 70, 18], "right_region": [80, 4, 80, 18],
            "click_region": [90, 4, 60, 18],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "button": "left", "no_match_action": "finish",
        }, None)

        self.assertEqual(player._row_list_condition_matches.call_args_list, [
            call({"type": "number"}, (100, 204, 70, 18)),
            call({"type": "number"}, (100, 234, 70, 18)),
            call({"type": "text"}, (180, 234, 80, 18)),
        ])

    def test_row_list_scans_only_rows_whose_child_regions_fit_inside_list(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(return_value=False)
        player._click_module_point = Mock()

        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 78], "row_height": 26,
            "left_region": [0, 20, 70, 20], "right_region": [80, 20, 80, 20],
            "click_region": [90, 20, 60, 20],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "no_match_action": "finish",
        }, None)

        self.assertEqual(player._row_list_condition_matches.call_args_list, [
            call({"type": "number"}, (100, 220, 70, 20)),
            call({"type": "number"}, (100, 246, 70, 20)),
        ])

    def test_row_list_applies_independent_local_separator_corrections(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(side_effect=[False, False, True, True])
        player._click_module_point = Mock()
        screen = np.full((78, 160, 3), 35, dtype=np.uint8)
        for y in (25, 50, 75):
            screen[y:y + 1, :] = 190

        with package_patch('player', 'capture_bgr', return_value=(screen, (100, 200))):
            player._execute_row_list_condition_click({
                "type": "row_list_condition_click",
                "list_region": [100, 200, 160, 78], "row_height": 26,
                "left_region": [0, 0, 70, 25], "right_region": [80, 0, 80, 25],
                "click_region": [90, 0, 60, 25],
                "left_condition": {"type": "number"},
                "right_condition": {"type": "text"},
                "no_match_action": "finish",
            }, None)

        self.assertEqual(player._row_list_condition_matches.call_args_list, [
            call({"type": "number"}, (100, 200, 70, 25)),
            call({"type": "number"}, (100, 225, 70, 25)),
            call({"type": "number"}, (100, 250, 70, 25)),
            call({"type": "text"}, (180, 250, 80, 25)),
        ])
        player._click_module_point.assert_called_once_with(220, 262, "left", 1, None)

    def test_row_list_skips_last_row_when_local_correction_moves_it_outside_list(self):
        player = MacroPlayer()
        player._row_list_condition_matches = Mock(return_value=False)
        player._click_module_point = Mock()
        screen = np.full((78, 160, 3), 35, dtype=np.uint8)
        for y in (23, 49, 77):
            screen[y:y + 1, :] = 190

        with package_patch('player', 'capture_bgr', return_value=(screen, (100, 200))):
            player._execute_row_list_condition_click({
                "type": "row_list_condition_click",
                "list_region": [100, 200, 160, 78], "row_height": 26,
                "left_region": [0, 0, 70, 25], "right_region": [80, 0, 80, 25],
                "click_region": [90, 0, 60, 25],
                "left_condition": {"type": "number"},
                "right_condition": {"type": "text"},
                "no_match_action": "finish",
            }, None)

        self.assertEqual(player._row_list_condition_matches.call_args_list, [
            call({"type": "number"}, (100, 200, 70, 25)),
            call({"type": "number"}, (100, 226, 70, 25)),
        ])
        player._click_module_point.assert_not_called()

    def test_row_list_rejects_child_regions_outside_list(self):
        action = {
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 52], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "no_match_action": "finish",
        }
        cases = [
            ("left_region", [91, 0, 70, 26]),
            ("right_region", [80, 52, 80, 1]),
            ("click_region", [90, -1, 60, 26]),
        ]
        for key, invalid_region in cases:
            with self.subTest(key=key, invalid_region=invalid_region):
                player = MacroPlayer()
                player._row_list_condition_matches = Mock(return_value=True)
                malformed = dict(action, **{key: invalid_region})
                with self.assertRaises(RuntimeError):
                    player._execute_row_list_condition_click(malformed, None)

    def test_row_list_scales_translated_regions_and_click_center_for_replay(self):
        player = MacroPlayer()
        player._source_screen = {"left": 0, "top": 0, "width": 1000, "height": 500}
        player._target_screen = {"left": 10, "top": 20, "width": 2000, "height": 1000}
        player._row_list_condition_matches = Mock(side_effect=[False, True, True])
        player._click_module_point = Mock()
        player._execute_row_list_condition_click({
            "type": "row_list_condition_click",
            "list_region": [100, 200, 160, 52], "row_height": 26,
            "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
            "click_region": [90, 0, 60, 26],
            "left_condition": {"type": "number"},
            "right_condition": {"type": "text"},
            "button": "left", "no_match_action": "finish",
        }, None)
        self.assertEqual(player._row_list_condition_matches.call_args_list, [
            call({"type": "number"}, (210, 420, 140, 52)),
            call({"type": "number"}, (210, 472, 140, 52)),
            call({"type": "text"}, (370, 472, 160, 52)),
        ])
        player._click_module_point.assert_called_once_with(450, 498, "left", 1, None)

    def test_ocr_hit_any_text_when_expected_empty(self):
        # 期望文字留空：识别到任意文字即命中。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'recognize_region', return_value='随便什么文字'):
            result = player._execute_text_ocr({
                "expected_text": "",
                "timeout_ms": 0,
                "on_found": "continue",
                "on_timeout": "continue",
                "show_result_notice": False,
            }, None)
        self.assertIsNone(result)
        self.assertEqual(waits, [0])

    def test_ocr_hit_jumps_to_target_action(self):
        # 命中后跳转到目标动作（按稳定动作 ID）。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'recognize_region', return_value='确认购买？'):
            result = player._execute_text_ocr({
                "expected_text": "确认",
                "timeout_ms": 0,
                "on_found": "jump",
                "found_jump_action_id": "target123",
                "found_delay_ms": 0,
                "on_timeout": "continue",
                "show_result_notice": False,
            }, None)
        self.assertEqual(result, ("action_id", "target123"))
        self.assertEqual(waits, [0])

    def test_ocr_miss_timeout_continues(self):
        # 未命中直到超时：按设置继续。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'recognize_region', return_value=''), \
             patch("macroflow.execution.player.time.perf_counter", side_effect=[100.0, 100.05, 101.0]):
            result = player._execute_text_ocr({
                "expected_text": "体力不足",
                "timeout_ms": 100,
                "interval_ms": 300,
                "on_found": "continue",
                "on_timeout": "continue",
                "timeout_delay_ms": 50,
                "show_result_notice": False,
            }, None)
        self.assertIsNone(result)
        # 第一次识别后未命中 → 等 interval 重试 → 重试后已超时 → 等 timeout_delay。
        self.assertEqual(waits, [300, 50])

    def test_ocr_miss_timeout_jumps(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'recognize_region', return_value='没有字'), \
             patch("macroflow.execution.player.time.perf_counter", side_effect=[100.0, 101.0]):
            result = player._execute_text_ocr({
                "expected_text": "体力不足",
                "timeout_ms": 50,
                "interval_ms": 300,
                "on_found": "continue",
                "on_timeout": "jump",
                "timeout_jump_action_id": "target456",
                "show_result_notice": False,
            }, None)
        self.assertEqual(result, ("action_id", "target456"))

    def test_ocr_miss_timeout_stops(self):
        # 超时后选择停止：抛出异常终止执行。
        player = MacroPlayer()
        player._wait = lambda milliseconds: None
        with package_patch('player', 'recognize_region', return_value='没有字'), \
             patch("macroflow.execution.player.time.perf_counter", side_effect=[100.0, 101.0]):
            with self.assertRaisesRegex(RuntimeError, "识别文字超时"):
                player._execute_text_ocr({
                    "expected_text": "体力不足",
                    "timeout_ms": 10,
                    "interval_ms": 300,
                    "on_found": "continue",
                    "on_timeout": "stop",
                    "show_result_notice": False,
                }, None)

    def test_ocr_timeout_zero_recognizes_once(self):
        # timeout_ms=0：只识别一次，不轮询。
        player = MacroPlayer()
        player._wait = lambda milliseconds: None
        with package_patch('player', 'recognize_region', return_value='') as recognize:
            result = player._execute_text_ocr({
                "expected_text": "体力不足",
                "timeout_ms": 0,
                "interval_ms": 300,
                "on_found": "continue",
                "on_timeout": "continue",
                "show_result_notice": False,
            }, None)
        self.assertIsNone(result)
        recognize.assert_called_once()

    def test_ocr_retries_until_hit(self):
        # 未命中时按检测间隔轮询，直到命中为止。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        results = ["", "", "体力不足，请补充"]
        with package_patch('player', 'recognize_region', side_effect=lambda _region: results.pop(0)), \
             patch("macroflow.execution.player.time.perf_counter", side_effect=[100.0, 100.1, 100.2, 100.3]):
            result = player._execute_text_ocr({
                "expected_text": "体力不足",
                "timeout_ms": 3000,
                "interval_ms": 300,
                "on_found": "continue",
                "on_timeout": "continue",
                "show_result_notice": False,
            }, None)
        self.assertIsNone(result)
        self.assertEqual(waits, [300, 300, 0])

    def test_ocr_custom_region_scaled_and_passed(self):
        # 自定义区域经 DPI 缩放后传给截屏识别。
        player = MacroPlayer()
        player._wait = lambda milliseconds: None
        with package_patch('player', 'recognize_region') as recognize:
            recognize.return_value = "命中文字"
            player._execute_text_ocr({
                "region_mode": "custom",
                "region": [10, 20, 300, 400],
                "expected_text": "命中",
                "timeout_ms": 0,
                "on_found": "continue",
                "on_timeout": "continue",
                "show_result_notice": False,
            }, None)
        # 未配置源/目标屏幕时缩放为恒等：区域原样传给识别。
        recognize.assert_called_once_with((10, 20, 300, 400))

    def test_ocr_window_region_uses_bound_window_rect(self):
        # 绑定窗口模式：使用目标窗口矩形做识别区域。
        player = MacroPlayer()
        player._wait = lambda milliseconds: None
        with package_patch('player', 'recognize_region') as recognize, \
             package_patch('player', 'get_window_rect', return_value=(1, 2, 800, 600)):
            recognize.return_value = "命中文字"
            player._execute_text_ocr({
                "region_mode": "window",
                "expected_text": "命中",
                "timeout_ms": 0,
                "on_found": "continue",
                "on_timeout": "continue",
                "show_result_notice": False,
            }, 12345)
        recognize.assert_called_once_with((1, 2, 800, 600))

    def test_image_fallback_without_click_only_detects(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                return None if len(calls) < 3 else dict(main_match)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button') as button:
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "fallback_click": False,
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        self.assertEqual(
            [Path(call).name for call in calls],
            ["main.png", "fallback.png", "main.png"],
        )
        move.assert_not_called()
        button.assert_not_called()

    def test_image_fallback_exit_ends_detection(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        calls = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                return None

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button') as button:
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "fallback_on_match": "直接退出识别",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        # 退出后不再继续检测主模板，也不再检测备用模板。
        self.assertEqual(
            [Path(call).name for call in calls],
            ["main.png", "fallback.png"],
        )
        move.assert_called_once_with(200, 300)
        button.assert_called()

    def test_image_fallback_exit_without_click(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                return None

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button') as button:
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "fallback_click": False,
                    "fallback_on_match": "直接退出识别",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        move.assert_not_called()
        button.assert_not_called()

    def test_image_fallback_returns_to_main_detection(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                return None if len(calls) < 3 else dict(main_match)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button'):
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "fallback_on_match": "回到主模板的检测",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        self.assertEqual(
            [Path(call).name for call in calls],
            ["main.png", "fallback.png", "main.png"],
        )
        move.assert_called_once_with(200, 300)

    def test_repeat_click_clicks_count_times_with_interval(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player._execute_action({
                "type": "repeat_click", "button": "left",
                "x": 100, "y": 200,
                "count": 3, "interval_ms": 50, "hold_ms": 30,
            }, None, False)
        self.assertEqual(move.call_args_list, [((100, 200),)] * 3)
        self.assertEqual(
            [call.args for call in button.call_args_list],
            [("left", True), ("left", False)] * 3,
        )
        # 每次点击 hold 30 ms，点击之间间隔 50 ms。
        self.assertEqual(waits, [30, 50, 30, 50, 30])

    def test_repeat_click_min_count_and_zero_interval(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._execute_action({
                "type": "repeat_click", "button": "right",
                "x": 5, "y": 6,
                "count": 0, "interval_ms": 0, "hold_ms": 10,
            }, None, False)
        # count 至少 1 次；间隔 0 时只在 hold 之间等待。
        self.assertEqual(len(button.call_args_list), 2)
        self.assertEqual(waits, [10])

    def test_repeat_click_aborts_when_stop_requested(self):
        player = MacroPlayer()
        player.stop_event.set()
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            with self.assertRaises(PlaybackStopped):
                player._execute_action({
                    "type": "repeat_click", "x": 1, "y": 2,
                    "count": 5, "interval_ms": 10, "hold_ms": 5,
                }, None, False)
        button.assert_not_called()

    def test_module_click_source_differs_from_guard_source(self):
        """来源标记：脚本里跑的模块点击标“模块”，守卫处理段里的标“守卫”。"""
        player = MacroPlayer()
        self.assertEqual(player._current_click_source(), CLICK_SOURCE_ACTION)
        self.assertEqual(player._current_module_click_source(), CLICK_SOURCE_MODULE)
        player._handler_depth = 1
        self.assertEqual(player._current_click_source(), CLICK_SOURCE_GUARD)
        self.assertEqual(player._current_module_click_source(), CLICK_SOURCE_GUARD)

    def test_guard_click_is_written_to_event_log(self):
        """全局模块的点击是事件级的：事件日志里要能看到“点了、点在哪”。"""
        player = MacroPlayer()
        events: list[str] = []
        player.on_log = events.append
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button'):
            player.handle_guard_hit(
                {"click": (1129, 291), "button": "left", "click_count": 1},
            )
        self.assertEqual(
            [line for line in events if "点击" in line],
            ["全局检测处理动作：点击 left @ (1129, 291)。"],
            events,
        )

    def test_global_guard_overlay_counts_down_the_delay_before_action(self):
        player = MacroPlayer()
        player.guard_settle_ms = 0
        player._wait = Mock()
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }

        with patch(
            "macroflow.execution.player.guards.show_overlay", create=True,
        ) as overlay:
            player.handle_guard_hit({"delay_ms": 10000, "match": match})

        self.assertEqual(
            [call.kwargs.get("label") for call in overlay.call_args_list],
            ["10s", "9s", "8s", "7s", "6s", "5s", "4s", "3s", "2s", "1s"],
        )
        self.assertEqual(
            [call.args[0] for call in player._wait.call_args_list],
            [1000] * 10,
        )

    def test_guard_handling_pauses_before_resuming_script(self):
        """全局模块先执行完，停 GUARD_SETTLE_MS，再继续原来的任务。"""
        player = MacroPlayer()
        traces: list[str] = []
        player.on_trace_line = traces.append
        player.on_guard_poll = lambda: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button'):
            started = time.perf_counter()
            player.handle_guard_hit(
                {"click": (1129, 291), "button": "left", "click_count": 1},
            )
            elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, GUARD_SETTLE_MS / 1000.0)
        self.assertLess(elapsed, GUARD_SETTLE_MS / 1000.0 + 0.5)
        self.assertTrue(
            any("继续原任务" in line and "等待" in line for line in traces),
            traces,
        )

    def test_guard_settle_does_not_wait_twice(self):
        """停顿只算一次：处理段正常走完只等一次，不会多停一秒。"""
        player = MacroPlayer()
        traces: list[str] = []
        player.on_trace_line = traces.append
        polls: list[int] = []
        player.on_guard_poll = lambda: polls.append(1)
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button'):
            player.handle_guard_hit(
                {"click": (1129, 291), "button": "left", "click_count": 1},
            )
        # 停顿期间不再评估守卫（这一秒是留给游戏消化的），也只出现一次停顿。
        self.assertEqual(polls, [], polls)
        settle_lines = [line for line in traces if "继续原任务" in line]
        self.assertEqual(len(settle_lines), 1, traces)

    def test_guard_jump_also_pauses_before_handing_over(self):
        """要求跳转的全局模块同样先把停顿走完再交回脚本。"""
        player = MacroPlayer()
        traces: list[str] = []
        player.on_trace_line = traces.append
        player.on_guard_poll = lambda: None
        started = time.perf_counter()
        with self.assertRaises(GuardJumpRequest):
            player.handle_guard_hit({"kind": "success", "jump_row": 3})
        self.assertGreaterEqual(
            time.perf_counter() - started, GUARD_SETTLE_MS / 1000.0,
        )
        self.assertTrue(
            any("继续原任务" in line for line in traces), traces,
        )

    def test_guard_click_suppresses_same_point_module_click(self):
        """守卫点过的同一颗按钮，脚本行紧接着再点一次要被跳过。"""
        # 关掉“处理段结束后停顿 1 秒”（guard_settle_ms=0）才能测到去重本身：
        # 带停顿的话脚本那一行是在 1 秒后才点，本来就已经超出防重复窗口。
        player = MacroPlayer(guard_settle_ms=0)
        traces: list[str] = []
        player.on_trace_line = traces.append
        player._wait = lambda milliseconds: None
        guard_click = {"click": (897, 462), "button": "left", "click_count": 1}
        with package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            # 1) 全局检测守卫先点掉“退出冒险频道的确定”。
            player.handle_guard_hit(guard_click)
            self.assertEqual(len(button.call_args_list), 2)
            # 2) 脚本第 2 行（模块）紧接着识别到同一颗按钮，落点只差几像素。
            player._execute_action({
                "type": "click", "button": "left",
                "x": 898, "y": 463, "hold_ms": 30,
            }, None, False)
        # 同一个按钮只按下/抬起一次。
        self.assertEqual(
            [call.args for call in button.call_args_list],
            [("left", True), ("left", False)],
        )
        self.assertEqual(move.call_args_list, [call(897, 462)])
        self.assertTrue(
            any("跳过重复点击" in line for line in traces),
            traces,
        )

    def test_guard_settle_pushes_action_click_outside_dedup_window(self):
        """带 1 秒停顿时，脚本那一行的点击已经超出防重复窗口，照常发出。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player.on_guard_poll = lambda: None
        guard_click = {"click": (897, 462), "button": "left", "click_count": 1}
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player.handle_guard_hit(guard_click)
            self.assertEqual(len(button.call_args_list), 2)
            player._execute_action({
                "type": "click", "button": "left",
                "x": 898, "y": 463, "hold_ms": 30,
            }, None, False)
        # 守卫 2 次（按下/抬起）+ 脚本行 2 次：间隔已超过 300 ms 的防重复窗口。
        self.assertEqual(len(button.call_args_list), 4)

    def test_module_click_suppresses_same_point_guard_click(self):
        """反过来也一样：脚本模块刚点过，守卫不该再点同一颗按钮。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(897, 462, "left", 1, None)
            self.assertEqual(len(button.call_args_list), 2)
            player.handle_guard_hit(
                {"click": (897, 462), "button": "left", "click_count": 1},
            )
        self.assertEqual(
            [call.args for call in button.call_args_list],
            [("left", True), ("left", False)],
        )

    def test_same_point_click_outside_window_still_clicks(self):
        """超过防重复窗口后，同一点照常点击（不能把正常连点吞掉）。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(897, 462, "left", 1, None)
            player._last_click["at"] -= CLICK_DEDUP_WINDOW_S + 0.01
            player._click_module_point(897, 462, "left", 1, None)
        self.assertEqual(len(button.call_args_list), 4)

    def test_different_point_click_is_not_suppressed(self):
        """落点不同就不是重复点击：相邻两行的不同按钮都要点。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(897, 462, "left", 1, None)
            player._click_module_point(1235, 491, "left", 1, None)
        self.assertEqual(len(button.call_args_list), 4)

    def test_different_button_click_is_not_suppressed(self):
        """同一个落点但按键不同（左/右键）不是重复点击。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(897, 462, "left", 1, None)
            player._click_module_point(897, 462, "right", 1, None)
        self.assertEqual(len(button.call_args_list), 4)

    def test_module_repeat_on_same_point_from_its_own_loop_still_clicks(self):
        """模块自己一轮一轮重试点同一颗按钮（“直到目标消失”）要照点。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(120, 210, "left", 1, None)
            player._click_module_point(120, 210, "left", 1, None)
        self.assertEqual(len(button.call_args_list), 4)

    def test_repeat_click_burst_keeps_its_own_count(self):
        """一行「连续点击 N 次」自己要求的次数不参与去重。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(897, 462, "left", 1, None)
            player._execute_action({
                "type": "repeat_click", "button": "left",
                "x": 897, "y": 462, "count": 3, "interval_ms": 20, "hold_ms": 10,
            }, None, False)
        # 守卫那一下 + 整段连点被跳过：只剩守卫的按下/抬起。
        self.assertEqual(len(button.call_args_list), 2)

    def test_repeat_click_on_same_point_without_guard_still_clicks(self):
        """没有守卫抢先时，连点同一颗按钮照原样执行。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._execute_action({
                "type": "repeat_click", "button": "left",
                "x": 841, "y": 103, "count": 2, "interval_ms": 100, "hold_ms": 30,
            }, None, False)
        self.assertEqual(len(button.call_args_list), 4)

    def test_repeat_click_after_guard_moves_elsewhere_still_clicks(self):
        """守卫点的是别处，脚本这一行的连点必须照常执行。"""
        player = MacroPlayer()
        player.on_trace_line = lambda text: None
        player._wait = lambda milliseconds: None
        with package_patch('player', 'send_move_absolute'), \
             package_patch('player', 'send_button') as button:
            player._click_module_point(400, 300, "left", 1, None)
            player._execute_action({
                "type": "repeat_click", "button": "left",
                "x": 897, "y": 462, "count": 2, "interval_ms": 20, "hold_ms": 10,
            }, None, False)
        self.assertEqual(len(button.call_args_list), 6)

    def test_image_wait_forever_fallback_loops_back_to_main(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")
            sequence = iter([
                None, dict(fallback_match), None, dict(fallback_match), dict(main_match),
            ])

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                return next(sequence)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button'):
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        self.assertEqual(
            [Path(call).name for call in calls],
            ["main.png", "fallback.png", "main.png", "fallback.png", "main.png"],
        )
        self.assertEqual(move.call_args_list, [((200, 300),), ((200, 300),)])

    def test_image_wait_forever_fallback_uses_its_own_region(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        main_attempts = {"count": 0}
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append((Path(template).name, region))
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                main_attempts["count"] += 1
                return None if main_attempts["count"] == 1 else dict(main_match)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute'), \
                 package_patch('player', 'send_button'):
                result = player._execute_image({
                    "template": str(main_png),
                    "region_mode": "custom", "region": [0, 0, 100, 100],
                    "fallback_template": str(fallback_png),
                    "fallback_region_mode": "custom",
                    "fallback_region": [10, 20, 30, 40],
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "continue",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        self.assertEqual(calls[0], ("main.png", (0, 0, 100, 100)))
        self.assertEqual(calls[1], ("fallback.png", (10, 20, 30, 40)))
        self.assertEqual(calls[2], ("main.png", (0, 0, 100, 100)))

    def test_image_fallback_active_main_still_detected(self):
        # 备用激活后主模板必须继续一起检测：备用一直不出现，主模板在超时后出现应命中。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        main_attempts = {"count": 0}
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                if str(template).endswith("fallback.png"):
                    return None
                main_attempts["count"] += 1
                return None if main_attempts["count"] == 1 else dict(main_match)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute'), \
                 package_patch('player', 'send_button'):
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "click",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        self.assertEqual(
            [Path(call).name for call in calls],
            ["main.png", "fallback.png", "main.png"],
        )

    def test_image_both_detected_main_template_handled_first(self):
        # 主备同时出现时主模板优先：命中后按主模板的 on_found 处理。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        fallback_match = {"x": 190, "y": 290, "width": 20, "height": 20,
                          "center_x": 200, "center_y": 300, "score": 0.95}
        main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                      "center_x": 25, "center_y": 40, "score": 0.9}
        calls = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            main_png = Path(folder) / "main.png"
            fallback_png = Path(folder) / "fallback.png"
            main_png.write_bytes(b"x")
            fallback_png.write_bytes(b"x")

            def fake_find(template, threshold, region, ignore_background=False, scale=1.0):
                calls.append(str(template))
                if str(template).endswith("fallback.png"):
                    return dict(fallback_match)
                return None if len(calls) < 2 else dict(main_match)

            with package_patch('player', 'find_template', side_effect=fake_find), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button') as button:
                result = player._execute_image({
                    "template": str(main_png),
                    "fallback_template": str(fallback_png),
                    "fallback_switch_ms": 0,
                    "timeout_ms": 0,
                    "wait_forever": True,
                    "on_found": "click",
                    "show_result_notice": False,
                    "interval_ms": 100,
                }, None)
        self.assertIsNone(result)
        # 第一次迭代备用命中点击 (200,300)；第二次主命中点击主模板中心 (25,40)。
        self.assertEqual(move.call_args_list, [((200, 300),), ((25, 40),)])

    def test_image_summary_shows_wait_forever_fallback(self):
        _kind, detail, _delay = action_summary({
            "type": "image_match", "template": "images/x.png",
            "wait_forever": True, "on_found": "continue",
            "fallback_template": "images/y.png", "fallback_switch_ms": 5000,
        })
        self.assertIn("一直等待", detail)
        self.assertIn("备用", detail)
        self.assertIn("y.png", detail)
        self.assertIn("5000", detail)

    def test_image_summary_ignores_fallback_without_wait_forever(self):
        _kind, detail, _delay = action_summary({
            "type": "image_match", "template": "images/x.png",
            "on_found": "continue", "fallback_template": "images/y.png",
        })
        self.assertNotIn("备用", detail)

    def test_global_module_row_summary_shows_jump_row(self):
        # v1.68：普通脚本内嵌全局模块行显示跳转行（启用跳转时）。
        kind, detail, _delay = action_summary({
            "type": "global_detect", "template": "images/g.png",
            "jump_row": 3, "jump_enabled": True, "hold_ms": 1500, "threshold": 0.9,
        })
        self.assertIn("全局模块", kind)
        self.assertIn("触发后跳转到第 3 行", detail)
        self.assertIn("g.png", detail)
        self.assertNotIn("点击", detail)

    def test_global_module_row_summary_shows_jump_disabled(self):
        # 未勾选“启用触发后跳转”：摘要显示触发后不跳转。
        _kind, detail, _delay = action_summary({
            "type": "global_detect", "template": "images/g.png",
            "jump_row": 3, "jump_action_id": "target-a",
            "jump_enabled": False, "hold_ms": 1500, "threshold": 0.9,
        })
        self.assertIn("触发后不跳转，继续执行", detail)
        self.assertNotIn("跳转到第 3 行", detail)
        self.assertNotIn("点击", detail)
        # 缺失字段同样按默认不启用处理。
        _kind, detail, _delay = action_summary({
            "type": "global_detect", "template": "images/g.png",
            "jump_row": 3, "hold_ms": 1500,
        })
        self.assertIn("触发后不跳转，继续执行", detail)

    def test_module_ref_summary_shows_legacy_jump(self):
        # 引用模块行沿用旧引擎跳转语义：缺失 jump_enabled 也按启用显示
        # （该行没有“启用触发后跳转”开关）。
        module_obj = {
            "enabled": True, "category": "script_global", "name": "结算确定",
            "template": "images/g.png", "after_action": "click_match",
            "click_count": 2,
        }
        with package_patch('app', 'registered_module_object', return_value=module_obj):
            kind, detail, _delay = action_summary({
                "type": "global_detect", "template": "images/g.png",
                "module_ref": True, "module_key": "images/g.png",
                "jump_row": 3, "jump_action_id": "target-a",
                "hold_ms": 1000, "threshold": 0.9,
            }, {"target-a": 6})
        self.assertIn("脚本全局模块", kind)
        self.assertIn("触发后跳转到第 6 行", detail)
        self.assertIn("点击识别位置", detail)

    def test_module_ref_summary_shows_deleted_jump_target(self):
        module_obj = {
            "enabled": True, "category": "script_global", "name": "结算确定",
            "template": "images/g.png", "after_action": "click_match",
        }
        with package_patch('app', 'registered_module_object', return_value=module_obj):
            _kind, detail, _delay = action_summary({
                "type": "global_detect", "template": "images/g.png",
                "module_ref": True, "module_key": "images/g.png",
                "jump_row": 3, "jump_action_id": "deleted-target",
                "hold_ms": 1000, "threshold": 0.9,
            }, {"target-a": 6})
        self.assertIn("触发后跳转目标已删除", detail)

    def test_global_module_row_summary_resolves_row_object(self):
        # v1.70：跳转目标是行的对象，摘要按动作标识解析到当前行号。
        action_rows = {"target-a": 6, "target-b": 2}
        _kind, detail, _delay = action_summary({
            "type": "global_detect", "template": "images/g.png",
            "jump_row": 1, "jump_action_id": "target-a", "jump_enabled": True,
        }, action_rows)
        self.assertIn("触发后跳转到第 6 行", detail)
        # 目标行被删除：明确提示而不是显示旧行号。
        _kind, detail, _delay = action_summary({
            "type": "global_detect", "template": "images/g.png",
            "jump_row": 3, "jump_action_id": "deleted-target", "jump_enabled": True,
        }, action_rows)
        self.assertIn("触发后跳转目标已删除", detail)

    def test_image_summary_shows_fallback_click_and_continue(self):
        _kind, detail, _delay = action_summary({
            "type": "image_match", "template": "images/x.png",
            "wait_forever": True, "on_found": "continue",
            "fallback_template": "images/y.png", "fallback_switch_ms": 5000,
            "fallback_click": False,
        })
        self.assertIn("不点击", detail)
        self.assertIn("出现后回到主模板检测", detail)

    def test_image_summary_shows_fallback_exit(self):
        _kind, detail, _delay = action_summary({
            "type": "image_match", "template": "images/x.png",
            "wait_forever": True, "on_found": "continue",
            "fallback_template": "images/y.png", "fallback_switch_ms": 5000,
            "fallback_click": True, "fallback_on_match": "直接退出识别",
        })
        self.assertIn("点击", detail)
        self.assertIn("出现后退出识别", detail)

    def test_image_summary_shows_wait_forever(self):
        kind, detail, _delay = action_summary({
            "type": "image_match", "template": "images/x.png",
            "wait_forever": True, "on_found": "continue",
        })
        self.assertIn("识图", kind)
        self.assertIn("一直等待", detail)
        self.assertIn("不超时", detail)

    def test_image_found_jump_waits_found_delay(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', return_value=match):
            result = player._execute_image({
                "template": "images/目标.png",
                "on_found": "jump",
                "found_jump_row": 3,
                "found_delay_ms": 900,
                "show_result_notice": False,
            }, None)
        self.assertEqual(result, ("row", 3))
        self.assertIn(900, waits)

    def test_image_found_can_finish_current_script_for_next_workflow_step(self):
        player = MacroPlayer()
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', return_value=match), \
             package_patch('player', 'show_overlay'):
            result = player._execute_image({
                "template": "images/目标.png",
                "on_found": "jump",
                "found_jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
                "found_delay_ms": 0,
                "show_result_notice": False,
            }, None)
        self.assertEqual(result, ("next_workflow_step", 0))

    def test_finish_current_script_skips_remaining_actions_and_repeats(self):
        player = MacroPlayer()
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        repeats_started = []
        repeats_completed = []
        actions_seen = []
        statuses = []
        player.on_status = statuses.append
        with package_patch('player', 'find_template', return_value=match) as find, \
             package_patch('player', 'show_overlay'):
            player.play([
                {
                    "type": "image_match", "template": "images/目标.png",
                    "delay_ms": 0, "on_found": "jump",
                    "found_jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
                    "found_delay_ms": 0, "show_result_notice": False,
                },
                {"type": "comment", "text": "不应执行"},
            ], repeats=3,
                on_repeat=lambda current, total: repeats_started.append((current, total)),
                on_repeat_complete=lambda current, total: repeats_completed.append((current, total)),
                on_action=lambda next_index, total: actions_seen.append((next_index, total)))
        self.assertEqual(find.call_count, 1)
        self.assertEqual(repeats_started, [(1, 3)])
        self.assertEqual(repeats_completed, [(1, 3)])
        self.assertEqual(actions_seen, [(1, 2)])
        self.assertTrue(any("执行工作流下一项" in text for text in statuses))

    def test_image_summary_shows_finish_current_script(self):
        _kind, detail, _delay = action_summary({
            "type": "image_match", "template": "images/x.png",
            "on_found": "jump",
            "found_jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
        })
        self.assertIn("结束当前脚本", detail)
        self.assertIn("工作流下一项", detail)

    def test_image_action_waits_after_execution_before_next_action(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', return_value=match):
            player.play([
                {
                    "type": "image_match", "template": "images/目标.png",
                    "delay_ms": 0, "timeout_ms": 0,
                    "on_found": "continue", "after_delay_ms": 700,
                },
                {"type": "notice", "text": "之后执行", "duration_ms": 1},
            ])
        self.assertEqual(notices, [("之后执行", 500)])
        self.assertIn(700, waits)

    def test_module_continue_runs_post_code_before_returning_to_script(self):
        player = MacroPlayer()
        order = []
        player._run_action_sequence = Mock(side_effect=lambda *_args, **_kwargs: order.append("segment"))
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        obj = {
            "after_action": "continue", "run_code_after_action": True,
            "on_success_actions": [{"type": "delay", "ms": 1}],
        }
        result = player._after_module_success(obj, match, None, None, 0)
        self.assertIsNone(result)
        self.assertEqual(order, ["segment"])
        player._run_action_sequence.assert_called_once_with(
            obj["on_success_actions"], None, script_stack=None, depth=1,
        )

    def test_module_post_code_special_restart_takes_effect(self):
        requests = []
        player = MacroPlayer(
            on_restart_workflow_request=lambda action: requests.append(action) or True,
        )
        obj = {
            "after_action": "continue", "run_code_after_action": True,
            "on_success_actions": [{"type": "restart_workflow"}],
        }
        with self.assertRaises(PlaybackStopped):
            player._after_module_success(
                obj, {"center_x": 1, "center_y": 2}, None, None, 0,
            )
        self.assertEqual(requests[0]["type"], "restart_workflow")

    def test_module_timeout_writes_log_with_module_name(self):
        logs = []
        player = MacroPlayer(on_log=lambda text: logs.append(text))
        module_obj = {
            "name": "专注", "template": "images/专注.png",
            "region": [0, 0, 0, 0], "blocking": False, "interval_ms": 50,
            "threshold": 0.85, "run_code_on_timeout": True,
            "not_found_timeout_ms": 0,
            "on_timeout_actions": [{"type": "delay", "ms": 1}],
        }
        actions = [{
            "type": "image_match", "template": "images/专注.png",
            "module_key": "module:专注", "module_ref": True,
            "region_mode": "template", "delay_ms": 0,
        }]
        with package_patch('player', 'registered_module_object', return_value=module_obj), \
             package_patch('player', 'find_template', return_value=None):
            player.play(actions)
        self.assertTrue(
            any("模块 专注 连续" in text and "未识别到" in text
                and "执行超时代码段" in text for text in logs),
            f"日志应包含模块超时原因，实际：{logs}",
        )

    def test_blocking_module_reference_timeout_override_skips_current_row(self):
        player = MacroPlayer()
        # 旧实现会继续无限等待；让测试中的等待主动停止播放器，避免红灯阶段挂死。
        player._wait = lambda _milliseconds: player.stop()
        module = {
            "name": "等待登录", "template": "images/login.png", "region": [],
            "blocking": True, "interval_ms": 250, "threshold": 0.85,
            "run_code_on_timeout": False,
        }
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:login", "template": "images/login.png",
            "region_mode": "template", "blocking_timeout_enabled": True,
            "blocking_timeout_ms": 0,
        }
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', return_value=None):
            try:
                result = player._execute_image(action, None)
            except PlaybackStopped:
                result = "still blocking"
        self.assertIsNone(result)

    def test_module_success_signal_jumps_to_stable_row_object(self):
        logs = []
        notices = []
        player = MacroPlayer(
            on_log=logs.append,
            on_notice=lambda text, _duration: notices.append(text),
        )
        module = {
            "name": "主线关卡", "template": "images/主线关卡.png",
            "region": [], "blocking": False, "interval_ms": 50,
            "threshold": 0.85, "delay_ms": 0,
            "after_action": "continue", "run_code_after_action": False,
        }
        match = {
            "x": 1, "y": 2, "width": 3, "height": 4,
            "center_x": 2, "center_y": 4, "score": 0.95,
        }
        actions = [
            {
                "type": "image_match", "module_ref": True,
                "module_key": "module:main", "template": "images/主线关卡.png",
                "delay_ms": 0, "on_found": "jump",
                "found_jump_action_id": "target",
            },
            {"type": "notice", "text": "不应执行", "action_id": "middle"},
            {"type": "notice", "text": "目标已执行", "action_id": "target"},
        ]
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', return_value=match), \
             package_patch('player', 'show_overlay'):
            player.play(actions)

        self.assertEqual(notices, ["目标已执行"])
        self.assertIn("模块 主线关卡 执行结果：成功", logs)

    def test_module_reference_requires_continuous_hold_before_success(self):
        player = MacroPlayer()
        player._wait = Mock()
        module = {
            "name": "返回游戏大厅", "template": "images/lobby.png",
            "region": [], "blocking": False, "interval_ms": 50,
            "threshold": 0.85, "not_found_timeout_ms": 10000,
            "hold_enabled": True, "hold_ms": 2000,
            "delay_ms": 0, "after_action": "continue",
            "run_code_after_action": False, "run_code_on_timeout": False,
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        clock = {"now": 0.0}
        observations = [
            (0.0, match), (1.0, match), (1.1, None),
            (2.0, match), (3.0, match), (4.0, match),
        ]

        def detect(*_args, **_kwargs):
            clock["now"], result = observations.pop(0)
            return result

        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', side_effect=detect) as find, \
             package_patch('player', 'show_overlay') as overlay, \
             patch("macroflow.execution.player.image.time.perf_counter",
                   side_effect=lambda: clock["now"]):
            result = player._execute_image({
                "type": "image_match", "module_ref": True,
                "module_key": "module:lobby", "template": "images/lobby.png",
            }, None)

        self.assertIsNone(result)
        self.assertEqual(find.call_count, 6)
        self.assertEqual(
            [call.kwargs.get("label") for call in overlay.call_args_list],
            [None],
        )

    def test_module_overlay_counts_down_the_delay_before_action(self):
        player = MacroPlayer()
        player._wait = Mock()
        module = {
            "name": "游戏大厅", "template": "images/lobby.png",
            "region": [], "blocking": False, "interval_ms": 50,
            "threshold": 0.85, "not_found_timeout_ms": 10000,
            "hold_enabled": True, "hold_ms": 1000,
            "delay_ms": 8000, "after_action": "continue",
            "run_code_after_action": False, "run_code_on_timeout": False,
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }

        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', return_value=match) as find, \
             package_patch('player', 'show_overlay') as overlay, \
             patch("macroflow.execution.player.image.time.perf_counter",
                   side_effect=[0.0, 0.0, 1.1]):
            result = player._execute_image({
                "type": "image_match", "module_ref": True,
                "module_key": "module:lobby", "template": "images/lobby.png",
            }, None)

        self.assertIsNone(result)
        self.assertEqual(find.call_count, 2)
        self.assertEqual(
            [call.kwargs.get("label") for call in overlay.call_args_list],
            ["8s", "7s", "6s", "5s", "4s", "3s", "2s", "1s"],
        )
        self.assertEqual([call.args[0] for call in player._wait.call_args_list], [
            50, 1000, 1000, 1000, 1000, 1000, 1000, 1000, 1000,
        ])

    def test_module_failure_signal_uses_object_timeout_then_jumps(self):
        logs = []
        notices = []
        player = MacroPlayer(
            on_log=logs.append,
            on_notice=lambda text, _duration: notices.append(text),
        )
        module = {
            "name": "主线关卡", "template": "images/主线关卡.png",
            "region": [], "blocking": False, "interval_ms": 50,
            "threshold": 0.85, "after_action": "continue",
            "run_code_after_action": False, "run_code_on_timeout": False,
            "not_found_timeout_ms": 0, "on_timeout_actions": [],
        }
        actions = [
            {
                "type": "image_match", "module_ref": True,
                "module_key": "module:main", "template": "images/主线关卡.png",
                "delay_ms": 0, "on_timeout": "jump",
                "timeout_jump_action_id": "target",
            },
            {"type": "notice", "text": "不应执行", "action_id": "middle"},
            {"type": "notice", "text": "失败目标已执行", "action_id": "target"},
        ]
        with package_patch('player', 'registered_module_object', return_value=module), \
             package_patch('player', 'find_template', return_value=None):
            player.play(actions)

        self.assertEqual(notices, ["失败目标已执行"])
        self.assertIn("模块 主线关卡 执行结果：失败", logs)

    def test_module_result_can_end_current_script_on_success_or_failure(self):
        match = {
            "x": 1, "y": 2, "width": 3, "height": 4,
            "center_x": 2, "center_y": 4, "score": 0.95,
        }
        success_module = {
            "name": "成功模块", "template": "images/s.png", "region": [],
            "blocking": False, "interval_ms": 50, "threshold": 0.85,
            "delay_ms": 0, "after_action": "continue", "run_code_after_action": False,
        }
        with package_patch('player', 'registered_module_object', return_value=success_module), \
             package_patch('player', 'find_template', return_value=match), \
             package_patch('player', 'show_overlay'):
            ended_on_success = MacroPlayer().play([
                {
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:s", "template": "images/s.png",
                    "delay_ms": 0, "on_found": "end_current_script",
                },
                {"type": "unknown_must_be_skipped"},
            ])
        failure_module = dict(
            success_module, name="失败模块", run_code_on_timeout=True,
            not_found_timeout_ms=0, on_timeout_actions=[],
        )
        with package_patch('player', 'registered_module_object', return_value=failure_module), \
             package_patch('player', 'find_template', return_value=None):
            ended_on_failure = MacroPlayer().play([
                {
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:f", "template": "images/f.png",
                    "delay_ms": 0, "on_timeout": "end_current_script",
                },
                {"type": "unknown_must_be_skipped"},
            ])

        self.assertTrue(ended_on_success)
        self.assertTrue(ended_on_failure)

    def test_module_success_logs_click_and_segment_with_name(self):
        logs = []
        player = MacroPlayer(on_log=lambda text: logs.append(text))
        player._run_action_sequence = Mock()
        obj = {
            "name": "结算确定", "after_action": "click_match",
            "run_code_after_action": True,
            "on_success_actions": [{"type": "delay", "ms": 1}],
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'send_move_absolute'), package_patch('player', 'send_button'):
            player._after_module_success(obj, match, None, None, 0)
        self.assertTrue(
            any("模块 结算确定 已点击 (25, 40)" in text for text in logs),
            f"日志应包含模块点击，实际：{logs}",
        )
        self.assertTrue(
            any("模块 结算确定 主动作完成，执行附加代码段" in text for text in logs),
            f"日志应包含附加代码段执行，实际：{logs}",
        )

    def test_module_success_respects_click_count(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        player._wait = Mock()
        obj = {
            "name": "连续领取", "after_action": "click_match",
            "button": "left", "click_count": 3,
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player._after_module_success(obj, match, None, None, 0)
        move.assert_called_once_with(25, 40)
        self.assertEqual(button.call_count, 6)
        self.assertIn("模块 连续领取 已点击 (25, 40) × 3", logs)

    def test_module_post_code_can_jump_to_outer_script_last_action(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, _duration: notices.append(text))
        module_obj = {
            "template": "images/module.png", "region": [0, 0, 0, 0],
            "blocking": False, "interval_ms": 50, "threshold": 0.85,
            "after_action": "continue", "run_code_after_action": True,
            "on_success_actions": [
                {"type": "jump_current_script_last"},
                {"type": "notice", "text": "代码段剩余动作"},
            ],
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        actions = [
            {
                "type": "image_match", "template": "images/module.png",
                "module_key": "module:test", "module_ref": True,
                "region_mode": "template", "delay_ms": 0,
            },
            {"type": "notice", "text": "脚本中间动作"},
            {"type": "notice", "text": "脚本最后一行"},
        ]
        with package_patch('player', 'registered_module_object', return_value=module_obj), \
             package_patch('player', 'find_template', return_value=match), \
             package_patch('player', 'show_overlay'):
            player.play(actions)

        self.assertEqual(notices, ["脚本最后一行"])

    def test_jump_to_last_does_not_repeat_module_when_module_is_already_last(self):
        player = MacroPlayer()
        module_obj = {
            "template": "images/module.png", "region": [0, 0, 0, 0],
            "blocking": False, "interval_ms": 50, "threshold": 0.85,
            "after_action": "continue", "run_code_after_action": True,
            "on_success_actions": [{"type": "jump_current_script_last"}],
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'registered_module_object', return_value=module_obj), \
             package_patch('player', 'find_template', return_value=match) as find, \
             package_patch('player', 'show_overlay'):
            player.play([{
                "type": "image_match", "template": "images/module.png",
                "module_key": "module:test", "module_ref": True,
                "region_mode": "template", "delay_ms": 0,
            }])
        self.assertEqual(find.call_count, 1)

    def test_module_not_found_timeout_runs_its_own_segment(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._run_action_sequence = Mock()
        timeout_segment = [{"type": "delay", "ms": 25}]
        obj = {
            "blocking": True, "interval_ms": 250, "threshold": 0.85,
            "run_code_on_timeout": True, "not_found_timeout_ms": 0,
            "on_timeout_actions": timeout_segment,
            "run_code_after_action": True,
            "on_success_actions": [{"type": "notice", "text": "不应执行"}],
        }
        with package_patch('player', 'registered_module_object', return_value=obj), \
             package_patch('player', 'find_template', return_value=None):
            result = player._execute_image({
                "type": "image_match", "template": "images/missing.png",
                "module_ref": True,
            }, None)
        self.assertIsNone(result)
        player._run_action_sequence.assert_called_once_with(
            timeout_segment, None, script_stack=None, depth=1,
        )

    def test_module_without_region_does_not_borrow_shared_image_region(self):
        player = MacroPlayer()
        player._template_region = Mock(side_effect=AssertionError("不应按共用图片反查区域"))
        obj = {
            "template": "images/shared.png", "region": [0, 0, 0, 0],
            "blocking": False, "interval_ms": 250, "threshold": 0.85,
            "after_action": "continue", "run_code_after_action": False,
        }
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'registered_module_object', return_value=obj) as lookup, \
             package_patch('player', 'find_template', return_value=match) as find:
            player._execute_image({
                "type": "image_match", "template": "images/shared.png",
                "module_key": "module:independent", "module_ref": True,
                "region_mode": "template",
            }, None)

        lookup.assert_called_once_with("module:independent")
        self.assertIsNone(find.call_args.args[2])
        player._template_region.assert_not_called()

    def test_activate_window_action_resolves_saved_signature(self):
        player = MacroPlayer()
        target = WindowInfo(456, "游戏窗口", "GameWnd", r"C:\\Game\\game.exe")
        with package_patch('player', 'resolve_window_signature', return_value=target), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player._execute_action({
                "type": "activate_window",
                "window": {
                    "title": target.title,
                    "class_name": target.class_name,
                    "process_path": target.process_path,
                },
            }, None, False)
        activate.assert_called_once_with(456)
        self.assertEqual(player._relative_target_hwnd, 456)

    def test_end_current_script_action_skips_remaining_actions(self):
        statuses = []
        player = MacroPlayer(on_status=statuses.append)

        advanced = player.play([
            {"type": "end_current_script"},
            {"type": "unknown_must_be_skipped"},
        ])

        self.assertTrue(advanced)
        self.assertIn("已结束当前最里层脚本，继续执行", statuses)

    def test_end_current_script_in_module_segment_reaches_script_boundary(self):
        player = MacroPlayer()
        module = {
            "name": "结束内层",
            "after_action": "continue",
            "run_code_after_action": True,
            "on_success_actions": [{"type": "end_current_script"}],
        }
        match = {
            "x": 1, "y": 2, "width": 3, "height": 4,
            "center_x": 2, "center_y": 4,
        }

        with self.assertRaises(EndCurrentScriptRequest):
            player._after_module_success(
                module, match, None, script_stack=None, depth=0,
            )

    def test_jump_action_uses_stable_target_action_id(self):
        player = MacroPlayer()
        waits = []
        player._wait = waits.append
        advanced = player.play([
            {"type": "jump", "jump_action_id": "target", "delay_ms": 0},
            {"type": "delay", "ms": 25, "action_id": "target", "delay_ms": 0},
        ])
        self.assertFalse(advanced)
        self.assertIn(25, waits)

    def test_jump_action_supports_script_start_and_end(self):
        player = MacroPlayer()
        self.assertEqual(
            player._execute_action(
                {"type": "jump", "jump_action_id": SCRIPT_START_TARGET_ID,
                 "workflow_repeat_at_least_2": False},
                None, False,
            ),
            ("row", 1),
        )
        self.assertEqual(
            player._execute_action(
                {"type": "jump", "jump_action_id": NEXT_WORKFLOW_STEP_TARGET_ID,
                 "workflow_repeat_at_least_2": False},
                None, False,
            ),
            ("next_workflow_step", ""),
        )

    def test_conditional_jump_only_applies_from_second_workflow_repeat(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {
                "type": "jump", "action_id": "jump", "jump_action_id": "target",
                "workflow_repeat_at_least_2": True,
            },
            {"type": "comment", "action_id": "middle"},
            {"type": "comment", "action_id": "target"},
        ]

        player.play(
            actions, repeats=2, workflow_context=True,
            on_action=lambda next_index, _total: executed.append(next_index),
        )

        self.assertEqual(executed, [1, 2, 3, 1, 3])

    def test_conditional_jump_is_skipped_when_script_runs_standalone(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {
                "type": "jump", "action_id": "jump", "jump_action_id": "target",
                "workflow_repeat_at_least_2": True,
            },
            {"type": "comment", "action_id": "middle"},
            {"type": "comment", "action_id": "target"},
        ]

        player.play(
            actions,
            on_action=lambda next_index, _total: executed.append(next_index),
        )

        self.assertEqual(executed, [1, 2, 3])

    def test_conditional_jump_applies_from_second_repeat_when_standalone(self):
        # 脚本多次执行（重复次数 >1）的第 2 次起生效：单独运行脚本同样适用。
        player = MacroPlayer()
        executed = []
        actions = [
            {
                "type": "jump", "action_id": "jump", "jump_action_id": "target",
                "workflow_repeat_at_least_2": True,
            },
            {"type": "comment", "action_id": "middle"},
            {"type": "comment", "action_id": "target"},
        ]

        player.play(
            actions, repeats=2,
            on_action=lambda next_index, _total: executed.append(next_index),
        )

        self.assertEqual(executed, [1, 2, 3, 1, 3])

    def test_jump_condition_defaults_to_second_repeat_on(self):
        # 缺失 workflow_repeat_at_least_2 字段时按“第 2 次及以后生效”处理。
        player = MacroPlayer()
        executed = []
        actions = [
            {"type": "jump", "action_id": "jump", "jump_action_id": "target"},
            {"type": "comment", "action_id": "middle"},
            {"type": "comment", "action_id": "target"},
        ]

        player.play(
            actions, repeats=2,
            on_action=lambda next_index, _total: executed.append(next_index),
        )

        self.assertEqual(executed, [1, 2, 3, 1, 3])

    def test_script_scope_callbacks_wrap_playback_even_when_starting_later(self):
        events = []
        actions = [
            {"type": "global_detect", "action_id": "global"},
            {"type": "comment", "action_id": "start"},
        ]
        player = MacroPlayer(
            on_script_scope_enter=(
                lambda value, origin_row=0, last_row=None: events.append(("enter", value)) or "scope"
            ),
            on_script_scope_exit=lambda token: events.append(("exit", token)),
        )

        player.play(actions, start_index=1)

        self.assertEqual(events, [("enter", actions), ("exit", "scope")])

    def test_image_timeout_jump_waits_after_execution(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'find_template', return_value=None):
            player.play([
                {
                    "type": "image_match", "template": "images/目标.png",
                    "delay_ms": 0, "timeout_ms": 0,
                    "on_timeout": "jump", "timeout_jump_row": 3,
                    "after_delay_ms": 500,
                },
                {"type": "unknown_must_be_skipped"},
                {"type": "notice", "text": "跳转目标", "duration_ms": 1},
            ])
        self.assertEqual(notices, [("跳转目标", 500)])
        self.assertIn(500, waits)

    def test_image_timeout_can_end_top_level_script(self):
        statuses = []
        player = MacroPlayer(on_status=statuses.append)
        with package_patch('player', 'find_template', return_value=None):
            advanced = player.play([
                {
                    "type": "image_match", "template": "images/目标.png",
                    "delay_ms": 0, "timeout_ms": 0,
                    "on_timeout": "end_current_script",
                },
                {"type": "unknown_must_be_skipped"},
            ])
        self.assertTrue(advanced)
        self.assertIn("识图超时，结束当前脚本", statuses)

    def test_image_timeout_ends_only_current_referenced_script(self):
        notices = []
        with tempfile.TemporaryDirectory() as folder:
            referenced_path = Path(folder) / "referenced.json"
            save_script(MacroScript(name="引用", actions=[
                {
                    "type": "image_match", "template": "images/目标.png",
                    "delay_ms": 0, "timeout_ms": 0,
                    "on_timeout": "end_current_script",
                },
                {"type": "unknown_must_be_skipped"},
            ]), referenced_path)
            player = MacroPlayer(on_notice=lambda text, duration: notices.append(text))
            with package_patch('player', 'find_template', return_value=None):
                advanced = player.play([
                    {"type": "script_ref", "script": str(referenced_path), "delay_ms": 0},
                    {"type": "notice", "text": "外层继续", "duration_ms": 1},
                ])
        self.assertFalse(advanced)
        self.assertEqual(notices, ["外层继续"])

    def test_global_detect_action_requests_monitor(self):
        requests = []
        player = MacroPlayer(on_global_detect_request=requests.append)
        player.play([
            {
                "type": "global_detect", "template": "images/g.png",
                "delay_ms": 0,
            },
        ])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["type"], "global_detect")

    def test_image_without_saved_delay_uses_new_1000_ms_default(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', return_value=match):
            player.play([{
                "type": "image_match", "template": "images/目标.png",
                "on_found": "continue",
            }])
        self.assertEqual(waits[0], 1000)

    def test_image_success_waits_then_clicks_custom_scaled_point(self):
        player = MacroPlayer()
        player._wait = Mock()
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        with package_patch('player', 'find_template', return_value=match), \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button'):
            player._execute_image({
                "template": "images/目标.png",
                "on_found": "click",
                "found_delay_ms": 1000,
                "click_target": "custom",
                "click_point": [700, 500],
            }, None)
        self.assertEqual(player._wait.call_args_list[0].args, (1000,))
        move.assert_called_once_with(700, 500)

    def test_image_result_notice_reports_success_details(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.936,
        }
        with package_patch('player', 'find_template', return_value=match):
            player._execute_image({
                "template": "images/目标.png",
                "show_result_notice": True,
                "on_found": "continue",
            }, None)
        self.assertEqual(len(notices), 1)
        self.assertIn("识图成功", notices[0][0])
        self.assertIn("93.6%", notices[0][0])
        self.assertIn("(25, 40)", notices[0][0])

    def test_image_result_notice_reports_timeout_when_continuing(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        with package_patch('player', 'find_template', return_value=None):
            player._execute_image({
                "template": "images/目标.png",
                "timeout_ms": 0,
                "on_timeout": "continue",
                "show_result_notice": True,
            }, None)
        self.assertEqual(len(notices), 1)
        self.assertIn("识图未找到", notices[0][0])
        self.assertIn("目标.png", notices[0][0])

    def test_text_module_hit_clicks_ocr_box_center_with_offsets(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._status = Mock()
        module_obj = {
            "recognize": "text", "expected_text": "体力不足", "match_mode": "contains",
            "template": "", "region": [10, 20, 300, 400],
            "blocking": False, "interval_ms": 250, "threshold": 0.85,
            "after_action": "click_match", "run_code_after_action": False,
            "delay_ms": 0, "ocr_offset_up": 5, "ocr_offset_down": 15,
            "ocr_offset_left": 20, "ocr_offset_right": 5,
        }
        found = {
            "text": "当前体力不足", "x": 80, "y": 60, "width": 80, "height": 40,
            "center_x": 120, "center_y": 80, "score": 0.99,
        }
        with package_patch('player', 'registered_module_object', return_value=module_obj), \
             package_patch('player', 'recognize_region_with_boxes', return_value=('当前体力不足', [found])) as recognize, \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button'):
            player._execute_image({
                "type": "image_match", "template": "", "module_ref": True,
                "module_key": "module:text", "region_mode": "template", "delay_ms": 0,
            }, None)
        recognize.assert_called_once()
        # OCR 中心 (120,80)，水平偏移 5-20=-15，垂直偏移 15-5=+10。
        move.assert_called_once_with(105, 90)
        self.assertTrue(any(
            "识别文字命中" in item.args[0]
            for item in player._status.call_args_list
        ))

    def test_text_module_miss_times_out_and_continues(self):
        player = MacroPlayer()
        player._wait = Mock()
        with package_patch('player', 'registered_module_object', return_value={'recognize': 'text', 'expected_text': '体力不足', 'match_mode': 'contains', 'template': '', 'region': [], 'blocking': False, 'interval_ms': 250, 'threshold': 0.85, 'after_action': 'continue', 'run_code_after_action': False}), \
             package_patch('player', 'recognize_region_with_boxes', return_value=('其他文字', [{'text': '其他文字'}])):
            result = player._execute_image({
                "type": "image_match", "template": "", "module_ref": True,
                "module_key": "module:text", "region_mode": "template",
                "timeout_ms": 0, "on_timeout": "continue",
            }, None)
        self.assertIsNone(result)

    def test_text_module_miss_writes_actual_text_to_log_and_status(self):
        logs = []
        statuses = []
        player = MacroPlayer(on_status=statuses.append, on_log=logs.append)
        player._wait = Mock()
        with package_patch('player', 'registered_module_object', return_value={'name': '奖励可领取', 'recognize': 'text', 'expected_text': '可领取', 'match_mode': 'contains', 'template': '', 'region': [], 'blocking': False, 'interval_ms': 250, 'threshold': 0.85, 'after_action': 'continue', 'run_code_after_action': False}), package_patch('player', 'recognize_region_with_boxes', return_value=('可锁取', [{'text': '可锁取', 'center_x': 10, 'center_y': 20}])):
            player._execute_image({
                "type": "image_match", "template": "", "module_ref": True,
                "module_key": "module:text", "region_mode": "template",
                "timeout_ms": 0, "on_timeout": "continue",
            }, None)
        expected = "奖励可领取 OCR：识别到「可锁取」；期望「可领取」· 未命中"
        self.assertIn(expected, logs)
        self.assertIn(expected, statuses)

    def test_text_module_waits_until_expected_text_disappears(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._status = Mock()
        player._run_action_sequence = Mock()
        module_obj = {
            "recognize": "text", "expected_text": "加载中", "match_mode": "contains",
            "wait_text_absent": True,
            "template": "", "region": [10, 20, 300, 400],
            "blocking": False, "interval_ms": 250, "threshold": 0.85,
            "after_action": "click_match", "run_code_after_action": False,
            "run_code_on_timeout": True, "not_found_timeout_ms": 0,
            "on_timeout_actions": [{"type": "delay", "ms": 1}], "delay_ms": 0,
            "ocr_offset_up": 2, "ocr_offset_down": 7,
            "ocr_offset_left": 3, "ocr_offset_right": 13,
        }
        with package_patch('player', 'registered_module_object', return_value=module_obj), \
             package_patch('player', 'recognize_region_with_boxes', side_effect=[('加载中', [{'text': '加载中', 'center_x': 50, 'center_y': 60}]), ('仍在加载中', [{'text': '仍在加载中', 'center_x': 80, 'center_y': 100}]), ('完成', [{'text': '完成', 'center_x': 20, 'center_y': 30}])]) as recognize, \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button'):
            result = player._execute_image({
                "type": "image_match", "template": "", "module_ref": True,
                "module_key": "module:text", "region_mode": "template",
                "timeout_ms": 0, "on_timeout": "continue",
            }, None)
        self.assertIsNone(result)
        self.assertEqual(recognize.call_count, 3)
        self.assertEqual(
            [call.args for call in move.call_args_list],
            [(60, 65), (90, 105)],
        )
        player._run_action_sequence.assert_not_called()
        self.assertTrue(any(
            "结束循环" in item.args[0]
            for item in player._status.call_args_list
        ))

    def test_template_module_repeats_until_target_image_disappears(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._status = Mock()
        module_obj = {
            "template": "images/claim.png", "region": [10, 20, 300, 400],
            "wait_text_absent": True, "blocking": False,
            "interval_ms": 250, "threshold": 0.85, "delay_ms": 0,
            "after_action": "click_match", "run_code_after_action": False,
            "run_code_on_timeout": True, "not_found_timeout_ms": 0,
            "on_timeout_actions": [{"type": "delay", "ms": 1}],
            "button": "left",
        }
        found = {
            "x": 100, "y": 200, "width": 40, "height": 20,
            "center_x": 120, "center_y": 210, "score": 0.96,
        }
        with package_patch('player', 'registered_module_object', return_value=module_obj), \
             package_patch('player', 'find_template', side_effect=[found, found, None]) as find, \
             package_patch('player', 'show_overlay'), \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button'):
            result = player._execute_image({
                "type": "image_match", "template": "images/claim.png",
                "module_ref": True, "module_key": "module:claim",
                "region_mode": "template", "timeout_ms": 0,
                "on_timeout": "continue",
            }, None)

        self.assertIsNone(result)
        self.assertEqual(find.call_count, 3)
        self.assertEqual([call.args for call in move.call_args_list], [(120, 210), (120, 210)])
        self.assertTrue(any(
            "目标模板" in item.args[0] and "结束循环" in item.args[0]
            for item in player._status.call_args_list
        ))

    def test_text_module_blocking_waits_then_runs_timeout_segment(self):
        player = MacroPlayer()
        player._wait = Mock()
        player._run_action_sequence = Mock()
        segment = [{"type": "delay", "ms": 25}]
        with package_patch('player', 'registered_module_object', return_value={'recognize': 'text', 'expected_text': '体力不足', 'match_mode': 'contains', 'template': '', 'region': [], 'blocking': True, 'interval_ms': 250, 'threshold': 0.85, 'after_action': 'continue', 'run_code_after_action': False, 'run_code_on_timeout': True, 'not_found_timeout_ms': 0, 'on_timeout_actions': segment}), \
             package_patch('player', 'recognize_region_with_boxes', return_value=('其他文字', [{'text': '其他文字'}])):
            result = player._execute_image({
                "type": "image_match", "template": "", "module_ref": True,
                "module_key": "module:text", "region_mode": "template",
            }, None)
        self.assertIsNone(result)
        player._run_action_sequence.assert_called_once_with(
            segment, None, script_stack=None, depth=1,
        )

    def test_notice_callback_does_not_block(self):
        events = []
        player = MacroPlayer(
            on_notice=lambda text, duration: events.append(("notice", text, duration)),
        )
        player.play([
            {"type": "notice", "text": "只是提醒", "duration_ms": 2500},
        ])
        self.assertEqual(events, [
            ("notice", "只是提醒", 2500),
        ])

    def test_scroll_playback_forwards_dx_and_dy(self):
        # 滚轮动作曾只传 dy：send_scroll(dx, dy) 双参数签名下直接 TypeError，
        # 且纵/横滚轮永远发不出。回归：dx、dy 必须原样传给 send_scroll。
        player = MacroPlayer()
        with package_patch('player', 'send_scroll') as scroll:
            player.play([
                {"type": "scroll", "dx": 3, "dy": -2, "delay_ms": 0},
            ])
        scroll.assert_called_once_with(3, -2)

    def test_scroll_playback_moves_cursor_to_the_action_position_first(self):
        # 「鼠标在指定位置滚轮上下滑动」：滚轮只发给光标下的窗口/控件，
        # 所以必须先移到动作坐标，再滚指定的方向与格数。
        player = MacroPlayer()
        with package_patch('player', 'send_scroll') as scroll, \
             package_patch('player', 'send_move_absolute') as move:
            player.play([
                {"type": "scroll", "dx": 0, "dy": -3, "x": 640, "y": 360, "delay_ms": 0},
            ])
        move.assert_called_once_with(640, 360)
        scroll.assert_called_once_with(0, -3)

    def test_scroll_playback_without_position_keeps_current_cursor(self):
        # 手写的滚轮动作没有坐标时只在光标当前位置滚动，不能把光标甩到 (0, 0)。
        player = MacroPlayer()
        with package_patch('player', 'send_scroll') as scroll, \
             package_patch('player', 'send_move_absolute') as move:
            player.play([{"type": "scroll", "dx": 0, "dy": 3, "delay_ms": 0}])
        move.assert_not_called()
        scroll.assert_called_once_with(0, 3)

    def test_player_template_scale_from_screens(self):
        player = MacroPlayer()
        player._source_screen = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        player._target_screen = {"left": 0, "top": 0, "width": 3840, "height": 2160}
        self.assertEqual(player._template_scale(), 2.0)
        player._source_screen = None
        self.assertEqual(player._template_scale(), 1.0)

    def test_image_action_passes_template_scale_to_matcher(self):
        # 识图动作的模板匹配必须带上录制屏 → 当前屏的缩放系数，
        # 否则截图尺寸不同时匹配度下降（坐标缩放 ≠ 模板缩放）。
        player = MacroPlayer()
        player._source_screen = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        player._target_screen = {"left": 0, "top": 0, "width": 3840, "height": 2160}
        player._wait = Mock()
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "t.png"
            template = np.zeros((8, 8, 3), dtype=np.uint8)
            cv2.imwrite(str(template_path), template)
            with package_patch('player', 'find_template', return_value=None) as find:
                player._execute_image({
                    "type": "image_match", "template": str(template_path),
                    "timeout_ms": 0, "interval_ms": 50, "threshold": 0.85,
                }, None)
            self.assertGreaterEqual(len(find.call_args_list), 1)
            self.assertEqual(find.call_args.kwargs["scale"], 2.0)

    def test_key_press_skips_zero_vk(self):
        player = MacroPlayer()
        with package_patch('player', 'send_key') as send:
            player.play([{"type": "key_press", "vk": 0, "hold_ms": 10}])
        send.assert_not_called()

    def test_key_press_stop_during_hold_releases_key(self):
        # F12 在按下-抬起窗口内停止：抬起必须补发，不能物理卡键。
        player = MacroPlayer()
        # 第一次 _wait 是动作的延时等待（0ms），第二次是按住等待：在其中停止。
        player._wait = Mock(side_effect=[None, PlaybackStopped()])
        with package_patch('player', 'send_key') as send:
            player.play([{"type": "key_press", "vk": 65, "hold_ms": 300}])
        self.assertEqual(
            [call.args for call in send.call_args_list], [(65, True), (65, False)],
        )

    def test_click_stop_during_hold_releases_button(self):
        player = MacroPlayer()
        player._wait = Mock(side_effect=[None, PlaybackStopped()])
        with package_patch('player', 'send_button') as send, \
             package_patch('player', 'send_move_absolute'):
            player.play([{"type": "click", "x": 10, "y": 20, "hold_ms": 300}])
        self.assertEqual(
            [call.args for call in send.call_args_list],
            [("left", True), ("left", False)],
        )

    def test_guard_jump_inside_nested_sequence_propagates_to_outer_frame(self):
        # 守卫跳转按设计只由最外层动作序列（depth==0）解析：嵌套帧必须
        # 原样抛出，否则会按内层动作列表错误解析目标行。
        hit = {
            "kind": "success", "log_subject": "模块[m] · 图",
            "jump_action_id": "outer-a", "jump_row": 1, "delay_ms": 0,
        }

        player = MacroPlayer(on_guard_poll=lambda: hit)
        actions = [{"type": "delay", "ms": 1, "action_id": "outer-a"}]
        with self.assertRaises(GuardJumpRequest):
            player._run_action_sequence(actions, None, depth=1)

    def test_activation_window_runs_once_then_target_is_raised(self):
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'is_window_process_foreground', return_value=True), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player.play(
                [{"type": "comment"}], hwnd=123,
                activation_hwnd=456, activate_target=True,
            )
        # 目标窗口已在前台：播放启动不再激活它（程序化激活会让游戏客户端
        # 重弹“点击游戏画面继续操作”），只激活执行前置窗口一次。
        self.assertEqual(activate.call_args_list, [call(456)])

    def test_prepared_activation_window_is_not_activated_again_by_player(self):
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'is_window_process_foreground', return_value=True), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player.play(
                [{"type": "comment"}], hwnd=123,
                activation_hwnd=456, activation_prepared=True,
            )
        # 应用层已在 OCR 等待前完成前置激活，播放器只负责继续播放，不能重复抢焦点。
        activate.assert_not_called()

    def test_play_start_raises_target_only_when_not_foreground(self):
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'is_window_process_foreground', side_effect=[False, True]), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player.play(
                [{"type": "comment"}], hwnd=123,
                activation_hwnd=456, activate_target=True,
            )
        # 播放启动时目标不在前台：激活一次；随后首个动作的前台守卫看到
        # 目标已在前台，不再重复激活（v1.1.0 每个输入动作前的前台校验）。
        self.assertEqual(activate.call_args_list, [call(456), call(123)])

    def test_explicit_activation_window_is_raised_when_target_activation_is_off(self):
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'activate_window', return_value=True) as activate:
            player.play(
                [{"type": "comment"}], hwnd=123,
                activation_hwnd=456, activate_target=False,
            )
        activate.assert_called_once_with(456)

    def test_disabled_auto_activation_does_not_raise_target_window(self):
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'is_window_process_foreground', return_value=False), \
             package_patch('player', 'activate_window') as activate:
            player.play([{"type": "comment"}], hwnd=123, activate_target=False)
        activate.assert_not_called()

    def test_stale_bound_window_does_not_stop_ordinary_actions(self):
        logs = []
        player = MacroPlayer(on_log=logs.append)
        with package_patch('player', 'is_window', return_value=False), \
             package_patch('player', 'activate_window') as activate, \
             package_patch('player', 'send_move_absolute') as move:
            player.play([
                {"type": "mouse_move", "mode": "absolute", "x": 120, "y": 240},
            ], hwnd=123)
        move.assert_called_once_with(120, 240)
        activate.assert_not_called()
        self.assertEqual(logs, [
            "绑定窗口已失效；普通动作继续执行，只有相对转向或窗口区域动作需要重新绑定。",
        ])

    def test_stale_bound_window_relative_action_sends_directly(self):
        # 相对移动是系统级事件（MOUSEEVENTF_MOVE），窗口失效时不再报错，
        # 直接发送到当前前台窗口（通用转向，不区分游戏/桌面窗口）。
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=False), \
             package_patch('player', 'send_move_relative') as move:
            player.play([
                {"type": "mouse_move", "mode": "relative", "dx": 2, "dy": 3},
            ], hwnd=123)
        move.assert_called_once_with(2, 3)

    def test_rebind_window_action_updates_remaining_actions_and_exposes_new_hwnd(self):
        request_target = Mock(return_value=456)
        player = MacroPlayer(on_target_window_request=request_target)
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'is_window_process_foreground', return_value=True), \
             package_patch('player', 'send_move_absolute'), \
             patch.object(player, '_clamp_click_point', return_value=(10, 20)) as clamp:
            player.play([
                {"type": "rebind_window"},
                {"type": "mouse_move", "mode": "absolute", "x": 10, "y": 20},
            ], hwnd=123)

        request_target.assert_called_once_with()
        clamp.assert_called_once_with(10, 20, 456)
        self.assertEqual(player.rebound_target_hwnd, 456)

    def test_rebind_window_action_stops_when_saved_target_is_not_found(self):
        player = MacroPlayer(on_target_window_request=Mock(return_value=None))

        with self.assertRaisesRegex(RuntimeError, "未找到已保存的目标窗口"):
            player.play([{"type": "rebind_window"}])

    def test_relative_action_resolves_game_window_created_after_workflow_start(self):
        player = MacroPlayer(on_target_window_request=Mock(return_value=456))
        with package_patch('player', 'is_window', side_effect=lambda hwnd: hwnd == 456), \
             package_patch('player', 'activate_window', return_value=True) as activate, \
             package_patch('player', 'send_move_relative') as move:
            player.play([
                {"type": "mouse_move", "mode": "relative", "dx": 2, "dy": 3},
            ], hwnd=None)
        player.on_target_window_request.assert_called_once_with()
        activate.assert_not_called()
        move.assert_called_once_with(2, 3)

    def test_relative_move_sends_when_auto_activation_off_and_not_foreground(self):
        # 关闭自动前置且目标窗口不在前台：仅提示，仍直接发送相对移动。
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'is_window_process_foreground', return_value=False), \
             package_patch('player', 'activate_window') as activate, \
             package_patch('player', 'send_move_relative') as move:
            player.play(
                [{"type": "mouse_move", "mode": "relative", "dx": 2, "dy": 3}],
                hwnd=123, activate_target=False,
            )
        activate.assert_not_called()
        move.assert_called_once_with(2, 3)

    def test_absolute_coordinates_scale_to_current_resolution(self):
        source = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        target = {"left": 0, "top": 0, "width": 1280, "height": 720}
        self.assertEqual(scale_screen_point(960, 540, source, target), (640, 360))
        player = MacroPlayer()
        with package_patch('player', 'get_playback_screen_rect', return_value=target), \
             package_patch('player', 'send_move_absolute') as move:
            player.play(
                [{"type": "mouse_move", "mode": "absolute", "x": 960, "y": 540}],
                source_screen=source,
            )
        move.assert_called_once_with(640, 360)

    def test_second_match_can_click_first_match_position(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            second_path = Path(folder) / "second.png"
            second_path.write_bytes(b"x")
            player = MacroPlayer()
            player._wait = Mock()
            first = {"center_x": 111, "center_y": 222}
            second = {
                "x": 10, "y": 20, "width": 30, "height": 40,
                "center_x": 25, "center_y": 40, "score": 0.9,
            }
            obj = {
                "second_match_template": str(second_path),
                "second_match_click_target": "first",
            }
            with package_patch('player', 'registered_template_region', return_value=[5, 6, 70, 80]), \
                 package_patch('player', 'find_template', return_value=second) as find, \
                 package_patch('player', 'show_overlay'), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button'):
                player._execute_second_match(obj, None, first)
            find.assert_called_once_with(second_path, 0.85, (5, 6, 70, 80),
                                         ignore_background=False, scale=1.0)
            move.assert_called_once_with(111, 222)

    def test_second_match_can_click_custom_region_center(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            second_path = Path(folder) / "second.png"
            second_path.write_bytes(b"x")
            player = MacroPlayer()
            player._wait = Mock()
            player._source_screen = None
            player._target_screen = None
            second = {
                "x": 10, "y": 20, "width": 30, "height": 40,
                "center_x": 25, "center_y": 40, "score": 0.9,
            }
            obj = {
                "second_match_template": str(second_path),
                "second_match_click_target": "custom_region",
                "second_match_click_region": [100, 200, 80, 40],
            }
            with package_patch('player', 'find_template', return_value=second), \
                 package_patch('player', 'show_overlay'), \
                 package_patch('player', 'send_move_absolute') as move, \
                 package_patch('player', 'send_button'):
                player._execute_second_match(obj, None)
            move.assert_called_once_with(140, 220)

    def test_invalid_recorded_resolution_does_not_change_coordinates(self):
        self.assertEqual(
            scale_screen_point(320, 240, {"width": 0, "height": 0}, {"width": 1280, "height": 720}),
            (320, 240),
        )

    def test_delay_and_comment(self):
        player = MacroPlayer()
        start = time.perf_counter()
        player.play([{"type": "delay", "ms": 15}, {"type": "comment", "text": "ok"}])
        self.assertGreaterEqual(time.perf_counter() - start, 0.01)

    def test_repeat_callback_reports_current_and_total(self):
        player = MacroPlayer()
        progress = []
        player.play([{"type": "delay", "ms": 0}], repeats=3,
                    on_repeat=lambda current, total: progress.append((current, total)))
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])

    def test_play_start_repeat_skips_earlier_repeats(self):
        player = MacroPlayer()
        progress = []
        completed = []
        player.play(
            [{"type": "delay", "ms": 0}], repeats=5, start_repeat=2,
            on_repeat=lambda current, total: progress.append((current, total)),
            on_repeat_complete=lambda current, total: completed.append((current, total)),
        )
        # 从第 3 次开始执行：只运行 3、4、5 次，但回调仍报告正确的总数。
        self.assertEqual(progress, [(3, 5), (4, 5), (5, 5)])
        self.assertEqual(completed, [(3, 5), (4, 5), (5, 5)])

    def test_play_start_repeat_clamps_out_of_range(self):
        player = MacroPlayer()
        progress = []
        player.play([{"type": "delay", "ms": 0}], repeats=2, start_repeat=99,
                    on_repeat=lambda current, total: progress.append((current, total)))
        self.assertEqual(progress, [(2, 2)])

    def test_play_click_current_position_uses_cursor(self):
        # 点击鼠标当前位置：不移动光标、不缩放，鼠标在哪就在哪点击。
        player = MacroPlayer()
        with package_patch('player', 'get_cursor_pos', return_value=(777, 888)) as cursor, \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player.play(
                [{"type": "click", "button": "left", "pos_mode": "current",
                  "hold_ms": 0, "delay_ms": 0}],
            )
        cursor.assert_not_called()
        move.assert_not_called()
        self.assertEqual(
            [call.args for call in button.call_args_list],
            [("left", True), ("left", False)],
        )

    def test_play_click_fixed_position_moves_and_scales(self):
        player = MacroPlayer()
        player._scale_point = Mock(side_effect=lambda x, y: (x * 2, y * 2))
        with package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button') as button:
            player.play(
                [{"type": "click", "button": "left", "x": 50, "y": 60,
                  "hold_ms": 0, "delay_ms": 0}],
            )
        move.assert_called_once_with(100, 120)
        self.assertEqual(
            [call.args for call in button.call_args_list],
            [("left", True), ("left", False)],
        )

    def test_play_start_repeat_zero_runs_all(self):
        player = MacroPlayer()
        progress = []
        player.play([{"type": "delay", "ms": 0}], repeats=2, start_repeat=0,
                    on_repeat=lambda current, total: progress.append((current, total)))
        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_play_resume_action_continues_from_next_action(self):
        player = MacroPlayer()
        executed = []
        player.play(
            [{"type": "delay", "ms": 0}] * 3, repeats=2, resume_action_index=1,
            on_action=lambda next_index, total: executed.append(next_index),
        )
        # 第一次重复从动作 2 的下一个动作继续（动作 2、3），第二次重复从头执行。
        self.assertEqual(executed, [2, 3, 1, 2, 3])

    def test_play_resume_action_combines_with_start_repeat(self):
        player = MacroPlayer()
        executed = []
        player.play(
            [{"type": "delay", "ms": 0}] * 4, repeats=4, start_repeat=2,
            resume_action_index=1,
            on_action=lambda next_index, total: executed.append(next_index),
        )
        # 第 3 次重复从动作 2 继续，第 4 次从头。
        self.assertEqual(executed, [2, 3, 4, 1, 2, 3, 4])

    def test_play_resume_action_at_script_end_skips_finished_repeat(self):
        player = MacroPlayer()
        executed = []
        player.play(
            [{"type": "delay", "ms": 0}] * 3, repeats=2, resume_action_index=3,
            on_action=lambda next_index, total: executed.append(next_index),
        )
        # 被打断重复的动作已全部完成：跳过该次，从下一次重复的第一个动作开始。
        self.assertEqual(executed, [1, 2, 3])

    def test_play_resume_action_last_repeat_done_completes_immediately(self):
        player = MacroPlayer()
        executed = []
        player.play(
            [{"type": "delay", "ms": 0}] * 2, repeats=2, start_repeat=1,
            resume_action_index=2,
            on_action=lambda next_index, total: executed.append(next_index),
        )
        self.assertEqual(executed, [])

    def test_repeat_start_action_id_starts_later_repeats_from_row(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {"type": "delay", "ms": 0, "action_id": f"id{i}"}
            for i in range(6)
        ]
        player.play(
            actions, repeats=5, repeat_start_action_id="id4",
            on_action=lambda next_index, total: executed.append(next_index),
        )
        # 第 1 次从头（1..6）；第 2-5 次从第 5 行（index 4）开始。
        self.assertEqual(
            executed,
            [1, 2, 3, 4, 5, 6, 5, 6, 5, 6, 5, 6, 5, 6],
        )

    def test_repeat_start_action_id_no_effect_on_single_repeat(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {"type": "delay", "ms": 0, "action_id": f"id{i}"}
            for i in range(6)
        ]
        player.play(
            actions, repeats=1, repeat_start_action_id="id4",
            on_action=lambda next_index, total: executed.append(next_index),
        )
        self.assertEqual(executed, [1, 2, 3, 4, 5, 6])

    def test_repeat_start_action_id_missing_falls_back_to_first_row(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {"type": "delay", "ms": 0, "action_id": f"id{i}"}
            for i in range(6)
        ]
        player.play(
            actions, repeats=5, repeat_start_action_id="missing",
            on_action=lambda next_index, total: executed.append(next_index),
        )
        self.assertEqual(executed, [1, 2, 3, 4, 5, 6] * 5)

    def test_repeat_start_action_id_combines_with_resume(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {"type": "delay", "ms": 0, "action_id": f"id{i}"}
            for i in range(6)
        ]
        player.play(
            actions, repeats=5, start_repeat=2, resume_action_index=1,
            repeat_start_action_id="id4",
            on_action=lambda next_index, total: executed.append(next_index),
        )
        # 第 3 次从断点（动作 2）继续；第 4-5 次从指定行（第 5 行）开始。
        self.assertEqual(executed, [2, 3, 4, 5, 6, 5, 6, 5, 6])

    def test_repeat_start_action_id_with_overflow_resume_skips_finished_repeat(self):
        player = MacroPlayer()
        executed = []
        actions = [
            {"type": "delay", "ms": 0, "action_id": f"id{i}"}
            for i in range(6)
        ]
        player.play(
            actions, repeats=5, start_repeat=2, resume_action_index=6,
            repeat_start_action_id="id4",
            on_action=lambda next_index, total: executed.append(next_index),
        )
        # 被打断的重复已全部完成：跳过；余下重复都从指定行（第 5 行）开始。
        self.assertEqual(executed, [5, 6, 5, 6])

    def test_on_action_reports_next_action_index_and_total(self):
        player = MacroPlayer()
        reports = []
        player.play(
            [{"type": "delay", "ms": 0}, {"type": "comment"}],
            on_action=lambda next_index, total: reports.append((next_index, total)),
        )
        self.assertEqual(reports, [(1, 2), (2, 2)])

    def test_repeat_complete_callback_only_runs_after_successful_repeat(self):
        player = MacroPlayer()
        completed = []
        player.play(
            [{"type": "delay", "ms": 0}], repeats=3,
            on_repeat_complete=lambda current, total: completed.append((current, total)),
        )
        self.assertEqual(completed, [(1, 3), (2, 3), (3, 3)])

        failed = []
        with self.assertRaises(RuntimeError):
            player.play(
                [{"type": "unknown-action"}],
                on_repeat_complete=lambda current, total: failed.append((current, total)),
            )
        self.assertEqual(failed, [])

    def test_repeat_interval_only_runs_between_repetitions(self):
        player = MacroPlayer()
        player._wait = Mock()
        player.play([{"type": "comment"}], repeats=3, repeat_interval_ms=750)
        self.assertEqual(
            [call.args[0] for call in player._wait.call_args_list if call.args[0]],
            [750, 750],
        )

    def test_stop_interrupts_wait(self):
        player = MacroPlayer()
        thread = threading.Thread(target=lambda: player.play([{"type": "delay", "ms": 5000}]))
        thread.start()
        time.sleep(0.03)
        player.stop()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())

    def test_relative_move_uses_121_compatibility_by_default(self):
        player = MacroPlayer()
        with package_patch('player', 'is_window', return_value=True), \
             package_patch('player', 'activate_window', return_value=True), \
             package_patch('player', 'send_move_relative') as send_relative:
            player.play([
                {"type": "mouse_move", "mode": "relative", "dx": 12, "dy": -4},
            ], hwnd=123)
        send_relative.assert_called_once_with(12, -4)

    def test_script_ref_executes_referenced_script_actions(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"
            ref_path.write_text(json.dumps({
                "name": "被引用脚本",
                "actions": [{"type": "notice", "text": "来自引用脚本", "duration_ms": 500}],
            }, ensure_ascii=False), encoding="utf-8")
            player.play([{"type": "script_ref", "script": str(ref_path), "delay_ms": 0}])
        self.assertEqual(notices, [("来自引用脚本", 500)])

    def test_script_ref_reads_latest_file_content_each_run(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"

            def write_referenced(text):
                ref_path.write_text(json.dumps({
                    "name": "ref",
                    "actions": [{"type": "notice", "text": text, "duration_ms": 500}],
                }, ensure_ascii=False), encoding="utf-8")

            write_referenced("第一版")
            player.play([{"type": "script_ref", "script": str(ref_path)}])
            write_referenced("第二版")
            player.play([{"type": "script_ref", "script": str(ref_path)}])
        self.assertEqual(notices, [("第一版", 500), ("第二版", 500)])

    def test_script_ref_executes_referenced_script_requested_number_of_times(self):
        notices = []
        player = MacroPlayer(on_notice=lambda text, duration: notices.append((text, duration)))
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"
            ref_path.write_text(json.dumps({
                "name": "被引用脚本",
                "actions": [{"type": "notice", "text": "重复执行", "duration_ms": 500}],
            }, ensure_ascii=False), encoding="utf-8")
            player.play([{
                "type": "script_ref", "script": str(ref_path), "repeats": 3,
            }])
        self.assertEqual(notices, [("重复执行", 500)] * 3)

    def test_script_ref_reloads_file_between_repetitions(self):
        notices = []
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"

            def write_referenced(text):
                ref_path.write_text(json.dumps({
                    "name": "ref",
                    "actions": [{"type": "notice", "text": text, "duration_ms": 500}],
                }, ensure_ascii=False), encoding="utf-8")

            write_referenced("第一版")

            def on_notice(text, duration):
                notices.append((text, duration))
                if text == "第一版":
                    write_referenced("第二版")

            player = MacroPlayer(on_notice=on_notice)
            player.play([{
                "type": "script_ref", "script": str(ref_path), "repeats": 2,
            }])
        self.assertEqual(notices, [("第一版", 500), ("第二版", 500)])

    def test_script_ref_respects_pre_and_after_delay(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"
            ref_path.write_text(json.dumps({
                "name": "ref",
                "actions": [{"type": "delay", "ms": 0}],
            }, ensure_ascii=False), encoding="utf-8")
            player.play([
                {"type": "script_ref", "script": str(ref_path), "delay_ms": 200, "after_delay_ms": 300},
            ])
        self.assertIn(200, waits)
        self.assertIn(300, waits)

    def test_script_ref_missing_file_raises(self):
        player = MacroPlayer()
        with self.assertRaisesRegex(RuntimeError, "不存在"):
            player.play([{"type": "script_ref", "script": "scripts/不存在的脚本.json"}])

    def test_script_ref_cycle_detected(self):
        player = MacroPlayer()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            a_path = Path(folder) / "a.json"
            b_path = Path(folder) / "b.json"
            a_path.write_text(json.dumps({
                "name": "A",
                "actions": [{"type": "script_ref", "script": str(b_path)}],
            }, ensure_ascii=False), encoding="utf-8")
            b_path.write_text(json.dumps({
                "name": "B",
                "actions": [{"type": "script_ref", "script": str(a_path)}],
            }, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "循环引用"):
                player.play([{"type": "script_ref", "script": str(a_path)}])

    def test_script_ref_summary(self):
        kind, detail, _delay = action_summary({
            "type": "script_ref", "script": "scripts/关卡/某脚本.json",
        })
        self.assertIn("引用脚本", kind)
        self.assertIn("某脚本", detail)
        self.assertIn("执行 1 次", detail)

    def test_script_ref_summary_shows_requested_repeat_count(self):
        _kind, detail, _delay = action_summary({
            "type": "script_ref", "script": "scripts/关卡/某脚本.json", "repeats": 4,
        })
        self.assertIn("执行 4 次", detail)

    def test_open_app_action_launches_selected_executable(self):
        player = MacroPlayer()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            exe = Path(folder) / "app.exe"
            exe.write_bytes(b"MZ")
            with patch("macroflow.execution.player.os.startfile") as startfile:
                player.play([{"type": "open_app", "path": str(exe)}])
            startfile.assert_called_once_with(str(exe), arguments="")

    def test_open_app_action_launches_with_arguments(self):
        player = MacroPlayer()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            exe = Path(folder) / "app.exe"
            exe.write_bytes(b"MZ")
            with patch("macroflow.execution.player.os.startfile") as startfile:
                player.play([{"type": "open_app", "path": str(exe), "args": "-windowed -u=dev"}])
            startfile.assert_called_once_with(str(exe), arguments="-windowed -u=dev")

    def test_open_app_missing_file_raises(self):
        player = MacroPlayer()
        with self.assertRaisesRegex(RuntimeError, "不存在"):
            player.play([{"type": "open_app", "path": "C:/no_such_dir/app.exe"}])

    def test_open_app_summary(self):
        kind, detail, _delay = action_summary({
            "type": "open_app", "path": "C:/Tools/某软件.exe",
        })
        self.assertIn("打开软件", kind)
        self.assertIn("某软件.exe", detail)
        self.assertNotIn("（", detail)

    def test_open_app_summary_with_arguments(self):
        _kind, detail, _delay = action_summary({
            "type": "open_app", "path": "C:/Tools/某软件.exe", "args": "-windowed",
        })
        self.assertIn("某软件.exe", detail)
        self.assertIn("-windowed", detail)

    def test_close_app_action_missing_name_raises(self):
        player = MacroPlayer()
        with self.assertRaisesRegex(RuntimeError, "缺少进程名"):
            player._execute_close_app({"type": "close_app", "name": "  "})

    def test_close_app_not_running_skips(self):
        player = MacroPlayer()
        statuses = []
        player._status = lambda text: statuses.append(text)
        with package_patch('player', 'is_process_running', return_value=False), \
             package_patch('player', 'taskkill_process') as taskkill:
            player._execute_close_app({
                "type": "close_app", "name": "clash-verge.exe",
                "graceful": True, "graceful_wait_ms": 2000,
            })
        taskkill.assert_not_called()
        self.assertTrue(any("未在运行" in text for text in statuses))

    def test_close_app_graceful_then_force_fallback(self):
        player = MacroPlayer()
        statuses = []
        player._status = lambda text: statuses.append(text)
        # running → graceful → still running (wait expired) → force → gone
        with package_patch('player', 'is_process_running', side_effect=[True, True, True, True, False]), \
             package_patch('player', 'taskkill_process', side_effect=[(0, ''), (0, '')]) as taskkill:
            player._execute_close_app({
                "type": "close_app", "name": "clash-verge.exe",
                "graceful": True, "graceful_wait_ms": 0,
            })
        self.assertEqual(
            [(call.args[0], call.kwargs.get("force")) for call in taskkill.call_args_list],
            [("clash-verge.exe", False), ("clash-verge.exe", True)],
        )
        self.assertTrue(any("强制结束" in text for text in statuses))

    def test_close_app_force_direct(self):
        player = MacroPlayer()
        with package_patch('player', 'is_process_running', side_effect=[True, True, False]), \
             package_patch('player', 'taskkill_process', side_effect=[(0, '')]) as taskkill:
            player._execute_close_app({
                "type": "close_app", "name": "demo.exe",
                "graceful": False, "graceful_wait_ms": 2000,
            })
        taskkill.assert_called_once_with("demo.exe", force=True, tree=False)

    def test_close_app_graceful_success(self):
        player = MacroPlayer()
        with package_patch('player', 'is_process_running', side_effect=[True, True, False, False]), \
             package_patch('player', 'taskkill_process', side_effect=[(0, '')]) as taskkill:
            player._execute_close_app({
                "type": "close_app", "name": "demo.exe",
                "graceful": True, "graceful_wait_ms": 2000,
            })
        taskkill.assert_called_once_with("demo.exe", force=False, tree=False)

    def test_close_app_graceful_access_denied_forces(self):
        player = MacroPlayer()
        statuses = []
        player._status = lambda text: statuses.append(text)
        # 优雅关闭请求被拒绝（如权限不足）→ 不再干等，直接强制结束
        with package_patch('player', 'is_process_running', side_effect=[True, True, False]), \
             package_patch('player', 'taskkill_process', side_effect=[(1, '拒绝访问'), (0, '')]) as taskkill:
            player._execute_close_app({
                "type": "close_app", "name": "demo.exe",
                "graceful": True, "graceful_wait_ms": 60000,
            })
        self.assertEqual(
            [(call.args[0], call.kwargs.get("force")) for call in taskkill.call_args_list],
            [("demo.exe", False), ("demo.exe", True)],
        )
        self.assertTrue(any("关闭请求失败" in text for text in statuses))

    def test_close_app_elevated_fallback(self):
        player = MacroPlayer()
        statuses = []
        player._status = lambda text: statuses.append(text)
        # 普通权限反复结束失败，最终由管理员权限结束
        with package_patch('player', 'is_process_running', side_effect=[True, True, False]), \
             package_patch('player', 'taskkill_process', return_value=(1, '拒绝访问')), \
             package_patch('player', 'elevated_taskkill', return_value=True) as elev:
            player._execute_close_app({
                "type": "close_app", "name": "app_launcher.exe",
                "graceful": True, "graceful_wait_ms": 2000,
                "tree": False, "elevated_retry": True,
            })
        elev.assert_called_once_with("app_launcher.exe", tree=False)
        self.assertTrue(any("管理员权限" in text for text in statuses))

    def test_close_app_elevated_declined_raises(self):
        player = MacroPlayer()
        # UAC 授权被取消 → 最终报错
        with package_patch('player', 'is_process_running', return_value=True), \
             package_patch('player', 'taskkill_process', return_value=(1, '拒绝访问')), \
             package_patch('player', 'elevated_taskkill', return_value=False) as elev:
            with self.assertRaisesRegex(RuntimeError, "无法结束进程"):
                player._execute_close_app({
                    "type": "close_app", "name": "demo.exe",
                    "graceful": False, "elevated_retry": True,
                })
        elev.assert_called_once()

    def test_close_app_summary(self):
        kind, detail, _delay = action_summary({
            "type": "close_app", "name": "clash-verge.exe",
        })
        self.assertIn("关闭软件", kind)
        self.assertIn("clash-verge.exe", detail)
        self.assertIn("优雅", detail)

    def test_close_app_summary_force(self):
        _kind, detail, _delay = action_summary({
            "type": "close_app", "name": "demo.exe", "graceful": False,
        })
        self.assertIn("强制", detail)


class SingleActionPlaybackTests(unittest.TestCase):
    """单独执行一个动作：只跑这一行，次数是这一行的调用次数，控制流不逃出边界。"""

    @staticmethod
    def _collector():
        notices = []
        logs = []
        player = MacroPlayer(
            on_notice=lambda text, _duration: notices.append(text),
            on_log=logs.append,
        )
        return player, notices, logs

    def test_only_the_selected_action_runs(self):
        player, notices, _logs = self._collector()
        actions = [
            {"type": "notice", "text": "第一行", "duration_ms": 1},
            {"type": "notice", "text": "选中行", "duration_ms": 1},
            {"type": "notice", "text": "第三行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=1, single_action=True)
        self.assertEqual(notices, ["选中行"])

    def test_repeats_are_the_call_count_of_the_selected_action(self):
        player, notices, _logs = self._collector()
        actions = [
            {"type": "notice", "text": "第一行", "duration_ms": 1},
            {"type": "notice", "text": "选中行", "duration_ms": 1},
        ]
        player.play(actions, repeats=3, start_index=1, single_action=True)
        self.assertEqual(notices, ["选中行"] * 3)

    def test_selected_row_keeps_its_own_count_and_delays(self):
        # 一行连点 3 次，单独执行 2 次 → 6 下；执行前后延时照旧生效。
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        with package_patch('player', 'send_button') as button, \
             package_patch('player', 'send_move_absolute'):
            player.play([
                {"type": "repeat_click", "button": "left", "x": 5, "y": 6,
                 "count": 3, "interval_ms": 10, "hold_ms": 1,
                 "delay_ms": 20, "after_delay_ms": 30},
            ], repeats=2, single_action=True)
        downs = [item for item in button.call_args_list if item.args[1] is True]
        self.assertEqual(len(downs), 6)
        self.assertIn(20, waits)
        self.assertIn(30, waits)

    def test_recorded_action_keeps_its_internal_steps(self):
        player, notices, _logs = self._collector()
        actions = [
            {"type": "comment", "text": "起点"},
            {"type": "recorded_input", RECORDED_INPUT_STEPS_KEY: [
                {"type": "notice", "text": "录制步骤一", "duration_ms": 1},
                {"type": "notice", "text": "录制步骤二", "duration_ms": 1},
            ]},
            {"type": "notice", "text": "第三行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=1, single_action=True)
        self.assertEqual(notices, ["录制步骤一", "录制步骤二"])

    def test_referenced_script_keeps_its_own_internal_repeat_count(self):
        player, notices, _logs = self._collector()
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"
            ref_path.write_text(json.dumps({
                "name": "被引用脚本",
                "actions": [{"type": "notice", "text": "引用动作", "duration_ms": 1}],
            }, ensure_ascii=False), encoding="utf-8")
            actions = [
                {"type": "comment", "text": "起点"},
                {"type": "script_ref", "script": str(ref_path), "repeats": 2, "delay_ms": 0},
                {"type": "notice", "text": "第三行", "duration_ms": 1},
            ]
            player.play(actions, repeats=1, start_index=1, single_action=True)
        self.assertEqual(notices, ["引用动作", "引用动作"])

    def test_image_action_keeps_its_own_detection_logic(self):
        player, notices, _logs = self._collector()
        match = {
            "x": 10, "y": 20, "width": 30, "height": 40,
            "center_x": 25, "center_y": 40, "score": 0.95,
        }
        actions = [
            {"type": "comment", "text": "起点"},
            {"type": "image_match", "template": "images/目标.png", "delay_ms": 0,
             "on_found": "click", "found_delay_ms": 5,
             "click_target": "custom", "click_point": [700, 500],
             "show_result_notice": False},
            {"type": "notice", "text": "第三行", "duration_ms": 1},
        ]
        with package_patch('player', 'find_template', return_value=match), \
             package_patch('player', 'send_move_absolute') as move, \
             package_patch('player', 'send_button'):
            player.play(actions, repeats=1, start_index=1, single_action=True)
        move.assert_called_once_with(700, 500)
        self.assertEqual(notices, [])

    def test_image_jump_does_not_escape_the_single_action(self):
        player, notices, logs = self._collector()
        actions = [
            {"type": "comment", "text": "起点"},
            {"type": "image_match", "template": "images/目标.png",
             "timeout_ms": 0, "delay_ms": 0,
             "on_timeout": "jump", "timeout_jump_row": 3,
             "show_result_notice": False},
            {"type": "notice", "text": "目标行", "duration_ms": 1},
        ]
        with package_patch('player', 'find_template', return_value=None):
            player.play(actions, repeats=1, start_index=1, single_action=True)
        self.assertEqual(notices, [])
        self.assertTrue(any("只记录不执行" in text for text in logs))

    def test_referenced_script_internal_global_detect_is_not_held(self):
        # 单独执行引用脚本行时，被引用脚本内部的全局检测行照常只注册守卫，
        # 不进入“保持检测直到触发”的等待（那是单独执行这一行本身才有的行为）。
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            ref_path = Path(folder) / "referenced.json"
            ref_path.write_text(json.dumps({
                "name": "被引用脚本",
                "actions": [
                    # jump_row 让它作为「内嵌全局模块行」留在动作列表里
                    # （没有 jump_row 的 global_detect 会被当成旧全局脚本迁移掉）。
                    {"type": "global_detect", "template": "images/guard.png", "jump_row": 0},
                    {"type": "notice", "text": "引用脚本后续动作", "duration_ms": 1},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            requests = []
            notices = []
            player = MacroPlayer(
                on_notice=lambda text, _duration: notices.append(text),
                on_global_detect_request=requests.append,
            )
            waits = []

            def wait(milliseconds):
                waits.append(milliseconds)
                if len(waits) > 10:
                    raise AssertionError("引用脚本内部的全局检测不应进入保持等待")

            player._wait = wait
            player.play([
                {"type": "comment", "text": "起点"},
                {"type": "script_ref", "script": str(ref_path), "delay_ms": 0},
            ], repeats=1, start_index=1, single_action=True)
        self.assertEqual(requests, [{"type": "global_detect", "template": "images/guard.png",
                                     "jump_row": 0}])
        self.assertEqual(notices, ["引用脚本后续动作"])

    def test_jump_action_records_target_without_running_it(self):
        player, notices, logs = self._collector()
        actions = [
            {"type": "comment", "text": "起点"},
            {"type": "jump", "jump_row": 3, "workflow_repeat_at_least_2": False},
            {"type": "notice", "text": "目标行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=1, single_action=True)
        self.assertEqual(notices, [])
        self.assertTrue(any("只记录不执行" in text for text in logs))

    def test_jump_to_last_row_does_not_execute_the_last_row(self):
        player, notices, logs = self._collector()
        actions = [
            {"type": "jump_current_script_last"},
            {"type": "notice", "text": "最后一行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=0, single_action=True)
        self.assertEqual(notices, [])
        self.assertTrue(any("只记录不执行" in text for text in logs))

    def test_end_current_script_action_ends_the_single_action_run(self):
        player, notices, logs = self._collector()
        actions = [
            {"type": "end_current_script"},
            {"type": "notice", "text": "第二行", "duration_ms": 1},
        ]
        player.play(actions, repeats=3, start_index=0, single_action=True)
        self.assertEqual(notices, [])
        self.assertEqual(
            len([text for text in logs if "单独执行：已" in text]), 1,
            "结束动作只记录一次，剩余重复立即结束",
        )

    def test_restart_workflow_is_skipped_when_running_alone(self):
        player, notices, logs = self._collector()
        player.on_restart_workflow_request = lambda _action: False
        actions = [
            {"type": "restart_workflow"},
            {"type": "notice", "text": "第二行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=0, single_action=True)
        self.assertEqual(notices, [])
        self.assertTrue(any("独立执行时跳过" in text for text in logs))

    def test_comment_row_completes_without_side_effects(self):
        traces = []
        player = MacroPlayer(on_trace_line=traces.append)
        player.play([
            {"type": "comment", "text": "备注"},
            {"type": "notice", "text": "第二行", "duration_ms": 1},
        ], repeats=1, start_index=0, single_action=True)
        self.assertTrue(any("无实际操作" in text for text in traces))

    def test_block_is_released_by_a_guard_jump_without_following_it(self):
        player, notices, _logs = self._collector()
        polls = []

        def poll():
            polls.append(1)
            return None if len(polls) == 1 else {"kind": "success"}

        player.on_guard_poll = poll
        player.handle_guard_hit = Mock(side_effect=GuardJumpRequest(jump_row=2))
        player._wait = lambda _milliseconds: None
        actions = [
            {"type": "block"},
            {"type": "notice", "text": "第二行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=0, single_action=True)
        player.handle_guard_hit.assert_called_once()
        self.assertEqual(notices, [])

    def test_global_detect_keeps_detecting_until_it_triggers(self):
        player, notices, _logs = self._collector()
        requests = []
        player.on_global_detect_request = requests.append
        polls = []

        def poll():
            polls.append(1)
            # 守卫一直不触发 → 保持检测；第 3 次评估才命中。
            return {"kind": "success"} if len(polls) == 3 else None

        player.on_guard_poll = poll
        player.handle_guard_hit = Mock()
        player._wait = lambda _milliseconds: None
        actions = [
            {"type": "global_detect", "template": "images/guard.png", "jump_row": 0},
            {"type": "notice", "text": "第二行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=0, single_action=True)
        self.assertEqual(requests, [actions[0]])
        self.assertGreaterEqual(len(polls), 3)
        player.handle_guard_hit.assert_called_once()
        self.assertEqual(notices, [])

    def test_stop_during_single_action_releases_held_input(self):
        player = MacroPlayer()
        player._held_keys.add(65)
        with package_patch('player', 'send_key') as key:
            player.play(
                [{"type": "delay", "ms": 5}, {"type": "notice", "text": "第二行"}],
                repeats=3, start_index=0, single_action=True,
                on_repeat=lambda _current, _total: player.stop(),
            )
        self.assertIn(call(65, False), key.call_args_list)
        self.assertFalse(player.running)


class SegmentPlaybackTests(unittest.TestCase):
    """循环执行片段：只跑选中的那一段，按轮重复，跳转不逃出片段。"""

    @staticmethod
    def _collector():
        notices = []
        logs = []
        player = MacroPlayer(
            on_notice=lambda text, _duration: notices.append(text),
            on_log=logs.append,
        )
        return player, notices, logs

    def test_only_the_selected_segment_runs(self):
        player, notices, _logs = self._collector()
        actions = [
            {"type": "notice", "text": "第一行", "duration_ms": 1},
            {"type": "notice", "text": "片段首行", "duration_ms": 1},
            {"type": "notice", "text": "片段末行", "duration_ms": 1},
            {"type": "notice", "text": "第四行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=1, segment_end=2)
        self.assertEqual(notices, ["片段首行", "片段末行"])

    def test_every_round_runs_the_whole_range_in_order(self):
        player, notices, _logs = self._collector()
        actions = [
            {"type": "notice", "text": "片段首行", "duration_ms": 1},
            {"type": "notice", "text": "片段末行", "duration_ms": 1},
            {"type": "notice", "text": "片段之外", "duration_ms": 1},
        ]
        player.play(actions, repeats=3, start_index=0, segment_end=1)
        self.assertEqual(notices, ["片段首行", "片段末行"] * 3)

    def test_segment_keeps_each_row_delay_and_skips_rows_outside(self):
        player = MacroPlayer()
        waits = []
        player._wait = lambda milliseconds: waits.append(milliseconds)
        actions = [
            {"type": "delay", "ms": 30, "delay_ms": 0},
            {"type": "delay", "ms": 40, "delay_ms": 0},
            {"type": "delay", "ms": 50, "delay_ms": 0},
        ]
        player.play(actions, repeats=2, start_index=0, segment_end=1)
        self.assertEqual(waits.count(30), 2)
        self.assertEqual(waits.count(40), 2)
        self.assertNotIn(50, waits)

    def test_jump_outside_the_segment_ends_the_run(self):
        player, notices, logs = self._collector()
        actions = [
            {"type": "comment", "text": "起点"},
            {"type": "jump", "jump_row": 3, "workflow_repeat_at_least_2": False},
            {"type": "notice", "text": "片段之外", "duration_ms": 1},
        ]
        player.play(actions, repeats=2, start_index=1, segment_end=1)
        self.assertEqual(notices, [])
        self.assertTrue(any("在片段之外" in text for text in logs))

    def test_jump_inside_the_segment_keeps_running(self):
        player, notices, _logs = self._collector()
        actions = [
            {"type": "comment", "text": "片段之外"},
            {"type": "jump", "jump_row": 3, "workflow_repeat_at_least_2": False},
            {"type": "notice", "text": "片段末行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=1, segment_end=2)
        self.assertEqual(notices, ["片段末行"])

    def test_jump_to_last_row_does_not_execute_outside_the_segment(self):
        player, notices, logs = self._collector()
        actions = [
            {"type": "comment", "text": "起点"},
            {"type": "jump_current_script_last"},
            {"type": "notice", "text": "最后一行", "duration_ms": 1},
        ]
        player.play(actions, repeats=1, start_index=1, segment_end=1)
        self.assertEqual(notices, [])
        self.assertTrue(any("在片段之外" in text for text in logs))

    def test_segment_scope_gets_its_last_row(self):
        rows = []
        player = MacroPlayer(
            on_script_scope_enter=(
                lambda actions, origin_row=0, last_row=None:
                rows.append((origin_row, last_row)) or ()
            ),
        )
        player._status = lambda text: None
        player.play([
            {"type": "notice", "text": "一", "duration_ms": 1},
            {"type": "notice", "text": "二", "duration_ms": 1},
            {"type": "notice", "text": "三", "duration_ms": 1},
        ], repeats=2, start_index=1, segment_end=2)
        self.assertEqual(rows, [(1, 2), (1, 2)])


class CaptureFailureToleranceTests(unittest.TestCase):
    """截图失败容错：瞬时失败自愈，等待识图期间按未识别到轮询，持续失败才收尾。"""

    class _FakeGrabber:
        monitors = [{"left": 0, "top": 0, "width": 4, "height": 4}]

        def __init__(self, state):
            self.state = state

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def grab(self, _monitor):
            self.state["calls"] += 1
            if self.state["calls"] <= self.state["failures"]:
                raise ScreenShotError("Windows graphics function failed: BitBlt: 拒绝访问")
            return np.zeros((4, 4, 4), dtype=np.uint8)

    def _capture_with_failures(self, failures: int):
        """返回 (state, 上下文管理器列表)：前 failures 次抓屏抛屏保式的拒绝访问。"""
        state = {"calls": 0, "failures": failures}
        return state, [
            patch.object(image_match_module.mss, "mss",
                         lambda: self._FakeGrabber(state)),
            patch.object(image_match_module, "CAPTURE_RETRY_DELAY_S", 0),
        ]

    def test_capture_bgr_retries_a_transient_failure(self):
        state, patches = self._capture_with_failures(1)
        with patches[0], patches[1]:
            screen, origin = image_match_module.capture_bgr()
        self.assertEqual(state["calls"], 2)
        self.assertEqual(screen.shape, (4, 4, 3))
        self.assertEqual(origin, (0, 0))

    def test_capture_bgr_raises_after_all_retries(self):
        state, patches = self._capture_with_failures(99)
        with patches[0], patches[1]:
            with self.assertRaises(ScreenShotError):
                image_match_module.capture_bgr()
        self.assertEqual(state["calls"], image_match_module.CAPTURE_ATTEMPTS)

    def _activity_module(self, folder: str, **overrides) -> tuple[dict, dict]:
        template = Path(folder) / "精彩活动叉叉.png"
        template.write_bytes(b"activity")
        module = {
            "name": "精彩活动叉叉", "recognize": "image", "template": str(template),
            "region": [1, 2, 30, 40], "threshold": 0.85, "blocking": True,
            "interval_ms": 50, "after_action": "continue",
        }
        module.update(overrides)
        action = {
            "type": "image_match", "module_ref": True, "module_key": "module:activity",
            "template": str(template), "region_mode": "template",
        }
        return module, action

    def test_module_wait_treats_transient_capture_failure_as_not_found(self):
        # 屏保启动那一瞬间的截图失败不能打断执行：这一轮按未识别到处理，
        # 下一轮截图恢复后照常命中模块。
        match = {"x": 10, "y": 20, "width": 30, "height": 40,
                 "center_x": 25, "center_y": 40, "score": 0.9}
        player = MacroPlayer()
        player._wait = lambda _milliseconds: None
        traces: list[str] = []
        player._trace = lambda text, **_kwargs: traces.append(text)
        with tempfile.TemporaryDirectory() as folder:
            module, action = self._activity_module(folder)
            with package_patch('player', 'registered_module_object', return_value=module), \
                 package_patch('player', 'find_template', side_effect=[ScreenShotError('Windows graphics function failed: BitBlt: 拒绝访问'), match]) as find, \
                 package_patch('player', 'show_overlay'):
                result = player._execute_image(action, None)
        self.assertIsNone(result)
        self.assertEqual(find.call_count, 2)
        self.assertTrue(
            any("屏幕截图暂时失败" in text for text in traces), traces,
        )

    def test_module_wait_stops_after_persistent_capture_failure(self):
        player = MacroPlayer()
        player._wait = lambda _milliseconds: None
        player._trace = lambda *_args, **_kwargs: None
        with tempfile.TemporaryDirectory() as folder:
            module, action = self._activity_module(folder)
            with package_patch('player', 'registered_module_object', return_value=module), \
                 package_patch('player', 'find_template', side_effect=ScreenShotError('Windows graphics function failed: BitBlt: 拒绝访问')), \
                 package_patch('player', 'CAPTURE_FAILURE_GRACE_S', 0.0), \
                 package_patch('player', 'show_overlay'):
                with self.assertRaises(RuntimeError) as caught:
                    player._execute_image(action, None)
        self.assertIn("屏幕截图连续", str(caught.exception))

    def test_wait_target_absent_does_not_report_success_on_capture_failure(self):
        # “等待目标消失”的语义是「识别不到就算完成」；截图不可用时识别不到
        # 并不代表目标真的消失，此时必须继续等，不能直接判定成功。
        player = MacroPlayer()
        player._wait = lambda _milliseconds: None
        player._trace = lambda *_args, **_kwargs: None
        player._module_result_route = Mock(return_value=None)
        with tempfile.TemporaryDirectory() as folder:
            module, action = self._activity_module(folder, wait_text_absent=True)
            with package_patch('player', 'registered_module_object', return_value=module), \
                 package_patch('player', 'find_template', side_effect=ScreenShotError('Windows graphics function failed: BitBlt: 拒绝访问')), \
                 package_patch('player', 'CAPTURE_FAILURE_GRACE_S', 0.0), \
                 package_patch('player', 'show_overlay'):
                with self.assertRaises(RuntimeError):
                    player._execute_image(action, None)
        player._module_result_route.assert_not_called()


class DisplayAwakeTests(unittest.TestCase):
    """执行期间阻止屏保：向系统声明「显示器保持点亮」，收尾必须撤销。"""

    def test_keep_display_awake_requests_continuous_display(self):
        with patch.object(display_power_module.ctypes, "windll") as windll:
            windll.kernel32.SetThreadExecutionState.return_value = 1
            self.assertTrue(display_power_module.keep_display_awake())
        windll.kernel32.SetThreadExecutionState.assert_called_once_with(
            display_power_module.ES_CONTINUOUS
            | display_power_module.ES_SYSTEM_REQUIRED
            | display_power_module.ES_DISPLAY_REQUIRED)

    def test_allow_display_sleep_drops_the_continuous_request(self):
        with patch.object(display_power_module.ctypes, "windll") as windll:
            windll.kernel32.SetThreadExecutionState.return_value = 1
            self.assertTrue(display_power_module.allow_display_sleep())
        windll.kernel32.SetThreadExecutionState.assert_called_once_with(
            display_power_module.ES_CONTINUOUS)

    def test_api_failure_is_reported_without_raising(self):
        with patch.object(display_power_module.ctypes, "windll") as windll:
            windll.kernel32.SetThreadExecutionState.return_value = 0
            self.assertFalse(display_power_module.keep_display_awake())
        with patch.object(display_power_module.ctypes, "windll") as windll:
            windll.kernel32.SetThreadExecutionState.side_effect = OSError("boom")
            self.assertFalse(display_power_module.keep_display_awake())
            self.assertFalse(display_power_module.allow_display_sleep())

    @staticmethod
    def _script_worker_app():
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._ui = Mock()
        app._log = Mock()
        app._sound = Mock()
        app._finish_execution_visibility = Mock()
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.ocr_engine_ready = True
        app.player = Mock()
        app.player.stop_event = threading.Event()
        return app

    def test_script_execution_keeps_display_awake_and_releases_it(self):
        app = self._script_worker_app()
        with package_patch('app', 'keep_display_awake', return_value=True) as keep, \
                package_patch('app', 'allow_display_sleep') as release:
            app._run_script_worker(
                [{"type": "delay", "delay_ms": 1}], 1, None, None, False, None, False, 0,
                script_name="挂机",
            )
        keep.assert_called_once_with()
        release.assert_called_once_with()

    def test_script_execution_releases_display_when_it_fails(self):
        app = self._script_worker_app()
        app.player.play.side_effect = RuntimeError("截图失败")
        app._handle_worker_error = Mock()
        with package_patch('app', 'keep_display_awake', return_value=True), \
                package_patch('app', 'allow_display_sleep') as release:
            app._run_script_worker(
                [{"type": "delay", "delay_ms": 1}], 1, None, None, False, None, False, 0,
            )
        release.assert_called_once_with()

    def test_unavailable_display_awake_is_logged(self):
        app = self._script_worker_app()
        with package_patch('app', 'keep_display_awake', return_value=False), \
                package_patch('app', 'allow_display_sleep'):
            app._run_script_worker(
                [{"type": "delay", "delay_ms": 1}], 1, None, None, False, None, False, 0,
            )
        self.assertTrue(any(
            "屏保" in str(call.args[1])
            for call in app._ui.call_args_list if len(call.args) > 1
        ))

    def test_workflow_execution_keeps_display_awake_and_releases_it(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._set_status = Mock()
        app._set_execution_progress = Mock()
        app._append_mini_step = Mock()
        app._log = Mock()
        app._sound = Mock()
        app._handle_worker_error = Mock()
        app._finish_execution_visibility = Mock()
        app.current_workflow_step_index = None
        app._ui = lambda callback, *args: callback(*args)
        with package_patch('app', 'keep_display_awake', return_value=True) as keep, \
                package_patch('app', 'allow_display_sleep') as release:
            app._run_workflow_worker([], None, None, False)
        keep.assert_called_once_with()
        release.assert_called_once_with()

if __name__ == '__main__':
    unittest.main()
