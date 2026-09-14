"""录制：整脚本录制、录制期间的界面行为、录制器与原始输入。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_recording.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.common import *  # noqa: E402,F401,F403


class ScriptRecordingSafetyTests(unittest.TestCase):
    def _recording_app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app.recorder = Mock()
        app.recorder.running = False
        app.recorder.limit_reached = False
        app.recorder.current_mode.return_value = "absolute"
        app.script = MacroScript(name="录制脚本", actions=[{"type": "delay", "ms": 1}])
        app.interval_var = Mock()
        app.interval_var.get.return_value = 20
        app.record_button = Mock()
        app._notify = Mock()
        app._log = Mock()
        app._clear_action_undo = Mock()
        app.rebuild_action_tree = Mock()
        app._set_status = Mock()
        app._sound = Mock()
        app._show_recording_mini = Mock()
        app._poll_recording_mode = Mock()
        app._bound_hwnd = Mock(return_value=100)
        app.saved_window_signature = None
        app._refresh_coordinate_scale_status = Mock()
        return app

    def test_start_recording_ignores_unsaved_changes(self):
        # 录制结束后编辑器是 dirty 的（刚录的动作还没保存）。此时再按 F8 必须
        # 直接开始新录制并清空动作列表——被 dirty 拦住就等于“录完一次后再也
        # 录不了，F8 没反应”。
        app = self._recording_app()
        app.dirty = True
        with patch_app("force_english_input", return_value=True), \
             patch_app("get_monitor_rect_for_window", return_value=None), \
             patch_app("get_primary_screen_rect",
                   return_value={"left": 0, "top": 0, "width": 1920, "height": 1080}):
            app.start_recording()
        app._notify.assert_not_called()
        app.recorder.start.assert_called_once()
        self.assertEqual(app.script.actions, [])
        self.assertEqual(app.recording_capture_mode, "absolute")
        app.record_button.configure.assert_called_once_with(
            text="停止录制    F8", bootstyle="danger",
        )

    def test_start_recording_still_blocks_while_script_runs(self):
        app = self._recording_app()
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        app.start_recording()
        app._notify.assert_called_once()
        app.recorder.start.assert_not_called()


class RecordingVisibilityTests(unittest.TestCase):
    """录制期间主窗口必须让开屏幕：不能挡住、也不能被录进鼠标轨迹。"""

    def _app(self, mini_enabled: bool):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.root.state.return_value = "normal"
        app.main_hidden_to_tray = False
        app.main_hidden_for_recording = False
        app.mini_window_enabled_var = Mock()
        app.mini_window_enabled_var.get.return_value = mini_enabled

        return app

    def test_recording_hides_main_window_with_tray(self):
        app = self._app(mini_enabled=False)
        app._hide_main_to_tray = Mock(return_value=True)
        app._show_operation_mini = Mock()

        app._show_recording_mini()

        # for_recording=True 由 _hide_main_to_tray 负责把「录制隐藏」标记立起来
        # （真实实现里会设置 main_hidden_for_recording，这里被 mock 掉）。
        app._hide_main_to_tray.assert_called_once_with(for_recording=True)
        app.root.withdraw.assert_not_called()
        # 没勾「显示悬浮小窗」就什么都不弹，但主窗口照样藏起来。
        app._show_operation_mini.assert_not_called()

    def test_recording_hides_main_window_without_tray_icon(self):
        # 托盘图标建不出来：仍然要让开屏幕，并且必须留下悬浮小窗（带停止按钮），
        # 否则用户除了 F8 没有任何停下来的入口。
        app = self._app(mini_enabled=False)
        app._hide_main_to_tray = Mock(return_value=False)
        app._show_operation_mini = Mock()

        app._show_recording_mini()

        app.root.withdraw.assert_called_once()
        self.assertTrue(app.main_hidden_for_recording)
        app._show_operation_mini.assert_called_once_with("recording")

    def test_recording_shows_mini_when_checkbox_enabled(self):
        app = self._app(mini_enabled=True)
        app._hide_main_to_tray = Mock(return_value=True)
        app._show_operation_mini = Mock()

        app._show_recording_mini()

        app._show_operation_mini.assert_called_once_with("recording")

    def test_restore_keeps_main_hidden_while_recording(self):
        # 录制中调用恢复（例如托盘菜单）：主窗口不能跳回来抢前台。
        app = self._app(mini_enabled=False)
        app.recorder = Mock(running=True)
        app._show_operation_mini = Mock()

        app._restore_main_window()

        app.root.deiconify.assert_not_called()
        app._show_operation_mini.assert_not_called()

    def test_restore_brings_window_back_after_recording(self):
        app = self._app(mini_enabled=False)
        app.recorder = Mock(running=False)
        app.main_hidden_for_recording = True
        app.main_hidden_to_tray = True
        app.tray_icon = None

        app._restore_main_window()

        app.root.deiconify.assert_called_once()
        self.assertFalse(app.main_hidden_for_recording)
        self.assertFalse(app.main_hidden_to_tray)

    def test_setting_toggle_never_restores_main_window_during_recording(self):
        app = self._app(mini_enabled=False)
        app.recorder = Mock(running=True)
        app._persist_sidebar_settings = Mock()
        app._hide_recording_mini = Mock()
        app._restore_main_window = Mock()

        app._settings_changed()

        app._hide_recording_mini.assert_called_once()
        app._restore_main_window.assert_not_called()

    def test_insert_recording_keeps_existing_actions(self):
        # 「录进一条动作」的录制**不能清空**编辑器里已有的动作：
        # 它只是插一条新动作，之前录/加的每一行都必须原样留着。
        app = self._app(mini_enabled=False)
        app.recorder = Mock()
        app.worker = None
        app._pending_recorded_input = lambda _steps: None
        existing = [{"type": "delay", "ms": 5, "action_id": "keep"}]
        app.script = Mock(actions=list(existing))
        app._clear_action_undo = Mock()
        app.rebuild_action_tree = Mock()
        app.record_button = Mock()
        app._set_status = Mock()
        app._log = Mock()
        app._sound = Mock()
        app._show_recording_mini = Mock()
        app._append_mini_step = Mock()
        app._poll_recording_mode = Mock()
        app._bound_hwnd = Mock(return_value=None)
        app.recording_screen = None
        app.recording_capture_mode = ""
        app.saved_window_signature = None
        app.interval_var = Mock()
        app.interval_var.get.return_value = "100"

        with patch_app("force_english_input", return_value=True):
            app.start_recording(from_ui=True)

        self.assertEqual(app.script.actions, existing)
        app._clear_action_undo.assert_not_called()

    def test_full_script_recording_still_clears_actions(self):
        # 侧栏/F8 的「重新录一遍」语义保持不变：开始录制就清空旧动作。
        app = self._app(mini_enabled=False)
        app.recorder = Mock()
        app.worker = None
        app.script = Mock(actions=[{"type": "delay", "ms": 5, "action_id": "old"}])
        app._clear_action_undo = Mock()
        app.rebuild_action_tree = Mock()
        app.record_button = Mock()
        app._set_status = Mock()
        app._log = Mock()
        app._sound = Mock()
        app._show_recording_mini = Mock()
        app._append_mini_step = Mock()
        app._poll_recording_mode = Mock()
        app._bound_hwnd = Mock(return_value=None)
        app.recording_screen = None
        app.recording_capture_mode = ""
        app.saved_window_signature = None
        app.interval_var = Mock()
        app.interval_var.get.return_value = "100"

        with patch_app("force_english_input", return_value=True):
            app.start_recording(from_ui=True)

        self.assertEqual(app.script.actions, [])
        app._clear_action_undo.assert_called_once()

    def test_toolbar_record_starts_the_shared_recording_flow(self):
        # 录制过程与侧栏/F8 完全同一条路径（所以悬浮小窗照旧），差别只在结果处理。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=False)
        app.worker = None
        app.toggle_record = Mock()
        app._notify = Mock()

        app._toggle_record_from_toolbar()

        app.toggle_record.assert_called_once_with(from_ui=True)
        self.assertTrue(callable(app._pending_recorded_input))
        app._notify.assert_not_called()

    def test_start_action_recording_refuses_while_recording_or_running(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.recorder = Mock(running=True)
        app.worker = None
        app.toggle_record = Mock()
        app._notify = Mock()

        self.assertFalse(app._start_action_recording(lambda _steps: None, "测试"))
        app.toggle_record.assert_not_called()

        app.recorder = Mock(running=False)
        app.worker = Mock()
        app.worker.is_alive.return_value = True
        self.assertFalse(app._start_action_recording(lambda _steps: None, "测试"))
        app.toggle_record.assert_not_called()


class RecordingDisplayTests(unittest.TestCase):
    def test_row_list_diagnostic_completion_restores_windows_before_showing_results(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        hidden_states = [(Mock(), "normal")]
        app._restore_macroflow_windows_after_diagnostic = Mock()

        with patch_app("RowListDiagnosticResultDialog") as result_dialog:
            app._finish_row_list_diagnostic(
                ["第1行结果：左侧命中；右侧未命中"],
                None,
                hidden_states,
            )

        app._restore_macroflow_windows_after_diagnostic.assert_called_once_with(hidden_states)
        result_dialog.assert_called_once_with(
            app.root,
            ["第1行结果：左侧命中；右侧未命中"],
            None,
        )
        result_dialog.return_value.show.assert_called_once()

    def test_test_row_list_condition_click_collects_diagnostic_results_for_window(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app._bound_hwnd = Mock(return_value=123)
        app._hide_macroflow_windows_for_diagnostic = Mock(return_value=[])
        app._log = Mock()
        app._ui = lambda callback, *args: callback(*args)
        app._finish_row_list_diagnostic = Mock()
        app.player = Mock()
        action = {"type": "row_list_condition_click"}

        with patch("macroflow.ui.app.threading.Thread") as thread_class:
            app.test_row_list_condition_click(action)
            target = thread_class.call_args.kwargs["target"]
            target()

        app.player._diagnose_row_list_condition_click.assert_called_once()
        diagnose_kwargs = app.player._diagnose_row_list_condition_click.call_args.kwargs
        self.assertIn("result_sink", diagnose_kwargs)
        app._finish_row_list_diagnostic.assert_called_once()


    def test_floating_notice_positions_cover_all_six_choices(self):
        self.assertEqual(floating_notice_xy("左上", 1920, 1080), (18, 18))
        self.assertEqual(floating_notice_xy("顶部居中", 1920, 1080), (780, 18))
        self.assertEqual(floating_notice_xy("右上", 1920, 1080), (1542, 18))
        self.assertEqual(floating_notice_xy("左下", 1920, 1080), (18, 994))
        self.assertEqual(floating_notice_xy("底部居中", 1920, 1080), (780, 994))
        self.assertEqual(floating_notice_xy("右下", 1920, 1080), (1542, 994))

    def test_successful_execution_always_restores_main_from_tray(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.execution_progress_text = "running"
        app.main_hidden_for_execution = True
        app.main_hidden_to_tray = False
        app.root = Mock()
        app._hide_execution_mini = Mock()
        app._restore_main_window = Mock()
        app._finish_execution_visibility()
        app._restore_main_window.assert_called_once()
        self.assertFalse(app.main_hidden_for_execution)

    def test_tray_leftover_after_execution_logs_how_to_quit(self):
        # 执行结束后窗口按设计留在托盘，很容易被当成"软件已经关了"——
        # 必须留下一条日志说明软件仍在运行、快捷键仍生效、怎么彻底退出。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.execution_progress_text = "running"
        app.main_hidden_for_execution = True
        app.main_hidden_to_tray = True
        app.root = Mock()
        app._hide_execution_mini = Mock()
        app._restore_main_window = Mock()
        app._tray_visible = Mock(return_value=True)
        logs: list[str] = []
        app._log = Mock(side_effect=logs.append)

        app._finish_execution_visibility()

        app._restore_main_window.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn("系统托盘", logs[0])
        self.assertIn("退出", logs[0])

    def test_worker_error_logs_mini_cleanup_before_hiding_it(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        events = []
        app._set_status = Mock()
        app._append_mini_step = Mock()
        app._sound = Mock()
        app._notify = Mock()
        app._clear_global_guards = Mock()
        app._log = Mock(side_effect=lambda text: events.append(("log", text)))
        app._finish_execution_visibility = Mock(
            side_effect=lambda: events.append(("finish", "")),
        )

        app._handle_worker_error("工作流执行失败", RuntimeError("绑定窗口已关闭"))

        self.assertEqual(events, [
            ("log", "工作流执行失败：绑定窗口已关闭"),
            ("log", "执行异常收尾：即将关闭执行小窗并恢复主界面。"),
            ("finish", ""),
        ])
        app._clear_global_guards.assert_called_once()

    def test_repeated_notice_reuses_single_window_and_restarts_timer(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        window = Mock()
        window.winfo_exists.return_value = True
        window.winfo_screenwidth.return_value = 1920
        window.winfo_screenheight.return_value = 1080
        window.after.return_value = "new-timer"
        app.execution_notice_window = window
        app.execution_notice_label = Mock()
        app.execution_notice_after_id = "old-timer"

        app._show_execution_notice("新的提醒内容", 4500)

        app.execution_notice_label.configure.assert_called_once_with(text="新的提醒内容")
        window.after_cancel.assert_called_once_with("old-timer")
        window.after.assert_called_once()
        self.assertEqual(app.execution_notice_after_id, "new-timer")

    def test_notice_action_has_clear_summary(self):
        notice = action_summary({
            "type": "notice", "text": "体力即将用完", "duration_ms": 3000, "delay_ms": 0,
        })
        self.assertIn("浮动提醒", notice[0])
        self.assertIn("3000 ms", notice[1])

    def test_text_summary_keeps_full_parameter_content(self):
        long_text = "这是必须完整显示的文本参数：" + "竞技结算确认" * 12

        _kind, detail, _delay = action_summary({
            "type": "text", "text": long_text,
        })

        self.assertIn(long_text, detail)

    def test_notice_summary_keeps_full_parameter_content(self):
        long_text = "这是必须完整显示的提醒参数：" + "请确认退出" * 12

        _kind, detail, _delay = action_summary({
            "type": "notice", "text": long_text,
        })

        self.assertIn(long_text, detail)

    def test_block_summary_explains_that_only_a_jump_can_release_it(self):
        kind, detail, _delay = action_summary({"type": "block"})

        self.assertIn("阻塞", kind)
        self.assertEqual(detail, "等待其他跳转离开")

    def test_multi_condition_summary_keeps_full_ocr_parameter(self):
        long_text = "这是必须完整显示的 OCR 参数：" + "竞技结算确认" * 8

        _kind, detail, _delay = action_summary({
            "type": "multi_condition_click",
            "conditions": [{
                "enabled": True,
                "type": "ocr",
                "expected_text": long_text,
                "region": [1, 2, 30, 40],
            }],
        })

        self.assertIn(long_text, detail)

    def test_coordinate_scale_status_is_useful(self):
        self.assertEqual(
            coordinate_scale_summary(
                {"width": 1920, "height": 1080},
                {"width": 1920, "height": 1080},
            ),
            "坐标缩放  1920×1080 → 1920×1080 （1:1）",
        )
        self.assertIn("自动缩放", coordinate_scale_summary(
            {"width": 1920, "height": 1080},
            {"width": 2560, "height": 1440},
        ))

    def _dpi_watch_app(self, tk_scaling):
        """只够跑 DPI 监视逻辑的 MacroFlowApp 夹具（不建界面）。"""
        app = MacroFlowApp.__new__(MacroFlowApp)
        root = Mock()
        root.winfo_id.return_value = 4242
        root.tk.call.return_value = tk_scaling
        app.root = root
        return app, root

    def test_watch_display_dpi_corrects_a_wrong_startup_scale(self):
        # 笔记本 200% + 外接屏 100%：窗口铺在 96 DPI 的屏上，但 Tk 启动 scaling
        # 取自主屏（192 DPI）。第一次探测就必须纠正，不能只记录不应用——旧实现
        # 只在"第二次探测"才应用，于是这套配置下界面永远按两倍放大。
        app, root = self._dpi_watch_app(192 / 72.0)
        app._apply_display_dpi = Mock()
        with patch_app("get_window_dpi", return_value=96):
            app._watch_display_dpi()
        app._apply_display_dpi.assert_called_once_with(96)
        root.after.assert_called_once_with(600, app._watch_display_dpi)

    def test_watch_display_dpi_is_quiet_when_scaling_already_matches(self):
        app, _root = self._dpi_watch_app(96 / 72.0)
        app._apply_display_dpi = Mock()
        with patch_app("get_window_dpi", return_value=96):
            app._watch_display_dpi()
        app._apply_display_dpi.assert_not_called()

    def test_sync_ui_scale_uses_the_window_monitor_dpi(self):
        app, root = self._dpi_watch_app(192 / 72.0)
        with patch_app("get_window_dpi", return_value=96), \
             patch_app("set_ui_scale") as app_scale, \
             patch("macroflow.ui.app.dialogs_ui.set_ui_scale") as dialog_scale, \
             patch.object(MacroFlowApp, "_configure_dark_theme") as theme:
            app._sync_ui_scale_to_monitor()
        self.assertEqual(root.tk.call.call_args.args, ("tk", "scaling", 96 / 72.0))
        app_scale.assert_called_once_with(root)
        dialog_scale.assert_called_once_with(root)
        theme.assert_called_once()

    def test_dark_theme_survives_reapply_after_startup_dpi_sync(self):
        # 启动时先按系统 DPI 配一次主题，窗口铺到目标屏后再按该屏 DPI 配一次
        # （_sync_ui_scale_to_monitor）。ttkbootstrap 会把 "TSeparator" 当新样式派生
        # （内部 element_create 一个全局元素），第二次 configure 抛
        # "Duplicate element Horizontal.Separator.separator"——表现为启动即写
        # crash.log、界面全黑。这里真实重建一次根窗口复现那条路径。
        previous_style = ttkbootstrap.style.Style.instance
        ttkbootstrap.style.Style.instance = None
        ttkbootstrap.publisher.Publisher.clear_subscribers()
        root = ttkbootstrap.Window(themename="darkly")
        root.withdraw()
        try:
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.root = root
            app._configure_dark_theme()
            with patch_app("get_window_dpi", return_value=96):
                app._sync_ui_scale_to_monitor()
            # 分隔线样式确实配过（守卫没有把这一行整个跳掉）
            self.assertTrue(root.style.lookup("TSeparator", "background"))
        finally:
            root.destroy()
            ttkbootstrap.style.Style.instance = previous_style
            ttkbootstrap.publisher.Publisher.clear_subscribers()

    def test_action_column_uses_compact_icons(self):
        cases = (
            ({"type": "delay", "ms": 100}, "◷  延时"),
            ({"type": "key", "name": "A", "down": True}, "⌨  键盘"),
            ({"type": "text", "text": "hello"}, "T  文本"),
            ({"type": "mouse_move", "mode": "absolute", "x": 1, "y": 2}, "↖  移动"),
            ({"type": "mouse_button", "button": "left", "down": True}, "◉  点击"),
            ({"type": "repeat_click", "x": 1, "y": 2}, "↻  连续点击"),
            ({"type": "scroll", "dx": 0, "dy": 1}, "↕  滚轮"),
            ({"type": "image_match", "template": "a.png"}, "▣  识图"),
        )
        for action, expected in cases:
            with self.subTest(action=action):
                self.assertEqual(action_summary(action)[0], expected)

    def test_absolute_move_shows_destination_and_mode(self):
        text = recorded_action_description({
            "type": "mouse_move", "mode": "absolute", "x": 640, "y": 360,
        })
        self.assertEqual(text, "鼠标移动到：(640, 360)（桌面坐标）")

    def test_relative_move_shows_delta_and_mode(self):
        text = recorded_action_description({
            "type": "mouse_move", "mode": "relative", "dx": 18, "dy": -7,
        })
        self.assertEqual(text, "游戏转向：ΔX=18，ΔY=-7（相对轨迹）")

    def test_click_and_scroll_show_coordinates(self):
        self.assertEqual(
            recorded_action_description({
                "type": "mouse_button", "button": "left", "down": True,
                "x": 321, "y": 456,
            }),
            "左键按下：(321, 456)",
        )
        self.assertIn("位置=(222, 333)", recorded_action_description({
            "type": "scroll", "dx": 0, "dy": -1, "x": 222, "y": 333,
        }))


class RecorderTests(unittest.TestCase):
    def test_discard_recent_ui_events(self):
        recorder = MacroRecorder()
        recorder.running = True
        recorder._last_action_time = time.perf_counter()
        recorder._append({"type": "mouse_button", "button": "left", "down": True})
        recorder._append({"type": "mouse_button", "button": "left", "down": False})
        removed = recorder.discard_recent(600)
        self.assertEqual(removed, 2)
        self.assertEqual(recorder.actions, [])

    def test_auto_recording_switches_mouse_capture_by_foreground_window(self):
        recorder = MacroRecorder()
        recorder.running = True
        recorder.mode = "auto"
        recorder.target_hwnd = 123
        recorder.interval_ms = 10
        recorder._last_action_time = time.perf_counter()
        with patch("macroflow.input.recorder.is_window_process_foreground", return_value=False):
            recorder._on_move(10, 20)
            self.assertEqual(recorder.actions[-1]["mode"], "absolute")
            recorder._on_raw_move(4, 5)
        with patch("macroflow.input.recorder.is_window_process_foreground", return_value=True):
            recorder._on_move(30, 40)
            recorder._on_raw_move(7, -3)
            recorder._flush_raw(force=True)
        self.assertEqual(recorder.actions[-1]["mode"], "relative")
        self.assertEqual((recorder.actions[-1]["dx"], recorder.actions[-1]["dy"]), (7, -3))

    def test_unbound_game_requires_stable_center_lock(self):
        recorder = MacroRecorder()
        recorder.running = True
        recorder.mode = "auto"
        recorder.target_hwnd = 123
        recorder.relative_requires_center_lock = True
        recorder.interval_ms = 100
        recorder._last_action_time = time.perf_counter()
        recorder._raw_last_flush = time.perf_counter() - 1
        with patch("macroflow.input.recorder.is_window_process_foreground", return_value=True), \
             patch("macroflow.input.recorder.is_cursor_near_window_center", return_value=True):
            recorder._on_raw_move(2, 1)
            recorder._on_raw_move(3, -1)
            self.assertEqual(recorder.current_mode(), "absolute")
            recorder._on_raw_move(4, 2)
            self.assertEqual(recorder.current_mode(), "relative")
        self.assertEqual(recorder.actions[-1]["mode"], "relative")

    def test_relative_capture_uses_game_frequency_cap(self):
        recorder = MacroRecorder()
        recorder.running = True
        recorder.mode = "relative"
        recorder.interval_ms = 100
        recorder._last_action_time = time.perf_counter()
        recorder._raw_last_flush = time.perf_counter() - 0.02
        recorder._on_raw_move(9, -2)
        self.assertEqual(recorder.actions[-1]["mode"], "relative")
        self.assertEqual((recorder.actions[-1]["dx"], recorder.actions[-1]["dy"]), (9, -2))


class RawInputTests(unittest.TestCase):
    def test_listener_lifecycle(self):
        listener = RawMouseListener(lambda _x, _y: None)
        listener.start()
        self.assertTrue(listener.hwnd)
        listener.stop()
        self.assertFalse(listener.thread.is_alive())

if __name__ == '__main__':
    unittest.main()
