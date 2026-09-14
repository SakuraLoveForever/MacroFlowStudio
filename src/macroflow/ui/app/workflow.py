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
from macroflow.execution.player import (
    JUMP_CURRENT_SCRIPT_LAST_RESULT, MAX_SCRIPT_REF_DEPTH,
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest,
    JumpToCurrentScriptLastAction, MacroPlayer, PlaybackStopped,
    screen_template_scale,
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
from pathlib import Path
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
from macroflow.core.display_power import allow_display_sleep, keep_display_awake
import copy
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont

from .base import (
    workflow_execution_progress,
    workflow_script_name,
)
from .constants import (
    COLOR_SURFACE,
    COLOR_TEXT,
)
from .startup import (
    spawn_new_instance,
)

class WorkflowMixin:
    """工作流页：步骤表格、单元格编辑、排序、全局模块列表与执行。"""

    def _global_module_steps(self) -> list[dict]:
        return [step for step in self.workflow.steps if step.get("kind") == "global_module"]
    def _workflow_only_steps(self) -> list[dict]:
        return [step for step in self.workflow.steps if step.get("kind") != "global_module"]
    @staticmethod
    def _workflow_module_key(step: dict) -> str:
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        return str(action.get("module_key") or action.get("template") or "").strip()
    def _workflow_module_enabled(self, step: dict) -> bool:
        """Whether the module registry currently allows this workflow row."""
        if step.get("kind") != "module":
            return True
        action = step.get("action") if isinstance(step.get("action"), dict) else {}
        if str(action.get("type", "")) in {
            "restart_workflow", "end_current_script", "jump_current_script_last",
        }:
            return True
        module_obj = registered_module_object(self._workflow_module_key(step))
        return bool(module_obj and module_obj.get("enabled", True))
    def _workflow_step_name(self, step: dict) -> str:
        if step.get("kind") != "module":
            return workflow_script_name(step.get("script", "")) or "未设置脚本"
        module_key = self._workflow_module_key(step)
        module_obj = registered_module_object(module_key)
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
    def _apply_workflow_restart_default(self, _event=None):
        """把控件当前选择写入工作流的统一默认跳转行并落盘草稿。"""
        combo = getattr(self, "workflow_restart_default_combo", None)
        if combo is None:
            return
        label = combo.get()
        mapping = getattr(self, "workflow_restart_default_ids", {})
        row = max(0, int(mapping.get(label, 0) or 0))
        self.workflow.restart_default_row = row
        self._schedule_workflow_draft_save()
    def rebuild_workflow_tree(self):
        self._sync_workflow_restart_default_ui()
        self.workflow_tree.delete(*self.workflow_tree.get_children())
        workflow_steps = self._workflow_only_steps()
        script_labels = []
        for index, step in enumerate(workflow_steps):
            is_module = step.get("kind") == "module"
            script_value = step.get("script", "")
            module_enabled = self._workflow_module_enabled(step)
            enabled = bool(step.get("enabled", True)) and module_enabled
            unlimited = bool(step.get("unlimited", False))
            exhausted = not unlimited and int(step.get("repeats", 1)) <= 0
            if is_module:
                module_key = self._workflow_module_key(step)
                module_obj = registered_module_object(module_key)
                missing = module_obj is None
                script_label = f"◆ {self._workflow_step_name(step)}"
            else:
                missing = not resolve_path(script_value).is_file()
                script_label = workflow_script_name(script_value)
            if missing:
                missing_text = "模块不存在" if is_module else "文件不存在"
                script_label = f"⚠ {script_label}  ·  {missing_text}"
            script_labels.append(script_label)
            if unlimited:
                repeat_label = "∞"
            else:
                repeat_label = step.get("repeats", 1)
            if str(step.get("repeat_start_action_id", "")).strip():
                repeat_label = f"{repeat_label} ↻"
            if is_module and not module_enabled:
                status_label = "● 模块已禁用"
            elif not bool(step.get("enabled", True)):
                status_label = "● 已禁用"
            elif unlimited:
                status_label = "✓ 不计次数"
            elif exhausted:
                status_label = "○ 次数用完"
            else:
                status_label = "✓ 启用"
            self.workflow_tree.insert(
                "", "end", iid=str(index),
                values=(
                    index + 1, script_label, repeat_label,
                    f"{step.get('before_ms', 0)} ms",
                    f"{step.get('repeat_interval_ms', DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS)} ms",
                    status_label,
                ),
                tags=("module_disabled",) if is_module and not module_enabled else (
                    ("disabled",) if not bool(step.get("enabled", True)) else (
                        ("unlimited",) if unlimited else (
                            ("exhausted",) if exhausted else (("missing",) if missing else ())
                        )
                    )
                ),
            )
        if workflow_steps:
            self.empty_workflow_hint.place_forget()
        else:
            self.empty_workflow_hint.place(relx=0.5, rely=0.45, anchor="center")
        self._autosize_tree_column(self.workflow_tree, "script", 500, script_labels)
        self.rebuild_global_tree()
    def rebuild_global_tree(self):
        tree = getattr(self, "global_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        module_labels = []
        for index, step in enumerate(self._global_module_steps()):
            row_enabled = bool(step.get("enabled", True))
            registry_state = self._workflow_global_module_registry_state(step)
            enabled = row_enabled and registry_state in (None, "enabled")
            module_label = self._global_module_label(step)
            module_labels.append(module_label)
            if registry_state == "missing":
                status_label = "⚠ 模块不存在"
            elif registry_state == "disabled":
                status_label = "● 模块已禁用"
            elif not row_enabled:
                status_label = "● 已禁用"
            else:
                status_label = "✓ 全局"
            tree.insert(
                "", "end", iid=str(index),
                values=(index + 1, module_label, status_label),
                tags=("disabled",) if not enabled else ("global",),
            )
        if self._global_module_steps():
            self.empty_global_hint.place_forget()
        else:
            self.empty_global_hint.place(relx=0.5, rely=0.45, anchor="center")
        self._autosize_tree_column(tree, "module", 700, module_labels)
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
    def _global_module_label(self, step: dict) -> str:
        """Full summary for a global module, reading the referenced script when needed."""
        config = dict(step.get("config") or {})
        script_value = step.get("script", "")
        if not config and script_value.strip():
            script_path = resolve_path(script_value)
            if script_path.is_file():
                try:
                    script = load_script(script_path)
                    config = self._script_trigger_config(script)
                except Exception:
                    pass
        if config.get("module_ref") and str(config.get("template", "")).strip():
            module_key = str(config.get("module_key") or config.get("template", "")).strip()
            module_obj = registered_module_object(module_key) or {}
            module_name = str(module_obj.get("name", "")).strip() or Path(
                module_key.replace("\\", "/"),
            ).stem
            script_text = f"◆ 模块对象 · {module_name}"
        elif str(script_value).strip():
            script_name = workflow_script_name(script_value)
            script_text = f"⇄ 引用脚本 · {script_name}" if script_name else "⇄ 引用脚本 · 未配置"
        else:
            script_text = "◈ 全局检测 · 未配置"
        template_name = Path(str(config.get("template", ""))).name
        if not template_name:
            return script_text
        region = config.get("region") or []
        region_mode = str(config.get("region_mode", ""))
        if region_mode == "template":
            region_text = "模板区域"
        elif region_mode == "window":
            region_text = "目标窗口"
        elif region_mode == "custom" or len(region) == 4:
            try:
                region_text = ",".join(str(int(part)) for part in region) if len(region) == 4 else "全屏"
            except (TypeError, ValueError):
                region_text = "全屏"
        else:
            region_text = "全屏"
        try:
            hold = int(config.get("hold_ms", 1000))
        except (TypeError, ValueError):
            hold = 1000
        hold_text = f"持续 {hold} ms" if config.get("hold_enabled", False) else "识别到立即执行"
        return (f"◈ 全局检测 · {script_text} · {template_name} · 区域 {region_text} · "
                f"{hold_text} · 触发后执行模块步骤，再继续工作流")
    def _measure_text_width(self, text: str) -> int:
        try:
            return tkfont.Font(family="Microsoft YaHei UI", size=11).measure(str(text))
        except RuntimeError:
            # No Tk root available (unit tests): estimate CJK vs ASCII widths.
            return sum(14 if ord(ch) > 0x2E7F else 7 for ch in str(text))
    def _autosize_tree_column(self, tree, column: str, min_width: int, texts: list[str]) -> None:
        """Widen a tree column so its longest text stays fully visible."""
        needed = max((self._measure_text_width(text) for text in texts), default=0)
        tree.column(column, width=max(min_width, min(1600, needed + 40)), minwidth=min_width)
    def new_workflow(self):
        self.workflow = Workflow()
        self.workflow_path = None
        self._clear_workflow_delete_history()
        self.workflow_name_var.set(self.workflow.name)
        self.workflow_start_var.set("")
        self.workflow_start_delay_enabled_var.set(False)
        self.workflow_start_delay_seconds_var.unit.set("ms")
        self.workflow_start_delay_seconds_var.set("5000")
        self._toggle_workflow_start_delay_control(persist=False)
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
    def open_workflow(self):
        path = filedialog.askopenfilename(parent=self.root, initialdir=WORKFLOWS_DIR, title="打开工作流", filetypes=[("MacroFlow 工作流", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            workflow = load_workflow(path)
            self._switch_to_workflow(workflow, Path(path), f"打开工作流：{path}")
        except Exception as exc:
            self._notify("打开失败", str(exc))
    def _switch_to_workflow(self, workflow: Workflow, path: Path, log_text: str) -> None:
        """切换当前工作流到指定对象并同步工作流页全部 UI 状态。"""
        self.workflow = workflow
        self.workflow_path = path
        self._clear_workflow_delete_history()
        self.workflow_name_var.set(workflow.name)
        self.workflow_start_var.set(workflow.start_at)
        self.workflow_start_delay_enabled_var.set(workflow.start_delay_enabled)
        self.workflow_start_delay_seconds_var.unit.set("ms")
        self.workflow_start_delay_seconds_var.set(
            str(int(workflow.start_delay_seconds) * 1000)
        )
        self._toggle_workflow_start_delay_control(persist=False)
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        self._log(log_text)
    def rename_workflow(self):
        """弹窗修改当前工作流名称；确认后立即保存（文件同步改名，绝不覆盖已有文件）。"""
        current = self.workflow_name_var.get().strip() or self.workflow.name
        new_name = simpledialog.askstring(
            "修改工作流名称", "请输入新的工作流名称：",
            initialvalue=current, parent=self.root,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            self._notify("修改工作流名称", "名称不能为空。")
            return
        if new_name == current:
            return
        self.workflow_name_var.set(new_name)
        self.save_current_workflow()
    def duplicate_workflow(self):
        """复制当前工作流为一个独立的新工作流（新文件、新步骤 ID），保存后直接打开副本。"""
        # 先把输入框里的最新内容同步进模型，再深拷贝。
        self.workflow.name = self.workflow_name_var.get().strip() or "未命名工作流"
        self.workflow.start_at = self.workflow_start_var.get().strip()
        self._read_workflow_start_delay(validate=False)
        new_name = simpledialog.askstring(
            "复制为新工作流",
            "请输入新工作流的名称（原工作流保持不变）：",
            initialvalue=f"{self.workflow.name} 副本",
            parent=self.root,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name:
            self._notify("复制为新工作流", "名称不能为空。")
            return
        copied = copy.deepcopy(self.workflow)
        copied.name = new_name
        copied.start_at = ""  # 定时执行是一次性的：副本不继承原计划的开始时间。
        # 副本是独立工作流：清空并重新分配步骤身份，避免两份文件共用同一批 ID。
        for step in copied.steps:
            step["step_id"] = ""
        ensure_workflow_step_ids(copied.steps)
        WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
        stem = safe_name(new_name, "workflow")
        target = WORKFLOWS_DIR / f"{stem}.json"
        number = 2
        while target.exists():
            target = WORKFLOWS_DIR / f"{stem} ({number}).json"
            number += 1
        try:
            path = save_workflow(copied, target)
        except Exception as exc:
            self._notify("复制失败", str(exc))
            return
        self._switch_to_workflow(copied, path, f"复制工作流并打开：{path}")
        self._set_status(f"已复制为新工作流：{path.name}", "success")
    def save_current_workflow(self):
        self.workflow.name = self.workflow_name_var.get().strip() or "未命名工作流"
        self.workflow.start_at = self.workflow_start_var.get().strip()
        if self._read_workflow_start_delay(validate=True) is None:
            return None
        target = self.workflow_path
        moved_from = None
        if target is None or target.stem != self.workflow.name:
            # 改名/新建：生成绝不覆盖已有文件的路径（save_workflow 直接写
            # 默认目录会静默覆盖同名文件）；保存成功后删除旧文件，避免
            # 孤儿工作流与陈旧引用。
            moved_from = target
            WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
            stem = safe_name(self.workflow.name, "workflow")
            target = WORKFLOWS_DIR / f"{stem}.json"
            number = 2
            while target.exists():
                target = WORKFLOWS_DIR / f"{stem} ({number}).json"
                number += 1
        try:
            self.workflow_path = save_workflow(self.workflow, target)
            self._persist_workflow_draft()
            if moved_from is not None and moved_from != self.workflow_path:
                try:
                    moved_from.unlink()
                except OSError:
                    pass
                self._set_status(f"已保存并移动 {self.workflow_path.name}", "success")
                self._log(f"保存工作流并移动：{moved_from} → {self.workflow_path}")
            else:
                self._set_status(f"已保存 {self.workflow_path.name}", "success")
                self._log(f"保存工作流：{self.workflow_path}")
            return self.workflow_path
        except Exception as exc:
            self._notify("保存失败", str(exc))
            return None
    def refresh_workflow_files(self):
        pass
    def add_current_script_step(self):
        path = self.save_current_script()
        if path:
            self._add_or_insert_workflow_step(path)
    def add_script_step(self):
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=self._script_category_dir(), title="选择脚本",
            filetypes=[("MacroFlow 脚本", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._add_or_insert_workflow_step(Path(path))
    def _add_or_insert_workflow_step(self, path: Path):
        """添加脚本：有选中行时按“插入位置”设置插到选中行上/下方，否则追加到末尾。"""
        index = self._workflow_insert_target_index()
        if index is None:
            self._append_workflow_step(path)
        else:
            self._insert_workflow_step_at(path, index)
    def add_workflow_module_step(self):
        actions = ModulePickerDialog(
            self.root, categories=("switch", "special"), multi_select=True,
            allow_number=False,
        ).show()
        if not actions:
            return
        selected_actions = actions if isinstance(actions, list) else [actions]
        steps = [self._new_workflow_module_step(action) for action in selected_actions]
        index = self._workflow_insert_target_index()
        if index is None:
            # 未选中行：追加到末尾（保持“添加”语义）。
            self.workflow.steps.extend(steps)
            self.rebuild_workflow_tree()
            self._persist_workflow_draft()
            target = len(self._workflow_only_steps()) - 1
            if target >= 0:
                self.workflow_tree.selection_set(str(target))
                self.workflow_tree.see(str(target))
        else:
            # 有选中行：按“插入位置”设置插到选中行上/下方。
            self._insert_workflow_tasks_at(steps, index)
        self._set_status(
            f"已{'插入' if index is not None else '添加'} {len(selected_actions)} 个模块到工作流",
            "success",
        )
    def add_workflow_global_module(self):
        actions = ModulePickerDialog(
            self.root, categories=("workflow_global",), multi_select=True,
        ).show()
        if not actions:
            return
        selected_actions = actions if isinstance(actions, list) else [actions]
        for action in selected_actions:
            self._append_global_module(config=dict(action), refresh=False)
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        count = len(selected_actions)
        if count:
            self.global_tree.selection_set(str(len(self._global_module_steps()) - 1))
            self.global_tree.see(str(len(self._global_module_steps()) - 1))
        self._set_status(f"已添加 {count} 个工作流全局模块", "success")
    def _append_global_module(self, config: dict | None = None, script: str = "",
                              refresh: bool = True):
        step = {
            "kind": "global_module",
            "script": script,
            "repeats": 1,
            "before_ms": 0,
            "repeat_interval_ms": DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
            "unlimited": True,
            "enabled": True,
            "config": config,
            "step_id": new_action_id(),
        }
        self.workflow.steps.append(step)
        if refresh:
            self.rebuild_workflow_tree()
            self._persist_workflow_draft()
    def _new_workflow_script_step(self, path: Path) -> dict:
        return {
            "script": display_path(path),
            "repeats": 1,
            "before_ms": 0,
            "repeat_interval_ms": DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
            "unlimited": False,
            "enabled": True,
            "step_id": new_action_id(),
        }
    def _append_workflow_step(self, path: Path):
        self.workflow.steps.append(self._new_workflow_script_step(path))
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
    @classmethod
    def _new_workflow_module_step(cls, action: dict) -> dict:
        action = dict(action)
        action.setdefault(ACTION_ID_KEY, new_action_id())
        module_key = str(action.get("module_key") or action.get("template", "")).strip()
        module_obj = registered_module_object(module_key)
        module_name = (
            str(module_obj.get("name", "")).strip()
            if module_obj else Path(module_key.replace("\\", "/")).stem
        )
        action["module_name"] = module_name
        return {
            "kind": "module",
            "action": action,
            "repeats": 1,
            "before_ms": 0,
            "repeat_interval_ms": DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
            "unlimited": False,
            "enabled": True,
            "step_id": new_action_id(),
        }
    def _apply_insert_position(self, var, above_button, below_button, above: bool):
        """按 above/below 设置插入位置变量并高亮对应的上/下按钮。"""
        var.set("above" if above else "below")
        above_button.configure(bootstyle="primary" if above else "secondary")
        below_button.configure(bootstyle="primary" if not above else "secondary")
    def _set_workflow_insert_position(self, above: bool):
        self._apply_insert_position(
            self.workflow_insert_position_var, self.workflow_insert_above_button,
            self.workflow_insert_below_button, above,
        )
    def insert_workflow_step(self):
        """在选中步骤的上方或下方插入一个脚本步骤（按插入位置设置）。"""
        workflow_steps = self._workflow_only_steps()
        selected = self._selected_workflow_index()
        if selected is None or not workflow_steps:
            self._notify("插入脚本", "请先选择插入位置所在的工作流行。")
            return
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=self._script_category_dir(), title="选择要插入的脚本",
            filetypes=[("MacroFlow 脚本", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        index = self._workflow_insert_target_index()
        if index is None:
            return
        self._insert_workflow_step_at(Path(path), index)
    def insert_workflow_module_step(self):
        workflow_steps = self._workflow_only_steps()
        selected = self._selected_workflow_index()
        if selected is None or not workflow_steps:
            self._notify("插入模块", "请先选择插入位置所在的工作流行。")
            return
        action = ModulePickerDialog(
            self.root, categories=("switch", "special"), allow_number=False,
        ).show()
        if not isinstance(action, dict):
            return
        index = self._workflow_insert_target_index()
        if index is None:
            return
        step = self._new_workflow_module_step(action)
        self._insert_workflow_task_at(step, index)
    def _workflow_insert_target_index(self) -> int | None:
        """添加/插入共用：按“插入位置”设置换算选中行的目标下标；未选中行返回 None。"""
        selected = self._selected_workflow_index()
        if selected is None:
            return None
        position_var = getattr(self, "workflow_insert_position_var", None)
        position = position_var.get() if position_var is not None else "below"
        return selected if position == "above" else selected + 1
    def _insert_workflow_step_at(self, path: Path, index: int):
        self._insert_workflow_task_at(self._new_workflow_script_step(path), index)
    def _insert_workflow_task_at(self, step: dict, index: int):
        self._insert_workflow_tasks_at([step], index)
    def _insert_workflow_tasks_at(self, steps: list[dict], index: int):
        """把一批步骤插入到指定下标（工作流列表按脚本/模块计，不含全局模块）。"""
        script_steps = self._workflow_only_steps()
        index = min(max(0, index), len(script_steps))
        if index >= len(script_steps):
            self.workflow.steps.extend(steps)
        else:
            anchor = script_steps[index]
            position = self.workflow.steps.index(anchor)
            self.workflow.steps[position:position] = steps
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        target = min(index + len(steps) - 1, len(self._workflow_only_steps()) - 1)
        self.workflow_tree.selection_set(str(target))
        self.workflow_tree.see(str(target))
        self._update_workflow_selection_color()
    def _selected_workflow_index(self) -> int | None:
        selected = self.workflow_tree.selection()
        return int(selected[0]) if selected else None
    def _selected_workflow_indices(self) -> list[int]:
        return sorted({int(item) for item in self.workflow_tree.selection()})
    def _select_all_workflow_steps(self, _event=None):
        rows = self.workflow_tree.get_children()
        if rows:
            self.workflow_tree.selection_set(*rows)
            self.workflow_tree.focus(rows[0])
            self.workflow_tree.see(rows[0])
        return "break"
    def _update_workflow_selection_color(self, _event=None):
        index = self._selected_workflow_index()
        workflow_steps = self._workflow_only_steps()
        if index is not None and 0 <= index < len(workflow_steps):
            step = workflow_steps[index]
            if not self._workflow_module_enabled(step):
                background, foreground = "#552B35", "#FFB3B3"
            elif not bool(step.get("enabled", True)):
                background, foreground = "#6B4615", "#FFE1A3"
            elif bool(step.get("unlimited", False)):
                background, foreground = "#1F4D30", "#7BC96F"
            else:
                background, foreground = "#244D78", "#FFFFFF"
        else:
            background, foreground = "#244D78", "#FFFFFF"
        self.root.style.map(
            "Workflow.Treeview",
            background=[("selected", background)],
            foreground=[("selected", foreground)],
        )
    def _edit_workflow_cell(self, event):
        row = self.workflow_tree.identify_row(event.y)
        column = self.workflow_tree.identify_column(event.x)
        if not row or column == "#1":
            return
        index = int(row)
        workflow_steps = self._workflow_only_steps()
        if not 0 <= index < len(workflow_steps):
            return
        step = workflow_steps[index]
        if column == "#2":
            if step.get("kind") == "module":
                action = ModulePickerDialog(
                    self.root, categories=("switch", "special"), allow_number=False,
                ).show()
                if not isinstance(action, dict):
                    return
                action = dict(action)
                action.setdefault(ACTION_ID_KEY, new_action_id())
                module_key = str(action.get("module_key") or action.get("template", "")).strip()
                module_obj = registered_module_object(module_key)
                action["module_name"] = (
                    str(module_obj.get("name", "")).strip()
                    if module_obj else Path(module_key.replace("\\", "/")).stem
                )
                step["action"] = action
            else:
                current = resolve_path(step.get("script", ""))
                initial_dir = current.parent if current.parent.is_dir() else self._level_scripts_dir()
                path = filedialog.askopenfilename(
                    parent=self.root, initialdir=initial_dir, title="替换这一行的脚本",
                    filetypes=[("MacroFlow 脚本", "*.json"), ("所有文件", "*.*")],
                )
                if not path:
                    return
                step["script"] = display_path(Path(path))
        elif column == "#3":
            if step.get("kind") == "module":
                repeat_script = MacroScript(
                    name=self._workflow_step_name(step),
                    actions=[dict(step.get("action") or {})],
                )
            else:
                try:
                    repeat_script = load_script(resolve_path(step.get("script", "")))
                except Exception:
                    repeat_script = None
            values = WorkflowRepeatDialog(
                self.root,
                repeats=int(step.get("repeats", 1)),
                unlimited=bool(step.get("unlimited", False)),
                actions=repeat_script.actions if repeat_script else [],
                repeat_start_action_id=str(step.get("repeat_start_action_id", "")),
                script_name=self._workflow_step_name(step),
            ).show()
            if values is None:
                return
            step["repeats"] = values["repeats"]
            step["unlimited"] = values["unlimited"]
            if values.get("repeat_start_action_id"):
                step["repeat_start_action_id"] = values["repeat_start_action_id"]
            else:
                step.pop("repeat_start_action_id", None)
        elif column == "#4":
            value = DurationDialog(
                self.root, "开始前等待", "执行这一行前等待：",
                int(step.get("before_ms", 0)),
            ).show()
            if value is None:
                return
            step["before_ms"] = value
        elif column == "#5":
            value = DurationDialog(
                self.root, "重复间隔", "同一脚本相邻两次执行之间等待：",
                int(step.get("repeat_interval_ms", DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS)),
            ).show()
            if value is None:
                return
            step["repeat_interval_ms"] = value
        elif column == "#6":
            step["enabled"] = not bool(step.get("enabled", True))
        else:
            return
        self.rebuild_workflow_tree()
        self.workflow_tree.selection_set(str(index))
        self._persist_workflow_draft()
    def toggle_selected_workflow_step(self):
        indices = self._selected_workflow_indices()
        if not indices:
            self._notify("未选择任务", "请先单击选择一个工作流任务。")
            return
        workflow_steps = self._workflow_only_steps()
        indices = [index for index in indices if 0 <= index < len(workflow_steps)]
        if not indices:
            return
        enabled = not all(bool(workflow_steps[index].get("enabled", True)) for index in indices)
        for index in indices:
            workflow_steps[index]["enabled"] = enabled
        self.rebuild_workflow_tree()
        rows = tuple(str(index) for index in indices)
        self.workflow_tree.selection_set(*rows)
        self.workflow_tree.see(rows[0])
        self._persist_workflow_draft()
        state = "启用" if enabled else "禁用"
        self._set_status(f"已{state} {len(indices)} 个工作流任务", "success")
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
        step["repeats"] = remaining
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
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
        step["repeats"] = remaining
        workflow_path = getattr(self, "workflow_path", None)
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
        self._ui(self._refresh_workflow_repeat_ui)
        return remaining
    def _refresh_workflow_repeat_ui(self) -> None:
        """Refresh workflow controls after a worker-thread repeat is consumed."""
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
    def set_all_workflow_step_options(self):
        workflow_steps = self._workflow_only_steps()
        if not workflow_steps:
            self._notify("没有任务", "请先向工作流添加脚本或模块。")
            return
        first = workflow_steps[0]
        values = WorkflowBatchSettingsDialog(
            self.root,
            repeats=int(first.get("repeats", 1)),
            before_ms=int(first.get("before_ms", 0)),
            repeat_interval_ms=int(first.get("repeat_interval_ms", DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS)),
            unlimited=bool(first.get("unlimited", False)),
        ).show()
        if values is None:
            return
        for step in workflow_steps:
            step.update(values)
        selected = self._selected_workflow_index()
        self.rebuild_workflow_tree()
        if selected is not None:
            self.workflow_tree.selection_set(str(selected))
        self._persist_workflow_draft()
        self._set_status(f"已统一设置 {len(workflow_steps)} 个工作流任务", "success")
    def delete_workflow_step(self):
        indices = self._selected_workflow_indices()
        if not indices:
            return
        workflow_steps = self._workflow_only_steps()
        indices = [index for index in indices if 0 <= index < len(workflow_steps)]
        if not indices:
            return
        history = getattr(self, "workflow_delete_undo_stack", None)
        if history is None:
            history = self.workflow_delete_undo_stack = []
        for index in reversed(indices):
            removed = workflow_steps.pop(index)
            history.append((index, copy.deepcopy(removed)))
        self.workflow.steps = self._global_module_steps() + workflow_steps
        self.rebuild_workflow_tree()
        if workflow_steps:
            target = min(indices[0], len(workflow_steps) - 1)
            self.workflow_tree.selection_set(str(target))
            self.workflow_tree.see(str(target))
            self._update_workflow_selection_color()
        self._update_workflow_delete_undo_buttons()
        self._persist_workflow_draft()
        self._set_status(f"已删除 {len(indices)} 个工作流任务，可逐项撤销", "warning")
    def undo_delete_workflow_step(self):
        history = getattr(self, "workflow_delete_undo_stack", [])
        if not history:
            self._update_workflow_delete_undo_buttons()
            return
        index, removed = history.pop()
        workflow_steps = self._workflow_only_steps()
        target = min(max(0, int(index)), len(workflow_steps))
        workflow_steps.insert(target, copy.deepcopy(removed))
        self.workflow.steps = self._global_module_steps() + workflow_steps
        self.rebuild_workflow_tree()
        self.workflow_tree.selection_set(str(target))
        self.workflow_tree.see(str(target))
        self._update_workflow_selection_color()
        self._update_workflow_delete_undo_buttons()
        self._persist_workflow_draft()
        self._set_status(f"已撤销删除，恢复工作流第 {target + 1} 行", "success")
    def move_workflow_step(self, offset: int):
        index = self._selected_workflow_index()
        if index is None:
            return
        workflow_steps = self._workflow_only_steps()
        target = index + offset
        if not 0 <= target < len(workflow_steps):
            return
        workflow_steps[index], workflow_steps[target] = workflow_steps[target], workflow_steps[index]
        self.workflow.steps = self._global_module_steps() + workflow_steps
        self.rebuild_workflow_tree()
        self.workflow_tree.selection_set(str(target))
        self._persist_workflow_draft()
    def _workflow_drag_start(self, event):
        row = self.workflow_tree.identify_row(event.y)
        self.workflow_drag_index = int(row) if row else None
        self.workflow_was_dragged = False
        if row:
            self.workflow_tree.selection_set(row)
    def _workflow_drag_motion(self, event):
        if self.workflow_drag_index is None:
            return
        row = self.workflow_tree.identify_row(event.y)
        if not row:
            return
        target = int(row)
        source = self.workflow_drag_index
        workflow_steps = self._workflow_only_steps()
        if target == source or not (0 <= target < len(workflow_steps)):
            return
        step = workflow_steps.pop(source)
        workflow_steps.insert(target, step)
        self.workflow.steps = self._global_module_steps() + workflow_steps
        self.workflow_drag_index = target
        self.workflow_was_dragged = True
        self.rebuild_workflow_tree()
        self.workflow_tree.selection_set(str(target))
        self.workflow_tree.see(str(target))
    def _workflow_drag_end(self, event):
        self.workflow_drag_index = None
        self.workflow_was_dragged = False
        self._persist_workflow_draft()
    def _selected_global_index(self) -> int | None:
        selected = self.global_tree.selection()
        return int(selected[0]) if selected else None
    def _selected_global_indices(self) -> list[int]:
        return sorted({int(item) for item in self.global_tree.selection()})
    def _select_all_global_modules(self, _event=None):
        rows = self.global_tree.get_children()
        if rows:
            self.global_tree.selection_set(*rows)
            self.global_tree.focus(rows[0])
            self.global_tree.see(rows[0])
        return "break"
    def _show_global_context_menu(self, event):
        """Right-click a workflow global module to edit its module object."""
        row_id = self.global_tree.identify_row(event.y)
        if not row_id:
            return
        self.global_tree.selection_set(row_id)
        index = int(row_id)
        modules = self._global_module_steps()
        if index >= len(modules):
            return
        step = modules[index]
        menu = tk.Menu(
            self.root, tearoff=False,
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        module_key = self._workflow_global_module_key(step)
        if module_key:
            menu.add_command(
                label="✎ 在当前编辑器打开模块",
                command=lambda: self._open_workflow_global_module_in_editor(step),
            )
            menu.add_command(
                label="⇪ 在新窗口编辑模块",
                command=lambda: self._open_workflow_global_module_in_new_window(step),
            )
        else:
            return
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
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
    def _open_module_object_editor(self, module_key: str, workflow_step: dict | None = None):
        """Open one referenced module object directly in this MacroFlow window."""
        key = str(module_key).strip()
        obj = registered_module_object(key) if key else None
        if not obj:
            self._notify("模块对象不存在", f"模块对象已被移除或路径已改变：{key or '未指定'}")
            return
        if obj.get("category") == "special" or obj.get("pure_action"):
            self._notify("固定特殊模块", "该模块行为固定，无需也不能编辑。")
            return
        result = TemplateRegionFormDialog(
            self.root, key, object_dict=obj,
            category=str(obj.get("category", "workflow_global")),
        ).show()
        if result is None:
            return
        old_key, new_key, updated_obj = result
        update_module_object(new_key, updated_obj, old_key=old_key)
        if workflow_step is not None:
            config = dict(workflow_step.get("config") or {})
            current_key = str(config.get("module_key") or config.get("template", "")).strip()
            if config.get("module_ref") and current_key == key:
                config["module_key"] = new_key
                config["template"] = str(updated_obj.get("template", config.get("template", "")))
                workflow_step["config"] = config
                self.rebuild_workflow_tree()
                self._persist_workflow_draft()
        self._set_status(
            f"已保存全局模块：{updated_obj.get('name') or Path(new_key).stem}", "success",
        )
    def _open_workflow_global_module_in_editor(self, step: dict):
        self._open_module_object_editor(self._workflow_global_module_key(step), workflow_step=step)
    def _open_workflow_global_module_in_new_window(self, step: dict):
        key = self._workflow_global_module_key(step)
        if not key or not registered_module_object(key):
            self._notify("模块对象不存在", f"模块对象已被移除或路径已改变：{key or '未指定'}")
            return
        try:
            args = [sys.executable]
            if not getattr(sys, "frozen", False):
                args.append(str(Path(__file__).resolve()))
            args += ["--edit-module", key]
            spawn_new_instance(args)
        except Exception as exc:
            self._notify("无法新开窗口", str(exc))
            return
        self._log(f"已在新窗口打开全局模块：{key}")
        self._set_status("已在新窗口打开全局模块", "success")
    def edit_selected_global_module(self):
        index = self._selected_global_index()
        modules = self._global_module_steps()
        if index is None or not 0 <= index < len(modules):
            self._notify("未选择全局模块", "请先单击选择一个全局模块。")
            return
        step = modules[index]
        replacement = ModulePickerDialog(
            self.root, categories=("workflow_global",),
        ).show()
        if not replacement:
            return
        step["config"] = dict(replacement)
        step["script"] = ""
        self.rebuild_workflow_tree()
        self.global_tree.selection_set(str(index))
        self._persist_workflow_draft()
        self._set_status("已更换工作流全局模块对象", "success")
    def toggle_selected_global_module(self):
        indices = self._selected_global_indices()
        modules = self._global_module_steps()
        indices = [index for index in indices if 0 <= index < len(modules)]
        if not indices:
            self._notify("未选择全局模块", "请先单击选择一个全局模块。")
            return
        enabled = not all(bool(modules[index].get("enabled", True)) for index in indices)
        for index in indices:
            modules[index]["enabled"] = enabled
        self.rebuild_workflow_tree()
        rows = tuple(str(index) for index in indices)
        self.global_tree.selection_set(*rows)
        self.global_tree.see(rows[0])
        self._persist_workflow_draft()
        state = "启用" if enabled else "禁用"
        self._set_status(f"已{state} {len(indices)} 个全局模块", "success")
    def delete_global_module(self):
        indices = self._selected_global_indices()
        modules = self._global_module_steps()
        indices = [index for index in indices if 0 <= index < len(modules)]
        if not indices:
            self._notify("未选择全局模块", "请先单击选择一个全局模块。")
            return
        history = getattr(self, "global_delete_undo_stack", None)
        if history is None:
            history = self.global_delete_undo_stack = []
        for index in reversed(indices):
            removed = modules.pop(index)
            history.append((index, copy.deepcopy(removed)))
        self.workflow.steps = modules + self._workflow_only_steps()
        self.rebuild_workflow_tree()
        if modules:
            target = min(indices[0], len(modules) - 1)
            self.global_tree.selection_set(str(target))
            self.global_tree.see(str(target))
        self._update_workflow_delete_undo_buttons()
        self._persist_workflow_draft()
        self._set_status(f"已删除 {len(indices)} 个全局模块，可逐项撤销", "warning")
    def undo_delete_global_module(self):
        history = getattr(self, "global_delete_undo_stack", [])
        if not history:
            self._update_workflow_delete_undo_buttons()
            return
        index, removed = history.pop()
        modules = self._global_module_steps()
        target = min(max(0, int(index)), len(modules))
        modules.insert(target, copy.deepcopy(removed))
        self.workflow.steps = modules + self._workflow_only_steps()
        self.rebuild_workflow_tree()
        self.global_tree.selection_set(str(target))
        self.global_tree.see(str(target))
        self._update_workflow_delete_undo_buttons()
        self._persist_workflow_draft()
        self._set_status(f"已撤销删除，恢复全局模块第 {target + 1} 行", "success")
    def _update_workflow_delete_undo_buttons(self):
        workflow_button = getattr(self, "workflow_delete_undo_button", None)
        if workflow_button is not None:
            workflow_button.configure(
                state="normal" if getattr(self, "workflow_delete_undo_stack", []) else "disabled",
            )
        global_button = getattr(self, "global_delete_undo_button", None)
        if global_button is not None:
            global_button.configure(
                state="normal" if getattr(self, "global_delete_undo_stack", []) else "disabled",
            )
    def _clear_workflow_delete_history(self):
        self.workflow_delete_undo_stack = []
        self.global_delete_undo_stack = []
        self._update_workflow_delete_undo_buttons()
    def choose_workflow_start(self):
        selected = ScheduleDialog(self.root, self.workflow_start_var.get()).show()
        if selected is not None:
            self.workflow_start_var.set(selected)
            self._persist_workflow_draft()
    def _toggle_workflow_start_delay_control(self, persist: bool = True):
        entry = getattr(self, "workflow_start_delay_entry", None)
        enabled_var = getattr(self, "workflow_start_delay_enabled_var", None)
        enabled = bool(enabled_var.get()) if enabled_var is not None else False
        if entry is not None:
            entry.configure(state="normal" if enabled else "disabled")
        if persist:
            self._persist_workflow_draft()
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
    def run_workflow_from_selected(self):
        index = self._selected_workflow_index()
        if index is None:
            self._notify("未选择任务", "请先单击选择要开始执行的工作流任务。")
            return
        self.run_workflow(start_index=index)
    def run_workflow(self, start_index: int = 0, start_repeat: int = 0,
                     resume_action_index: int | None = None,
                     preserve_global_rearm_locks: bool = False,
                     test_mode: bool | None = None,
                     suppress_start_sound: bool = False):
        return self._run_detection_entrypoint(
            self._run_workflow_impl, start_index, start_repeat,
            resume_action_index, preserve_global_rearm_locks, test_mode,
            suppress_start_sound,
        )
    def _run_workflow_impl(self, start_index: int = 0, start_repeat: int = 0,
                      resume_action_index: int | None = None,
                      preserve_global_rearm_locks: bool = False,
                      test_mode: bool | None = None,
                      suppress_start_sound: bool = False):
        recorder = getattr(self, "recorder", None)
        if recorder is not None and recorder.running:
            self.stop_recording()
        if self.worker and self.worker.is_alive():
            self._notify("正在运行", "已有脚本或工作流正在执行。")
            return
        workflow_steps = self._workflow_only_steps()
        global_modules = [dict(step) for step in self._global_module_steps()]
        if not workflow_steps and not global_modules:
            self._notify("没有步骤", "请先向工作流添加脚本或模块。")
            return
        if resume_action_index is None:
            self._begin_detection_run()
        self._ensure_detection_worker()
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
        else:
            # 全局模块断点恢复与“重新执行工作流”属于同一次运行，不重复等待。
            start_delay_seconds = 0
        self._workflow_snapshot()
        self._persist_workflow_draft()
        self.rebuild_workflow_tree()
        missing_rows = [
            index + 1 for index, step in enumerate(workflow_steps)
            if index >= start_index
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
        if start_text:
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
        if start_index > 0 and start_index < len(workflow_steps):
            # 从选中行运行时，优先使用所选工作流脚本自己的前置窗口，
            # 而不是编辑器当前打开的另一份脚本配置。
            workflow_activation_enabled, workflow_activation_signature = (
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
        if not preserve_global_rearm_locks:
            self._clear_global_detect_rearm_locks()
        self.workflow_stop.clear()
        if not suppress_start_sound:
            self._sound("run_start")
        self._hide_main_for_execution()
        # 只在用户/启动项真正开始一次工作流时归零。全局模块触发后的
        # 断点恢复属于同一次运行，必须沿用原始开始时间。
        self._reset_execution_clock_for_new_run(resume_action_index)
        steps = [dict(step) for step in workflow_steps]
        if start_index:
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
                  workflow_activation_allowed),
            daemon=True,
        )
        self.worker.start()
        self._show_execution_mini()
        if self.workflow_test_mode_active:
            self._append_mini_step("工作流测试模式：普通计次行最多执行 1 次，且不扣减剩余次数。")
            self._log("工作流测试模式已启用：普通计次行最多执行 1 次；不计次数行保持原样。")
        if start_index:
            self._append_mini_step(f"从第 {start_index + 1}/{len(steps)} 行开始执行工作流。")
        else:
            self._append_mini_step(f"开始执行工作流，共 {len(steps)} 个步骤。")
    def _run_workflow_worker(self, steps, start_at, hwnd,
                             focus_enabled=False, activate_target=True,
                             start_index=0, global_modules=None, start_repeat=0,
                             resume_action_index=None, workflow_activation_hwnd=None,
                             test_mode=False, start_delay_seconds=0,
                             activation_allowed=True):
        try:
            if start_at and start_at > datetime.now():
                seconds = (start_at - datetime.now()).total_seconds()
                self._ui(self._set_status, f"工作流已排期：{start_at:%m-%d %H:%M:%S}", "warning")
                self._ui(self._log, f"工作流等待至 {start_at:%Y-%m-%d %H:%M:%S} 开始。")
                if self.workflow_stop.wait(seconds):
                    return
            if start_delay_seconds > 0:
                delay_text = f"工作流启动延时 {start_delay_seconds} 秒，等待结束后执行。"
                self._ui(self._set_status, delay_text, "warning")
                self._ui(self._set_execution_progress, f"{delay_text} · F12 停止")
                self._ui(self._append_mini_step, delay_text)
                self._ui(self._log, delay_text)
                if self.workflow_stop.wait(start_delay_seconds):
                    return
                self._ui(self._log, "工作流启动延时结束，开始执行。")
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
            if not test_mode and counted_steps \
                    and all(int(step.get("repeats", 1)) <= 0 for step in counted_steps):
                # 所有计次脚本都已执行完毕：整个工作流结束，不再循环执行不计次数脚本。
                self._clear_global_guards()
                self._ui(self._set_status, "工作流执行完成", "success")
                self._ui(self._append_mini_step, "所有计次脚本已执行完毕，工作流结束。")
                self._ui(self._log, "所有计次脚本已执行完毕，工作流结束。")
                self._ui(self._sound, "run_done")
                return
            for module in (global_modules or []):
                if self.workflow_stop.is_set():
                    return
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
                    return
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
                    # 守卫注册与播放器评估同线程（worker），直接调用避免跨线程竞态。
                    self._activate_global_detect_from_config(config, module)
                    self._ui(self._append_mini_step, "全局检测模块已启用。")
                else:
                    self._ui(
                        self._log,
                        f"全局模块 {workflow_script_name(script_value) or '未配置'}："
                        "未找到全局检测配置，未启用检测。",
                    )
            # “执行前置窗口”是整个工作流的一次性准备动作，只交给第一个实际
            # 执行的脚本。后续脚本以及全局模块断点恢复都直接以目标窗口为准。
            pending_activation_hwnd = workflow_activation_hwnd
            pending_activation_prepared = activation_prepared
            activation_consumed = False
            for index in range(start_index, len(steps)):
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
                if not unlimited and planned_repeats <= 0:
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
                repeats = 1 if unlimited else (min(planned_repeats, 1) if test_mode else planned_repeats)
                repeat_desc = (
                    "不计次数，每次到达执行 1 次"
                    if unlimited else
                    f"测试执行 {repeats} 次（不扣减）" if test_mode else
                    f"执行 {repeats} 次"
                )
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
                    start_repeat=start_repeat if index == start_index else 0,
                    resume_action_index=(
                        resume_action_index if index == start_index else None
                    ),
                    repeat_start_action_id=repeat_start_action_id,
                    workflow_context=True,
                    on_action=lambda next_index, _total: self._record_workflow_action(
                        next_index,
                    ),
                    on_repeat=lambda current, total, number=script_number, name=script.name: self._record_workflow_repeat(
                        current, total, number, len(steps), name,
                    ),
                    on_repeat_complete=lambda _current, _total, row=index, testing=test_mode: self._consume_workflow_repeat_from_worker(
                        row, testing,
                    ),
                )
                played_any_step = True
                if step_activation is not None:
                    activation_consumed = True
                pending_activation_hwnd = None
                pending_activation_prepared = False
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
            if not getattr(self, "workflow_restart_requested", False):
                self._leave_focus_mode()
                self._ui(self._finish_execution_visibility)
                self.workflow_test_mode_active = False
