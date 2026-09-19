from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    SCRIPT_START_TARGET_ID, recorded_input_steps, script_ref_repeat_count,
)
from pathlib import Path
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
import time

from .base import (
    MAX_SCRIPT_REF_DEPTH,
    get_playback_screen_rect,
)
from .control import (
    GuardJumpRequest,
    PlaybackStopped,
)

class GuardsMixin:
    """全局守卫：轮询、命中处理段与命中后的稳定等待。"""

    def _poll_guards(self) -> bool:
        """动作边界/等待片上的守卫评估：依次内联执行当前轮全部命中。

        返回这一轮是否真的执行过处理段（单独执行全局检测时据此判断“已触发”）。
        """
        if self._handler_depth > 0 or self.on_guard_poll is None:
            return False
        # 刚处理完一个全局模块的那一秒停顿里不再评估守卫：那是留给游戏消化
        # 这一下的时间，也让“一个处理段”真正只对应一次触发。
        if self._guard_settle_deadline is not None:
            return False
        handled = False
        while True:
            started = time.perf_counter()
            try:
                hit = self.on_guard_poll()
            finally:
                self._timeline.metrics.recognition_ms += (
                    time.perf_counter() - started
                ) * 1000
            if not hit:
                return handled
            self.handle_guard_hit(hit)
            handled = True
            self._timeline.mark_boundary()
    @staticmethod
    def _guard_processing_action_description(action: object) -> str:
        """Return a concise, parameterized description for a guard action."""
        if not isinstance(action, dict):
            return "未知动作"
        kind = str(action.get("type") or "未知动作")
        if kind == "end_current_script":
            return END_CURRENT_SCRIPT_LABEL
        if kind == "restart_workflow":
            return "重新执行工作流"
        if kind == "jump_current_script_last":
            return "跳转到当前脚本最后一行"
        if kind == "block":
            return "阻塞等待其他跳转"
        if kind == "delay":
            return f"等待 {action.get('ms', 0)} ms"
        if kind == "click":
            if action.get("pos_mode") == "current":
                target = "鼠标当前位置"
            else:
                target = f"({action.get('x', 0)}, {action.get('y', 0)})"
            return f"点击 {action.get('button', 'left')} @ {target}"
        if kind == "repeat_click":
            return (
                f"连续点击 {action.get('button', 'left')} @ "
                f"({action.get('x', 0)}, {action.get('y', 0)}) × {action.get('count', 2)}"
            )
        if kind == "key_press":
            return f"敲击 {action.get('name', action.get('vk'))}"
        if kind == "key":
            state = "按下" if bool(action.get("down", True)) else "抬起"
            return f"{state} {action.get('name', action.get('vk'))}"
        if kind == "text":
            text = str(action.get("text", "")).replace("\n", "↵")
            return f"输入文本「{text[:100]}」"
        if kind == "script_ref":
            return f"执行引用脚本：{action.get('script', '未设置')}"
        if kind == "image_match":
            template = Path(str(action.get("template", ""))).name or "未设置模板"
            return f"识图：{template}"
        if kind == "global_detect":
            template = Path(str(action.get("template", ""))).name or "未设置模板"
            return f"启用全局检测：{template}"
        if kind == "notice":
            return f"显示提醒：{action.get('text', '提醒')}"
        if kind == "jump":
            target = str(action.get("jump_action_id") or "").strip()
            if target == NEXT_WORKFLOW_STEP_TARGET_ID:
                return "跳转到工作流下一项"
            return f"跳转到动作 {target or action.get('jump_row', 1)}"
        return kind
    def _log_guard_processing_actions(self, actions: object) -> None:
        """Log each configured guard-processing action before it runs."""
        if not isinstance(actions, (list, tuple)):
            return
        total = len(actions)
        for index, action in enumerate(actions, start=1):
            self._trace(
                f"全局检测处理段动作 {index}/{total}："
                f"{self._guard_processing_action_description(action)}。"
            )
    def _enter_script_scope(self, actions: list[dict], origin_row: int = 0,
                            last_row: int | None = None):
        """进入脚本全局作用域（把本次播放覆盖的行范围一并交给应用层）。

        ``origin_row`` / ``last_row``（含）让应用层只启用这次真的会执行到的
        那几行里的脚本全局模块：从第 N 行起跑时它之前的行不启用，片段循环时
        片段之外（含片段末行之后）的行同样不启用。
        """
        handler = self.on_script_scope_enter
        if handler is None:
            return None
        return handler(actions, origin_row, last_row)
    def handle_guard_hit(self, hit: dict) -> None:
        """守卫触发处理段（播放器线程内联）：延时 → 点击/二次识别 → 代码段/
        模块脚本 → 跳转。原执行流在处理段结束后原地继续，无需断点快照与恢复。
        """
        if self._handler_depth >= MAX_SCRIPT_REF_DEPTH:
            raise RuntimeError("全局守卫处理段嵌套过深，已停止执行")
        hwnd = hit.get("hwnd")
        self._handler_depth += 1
        # 处理段跑完后至少停 self.guard_settle_ms 再回原脚本（见 _settle_after_guard）。
        self._guard_settle_deadline = (
            time.perf_counter() + self.guard_settle_ms / 1000.0
            if self.guard_settle_ms else None
        )
        try:
            subject = str(hit.get("log_subject") or "守卫")
            script_context = (
                f"脚本[{self._active_script_name}] · "
                if self._active_script_name else ""
            )
            kind = str(hit.get("kind") or "success")
            if kind == "timeout":
                self._log_event(f"全局检测超时：{script_context}{subject}，执行超时处理段。")
            else:
                self._trace(f"全局检测触发：{script_context}{subject}，开始执行处理段。")
            delay = max(0, int(hit.get("delay_ms", 0)))
            if delay:
                self._trace(f"全局检测处理动作：等待 {delay} ms。")
                self._wait(delay)
            activation_hwnd = hit.get("activation_hwnd")
            if activation_hwnd and is_window(activation_hwnd):
                self._trace("全局检测处理动作：执行前置窗口激活。")
                if not activate_window(activation_hwnd):
                    self._status("未能执行一次前置窗口激活，将继续尝试发送输入")
            click = hit.get("click")
            if click and len(click) == 2:
                click_count = max(1, int(hit.get("click_count", 1)))
                # 全局模块这一下点击是事件级的：它会打断正在跑的那一行，用户要能
                # 在事件日志里看到“确实点了、点在哪”，不必翻执行明细。
                self._log_event(
                    f"全局检测处理动作：点击 {hit.get('button', 'left')} "
                    f"@ ({int(click[0])}, {int(click[1])})"
                    + (f" × {click_count}" if click_count > 1 else "")
                    + "。"
                )
                self._click_module_point(
                    int(click[0]), int(click[1]),
                    str(hit.get("button", "left")),
                    click_count,
                    hwnd,
                )
            second = hit.get("second")
            if second:
                self._trace(
                    "全局检测处理动作：执行二次识别"
                    f"（{Path(str(second.get('second_match_template', ''))).name or '未设置模板'}）。"
                )
                self._execute_second_match(second, hwnd, hit.get("match"))
            actions = hit.get("actions")
            if actions:
                self._log_guard_processing_actions(actions)
                self._play_guard_actions(
                    actions, hwnd, hit,
                    source_screen=hit.get("source_screen"),
                )
            script_value = str(hit.get("script", "")).strip()
            if script_value:
                script_path = resolve_path(script_value)
                if not script_path.is_file():
                    raise RuntimeError(f"全局模块脚本不存在：{script_value}")
                script = load_script(script_path)
                self._trace(f"全局检测处理动作：执行模块脚本「{script.name}」。")
                self._play_guard_actions(
                    script.actions, hwnd, hit,
                    source_screen=dict(script.settings.get("recorded_screen", {})) or None,
                )
                self._trace(f"全局模块步骤已执行：{script.name}。")
            jump_action_id = str(hit.get("jump_action_id", "")).strip()
            if jump_action_id or hit.get("jump_row"):
                target = (
                    "工作流下一项"
                    if jump_action_id == NEXT_WORKFLOW_STEP_TARGET_ID
                    else f"动作 {jump_action_id}" if jump_action_id
                    else f"第 {max(1, int(hit.get('jump_row', 1)))} 行"
                )
                self._trace(f"全局检测处理动作：跳转到{target}。")
                raise GuardJumpRequest(
                    jump_action_id, max(1, int(hit.get("jump_row", 1))),
                    hit.get("scope_action_ids"),
                )
            # 正常走完处理段：先让游戏消化这一下，再回到原脚本继续跑。
            self._settle_after_guard()
        finally:
            self._handler_depth -= 1
            # 超时处理段等提前返回的路径同样要有这段停顿（幂等，重复调用不会多等）。
            self._settle_after_guard()
    def _settle_after_guard(self) -> None:
        """全局模块处理完之后的停顿：先执行完模块步骤，等 1 秒，再继续原来的任务。

        全局检测是在脚本跑到一半时插进来的（脚本那一行还在下面等着）。守卫刚点完
        × / 确定就立刻回脚本继续点，两个来源的输入挤在一起时，游戏常把守卫那一下
        丢掉（日志里同一颗 × 有过点得掉、也有过点不掉）。这里强制留出
        ``GUARD_SETTLE_MS`` 的喘息时间，让弹窗/界面先把这一下处理完。
        """
        if self._guard_settle_deadline is None:
            return
        deadline = self._guard_settle_deadline
        self._guard_settle_deadline = None
        if self.stop_event.is_set():
            # 已经按了 F12：不再为停顿多等一秒。
            raise PlaybackStopped()
        remaining = deadline - time.perf_counter()
        if remaining > 0:
            self._trace(f"全局检测处理完成，等待 {int(remaining * 1000)} ms 后继续原任务。")
            # 这一段是纯等待：不评估守卫（_poll_guards 看到截止时刻会跳过），
            # 只保证 F12 能及时中断。
            while remaining > 0:
                if self.stop_event.wait(remaining):
                    raise PlaybackStopped()
                remaining = deadline - time.perf_counter()
    def _play_guard_actions(self, actions: list[dict], hwnd: int | None,
                            hit: dict, source_screen: dict | None = None) -> None:
        """在播放器内联执行守卫处理段动作：注册脚本作用域、临时切换录制屏幕。

        处理段结束后恢复外层脚本的屏幕缩放上下文；作用域守卫随处理段退出。
        """
        scope = None
        saved_source = self._source_screen
        saved_target = self._target_screen
        self._guard_processing_depth += 1
        try:
            if self.on_script_scope_enter:
                scope = self.on_script_scope_enter(actions)
            if source_screen:
                self._source_screen = dict(source_screen)
                self._target_screen = get_playback_screen_rect(hwnd)
            self._run_action_sequence(actions, hwnd, depth=1)
        finally:
            self._source_screen = saved_source
            self._target_screen = saved_target
            self._guard_processing_depth -= 1
            if self.on_script_scope_exit and scope is not None:
                self.on_script_scope_exit(scope)
