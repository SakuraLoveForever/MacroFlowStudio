from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, DEFAULT_MOUSE_MOVE_INTERVAL_MS, DEFAULT_RECORDED_SCREEN,
    DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
    END_CURRENT_SCRIPT_LABEL, JUMP_TARGET_KEYS, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID,
    MacroScript, Workflow, clone_actions_with_new_ids,
    ensure_action_ids, ensure_workflow_step_ids, is_global_script,
    new_action_id,
    recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
)
from macroflow.input.wininput import (
    WindowInfo, activate_window, enum_windows, get_cursor_pos,
    get_monitor_rect_for_window, get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect, get_virtual_screen_rect,
    get_window_dpi,
    force_english_input, get_foreground_window_info, get_window_rect,
    is_current_process_window, is_window, is_window_process_foreground,
    make_window_no_activate, send_button, send_move_absolute,
    set_dark_titlebar, set_rounded_window, show_window,
    show_window_no_activate,
)
from macroflow.ui.dialogs import (
    ClickDialog, GameSetupNoteDialog, GlobalDetectDialog, TurnActionDialog,
    HotkeyScriptsDialog,
    JsonActionDialog, JumpActionDialog, KeyActionDialog,
    RepeatClickDialog, CloseAppDialog, OcrCompareActionDialog, MultiConditionClickDialog,
    RowListConditionClickDialog,
    RowListDiagnosticResultDialog,
    RowRecognitionResultDialog,
    ModulePickerDialog,
    MouseMoveDialog, ResolutionStylesDialog, ScheduleDialog, ScrollDialog,
    SetResolutionActionDialog,
    OpenAppDialog, ScriptDirectoriesDialog, TemplateRegionFormDialog,
    TemplateRegionManagerDialog, WindowPicker,
    WorkflowBatchSettingsDialog, WorkflowRepeatDialog,
    DurationDialog, DurationVar, TIME_UNITS, Tooltip, edit_action,
    key_to_vk, recorded_action_description, vk_to_key_name,
    show_floating_notice, workflow_step_label,
)
import time

class RecordingMixin:
    """录制入口与录制小窗。"""

    def _toggle_record_from_toolbar(self):
        """工具栏「录制」：把这一段键鼠操作录成**一条**动作插进脚本。

        与侧栏「开始录制 F8」的区别只在**录制结果怎么处理**：侧栏是「重新录
        一遍整个脚本」（录制内容直接替换编辑器里的动作），工具栏是在插入位置
        处插入一行「录制动作」，已有的脚本内容一个都不动。录制过程本身完全一样
        ——同一套录制引擎、同一个悬浮小窗（已录条数 / 模式 / F8 停止），所以
        录制期间的手感与原来的整脚本录制一致。
        """
        self._start_action_recording(self._fold_recorded_input, "已插入录制动作")
    def _start_action_recording(self, on_recorded, log_label: str):
        """开始一段「录进一条动作」的录制（工具栏 / 模块代码段共用）。

        on_recorded 在录制结束时被调用：``on_recorded(steps)``，steps 是录到的
        动作列表（已补好 id）。
        """
        if self.recorder.running:
            self._notify("正在录制", "已经在录制中，请先按 F8 停止。")
            return False
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "请先停止当前脚本或工作流。")
            return False
        self._pending_recorded_input = on_recorded
        self._recorded_input_log_label = log_label
        # 走与侧栏/F8 完全相同的录制入口：同一个悬浮小窗、同一套模式切换与停止逻辑。
        self.toggle_record(from_ui=True)
        return True
    def _fold_recorded_input(self, steps: list[dict]) -> None:
        """把录到的一整段操作折成一条「录制动作」插进脚本（不动其他动作）。"""
        if not steps:
            return
        ensure_action_ids(steps)
        self._insert_action({
            "type": RECORDED_INPUT_TYPE,
            RECORDED_INPUT_STEPS_KEY: [dict(step) for step in steps],
            "delay_ms": 0,
        })
        self._set_status(f"已插入录制动作：{len(steps)} 步", "success")
    def toggle_record(self, from_ui: bool = False):
        if self.recorder.running:
            self.stop_recording(discard_recent=from_ui)
        else:
            self.start_recording(from_ui=from_ui)
    def start_recording(self, from_ui: bool = False):
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "请先停止当前脚本或工作流。")
            return
        # 录制本身就是「重新录一遍」：不再拦未保存的修改，否则录制结束会被
        # 自己置上的 dirty 卡住，F8 从此没反应（只能先保存或撤销打开）。
        # 录制只覆盖编辑器里的动作，已保存的旧文件不动，保存新录制时才提示覆盖。
        #
        # 例外：「录进一条动作」的录制（工具栏 / 模块代码段）**不动已有动作**——
        # 它只是把这一段录制插成一条新动作，编辑器里原来那些行必须原样留着。
        insert_record = getattr(self, "_pending_recorded_input", None) is not None
        hwnd = self._bound_hwnd()
        # A bound window may be either a game or an ordinary desktop program.
        # Center-lock detection is what makes the single recording mode choose
        # relative camera deltas without a separate game-mode switch.
        relative_requires_center_lock = bool(hwnd)
        if not hwnd and not from_ui:
            foreground = get_foreground_window_info()
            if foreground and not is_current_process_window(foreground.hwnd):
                # F8 inside an unbound game should work without a setup step.
                # Requiring a stable center cursor keeps ordinary desktop apps
                # on absolute coordinates even when recording starts by F8.
                hwnd = foreground.hwnd
                relative_requires_center_lock = True
        try:
            interval = max(10, min(500, int(self.interval_var.get())))
            self.interval_var.set(interval)
            if not force_english_input(hwnd):
                raise RuntimeError("无法切换到英语输入法，请确认系统已安装英语（美国）键盘。")
            self.recorder.start(
                "auto", interval,
                target_hwnd=hwnd,
                target_relative_enabled=True,
                relative_requires_center_lock=relative_requires_center_lock,
                filter_vks=getattr(self, "_hotkey_recorder_filter_vks", set()),
            )
        except Exception as exc:
            self._notify("无法录制", str(exc))
            self._log(f"启动录制失败：{exc}")
            return
        # 记录"目标窗口所在显示器"的物理矩形：回放时用同一块屏的矩形做基准，
        # 同屏执行坐标 1:1，换屏执行按比例映射。
        self.recording_screen = get_monitor_rect_for_window(hwnd) or get_primary_screen_rect()
        if not insert_record:
            # 重新录制直接覆盖当前文档：保留原脚本名称与保存路径，保存时再提示
            # 是否覆盖（覆盖前自动归档旧版本到 backups/overwritten/）。
            self.script.actions = []
            self._clear_action_undo()
        self.record_started_at = time.perf_counter()
        self.recording_capture_mode = self.recorder.current_mode()
        self.rebuild_action_tree()
        self.record_button.configure(text="停止录制    F8", bootstyle="danger")
        self._set_status("正在录制输入…", "error")
        target_note = "已绑定目标" if self.saved_window_signature else (
            "正在识别锁中心游戏" if relative_requires_center_lock else "桌面坐标"
        )
        self._log(f"开始智能录制：{target_note}；桌面间隔 {interval} ms，游戏转向间隔不高于 16 ms。")
        self._log("录制前已强制切换为英语（美国）输入法，并关闭中文输入状态。")
        self._sound("record_start")
        self._show_recording_mini()
        initial_mode = "游戏转向模式（相对轨迹）" if self.recording_capture_mode == "relative" else "桌面模式（绝对坐标）"
        self._append_mini_step(f"当前模式：{initial_mode}")
        self._poll_recording_mode()
    def _poll_recording_mode(self):
        if not self.recorder.running:
            return
        # The game may be launched after recording starts and its window handle
        # changes on every launch. Re-resolve the saved title/class signature
        # while recording so auto mode can switch to raw relative capture as
        # soon as the current target becomes foreground.
        if self.saved_window_signature:
            previous_hwnd = self.recorder.target_hwnd
            foreground = get_foreground_window_info()
            resolved_hwnd = foreground.hwnd if foreground and self._foreground_matches_target(foreground) else self._bound_hwnd()
            if resolved_hwnd != previous_hwnd:
                self.recorder.target_hwnd = resolved_hwnd
                if resolved_hwnd:
                    self._log(f"已重新绑定当前目标窗口，HWND={resolved_hwnd}；后续按前台状态自动选择坐标模式。")
        current = self.recorder.current_mode()
        if current != self.recording_capture_mode:
            self.recording_capture_mode = current
            label = "游戏转向模式（相对轨迹）" if current == "relative" else "桌面模式（绝对坐标）"
            self._set_status(f"正在录制：{label}", "warning")
            self._log(f"录制模式已切换：{label}。")
            self._append_mini_step(f"模式切换到：{label}")
        self.root.after(180, self._poll_recording_mode)
    def stop_recording(self, discard_recent: bool = False, sound: bool = True):
        # 「录进一条动作」的录制（工具栏 / 模块代码段）与整脚本录制的收尾不同：
        # 录到的动作要交给调用方，不能替换编辑器里的脚本内容。
        handler = getattr(self, "_pending_recorded_input", None)
        self._pending_recorded_input = None
        if handler is not None:
            self._stop_recorded_input(handler, discard_recent=discard_recent, sound=sound)
            return
        if discard_recent:
            removed = self.recorder.discard_recent(600)
            if removed:
                self._log(f"已清理悬浮窗操作产生的 {removed} 条末尾事件。")
        actions = self.recorder.stop()
        unsupported_buttons = sorted(getattr(self.recorder, "unsupported_buttons", ()))
        if unsupported_buttons:
            self._log(
                "已跳过无法回放的鼠标按键："
                + "、".join(unsupported_buttons)
                + "（侧键暂不支持录制回放，请改用左/右/中键）"
            )
        ensure_action_ids(actions)
        self.script.actions = actions
        self.script.settings = self._current_script_settings(self.recording_screen)
        self.recording_screen = None
        self._refresh_coordinate_scale_status()
        self.recording_capture_mode = ""
        self.record_button.configure(text="开始录制    F8", bootstyle="danger")
        self.rebuild_action_tree()
        self._mark_dirty()
        self._set_status(f"录制完成：{len(actions)} 个动作", "success")
        self._log(f"录制完成，共 {len(actions)} 个动作。")
        # 先恢复主窗口再收小窗：_restore_main_window 会看 recorder.running 判断
        # 是不是「录制中」，此刻 recorder 已停止，主窗口必定回来（录制期间它被藏
        # 起来了，不恢复用户会以为软件消失了）。
        if self.main_hidden_for_recording:
            self._restore_main_window()
        self._hide_recording_mini()
        if sound:
            self._sound("record_stop")
        if self.recorder.limit_reached:
            self._notify("已达到安全上限", "录制达到 200,000 个动作。请保存并拆分脚本，避免界面和执行卡顿。")
    def _stop_recorded_input(self, handler, *, discard_recent: bool = False,
                             sound: bool = True) -> None:
        """结束「录进一条动作」的录制：恢复界面，把这段动作交给调用方。

        界面收尾与整脚本录制完全一致（先恢复主窗口再收悬浮小窗、播放停止音），
        区别只有最后一步：录到的动作不写进脚本动作列表，而是交给 handler。
        """
        if discard_recent:
            removed = self.recorder.discard_recent(600)
            if removed:
                self._log(f"已清理悬浮窗操作产生的 {removed} 条末尾事件。")
        try:
            actions = self.recorder.stop()
        finally:
            if self.main_hidden_for_recording:
                self._restore_main_window()
            self._hide_recording_mini()
        unsupported_buttons = sorted(getattr(self.recorder, "unsupported_buttons", ()))
        if unsupported_buttons:
            self._log(
                "已跳过无法回放的鼠标按键："
                + "、".join(unsupported_buttons)
                + "（侧键暂不支持录制回放，请改用左/右/中键）"
            )
        self.recording_screen = None
        self.recording_capture_mode = ""
        self.record_button.configure(text="开始录制    F8", bootstyle="danger")
        self._log(f"录制完成，共 {len(actions)} 个动作。")
        if sound:
            self._sound("record_stop")
        if self.recorder.limit_reached:
            self._notify("已达到安全上限", "录制达到 200,000 个动作。请拆分后再录。")
        label = getattr(self, "_recorded_input_log_label", "") or "录制动作"
        self._recorded_input_log_label = ""
        if not actions:
            self._notify("没有录到内容", "本次录制没有录到任何键鼠操作。")
            return
        try:
            handler([dict(action) for action in actions])
        except Exception as exc:
            self._log(f"{label}失败：{exc}")
            self._notify("录制结果处理失败", str(exc))
            return
        self._log(f"{label}：共 {len(actions)} 步。")
    def _record_action_callback(self, action: dict):
        count = len(self.recorder.actions)
        self._ui(self._show_recorded_action, dict(action), count)
    def _show_recorded_action(self, action: dict, count: int):
        if not self.recorder.running:
            return
        self.record_count_var.set(f"{count} 个动作（录制中）")
        delay = int(action.get("delay_ms", 0))
        self._append_mini_step(f"#{count}  {recorded_action_description(action)} · 间隔 {delay} ms")
