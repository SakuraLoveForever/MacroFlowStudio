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
from macroflow.core.storage import (
    BASE_DIR, IMAGES_DIR, SCRIPTS_DIR, WORKFLOWS_DIR, archive_overwritten_script,
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    available_script_path, backup_script,
    display_path, ensure_dirs, migrate_workflow_templates, safe_name,
    DIRECTION_SCRIPTS_DIR,
    load_app_settings, load_script, load_workflow,
    script_category_for_path,
    registered_module_object, registered_template_region, remap_hotkey_script_bindings,
    resolve_path, save_app_settings,
    save_script, save_workflow,
    update_module_object,
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
from macroflow.input.input_guard import (
    FocusInputGuard, InputCapturer, KeyCapturer, RESERVED_HOTKEY_VKS,
)
from pathlib import Path
import copy
from tkinter import filedialog, messagebox, simpledialog
import sys
import threading
import time
import tkinter as tk

from .base import (
    pad,
    script_category_key,
    script_category_label,
)
from .constants import (
    COLOR_RED,
    COLOR_SURFACE,
    COLOR_TEXT,
    MAX_TREE_ROWS,
)
from .startup import (
    spawn_new_instance,
)
from .summaries import (
    action_summary,
    key_action_matches,
    set_matching_key_action_delays,
)

class ScriptsMixin:
    """脚本持久化与动作列表：打开 / 保存 / 草稿 / 撤销 / 树操作。"""

    def _refresh_action_segment_bar(self, _event=None) -> None:
        """动作列表选中一段时，在最左边画出那根实心竖条。"""
        self._refresh_row_segment_bar(self.action_tree, "action_segment_painted")
    def rebuild_action_tree(self):
        self._reset_row_segment_bar("action_segment_painted")
        self.action_tree.delete(*self.action_tree.get_children())
        action_rows = {
            str(action.get(ACTION_ID_KEY, "")): index + 1
            for index, action in enumerate(self.script.actions)
            if action.get(ACTION_ID_KEY)
        }
        detail_texts = []
        for index, action in enumerate(self.script.actions[:MAX_TREE_ROWS]):
            kind, detail, delay = action_summary(action, action_rows)
            if action.get("failure_segment_enabled"):
                detail += f" · 失败后执行代码段 {len(action.get('failure_actions') or [])} 项"
            detail_texts.append(detail)
            self.action_tree.insert(
                "", "end", iid=str(index),
                values=("", index + 1, kind, detail, delay),
            )
        self._autosize_tree_column(self.action_tree, "detail", 590, detail_texts)
        total = len(self.script.actions)
        suffix = "（仅显示前 20,000 条）" if total > MAX_TREE_ROWS else ""
        self.record_count_var.set(f"{total} 个动作{suffix}")
        if total == 0:
            self.empty_action_hint.place(relx=0.5, rely=0.45, anchor="center")
        else:
            self.empty_action_hint.place_forget()
        self._sync_global_script_marker()
        self._update_action_edit_button()
    def _sync_global_script_marker(self):
        # v1.68 起普通脚本可内嵌全局模块行（global_detect + jump_row），
        # 它们不代表全局脚本；只有类别为全局或带触发条件才标记为全局脚本。
        category = self.script.settings.get("category", "level")
        is_global = bool(self.script.settings.get("trigger"))
        self.script.is_global = is_global
        marker = "◈ 旧全局触发脚本" if is_global else ""
        self.global_script_marker.configure(text=marker)
        self._refresh_global_trigger_section()
    def _refresh_global_trigger_section(self):
        """全局脚本显示"触发条件 + 语句体"区块；普通脚本隐藏。"""
        if not getattr(self, "trigger_section", None):
            return
        if self.script.is_global:
            self.trigger_section.pack(fill="x")
            trigger = self.script.settings.get("trigger") or {}
            summary = self._trigger_summary(trigger)
            self.trigger_summary_var.set(summary)
            self.trigger_summary_label.configure(
                foreground=COLOR_RED if not trigger.get("template") else COLOR_TEXT,
            )
            if trigger.get("template"):
                self.clear_trigger_button.pack(side="right", padx=pad(6, 0))
            else:
                self.clear_trigger_button.pack_forget()
        else:
            self.trigger_section.pack_forget()
    def _trigger_summary(self, trigger: dict) -> str:
        """触发条件的摘要文本（用于脚本编辑器的触发条件区块）。"""
        template = str(trigger.get("template", ""))
        if not template:
            return "未配置触发条件（编辑后保存，识别成功即触发执行语句体）"
        parts = [Path(template).name]
        region = trigger.get("region") or []
        mode = str(trigger.get("region_mode", ""))
        if mode == "template":
            parts.append("区域：模板")
        elif mode == "window":
            parts.append("区域：目标窗口")
        elif mode == "custom" or len(region) == 4:
            try:
                parts.append("区域：" + ",".join(str(int(part)) for part in region))
            except (TypeError, ValueError):
                pass
        else:
            parts.append("区域：全屏")
        try:
            parts.append(f"持续超过 {int(trigger.get('hold_ms', 1000))} ms")
        except (TypeError, ValueError):
            pass
        return " · ".join(parts)
    def _edit_global_trigger(self):
        trigger = dict(self.script.settings.get("trigger") or {})
        config = GlobalDetectDialog(self.root, trigger, require_click=False).show()
        if config is None:
            return
        config.pop("type", None)
        self.script.settings["trigger"] = config
        self._mark_dirty()
        self._sync_global_script_marker()
        self._set_status("触发条件已更新：识别成功后依次执行脚本内的所有动作", "success")
    def _clear_global_trigger(self):
        self.script.settings.pop("trigger", None)
        self._mark_dirty()
        self._sync_global_script_marker()
    def _reset_script_editor(self):
        """把脚本编辑器重置为空白新脚本（不清除撤销打开栈）。"""
        self.script = self._blank_script_with_activation_draft()
        self.script_path = None
        self.script_requires_new_file = False
        self.script_name_var.set(self.script.name)
        self.record_mode_var.set("auto")
        self.interval_var.set(DEFAULT_MOUSE_MOVE_INTERVAL_MS)
        self.script_category_var.set("关卡")
        self.dirty = False
        self._clear_action_undo()
        self.rebuild_action_tree()
        self._refresh_coordinate_scale_status()
        self._sync_activation_ui_from_script()
    def new_script(self):
        if self.dirty and not self._confirm_discard_unsaved_edits("新建脚本"):
            return
        self._reset_script_editor()
        self.undo_open_stack = []
        self._update_undo_open_button()
        self._set_status("已新建脚本", "success")
    def close_script(self):
        """关闭当前脚本：保留一份快照供撤销打开恢复，然后清空编辑器。"""
        snapshot = {
            "script": copy.deepcopy(self.script),
            "script_path": self.script_path,
            "script_requires_new_file": self.script_requires_new_file,
            "name": self.script_name_var.get(),
            "interval": self.interval_var.get(),
            "category": self.script_category_var.get(),
            "dirty": self.dirty,
            "action_undo_stack": copy.deepcopy(getattr(self, "action_undo_stack", [])),
            "action_redo_stack": copy.deepcopy(getattr(self, "action_redo_stack", [])),
        }
        history = getattr(self, "undo_open_stack", None)
        if history is None:
            history = self.undo_open_stack = []
        history.append(snapshot)
        if len(history) > 10:
            del history[:-10]
        self._reset_script_editor()
        self._update_undo_open_button()
        self._set_status("已关闭脚本，可撤销打开", "success")
        self._log("关闭脚本，可撤销打开")
    def undo_open_script(self):
        """撤销上次的关闭/打开操作，恢复关闭前的脚本及其编辑状态。"""
        history = getattr(self, "undo_open_stack", [])
        if not history:
            self._update_undo_open_button()
            return
        snapshot = history.pop()
        self.script = snapshot["script"]
        self.script_path = snapshot["script_path"]
        self.script_requires_new_file = snapshot["script_requires_new_file"]
        self.script_name_var.set(snapshot["name"])
        self.interval_var.set(snapshot["interval"])
        self.script_category_var.set(snapshot["category"])
        self.dirty = snapshot["dirty"]
        self.action_undo_stack = snapshot["action_undo_stack"]
        self.action_redo_stack = snapshot.get("action_redo_stack", [])
        self.rebuild_action_tree()
        self._refresh_coordinate_scale_status()
        self._sync_activation_ui_from_script()
        self._update_undo_button()
        self._update_redo_button()
        self._update_undo_open_button()
        self._set_status("已撤销打开，恢复关闭前的脚本", "success")
    def _update_undo_open_button(self):
        button = getattr(self, "undo_open_button", None)
        if button is not None:
            button.configure(
                state="normal" if getattr(self, "undo_open_stack", []) else "disabled",
            )
    def open_new_window(self):
        """Launch a second MacroFlow instance starting with a new script."""
        try:
            args = [sys.executable]
            if not getattr(sys, "frozen", False):
                args.append(str(Path(__file__).resolve()))
            args.append("--new-script")
            spawn_new_instance(args)
        except Exception as exc:
            self._notify("无法新开窗口", str(exc))
            return
        self._log("已新开一个 MacroFlow 窗口（新建脚本）。")
        self._set_status("已新开窗口", "success")
    def _show_action_context_menu(self, event):
        """动作列表右键菜单：从此行运行 / 单独执行这一行 / 循环执行选中的片段。"""
        row_id = self.action_tree.identify_row(event.y)
        if not row_id:
            return
        # 右键落在已选中的一段里就保留这段选中（「循环执行片段」用它定片段）；
        # 落在别处则只选中右键那一行，避免执行错行。
        if row_id not in self.action_tree.selection():
            self.action_tree.selection_set(row_id)
        index = int(row_id)
        if index >= len(self.script.actions):
            return
        action = self.script.actions[index]
        segment = self._selected_row_segment(self.action_tree)
        menu = tk.Menu(
            self.root, tearoff=False,
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        menu.add_command(
            label="▶ 从此行开始运行",
            command=lambda: self.run_current_script(start_index=index),
        )
        menu.add_command(
            label="▶ 单独执行此动作…",
            command=lambda: self.run_single_action_with_count(index),
        )
        if segment is not None:
            menu.add_command(
                label="▶ 循环执行片段…",
                command=self.run_action_segment,
            )
        if str(action.get("type")) == "script_ref":
            menu.add_separator()
            menu.add_command(
                label="⇪ 在新窗口打开引用的脚本",
                command=lambda: self.open_referenced_script_in_new_window(action),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    def open_referenced_script_in_new_window(self, action: dict):
        """Launch a second MacroFlow window loading the referenced script."""
        ref_value = str(action.get("script", "")).strip()
        if not ref_value:
            self._notify("引用脚本无效", "该引用动作没有脚本路径。")
            return
        ref_path = resolve_path(ref_value)
        if not ref_path.is_file():
            self._notify("引用脚本不存在", f"找不到文件：{ref_value}")
            return
        try:
            args = [sys.executable]
            if not getattr(sys, "frozen", False):
                args.append(str(Path(__file__).resolve()))
            args += ["--open-script", str(ref_path)]
            spawn_new_instance(args)
        except Exception as exc:
            self._notify("无法新开窗口", str(exc))
            return
        self._log(f"已在新窗口打开引用的脚本：{ref_path}")
        self._set_status(f"已在新窗口打开 {ref_path.name}", "success")
    def run_single_action_with_count(self, index: int):
        """右键「单独执行此动作…」：先问次数（默认 1 次），再单独执行这一行动作。

        次数是这一行的调用次数：引用脚本行执行一次仍按动作里保存的内部执行
        次数跑，录制动作、识图、连点等动作自身的参数也照常生效。
        """
        repeats = self._ask_repeats(
            "单独执行此动作", "这一行的动作要单独执行几次？",
        )
        if repeats is None:
            return
        self.run_current_script(start_index=index, single_action_repeats=repeats)
    def run_action_segment(self):
        """右键「▶ 循环执行片段…」：把选中的这一段动作循环执行指定次数。

        片段 = 列表里选中的第一行到最后一行（左边那根实心竖条就是它）；
        次数是这一段循环几轮，每一轮按顺序把这段里的动作各跑一次。
        """
        segment = self._selected_row_segment(self.action_tree)
        if segment is None:
            self._notify("循环执行片段", "请先选中片段的第一行到最后一行（至少两行）。")
            return
        first, last = segment
        repeats = self._ask_repeats(
            "循环执行片段", f"第 {first + 1}-{last + 1} 行动作要循环执行几次？",
        )
        if repeats is None:
            return
        self.run_current_script(segment=segment, segment_repeats=repeats)
    def _show_workflow_context_menu(self, event):
        """工作流表格右键菜单：执行这一行 / 循环执行选中的片段 / 打开它的脚本。"""
        row_id = self.workflow_tree.identify_row(event.y)
        if not row_id:
            return
        # 右键落在已选中的一段里就保留这段选中（「循环执行片段」用它定片段）；
        # 落在别处则只选中右键那一行，避免执行错行。
        if row_id not in self.workflow_tree.selection():
            self.workflow_tree.selection_set(row_id)
        index = int(row_id)
        steps = self._workflow_only_steps()
        if index >= len(steps):
            return
        step = steps[index]
        has_script = bool(str(step.get("script", "")).strip())
        segment = self._selected_row_segment(self.workflow_tree)
        if not has_script and segment is None:
            return
        menu = tk.Menu(
            self.root, tearoff=False,
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        if has_script:
            menu.add_command(
                label="▶ 单独执行此步骤…",
                command=lambda: self.run_workflow_step_with_count(step),
            )
        if segment is not None:
            menu.add_command(
                label="▶ 循环执行片段…",
                command=self.run_workflow_segment,
            )
        if has_script:
            menu.add_command(
                label="⇪ 在新窗口打开脚本",
                command=lambda: self.open_referenced_script_in_new_window(step),
            )
            menu.add_command(
                label="✎ 在当前编辑器打开",
                command=lambda: self._open_workflow_script_in_editor(step),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
    def run_workflow_step_with_count(self, step: dict):
        """右键「单独执行此步骤…」：先问次数（默认 1 次），再单独跑这一行的脚本。

        次数只作用于这一次单独执行：工作流行自己保存的剩余次数照旧不扣减。
        """
        repeats = self._ask_repeats(
            "单独执行此步骤", "这一行的脚本要单独执行几次？",
        )
        if repeats is None:
            return
        self.run_referenced_script_alone(step, repeats)
    def run_workflow_segment(self):
        """右键「▶ 循环执行片段…」：把选中的这一段步骤循环执行指定次数。

        片段 = 表格里选中的第一行到最后一行（左边那根实心竖条就是它）；
        每一轮里这些行按顺序各执行一次，且不扣减各行自己保存的剩余次数。
        """
        segment = self._selected_row_segment(self.workflow_tree)
        if segment is None:
            self._notify("循环执行片段", "请先选中片段的第一行到最后一行（至少两行）。")
            return
        first, last = segment
        repeats = self._ask_repeats(
            "循环执行片段", f"第 {first + 1}-{last + 1} 行要循环执行几次？",
        )
        if repeats is None:
            return
        self.run_workflow(segment=segment, segment_repeats=repeats)
    def _open_workflow_script_in_editor(self, step: dict):
        """Load a workflow step's script into the current script editor."""
        ref_value = str(step.get("script", "")).strip()
        ref_path = resolve_path(ref_value)
        if not ref_path.is_file():
            self._notify("脚本不存在", f"找不到文件：{ref_value}")
            return
        # 未保存修改的处理统一在 load_script_into_editor 里问一次（保存/放弃/取消）。
        self.load_script_into_editor(ref_path)
    def run_referenced_script_alone(self, ref: dict, repeats: int = 1):
        """单独运行一份被引用脚本（工作流右键「单独执行此步骤…」的入口）。"""
        return self._run_detection_entrypoint(
            self._run_referenced_script_alone_impl, ref, max(1, int(repeats)),
        )
    def _run_referenced_script_alone_impl(self, ref: dict, repeats: int = 1):
        """加载被引用脚本单独执行指定次数，不扣减工作流次数。

        使用该脚本自己的前置窗口与录屏设置（同打开脚本按 F9），
        目标窗口沿用当前编辑器绑定，与工作流执行保持一致。
        """
        script_value = str(ref.get("script", "")).strip()
        script_path = resolve_path(script_value)
        if not script_path.is_file():
            self._notify("脚本不存在", f"找不到文件：{script_value}")
            return
        try:
            script = load_script(script_path)
        except Exception as exc:
            self._notify("无法加载脚本", f"脚本解析失败：{exc}")
            return
        if self.recorder.running:
            self.stop_recording()
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "已有脚本或工作流正在执行。")
            return
        trigger = dict(script.settings.get("trigger") or {})
        if not script.actions and not trigger.get("template"):
            self._notify("没有动作", f"脚本 {script.name} 没有动作，无法执行。")
            return
        self._begin_detection_run()
        self._ensure_detection_worker()
        try:
            hwnd = self._bound_hwnd()
        except BaseException:
            self._shutdown_detection_worker()
            raise
        activation_enabled = bool(script.settings.get("activation_window_enabled", False))
        activation_signature = script.settings.get("activation_window")
        if isinstance(activation_signature, dict) and activation_signature.get("title"):
            activation_signature = {
                "title": str(activation_signature.get("title", "")),
                "class_name": str(activation_signature.get("class_name", "")),
                "process_path": str(activation_signature.get("process_path", "")),
            }
        else:
            activation_signature = None
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
        source_screen = dict(script.settings.get("recorded_screen", {})) or None
        self.workflow_stop.clear()
        self._sound("run_start")
        self._hide_main_for_execution()
        self.execution_started_at = time.perf_counter()
        self._set_execution_progress(
            f"单独测试 · {script.name} · 共执行 {repeats} 次 · 正在准备 · F12 停止")
        self.worker = threading.Thread(
            target=self._run_script_worker,
            args=(list(script.actions), repeats, hwnd, activation_hwnd,
                  source_screen, focus_enabled, activate_target, 0),
            kwargs={"trigger": trigger, "script_name": script.name},
            daemon=True,
        )
        self.worker.start()
        self._show_execution_mini()
        self._append_mini_step(f"单独执行测试：脚本 {script.name}，共 {repeats} 次。")
    def _load_startup_script(self, path: Path):
        if not path.is_file():
            self._notify("无法打开引用脚本", f"文件不存在：{path}")
            return
        self.load_script_into_editor(path)
    def _restore_editor_draft(self, draft: dict) -> bool:
        """Restore an editor snapshot captured during the previous shutdown."""
        raw_script = draft.get("script") if isinstance(draft, dict) else None
        if not isinstance(raw_script, dict):
            return False
        try:
            script = MacroScript.from_dict(raw_script)
            ensure_action_ids(script.actions)
        except (TypeError, ValueError, KeyError):
            return False

        self.script = script
        raw_path = str(draft.get("script_path", "") or "").strip()
        self.script_path = resolve_path(raw_path) if raw_path else None
        self.script_requires_new_file = bool(draft.get("script_requires_new_file", False))
        self.script_name_var.set(script.name)
        self.record_mode_var.set("auto")
        try:
            interval = max(10, min(500, int(script.settings.get(
                "move_interval_ms", DEFAULT_MOUSE_MOVE_INTERVAL_MS,
            ))))
        except (TypeError, ValueError):
            interval = DEFAULT_MOUSE_MOVE_INTERVAL_MS
        self.interval_var.set(interval)
        self.script_category_var.set(script_category_label(
            script_category_for_path(
                self.script_path, getattr(self, "app_settings", None), script,
            )
        ))
        self.dirty = bool(draft.get("dirty", False))
        self._clear_action_undo()
        self.undo_open_stack = []
        self._update_undo_open_button()
        self.rebuild_action_tree()
        self._refresh_coordinate_scale_status()
        self._sync_activation_ui_from_script()
        self._set_status(f"已恢复上次编辑状态：{script.name}", "success")
        self._log(f"已恢复上次关闭时的编辑状态：{script.name}")
        return True
    def _restore_last_editor_state(self) -> None:
        draft = self.app_settings.get("editor_draft")
        if isinstance(draft, dict) and self._restore_editor_draft(draft):
            return
        self._load_last_script()
    def _load_last_script(self):
        """启动时恢复上次关闭时脚本编辑页正在编辑的脚本。

        记录值在每次设置持久化时写入（含关闭应用），因此总能反映
        上次退出时编辑器打开的文件；文件已不存在时静默跳过。
        """
        raw = str(self.app_settings.get("last_script_path", "")).strip()
        if not raw:
            return
        path = resolve_path(raw)
        if not path.is_file():
            self._log(f"上次编辑的脚本已不存在，跳过恢复：{raw}")
            return
        self.load_script_into_editor(path)
    def _current_script_settings(self, recorded_screen: dict | None = None) -> dict:
        settings = dict(self.script.settings)
        configured = settings.get("activation_window_configured")
        if configured is None:
            configured = bool(self.activation_enabled_var.get() or self.saved_activation_signature)
        settings.update({
            "record_mode": "auto",
            "move_interval_ms": int(self.interval_var.get()),
            "activation_window_enabled": bool(self.activation_enabled_var.get()),
            "activation_window": (
                dict(self.saved_activation_signature) if self.saved_activation_signature else None
            ),
            "activation_window_configured": bool(configured),
        })
        if recorded_screen:
            settings["recorded_screen"] = dict(recorded_screen)
        else:
            settings.setdefault("recorded_screen", dict(DEFAULT_RECORDED_SCREEN))
        return settings
    def _resolve_scripts_dir(self, var_name: str, default: str) -> Path:
        var = getattr(self, var_name, None)
        value = var.get().strip() if var is not None else default
        path = Path(value)
        return path if path.is_absolute() else BASE_DIR / path
    def _level_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("level_scripts_dir_var", "scripts/关卡")
    def _level_pack_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("level_pack_scripts_dir_var", "scripts/关卡封装")
    def _switch_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("switch_scripts_dir_var", "scripts/切换")
    def _direction_scripts_dir(self) -> Path:
        return self._resolve_scripts_dir("direction_scripts_dir_var", DIRECTION_SCRIPTS_DIR)
    def _script_category_dir(self) -> Path:
        var = getattr(self, "script_category_var", None)
        label = var.get() if var is not None else "关卡"
        if label == "关卡封装":
            return self._level_pack_scripts_dir()
        if label == "切换":
            return self._switch_scripts_dir()
        if label == "方向":
            return self._direction_scripts_dir()
        return self._level_scripts_dir()
    def _script_category_changed(self, _event=None):
        label = self.script_category_var.get()
        self.script.settings["category"] = script_category_key(label)
        self._mark_dirty()
        self._sync_global_script_marker()
        self._set_status(f"脚本类别已改为：{label}", "success")
    def _set_insert_position(self, above: bool):
        self._apply_insert_position(
            self.insert_position_var, self.insert_above_button, self.insert_below_button, above,
        )
    def open_template_region_manager(self):
        """打开统一的"模板区域"管理模块：查看/修改每个模板图片登记的搜索区域。

        识图与全局识图共用同一份登记（template_regions.json），选中模板时自动导入。
        """
        TemplateRegionManagerDialog(self.root).show()
        if getattr(self, "workflow_tree", None) is not None:
            self.rebuild_workflow_tree()
    def jump_to_module_reference(self, path: Path, action_index: int):
        """Open a script and select the referenced module row in the editor."""
        path = Path(path)
        if not path.exists():
            self._notify("引用脚本不存在", f"找不到文件：{path}")
            return
        self.load_script_into_editor(path)
        index = int(action_index)

        def select_row():
            tree = getattr(self, "action_tree", None)
            if tree is None or not 0 <= index < len(self.script.actions):
                return
            row = str(index)
            tree.selection_set(row)
            tree.focus(row)
            tree.see(row)

        self.root.after_idle(select_row)
    def _configure_script_directories(self):
        values = ScriptDirectoriesDialog(
            self.root,
            level_dir=self.level_scripts_dir_var.get(),
            level_pack_dir=self.level_pack_scripts_dir_var.get(),
            switch_dir=self.switch_scripts_dir_var.get(),
            direction_dir=self.direction_scripts_dir_var.get(),
        ).show()
        if not values:
            return
        self.level_scripts_dir_var.set(values["level_dir"])
        self.level_pack_scripts_dir_var.set(values["level_pack_dir"])
        self.switch_scripts_dir_var.set(values["switch_dir"])
        self.direction_scripts_dir_var.set(values["direction_dir"])
        self._level_scripts_dir().mkdir(parents=True, exist_ok=True)
        self._level_pack_scripts_dir().mkdir(parents=True, exist_ok=True)
        self._switch_scripts_dir().mkdir(parents=True, exist_ok=True)
        self._direction_scripts_dir().mkdir(parents=True, exist_ok=True)
        self._persist_sidebar_settings()
        self._set_status("脚本保存目录已更新", "success")
        self._log(
            f"关卡目录：{self._level_scripts_dir()}；关卡封装目录：{self._level_pack_scripts_dir()}；"
            f"切换目录：{self._switch_scripts_dir()}；方向目录：{self._direction_scripts_dir()}",
        )
    def open_script(self):
        initial_dir = self._script_category_dir()
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=initial_dir, title="打开脚本",
            filetypes=[("MacroFlow 脚本", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.load_script_into_editor(Path(path))
    def _confirm_discard_unsaved_edits(self, action: str) -> bool:
        """编辑器有未保存修改时，先问清楚怎么处理再继续。

        以前这种情况只在屏幕上飘一条提示就直接返回：用户点开文件对话框、双击
        脚本，对话框关了、脚本没换，提示一闪而过，看起来就是「双击没反应」；
        编辑器本身也没有任何「未保存」标记，用户根本不知道被什么挡住了。
        现在明确给出三条路：保存后继续 / 放弃修改继续 / 留在当前脚本。
        """
        name = self.script_name_var.get().strip() or self.script.name or "当前脚本"
        answer = messagebox.askyesnocancel(
            "当前修改尚未保存",
            f"当前脚本「{name}」有未保存的修改。\n\n"
            f"是：保存后{action}\n"
            f"否：放弃修改，直接{action}\n"
            "取消：留在当前脚本",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            # 保存失败（或用户在保存流程里取消）时不继续，避免改动被默默丢掉。
            return self.save_current_script() is not None
        return True
    def load_script_into_editor(self, path: Path) -> bool:
        if getattr(self, "dirty", False) and not self._confirm_discard_unsaved_edits(
            f"打开「{Path(path).stem}」",
        ):
            return False
        try:
            self.script = load_script(path)
            ensure_action_ids(self.script.actions)
            # 文件名是脚本引用和工作流定位脚本的唯一外部身份。
            # 脚本在目录中被改名后，JSON 内旧的 name 不能继续覆盖编辑器名称，
            # 否则保存会再次按旧名称写回/移动文件。
            path = Path(path)
            self.script.name = path.stem
            self.script_path = path
            self.script_requires_new_file = False
            self.script_name_var.set(self.script.name)
            self.record_mode_var.set("auto")
            self.interval_var.set(int(self.script.settings.get(
                "move_interval_ms", DEFAULT_MOUSE_MOVE_INTERVAL_MS,
            )))
            # 类别显示脚本自己的类别：以所在目录为准（保存时按类别进目录），
            # 文件不在任何脚本目录内才用脚本里保存的类别。
            self.script_category_var.set(script_category_label(
                script_category_for_path(
                    path, getattr(self, "app_settings", None), self.script,
                )
            ))
            self.dirty = False
            self._clear_action_undo()
            self.undo_open_stack = []
            self._update_undo_open_button()
            self.rebuild_action_tree()
            self._refresh_coordinate_scale_status()
            self._sync_activation_ui_from_script()
            self._set_status(f"已打开 {path.name}", "success")
            self._log(f"打开脚本：{path}")
            return True
        except Exception as exc:
            self._notify("打开失败", str(exc))
            return False
    def save_current_script(self):
        recorder = getattr(self, "recorder", None)
        if recorder is not None and recorder.running:
            # 录制中的动作在 recorder.actions 里，编辑器动作列表是空的；
            # 此时保存会写出一个永远为空内容的“已保存”文件。
            self._notify("正在录制", "请先停止录制（F8）再保存脚本。")
            return None
        name = self.script_name_var.get().strip() or "未命名脚本"
        self.script.name = name
        self.script.settings = self._current_script_settings()
        category_var = getattr(self, "script_category_var", None)
        category_label = category_var.get() if category_var is not None else "关卡"
        self.script.settings["category"] = script_category_key(category_label)
        ensure_action_ids(self.script.actions)
        self.script.is_global = is_global_script(self.script.to_dict())
        self._refresh_coordinate_scale_status()
        if category_label == "关卡封装":
            target_dir = self._level_pack_scripts_dir()
        elif category_label == "切换":
            target_dir = self._switch_scripts_dir()
        elif category_label == "方向":
            target_dir = self._direction_scripts_dir()
        else:
            target_dir = self._level_scripts_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self.script_path
        moved_from = None
        if self.script_requires_new_file:
            target = available_script_path(name, target_dir)
        elif target is None or target.stem != name:
            # 改名：保存成功后删除旧文件，避免孤儿文件与陈旧引用
            # （与“类别变化”分支的 moved_from 语义一致）。
            moved_from = target
            target = available_script_path(name, target_dir)
        elif target.parent != target_dir:
            # 类别变化（或脚本应归入其他目录）：保存到新目录并移走旧位置的文件。
            moved_from = target
            target = available_script_path(name, target_dir)
        try:
            if target.is_file():
                # 覆盖已存在的脚本文件：自动把旧版本归档到备份目录。
                archive = archive_overwritten_script(target)
                if archive:
                    self._log(f"覆盖前已备份旧版本：{display_path(archive)}")
            self.script_path = save_script(self.script, target)
            self.script_requires_new_file = False
            self.dirty = False
            self._clear_action_undo()
            hotkey_bindings_updated = 0
            if (
                moved_from is not None
                and moved_from != self.script_path
                and category_label == "方向"
            ):
                hotkey_bindings_updated = remap_hotkey_script_bindings(
                    self.hotkey_scripts, moved_from, self.script_path,
                )
                if hotkey_bindings_updated:
                    self._apply_hotkey_bindings()
                    self._refresh_hotkey_summary()
                    self._persist_sidebar_settings()
            self.refresh_script_files()
            if getattr(self, "workflow_tree", None) is not None:
                self.rebuild_workflow_tree()
            if moved_from is not None and moved_from != self.script_path:
                try:
                    moved_from.unlink()
                except OSError:
                    pass
            if moved_from is not None:
                self._set_status(f"已保存并移动到 {self.script_path.parent.name}/{self.script_path.name}", "success")
                self._log(f"保存脚本并移动：{moved_from} → {self.script_path}")
                if hotkey_bindings_updated:
                    self._log(f"已同步 {hotkey_bindings_updated} 个快捷键脚本绑定")
            else:
                self._set_status(f"已保存 {self.script_path.name}", "success")
                self._log(f"保存脚本：{self.script_path}")
            return self.script_path
        except Exception as exc:
            self._notify("保存失败", str(exc))
            return None
    def refresh_script_files(self):
        pass
    def _selected_action_index(self) -> int | None:
        selected = self.action_tree.selection()
        return int(selected[0]) if selected else None
    def _clear_captured_search_query_kind(self, *_args):
        """Manual edits switch the search back to fuzzy cross-type matching."""
        self._key_search_query_kind = ""
    def _set_search_capture_buttons_state(self, state: str):
        button = getattr(self, "input_search_capture_button", None)
        if button is None:
            return "break"
        try:
            button.configure(state=state)
        except tk.TclError:
            pass
    def start_input_search_capture(self):
        """Capture the next physical key or mouse button for input search."""
        if getattr(self, "_input_search_capturer", None) is not None:
            return "break"
        self._set_search_capture_buttons_state("disabled")
        self.key_search_match_var.set("请按下要搜索的键或鼠标按键…按 Esc 取消")

        def on_input(kind, value):
            try:
                self.root.after(0, self._apply_captured_search_input, kind, value)
            except tk.TclError:
                pass

        def on_cancel():
            try:
                self.root.after(0, self._cancel_search_capture)
            except tk.TclError:
                pass

        capturer = InputCapturer(on_input, on_cancel)
        self._input_search_capturer = capturer
        if not capturer.start():
            self._input_search_capturer = None
            self._set_search_capture_buttons_state("normal")
            self.key_search_match_var.set("无法捕获键鼠输入")
        return "break"
    def _apply_captured_search_input(self, kind: str, value):
        query_kind = str(kind).strip().casefold()
        if query_kind == "mouse":
            value = {
                "left": "左键", "right": "右键", "middle": "中键",
            }.get(str(value).strip().casefold(), str(value))
        else:
            query_kind = "key"
            value = vk_to_key_name(int(value))
        self.key_search_var.set(value)
        self._key_search_query_kind = query_kind
        self._finish_search_capture()
        self._search_key_actions(1)
    def _cancel_search_capture(self):
        self._finish_search_capture()
        self.key_search_match_var.set("已取消键鼠检测")
    def _finish_search_capture(self):
        capturer = getattr(self, "_input_search_capturer", None)
        self._input_search_capturer = None
        if capturer is not None:
            try:
                capturer.stop()
            except Exception:
                pass
        self._set_search_capture_buttons_state("normal")
    def _search_key_actions(self, direction: int = 1):
        state = {
            "全部": "all", "按下": "down", "抬起": "up", "Press": "press",
        }.get(self.key_search_state_var.get(), "all")
        query = self.key_search_var.get()
        query_kind = getattr(self, "_key_search_query_kind", "")
        matches = [
            index for index, action in enumerate(self.script.actions)
            if key_action_matches(action, query, state, query_kind=query_kind)
        ]
        if not query.strip() and state == "all":
            matches = []
        self.key_search_match_var.set(f"匹配 {len(matches)} 项" if matches else "未找到")
        if not matches:
            return "break"
        current = self._selected_action_index()
        if current in matches:
            position = matches.index(current)
            target = matches[(position + (1 if direction >= 0 else -1)) % len(matches)]
        else:
            target = matches[0] if direction >= 0 else matches[-1]
        if target < MAX_TREE_ROWS:
            self.action_tree.selection_set(str(target))
            self.action_tree.focus(str(target))
            self.action_tree.see(str(target))
        return "break"
    def _clear_key_search(self):
        self._finish_search_capture()
        self.key_search_var.set("")
        self._key_search_query_kind = ""
        self.key_search_state_var.set("全部")
        self.key_search_delay_var.set("0")
        self.key_search_match_var.set("")
    def _set_matching_key_action_delays(self):
        query = self.key_search_var.get().strip()
        if not query:
            self.key_search_match_var.set("请先输入按键")
            return "break"
        try:
            delay = int(self.key_search_delay_var.get().strip())
            if delay < 0:
                raise ValueError
        except (TypeError, ValueError):
            self._notify("参数错误", "统一前延时请输入不小于 0 的整数毫秒值")
            return "break"
        state = {
            "全部": "all", "按下": "down", "抬起": "up", "Press": "press",
        }.get(self.key_search_state_var.get(), "all")
        query_kind = getattr(self, "_key_search_query_kind", "")
        candidate_indices = [
            index for index, action in enumerate(self.script.actions)
            if key_action_matches(action, query, state, query_kind=query_kind)
        ]
        if not candidate_indices:
            self.key_search_match_var.set("未找到")
            return "break"
        self._checkpoint_action_edit()
        changed = set_matching_key_action_delays(
            self.script.actions, query, state, delay, query_kind=query_kind,
        )
        self._mark_dirty()
        self.rebuild_action_tree()
        self.key_search_match_var.set(f"已统一 {len(changed)} 项为 {delay} ms")
        # 动作树只插入前 MAX_TREE_ROWS 行，超出的行没有 iid，选中会抛 TclError。
        if changed[0] < MAX_TREE_ROWS:
            self.action_tree.selection_set(str(changed[0]))
            self.action_tree.focus(str(changed[0]))
            self.action_tree.see(str(changed[0]))
        return "break"
    def _select_all_actions(self, _event=None):
        """Select every action row when Ctrl+A is pressed in the script list."""
        rows = self.action_tree.get_children()
        if rows:
            self.action_tree.selection_set(*rows)
            self.action_tree.focus(rows[0])
            self.action_tree.see(rows[0])
        return "break"
    def _update_undo_button(self):
        button = getattr(self, "undo_button", None)
        if button is not None:
            button.configure(state="normal" if getattr(self, "action_undo_stack", []) else "disabled")
    def _update_redo_button(self):
        button = getattr(self, "redo_button", None)
        if button is not None:
            button.configure(state="normal" if getattr(self, "action_redo_stack", []) else "disabled")
    def _clear_action_undo(self):
        self.action_undo_stack = []
        self.action_redo_stack = []
        self._update_undo_button()
        self._update_redo_button()
    def _checkpoint_action_edit(self):
        history = getattr(self, "action_undo_stack", None)
        if history is None:
            history = self.action_undo_stack = []
        snapshot = copy.deepcopy(self.script.actions)
        if not history or history[-1] != snapshot:
            history.append(snapshot)
            if len(history) > 100:
                del history[:-100]
            # 新的编辑使"重做"历史失效：撤销之后改动作，重做栈作废。
            if getattr(self, "action_redo_stack", None):
                self.action_redo_stack = []
                self._update_redo_button()
        self._update_undo_button()
    def _undo_redo_action_edit(self, redo: bool):
        """撤销/重做脚本编辑：redo=True 时从重做栈恢复，否则从撤销栈恢复。"""
        source_stack = getattr(self, "action_redo_stack" if redo else "action_undo_stack", [])
        if not source_stack:
            if redo:
                self._update_redo_button()
            else:
                self._update_undo_button()
            return
        selected = self._selected_action_index()
        target_stack = getattr(self, "action_undo_stack" if redo else "action_redo_stack", None)
        if target_stack is None:
            if redo:
                target_stack = self.action_undo_stack = []
            else:
                target_stack = self.action_redo_stack = []
        target_stack.append(copy.deepcopy(self.script.actions))
        self.script.actions = source_stack.pop()
        self._mark_dirty()
        self.rebuild_action_tree()
        if self.script.actions and selected is not None:
            restored_index = min(selected, len(self.script.actions) - 1)
            if restored_index < MAX_TREE_ROWS:
                self.action_tree.selection_set(str(restored_index))
                self.action_tree.see(str(restored_index))
        self._update_undo_button()
        self._update_redo_button()
        self._set_status("已重做上一次脚本编辑" if redo else "已撤销上一次脚本编辑", "success")
    def _insert_action(self, action: dict):
        index = self._selected_action_index()
        position_var = getattr(self, "insert_position_var", None)
        position = position_var.get() if position_var is not None else "below"
        if position == "above":
            insert_at = index if index is not None else 0
        else:
            insert_at = len(self.script.actions) if index is None else index + 1
        action = dict(action)
        action[ACTION_ID_KEY] = new_action_id()
        self._checkpoint_action_edit()
        self.script.actions.insert(insert_at, action)
        self._mark_dirty()
        self.rebuild_action_tree()
        if insert_at < MAX_TREE_ROWS:
            self.action_tree.selection_set(str(insert_at))
            self.action_tree.see(str(insert_at))
        return insert_at
    def add_delay(self):
        value = DurationDialog(self.root, "插入延时", "延时时间：", 500).show()
        if value is not None:
            self._insert_action({"type": "delay", "ms": value, "delay_ms": 0})
    def add_jump(self):
        ensure_action_ids(self.script.actions)
        action = JumpActionDialog(self.root, actions=self.script.actions).show()
        if action:
            self._insert_action(action)
    def add_key(self):
        action = KeyActionDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_text(self):
        text = simpledialog.askstring("插入文本", "要输入的文本：", parent=self.root)
        if text is not None:
            self._insert_action({"type": "text", "text": text, "char_delay_ms": 15, "delay_ms": 0})
    def add_notice(self):
        text = simpledialog.askstring("添加浮动提醒", "提醒会显示 3 秒，脚本不会暂停。\n提醒文字：", parent=self.root)
        if text is not None and text.strip():
            self._insert_action({
                "type": "notice", "text": text.strip(),
                "duration_ms": 3000, "delay_ms": 0,
            })
    def add_mouse_move(self):
        action = MouseMoveDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_click(self):
        action = ClickDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_turn(self):
        action = TurnActionDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_repeat_click(self):
        action = RepeatClickDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_scroll(self):
        action = ScrollDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_ocr_compare(self):
        ensure_action_ids(self.script.actions)
        action = OcrCompareActionDialog(
            self.root, actions=self.script.actions,
        ).show()
        if action:
            self._insert_action(action)
    def add_multi_condition_click(self):
        action = MultiConditionClickDialog(self.root).show()
        if action:
            self._insert_action(action)
    def test_row_list_condition_click(self, action: dict, on_complete=None,
                                      cancel_event=None, source="screen"):
        """Run a one-shot, non-clicking diagnostic scan for a row-list action.

        ``source`` selects the frame under test: ``"screen"`` captures the
        current screen, ``"image"`` reads the action's chosen test image.
        """
        def reject(message: str) -> None:
            if on_complete is not None:
                on_complete(None, message, 0, False)

        if getattr(self, "worker", None) and self.worker.is_alive():
            self._notify("无法测试识别", "当前已有脚本正在执行，请先停止执行。")
            reject("当前已有脚本正在执行")
            return
        if getattr(self, "_row_list_diagnostic_running", False):
            self._notify("无法测试识别", "当前已有列表逐行识别诊断正在执行，请稍候。")
            reject("当前已有列表逐行识别诊断正在执行")
            return
        image_source = str(source) == "image"
        image_path = str(action.get("screenshot_path", "")).strip() if image_source else ""
        if image_source and not image_path:
            self._notify("无法测试识别", "请先点「选择图片…」选一张整屏截图。")
            reject("未选择测试图片")
            return
        cancel_event = cancel_event or threading.Event()
        started_at = time.perf_counter()
        self._row_list_diagnostic_running = True
        # 选图测试不截当前屏幕，无需隐藏窗口或绑定游戏窗口。
        need_screen = not image_source
        hwnd = self._bound_hwnd() if need_screen else None
        source_label = "当前屏幕" if need_screen else "选择的图片"
        self._log(f"列表逐行识别诊断：开始识别全部行（{source_label}，不会点击）。")
        result_lines: list[str] = []
        try:
            hidden_states = (
                self._hide_macroflow_windows_for_diagnostic() if need_screen else None
            )
        except Exception as exc:
            self._row_list_diagnostic_running = False
            self._row_list_diagnostic_kind = ""
            self._notify("无法测试识别", str(exc))
            reject(str(exc))
            return

        def run_diagnostic():
            error = None
            diagnostic_result = None
            original_wait = self.player.on_ocr_engine_wait
            self.player.on_ocr_engine_wait = lambda: (
                not cancel_event.is_set()
                and (original_wait() if original_wait else True)
            )
            try:
                diagnostic_result = self.player._diagnose_row_list_condition_click(
                    action, hwnd, result_sink=result_lines.append,
                    image_path=image_path or None,
                )
            except Exception as exc:
                error = exc
            finally:
                self.player.on_ocr_engine_wait = original_wait
                self._ui(
                    self._finish_row_list_diagnostic,
                    result_lines,
                    error,
                    hidden_states,
                    diagnostic_result,
                    on_complete,
                    started_at,
                    cancel_event,
                )

        threading.Thread(target=run_diagnostic, daemon=True).start()
    def _macroflow_diagnostic_window_candidates(self) -> list[tk.Misc]:
        """Collect every Tk top-level owned by MacroFlow for a short hide cycle."""
        windows: list[tk.Misc] = []
        seen: set[str] = set()

        def add(widget) -> None:
            if widget is None:
                return
            try:
                top = widget.winfo_toplevel()
                if not top.winfo_exists():
                    return
                key = str(top.winfo_id())
            except (AttributeError, tk.TclError):
                return
            if key not in seen:
                seen.add(key)
                windows.append(top)

        def visit(widget) -> None:
            add(widget)
            try:
                children = widget.winfo_children()
            except (AttributeError, tk.TclError):
                return
            for child in children:
                visit(child)

        visit(self.root)
        for attribute in ("mini_window", "execution_notice_window", "cursor_tracking_mini"):
            add(getattr(self, attribute, None))
        return windows
    def _hide_macroflow_windows_for_diagnostic(self) -> list[tuple[tk.Misc, str]]:
        """Hide MacroFlow windows while leaving the bound external window untouched."""
        try:
            self._row_list_diagnostic_grab_window = self.root.grab_current()
        except tk.TclError:
            self._row_list_diagnostic_grab_window = None
        grab_window = self._row_list_diagnostic_grab_window
        if grab_window is not None:
            try:
                grab_window.grab_release()
            except tk.TclError:
                pass

        hidden_states: list[tuple[tk.Misc, str]] = []
        for window in self._macroflow_diagnostic_window_candidates():
            try:
                state = str(window.state())
                if state == "withdrawn":
                    continue
                hidden_states.append((window, state))
                window.withdraw()
            except tk.TclError:
                continue
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
        return hidden_states
    def _restore_macroflow_windows_after_diagnostic(
            self, hidden_states: list[tuple[tk.Misc, str]]) -> None:
        """Restore exactly the MacroFlow windows that were visible before scanning."""
        for window, state in hidden_states:
            try:
                if not window.winfo_exists():
                    continue
                window.deiconify()
                if state != "normal":
                    window.state(state)
            except tk.TclError:
                continue
        # The recognition dialog is still waiting in its original modal loop.
        # Its grab was released before hiding so the new result window remains
        # usable; the dialog itself is restored as an ordinary window.
        self._row_list_diagnostic_grab_window = None
        try:
            self.root.update_idletasks()
        except tk.TclError:
            pass
    def _finish_row_list_diagnostic(
            self, result_lines: list[str], error: Exception | None,
            hidden_states: list[tuple[tk.Misc, str]] | None, diagnostic_result=None,
            on_complete=None, started_at: float | None = None,
            cancel_event=None) -> None:
        """Restore the app and show the completed diagnostic in a new window."""
        elapsed_ms = round(max(0.0, time.perf_counter() - (started_at or time.perf_counter())) * 1000)
        cancelled = bool(cancel_event is not None and cancel_event.is_set())
        if hidden_states is not None:
            self._restore_macroflow_windows_after_diagnostic(hidden_states)
        self._row_list_diagnostic_running = False
        if on_complete is not None:
            matched_rows = 0
            if isinstance(diagnostic_result, dict):
                cells = diagnostic_result.get("cells", [])
                by_row = {}
                left_column = int(diagnostic_result.get("left_column", -1)) + 1
                right_column = int(diagnostic_result.get("right_column", -1)) + 1
                for cell in cells:
                    by_row.setdefault(cell.get("row"), {})[cell.get("column")] = cell.get("matched")
                matched_rows = sum(
                    bool(values.get(left_column)) and bool(values.get(right_column))
                    for values in by_row.values()
                )
            on_complete(
                {"matched_rows": matched_rows} if diagnostic_result is not None else None,
                "已取消" if cancelled else error,
                elapsed_ms,
                cancelled,
            )
        if cancelled:
            return
        if isinstance(diagnostic_result, dict):
            RowRecognitionResultDialog(
                self.root, diagnostic_result, error,
            ).show()
        else:
            RowListDiagnosticResultDialog(
                self.root, list(result_lines), error,
            ).show()
    def add_row_list_condition_click(self):
        ensure_action_ids(self.script.actions)
        dialog = RowListConditionClickDialog(self.root, actions=self.script.actions)
        dialog.on_test = self.test_row_list_condition_click
        action = dialog.show()
        if action:
            self._insert_action(action)
    def add_open_app(self):
        action = OpenAppDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_close_app(self):
        action = CloseAppDialog(self.root).show()
        if action:
            self._insert_action(action)
    def add_set_resolution(self):
        action = SetResolutionActionDialog(
            self.root, settings={"resolution_styles": self.resolution_styles},
        ).show()
        if action:
            self._insert_action(action)
    def add_block(self):
        self._insert_action({"type": "block", "delay_ms": 0})
    def add_global_detect(self):
        # 普通脚本内嵌全局模块行：播放到该行时启用全局检测，触发后跳转到脚本第 N 行。
        # 全局脚本的触发条件在"触发条件"区块配置，不能添加模块行。
        if self.script.settings.get("trigger"):
            self._notify(
                "不能添加",
                "全局脚本在“触发条件”区块配置识别设置，不需要也不能添加全局模块行。",
            )
            return
        ensure_action_ids(self.script.actions)
        action = GlobalDetectDialog(self.root, jump=True, actions=self.script.actions).show()
        if action:
            self._insert_action(action)
    def add_module(self):
        """打开模块选择窗口，把选中的模块对象 / 特殊动作插入脚本。

        支持在选择器中用 Ctrl / Shift 多选；每个模块仍单独经过自身需要的
        行级配置窗口。多选插入时保持列表顺序，向上插入时反向调用底层插入
        方法以抵消插入位置变化。
        """
        ensure_action_ids(self.script.actions)
        selected = ModulePickerDialog(
            self.root, actions=self.script.actions, multi_select=True,
        ).show()
        if not selected:
            return
        selected_actions = selected if isinstance(selected, list) else [selected]
        configured_actions = []
        for action in selected_actions:
            if action.get("module_ref") and action.get("module_category") in (
                    "script_global", "global", "special") and self.script.settings.get("trigger"):
                self._notify(
                    "不能添加",
                    "全局脚本在“触发条件”区块配置识别设置，不能添加全局模块行。",
                )
                continue
            module_key = str(action.get("module_key") or action.get("template", ""))
            module_obj = registered_module_object(module_key)
            if module_obj and module_obj.get("recognize") == "number":
                configured = edit_action(
                    self.root, action, all_actions=self.script.actions,
                )
                if configured is None:
                    continue
                action = configured
            if action.get("module_ref") and action.get("module_category") in (
                    "workflow_global", "script_global", "global"):
                configured = GlobalDetectDialog(
                    self.root, action, jump=True, actions=self.script.actions,
                ).show()
                if configured is None:
                    continue
                action = configured
            configured_actions.append(action)

        position_var = getattr(self, "insert_position_var", None)
        position = position_var.get() if position_var is not None else "below"
        if position == "above":
            configured_actions.reverse()
        for action in configured_actions:
            self._insert_action(action)
    def edit_selected_action(self):
        index = self._selected_action_index()
        if index is None:
            self._notify("编辑动作", "请先选择一条动作。")
            return
        if self.script.actions[index].get("type") in (
                "restart_workflow", "end_current_script", "block",
        ):
            self._set_status("特殊模块为固定动作，无需编辑", "success")
            return
        ensure_action_ids(self.script.actions)
        updated = edit_action(
            self.root, self.script.actions[index], self.script.actions,
            on_row_list_test=self.test_row_list_condition_click,
            settings={"resolution_styles": self.resolution_styles},
        )
        if updated:
            self._checkpoint_action_edit()
            self.script.actions[index] = updated
            self._mark_dirty()
            self.rebuild_action_tree()
            self.action_tree.selection_set(str(index))
    def _update_action_edit_button(self, _event=None):
        button = getattr(self, "edit_action_button", None)
        if button is None:
            return
        index = self._selected_action_index()
        editable = (
            index is not None
            and index < len(self.script.actions)
            and self.script.actions[index].get("type") not in (
                "restart_workflow", "end_current_script", "block",
            )
        )
        button.configure(state="normal" if editable else "disabled")
    def delete_actions(self):
        selected = sorted((int(item) for item in self.action_tree.selection()), reverse=True)
        if not selected:
            self._notify("删除动作", "请先选择一行或多行动作。")
            return
        next_selection = min(selected)
        self._checkpoint_action_edit()
        for index in selected:
            if index < len(self.script.actions):
                self.script.actions.pop(index)
        self._mark_dirty()
        self.rebuild_action_tree()
        if self.script.actions:
            # The original next row shifts into the deleted row's position.
            # When deleting the final row, keep the new final row selected.
            next_selection = min(next_selection, len(self.script.actions) - 1)
            item = str(next_selection)
            self.action_tree.selection_set(item)
            self.action_tree.focus(item)
            self.action_tree.see(item)
        self._set_status(f"已删除 {len(selected)} 行动作，可使用撤销恢复", "success")
    def copy_selected_actions_down(self):
        selected = sorted({int(item) for item in self.action_tree.selection()})
        if not selected:
            self._notify("向下复制", "请先选择一行或连续多行动作。")
            return
        if selected != list(range(selected[0], selected[-1] + 1)):
            self._notify("无法复制", "请选择连续的多行动作后再复制。")
            return
        if selected[-1] >= len(self.script.actions):
            return
        copies = clone_actions_with_new_ids(self.script.actions[selected[0]:selected[-1] + 1])
        insert_at = selected[-1] + 1
        self._checkpoint_action_edit()
        self.script.actions[insert_at:insert_at] = copies
        self._mark_dirty()
        self.rebuild_action_tree()
        copied_rows = [str(index) for index in range(insert_at, insert_at + len(copies)) if index < MAX_TREE_ROWS]
        if copied_rows:
            self.action_tree.selection_set(*copied_rows)
            self.action_tree.see(copied_rows[-1])
        self._set_status(f"已向下复制 {len(copies)} 行动作", "success")
    def _insert_script_position(self) -> int | None:
        """计算插入位置；未选中行且脚本已有动作时提示并返回 None。"""
        selected = sorted({int(item) for item in self.action_tree.selection()})
        if not selected and self.script.actions:
            self._notify("插入脚本", "请先选择插入位置所在的动作行。")
            return None
        position_var = getattr(self, "insert_position_var", None)
        position = position_var.get() if position_var is not None else "below"
        if position == "above":
            return selected[0] if selected else 0
        return min(len(self.script.actions), selected[-1] + 1) if selected else 0
    def _pick_script_files(self) -> list[Path] | None:
        """文件多选 + 校验每个文件都可解析为 MacroFlow 脚本。"""
        selected = filedialog.askopenfilenames(
            parent=self.root,
            initialdir=self._script_category_dir(),
            title="选择要插入的脚本（可 Ctrl/Shift 多选）",
            filetypes=[("MacroFlow 脚本", "*.json"), ("所有文件", "*.*")],
        )
        if not selected:
            return None
        paths = [Path(path) for path in selected]
        for path in paths:
            try:
                # Validate every selected file before changing the current script.
                load_script(path)
            except Exception as exc:
                self._notify("无法插入脚本", f"{path.name}：{exc}")
                return None
        return paths
    @staticmethod
    def _script_reference_action(path: Path) -> dict:
        ref_action = {
            "type": "script_ref",
            "script": display_path(path),
            "repeats": 1,
            "delay_ms": 0,
            "after_delay_ms": 0,
        }
        ref_action[ACTION_ID_KEY] = new_action_id()
        return ref_action
    @staticmethod
    def _expanded_script_actions(path: Path) -> list[dict]:
        """逐行复制一个脚本，重建行 ID 并同步映射跳转引用。"""
        script = load_script(path)
        actions = [dict(action) for action in (script.actions or [])]
        # 与 clone_actions_with_new_ids（向下复制）一致：先补全动作 ID 并把
        # 旧版 jump_row 迁移为 jump_action_id，再统一重映射，否则旧式跳转
        # 会带着源脚本的相对行号插入，指向错误位置。
        ensure_action_ids(actions)
        id_map = {}
        for action in actions:
            old_id = str(action.get(ACTION_ID_KEY, "")).strip()
            if old_id:
                id_map[old_id] = new_action_id()
        for action in actions:
            old_id = str(action.get(ACTION_ID_KEY, "")).strip()
            if old_id:
                action[ACTION_ID_KEY] = id_map[old_id]
            for field in JUMP_TARGET_KEYS:
                target = str(action.get(field, "")).strip()
                if target in id_map:
                    action[field] = id_map[target]
        return actions
    def _insert_script_actions(self, insert_at: int, actions: list[dict]):
        self._checkpoint_action_edit()
        self.script.actions[insert_at:insert_at] = actions
        self._mark_dirty()
        self.rebuild_action_tree()
        selected_rows = [
            str(index)
            for index in range(insert_at, insert_at + len(actions))
            if index < MAX_TREE_ROWS
        ]
        if selected_rows:
            self.action_tree.selection_set(*selected_rows)
            self.action_tree.see(selected_rows[-1])
    def _insert_script(self, expanded: bool):
        """插入脚本：expanded=False 插入一行引用动作（实时读取原脚本），
        expanded=True 逐行复制到当前位置（插入后可单独修改）。"""
        insert_at = self._insert_script_position()
        if insert_at is None:
            return
        paths = self._pick_script_files()
        if paths is None:
            return
        if expanded:
            actions = []
            for path in paths:
                actions.extend(self._expanded_script_actions(path))
            self._insert_script_actions(insert_at, actions)
            self._notify(
                "已逐行插入脚本",
                f"{len(paths)} 个脚本 · 共 {len(actions)} 行插入到第 {insert_at + 1} 行，插入后可单独修改",
            )
        else:
            actions = [self._script_reference_action(path) for path in paths]
            self._insert_script_actions(insert_at, actions)
            if len(paths) == 1:
                detail = (
                    f"{paths[0].stem} · 执行时实时读取该脚本 · "
                    f"插入到第 {insert_at + 1} 行"
                )
            else:
                detail = (
                    f"共 {len(paths)} 个脚本 · 执行时实时读取原脚本 · "
                    f"插入到第 {insert_at + 1} 行起"
                )
            self._notify("已插入脚本引用", detail)
    def move_action(self, offset: int):
        """整体上移/下移选中的动作行。

        连续多选时把这一整段当作一个块移动一行（块内顺序不变），单行选中就是
        与相邻行交换——两种情况走同一条“摘出再插入”的路径。
        """
        selected = sorted({int(item) for item in self.action_tree.selection()})
        if not selected:
            return
        if selected != list(range(selected[0], selected[-1] + 1)):
            self._notify("无法移动", "请选择连续的多行动作后再移动。")
            return
        start, end = selected[0], selected[-1]
        target = start + offset
        # 已经在脚本首/末行：整块无处可去（单行与多行同一条边界判断）。
        if target < 0 or end + offset >= len(self.script.actions):
            return
        block = self.script.actions[start:end + 1]
        self._checkpoint_action_edit()
        del self.script.actions[start:end + 1]
        self.script.actions[target:target] = block
        self._mark_dirty()
        self.rebuild_action_tree()
        rows = [str(index) for index in range(target, target + len(block)) if index < MAX_TREE_ROWS]
        if rows:
            self.action_tree.selection_set(*rows)
            # 上移看块首（新露出的上一行）、下移看块尾，避免整块滚出视野。
            self.action_tree.see(rows[0] if offset < 0 else rows[-1])
        if len(block) > 1:
            self._set_status(
                f"已整体{'上移' if offset < 0 else '下移'} {len(block)} 行动作", "success",
            )
