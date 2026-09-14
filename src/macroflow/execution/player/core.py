from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    SCRIPT_START_TARGET_ID, recorded_input_steps, script_ref_repeat_count,
)
from typing import Callable
from pathlib import Path
from macroflow.execution.timeline import PlaybackTimeline
from macroflow.input.wininput import (
    activate_window, get_cursor_pos, get_foreground_window_info,
    get_display_resolution_for_window, get_display_scaling_for_window,
    get_monitor_rect_for_window, get_primary_screen_rect, get_virtual_screen_rect,
    get_window_rect, is_window,
    is_window_process_foreground, resolve_window_signature, send_button, send_key,
    send_move_absolute, send_move_relative, send_scroll,
    set_display_resolution_for_window, set_display_scaling_for_window,
    send_text, set_cursor_pos,
)
from macroflow.core.storage import (
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS, load_script, registered_module_object,
    registered_template_region, resolve_path,
)
import os
import threading
import time

from .base import (
    CAPTURE_FAILURE_GRACE_S,
    GUARD_SETTLE_MS,
    INPUT_ACTION_KINDS,
    JUMP_CURRENT_SCRIPT_LAST_RESULT,
    MAX_SCRIPT_REF_DEPTH,
    get_playback_screen_rect,
)
from .control import (
    AdvanceToNextWorkflowStep,
    EndCurrentScriptRepeatRequest,
    EndCurrentScriptRequest,
    GuardJumpRequest,
    JumpToCurrentScriptLastAction,
    PlaybackStopped,
)

class CoreMixin:
    """回放核心：状态、日志、时间线等待、动作分发与动作序列。"""

    def __init__(self, on_status: Callable[[str], None] | None = None,
                 on_notice: Callable[[str, int], None] | None = None,
                 on_global_detect_request: Callable[[dict], None] | None = None,
                 on_restart_workflow_request: Callable[[dict], bool] | None = None,
                 on_log: Callable[[str], None] | None = None,
                 on_trace: Callable[[dict], None] | None = None,
                 on_trace_line: Callable[[str], None] | None = None,
                 on_script_scope_enter: Callable[..., object] | None = None,
                 on_script_scope_exit: Callable[[object], None] | None = None,
                 on_target_window_request: Callable[[], int | None] | None = None,
                 on_guard_poll: Callable[[], dict | None] | None = None,
                 on_ocr_engine_wait: Callable[[], bool] | None = None,
                 on_resolution_monitor_request: Callable[[], int | None] | None = None,
                 on_timing: Callable[[dict], None] | None = None,
                 guard_settle_ms: int = GUARD_SETTLE_MS):
        self.on_status = on_status
        self.on_notice = on_notice
        self.on_global_detect_request = on_global_detect_request
        # 特殊模块“重新执行工作流”只由应用在当前工作流中接管；独立脚本
        # 没有可重启的工作流，因此回调返回 False 时跳过该固定动作。
        self.on_restart_workflow_request = on_restart_workflow_request
        # 运行日志通道：模块触发/超时等关键操作同步写入（on_status 只进状态栏）。
        self.on_log = on_log
        # 执行明细通道：每执行一行脚本动作回调一次（应用层写“执行明细”日志，
        # 不进界面日志）。事件字典字段：
        #   phase="start"/"end"、script、depth、index、total、action、elapsed_ms
        self.on_trace = on_trace
        # 执行期细节行（模块识别/点击/OCR 观察等）：只进“执行明细”日志。
        self.on_trace_line = on_trace_line
        self.on_script_scope_enter = on_script_scope_enter
        self.on_script_scope_exit = on_script_scope_exit
        self.on_target_window_request = on_target_window_request
        # 守卫引擎：播放器在动作边界与长等待期间回调应用层评估全部全局守卫，
        # 命中时内联执行处理段（不停止、不快照、不重启任何监控）。
        self.on_guard_poll = on_guard_poll
        # OCR 引擎就绪等待回调（应用层可中断轮询）：文字识别前调用，
        # 返回 False 表示用户已请求停止，播放器应立即中断（F12 不再被
        # 首次 OCR 导入卡住）。
        self.on_ocr_engine_wait = on_ocr_engine_wait
        # 分辨率动作未指定参照窗口时，用它拿到"软件自己所在显示器"的窗口句柄：
        # 用户只想改当前系统分辨率，不该被要求先把游戏窗口打开。
        self.on_resolution_monitor_request = on_resolution_monitor_request
        self.on_timing = on_timing
        # 全局模块处理完之后的停顿（毫秒）：先执行完模块步骤、停一下、再继续原任务。
        self.guard_settle_ms = max(0, int(guard_settle_ms))
        self.playback_speed = 1.0
        # 本次播放定格的倍速：录制时间轴是绝对时间，运行中改倍速会让已排好的
        # 时刻突然变短、动作成串爆发，因此只在 play() 开始时取一次快照。
        self._timeline_speed = 1.0
        self.stop_event = threading.Event()
        self.running = False
        self._held_keys: set[int] = set()
        self._held_buttons: set[str] = set()
        self._relative_target_hwnd: int | None = None
        self._legacy_relative_started = False
        self._source_screen: dict | None = None
        self._target_screen: dict | None = None
        self._jump_reason: str | None = None
        self._activate_target = True
        self._activation_hwnd: int | None = None
        # 前台被外部窗口抢占的日志限频（秒）：避免每次输入动作前都刷屏。
        self._last_thief_log_time = 0.0
        self._workflow_context = False
        self._workflow_repeat_number = 0
        self._active_script_name = ""
        self._script_scope_managed = False
        # 守卫处理段执行深度：处理段内不再评估守卫（与旧模型"模块执行期间
        # 其它检测暂停"一致），同一时刻只允许一个处理段。
        self._handler_depth = 0
        # 上一次点击落点（坐标/按键/时刻/来源）：用于同一点防重复点击。
        self._last_click: dict | None = None
        # 当前全局模块处理段的“回脚本前最短停顿”截止时刻（处理段结束时清空）。
        self._guard_settle_deadline: float | None = None
        # 标记当前动作是否来自全局守卫处理段；其中的结束动作只结束本次
        # 脚本重复，不能让外层播放循环跳过剩余重复。
        self._guard_processing_depth = 0
        self._timeline_waiting = False
        # 截图连续失败的起点：等待识图期间截图暂时不可用时按「这一轮没识别到」
        # 处理，从这里起算超过 CAPTURE_FAILURE_GRACE_S 才终止。
        self._capture_failure_since: float | None = None
        self._capture_failure_reported = False
        self._timeline = PlaybackTimeline(
            now=time.perf_counter,
            wait=self._wait_on_timeline,
        )
        self._stop_requested_at: float | None = None
    def stop(self) -> None:
        if self._stop_requested_at is None:
            self._stop_requested_at = time.perf_counter()
        self.stop_event.set()
    def reset(self) -> None:
        self.stop_event.clear()
        # 上一次播放的落点不该影响这一轮：新一轮的第一次点击总是照常发出。
        self._last_click = None
        self._guard_settle_deadline = None
    def set_playback_speed(self, speed: float) -> None:
        """Set the global delay multiplier; key/button hold durations stay unchanged."""
        try:
            value = float(speed)
        except (TypeError, ValueError):
            value = 1.0
        self.playback_speed = max(0.5, min(2.0, round(value, 1)))
        self._timeline_speed = self.playback_speed
    def _scaled_delay(self, milliseconds: int) -> int:
        if milliseconds <= 0:
            return 0
        speed = self._timeline_speed if self._timeline_speed > 0 else 1.0
        return max(1, round(milliseconds / speed))
    def _timeline_offset_ms(self, action: dict) -> float:
        """录制时间轴上的动作时刻 → 本次播放的目标时刻（按倍速缩放）。"""
        try:
            offset = float(action.get("recorded_at_ms", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0
        speed = self._timeline_speed if self._timeline_speed > 0 else 1.0
        return offset / speed
    def _wait_on_timeline(self, seconds: float) -> None:
        self._timeline_waiting = True
        try:
            self._wait(seconds * 1000)
        finally:
            self._timeline_waiting = False
    def _mark_explicit_wait(self, milliseconds: float) -> None:
        if self._timeline_waiting:
            return
        self._timeline.metrics.explicit_wait_ms += max(0.0, milliseconds)
        self._timeline.mark_boundary()
    def _status(self, text: str) -> None:
        if self.on_status:
            self.on_status(text)
    def _log_event(self, text: str) -> None:
        """状态栏 + 事件日志双写（脚本/工作流边界、关键状态变化）。"""
        self._status(text)
        if self.on_log:
            self.on_log(text)
    def _trace(self, text: str, module_detail: bool = False) -> None:
        """执行期细节只写“执行明细”日志：不进事件日志、也不刷状态栏。

        逐行识别/点击/轮询这类信息量极大（一次挂机几十万行），放在事件日志里
        会把真正有用的状态变化埋掉；排查“某一行到底做了什么”看明细日志。

        ``module_detail`` 标记“这一行脚本正在做这件事”的内部说明（模块命中/点击/
        阻塞/逐行扫描等），由界面层缩进显示，与脚本行本身区分开。

        只传了 on_log（例如单元测试或独立使用播放器）时退回 on_log，保证明细
        不会凭空消失。
        """
        if self.on_trace_line:
            if module_detail:
                try:
                    self.on_trace_line(text, module_detail=True)
                except TypeError:
                    # 只接受一个位置参数的旧回调（测试替身/第三方接入）。
                    self.on_trace_line(text)
            else:
                self.on_trace_line(text)
        elif self.on_log:
            self.on_log(text)
    def _note_capture_failure(self, exc: BaseException) -> None:
        """登记一次「截图暂时不可用」，连续失败超过阈值时终止。

        锁屏 / 屏保 / 独占全屏时 BitBlt 会一直返回「拒绝访问」；这类失败
        按「这一轮没识别到」继续轮询，只有持续超过 CAPTURE_FAILURE_GRACE_S
        才报错收尾，避免一次瞬时失败把整个工作流打断。
        """
        now = time.perf_counter()
        if self._capture_failure_since is None:
            self._capture_failure_since = now
        if not self._capture_failure_reported:
            self._capture_failure_reported = True
            self._trace(
                f"屏幕截图暂时失败（锁屏 / 屏保 / 独占全屏时会出现），"
                f"先按未识别到继续轮询：{exc}",
                module_detail=True,
            )
        if now - self._capture_failure_since >= CAPTURE_FAILURE_GRACE_S:
            raise RuntimeError(
                f"屏幕截图连续 {CAPTURE_FAILURE_GRACE_S:.0f} 秒失败"
                f"（可能处于锁屏 / 屏保 / 独占全屏状态）：{exc}"
            ) from exc
    def _clear_capture_failure(self) -> None:
        """截图恢复正常：清掉连续失败计时，下一次失败重新计满额度。"""
        self._capture_failure_since = None
        self._capture_failure_reported = False
    def _diagnostic_log_event(self, text: str,
                              result_sink: Callable[[str], None] | None = None) -> None:
        """Write a diagnostic event to the normal log and an optional result sink."""
        self._log_event(text)
        if result_sink is not None:
            result_sink(text)
    def _wait(self, milliseconds: int) -> None:
        if milliseconds <= 0:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            self._mark_explicit_wait(milliseconds)
            return
        # 长等待切成 100ms 片并逐片检查守卫：异常在等待期间也能被处理，
        # 处理段内联执行完继续剩余等待。短等待（按键按住/点击间隙）保持
        # 单次等待，避免逐片开销。
        if milliseconds >= 200 and self.on_guard_poll is not None:
            remaining = milliseconds / 1000
            while remaining > 0:
                slice_ms = min(0.1, remaining)
                if self.stop_event.wait(slice_ms):
                    raise PlaybackStopped()
                remaining -= slice_ms
                self._poll_guards()
                # 长等待只轮询守卫：前台恢复由播放开始时的激活与每次截图后的
                # 校验负责（见 app._restore_workflow_scan_foreground），等待
                # 期间不再反复检测/激活目标窗口。
            self._mark_explicit_wait(milliseconds)
            return
        if self.stop_event.wait(milliseconds / 1000):
            raise PlaybackStopped()
        self._mark_explicit_wait(milliseconds)
    def play(self, actions: list[dict], repeats: int = 1, hwnd: int | None = None,
             repeat_interval_ms: int = 0,
             source_screen: dict | None = None,
             script_name: str = "",
             activate_target: bool = True,
             activation_hwnd: int | None = None,
             activation_prepared: bool = False,
             on_repeat: Callable[[int, int], None] | None = None,
             on_repeat_complete: Callable[[int, int], None] | None = None,
             start_index: int = 0, start_repeat: int = 0,
             resume_action_index: int | None = None,
             repeat_start_action_id: str | None = None,
             on_action: Callable[[int, int], None] | None = None,
             propagate_current_script_jump: bool = False,
             workflow_context: bool = False) -> bool | str:
        if self.running:
            raise RuntimeError("已有脚本正在执行")
        self.running = True
        self._timeline_waiting = False
        self._stop_requested_at = None
        self._timeline_speed = max(0.5, min(2.0, float(self.playback_speed)))
        self._timeline = PlaybackTimeline(
            now=time.perf_counter,
            wait=self._wait_on_timeline,
        )
        # 每次播放前清空上次中断时的被引用脚本信息。
        self._last_stop_referenced_actions = None
        self._last_stop_referenced_source_screen = None
        self.reset()
        self._relative_target_hwnd = None
        self._legacy_relative_started = False
        self._source_screen = dict(source_screen) if source_screen else None
        self._target_screen = get_playback_screen_rect(hwnd) if self._source_screen else None
        self._active_script_name = str(script_name).strip()
        self._activate_target = bool(activate_target)
        self._activation_hwnd = int(activation_hwnd) if activation_hwnd else None
        self._activation_prepared = bool(activation_prepared and self._activation_hwnd)
        self._workflow_context = bool(workflow_context)
        self._workflow_repeat_number = 0
        self._advance_reason = ""
        advanced_to_next_workflow_step = False
        jump_current_script_last = False
        script_scope = None

        def exit_script_scope() -> None:
            nonlocal script_scope
            if self.on_script_scope_exit and script_scope is not None:
                self.on_script_scope_exit(script_scope)
                script_scope = None

        try:
            if hwnd and not is_window(hwnd):
                self._log_event(
                    "绑定窗口已失效；普通动作继续执行，只有相对转向或窗口区域动作需要重新绑定。",
                )
                hwnd = None
            if self._activation_hwnd and not is_window(self._activation_hwnd):
                self._activation_hwnd = None
                self._activation_prepared = False
                self._log_event("前置窗口已关闭，已跳过前置窗口，继续执行。")
            # “执行前置窗口”只在本次播放开始前激活一次，用于完成准备动作；
            # 它不是输入目标，不能在之后的相对鼠标动作中反复抢回前台。
            if self._activation_hwnd and not self._activation_prepared \
                    and not activate_window(self._activation_hwnd):
                self._status("未能执行一次前置窗口激活，将继续尝试发送输入")
            focus_hwnd = hwnd
            if focus_hwnd:
                if self._activate_target:
                    if is_window_process_foreground(focus_hwnd):
                        # 目标窗口已在前台：不再做任何激活操作。游戏客户端会把
                        # 程序化激活（WM_ACTIVATE / BringWindowToTop）当作
                        # “需要真人点击”，随即弹出“点击游戏画面继续操作”；
                        # 工作流每次重启都会走到这里，保持原样才能不打断游戏。
                        self._relative_target_hwnd = int(focus_hwnd)
                    elif not activate_window(focus_hwnd):
                        self._status("未能强制前置目标窗口，将继续尝试发送输入")
                    else:
                        self._relative_target_hwnd = int(focus_hwnd)
                elif is_window_process_foreground(focus_hwnd):
                    self._relative_target_hwnd = int(focus_hwnd)
                else:
                    self._status("已关闭自动前置；目标窗口当前不在前台")
            repeat_total = max(1, int(repeats))
            repeat_interval = max(0, int(repeat_interval_ms))
            first_action_index = max(0, min(int(start_index), max(0, len(actions) - 1)))
            start_repeat = max(0, min(int(start_repeat), max(0, repeat_total - 1)))
            resume_action = resume_action_index
            if resume_action is not None and int(resume_action) >= len(actions):
                # 被打断重复的脚本动作已全部完成：跳过该次重复，从下一次开头继续。
                # 断点置 None 而非 0，让下一次"全新重复"走 first_action_index 或
                # repeat_start_action_id 指定的起始行。start_repeat 超过
                # repeat_total - 1 时循环为空，表示整个步骤已完成。
                resume_action = None
                start_repeat = start_repeat + 1
            repeat_start_idx = None
            if repeat_start_action_id:
                repeat_start_idx = next(
                    (
                        i for i, action in enumerate(actions)
                        if str(action.get(ACTION_ID_KEY, "")).strip()
                        == str(repeat_start_action_id).strip()
                    ),
                    None,
                )
                if repeat_start_idx is None:
                    self._status("第 2 次起的起始行已失效（原行不存在），回退从第 1 行开始")
            action_indices_by_id = {
                str(action.get(ACTION_ID_KEY)): index
                for index, action in enumerate(actions)
                if action.get(ACTION_ID_KEY)
            }
            # 重复间隔等待期间守卫命中携带的跳转：应用到下一次重复的起始行。
            pending_start_index = None
            for repeat_index in range(start_repeat, repeat_total):
                self._workflow_repeat_number = repeat_index + 1
                if on_repeat:
                    on_repeat(repeat_index + 1, repeat_total)
                action_start = first_action_index
                if pending_start_index is not None:
                    # 上一重复的间隔等待中守卫要求跳转：本次重复从目标行开始。
                    action_start = pending_start_index
                    pending_start_index = None
                elif resume_action is not None and repeat_index == start_repeat:
                    # 断点恢复优先：被打断的那一次从断点继续。
                    action_start = max(0, min(int(resume_action), max(0, len(actions) - 1)))
                elif repeat_index >= 1 and repeat_start_idx is not None:
                    # 第 2 次及以后从指定行开始。
                    action_start = repeat_start_idx
                # 每次重复重新进入脚本全局作用域。上一重复已在完成回调和
                # 重复间隔之前退出，因此每次"执行 x 次"都有独立的全局检测
                # 监控，超时等计时从本次重复开始重新计算。起始行一并告诉应用层：
                # 「▶ 从此开始执行」时，本次重复之前那些全局模块行并不会被执行到。
                if self.on_script_scope_enter:
                    script_scope = self._enter_script_scope(actions, action_start)
                    self._script_scope_managed = True
                if repeat_start_idx is not None and repeat_index >= 1:
                    self._status(
                        f"执行第 {repeat_index + 1}/{repeat_total} 次"
                        f"（从第 {repeat_start_idx + 1} 行）"
                    )
                else:
                    self._status(f"执行第 {repeat_index + 1}/{repeat_total} 次")
                try:
                    self._run_action_sequence(
                        actions, hwnd, start_index=action_start, on_action=on_action,
                    )
                except JumpToCurrentScriptLastAction as request:
                    if propagate_current_script_jump:
                        jump_current_script_last = True
                        self._status("模块代码段要求跳转到当前脚本最后一行")
                        break
                    last_index = len(actions) - 1
                    if last_index >= 0 and last_index != request.current_index \
                            and str(actions[last_index].get("type")) \
                            != "jump_current_script_last":
                        self._status(f"模块代码段跳转到当前脚本第 {last_index + 1} 行")
                        self._run_action_sequence(
                            actions, hwnd, start_index=last_index,
                            on_action=on_action,
                        )
                except EndCurrentScriptRequest as request:
                    if request.repeat_only:
                        self._log_event(
                            f"已{END_CURRENT_SCRIPT_LABEL}本次执行；"
                            "继续执行当前脚本下一次重复。"
                        )
                        self._status(f"已{END_CURRENT_SCRIPT_LABEL}")
                    else:
                        exit_script_scope()
                        if on_repeat_complete:
                            on_repeat_complete(repeat_index + 1, repeat_total)
                        advanced_to_next_workflow_step = True
                        self._log_event(
                            f"已{END_CURRENT_SCRIPT_LABEL}；"
                            "跳过当前脚本剩余重复，继续执行工作流下一项。"
                        )
                        self._status(f"已{END_CURRENT_SCRIPT_LABEL}")
                        break
                except AdvanceToNextWorkflowStep:
                    # The current repeat counts as completed, but remaining
                    # repeats of this workflow step are skipped immediately.
                    exit_script_scope()
                    if on_repeat_complete:
                        on_repeat_complete(repeat_index + 1, repeat_total)
                    advanced_to_next_workflow_step = True
                    self._status(
                        self._advance_reason
                        or "已结束当前脚本，执行工作流下一项"
                    )
                    break
                # A script-global guard belongs only to this repeat. Clear it
                # before completion callbacks and the repeat interval so that
                # no stale recognition state can run between two repeats.
                exit_script_scope()
                if on_repeat_complete:
                    on_repeat_complete(repeat_index + 1, repeat_total)
                if repeat_index + 1 < repeat_total and repeat_interval:
                    self._status(f"重复间隔 {repeat_interval} ms")
                    try:
                        self._wait(repeat_interval)
                    except GuardJumpRequest as request:
                        self._timeline.rebase()
                        # 守卫在间隔等待中命中并携带跳转：解析后应用到下一次
                        # 重复的起始行，而不是让异常逃出 play() 造成“执行失败”。
                        if request.jump_action_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                            # 守卫要求“工作流下一项”：与脚本内的同语义分支一致，
                            # 结束本步骤剩余重复并让工作流推进。
                            advanced_to_next_workflow_step = True
                            self._status("全局检测要求执行工作流下一项")
                            break
                        target_index = None
                        if request.jump_action_id:
                            target_index = action_indices_by_id.get(str(request.jump_action_id))
                        if target_index is None:
                            target_index = max(0, min(
                                request.jump_row - 1, max(0, len(actions) - 1),
                            ))
                        pending_start_index = target_index
                        self._status(
                            f"全局检测触发：下一次重复从第 {target_index + 1} 行开始"
                        )
                    except EndCurrentScriptRequest as request:
                        if request.repeat_only:
                            self._status(
                                f"已{END_CURRENT_SCRIPT_LABEL}本次执行，"
                                "继续当前脚本下一次重复"
                            )
                            continue
                        advanced_to_next_workflow_step = True
                        self._status(f"已{END_CURRENT_SCRIPT_LABEL}")
                        break
                    except AdvanceToNextWorkflowStep:
                        advanced_to_next_workflow_step = True
                        self._status(
                            self._advance_reason
                            or "已结束当前脚本，执行工作流下一项"
                        )
                        break
        except PlaybackStopped as stopped:
            self._status("执行已停止")
            # 全局模块中断时若正处于被引用脚本内部，把该脚本的动作序列
            # 暴露给应用层，用于"结束当前脚本"跳到最内层脚本最后一行。
            self._last_stop_referenced_actions = stopped.referenced_actions
            self._last_stop_referenced_source_screen = stopped.referenced_source_screen
        finally:
            exit_script_scope()
            self._release_all(hwnd)
            cleanup_finished = time.perf_counter()
            if self._stop_requested_at is not None:
                self._timeline.metrics.stop_cleanup_ms += (
                    cleanup_finished - self._stop_requested_at
                ) * 1000
            if self.on_timing:
                self.on_timing(self._timeline.metrics.snapshot())
            self._relative_target_hwnd = None
            self._source_screen = None
            self._target_screen = None
            self._activate_target = True
            self._activation_hwnd = None
            self._activation_prepared = False
            self._workflow_context = False
            self._workflow_repeat_number = 0
            self._active_script_name = ""
            self._script_scope_managed = False
            self.running = False
        if jump_current_script_last:
            return JUMP_CURRENT_SCRIPT_LAST_RESULT
        return advanced_to_next_workflow_step
    def _run_action_sequence(self, actions: list[dict], hwnd: int | None,
                             start_index: int = 0,
                             script_stack: set[str] | None = None,
                             depth: int = 0,
                             on_action: Callable[[int, int], None] | None = None) -> None:
        """Execute an action sequence (a script or a referenced script) in order."""
        nested_timeline_state = None
        if depth > 0:
            self._timeline.mark_boundary()
            nested_timeline_state = (
                self._timeline._base_wall_time,
                self._timeline._base_offset_ms,
                self._timeline._last_scheduled_offset_ms,
            )
            index = max(0, min(int(start_index), max(0, len(actions) - 1)))
            first_offset_ms = self._timeline_offset_ms(actions[index]) if actions else 0.0
            self._timeline.start(first_offset_ms)
        else:
            index = max(0, min(int(start_index), max(0, len(actions) - 1)))
            first_offset_ms = self._timeline_offset_ms(actions[index]) if actions else 0.0
            self._timeline.start(first_offset_ms)
        try:
            self._run_action_sequence_body(
                actions, hwnd, start_index, script_stack, depth, on_action,
            )
        finally:
            if nested_timeline_state is not None:
                (
                    self._timeline._base_wall_time,
                    self._timeline._base_offset_ms,
                    self._timeline._last_scheduled_offset_ms,
                ) = nested_timeline_state
                self._timeline.mark_boundary()
    def _trace_action(self, action: dict, index: int, total: int, depth: int,
                      elapsed_ms: float, waited_ms: float = 0.0) -> None:
        """把一行动作的执行明细交给应用层写「执行明细」日志（失败不影响执行）。

        elapsed_ms = 这一行真正执行的耗时；waited_ms = 执行前等待（录制间隔/
        手工延时）。两者分开记，才能看出“是等太久”还是“这一行本身卡住”。
        """
        callback = self.on_trace
        if callback is None:
            return
        try:
            callback({
                "script": self._active_script_name,
                "depth": int(depth),
                "index": int(index),
                "total": int(total),
                "action": action,
                "elapsed_ms": round(float(elapsed_ms), 1),
                "waited_ms": round(float(waited_ms), 1),
            })
        except Exception:
            pass
    def _run_action_sequence_body(self, actions: list[dict], hwnd: int | None,
                                  start_index: int,
                                  script_stack: set[str] | None,
                                  depth: int,
                                  on_action: Callable[[int, int], None] | None) -> None:
        """Execute a sequence after its caller has established timeline ownership."""
        action_indices_by_id = {
            str(action.get("action_id")): index
            for index, action in enumerate(actions)
            if action.get("action_id")
        }
        index = max(0, min(int(start_index), max(0, len(actions) - 1)))
        total = len(actions)
        while index < len(actions):
            action = actions[index]
            try:
                # 动作边界守卫评估：命中时内联执行处理段（可携带跳转/结束/推进语义）。
                self._poll_guards()
                # 等这一行的“执行前延时”：明细日志里单独记一段等待时长，
                # 动作本身的耗时只算真正发出去的那一下。
                default_delay = 1000 if action.get("type") == "image_match" else 0
                wait_started = time.perf_counter()
                if "recorded_at_ms" in action:
                    self._timeline.wait_until(self._timeline_offset_ms(action))
                else:
                    self._wait(self._scaled_delay(int(action.get("delay_ms", default_delay))))
                waited_ms = (time.perf_counter() - wait_started) * 1000
                action_started = time.perf_counter()
                jump_target = self._execute_action(action, hwnd, script_stack, depth)
                # 一行动作一条明细：前面等多久 + 这一下真正花了多久。
                self._trace_action(
                    action, index, total, depth,
                    (time.perf_counter() - action_started) * 1000, waited_ms,
                )
                if action.get("type") in {"delay", "script_ref", "image_match"}:
                    self._timeline.mark_boundary()
            except GuardJumpRequest as request:
                self._timeline.rebase()
                # 只在守卫所属脚本帧解析行目标；模块代码段或其他脚本帧
                # 先原样抛出，直到回到对应的脚本动作序列。
                current_scope_ids = frozenset(action_indices_by_id)
                if depth > 0 and (
                        not request.scope_action_ids
                        or request.scope_action_ids != current_scope_ids
                ):
                    raise
                if depth > 0 and request.scope_action_ids \
                        and request.jump_action_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                    raise EndCurrentScriptRepeatRequest()
                # 守卫处理段要求跳到当前脚本某一行：按动作唯一标识解析后从该行继续。
                if request.jump_action_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                    raise EndCurrentScriptRequest()
                target_index = None
                if request.jump_action_id:
                    target_index = action_indices_by_id.get(str(request.jump_action_id))
                if target_index is None:
                    target_index = max(0, min(request.jump_row - 1, max(0, len(actions) - 1)))
                script_context = (
                    f"脚本[{self._active_script_name}]："
                    if self._active_script_name else ""
                )
                self._log_event(
                    f"{script_context}全局检测跳转到第 {target_index + 1} 行执行。"
                )
                index = target_index
                continue
            except JumpToCurrentScriptLastAction as request:
                if depth == 0:
                    request.current_index = index
                raise
            self._status(f"动作 {index + 1}/{len(actions)}")
            if on_action and depth == 0:
                # 只在最外层记录，报告下一个要执行的动作下标（可能等于总数，表示脚本已完成）。
                on_action(index + 1, len(actions))
            after_delay = max(0, int(action.get("after_delay_ms", 0)))
            if after_delay:
                self._wait(self._scaled_delay(after_delay))
            if jump_target is None:
                index += 1
                continue
            target_kind, target_value = jump_target
            if target_kind == "end_current_script":
                raise EndCurrentScriptRequest(
                    repeat_only=self._guard_processing_depth > 0,
                )
            if target_kind == "next_workflow_step":
                if depth > 0:
                    raise EndCurrentScriptRepeatRequest()
                self._advance_reason = "已结束当前脚本，执行工作流下一项"
                raise AdvanceToNextWorkflowStep()
            if target_kind == "action_id":
                target_index = action_indices_by_id.get(str(target_value))
                if target_index is None:
                    raise RuntimeError("识图跳转目标动作已被删除，请重新选择")
            else:
                jump_row = int(target_value)
                if not 1 <= jump_row <= len(actions):
                    raise RuntimeError(f"识图跳转行无效：第 {jump_row} 行，脚本共 {len(actions)} 行")
                target_index = jump_row - 1
            self._status(f"{self._jump_reason or '识图'}，跳到第 {target_index + 1} 行目标动作")
            index = target_index
            self._timeline.mark_boundary()
    def _execute_action(self, action: dict, hwnd: int | None,
                        script_stack: set[str] | None = None,
                        depth: int = 0) -> tuple[str, str | int] | None:
        # 每个输入动作前确保目标窗口在前台：焦点被抢（其他软件弹窗、误点桌面、
        # 执行小窗/通知闪现）后，接下来的输入会发给当时的前台窗口——只有鼠标
        # 坐标点击还带坐标，按键则完全丢失（表现就是“某个键没反应”）。
        # 目标窗口本来就在前台时不激活，避免多一次 SetForegroundWindow 让游戏
        # 弹“点击游戏画面继续操作”。
        kind = action.get("type")
        if kind in INPUT_ACTION_KINDS:
            self._ensure_foreground_for_input(hwnd)
        if kind == "delay":
            # 手工延时同样跟随倍速，否则同一份脚本里“录制间隔加速、手工延时
            # 不加速”两种口径混在一起。
            self._wait(self._scaled_delay(int(action.get("ms", 100))))
        elif kind == "key":
            vk = int(action.get("vk", 0))
            down = bool(action.get("down", True))
            if not vk:
                return
            send_key(vk, down)
            (self._held_keys.add if down else self._held_keys.discard)(vk)
        elif kind == "key_press":
            vk = int(action.get("vk", 0))
            if not vk:
                return
            hold = int(action.get("hold_ms", 30))
            send_key(vk, True)
            self._held_keys.add(vk)
            try:
                self._wait(hold)
            finally:
                send_key(vk, False)
                self._held_keys.discard(vk)
        elif kind == "text":
            text = str(action.get("text", ""))
            char_delay = max(0, int(action.get("char_delay_ms", 10)))
            # 逐字符发送、间隔用 _wait（检查停止信号）：长文本发送期间
            # F12 也能立即中断，不会整段发完才响应停止。
            for char in text:
                send_text(char, 0)
                if char_delay:
                    self._wait(char_delay)
        elif kind == "mouse_move":
            move_mode = action.get("mode", "absolute")
            if move_mode == "relative":
                dx = int(action.get("dx", 0))
                dy = int(action.get("dy", 0))
                # 通用相对转向：MOUSEEVENTF_MOVE 是系统级事件，Windows 直接
                # 投递给当前前台窗口，无需任何窗口句柄，也不区分游戏/桌面
                # 窗口。有可用目标窗口时仅“尽力”激活到前台保证送达（激活
                # 失败不影响发送）；没有窗口也直接发送——任何前台状态下都
                # 能执行，不再有任何“需要有效目标窗口”的报错。
                focus_hwnd = self._relative_target_hwnd
                if not focus_hwnd or not is_window(focus_hwnd):
                    focus_hwnd = hwnd if hwnd and is_window(hwnd) else None
                    if self.on_target_window_request:
                        focus_hwnd = self.on_target_window_request() or focus_hwnd
                # 只在目标窗口变化时激活到前台（通常播放开始一次）；此后每个
                # 转向动作直接发送相对移动，避免每次 SetForegroundWindow 的
                # 系统开销拖慢转向序列。
                if focus_hwnd:
                    self._relative_target_hwnd = int(focus_hwnd)
                if not self._legacy_relative_started:
                    self._status("游戏相对轨迹使用 1.2.1 兼容方式")
                    self._legacy_relative_started = True
                send_move_relative(dx, dy)
            else:
                x, y = int(action.get("x", 0)), int(action.get("y", 0))
                x, y = self._scale_point(x, y)
                x, y = self._clamp_click_point(x, y, hwnd)
                send_move_absolute(x, y)
        elif kind == "mouse_button":
            button = str(action.get("button", "left"))
            down = bool(action.get("down", True))
            if action.get("mode") == "absolute":
                x, y = int(action.get("x", 0)), int(action.get("y", 0))
                x, y = self._scale_point(x, y)
                x, y = self._clamp_click_point(x, y, hwnd)
                send_move_absolute(x, y)
            send_button(button, down)
            (self._held_buttons.add if down else self._held_buttons.discard)(button)
        elif kind == "click":
            button = str(action.get("button", "left"))
            hold_ms = int(action.get("hold_ms", 30))
            if action.get("pos_mode") == "current":
                # 点击鼠标当前位置：不移动光标、不做分辨率缩放，也不参与
                # 同一点去重（这一下点在哪里要等按键落下才知道）。
                send_button(button, True)
                self._held_buttons.add(button)
                try:
                    self._wait(hold_ms)
                finally:
                    send_button(button, False)
                    self._held_buttons.discard(button)
                self._last_click = None
            else:
                x, y = int(action.get("x", get_cursor_pos()[0])), int(action.get("y", get_cursor_pos()[1]))
                x, y = self._scale_point(x, y)
                x, y = self._clamp_click_point(x, y, hwnd)
                source = self._current_click_source()
                if self._skip_duplicate_click(x, y, button, source):
                    return None
                self._record_click(x, y, button, source)
                send_move_absolute(x, y)
                send_button(button, True)
                self._held_buttons.add(button)
                try:
                    self._wait(hold_ms)
                finally:
                    send_button(button, False)
                    self._held_buttons.discard(button)
        elif kind == "repeat_click":
            button = str(action.get("button", "left"))
            x, y = int(action.get("x", get_cursor_pos()[0])), int(action.get("y", get_cursor_pos()[1]))
            x, y = self._scale_point(x, y)
            x, y = self._clamp_click_point(x, y, hwnd)
            count = max(1, int(action.get("count", 2)))
            interval_ms = max(0, int(action.get("interval_ms", 100)))
            hold_ms = max(1, int(action.get("hold_ms", 30)))
            # 去重只看整段连点的第一次：这一行自己要求的 count 次照点不误。
            source = self._current_click_source()
            if self._skip_duplicate_click(x, y, button, source):
                return None
            self._record_click(x, y, button, source)
            self._status(f"连续点击 {count} 次，间隔 {interval_ms} ms @ ({x}, {y})")
            for index in range(count):
                if self.stop_event.is_set():
                    raise PlaybackStopped()
                send_move_absolute(x, y)
                send_button(button, True)
                self._held_buttons.add(button)
                try:
                    self._wait(hold_ms)
                finally:
                    send_button(button, False)
                    self._held_buttons.discard(button)
                if index < count - 1 and interval_ms > 0:
                    self._wait(interval_ms)
        elif kind == "turn":
            # 转向：鼠标相对移动 ΔX/ΔY，不按键
            dx = int(action.get("dx", 0))
            dy = int(action.get("dy", 0))
            steps = max(1, min(500, int(action.get("steps", 1))))
            if "pulse_duration_ms" in action:
                duration_ms = max(0, int(action.get("pulse_duration_ms", 0)))
            else:
                duration_ms = max(0, int(action.get("duration_ms", 10)))
            self._status(
                f"转向：ΔX={dx}，ΔY={dy}，{steps} 步，{duration_ms} ms"
            )
            # 转向前把目标窗口带到前台并把光标移回窗口中心：录制转向时
            # 游戏在前台且锁定光标，ΔX/ΔY 从中心起算；回放若游戏不在
            # 前台（MacroFlow 窗口挡住游戏、上次转向把光标停在屏幕边缘），
            # 同样的位移会被桌面边界截短或完全被游戏忽略，坦克不转。
            self._center_cursor_for_turn(hwnd)
            per_step_dx = dx / steps
            per_step_dy = dy / steps
            for step in range(1, steps + 1):
                if self.stop_event.is_set():
                    raise PlaybackStopped()
                current_dx = round(per_step_dx * step)
                current_dy = round(per_step_dy * step)
                # 发送累计偏移量，确保最终精确到达目标偏移
                step_dx = current_dx - round(per_step_dx * (step - 1))
                step_dy = current_dy - round(per_step_dy * (step - 1))
                send_move_relative(step_dx, step_dy)
                step_start_ms = duration_ms * (step - 1) // steps
                step_end_ms = duration_ms * step // steps
                self._wait(step_end_ms - step_start_ms)
        elif kind == "scroll":
            dx, dy = int(action.get("dx", 0)), int(action.get("dy", 0))
            # 滚轮只作用于光标所在窗口/控件：先移到指定位置再滚，否则滚动会落在
            # 光标当时恰好停着的地方（回放时通常已经不是录制/指定的位置）。
            if action.get("x") is not None and action.get("y") is not None:
                x, y = self._scale_point(int(action["x"]), int(action["y"]))
                x, y = self._clamp_click_point(x, y, hwnd)
                send_move_absolute(x, y)
            send_scroll(dx, dy)
        elif kind == "recorded_input":
            # 折叠的「录制动作」：整段录制内容作为一条动作播放，内部按录制顺序
            # 逐步执行（timeline 负责还原每一步之间的间隔）。
            steps = recorded_input_steps(action)
            if steps:
                self._run_action_sequence(steps, hwnd, script_stack=script_stack, depth=depth + 1)
        elif kind == "image_match":
            return self._execute_image(action, hwnd, script_stack, depth)
        elif kind == "text_ocr":
            return self._execute_text_ocr(action, hwnd, script_stack, depth)
        elif kind == "ocr_compare":
            return self._execute_ocr_compare(action, hwnd, script_stack, depth)
        elif kind == "multi_condition_click":
            return self._execute_multi_condition_click(action, hwnd, script_stack, depth)
        elif kind == "row_list_condition_click":
            return self._execute_row_list_condition_click(action, hwnd, script_stack, depth)
        elif kind == "global_detect":
            if self.on_global_detect_request and not self._script_scope_managed:
                self.on_global_detect_request(action)
        elif kind == "restart_workflow":
            if self.on_restart_workflow_request and self.on_restart_workflow_request(action):
                # 应用已接管：停止当前工作流并从目标行重新执行。
                raise PlaybackStopped()
            # 独立脚本运行时没有“当前工作流”，该固定动作不执行。
            return None
        elif kind == "end_current_script":
            raise EndCurrentScriptRequest(
                repeat_only=self._guard_processing_depth > 0,
            )
        elif kind == "jump_current_script_last":
            raise JumpToCurrentScriptLastAction()
        elif kind == "block":
            self._status("阻塞等待其他跳转")
            while True:
                # 用短片段等待，保证停止信号及时生效；动作边界之外也要主动
                # 轮询全局守卫，让全局模块的跳转可以释放这个阻塞动作。
                self._wait(100)
                self._poll_guards()
        elif kind == "activate_window":
            self._execute_activate_window(action)
        elif kind == "jump":
            # 默认“第 2 次及以后生效”：工作流第 1 次、脚本重复执行的第 1 次
            # 和单次运行脚本时都继续下一行；第 2 次起才跳转。
            if bool(action.get("workflow_repeat_at_least_2", True)) and (
                self._workflow_repeat_number < 2
            ):
                self._jump_reason = None
                self._status("跳转动作条件未满足：仅在第 2 次及以后跳转")
                return None
            self._jump_reason = "跳转动作"
            target_id = str(action.get("jump_action_id", "")).strip()
            if target_id == SCRIPT_START_TARGET_ID:
                return "row", 1
            if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                return "next_workflow_step", ""
            if target_id:
                return "action_id", target_id
            return "row", max(1, int(action.get("jump_row", 1)))
        elif kind == "script_ref":
            script_value = str(action.get("script", "")).strip()
            if not script_value:
                raise RuntimeError("引用脚本动作缺少脚本文件")
            script_path = resolve_path(script_value)
            if not script_path.is_file():
                raise RuntimeError(f"引用的脚本不存在：{script_value}")
            resolved = str(script_path.resolve())
            if script_stack is None:
                script_stack = set()
            if resolved in script_stack:
                raise RuntimeError(f"检测到脚本循环引用：{Path(script_path).stem} 引用了自身或其上级脚本")
            if depth >= MAX_SCRIPT_REF_DEPTH:
                raise RuntimeError("脚本引用嵌套过深，已停止执行")
            repeat_total = script_ref_repeat_count(action)
            script_stack.add(resolved)
            try:
                for repeat_index in range(repeat_total):
                    referenced = load_script(script_path)
                    referenced_scope = None
                    try:
                        if self.on_script_scope_enter:
                            referenced_scope = self.on_script_scope_enter(referenced.actions)
                        if repeat_total > 1:
                            self._status(
                                f"执行引用脚本 {referenced.name}（第 {repeat_index + 1}/{repeat_total} 次，"
                                f"{len(referenced.actions)} 个动作）",
                            )
                        else:
                            self._status(
                                f"执行引用脚本 {referenced.name}（{len(referenced.actions)} 个动作）",
                            )
                        self._run_action_sequence(
                            referenced.actions, hwnd,
                            script_stack=script_stack, depth=depth + 1,
                        )
                    except EndCurrentScriptRequest as request:
                        if request.repeat_only:
                            self._status(
                                f"已{END_CURRENT_SCRIPT_LABEL}本次执行，"
                                "进入下一次引用执行"
                            )
                            continue
                        # 只在最近的脚本引用边界接住；模块代码段本身不是脚本边界，
                        # 因而结束信号会先穿过代码段，再结束当前最里层引用脚本。
                        self._status(f"已{END_CURRENT_SCRIPT_LABEL}（返回外层脚本）")
                        break
                    except EndCurrentScriptRepeatRequest:
                        # “脚本结尾”命中嵌套脚本时只结束本次引用执行，
                        # 不能把该引用脚本剩余 repeats 一起跳过，更不能推进外层工作流。
                        self._status(
                            f"已结束 {referenced.name} 当前执行，进入下一次引用执行"
                        )
                        continue
                    except PlaybackStopped as stopped:
                        # 异常从最内层先冒泡：只保留最内层被引用脚本的信息，
                        # 嵌套更深的外层引用不覆盖。
                        if stopped.referenced_actions is None:
                            stopped.referenced_actions = referenced.actions
                            stopped.referenced_source_screen = (
                                dict(referenced.settings.get("recorded_screen", {})) or None
                            )
                        raise
                    finally:
                        if self.on_script_scope_exit and referenced_scope is not None:
                            self.on_script_scope_exit(referenced_scope)
            finally:
                script_stack.discard(resolved)
        elif kind == "open_app":
            app_value = str(action.get("path", "")).strip()
            if not app_value:
                raise RuntimeError("打开软件动作缺少路径")
            app_path = resolve_path(app_value)
            if not app_path.is_file():
                raise RuntimeError(f"要打开的软件不存在：{app_value}")
            app_args = str(action.get("args", "")).strip()
            try:
                os.startfile(str(app_path), arguments=app_args)
            except OSError as exc:
                raise RuntimeError(f"无法打开软件：{app_value}（{exc}）") from exc
            # 走日志通道：打开/关闭软件是排障关键信息，只写状态栏会看不见。
            self._log_event(f"已启动软件 {app_path.name}" + (f"（{app_args}）" if app_args else ""))
        elif kind == "close_app":
            self._execute_close_app(action)
        elif kind == "set_resolution":
            signature = action.get("window") or {}
            has_signature = isinstance(signature, dict) and any(
                str(signature.get(key, "")).strip()
                for key in ("title", "class_name", "process_path")
            )
            resolution_hwnd = None
            monitor_label = "当前软件所在显示器"
            if has_signature:
                resolution_window = resolve_window_signature(signature)
                if resolution_window is not None:
                    resolution_hwnd = int(resolution_window.hwnd)
                    monitor_label = "参照窗口所在显示器"
                else:
                    label = str(
                        signature.get("title")
                        or signature.get("class_name")
                        or signature.get("process_path")
                        or "未命名窗口"
                    ).strip()
                    # 参照窗口没打开时不再让整条工作流失败：退回"当前软件所在
                    # 显示器"并记一条提示（用户多数只是想改本机分辨率）。
                    self._log_event(
                        f"分辨率参照窗口未找到：{label}，改为修改{monitor_label}。"
                    )
            if resolution_hwnd is None:
                resolution_hwnd = self._request_resolution_monitor()
            if resolution_hwnd is None:
                raise RuntimeError(
                    "无法确定要修改的显示器：参照窗口未找到，"
                    "且读不到当前软件所在的显示器"
                )
            try:
                width = int(action.get("width", 0))
                height = int(action.get("height", 0))
                refresh_rate = int(action.get("refresh_rate", 0) or 0)
                scale_percent = int(action.get("scale_percent", 100) or 100)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("分辨率动作参数无效") from exc
            if width <= 0 or height <= 0:
                raise RuntimeError("分辨率动作缺少有效的宽高")
            if not self._ensure_display_resolution(
                    resolution_hwnd, width, height, refresh_rate, monitor_label):
                raise RuntimeError(f"无法将{monitor_label}切换到 {width}×{height}")
            self._ensure_display_scaling(resolution_hwnd, scale_percent, monitor_label)
            self._status(
                f"{monitor_label}已确认 {width}×{height}，缩放目标 {scale_percent}%"
            )
            self._wait(500)
            # 分辨率/缩放变了，执行参考屏必须重新取：优先本次执行的目标窗口
            # 所在显示器（分辨率动作可能改的是另一块屏），没有目标窗口时退回
            # 被修改的那块屏。
            self._target_screen = get_playback_screen_rect(hwnd or resolution_hwnd)
        elif kind == "notice":
            text = str(action.get("text", "提醒"))
            duration = max(500, min(60000, int(action.get("duration_ms", 3000))))
            if self.on_notice:
                self.on_notice(text, duration)
        elif kind in {"comment", None}:
            return
        else:
            raise RuntimeError(f"未知动作类型：{kind}")
    def _release_all(self, hwnd: int | None) -> None:
        for vk in list(self._held_keys):
            try:
                send_key(vk, False)
            except Exception:
                pass
        for button in list(self._held_buttons):
            try:
                send_button(button, False)
            except Exception:
                pass
        self._held_keys.clear()
        self._held_buttons.clear()
