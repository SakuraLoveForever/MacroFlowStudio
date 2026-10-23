from __future__ import annotations

from macroflow.ui.dialogs.process_names import ProcessLibraryDialog
from macroflow.ui.dialogs.app_dialogs import CoordinateFlashDialog
from macroflow.ui.dialogs.base import TIME_UNITS, Tooltip
import sys
from macroflow.core.storage import resolve_path
import tkinter as tk
import ttkbootstrap as ttk
from macroflow.ui.form_controls import bind_vertical_mousewheel

from .base import apply_pointer_cursors, attach_autohide_scrollbar, bind_tree_hover, pad, px
from .constants import (
    ACTION_TREE_COLUMNS, APP_VERSION, COLOR_BG, COLOR_BLUE, COLOR_BLUE_FILL, COLOR_BORDER,
    COLOR_RED, COLOR_SIDEBAR, COLOR_SURFACE, COLOR_SURFACE_ALT,
    COLOR_TEXT, FLOATING_NOTICE_POSITIONS, FONT_BODY, FONT_FAMILY, GLOBAL_TREE_COLUMNS,
    PAGE_PAD_X, PAGE_PAD_Y, RAIL_WIDTH, RECORD_TOOLBAR_BUTTON_LABEL, SCRIPT_CATEGORY_VALUES,
    WORKFLOW_TREE_COLUMNS,
)
from .design import (
    Card, FlowFrame, NavItem, PageStack, SegmentedControl, checkbutton, radiobutton,
)

from .action_palette import action_category_specs


def fit_sidebar_scrollregion(canvas):
    bounds = canvas.bbox("all")
    if bounds is None:
        return
    width, height = canvas.winfo_width(), canvas.winfo_height()
    canvas.configure(scrollregion=(0, 0, max(width, bounds[2]), max(height, bounds[3])))
    if bounds[3] <= height:
        canvas.yview_moveto(0)


def _bind_canvas_wheel(canvas, widget):
    """Route mouse wheel scrolling inside a canvas page to the canvas."""
    def bind(inner):
        bind_vertical_mousewheel(inner, canvas)
        for child in inner.winfo_children():
            bind(child)

    bind_vertical_mousewheel(canvas, canvas)
    bind(widget)


def _draw_logo(canvas, size):
    """Draw the MacroFlow mark: a rounded tile with a play glyph."""
    canvas.delete("all")
    radius = px(9)
    fill = COLOR_BLUE_FILL
    points = (radius, 0, size - radius, 0, size, 0, size, radius,
              size, size - radius, size, size, size - radius, size,
              radius, size, 0, size, 0, size - radius, 0, radius, 0, 0)
    canvas.create_polygon(points, smooth=True, splinesteps=18, fill=fill, outline=fill)
    x0 = size * 0.36
    canvas.create_polygon(
        x0, size * 0.30, x0, size * 0.70, size * 0.72, size * 0.50,
        fill="#FFFFFF", outline="#FFFFFF",
    )


class SidebarButton(tk.Canvas):
    """Compact rounded control with mouse and keyboard activation."""

    def __init__(self, parent, *, text, command, state="normal", bootstyle="", style="",
                 width=None, surface=COLOR_SIDEBAR):
        self._text, self._command, self._state = text, command, state
        self._tone = bootstyle
        self._surface = surface
        self._pressed = False
        super().__init__(
            parent, width=px(width * 10 + 16 if width else len(text) * 10 + 20),
            height=px(32), background=surface, borderwidth=0, autostyle=False,
            highlightthickness=0, takefocus=state != "disabled",
            cursor="arrow" if state == "disabled" else "hand2",
        )
        self.bind("<Configure>", self._draw)
        self.bind("<FocusIn>", self._draw)
        self.bind("<FocusOut>", self._draw)
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<space>", lambda _event: self.invoke())
        self.bind("<Return>", lambda _event: self.invoke())

    def _press(self, _event):
        if self._state != "disabled":
            self._pressed = True
            self.focus_set()
            self._draw()

    def _release(self, event):
        pressed, self._pressed = self._pressed, False
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()
        self._draw()

    def invoke(self):
        if self._state != "disabled":
            return self._command()

    def configure(self, cnf=None, **kwargs):
        options = dict(cnf or {}, **kwargs)
        for option, attribute in (("text", "_text"), ("command", "_command"),
                                  ("state", "_state"), ("bootstyle", "_tone")):
            if option in options:
                setattr(self, attribute, options.pop(option))
        options.pop("style", None)
        options.update(takefocus=self._state != "disabled",
                       cursor="arrow" if self._state == "disabled" else "hand2")
        if options:
            super().configure(**options)
        self._draw()

    config = configure

    def cget(self, key):
        attributes = {"text": "_text", "command": "_command", "state": "_state", "bootstyle": "_tone"}
        return getattr(self, attributes[key]) if key in attributes else super().cget(key)

    __getitem__ = cget

    def _draw(self, _event=None):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        radius = px(7)
        disabled = self._state == "disabled"
        tone_fill = {
            "danger": COLOR_RED, "success": "#1A7F37", "primary": COLOR_BLUE_FILL,
        }
        if disabled:
            fill, foreground, outline = "#1B242E", "#5A6673", "#1B242E"
        elif self._tone in tone_fill:
            fill, foreground, outline = tone_fill[self._tone], "#FFFFFF", tone_fill[self._tone]
        else:
            fill, foreground, outline = COLOR_SURFACE_ALT, COLOR_TEXT, COLOR_BORDER
        points = (1 + radius, 1, width - radius - 1, 1, width - 1, 1,
                  width - 1, radius + 1, width - 1, height - radius - 1,
                  width - 1, height - 1, width - radius - 1, height - 1,
                  radius + 1, height - 1, 1, height - 1, 1, height - radius - 1,
                  1, radius + 1, 1, 1)
        self.create_polygon(points, smooth=True, splinesteps=16, fill=fill,
                            outline=COLOR_BLUE if self.focus_get() == self else outline,
                            width=px(2 if self.focus_get() == self else 1))
        self.create_text(width / 2, height / 2, text=self._text, fill=foreground,
                         font=(FONT_FAMILY, FONT_BODY, "bold"))


class ShellMixin:
    """Studio authoring behavior."""

    def _restore_execution_settings(self):
        self.switch_english_input_enabled = bool(
            self.app_settings.get("switch_english_input_enabled", False)
        )

    def _configure_client_startup(self):
        self._restore_execution_settings()
        self.startup_new_script = "--new-script" in sys.argv
        self.startup_open_script = None
        if "--open-script" in sys.argv:
            try:
                self.startup_open_script = sys.argv[sys.argv.index("--open-script") + 1]
            except IndexError:
                pass
        self.startup_edit_module = None
        if "--edit-module" in sys.argv:
            try:
                self.startup_edit_module = sys.argv[sys.argv.index("--edit-module") + 1]
            except IndexError:
                pass

    def _schedule_client_startup(self, explicit_editor_start):
        if self.startup_open_script:
            self.root.after(300, lambda: self._load_startup_script(resolve_path(self.startup_open_script)))
        if self.startup_edit_module:
            self.root.after(300, lambda: self._open_module_object_editor(self.startup_edit_module))
        if not explicit_editor_start:
            # 恢复上次关闭时脚本编辑页的完整状态（含未保存动作）。
            self.root.after(400, self._restore_last_editor_state)

    # ------------------------------------------------------------- main shell
    def _build_ui(self):
        root_frame = ttk.Frame(self.root, style="App.TFrame")
        self.root_frame = root_frame
        root_frame.pack(fill="both", expand=True)

        status = ttk.Frame(root_frame, style="Status.TFrame", padding=pad(18, 5, 18, 5))
        status.pack(side="bottom", fill="x")
        ttk.Separator(root_frame, orient="horizontal").pack(side="bottom", fill="x")
        self.status_dot = ttk.Label(status, text="●", style="StatusDot.TLabel")
        self.status_dot.pack(side="left")
        ttk.Label(status, textvariable=self.status_var, style="StatusText.TLabel").pack(
            side="left", padx=pad(7, 0))
        ttk.Label(status, textvariable=self.coordinate_scale_var, style="StatusCaption.TLabel").pack(
            side="right")

        body = ttk.Frame(root_frame, style="App.TFrame")
        body.pack(fill="both", expand=True)
        self._build_rail(body)

        main = ttk.Frame(body, style="App.TFrame",
                         padding=pad(PAGE_PAD_X, PAGE_PAD_Y, PAGE_PAD_X, 10))
        main.pack(side="left", fill="both", expand=True)
        self.notebook = PageStack(main, on_page_change=self._on_page_changed)
        self.notebook.pack(fill="both", expand=True)
        self.script_tab = self.notebook.add_page("脚本编辑")
        self.workflow_tab = self.notebook.add_page("工作流")
        self.log_tab = self.notebook.add_page("运行日志")
        self.plugins_tab = self.notebook.add_page("插件")
        self.settings_tab = self.notebook.add_page("设置")
        self._build_script_tab()
        self._build_workflow_tab()
        self._build_log_tab()
        from .plugins_page import build_plugins_page
        build_plugins_page(self.plugins_tab, self)
        self._build_settings_page(self.settings_tab)
        self.notebook.select(self.script_tab)
        apply_pointer_cursors(root_frame)

    def _on_page_changed(self, page):
        for frame, key in (
            (getattr(self, "script_tab", None), "script"),
            (getattr(self, "workflow_tab", None), "workflow"),
            (getattr(self, "log_tab", None), "log"),
            (getattr(self, "plugins_tab", None), "plugins"),
            (getattr(self, "settings_tab", None), "settings"),
        ):
            item = getattr(self, "_rail_items", {}).get(key)
            if item is not None:
                item.set_active(frame is page)

    def _show_page(self, key: str):
        page = {
            "script": getattr(self, "script_tab", None),
            "workflow": getattr(self, "workflow_tab", None),
            "log": getattr(self, "log_tab", None),
            "plugins": getattr(self, "plugins_tab", None),
            "settings": getattr(self, "settings_tab", None),
        }.get(key)
        if page is not None:
            self.notebook.select(page)

    def _rebuild_ui_for_display_change(self):
        """Recreate widgets whose pixel sizes were fixed when they were built."""
        old_frame = getattr(self, "root_frame", None)
        if old_frame is None:
            return
        try:
            page_index = self.notebook.index(self.notebook.current())
        except (AttributeError, tk.TclError, ValueError):
            page_index = 0
        log_view = self._active_log_view()
        global_buffers = getattr(self, "_global_view_buffers", {"workflow": [], "script": []})
        tree_views = {}
        for name in ("action_tree", "workflow_tree", "global_tree"):
            tree = getattr(self, name, None)
            if tree is not None:
                try:
                    tree_views[name] = (tuple(tree.selection()), float(tree.yview()[0]))
                except (tk.TclError, TypeError, IndexError, ValueError):
                    pass
        for view in ("event", "trace"):
            job = getattr(self, f"_{view}_view_job", None)
            if job is not None:
                self.root.after_cancel(job)
            self._flush_log_view(view)
        event_buffer = self._event_view_buffer
        trace_buffer = self._trace_view_buffer
        old_frame.destroy()
        self._build_ui()
        self._event_view_buffer = event_buffer
        self._trace_view_buffer = trace_buffer
        self._global_view_buffers = global_buffers
        self.rebuild_action_tree()
        self.rebuild_workflow_tree()
        for name, (selection, top) in tree_views.items():
            tree = getattr(self, name)
            selected = tuple(row for row in selection if tree.exists(row))
            if selected:
                tree.selection_set(selected)
            tree.yview_moveto(top)
        self._sync_activation_ui_from_script()
        self._refresh_hotkey_summary()
        self.refresh_script_files()
        self.notebook.select(page_index)
        self._show_log_view(log_view)

    # ------------------------------------------------------------------ rail
    def _build_rail(self, parent):
        rail = ttk.Frame(parent, style="Rail.TFrame", width=px(RAIL_WIDTH))
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        inner = ttk.Frame(rail, style="Rail.TFrame", padding=pad(12, 16, 12, 12))
        inner.pack(fill="both", expand=True)

        brand = ttk.Frame(inner, style="Rail.TFrame")
        brand.pack(fill="x")
        mark = tk.Canvas(brand, width=px(30), height=px(30), background=COLOR_SIDEBAR,
                         borderwidth=0, highlightthickness=0, autostyle=False)
        mark.pack(side="left")
        mark.bind("<Configure>", lambda event: _draw_logo(mark, min(event.width, event.height)))
        brand_text = ttk.Frame(brand, style="Rail.TFrame")
        brand_text.pack(side="left", padx=pad(9, 0))
        ttk.Label(brand_text, text="MacroFlow", style="RailBrand.TLabel").pack(anchor="w")
        ttk.Label(brand_text, text="自动化工作台", style="RailCaption.TLabel").pack(anchor="w")

        nav = ttk.Frame(inner, style="Rail.TFrame")
        nav.pack(fill="x", pady=pad(16, 10))
        self._rail_items = {}
        for key, text, icon in (
            ("script", "脚本编辑", "script"),
            ("workflow", "工作流", "workflow"),
            ("log", "运行日志", "log"),
            ("plugins", "插件", "plugins"),
        ):
            item = NavItem(nav, text=text, icon=icon, command=lambda key=key: self._show_page(key))
            item.pack(fill="x", pady=px(1))
            self._rail_items[key] = item

        quick = ttk.Frame(inner, style="Card.TFrame", padding=pad(10, 10, 10, 11))
        quick.pack(fill="x", pady=pad(4, 0))
        ttk.Label(quick, text="快速操作", style="CardHint.TLabel").pack(anchor="w", pady=pad(0, 7))
        self.record_button = SidebarButton(
            quick, text="开始录制    F8", command=lambda: self.toggle_record(from_ui=True),
            bootstyle="danger", surface=COLOR_SURFACE,
        )
        self.record_button.pack(fill="x")
        self.run_button = SidebarButton(
            quick, text="执行脚本    F9", command=self.run_current_script,
            bootstyle="success", surface=COLOR_SURFACE,
        )
        self.run_button.pack(fill="x", pady=pad(6, 0))
        secondary = ttk.Frame(quick, style="Card.TFrame")
        secondary.pack(fill="x", pady=pad(6, 0))
        secondary.columnconfigure((0, 1), weight=1, uniform="rail_secondary")
        self.script_pause_button = SidebarButton(
            secondary, text="暂停", command=self.toggle_execution_pause, state="disabled",
            surface=COLOR_SURFACE,
        )
        self.script_pause_button.grid(row=0, column=0, sticky="ew", padx=pad(0, 3))
        SidebarButton(secondary, text="停止 F12", command=self.stop_all,
                      surface=COLOR_SURFACE).grid(row=0, column=1, sticky="ew", padx=pad(3, 0))
        ttk.Label(quick, text="F8 录制 / 暂停 · F9 执行 · F12 停止",
                  style="CardHint.TLabel", wraplength=px(164)).pack(anchor="w", pady=pad(8, 0))

        ttk.Separator(quick, orient="horizontal").pack(fill="x", pady=pad(8, 6))
        ttk.Label(quick, text="目标窗口", style="CardHint.TLabel").pack(anchor="w")
        self.bind_label_widget = ttk.Label(
            quick, textvariable=self.bind_label_var, wraplength=px(158),
            style="CardLabel.TLabel",
        )
        self.bind_label_widget.pack(anchor="w", pady=pad(2, 5))
        target_row = ttk.Frame(quick, style="Card.TFrame")
        target_row.pack(fill="x")
        ttk.Button(target_row, text="选择窗口", command=self.choose_window,
                   style="Primary.TButton").pack(side="left")
        ttk.Button(target_row, text="清除", command=self.unbind_window,
                   style="Ghost.TButton").pack(side="left", padx=pad(6, 0))

        footer = ttk.Frame(inner, style="Rail.TFrame")
        footer.pack(side="bottom", fill="x", pady=pad(8, 0))
        settings_item = NavItem(footer, text="设置", icon="settings",
                                command=lambda: self._show_page("settings"))
        settings_item.pack(fill="x")
        self._rail_items["settings"] = settings_item
        ttk.Label(footer, text=f"v{APP_VERSION} · 本地免安装", style="RailCaption.TLabel").pack(
            anchor="w", padx=px(4), pady=pad(6, 0))

    # -------------------------------------------------------------- settings
    def _build_settings_page(self, parent):
        canvas = tk.Canvas(parent, background=COLOR_BG, highlightthickness=0,
                           borderwidth=0, autostyle=False)
        scrollbar = ttk.Scrollbar(parent, bootstyle="dark", orient="vertical",
                                  command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body = ttk.Frame(canvas, style="App.TFrame")
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def resize_content(event):
            canvas.itemconfigure(window, width=event.width)

        def update_region(_event=None):
            bounds = canvas.bbox("all")
            if bounds is not None:
                canvas.configure(scrollregion=(
                    0, 0, max(canvas.winfo_width(), bounds[2]), bounds[3]))

        canvas.bind("<Configure>", resize_content)
        body.bind("<Configure>", update_region)

        ttk.Label(body, text="设置", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(body, text="录制、目标窗口、执行行为与快捷键脚本集中在这里；运行时常用的"
                             "录制 / 执行仍在左栏。",
                  style="PageSubtitle.TLabel").pack(anchor="w", pady=pad(3, 14))

        grid = ttk.Frame(body, style="App.TFrame")
        grid.pack(fill="both", expand=True)
        grid.columnconfigure((0, 1), weight=1, uniform="settings")

        # ---------------------------------------------------------- recording
        record_card = Card(grid, title="录制")
        record_card.grid(row=0, column=0, sticky="nsew", padx=pad(0, 6), pady=pad(0, 10))
        self.sidebar_save_button = ttk.Button(
            record_card.header, text="保存配置", command=self.save_sidebar_config,
            style="Ghost.TButton", state="disabled",
        )
        self.sidebar_save_button.pack(side="right")
        ttk.Button(record_card.header, text="测试声音", command=self.test_sound,
                   style="Ghost.TButton").pack(side="right", padx=pad(6, 0))
        record_body = record_card.body
        for text, variable in (
            ("快捷键提示音", self.sound_enabled_var),
            ("录制时显示悬浮小窗", self.mini_window_enabled_var),
            ("执行时显示悬浮小窗", self.execution_mini_enabled_var),
        ):
            checkbutton(record_body, text, variable, self._settings_changed,
                        background=COLOR_SURFACE).pack(anchor="w", pady=px(1))
        ttk.Button(record_body, text="调节小窗位置",
                   command=self._adjust_execution_mini_position,
                   style="Ghost.TButton").pack(anchor="w", pady=pad(3, 8))
        ttk.Label(record_body, text="点击关闭按钮时", style="CardHint.TLabel").pack(anchor="w")
        close_row = ttk.Frame(record_body, style="Card.TFrame")
        close_row.pack(fill="x", pady=pad(3, 0))
        for value, label in (("exit", "直接退出"), ("tray", "隐藏到托盘")):
            radiobutton(close_row, label, value, self.close_action_var,
                        self._settings_changed, background=COLOR_SURFACE).pack(
                side="left", padx=pad(0, 12))
        ttk.Label(
            record_body,
            text="“直接退出”＝关掉窗口就结束进程；“隐藏到托盘”＝窗口收起后快捷键继续生效，"
                 "右键托盘图标选“退出”才结束。",
            wraplength=px(330), style="CardHint.TLabel",
        ).pack(anchor="w", pady=pad(4, 6))
        smart_label = ttk.Label(record_body, text="智能录制", style="CardLabel.TLabel")
        smart_label.pack(anchor="w")
        self._attach_tip(
            smart_label,
            "桌面自动记录坐标；绑定游戏窗口，或在游戏中按 F8，可自动记录锁中心的视角转向。",
        )

        # ------------------------------------------------------- target window
        target_card = Card(grid, title="目标窗口", hint="坐标按窗口记录与回放")
        target_card.grid(row=0, column=1, sticky="nsew", padx=pad(6, 0), pady=pad(0, 10))
        target_body = target_card.body
        ttk.Button(target_card.header, text="闪烁定位",
                   command=lambda: CoordinateFlashDialog(self.root).show(),
                   style="Ghost.TButton").pack(side="right")
        self.bind_label_widget = ttk.Label(
            target_body, textvariable=self.bind_label_var, wraplength=px(320),
            style="CardLabel.TLabel",
        )
        self.bind_label_widget.pack(anchor="w", pady=pad(0, 6))
        bind_row = ttk.Frame(target_body, style="Card.TFrame")
        bind_row.pack(fill="x", pady=pad(0, 8))
        ttk.Button(bind_row, text="选择窗口", command=self.choose_window,
                   style="Primary.TButton").pack(side="left")
        ttk.Button(bind_row, text="清除绑定", command=self.unbind_window,
                   style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        coordinate_row = ttk.Frame(target_body, style="Card.TFrame")
        coordinate_row.pack(fill="x", pady=pad(0, 8))
        self.cursor_position_button = ttk.Button(
            coordinate_row, text="开始实时读取", command=self.toggle_cursor_tracking,
            style="Ghost.TButton",
        )
        self.cursor_position_button.pack(side="left")
        ttk.Label(coordinate_row, textvariable=self.cursor_position_var,
                  style="CardHint.TLabel").pack(side="left", padx=pad(8, 0))
        self._attach_tip(
            self.bind_label_widget,
            "绑定后，坐标会按目标窗口记录和回放；游戏相对转向保持原始视角幅度，不参与分辨率缩放。",
        )
        self.switch_english_input_var = tk.BooleanVar(
            master=self.root,
            value=bool(getattr(self, "switch_english_input_enabled", False)),
        )
        for text, variable, command in (
            ("执行前切换英文输入法", self.switch_english_input_var, self._toggle_english_input),
            ("强制专注模式", self.focus_mode_enabled_var, self._settings_changed),
            ("执行时前置目标", self.activate_target_enabled_var, self._toggle_target_activation),
        ):
            checkbutton(target_body, text, variable, command,
                        background=COLOR_SURFACE).pack(anchor="w", pady=px(1))
        self._attach_tip(
            target_body.winfo_children()[-1],
            "勾选后每次执行前激活目标窗口；取消勾选只停止前置，不会清除已保存的目标窗口。",
        )

        # --------------------------------------------------------- execution
        execution_card = Card(grid, title="执行行为")
        execution_card.grid(row=1, column=0, sticky="nsew", padx=pad(0, 6), pady=pad(0, 10))
        execution_body = execution_card.body

        aim_row = ttk.Frame(execution_body, style="Card.TFrame")
        aim_row.pack(fill="x", pady=pad(0, 6))
        ttk.Label(aim_row, text="锁定跟随等待", style="CardLabel.TLabel").pack(side="left")
        self.aim_follow_interval_var = tk.StringVar(
            master=self.root, value=str(self.app_settings.get('ssjj_aim_interval_ms', 2)))
        aim_interval = ttk.Spinbox(aim_row, from_=1, to=1000, width=5,
                                   textvariable=self.aim_follow_interval_var,
                                   command=self.apply_aim_follow_interval)
        aim_interval.pack(side="left", padx=pad(8, 3))
        aim_interval.bind('<Return>', self.apply_aim_follow_interval)
        ttk.Label(aim_row, text="ms", style="CardHint.TLabel").pack(side="left")
        self.aim_apply_button = ttk.Button(
            aim_row, text="应用", width=5, command=self.apply_aim_follow_interval,
            style="Ghost.TButton", state='disabled',
        )
        self.aim_apply_button.pack(side="right")

        repeat_row = ttk.Frame(execution_body, style="Card.TFrame")
        repeat_row.pack(fill="x", pady=pad(0, 6))
        repeat_label = ttk.Label(repeat_row, text="脚本重复次数", style="CardLabel.TLabel")
        repeat_label.pack(side="left")
        self._attach_tip(repeat_label, "执行当前脚本时完整重复的次数。")
        self.repeat_spin = ttk.Spinbox(repeat_row, from_=1, to=999999,
                                       textvariable=self.repeat_var, width=6,
                                       font=(FONT_FAMILY, FONT_BODY))
        self.repeat_spin.pack(side="left", padx=pad(8, 3))
        self.repeat_edit_button = ttk.Button(
            repeat_row, text="修改", width=5, style="Ghost.TButton",
            command=lambda: self._toggle_locked_spinbox(
                self.repeat_spin, self.repeat_edit_button, self._settings_changed),
        )
        self.repeat_edit_button.pack(side="left")
        self.repeat_spin.configure(state="disabled")

        notice_row = ttk.Frame(execution_body, style="Card.TFrame")
        notice_row.pack(fill="x", pady=pad(0, 6))
        notice_label = ttk.Label(notice_row, text="浮动提醒位置", style="CardLabel.TLabel")
        notice_label.pack(side="left")
        self._attach_tip(notice_label, "选择脚本“提醒”动作在屏幕上的显示位置。")
        notice_position_box = ttk.Combobox(
            notice_row, textvariable=self.floating_notice_position_var,
            values=FLOATING_NOTICE_POSITIONS, state="readonly", width=10,
        )
        notice_position_box.pack(side="right")
        notice_position_box.bind("<<ComboboxSelected>>", self._settings_changed)
        notice_position_box.bind("<MouseWheel>", lambda _event: "break")

        ttk.Separator(execution_body, orient="horizontal").pack(fill="x", pady=pad(9, 7))
        ttk.Label(execution_body, text="局部执行时启用", style="CardHint.TLabel").pack(anchor="w")
        for label, variable in (
            ("脚本全局模块", self.partial_script_globals_var),
            ("工作流全局模块", self.partial_workflow_globals_var),
        ):
            checkbutton(execution_body, label, variable, self._settings_changed,
                        background=COLOR_SURFACE).pack(anchor="w")

        activation_check = checkbutton(
            execution_body, "启用执行前置窗口", self.activation_enabled_var,
            self._toggle_activation_enabled, background=COLOR_SURFACE,
        )
        activation_check.pack(anchor="w", pady=pad(8, 2))
        self._attach_tip(activation_check, "可指定另一个窗口先被激活，再执行当前脚本。")
        ttk.Label(execution_body, textvariable=self.activation_label_var,
                  wraplength=px(330), style="CardHint.TLabel").pack(anchor="w", pady=pad(2, 6))
        activation_row = ttk.Frame(execution_body, style="Card.TFrame")
        activation_row.pack(fill="x", pady=pad(0, 6))
        ttk.Button(activation_row, text="选择前置窗口",
                   command=self.choose_activation_window,
                   style="Ghost.TButton").pack(side="left", fill="x", expand=True)
        ttk.Button(activation_row, text="跟随目标", command=self.unbind_activation_window,
                   style="Ghost.TButton").pack(side="left", padx=pad(8, 0))
        interval_row = ttk.Frame(execution_body, style="Card.TFrame")
        interval_row.pack(fill="x", pady=pad(0, 4))
        ttk.Label(interval_row, text="两次前置间隔", style="CardHint.TLabel").pack(side="left")
        interval_entry = ttk.Entry(interval_row, textvariable=self.activation_interval_var,
                                   width=4)
        interval_entry.pack(side="left", padx=pad(8, 4))
        interval_entry.bind("<FocusOut>", self._save_activation_interval)
        interval_entry.bind("<Return>", self._save_activation_interval)
        ttk.Label(interval_row, text="秒", style="CardHint.TLabel").pack(side="left")

        # --------------------------------------------------- shortcuts & help
        help_card = Card(grid, title="快捷键与样式")
        help_card.grid(row=1, column=1, sticky="nsew", padx=pad(6, 0), pady=pad(0, 10))
        help_body = help_card.body

        hotkey_title = ttk.Frame(help_body, style="Card.TFrame")
        hotkey_title.pack(fill="x", pady=pad(0, 4))
        hotkey_label = ttk.Label(hotkey_title, text="快捷键脚本", style="CardLabel.TLabel")
        hotkey_label.pack(side="left")
        self._attach_tip(
            hotkey_label,
            "把脚本绑定到快捷键：录制或执行脚本的过程中，按下快捷键立即执行该脚本，"
            "例如游戏中按 J 执行“转向左 90°”。",
        )
        ttk.Button(hotkey_title, text="设置…", width=7, style="Ghost.TButton",
                   command=self._configure_hotkey_scripts).pack(side="right")
        ttk.Label(help_body, textvariable=self.hotkey_summary_var, style="CardHint.TLabel",
                  wraplength=px(320)).pack(anchor="w", pady=pad(0, 6))

        resolution_title = ttk.Frame(help_body, style="Card.TFrame")
        resolution_title.pack(fill="x", pady=pad(8, 4))
        resolution_label = ttk.Label(resolution_title, text="分辨率样式", style="CardLabel.TLabel")
        resolution_label.pack(side="left")
        self._attach_tip(
            resolution_label,
            "脚本中的“分辨率”动作从这里选择样式；参照窗口留空即改当前软件所在的显示器，"
            "选了窗口则只改该窗口所在的显示器。",
        )
        ttk.Button(resolution_title, text="设置…", width=7, style="Ghost.TButton",
                   command=self._configure_resolution_styles).pack(side="right")

        ttk.Button(help_body, text="游戏设置说明", command=self.open_game_setup_note,
                   style="Ghost.TButton").pack(anchor="w", pady=pad(8, 8))

        ttk.Separator(help_body, orient="horizontal").pack(fill="x", pady=pad(4, 8))
        ttk.Label(help_body, text="全局快捷键", style="CardHint.TLabel").pack(anchor="w")
        for keys, text in (
            ("F8", "空闲时开始 / 停止录制；执行中暂停 / 继续"),
            ("F9", "执行当前脚本"),
            ("F10", "执行当前工作流"),
            ("F12", "紧急停止录制与执行"),
        ):
            row = ttk.Frame(help_body, style="Card.TFrame")
            row.pack(fill="x", pady=px(2))
            ttk.Label(row, text=keys, style="CardValue.TLabel", width=5).pack(side="left")
            ttk.Label(row, text=text, style="CardHint.TLabel").pack(side="left")

        self._watch_settings_saves(body)
        _bind_canvas_wheel(canvas, body)
        bind_vertical_mousewheel(scrollbar, canvas)
        update_region()

    # --------------------------------------------------------- action palette
    @staticmethod
    def _chip_style(style_name: str) -> str:
        return {
            "ScriptTool.TButton": "Chip.TButton",
            "AccentScriptTool.TButton": "ChipAccent.TButton",
            "DangerScriptTool.TButton": "ChipDanger.TButton",
        }.get(style_name, "Chip.TButton")

    def _build_action_palette(self, parent):
        categories = action_category_specs(self._script_action_button_specs())
        labels = [label for label, _primary, _extra in categories]
        self.action_palette = SegmentedControl(
            parent, options=[(label, label) for label in labels],
            on_change=self._palette_category_changed, background=COLOR_SURFACE,
        )
        self.action_palette.pack(fill="x")
        self._palette_holders = {}
        self._palette_flows = {}
        self._palette_buttons = {}
        for label, primary, extra in categories:
            holder = ttk.Frame(parent, style="Card.TFrame")
            flow = FlowFrame(holder, gap=6, row_gap=6, background=COLOR_SURFACE, max_rows=1)
            flow.pack(fill="x", pady=pad(9, 0))
            for text, command, style_name in (*primary, *extra):
                flow.add(ttk.Button(
                    flow, text=text, command=getattr(self, command),
                    style=self._chip_style(style_name),
                ))
            flow.finish()
            self._palette_holders[label] = holder
            self._palette_flows[label] = flow
        self._palette_category_changed(labels[0])

    def _palette_category_changed(self, label):
        holders = getattr(self, "_palette_holders", None)
        if not holders:
            return
        for key, holder in holders.items():
            if key == label:
                holder.pack(fill="x")
            else:
                holder.pack_forget()
        flow = getattr(self, "_palette_flows", {}).get(label)
        if flow is not None:
            self.root.after_idle(flow._relayout)

    # ----------------------------------------------------------------- menus
    def _insert_script_reference(self):
        self._insert_script(False)

    def _insert_script_inline(self):
        self._insert_script(True)

    def _show_script_more_menu(self):
        """「⋯」：独有文件操作、低频管理入口，最后为三个脚本插入选项。"""
        menu = tk.Menu(
            self.root, tearoff=False, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1F4C80", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        menu.add_command(label="✕ 关闭", command=self.close_script)
        menu.add_command(label="⧉ 新开窗口", command=self.open_new_window)
        menu.add_separator()
        menu.add_command(label="导入包…", command=self.import_portable_bundle)
        menu.add_command(label="导出当前脚本…", command=self.export_portable_bundle)
        menu.add_separator()
        menu.add_command(label="📂 打开脚本目录",
                         command=lambda: self.open_folder(self._script_category_dir()))
        menu.add_command(label="🗂 目录设置…", command=self._configure_script_directories)
        menu.add_command(label="▤ 模块管理…", command=self.open_template_region_manager)
        menu.add_separator()
        menu.add_command(label="⇥ 引用脚本（实时读取）", command=self._insert_script_reference)
        menu.add_command(label="⇥ 逐行插入脚本", command=self._insert_script_inline)
        menu.add_command(label="⇥ 插入脚本指定行…", command=self._insert_script_range)
        self._popup_menu(menu, self.script_more_menu_button)

    def _popup_menu(self, menu, anchor):
        """在按钮正下方弹出菜单（键盘也可用：按钮获得焦点后按空格/回车）。"""
        anchor.update_idletasks()
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    @staticmethod
    def _script_action_button_specs():
        """Return the action buttons exposed by the script editor toolbar."""
        return (
            ("⇥ 引用脚本", "_insert_script_reference", "ScriptTool.TButton"),
            ("⇥ 逐行插入脚本", "_insert_script_inline", "ScriptTool.TButton"),
            ("⇥ 插入指定行…", "_insert_script_range", "ScriptTool.TButton"),
            ("◷ 延时", "add_delay", "ScriptTool.TButton"),
            ("◷ 等待小怪消失", "add_wait_mobs_absent", "AccentScriptTool.TButton"),
            ("⌖ 光标锁定/解锁", "add_aim_lock", "AccentScriptTool.TButton"),
            ("◎ 重新绑定", "add_rebind_window", "ScriptTool.TButton"),
            ("▣ 前置窗口", "add_activate_window", "ScriptTool.TButton"),
            ("⌨ 键盘", "add_key", "ScriptTool.TButton"),
            ("⌨ 组合按键", "add_key_sequence", "ScriptTool.TButton"),
            ("Py 逻辑", "add_python_script", "AccentScriptTool.TButton"),
            ("⌖ 游戏坐标", "open_ssjj_coordinates", "AccentScriptTool.TButton"),
            ("T 文本", "add_text", "ScriptTool.TButton"),
            ("i 提醒", "add_notice", "ScriptTool.TButton"),
            ("↖ 移动", "add_mouse_move", "ScriptTool.TButton"),
            ("◉ 点击", "add_click", "ScriptTool.TButton"),
            ("↺ 转向", "add_turn", "ScriptTool.TButton"),
            ("↻ 连点", "add_repeat_click", "ScriptTool.TButton"),
            ("↕ 滚轮", "add_scroll", "ScriptTool.TButton"),
            ("↕ 组合滚轮", "add_scroll_sequence", "ScriptTool.TButton"),
            ("⇄ 数字比较", "add_ocr_compare", "AccentScriptTool.TButton"),
            ("⊞ 多条件识图", "add_multi_condition_click", "AccentScriptTool.TButton"),
            ("▤ 列表逐行点击", "add_row_list_condition_click", "AccentScriptTool.TButton"),
            ("◫ 页面", "add_page", "AccentScriptTool.TButton"),
            ("▶ 软件", "add_open_app", "ScriptTool.TButton"),
            ("✕ 关闭", "add_close_app", "ScriptTool.TButton"),
            ("▣ 分辨率", "add_set_resolution", "AccentScriptTool.TButton"),
            ("▤ 模块", "add_module", "AccentScriptTool.TButton"),
            ("⇢ 跳转", "add_jump", "AccentScriptTool.TButton"),
            ("⏸ 阻塞", "add_block", "AccentScriptTool.TButton"),

            # 录制与左栏「开始录制 F8」同一个开关：脚本编辑时不必再回左栏找。
            (RECORD_TOOLBAR_BUTTON_LABEL, "_toggle_record_from_toolbar", "DangerScriptTool.TButton"),
        )

    # ------------------------------------------------------------ script page
    def _build_script_tab(self):
        outer = ttk.Frame(self.script_tab, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x")
        titles = ttk.Frame(header, style="App.TFrame")
        titles.pack(side="left")
        ttk.Label(titles, text="脚本编辑", style="PageTitle.TLabel").pack(anchor="w")
        ttk.Label(titles, textvariable=self.record_count_var,
                  style="PageSubtitle.TLabel").pack(anchor="w", pady=pad(1, 0))
        actions = ttk.Frame(header, style="App.TFrame")
        actions.pack(side="right")
        ttk.Button(actions, text="新建", command=self.new_script,
                   style="Ghost.TButton").pack(side="left")
        ttk.Button(actions, text="打开", command=self.open_script,
                   style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        self.undo_open_button = ttk.Button(
            actions, text="↩ 撤销打开", command=self.undo_open_script,
            style="Ghost.TButton", state="disabled",
        )
        self.undo_open_button.pack(side="left", padx=pad(6, 0))
        script_save_button = ttk.Button(actions, text="保存", command=self.save_current_script,
                                        style="Primary.TButton", state='disabled')
        script_save_button.pack(side="left", padx=pad(6, 0))
        from macroflow.ui.save_state import SaveState
        self._script_save_state = SaveState(actions, script_save_button,
                                            lambda: bool(self.dirty), baseline=False)
        self.script_more_menu_button = ttk.Button(
            actions, text="⋯", width=3, command=self._show_script_more_menu,
            style="Ghost.TButton",
        )
        self.script_more_menu_button.pack(side="left", padx=pad(6, 0))

        meta_card = Card(outer, padding=(14, 9, 14, 9))
        meta_card.pack(fill="x", pady=pad(10, 0))
        meta_row = ttk.Frame(meta_card.body, style="Card.TFrame")
        meta_row.pack(fill="x")
        ttk.Label(meta_row, text="脚本名称", style="CardHint.TLabel").pack(side="left")
        name_entry = ttk.Entry(meta_row, textvariable=self.script_name_var, width=34)
        name_entry.pack(side="left", padx=pad(9, 14), ipady=px(1), fill="x", expand=True)
        name_entry.bind("<KeyRelease>", lambda _: self._mark_dirty())
        self.global_script_marker = ttk.Label(meta_row, text="", style="PillAccent.TLabel")
        self.global_script_marker.pack(side="left", padx=pad(0, 12))
        ttk.Label(meta_row, text="类别", style="CardHint.TLabel").pack(side="left")
        category_box = ttk.Combobox(
            meta_row, textvariable=self.script_category_var,
            values=SCRIPT_CATEGORY_VALUES, state="readonly", width=10,
        )
        category_box.pack(side="left", padx=pad(8, 0))
        category_box.bind("<<ComboboxSelected>>", self._script_category_changed)

        palette_card = Card(outer, title="添加动作",
                            hint="点击插入；插入位置与编辑操作见下方工具条")
        palette_card.pack(fill="x", pady=pad(10, 0))
        self._build_action_palette(palette_card.body)

        # 全局脚本：触发条件区块 + 语句体标题（类别为"全局"时显示）。
        self.trigger_holder = ttk.Frame(outer, style="App.TFrame")
        self.trigger_holder.pack(fill="x")
        self.trigger_section = ttk.Frame(self.trigger_holder, style="Card.TFrame")
        trigger_row = ttk.Frame(self.trigger_section, style="Card.TFrame")
        trigger_row.pack(fill="x", pady=pad(10, 6), padx=px(12))
        ttk.Label(
            trigger_row, text="◈ 触发条件：", style="GlobalTriggerTitle.TLabel",
        ).pack(side="left")
        self.trigger_summary_var = tk.StringVar(value="")
        self.trigger_summary_label = ttk.Label(
            trigger_row, textvariable=self.trigger_summary_var,
            style="GlobalTriggerSummary.TLabel",
        )
        self.trigger_summary_label.pack(side="left", fill="x", expand=True)
        ttk.Button(
            trigger_row, text="编辑触发条件", command=self._edit_global_trigger,
            style="Ghost.TButton",
        ).pack(side="right")
        self.clear_trigger_button = ttk.Button(
            trigger_row, text="清除", command=self._clear_global_trigger,
            style="DangerScriptTool.TButton",
        )
        ttk.Label(
            self.trigger_section, text="要执行的动作（触发后按顺序执行）：",
            style="GlobalTriggerTitle.TLabel",
        ).pack(fill="x", pady=pad(0, 8), padx=px(12))

        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.pack(fill="x", pady=pad(10, 0))
        self.script_toolbar = toolbar
        add_buttons = ttk.Frame(toolbar, style="App.TFrame")
        add_buttons.pack(side="left")
        ttk.Label(add_buttons, text="插入位置", style="Muted.TLabel").pack(
            side="left", padx=pad(0, 7))
        self.insert_above_button = ttk.Button(
            add_buttons, text="↑ 向上", command=lambda: self._set_insert_position(True),
            bootstyle="secondary",
        )
        self.insert_below_button = ttk.Button(
            add_buttons, text="↓ 向下", command=lambda: self._set_insert_position(False),
            bootstyle="secondary",
        )
        self.insert_above_button.pack(side="left", padx=px(2))
        self.insert_below_button.pack(side="left", padx=px(2))

        edit_buttons = ttk.Frame(toolbar, style="App.TFrame")
        edit_buttons.pack(side="left", padx=pad(12, 0))
        self.undo_button = ttk.Button(
            edit_buttons, text="↶ 撤销", command=lambda: self._undo_redo_action_edit(False),
            style="CompactGhost.TButton", state="disabled",
        )
        self.undo_button.pack(side="left")
        self.redo_button = ttk.Button(
            edit_buttons, text="↷ 重做", command=lambda: self._undo_redo_action_edit(True),
            style="CompactGhost.TButton", state="disabled",
        )
        self.redo_button.pack(side="left", padx=px(3))
        for text, command, style_name in (
            ("✎ 编辑", self.edit_selected_action, "CompactGhost.TButton"),
            ("⧉ 复制", self.copy_selected_actions_down, "CompactGhost.TButton"),
            ("▶ 从此", self.run_script_from_selected_action, "AccentScriptTool.TButton"),
            ("↑ 上移", lambda: self.move_action(-1), "CompactGhost.TButton"),
            ("↓ 下移", lambda: self.move_action(1), "CompactGhost.TButton"),
            ("× 删除", self.delete_actions, "DangerScriptTool.TButton"),
        ):
            button = ttk.Button(edit_buttons, text=text, command=command, style=style_name)
            button.pack(side="left", padx=px(3))
            if text == "✎ 编辑":
                self.edit_action_button = button
        self._set_insert_position(False)

        table_card = Card(outer, padding=(12, 10, 12, 10))
        table_card.pack(fill="both", expand=True, pady=pad(10, 0))
        table = table_card.body

        search = ttk.Frame(table, style="Card.TFrame")
        search.pack(fill="x", pady=pad(0, 8))
        search_left = ttk.Frame(search, style="Card.TFrame")
        search_left.pack(side="left")
        ttk.Label(search_left, text="搜索键鼠", style="CardHint.TLabel").pack(side="left")
        key_search_entry = ttk.Entry(search_left, textvariable=self.key_search_var, width=10)
        key_search_entry.pack(side="left", padx=pad(8, 5))
        key_search_entry.bind("<Return>", lambda _event: self._search_key_actions(1))
        self.input_search_capture_button = ttk.Button(
            search_left, text="检测键鼠…", width=9,
            command=self.start_input_search_capture, style="Ghost.TButton",
        )
        self.input_search_capture_button.pack(side="left", padx=pad(0, 5))
        key_search_state = ttk.Combobox(
            search_left, textvariable=self.key_search_state_var,
            values=("全部", "按下", "抬起", "Press"), state="readonly", width=8,
        )
        key_search_state.pack(side="left")
        key_search_state.bind(
            "<<ComboboxSelected>>", lambda _event: self._search_key_actions(1))
        ttk.Button(search_left, text="上一个", width=7,
                   command=lambda: self._search_key_actions(-1),
                   style="Ghost.TButton").pack(side="left", padx=pad(8, 3))
        ttk.Button(search_left, text="下一个", width=7,
                   command=lambda: self._search_key_actions(1),
                   style="Ghost.TButton").pack(side="left")
        ttk.Button(search_left, text="清除", width=5,
                   command=self._clear_key_search,
                   style="Ghost.TButton").pack(side="left", padx=pad(3, 8))

        search_right = ttk.Frame(table, style="Card.TFrame")
        search_right.pack(fill="x", pady=pad(0, 8))
        ttk.Label(search_right, textvariable=self.key_search_match_var,
                  style="CardHint.TLabel").pack(side="left", padx=pad(0, 8))
        ttk.Button(search_right, text="统一设置", width=8,
                   command=self._set_matching_key_action_delays,
                   style="Ghost.TButton").pack(side="left", padx=pad(0, 8))
        ttk.Entry(search_right, textvariable=self.key_search_delay_var, width=6).pack(side="left")
        ttk.Label(search_right, text="前延时 ms", style="CardHint.TLabel").pack(
            side="left", padx=pad(0, 6))

        action_tree_shell = ttk.Frame(table, style="Card.TFrame")
        action_tree_shell.pack(fill="both", expand=True)
        action_tree_shell.columnconfigure(0, weight=1)
        action_tree_shell.rowconfigure(0, weight=1)
        self.action_tree = ttk.Treeview(
            action_tree_shell,
            columns=("mark", "index", "kind", "detail", "delay"),
            show="headings",
            selectmode="extended",
        )
        self._apply_column_widths(self.action_tree, ACTION_TREE_COLUMNS, "detail")
        self.action_tree.column("kind", minwidth=96)
        scroll = ttk.Scrollbar(action_tree_shell, bootstyle="dark", orient="vertical",
                               command=self.action_tree.yview)
        horizontal_scroll = ttk.Scrollbar(
            action_tree_shell, bootstyle="dark", orient="horizontal",
            command=self.action_tree.xview,
        )
        self.action_tree.configure(xscrollcommand=horizontal_scroll.set)
        attach_autohide_scrollbar(self.action_tree, scroll)
        self.action_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.empty_action_hint = ttk.Label(
            action_tree_shell,
            text="还没有动作\n按 F8 开始录制，或用上方动作按钮逐条添加",
            style="Empty.TLabel", anchor="center", justify="center",
        )
        self.action_tree.bind("<Double-1>", lambda _: self.edit_selected_action())
        self.action_tree.bind("<<TreeviewSelect>>", self._update_action_edit_button, add="+")
        self.action_tree.bind("<<TreeviewSelect>>", self._refresh_action_segment_bar, add="+")
        self.action_tree.bind("<Delete>", lambda _: self.delete_actions())
        self.action_tree.bind("<Control-z>", lambda _: self._undo_redo_action_edit(False))
        self.action_tree.bind("<Control-y>", lambda _: self._undo_redo_action_edit(True))
        self.action_tree.bind("<Control-Shift-z>", lambda _: self._undo_redo_action_edit(True))
        self.action_tree.bind("<Control-a>", self._select_all_actions)
        self.action_tree.bind("<Button-3>", self._show_action_context_menu)
        bind_tree_hover(self.action_tree)

    # ---------------------------------------------------------- workflow page
    def _build_workflow_tab(self):
        outer = ttk.Frame(self.workflow_tab, style="App.TFrame")
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x")
        titles = ttk.Frame(header, style="App.TFrame")
        titles.pack(side="left")
        ttk.Label(titles, text="工作流", style="PageTitle.TLabel").pack(anchor="w", pady=pad(4, 0))
        header_actions = ttk.Frame(header, style="App.TFrame")
        header_actions.pack(side="right")
        ttk.Button(header_actions, text="新建", command=self.new_workflow,
                   style="Ghost.TButton").pack(side="left")
        ttk.Button(header_actions, text="打开", command=self.open_workflow,
                   style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        workflow_save_button = ttk.Button(
            header_actions, text="保存", command=self.save_current_workflow,
            style="Primary.TButton", state='disabled',
        )
        workflow_save_button.pack(side="left", padx=pad(6, 0))
        from macroflow.core.models import Workflow
        from macroflow.core.storage import load_workflow
        from macroflow.ui.save_state import SaveState
        saved_workflow = Workflow()
        if self.workflow_path and self.workflow_path.is_file():
            try:
                saved_workflow = load_workflow(self.workflow_path)
            except (OSError, ValueError):
                pass
        self._workflow_save_state = SaveState(header_actions, workflow_save_button,
            self._workflow_save_values, baseline=vars(saved_workflow))
        self._workflow_save_state.watch(self.workflow_name_var, self.workflow_start_var,
            self.workflow_start_delay_enabled_var, self.workflow_start_delay_seconds_var)
        self.workflow_pause_button = ttk.Button(
            header_actions, text="暂停", command=self.toggle_execution_pause,
            style="Ghost.TButton", state="disabled",
        )
        self.workflow_pause_button.pack(side="left", padx=pad(6, 0))
        # 执行辅助项与主操作同一行，省下整行高度留给步骤表。
        workflow_action_bar = ttk.Frame(header_actions, style="App.TFrame")
        workflow_action_bar.pack(side="left", padx=pad(6, 0))
        ttk.Checkbutton(
            workflow_action_bar, text="测试模式", variable=self.workflow_test_mode_var,
            bootstyle="round-toggle",
        ).pack(side="left")
        ttk.Button(workflow_action_bar, text="从选中行运行",
                   command=self.run_workflow_from_selected,
                   style="Ghost.TButton").pack(side="left", padx=pad(8, 0))
        ttk.Button(
            header_actions, text="运行工作流    F10", command=self.run_workflow,
            style="Primary.TButton",
        ).pack(side="left", padx=pad(6, 0))
        workflow_more_menu = tk.Menu(self.root, tearoff=False)
        workflow_more_menu.add_command(label="导入包…", command=self.import_portable_bundle)
        workflow_more_menu.add_command(
            label="导出当前工作流…", command=lambda: self.export_portable_bundle("workflow"))
        workflow_more_menu.add_separator()
        workflow_more_menu.add_command(
            label="防遮挡进程…", command=self.edit_foreground_minimize_processes)

        def _show_workflow_more_menu():
            workflow_more_menu.tk_popup(
                workflow_more_button.winfo_rootx(),
                workflow_more_button.winfo_rooty() + workflow_more_button.winfo_height(),
            )
            workflow_more_menu.grab_release()

        workflow_more_button = ttk.Button(
            header_actions, text="⋯", width=3, style="Ghost.TButton",
            command=_show_workflow_more_menu,
        )
        workflow_more_button.pack(side="left")

        meta_card = Card(outer, padding=(14, 9, 14, 9))
        meta_card.pack(fill="x", pady=pad(10, 0))
        workflow_meta_bar = ttk.Frame(meta_card.body, style="Card.TFrame")
        workflow_meta_bar.pack(fill="x")
        ttk.Label(workflow_meta_bar, text="名称", style="CardHint.TLabel").pack(side="left")
        workflow_name_entry = ttk.Entry(workflow_meta_bar, textvariable=self.workflow_name_var,
                                        width=18)
        workflow_name_entry.pack(side="left", padx=pad(8, 6), ipady=px(1))
        workflow_name_entry.bind("<KeyRelease>", self._schedule_workflow_draft_save)
        ttk.Button(workflow_meta_bar, text="重命名", command=self.rename_workflow,
                   style="Ghost.TButton").pack(side="left")
        ttk.Button(workflow_meta_bar, text="复制工作流", command=self.duplicate_workflow,
                   style="Ghost.TButton").pack(side="left", padx=pad(6, 10))

        start_label = ttk.Frame(workflow_meta_bar, style="Card.TFrame")
        start_label.pack(side="right")
        ttk.Button(start_label, text="📅 选择", command=self.choose_workflow_start,
                   style="Ghost.TButton").pack(side="right")
        ttk.Entry(start_label, textvariable=self.workflow_start_var, width=18,
                  state="readonly").pack(side="right", padx=pad(6, 4))
        start_label_text = ttk.Label(start_label, text="开始时间", style="CardHint.TLabel")
        start_label_text.pack(side="right")
        self._attach_tip(
            start_label_text,
            "留空表示手动运行；设置后到达指定时间自动开始当前工作流。",
        )

        # 运行参数与文件信息同卡，避免在工作流页再占一张整卡的高度。
        run_settings = ttk.Frame(meta_card.body, style="Card.TFrame")
        run_settings.pack(fill="x", pady=pad(8, 0))
        run_settings.columnconfigure((0, 1), weight=1, uniform="run")

        start_delay_bar = ttk.Frame(run_settings, style="Card.TFrame")
        start_delay_bar.grid(row=0, column=0, sticky="w", pady=pad(0, 6))
        start_delay_label = ttk.Label(start_delay_bar, text="启动延时", style="CardLabel.TLabel")
        start_delay_label.pack(side="left")
        self._attach_tip(
            start_delay_label,
            "启用后，从头运行或从选中行运行时都会先等待指定时长再开始。",
        )
        ttk.Checkbutton(
            start_delay_bar, text="启用", variable=self.workflow_start_delay_enabled_var,
            command=self._toggle_workflow_start_delay_control, bootstyle="round-toggle",
        ).pack(side="left", padx=pad(9, 4))
        self.workflow_start_delay_entry = ttk.Entry(
            start_delay_bar, textvariable=self.workflow_start_delay_seconds_var, width=8,
        )
        self.workflow_start_delay_entry.pack(side="left", padx=pad(6, 3))
        self.workflow_start_delay_entry.bind("<KeyRelease>", self._schedule_workflow_draft_save)
        ttk.Combobox(
            start_delay_bar, textvariable=self.workflow_start_delay_seconds_var.unit,
            values=TIME_UNITS, state="readonly", width=4,
        ).pack(side="left", padx=pad(0, 4))
        self._toggle_workflow_start_delay_control(persist=False)

        restart_default_bar = ttk.Frame(run_settings, style="Card.TFrame")
        restart_default_bar.grid(row=0, column=1, sticky="w", pady=pad(0, 6))
        restart_label = ttk.Label(restart_default_bar, text="重新执行默认跳转行",
                                  style="CardLabel.TLabel")
        restart_label.pack(side="left")
        self._attach_tip(
            restart_label,
            "动作没有指定跳转行时从这里开始；留空表示第 1 行。",
        )
        restart_field = ttk.Frame(restart_default_bar, style="Card.TFrame")
        restart_field.pack(side="left", padx=pad(8, 8))
        self.workflow_restart_default_combo = ttk.Combobox(
            restart_field, state="readonly", width=16,
        )
        self.workflow_restart_default_combo.pack(fill="x")
        self.workflow_restart_default_combo.bind(
            "<<ComboboxSelected>>", self._apply_workflow_restart_default)
        self._sync_workflow_restart_default_ui()

        resolution_bar = ttk.Frame(run_settings, style="Card.TFrame")
        resolution_bar.grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(resolution_bar, text="启动分辨率", style="CardLabel.TLabel").pack(side="left")
        start_resolution_field = ttk.Frame(resolution_bar, style="Card.TFrame")
        start_resolution_field.pack(side="left", padx=pad(8, 10))
        self.workflow_start_resolution_combo = ttk.Combobox(
            start_resolution_field, textvariable=self.workflow_start_resolution_var,
            state="readonly", width=14, postcommand=self._refresh_workflow_resolution_options,
        )
        self.workflow_start_resolution_combo.pack(fill="x")
        self.workflow_start_resolution_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._set_workflow_resolution("start"))
        ttk.Label(resolution_bar, text="停止后恢复", style="CardLabel.TLabel").pack(side="left")
        end_resolution_field = ttk.Frame(resolution_bar, style="Card.TFrame")
        end_resolution_field.pack(side="left", padx=pad(8, 10))
        self.workflow_end_resolution_combo = ttk.Combobox(
            end_resolution_field, textvariable=self.workflow_end_resolution_var,
            state="readonly", width=14, postcommand=self._refresh_workflow_resolution_options,
        )
        self.workflow_end_resolution_combo.pack(fill="x")
        self.workflow_end_resolution_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._set_workflow_resolution("end"))
        ttk.Button(resolution_bar, text="编辑分辨率样式",
                   command=self._configure_resolution_styles,
                   style="Ghost.TButton").pack(side="left")
        self._refresh_workflow_resolution_options()

        # Global modules and workflow steps share a draggable vertical split.
        self.workflow_content_pane = ttk.Panedwindow(outer, orient="vertical")
        self.workflow_content_pane.pack(fill="both", expand=True, pady=pad(10, 0))

        global_card = Card(self.workflow_content_pane, title="全局模块",
                           padding=(14, 6, 14, 8))
        self.workflow_content_pane.add(global_card, weight=2)
        global_box = global_card.body
        global_toolbar = ttk.Frame(global_box, style="Card.TFrame")
        global_toolbar.pack(fill="x", pady=pad(0, 7))
        ttk.Button(
            global_toolbar, text="添加模块", command=self.add_workflow_global_module,
            style="ChipAccent.TButton",
        ).pack(side="left")
        ttk.Button(
            global_toolbar, text="添加插件", command=self.add_workflow_plugin,
            style="ChipAccent.TButton",
        ).pack(side="left", padx=px(5))
        ttk.Button(
            global_toolbar, text="进程名库",
            command=lambda: ProcessLibraryDialog(self.root).show(),
            style="Chip.TButton",
        ).pack(side="left")
        ttk.Button(
            global_toolbar, text="编辑选中", command=self.edit_selected_global_module,
            style="Chip.TButton",
        ).pack(side="left", padx=px(5))
        ttk.Button(
            global_toolbar, text="启用/禁用", command=self.toggle_selected_global_module,
            style="Chip.TButton",
        ).pack(side="left")
        ttk.Button(
            global_toolbar, text="删除", command=self.delete_global_module,
            style="ChipDanger.TButton",
        ).pack(side="left", padx=px(5))
        self.global_undo_button = ttk.Button(
            global_toolbar, text="↶ 撤销", command=self.undo_workflow_edit,
            style="Chip.TButton", state="disabled",
        )
        self.global_undo_button.pack(side="left")

        global_tree_frame = ttk.Frame(global_box, style="Card.TFrame")
        global_tree_frame.pack(fill="both", expand=True, pady=pad(0, 4))
        self.global_tree = ttk.Treeview(
            global_tree_frame, columns=("index", "module", "status"),
            show="headings", selectmode="extended", style="Workflow.Treeview", height=6,
        )
        self._apply_column_widths(self.global_tree, GLOBAL_TREE_COLUMNS, "module")
        global_scroll = ttk.Scrollbar(global_tree_frame, bootstyle="dark", orient="vertical",
                                      command=self.global_tree.yview)
        attach_autohide_scrollbar(self.global_tree, global_scroll)
        self.global_tree.tag_configure("disabled", foreground="#F2B84B", background="#2B2418")
        self.global_tree.tag_configure("global", foreground="#7BC96F", background="#14261B")
        self.global_tree.pack(side="left", fill="both", expand=True)
        global_scroll.pack(side="right", fill="y")
        self.empty_global_hint = ttk.Label(
            global_tree_frame,
            text="还没有工作流全局模块\n添加模块，或点击“添加插件”配置进程缺失重启",
            style="Empty.TLabel", anchor="center", justify="center",
        )
        self.global_tree.bind("<Double-1>", lambda _event: self.edit_selected_global_module())
        self.global_tree.bind("<Control-a>", self._select_all_global_modules)
        self.global_tree.bind("<Button-3>", self._show_global_context_menu, add="+")

        workflow_card = Card(self.workflow_content_pane, title="步骤",
                             padding=(14, 6, 14, 8))
        self.workflow_content_pane.add(workflow_card, weight=3)
        workflow_box = workflow_card.body
        toolbar = ttk.Frame(workflow_box, style="Card.TFrame")
        toolbar.pack(fill="x", pady=pad(0, 6))

        add_toolbar = ttk.Frame(toolbar, style="Card.TFrame")
        add_toolbar.pack(fill="x")
        ttk.Label(add_toolbar, text="添加 / 插入", style="CardHint.TLabel").pack(
            side="left", padx=pad(0, 8))
        self.workflow_add_kind_combo = ttk.Combobox(
            add_toolbar, values=("当前脚本", "已有脚本", "模块"), state="readonly", width=10,
        )
        self.workflow_add_kind_combo.current(0)
        self.workflow_add_kind_combo.pack(side="left")
        ttk.Button(add_toolbar, text="确定", command=self._add_selected_workflow_step,
                   style="ChipAccent.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Label(add_toolbar, text="插入位置", style="CardHint.TLabel").pack(
            side="left", padx=pad(14, 6))
        self.workflow_insert_above_button = ttk.Button(
            add_toolbar, text="▲", width=3,
            command=lambda: self._set_workflow_insert_position(True),
            bootstyle="secondary",
        )
        self.workflow_insert_above_button.pack(side="left")
        self.workflow_insert_below_button = ttk.Button(
            add_toolbar, text="▼", width=3,
            command=lambda: self._set_workflow_insert_position(False),
            bootstyle="secondary",
        )
        self.workflow_insert_below_button.pack(side="left", padx=pad(5, 0))
        self._attach_tip(self.workflow_insert_above_button, "插入到选中步骤上方；未选中时追加到末尾。")
        self._attach_tip(self.workflow_insert_below_button, "插入到选中步骤下方；未选中时追加到末尾。")
        self._set_workflow_insert_position(False)

        edit_toolbar = ttk.Frame(toolbar, style="Card.TFrame")
        edit_toolbar.pack(fill="x", pady=pad(5, 0))
        ttk.Label(edit_toolbar, text="编辑 / 排序", style="CardHint.TLabel").pack(
            side="left", padx=pad(0, 8))
        ttk.Button(edit_toolbar, text="统一设置参数", command=self.set_all_workflow_step_options,
                   style="Chip.TButton").pack(side="left")
        ttk.Button(edit_toolbar, text="恢复全部次数", command=self.restore_all_workflow_counts,
                   style="Chip.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="启用/禁用", command=self.toggle_selected_workflow_step,
                   style="Chip.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="上移", command=lambda: self.move_workflow_step(-1),
                   style="Chip.TButton").pack(side="left", padx=pad(5, 2))
        ttk.Button(edit_toolbar, text="下移", command=lambda: self.move_workflow_step(1),
                   style="Chip.TButton").pack(side="left")
        ttk.Button(edit_toolbar, text="移到行…", command=self.move_workflow_step_to_row,
                   style="Chip.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="删除", command=self.delete_workflow_step,
                   style="ChipDanger.TButton").pack(side="left", padx=px(5))
        self.workflow_undo_button = ttk.Button(
            edit_toolbar, text="↶ 撤销", command=self.undo_workflow_edit,
            style="Chip.TButton", state="disabled",
        )
        self.workflow_undo_button.pack(side="left")

        frame = ttk.Frame(workflow_box, style="Card.TFrame")
        frame.pack(fill="both", expand=True)
        self.workflow_tree = ttk.Treeview(
            frame,
            columns=("mark", "index", "script", "original", "repeat", "restore",
                     "before", "interval", "enabled"),
            show="headings", selectmode="extended", style="Workflow.Treeview", height=10,
        )
        self._apply_column_widths(self.workflow_tree, WORKFLOW_TREE_COLUMNS, "script")
        scroll = ttk.Scrollbar(frame, bootstyle="dark", orient="vertical",
                               command=self.workflow_tree.yview)
        attach_autohide_scrollbar(self.workflow_tree, scroll)
        self.workflow_tree.tag_configure("missing", foreground="#FF6B6B", background="#321F24")
        self.workflow_tree.tag_configure("disabled", foreground="#F2B84B", background="#2B2418")
        self.workflow_tree.tag_configure("module_disabled", foreground="#FF8A8A", background="#3A2028")
        self.workflow_tree.tag_configure("exhausted", foreground="#87939E", background="#161D23")
        self.workflow_tree.tag_configure("unlimited", foreground="#7BC96F", background="#14261B")
        bind_tree_hover(self.workflow_tree)
        self.workflow_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.empty_workflow_hint = ttk.Label(
            frame, text="工作流还没有步骤\n添加脚本或模块后，可按住步骤上下拖动调整顺序",
            style="Empty.TLabel", anchor="center", justify="center",
        )
        self.workflow_tree.bind("<ButtonPress-1>", self._workflow_drag_start, add="+")
        self.workflow_tree.bind("<B1-Motion>", self._workflow_drag_motion, add="+")
        self.workflow_tree.bind("<ButtonRelease-1>", self._workflow_drag_end, add="+")
        self.workflow_tree.bind("<ButtonRelease-1>", self._click_workflow_restore, add="+")
        self.workflow_tree.bind("<Double-1>", self._edit_workflow_cell, add="+")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._update_workflow_selection_color, add="+")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._refresh_workflow_segment_bar, add="+")
        self.workflow_tree.bind("<Control-a>", self._select_all_workflow_steps)
        self.workflow_tree.bind("<Control-z>", self.undo_workflow_edit)
        self.global_tree.bind("<Control-z>", self.undo_workflow_edit)
        self.workflow_tree.bind("<Button-3>", self._show_workflow_context_menu, add="+")
        if self.__dict__.get('_workflow_history') is None:
            self._clear_workflow_history()
        else:
            self._update_workflow_undo_buttons()

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _attach_tip(widget, text: str):
        """把说明挂到文字控件上：鼠标悬停在文字上显示，不再画问号角标。"""
        Tooltip(widget, text, anchor=widget.master)

    def _create_variables(self):
        super()._create_variables()
        self.key_search_var = tk.StringVar(value="")
        self._key_search_query_kind = ""
        self.key_search_var.trace_add("write", self._clear_captured_search_query_kind)
        self.key_search_state_var = tk.StringVar(value="全部")
        self.key_search_delay_var = tk.StringVar(value="0")
        self.key_search_match_var = tk.StringVar(value="")
        self._input_search_capturer = None
