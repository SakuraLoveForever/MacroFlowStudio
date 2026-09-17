"""全局检测守卫：脚本全局检测、工作流全局模块、OCR 需求判定。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_global_detect.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.common import *  # noqa: E402,F401,F403


class GuardTestHelpers:
    """守卫引擎测试共用夹具：guard 字典构造与 app 骨架。"""

    def _make_guard(self, template, **overrides):
        """Build a guard dict as used by the guard engine (no thread/stop fields)."""
        guard = {
            "key": "<test>",
            "module": None,
            "template": Path(template),
            "threshold": 0.85,
            "interval_ms": 100,
            "start_delay_ms": 0,
            "start_delay_since": 0.0,
            "start_delay_done": False,
            "fallback_module_key": "",
            "fallback_click": False,
            "fallback_click_count": 1,
            "fallback_click_interval_ms": 100,
            "fallback_present": False,
            "fallback_click_since": 0.0,
            "ignore_background": False,
            "recognize": "",
            "expected_text": "",
            "match_mode": "contains",
            "wait_text_absent": False,
            "target_absent_armed": False,
            "click_count": 1,
            "ocr_offset_up": 0, "ocr_offset_down": 0,
            "ocr_offset_left": 0, "ocr_offset_right": 0,
            "hold_ms": 0,
            "hold_enabled": True,
            "delay_ms": 0,
            "region_mode": "screen",
            "region": None,
            "click": None,
            "jump_row": 0,
            "jump_action_id": "",
            "module_ref": False,
            "module_key": "",
            "module_display_name": "测试模块",
            "after_action": "click_match",
            "button": "left",
            "second": None,
            "segment": [],
            "success_segment": [],
            "segment_ready": False,
            "timeout_enabled": False,
            "not_found_timeout_ms": 3000,
            "timeout_segment": [],
            "timeout_triggered": False,
            "not_found_since": time.perf_counter(),
            "trigger_kind": "success",
            "was_detected": False,
            "triggered": False,
            "awaiting_clear": False,
            "awaiting_clear_logged": False,
            "match_since": None,
            "match_data": None,
            "last_check_time": 0.0,
            "warned_missing_template": False,
            "warned_find_error": False,
            "warned_missing_module": False,
            "standalone_replay": None,
        }
        guard.update(overrides)
        return guard

    def _make_guard_app(self):
        """App skeleton with the guard-engine state the evaluator touches."""
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app.global_detect_trigger_count = 0
        app._evaluating_guards = False
        app.exiting = False
        app._bound_hwnd = Mock(return_value=None)
        app._restore_workflow_scan_foreground = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app._log = Mock()
        # 执行期细节（逐次识别结果/启用摘要等）走执行明细通道。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        app._append_mini_step = Mock()
        app.player = MacroPlayer()
        return app


class GlobalDetectTests(GuardTestHelpers, unittest.TestCase):
    def test_guard_without_action_warns_once_instead_of_silent_trigger(self):
        """「识别后的行为」是不点击时，命中要明确说一次，不能只有一条触发日志。"""
        app = self._make_guard_app()
        guard = self._make_guard(
            Path("images/部分/输入密码.png"),
            after_action="continue",
            module_display_name="输入密码",
            module_ref=True,
            match_data={"x": 1, "y": 2, "width": 3, "height": 4,
                        "center_x": 914, "center_y": 291},
        )

        hit = app._build_guard_hit(guard, resolve_hwnd=False)
        app._build_guard_hit(guard, resolve_hwnd=False)

        self.assertNotIn("click", hit)
        messages = [call.args[0] for call in app._log.call_args_list]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("输入密码", messages[0])
        self.assertIn("不点击", messages[0])

    def test_guard_custom_click_without_position_warns(self):
        """自定义点击位置没填时提示，而不是等到运行中抛异常。"""
        app = self._make_guard_app()
        guard = self._make_guard(
            Path("images/部分/输入密码.png"),
            after_action="click_custom",
            click=None,
            module_display_name="输入密码",
        )

        app._build_guard_hit(guard, resolve_hwnd=False)

        messages = [call.args[0] for call in app._log.call_args_list]
        self.assertEqual(len(messages), 1, messages)
        self.assertIn("自定义点击位置没设置", messages[0])

    def test_guard_module_custom_click_position_refreshes_live(self):
        """运行中改模块的「点击自定义位置」，下一轮检测就用新坐标。"""
        app = self._make_guard_app()
        guard = self._make_guard(
            Path("images/部分/输入密码.png"),
            after_action="click_custom",
            click=(1, 1),
            module_ref=True,
            module_key="module:pwd",
        )
        module = {
            "template": "images/部分/输入密码.png", "region": [914, 273, 91, 36],
            "after_action": "click_custom", "click_point": [1129, 291],
            "button": "left", "click_count": 2,
        }
        with patch_app("registered_module_object", return_value=module):
            app._refresh_guard_from_module(guard)
        self.assertEqual(guard["click"], (1129, 291))
        self.assertEqual(guard["click_count"], 2)

        module["click_point"] = [1200, 300]
        module["after_action"] = "click_match"
        with patch_app("registered_module_object", return_value=module):
            app._refresh_guard_from_module(guard)
        # 改回「点击识别区域」后按识别位置点，自定义坐标不再生效。
        hit = app._build_guard_hit(dict(guard, match_data={
            "x": 1, "y": 2, "width": 3, "height": 4, "center_x": 950, "center_y": 291,
        }), resolve_hwnd=False)
        self.assertEqual(hit["click"], (950, 291))

    def test_guard_module_delay_and_button_refresh_live(self):
        """运行中改模块的「延时」（命中后点击前的等待）与按键，下一轮就生效。"""
        app = self._make_guard_app()
        guard = self._make_guard(
            Path("images/部分/输入密码.png"),
            delay_ms=0,
            button="left",
            module_ref=True,
            module_key="module:pwd",
        )
        module = {
            "template": "images/部分/输入密码.png", "region": [914, 273, 91, 36],
            "after_action": "click_custom", "click_point": [1129, 291],
            "button": "right", "click_count": 1, "delay_ms": 1500,
        }
        with patch_app("registered_module_object", return_value=module):
            app._refresh_guard_from_module(guard)
        self.assertEqual(guard["delay_ms"], 1500)
        self.assertEqual(guard["button"], "right")
        hit = app._build_guard_hit(guard, resolve_hwnd=False)
        self.assertEqual(hit["delay_ms"], 1500)
        self.assertEqual(hit["button"], "right")

    def test_run_workflow_validation_early_return_shuts_down_detection_worker(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=False)
        app.worker = None
        app._workflow_only_steps = Mock(return_value=[{"kind": "script", "enabled": True}])
        app._global_module_steps = Mock(return_value=[])
        app._begin_detection_run = Mock()
        app._read_workflow_start_delay = Mock(return_value=None)
        worker = DetectionWorker(lambda _run_id, _config_version: None)
        app._detection_worker = worker
        app._ensure_detection_worker = Mock()
        app._notify = Mock()
        self.addCleanup(worker.close)

        app.run_workflow(test_mode=False)

        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(app._detection_worker)
        self.assertTrue(worker._closed)

    def test_run_current_script_startup_exception_shuts_down_detection_worker(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=False)
        app.worker = None
        app.script = Mock(actions=[{"type": "delay", "ms": 1}], settings={"trigger": {}})
        app.repeat_var = Mock()
        app.repeat_var.get.return_value = 1
        app._begin_detection_run = Mock()
        worker = DetectionWorker(lambda _run_id, _config_version: None)
        app._detection_worker = worker
        app._ensure_detection_worker = Mock()
        app._bound_hwnd = Mock(side_effect=RuntimeError("binding failed"))
        self.addCleanup(worker.close)

        with self.assertRaises(RuntimeError):
            app.run_current_script()

        self.assertFalse(worker.thread.is_alive())
        self.assertIsNone(app._detection_worker)
        self.assertTrue(worker._closed)

    def test_worker_evaluator_defers_overlay_and_fallback_click_to_player_thread(self):
        app = self._make_guard_app()
        app._pending_global_guard_hits = []
        app._guard_config_version = 3
        app._detection_run_id = 7
        app._ui = Mock()
        with tempfile.TemporaryDirectory() as folder:
            main_path = Path(folder) / "main.png"
            fallback_path = Path(folder) / "fallback.png"
            main_path.write_bytes(b"main")
            fallback_path.write_bytes(b"fallback")
            guard = self._make_guard(
                main_path,
                key="script:g1",
                fallback_module_key="module:fallback",
                fallback_click=True,
            )
            app.global_guards[guard["key"]] = guard
            fallback_obj = {"name": "备用", "template": str(fallback_path), "threshold": 0.8}
            fallback_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                              "center_x": 25, "center_y": 40}
            with patch_app("capture_bgr", return_value=(None, None)), \
                 patch_app("registered_module_object", return_value=fallback_obj), \
                 patch_app("find_template", side_effect=[None, fallback_match]), \
                 patch_app("show_overlay") as overlay, \
                 patch.object(app.player, "_click_module_point") as fallback_click:
                evaluation = app._evaluate_global_guards_sync()

        self.assertIsInstance(evaluation, DetectionEvaluation)
        self.assertIsNone(evaluation.hit)
        self.assertEqual([event["kind"] for event in evaluation.deferred_events],
                         ["restore_foreground", "overlay", "fallback_click"])
        overlay.assert_not_called()
        fallback_click.assert_not_called()
        with patch_app("show_overlay") as consumed_overlay, \
             patch.object(app, "_restore_workflow_scan_foreground") as restore_foreground, \
             patch.object(app.player, "_click_module_point") as consumed_click:
            app._consume_detection_events(evaluation.deferred_events)
        consumed_overlay.assert_called_once()
        restore_foreground.assert_called_once()
        consumed_click.assert_called_once()

    def test_worker_result_is_returned_only_for_current_run_and_config(self):
        class FakeWorker:
            def __init__(self, result):
                self.result = result
                self.submitted = []

            def submit(self, run_id, config_version):
                self.submitted.append((run_id, config_version))

            def poll(self):
                result, self.result = self.result, None
                return result

        app = MacroFlowApp.__new__(MacroFlowApp)
        app.exiting = False
        app._detection_run_id = 4
        app._guard_config_version = 6
        app._detection_request = None
        app.guards_lock = threading.Lock()
        app.global_guards = {}
        app._pending_global_guard_hits = []
        app.player = MacroPlayer()
        worker = FakeWorker(DetectionResult(3, 6, 1.0, 2.0, {"stale": True}))
        app._detection_worker = worker
        app._evaluate_global_guards_sync = Mock(return_value={"fresh": True})

        self.assertIsNone(app._evaluate_global_guards())
        self.assertEqual(worker.submitted, [(4, 6)])
        worker.result = DetectionResult(4, 6, 3.0, 4.0, {"fresh": True})
        self.assertEqual(app._evaluate_global_guards(), {"fresh": True})
        app._evaluate_global_guards_sync.assert_not_called()

    def test_pending_hits_from_previous_run_are_discarded(self):
        app = self._make_guard_app()
        app._detection_run_id = 2
        app._guard_config_version = 4
        app._pending_global_guard_hits = [{"guard_key": "stale"}]
        app._pending_global_guard_hits_version = (1, 3)

        evaluation = app._evaluate_global_guards_sync()

        self.assertIsNone(evaluation.hit)
        self.assertEqual(app._pending_global_guard_hits, [])

    def test_switch_module_fallback_clicks_once_then_main_match_finishes(self):
        with tempfile.TemporaryDirectory() as folder:
            main_path = Path(folder) / "main.png"
            fallback_path = Path(folder) / "fallback.png"
            main_path.write_bytes(b"main")
            fallback_path.write_bytes(b"fallback")
            main_obj = {
                "name": "主模块", "template": str(main_path), "region": [1, 2, 30, 40],
                "threshold": 0.85, "interval_ms": 50, "blocking": True,
                "fallback_module_key": "module:fallback", "fallback_click": True,
                "after_action": "continue",
            }
            fallback_obj = {
                "name": "备用模块", "template": str(fallback_path), "region": [5, 6, 20, 20],
                "threshold": 0.8, "button": "left", "click_count": 1,
            }
            main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                          "center_x": 25, "center_y": 40, "score": 0.9}
            fallback_match = {"x": 50, "y": 60, "width": 20, "height": 20,
                              "center_x": 60, "center_y": 70, "score": 0.9}
            player = MacroPlayer()
            player._wait = Mock()
            with patch_player("registered_module_object", side_effect=lambda key: {
                "module:main": main_obj, "module:fallback": fallback_obj,
            }.get(key)), patch_player("find_template", side_effect=[
                None, fallback_match, main_match,
            ]), patch_player("send_move_absolute") as move, patch_player("send_button") as button, \
                 patch_player("show_overlay"):
                player._execute_image({
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:main", "template": str(main_path),
                    "region_mode": "template",
                }, None)
            move.assert_called_once_with(60, 70)
            self.assertEqual(button.call_count, 2)

    def test_module_object_start_delay_waits_before_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            main_path = Path(folder) / "main.png"
            main_path.write_bytes(b"main")
            main_obj = {
                "name": "娱乐模式", "template": str(main_path), "region": [1, 2, 30, 40],
                "threshold": 0.85, "interval_ms": 50, "start_delay_ms": 2500,
                "after_action": "continue",
            }
            main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                          "center_x": 25, "center_y": 40, "score": 0.9}
            player = MacroPlayer()
            player._wait = Mock()
            logs: list[str] = []
            player.on_log = logs.append
            with patch_player("registered_module_object",
                       return_value=main_obj), \
                    patch_player("find_template",
                          return_value=main_match) as find, \
                    patch_player("show_overlay"):
                player._execute_image({
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:entertain", "template": str(main_path),
                    "region_mode": "template",
                }, None)
            # 进入模块前延时先等（识别前的第一件事），识别成功后动作前的
            # 「延时」是另一码事（此处 delay_ms=0，仍会走一次 0 ms 等待）。
            self.assertEqual(player._wait.call_args_list[0].args, (2500,))
            self.assertEqual(find.call_count, 1)
            self.assertTrue(
                any("模块 娱乐模式 进入前延时 2500 ms" in line for line in logs),
                logs,
            )

    def test_module_object_without_start_delay_waits_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            main_path = Path(folder) / "main.png"
            main_path.write_bytes(b"main")
            main_obj = {
                "name": "游戏大厅", "template": str(main_path), "region": [1, 2, 30, 40],
                "threshold": 0.85, "interval_ms": 50, "after_action": "continue",
            }
            main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                          "center_x": 25, "center_y": 40, "score": 0.9}
            player = MacroPlayer()
            player._wait = Mock()
            logs: list[str] = []
            player.on_log = logs.append
            with patch_player("registered_module_object",
                       return_value=main_obj), \
                    patch_player("find_template",
                          return_value=main_match), \
                    patch_player("show_overlay"):
                player._execute_image({
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:lobby", "template": str(main_path),
                    "region_mode": "template",
                }, None)
            self.assertFalse(any("进入前延时" in line for line in logs), logs)

    def test_switch_module_continuous_fallback_is_clicked_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            main_path = Path(folder) / "main.png"
            fallback_path = Path(folder) / "fallback.png"
            main_path.write_bytes(b"main")
            fallback_path.write_bytes(b"fallback")
            main_obj = {
                "name": "main", "template": str(main_path), "region": [1, 2, 30, 40],
                "threshold": 0.85, "interval_ms": 50, "blocking": True,
                "fallback_module_key": "module:fallback", "fallback_click": True,
                "after_action": "continue",
            }
            fallback_obj = {
                "name": "fallback", "template": str(fallback_path),
                "region": [5, 6, 20, 20], "threshold": 0.8,
                "button": "left", "click_count": 1,
            }
            main_match = {"x": 10, "y": 20, "width": 30, "height": 40,
                          "center_x": 25, "center_y": 40, "score": 0.9}
            fallback_match = {"x": 50, "y": 60, "width": 20, "height": 20,
                              "center_x": 60, "center_y": 70, "score": 0.9}
            player = MacroPlayer()
            player._wait = Mock()
            with patch_player("registered_module_object", side_effect=lambda key: {
                "module:main": main_obj, "module:fallback": fallback_obj,
            }.get(key)), patch_player("find_template", side_effect=[
                None, fallback_match, None, fallback_match, main_match,
            ]), patch_player("send_move_absolute") as move, patch_player("send_button") as button, \
                 patch_player("show_overlay"):
                player._execute_image({
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:main", "template": str(main_path),
                    "region_mode": "template",
                }, None)
            move.assert_called_once_with(60, 70)
            self.assertEqual(button.call_count, 2)

    def test_switch_module_fallback_can_click_and_exit_main_recognition(self):
        with tempfile.TemporaryDirectory() as folder:
            main_path = Path(folder) / "main.png"
            fallback_path = Path(folder) / "fallback.png"
            main_path.write_bytes(b"main")
            fallback_path.write_bytes(b"fallback")
            main_obj = {
                "name": "main", "template": str(main_path), "region": [1, 2, 30, 40],
                "threshold": 0.85, "interval_ms": 50, "blocking": True,
                "fallback_module_key": "module:fallback",
                "fallback_on_match": "click_exit",
                "after_action": "continue",
            }
            fallback_obj = {
                "name": "fallback", "template": str(fallback_path),
                "region": [5, 6, 20, 20], "threshold": 0.8,
                "button": "left", "click_count": 1,
            }
            fallback_match = {"x": 50, "y": 60, "width": 20, "height": 20,
                              "center_x": 60, "center_y": 70, "score": 0.9}
            player = MacroPlayer()
            player._wait = Mock()
            with patch_player("registered_module_object", side_effect=lambda key: {
                "module:main": main_obj, "module:fallback": fallback_obj,
            }.get(key)), patch_player("find_template", side_effect=[
                None, fallback_match,
            ]) as find, patch_player("send_move_absolute") as move, \
                 patch_player("send_button") as button, \
                 patch_player("show_overlay"):
                result = player._execute_image({
                    "type": "image_match", "module_ref": True,
                    "module_key": "module:main", "template": str(main_path),
                    "region_mode": "template",
                }, None)
        self.assertIsNone(result)
        self.assertEqual(find.call_count, 2)
        move.assert_called_once_with(60, 70)
        self.assertEqual(button.call_count, 2)

    def test_timeout_guard_returns_timeout_hit(self):
        # recognize=none 守卫不截图；超过 not_found_timeout_ms 后返回 kind=timeout 的
        # 处理段描述（超时段动作随 hit 返回，由播放器内联执行）。
        app = self._make_guard_app()
        app._log = Mock()
        app._append_mini_step = Mock()
        segment = [{"type": "delay", "ms": 1}]
        guard = self._make_guard(
            "", recognize="none", timeout_enabled=True, not_found_timeout_ms=0,
            timeout_segment=segment, not_found_since=time.perf_counter() - 1.0,
        )
        app.global_guards[guard["key"]] = guard
        with patch_app("find_template_in_image") as find, \
             patch_app("registered_module_object", return_value=None):
            hit = app._evaluate_global_guards()
        find.assert_not_called()
        self.assertIsNotNone(hit)
        self.assertEqual(hit["kind"], "timeout")
        self.assertEqual(hit["actions"], segment)
        self.assertTrue(guard["timeout_triggered"])

    def test_success_guard_carries_success_segment(self):
        # 引用模块勾选“再执行代码段”：命中后成功代码段必须随 hit 返回由
        # 播放器内联执行（曾因 segment_ready 死分支而永不执行）。
        app = self._make_guard_app()
        segment = [{"type": "delay", "ms": 1}]
        guard = self._make_guard(
            "images/g.png", recognize="image", success_segment=segment,
        )
        app.global_guards[guard["key"]] = guard
        hit = app._build_guard_hit(guard)
        self.assertEqual(hit["kind"], "success")
        self.assertEqual(hit["actions"], segment)

    def test_script_global_guard_hit_carries_owning_script_actions(self):
        app = self._make_guard_app()
        app._activate_global_detect_from_config = MacroFlowApp._activate_global_detect_from_config.__get__(
            app, MacroFlowApp,
        )
        actions = [
            {
                "type": "global_detect",
                "template": "images/g.png",
                "jump_enabled": True,
            },
            {"type": "notice"},
        ]
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            keys = app._enter_script_global_scope(actions)
        guard = app.global_guards[keys[0]]
        guard["jump_action_id"] = NEXT_WORKFLOW_STEP_TARGET_ID
        guard["jump_row"] = 3
        app._bound_hwnd = Mock(return_value=None)

        hit = app._build_guard_hit(guard)

        self.assertEqual(
            set(hit["scope_action_ids"]),
            {action["action_id"] for action in actions},
        )

    def test_guard_template_scale_uses_player_screens(self):
        # 守卫图片匹配必须带上播放器当前脚本的录制屏 → 当前屏缩放系数，
        # 否则截图尺寸不同时全局检测的匹配度同样下降。
        app = self._make_guard_app()
        app.player._source_screen = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        app.player._target_screen = {"left": 0, "top": 0, "width": 3840, "height": 2160}
        self.assertEqual(app._guard_template_scale(), 2.0)
        app.player._source_screen = None
        self.assertEqual(app._guard_template_scale(), 1.0)
        app.player = None
        self.assertEqual(app._guard_template_scale(), 1.0)

    def test_ensure_ocr_ready_loads_engine_once(self):
        # OCR 引擎首次导入不可中断且可能耗时数十秒：播放开始前确保就绪，
        # 避免第一次文字识别把“正在播放”卡在导入里。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app.ocr_engine_ready = False
        with patch_app("_get_engine", return_value=object()) as load:
            self.assertTrue(app._ensure_ocr_ready())
        load.assert_called_once()
        self.assertTrue(app.ocr_engine_ready)
        with patch_app("_get_engine") as load_again:
            self.assertTrue(app._ensure_ocr_ready())
        load_again.assert_not_called()

    def test_ocr_progress_updates_execution_text_and_progressbar(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.execution_progress_text = ""
        app.mini_mode = "execution"
        app.mini_count_var = Mock()
        app.mini_ocr_progress_var = Mock()
        app.mini_ocr_progressbar = Mock()

        app._on_ocr_progress("正在导入 PaddleOCR", 25)

        self.assertEqual(app.execution_progress_text, "OCR：正在导入 PaddleOCR · 25% · F12 停止")
        app.mini_ocr_progress_var.set.assert_called_once_with(25)
        app.mini_count_var.set.assert_called_once_with(
            "OCR：正在导入 PaddleOCR · 25% · F12 停止",
        )

    def test_ensure_ocr_ready_aborts_when_stop_requested(self):
        # 等待引擎加载期间按 F12：加载完成后必须中止执行，不能继续播放。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app.player.stop_event.set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app.ocr_engine_ready = False
        with patch_app("_get_engine", return_value=object()):
            self.assertFalse(app._ensure_ocr_ready())

    def test_wait_ocr_ready_interruptible_by_stop(self):
        # 预加载线程仍在导入（引擎未就绪）时按 F12：轮询等待必须立即
        # 返回 False，不能像抢初始化锁那样卡住不可中断。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app.player.stop_event.set()
        app.ocr_engine_ready = False
        app.ocr_warmup_thread = Mock()
        app.ocr_warmup_thread.is_alive.return_value = True
        self.assertFalse(app._wait_ocr_ready())

    def test_wait_ocr_ready_returns_when_prewarm_finished(self):
        # 预加载线程已结束（失败）或不存在：不再轮询，交由调用方同步重试。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app.ocr_engine_ready = False
        app.ocr_warmup_thread = Mock()
        app.ocr_warmup_thread.is_alive.return_value = False
        self.assertTrue(app._wait_ocr_ready())


class ScriptOcrNeedTests(GuardTestHelpers, unittest.TestCase):
    """_script_needs_ocr / _workflow_needs_ocr：按需等待 OCR 引擎的判断。"""

    def test_pure_input_script_does_not_need_ocr(self):
        # 纯键鼠 + 模板匹配脚本：不等待 OCR 引擎，立即开始执行。
        app = MacroFlowApp.__new__(MacroFlowApp)
        actions = [
            {"type": "key", "vk": 65, "down": True},
            {"type": "click", "x": 100, "y": 200},
            {"type": "image_match", "template": "images/a.png", "region_mode": "template"},
            {"type": "global_detect", "template": "images/b.png", "region_mode": "template"},
            {"type": "script_ref", "script": "scripts/other.json"},
        ]
        with patch_app("resolve_path", return_value=Path("scripts/other.json")) as resolve, \
                patch_app("load_script") as load, \
                patch_app("registered_module_object", return_value=None) as lookup:
            self.assertFalse(app._script_needs_ocr(actions))
        load.assert_not_called()

    def test_text_ocr_action_needs_ocr(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        self.assertTrue(app._script_needs_ocr(
            [{"type": "text_ocr", "region": [0, 0, 10, 10]}],
        ))

    def test_ocr_compare_action_needs_ocr(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        self.assertTrue(app._script_needs_ocr(
            [{"type": "ocr_compare", "region": [0, 0, 10, 10]}],
        ))

    def test_row_failure_segment_with_ocr_needs_ocr(self):
        # 行级失败代码段里用 OCR：播放前必须等 OCR 引擎，不能中途才导入。
        app = MacroFlowApp.__new__(MacroFlowApp)
        actions = [{
            "type": "image_match", "module_ref": True,
            "failure_segment_enabled": True,
            "failure_actions": [{"type": "text_ocr", "region": [0, 0, 10, 10]}],
        }]
        self.assertTrue(app._script_needs_ocr(actions))

    def test_multi_condition_click_needs_ocr_only_for_enabled_ocr_conditions(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        image_only = [{
            "type": "multi_condition_click",
            "conditions": [
                {"enabled": True, "type": "image"},
                {"enabled": False, "type": "ocr"},
                {"enabled": False, "type": "ocr", "ocr_mode": "number"},
            ],
        }]
        self.assertFalse(app._script_needs_ocr(image_only))
        mixed = [{
            "type": "multi_condition_click",
            "conditions": [
                {"enabled": True, "type": "image"},
                {"enabled": True, "type": "ocr"},
                {"enabled": False, "type": "ocr", "ocr_mode": "number"},
            ],
        }]
        self.assertTrue(app._script_needs_ocr(mixed))

    def test_text_guard_needs_ocr(self):
        # 文字识别全局守卫（recognize == "text"）必须等 OCR 引擎。
        app = MacroFlowApp.__new__(MacroFlowApp)
        self.assertTrue(app._script_needs_ocr(
            [{"type": "global_detect", "recognize": "text", "expected_text": "确认"}],
        ))

    def test_module_ref_text_object_needs_ocr(self):
        # 引用模块本身是文字识别模块：命中判断走 OCR。
        app = MacroFlowApp.__new__(MacroFlowApp)
        with patch_app("registered_module_object",
                   return_value={"recognize": "text", "expected_text": "体力不足"}) as lookup:
            self.assertTrue(app._script_needs_ocr(
                [{"type": "global_detect", "module_key": "module:123", "module_ref": True}],
            ))
        lookup.assert_called_once_with("module:123")

    def test_module_ref_code_segment_needs_ocr(self):
        # 模块本体是模板，但成功代码段里有文字识别动作。
        app = MacroFlowApp.__new__(MacroFlowApp)
        module = {
            "recognize": "template", "template": "images/a.png",
            "on_success_actions": [{"type": "text_ocr", "region": [0, 0, 10, 10]}],
            "on_timeout_actions": [],
        }
        with patch_app("registered_module_object", return_value=module):
            self.assertTrue(app._script_needs_ocr(
                [{"type": "global_detect", "module_key": "module:123"}],
            ))

    def test_fallback_text_module_needs_ocr(self):
        # 主模块是模板，备用识别模块是文字模块。
        app = MacroFlowApp.__new__(MacroFlowApp)
        with patch_app("registered_module_object",
                   side_effect=[None, {"recognize": "text"}]):
            self.assertTrue(app._script_needs_ocr(
                [{"type": "global_detect", "module_key": "module:main",
                  "fallback_module_key": "module:fb"}],
            ))

    def test_script_ref_follows_referenced_script(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        referenced = Mock()
        referenced.actions = [{"type": "text_ocr", "region": [0, 0, 10, 10]}]
        path = Mock()
        path.is_file.return_value = True
        path.resolve.return_value = "C:/scripts/ref.json"
        with patch_app("resolve_path", return_value=path) as resolve, \
                patch_app("load_script", return_value=referenced) as load:
            self.assertTrue(app._script_needs_ocr(
                [{"type": "script_ref", "script": "scripts/ref.json"}],
            ))
        load.assert_called_once()

    def test_script_ref_cycle_is_safe(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        a = Mock()
        a.actions = [{"type": "script_ref", "script": "b.json"}]
        b = Mock()
        b.actions = [{"type": "script_ref", "script": "a.json"}]

        def fake_resolve(value):
            path = Mock()
            path.is_file.return_value = True
            path.resolve.return_value = f"C:/{value}"
            return path

        def fake_load(path):
            return a if path.resolve() == "C:/a.json" else b

        with patch_app("resolve_path", side_effect=fake_resolve), \
                patch_app("load_script", side_effect=fake_load):
            self.assertFalse(app._script_needs_ocr(
                [{"type": "script_ref", "script": "a.json"}],
            ))

    def test_unresolvable_ref_is_conservative(self):
        # 引用脚本解析失败：宁可按需要 OCR 等待，不在播放中途撞上导入。
        app = MacroFlowApp.__new__(MacroFlowApp)
        path = Mock()
        path.is_file.return_value = True
        path.resolve.return_value = "C:/broken.json"
        with patch_app("resolve_path", return_value=path), \
                patch_app("load_script", side_effect=RuntimeError("解析失败")):
            self.assertTrue(app._script_needs_ocr(
                [{"type": "script_ref", "script": "scripts/broken.json"}],
            ))

    def test_workflow_script_steps(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        pure = Mock()
        pure.actions = [{"type": "click", "x": 1, "y": 2}]
        ocr_script = Mock()
        ocr_script.actions = [{"type": "text_ocr"}]

        def fake_resolve(value):
            path = Mock()
            path.is_file.return_value = True
            path.resolve.return_value = f"C:/{value}"
            return path

        def fake_load(path):
            return pure if path.resolve() == "C:/pure.json" else ocr_script

        with patch_app("resolve_path", side_effect=fake_resolve), \
                patch_app("load_script", side_effect=fake_load):
            self.assertFalse(app._workflow_needs_ocr(
                [{"kind": "script", "script": "pure.json"}], [],
            ))
            self.assertTrue(app._workflow_needs_ocr(
                [{"kind": "script", "script": "pure.json"},
                 {"kind": "script", "script": "ocr.json"}], [],
            ))

    def test_workflow_module_step(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        steps = [{"kind": "module", "action": {
            "module_key": "module:123", "type": "global_detect",
        }}]
        with patch_app("registered_module_object", return_value={"recognize": "text"}):
            self.assertTrue(app._workflow_needs_ocr(steps, []))

    def test_workflow_global_module_config(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        module = {"config": {
            "module_ref": True, "module_key": "module:g", "template": "images/g.png",
        }}
        with patch_app("registered_module_object", return_value={"recognize": "text"}):
            self.assertTrue(app._workflow_needs_ocr([], [module]))

    def test_activate_global_detect_from_config_configures_guard(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module = {"kind": "global_module", "script": "m.json", "step_id": "m1"}
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect",
                "template": "images/g.png",
                "threshold": "1.7",
                "interval_ms": "50",
                "hold_ms": "700000",
                "restart_delay_ms": "200",
                "region": [100, 50, 300, 200],
                "click_point": [640, 360],
            }, module)
        guard = app.global_guards["workflow:m1"]
        self.assertEqual(guard["threshold"], 1.0)
        self.assertEqual(guard["interval_ms"], 100)
        self.assertEqual(guard["hold_ms"], 600000)
        self.assertEqual(guard["delay_ms"], 200)
        self.assertEqual(guard["template"], Path("images/g.png"))
        self.assertEqual(guard["click"], (640, 360))
        self.assertEqual(guard["region"], (100, 50, 300, 200))
        self.assertEqual(guard["module"], module)
        # 注册只写数据，不启动线程。
        self.assertNotIn("thread", guard)
        self.assertNotIn("stop", guard)

    def test_script_global_module_start_delay_is_loaded_into_guard(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module_obj = {
            "enabled": True, "category": "script_global", "template": "images/g.png",
            "start_delay_ms": 125000,
        }
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:g", "action_id": "row-g",
            })
        self.assertEqual(app.global_guards["script:row-g"]["start_delay_ms"], 125000)

    def test_guard_check_interval_throttles(self):
        # 节流窗口内重复评估只截一次图：所有到点守卫共享同一帧，未到点的跳过。
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder) / "g.png"
            template.write_bytes(b"x")
            app = self._make_guard_app()
            guard = self._make_guard(template, interval_ms=100)
            app.global_guards[guard["key"]] = guard
            screen = np.zeros((60, 80, 3), dtype=np.uint8)
            with patch_app("capture_bgr", return_value=(screen, (-20, 0))) as capture, \
                 patch_app("find_template_in_image", return_value=None), \
                 patch_app("show_overlay"):
                self.assertIsNone(app._evaluate_global_guards())
                self.assertEqual(capture.call_count, 1)
                # 拉长间隔：下一次评估未到节流点，直接跳过，不截图。
                guard["interval_ms"] = 10000
                self.assertIsNone(app._evaluate_global_guards())
            self.assertEqual(capture.call_count, 1)

    def test_guard_text_module_trigger_uses_ocr_text_box(self):
        # 识别文字全局守卫：命中后把 OCR 返回的命中文字中心写入 match_data，
        # 处理段点击它而不是整个区域中心。
        app = self._make_guard_app()
        found = {
            "text": "体力不足", "x": 180, "y": 80, "width": 80, "height": 30,
            "center_x": 220, "center_y": 95, "score": 0.99,
        }
        guard = self._make_guard(
            "module-x.png", recognize="text", expected_text="体力不足",
            match_mode="contains", region=None, hold_ms=0,
        )
        app.global_guards[guard["key"]] = guard
        screen = np.zeros((400, 400, 3), dtype=np.uint8)
        with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
             patch_app("recognize_image_with_boxes", return_value=("体力不足", [found])) as recognize, \
             patch_app("show_overlay"):
            hit = app._evaluate_global_guards()
        recognize.assert_called_once()
        self.assertIsNotNone(hit)
        self.assertEqual(hit["kind"], "success")
        self.assertEqual(guard["match_data"]["center_x"], 220)
        self.assertEqual(guard["match_data"]["center_y"], 95)

    def test_guard_text_absent_repeats_while_target_present_and_rearms_after_disappear(self):
        app = self._make_guard_app()
        guard = self._make_guard(
            "module-x.png", recognize="text", expected_text="加载中",
            match_mode="contains", region=None, wait_text_absent=True,
            target_absent_armed=False, hold_ms=0,
        )
        app.global_guards[guard["key"]] = guard
        screen = np.zeros((400, 400, 3), dtype=np.uint8)
        # 未出现：不触发也不武装。
        with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
             patch_app("recognize_image_with_boxes",
                   return_value=("其他文字", [{"text": "其他文字"}])), \
             patch_app("show_overlay"):
            self.assertIsNone(app._evaluate_global_guards())
        self.assertFalse(guard["target_absent_armed"])
        # 出现：第一次触发成功动作，并进入持续重试状态。
        guard["last_check_time"] = 0.0
        found = {
            "text": "加载中", "x": 180, "y": 80, "width": 80, "height": 30,
            "center_x": 220, "center_y": 95,
        }
        with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
             patch_app("recognize_image_with_boxes", return_value=(
                 "加载中",
                 [found],
             )), \
             patch_app("show_overlay"):
            first_hit = app._evaluate_global_guards()
        self.assertIsNotNone(first_hit)
        self.assertTrue(guard["target_absent_armed"])
        # 目标仍在：下一轮继续触发，不能被 awaiting_clear 吞掉。
        guard["last_check_time"] = 0.0
        with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
             patch_app("recognize_image_with_boxes",
                   return_value=("加载中", [found])), \
             patch_app("show_overlay"):
            second_hit = app._evaluate_global_guards()
        self.assertIsNotNone(second_hit)
        self.assertEqual(second_hit["kind"], "success")
        # 目标消失：结束本轮重试，之后再次出现可以重新触发。
        guard["last_check_time"] = 0.0
        with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
             patch_app("recognize_image_with_boxes",
                   return_value=("已完成", [{"text": "已完成"}])), \
             patch_app("show_overlay"):
            self.assertIsNone(app._evaluate_global_guards())
        self.assertFalse(guard["target_absent_armed"])

    def test_guard_template_absent_repeats_while_target_present_and_rearms_after_disappear(self):
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder) / "target.png"
            template.write_bytes(b"x")
            app = self._make_guard_app()
            guard = self._make_guard(
                template, region=None, wait_text_absent=True,
                target_absent_armed=False, hold_ms=0,
            )
            app.global_guards[guard["key"]] = guard
            found = {
                "x": 180, "y": 80, "width": 80, "height": 30,
                "center_x": 220, "center_y": 95, "score": 0.99,
            }
            screen = np.zeros((400, 400, 3), dtype=np.uint8)
            with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
                 patch_app("find_template_in_image", return_value=found) as find, \
                 patch_app("show_overlay"):
                first_hit = app._evaluate_global_guards()
            self.assertIsNotNone(first_hit)
            self.assertTrue(guard["target_absent_armed"])
            self.assertEqual(find.call_count, 1)
            # 目标仍在：下一轮继续触发。
            guard["last_check_time"] = 0.0
            with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
                 patch_app("find_template_in_image", return_value=found), \
                 patch_app("show_overlay"):
                second_hit = app._evaluate_global_guards()
            self.assertIsNotNone(second_hit)
            self.assertEqual(guard["match_data"]["center_x"], 220)
            # 目标消失：结束本轮重试。
            guard["last_check_time"] = 0.0
            with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
                 patch_app("find_template_in_image", return_value=None), \
                 patch_app("show_overlay"):
                self.assertIsNone(app._evaluate_global_guards())
            self.assertFalse(guard["target_absent_armed"])

    def test_global_guard_hits_share_one_screenshot_and_are_queued_in_order(self):
        with tempfile.TemporaryDirectory() as folder:
            first_template = Path(folder) / "first.png"
            second_template = Path(folder) / "second.png"
            first_template.write_bytes(b"1")
            second_template.write_bytes(b"2")
            app = self._make_guard_app()
            first = self._make_guard(
                first_template, key="first", module_display_name="first", hold_ms=0,
            )
            second = self._make_guard(
                second_template, key="second", module_display_name="second", hold_ms=0,
            )
            app.global_guards[first["key"]] = first
            app.global_guards[second["key"]] = second
            match = {
                "x": 10, "y": 20, "width": 30, "height": 40,
                "center_x": 25, "center_y": 40, "score": 0.9,
            }
            screen = np.zeros((60, 80, 3), dtype=np.uint8)
            with patch_app("capture_bgr", return_value=(screen, (-20, 0))) as capture, \
                 patch_app("find_template_in_image", return_value=match), \
                 patch_app("show_overlay"):
                first_hit = app._evaluate_global_guards()
                second_hit = app._evaluate_global_guards()
            self.assertIn("模块[first]", first_hit["log_subject"])
            self.assertIn("模块[second]", second_hit["log_subject"])
            self.assertEqual(capture.call_count, 1)

    def test_guard_text_module_not_found_triggers_timeout_branch(self):
        app = self._make_guard_app()
        guard = self._make_guard(
            "module-x.png", recognize="text", expected_text="体力不足",
            match_mode="contains", region=None, timeout_enabled=True,
            not_found_timeout_ms=0, not_found_since=time.perf_counter() - 1.0,
        )
        app.global_guards[guard["key"]] = guard
        screen = np.zeros((400, 400, 3), dtype=np.uint8)
        with patch_app("capture_bgr", return_value=(screen, (0, 0))), \
             patch_app("recognize_image_with_boxes",
                   return_value=("其他文字", [{"text": "其他文字"}])), \
             patch_app("show_overlay"):
            hit = app._evaluate_global_guards()
        self.assertIsNotNone(hit)
        self.assertEqual(hit["kind"], "timeout")
        self.assertTrue(guard["timeout_triggered"])
        observation = "体力不足 OCR：识别到「其他文字」；期望「体力不足」· 未命中"
        # 逐次识别结果属于执行明细，不再写事件日志。
        app._trace_event.assert_any_call(observation)
        app._append_mini_step.assert_any_call(observation)

    def test_module_ref_activation_uses_resolved_object_and_preserves_rearm_lock(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = {"workflow:m1"}
        logs = []
        app._log = Mock(side_effect=logs.append)
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        module = {"kind": "global_module", "step_id": "m1"}
        resolved = Path("C:/Macro/images/点击游戏画面.png")
        obj = {
            "threshold": 0.85, "interval_ms": 250, "hold_ms": 100,
            "hold_enabled": True,
            "delay_ms": 0, "after_action": "click_match", "button": "left",
        }

        with patch_app("resolve_path", return_value=resolved), \
             patch_app("registered_module_object", return_value=obj) as lookup:
            app._activate_global_detect_from_config({
                "template": "images/点击游戏画面.png", "module_ref": True,
                "hold_ms": 1000,
            }, module)

        guard = app.global_guards["workflow:m1"]
        self.assertEqual(guard["hold_ms"], 100)
        # 重新武装锁跨执行保留：同 key 守卫注册后仍处于 awaiting_clear。
        self.assertTrue(guard["awaiting_clear"])
        self.assertTrue(any("持续超过 100 ms" in text for text in app._trace_logs))
        self.assertEqual(lookup.call_count, 2)
        self.assertTrue(all(
            item.args == ("images/点击游戏画面.png",) for item in lookup.call_args_list
        ))

    def test_disabled_module_reference_cannot_register_guard(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)

        with patch_app("resolve_path", return_value=Path("images/disabled.png")), \
             patch_app("registered_module_object", return_value={
                 "name": "已禁用模块", "enabled": False,
             }):
            app._activate_global_detect_from_config({
                "module_ref": True, "module_key": "module:disabled",
                "template": "images/disabled.png",
            }, {"kind": "global_module", "step_id": "disabled-row"})

        self.assertEqual(app.global_guards, {})
        self.assertTrue(any("已禁用" in call.args[0] for call in app._log.call_args_list))

    def test_activate_global_detect_from_config_carries_jump_row(self):
        # v1.68：普通脚本内嵌全局模块行的配置携带跳转行，启用日志随之变化。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        logs = []
        app._log = Mock(side_effect=lambda text: logs.append(text))
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect",
                "action_id": "global-a",
                "template": "images/g.png",
                "jump_row": 4,
                "jump_action_id": "target-a",
                "jump_enabled": True,
            })
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["jump_row"], 4)
        self.assertEqual(guard["jump_action_id"], "target-a")
        self.assertTrue(any("跳转到目标行执行，播放到末尾后结束" in text
                            for text in app._trace_logs))

    def test_activate_global_detect_jump_disabled_does_not_jump(self):
        # 未勾选“启用触发后跳转”：守卫保留目标配置（避免落入旧版“无跳转则
        # 点击识别处”的兼容分支），但命中打包不带跳转，触发后继续执行。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        logs = []
        app._log = Mock(side_effect=lambda text: logs.append(text))
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect",
                "action_id": "global-a",
                "template": "images/g.png",
                "jump_row": 4,
                "jump_action_id": "target-a",
                "jump_enabled": False,
            })
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["jump_row"], 4)
        self.assertEqual(guard["jump_action_id"], "target-a")
        self.assertTrue(guard["jump_disabled"])
        self.assertTrue(any("不跳转，继续执行脚本" in text for text in app._trace_logs))
        # 命中打包：不写跳转字段，也不触发旧版“点击识别处”兜底。
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 16, "center_y": 22}))
        self.assertNotIn("jump_row", hit)
        self.assertNotIn("jump_action_id", hit)
        self.assertNotIn("click", hit)

    def test_module_ref_click_match_clicks_detected_position(self):
        # 回归：引用模块「点击识别区域」（默认动作）命中后必须点击识别位置，
        # 与旧全局检测引擎一致——否则脚本内嵌全局模块行“只触发不点击”。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        module_obj = {
            "enabled": True, "category": "script_global", "name": "测试模块",
            "template": "images/g.png", "after_action": "click_match",
            "click_count": 2, "button": "right",
            "ocr_offset_up": 5, "ocr_offset_down": 0,
            "ocr_offset_left": 0, "ocr_offset_right": 0,
        }
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:g", "action_id": "row-g",
            })
        guard = app.global_guards["script:row-g"]
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertEqual(hit["click"], (100, 195))  # y 减去 ocr_offset_up 5
        self.assertEqual(hit["click_count"], 2)
        self.assertEqual(hit["button"], "right")

    def test_module_ref_click_custom_uses_module_point(self):
        # 引用模块「点击自定义位置」：命中后点击模块保存的自定义坐标。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        module_obj = {
            "enabled": True, "category": "script_global", "name": "测试模块",
            "template": "images/g.png", "after_action": "click_custom",
            "click_point": [640, 360], "click_count": 1,
        }
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:g", "action_id": "row-g",
            })
        guard = app.global_guards["script:row-g"]
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertEqual(hit["click"], (640, 360))

    def test_module_ref_continue_does_not_click(self):
        # 引用模块「成功后继续」：命中只触发不点击。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module_obj = {
            "enabled": True, "category": "script_global", "name": "测试模块",
            "template": "images/g.png", "after_action": "continue",
        }
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:g", "action_id": "row-g",
            })
        guard = app.global_guards["script:row-g"]
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertNotIn("click", hit)

    def test_module_ref_legacy_jump_target_stays_active(self):
        # 回归：引用模块行没有“启用触发后跳转”开关，旧脚本里配置的
        # jump_row/jump_action_id（缺失 jump_enabled 字段）必须继续生效，
        # 命中后跳转——不能因新复选框默认不勾选而静默失效。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module_obj = {
            "enabled": True, "category": "script_global", "name": "测试模块",
            "template": "images/g.png", "after_action": "click_match",
            "click_count": 2,
        }
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:g", "action_id": "row-g",
                "jump_row": 2, "jump_action_id": "target-a",
            })
        guard = app.global_guards["script:row-g"]
        self.assertFalse(guard["jump_disabled"])
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertEqual(hit["jump_row"], 2)
        self.assertEqual(hit["jump_action_id"], "target-a")
        # 跳转不替代模块的点击：两者都要。
        self.assertEqual(hit["click"], (100, 200))

    def test_module_ref_explicit_jump_disabled_keeps_click(self):
        # 显式写入 jump_enabled=False 的引用模块行：不跳转，但模块动作的
        # 点击仍然执行（点击由模块对象配置，与行级跳转开关无关）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module_obj = {
            "enabled": True, "category": "script_global", "name": "测试模块",
            "template": "images/g.png", "after_action": "click_match",
        }
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "module_ref": True,
                "module_key": "module:g", "action_id": "row-g",
                "jump_row": 2, "jump_action_id": "target-a",
                "jump_enabled": False,
            })
        guard = app.global_guards["script:row-g"]
        self.assertTrue(guard["jump_disabled"])
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertNotIn("jump_row", hit)
        self.assertNotIn("jump_action_id", hit)
        self.assertEqual(hit["click"], (100, 200))

    def test_direct_row_with_jump_enabled_clicks_then_jumps(self):
        # 回归：普通行（非引用）配置了跳转目标且“启用触发后跳转”勾选时，
        # 命中后必须先点击识别位置再跳转（旧引擎语义）——否则结算确定这类
        # 按钮永远不会被点击，结算界面不关闭，脚本重复执行时反复触发死循环。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "row-g",
                "template": "images/g.png",
                "jump_row": 14, "jump_action_id": "target-a",
                "jump_enabled": True,
            })
        guard = app.global_guards["script:row-g"]
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertEqual(hit["click"], (100, 200))
        self.assertEqual(hit["jump_row"], 14)
        self.assertEqual(hit["jump_action_id"], "target-a")

    def test_direct_row_without_jump_clicks_match(self):
        # 普通行没有跳转目标：命中后点击识别位置（“点击位置留空=点识别处”）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "row-g",
                "template": "images/g.png",
            })
        guard = app.global_guards["script:row-g"]
        app._bound_hwnd = Mock(return_value=None)
        hit = app._build_guard_hit(dict(guard, match_data={"center_x": 100, "center_y": 200}))
        self.assertEqual(hit["click"], (100, 200))

    def test_clear_global_guards_empties_registry(self):
        # _clear_global_guards 替代 _stop_all_global_detect_monitors：只清空守卫
        # 注册表（守卫无线程可停）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {"a": {"key": "a"}, "b": {"key": "b"}}
        app.guards_lock = threading.Lock()
        app._clear_global_guards()
        self.assertEqual(app.global_guards, {})

    def test_activate_global_detect_defaults_click_delay_to_1000ms(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "global-a",
                "template": "images/g.png",
            })
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["delay_ms"], 1000)

    def test_activate_global_detect_multiple_modules_each_get_own_guard(self):
        # 核心回归：每个全局模块启用后都有自己的守卫，互不覆盖。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module_a = {"kind": "global_module", "script": "a.json", "step_id": "a"}
        module_b = {"kind": "global_module", "script": "b.json", "step_id": "b"}
        with patch_app("resolve_path", side_effect=[Path("images/a.png"), Path("images/b.png")]):
            app._activate_global_detect_from_config(
                {"type": "global_detect", "template": "images/a.png"}, module_a,
            )
            app._activate_global_detect_from_config(
                {"type": "global_detect", "template": "images/b.png"}, module_b,
            )
        self.assertEqual(set(app.global_guards), {"workflow:a", "workflow:b"})
        self.assertEqual(app.global_guards["workflow:a"]["template"], Path("images/a.png"))
        self.assertEqual(app.global_guards["workflow:b"]["template"], Path("images/b.png"))
        # 同一个模块重新启用（工作流恢复）会替换旧守卫，而不是再开一个。
        with patch_app("resolve_path", return_value=Path("images/a.png")):
            app._activate_global_detect_from_config(
                {"type": "global_detect", "template": "images/a.png"}, module_a,
            )
        self.assertEqual(len(app.global_guards), 2)

    def test_script_global_actions_each_keep_an_independent_guard(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)

        with patch_app("resolve_path", side_effect=[Path("images/mainline.png"), Path("images/init.png")]):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "mainline",
                "template": "images/mainline.png",
            })
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "initialize",
                "template": "images/init.png",
            })

        self.assertEqual(
            set(app.global_guards),
            {"script:mainline", "script:initialize"},
        )

    def test_script_global_actions_have_independent_rearm_locks(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = {"script:mainline"}
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)

        with patch_app("resolve_path", side_effect=[Path("images/mainline.png"), Path("images/init.png")]):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "mainline",
                "template": "images/mainline.png",
            })
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "initialize",
                "template": "images/init.png",
            })

        self.assertTrue(app.global_guards["script:mainline"]["awaiting_clear"])
        self.assertFalse(app.global_guards["script:initialize"]["awaiting_clear"])

    def test_script_scope_enables_all_globals_when_starting_from_the_top(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._activate_global_detect_from_config = Mock()
        actions = [
            {"type": "global_detect", "action_id": "before", "template": "images/a.png"},
            {"type": "delay", "ms": 0, "action_id": "start"},
            {"type": "global_detect", "action_id": "after", "template": "images/b.png"},
        ]

        keys = app._enter_script_global_scope(actions)

        self.assertEqual(keys, ("script:before", "script:after"))
        self.assertEqual(
            [call.args[0]["action_id"] for call in app._activate_global_detect_from_config.call_args_list],
            ["before", "after"],
        )

    def test_script_scope_from_row_skips_globals_above_the_start_row(self):
        # 「▶ 从此开始执行」从第 3 行起跑：第 1、2 行的全局模块不会被执行到，
        # 就不能注册——否则选中的行白选，前面的全局模块照样在后台识别并点击
        # （用户看到的就是“还是从头执行了”）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app._activate_global_detect_from_config = Mock()
        actions = [
            {"type": "global_detect", "action_id": "before-a", "template": "images/a.png"},
            {"type": "global_detect", "action_id": "before-b", "template": "images/b.png"},
            {"type": "delay", "ms": 0, "action_id": "start"},
            {"type": "global_detect", "action_id": "after", "template": "images/c.png"},
        ]

        keys = app._enter_script_global_scope(actions, 2)

        self.assertEqual(keys, ("script:after",))
        self.assertEqual(
            [call.args[0]["action_id"] for call in app._activate_global_detect_from_config.call_args_list],
            ["after"],
        )
        self.assertTrue(any("第 1、2 行的全局模块不启用" in call.args[0] for call in app._log.call_args_list))

    def test_script_scope_keeps_only_globals_inside_the_segment(self):
        # 循环执行片段：片段之外（含片段末行之后）的全局模块行本次不会执行到，
        # 同样不能注册——否则用户只跑第 2~3 行，第 4 行的全局模块照样在后台
        # 识别并点击，看起来就像“把整份脚本都跑了”。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app._activate_global_detect_from_config = Mock()
        actions = [
            {"type": "global_detect", "action_id": "above", "template": "images/a.png"},
            {"type": "delay", "ms": 0, "action_id": "first"},
            {"type": "global_detect", "action_id": "inside", "template": "images/b.png"},
            {"type": "global_detect", "action_id": "below", "template": "images/c.png"},
        ]

        keys = app._enter_script_global_scope(actions, 1, 2)

        self.assertEqual(keys, ("script:inside",))
        self.assertEqual(
            [call.args[0]["action_id"] for call in app._activate_global_detect_from_config.call_args_list],
            ["inside"],
        )
        message = app._log.call_args.args[0]
        self.assertIn("循环执行片段 第 2-3 行", message)
        self.assertIn("第 1、4 行的全局模块不启用", message)

    def test_script_scope_restarts_module_start_delay_from_each_script_start(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module_obj = {
            "enabled": True,
            "category": "script_global",
            "template": "images/g.png",
            "start_delay_ms": 125000,
        }
        actions = [
            {
                "type": "global_detect",
                "module_ref": True,
                "module_key": "module:g",
                "action_id": "row-a",
            },
            {
                "type": "global_detect",
                "module_ref": True,
                "module_key": "module:g",
                "action_id": "row-b",
            },
        ]
        clock = iter((
            100.0, 101.0, 102.0, 103.0, 104.0,
            200.0, 201.0, 202.0, 203.0, 204.0,
        ))
        with patch_app("registered_module_object", return_value=module_obj), \
             patch_app("resolve_path", return_value=Path("images/g.png")), \
             patch("macroflow.ui.app.time.perf_counter", side_effect=clock):
            app._enter_script_global_scope(actions)
            first_starts = [
                app.global_guards[f"script:row-{suffix}"]["start_delay_since"]
                for suffix in ("a", "b")
            ]
            app._enter_script_global_scope(actions)
            second_starts = [
                app.global_guards[f"script:row-{suffix}"]["start_delay_since"]
                for suffix in ("a", "b")
            ]

        self.assertEqual(first_starts, [100.0, 100.0])
        self.assertEqual(second_starts, [200.0, 200.0])

    def test_scope_exit_removes_only_script_guards(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {
            "script:one": {"key": "script:one"},
            "workflow:one": {"key": "workflow:one"},
        }
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = {"script:one", "workflow:one"}
        app._guard_config_version = 8
        app._pending_global_guard_hits = [{"guard_key": "script:one"}]

        app._exit_script_global_scope(("script:one",))

        self.assertNotIn("script:one", app.global_guards)
        self.assertIn("workflow:one", app.global_guards)
        # 离开作用域同时丢弃该脚本守卫的重新武装锁。
        self.assertEqual(app.global_detect_rearm_locks, {"workflow:one"})
        self.assertEqual(app._guard_config_version, 9)
        self.assertEqual(app._pending_global_guard_hits, [])

    def test_activate_global_detect_region_mode_parsing(self):
        # 旧配置没有 region_mode：无区域 → 全屏；有区域 → 自定义区域。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config(
                {"type": "global_detect", "action_id": "global-a", "template": "images/g.png"},
            )
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["region_mode"], "screen")
        self.assertIsNone(guard["region"])
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect",
                "action_id": "global-a",
                "template": "images/g.png",
                "region": [100, 50, 300, 200],
            })
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["region_mode"], "custom")
        self.assertEqual(guard["region"], (100, 50, 300, 200))
        # 显式 window 模式：记录模式，region 无意义。
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect",
                "action_id": "global-a",
                "template": "images/g.png",
                "region_mode": "window",
            })
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["region_mode"], "window")

    def test_activate_global_detect_template_mode_reads_registered_region(self):
        # v1.78：region_mode="template" 时区域运行时从模板登记表读取。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        logs = []
        app._log = Mock(side_effect=lambda text: logs.append(text))
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        with patch_app("resolve_path", return_value=Path("images/g.png")), \
             patch_app("registered_template_region", return_value=[100, 50, 300, 200]):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "global-a", "template": "images/g.png",
                "region_mode": "template",
            })
        guard = app.global_guards["script:global-a"]
        self.assertEqual(guard["region_mode"], "template")
        self.assertEqual(guard["region"], (100, 50, 300, 200))
        self.assertTrue(any("区域 模板区域" in text for text in app._trace_logs))

    def test_activate_global_detect_template_without_region_uses_fullscreen(self):
        # 模板未登记 / 未设置区域：按全屏检测并在日志中告警。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        logs = []
        app._log = Mock(side_effect=lambda text: logs.append(text))
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        with patch_app("resolve_path", return_value=Path("images/g.png")), \
             patch_app("registered_template_region", return_value=None):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "global-a", "template": "images/g.png",
                "region_mode": "template",
            })
        guard = app.global_guards["script:global-a"]
        self.assertIsNone(guard["region"])
        self.assertTrue(any("模板未设置区域，按全屏检测" in text for text in app._trace_logs))

    def test_trigger_summary_template_mode_shows_template_region(self):
        # v1.78：引用模板的触发条件摘要显示"区域：模板"，不展开坐标。
        app = MacroFlowApp.__new__(MacroFlowApp)
        summary = app._trigger_summary({
            "template": "images/g.png", "region_mode": "template",
            "region": [], "hold_ms": 1500, "hold_enabled": True,
        })
        self.assertIn("g.png", summary)
        self.assertIn("区域：模板", summary)
        self.assertNotIn("0,0,0,0", summary)
        self.assertIn("持续超过 1500 ms", summary)

    def test_guard_window_mode_uses_bound_window_rect(self):
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "g.png"
            template_path.write_bytes(b"x")
            app = self._make_guard_app()
            guard = self._make_guard(template_path, region_mode="window")
            match = {"x": 1, "y": 2, "width": 30, "height": 40, "score": 0.9,
                     "center_x": 16, "center_y": 22}
            with patch_app("find_template", return_value=match) as find, \
                 patch_app("get_window_rect", return_value=(1, 2, 300, 200)), \
                 patch_app("show_overlay"):
                hit = app._evaluate_one_guard(guard, None, None, time.perf_counter())
            self.assertIsNotNone(hit)
            app._bound_hwnd.assert_called()
            # 每轮用目标窗口当前区域作为识别区域。
            find.assert_called_once()
            self.assertEqual(find.call_args.args[2], (1, 2, 300, 200))

    def test_activate_registers_guard_without_thread(self):
        # 注册只写守卫数据，不启动任何后台线程；守卫没有 thread/stop 字段。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.global_guards = {}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        # 启用摘要（区域/跳转/持续时长）现在走执行明细通道，不再写事件日志。
        app._trace_logs = []
        app._trace_event = Mock(side_effect=app._trace_logs.append)
        module = {"kind": "global_module", "script": "m.json", "step_id": "m1"}
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "template": "images/g.png",
            }, module)
        self.assertIn("workflow:m1", app.global_guards)
        workflow_guard = app.global_guards["workflow:m1"]
        self.assertNotIn("thread", workflow_guard)
        self.assertNotIn("stop", workflow_guard)
        with patch_app("resolve_path", return_value=Path("images/g.png")):
            app._activate_global_detect_from_config({
                "type": "global_detect", "action_id": "row-g", "template": "images/g.png",
            })
        self.assertIn("script:row-g", app.global_guards)
        script_guard = app.global_guards["script:row-g"]
        self.assertNotIn("thread", script_guard)
        self.assertNotIn("stop", script_guard)

    def test_evaluate_global_guards_hold_then_trigger(self):
        # 上升沿语义：首次评估只置 was_detected/match_since，达到 hold 时长后
        # 第二次评估返回 hit，且守卫进入 awaiting_clear。
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder) / "g.png"
            template.write_bytes(b"x")
            app = self._make_guard_app()
            guard = self._make_guard(template, hold_ms=1000)
            app.global_guards[guard["key"]] = guard
            match = {"x": 10, "y": 20, "width": 30, "height": 40,
                     "center_x": 25, "center_y": 40, "score": 0.9}
            screen = np.zeros((60, 80, 3), dtype=np.uint8)
            with patch_app("capture_bgr", return_value=(screen, (-20, 0))), \
                 patch_app("find_template_in_image", return_value=match), \
                 patch_app("show_overlay"):
                # 第一次评估：识别到但未到持续时长 → 无 hit。
                self.assertIsNone(app._evaluate_global_guards())
                self.assertTrue(guard["was_detected"])
                self.assertIsNotNone(guard["match_since"])
                self.assertFalse(guard["awaiting_clear"])
                # 拨快 match_since 到超过 hold，第二次评估触发。
                guard["match_since"] = time.perf_counter() - 2.0
                guard["last_check_time"] = 0.0
                hit = app._evaluate_global_guards()
            self.assertIsNotNone(hit)
            self.assertEqual(hit["kind"], "success")
            self.assertTrue(guard["triggered"])
            self.assertTrue(guard["awaiting_clear"])
            self.assertIn(guard["key"], app.global_detect_rearm_locks)

    def test_evaluate_global_guards_awaiting_clear_blocks_retrigger(self):
        with tempfile.TemporaryDirectory() as folder:
            template = Path(folder) / "g.png"
            template.write_bytes(b"x")
            app = self._make_guard_app()
            guard = self._make_guard(
                template, triggered=True, awaiting_clear=True, awaiting_clear_logged=False,
            )
            app.global_guards[guard["key"]] = guard
            app.global_detect_rearm_locks = {guard["key"]}
            match = {"x": 10, "y": 20, "width": 30, "height": 40,
                     "center_x": 25, "center_y": 40, "score": 0.9}
            screen = np.zeros((60, 80, 3), dtype=np.uint8)
            # 目标仍在：awaiting_clear 阻止再次触发。
            with patch_app("capture_bgr", return_value=(screen, (-20, 0))), \
                 patch_app("find_template_in_image", return_value=match), \
                 patch_app("show_overlay"):
                self.assertIsNone(app._evaluate_global_guards())
            self.assertTrue(guard["awaiting_clear"])
            self.assertIn(guard["key"], app.global_detect_rearm_locks)
            # 目标消失：解除 awaiting_clear 并重新武装，允许下次触发。
            guard["last_check_time"] = 0.0
            with patch_app("capture_bgr", return_value=(screen, (-20, 0))), \
                 patch_app("find_template_in_image", return_value=None), \
                 patch_app("show_overlay"):
                self.assertIsNone(app._evaluate_global_guards())
            self.assertFalse(guard["awaiting_clear"])
            self.assertNotIn(guard["key"], app.global_detect_rearm_locks)

    def test_evaluate_skips_when_player_stopped(self):
        app = self._make_guard_app()
        app.player.stop_event.set()
        guard = self._make_guard("images/g.png")
        app.global_guards[guard["key"]] = guard
        with patch_app("capture_bgr") as capture, \
             patch_app("find_template_in_image"), \
             patch_app("show_overlay"):
            self.assertIsNone(app._evaluate_global_guards())
        capture.assert_not_called()

    def test_guard_disabled_hold_delay_triggers_on_first_match(self):
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "g.png"
            template_path.write_bytes(b"x")
            app = self._make_guard_app()
            logs = []
            app._log = Mock(side_effect=logs.append)
            guard = self._make_guard(
                template_path, hold_ms=60000, hold_enabled=False,
            )
            app.global_guards[guard["key"]] = guard
            match = {
                "x": 10, "y": 20, "width": 30, "height": 40, "score": 0.9,
                "center_x": 25, "center_y": 40,
            }
            screen = np.zeros((60, 80, 3), dtype=np.uint8)
            with patch_app("capture_bgr", return_value=(screen, (-20, 0))), \
                 patch_app("find_template_in_image", return_value=match), \
                 patch_app("show_overlay"):
                hit = app._evaluate_global_guards()
            self.assertIsNotNone(hit)
            self.assertTrue(guard["awaiting_clear"])
            self.assertTrue(any("立即触发" in text for text in app._trace_logs))

    def test_guard_module_ref_reads_object_each_round(self):
        # 引用模块守卫：每轮评估实时重读对象（阈值/区域/持续时长），
        # 修改对象即时生效。
        with tempfile.TemporaryDirectory() as folder:
            template_path = Path(folder) / "g.png"
            template_path.write_bytes(b"x")
            app = self._make_guard_app()
            guard = self._make_guard(
                template_path, module_ref=True, module_key="module:g",
                interval_ms=500, threshold=0.85, hold_ms=1000,
            )
            app.global_guards[guard["key"]] = guard
            obj = {
                "category": "switch", "region": [1, 2, 30, 40],
                "threshold": 0.9, "interval_ms": 300, "blocking": False,
                "hold_ms": 2000, "hold_enabled": True,
                "delay_ms": 0, "after_action": "click_match",
            }
            match = {"x": 1, "y": 2, "width": 30, "height": 40, "score": 0.9,
                     "center_x": 16, "center_y": 22}
            screen = np.zeros((60, 80, 3), dtype=np.uint8)
            with patch_app("registered_module_object", return_value=obj) as obj_lookup, \
                 patch_app("capture_bgr", return_value=(screen, (-20, 0))), \
                 patch_app("find_template_in_image", return_value=match) as find, \
                 patch_app("show_overlay"):
                self.assertIsNone(app._evaluate_global_guards())
            obj_lookup.assert_called_once_with("module:g")
            # 阈值 / 区域来自对象，且 hold_ms 被对象值覆盖。
            self.assertEqual(find.call_args.args[2], 0.9)
            self.assertEqual(find.call_args.args[4], (1, 2, 30, 40))
            self.assertEqual(guard["hold_ms"], 2000)

    def test_guard_logs_missing_template_once(self):
        # 模板缺失是"加了没反应"的常见原因：日志提示一次，不每轮刷屏。
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.png"
            app = self._make_guard_app()
            logs = []
            app._log = Mock(side_effect=logs.append)
            guard = self._make_guard(missing)
            with patch_app("find_template_in_image") as find, \
                 patch_app("show_overlay"):
                detected, match = app._guard_image_detect(guard, None, None)
            self.assertFalse(detected)
            self.assertIsNone(match)
            find.assert_not_called()
            self.assertEqual(len(logs), 1)
            self.assertIn("模板图片不存在", logs[0])
            # 第二轮不再刷屏。
            with patch_app("find_template_in_image") as find2, \
                 patch_app("show_overlay"):
                detected, match = app._guard_image_detect(guard, None, None)
            self.assertEqual(len(logs), 1)
            find2.assert_not_called()

    def test_on_restart_workflow_request_standalone_returns_false(self):
        # 独立脚本（非工作流）：没有当前工作流，固定特殊动作被跳过。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.current_workflow_step_index = None
        app.workflow_restart_requested = False
        app.player = Mock()
        app.workflow_stop = threading.Event()
        app._ui = Mock()
        app._log = Mock()
        result = app._on_restart_workflow_request({"type": "restart_workflow"})
        self.assertFalse(result)
        self.assertFalse(app.workflow_restart_requested)
        app.player.stop.assert_not_called()
        app._ui.assert_called_once()

    def test_on_restart_workflow_request_workflow_restarts(self):
        # 工作流中：置标志、解析动作级跳转行、停播放并清空守卫，再轮询等 worker 死后重启。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.current_workflow_step_index = 0
        app.workflow_restart_requested = False
        app.workflow = Workflow(steps=[{"script": "a.json", "step_id": "row-a"}])
        app.player = Mock()
        app.workflow_stop = threading.Event()
        app.worker = None
        scheduled = []
        app._ui = lambda callback, *args: scheduled.append(callback)
        app._clear_global_guards = Mock()
        app._launch_workflow_restart = Mock()
        result = app._on_restart_workflow_request({
            "type": "restart_workflow",
            "restart_workflow_target_row": 3,
        })
        self.assertTrue(result)
        self.assertTrue(app.workflow_restart_requested)
        self.assertEqual(app.workflow_restart_target_row, 3)
        self.assertTrue(app.workflow_stop.is_set())
        app.player.stop.assert_called_once()
        app._clear_global_guards.assert_called_once()
        # 主线程轮询：worker 已死 → 立即重启工作流。
        app._poll_workflow_stop_for_restart_workflow()
        app._launch_workflow_restart.assert_called_once()

    def test_poll_workflow_stop_for_restart_waits_for_worker(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        app.workflow_restart_requested = True
        app._launch_workflow_restart = Mock()
        app.root = Mock()
        app._poll_workflow_stop_for_restart_workflow()
        app._launch_workflow_restart.assert_not_called()
        app.root.after.assert_called_once()

    def test_poll_workflow_stop_for_restart_cancelled_by_f12(self):
        # F12 紧急停止已把 workflow_restart_requested 清 False：残留的轮询
        # 不得再拉起工作流（否则会带着已清理的重启状态执行）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app.workflow_restart_requested = False
        app._launch_workflow_restart = Mock()
        app.root = Mock()
        app._poll_workflow_stop_for_restart_workflow()
        app._launch_workflow_restart.assert_not_called()
        app.root.after.assert_not_called()

    def test_launch_workflow_restart_preserves_current_repeats(self):
        # 重启工作流只能沿用当前剩余次数，不能恢复到本轮初始快照。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {"kind": "script", "script": "a.json", "repeats": 0, "unlimited": False},
            {"kind": "script", "script": "b.json", "repeats": 5, "unlimited": False},
        ])
        app.workflow_restart_requested = True
        app.workflow_repeats_snapshot = {0: (2, False), 1: (3, True)}
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._log = Mock()
        app._append_mini_step = Mock()
        app.run_workflow = Mock()
        app._launch_workflow_restart()
        steps = app._workflow_only_steps()
        self.assertEqual(steps[0]["repeats"], 0)
        self.assertEqual(steps[1]["repeats"], 5)
        self.assertFalse(steps[1]["unlimited"])
        self.assertFalse(app.workflow_restart_requested)
        app.run_workflow.assert_called_once_with(
            start_index=0, start_repeat=0, resume_action_index=0,
            preserve_global_rearm_locks=True, suppress_start_sound=True,
        )

    def test_launch_workflow_restart_uses_configured_row_object(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {"kind": "script", "script": "a.json", "step_id": "row-a"},
            {"kind": "script", "script": "b.json", "step_id": "row-b"},
        ])
        app.workflow_restart_requested = True
        app.workflow_restart_target_row = 2
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._log = Mock()
        app._append_mini_step = Mock()
        app.run_workflow = Mock()

        app._launch_workflow_restart()

        app.run_workflow.assert_called_once_with(
            start_index=1, start_repeat=0, resume_action_index=0,
            preserve_global_rearm_locks=True, suppress_start_sound=True,
        )

    def test_launch_workflow_restart_clamps_row_beyond_workflow_length(self):
        # 跳转行越界时收敛到工作流最后一行。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {"kind": "script", "script": "a.json"},
            {"kind": "script", "script": "b.json"},
        ])
        app.workflow_restart_requested = True
        app.workflow_restart_target_row = 99
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._log = Mock()
        app._append_mini_step = Mock()
        app.run_workflow = Mock()

        app._launch_workflow_restart()

        app.run_workflow.assert_called_once_with(
            start_index=1, start_repeat=0, resume_action_index=0,
            preserve_global_rearm_locks=True, suppress_start_sound=True,
        )
        # 重启完成后目标行复位，避免影响下一次触发。
        self.assertEqual(app.workflow_restart_target_row, 1)

    def test_workflow_module_enabled_follows_registry_state(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        step = {"kind": "module", "action": {"module_key": "module:test"}}
        with patch_app("registered_module_object", return_value={"enabled": False}):
            self.assertFalse(app._workflow_module_enabled(step))
        with patch_app("registered_module_object", return_value={"enabled": True}):
            self.assertTrue(app._workflow_module_enabled(step))

    def test_record_workflow_repeat_stores_index(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.current_workflow_repeat_index = 0
        app._set_execution_progress = Mock()
        app._ui = lambda callback, *args: callback(*args)
        with patch_app("workflow_execution_progress", return_value="p"):
            app._record_workflow_repeat(3, 5, 1, 2, "脚本")
        self.assertEqual(app.current_workflow_repeat_index, 2)
        app._set_execution_progress.assert_called_once_with("p")

    def test_ensure_workflow_step_ids_assigns_ids(self):
        steps = [
            {"script": "a.json"},
            {"kind": "global_module", "config": {"template": "x.png"}},
            {"script": "b.json"},
        ]
        self.assertTrue(ensure_workflow_step_ids(steps))
        self.assertTrue(steps[0].get("step_id"))
        self.assertTrue(steps[1].get("step_id"))
        first_ids = [step["step_id"] for step in steps]
        self.assertFalse(ensure_workflow_step_ids(steps))
        self.assertEqual([step["step_id"] for step in steps], first_ids)

    def test_append_global_module_always_goes_to_row_one(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"script": "a"}, {"script": "b"}])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ("1",)
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._append_global_module(config={"template": "x.png"})
        modules = app._global_module_steps()
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0]["config"]["template"], "x.png")
        self.assertEqual([step["script"] for step in app._workflow_only_steps()], ["a", "b"])

    def test_workflow_worker_activates_global_module(self):
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
        app._activate_global_detect_from_config = Mock()

        def fake_ui(callback, *args):
            callback(*args)

        app._ui = fake_ui
        module = {
            "kind": "global_module", "script": "", "enabled": True,
            "config": {"template": "x.png"},
        }
        app._run_workflow_worker(
            [], None, None, False, global_modules=[module],
        )
        app._activate_global_detect_from_config.assert_called_once_with(
            {"template": "x.png"}, module,
        )
        # 新设计：全局模块在开始时只开启检测，不执行脚本。
        app.player.play.assert_not_called()

    def test_pure_global_module_workflow_dwells_until_stopped(self):
        # 纯全局模块工作流（无脚本步骤）：守卫必须持续评估直到 F12，
        # 否则注册后立即“执行完成”、检测永不生效（v1.0 常驻监控行为）。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app.player.handle_guard_hit = Mock()
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
        app.global_guards = {"m1": {"key": "m1"}}
        app.guards_lock = threading.Lock()
        app.global_detect_rearm_locks = set()
        app.global_detect_trigger_count = 0
        app._evaluating_guards = False
        app.exiting = False
        app._restore_workflow_scan_foreground = Mock()
        calls = {"n": 0}

        def evaluate():
            calls["n"] += 1
            if calls["n"] >= 3:
                app.workflow_stop.set()
            return None

        app._evaluate_global_guards = Mock(side_effect=evaluate)

        app._run_workflow_worker([], None, None, False)

        self.assertGreaterEqual(calls["n"], 3)
        self.assertTrue(any(
            "持续运行全局检测" in call.args[0] for call in app._log.call_args_list
        ))
        self.assertFalse(any(
            "工作流执行完成" in call.args[0] for call in app._log.call_args_list
        ))

    def test_workflow_interrupt_keeps_focus_dispatcher_alive_for_restart(self):
        # 特殊模块「重新执行工作流」：worker 收尾时保留输入锁给重启流程。
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
        app._ui = lambda callback, *args: callback(*args)
        app.workflow_restart_requested = True

        app._run_workflow_worker([], None, None, False)

        app._leave_focus_mode.assert_not_called()
        app._finish_execution_visibility.assert_not_called()

    def test_workflow_resume_does_not_reenter_focus_mode(self):
        # 全局模块中断后的断点恢复（resume_action_index 非空）不再重复设置
        # 专注模式：输入法切换和系统输入锁只在工作流首次开始时执行一次。
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
        app._activate_global_detect_from_config = Mock()
        app._ui = lambda callback, *args: callback(*args)

        app._run_workflow_worker([], None, None, False, resume_action_index=2)

        app._enter_focus_mode.assert_not_called()
        self.assertTrue(any("沿用已开启的强制专注模式" in call.args[0]
                            for call in app._log.call_args_list))

    def test_workflow_fresh_start_enters_focus_mode_once(self):
        # 工作流首次开始（resume_action_index 为空）：执行一次专注模式设置。
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
        app._activate_global_detect_from_config = Mock()
        app._ui = lambda callback, *args: callback(*args)

        app._run_workflow_worker([], None, None, focus_enabled=True)

        app._enter_focus_mode.assert_called_once_with(None, True)

    def test_workflow_worker_skips_disabled_global_module(self):
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
        app._activate_global_detect_from_config = Mock()

        def fake_ui(callback, *args):
            callback(*args)

        app._ui = fake_ui
        app._run_workflow_worker(
            [], None, None, False,
            global_modules=[{
                "kind": "global_module", "script": "", "enabled": False,
                "config": {"template": "x.png"},
            }],
        )
        app._activate_global_detect_from_config.assert_not_called()
        app.player.play.assert_not_called()

    def test_workflow_worker_skips_registry_disabled_global_module_without_waiting(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = Mock()
        app.workflow_stop.is_set.return_value = False
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
        app._activate_global_detect_from_config = Mock()
        app._ui = lambda callback, *args: callback(*args)
        module = {
            "kind": "global_module", "script": "", "enabled": True,
            "before_ms": 99999,
            "config": {
                "module_ref": True, "module_key": "module:disabled",
                "template": "images/disabled.png",
            },
        }

        with patch_app("registered_module_object", return_value={
            "name": "禁用检测", "enabled": False,
        }):
            app._run_workflow_worker(
                [], None, None, False, global_modules=[module],
            )

        app.workflow_stop.wait.assert_not_called()
        app._activate_global_detect_from_config.assert_not_called()
        self.assertTrue(any(
            "模块管理中已禁用" in call.args[0] for call in app._log.call_args_list
        ))

    def test_workflow_worker_extracts_config_from_module_script(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "m.json"
            actions = [
                {"type": "global_detect", "template": "images/检测图.png",
                 "hold_ms": 1500, "region": [10, 20, 30, 40]},
                {"type": "delay", "delay_ms": 100},
            ]
            save_script(MacroScript(name="m", actions=actions), script_path)
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
            app._activate_global_detect_from_config = Mock()

            def fake_ui(callback, *args):
                callback(*args)

            app._ui = fake_ui
            module = {
                "kind": "global_module", "script": str(script_path),
                "enabled": True, "config": None,
            }
            with patch_app("resolve_path", return_value=script_path):
                app._run_workflow_worker(
                    [], None, None, False, global_modules=[module],
                )
            # 配置来自脚本 settings["trigger"]（迁移后不含 "type"）。
            app._activate_global_detect_from_config.assert_called_once_with(
                {"template": "images/检测图.png", "hold_ms": 1500, "region": [10, 20, 30, 40]},
                module,
            )
            app.player.play.assert_not_called()

    def test_tab_changed_refreshes_workflow_tree(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_tree = Mock()
        app.notebook = Mock()
        app.notebook.index.return_value = 1
        app.rebuild_workflow_tree = Mock()
        app._on_tab_changed()
        app.rebuild_workflow_tree.assert_called_once()

        app.rebuild_workflow_tree.reset_mock()
        app.notebook.index.return_value = 0
        app._on_tab_changed()
        app.rebuild_workflow_tree.assert_not_called()

    def test_global_module_label_reads_script_config(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "g.json"
            actions = [
                {
                    "type": "global_detect", "template": "images/检测图.png",
                    "hold_ms": 1500, "hold_enabled": True, "region": [10, 20, 30, 40],
                },
            ]
            save_script(MacroScript(name="g", actions=actions), script_path)
            app = MacroFlowApp.__new__(MacroFlowApp)
            with patch_app("resolve_path", return_value=script_path), \
                 patch_app("load_script", return_value=MacroScript(name="g", actions=actions)):
                label = app._global_module_label({
                    "kind": "global_module", "script": "scripts/g.json",
                })
            self.assertIn("⇄ 引用脚本 · g · 检测图.png", label)
            self.assertIn("检测图.png", label)
            self.assertIn("10,20,30,40", label)
            self.assertIn("1500", label)
            self.assertIn("触发后执行模块步骤，再继续工作流", label)

    def test_screen_point_picker_restores_main_without_owner(self):
        picker = ScreenPointPicker.__new__(ScreenPointPicker)
        picker.owner = None
        picker.main = Mock()
        picker.main_previous_state = "normal"
        picker.overlay = None
        picker.screenshot = None
        picker.canvas = None
        picker.tip_id = None
        picker.first_point = None
        picker.close()
        picker.main.deiconify.assert_called_once()

    def test_screen_point_picker_keeps_ancestor_windows_mapped_while_hidden(self):
        owner = Mock()
        manager = Mock()
        root = Mock()
        root.attributes.return_value = 0.85
        picker = ScreenPointPicker(owner, manager, Mock(), hidden_windows=[root])
        picker.start()
        root.attributes.assert_any_call("-alpha", 0.0)
        root.withdraw.assert_not_called()
        picker.close()
        root.attributes.assert_any_call("-alpha", 0.85)
        root.deiconify.assert_not_called()

    def test_screen_region_picker_drag_reports_region(self):
        picker = ScreenRegionPicker.__new__(ScreenRegionPicker)
        picker.canvas = Mock()
        picker.rectangle_id = None
        picker.tip_id = 1
        picker.overlay = None
        picker.owner = None
        picker.main = Mock()
        picker.main_previous_state = "normal"
        picker.hidden_windows = []
        picker.hidden_states = []
        picker.on_result = Mock()
        picker._drag_begin(Mock(x=10, y=20, x_root=100, y_root=200))
        self.assertEqual(picker.drag_start, (100, 200, 10, 20))
        picker.canvas.create_rectangle.assert_called_once()
        picker._drag_finish(Mock(x=50, y=80, x_root=300, y_root=400))
        picker.on_result.assert_called_once_with([100, 200, 200, 200])
        picker.main.deiconify.assert_called()

    def test_screen_region_picker_runs_result_before_restoring_windows(self):
        # v1.79："截图新建…"的回调（截屏）必须在窗口恢复之前执行，
        # 否则截图会把本程序自己的窗口截进去。
        picker = ScreenRegionPicker.__new__(ScreenRegionPicker)
        picker.overlay = None
        picker.canvas = None
        picker.rectangle_id = None
        picker.tip_id = None
        picker.drag_start = (100, 200, 0, 0)
        picker.owner = None
        picker.main = Mock()
        picker.main_previous_state = "normal"
        picker.hidden_windows = []
        picker.hidden_states = []
        calls = []
        picker.on_result = Mock(side_effect=lambda region: calls.append("result"))
        picker._restore_windows = Mock(side_effect=lambda: calls.append("restore"))
        picker._drag_finish(Mock(x=150, y=280, x_root=300, y_root=400))
        self.assertEqual(calls, ["result", "restore"])

    def test_screen_offset_picker_drag_reports_start_and_end_points(self):
        picker = ScreenOffsetPicker.__new__(ScreenOffsetPicker)
        picker.canvas = Mock()
        picker.rectangle_id = None
        picker.tip_id = 1
        picker.overlay = None
        picker.owner = None
        picker.main = Mock()
        picker.main_previous_state = "normal"
        picker.hidden_windows = []
        picker.hidden_states = []
        picker.on_result = Mock()
        picker._drag_begin(Mock(x=10, y=20, x_root=400, y_root=300))
        picker.canvas.create_line.assert_called_once()
        picker._drag_move(Mock(x=55, y=5, x_root=445, y_root=285))
        picker._drag_finish(Mock(x=55, y=5, x_root=445, y_root=285))
        picker.on_result.assert_called_once_with(400, 300, 445, 285)
        picker.main.deiconify.assert_called()

    def test_screen_region_picker_hides_whole_window_chain(self):
        # v1.79：框选 / 截图时隐藏从表单到主窗口的整条窗口链，避免遮挡屏幕。
        owner = Mock()
        manager = Mock()
        manager.state.return_value = "normal"
        root = Mock()
        root.state.return_value = "normal"
        picker = ScreenRegionPicker(owner, manager, Mock(), hidden_windows=[root])
        picker.start()
        root.withdraw.assert_called_once()
        manager.withdraw.assert_called_once()
        owner.withdraw.assert_called_once()
        manager.after.assert_called_once()

    def test_screen_region_picker_restores_hidden_chain_on_close(self):
        # 取消（Esc / 无效框选）时窗口链一起恢复，且不丢失最大化状态。
        owner = Mock()
        manager = Mock()
        manager.state.return_value = "zoomed"
        root = Mock()
        root.state.return_value = "normal"
        picker = ScreenRegionPicker(owner, manager, Mock(), hidden_windows=[root])
        picker.start()
        picker.close()
        root.deiconify.assert_called_once()
        manager.deiconify.assert_called()
        owner.deiconify.assert_called()
        manager.state.assert_called_with("zoomed")
        # root 恢复前是 normal：不重置其最大化状态（只记录过无参的读取调用）。
        self.assertEqual([call.args for call in root.state.call_args_list], [()])

    def test_global_detect_dialog_saves_action(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.restart_delay = Mock()
        dialog.restart_delay.get.return_value = "200"
        dialog.region = Mock()
        dialog.region.get.return_value = "100,50,300,200"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "custom"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "640,360"
        dialog.require_click = True
        dialog.destroy = Mock()
        dialog.save()
        result = dialog.result
        self.assertEqual(result["type"], "global_detect")
        self.assertEqual(result["template"], "images/g.png")
        self.assertAlmostEqual(result["threshold"], 0.9)
        self.assertEqual(result["region"], [100, 50, 300, 200])
        self.assertEqual(result["region_mode"], "custom")
        self.assertEqual(result["click_point"], [640, 360])
        self.assertEqual(result["restart_delay_ms"], 200)
        self.assertNotIn("jump_row", result)
        self.assertNotIn("jump_enabled", result)
        self.assertNotIn("jump_step_id", result)
        dialog.destroy.assert_called_once()

    def test_global_detect_module_selection_copies_bound_region(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.module_key = Mock()
        dialog.template = Mock()
        dialog.region_mode = Mock()
        dialog.region = Mock()
        dialog.template_combo = Mock()
        dialog.module_name = Mock()
        with patch_dialogs("choose_module_binding", return_value={
            "module_ref": True, "module_key": "module:first",
            "template": "images/shared.png", "region_mode": "template",
            "region": [11, 22, 333, 444],
        }) as choose:
            dialog.select_image_module()

        choose.assert_called_once_with(
            dialog, categories=("switch", "workflow_global", "script_global"),
        )
        dialog.module_key.set.assert_called_once_with("module:first")
        dialog.template.set.assert_called_once_with("images/shared.png")
        dialog.region_mode.set.assert_called_once_with("template")
        dialog.region.set.assert_called_once_with("11,22,333,444")

    def test_global_module_selection_limits_picker_to_global_categories(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.jump = True
        dialog.module_key = Mock()
        dialog.template = Mock()
        dialog.region_mode = Mock()
        dialog.region = Mock()
        dialog.template_combo = Mock()
        dialog.module_name = Mock()
        with patch_dialogs("choose_module_binding", return_value={
            "module_ref": True, "module_key": "module:global",
            "template": "images/global.png", "region_mode": "template",
            "region": [1, 2, 30, 40],
        }) as choose:
            dialog.select_image_module()

        self.assertEqual(
            choose.call_args.kwargs,
            {"categories": ("workflow_global", "script_global")},
        )

    def test_global_module_save_requires_selected_global_module(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.jump = True
        dialog.require_click = False
        dialog.module_key = Mock(**{"get.return_value": ""})
        dialog.template = Mock(**{"get.return_value": "images/legacy.png"})
        dialog.threshold = Mock(**{"get.return_value": "0.9"})
        dialog.interval = Mock(**{"get.return_value": "500"})
        dialog.hold = Mock(**{"get.return_value": "1000"})
        dialog.region = Mock(**{"get.return_value": ""})
        dialog.region_mode = Mock(**{"get.return_value": "screen"})
        dialog.jump_target_ids = {}
        dialog.jump_row = Mock(**{"get.return_value": "1"})
        dialog.click_point = Mock(**{"get.return_value": ""})
        dialog.restart_delay = Mock(**{"get.return_value": "0"})
        dialog.destroy = Mock()
        with patch_dialogs("show_floating_notice") as notice:
            dialog.save()

        notice.assert_called_once()
        self.assertIn("全局模块", notice.call_args.args[2])
        dialog.destroy.assert_not_called()

    def test_global_detect_saves_selected_module_identity_and_region(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.module_key = Mock(**{"get.return_value": "module:first"})
        dialog.template = Mock(**{"get.return_value": "images/stale.png"})
        dialog.threshold = Mock(**{"get.return_value": "0.9"})
        dialog.interval = Mock(**{"get.return_value": "500"})
        dialog.hold = Mock(**{"get.return_value": "1000"})
        dialog.region = Mock(**{"get.return_value": "1,2,3,4"})
        dialog.region_mode = Mock(**{"get.return_value": "custom"})
        dialog.click_point = Mock(**{"get.return_value": "640,360"})
        dialog.restart_delay = Mock(**{"get.return_value": "200"})
        dialog.require_click = True
        dialog.destroy = Mock()
        with patch_dialogs("registered_module_object", return_value={
            "category": "workflow_global", "template": "images/shared.png",
            "region": [11, 22, 333, 444],
        }):
            dialog.save()

        self.assertEqual(dialog.result["module_key"], "module:first")
        self.assertTrue(dialog.result["module_ref"])
        self.assertEqual(dialog.result["template"], "images/shared.png")
        self.assertEqual(dialog.result["region"], [11, 22, 333, 444])
        self.assertEqual(dialog.result["region_mode"], "template")

    def test_global_detect_dialog_saves_template_region_mode(self):
        # v1.78：模板已登记 → 动作只引用模板，区域运行时从登记表读取。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region_mode = Mock()
        dialog.require_click = True
        dialog.restart_delay = Mock()
        dialog.restart_delay.get.return_value = "200"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "640,360"
        dialog.destroy = Mock()
        with patch_dialogs("load_template_regions", return_value={
            "images/g.png": [100, 50, 300, 200],
        }):
            dialog.save()
        result = dialog.result
        self.assertEqual(result["template"], "images/g.png")
        self.assertEqual(result["region_mode"], "template")
        self.assertEqual(result["region"], [])
        dialog.destroy.assert_called_once()

    def test_global_detect_dialog_keeps_legacy_region_when_template_not_registered(self):
        # 编辑旧动作且模板不在登记表（旧模板被删除）：保留原有区域配置。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = "100,50,300,200"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "custom"
        dialog.require_click = True
        dialog.restart_delay = Mock()
        dialog.restart_delay.get.return_value = "200"
        dialog.click_point = Mock()
        dialog.click_point.get.return_value = "640,360"
        dialog.destroy = Mock()
        with patch_dialogs("load_template_regions", return_value={}):
            dialog.save()
        result = dialog.result
        self.assertEqual(result["region_mode"], "custom")
        self.assertEqual(result["region"], [100, 50, 300, 200])
        dialog.destroy.assert_called_once()

    def test_global_detect_dialog_trigger_mode_saves_without_click(self):
        # 触发条件模式（require_click=False）：不写点击位置与点击后延时。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.require_click = False
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = "100,50,300,200"
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "custom"
        dialog.destroy = Mock()
        dialog.save()
        result = dialog.result
        self.assertEqual(result["template"], "images/g.png")
        self.assertEqual(result["region"], [100, 50, 300, 200])
        self.assertIsNone(result["click_point"])
        self.assertEqual(result["restart_delay_ms"], 0)
        dialog.destroy.assert_called_once()

    def test_global_detect_dialog_jump_mode_saves_row_object(self):
        # 普通脚本内嵌全局模块行（jump=True）：保存跳转行号和动作标识，不写点击位置。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.require_click = False
        dialog.jump = True
        dialog.module_key = Mock(**{"get.return_value": "module:global"})
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = ""
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.jump_target_ids = {
            "第 3 行 · 键盘 · A": "target-a",
            "第 5 行 · 延时": "target-b",
        }
        dialog.jump_row_numbers = {"第 3 行 · 键盘 · A": 3, "第 5 行 · 延时": 5}
        dialog.jump_row = Mock()
        dialog.jump_row.get.return_value = "第 5 行 · 延时"
        dialog.destroy = Mock()
        with patch_dialogs("registered_module_object", return_value={
            "category": "workflow_global", "template": "images/g.png", "region": [],
        }):
            dialog.save()
        result = dialog.result
        self.assertTrue(result["module_ref"])
        self.assertEqual(result["module_key"], "module:global")
        self.assertEqual(result["template"], "images/g.png")
        self.assertEqual(result["region_mode"], "template")
        self.assertEqual(result["jump_row"], 5)
        self.assertEqual(result["jump_action_id"], "target-b")
        self.assertIsNone(result["click_point"])
        self.assertEqual(result["restart_delay_ms"], 0)

    def test_global_detect_dialog_saves_script_end_target(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.require_click = False
        dialog.jump = True
        dialog.module_key = Mock(**{"get.return_value": "module:global"})
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = ""
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.jump_target_ids = {
            "脚本结束（结束当前执行）": NEXT_WORKFLOW_STEP_TARGET_ID,
        }
        dialog.jump_row_numbers = {"脚本结束（结束当前执行）": 4}
        dialog.jump_row = Mock()
        dialog.jump_row.get.return_value = "脚本结束（结束当前执行）"
        dialog.destroy = Mock()

        with patch_dialogs("registered_module_object", return_value={
            "category": "script_global", "template": "images/g.png", "region": [],
        }):
            dialog.save()

        self.assertEqual(dialog.result["jump_row"], 4)
        self.assertEqual(
            dialog.result["jump_action_id"], NEXT_WORKFLOW_STEP_TARGET_ID,
        )

    def test_global_detect_dialog_jump_disabled_keeps_target_but_flags_off(self):
        # 取消勾选“启用触发后跳转”：保留目标配置（便于重新启用），
        # 但写入 jump_enabled=False 供运行时与显示判断。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.require_click = False
        dialog.jump = True
        dialog.module_key = Mock(**{"get.return_value": "module:global"})
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = ""
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.jump_target_ids = {
            "第 3 行 · 键盘 · A": "target-a",
            "第 5 行 · 延时": "target-b",
        }
        dialog.jump_row_numbers = {"第 3 行 · 键盘 · A": 3, "第 5 行 · 延时": 5}
        dialog.jump_row = Mock()
        dialog.jump_row.get.return_value = "第 5 行 · 延时"
        dialog.jump_enabled_var = FakeBooleanVar(False)
        dialog.destroy = Mock()
        with patch_dialogs("registered_module_object", return_value={
            "category": "workflow_global", "template": "images/g.png", "region": [],
        }):
            dialog.save()
        result = dialog.result
        self.assertFalse(result["jump_enabled"])
        self.assertEqual(result["jump_row"], 5)
        self.assertEqual(result["jump_action_id"], "target-b")

    def test_global_detect_dialog_jump_defaults_to_disabled(self):
        # 默认不勾选“启用触发后跳转”：未配置 jump_enabled 时按停用处理。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.require_click = False
        dialog.jump = True
        dialog.module_key = Mock(**{"get.return_value": "module:global"})
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = ""
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.jump_target_ids = {"第 5 行 · 延时": "target-b"}
        dialog.jump_row_numbers = {"第 5 行 · 延时": 5}
        dialog.jump_row = Mock()
        dialog.jump_row.get.return_value = "第 5 行 · 延时"
        dialog.destroy = Mock()
        with patch_dialogs("registered_module_object", return_value={
            "category": "workflow_global", "template": "images/g.png", "region": [],
        }):
            dialog.save()
        self.assertFalse(dialog.result["jump_enabled"])

    def test_global_detect_dialog_jump_mode_falls_back_to_spinbox(self):
        # 脚本没有可跳转的行时退回数字行号输入：只保存 jump_row。
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.require_click = False
        dialog.jump = True
        dialog.module_key = Mock(**{"get.return_value": "module:global"})
        dialog.template = Mock()
        dialog.template.get.return_value = "images/g.png"
        dialog.threshold = Mock()
        dialog.threshold.get.return_value = "0.9"
        dialog.interval = Mock()
        dialog.interval.get.return_value = "500"
        dialog.hold = Mock()
        dialog.hold.get.return_value = "1000"
        dialog.region = Mock()
        dialog.region.get.return_value = ""
        dialog.region_mode = Mock()
        dialog.region_mode.get.return_value = "screen"
        dialog.jump_target_ids = {}
        dialog.jump_row = Mock()
        dialog.jump_row.get.return_value = "5"
        dialog.destroy = Mock()
        with patch_dialogs("registered_module_object", return_value={
            "category": "script_global", "template": "images/g.png", "region": [],
        }):
            dialog.save()
        result = dialog.result
        self.assertEqual(result["jump_row"], 5)
        self.assertNotIn("jump_action_id", result)
        self.assertIsNone(result["click_point"])

    def test_select_jump_target_label_prefers_stable_id(self):
        options = [("第 1 行 · 延时", "a"), ("第 2 行 · 键盘 · B", "b")]
        # 行移动后旧行号失效，但动作标识仍能解析到当前行。
        self.assertEqual(
            select_jump_target_label("b", 1, options), "第 2 行 · 键盘 · B",
        )
        # 无标识时按保存的行号。
        self.assertEqual(select_jump_target_label("", 1, options), "第 1 行 · 延时")
        # 行号越界时选第一行兜底。
        self.assertEqual(select_jump_target_label("", 99, options), "第 1 行 · 延时")
        self.assertEqual(select_jump_target_label("", 0, []), "")

    def test_global_detect_dialog_requires_template(self):
        dialog = GlobalDetectDialog.__new__(GlobalDetectDialog)
        dialog.template = Mock()
        dialog.template.get.return_value = ""
        with patch_dialogs("show_floating_notice") as notice:
            dialog.save()
        notice.assert_called_once()

    def test_script_directories_dialog_saves_paths(self):
        dialog = ScriptDirectoriesDialog.__new__(ScriptDirectoriesDialog)
        dialog.level_dir = Mock()
        dialog.level_dir.get.return_value = "scripts/关卡"
        dialog.level_pack_dir = Mock()
        dialog.level_pack_dir.get.return_value = "scripts/关卡封装"
        dialog.switch_dir = Mock()
        dialog.switch_dir.get.return_value = "D:/switch"
        dialog.direction_dir = Mock()
        dialog.direction_dir.get.return_value = "D:/direction"
        dialog.destroy = Mock()
        dialog.save()
        self.assertEqual(dialog.result, {
            "level_dir": "scripts/关卡",
            "level_pack_dir": "scripts/关卡封装",
            "switch_dir": "D:/switch",
            "direction_dir": "D:/direction",
        })
        dialog.destroy.assert_called_once()

    def test_script_dir_helpers_use_configurable_paths(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.level_scripts_dir_var = Mock()
        app.level_scripts_dir_var.get.return_value = "my_level"
        app.level_pack_scripts_dir_var = Mock()
        app.level_pack_scripts_dir_var.get.return_value = "my_level_pack"
        app.switch_scripts_dir_var = Mock()
        app.switch_scripts_dir_var.get.return_value = "my_switch"
        self.assertEqual(app._level_scripts_dir(), BASE_DIR / "my_level")
        self.assertEqual(app._level_pack_scripts_dir(), BASE_DIR / "my_level_pack")
        self.assertEqual(app._switch_scripts_dir(), BASE_DIR / "my_switch")

    def test_script_category_key_and_dir_routing(self):
        from macroflow.ui.app import (
            SCRIPT_CATEGORY_VALUES, script_category_key, script_category_label,
        )
        self.assertEqual(SCRIPT_CATEGORY_VALUES, ("关卡", "关卡封装", "切换", "方向"))
        self.assertEqual(script_category_key("关卡"), "level")
        self.assertEqual(script_category_key("关卡封装"), "level_pack")
        self.assertEqual(script_category_key("切换"), "switch")
        self.assertEqual(script_category_key("方向"), "direction")
        self.assertEqual(script_category_key("工作流全局"), "level")
        self.assertEqual(script_category_key("脚本全局"), "level")
        self.assertEqual(script_category_label("direction"), "方向")
        self.assertEqual(script_category_label("level"), "关卡")
        self.assertEqual(script_category_label("已废弃的旧键"), "关卡")

        app = MacroFlowApp.__new__(MacroFlowApp)
        app.script_category_var = Mock()
        app._level_pack_scripts_dir = Mock(return_value=Path("lp"))
        app._switch_scripts_dir = Mock(return_value=Path("s"))
        app._direction_scripts_dir = Mock(return_value=Path("d"))
        app._level_scripts_dir = Mock(return_value=Path("l"))
        app.script_category_var.get.return_value = "切换"
        self.assertEqual(app._script_category_dir(), Path("s"))
        app.script_category_var.get.return_value = "关卡封装"
        self.assertEqual(app._script_category_dir(), Path("lp"))
        app.script_category_var.get.return_value = "方向"
        self.assertEqual(app._script_category_dir(), Path("d"))
        app.script_category_var.get.return_value = "关卡"
        self.assertEqual(app._script_category_dir(), Path("l"))

    def test_toggle_locked_spinbox_edit_and_save(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        spin = Mock()
        button = Mock()
        on_save = Mock()
        button.cget.return_value = "修改"
        app._toggle_locked_spinbox(spin, button, on_save)
        button.configure.assert_called_with(text="保存")
        spin.configure.assert_called_with(state="normal")
        spin.focus_set.assert_called_once()
        on_save.assert_not_called()

        button.cget.return_value = "保存"
        app._toggle_locked_spinbox(spin, button, on_save)
        button.configure.assert_called_with(text="修改")
        spin.configure.assert_called_with(state="disabled")
        on_save.assert_called_once()

if __name__ == '__main__':
    unittest.main()
