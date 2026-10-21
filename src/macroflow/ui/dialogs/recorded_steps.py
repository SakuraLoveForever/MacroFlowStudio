"""录制步骤与坐标路线的模块化编辑器。

高级动作编辑原来是整段 JSON 文本：键盘只给虚键码、每条输入挤在一行里，
既看不懂也不好改。这里把整段拆成一行一个模块的列表，双击某一行只改那一项，
按键一律显示成 W / E / SPACE 这类名称；保存时仍写回原来的动作结构。
"""
from __future__ import annotations

import math
import copy
import tkinter as tk
from tkinter import ttk

from macroflow.execution.ssjj_recording import mark_offsets, validate_recording
from macroflow.input.input_guard import KeyCapturer

from .base import (
    COLOR_MUTED, COLOR_SURFACE, COLOR_SURFACE_ALT, COLOR_TEXT, FONT_BODY, FONT_FAMILY,
    DurationVar, KEY_HINT_CAPTURING, ModalDialog, Tooltip, key_to_vk, pad, px,
    show_floating_notice, vk_to_key_name,
)
from .helpers import key_display_name, recorded_input_row_label


BUTTON_LABELS = {"left": "鼠标左键", "right": "鼠标右键", "middle": "鼠标中键",
                 "x2": "鼠标前侧键", "x1": "鼠标后侧键"}
BUTTON_NAMES = {label: name for name, label in BUTTON_LABELS.items()}
# 工具栏按钮之间的固定缝隙（右间距）：所有按钮用同一个值，不再有的贴在一起。
ROW_GAP = 8


def pack_row_buttons(parent, specs):
    """一行工具栏按钮：全部贴左排列，按钮之间保持同样的缝隙。"""
    for text, command in specs:
        ttk.Button(parent, text=text, command=command).pack(side="left", padx=(0, px(ROW_GAP)))


def milliseconds_var(value, master):
    variable = DurationVar(round(float(value)), master=master)
    variable.unit.set("ms")
    return variable


def mark_row_label(index: int, mark: dict) -> str:
    position = list(mark.get("position") or [0, 0, 0])
    while len(position) < 3:
        position.append(0)
    timing = "起点" if index == 0 else f"距上一点 {float(mark.get('gap_ms', 0) or 0):.0f} ms"
    return (f"{index + 1}. 纠正点 · {timing} · "
            f"({float(position[0]):.0f}, {float(position[1]):.0f}, {float(position[2]):.0f}) · "
            f"朝向 {float(mark.get('yaw', 0)):.1f}° / 俯仰 {float(mark.get('pitch', 0)):.1f}°")


def input_row_label(index: int, step: dict) -> str:
    return f"{index + 1}. {recorded_input_row_label(step)} · {float(step.get('recorded_at_ms', 0)):.0f} ms"


class RecordedStepDialog(ModalDialog):
    """双击一行后的步骤编辑器：只显示这一步真正用到的字段。"""

    TITLES = {
        "key": "编辑按键步骤", "key_press": "编辑敲击步骤",
        "mouse_button": "编辑鼠标按键步骤", "mouse_move": "编辑鼠标移动步骤",
        "scroll": "编辑滚轮步骤", "turn": "编辑转向步骤",
    }
    # 每种步骤里按整数解析的字段（其余字段原样保留）。
    INT_FIELDS = {
        "mouse_button": ("x", "y"),
        "mouse_move": ("x", "y", "dx", "dy"),
        "scroll": ("dx", "dy", "x", "y"),
        "turn": ("dx", "dy", "pulse_duration_ms", "duration_ms"),
    }
    STEP_KINDS = ("key", "key_press", "mouse_button", "mouse_move", "scroll", "turn")

    @classmethod
    def handles(cls, action: dict) -> bool:
        """录制时间轴上的步骤才用这套行编辑器；面板里新建的动作走各自对话框。"""
        return str(action.get("type", "")) in cls.STEP_KINDS and "recorded_at_ms" in action

    def __init__(self, parent, step: dict, *, delay_ms: float | None = None):
        kind = str(step.get("type", ""))
        super().__init__(parent, self.TITLES.get(kind, "编辑录制步骤"), 460, 320)
        self._source = dict(step)
        self.capturer = None
        self._skip_auto_duration_units = True
        self._step_delay_ms = delay_ms
        self._duration_key = ""
        self.result = None
        self.fields: dict[str, tk.StringVar] = {}
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        row = [0]

        def field(label: str, name: str, value=None, hint: str = "", *,
                  choices=None, capture=False, duration=False) -> None:
            ttk.Label(body, text=label).grid(row=row[0], column=0, sticky="w", pady=px(8))
            value = step.get(name, 0) if value is None else value
            var = milliseconds_var(value, self) if duration else tk.StringVar(value=str(value))
            control = ttk.Frame(body)
            control.grid(row=row[0], column=1, sticky="ew")
            if choices:
                ttk.Combobox(control, textvariable=var, values=choices,
                             state="readonly", width=8).pack(fill="x")
            else:
                entry = ttk.Entry(control, textvariable=var, width=12,
                                  state="readonly" if capture else "normal")
                entry.pack(side="left", fill="x", expand=True)
                if capture:
                    entry.bind("<Button-1>", lambda _event: self.start_capture())
                    self.capture_button = ttk.Button(control, text="检测按键…", command=self.start_capture)
                    self.capture_button.pack(side="left", padx=pad(6, 0))
                if duration:
                    ttk.Combobox(control, textvariable=var.unit, values=("ms", "s"),
                                 state="readonly", width=3).pack(side="left", padx=pad(6, 0))
            if capture:
                self.capture_hint = tk.StringVar(value="点击检测后，按下目标按键")
                ttk.Label(body, textvariable=self.capture_hint, foreground=COLOR_MUTED,
                          wraplength=px(150)).grid(row=row[0], column=2, sticky="w", padx=px(6))
            elif hint:
                ttk.Label(body, text=hint, foreground=COLOR_MUTED, wraplength=px(150)).grid(
                    row=row[0], column=2, sticky="w", padx=px(6))
            self.fields[name] = var
            row[0] += 1

        if kind in ("key", "key_press"):
            field("按键", "key", key_display_name(step), capture=True)
            if kind == "key":
                field("状态", "down", "↓" if step.get("down") else "↑", choices=("↓", "↑"))
        elif kind == "mouse_button":
            field("按键", "button", BUTTON_LABELS.get(str(step.get("button")), str(step.get("button"))),
                  choices=tuple(BUTTON_LABELS.values()))
            field("状态", "down", "↓" if step.get("down") else "↑", choices=("↓", "↑"))
            if "x" in step:
                field("X", "x")
                field("Y", "y")
        elif kind == "mouse_move":
            field("模式", "mode", step.get("mode", "relative"), "relative / absolute")
            if step.get("mode") == "absolute":
                field("X", "x")
                field("Y", "y")
            else:
                field("ΔX", "dx")
                field("ΔY", "dy")
        elif kind == "scroll":
            field("横向格数", "dx", step.get("dx", 0))
            field("纵向格数", "dy", step.get("dy", 0), "正数向上、负数向下")
            if "x" in step:
                field("X", "x")
                field("Y", "y")
        elif kind == "turn":
            field("ΔX", "dx")
            field("ΔY", "dy")
            # 录制的转向用 pulse_duration_ms，面板动作才有 duration_ms：按现有的那个改。
            self._duration_key = ("pulse_duration_ms" if "pulse_duration_ms" in step
                                  else "duration_ms")
            field("时长", self._duration_key, step.get(self._duration_key, 0), duration=True)
        else:
            show_floating_notice(self, "暂不支持", f"这种步骤请用高级动作编辑：{kind or '未知'}")
            self.after(0, self.destroy)
        if delay_ms is not None:
            field("延时", "step_delay_ms", delay_ms, "距上一步输入的间隔；后续步骤一起调整", duration=True)
        elif "recorded_at_ms" in step:
            field("时间", "recorded_at_ms", step.get("recorded_at_ms", 0),
                  "这一步在录制时间轴上的时刻", duration=True)

        buttons = ttk.Frame(body)
        buttons.grid(row=row[0], column=0, columnspan=3, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def start_capture(self):
        if self.capturer is not None:
            return
        self.capture_button.configure(state="disabled")
        self.capture_hint.set(KEY_HINT_CAPTURING)
        self.capturer = KeyCapturer(
            on_key=lambda vk: self.after(0, self._apply_captured_key, vk),
            on_cancel=lambda: self.after(0, self._end_capture),
            allow_escape=True,
        )
        if not self.capturer.start():
            self._end_capture()
            show_floating_notice(self, "按键检测失败", "无法启动按键检测，请重试")

    def _apply_captured_key(self, vk):
        self.fields["key"].set(vk_to_key_name(vk))
        self._end_capture()

    def _end_capture(self):
        if self.capturer is not None:
            self.capturer.stop()
            self.capturer = None
        self.capture_button.configure(state="normal")
        self.capture_hint.set("点击检测后，按下目标按键")

    def destroy(self):
        if self.capturer is not None:
            self.capturer.stop()
            self.capturer = None
        super().destroy()

    def save(self):
        kind = str(self._source.get("type", ""))
        try:
            values = {name: var.get().strip() for name, var in self.fields.items()}
            step = dict(self._source)
            if kind in ("key", "key_press"):
                vk, name = key_to_vk(values["key"])
                step["vk"], step["name"] = vk, name
                if kind == "key":
                    step["down"] = values["down"] == "↓"
            elif kind == "mouse_button":
                if values["button"] not in BUTTON_NAMES:
                    raise ValueError("请选择鼠标左键、右键、中键、前侧键或后侧键")
                step["button"] = BUTTON_NAMES[values["button"]]
                step["down"] = values["down"] == "↓"
                for axis in self.INT_FIELDS["mouse_button"]:
                    if axis in values:
                        step[axis] = int(values[axis])
            elif kind == "mouse_move":
                step["mode"] = values["mode"]
                for axis in self.INT_FIELDS["mouse_move"]:
                    if axis in values:
                        step[axis] = int(values[axis])
            elif kind == "scroll":
                for axis in self.INT_FIELDS["scroll"]:
                    if axis in values:
                        step[axis] = int(values[axis])
            elif kind == "turn":
                for axis in self.INT_FIELDS["turn"]:
                    if axis in values:
                        step[axis] = max(0, int(values[axis])) if "duration" in axis else int(values[axis])
            if "recorded_at_ms" in values:
                step["recorded_at_ms"] = max(0.0, float(values["recorded_at_ms"]))
            if "step_delay_ms" in values:
                delay = float(values["step_delay_ms"])
                if not math.isfinite(delay) or delay < 0:
                    raise ValueError("延时必须是非负的有限数值")
                step["recorded_at_ms"] = step_offset(self._source) + delay - self._step_delay_ms
        except (ValueError, OverflowError) as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = step
        self.destroy()


def step_offset(step: dict) -> float:
    """步骤在录制时间轴上的时刻。"""
    try:
        return float(step.get("recorded_at_ms", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


class DelayInsertDialog(ModalDialog):
    """插入延时：问延时长度，路线里还要问插在哪个时刻。"""

    def __init__(self, parent, *, at_ms: float | None = None, duration_ms: int = 500):
        super().__init__(parent, "插入延时", 420, 240 if at_ms is None else 300)
        self._skip_auto_duration_units = True
        self.result = None
        self.fields: dict[str, tk.StringVar] = {}
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        rows = [("延时", "duration_ms", duration_ms)]
        if at_ms is not None:
            rows.insert(0, ("插在什么时刻", "at_ms", at_ms))
        for row, (label, name, value) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            var = milliseconds_var(value, self)
            control = ttk.Frame(body)
            control.grid(row=row, column=1, sticky="ew")
            ttk.Entry(control, textvariable=var, width=12).pack(side="left", fill="x", expand=True)
            ttk.Combobox(control, textvariable=var.unit, values=("ms", "s"),
                         state="readonly", width=3).pack(side="left", padx=pad(6, 0))
            self.fields[name] = var
        hint = ("延时只把后面的步骤往后推，已录的按键顺序不变。"
                if at_ms is not None else "这一步会等待指定的毫秒数再继续。")
        ttk.Label(body, text=hint, foreground=COLOR_MUTED, wraplength=px(370)).grid(
            row=len(rows), column=0, columnspan=2, sticky="w", pady=pad(10, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="ew", pady=pad(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def save(self):
        try:
            result = {"duration_ms": int(self.fields["duration_ms"].get().strip())}
            if result["duration_ms"] < 1:
                raise ValueError("延时至少 1 ms")
            if "at_ms" in self.fields:
                result["at_ms"] = max(0.0, float(self.fields["at_ms"].get().strip()))
        except (ValueError, OverflowError) as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = result
        self.destroy()


class RouteMarkDialog(ModalDialog):
    """纠正点：距上一点的间隔、位置与朝向。"""

    def __init__(self, parent, mark: dict, *, first: bool = False):
        super().__init__(parent, "编辑纠正点", 460, 340)
        position = list(mark.get("position") or [0, 0, 0])
        while len(position) < 3:
            position.append(0)
        self._source = dict(mark)
        self._first = bool(first)
        self.result = None
        self.fields: dict[str, tk.StringVar] = {}
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        for row, (label, name, value) in enumerate((
            ("距上一点 (ms)", "gap_ms", 0 if self._first else mark.get("gap_ms", 0)),
            ("X", "x", position[0]), ("Y", "y", position[1]), ("Z", "z", position[2]),
            ("朝向 (°)", "yaw", mark.get("yaw", 0)), ("俯仰 (°)", "pitch", mark.get("pitch", 0)),
        )):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            var = tk.StringVar(value=str(value))
            ttk.Entry(body, textvariable=var).grid(row=row, column=1, sticky="ew")
            self.fields[name] = var
        ttk.Label(body, text=("第一个纠正点必须是起点：间隔固定为 0，回放时从它开始对齐；"
                              "后面的纠正点只填「距上一点多少毫秒」。"),
                  foreground=COLOR_MUTED, wraplength=px(400)).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=pad(10, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=2, sticky="ew", pady=pad(16, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def save(self):
        try:
            values = {name: var.get().strip() for name, var in self.fields.items()}
            mark = dict(self._source)
            mark.pop("at_ms", None)
            mark["gap_ms"] = 0.0 if self._first else max(0.0, float(values["gap_ms"]))
            mark["position"] = [float(values["x"]), float(values["y"]), float(values["z"])]
            mark["yaw"] = float(values["yaw"])
            mark["pitch"] = float(values["pitch"])
        except ValueError as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = mark
        self.destroy()


class RouteActionDialog(ModalDialog):
    """坐标路线：纠正点一行一个、键鼠输入一行一个，双击改其中一项。"""

    MARK_ROWS = 6
    INPUT_ROWS = 12

    def __init__(self, parent, action: dict):
        marks = [dict(mark) for mark in action.get("marks") or []]
        if any("at_ms" in mark for mark in marks):
            # 旧路线按绝对时间记纠正点，打开后只会显示成一串 0 间隔，改一下就写坏；
            # 直接拒绝并要求重录，不做格式转换。
            raise ValueError('纠正点只记「距上一点的间隔」：这条路线还是旧的绝对时间格式，请重新录制')
        super().__init__(parent, "编辑坐标路线", 780, 660)
        self._source = dict(action)
        self.marks = marks
        self.inputs = copy.deepcopy(action.get("inputs") or [])
        # 轨迹只用于校验和末帧姿态，但插入延时时它必须跟时间轴一起挪，否则校验不过。
        self.trajectory = [dict(sample) for sample in action.get("trajectory") or []]
        self.duration = float(action.get("duration_ms", 0) or 0)
        self._init_history()
        self.result = None
        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        duration = float(action.get("duration_ms", 0) or 0)
        ttk.Label(body, text=(f"地图 {action.get('map_id', '?')} · 录制时长 {self.duration:.0f} ms · "
                              f"轨迹采样 {len(self.trajectory)} 个（只用于校验，不参与行走）"),
                  foreground=COLOR_MUTED, wraplength=px(740)).pack(anchor="w")
        ttk.Label(body, text="纠正点（第一个是起点，按内存坐标纠偏；每个点记的是距上一点的间隔，两点之间按录制键鼠原样重放）",
                  foreground=COLOR_TEXT).pack(anchor="w", pady=pad(10, 2))
        self.marks_list = self._list(body, self.MARK_ROWS, self._edit_mark)
        self.marks_list.bind("<<ListboxSelect>>", lambda _event: self._jump_to_mark())
        marks_row = ttk.Frame(body)
        marks_row.pack(fill="x", pady=pad(6, 0))
        pack_row_buttons(marks_row, (
            ("添加纠正点", self._add_mark), ("编辑", self._edit_mark),
            ("删除", self._remove_mark), ("上移", lambda: self._move(self.marks_list, self.marks, -1)),
            ("下移", lambda: self._move(self.marks_list, self.marks, 1)),
        ))
        self._history_buttons(marks_row)
        ttk.Label(body, text="键鼠输入 / 脚本动作（延时为距上一步的间隔；彩色行为纠正点）",
                  foreground=COLOR_TEXT).pack(anchor="w", pady=pad(10, 2))
        self.inputs_list = self._list(body, self.INPUT_ROWS, self._edit_input)
        self.inputs_list.bind("<Button-3>", self._show_step_menu)
        inputs_row = ttk.Frame(body)
        inputs_row.pack(fill="x", pady=pad(6, 0))
        pack_row_buttons(inputs_row, (
            ("插入按键", lambda: self._insert_step("key")),
            ("插入鼠标", lambda: self._insert_step("mouse_button")),
            ("插入延时", self._insert_delay),
            ("等待小怪消失", lambda: self._insert_script_command('add_wait_mobs_absent')),
        ))
        # 用普通按钮弹出分类菜单：ttk 的 Menubutton 会在右边再画一个下拉箭头，
        # 和文字里的 ▾ 重复。
        self.action_menu = tk.Menu(self, tearoff=False)
        self.action_menu_button = ttk.Button(inputs_row, text='插入脚本动作 ▾',
                                             command=self._show_action_menu)
        self.action_menu_button.pack(side='left')
        edit_row = ttk.Frame(body)
        edit_row.pack(fill="x", pady=pad(4, 0))
        pack_row_buttons(edit_row, (
            ("编辑", self._edit_input), ("删除", self._remove_input),
            ("上移", lambda: self._move_input(-1)), ("下移", lambda: self._move_input(1)),
        ))
        self._history_buttons(edit_row)
        ttk.Label(body, text="右键键鼠输入行可从这一行开始执行（试跑整条路线，不改动脚本）",
                  foreground=COLOR_MUTED).pack(anchor="w", pady=pad(4, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        self.reload()

    def _init_history(self):
        self._undo_stack = []
        self._redo_stack = []
        self._history_controls = []

    def _history_buttons(self, parent):
        controls = []
        for icon, label, command in (("↶", "撤销", self._undo), ("↷", "重做", self._redo)):
            button = ttk.Button(parent, text=icon, width=3, command=command)
            button.pack(side="left", padx=(0, px(ROW_GAP)))
            Tooltip(button, label)
            controls.append(button)
        self._history_controls.append(controls)
        self._update_history_buttons()

    def _update_history_buttons(self):
        for undo, redo in self._history_controls:
            undo.configure(state="normal" if self._undo_stack else "disabled")
            redo.configure(state="normal" if self._redo_stack else "disabled")

    def _snapshot(self):
        # 延时编辑会联动两张列表和校验轨迹，必须作为一次完整操作恢复。
        return copy.deepcopy((self.marks, self.inputs, self.trajectory, self.duration,
                              tuple(self.marks_list.curselection()),
                              tuple(self.inputs_list.curselection())))

    def _checkpoint(self):
        self._undo_stack.append(self._snapshot())
        del self._undo_stack[:-100]
        self._redo_stack.clear()
        self._update_history_buttons()

    def _restore_history(self, source, destination):
        if not source:
            return
        destination.append(self._snapshot())
        self.marks, self.inputs, self.trajectory, self.duration, marks, inputs = source.pop()
        self.reload()
        for listbox, selection, count in ((self.marks_list, marks, len(self.marks)),
                                           (self.inputs_list, inputs, len(self.input_rows))):
            for row in selection:
                if 0 <= int(row) < count:
                    listbox.selection_set(row)
                    listbox.see(row)
        self._update_history_buttons()

    def _undo(self):
        self._restore_history(self._undo_stack, self._redo_stack)

    def _redo(self):
        self._restore_history(self._redo_stack, self._undo_stack)

    def _list(self, parent, rows: int, on_double_click):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(
            frame, background=COLOR_SURFACE, foreground=COLOR_TEXT, height=rows,
            font=(FONT_FAMILY, FONT_BODY), relief="flat", borderwidth=0,
            selectmode="extended", exportselection=False, activestyle="none",
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        listbox.bind("<Double-1>", lambda _event: on_double_click())
        return listbox

    def reload(self):
        self.marks_list.delete(0, "end")
        for index, mark in enumerate(self.marks):
            self.marks_list.insert("end", mark_row_label(index, mark))
        self.inputs_list.delete(0, "end")
        offsets = mark_offsets(self.marks)
        self.input_rows = sorted(
            [("mark", i) for i in range(len(self.marks))] +
            [("input", i) for i in range(len(self.inputs))],
            key=lambda row: (offsets[row[1]] if row[0] == "mark"
                             else step_offset(self.inputs[row[1]]), row[0] != "mark"))
        for row, (kind, index) in enumerate(self.input_rows):
            if kind == "mark":
                self.inputs_list.insert("end", mark_row_label(index, self.marks[index]))
                self.inputs_list.itemconfig(row, foreground="#F2BD65", background=COLOR_SURFACE_ALT,
                                            selectforeground="#F2BD65")
            else:
                previous = step_offset(self.inputs[index - 1]) if index else 0
                if self.inputs[index].get('route_script_action'):
                    from macroflow.ui.app.summaries import action_summary
                    title, detail, _ = action_summary(self.inputs[index])
                    label = f'{title} · {detail}'
                else:
                    label = recorded_input_row_label(self.inputs[index])
                self.inputs_list.insert("end", f"{index + 1}. {label} · 延时 {step_offset(self.inputs[index]) - previous:.0f} ms")

    def _select_input_row(self, kind, index):
        row = self.input_rows.index((kind, index))
        self.inputs_list.selection_clear(0, "end")
        self.inputs_list.selection_set(row)
        self.inputs_list.see(row)

    def _jump_to_mark(self):
        index = self._selection(self.marks_list)
        if index is not None:
            self._select_input_row("mark", index)

    def _input_selection(self):
        row = self._selection(self.inputs_list)
        if row is None:
            return None
        return self.input_rows[row]

    def _show_step_menu(self, event):
        """步骤列表右键菜单：从这一行开始执行（只试跑这一条路线）。"""
        row = self.inputs_list.nearest(event.y)
        if not 0 <= row < len(self.input_rows):
            return
        self.inputs_list.selection_clear(0, "end")
        self.inputs_list.selection_set(row)
        self.inputs_list.see(row)
        menu = tk.Menu(
            self, tearoff=False,
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        menu.add_command(
            label="▶ 从此行开始执行",
            command=self.run_from_selected_step,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def selected_step_index(self) -> int | None:
        """选中行对应的起跑步骤：纠正点行从它之后的第一个输入开始。"""
        selected = self._input_selection()
        if selected is None:
            return None
        kind, index = selected
        if kind == "input":
            return index
        at = mark_offsets(self.marks)[index]
        return next((i for i, step in enumerate(self.inputs)
                     if step_offset(step) >= at), None)

    def run_from_selected_step(self):
        """从选中行开始单独试跑这条路线，并把当前编辑结果写回脚本。"""
        step_index = self.selected_step_index()
        if step_index is None:
            show_floating_notice(self, "无法执行",
                                 "先选中一行键鼠输入、脚本动作或纠正点，再从这一行开始执行。")
            return
        updated = self._validated_result("无法执行")
        if updated is None:
            return
        from .helpers import _app_via_parent
        from macroflow.ui.app.route_preview import run_route_step_test
        app = _app_via_parent(self)
        if app is None:
            show_floating_notice(self, "无法执行", "当前窗口不能单独试跑这条路线。")
            return
        self.result = updated
        self.destroy()
        run_route_step_test(app, updated, step_index)

    def _move_input(self, delta):
        selected = self._input_selection()
        if selected is None or selected[0] != "input":
            return
        index = selected[1]
        target = index + delta
        if not 0 <= target < len(self.inputs):
            return
        self._checkpoint()
        # Exchange input contents while retaining the two timeline slots.
        first, second = step_offset(self.inputs[index]), step_offset(self.inputs[target])
        self.inputs[index], self.inputs[target] = self.inputs[target], self.inputs[index]
        self.inputs[index]["recorded_at_ms"], self.inputs[target]["recorded_at_ms"] = first, second
        self.reload()
        self._select_input_row("input", target)

    def _selection(self, listbox) -> int | None:
        selection = listbox.curselection()
        return int(selection[0]) if len(selection) == 1 else None

    def _move(self, listbox, items: list, delta: int):
        index = self._selection(listbox)
        if index is None:
            show_floating_notice(self, "请选择一行", "先选中一行，再移动。")
            return
        target = index + delta
        if not 0 <= target < len(items):
            return
        self._checkpoint()
        items.insert(target, items.pop(index))
        self.reload()
        listbox.selection_set(target)

    def _add_mark(self):
        last = self.marks[-1] if self.marks else {"gap_ms": 0, "position": [0, 0, 0],
                                                 "yaw": 0, "pitch": 0}
        mark = RouteMarkDialog(self, {**last, "gap_ms": 1000.0}).show()
        if mark is not None:
            self._checkpoint()
            self.marks.append(mark)
            self.reload()

    def _edit_mark(self):
        index = self._selection(self.marks_list)
        if index is None:
            show_floating_notice(self, "请选择一行", "先选中一个纠正点，再编辑。")
            return
        mark = RouteMarkDialog(self, self.marks[index], first=index == 0).show()
        if mark is not None:
            self._checkpoint()
            self.marks[index] = mark
            self.reload()
            self.marks_list.selection_set(index)

    def _remove_mark(self):
        indices = [int(row) for row in self.marks_list.curselection()]
        if not indices:
            show_floating_notice(self, "请选择要删除的行", "先选中一个或多个纠正点，再删除。")
            return
        self._checkpoint()
        for index in sorted(indices, reverse=True):
            del self.marks[index]
        self.reload()
        self._select_after_delete(self.marks_list, min(indices), len(self.marks))

    def _edit_input(self):
        selected = self._input_selection()
        if selected is None:
            show_floating_notice(self, "请选择一行", "先选中一步输入，再编辑。")
            return
        kind, index = selected
        if kind == "mark":
            mark = RouteMarkDialog(self, self.marks[index], first=index == 0).show()
            if mark is not None:
                self._checkpoint()
                self.marks[index] = mark
                self.reload()
                self._select_input_row("mark", index)
            return
        previous = step_offset(self.inputs[index - 1]) if index else 0
        old_at = step_offset(self.inputs[index])
        if self.inputs[index].get('route_script_action'):
            from .actions import edit_action
            source = {key: copy.deepcopy(value) for key, value in self.inputs[index].items()
                      if key not in ('recorded_at_ms', 'route_script_action')}
            step = edit_action(self, source, all_actions=self.inputs)
            if step is not None:
                self._checkpoint()
                self.inputs[index] = dict(step, recorded_at_ms=old_at, route_script_action=True)
                self.reload()
                self._select_input_row('input', index)
            return
        step = RecordedStepDialog(self, self.inputs[index], delay_ms=old_at - previous).show()
        if step is not None:
            self._checkpoint()
            delta = step_offset(step) - old_at
            self._retime_delay(index, previous, old_at, delta)
            if "pulse_started_at_ms" in step:
                step["pulse_started_at_ms"] = self.inputs[index]["pulse_started_at_ms"]
            self.inputs[index] = step
            self.reload()
            self._select_input_row("input", index)

    def _retime_delay(self, index, previous, at, delta):
        def retime(value):
            if value == 0:
                return 0.0
            if value >= at:
                return value + delta
            if value > previous and at > previous:
                return previous + (value - previous) * (at - previous + delta) / (at - previous)
            return value
        for step in self.inputs[index:]:
            step["recorded_at_ms"] = step_offset(step) + delta
            if "pulse_started_at_ms" in step:
                step["pulse_started_at_ms"] = retime(step["pulse_started_at_ms"])
        # 纠正点只存间隔：先把绝对时刻按同一套规则挪好，再写回相邻两点的差。
        times = [retime(value) for value in mark_offsets(self.marks)]
        for position, item in enumerate(self.marks):
            previous_time = times[position - 1] if position else 0.0
            item.pop("at_ms", None)
            item["gap_ms"] = max(0.0, times[position] - previous_time)
        for item in self.trajectory:
            item["at_ms"] = retime(float(item["at_ms"]))
        # A zero delay can merge trajectory timestamps; retain the last sample.
        samples = {}
        for sample in self.trajectory:
            if sample["at_ms"] == 0 and 0 in samples:
                continue
            samples[sample["at_ms"]] = sample
        self.trajectory = list(samples.values())
        self.duration += delta

    def _remove_input(self):
        rows = [int(row) for row in self.inputs_list.curselection()]
        if not rows:
            show_floating_notice(self, "请选择要删除的行", "先选中一行或多行，再删除。")
            return
        selected = [self.input_rows[row] for row in rows]
        self._checkpoint()
        for kind, index in sorted(selected, reverse=True):
            del (self.marks if kind == "mark" else self.inputs)[index]
        self.reload()
        self._select_after_delete(self.inputs_list, min(rows), len(self.input_rows))

    def _select_after_delete(self, listbox, deleted_row, count):
        if count:
            row = min(max(0, deleted_row - 1), count - 1)
            listbox.selection_clear(0, "end")
            listbox.selection_set(row)
            listbox.see(row)

    def _insert_offset(self) -> float:
        return self._insertion_context()[0] + 500

    def _insertion_context(self):
        row = self._selection(self.inputs_list)
        if row is None:
            return self.duration, len(self.inputs), []
        kind, index = self.input_rows[row]
        at = mark_offsets(self.marks)[index] if kind == "mark" else step_offset(self.inputs[index])
        index = sum(kind == "input" for kind, _ in self.input_rows[:row + 1])
        return at, index, self.input_rows[row + 1:]

    def _place_input(self, step: dict):
        self._checkpoint()
        at, index, _ = self._insertion_context()
        self._shift_after_selection(max(0, step_offset(step) - at))
        self.inputs.insert(index, step)
        self.reload()
        self._select_input_row("input", index)

    def _insert_step(self, kind: str):
        at = self._insert_offset()
        if kind == "key":
            template = {"type": "key", "vk": 87, "name": "W", "down": True,
                        "recorded_at_ms": at, "delay_ms": 0}
        else:
            template = {"type": "mouse_button", "button": "left", "down": True,
                        "recorded_at_ms": at, "delay_ms": 0}
        step = RecordedStepDialog(self, template, delay_ms=500).show()
        if step is not None:
            self._place_input(step)

    def _insert_script_command(self, command):
        from .helpers import _app_via_parent
        from .route_action_insert import RouteActionInserter
        inserter = RouteActionInserter(self, _app_via_parent(self))
        getattr(inserter, command)()

    def _show_action_menu(self):
        """在按钮下方弹出分类菜单（按钮本身不再画 ttk 的下拉箭头）。"""
        self._fill_action_menu(self.action_menu)
        try:
            self.action_menu.tk_popup(
                self.action_menu_button.winfo_rootx(),
                self.action_menu_button.winfo_rooty() + self.action_menu_button.winfo_height(),
            )
        finally:
            self.action_menu.grab_release()

    def _fill_action_menu(self, menu):
        from .route_action_insert import route_action_categories
        menu.delete(0, 'end')
        for category, specs in route_action_categories():
            submenu = tk.Menu(menu, tearoff=False)
            for label, command, _style in specs:
                submenu.add_command(label=label, command=lambda name=command: self._insert_script_command(name))
            menu.add_cascade(label=category, menu=submenu)

    def _insert_delay(self):
        result = DelayInsertDialog(self, duration_ms=500).show()
        if result is None:
            return
        at, _, _ = self._insertion_context()
        duration = result["duration_ms"]
        self._checkpoint()
        # 路线的延时就是时间轴上的一段空档：把后面的步骤整体后移，不插入假动作，
        # 否则回放会多出一条它并不认识的输入。
        self._shift_after_selection(duration)
        self.reload()
        show_floating_notice(self, "已插入延时",
                             f"{at:.0f} ms 之后的步骤整体后移 {duration} ms，"
                             f"录制时长变为 {self.duration:.0f} ms。")

    def _shift_after_selection(self, delta):
        """只后移选中行后面的内容，选中输入和起点保持原位。"""
        at, index, following = self._insertion_context()
        for step in self.inputs[index:]:
            step["recorded_at_ms"] = step_offset(step) + delta
            if "pulse_started_at_ms" in step:
                step["pulse_started_at_ms"] += delta
        # 纠正点存的是间隔：只有插在它前面的那段空档变长了，所以只加第一个后续点。
        first_mark = next((mark_index for kind, mark_index in following if kind == "mark"), None)
        if first_mark is not None and first_mark > 0:
            self.marks[first_mark]["gap_ms"] = (
                float(self.marks[first_mark].get("gap_ms", 0) or 0) + delta)
        for index, sample in enumerate(self.trajectory):
            if sample["at_ms"] > at or index == len(self.trajectory) - 1:
                sample["at_ms"] = float(sample.get("at_ms", 0) or 0) + delta
        self.duration += delta

    def _validated_result(self, title: str = "无法保存"):
        """校验当前编辑内容并返回可写回脚本的动作；失败时提示并返回 None。"""
        try:
            if not self.marks:
                raise ValueError("至少要有一个纠正点（第一个点就是起点）")
            updated = dict(self._source)
            # 纠正点顺序就是时间顺序（间隔非负），不再按时间重排。
            updated["marks"] = [dict(mark) for mark in self.marks]
            updated["inputs"] = sorted((dict(step) for step in self.inputs), key=step_offset)
            updated["trajectory"] = [dict(sample) for sample in self.trajectory]
            updated["duration_ms"] = self.duration
            validate_recording(updated)
        except (ValueError, RuntimeError) as exc:
            show_floating_notice(self, title, str(exc))
            return None
        return updated

    def save(self):
        updated = self._validated_result()
        if updated is None:
            return
        self.result = updated
        self.destroy()
