from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL,
    ensure_action_ids, recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
    special_action_label,
)
from ttkbootstrap import DateEntry
from macroflow.input.input_guard import KeyCapturer, RESERVED_HOTKEY_VKS
from pathlib import Path
from macroflow.core.resolution import (
    build_resolution_action, normalize_resolution_style,
    resolution_styles_from_settings, SUPPORTED_SCALE_PERCENTS,
)
from datetime import datetime, timedelta
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
from macroflow.input.wininput import (
    WindowInfo, enum_windows, get_cursor_pos,
    get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect,
    get_virtual_screen_rect, is_current_process_window, make_window_no_activate,
    set_dark_titlebar, set_rounded_window, show_window_no_activate,
    window_from_point,
)
import json
from macroflow.execution.player import running_process_names
import tkinter as tk

from .app_dialogs import (
    RestartWorkflowTargetDialog,
    ScriptRefDialog,
    WindowPicker,
)
from .base import (
    COLOR_BG,
    COLOR_BLUE_SELECTION,
    COLOR_MUTED,
    COLOR_SURFACE,
    COLOR_TEXT,
    DEFAULT_GAME_SETUP_NOTE,
    FONT_BODY,
    FONT_FAMILY,
    FONT_MONO,
    FONT_SUBTITLE,
    KEY_HINT_CAPTURING,
    KEY_HINT_DEFAULT,
    ModalDialog,
    SCRIPT_END_LABEL,
    SCRIPT_START_LABEL,
    TIME_UNITS,
    app_windows,
    dark_checkbutton,
    duration_var,
    fit_window_to_content,
    key_to_vk,
    pad,
    px,
    show_floating_notice,
    vk_to_key_name,
)
from .helpers import (
    _app_workflow_default_row,
    image_jump_target_options,
)
from .module_objects import (
    ModuleReferenceDelayDialog,
)
from .recognition import (
    GlobalDetectDialog,
    ImageActionDialog,
    MultiConditionClickDialog,
    OcrActionDialog,
    OcrCompareActionDialog,
    RowListConditionClickDialog,
)
from .screen_pickers import (
    ScreenPointPicker,
)
from .segments import (
    RecordedInputDialog,
)


class ScheduleDialog(ModalDialog):
    def __init__(self, parent, value: str = ""):
        super().__init__(parent, "选择工作流开始时间", 470, 245)
        try:
            initial = datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else datetime.now() + timedelta(minutes=1)
        except ValueError:
            initial = datetime.now() + timedelta(minutes=1)

        body = ttk.Frame(self, padding=px(13))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="日期").grid(row=0, column=0, sticky="w", pady=pad(0, 8))
        self.date_entry = DateEntry(
            body, dateformat="%Y-%m-%d", startdate=initial.date(),
            popup_title="选择日期", width=16,
        )
        self.date_entry.grid(row=1, column=0, sticky="ew", padx=pad(0, 14))

        time_frame = ttk.Frame(body)
        time_frame.grid(row=1, column=1, sticky="w")
        self.hour_var = tk.StringVar(value=f"{initial.hour:02d}")
        self.minute_var = tk.StringVar(value=f"{initial.minute:02d}")
        self.second_var = tk.StringVar(value=f"{initial.second:02d}")
        ttk.Label(body, text="时间").grid(row=0, column=1, sticky="w", pady=pad(0, 8))
        for index, (variable, values) in enumerate((
            (self.hour_var, [f"{n:02d}" for n in range(24)]),
            (self.minute_var, [f"{n:02d}" for n in range(60)]),
            (self.second_var, [f"{n:02d}" for n in range(60)]),
        )):
            ttk.Combobox(time_frame, textvariable=variable, values=values, state="readonly", width=3).pack(side="left")
            if index < 2:
                ttk.Label(time_frame, text=":").pack(side="left", padx=px(3))

        ttk.Label(body, text="点击日期框右侧的日历按钮选择日期；时间使用下拉框选择。",
                  foreground=COLOR_MUTED).grid(row=2, column=0, columnspan=2, sticky="w", pady=pad(18, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=pad(24, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self.save).pack(side="right", padx=px(8))
        ttk.Button(buttons, text="立即执行（清除时间）", command=self.clear).pack(side="left")
        body.columnconfigure(0, weight=1)

    def save(self):
        text = f"{self.date_entry.entry.get()} {self.hour_var.get()}:{self.minute_var.get()}:{self.second_var.get()}"
        try:
            datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            show_floating_notice(self, "时间无效", "请选择有效的日期和时间。")
            return
        self.result = text
        self.destroy()

    def clear(self):
        self.result = ""
        self.destroy()


class DurationDialog(ModalDialog):
    """Small reusable duration editor for standalone delay prompts."""

    def __init__(self, parent, title: str, prompt: str, initial_ms: int = 0):
        super().__init__(parent, title, 430, 205)
        # 单位框已在下方手动放置；show() 的自动安装器会再插一个，必须跳过。
        self._skip_auto_duration_units = True
        self.value = duration_var(max(0, int(initial_ms)))
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=prompt).pack(anchor="w")
        row = ttk.Frame(body)
        row.pack(fill="x", pady=pad(12, 0))
        ttk.Entry(row, textvariable=self.value).pack(side="left", fill="x", expand=True)
        ttk.Combobox(
            row, textvariable=self.value.unit, values=TIME_UNITS,
            state="readonly", width=4,
        ).pack(side="left", padx=pad(8, 0))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(20, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def save(self):
        try:
            value = int(self.value.get())
            if value < 0 or value > 86400000:
                raise ValueError
        except ValueError:
            show_floating_notice(self, "时间无效", "请输入 0–86400000 ms 以内的时间。")
            return
        self.result = value
        self.destroy()


class JumpActionDialog(ModalDialog):
    """Configure a jump target and its optional workflow-repeat condition."""

    def __init__(self, parent, action: dict | None = None,
                 actions: list[dict] | None = None):
        super().__init__(parent, "添加跳转动作" if not action else "编辑跳转动作", 620, 370)
        action = action or {}
        self._source = dict(action or {})
        action_list = actions or []
        current_id = str(action.get(ACTION_ID_KEY, "")).strip()
        normal_options = [
            (label, action_id)
            for label, action_id in image_jump_target_options(action_list)
            if action_id != current_id
        ]
        self.target_ids = {
            SCRIPT_START_LABEL: SCRIPT_START_TARGET_ID,
            **{label: action_id for label, action_id in normal_options},
            SCRIPT_END_LABEL: NEXT_WORKFLOW_STEP_TARGET_ID,
        }
        row_by_id = {
            str(item.get(ACTION_ID_KEY, "")).strip(): index + 1
            for index, item in enumerate(action_list)
        }
        self.target_rows = {
            SCRIPT_START_LABEL: 1,
            **{label: row_by_id.get(action_id, 1) for label, action_id in normal_options},
            SCRIPT_END_LABEL: len(action_list) + 1,
        }
        saved_id = str(action.get("jump_action_id", "")).strip()
        if saved_id == SCRIPT_START_TARGET_ID:
            selected = SCRIPT_START_LABEL
        elif saved_id == NEXT_WORKFLOW_STEP_TARGET_ID:
            selected = SCRIPT_END_LABEL
        else:
            selected = next(
                (label for label, action_id in normal_options if action_id == saved_id),
                SCRIPT_START_LABEL,
            )
        self.target = tk.StringVar(value=selected)
        self.workflow_repeat_at_least_2 = tk.BooleanVar(
            value=bool(action.get("workflow_repeat_at_least_2", True)),
        )

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="跳转到").grid(row=0, column=0, sticky="w", padx=pad(0, 12), pady=px(8))
        ttk.Combobox(
            body, textvariable=self.target, values=list(self.target_ids),
            state="readonly", width=50,
        ).grid(row=0, column=1, sticky="ew", pady=px(8))
        ttk.Label(
            body,
            text=("脚本开头会从第 1 行重新执行；指定行会跟随该动作移动；"
                  "脚本结尾会结束当前脚本执行；引用脚本继续下一次，顶层脚本进入工作流下一项。"),
            foreground=COLOR_MUTED, wraplength=px(530),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=pad(10, 0))
        condition_frame = ttk.LabelFrame(body, text="跳转生效条件", padding=pad(12, 8))
        condition_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=pad(14, 0))
        ttk.Radiobutton(
            condition_frame,
            text="每次执行到该动作都跳转",
            variable=self.workflow_repeat_at_least_2,
            value=False,
        ).pack(anchor="w")
        ttk.Radiobutton(
            condition_frame,
            text="仅当工作流第 2 次或脚本多次执行的第 2 次及以后时跳转",
            variable=self.workflow_repeat_at_least_2,
            value=True,
        ).pack(anchor="w", pady=pad(6, 0))
        ttk.Label(
            body,
            text=("选择第二项后：工作流第 1 次、脚本重复执行的第 1 次和单次运行脚本时"
                  "都会继续下一行；从第 2 次开始才跳到上方选择的行对象。"),
            foreground=COLOR_MUTED, wraplength=px(530),
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=pad(4, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=pad(0, 8))

    def save(self):
        label = self.target.get()
        target_id = self.target_ids.get(label)
        if not target_id:
            show_floating_notice(self, "请选择目标", "请选择脚本开头、指定动作或脚本结尾。")
            return
        condition_var = getattr(self, "workflow_repeat_at_least_2", None)
        updated = dict(getattr(self, "_source", None) or {})
        updated.update({
            "type": "jump",
            "jump_action_id": target_id,
            "jump_row": int(self.target_rows.get(label, 1)),
            "workflow_repeat_at_least_2": (
                bool(condition_var.get()) if condition_var is not None else True
            ),
        })
        updated.setdefault("delay_ms", 0)
        self.result = updated
        self.destroy()


class OpenAppDialog(ModalDialog):
    """Choose an application to launch; its path is saved in the action."""

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "打开软件", 560, 350)
        action = action or {}
        self.path = tk.StringVar(value=str(action.get("path", "")))
        self.args = tk.StringVar(value=str(action.get("args", "")))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.after_delay = duration_var(action.get("after_delay_ms", 0))

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="软件路径").grid(row=0, column=0, sticky="w", pady=px(8))
        path_row = ttk.Frame(body)
        path_row.grid(row=0, column=1, sticky="ew")
        ttk.Entry(path_row, textvariable=self.path, state="readonly").pack(
            side="left", fill="x", expand=True,
        )
        ttk.Button(path_row, text="选择…", command=self.choose).pack(
            side="left", padx=pad(6, 0),
        )

        ttk.Label(body, text="启动参数").grid(row=1, column=0, sticky="w", pady=px(8))
        ttk.Entry(body, textvariable=self.args).grid(row=1, column=1, sticky="ew")
        ttk.Label(
            body,
            text="例：-windowed -u=xxx；留空则不带参数启动。",
            foreground=COLOR_MUTED,
        ).grid(row=2, column=1, sticky="w")

        ttk.Label(body, text="执行前延时").grid(row=3, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.delay, width=10,
        ).grid(row=3, column=1, sticky="ew")
        ttk.Label(body, text="执行后延时").grid(row=4, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.after_delay, width=10,
        ).grid(row=4, column=1, sticky="ew")

        ttk.Label(
            body,
            text="执行到这一行时会启动所选软件，然后再继续后面的动作。",
            foreground=COLOR_MUTED, wraplength=px(480),
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=pad(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def choose(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择要打开的软件",
            filetypes=[("程序", "*.exe *.bat *.cmd *.lnk"), ("所有文件", "*.*")],
        )
        if path:
            self.path.set(display_path(Path(path)))

    def save(self):
        path = self.path.get().strip()
        if not path:
            show_floating_notice(self, "路径无效", "请选择要打开的软件。")
            return
        try:
            delay = max(0, int(self.delay.get()))
            after_delay = max(0, int(self.after_delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "延时必须是整数毫秒。")
            return
        self.result = {
            "type": "open_app",
            "path": path,
            "args": self.args.get().strip(),
            "delay_ms": delay,
            "after_delay_ms": after_delay,
        }
        self.destroy()


class CloseAppDialog(ModalDialog):
    """Terminate a running program by image name (e.g. clash-verge.exe)."""

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "关闭软件", 560, 400)
        action = action or {}
        self.name = tk.StringVar(value=str(action.get("name", "")))
        self.graceful = tk.BooleanVar(value=bool(action.get("graceful", True)))
        self.graceful_wait_ms = duration_var(action.get("graceful_wait_ms", 2000))
        self.tree = tk.BooleanVar(value=bool(action.get("tree", False)))
        self.elevated_retry = tk.BooleanVar(value=bool(action.get("elevated_retry", True)))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.after_delay = duration_var(action.get("after_delay_ms", 0))

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="进程名").grid(row=0, column=0, sticky="w", pady=px(8))
        name_row = ttk.Frame(body)
        name_row.grid(row=0, column=1, sticky="ew")
        ttk.Entry(name_row, textvariable=self.name).pack(side="left", fill="x", expand=True)
        ttk.Button(name_row, text="选择…", command=self.choose).pack(
            side="left", padx=pad(6, 0),
        )
        ttk.Label(
            body,
            text="填任务管理器里的映像名称，如 clash-verge.exe；同名的所有进程都会被结束。",
            foreground=COLOR_MUTED, wraplength=px(480),
        ).grid(row=1, column=1, sticky="w")

        dark_checkbutton(
            body, "先发送关闭请求（优雅退出），超时后强制结束", self.graceful,
        ).grid(row=2, column=1, sticky="w", pady=pad(10, 0))
        ttk.Label(body, text="优雅退出等待").grid(row=3, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=60000, increment=100,
            textvariable=self.graceful_wait_ms, width=10,
        ).grid(row=3, column=1, sticky="ew")

        dark_checkbutton(
            body, "连同其子进程一起结束（进程树，慎用）", self.tree,
        ).grid(row=4, column=1, sticky="w", pady=pad(8, 0))
        dark_checkbutton(
            body, "普通权限结束失败时以管理员权限重试（会弹出 UAC 授权窗口）", self.elevated_retry,
        ).grid(row=5, column=1, sticky="w", pady=pad(4, 0))

        ttk.Label(body, text="执行前延时").grid(row=6, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.delay, width=10,
        ).grid(row=6, column=1, sticky="ew")
        ttk.Label(body, text="执行后延时").grid(row=7, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.after_delay, width=10,
        ).grid(row=7, column=1, sticky="ew")

        ttk.Label(
            body,
            text="执行到这一行时会结束指定软件，再继续后面的动作；进程不存在时自动跳过。"
            "普通权限反复强制结束仍失败（通常是目标软件以管理员身份运行）时，会尝试以管理员权限结束并弹出 UAC 授权窗口。",
            foreground=COLOR_MUTED, wraplength=px(480),
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=pad(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

        # 固定 400 高度在打包后的 EXE（按真实 DPI 渲染）里会装不下内容：
        # 行数多、两条长说明文字在高 DPI 下换行更多，底部按钮行被挤出窗口，
        # 确定/取消按钮完全看不见。按内容实际需求重设窗口尺寸并重新居中。
        fit_window_to_content(self, parent)

    def choose(self):
        names = running_process_names()
        if not names:
            show_floating_notice(self, "无法枚举进程", "读取进程列表失败。")
            return
        picker = tk.Toplevel(self)
        picker.title("选择正在运行的进程")
        picker.configure(background=COLOR_BG)
        picker.geometry(f"{px(380)}x{px(420)}")
        picker.transient(self)
        frame = ttk.Frame(picker, padding=px(12))
        frame.pack(fill="both", expand=True)
        listbox = tk.Listbox(
            frame, font=(FONT_MONO, FONT_BODY),
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            selectbackground=COLOR_BLUE_SELECTION,
            highlightthickness=1, relief="solid",
        )
        scroll = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for name in names:
            listbox.insert("end", name)

        def confirm(_event=None):
            selection = listbox.curselection()
            if selection:
                self.name.set(listbox.get(selection[0]))
            picker.destroy()

        listbox.bind("<Double-Button-1>", confirm)
        buttons_row = ttk.Frame(frame)
        buttons_row.pack(fill="x", pady=pad(10, 0))
        ttk.Button(buttons_row, text="取消", command=picker.destroy).pack(side="right")
        ttk.Button(buttons_row, text="确定", command=confirm).pack(side="right", padx=px(8))

    def save(self):
        name = self.name.get().strip()
        if not name:
            show_floating_notice(self, "进程名无效", "请输入要结束的进程名。")
            return
        try:
            graceful_wait_ms = max(0, int(self.graceful_wait_ms.get()))
            delay = max(0, int(self.delay.get()))
            after_delay = max(0, int(self.after_delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "延时必须是整数毫秒。")
            return
        self.result = {
            "type": "close_app",
            "name": name,
            "graceful": self.graceful.get(),
            "graceful_wait_ms": graceful_wait_ms,
            "tree": self.tree.get(),
            "elevated_retry": self.elevated_retry.get(),
            "delay_ms": delay,
            "after_delay_ms": after_delay,
        }
        self.destroy()


class KeyActionDialog(ModalDialog):
    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "添加键盘动作", 560, 330)
        self._source = dict(action or {})
        action = action or {}
        self.mode = tk.StringVar(value="press" if action.get("type") == "key_press" else ("down" if action.get("down", True) else "up"))
        self.key = tk.StringVar(value=str(action.get("name", "A")))
        self.hold = duration_var(action.get("hold_ms", 30))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.capturer: KeyCapturer | None = None
        self.capture_hint = tk.StringVar(value=KEY_HINT_DEFAULT)
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="按键", font=(FONT_FAMILY, FONT_BODY, "bold")).grid(row=0, column=0, sticky="w", pady=px(8))
        key_row = ttk.Frame(body)
        key_row.grid(row=0, column=1, sticky="ew", pady=px(8))
        ttk.Entry(key_row, textvariable=self.key).pack(side="left", fill="x", expand=True)
        self.capture_button = ttk.Button(key_row, text="检测按键…", command=self.start_capture)
        self.capture_button.pack(side="left", padx=pad(8, 0))
        ttk.Label(body, textvariable=self.capture_hint, foreground=COLOR_MUTED).grid(row=1, column=1, sticky="w")
        ttk.Label(body, text="动作").grid(row=2, column=0, sticky="w", pady=px(14))
        box = ttk.Combobox(body, textvariable=self.mode, values=("press", "down", "up"), state="readonly")
        box.grid(row=2, column=1, sticky="ew")
        ttk.Label(body, text="按住时长").grid(row=3, column=0, sticky="w", pady=px(8))
        ttk.Entry(body, textvariable=self.hold).grid(row=3, column=1, sticky="ew")
        ttk.Label(body, text="执行前延时").grid(row=4, column=0, sticky="w", pady=px(8))
        ttk.Entry(body, textvariable=self.delay).grid(row=4, column=1, sticky="ew")
        body.columnconfigure(1, weight=1)
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def start_capture(self):
        if self.capturer is not None:
            return
        self.capture_button.configure(state="disabled")
        self.capture_hint.set(KEY_HINT_CAPTURING)
        self.capturer = KeyCapturer(
            on_key=lambda vk: self.after(0, self._apply_captured_key, vk),
            on_cancel=lambda: self.after(0, self._cancel_capture),
            allow_escape=True,
        )
        if not self.capturer.start():
            self._end_capture()
            show_floating_notice(self, "按键检测失败", "无法启动按键检测，请重试")

    def _apply_captured_key(self, vk: int):
        self.key.set(vk_to_key_name(vk))
        self._end_capture()

    def _cancel_capture(self):
        self._end_capture()

    def _end_capture(self):
        self.capture_hint.set(KEY_HINT_DEFAULT)
        if self.capturer is not None:
            self.capturer.stop()
            self.capturer = None
        try:
            self.capture_button.configure(state="normal")
        except tk.TclError:
            pass

    def destroy(self):
        if self.capturer is not None:
            self.capturer.stop()
            self.capturer = None
        super().destroy()

    def save(self):
        try:
            vk, name = key_to_vk(self.key.get())
            hold = max(1, int(self.hold.get()))
            delay = max(0, int(self.delay.get()))
        except ValueError as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        if self.mode.get() == "press":
            updated = dict(getattr(self, "_source", None) or {})
            updated.update({"type": "key_press", "vk": vk, "name": name, "hold_ms": hold, "delay_ms": delay})
            updated.pop("down", None)
            self.result = updated
        else:
            updated = dict(getattr(self, "_source", None) or {})
            updated.update({"type": "key", "vk": vk, "name": name, "down": self.mode.get() == "down", "delay_ms": delay})
            updated.pop("hold_ms", None)
            self.result = updated
        self.destroy()


class MouseMoveDialog(ModalDialog):
    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "添加鼠标移动", 480, 300)
        self._source = dict(action or {})
        action = action or {}
        self.mode = tk.StringVar(value=action.get("mode", "absolute"))
        self.x = tk.StringVar(value=str(action.get("x", action.get("dx", 0))))
        self.y = tk.StringVar(value=str(action.get("y", action.get("dy", 0))))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.picker = None
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        labels = (("坐标模式", self.mode), ("X / ΔX", self.x), ("Y / ΔY", self.y), ("执行前延时", self.delay))
        for row, (label, variable) in enumerate(labels):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(9))
            if row == 0:
                combo = ttk.Combobox(body, textvariable=variable, values=("absolute", "relative"), state="readonly")
                combo.grid(row=row, column=1, sticky="ew")
                combo.bind("<<ComboboxSelected>>", self._mode_changed)
            elif variable is self.x:
                frame = ttk.Frame(body)
                frame.grid(row=row, column=1, sticky="ew")
                ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True)
                self.pick_button = ttk.Button(frame, text="点击屏幕选取…", command=self.start_pick_position)
                self.pick_button.pack(side="left", padx=pad(8, 0))
            else:
                entry = ttk.Entry(body, textvariable=variable)
                entry.grid(row=row, column=1, sticky="ew")
        self._update_pick_label()
        body.columnconfigure(1, weight=1)
        buttons = ttk.Frame(self, padding=pad(22, 0, 22, 18))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def _mode_changed(self, _event=None):
        self._update_pick_label()

    def _update_pick_label(self):
        if self.mode.get() == "relative":
            self.pick_button.configure(text="两点测量…")
        else:
            self.pick_button.configure(text="点击屏幕选取…")

    def start_pick_position(self):
        two_points = self.mode.get() == "relative"
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_point, two_points=two_points,
            tip_text=(
                "第一次点击记录起点，移动光标到终点后再次点击，得到 ΔX/ΔY；Esc 取消"
                if two_points else
                "点击要移动到的位置；只记录坐标，不会点击下方窗口；Esc 取消"
            ),
            # 从代码段/模块表单里打开时，上级对话框与主窗口也要一起让开。
            hidden_windows=app_windows(self.master),
        )
        self.picker.start()

    def _apply_picked_point(self, *coords):
        if self.mode.get() == "relative":
            start_x, start_y, end_x, end_y = coords
            self.x.set(str(int(end_x) - int(start_x)))
            self.y.set(str(int(end_y) - int(start_y)))
        else:
            self.x.set(str(int(coords[0])))
            self.y.set(str(int(coords[1])))

    def save(self):
        try:
            x, y, delay = int(self.x.get()), int(self.y.get()), max(0, int(self.delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "坐标和延时必须是整数。")
            return
        updated = dict(getattr(self, "_source", None) or {})
        updated["type"] = "mouse_move"
        updated["mode"] = self.mode.get()
        updated["delay_ms"] = delay
        if self.mode.get() == "relative":
            updated["dx"] = x
            updated["dy"] = y
            updated.pop("x", None)
            updated.pop("y", None)
        else:
            updated["x"] = x
            updated["y"] = y
            updated.pop("dx", None)
            updated.pop("dy", None)
        self.result = updated
        self.destroy()


class ClickDialog(ModalDialog):
    """Edit a mouse click; also handles recorded 'mouse_button' actions (按下/松开)."""

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "编辑鼠标点击" if action else "添加鼠标点击", 480, 385)
        self._source = dict(action or {})
        action = action or {}
        self.kind = str(action.get("type", "click"))
        cursor = get_cursor_pos()
        self.button = tk.StringVar(value=action.get("button", "left"))
        self.x = tk.StringVar(value=str(action.get("x", cursor[0])))
        self.y = tk.StringVar(value=str(action.get("y", cursor[1])))
        self.hold = duration_var(action.get("hold_ms", 30))
        # 录制的点击原本不带延时，编辑时默认 0，避免无意间改变播放节奏。
        self.delay = duration_var(action.get("delay_ms", 1000 if self.kind == "click" else 0))
        self.down = tk.StringVar(value="按下" if action.get("down", True) else "松开")
        self.pos_mode = tk.BooleanVar(value=action.get("pos_mode") == "current")
        self.picker = None
        self._x_entry = None
        self._y_entry = None
        self._pick_button = None
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        if self.kind == "mouse_button":
            values: list[tuple[str, tk.StringVar | None]] = [
                ("鼠标键", self.button), ("状态", None), ("屏幕 X", self.x), ("屏幕 Y", self.y),
                ("执行前延时", self.delay),
            ]
            row_offset = 0
        else:
            values = [
                ("鼠标键", self.button), ("屏幕 X", self.x), ("屏幕 Y", self.y),
                ("按住时长", self.hold), ("执行前延时", self.delay),
            ]
            row_offset = 1
            ttk.Checkbutton(
                body,
                text="点击鼠标当前位置（执行时不移动鼠标）",
                variable=self.pos_mode,
                command=self._update_pos_mode,
            ).grid(row=0, column=0, columnspan=2, sticky="w", pady=pad(0, 10))
        for row, (label, variable) in enumerate(values):
            grid_row = row + row_offset
            ttk.Label(body, text=label).grid(row=grid_row, column=0, sticky="w", pady=px(8))
            if row == 0:
                ttk.Combobox(body, textvariable=variable, values=("left", "right", "middle"), state="readonly").grid(row=grid_row, column=1, sticky="ew")
            elif self.kind == "mouse_button" and variable is None:
                ttk.Combobox(body, textvariable=self.down, values=("按下", "松开"), state="readonly").grid(row=grid_row, column=1, sticky="ew")
            elif variable is self.x:
                frame = ttk.Frame(body)
                frame.grid(row=grid_row, column=1, sticky="ew")
                self._x_entry = ttk.Entry(frame, textvariable=variable)
                self._x_entry.pack(side="left", fill="x", expand=True)
                if self.kind != "mouse_button":
                    self._pick_button = ttk.Button(
                        frame, text="点击屏幕选取…", command=self.start_pick_position,
                    )
                    self._pick_button.pack(side="left", padx=pad(8, 0))
            else:
                entry = ttk.Entry(body, textvariable=variable)
                entry.grid(row=grid_row, column=1, sticky="ew")
                if variable is self.y:
                    self._y_entry = entry
        body.columnconfigure(1, weight=1)
        buttons = ttk.Frame(self, padding=pad(22, 0, 22, 18))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        self._update_pos_mode()

    def _update_pos_mode(self, _event=None):
        current = self.pos_mode.get()
        state = "disabled" if current else "normal"
        for entry in (self._x_entry, self._y_entry):
            if entry is not None:
                entry.configure(state=state)
        if self._pick_button is not None:
            self._pick_button.configure(state="disabled" if current else "normal")

    def start_pick_position(self):
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_point,
            tip_text="点击要执行操作的位置；只记录坐标，不会点击下方窗口；Esc 取消",
            hidden_windows=app_windows(self.master),
        )
        self.picker.start()

    def _apply_picked_point(self, x, y):
        self.x.set(str(int(x)))
        self.y.set(str(int(y)))

    def save(self):
        try:
            x, y = int(self.x.get()), int(self.y.get())
            delay = max(0, int(self.delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "坐标和时间必须是整数。")
            return
        if self.kind == "mouse_button":
            updated = dict(getattr(self, "_source", None) or {})
            updated.update({
                "type": "mouse_button", "button": self.button.get(),
                "down": self.down.get() == "按下", "x": x, "y": y,
                "delay_ms": delay,
            })
            updated.pop("hold_ms", None)
            self.result = updated
            self.destroy()
            return
        try:
            hold = max(1, int(self.hold.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "时间必须是整数。")
            return
        updated = dict(getattr(self, "_source", None) or {})
        updated.update({
            "type": "click", "button": self.button.get(),
            "x": x, "y": y, "hold_ms": hold, "delay_ms": delay,
        })
        # 固定坐标时不写 pos_mode 字段（旧文件零迁移）；勾选当前位置才标记。
        if (getattr(self, "pos_mode", None) is not None and self.pos_mode.get()):
            updated["pos_mode"] = "current"
        else:
            updated.pop("pos_mode", None)
        self.result = updated
        self.destroy()


class ScrollDialog(ModalDialog):
    """添加 / 编辑滚轮动作：把鼠标移到指定位置后向上或向下滚若干格。"""

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "编辑滚轮" if action else "添加滚轮", 480, 360)
        self._source = dict(action or {})
        action = action or {}
        cursor = get_cursor_pos()
        try:
            dy = int(action.get("dy", 0))
        except (TypeError, ValueError):
            dy = 0
        self.direction = tk.StringVar(value=scroll_direction_label(dy))
        self.clicks = tk.StringVar(value=str(scroll_clicks(dy)))
        self.x = tk.StringVar(value=str(action.get("x", cursor[0])))
        self.y = tk.StringVar(value=str(action.get("y", cursor[1])))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.picker = None
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        values = (
            ("滚动方向", self.direction),
            ("滚动格数", self.clicks),
            ("屏幕 X", self.x),
            ("屏幕 Y", self.y),
            ("执行前延时", self.delay),
        )
        for row, (label, variable) in enumerate(values):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            if variable is self.direction:
                ttk.Combobox(
                    body, textvariable=variable,
                    values=(SCROLL_UP_LABEL, SCROLL_DOWN_LABEL), state="readonly",
                ).grid(row=row, column=1, sticky="ew")
            elif variable is self.x:
                frame = ttk.Frame(body)
                frame.grid(row=row, column=1, sticky="ew")
                ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True)
                ttk.Button(
                    frame, text="点击屏幕选取…", command=self.start_pick_position,
                ).pack(side="left", padx=pad(8, 0))
            else:
                ttk.Entry(body, textvariable=variable).grid(row=row, column=1, sticky="ew")
        buttons = ttk.Frame(body)
        buttons.grid(row=len(values), column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def start_pick_position(self):
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_point,
            tip_text="点击要滚动的位置；只记录坐标，不会点击下方窗口；Esc 取消",
            hidden_windows=app_windows(self.master),
        )
        self.picker.start()

    def _apply_picked_point(self, x, y):
        self.x.set(str(int(x)))
        self.y.set(str(int(y)))

    def save(self):
        try:
            x, y = int(self.x.get()), int(self.y.get())
            clicks = max(1, int(self.clicks.get()))
            delay = max(0, int(self.delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "坐标、格数和时间必须是整数。")
            return
        updated = dict(getattr(self, "_source", None) or {})
        updated.update({
            "type": "scroll",
            # Windows 滚轮 delta：正数向上、负数向下，滚动格数即 delta 的绝对值。
            "dx": 0,
            "dy": clicks if self.direction.get() == SCROLL_UP_LABEL else -clicks,
            "x": x, "y": y,
            "delay_ms": delay,
        })
        self.result = updated
        self.destroy()


class GameSetupNoteDialog(ModalDialog):
    """查看/编辑使用本软件前游戏需要设置的参数说明（文字可自行修改）。"""

    def __init__(self, parent, initial_text: str | None = None):
        super().__init__(parent, "游戏设置说明", 660, 480)
        body = ttk.Frame(self, padding=pad(16, 14))
        body.pack(fill="both", expand=True)
        editor = ttk.Frame(body)
        editor.pack(fill="both", expand=True)
        text = tk.Text(
            editor, wrap="word", undo=True,
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            insertbackground=COLOR_TEXT, selectbackground=COLOR_BLUE_SELECTION,
            relief="flat", borderwidth=0, padx=px(10), pady=px(8),
            font=(FONT_FAMILY, FONT_SUBTITLE),
        )
        scroll = ttk.Scrollbar(editor, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", initial_text if initial_text is not None else DEFAULT_GAME_SETUP_NOTE)
        self.text = text
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(12, 0))
        ttk.Button(buttons, text="恢复默认", command=self._restore_default).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def _restore_default(self):
        if self.text.get("1.0", "end-1c").strip() != DEFAULT_GAME_SETUP_NOTE.strip() \
                and not messagebox.askyesno(
                    "恢复默认", "将清空当前修改并恢复默认说明，确定吗？", parent=self,
                ):
            return
        self.text.delete("1.0", "end")
        self.text.insert("1.0", DEFAULT_GAME_SETUP_NOTE)

    def save(self):
        self.result = self.text.get("1.0", "end-1c")
        self.destroy()


class TurnActionDialog(ModalDialog):
    """添加/编辑鼠标转向动作：鼠标相对移动 ΔX/ΔY，不按键（用于游戏内转向）。"""

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "编辑鼠标转向" if action else "添加鼠标转向", 420, 250)
        self._source = dict(action or {})
        self.dx = tk.StringVar(value=str(self._source.get("dx", 0)))
        self.dy = tk.StringVar(value=str(self._source.get("dy", 0)))
        self.delay = duration_var(self._source.get("delay_ms", 0))
        body = ttk.Frame(self, padding=pad(16, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="ΔX").grid(row=0, column=0, sticky="w", pady=px(5))
        ttk.Entry(body, textvariable=self.dx, width=8).grid(row=0, column=1, sticky="ew")
        ttk.Label(body, text="ΔY").grid(row=1, column=0, sticky="w", pady=px(5))
        ttk.Entry(body, textvariable=self.dy, width=8).grid(row=1, column=1, sticky="ew")
        ttk.Label(body, text="执行前延时").grid(row=2, column=0, sticky="w", pady=px(5))
        ttk.Entry(body, textvariable=self.delay, width=8).grid(row=2, column=1, sticky="ew")
        ttk.Label(
            body,
            text="鼠标相对移动量，不按键（游戏内转向）；ΔX 正值向右，ΔY 正值向下。",
            foreground=COLOR_MUTED,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=pad(8, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=pad(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        # 窗口尺寸按内容实际需求收敛（防高 DPI 下内容被裁掉），下限只保证
        # 不会缩得过分，不再把窗口撑出大片空白。
        fit_window_to_content(self, parent)

    def save(self):
        try:
            dx = int(self.dx.get())
            dy = int(self.dy.get())
            delay = max(0, int(self.delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "ΔX、ΔY 和延时都必须是整数。")
            return
        # 基于原动作更新，保留未在对话框中展示的字段（执行后延时、步数等）。
        updated = dict(getattr(self, "_source", None) or {})
        updated["type"] = "turn"
        updated["dx"] = dx
        updated["dy"] = dy
        updated["delay_ms"] = delay
        self.result = updated
        self.destroy()


class RepeatClickDialog(ModalDialog):
    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "添加连续点击", 480, 400)
        self._source = dict(action or {})
        action = action or {}
        cursor = get_cursor_pos()
        self.button = tk.StringVar(value=action.get("button", "left"))
        self.x = tk.StringVar(value=str(action.get("x", cursor[0])))
        self.y = tk.StringVar(value=str(action.get("y", cursor[1])))
        self.count = tk.StringVar(value=str(action.get("count", 2)))
        self.interval = duration_var(action.get("interval_ms", 100))
        self.hold = duration_var(action.get("hold_ms", 30))
        self.delay = duration_var(action.get("delay_ms", 1000))
        self.picker = None
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        values = (
            ("鼠标键", self.button),
            ("屏幕 X", self.x),
            ("屏幕 Y", self.y),
            ("点击次数", self.count),
            ("点击间隔", self.interval),
            ("按住时长", self.hold),
            ("执行前延时", self.delay),
        )
        for row, (label, variable) in enumerate(values):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            if variable is self.button:
                ttk.Combobox(body, textvariable=variable, values=("left", "right", "middle"),
                             state="readonly").grid(row=row, column=1, sticky="ew")
            elif variable is self.x:
                frame = ttk.Frame(body)
                frame.grid(row=row, column=1, sticky="ew")
                ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True)
                ttk.Button(frame, text="点击屏幕选取…", command=self.start_pick_position).pack(side="left", padx=pad(8, 0))
            elif variable is self.y:
                entry = ttk.Entry(body, textvariable=variable)
                entry.grid(row=row, column=1, sticky="ew")
            else:
                ttk.Entry(body, textvariable=variable).grid(row=row, column=1, sticky="ew")
        buttons = ttk.Frame(body)
        buttons.grid(row=len(values), column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def start_pick_position(self):
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_point,
            tip_text="点击要连续点击的位置；只记录坐标，不会点击下方窗口；Esc 取消",
            hidden_windows=app_windows(self.master),
        )
        self.picker.start()

    def _apply_picked_point(self, x, y):
        self.x.set(str(int(x)))
        self.y.set(str(int(y)))

    def save(self):
        try:
            x, y = int(self.x.get()), int(self.y.get())
            count = max(1, int(self.count.get()))
            interval = max(0, int(self.interval.get()))
            hold, delay = max(1, int(self.hold.get())), max(0, int(self.delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "坐标、次数和时间必须是整数。")
            return
        updated = dict(getattr(self, "_source", None) or {})
        updated.update({
            "type": "repeat_click",
            "button": self.button.get(),
            "x": x, "y": y,
            "count": count, "interval_ms": interval,
            "hold_ms": hold, "delay_ms": delay,
        })
        self.result = updated
        self.destroy()


class TextActionDialog(ModalDialog):
    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "编辑文本动作", 520, 280)
        self._source = dict(action or {})
        action = action or {}
        self.text_var = tk.StringVar(value=str(action.get("text", "")))
        self.char_delay = duration_var(action.get("char_delay_ms", 15))
        self.delay = duration_var(action.get("delay_ms", 0))
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="文本内容").grid(row=0, column=0, sticky="w", pady=px(8))
        ttk.Entry(body, textvariable=self.text_var).grid(row=0, column=1, sticky="ew")
        ttk.Label(body, text="字符间隔").grid(row=1, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=10000, increment=1,
            textvariable=self.char_delay, width=10,
        ).grid(row=1, column=1, sticky="ew")
        ttk.Label(body, text="执行前延时").grid(row=2, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.delay, width=10,
        ).grid(row=2, column=1, sticky="ew")
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def save(self):
        try:
            char_delay = max(0, min(10000, int(self.char_delay.get())))
            delay = max(0, min(86400000, int(self.delay.get())))
        except (tk.TclError, ValueError):
            show_floating_notice(self, "参数错误", "时间必须是整数。")
            return
        updated = dict(getattr(self, "_source", None) or {})
        updated.update({
            "type": "text",
            "text": self.text_var.get(),
            "char_delay_ms": char_delay,
            "delay_ms": delay,
        })
        self.result = updated
        self.destroy()


class SetResolutionActionDialog(ModalDialog):
    """Choose a preset and an independent window whose monitor will change."""

    def __init__(self, parent, action: dict | None = None, settings: dict | None = None):
        super().__init__(parent, "设置屏幕分辨率", 560, 470)
        action = action or {}
        self.settings = settings if isinstance(settings, dict) else {}
        self.styles = resolution_styles_from_settings(self.settings)
        names = [str(style["name"]) for style in self.styles]
        saved_name = str(action.get("name", "")).strip()
        if saved_name not in names:
            saved_name = names[0] if names else ""
        self.name = tk.StringVar(value=saved_name)
        self.window_signature = dict(action.get("window") or {})
        self.window_label = tk.StringVar(value=self._window_text(self.window_signature))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.after_delay = duration_var(action.get("after_delay_ms", 0))

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1, minsize=px(380))
        ttk.Label(body, text="分辨率样式").grid(row=0, column=0, sticky="w", pady=px(8))
        self.style_box = ttk.Combobox(
            body, textvariable=self.name, values=names, state="readonly", width=28,
        )
        self.style_box.grid(row=0, column=1, sticky="ew", pady=px(8))
        ttk.Label(
            body,
            text="留空即直接修改当前软件所在显示器；也可以选一个窗口，只修改该窗口"
                 "所在的显示器（不需要游戏目标窗口）。",
            foreground=COLOR_MUTED, wraplength=px(520), justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=pad(0, 4))
        ttk.Label(body, text="参照窗口（可留空）").grid(
            row=2, column=0, sticky="w", pady=px(8),
        )
        window_row = ttk.Frame(body)
        window_row.grid(row=2, column=1, sticky="ew", pady=px(8))
        ttk.Label(
            window_row, textvariable=self.window_label, foreground=COLOR_TEXT,
            wraplength=px(360),
        ).pack(side="left", fill="x", expand=True, anchor="w")
        ttk.Button(
            window_row, text="选择窗口…", command=self.choose_window,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            window_row, text="清除", command=self.clear_window,
        ).pack(side="left", padx=pad(6, 0))
        ttk.Label(body, text="执行前延时").grid(row=3, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(body, from_=0, to=86400000, increment=100,
                    textvariable=self.delay, width=10).grid(row=3, column=1, sticky="w")
        ttk.Label(body, text="执行后延时").grid(row=4, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(body, from_=0, to=86400000, increment=100,
                    textvariable=self.after_delay, width=10).grid(row=4, column=1, sticky="w")
        ttk.Label(
            body,
            text="留空 = 始终改软件自己所在的显示器；选了参照窗口则优先改该窗口所在的"
                 "显示器，窗口没打开时自动退回软件所在显示器并记录一条提示。",
            foreground=COLOR_MUTED, wraplength=px(480), justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=pad(12, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        fit_window_to_content(self, parent)

    @staticmethod
    def _window_text(signature: dict) -> str:
        if not signature:
            return "当前软件所在显示器（未选择参照窗口）"
        title = str(signature.get("title", "")).strip()
        class_name = str(signature.get("class_name", "")).strip()
        return "  ·  ".join(value for value in (title, class_name) if value) or "已保存窗口"

    def choose_window(self):
        selected = WindowPicker(
            self, title="选择分辨率参照窗口", confirm_text="选择此窗口",
        ).show()
        if selected is None:
            return
        self.window_signature = {
            "title": selected.title,
            "class_name": selected.class_name,
            "process_path": selected.process_path,
        }
        self.window_label.set(self._window_text(self.window_signature))

    def clear_window(self):
        self.window_signature = {}
        self.window_label.set(self._window_text(self.window_signature))

    def save(self):
        try:
            action = build_resolution_action(self.settings, self.name.get())
            action["delay_ms"] = max(0, int(self.delay.get()))
            action["after_delay_ms"] = max(0, int(self.after_delay.get()))
            # 留空表示"改当前软件所在显示器"，不写 window 键。
            if self.window_signature:
                action["window"] = dict(self.window_signature)
        except (KeyError, TypeError, ValueError) as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = action
        self.destroy()


class JsonActionDialog(ModalDialog):
    def __init__(self, parent, action: dict):
        super().__init__(parent, "高级动作编辑", 640, 500)
        ttk.Label(self, text="编辑当前动作参数（JSON）", padding=pad(14, 10, 14, 5)).pack(anchor="w")
        self.text = tk.Text(
            self, font=(FONT_MONO, FONT_SUBTITLE), wrap="none", undo=True,
            background=COLOR_SURFACE, foreground=COLOR_TEXT,
            insertbackground=COLOR_TEXT, selectbackground=COLOR_BLUE_SELECTION,
            relief="flat", borderwidth=0, padx=px(12), pady=px(10),
        )
        self.text.pack(fill="both", expand=True, padx=px(18))
        self.text.insert("1.0", json.dumps(action, ensure_ascii=False, indent=2))
        buttons = ttk.Frame(self, padding=px(12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self.save).pack(side="right", padx=px(8))

    def save(self):
        try:
            value = json.loads(self.text.get("1.0", "end"))
            if not isinstance(value, dict) or not value.get("type"):
                raise ValueError("动作必须是包含 type 的对象")
        except (json.JSONDecodeError, ValueError) as exc:
            show_floating_notice(self, "格式错误", str(exc))
            return
        self.result = value
        self.destroy()


def edit_action(parent, action: dict, all_actions: list[dict] | None = None,
                segment_depth: int = 0, on_row_list_test=None,
                settings: dict | None = None) -> dict | None:
    def preserve_identity(updated: dict | None) -> dict | None:
        if updated is not None and action.get("action_id"):
            updated["action_id"] = action["action_id"]
        return updated

    kind = action.get("type")
    if kind == "restart_workflow":
        return preserve_identity(
            RestartWorkflowTargetDialog(
                parent, action, default_row=_app_workflow_default_row(parent),
            ).show(),
        )
    if kind in ("end_current_script", "jump_current_script_last", "block"):
        message = (
            f"{END_CURRENT_SCRIPT_LABEL}，无需配置。"
            if kind == "end_current_script" else
            "执行到这里时会离开模块代码段，并从当前脚本最后一行继续，无需配置。"
            if kind == "jump_current_script_last" else
            "执行到这里时会一直等待，只有其他跳转才能离开，无需配置。"
        )
        show_floating_notice(parent, "特殊模块", message)
        return None
    if kind == "jump":
        return preserve_identity(JumpActionDialog(parent, action, actions=all_actions).show())
    if kind == RECORDED_INPUT_TYPE:
        steps = RecordedInputDialog(parent, recorded_input_steps(action)).show()
        if steps is None:
            return None
        updated = dict(action)
        updated[RECORDED_INPUT_STEPS_KEY] = steps
        return preserve_identity(updated)
    if kind == "activate_window":
        selected = WindowPicker(parent).show()
        if not selected:
            return None
        updated = dict(action)
        updated["window"] = {
            "title": selected.title,
            "class_name": selected.class_name,
            "process_path": selected.process_path,
        }
        return preserve_identity(updated)
    if kind == "global_detect" and action.get("module_ref"):
        return preserve_identity(
            GlobalDetectDialog(
                parent, action, jump=True, actions=all_actions,
            ).show(),
        )
    if kind == "image_match" and action.get("module_ref"):
        return preserve_identity(
            ModuleReferenceDelayDialog(parent, action, actions=all_actions).show(),
        )
    if kind in {"key", "key_press"}:
        return preserve_identity(KeyActionDialog(parent, action).show())
    if kind == "mouse_move":
        return preserve_identity(MouseMoveDialog(parent, action).show())
    if kind in {"click", "mouse_button"}:
        return preserve_identity(ClickDialog(parent, action).show())
    if kind == "turn":
        return preserve_identity(TurnActionDialog(parent, action).show())
    if kind == "repeat_click":
        return preserve_identity(RepeatClickDialog(parent, action).show())
    if kind == "scroll":
        return preserve_identity(ScrollDialog(parent, action).show())
    if kind == "image_match":
        return preserve_identity(ImageActionDialog(parent, action, actions=all_actions).show())
    if kind == "text_ocr":
        return preserve_identity(
            OcrActionDialog(parent, action, actions=all_actions).show(),
        )
    if kind == "ocr_compare":
        return preserve_identity(
            OcrCompareActionDialog(parent, action, actions=all_actions).show(),
        )
    if kind == "multi_condition_click":
        return preserve_identity(
            MultiConditionClickDialog(parent, action).show(),
        )
    if kind == "row_list_condition_click":
        dialog_kwargs = {"on_test": on_row_list_test} if on_row_list_test is not None else {}
        return preserve_identity(
            RowListConditionClickDialog(
                parent, action, actions=all_actions, **dialog_kwargs,
            ).show(),
        )
    if kind == "global_detect":
        return preserve_identity(
            GlobalDetectDialog(
                parent, action, jump=bool(action.get("jump_row")),
                actions=all_actions,
            ).show(),
        )
    if kind == "text":
        return preserve_identity(TextActionDialog(parent, action).show())
    if kind == "delay":
        value = DurationDialog(
            parent, "编辑延时", "延时时间：", int(action.get("ms", 100)),
        ).show()
        if value is not None:
            updated = dict(action)
            updated["ms"] = value
            return updated
        return None
    if kind == "notice":
        title = "编辑浮动提醒"
        text = simpledialog.askstring(
            title, "提示文字：", parent=parent,
            initialvalue=str(action.get("text", "")),
        )
        if text is not None and text.strip():
            updated = dict(action)
            updated["text"] = text.strip()
            return updated
        return None
    if kind == "script_ref":
        return preserve_identity(ScriptRefDialog(parent, action).show())
    if kind == "open_app":
        return preserve_identity(OpenAppDialog(parent, action).show())
    if kind == "close_app":
        return preserve_identity(CloseAppDialog(parent, action).show())
    if kind == "set_resolution":
        return preserve_identity(
            SetResolutionActionDialog(parent, action, settings=settings).show(),
        )
    return preserve_identity(JsonActionDialog(parent, action).show())
