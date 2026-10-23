import tkinter as tk
from tkinter import ttk

from macroflow.core.page_state import (
    PAGE_DEFAULT_THRESHOLD, PAGE_LIBRARY_DIR,
)
from macroflow.core.storage import BASE_DIR
from macroflow.ui.form_controls import integer_seconds_spinbox
from .base import (
    COLOR_MUTED, ModalDialog, app_windows, dark_checkbutton, duration_var,
    interval_var, pad, px, show_floating_notice,
)
from .helpers import image_jump_target_options
from .screen_pickers import ScreenPointPicker

MODE_OPTIONS = (
    ("等待出现", "enter"),
    ("等待消失", "leave"),
    ("立即判断", "check"),
)
TIMEOUT_OPTIONS = (
    ("继续下一行", "continue"),
    ("结束当前脚本", "end_current_script"),
    ("跳转到脚本行", "jump"),
)
BUTTON_OPTIONS = ("左键", "右键", "中键")
SOURCE_OPTIONS = (
    ("战斗界面（内存）", "battle_ui"),
    ("大厅页面（整页截图）", "screen"),
)

# 本次运行里读到过的战斗界面名，供对话框下拉复用。
_UI_KEY_CACHE: list[str] = []


def page_library_names() -> list[str]:
    from macroflow.core.page_state import PageMatcher
    return PageMatcher(BASE_DIR / PAGE_LIBRARY_DIR).page_names()


class PageActionDialog(ModalDialog):
    """页面动作：等战斗界面/大厅页面出现或消失，或立即判断一次。"""

    def __init__(self, parent, action: dict | None = None,
                 actions: list[dict] | None = None):
        super().__init__(parent, "编辑页面动作" if action else "添加页面动作", 680, 800)
        self.data = dict(action or {})
        source_key = str(self.data.get("source") or "battle_ui")
        selected_source = next(
            (label for label, key in SOURCE_OPTIONS if key == source_key),
            SOURCE_OPTIONS[0][0],
        )
        self.source = tk.StringVar(self, selected_source)
        names = page_library_names()
        self.page_names = names
        self.page_name = tk.StringVar(
            self, str(self.data.get("page") or (names[0] if names else "")),
        )
        self.ui_key = tk.StringVar(self, str(self.data.get("ui_key") or ""))
        self._probe_text = ""
        selected_mode = next(
            (label for label, key in MODE_OPTIONS if key == self.data.get("mode")),
            MODE_OPTIONS[0][0],
        )
        self.mode = tk.StringVar(self, selected_mode)
        self.threshold = tk.StringVar(
            self, str(self.data.get("threshold", PAGE_DEFAULT_THRESHOLD)),
        )
        self.timeout = duration_var(self.data.get("timeout_ms", 3000))
        self.interval = interval_var(self.data.get("interval_ms", 3000))
        self.wait_forever = tk.BooleanVar(
            self, int(self.data.get("timeout_ms", 3000) or 0) <= 0,
        )
        click_point = self.data.get("click_point") or [0, 0]
        self.click_enabled = tk.BooleanVar(self, bool(self.data.get("click")))
        self.click_point = tk.StringVar(
            self, ",".join(map(str, click_point)) if len(click_point) == 2 else "",
        )
        self.click_count = tk.StringVar(self, str(max(1, int(self.data.get("click_count", 1)))))
        self.button = tk.StringVar(self, {
            "left": "左键", "right": "右键", "middle": "中键",
        }.get(str(self.data.get("button", "left")), "左键"))
        selected_timeout = next(
            (label for label, key in TIMEOUT_OPTIONS if key == self.data.get("on_timeout")),
            TIMEOUT_OPTIONS[0][0],
        )
        self.on_timeout = tk.StringVar(self, selected_timeout)
        jump_options = image_jump_target_options(actions or [])
        self.jump_target_labels = [label for label, _action_id in jump_options]
        self.jump_target_ids = dict(jump_options)
        saved_target_id = str(self.data.get("timeout_jump_action_id", "")).strip()
        selected_target = next(
            (label for label in self.jump_target_labels
             if self.jump_target_ids.get(label) == saved_target_id),
            self.jump_target_labels[0] if self.jump_target_labels else "",
        )
        self.jump_target = tk.StringVar(self, selected_target)
        self.notice = tk.BooleanVar(self, bool(self.data.get("show_result_notice", False)))
        self.delay = duration_var(self.data.get("delay_ms", 0))
        self.after_delay = duration_var(self.data.get("after_delay_ms", 0))

        body = ttk.Frame(self, padding=px(16))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        row = [0]
        self._row_widgets = {}

        def next_row() -> int:
            row[0] += 1
            return row[0] - 1

        def labeled(title: str, widget, note: str = "") -> int:
            index = next_row()
            row_widgets = []
            label = ttk.Label(body, text=title)
            label.grid(
                row=index, column=0, sticky="w", padx=pad(0, 10), pady=pad(3, 3),
            )
            widget.grid(row=index, column=1, sticky="we", pady=pad(3, 3))
            row_widgets.extend((label, widget))
            if note:
                note_label = ttk.Label(
                    body, text=note, foreground=COLOR_MUTED, wraplength=px(450),
                )
                note_label.grid(row=next_row(), column=0, columnspan=2, sticky="w")
                row_widgets.append(note_label)
            self._row_widgets[index] = tuple(row_widgets)
            return index

        def full_width(text: str) -> tuple[int, ttk.Label]:
            index = next_row()
            label = ttk.Label(
                body, text=text, foreground=COLOR_MUTED, wraplength=px(590),
            )
            label.grid(row=index, column=0, columnspan=2, sticky="w", pady=pad(0, 6))
            self._row_widgets[index] = (label,)
            return index, label

        full_width(
            "战斗界面直接读游戏内存（需先进对局），不再截图；"
            "大厅没有可读的页面状态，只能用整页截图判断。",
        )

        source_frame = ttk.Frame(body)
        for label, _key in SOURCE_OPTIONS:
            ttk.Radiobutton(
                source_frame, text=label, variable=self.source, value=label,
                command=self._refresh_visibility,
            ).pack(side="left", padx=pad(0, 16))
        self.source_row = labeled("来源", source_frame)

        battle_frame = ttk.Frame(body)
        self.ui_key_combo = ttk.Combobox(
            battle_frame, textvariable=self.ui_key, values=_UI_KEY_CACHE,
        )
        self.ui_key_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(
            battle_frame, text="读取当前", command=self._read_current_ui,
        ).pack(side="left", padx=pad(8, 0))
        self.battle_row = labeled(
            "战斗界面", battle_frame,
            "点“读取当前”抓取当前可见的界面名；要等待还没出现的界面时，可直接输入名字。",
        )

        self.probe_row, self._probe_label = full_width("")

        page_combo = ttk.Combobox(
            body, textvariable=self.page_name, values=names, state="readonly",
        )
        self.page_row = labeled("页面", page_combo)

        self.empty_row = None
        if not names:
            self.empty_row, _empty_label = full_width(
                f"页面库为空：请把整页截图放到 {PAGE_LIBRARY_DIR} 后再新建页面动作。",
            )

        mode_combo = ttk.Combobox(
            body, textvariable=self.mode, values=[label for label, _key in MODE_OPTIONS],
            state="readonly",
        )
        mode_combo.bind("<<ComboboxSelected>>", self._refresh_visibility)
        labeled(
            "判定方式", mode_combo,
            "等待出现：界面/页面没出现就一直等；等待消失：还在就一直等；"
            "立即判断：只判断一次，不等待。",
        )

        threshold_row = ttk.Frame(body)
        threshold_entry = ttk.Entry(
            threshold_row, textvariable=self.threshold, width=8,
        )
        threshold_entry.pack(side="left")
        ttk.Label(threshold_row, text="（0.1~1.0）").pack(side="left", padx=pad(6, 0))
        self.threshold_row = labeled(
            "相似度阈值", threshold_row,
            "与页面库截图的相似度；同一页面实机通常在 0.98 以上，不同页面大多低于 0.9。",
        )

        self.timeout_row = ttk.Frame(body)
        timeout_entry = ttk.Entry(self.timeout_row, textvariable=self.timeout, width=10)
        timeout_entry.pack(side="left")
        self.timeout_entry = timeout_entry
        self.wait_forever_check = dark_checkbutton(
            self.timeout_row, "一直等待（不超时）", self.wait_forever,
            command=self._refresh_visibility,
        )
        self.wait_forever_check.pack(side="left", padx=pad(12, 0))
        self.timeout_row_index = labeled("超时时间", self.timeout_row)

        self.interval_row = ttk.Frame(body)
        self.interval_spinbox = integer_seconds_spinbox(
            self.interval_row, textvariable=self.interval, to=10,
        )
        self.interval_spinbox.pack(side="left")
        self.interval_row_index = labeled(
            "检测间隔（秒）", self.interval_row,
            "每次判断之间的整数秒数，默认 3 秒；越小越灵敏，越大越省资源。",
        )

        self.click_row = ttk.Frame(body)
        self.click_check = dark_checkbutton(
            self.click_row, "成功后点击", self.click_enabled,
            command=self._refresh_visibility,
        )
        self.click_check.pack(side="left")
        labeled("成功后", self.click_row)

        self.click_point_row = ttk.Frame(body)
        self.click_point_entry = ttk.Entry(
            self.click_point_row, textvariable=self.click_point, width=14,
        )
        self.click_point_entry.pack(side="left")
        self.click_point_button = ttk.Button(
            self.click_point_row, text="幕布选取…", command=self._pick_click_point,
        )
        self.click_point_button.pack(side="left", padx=pad(8, 0))
        ttk.Label(self.click_point_row, text="按钮").pack(side="left", padx=pad(8, 0))
        self.button_combo = ttk.Combobox(
            self.click_point_row, textvariable=self.button, values=BUTTON_OPTIONS,
            state="readonly", width=5,
        )
        self.button_combo.pack(side="left")
        ttk.Label(self.click_point_row, text="次数").pack(side="left", padx=pad(8, 0))
        ttk.Entry(self.click_point_row, textvariable=self.click_count, width=4).pack(side="left")
        self.click_point_index = labeled(
            "点击坐标", self.click_point_row,
            "点击位置按 1920×1080 录制坐标保存，运行时自动缩放到当前分辨率。",
        )

        self.timeout_behavior_row = ttk.Frame(body)
        self.timeout_combo = ttk.Combobox(
            self.timeout_behavior_row, textvariable=self.on_timeout,
            values=[label for label, _key in TIMEOUT_OPTIONS], state="readonly",
        )
        self.timeout_combo.bind("<<ComboboxSelected>>", self._refresh_visibility)
        self.timeout_combo.pack(side="left")
        self.timeout_behavior_index = labeled("超时后", self.timeout_behavior_row)

        self.jump_row = ttk.Frame(body)
        self.jump_combo = ttk.Combobox(
            self.jump_row, textvariable=self.jump_target,
            values=self.jump_target_labels, state="readonly",
        )
        self.jump_combo.pack(side="left", fill="x", expand=True)
        self.jump_row_index = labeled("跳转目标", self.jump_row)

        notice_row = ttk.Frame(body)
        self.notice_check = dark_checkbutton(notice_row, "命中后显示浮动提醒", self.notice)
        self.notice_check.pack(side="left")
        labeled("提醒", notice_row)

        delay_row = ttk.Frame(body)
        ttk.Entry(delay_row, textvariable=self.delay, width=8).pack(side="left")
        labeled("执行前延时", delay_row)
        after_row = ttk.Frame(body)
        ttk.Entry(after_row, textvariable=self.after_delay, width=8).pack(side="left")
        labeled("成功后延时", after_row)

        buttons = ttk.Frame(body)
        buttons.grid(row=next_row(), column=0, columnspan=2, sticky="e", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        self._refresh_visibility()

    def _pick_click_point(self):
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_click_point,
            tip_text="点击页面命中后要点击的位置；只记录坐标；Esc 取消",
            hidden_windows=app_windows(self.master),
        )
        self.picker.start()

    def _apply_picked_click_point(self, x, y):
        self.click_point.set(f"{int(x)},{int(y)}")
        self.click_enabled.set(True)
        self._refresh_visibility()

    def _read_current_ui(self):
        """只读探针：抓一次当前战斗界面可见状态，填进下拉。"""
        from macroflow.execution.memory_plugin import plugin
        try:
            snapshot = plugin.read_battle_uis()
        except Exception as exc:
            show_floating_notice(
                self, "读不到战斗界面", f"需要先进入对局再读取：{exc}",
            )
            return
        visible = [name for name, flag in snapshot.items() if flag]
        keys = sorted(set(_UI_KEY_CACHE) | set(snapshot))
        _UI_KEY_CACHE[:] = keys
        self.ui_key_combo.configure(values=keys)
        if visible and not self.ui_key.get().strip():
            self.ui_key.set(visible[0])
        text = "当前可见：" + ("、".join(visible) if visible else "（无）")
        text += f"　共 {len(snapshot)} 个界面，下拉里可选全部"
        self._probe_text = text
        self._probe_label.configure(text=text)
        self._refresh_visibility()

    def _source_key(self) -> str:
        return dict(SOURCE_OPTIONS).get(self.source.get(), "battle_ui")

    def _mode_key(self) -> str:
        return dict(MODE_OPTIONS).get(self.mode.get(), "enter")

    def _set_row_visible(self, index: int | None, visible: bool) -> None:
        if index is None:
            return
        for widget in self._row_widgets.get(index) or ():
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def _refresh_visibility(self, _event=None):
        mode = self._mode_key()
        is_check = mode == "check"
        battle = self._source_key() == "battle_ui"
        self.timeout_entry.configure(
            state="disabled" if self.wait_forever.get() or is_check else "normal",
        )
        self.wait_forever_check.configure(state="disabled" if is_check else "normal")
        click_state = "normal" if self.click_enabled.get() else "disabled"
        for widget in (self.click_point_entry, self.click_point_button):
            widget.configure(state=click_state)
        self.button_combo.configure(
            state="readonly" if self.click_enabled.get() else "disabled",
        )
        # 立即判断没有“超时”，但判断失败后仍可跳转，所以行为下拉保持可用。
        self.timeout_combo.configure(state="disabled" if self.wait_forever.get() else "normal")
        jump_enabled = (
            not self.wait_forever.get()
            and dict(TIMEOUT_OPTIONS).get(self.on_timeout.get()) == "jump"
        )
        self.jump_combo.configure(state="readonly" if jump_enabled else "disabled")
        self._set_row_visible(self.battle_row, battle)
        self._set_row_visible(self.probe_row, battle and bool(self._probe_text))
        self._set_row_visible(self.page_row, not battle)
        self._set_row_visible(
            self.empty_row, (not battle) and not self.page_names,
        )
        self._set_row_visible(self.threshold_row, not battle)
        self._set_row_visible(self.timeout_row, not is_check)
        self._set_row_visible(self.interval_row, not is_check)

    def save(self):
        try:
            source = self._source_key()
            mode = self._mode_key()
            timeout_ms = 0 if self.wait_forever.get() or mode == "check" \
                else max(0, int(self.timeout.get()))
            interval_ms = max(1000, int(self.interval.get()))
            click = bool(self.click_enabled.get())
            click_point = []
            if click:
                parts = [part.strip() for part in self.click_point.get().split(",")]
                if len(parts) != 2 or not all(part.lstrip("-").isdigit() for part in parts):
                    raise ValueError("点击坐标必须是 x,y 两个整数")
                click_point = [int(parts[0]), int(parts[1])]
            click_count = max(1, int(self.click_count.get()))
            behavior = dict(TIMEOUT_OPTIONS).get(self.on_timeout.get(), "continue")
            target_id = ""
            if behavior == "jump":
                target_id = self.jump_target_ids.get(self.jump_target.get(), "")
                if not target_id:
                    raise ValueError("请选择超时后的跳转目标")
            action = dict(self.data)
            action.update({
                "type": "page",
                "source": source,
                "mode": mode,
                "timeout_ms": timeout_ms,
                "interval_ms": interval_ms,
                "click": click,
                "click_point": click_point,
                "button": {"左键": "left", "右键": "right", "中键": "middle"}.get(
                    self.button.get(), "left"),
                "click_count": click_count,
                "on_timeout": behavior,
                "timeout_jump_action_id": target_id,
                "show_result_notice": bool(self.notice.get()),
                "delay_ms": max(0, int(self.delay.get())),
                "after_delay_ms": max(0, int(self.after_delay.get())),
            })
            if source == "battle_ui":
                key = self.ui_key.get().strip()
                if not key:
                    raise ValueError("请点“读取当前”选择战斗界面，或直接输入界面名")
                action["ui_key"] = key
                action.pop("page", None)
                action.pop("threshold", None)
            else:
                name = self.page_name.get().strip()
                if not name:
                    raise ValueError(f"请先选择页面；页面截图应放在 {PAGE_LIBRARY_DIR}")
                if name not in self.page_names:
                    raise ValueError(f"页面库中没有「{name}」")
                threshold = float(self.threshold.get())
                if not 0.1 <= threshold <= 1.0:
                    raise ValueError("相似度阈值必须在 0.1 到 1.0 之间")
                action["page"] = name
                action["threshold"] = threshold
                action.pop("ui_key", None)
            action.pop("timeout_jump_row", None)
            self.result = action
        except ValueError as exc:
            show_floating_notice(self, "页面动作设置无效", str(exc))
            return
        self.destroy()