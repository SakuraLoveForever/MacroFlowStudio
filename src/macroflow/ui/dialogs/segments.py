from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL,
    ensure_action_ids, recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
    special_action_label,
)
from pathlib import Path
from macroflow.core.storage import (
    BASE_DIR, DIRECTION_SCRIPTS_DIR, IMAGES_DIR, SCRIPTS_DIR, display_path,
    DEFAULT_MODULE_INTERVAL_MS, DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    load_app_settings, load_module_images_dir, load_module_objects,
    load_script, load_template_regions,
    module_image_inventory, module_objects_by_category,
    registered_module_object, resolve_path, save_module_images_dir, save_module_objects,
    save_template_regions, save_script, script_category_for_path, update_module_object,
)
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk

from .base import (
    COLOR_BLUE_SELECTION,
    COLOR_MUTED,
    COLOR_SURFACE,
    COLOR_TEXT,
    FONT_BODY,
    FONT_FAMILY,
    ModalDialog,
    SEGMENT_DEPTH_LIMIT,
    dark_checkbutton,
    pad,
    px,
    show_floating_notice,
)
from .helpers import (
    _app_via_parent,
    recorded_input_row_label,
    segment_action_is_blocking,
    segment_row_label,
)


def module_reference_binding(key: str, obj: dict | None = None) -> dict:
    """Return the stable image/module binding carried by inserted actions."""
    obj = obj if obj is not None else (registered_module_object(key) or {})
    raw_region = obj.get("region", [])
    region = []
    if isinstance(raw_region, (list, tuple)) and len(raw_region) == 4:
        try:
            parts = [int(part) for part in raw_region]
        except (TypeError, ValueError):
            parts = []
        if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
            region = parts
    return {
        "template": str(obj.get("template") or key),
        "module_key": key,
        "module_ref": True,
        "module_category": str(obj.get("category") or "switch"),
        "region_mode": "template",
        "region": region,
    }


def action_with_live_module_binding(action: dict | None) -> dict:
    """Refresh editable action fields from its current module object."""
    updated = dict(action or {})
    if not updated.get("module_ref"):
        return updated
    key = str(updated.get("module_key", "")).strip()
    obj = registered_module_object(key) if key else None
    if obj is None:
        return updated
    updated.update(module_reference_binding(key, obj))
    return updated


def choose_module_binding(parent, categories: tuple[str, ...]) -> dict | None:
    """Open the shared module picker and return only its image/region binding."""
    from .module_objects import (ModulePickerDialog)
    result = ModulePickerDialog(
        parent, categories=categories, selection_only=True, allow_number=False,
    ).show()
    return result if isinstance(result, dict) and result.get("module_ref") else None


def module_display_name(module_key: str, module_obj: dict | None = None) -> str:
    """Return a human-readable module label without exposing stable object IDs."""
    key = str(module_key or "").strip()
    if not key:
        return "未选择模块"
    obj = module_obj if module_obj is not None else registered_module_object(key)
    if obj:
        name = str(obj.get("name") or "").strip()
        if name:
            return name
        template = str(obj.get("template") or "").strip()
        if template:
            return Path(template.replace("\\", "/")).stem or "未命名模块"
    if key.startswith("module:"):
        return "未找到模块"
    return Path(key.replace("\\", "/")).stem or "未命名模块"


def module_action_for_key(key: str, category: str, obj: dict | None = None) -> dict:
    """Build the live-reference action stored when a module is inserted."""
    if category == "special":
        return {
            "type": "end_current_script"
            if key == END_CURRENT_SCRIPT_LABEL
            else "restart_workflow"
        }
    obj = obj if obj is not None else (registered_module_object(key) or {})
    binding = module_reference_binding(key, obj)
    if category in ("workflow_global", "script_global", "global"):
        return {
            "type": "global_detect", **binding,
            "module_category": (
                "workflow_global" if category == "global" else category
            ), "delay_ms": 0,
        }
    action = {
        "type": "image_match", **binding,
        "module_category": "switch", "delay_ms": 0,
        "on_found": "continue", "on_timeout": "continue",
    }
    if obj.get("recognize") == "number":
        # 数字模块插入脚本时由行编辑框补比较值；相等默认跳转，失败默认继续下一行。
        action["on_found"] = "jump"
    return action


class RecordedInputDialog(ModalDialog):
    """双击「录制动作」那一行后打开的折叠内容：查看 / 修改里面的每一步。

    脚本列表里这条动作只占一行（外部内容不变），具体操作都收在这里：
    可重新录一段、逐条编辑/移除/上下移动/清空，确定后把整段写回那一条动作。
    ``show()`` 返回新的 steps 列表；取消返回 None。
    """

    LIST_ROWS = 12

    def __init__(self, parent, steps: list[dict] | None = None):
        super().__init__(parent, "录制动作内容", 700, 560)
        self.app = _app_via_parent(parent)
        self.steps: list[dict] = [dict(step) for step in recorded_input_steps(
            {RECORDED_INPUT_STEPS_KEY: steps or []},
        )]
        self.result: list[dict] | None = None

        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="这一步在脚本里只占一行，下面是它录到的内容；"
                 "确定后写回该行。每一步都能单独编辑、上下移动或删除。",
            foreground=COLOR_MUTED, wraplength=px(660),
        ).pack(anchor="w")
        self.summary_var = tk.StringVar(value="")
        ttk.Label(
            body, textvariable=self.summary_var, foreground=COLOR_TEXT,
        ).pack(anchor="w", pady=pad(8, 4))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            list_frame, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            selectbackground=COLOR_BLUE_SELECTION, height=self.LIST_ROWS,
            font=(FONT_FAMILY, FONT_BODY), relief="flat", borderwidth=0,
            selectmode="extended", exportselection=False,
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.listbox.bind("<Double-1>", lambda _event: self._edit_selected())
        self.listbox.bind("<Control-a>", self._select_all)
        rows = ttk.Frame(body)
        rows.pack(fill="x", pady=pad(10, 0))
        ttk.Button(rows, text="重新录制…", command=self._record_again).pack(side="left")
        ttk.Button(rows, text="编辑", command=self._edit_selected).pack(side="left", padx=pad(8, 0))
        ttk.Button(rows, text="移除", command=self._remove_selected).pack(side="left", padx=pad(8, 0))
        ttk.Button(rows, text="上移", command=lambda: self._move_selected(-1)).pack(side="left", padx=pad(8, 0))
        ttk.Button(rows, text="下移", command=lambda: self._move_selected(1)).pack(side="left", padx=pad(8, 0))
        ttk.Button(rows, text="清空", command=self._clear_steps).pack(side="left", padx=pad(8, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(12, 0))
        ttk.Button(buttons, text="确定", command=self._accept).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=pad(8, 0))
        self._reload()

    def _reload(self):
        self.listbox.delete(0, "end")
        for index, step in enumerate(self.steps):
            text = f"{index + 1}. {recorded_input_row_label(step)}"
            delay = int(step.get("delay_ms", 0) or 0)
            if delay > 0:
                text += f" · 延时 {delay} ms"
            self.listbox.insert("end", text)
        self.summary_var.set(f"共 {len(self.steps)} 步")

    def _select_all(self, _event=None):
        self.listbox.selection_set(0, "end")
        return "break"

    def _selection(self) -> list[int]:
        return sorted(int(index) for index in self.listbox.curselection())

    def _record_again(self):
        # 重新录制同样走与应用侧栏一致的录制路径（悬浮小窗 + F8 停止）。
        start = getattr(self.app, "_start_action_recording", None)
        if not callable(start):
            show_floating_notice(self, "无法录制", "录制引擎未就绪，请重开该窗口后再试。")
            return
        start(self._replace_steps_from_recording, "重新录制动作")

    def _replace_steps_from_recording(self, recorded: list[dict]) -> None:
        ensure_action_ids(recorded)
        self.steps = [dict(step) for step in recorded]
        self._reload()

    def _edit_selected(self):
        from .actions import (edit_action)
        selection = self._selection()
        if len(selection) != 1:
            show_floating_notice(self, "请选择一步", "先选中一步，再编辑。")
            return
        index = selection[0]
        updated = edit_action(self, self.steps[index], all_actions=self.steps)
        if updated is not None:
            self.steps[index] = updated
            self._reload()
            self.listbox.selection_set(index)

    def _remove_selected(self):
        selection = self._selection()
        if not selection:
            return
        for index in reversed(selection):
            del self.steps[index]
        self._reload()

    def _move_selected(self, delta: int):
        selection = self._selection()
        if len(selection) != 1:
            return
        index = selection[0]
        target = index + delta
        if not 0 <= target < len(self.steps):
            return
        self.steps.insert(target, self.steps.pop(index))
        self._reload()
        self.listbox.selection_set(target)

    def _clear_steps(self):
        self.steps = []
        self._reload()

    def _accept(self):
        ensure_action_ids(self.steps)
        self.result = [dict(step) for step in self.steps]
        self.destroy()


class SegmentEditorMixin:
    """可复用的“动作代码段”编辑器（模块对象表单与脚本行共用）。

    使用方通过 segment_attr / listbox_attr 指定要编辑的列表属性；
    嵌套深度由 segment_depth 控制（默认 0）。
    """

    segment_depth = 0

    def _build_segment_panel(self, body, row, *, segment_attr="segment",
                             listbox_attr="segment_listbox",
                             title="主动作完成后执行的代码段",
                             pack=False, columnspan=2, height=5):
        frame = ttk.LabelFrame(body, text=title)
        if pack:
            frame.pack(fill="x", pady=pad(6, 0))
        else:
            frame.grid(row=row, column=0, columnspan=columnspan, sticky="ew",
                       pady=pad(12, 0))
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True, padx=px(10), pady=pad(6, 0))
        listbox = tk.Listbox(
            list_frame, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            selectbackground=COLOR_BLUE_SELECTION, height=height,
            font=(FONT_FAMILY, FONT_BODY), relief="flat", borderwidth=0,
            selectmode="extended", exportselection=False,
        )
        setattr(self, listbox_attr, listbox)
        listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scroll.set)
        listbox.bind(
            "<Double-1>",
            lambda _event: self._edit_segment_item(segment_attr, listbox_attr),
        )
        listbox.bind(
            "<Control-a>",
            lambda _event: self._select_all_segment_items(listbox_attr),
        )
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", padx=px(10), pady=pad(6, 10))
        ttk.Button(
            buttons, text="添加…",
            command=lambda: self._add_segment_item(segment_attr, listbox_attr),
        ).pack(side="left")
        ttk.Button(
            buttons, text="编辑",
            command=lambda: self._edit_segment_item(segment_attr, listbox_attr),
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            buttons, text="移除",
            command=lambda: self._remove_segment_item(segment_attr, listbox_attr),
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            buttons, text="上移",
            command=lambda: self._move_segment_item(-1, segment_attr, listbox_attr),
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            buttons, text="下移",
            command=lambda: self._move_segment_item(1, segment_attr, listbox_attr),
        ).pack(side="left", padx=pad(8, 0))
        self._reload_segment_list(segment_attr, listbox_attr)
        return frame

    def _reload_segment_list(self, segment_attr="segment", listbox_attr="segment_listbox"):
        segment = getattr(self, segment_attr)
        listbox = getattr(self, listbox_attr)
        listbox.delete(0, "end")
        for index, item in enumerate(segment):
            listbox.insert("end", f"{index + 1}. {segment_row_label(item)}")
            if segment_action_is_blocking(item):
                listbox.itemconfigure(index, foreground="#F2B84B")

    def _segment_selection(self, listbox_attr="segment_listbox"):
        selection = getattr(self, listbox_attr).curselection()
        return selection[0] if selection else None

    def _select_all_segment_items(self, listbox_attr="segment_listbox"):
        """Select every row in one of the module's internal action segments."""
        listbox = getattr(self, listbox_attr)
        listbox.selection_set(0, "end")
        return "break"

    def _add_segment_item(self, segment_attr="segment", listbox_attr="segment_listbox"):
        from .app_dialogs import (ScriptRefDialog)
        from .actions import (ClickDialog, CloseAppDialog, KeyActionDialog, MouseMoveDialog, OpenAppDialog, RepeatClickDialog, ScrollDialog, TextActionDialog)
        menu = tk.Menu(self, tearoff=0)
        target = (segment_attr, listbox_attr)
        menu.add_command(label="延时", command=lambda: self._add_segment_delay(*target))
        menu.add_command(
            label="录制动作…",
            command=lambda: self._record_segment_actions(*target),
        )
        menu.add_command(label="键盘", command=lambda: self._add_segment_dialog(KeyActionDialog, *target))
        menu.add_command(label="文本", command=lambda: self._add_segment_dialog(TextActionDialog, *target))
        menu.add_command(label="点击", command=lambda: self._add_segment_dialog(ClickDialog, *target))
        menu.add_command(label="连续点击", command=lambda: self._add_segment_dialog(RepeatClickDialog, *target))
        menu.add_command(label="滚轮", command=lambda: self._add_segment_dialog(ScrollDialog, *target))
        menu.add_command(label="移动", command=lambda: self._add_segment_dialog(MouseMoveDialog, *target))
        menu.add_command(
            label="识别模块…", command=lambda: self._add_segment_module_ref(*target),
            state="normal" if self.segment_depth < SEGMENT_DEPTH_LIMIT else "disabled",
        )
        menu.add_command(label="引用脚本", command=lambda: self._add_segment_dialog(ScriptRefDialog, *target))
        menu.add_command(label="打开软件", command=lambda: self._add_segment_dialog(OpenAppDialog, *target))
        menu.add_command(label="关闭软件", command=lambda: self._add_segment_dialog(CloseAppDialog, *target))
        menu.add_command(label="提醒", command=lambda: self._add_segment_notice(*target))
        menu.add_command(label="前置指定窗口…", command=lambda: self._add_segment_activate_window(*target))
        menu.add_separator()
        menu.add_command(
            label="跳转到当前脚本最后一行",
            command=lambda: self._add_segment_jump_current_script_last(*target),
        )
        menu.add_command(
            label=END_CURRENT_SCRIPT_LABEL,
            command=lambda: self._add_segment_end_current_script(*target),
        )
        if self.segment_depth >= SEGMENT_DEPTH_LIMIT:
            menu.add_command(label="（代码段嵌套最多 8 层）", state="disabled")
        try:
            menu.tk_popup(self.winfo_pointerx(), self.winfo_pointery())
        finally:
            menu.grab_release()

    def _append_segment(self, action: dict, segment_attr="segment",
                        listbox_attr="segment_listbox"):
        ensure_action_ids([action])
        getattr(self, segment_attr).append(action)
        self._reload_segment_list(segment_attr, listbox_attr)

    def _add_segment_delay(self, segment_attr="segment", listbox_attr="segment_listbox"):
        from .actions import (DurationDialog)
        value = DurationDialog(self, "添加延时", "延时时间：", 100).show()
        if value is not None:
            self._append_segment({"type": "delay", "ms": value}, segment_attr, listbox_attr)

    def _record_segment_actions(self, segment_attr="segment", listbox_attr="segment_listbox"):
        """录一段键鼠操作，停止后原样插入当前代码段（每条都能再编辑）。

        走与侧栏「开始录制 F8」**完全同一条录制路径**（同一个录制引擎、同一个
        悬浮小窗：已录条数 / 坐标模式 / F8 停止），所以录制期间的手感与原来的
        整脚本录制一样，录制期间主窗口也照旧让开屏幕。
        """
        app = _app_via_parent(self)
        start = getattr(app, "_start_action_recording", None)
        if not callable(start):
            show_floating_notice(self, "无法录制", "录制引擎未就绪，请重开模块窗口后再试。")
            return
        start(
            lambda steps: self._append_recorded_segment(steps, segment_attr, listbox_attr),
            "代码段录制动作",
        )

    def _append_recorded_segment(self, steps: list[dict], segment_attr: str,
                                 listbox_attr: str) -> None:
        for step in steps:
            self._append_segment(dict(step), segment_attr, listbox_attr)

    def _add_segment_notice(self, segment_attr="segment", listbox_attr="segment_listbox"):
        text = simpledialog.askstring("添加提醒", "提示文字：", parent=self, initialvalue="")
        if text is not None and text.strip():
            self._append_segment({"type": "notice", "text": text.strip()}, segment_attr, listbox_attr)

    def _add_segment_end_current_script(self, segment_attr="segment", listbox_attr="segment_listbox"):
        self._append_segment({"type": "end_current_script"}, segment_attr, listbox_attr)

    def _add_segment_jump_current_script_last(self, segment_attr="segment",
                                               listbox_attr="segment_listbox"):
        self._append_segment({"type": "jump_current_script_last"}, segment_attr, listbox_attr)

    def _add_segment_activate_window(self, segment_attr="segment", listbox_attr="segment_listbox"):
        from .app_dialogs import (WindowPicker)
        selected = WindowPicker(self).show()
        if selected:
            self._append_segment({
                "type": "activate_window",
                "window": {
                    "title": selected.title,
                    "class_name": selected.class_name,
                    "process_path": selected.process_path,
                },
            }, segment_attr, listbox_attr)

    def _add_segment_dialog(self, dialog_class, segment_attr="segment", listbox_attr="segment_listbox"):
        result = dialog_class(self, None).show()
        if result is not None:
            self._append_segment(result, segment_attr, listbox_attr)

    def _add_segment_module_ref(self, segment_attr="segment", listbox_attr="segment_listbox"):
        from .module_objects import (ModulePickerDialog)
        result = ModulePickerDialog(
            self, nested=True, segment_depth=self.segment_depth + 1,
        ).show()
        if result is not None:
            self._append_segment(result, segment_attr, listbox_attr)

    def _edit_segment_item(self, segment_attr="segment", listbox_attr="segment_listbox"):
        from .actions import (edit_action)
        index = self._segment_selection(listbox_attr)
        if index is None:
            return
        updated = edit_action(
            self, getattr(self, segment_attr)[index], all_actions=getattr(self, segment_attr),
            segment_depth=self.segment_depth + 1,
        )
        if updated is not None:
            getattr(self, segment_attr)[index] = updated
            self._reload_segment_list(segment_attr, listbox_attr)

    def _remove_segment_item(self, segment_attr="segment", listbox_attr="segment_listbox"):
        selection = getattr(self, listbox_attr).curselection()
        if not selection:
            return
        segment = getattr(self, segment_attr)
        for index in sorted((int(item) for item in selection), reverse=True):
            del segment[index]
        self._reload_segment_list(segment_attr, listbox_attr)

    def _move_segment_item(self, delta: int, segment_attr="segment", listbox_attr="segment_listbox"):
        """整体上移/下移选中的代码段行（连续多选当作一个块移动，块内顺序不变）。"""
        listbox = getattr(self, listbox_attr)
        selection = [int(index) for index in listbox.curselection()]
        if not selection:
            return
        if selection != list(range(selection[0], selection[-1] + 1)):
            show_floating_notice(self, "无法移动", "请选择连续的多行动作后再移动。")
            return
        segment = getattr(self, segment_attr)
        start, end = selection[0], selection[-1]
        target = start + delta
        if target < 0 or end + delta >= len(segment):
            return
        block = segment[start:end + 1]
        del segment[start:end + 1]
        segment[target:target] = block
        self._reload_segment_list(segment_attr, listbox_attr)
        listbox.selection_set(target, target + len(block) - 1)
        listbox.see(target if delta < 0 else target + len(block) - 1)


class FailureSegmentMixin(SegmentEditorMixin):
    """脚本行级“失败后执行代码段”：识别类动作失败时先跑这段，再走失败分支。"""

    def _init_failure_segment(self, action: dict) -> None:
        # 行级代码段里还能再引用识别模块，嵌套深度从 1 起算。
        self.segment_depth = 1
        self.failure_segment = [
            dict(item) for item in (action.get("failure_actions") or [])
            if isinstance(item, dict)
        ]
        self.failure_segment_enabled = tk.BooleanVar(
            value=bool(action.get("failure_segment_enabled", False)),
        )

    FAILURE_SEGMENT_LABEL = "失败后执行代码段（失败时先跑这段，再走失败分支）"

    def _build_failure_segment_controls(self, parent, *, row=None, pack=False,
                                        columnspan=2):
        """放一行开关 + 内联代码段编辑器（与模块对象里的代码段同一套控件）。"""
        if pack:
            dark_checkbutton(
                parent, text=self.FAILURE_SEGMENT_LABEL,
                variable=self.failure_segment_enabled,
            ).pack(anchor="w", pady=pad(8, 0))
            self._build_segment_panel(
                parent, None, segment_attr="failure_segment",
                listbox_attr="failure_segment_listbox",
                title="失败后执行的代码段", pack=True, height=4,
            )
            return None
        dark_checkbutton(
            parent, text=self.FAILURE_SEGMENT_LABEL,
            variable=self.failure_segment_enabled,
        ).grid(row=row, column=0, columnspan=columnspan, sticky="w", pady=pad(8, 0))
        self._build_segment_panel(
            parent, row + 1, segment_attr="failure_segment",
            listbox_attr="failure_segment_listbox",
            title="失败后执行的代码段", columnspan=columnspan, height=4,
        )
        return row + 2

    def _failure_segment_fields(self, result: dict) -> None:
        """写入本行失败代码段字段；未初始化时按空处理（兼容测试构造的实例）。"""
        variable = getattr(self, "failure_segment_enabled", None)
        segment = getattr(self, "failure_segment", None)
        result["failure_segment_enabled"] = bool(
            variable.get() if variable is not None else False
        )
        result["failure_actions"] = (
            [dict(item) for item in segment] if isinstance(segment, list) else []
        )
