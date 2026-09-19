from __future__ import annotations

from macroflow.execution.player import (
    JUMP_CURRENT_SCRIPT_LAST_RESULT, MAX_SCRIPT_REF_DEPTH,
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest,
    JumpToCurrentScriptLastAction, MacroPlayer, PlaybackStopped,
    screen_template_scale,
)
from macroflow.execution.detection_worker import DetectionEvaluation, DetectionWorker
from macroflow.core.storage import registered_module_object
from pathlib import Path
from macroflow.core.display_power import allow_display_sleep, keep_display_awake
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
import sys
import threading
import time
import tkinter as tk
import traceback
import ttkbootstrap as ttk

from .base import (
    floating_notice_xy,
    pad,
    px,
)
from .constants import (
    FLOATING_NOTICE_HEIGHT,
    FLOATING_NOTICE_WIDTH,
)
from macroflow.ui.dialogs.segments import module_action_for_key

class ExecutionMixin:
    """执行入口：F9、工作流、单独执行、执行小窗与收尾。"""

    def _enter_focus_mode(self, hwnd: int | None, enabled: bool = True) -> bool:
        if not force_english_input(hwnd):
            raise RuntimeError("无法切换到英语输入法，已取消执行。")
        if not enabled:
            self._ui(self._log, "已切换英语输入法；强制专注模式未开启，实体键鼠不会被锁定。")
            return False
        # 每次进入专注模式前重新同步守卫的快捷键集合：绑定可能在最近一次
        # 同步后变化（或上次同步丢失），守卫钩子只按这个集合识别快捷键。
        self._apply_hotkey_bindings()
        if not self.input_guard.start():
            raise RuntimeError("无法启动专注模式，已取消执行以避免误触。")
        if not self.input_guard.block():
            self.input_guard.stop()
            raise RuntimeError("系统级输入锁定失败，请尝试以管理员身份运行软件。")
        if self.hotkey_scripts:
            names = "，".join(
                f"{item.get('key', '?')}→{Path(str(item.get('script', ''))).stem}"
                for item in self.hotkey_scripts
            )
            self._ui(self._log, f"专注模式快捷键（守卫钩子识别触发）：{names}")
        # 专注模式下输入全部由守卫钩子线程发出，发之前必须把目标窗口抢回
        # 前台：否则焦点被抢（弹窗/误点桌面/小窗闪现）时按键会发给别的前台
        # 窗口，游戏收不到，表现就是“某个键没反应”。回调在钩子线程上执行。
        self.input_guard.set_before_input(self._restore_input_focus)
        self._ui(self._log, "已切换英语输入法并进入强制专注模式；桌面及游戏原始键鼠输入均已锁定，仅 F12 可紧急停止。")
        return True
    def _restore_input_focus(self) -> None:
        """专注重放期间：目标窗口不在前台就先抢回来，再发这次输入。

        只在目标进程确实不在前台时才激活——本来就在前台时不做任何操作，
        避免多余的 SetForegroundWindow 让游戏弹“点击游戏画面继续操作”。
        """
        player = getattr(self, "player", None)
        if player is None or not getattr(player, "_activate_target", False):
            return
        hwnd = getattr(self, "bound_window", None)
        hwnd = int(hwnd.hwnd) if hwnd is not None else None
        if not hwnd or not is_window(hwnd) or is_window_process_foreground(hwnd):
            return
        player._ensure_foreground_for_input(self._bound_hwnd(update_display=False))
    def _leave_focus_mode(self) -> None:
        guard = getattr(self, "input_guard", None)
        if guard is not None:
            guard.set_before_input(None)
            guard.release()
    def _set_execution_progress(self, text: str) -> None:
        self.execution_progress_text = text
        if getattr(self, "mini_mode", "") == "execution":
            self.mini_count_var.set(text)
    def _on_ocr_progress(self, stage: str, percent: int) -> None:
        """Update OCR warmup progress in the execution mini window."""
        value = max(0, min(100, int(percent)))
        text = f"OCR：{stage} · {value}% · F12 停止"
        self.execution_progress_text = text
        progress_var = getattr(self, "mini_ocr_progress_var", None)
        if progress_var is not None:
            progress_var.set(value)
        if getattr(self, "mini_mode", "") == "execution":
            self.mini_count_var.set(text)
    def _reset_execution_clock_for_new_run(self, resume_action_index: int | None) -> None:
        """Start at zero only for a newly requested run, never for internal resume."""
        if resume_action_index is not None:
            return
        self.execution_started_at = time.perf_counter()
        self.mini_elapsed_var.set("00:00")
    def _begin_detection_run(self) -> None:
        self._detection_run_id = getattr(self, "_detection_run_id", 0) + 1
        self._detection_request = None
    def _ensure_detection_worker(self) -> None:
        worker = getattr(self, "_detection_worker", None)
        if worker is None or not worker.thread.is_alive():
            self._detection_worker = DetectionWorker(self._evaluate_global_guards_sync)
    def _shutdown_detection_worker(self) -> None:
        worker = getattr(self, "_detection_worker", None)
        if worker is not None:
            worker.close()
            self._detection_worker = None
    def _run_detection_entrypoint(self, callback, *args, **kwargs):
        try:
            return callback(*args, **kwargs)
        except BaseException:
            self._shutdown_detection_worker()
            raise
    def run_script_from_selected_action(self):
        selected = sorted(int(item) for item in self.action_tree.selection())
        if not selected:
            self._notify("从选中行运行", "请先选择一行动作。")
            return
        self.run_current_script(start_index=selected[0])
    def run_current_script(self, start_index: int = 0,
                           single_action_repeats: int | None = None,
                           segment: tuple[int, int] | None = None,
                           segment_repeats: int = 1):
        """执行当前脚本；single_action_repeats = 单独执行起始行，segment = 循环执行那一段。"""
        return self._run_detection_entrypoint(
            self._run_current_script_impl, start_index, single_action_repeats,
            segment=segment, segment_repeats=segment_repeats,
        )

    def run_module_object_test(self, module_key: str, repeats: int = 1):
        """Run one saved module as a standalone live-reference action."""
        return self._run_detection_entrypoint(
            self._run_module_object_test_impl, module_key, max(1, int(repeats)),
        )

    def _run_module_object_test_impl(self, module_key: str, repeats: int = 1):
        """Start a module-only run without changing the current script or workflow."""
        module_obj = registered_module_object(str(module_key))
        if module_obj is None:
            self._notify("模块不存在", f"找不到模块：{module_key}")
            return
        if self.recorder.running:
            self.stop_recording()
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "已有脚本或工作流正在执行。")
            return

        category = str(module_obj.get("category") or "switch")
        # 全局模块在独立测试时按普通识别动作执行一次完整识别/动作链；否则注册
        # 为脚本守卫后会一直常驻，且已禁用模块会在注册阶段被过滤，无法诊断。
        action_category = "special" if category == "special" else "switch"
        action = module_action_for_key(module_key, action_category, module_obj)
        name = str(module_obj.get("name") or "").strip() or Path(
            str(module_obj.get("template") or module_key).replace("\\", "/"),
        ).stem or "未命名模块"
        repeats = max(1, int(repeats))

        self._begin_detection_run()
        self._ensure_detection_worker()
        hwnd = self._bound_hwnd()
        activation_enabled, activation_signature = self._activation_settings_from_script()
        activation_hwnd = None
        try:
            activation_hwnd = self._execution_activation_hwnd(
                hwnd, activation_enabled, activation_signature,
            )
        except RuntimeError:
            self._log("前置窗口未打开，已跳过前置窗口，继续执行模块测试。")
        focus_enabled = bool(self.focus_mode_enabled_var.get())
        activate_target = bool(self.activate_target_enabled_var.get())
        self.execution_focus_requested = focus_enabled
        source_screen = dict(self.script.settings.get("recorded_screen", {})) or None
        self.workflow_stop.clear()
        self.execution_started_at = time.perf_counter()
        self._set_execution_progress(
            f"模块测试 · {name} · 共执行 {repeats} 次 · 正在准备 · F12 停止"
        )
        self.worker = threading.Thread(
            target=self._run_script_worker,
            args=([action], repeats, hwnd, activation_hwnd, source_screen,
                  focus_enabled, activate_target, 0),
            kwargs={
                "trigger": {}, "script_name": f"模块测试：{name}",
                "single_action": True,
            },
            daemon=True,
        )
        self.worker.start()
        self._sound("run_start")
        self._hide_main_for_execution()
        self._show_execution_mini()
        self._append_mini_step(f"独立测试模块 {name}，共 {repeats} 次。")
    def _run_current_script_impl(self, start_index: int = 0,
                                 single_action_repeats: int | None = None,
                                 segment: tuple[int, int] | None = None,
                                 segment_repeats: int = 1):
        if self.recorder.running:
            self.stop_recording()
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "已有脚本或工作流正在执行。")
            return
        trigger = dict(self.script.settings.get("trigger") or {})
        if not self.script.actions and not trigger.get("template"):
            self._notify("没有动作", "请先录制或添加动作。")
            return
        total_actions = len(self.script.actions)
        # 片段：只跑「开头行 → 结尾行」这一段，次数是这一段循环几轮。
        # 与单独执行互斥——那是只跑一行，这是一段。
        segment_end = None
        if segment is not None:
            first, last = sorted((int(segment[0]), int(segment[1])))
            start_index = max(0, min(first, total_actions - 1))
            segment_end = max(start_index, min(last, total_actions - 1))
        start_index = max(0, min(int(start_index), total_actions - 1))
        self._begin_detection_run()
        self._ensure_detection_worker()
        # 单独执行某一行动作：次数是这一行的调用次数，其余动作一概不执行
        # （动作列表仍整份交给播放器，动作ID/跳转目标/脚本上下文都要在）。
        single_action = single_action_repeats is not None and segment_end is None
        if single_action:
            repeats = max(1, int(single_action_repeats))
        elif segment_end is not None:
            repeats = max(1, int(segment_repeats))
        else:
            repeats = max(1, int(self.repeat_var.get()))
        hwnd = self._bound_hwnd()
        activation_enabled, activation_signature = self._activation_settings_from_script()
        activation_hwnd = None
        try:
            activation_hwnd = self._execution_activation_hwnd(
                hwnd, activation_enabled, activation_signature,
            )
        except RuntimeError:
            self._log("前置窗口未打开，已跳过前置窗口，继续执行脚本。")
        focus_enabled = bool(self.focus_mode_enabled_var.get())
        activate_target = bool(self.activate_target_enabled_var.get())
        self.execution_focus_requested = focus_enabled
        source_screen = dict(self.script.settings.get("recorded_screen", {})) or None
        self.workflow_stop.clear()
        self.execution_started_at = time.perf_counter()
        if single_action:
            self._set_execution_progress(
                f"单独执行第 {start_index + 1}/{total_actions} 行动作 · "
                f"共 {repeats} 次 · 正在准备 · F12 停止"
            )
        elif segment_end is not None:
            self._set_execution_progress(
                f"循环执行片段 第 {start_index + 1}-{segment_end + 1}/{total_actions} 行 · "
                f"共 {repeats} 次 · 正在准备 · F12 停止"
            )
        else:
            start_note = f"从第 {start_index + 1}/{total_actions} 行开始 · " if start_index else ""
            self._set_execution_progress(f"当前脚本 · {start_note}共执行 {repeats} 次 · 正在准备 · F12 停止")
        self.worker = threading.Thread(
            target=self._run_script_worker,
            args=(list(self.script.actions), repeats, hwnd, activation_hwnd,
                  source_screen, focus_enabled, activate_target, start_index),
            kwargs={"trigger": trigger, "script_name": self.script.name,
                    "single_action": single_action, "segment_end": segment_end},
            daemon=True,
        )
        # 先启动执行线程再收尾 UI：输入法切换/输入锁定与托盘隐藏、提示音
        # 并行进行，按下 F9 后首个动作尽快开始。
        self.worker.start()
        self._sound("run_start")
        self._hide_main_for_execution()
        self._show_execution_mini()
        if single_action:
            self._append_mini_step(
                f"单独执行第 {start_index + 1}/{total_actions} 行动作，共 {repeats} 次。"
            )
        elif segment_end is not None:
            self._append_mini_step(
                f"循环执行片段 第 {start_index + 1}-{segment_end + 1}/{total_actions} 行，"
                f"共 {repeats} 次。"
            )
        elif start_index:
            self._append_mini_step(
                f"从第 {start_index + 1}/{total_actions} 行开始执行，重复 {repeats} 次。"
            )
        else:
            self._append_mini_step(f"开始执行当前脚本，重复 {repeats} 次。")
    def _run_script_worker(self, actions, repeats, hwnd, activation_hwnd, source_screen,
                           focus_enabled, activate_target, start_index=0, trigger=None,
                           script_name: str = "", single_action: bool = False,
                           segment_end: int | None = None):
        script_label = str(script_name).strip() or "未命名脚本"
        segment_mode = segment_end is not None
        # 明细日志的层级标题：先写明是哪个脚本、共几行、执行几次，再往下逐行记。
        self._set_trace_context(
            step=0, steps=0, script=script_label,
            total=len(actions), repeat=0, repeats=repeats,
        )
        self._ui(self._set_status, "正在执行脚本…", "warning")
        if single_action:
            self._ui(
                self._log,
                f"单独执行第 {start_index + 1}/{len(actions)} 行动作：{script_label}，共 {repeats} 次。",
            )
        elif segment_mode:
            self._ui(
                self._log,
                f"循环执行片段：{script_label} 第 {start_index + 1}-{segment_end + 1} 行，"
                f"共 {repeats} 次。",
            )
        elif start_index:
            self._ui(
                self._log,
                f"从第 {start_index + 1}/{len(actions)} 行开始执行脚本：{script_label}，重复 {repeats} 次。",
            )
        else:
            self._ui(self._log, f"开始执行脚本：{script_label}，重复 {repeats} 次。")
        try:
            # 执行期间必须阻止屏保/熄屏：等待识图只截屏不发输入，空闲计满屏保
            # 时间后屏保会接管显示，此后 BitBlt 对普通进程返回「拒绝访问」，
            # 截图全部失败。声明挂在本线程上，执行结束（含报错/F12）一并撤销。
            if not keep_display_awake():
                self._ui(
                    self._log,
                    "无法向系统声明「保持显示器点亮」：长时间等待识图时可能被"
                    "屏保 / 熄屏打断，建议把屏保等待时间调长。",
                )
            activation_prepared = self._activate_execution_window_before_ocr(activation_hwnd)
            # 专注模式（输入法切换 + 系统输入锁）先于 OCR 等待生效：
            # 按下 F9 后输入立即锁定，不存在“提示正在执行却还能动鼠标”
            # 的窗口期。
            self._enter_focus_mode(activation_hwnd or hwnd, focus_enabled)
            # 首次 OCR 引擎导入可能耗时数十秒且不可中断：仅在脚本动作树
            # 可能用到文字识别时提前等待（等待期间按 F12 会中止执行），
            # 纯键鼠/模板匹配脚本跳过等待立即开始。
            if self._script_needs_ocr(actions) and not self._ensure_ocr_ready():
                return
            if single_action:
                progress_prefix = f"单独执行第 {start_index + 1}/{len(actions)} 行 · "
            elif segment_mode:
                progress_prefix = (
                    f"循环执行片段 第 {start_index + 1}-{segment_end + 1} 行 · "
                )
            else:
                progress_prefix = "当前脚本 · "
                if start_index:
                    progress_prefix += f"从第 {start_index + 1}/{len(actions)} 行 · "
            self.player.play(
                actions, repeats, hwnd, source_screen=source_screen,
                script_name=script_label,
                activate_target=activate_target, activation_hwnd=activation_hwnd,
                activation_prepared=activation_prepared,
                start_index=start_index,
                single_action=single_action,
                segment_end=segment_end,
                on_repeat=lambda current, total: self._ui(
                    self._set_execution_progress,
                    f"{progress_prefix}"
                    f"共执行 {total} 次 · 当前第 {current}/{total} 次 · F12 停止",
                ),
            )
            # 单独执行 / 片段循环都是有界的试跑：不做“按脚本触发条件持续检测”的
            # 全局脚本收尾。
            if not self.player.stop_event.is_set() and not single_action and not segment_mode:
                if trigger and str(trigger.get("template", "")).strip():
                    # 全局脚本：播放完成后不结束，保持守卫检测直到停止；
                    # 触发条件满足时在播放器内联重新执行语句体（脚本内的所有动作）。
                    self.standalone_global_replay = {
                        "actions": list(actions),
                        "hwnd": hwnd,
                        "activation_hwnd": activation_hwnd,
                        "source_screen": source_screen,
                        "activate_target": activate_target,
                    }
                    self._activate_global_detect_from_config(
                        dict(trigger), standalone_replay=self.standalone_global_replay,
                    )
                    self._ui(
                        self._set_status,
                        "全局检测运行中 · 触发后执行脚本动作 · F12 停止",
                        "warning",
                    )
                    self._ui(
                        self._append_mini_step,
                        "全局检测已启用，持续检测中：触发后执行脚本动作。",
                    )
                    self._ui(
                        self._log,
                        "全局检测已启用，持续检测中：触发后执行脚本动作，按 F12 停止。",
                    )
                    while not self.player.stop_event.is_set():
                        hit = self._evaluate_global_guards()
                        if hit is not None:
                            try:
                                self.player.handle_guard_hit(hit)
                            except PlaybackStopped:
                                break
                            except (EndCurrentScriptRequest, JumpToCurrentScriptLastAction,
                                    AdvanceToNextWorkflowStep, GuardJumpRequest):
                                self._ui(self._log, "全局脚本已按处理段要求结束。")
                                break
                            continue
                        self.player.stop_event.wait(0.1)
                    # 这段守卫循环不经过 play()，处理段里按下的键/鼠标键没有
                    # 收尾路径，退出前统一释放。
                    self.player._release_all(None)
                else:
                    self._clear_trace_context()
                    self._ui(self._append_mini_step, "脚本执行完成。")
                    self._ui(self._set_status, "脚本执行完成", "success")
                    self._ui(self._log, "脚本执行完成。")
                    self._ui(self._sound, "run_done")
        except Exception as exc:
            self._ui(self._handle_worker_error, "脚本执行失败", exc)
        finally:
            allow_display_sleep()
            self.standalone_global_replay = None
            self._clear_global_guards()
            self._shutdown_detection_worker()
            self._leave_focus_mode()
            self._ui(self._finish_execution_visibility)
    def _player_status_callback(self, text: str):
        self._ui(self._set_status, text, "warning")
        self._ui(self._append_mini_step, text)
    def _notify(self, title: str, text: str, duration_ms: int = 4500):
        self._show_execution_notice(f"{title}：{text}", duration_ms)
    def _player_notice_callback(self, text: str, duration_ms: int):
        self._ui(self._show_execution_notice, text, duration_ms)
    def _show_execution_notice(self, text: str, duration_ms: int):
        try:
            keep_main_hidden = self.root.state() == "withdrawn" or self.main_hidden_for_execution
        except (AttributeError, tk.TclError):
            keep_main_hidden = False
        try:
            position = self.floating_notice_position_var.get()
        except (AttributeError, tk.TclError):
            position = "顶部居中"
        area = get_monitor_work_area_for_window(self._app_window_hwnd()) \
            or get_primary_screen_rect()
        existing = self.execution_notice_window
        if existing is not None and existing.winfo_exists():
            self.execution_notice_label.configure(text=text)
            x, y = floating_notice_xy(
                position, area["width"], area["height"],
                px(FLOATING_NOTICE_WIDTH), px(FLOATING_NOTICE_HEIGHT),
            )
            x += area["left"]
            y += area["top"]
            existing.geometry(f"{px(FLOATING_NOTICE_WIDTH)}x{px(FLOATING_NOTICE_HEIGHT)}+{x}+{y}")
            if self.execution_notice_after_id is not None:
                existing.after_cancel(self.execution_notice_after_id)
            existing.deiconify()
            existing.lift()
            if keep_main_hidden:
                self.root.withdraw()
            self.execution_notice_after_id = existing.after(
                duration_ms, lambda window=existing: self._close_execution_notice(window),
            )
            return
        notice = tk.Toplevel(self.root)
        self.execution_notice_window = notice
        notice.withdraw()
        notice.overrideredirect(True)
        notice.attributes("-topmost", True)
        notice.configure(background="#263541")
        width, height = px(FLOATING_NOTICE_WIDTH), px(FLOATING_NOTICE_HEIGHT)
        x, y = floating_notice_xy(position, area["width"], area["height"], width, height)
        notice.geometry(f"{width}x{height}+{x + area['left']}+{y + area['top']}")
        set_rounded_window(notice.winfo_id(), px(10))
        frame = ttk.Frame(notice, padding=pad(12, 10), style="Surface.TFrame")
        frame.pack(fill="both", expand=True)
        self.execution_notice_label = ttk.Label(
            frame, text=text, style="MiniText.TLabel", wraplength=px(330), justify="left",
        )
        self.execution_notice_label.pack(anchor="w", fill="both", expand=True)
        notice.update_idletasks()
        make_window_no_activate(notice.winfo_id())
        notice.deiconify()
        notice.lift()
        if keep_main_hidden:
            self.root.withdraw()
        self.execution_notice_after_id = notice.after(
            duration_ms, lambda window=notice: self._close_execution_notice(window),
        )
    def _close_execution_notice(self, notice):
        if notice is not self.execution_notice_window:
            return
        self.execution_notice_window = None
        self.execution_notice_label = None
        self.execution_notice_after_id = None
        try:
            notice.destroy()
        except tk.TclError:
            pass
    def _handle_worker_error(self, title: str, exc: Exception):
        self._set_status(str(exc), "error")
        self._log(f"{title}：{exc}")
        self._append_mini_step(f"{title}：{exc}")
        self._log("执行异常收尾：即将关闭执行小窗并恢复主界面。")
        self._clear_global_guards()
        self._finish_execution_visibility()
        self._sound("error")
        self._notify(title, str(exc), 6000)
    def stop_all(self, from_ui: bool = False):
        if self.recorder.running:
            self.stop_recording(discard_recent=from_ui, sound=False)
        self.workflow_stop.set()
        self.player.stop()
        hotkey_player = getattr(self, "hotkey_player", None)
        if hotkey_player is not None:
            hotkey_player.stop()
        self.workflow_restart_requested = False
        self._clear_global_detect_cooldowns()
        self._clear_global_guards()
        # F12 必须立即解除 BlockInput，不能等待工作流/全局模块线程自然退出。
        self._leave_focus_mode()
        self._finish_execution_visibility()
        self._set_status("已发送停止指令", "warning")
        self._log("用户触发紧急停止。")
        self._sound("emergency_stop")
        # 诊断：worker 若未在限定时间内退出（卡在 OCR/截图/长文本等不可
        # 中断的调用里），把它的线程堆栈写进日志，下次遇到即可定位卡点。
        worker = self.worker
        if worker is not None and worker.is_alive():

            def _report_stuck_worker():
                if not worker.is_alive():
                    return
                frame = sys._current_frames().get(worker.ident)
                if frame is not None:
                    stack = "".join(traceback.format_stack(frame))
                else:
                    stack = "（无法获取线程堆栈，线程可能在原生代码中）"
                self._log(
                    "紧急停止后工作线程仍在运行，可能卡在不可中断的操作上：\n"
                    + stack
                )

            self.root.after(3000, _report_stuck_worker)
