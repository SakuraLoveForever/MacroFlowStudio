from __future__ import annotations

from macroflow.core.models import ACTION_ID_KEY, DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS, MacroScript, Workflow, ensure_workflow_step_ids, new_action_id
from macroflow.core.resolution import build_resolution_action
from macroflow.ui.dialogs.actions import DurationDialog, ScheduleDialog
from macroflow.ui.dialogs.foreground_processes import ForegroundProcessesDialog
from macroflow.ui.dialogs.workflow_plugins import ProcessAbsencePluginDialog
from macroflow.core.workflow_plugins import PROCESS_ABSENCE_RESTART, process_absence_label
from macroflow.ui.dialogs.app_dialogs import (
    WorkflowBatchSettingsDialog,
    WorkflowRepeatDialog,
    WorkflowResolutionDialog,
)
from macroflow.ui.dialogs.module_objects import ModulePickerDialog, TemplateRegionFormDialog
from pathlib import Path
from macroflow.core.storage import WORKFLOWS_DIR, display_path, safe_name, load_script, load_workflow, registered_module_object, resolve_path, save_workflow, update_module_object
import copy
from tkinter import filedialog, simpledialog
import sys
import tkinter as tk
import tkinter.font as tkfont

from .base import _UI_SCALE, workflow_script_name
from .constants import COLOR_SURFACE, COLOR_TEXT, FONT_FAMILY, FONT_SUBTITLE, WORKFLOW_TREE_COLUMNS
from .script_edit import module_objects_snapshot
from .startup import instance_command, spawn_new_instance
from .workflow_history import WorkflowEditHistory, workflow_edit

# 已经量过列宽的 (tree, column)：普通编辑刷新不再重测整列文本。
_AUTOSIZED_COLUMNS: set[tuple[int, str]] = set()


class WorkflowMixin:
    """Studio authoring behavior."""

    @workflow_edit
    def edit_foreground_minimize_processes(self):
        settings = ForegroundProcessesDialog(
            self.root, self.workflow.foreground_minimize_processes,
            self.workflow.foreground_minimize_interval_ms,
        ).show()
        if settings is not None:
            names, interval_ms = settings
            self.workflow.foreground_minimize_processes = names
            self.workflow.foreground_minimize_interval_ms = interval_ms
            self._schedule_workflow_draft_save()

    def _refresh_workflow_segment_bar(self, _event=None) -> None:
        """工作流表格选中一段步骤时，在最左边画出那根实心竖条。"""
        self._refresh_row_segment_bar(self.workflow_tree, "workflow_segment_painted")

    def _workflow_row_values(self, step: dict, index: int,
                             module_objects: dict) -> tuple[tuple, tuple]:
        """一行工作流步骤的显示值与标签。"""
        is_module = step.get("kind") == "module"
        module_enabled = self._workflow_module_enabled(step, module_objects)
        unlimited = bool(step.get("unlimited", False))
        exhausted = not unlimited and int(step.get("repeats", 1)) <= 0
        if is_module:
            module_obj = self._module_object(self._workflow_module_key(step), module_objects)
            missing = module_obj is None
            script_label = f"◆ {self._workflow_step_name(step, module_objects)}"
        else:
            missing = not resolve_path(step.get("script", "")).is_file()
            script_label = workflow_script_name(step.get("script", ""))
        if missing:
            script_label = f"⚠ {script_label}  ·  {'模块不存在' if is_module else '文件不存在'}"
        original_label = "∞" if unlimited else step.get("original_repeats", step.get("repeats", 1))
        repeat_label = "∞" if unlimited else step.get("repeats", 1)
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
        if is_module and not module_enabled:
            tags = ("module_disabled",)
        elif not bool(step.get("enabled", True)):
            tags = ("disabled",)
        elif unlimited:
            tags = ("unlimited",)
        elif exhausted:
            tags = ("exhausted",)
        elif missing:
            tags = ("missing",)
        else:
            tags = ()
        values = (
            "", index + 1, script_label, original_label, repeat_label,
            "" if unlimited else "↶ 恢复",
            f"{step.get('before_ms', 0)} ms",
            f"{step.get('repeat_interval_ms', DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS)} ms",
            status_label,
        )
        return values, tags

    def rebuild_workflow_tree(self):
        """整表重建：打开 / 新建 / 替换工作流，或列表结构整体变化时使用。"""
        self._reset_row_segment_bar("workflow_segment_painted")
        self.workflow_tree.delete(*self.workflow_tree.get_children())
        module_objects = module_objects_snapshot()
        self.workflow_module_objects = module_objects
        workflow_steps = self._workflow_only_steps()
        script_labels = []
        for index, step in enumerate(workflow_steps):
            values, tags = self._workflow_row_values(step, index, module_objects)
            script_labels.append(values[2])
            self.workflow_tree.insert(
                "", "end", iid=str(index), values=values, tags=tags,
            )
        if workflow_steps:
            self.empty_workflow_hint.place_forget()
        else:
            self.empty_workflow_hint.place(relx=0.5, rely=0.45, anchor="center")
        self._autosize_tree_column(self.workflow_tree, "script", 220, script_labels)
        self.rebuild_global_tree(module_objects)

    def _set_workflow_row(self, row: int, module_objects: dict | None = None) -> None:
        """按当前数据重画一行工作流步骤。"""
        steps = self._workflow_only_steps()
        if not 0 <= row < len(steps):
            return
        iid = str(row)
        if not self.workflow_tree.exists(iid):
            return
        values, tags = self._workflow_row_values(
            steps[row], row,
            module_objects if module_objects is not None
            else getattr(self, "workflow_module_objects", None) or module_objects_snapshot(),
        )
        column_names = tuple(
            column[0] if isinstance(column, (tuple, list)) else column
            for column in WORKFLOW_TREE_COLUMNS
        )
        for column, value in zip(column_names, values):
            if column != "mark":
                self.workflow_tree.set(iid, column, value)
        self.workflow_tree.item(iid, tags=tags)

    def _refresh_one_workflow_row(self, row: int) -> None:
        """单行步骤变化：只重画这一行，不重建全局模块表。"""
        self._set_workflow_row(row)

    def rebuild_global_tree(self, module_objects: dict | None = None):
        tree = getattr(self, "global_tree", None)
        if tree is None:
            return
        if module_objects is None:
            module_objects = getattr(self, "workflow_module_objects", None) \
                or module_objects_snapshot()
        self.workflow_module_objects = module_objects
        tree.delete(*tree.get_children())
        module_labels = []
        for index, step in enumerate(self._global_module_steps()):
            row_enabled = bool(step.get("enabled", True))
            registry_state = self._workflow_global_module_registry_state(step)
            enabled = row_enabled and registry_state in (None, "enabled")
            config = step.get("config") or {}
            module_label = (
                process_absence_label(config) if config.get("plugin") == PROCESS_ABSENCE_RESTART
                else self._global_module_label(step, module_objects)
            )
            module_labels.append(module_label)
            if registry_state == "missing":
                status_label = "⚠ 模块不存在"
            elif registry_state == "disabled":
                status_label = "● 模块已禁用"
            elif not row_enabled:
                status_label = "● 已禁用"
            else:
                status_label = "✓ 插件" if config.get("plugin") else "✓ 全局"
            tree.insert(
                "", "end", iid=str(index),
                values=(index + 1, module_label, status_label),
                tags=("disabled",) if not enabled else ("global",),
            )
        if self._global_module_steps():
            self.empty_global_hint.place_forget()
        else:
            self.empty_global_hint.place(relx=0.5, rely=0.45, anchor="center")
        self._autosize_tree_column(tree, "module", 240, module_labels)


    def _measure_font(self):
        """测量用字体：缓存一份，字号 / DPI 变了才重建。

        每次测量都新建 ``tkfont.Font`` 会让自动列宽变成大列表上的主要开销；
        缓存后按 (family, size, scaling) 失效，DPI 或字号改变时自然刷新。
        """
        signature = (FONT_FAMILY, FONT_SUBTITLE, round(_UI_SCALE, 3))
        cached = getattr(self, "_measure_font_cache", None)
        if cached is not None and cached[0] == signature:
            return cached[1]
        try:
            font = tkfont.Font(family=FONT_FAMILY, size=FONT_SUBTITLE)
        except RuntimeError:
            font = None
        self._measure_font_cache = (signature, font)
        return font

    def _measure_text_width(self, text: str) -> int:
        font = self._measure_font()
        if font is None:
            # No Tk root available (unit tests): estimate CJK vs ASCII widths.
            return sum(14 if ord(ch) > 0x2E7F else 7 for ch in str(text))
        return font.measure(str(text))

    def _autosize_tree_column(self, tree, column: str, min_width: int, texts: list[str],
                              force: bool = True) -> None:
        """Widen a tree column so its longest text stays fully visible.

        全量测量只在打开文档 / 首次显示时跑（``force=True``）；普通编辑刷新复用
        上次量好的列宽，不为了几个字符把整列文本重新量一遍。
        """
        key = (id(tree), column)
        if not force and key in _AUTOSIZED_COLUMNS:
            return
        needed = max((self._measure_text_width(text) for text in texts), default=0)
        tree.column(column, width=max(min_width, min(1600, needed + 40)), minwidth=min_width)
        _AUTOSIZED_COLUMNS.add(key)

    def new_workflow(self):
        self.workflow = Workflow()
        self.workflow_path = None
        self.workflow_name_var.set(self.workflow.name)
        self.workflow_start_var.set("")
        self._sync_workflow_resolution_ui()
        self.rebuild_workflow_tree()
        self._clear_workflow_history()
        self._persist_workflow_draft()
        self._workflow_saved()

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
        self.workflow_name_var.set(workflow.name)
        self.workflow_start_var.set(workflow.start_at)
        self._sync_workflow_resolution_ui()
        self.rebuild_workflow_tree()
        self._clear_workflow_history()
        self._persist_workflow_draft()
        self._log(log_text)
        self._workflow_saved()

    def _sync_workflow_resolution_ui(self):
        names = []
        for phase, field in (("start", "start_resolution"), ("end", "end_resolution")):
            action = getattr(self.workflow, field, None) or {}
            name = str(action.get("name") or "不修改")
            variable = getattr(self, f"workflow_{phase}_resolution_var", None)
            if variable is not None:
                variable.set(name)
            names.append(name)
        summary = getattr(self, "workflow_resolution_summary", None)
        if summary is not None:
            parts = []
            if names[0] != "不修改":
                parts.append(f"启动 {names[0]}")
            if names[1] != "不修改":
                parts.append(f"结束后 {names[1]}")
            summary.configure(
                text=" · ".join(parts) if parts else "不修改（保持系统当前设置）")

    @workflow_edit
    def _configure_workflow_resolution(self):
        """弹窗设置工作流启动 / 结束分辨率，页面上只保留摘要入口。"""
        result = WorkflowResolutionDialog(
            self.root,
            self.workflow_start_resolution_var.get(),
            self.workflow_end_resolution_var.get(),
            styles=self.resolution_styles,
            edit_styles=self._configure_resolution_styles,
        ).show()
        if result is None:
            return
        for phase, name in zip(("start", "end"), result):
            setattr(
                self.workflow, f"{phase}_resolution",
                None if name == "不修改" else build_resolution_action(
                    {"resolution_styles": self.resolution_styles}, name,
                ),
            )
        self._sync_workflow_resolution_ui()
        self._persist_workflow_draft()

    @workflow_edit
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
            self._workflow_saved()
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


    def _add_selected_workflow_step(self):
        handlers = (
            self.add_current_script_step, self.add_script_step, self.add_workflow_module_step,
        )
        handlers[self.workflow_add_kind_combo.current()]()

    def add_current_script_step(self):
        path = self.save_current_script()
        if path:
            self._add_or_insert_workflow_step(path)

    def add_script_step(self):
        path = filedialog.askopenfilename(
            parent=self.root, initialdir=self._script_category_dir(), title="选择脚本",
            filetypes=[("MacroFlow 脚本 / Python", "*.json *.py"), ("所有文件", "*.*")],
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

    @workflow_edit
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

    def add_workflow_plugin(self):
        config = ProcessAbsencePluginDialog(self.root).show()
        if config is None:
            return
        self._append_global_module(config=config)
        index = len(self._global_module_steps()) - 1
        self.global_tree.selection_set(str(index))
        self.global_tree.see(str(index))
        self._set_status("已添加进程缺失重启插件", "success")

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

    @workflow_edit
    def _append_global_module(self, config: dict | None = None, script: str = "",
                              refresh: bool = True):
        step = {
            "kind": "global_module",
            "script": script,
            "repeats": 1,
            "original_repeats": 1,
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
            "original_repeats": 1,
            "before_ms": 0,
            "repeat_interval_ms": DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
            "unlimited": False,
            "enabled": True,
            "step_id": new_action_id(),
        }

    @workflow_edit
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
            "original_repeats": 1,
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

    @workflow_edit
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

    @workflow_edit
    def _edit_workflow_cell(self, event):
        # 最左边两列是「片段竖条」与「步骤」序号，双击它们不编辑任何单元格。
        row = self.workflow_tree.identify_row(event.y)
        column = self.workflow_tree.identify_column(event.x)
        if not row or column in {"#1", "#2"}:
            return
        index = int(row)
        workflow_steps = self._workflow_only_steps()
        if not 0 <= index < len(workflow_steps):
            return
        step = workflow_steps[index]
        if column == "#3":
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
                    filetypes=[("MacroFlow 脚本 / Python", "*.json *.py"), ("所有文件", "*.*")],
                )
                if not path:
                    return
                step["script"] = display_path(Path(path))
        elif column in {"#4", "#5"}:
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
                repeats=int(step.get("original_repeats", step.get("repeats", 1))) if column == "#4" else int(step.get("repeats", 1)),
                unlimited=bool(step.get("unlimited", False)),
                actions=repeat_script.actions if repeat_script else [],
                repeat_start_action_id=str(step.get("repeat_start_action_id", "")),
                script_name=self._workflow_step_name(step),
            ).show()
            if values is None:
                return
            step["repeats"] = values["repeats"]
            if column == "#4":
                step["original_repeats"] = values["repeats"]
            step["unlimited"] = values["unlimited"]
            if values.get("repeat_start_action_id"):
                step["repeat_start_action_id"] = values["repeat_start_action_id"]
            else:
                step.pop("repeat_start_action_id", None)
        elif column == "#6":
            self.restore_workflow_step_count(index)
            return
        elif column == "#7":
            value = DurationDialog(
                self.root, "开始前等待", "执行这一行前等待：",
                int(step.get("before_ms", 0)),
            ).show()
            if value is None:
                return
            step["before_ms"] = value
        elif column == "#8":
            value = DurationDialog(
                self.root, "重复间隔", "同一脚本相邻两次执行之间等待：",
                int(step.get("repeat_interval_ms", DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS)),
            ).show()
            if value is None:
                return
            step["repeat_interval_ms"] = value
        elif column == "#9":
            step["enabled"] = not bool(step.get("enabled", True))
        else:
            return
        # 只重画被改的这一行：行数与其它行都没变，不需要重建整张表。
        self._refresh_one_workflow_row(index)
        self.workflow_tree.selection_set(str(index))
        self._update_workflow_selection_color()
        self._persist_workflow_draft()

    @workflow_edit
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
            self._refresh_one_workflow_row(index)
        rows = tuple(str(index) for index in indices)
        self.workflow_tree.selection_set(*rows)
        self.workflow_tree.see(rows[0])
        self._persist_workflow_draft()
        state = "启用" if enabled else "禁用"
        self._set_status(f"已{state} {len(indices)} 个工作流任务", "success")

    @workflow_edit
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
            if "repeats" in values:
                step["original_repeats"] = values["repeats"]
        selected = self._selected_workflow_index()
        self.rebuild_workflow_tree()
        if selected is not None:
            self.workflow_tree.selection_set(str(selected))
        self._persist_workflow_draft()
        self._set_status(f"已统一设置 {len(workflow_steps)} 个工作流任务", "success")

    @workflow_edit
    def restore_workflow_step_count(self, index: int) -> None:
        steps = self._workflow_only_steps()
        if not 0 <= index < len(steps) or steps[index].get("unlimited", False):
            return
        step = steps[index]
        step["repeats"] = int(step.get("original_repeats", step.get("repeats", 1)))
        self._refresh_one_workflow_row(index)
        self._persist_workflow_draft()

    def _click_workflow_restore(self, event) -> None:
        if self.workflow_tree.identify_column(event.x) != "#6":
            return
        row = self.workflow_tree.identify_row(event.y)
        if row:
            self.restore_workflow_step_count(int(row))

    @workflow_edit
    def restore_all_workflow_counts(self) -> None:
        steps = self._workflow_only_steps()
        for step in steps:
            if not step.get("unlimited", False):
                step["repeats"] = int(step.get("original_repeats", step.get("repeats", 1)))
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        self._set_status("已恢复全部工作流计次行", "success")

    @workflow_edit
    def delete_workflow_step(self):
        indices = self._selected_workflow_indices()
        if not indices:
            return
        workflow_steps = self._workflow_only_steps()
        indices = [index for index in indices if 0 <= index < len(workflow_steps)]
        if not indices:
            return
        for index in reversed(indices):
            workflow_steps.pop(index)
        self.workflow.steps = self._global_module_steps() + workflow_steps
        self.rebuild_workflow_tree()
        if workflow_steps:
            target = min(indices[0], len(workflow_steps) - 1)
            self.workflow_tree.selection_set(str(target))
            self.workflow_tree.see(str(target))
            self._update_workflow_selection_color()
        self._persist_workflow_draft()
        self._set_status(f"已删除 {len(indices)} 个工作流任务，可撤销", "warning")

    def move_workflow_step(self, offset: int):
        selected = self._selected_workflow_indices()
        if not selected:
            return
        self._move_selected_workflow_steps(selected, selected[0] + offset)

    def move_workflow_step_to_row(self):
        selected = self._selected_workflow_indices()
        if not selected:
            return
        if selected != list(range(selected[0], selected[-1] + 1)):
            self._notify("无法移动", "请选择连续的多行工作流任务后再移动。")
            return
        row = simpledialog.askinteger(
            "移动工作流任务", "将选中任务的第一行移到第几行？",
            parent=self.root, initialvalue=selected[0] + 1,
            minvalue=1, maxvalue=len(self._workflow_only_steps()) - len(selected) + 1,
        )
        if row is not None:
            self._move_selected_workflow_steps(selected, row - 1)

    @workflow_edit
    def _move_selected_workflow_steps(self, selected: list[int], target: int):
        if selected != list(range(selected[0], selected[-1] + 1)):
            self._notify("无法移动", "请选择连续的多行工作流任务后再移动。")
            return
        workflow_steps = self._workflow_only_steps()
        if not 0 <= target <= len(workflow_steps) - len(selected) or target == selected[0]:
            return
        block = workflow_steps[selected[0]:selected[-1] + 1]
        del workflow_steps[selected[0]:selected[-1] + 1]
        workflow_steps[target:target] = block
        self.workflow.steps = self._global_module_steps() + workflow_steps
        self.rebuild_workflow_tree()
        rows = tuple(str(index) for index in range(target, target + len(block)))
        self.workflow_tree.selection_set(*rows)
        self.workflow_tree.see(rows[0])
        self._update_workflow_selection_color()
        self._persist_workflow_draft()

    @workflow_edit
    def _workflow_drag_start(self, event):
        row = self.workflow_tree.identify_row(event.y)
        modifiers = getattr(event, "state", 0) & 0x0005
        selected = self.workflow_tree.selection()
        self.workflow_drag_index = int(row) if row and not modifiers else None
        self.workflow_was_dragged = False
        if row and not modifiers and row in selected and len(selected) > 1:
            self.workflow_drag_index = None
            return "break"

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
        if (step.get("config") or {}).get("plugin") == PROCESS_ABSENCE_RESTART:
            menu.add_command(label="✎ 编辑插件", command=self.edit_selected_global_module)
        elif module_key:
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

    @workflow_edit
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
            args = instance_command()
            args += ["--edit-module", key]
            spawn_new_instance(args)
        except Exception as exc:
            self._notify("无法新开窗口", str(exc))
            return
        self._log(f"已在新窗口打开全局模块：{key}")
        self._set_status("已在新窗口打开全局模块", "success")

    @workflow_edit
    def edit_selected_global_module(self):
        index = self._selected_global_index()
        modules = self._global_module_steps()
        if index is None or not 0 <= index < len(modules):
            self._notify("未选择全局模块", "请先单击选择一个全局模块。")
            return
        step = modules[index]
        if (step.get("config") or {}).get("plugin") == PROCESS_ABSENCE_RESTART:
            config = ProcessAbsencePluginDialog(self.root, step["config"]).show()
            if config is not None:
                step["config"] = config
                self.rebuild_workflow_tree()
                self.global_tree.selection_set(str(index))
                self._persist_workflow_draft()
                self._set_status("已更新进程缺失重启插件", "success")
            return
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

    @workflow_edit
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

    @workflow_edit
    def delete_global_module(self):
        indices = self._selected_global_indices()
        modules = self._global_module_steps()
        indices = [index for index in indices if 0 <= index < len(modules)]
        if not indices:
            self._notify("未选择全局模块", "请先单击选择一个全局模块。")
            return
        for index in reversed(indices):
            modules.pop(index)
        self.workflow.steps = modules + self._workflow_only_steps()
        self.rebuild_workflow_tree()
        if modules:
            target = min(indices[0], len(modules) - 1)
            self.global_tree.selection_set(str(target))
            self.global_tree.see(str(target))
        self._persist_workflow_draft()
        self._set_status(f"已删除 {len(indices)} 个全局模块，可撤销", "warning")

    def _workflow_edit_snapshot(self):
        fields = {}
        for name in ('workflow_name_var', 'workflow_start_var'):
            variable = self.__dict__.get(name)
            if variable is not None:
                fields[name] = variable.get()
        return {'workflow': vars(self.workflow), 'fields': fields}

    def _clear_workflow_history(self):
        self._workflow_history = WorkflowEditHistory(self._workflow_edit_snapshot())
        self._update_workflow_undo_buttons()

    def _record_workflow_edit(self):
        history = self.__dict__.get('_workflow_history')
        if history is None or self.__dict__.get('workflow_was_dragged', False):
            return
        worker = self.__dict__.get('worker')
        if worker is not None and worker.is_alive():
            # Playback changes remaining counts; never undo across those changes.
            self._clear_workflow_history()
            return
        history.record(self._workflow_edit_snapshot())
        self._update_workflow_undo_buttons()

    def _update_workflow_undo_buttons(self):
        history = self.__dict__.get('_workflow_history')
        worker = self.__dict__.get('worker')
        enabled = bool(history is not None and history.undo) and not (worker is not None and worker.is_alive())
        for name in ('workflow_undo_button', 'global_undo_button'):
            button = self.__dict__.get(name)
            if button is not None:
                button.configure(state='normal' if enabled else 'disabled')

    def undo_workflow_edit(self, _event=None):
        worker = self.__dict__.get('worker')
        if worker is not None and worker.is_alive():
            return 'break'
        self._record_workflow_edit()  # Include text still waiting for its draft timer.
        history = self.__dict__.get('_workflow_history')
        state = history.step() if history is not None else None
        if state is None:
            return 'break'
        selections = {name: tuple(getattr(self, name).selection())
                      for name in ('workflow_tree', 'global_tree')}
        self.workflow = Workflow(**state['workflow'])
        for name, value in state['fields'].items():
            getattr(self, name).set(value)
        self._sync_workflow_resolution_ui()
        self.rebuild_workflow_tree()
        for name, steps in (('workflow_tree', self._workflow_only_steps()),
                            ('global_tree', self._global_module_steps())):
            rows = tuple(str(min(int(row), len(steps) - 1)) for row in selections[name] if steps)
            if rows:
                tree = getattr(self, name)
                tree.selection_set(*dict.fromkeys(rows))
                tree.see(rows[0])
        self._update_workflow_selection_color()
        self._persist_workflow_draft()
        self._update_workflow_undo_buttons()
        save_state = self.__dict__.get('_workflow_save_state')
        if save_state is not None:
            save_state.refresh()
        self._set_status('已撤销上一次工作流修改', 'success')
        return 'break'

    @workflow_edit
    def choose_workflow_start(self):
        selected = ScheduleDialog(self.root, self.workflow_start_var.get()).show()
        if selected is not None:
            self.workflow_start_var.set(selected)
            self._persist_workflow_draft()

    def run_workflow_from_selected(self):
        index = self._selected_workflow_index()
        if index is None:
            self._notify("未选择任务", "请先单击选择要开始执行的工作流任务。")
            return
        self.run_workflow(start_index=index, partial_run=True)

