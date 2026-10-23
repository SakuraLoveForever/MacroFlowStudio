from __future__ import annotations
from macroflow.execution.recovery import CaptureUnavailable

from macroflow.core.models import DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS, END_CURRENT_SCRIPT_LABEL, MacroScript
from macroflow.execution.player import AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest, JumpToCurrentScriptLastAction, PlaybackStopped
from macroflow.ui.dialogs.helpers import workflow_step_label
from pathlib import Path
from macroflow.core.storage import load_script, registered_module_object, resolve_path, save_workflow
from macroflow.core.display_power import allow_display_sleep, keep_display_awake
from datetime import datetime
import threading

from ..base import workflow_execution_progress, workflow_script_name

# 已经量过列宽的 (tree, column)：普通编辑刷新不再重测整列文本。



from macroflow.core.models import DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS, MacroScript

from ..base import workflow_script_name

# 已经量过列宽的 (tree, column)：普通编辑刷新不再重测整列文本。


class RuntimeWorkflowMixin:
    """Shared workflow behavior used by both clients."""

    def _global_module_steps(self) -> list[dict]:
        return [step for step in self.workflow.steps if step.get("kind") == "global_module"]

    def _workflow_only_steps(self) -> list[dict]:
        return [step for step in self.workflow.steps if step.get("kind") != "global_module"]

    @staticmethod
    def _workflow_module_key(step: dict) -> str:
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        return str(action.get("module_key") or action.get("template") or "").strip()

    def _workflow_module_enabled(self, step: dict, module_objects: dict | None = None) -> bool:
        """Whether the module registry currently allows this workflow row."""
        if step.get("kind") != "module":
            return True
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        if str(action.get("type", "")) in {
            "restart_workflow", "end_current_script", "jump_current_script_last",
        }:
            return True
        module_obj = self._module_object(self._workflow_module_key(step), module_objects)
        return bool(module_obj and module_obj.get("enabled", True))

    @staticmethod
    def _module_object(module_key: str, module_objects: dict | None = None):
        """查模块对象：给了本次刷新的快照就查快照，否则读一次仓库。"""
        if module_objects is not None:
            return module_objects.get(module_key)
        return registered_module_object(module_key)

    def _workflow_step_name(self, step: dict, module_objects: dict | None = None) -> str:
        if step.get("kind") != "module":
            return workflow_script_name(step.get("script", "")) or "未设置脚本"
        module_key = self._workflow_module_key(step)
        module_obj = self._module_object(module_key, module_objects)
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        special_type = str(action.get("type", ""))
        if special_type == "restart_workflow":
            return "重新执行工作流"
        if special_type == "end_current_script":
            return END_CURRENT_SCRIPT_LABEL
        if special_type == "jump_current_script_last":
            return "跳转到当前脚本最后一行"
        name = (
            str(action.get("module_name", "")).strip()
            or (str(module_obj.get("name", "")).strip() if module_obj else "")
            or Path(module_key.replace("\\", "/")).stem
        )
        return f"模块 {name or '未设置'}"

    def _workflow_restart_default_options(self) -> tuple[list[str], dict[str, int]]:
        """工作流页「重新执行默认跳转行」下拉选项：未设置 + 各步骤行。"""
        labels = ["（未设置：按第 1 行）"]
        mapping = {labels[0]: 0}
        for index, step in enumerate(self._workflow_only_steps()):
            label = f"第 {index + 1} 行 · {workflow_step_label(step)}"
            labels.append(label)
            mapping[label] = index + 1
        return labels, mapping

    def _sync_workflow_restart_default_ui(self):
        """按当前工作流刷新默认跳转行控件（行列表变化 / 打开 / 新建时调用）。"""
        combo = getattr(self, "workflow_restart_default_combo", None)
        if combo is None:
            return
        labels, mapping = self._workflow_restart_default_options()
        self.workflow_restart_default_ids = mapping
        combo.configure(values=labels)
        row = max(0, int(getattr(self.workflow, "restart_default_row", 0) or 0))
        selected = next(
            (label for label, saved in mapping.items() if saved == row),
            labels[0],  # 保存的行号不在当前行列表里时按未设置显示（运行时仍会收敛）。
        )
        combo.set(selected)

    @staticmethod
    def _script_trigger_config(script) -> dict:
        """全局脚本的触发条件：优先 settings["trigger"]，旧脚本回退扫描全局检测动作。

        v1.68 起普通脚本可内嵌全局模块行（带 jump_row），它们不是触发条件，
        回退扫描时跳过。
        """
        config = dict(script.settings.get("trigger") or {})
        if not config:
            for action in script.actions:
                if str(action.get("type")) == "global_detect" and "jump_row" not in action:
                    return dict(action)
        return config

    def _consume_workflow_repeat(self, index: int, test_mode: bool | None = None) -> int:
        """扣减一行工作流的重复次数并返回剩余次数（供调用方判定是否继续）。

        返回语义：测试模式 / 不计次数 / 越界返回 0；正常扣减返回剩余次数。
        """
        workflow_steps = self._workflow_only_steps()
        if not 0 <= index < len(workflow_steps):
            return 0
        step = workflow_steps[index]
        if test_mode is None:
            test_mode = bool(getattr(self, "workflow_test_mode_active", False))
        if test_mode:
            self._log(f"工作流第 {index + 1} 行测试完成一次（测试模式，不扣减次数）。")
            return 0
        if bool(step.get("unlimited", False)):
            self._log(f"工作流第 {index + 1} 行完成一次（不计次数，不扣减）。")
            return 0
        remaining = max(0, int(step.get("repeats", 0)) - 1)
        step.setdefault("original_repeats", int(step.get("repeats", 0)))
        step["repeats"] = remaining
        self._refresh_one_workflow_row(index)
        self._persist_workflow_draft(record_history=False)
        if self.workflow_path is not None:
            try:
                save_workflow(self.workflow, self.workflow_path)
            except Exception as exc:
                self._log(f"自动保存工作流剩余次数失败：{exc}")
        state_note = "，次数已用完，下次将自动跳过" if remaining == 0 else ""
        self._log(f"工作流第 {index + 1} 行成功完成一次，剩余 {remaining} 次{state_note}。")
        return remaining

    def _consume_workflow_repeat_from_worker(
        self, index: int, test_mode: bool | None = None,
    ) -> int:
        """在播放器线程先提交剩余次数，再把界面刷新排入 Tk 主线程。

        工作流播放器运行在后台线程；如果把整个扣减操作通过 ``root.after``
        异步排队，播放器可能已经返回并开始下一步，而当前步骤的剩余次数
        仍停留在旧值。次数状态必须先更新，Tk 控件刷新和草稿持久化随后执行。
        """
        workflow_steps = self._workflow_only_steps()
        if not 0 <= index < len(workflow_steps):
            return 0
        step = workflow_steps[index]
        if test_mode is None:
            test_mode = bool(getattr(self, "workflow_test_mode_active", False))
        if test_mode:
            self._ui(
                self._log,
                f"工作流第 {index + 1} 行测试完成一次（测试模式，不扣减次数）。",
            )
            return 0
        if bool(step.get("unlimited", False)):
            self._ui(
                self._log,
                f"工作流第 {index + 1} 行完成一次（不计次数，不扣减）。",
            )
            return 0

        remaining = max(0, int(step.get("repeats", 0)) - 1)
        step.setdefault("original_repeats", int(step.get("repeats", 0)))
        step["repeats"] = remaining
        workflow_path = getattr(self, "workflow_path", None)
        recovery = getattr(self, "_workflow_recovery", None)
        if recovery is not None:
            recovery.checkpoint()
        if workflow_path is not None:
            try:
                save_workflow(self.workflow, workflow_path)
            except Exception as exc:
                self._ui(self._log, f"自动保存工作流剩余次数失败：{exc}")

        state_note = "，次数已用完，下次将自动跳过" if remaining == 0 else ""
        self._ui(
            self._log,
            f"工作流第 {index + 1} 行成功完成一次，剩余 {remaining} 次{state_note}。",
        )
        self._ui(self._refresh_workflow_repeat_ui, index)
        return remaining

    def _refresh_workflow_repeat_ui(self, index: int | None = None) -> None:
        """Refresh workflow controls after a worker-thread repeat is consumed."""
        if index is not None:
            self._refresh_one_workflow_row(index)
        else:
            self.rebuild_workflow_tree()
        self._persist_workflow_draft(record_history=False)

    @staticmethod
    def _workflow_global_module_key(step: dict) -> str:
        config = step.get("config")
        if not isinstance(config, dict) or not config.get("module_ref"):
            return ""
        return str(config.get("module_key") or config.get("template", "")).strip()

    def _workflow_global_module_registry_state(self, step: dict) -> str | None:
        """Return the live registry state of a referenced workflow-global module."""
        module_key = self._workflow_global_module_key(step)
        if not module_key:
            return None
        module_obj = registered_module_object(module_key)
        if module_obj is None:
            return "missing"
        return "enabled" if bool(module_obj.get("enabled", True)) else "disabled"

    def _read_workflow_start_delay(self, *, validate: bool) -> int | None:
        enabled_var = getattr(self, "workflow_start_delay_enabled_var", None)
        seconds_var = getattr(self, "workflow_start_delay_seconds_var", None)
        enabled = bool(enabled_var.get()) if enabled_var is not None else False
        raw = seconds_var.get().strip() if seconds_var is not None else "5000"
        try:
            milliseconds = int(raw)
            if milliseconds < 0 or milliseconds > 86400000:
                raise ValueError
            # 存储精度是整秒：向上取整保证亚秒延时不被 round 截断成 0
            # （500ms → 1s，2500ms → 3s），延时只多不少。
            seconds = -(-milliseconds // 1000)
        except (TypeError, ValueError):
            if validate:
                self._notify("启动延时无效", "启动延时请输入 0–86400000 ms（1440 分钟）以内的时间。")
                return None
            workflow = getattr(self, "workflow", None)
            seconds = int(getattr(workflow, "start_delay_seconds", 5))
        workflow = getattr(self, "workflow", None)
        if workflow is not None:
            workflow.start_delay_enabled = enabled
            workflow.start_delay_seconds = seconds
        return seconds if enabled else 0

    def run_workflow(self, start_index: int = 0, start_repeat: int = 0,
                     resume_action_index: int | None = None,
                     preserve_global_cooldowns: bool = False,
                     test_mode: bool | None = None,
                     suppress_start_sound: bool = False,
                     segment: tuple[int, int] | None = None,
                     segment_repeats: int = 1,
                     partial_run: bool = False):
        """执行工作流；segment = 只循环执行「开头行 → 结尾行」这一段。"""
        return self._run_detection_entrypoint(
            self._run_workflow_impl, start_index, start_repeat,
            resume_action_index, preserve_global_cooldowns, test_mode,
            suppress_start_sound, segment=segment, segment_repeats=segment_repeats,
            partial_run=partial_run,
        )

    def _run_workflow_impl(self, start_index: int = 0, start_repeat: int = 0,
                      resume_action_index: int | None = None,
                      preserve_global_cooldowns: bool = False,
                      test_mode: bool | None = None,
                      suppress_start_sound: bool = False,
                      segment: tuple[int, int] | None = None,
                      segment_repeats: int = 1,
                      partial_run: bool = False):
        recorder = getattr(self, "recorder", None)
        if recorder is not None and recorder.running:
            self.stop_recording()
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "已有脚本或工作流正在执行。")
            return
        continuing_recovery = (resume_action_index is not None
                               and getattr(self, "_workflow_recovery", None) is not None)
        automatic_generation = getattr(self, "_automatic_recovery_generation", None)
        if automatic_generation is not None \
                and automatic_generation != getattr(self, "_recovery_cancel_generation", 0):
            return
        with self._recovery_lock():
            self._cancel_workflow_recovery(manual=not getattr(self, "_automatic_restarting", False))
            launch_generation = (automatic_generation if automatic_generation is not None
                                 else getattr(self, "_recovery_cancel_generation", 0))
        self._save_activation_interval()
        workflow_steps = self._workflow_only_steps()
        global_modules = [dict(step) for step in self._global_module_steps()]
        partial_run = bool(partial_run or start_index or segment is not None)
        script_globals_enabled = (
            bool(self.partial_script_globals_var.get()) if partial_run else True
        ) if hasattr(self, "partial_script_globals_var") else True
        if partial_run and hasattr(self, "partial_workflow_globals_var") \
                and not self.partial_workflow_globals_var.get():
            global_modules = []
        if not workflow_steps and not global_modules:
            self._notify("没有步骤", "请先向工作流添加脚本或模块。")
            return
        if resume_action_index is None:
            self._begin_detection_run()
        self._ensure_detection_worker()
        # 片段：只跑「开头行 → 结尾行」这一段，轮次由用户指定。
        segment_end = None
        segment_start = None
        segment_rounds = max(1, int(segment_repeats))
        if segment is not None:
            first, last = sorted((int(segment[0]), int(segment[1])))
            segment_start = max(0, min(first, max(0, len(workflow_steps) - 1)))
            segment_end = max(
                segment_start, min(last, max(0, len(workflow_steps) - 1)),
            )
            start_index = segment_start if resume_action_index is None else max(
                segment_start, min(int(start_index), segment_end),
            )
        start_index = max(0, min(int(start_index), max(0, len(workflow_steps) - 1)))
        if resume_action_index is None:
            if test_mode is None:
                test_mode_var = getattr(self, "workflow_test_mode_var", None)
                test_mode = bool(test_mode_var.get()) if test_mode_var is not None else False
            self.workflow_test_mode_active = bool(test_mode)
            start_delay_seconds = self._read_workflow_start_delay(validate=True)
            if start_delay_seconds is None:
                self._shutdown_detection_worker()
                return
            if getattr(self, "_automatic_restarting", False):
                start_delay_seconds = 0
        else:
            # 全局模块断点恢复与“重新执行工作流”属于同一次运行，不重复等待。
            start_delay_seconds = 0
        self._workflow_snapshot()
        self._persist_workflow_draft()
        self.rebuild_workflow_tree()
        missing_rows = [
            index + 1 for index, step in enumerate(workflow_steps)
            if index >= start_index
            if segment_end is None or index <= segment_end
            if bool(step.get("enabled", True))
            if step.get("kind") != "module"
            if not resolve_path(step.get("script", "")).is_file()
        ]
        if missing_rows:
            row_text = "、".join(str(row) for row in missing_rows)
            self._set_status(f"第 {row_text} 行脚本不存在，执行时将跳过", "error")
            self._log(f"工作流缺失脚本：第 {row_text} 行；这些行将在执行时跳过。")
        start_text = self.workflow_start_var.get().strip()
        start_at = None
        if start_text and not getattr(self, "_automatic_restarting", False):
            try:
                start_at = datetime.strptime(start_text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                self._notify("时间格式错误", "请使用格式：2026-08-03 23:30:00")
                self._shutdown_detection_worker()
                return
        hwnd = self._bound_hwnd()
        # 当前侧栏选择作为整个工作流的默认前置窗口；步骤脚本若保存了自己的
        # 前置窗口，则在 _run_workflow_worker 中覆盖这个默认值。
        workflow_activation_enabled, workflow_activation_signature = self._activation_settings_from_script()
        editor_script = getattr(self, "script", None)
        workflow_activation_interval_ms = int(
            editor_script.settings.get("activation_window_interval_ms", 0)
            if editor_script is not None else 0
        )
        if start_index > 0 and start_index < len(workflow_steps):
            # 从选中行运行时，优先使用所选工作流脚本自己的前置窗口，
            # 而不是编辑器当前打开的另一份脚本配置。
            workflow_activation_enabled, workflow_activation_signature, workflow_activation_interval_ms = (
                self._activation_settings_from_workflow_step(workflow_steps[start_index])
            )
        activation_toggle = getattr(self, "activation_enabled_var", None)
        workflow_activation_allowed = (
            bool(activation_toggle.get()) if activation_toggle is not None
            else workflow_activation_enabled
        )
        if not workflow_activation_allowed:
            workflow_activation_enabled = False
            workflow_activation_signature = None
        try:
            workflow_activation_hwnd = self._execution_activation_hwnd(
                hwnd, workflow_activation_enabled, workflow_activation_signature,
            )
        except RuntimeError:
            workflow_activation_hwnd = None
            self._log("前置窗口未打开，已跳过前置窗口，继续执行工作流。")
        # 全局模块中断后的断点恢复仍属于同一次工作流，不能再次执行前置窗口。
        if resume_action_index is not None:
            workflow_activation_hwnd = None
        focus_enabled = bool(self.focus_mode_enabled_var.get())
        activate_target = bool(self.activate_target_enabled_var.get())
        # 侧栏“启用执行前置窗口”是本次执行的总开关：未勾选时，工作流步骤脚本
        # 自己保存的前置窗口也一律不激活（见 _run_workflow_worker 的步骤回退）。
        self.execution_focus_requested = focus_enabled
        # 每次开始前清空上一轮守卫（守卫生命周期 = 一次工作流运行），
        # 工作流全局模块随后由 worker 重新注册。
        self._clear_global_guards()
        if not preserve_global_cooldowns:
            self._clear_global_detect_cooldowns()
        if not suppress_start_sound:
            self._sound("run_start")
        self._hide_main_for_execution()
        # 只在用户/启动项真正开始一次工作流时归零。全局模块触发后的
        # 断点恢复属于同一次运行，必须沿用原始开始时间。
        self._reset_execution_clock_for_new_run(resume_action_index)
        steps = [dict(step) for step in workflow_steps]
        if segment_end is not None:
            initial_progress = (
                f"工作流片段 第 {segment_start + 1}-{segment_end + 1}/{len(steps)} 行 · "
                f"循环 {segment_rounds} 次 · 等待开始 · F12 停止"
            )
        elif start_index:
            initial_progress = f"工作流从第 {start_index + 1}/{len(steps)} 行开始 · 等待开始 · F12 停止"
        else:
            initial_progress = f"工作流 0/{len(steps)} · 等待开始 · F12 停止"
        self._set_execution_progress(initial_progress)
        self.worker = threading.Thread(
            target=self._run_workflow_worker,
            args=(steps, start_at, hwnd, focus_enabled,
                  activate_target, start_index, global_modules,
                  start_repeat, resume_action_index, workflow_activation_hwnd,
                  self.workflow_test_mode_active, start_delay_seconds,
                  workflow_activation_allowed, segment_end, segment_rounds),
            kwargs={"workflow_activation_interval_ms": workflow_activation_interval_ms,
                    "script_globals_enabled": script_globals_enabled,
                    "segment_start": segment_start},
            daemon=True,
        )
        # Recovery belongs only to a complete production workflow. A test or
        # selected fragment must never silently expand into the full workflow.
        if (not partial_run or continuing_recovery) and not self.workflow_test_mode_active \
                and hasattr(self, "session_log_path"):
            self._arm_workflow_recovery()
        # F12 and tray Quit share this short critical section with starting the
        # replacement, so a manual stop cannot be cleared by a delayed handoff.
        with self._recovery_lock():
            cancelled = launch_generation != getattr(self, "_recovery_cancel_generation", 0)
            if not cancelled:
                self.workflow_stop.clear()
                if getattr(self, "player", None) is not None:
                    self.player.reset()
                self.worker.start()
        if cancelled:
            self._cancel_workflow_recovery(manual=False)
            self._shutdown_detection_worker()
            self._finish_execution_visibility()
            return
        self._show_execution_mini()
        if self.workflow_test_mode_active:
            self._append_mini_step("工作流测试模式：普通计次行最多执行 1 次，且不扣减剩余次数。")
            self._log("工作流测试模式已启用：普通计次行最多执行 1 次；不计次数行保持原样。")
        if segment_end is not None:
            segment_text = (
                f"循环执行片段：工作流第 {segment_start + 1}-{segment_end + 1} 行，"
                f"共 {segment_rounds} 轮（每轮按顺序各执行一次，不扣减各行剩余次数）。"
            )
            self._append_mini_step(segment_text)
            self._log(segment_text)
        elif start_index:
            self._append_mini_step(f"从第 {start_index + 1}/{len(steps)} 行开始执行工作流。")
        else:
            self._append_mini_step(f"开始执行工作流，共 {len(steps)} 个步骤。")

    def _run_workflow_worker(self, steps, start_at, hwnd,
                             focus_enabled=False, activate_target=True,
                             start_index=0, global_modules=None, start_repeat=0,
                             resume_action_index=None, workflow_activation_hwnd=None,
                             test_mode=False, start_delay_seconds=0,
                             activation_allowed=True, segment_end=None,
                             segment_repeats=1,
                             workflow_activation_interval_ms=0,
                             script_globals_enabled=True, segment_start=None):
        workflow = getattr(self, "workflow", None)
        start_resolution = dict(getattr(workflow, "start_resolution", None) or {})
        end_resolution = dict(getattr(workflow, "end_resolution", None) or {})
        display_run_started = False
        try:
            if start_at and start_at > datetime.now():
                seconds = (start_at - datetime.now()).total_seconds()
                self._ui(self._set_status, f"工作流已排期：{start_at:%m-%d %H:%M:%S}", "warning")
                self._ui(self._log, f"工作流等待至 {start_at:%Y-%m-%d %H:%M:%S} 开始。")
                if not self._guard_wait(seconds):
                    return
            if start_delay_seconds > 0:
                delay_text = f"工作流启动延时 {start_delay_seconds} 秒，等待结束后执行。"
                self._ui(self._set_status, delay_text, "warning")
                self._ui(self._set_execution_progress, f"{delay_text} · F12 停止")
                self._ui(self._append_mini_step, delay_text)
                self._ui(self._log, delay_text)
                if not self._guard_wait(start_delay_seconds):
                    return
                self._ui(self._log, "工作流启动延时结束，开始执行。")
            if self.workflow_stop.is_set():
                return
            display_run_started = True
            if start_resolution:
                self._apply_workflow_resolution(start_resolution, "启动")
                if self.workflow_stop.is_set():
                    return
            # 执行期间必须阻止屏保/熄屏（工作流整轮都算执行期：步骤之间还有
            # 开始前等待与重复间隔，等待中屏保一起来同样会让截图全部失败）。
            if not keep_display_awake():
                self._ui(
                    self._log,
                    "无法向系统声明「保持显示器点亮」：长时间等待识图时可能被"
                    "屏保 / 熄屏打断，建议把屏保等待时间调长。",
                )
            activation_prepared = False
            if resume_action_index is None:
                activation_prepared = self._activate_execution_window_before_ocr(
                    workflow_activation_hwnd,
                )
                # 专注模式（切换英语输入法 + 系统输入锁）只在工作流首次开始时
                # 执行一次。全局模块中断后的断点恢复、特殊模块“重新执行工作流”
                # 都沿用第一次建立的输入锁，不再重复切换输入法或重新锁定，
                # 避免中途重置输入状态。专注模式先于 OCR 等待生效：启动后
                # 输入立即锁定，不存在“提示正在执行却还能动鼠标”的窗口期。
                self._enter_focus_mode(hwnd, focus_enabled)
            else:
                self._ui(
                    self._log,
                    "继续执行：沿用已开启的强制专注模式，不再重复设置输入法/输入锁。",
                )
            # 首次 OCR 引擎导入可能耗时数十秒且不可中断：仅在工作流任一
            # 脚本/模块可能用到文字识别时提前等待（等待期间按 F12 会中止
            # 执行）；纯键鼠/模板匹配工作流跳过等待立即开始。
            if self._workflow_needs_ocr(steps, global_modules) and not self._ensure_ocr_ready():
                return
            start_index = max(0, min(int(start_index), max(0, len(steps) - 1)))
            # 片段循环：只跑「开头行 → 结尾行」这一段，并按轮重复；每轮里每一行
            # 各执行一次，各行的剩余次数不参与也不扣减。
            segment_mode = segment_end is not None
            last_index = len(steps) - 1 if not segment_mode else max(
                start_index, min(int(segment_end), max(0, len(steps) - 1)),
            )
            segment_rounds = max(1, int(segment_repeats)) if segment_mode else 1
            first_index = start_index if segment_start is None else max(
                0, min(int(segment_start), start_index),
            )
            self.workflow_segment = (first_index, last_index) if segment_mode else None
            self.workflow_segment_rounds_remaining = segment_rounds
            if segment_mode and resume_action_index is None:
                self.workflow_segment_total_rounds = segment_rounds
            self.current_workflow_step_index = start_index if steps else None
            self.current_workflow_repeat_index = 0
            self.current_workflow_action_index = 0
            played_any_step = False
            if start_index:
                self._ui(self._log, f"从第 {start_index + 1}/{len(steps)} 行开始执行工作流。")
            else:
                self._ui(self._log, f"开始执行工作流，共 {len(steps)} 个步骤。")
            counted_steps = [
                step for step in steps[start_index:]
                if bool(step.get("enabled", True)) and not bool(step.get("unlimited", False))
                and self._workflow_module_enabled(step)
            ]
            if not test_mode and not segment_mode and counted_steps \
                    and all(int(step.get("repeats", 1)) <= 0 for step in counted_steps):
                # 所有计次脚本都已执行完毕：整个工作流结束，不再循环执行不计次数脚本。
                self._clear_global_guards()
                self._ui(self._set_status, "工作流执行完成", "success")
                self._ui(self._append_mini_step, "所有计次脚本已执行完毕，工作流结束。")
                self._ui(self._log, "所有计次脚本已执行完毕，工作流结束。")
                self._ui(self._sound, "run_done")
                return
            if not self._register_workflow_global_modules(global_modules or []):
                return
            # “执行前置窗口”是整个工作流的一次性准备动作，只交给第一个实际
            # 执行的脚本。后续脚本以及全局模块断点恢复都直接以目标窗口为准。
            pending_activation_hwnd = workflow_activation_hwnd
            pending_activation_prepared = activation_prepared
            pending_activation_at = (
                getattr(self, "_activation_prepared_at", None) if activation_prepared else None
            )
            activation_consumed = False
            # 片段循环的执行顺序摊平成一张计划表：每轮从开头行到结尾行各走一遍。
            # 不循环时（整轮执行 / 从选中行执行）就是原来的 start_index → 末尾。
            step_plan = [
                (round_index, index)
                for round_index in range(segment_rounds)
                for index in range(start_index if round_index == 0 else first_index, last_index + 1)
            ]
            reported_round = -1
            for round_index, index in step_plan:
                self.workflow_segment_rounds_remaining = segment_rounds - round_index
                if segment_mode and round_index != reported_round:
                    reported_round = round_index
                    round_number = self.workflow_segment_total_rounds - self.workflow_segment_rounds_remaining + 1
                    round_text = (
                        f"循环执行片段 第 {first_index + 1}-{last_index + 1} 行 · "
                        f"第 {round_number}/{self.workflow_segment_total_rounds} 轮"
                    )
                    self._ui(self._set_execution_progress, f"{round_text} · F12 停止")
                    self._ui(self._append_mini_step, f"{round_text}：开始。")
                    self._ui(self._log, f"{round_text}：开始。")
                step = steps[index]
                if self.workflow_stop.is_set():
                    return
                script_number = index + 1
                self.current_workflow_step_index = index
                self.current_workflow_repeat_index = 0
                self.current_workflow_action_index = 0
                is_module = step.get("kind") == "module"
                if not bool(step.get("enabled", True)):
                    disabled_name = self._workflow_step_name(step)
                    message = f"— 跳过工作流第 {script_number}/{len(steps)} 行：{disabled_name}，该任务已禁用"
                    self._ui(self._set_execution_progress, message)
                    self._ui(self._append_mini_step, message)
                    self._ui(self._log, message)
                    continue
                if is_module and not self._workflow_module_enabled(step):
                    disabled_name = self._workflow_step_name(step)
                    message = (
                        f"— 跳过工作流第 {script_number}/{len(steps)} 行：{disabled_name}，"
                        "模块管理中已禁用"
                    )
                    self._ui(self._set_execution_progress, message)
                    self._ui(self._append_mini_step, message)
                    self._ui(self._log, message)
                    continue
                unlimited = bool(step.get("unlimited", False))
                planned_repeats = int(step.get("repeats", 1))
                if not segment_mode and not unlimited and planned_repeats <= 0:
                    exhausted_name = self._workflow_step_name(step)
                    message = f"— 跳过工作流第 {script_number}/{len(steps)} 行：{exhausted_name}，执行次数已用完"
                    self._ui(self._set_execution_progress, message)
                    self._ui(self._append_mini_step, message)
                    self._ui(self._log, message)
                    continue
                if is_module:
                    action = dict(step.get("action") or {})
                    module_key = self._workflow_module_key(step)
                    if module_key and not action.get("module_key"):
                        action["module_key"] = module_key
                    if module_key and not action.get("template"):
                        action["template"] = module_key
                    if registered_module_object(module_key) is None:
                        message = (
                            f"⚠ 跳过工作流第 {script_number}/{len(steps)} 行："
                            f"{self._workflow_step_name(step)}，模块不存在"
                        )
                        self._ui(self._set_execution_progress, message)
                        self._ui(self._append_mini_step, message)
                        self._ui(self._log, message)
                        continue
                    script = MacroScript(
                        name=self._workflow_step_name(step), actions=[action],
                    )
                else:
                    script_path = resolve_path(step.get("script", ""))
                    if not script_path.is_file():
                        missing_name = workflow_script_name(step.get("script", ""))
                        message = f"⚠ 跳过工作流第 {script_number}/{len(steps)} 行：{missing_name}，文件不存在"
                        self._ui(self._set_execution_progress, message)
                        self._ui(self._append_mini_step, message)
                        self._ui(self._log, message)
                        continue
                    script = load_script(script_path)
                before = max(0, int(step.get("before_ms", 0)))
                self._ui(self._set_status, f"工作流步骤 {index + 1}/{len(steps)}", "warning")
                if before and not self._guard_wait(before / 1000):
                    return
                repeats = (
                    1 if unlimited or segment_mode
                    else (min(planned_repeats, 1) if test_mode else planned_repeats)
                )
                if segment_mode:
                    repeat_desc = "片段循环，每轮执行 1 次（不扣减次数）"
                elif unlimited:
                    repeat_desc = "不计次数，每次到达执行 1 次"
                elif test_mode:
                    repeat_desc = f"测试执行 {repeats} 次（不扣减）"
                else:
                    repeat_desc = f"执行 {repeats} 次"
                repeat_interval = max(0, int(step.get(
                    "repeat_interval_ms", DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
                )))
                repeat_start_action_id = str(
                    step.get("repeat_start_action_id", ""),
                ).strip() or None
                if repeat_start_action_id:
                    repeat_start_row = next(
                        (
                            i + 1 for i, action in enumerate(script.actions)
                            if str(action.get("action_id", "")).strip()
                            == repeat_start_action_id
                        ),
                        None,
                    )
                    if repeat_start_row:
                        repeat_desc += f"，第 2 次起从第 {repeat_start_row} 行开始"
                self._ui(
                    self._set_execution_progress,
                    workflow_execution_progress(
                        script_number, len(steps), script.name, repeats, unlimited=unlimited,
                    ),
                )
                self._ui(
                    self._log,
                    f"工作流第 {script_number}/{len(steps)} 步：脚本[{script.name}]，"
                    f"{repeat_desc}，每次间隔 {repeat_interval} ms。",
                )
                step_activation = pending_activation_hwnd
                step_activation_prepared = pending_activation_prepared
                step_activation_at = pending_activation_at
                step_activation_interval_ms = (
                    workflow_activation_interval_ms if step_activation is not None else 0
                )
                if not is_module and not activation_consumed and step_activation is None \
                        and workflow_activation_hwnd is None \
                        and activation_allowed \
                        and bool(script.settings.get("activation_window_enabled", False)):
                    # 侧栏“启用执行前置窗口”已勾选（activation_allowed）且编辑器
                    # 脚本没有自己的前置配置时，首个实际执行脚本可提供一次性
                    # 前置窗口；执行后同样不再重复。未勾选时，步骤脚本自己保存
                    # 的前置窗口一律不激活，避免“明明关了开关却仍被前置”。
                    try:
                        step_activation = self._execution_activation_hwnd(
                            hwnd, True, script.settings.get("activation_window"),
                        )
                        step_activation_interval_ms = int(
                            script.settings.get("activation_window_interval_ms", 0)
                        )
                    except RuntimeError:
                        message = (
                            f"工作流第 {script_number}/{len(steps)} 行：前置窗口未打开，"
                            "已跳过前置窗口条件，继续执行脚本。"
                        )
                        self._ui(self._set_execution_progress, message)
                        self._ui(self._append_mini_step, message)
                        self._ui(self._log, message)
                        step_activation = None
                        step_activation_prepared = False
                        step_activation_at = None
                self._set_trace_context(
                    step=script_number, steps=len(steps), script=script.name,
                    total=len(script.actions), repeat=0, repeats=repeats,
                )
                self.player.play(
                    script.actions, repeats, hwnd,
                    repeat_interval_ms=repeat_interval,
                    source_screen=dict(script.settings.get("recorded_screen", {})) or None,
                    script_name=script.name,
                    activate_target=activate_target, activation_hwnd=step_activation,
                    activation_prepared=step_activation_prepared,
                    activation_interval_ms=step_activation_interval_ms,
                    activation_prepared_at=step_activation_at,
                    start_repeat=start_repeat if round_index == 0 and index == start_index else 0,
                    resume_action_index=(
                        resume_action_index if round_index == 0 and index == start_index else None
                    ),
                    repeat_start_action_id=repeat_start_action_id,
                    workflow_context=True,
                    enable_script_globals=script_globals_enabled,
                    on_action=lambda next_index, _total: self._record_workflow_action(
                        next_index,
                    ),
                    on_repeat=lambda current, total, number=script_number, name=script.name: self._record_workflow_repeat(
                        current, total, number, len(steps), name,
                    ),
                    on_repeat_complete=None if segment_mode else (
                        lambda _current, _total, row=index, testing=test_mode:
                        self._consume_workflow_repeat_from_worker(row, testing)
                    ),
                )
                rebound_hwnd = getattr(self.player, "rebound_target_hwnd", None)
                if isinstance(rebound_hwnd, int) and rebound_hwnd:
                    hwnd = rebound_hwnd
                played_any_step = True
                if step_activation is not None:
                    activation_consumed = True
                pending_activation_hwnd = None
                pending_activation_prepared = False
                pending_activation_at = None
                if self.workflow_stop.is_set() or self.player.stop_event.is_set():
                    return
            # 纯全局模块工作流（没有任何实际执行的脚本步骤）：守卫没有播放器
            # 作为评估载体，注册后必须在这里持续评估，否则立即“执行完成”、
            # 检测永不生效（v1.0 的全局监控线程常驻直到 F12 的行为）。
            if not played_any_step and getattr(self, "global_guards", None) \
                    and not self.workflow_stop.is_set() and not self.player.stop_event.is_set():
                self._ui(self._set_status, "全局检测持续运行中 · F12 停止", "warning")
                self._ui(self._append_mini_step, "工作流无可执行脚本步骤，持续运行全局检测（F12 停止）。")
                self._ui(self._log, "工作流无可执行脚本步骤，持续运行全局检测（F12 停止）。")
                while not self.workflow_stop.is_set() and not self.player.stop_event.is_set():
                    try:
                        self.player.wait_while_paused()
                    except PlaybackStopped:
                        break
                    hit = self._evaluate_global_guards()
                    if hit is not None:
                        try:
                            self.player.handle_guard_hit(hit)
                        except PlaybackStopped:
                            break
                        except (EndCurrentScriptRequest, JumpToCurrentScriptLastAction,
                                AdvanceToNextWorkflowStep, GuardJumpRequest):
                            self._ui(self._log, "全局检测：处理段请求已忽略，继续检测。")
                        continue
                    self.player.stop_event.wait(0.1)
                # 这段守卫循环不经过 play()，处理段里按下的键/鼠标键没有收尾路径。
                self.player._release_all(None)
            if not self.workflow_stop.is_set() and not self.player.stop_event.is_set():
                self._clear_trace_context()
                self._ui(self._set_status, "工作流执行完成", "success")
                self._ui(self._append_mini_step, "工作流执行完成。")
                self._ui(self._log, "工作流执行完成。")
                self._ui(self._sound, "run_done")
        except Exception as exc:
            if not isinstance(exc, CaptureUnavailable) \
                    or not self._request_workflow_recovery(str(exc)):
                self._ui(self._handle_worker_error, "工作流执行失败", exc)
        finally:
            allow_display_sleep()
            self.current_workflow_step_index = None
            self.current_workflow_repeat_index = 0
            self.current_workflow_action_index = 0
            # 守卫生命周期 = 一次执行：正常完成/报错/F12 都必须清空，
            # 否则残留的工作流全局模块守卫会在之后的单独脚本执行中继续触发。
            self._clear_global_guards()
            self._shutdown_detection_worker()
            # 特殊模块「重新执行工作流」继续沿用当前执行的输入锁；
            # 其余情况（普通完成/报错/F12）正常收尾。
            restarting = bool(getattr(self, "workflow_restart_requested", False))
            if not restarting:
                self.workflow_segment = None
                self.workflow_segment_rounds_remaining = 0
                self.workflow_segment_total_rounds = 0
                self._leave_focus_mode()
            if display_run_started and end_resolution:
                try:
                    self._apply_workflow_resolution(end_resolution, "结束恢复")
                except Exception as exc:
                    self._ui(self._log, f"工作流结束后恢复分辨率/缩放失败：{exc}")
            if not restarting:
                self._ui(self._finish_execution_visibility)
                self.workflow_test_mode_active = False
            self._finish_workflow_recovery()

    def _register_workflow_global_modules(self, modules: list[dict]) -> bool:
        """Register the workflow guards for a workflow or a partial script run."""
        for module in modules:
            if self.workflow_stop.is_set():
                return False
            if not bool(module.get("enabled", True)):
                continue
            registry_state = self._workflow_global_module_registry_state(module)
            if registry_state in {"disabled", "missing"}:
                module_label = self._global_module_label(module)
                reason = "模块管理中已禁用" if registry_state == "disabled" else "模块对象不存在"
                message = f"— 跳过工作流全局模块：{module_label}，{reason}"
                self._ui(self._append_mini_step, message)
                self._ui(self._log, message)
                continue
            before = max(0, int(module.get("before_ms", 0)))
            if before and not self._guard_wait(before / 1000):
                return False
            config = dict(module.get("config") or {})
            script_value = str(module.get("script", "")).strip()
            if not config and script_value:
                script_path = resolve_path(script_value)
                if script_path.is_file():
                    try:
                        script = load_script(script_path)
                        config = self._script_trigger_config(script)
                    except Exception:
                        pass
            if config:
                self._activate_global_detect_from_config(config, module)
                self._ui(self._append_mini_step, "全局检测模块已启用。")
            else:
                self._ui(
                    self._log,
                    f"全局模块 {workflow_script_name(script_value) or '未配置'}："
                    "未找到全局检测配置，未启用检测。",
                )
        return True

    def _apply_workflow_resolution(self, action: dict, phase: str) -> None:
        self._ui(self._log, f"工作流{phase}：设置 {action.get('name', '')} 分辨率与缩放。")
        self.player._execute_action(
            dict(action), None, interruptible_display_wait=False,
        )


    def _global_module_label(self, step: dict, module_objects: dict | None = None) -> str:
        """全局模块行只显示用户起的标题，不重复引擎细节（区域/图片名/触发说明）。"""
        config = dict(step.get("config") or {})
        script_value = step.get("script", "")
        if not config and str(script_value).strip():
            script_path = resolve_path(script_value)
            if script_path.is_file():
                try:
                    config = self._script_trigger_config(load_script(script_path))
                except Exception:
                    pass
        if config.get("module_ref") and str(
            config.get("module_key") or config.get("template") or ""
        ).strip():
            module_key = str(config.get("module_key") or config.get("template", "")).strip()
            module_obj = self._module_object(module_key, module_objects) or {}
            name = str(module_obj.get("name", "")).strip()
            if name:
                return name
            return "未找到模块" if module_key.startswith("module:") else Path(
                module_key.replace("\\", "/"),
            ).stem
        if str(script_value).strip():
            return workflow_script_name(script_value) or "未配置"
        return "未配置"

