from __future__ import annotations

from macroflow.core.storage import (
    BASE_DIR, DIRECTION_SCRIPTS_DIR, IMAGES_DIR, SCRIPTS_DIR, display_path,
    DEFAULT_MODULE_INTERVAL_MS, DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    load_app_settings, load_module_images_dir, load_module_objects,
    load_script, load_template_regions,
    module_image_inventory, module_objects_by_category,
    registered_module_object, resolve_path, save_module_images_dir, save_module_objects,
    save_template_regions, save_script, script_category_for_path, update_module_object,
)
from macroflow.input.input_guard import KeyCapturer, RESERVED_HOTKEY_VKS
from pathlib import Path
from macroflow.core.resolution import (
    build_resolution_action, normalize_resolution_style,
    resolution_styles_from_settings, SUPPORTED_SCALE_PERCENTS,
)
from macroflow.input.wininput import (
    WindowInfo, enum_windows, get_cursor_pos,
    get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect,
    get_virtual_screen_rect, is_current_process_window, make_window_no_activate,
    set_dark_titlebar, set_rounded_window, show_window_no_activate,
    window_from_point,
)
from tkinter import filedialog, messagebox, simpledialog, ttk
from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL,
    ensure_action_ids, recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
    special_action_label,
)
import tkinter as tk

from .base import (
    COLOR_BLUE_SELECTION,
    COLOR_MUTED,
    COLOR_TEXT,
    HOTKEY_DISALLOWED_NAMES,
    ModalDialog,
    dark_checkbutton,
    duration_var,
    fit_window_to_content,
    pad,
    px,
    selectable_target_windows,
    show_floating_notice,
    vk_to_key_name,
)
from .helpers import (
    _app_workflow_steps,
    image_jump_target_options,
    restart_workflow_row_options,
    select_jump_target_label,
)
from .module_objects import (
    direction_script_files,
)


class WorkflowBatchSettingsDialog(ModalDialog):
    def __init__(self, parent, repeats: int = 1, before_ms: int = 0,
                 repeat_interval_ms: int = 1000, unlimited: bool = False):
        super().__init__(parent, "统一设置工作流参数", 430, 345)
        self.repeats_var = tk.IntVar(value=max(0, int(repeats)))
        self.before_var = duration_var(max(0, int(before_ms)))
        self.interval_var = duration_var(max(0, int(repeat_interval_ms)))
        self.unlimited_var = tk.BooleanVar(value=bool(unlimited))
        self.enabled_vars = {
            "repeats": tk.BooleanVar(value=False),
            "before_ms": tk.BooleanVar(value=False),
            "repeat_interval_ms": tk.BooleanVar(value=False),
        }
        self.value_widgets = {}

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="只勾选需要统一的参数，未勾选项保持每行原值。",
                  foreground=COLOR_MUTED).grid(row=0, column=0, columnspan=3, sticky="w", pady=pad(0, 18))
        for row, (key, label, variable, minimum, maximum, unit) in enumerate((
            ("repeats", "执行次数", self.repeats_var, 0, 999999, "次"),
            ("before_ms", "开始前等待", self.before_var, 0, 86400000, ""),
            ("repeat_interval_ms", "重复间隔", self.interval_var, 0, 86400000, ""),
        ), start=1):
            dark_checkbutton(
                body, text=label, variable=self.enabled_vars[key],
                command=lambda selected=key: self._toggle_field(selected),
            ).grid(row=row, column=0, sticky="w", pady=px(7))
            widget = ttk.Spinbox(body, textvariable=variable, from_=minimum, to=maximum,
                                 width=16, state="disabled")
            widget.grid(row=row, column=1, sticky="ew", padx=pad(18, 8), pady=px(7))
            self.value_widgets[key] = widget
            ttk.Label(body, text=unit, foreground=COLOR_MUTED).grid(row=row, column=2, sticky="w")

        dark_checkbutton(
            body,
            text="不计次数（每次到达这一行都执行一次，不扣减）",
            variable=self.unlimited_var,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=px(7))
        ttk.Label(
            body, text="勾选后所有行都设为不计次数。",
            foreground=COLOR_MUTED,
        ).grid(row=5, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=3, sticky="ew", pady=pad(22, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="应用到全部任务", command=self.save).pack(side="right", padx=px(8))
        body.columnconfigure(1, weight=1)

    def _toggle_field(self, key: str):
        state = "normal" if self.enabled_vars[key].get() else "disabled"
        self.value_widgets[key].configure(state=state)

    def save(self):
        selected = {key for key, variable in self.enabled_vars.items() if variable.get()}
        if self.unlimited_var.get():
            selected.add("unlimited")
        if not selected:
            show_floating_notice(self, "尚未选择", "请至少勾选一个需要统一的参数。")
            return
        try:
            values = {
                "repeats": int(self.repeats_var.get()),
                "before_ms": int(self.before_var.get()),
                "repeat_interval_ms": int(self.interval_var.get()),
            }
        except (tk.TclError, ValueError):
            show_floating_notice(self, "数值无效", "请输入有效的整数。")
            return
        ranges = {
            "repeats": (0, 999999),
            "before_ms": (0, 86400000),
            "repeat_interval_ms": (0, 86400000),
        }
        # “不计次数”不是数值字段，只做开关，不参与范围校验与取值。
        field_keys = [key for key in selected if key in ranges]
        if any(not ranges[key][0] <= values[key] <= ranges[key][1] for key in field_keys):
            show_floating_notice(self, "数值超出范围", "请检查执行次数和等待时间。")
            return
        result = {key: values[key] for key in field_keys}
        if "unlimited" in selected:
            result["unlimited"] = True
        self.result = result
        self.destroy()


class WorkflowRepeatDialog(ModalDialog):
    """Single-row workflow count editor with an always-execute option and an
    optional "repeat 2+ starts from a chosen row" target."""

    def __init__(self, parent, repeats: int = 1, unlimited: bool = False,
                 actions: list[dict] | None = None,
                 repeat_start_action_id: str = "", script_name: str = ""):
        title = "设置执行次数"
        if script_name:
            name = str(script_name).strip()
            if len(name) > 24:
                name = name[:24] + "…"
            title += f" — {name}"
        super().__init__(parent, title, 520, 345)
        self.repeats_var = tk.IntVar(value=max(0, int(repeats)))
        self.unlimited_var = tk.BooleanVar(value=bool(unlimited))
        self.actions = list(actions) if actions else []
        self.jump_options = image_jump_target_options(self.actions)
        self.repeat_start_var = tk.BooleanVar(
            value=bool(repeat_start_action_id) and any(
                action_id == str(repeat_start_action_id).strip()
                for _label, action_id in self.jump_options
            ),
        )
        self._preserved_repeat_start_id = str(repeat_start_action_id).strip()

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="不计次数：只要轮到这一行就执行一次，不扣减次数。",
            foreground=COLOR_MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=pad(0, 14))
        dark_checkbutton(
            body,
            text="不计次数（始终执行）",
            variable=self.unlimited_var,
            command=self._update_count_state,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=px(6))

        count_row = ttk.Frame(body)
        count_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=px(6))
        ttk.Label(count_row, text="剩余次数").pack(side="left")
        self.repeats_spin = ttk.Spinbox(
            count_row, textvariable=self.repeats_var, from_=0, to=999999, width=16,
        )
        self.repeats_spin.pack(side="left", padx=pad(18, 8))
        ttk.Label(count_row, text="次", foreground=COLOR_MUTED).pack(side="left")
        self._update_count_state()

        dark_checkbutton(
            body,
            text="第 2 次及以后从指定行开始",
            variable=self.repeat_start_var,
            command=self._update_repeat_start_state,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=pad(10, 6))

        start_row = ttk.Frame(body)
        start_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=px(6))
        ttk.Label(start_row, text="起始行").pack(side="left")
        if self.jump_options:
            labels = [label for label, _action_id in self.jump_options]
            saved_label = select_jump_target_label(
                str(repeat_start_action_id).strip(), 0, self.jump_options,
            )
            self.repeat_start_combo = ttk.Combobox(
                start_row, values=labels, state="readonly", width=52,
            )
            if saved_label in labels:
                self.repeat_start_combo.set(saved_label)
            elif labels:
                self.repeat_start_combo.set(labels[0])
        else:
            self.repeat_start_combo = ttk.Combobox(
                start_row, values=[], state="disabled", width=52,
            )
        self.repeat_start_combo.pack(side="left", padx=pad(18, 8))

        if not self.jump_options:
            hint = "脚本文件不存在，无法选择起始行。"
        else:
            hint = "第 1 次始终从脚本第 1 行开始；不计次数时此项不生效。"
        ttk.Label(
            body, text=hint, foreground=COLOR_MUTED,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=pad(2, 0))
        self._update_repeat_start_state()

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=pad(20, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        body.columnconfigure(1, weight=1)

    def _update_count_state(self, _event=None):
        state = "disabled" if self.unlimited_var.get() else "normal"
        self.repeats_spin.configure(state=state)

    def _update_repeat_start_state(self, _event=None):
        enabled = bool(self.repeat_start_var.get()) and bool(self.jump_options)
        self.repeat_start_combo.configure(
            state="readonly" if enabled else "disabled",
        )

    def save(self):
        try:
            repeats = max(0, int(self.repeats_var.get()))
        except (tk.TclError, ValueError):
            show_floating_notice(self, "数值无效", "请输入有效的整数。")
            return
        repeat_start_action_id = ""
        if self.repeat_start_var.get() and self.jump_options:
            selected = str(self.repeat_start_combo.get())
            repeat_start_action_id = next(
                (
                    action_id for label, action_id in self.jump_options
                    if label == selected
                ),
                "",
            )
        elif not self.jump_options:
            # 脚本文件缺失：原样保留已有配置，避免误抹掉。
            repeat_start_action_id = str(
                getattr(self, "_preserved_repeat_start_id", ""),
            )
        self.result = {
            "repeats": repeats,
            "unlimited": bool(self.unlimited_var.get()),
            "repeat_start_action_id": repeat_start_action_id,
        }
        self.destroy()


class RestartWorkflowTargetDialog(ModalDialog):
    """配置「重新执行工作流」动作的跳转目标：行对象或使用默认跳转行。

    row=0 表示使用默认：按当前工作流页面统一设置的默认跳转行
    （default_row，工作流文件里的 restart_default_row），未设置则从第 1 行开始。
    """

    def __init__(self, parent, action: dict | None = None,
                 workflow_steps: list[dict] | None = None,
                 default_row: int = 0):
        super().__init__(parent, "重新执行工作流跳转目标", 600, 300)
        self.workflow_steps = list(workflow_steps if workflow_steps is not None
                                   else _app_workflow_steps(parent))
        self.default_row = max(0, int(default_row or 0))
        try:
            saved_row = max(0, int((action or {}).get("restart_workflow_target_row", 0) or 0))
        except (TypeError, ValueError):
            saved_row = 0
        self.row_var = tk.StringVar()
        self.row_ids: dict[str, int] = {}
        self.row_spin_var = tk.StringVar(value=str(saved_row if saved_row > 0 else 1))
        self._reload_options(selected_row=saved_row)

        body = ttk.Frame(self, padding=px(13))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="跳转到").grid(row=0, column=0, sticky="w", padx=pad(0, 12), pady=pad(0, 8))
        self.row_combo = ttk.Combobox(
            body, textvariable=self.row_var, values=self.row_labels,
            state="readonly", width=46,
        )
        self.row_combo.grid(row=0, column=1, sticky="ew", pady=pad(0, 8))
        self.row_combo.bind("<<ComboboxSelected>>", self._on_row_selected)
        ttk.Label(
            body,
            text=("选择工作流里的行对象，触发后从该行重新执行工作流；"
                  "选“使用默认跳转行”时按工作流页面统一设置的默认决定。"
                  "未打开工作流时可勾选“自定义行号…”直接输入。"),
            foreground=COLOR_MUTED, wraplength=px(540),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=pad(4, 0))
        row_frame = ttk.Frame(body)
        row_frame.grid(row=2, column=1, sticky="w", pady=pad(10, 0))
        ttk.Label(row_frame, text="行号", foreground=COLOR_MUTED).pack(side="left")
        self.row_spin = ttk.Spinbox(
            row_frame, from_=1, to=99999, textvariable=self.row_spin_var,
            width=8,
        )
        self.row_spin.pack(side="left", padx=pad(8, 0))
        self._on_row_selected()
        hint_frame = ttk.Frame(body)
        hint_frame.grid(row=3, column=0, columnspan=2, sticky="w", pady=pad(14, 0))
        self.default_label = ttk.Label(
            hint_frame,
            text=self._default_hint(), foreground=COLOR_MUTED,
        )
        self.default_label.pack(side="left")
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="ew", pady=pad(20, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def _default_hint(self) -> str:
        if not self.default_row:
            return "工作流默认：未设置（按第 1 行处理）"
        return f"工作流默认：第 {self.default_row} 行（在工作流页面统一设置）"

    def _reload_options(self, selected_row: int = 0):
        labels, self.row_ids = restart_workflow_row_options(
            self.workflow_steps, self.default_row,
        )
        self.row_labels = labels + ["自定义行号…"]
        selected = next(
            (label for label, row in self.row_ids.items() if row == selected_row),
            "自定义行号…" if not self.workflow_steps else self.row_labels[0],
        )
        self.row_var.set(selected)

    def _on_row_selected(self, _event=None):
        custom = self.row_var.get() == "自定义行号…"
        if not custom and not self.workflow_steps:
            self.row_var.set("自定义行号…")
            custom = True
        self.row_spin.configure(state="normal" if custom else "readonly")

    def save(self):
        label = self.row_var.get()
        row = self.row_ids.get(label, 0)
        if not row and label == "自定义行号…":
            try:
                row = max(1, int(self.row_spin_var.get()))
            except (TypeError, ValueError):
                show_floating_notice(self, "行号格式错误", "请输入 1–99999 之间的行号。")
                return
        self.result = {"type": "restart_workflow", "restart_workflow_target_row": row}
        self.destroy()


class ScriptRefDialog(ModalDialog):
    """Choose another script to reference; its latest content is read at runtime."""

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "引用脚本", 560, 360)
        action = action or {}
        self.script = tk.StringVar(value=str(action.get("script", "")))
        self.repeats = tk.StringVar(value=str(script_ref_repeat_count(action)))
        self.delay = duration_var(action.get("delay_ms", 0))
        self.after_delay = duration_var(action.get("after_delay_ms", 0))

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="脚本文件").grid(row=0, column=0, sticky="w", pady=px(8))
        script_row = ttk.Frame(body)
        script_row.grid(row=0, column=1, sticky="ew")
        ttk.Entry(script_row, textvariable=self.script, state="readonly").pack(
            side="left", fill="x", expand=True,
        )
        ttk.Button(script_row, text="替换脚本…", command=self.choose).pack(
            side="left", padx=pad(6, 0),
        )

        ttk.Label(body, text="执行次数").grid(row=1, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=1, to=999999, increment=1,
            textvariable=self.repeats, width=10,
        ).grid(row=1, column=1, sticky="ew")

        ttk.Label(body, text="执行前延时").grid(row=2, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.delay, width=10,
        ).grid(row=2, column=1, sticky="ew")
        ttk.Label(body, text="执行后延时").grid(row=3, column=0, sticky="w", pady=px(8))
        ttk.Spinbox(
            body, from_=0, to=86400000, increment=100,
            textvariable=self.after_delay, width=10,
        ).grid(row=3, column=1, sticky="ew")

        ttk.Label(
            body,
            text="运行时实时读取所选脚本的最新内容；修改原脚本后，这里的引用会自动跟着更新。执行次数为每次引用动作的完整运行次数。",
            foreground=COLOR_MUTED, wraplength=px(480),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=pad(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def choose(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择要引用的脚本",
            filetypes=[("MacroFlow 脚本", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self.script.set(display_path(Path(path)))

    def save(self):
        script = self.script.get().strip()
        if not script:
            show_floating_notice(self, "脚本无效", "请选择要引用的脚本。")
            return
        try:
            repeats = int(self.repeats.get())
            if repeats < 1:
                raise ValueError
            delay = max(0, int(self.delay.get()))
            after_delay = max(0, int(self.after_delay.get()))
        except ValueError:
            show_floating_notice(self, "参数错误", "执行次数必须是正整数，延时必须是整数毫秒。")
            return
        self.result = {
            "type": "script_ref",
            "script": script,
            "repeats": repeats,
            "delay_ms": delay,
            "after_delay_ms": after_delay,
        }
        self.destroy()


class ScriptDirectoriesDialog(ModalDialog):
    """Configure the save folders for the three script categories."""

    def __init__(self, parent, level_dir: str = "scripts/关卡",
                 level_pack_dir: str = "scripts/关卡封装",
                 switch_dir: str = "scripts/切换",
                 direction_dir: str = DIRECTION_SCRIPTS_DIR):
        super().__init__(parent, "脚本保存目录", 560, 420)
        self.level_dir = tk.StringVar(value=level_dir or "scripts/关卡")
        self.level_pack_dir = tk.StringVar(value=level_pack_dir or "scripts/关卡封装")
        self.switch_dir = tk.StringVar(value=switch_dir or "scripts/切换")
        self.direction_dir = tk.StringVar(value=direction_dir or DIRECTION_SCRIPTS_DIR)
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        for row, (label, variable) in enumerate((
            ("关卡脚本目录", self.level_dir),
            ("关卡封装脚本目录", self.level_pack_dir),
            ("切换脚本目录", self.switch_dir),
            ("方向脚本目录", self.direction_dir),
        )):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            row_frame = ttk.Frame(body)
            row_frame.grid(row=row, column=1, sticky="ew")
            ttk.Entry(row_frame, textvariable=variable).pack(side="left", fill="x", expand=True)
            ttk.Button(
                row_frame, text="浏览…", width=7,
                command=lambda var=variable: self._browse(var),
            ).pack(side="left", padx=pad(6, 0))
        ttk.Label(
            body,
            text="脚本类别包含关卡、关卡封装、切换和方向；方向目录的脚本供快捷键绑定执行。工作流全局与脚本全局属于模块类别。可填绝对路径或相对路径。",
            foreground=COLOR_MUTED, wraplength=px(480),
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=pad(12, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    def _browse(self, variable):
        path = filedialog.askdirectory(
            parent=self, title="选择脚本保存目录",
            initialdir=str(Path(variable.get()).resolve()) if variable.get().strip() else str(BASE_DIR),
        )
        if path:
            variable.set(path)

    def save(self):
        self.result = {
            "level_dir": self.level_dir.get().strip() or "scripts/关卡",
            "level_pack_dir": self.level_pack_dir.get().strip() or "scripts/关卡封装",
            "switch_dir": self.switch_dir.get().strip() or "scripts/切换",
            "direction_dir": self.direction_dir.get().strip() or DIRECTION_SCRIPTS_DIR,
        }
        self.destroy()


class HotkeyBindingDialog(ModalDialog):
    """Capture one hotkey key and pick the script it runs."""

    def __init__(self, parent, current: dict | None = None):
        super().__init__(parent, "设置快捷键绑定", 580, 300)
        self.current = dict(current) if current else None
        self.key_vk = int((current or {}).get("vk") or 0)
        self.key_name_var = tk.StringVar(
            value=str((current or {}).get("key", "")) if current else ""
        )
        self.script_var = tk.StringVar(
            value=str((current or {}).get("script", "")) if current else ""
        )
        self._capturer = None
        self._script_labels: dict[str, Path] = {}
        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="快捷键").grid(row=0, column=0, sticky="w", pady=px(8))
        key_row = ttk.Frame(body)
        key_row.grid(row=0, column=1, sticky="ew")
        self.key_label = ttk.Label(
            key_row, text=self.key_name_var.get() or "未设置",
            foreground=COLOR_MUTED, width=16,
        )
        self.key_label.pack(side="left")
        ttk.Button(
            key_row, text="按下新键…", width=10,
            command=self._capture_key,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Label(body, text="执行脚本").grid(row=1, column=0, sticky="w", pady=px(8))
        script_row = ttk.Frame(body)
        script_row.grid(row=1, column=1, sticky="ew")
        self.script_box = ttk.Combobox(
            script_row, textvariable=self.script_var, width=34,
        )
        self.script_box.pack(side="left", fill="x", expand=True)
        self.script_box.bind("<MouseWheel>", lambda _event: "break")
        self._refresh_script_options()
        ttk.Label(
            body,
            text="执行脚本只能从「scripts/方向」目录选择（下拉只显示脚本名）；按快捷键立即执行该脚本（快捷键本身不会录进当前脚本，脚本回放的按键与鼠标会被录进）。",
            foreground=COLOR_MUTED, wraplength=px(500),
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=pad(12, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

        # 固定 300 高度在打包后的 EXE（按真实 DPI 渲染）里会装不下内容：
        # 高 DPI 下各行与说明文字的实际需求高度超过窗口，按钮行被挤出窗口。
        # 按内容实际需求重设窗口尺寸并重新居中（与仓库其他对话框一致）。
        fit_window_to_content(self, parent)

    def _refresh_script_options(self):
        root = resolve_path(DIRECTION_SCRIPTS_DIR)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        labels = {}
        try:
            files = direction_script_files()
        except Exception:
            files = []
        for path in files:
            # 下拉只显示脚本名，不显示父辈目录。
            labels[path.stem] = path
        saved = self.script_var.get().strip()
        if saved:
            candidate = resolve_path(saved)
            try:
                resolved = candidate.resolve()
                inside = resolved == root.resolve() or root.resolve() in resolved.parents
            except OSError:
                inside = False
            if candidate.is_file() and inside:
                # 已保存的是相对/绝对路径：回填为脚本名。
                self.script_var.set(candidate.stem)
        self._script_labels = labels
        self.script_box.configure(values=list(labels))

    def _capture_key(self):
        self.key_label.configure(text="请按键…")

        def on_key(vk):
            def apply():
                name = vk_to_key_name(vk)
                if name in HOTKEY_DISALLOWED_NAMES:
                    self.key_label.configure(text=self.key_name_var.get() or "未设置")
                    show_floating_notice(
                        self, "快捷键不可用",
                        f"{name} 是系统功能键，不能单独作为快捷键。",
                    )
                    return
                self.key_vk = int(vk)
                self.key_name_var.set(name)
                self.key_label.configure(text=name)
            try:
                self.after(0, apply)
            except tk.TclError:
                pass

        def on_cancel():
            def apply():
                self.key_label.configure(text=self.key_name_var.get() or "未设置")
            try:
                self.after(0, apply)
            except tk.TclError:
                pass

        capturer = KeyCapturer(on_key, on_cancel)
        self._capturer = capturer
        if not capturer.start():
            self._capturer = None
            self.key_label.configure(text="无法捕获按键")
            return

    def destroy(self):
        capturer = self._capturer
        if capturer is not None:
            try:
                capturer.stop()
            except Exception:
                pass
        self._capturer = None
        super().destroy()

    def save(self):
        if not self.key_vk:
            show_floating_notice(self, "缺少快捷键", "请先点击“按下新键…”设置快捷键。")
            return
        name = vk_to_key_name(self.key_vk)
        if name in HOTKEY_DISALLOWED_NAMES:
            show_floating_notice(self, "快捷键不可用", f"{name} 是系统功能键，不能单独作为快捷键。")
            return
        if self.key_vk in RESERVED_HOTKEY_VKS:
            show_floating_notice(self, "快捷键不可用", "F8/F9/F12 已被录制、执行与紧急停止占用。")
            return
        raw = self.script_var.get().strip()
        path = self._script_labels.get(raw)
        if path is None:
            # 只允许 scripts/方向 目录里的脚本：按名字或路径解析后校验目录。
            candidate = resolve_path(raw)
            root = resolve_path(DIRECTION_SCRIPTS_DIR)
            try:
                resolved = candidate.resolve()
                inside = resolved == root.resolve() or root.resolve() in resolved.parents
            except OSError:
                inside = False
            if candidate.is_file() and inside:
                path = candidate
        if path is None:
            show_floating_notice(
                self, "缺少脚本",
                f"只能选择「{DIRECTION_SCRIPTS_DIR}」目录中的脚本（下拉只显示脚本名）。",
            )
            return
        self.result = {"key": name, "vk": self.key_vk, "script": display_path(path)}
        self.destroy()


class HotkeyScriptsDialog(ModalDialog):
    """Manage the list of hotkey → script bindings."""

    def __init__(self, parent, bindings: list[dict] | None = None):
        super().__init__(parent, "快捷键脚本", 720, 470)
        self.bindings = [dict(item) for item in (bindings or [])]
        top = ttk.Frame(self, padding=pad(14, 10, 14, 5))
        top.pack(fill="x")
        ttk.Label(
            top, text="在录制或执行脚本的过程中，按下快捷键立即执行绑定的脚本。",
            foreground=COLOR_MUTED,
        ).pack(anchor="w")
        ttk.Label(
            top, text="例如把 J 绑定到“转向左 90°”脚本：录制时按 J，转向操作会被录进当前脚本。",
            foreground=COLOR_MUTED,
        ).pack(anchor="w", pady=pad(4, 0))
        frame = ttk.Frame(self, padding=pad(14, 5, 14, 6))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            frame, columns=("key", "script"), show="headings", selectmode="browse",
        )
        self.tree.heading("key", text="快捷键")
        self.tree.heading("script", text="脚本")
        self.tree.column("key", width=px(110), anchor="center")
        self.tree.column("script", width=px(460), stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self._edit_selected())
        side = ttk.Frame(frame)
        side.pack(side="left", fill="y", padx=pad(10, 0))
        ttk.Button(side, text="添加", command=self._add).pack(fill="x")
        ttk.Button(side, text="编辑", command=self._edit_selected).pack(fill="x", pady=pad(6, 0))
        ttk.Button(side, text="删除", command=self._remove_selected).pack(fill="x", pady=pad(6, 0))
        ttk.Button(side, text="清空", command=self._clear_all).pack(fill="x", pady=pad(6, 0))
        bottom = ttk.Frame(self, padding=pad(18, 8, 18, 14))
        bottom.pack(fill="x")
        ttk.Label(
            bottom, text="F8/F9/F12 为系统功能键，不可绑定；快捷键脚本按纯动作执行。",
            foreground=COLOR_MUTED,
        ).pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="保存", command=self.save).pack(side="right", padx=px(8))
        self._render()

        # 固定 720×470 在打包后的 EXE（按真实 DPI 渲染）里会装不下内容：
        # 高 DPI 下列表行高、按钮和说明文字的需求尺寸都变大，底部按钮行被
        # 挤出窗口；脚本列固定 530px 也常被右侧按钮区挤出。按内容实际需求
        # 重设窗口尺寸并重新居中，脚本列随窗口拉伸（与仓库其他对话框一致）。
        fit_window_to_content(self, parent)

    def _render(self):
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.bindings):
            script = str(item.get("script", ""))
            name = Path(script).stem or "未设置"
            # 只显示脚本名，不显示父辈目录。
            self.tree.insert(
                "", "end", iid=str(index),
                values=(str(item.get("key", "")), name),
            )

    def _add(self):
        self._edit_index(None)

    def _edit_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        self._edit_index(int(selected[0]))

    def _edit_index(self, index: int | None):
        current = self.bindings[index] if index is not None else None
        dialog = HotkeyBindingDialog(self, current)
        result = dialog.show()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        if result is None:
            return
        key = str(result.get("key", ""))
        if any(
            str(item.get("key", "")).upper() == key.upper()
            for pos, item in enumerate(self.bindings)
            if pos != index
        ):
            show_floating_notice(self, "快捷键重复", f"快捷键 {key} 已被其他绑定使用。")
            return
        if index is None:
            self.bindings.append(result)
        else:
            self.bindings[index] = result
        self._render()

    def _remove_selected(self):
        selected = self.tree.selection()
        if not selected:
            return
        self.bindings.pop(int(selected[0]))
        self._render()

    def _clear_all(self):
        self.bindings = []
        self._render()

    def save(self):
        self.result = list(self.bindings)
        self.destroy()


class WindowPicker(ModalDialog):
    def __init__(self, parent, title: str = "选择要绑定的窗口",
                 confirm_text: str = "绑定所选窗口"):
        super().__init__(parent, title, 820, 520)
        self.confirm_text = confirm_text
        self.windows: list[WindowInfo] = []
        self.search_var = tk.StringVar()
        self.dragging = False
        top = ttk.Frame(self, padding=px(14))
        top.pack(fill="x")
        ttk.Label(top, text="搜索窗口").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.search_var, width=42)
        entry.pack(side="left", padx=px(10))
        entry.bind("<KeyRelease>", lambda _: self._render())
        self.drag_handle = tk.Label(
            top, text="✚", width=3, cursor="crosshair",
            background=COLOR_BLUE_SELECTION, foreground=COLOR_TEXT,
            font=("Segoe UI Symbol", 13), relief="raised", bd=1,
        )
        self.drag_handle.pack(side="right", padx=pad(8, 0))
        self.drag_handle.bind("<ButtonPress-1>", self._drag_start)
        self.drag_handle.bind("<B1-Motion>", self._drag_motion)
        self.drag_handle.bind("<ButtonRelease-1>", self._drag_release)
        ttk.Label(top, text="按住十字拖到目标窗口后松开", foreground=COLOR_MUTED).pack(side="right", padx=pad(8, 0))
        ttk.Button(top, text="刷新", command=self.refresh).pack(side="right")
        frame = ttk.Frame(self, padding=pad(14, 0, 14, 8))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("title", "class"), show="headings", selectmode="browse")
        self.tree.heading("title", text="窗口标题")
        self.tree.heading("class", text="窗口类")
        self.tree.column("title", width=px(540))
        self.tree.column("class", width=px(220))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _: self.choose())
        bottom = ttk.Frame(self, padding=px(14))
        bottom.pack(fill="x")
        ttk.Label(bottom, text="仅显示当前可见且有标题的顶层窗口。", foreground=COLOR_MUTED).pack(side="left")
        ttk.Button(bottom, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text=self.confirm_text, command=self.choose).pack(side="right", padx=px(8))
        self.refresh()

    def refresh(self):
        self.windows = selectable_target_windows(enum_windows())
        self._render()

    def _render(self):
        query = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(self.windows):
            if query and query not in item.title.lower() and query not in item.class_name.lower():
                continue
            self.tree.insert("", "end", iid=str(index), values=(item.title, item.class_name))

    def choose(self):
        selected = self.tree.selection()
        if not selected:
            show_floating_notice(self, "请选择", "请先选择一个窗口。")
            return
        self.result = self.windows[int(selected[0])]
        self.destroy()


    def _drag_start(self, _event):
        self.dragging = True
        self.configure(cursor="crosshair")
        self.drag_handle.configure(text="●")

    def _drag_motion(self, _event):
        if not self.dragging:
            return
        x, y = get_cursor_pos()
        info = window_from_point(x, y)
        if info and not is_current_process_window(info.hwnd):
            self.title(f"拖放选择：{info.title or info.class_name}")
        else:
            self.title("选择要绑定的窗口")

    def _drag_release(self, _event):
        if not self.dragging:
            return
        self.dragging = False
        self.configure(cursor="")
        self.drag_handle.configure(text="✚")
        x, y = get_cursor_pos()
        info = window_from_point(x, y)
        if info and not is_current_process_window(info.hwnd):
            self.result = info
            self.destroy()
        else:
            self.title("选择要绑定的窗口")


class ResolutionStyleEditorDialog(ModalDialog):
    """Edit one named display-resolution preset."""

    def __init__(self, parent, style: dict | None = None):
        super().__init__(parent, "编辑分辨率样式", 500, 390)
        style = style or {}
        self.name = tk.StringVar(value=str(style.get("name", "")))
        self.width = tk.StringVar(value=str(style.get("width", "1920")))
        self.height = tk.StringVar(value=str(style.get("height", "1080")))
        self.refresh_rate = tk.StringVar(value=str(style.get("refresh_rate", 0)))
        self.scale_percent = tk.StringVar(value=str(style.get("scale_percent", 100)))

        body = ttk.Frame(self, padding=px(14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        for row, label, variable in (
            (0, "样式名称", self.name),
            (1, "宽度", self.width),
            (2, "高度", self.height),
            (3, "刷新率", self.refresh_rate),
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            ttk.Entry(body, textvariable=variable, width=18).grid(
                row=row, column=1, sticky="ew", pady=px(8),
            )
        ttk.Label(body, text="缩放").grid(row=4, column=0, sticky="w", pady=px(8))
        scale_row = ttk.Frame(body)
        scale_row.grid(row=4, column=1, sticky="w", pady=px(8))
        ttk.Combobox(
            scale_row,
            textvariable=self.scale_percent,
            values=[str(value) for value in SUPPORTED_SCALE_PERCENTS],
            state="readonly", width=16,
        ).pack(side="left")
        ttk.Label(scale_row, text="%", foreground=COLOR_MUTED).pack(
            side="left", padx=pad(6, 0),
        )
        ttk.Label(
            body, text="刷新率填 0 表示沿用当前显示器的刷新率；缩放默认 100%。",
            foreground=COLOR_MUTED,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=pad(4, 0))
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        fit_window_to_content(self, parent)

    def save(self):
        try:
            self.result = normalize_resolution_style({
                "name": self.name.get(),
                "width": self.width.get(),
                "height": self.height.get(),
                "refresh_rate": self.refresh_rate.get(),
                "scale_percent": self.scale_percent.get(),
            })
        except ValueError as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.destroy()


class ResolutionStylesDialog(ModalDialog):
    """Manage the named display-resolution presets stored in app settings."""

    def __init__(self, parent, settings: dict | None = None):
        super().__init__(parent, "分辨率样式设置", 680, 480)
        self.styles = resolution_styles_from_settings(settings)

        body = ttk.Frame(self, padding=px(16))
        body.pack(fill="both", expand=True)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body,
            text="动作会从这里读取样式；样式保存为动作快照，之后修改本列表不会改变已有动作。",
            foreground=COLOR_MUTED, wraplength=px(620),
        ).grid(row=0, column=0, sticky="w", pady=pad(0, 10))
        self.tree = ttk.Treeview(
            body, columns=("name", "size", "refresh", "scale"), show="headings", height=10,
        )
        for column, title, width in (
            ("name", "名称", 260), ("size", "分辨率", 140),
            ("refresh", "刷新率", 110), ("scale", "缩放", 90),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew")
        self.tree.bind("<Double-1>", lambda _event: self.edit())

        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, sticky="ew", pady=pad(10, 0))
        ttk.Button(buttons, text="新增", command=self.add).pack(side="left")
        ttk.Button(buttons, text="编辑", command=self.edit).pack(side="left", padx=pad(6, 0))
        ttk.Button(buttons, text="删除", command=self.remove).pack(side="left", padx=pad(6, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self.save).pack(side="right", padx=pad(0, 8))
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for index, style in enumerate(self.styles):
            refresh = f"{style['refresh_rate']} Hz" if style["refresh_rate"] else "沿用当前"
            scale = f"{style['scale_percent']}%"
            self.tree.insert(
                "", "end", iid=str(index),
                values=(style["name"], f"{style['width']}×{style['height']}", refresh, scale),
            )

    def _selected_index(self) -> int | None:
        selected = self.tree.selection()
        if not selected:
            return None
        try:
            index = int(selected[0])
        except ValueError:
            return None
        return index if 0 <= index < len(self.styles) else None

    def add(self):
        result = ResolutionStyleEditorDialog(self).show()
        if result is None:
            return
        if any(str(item["name"]).casefold() == str(result["name"]).casefold() for item in self.styles):
            show_floating_notice(self, "名称重复", "请使用不同的样式名称。")
            return
        self.styles.append(result)
        self._refresh_tree()

    def edit(self):
        index = self._selected_index()
        if index is None:
            show_floating_notice(self, "编辑样式", "请先选择一个样式。")
            return
        result = ResolutionStyleEditorDialog(self, self.styles[index]).show()
        if result is None:
            return
        if any(
            other != index and str(item["name"]).casefold() == str(result["name"]).casefold()
            for other, item in enumerate(self.styles)
        ):
            show_floating_notice(self, "名称重复", "请使用不同的样式名称。")
            return
        self.styles[index] = result
        self._refresh_tree()

    def remove(self):
        index = self._selected_index()
        if index is None:
            show_floating_notice(self, "删除样式", "请先选择一个样式。")
            return
        self.styles.pop(index)
        self._refresh_tree()

    def save(self):
        if not self.styles:
            show_floating_notice(self, "无法保存", "至少保留一个分辨率样式。")
            return
        self.result = [dict(style) for style in self.styles]
        self.destroy()
