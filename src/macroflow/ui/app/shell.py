from __future__ import annotations
from macroflow.ui.dialogs.process_names import ProcessLibraryDialog

from macroflow.core.storage import WORKFLOWS_DIR
from macroflow.ui.dialogs.app_dialogs import CoordinateFlashDialog
from macroflow.ui.dialogs.base import TIME_UNITS, Tooltip
import sys
from macroflow.core.storage import resolve_path
import tkinter as tk
import ttkbootstrap as ttk

from .base import apply_pointer_cursors, attach_autohide_scrollbar, bind_tree_hover, pad, px
from .constants import ACTION_TREE_COLUMNS, COLOR_BLUE, COLOR_GREEN, COLOR_RED, COLOR_SIDEBAR, COLOR_SURFACE, COLOR_SURFACE_ALT, COLOR_TEXT, FLOATING_NOTICE_POSITIONS, FONT_BODY, FONT_FAMILY, GLOBAL_TREE_COLUMNS, RECORD_TOOLBAR_BUTTON_LABEL, SCRIPT_CATEGORY_VALUES, WORKFLOW_TREE_COLUMNS

from .action_palette import action_button_positions, action_category_specs


def attach_combobox_scrollbar(parent, combo):
    scrollbar = ttk.Scrollbar(parent, bootstyle="dark", orient="horizontal", command=combo.xview)
    def changed(first, last):
        scrollbar.set(first, last)
        if float(first) > 0 or float(last) < 1:
            scrollbar.pack(fill="x")
        else:
            scrollbar.pack_forget()
    combo.configure(xscrollcommand=changed)
    return scrollbar


def fit_sidebar_scrollregion(canvas):
    bounds = canvas.bbox("all")
    if bounds is None:
        return
    width, height = canvas.winfo_width(), canvas.winfo_height()
    canvas.configure(scrollregion=(0, 0, max(width, bounds[2]), max(height, bounds[3])))
    if bounds[3] <= height:
        canvas.yview_moveto(0)


class SidebarButton(tk.Canvas):
    """Compact rounded sidebar control with mouse and keyboard activation."""

    def __init__(self, parent, *, text, command, state="normal", bootstyle="", style="", width=None):
        self._text, self._command, self._state = text, command, state
        self._tone = bootstyle
        self._pressed = False
        super().__init__(
            parent, width=px(width * 10 + 16 if width else len(text) * 10 + 20),
            height=px(30), background=COLOR_SIDEBAR, borderwidth=0, autostyle=False,
            highlightthickness=0, takefocus=state != "disabled", cursor="arrow" if state == "disabled" else "hand2",
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

    def _release(self, event):
        pressed, self._pressed = self._pressed, False
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

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
        options.update(takefocus=self._state != "disabled", cursor="arrow" if self._state == "disabled" else "hand2")
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
        width, height, radius = self.winfo_width(), self.winfo_height(), px(8)
        fill = {"danger": COLOR_RED, "success": COLOR_GREEN, "primary": COLOR_BLUE}.get(self._tone, COLOR_SURFACE_ALT)
        disabled = self._state == "disabled"
        if disabled:
            fill = COLOR_SURFACE
        points = (1 + radius, 1, width - radius - 1, 1, width - 1, 1,
                  width - 1, radius + 1, width - 1, height - radius - 1,
                  width - 1, height - 1, width - radius - 1, height - 1,
                  radius + 1, height - 1, 1, height - 1, 1, height - radius - 1,
                  1, radius + 1, 1, 1)
        self.create_polygon(points, smooth=True, splinesteps=16, fill=fill,
                            outline=COLOR_BLUE if self.focus_get() == self else fill, width=px(2))
        self.create_text(width / 2, height / 2, text=self._text,
                         fill="#77838F" if disabled else COLOR_TEXT, font=(FONT_FAMILY, FONT_BODY))


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


    def _build_ui(self):
        root_frame = ttk.Frame(self.root, style="Workspace.TFrame")
        self.root_frame = root_frame
        root_frame.pack(fill="both", expand=True)
        status = ttk.Frame(root_frame, padding=pad(14, 5, 14, 5), style="Status.TFrame")
        status.pack(side="bottom", fill="x")
        ttk.Separator(root_frame, orient="horizontal").pack(side="bottom", fill="x")
        self.status_dot = ttk.Label(status, textvariable=self.status_var, style="StatusText.TLabel")
        self.status_dot.pack(side="left")
        ttk.Label(status, textvariable=self.coordinate_scale_var, style="Muted.TLabel").pack(side="right")

        content = ttk.Frame(root_frame, style="Workspace.TFrame")
        content.pack(fill="both", expand=True)
        self._build_sidebar(content)
        main = ttk.Frame(content, padding=pad(14, 10, 14, 8), style="Workspace.TFrame")
        main.pack(side="left", fill="both", expand=True)
        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)
        self.script_tab = ttk.Frame(self.notebook)
        self.workflow_tab = ttk.Frame(self.notebook)
        self.log_tab = ttk.Frame(self.notebook)
        self.plugins_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.script_tab, text="脚本编辑")
        self.notebook.add(self.workflow_tab, text="工作流")
        self.notebook.add(self.log_tab, text="运行日志")
        self.notebook.add(self.plugins_tab, text="插件")
        self._build_script_tab()
        self._build_workflow_tab()
        self._build_log_tab()
        from .plugins_page import build_plugins_page
        build_plugins_page(self.plugins_tab, self)
        apply_pointer_cursors(root_frame)

    def _rebuild_ui_for_display_change(self):
        """Recreate widgets whose pixel sizes were fixed when they were built."""
        old_frame = getattr(self, "root_frame", None)
        if old_frame is None:
            return
        try:
            tab_index = self.notebook.index(self.notebook.select())
        except (AttributeError, tk.TclError, ValueError):
            tab_index = 0
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
        self.notebook.select(tab_index)
        self._show_log_view(log_view)

    def _build_sidebar(self, parent):
        # Keep the configuration panel usable on shorter screens. The inner
        # frame keeps its existing layout while the canvas provides vertical
        # scrolling for all controls.
        sidebar_shell = ttk.Frame(parent, width=px(372), style="Sidebar.TFrame")
        sidebar_shell.pack(side="left", fill="y")
        sidebar_shell.pack_propagate(False)
        sidebar_canvas = tk.Canvas(
            sidebar_shell, background=COLOR_SIDEBAR, highlightthickness=0, autostyle=False,
            borderwidth=0, width=px(350),
        )
        sidebar_scrollbar = ttk.Scrollbar(sidebar_shell, bootstyle="dark", orient="vertical", command=sidebar_canvas.yview)
        sidebar_canvas.configure(yscrollcommand=sidebar_scrollbar.set)
        sidebar_canvas.pack(side="left", fill="both", expand=True)
        sidebar_scrollbar.pack(side="right", fill="y")
        sidebar = ttk.Frame(sidebar_canvas, width=px(350), padding=pad(14, 14), style="Sidebar.TFrame")
        sidebar_window = sidebar_canvas.create_window((0, 0), window=sidebar, anchor="nw")

        def update_sidebar_scrollregion(_event=None):
            fit_sidebar_scrollregion(sidebar_canvas)

        def resize_sidebar_content(event):
            sidebar_canvas.itemconfigure(sidebar_window, width=event.width)
            fit_sidebar_scrollregion(sidebar_canvas)

        sidebar.bind("<Configure>", update_sidebar_scrollregion)
        sidebar_canvas.bind("<Configure>", resize_sidebar_content)

        def scroll_sidebar(event):
            if event.delta:
                sidebar_canvas.yview_scroll(-int(event.delta / 120), "units")

        def bind_sidebar_wheel(widget):
            widget.bind("<MouseWheel>", scroll_sidebar, add="+")
            for child in widget.winfo_children():
                bind_sidebar_wheel(child)
        ttk.Label(sidebar, text="MacroFlow", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="录制、识图与自动工作流", style="SidebarMuted.TLabel").pack(anchor="w", pady=pad(1, 7))
        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=pad(0, 9))

        primary_controls = ttk.Frame(sidebar, style="Sidebar.TFrame")
        primary_controls.pack(fill="x")
        primary_controls.columnconfigure((0, 1), weight=1, uniform="primary")
        self.record_button = SidebarButton(primary_controls, text="开始录制    F8", command=lambda: self.toggle_record(from_ui=True), bootstyle="danger")
        self.record_button.grid(row=0, column=0, sticky="ew", padx=pad(0, 3))
        self.run_button = SidebarButton(primary_controls, text="执行当前脚本    F9", command=self.run_current_script, bootstyle="success")
        self.run_button.grid(row=0, column=1, sticky="ew", padx=pad(3, 0))
        secondary_controls = ttk.Frame(sidebar, style="Sidebar.TFrame")
        secondary_controls.pack(fill="x", pady=pad(6, 11))
        secondary_controls.columnconfigure((0, 1, 2), weight=1, uniform="secondary")
        self.script_pause_button = SidebarButton(secondary_controls, text="暂停", command=self.toggle_execution_pause, state="disabled")
        self.script_pause_button.grid(row=0, column=0, sticky="ew", padx=pad(0, 3))
        SidebarButton(secondary_controls, text="停止 F12", command=self.stop_all).grid(row=0, column=1, sticky="ew", padx=px(3))
        SidebarButton(secondary_controls, text="游戏设置说明", command=self.open_game_setup_note).grid(row=0, column=2, sticky="ew", padx=pad(3, 0))

        settings_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        settings_title.pack(fill="x", pady=pad(0, 5))
        ttk.Label(settings_title, text="录制设置", style="SidebarSection.TLabel").pack(side="left")
        SidebarButton(settings_title, text="测试声音", command=self.test_sound,
                   style="SidebarGhost.TButton", width=7).pack(side="right")
        self.sidebar_save_button = SidebarButton(settings_title, text="保存配置", command=self.save_sidebar_config,
                   style="SidebarGhost.TButton", width=7, state='disabled')
        self.sidebar_save_button.pack(side="right", padx=pad(0, 4))
        option_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        option_row.pack(fill="x", pady=pad(0, 10))
        check_style = {
            "autostyle": False, "anchor": "w", "background": COLOR_SIDEBAR, "activebackground": COLOR_SIDEBAR,
            "foreground": COLOR_TEXT, "activeforeground": "#FFFFFF", "selectcolor": COLOR_SURFACE_ALT,
            "highlightthickness": 0, "borderwidth": 0, "font": (FONT_FAMILY, FONT_BODY),
        }
        radio_style = dict(check_style)
        radio_style.pop("anchor")
        tk.Checkbutton(option_row, text="快捷键提示音", variable=self.sound_enabled_var,
                       command=self._settings_changed, **check_style).pack(anchor="w", pady=px(1))
        tk.Checkbutton(option_row, text="录制时显示悬浮小窗", variable=self.mini_window_enabled_var,
                       command=self._settings_changed, **check_style).pack(anchor="w", pady=px(1))
        tk.Checkbutton(option_row, text="执行时显示悬浮小窗", variable=self.execution_mini_enabled_var,
                       command=self._settings_changed, **check_style).pack(anchor="w", pady=px(1))
        SidebarButton(
            option_row, text="调节小窗位置",
            command=self._adjust_execution_mini_position,
            style="SidebarGhost.TButton",
        ).pack(anchor="w", pady=pad(2, 6))
        ttk.Label(option_row, text="点击关闭按钮时", style="Section.TLabel").pack(anchor="w", pady=pad(7, 2))
        close_row = ttk.Frame(option_row, style="Sidebar.TFrame")
        close_row.pack(fill="x")
        for value, label in (("exit", "直接退出"), ("tray", "隐藏到托盘")):
            tk.Radiobutton(
                close_row, text=label, value=value, variable=self.close_action_var,
                command=self._settings_changed, **radio_style,
            ).pack(side="left", padx=pad(0, 10))
        ttk.Label(
            option_row,
            text="“直接退出”＝关掉窗口就彻底结束进程（无后台）；“隐藏到托盘”＝窗口收起、"
                 "快捷键继续生效，右键托盘图标选“退出”才结束。",
            wraplength=px(300), style="SidebarMuted.TLabel",
        ).pack(anchor="w", pady=pad(4, 0))

        record_mode_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        record_mode_title.pack(fill="x", pady=pad(0, 5))
        record_mode_label = ttk.Label(
            record_mode_title, text="智能录制", style="SidebarSection.TLabel",
        )
        record_mode_label.pack(side="left")
        self._attach_tip(
            record_mode_label,
            "桌面自动记录坐标；绑定游戏窗口，或在游戏中按 F8，可自动记录锁中心的视角转向。",
        )
        ttk.Separator(sidebar).pack(fill="x", pady=px(4))
        target_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        target_title.pack(fill="x", pady=pad(16, 7))
        target_label = ttk.Label(target_title, text="目标窗口", style="Section.TLabel")
        target_label.pack(side="left")
        self._attach_tip(
            target_label,
            "绑定后，坐标会按目标窗口记录和回放；游戏相对转向保持原始视角幅度，不参与分辨率缩放。",
        )
        # wraplength 必须等于侧栏可用宽度：小于它才会换行，等于更大只会被裁切。
        self.bind_label_widget = ttk.Label(sidebar, textvariable=self.bind_label_var,
                                           wraplength=px(318), style="SidebarMuted.TLabel")
        self.bind_label_widget.pack(anchor="w", fill="x", pady=pad(0, 8))
        row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        row.pack(fill="x")
        SidebarButton(row, text="选择窗口", command=self.choose_window, bootstyle="primary").pack(side="left")
        SidebarButton(row, text="清除绑定", command=self.unbind_window, width=7, style="SidebarGhost.TButton").pack(side="left", padx=pad(6, 0))
        coordinate_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        coordinate_row.pack(fill="x", pady=pad(8, 0))
        self.cursor_position_button = SidebarButton(
            coordinate_row, text="开始实时读取", command=self.toggle_cursor_tracking,
            style="SidebarGhost.TButton",
        )
        self.cursor_position_button.pack(side="left")
        ttk.Label(
            coordinate_row, textvariable=self.cursor_position_var,
            style="SidebarMuted.TLabel",
        ).pack(side="left", padx=pad(8, 0))
        SidebarButton(
            row, text="闪烁定位", command=lambda: CoordinateFlashDialog(self.root).show(),
            style="SidebarGhost.TButton",
        ).pack(side="left", padx=pad(6, 0))

        ttk.Label(sidebar, text="执行设置", style="SidebarSection.TLabel").pack(anchor="w", pady=pad(18, 7))
        aim_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        aim_row.pack(fill="x", pady=pad(0, 6))
        ttk.Label(aim_row, text="锁定跟随等待", style="Sidebar.TLabel").pack(side="left")
        self.aim_follow_interval_var = tk.StringVar(
            master=self.root, value=str(self.app_settings.get('ssjj_aim_interval_ms', 2)))
        aim_interval = ttk.Spinbox(aim_row, from_=1, to=1000, width=5,
                                  textvariable=self.aim_follow_interval_var,
                                  command=self.apply_aim_follow_interval)
        aim_interval.pack(side="left", padx=pad(6, 3))
        aim_interval.bind('<Return>', self.apply_aim_follow_interval)
        ttk.Label(aim_row, text="ms", style="SidebarMuted.TLabel").pack(side="left")
        self.aim_apply_button = SidebarButton(aim_row, text="应用", width=5, command=self.apply_aim_follow_interval,
                      style="SidebarGhost.TButton", state='disabled')
        self.aim_apply_button.pack(side="right")
        resolution_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        resolution_title.pack(fill="x", pady=pad(4, 2))
        resolution_label = ttk.Label(
            resolution_title, text="分辨率样式", style="Sidebar.TLabel",
        )
        resolution_label.pack(side="left")
        self._attach_tip(
            resolution_label,
            "脚本中的“分辨率”动作从这里选择样式；参照窗口留空即改当前软件所在的显示器，"
            "选了窗口则只改该窗口所在的显示器。",
        )
        SidebarButton(
            resolution_title, text="设置…", width=7,
            command=self._configure_resolution_styles,
            style="SidebarGhost.TButton",
        ).pack(side="right")
        self.switch_english_input_var = tk.BooleanVar(
            master=self.root, value=self.switch_english_input_enabled,
        )
        tk.Checkbutton(
            sidebar, text="执行前切换英文输入法",
            variable=self.switch_english_input_var,
            command=lambda: setattr(self, "switch_english_input_enabled",
                                    bool(self.switch_english_input_var.get())),
            **check_style,
        ).pack(anchor="w", pady=pad(0, 6))
        focus_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        focus_row.pack(fill="x", pady=pad(0, 6))
        focus_check = tk.Checkbutton(
            focus_row, text="强制专注模式",
            variable=self.focus_mode_enabled_var,
            command=self._settings_changed,
            **check_style,
        )
        focus_check.pack(side="left")
        self._attach_tip(
            focus_check, "执行时锁定实体键鼠，适合需要持续前台操作的目标。",
        )
        activate_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activate_row.pack(fill="x", pady=pad(0, 6))
        activate_check = tk.Checkbutton(
            activate_row, text="执行时前置目标",
            variable=self.activate_target_enabled_var,
            command=self._toggle_target_activation,
            **check_style,
        )
        activate_check.pack(side="left")
        self._attach_tip(
            activate_check,
            "勾选后每次执行前激活目标窗口；取消勾选只停止前置，不会清除已保存的目标窗口。",
        )
        ttk.Label(sidebar, text="局部执行时启用", style="SidebarMuted.TLabel").pack(anchor="w")
        for label, variable in (
            ("脚本全局模块", self.partial_script_globals_var),
            ("工作流全局模块", self.partial_workflow_globals_var),
        ):
            tk.Checkbutton(
                sidebar, text=label, variable=variable,
                command=self._settings_changed, **check_style,
            ).pack(anchor="w")
        activation_toggle_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activation_toggle_row.pack(fill="x", pady=pad(0, 4))
        activation_check = tk.Checkbutton(
            activation_toggle_row, text="启用执行前置窗口",
            variable=self.activation_enabled_var,
            command=self._toggle_activation_enabled,
            **check_style,
        )
        activation_check.pack(side="left")
        self._attach_tip(
            activation_check, "可指定另一个窗口先被激活，再执行当前脚本。",
        )
        ttk.Label(sidebar, text="执行前置窗口", style="SidebarMuted.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar, textvariable=self.activation_label_var,
            wraplength=px(318), style="SidebarMuted.TLabel",
        ).pack(anchor="w", pady=pad(2, 6))
        activation_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activation_row.pack(fill="x", pady=pad(0, 8))
        SidebarButton(
            activation_row, text="选择前置窗口", command=self.choose_activation_window,
            style="SidebarGhost.TButton",
        ).pack(side="left", fill="x", expand=True)
        SidebarButton(
            activation_row, text="跟随目标", command=self.unbind_activation_window,
            width=8, style="SidebarGhost.TButton",
        ).pack(side="left", padx=pad(8, 0))
        activation_interval_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activation_interval_row.pack(fill="x", pady=pad(0, 8))
        ttk.Label(activation_interval_row, text="两次前置间隔", style="SidebarMuted.TLabel").pack(side="left")
        interval_entry = ttk.Entry(
            activation_interval_row, textvariable=self.activation_interval_var, width=9,
        )
        interval_entry.pack(side="left", padx=pad(8, 4))
        interval_entry.bind("<FocusOut>", self._save_activation_interval)
        interval_entry.bind("<Return>", self._save_activation_interval)
        ttk.Label(activation_interval_row, text="秒", style="SidebarMuted.TLabel").pack(side="left")
        # 同“桌面轨迹间隔”：标题与控件分两行，避免高 DPI 下控件被挤出侧栏。
        repeat_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        repeat_title.pack(fill="x", pady=pad(0, 7))
        repeat_title_label = ttk.Label(repeat_title, text="脚本重复次数", style="Sidebar.TLabel")
        repeat_title_label.pack(side="left")
        self._attach_tip(repeat_title_label, "执行当前脚本时完整重复的次数。")
        repeat_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        repeat_row.pack(fill="x")
        self.repeat_spin = ttk.Spinbox(
            repeat_row, from_=1, to=999999, textvariable=self.repeat_var, width=6, font=(FONT_FAMILY, FONT_BODY),
        )
        self.repeat_spin.pack(side="left")
        self.repeat_edit_button = SidebarButton(
            repeat_row, text="修改", width=5, style="SidebarGhost.TButton",
            command=lambda: self._toggle_locked_spinbox(
                self.repeat_spin, self.repeat_edit_button, self._settings_changed,
            ),
        )
        self.repeat_edit_button.pack(side="left", padx=pad(8, 0))
        self.repeat_spin.configure(state="disabled")

        notice_position_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        notice_position_row.pack(fill="x", pady=pad(9, 0))
        notice_title = ttk.Frame(notice_position_row, style="Sidebar.TFrame")
        notice_title.pack(side="left")
        notice_title_label = ttk.Label(notice_title, text="浮动提醒位置", style="Sidebar.TLabel")
        notice_title_label.pack(side="left")
        self._attach_tip(notice_title_label, "选择脚本“提醒”动作在屏幕上的显示位置。")
        notice_position_box = ttk.Combobox(
            notice_position_row,
            textvariable=self.floating_notice_position_var,
            values=FLOATING_NOTICE_POSITIONS,
            state="readonly",
            width=10,
        )
        notice_position_box.pack(side="right")
        notice_position_box.bind("<<ComboboxSelected>>", self._settings_changed)
        notice_position_box.bind("<MouseWheel>", lambda _event: "break")

        ttk.Separator(sidebar).pack(fill="x", pady=pad(16, 4))
        hotkey_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        hotkey_title.pack(fill="x", pady=pad(12, 7))
        hotkey_title_label = ttk.Label(
            hotkey_title, text="快捷键脚本", style="SidebarSection.TLabel",
        )
        hotkey_title_label.pack(side="left")
        self._attach_tip(
            hotkey_title_label,
            "把脚本绑定到快捷键：录制或执行脚本的过程中，按下快捷键立即执行该脚本，"
            "例如游戏中按 J 执行“转向左 90°”。",
        )
        SidebarButton(
            hotkey_title, text="设置…", width=7, style="SidebarGhost.TButton",
            command=self._configure_hotkey_scripts,
        ).pack(side="right")
        bind_sidebar_wheel(sidebar)
        update_sidebar_scrollregion()
        self._watch_settings_saves(sidebar)

    @staticmethod
    def _attach_tip(widget, text: str):
        """把说明挂到文字控件上：鼠标悬停在文字上显示，不再画问号角标。"""
        Tooltip(widget, text, anchor=widget.master)

    def _build_action_palette(self, parent):
        self.action_palette = ttk.Notebook(parent)
        self.action_palette.pack(fill="x", padx=px(12), pady=pad(0, 6))
        for label, primary, extra in action_category_specs(self._script_action_button_specs()):
            tab = ttk.Frame(self.action_palette)
            self.action_palette.add(tab, text=label)
            tab.action_buttons = [
                ttk.Button(tab, text=text, command=getattr(self, command), style=style)
                for text, command, style in (*primary, *extra)
            ]
            tab.action_expanded = False
            tab.action_layout = None
            tab.action_toggle = ttk.Button(tab, text="展开 ▾", style="ScriptTool.TButton")
            tab.action_toggle.configure(command=lambda pane=tab: self._toggle_action_category(pane))
            tab.bind("<Configure>", lambda event, pane=tab: self._layout_action_category(pane))
        self.action_palette.bind("<<NotebookTabChanged>>", self._resize_action_palette)

    def _layout_action_category(self, tab):
        width = tab.winfo_width() - px(16)
        if width <= 0:
            return
        buttons, toggle = tab.action_buttons, tab.action_toggle
        widths = tuple(button.winfo_reqwidth() for button in buttons)
        layout = (width, widths, toggle.winfo_reqwidth(), tab.action_expanded)
        if tab.action_layout == layout:
            return
        tab.action_layout = layout
        positions, toggle_position = action_button_positions(*layout, gap=px(6))
        height = max(button.winfo_reqheight() for button in (*buttons, toggle))
        rows = 1
        for button, position in zip((*buttons, toggle), (*positions, toggle_position)):
            if position is None:
                button.place_forget()
            else:
                x, row = position
                button.place(x=px(8) + x, y=px(5) + row * (height + px(6)))
                rows = max(rows, row + 1)
        tab.configure(height=px(10) + rows * height + (rows - 1) * px(6))
        self.action_palette.after_idle(self._resize_action_palette)

    def _resize_action_palette(self, _event=None):
        selected = self.action_palette.select()
        if selected:
            tab = self.action_palette.nametowidget(selected)
            self.action_palette.configure(height=tab.winfo_reqheight())

    def _toggle_action_category(self, tab):
        tab.action_expanded = not tab.action_expanded
        tab.action_toggle.configure(text="收起 ▴" if tab.action_expanded else "展开 ▾")
        self._layout_action_category(tab)

    def _insert_script_reference(self):
        self._insert_script(False)

    def _insert_script_inline(self):
        self._insert_script(True)

    def _show_script_more_menu(self):
        """「⋯ 更多」：脚本插入与低频管理入口。"""
        menu = tk.Menu(
            self.root, tearoff=False, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        menu.add_command(label="⇥ 引用脚本（实时读取）", command=lambda: self._insert_script(False))
        menu.add_command(label="⇥ 逐行插入脚本", command=lambda: self._insert_script(True))
        menu.add_command(label="⇥ 插入脚本指定行…", command=self._insert_script_range)
        menu.add_separator()
        menu.add_command(label="📂 打开脚本目录",
                         command=lambda: self.open_folder(self._script_category_dir()))
        menu.add_command(label="🗂 目录设置…", command=self._configure_script_directories)
        menu.add_command(label="▤ 模块管理…", command=self.open_template_region_manager)
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

            # 录制与侧栏「开始录制 F8」同一个开关：脚本编辑时不必再回侧栏找。
            (RECORD_TOOLBAR_BUTTON_LABEL, "_toggle_record_from_toolbar", "DangerScriptTool.TButton"),
        )

    def _build_script_tab(self):
        header = ttk.Frame(self.script_tab, padding=pad(16, 14, 16, 8), style="Workspace.TFrame")
        header.pack(fill="x")
        meta_row = ttk.Frame(header, style="Workspace.TFrame")
        meta_row.pack(fill="x")
        ttk.Label(meta_row, text="脚本名称", style="PageTitle.TLabel").pack(side="left")
        name_entry = ttk.Entry(meta_row, textvariable=self.script_name_var, width=30)
        name_entry.pack(side="left", padx=pad(12, 14), ipady=px(2))
        name_entry.bind("<KeyRelease>", lambda _: self._mark_dirty())
        self.global_script_marker = ttk.Label(meta_row, text="", style="GlobalMarker.TLabel")
        self.global_script_marker.pack(side="left", padx=pad(0, 10))
        ttk.Label(meta_row, text="类别").pack(side="left")
        category_box = ttk.Combobox(
            meta_row, textvariable=self.script_category_var,
            values=SCRIPT_CATEGORY_VALUES, state="readonly", width=10,
        )
        category_box.pack(side="left", padx=pad(6, 14))
        category_box.bind("<<ComboboxSelected>>", self._script_category_changed)
        ttk.Label(meta_row, textvariable=self.record_count_var, style="Muted.TLabel").pack(side="right")

        file_row = ttk.Frame(header, style="Workspace.TFrame")
        file_row.pack(fill="x", pady=pad(10, 0))
        bundle_row = ttk.Frame(header, style="Workspace.TFrame")
        bundle_row.pack(fill="x", pady=pad(6, 0))
        ttk.Button(bundle_row, text="导入包…", command=self.import_portable_bundle,
                   style="Ghost.TButton").pack(side="left")
        ttk.Button(bundle_row, text="导出当前脚本…", command=self.export_portable_bundle,
                   style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="新建", command=self.new_script, style="Ghost.TButton").pack(side="left")
        ttk.Button(file_row, text="打开", command=self.open_script, style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="关闭", command=self.close_script, style="DangerScriptTool.TButton").pack(side="left", padx=pad(6, 0))
        self.undo_open_button = ttk.Button(
            file_row, text="↩ 撤销打开", command=self.undo_open_script,
            style="Ghost.TButton", state="disabled",
        )
        self.undo_open_button.pack(side="left", padx=pad(6, 0))
        script_save_button = ttk.Button(file_row, text="保存", command=self.save_current_script,
                                       bootstyle="primary", state='disabled')
        script_save_button.pack(side="left", padx=pad(6, 0))
        from macroflow.ui.save_state import SaveState
        self._script_save_state = SaveState(file_row, script_save_button,
                                            lambda: bool(self.dirty), baseline=False)
        ttk.Button(file_row, text="新开窗口", command=self.open_new_window, style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="模块管理…", command=self.open_template_region_manager,
                   style="Ghost.TButton").pack(side="right")
        ttk.Button(file_row, text="目录设置…", command=self._configure_script_directories,
                   style="Ghost.TButton").pack(side="right", padx=pad(0, 6))
        ttk.Button(file_row, text="打开脚本目录", command=lambda: self.open_folder(self._script_category_dir()),
                   style="Ghost.TButton").pack(side="right", padx=pad(0, 6))

        self._build_action_palette(self.script_tab)
        toolbar = ttk.Frame(self.script_tab, padding=pad(12, 3, 12, 8), style="Toolbar.TFrame")
        toolbar.pack(fill="x")

        add_buttons = ttk.Frame(toolbar, style="Toolbar.TFrame")
        add_buttons.pack(side="left", fill="x")

        ttk.Label(add_buttons, text="插入位置", style="Muted.TLabel").pack(
            side="left", padx=pad(4, 2),
        )
        self.insert_above_button = ttk.Button(
            add_buttons, text="▲ 向上插入",
            command=lambda: self._set_insert_position(True),
        )
        self.insert_below_button = ttk.Button(
            add_buttons, text="▼ 向下插入",
            command=lambda: self._set_insert_position(False),
        )
        self.insert_above_button.pack(side="left", padx=px(2))
        self.insert_below_button.pack(side="left", padx=px(2))

        edit_buttons = ttk.Frame(toolbar, style="Toolbar.TFrame")
        edit_buttons.pack(side="left", fill="x")
        self.undo_button = ttk.Button(edit_buttons, text="↶ 撤销",
                                      command=lambda: self._undo_redo_action_edit(False),
                                      style="ScriptTool.TButton", state="disabled")
        self.undo_button.pack(side="left")
        self.redo_button = ttk.Button(edit_buttons, text="↷ 重做",
                                      command=lambda: self._undo_redo_action_edit(True),
                                      style="ScriptTool.TButton", state="disabled")
        self.redo_button.pack(side="left", padx=px(4))
        for text, command, style_name in (
            ("✎ 编辑", self.edit_selected_action, "ScriptTool.TButton"),
            ("⧉ 复制", self.copy_selected_actions_down, "ScriptTool.TButton"),
            ("▶ 从此", self.run_script_from_selected_action, "AccentScriptTool.TButton"),
            ("↑ 上移", lambda: self.move_action(-1), "ScriptTool.TButton"),
            ("↓ 下移", lambda: self.move_action(1), "ScriptTool.TButton"),
            ("× 删除", self.delete_actions, "DangerScriptTool.TButton"),
        ):
            button = ttk.Button(edit_buttons, text=text, command=command, style=style_name)
            button.pack(side="left", padx=px(4))
            if text == "✎ 编辑":
                self.edit_action_button = button

        # 「更多」：脚本插入与低频管理入口。
        self.script_more_menu_button = ttk.Button(
            toolbar, text="⋯ 更多", command=self._show_script_more_menu,
            style="ScriptTool.TButton",
        )
        self.script_more_menu_button.pack(side="right")
        self._set_insert_position(False)

        # 全局脚本：触发条件区块 + 语句体标题（类别为"全局"时显示）。
        self.trigger_holder = ttk.Frame(self.script_tab, padding=pad(16, 0, 16, 0), style="Workspace.TFrame")
        self.trigger_holder.pack(fill="x")
        self.trigger_section = ttk.Frame(self.trigger_holder, style="GlobalTrigger.TFrame")
        trigger_row = ttk.Frame(self.trigger_section, style="GlobalTrigger.TFrame")
        trigger_row.pack(fill="x", pady=pad(10, 6))
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
        ).pack(fill="x", pady=pad(0, 8))

        frame = ttk.Frame(self.script_tab, padding=pad(16, 0, 16, 16), style="Surface.TFrame")
        frame.pack(fill="both", expand=True)
        key_search_bar = ttk.Frame(frame, style="Surface.TFrame")
        key_search_bar.pack(fill="x", pady=pad(0, 8))
        ttk.Label(key_search_bar, text="搜索键鼠", style="SidebarMuted.TLabel").pack(side="left")
        key_search_entry = ttk.Entry(
            key_search_bar, textvariable=self.key_search_var, width=18,
        )
        key_search_entry.pack(side="left", padx=pad(8, 5))
        self.input_search_capture_button = ttk.Button(
            key_search_bar, text="检测键鼠…", width=10,
            command=self.start_input_search_capture, style="Ghost.TButton",
        )
        self.input_search_capture_button.pack(side="left", padx=pad(0, 5))
        key_search_entry.bind("<Return>", lambda _event: self._search_key_actions(1))
        key_search_state = ttk.Combobox(
            key_search_bar, textvariable=self.key_search_state_var,
            values=("全部", "按下", "抬起", "Press"), state="readonly", width=8,
        )
        key_search_state.pack(side="left")
        key_search_state.bind(
            "<<ComboboxSelected>>", lambda _event: self._search_key_actions(1),
        )
        ttk.Button(
            key_search_bar, text="上一个", width=7,
            command=lambda: self._search_key_actions(-1), style="Ghost.TButton",
        ).pack(side="left", padx=pad(8, 3))
        ttk.Button(
            key_search_bar, text="下一个", width=7,
            command=lambda: self._search_key_actions(1), style="Ghost.TButton",
        ).pack(side="left")
        ttk.Button(
            key_search_bar, text="清除", width=5,
            command=self._clear_key_search, style="Ghost.TButton",
        ).pack(side="left", padx=pad(3, 8))
        ttk.Label(key_search_bar, text="统一前延时 ms", style="SidebarMuted.TLabel").pack(side="left")
        ttk.Entry(
            key_search_bar, textvariable=self.key_search_delay_var, width=8,
        ).pack(side="left", padx=pad(5, 3))
        ttk.Button(
            key_search_bar, text="统一设置", width=8,
            command=self._set_matching_key_action_delays, style="Ghost.TButton",
        ).pack(side="left", padx=pad(0, 8))
        ttk.Label(
            key_search_bar, textvariable=self.key_search_match_var,
            style="SidebarMuted.TLabel",
        ).pack(side="left")
        action_tree_shell = ttk.Frame(frame, style="Surface.TFrame")
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
        scroll = ttk.Scrollbar(action_tree_shell, bootstyle="dark", orient="vertical", command=self.action_tree.yview)
        horizontal_scroll = ttk.Scrollbar(
            action_tree_shell, bootstyle="dark", orient="horizontal", command=self.action_tree.xview,
        )
        self.action_tree.configure(xscrollcommand=horizontal_scroll.set)
        attach_autohide_scrollbar(self.action_tree, scroll)
        self.action_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.empty_action_hint = ttk.Label(
            frame, text="还没有动作\n按 F8 或点「录制/脚本」类别里的「录制」开始，也可用上方按钮逐条添加",
            style="Empty.TLabel", anchor="center", justify="center"
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

    def _build_workflow_tab(self):
        header = ttk.Frame(self.workflow_tab, padding=pad(16, 18, 16, 12), style="Workspace.TFrame")
        header.pack(fill="x")
        workflow_meta_bar = ttk.Frame(header, style="Workspace.TFrame")
        workflow_meta_bar.pack(fill="x")
        bundle_row = ttk.Frame(self.workflow_tab, padding=pad(16, 4), style="Workspace.TFrame")
        bundle_row.pack(fill="x")
        ttk.Button(bundle_row, text="防遮挡进程…", command=self.edit_foreground_minimize_processes,
                   style="CompactGhost.TButton").pack(side="left")
        ttk.Button(bundle_row, text="导入包…", command=self.import_portable_bundle,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Button(bundle_row, text="导出当前工作流…", command=lambda: self.export_portable_bundle("workflow"),
                   style="CompactGhost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Label(workflow_meta_bar, text="工作流名称", style="PageTitle.TLabel").pack(side="left")
        workflow_name_entry = ttk.Entry(workflow_meta_bar, textvariable=self.workflow_name_var, width=22)
        workflow_name_entry.pack(side="left", padx=pad(8, 6))
        workflow_name_entry.bind("<KeyRelease>", self._schedule_workflow_draft_save)
        ttk.Button(workflow_meta_bar, text="✏️ 修改名称", command=self.rename_workflow,
                   style="CompactGhost.TButton").pack(side="left")
        ttk.Button(workflow_meta_bar, text="⧉ 复制为新工作流", command=self.duplicate_workflow,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(5, 15))
        start_label = ttk.Frame(workflow_meta_bar, style="Workspace.TFrame")
        start_label.pack(side="left")
        start_label_text = ttk.Label(start_label, text="开始时间")
        start_label_text.pack(side="left")
        self._attach_tip(
            start_label_text,
            "留空表示手动运行；设置后到达指定时间自动开始当前工作流。",
        )
        ttk.Entry(workflow_meta_bar, textvariable=self.workflow_start_var, width=20, state="readonly").pack(side="left", padx=pad(8, 4))
        ttk.Button(workflow_meta_bar, text="📅 选择", command=self.choose_workflow_start,
                   style="CompactGhost.TButton").pack(side="left")
        workflow_action_bar = ttk.Frame(header, style="Workspace.TFrame")
        workflow_action_bar.pack(fill="x", pady=pad(8, 0))
        ttk.Button(
            workflow_action_bar, text="运行工作流    F10", command=self.run_workflow,
            bootstyle="success",
        ).pack(side="right")
        self.workflow_pause_button = ttk.Button(
            workflow_action_bar, text="暂停", command=self.toggle_execution_pause,
            style="CompactGhost.TButton", state="disabled",
        )
        self.workflow_pause_button.pack(side="right", padx=pad(0, 8))
        ttk.Checkbutton(
            workflow_action_bar, text="测试模式", variable=self.workflow_test_mode_var,
            bootstyle="round-toggle",
        ).pack(side="right", padx=pad(0, 8))
        ttk.Button(workflow_action_bar, text="从选中行运行", command=self.run_workflow_from_selected,
                   style="CompactGhost.TButton").pack(side="right", padx=pad(0, 8))

        start_delay_bar = ttk.Frame(self.workflow_tab, padding=pad(16, 0, 16, 8), style="Workspace.TFrame")
        start_delay_bar.pack(fill="x")
        ttk.Checkbutton(
            start_delay_bar, text="启动延时", variable=self.workflow_start_delay_enabled_var,
            command=self._toggle_workflow_start_delay_control, bootstyle="round-toggle",
        ).pack(side="left")
        self.workflow_start_delay_entry = ttk.Entry(
            start_delay_bar, textvariable=self.workflow_start_delay_seconds_var, width=8,
        )
        self.workflow_start_delay_entry.pack(side="left", padx=pad(8, 5))
        self.workflow_start_delay_entry.bind("<KeyRelease>", self._schedule_workflow_draft_save)
        ttk.Combobox(
            start_delay_bar, textvariable=self.workflow_start_delay_seconds_var.unit,
            values=TIME_UNITS, state="readonly", width=4,
        ).pack(side="left", padx=pad(0, 5))
        ttk.Label(start_delay_bar, text="后开始（从头运行和从选中行运行均生效）",
                  style="Muted.TLabel").pack(side="left")
        self._toggle_workflow_start_delay_control(persist=False)

        resolution_bar = ttk.Frame(self.workflow_tab, padding=pad(16, 0, 16, 8), style="Workspace.TFrame")
        resolution_bar.pack(fill="x")
        ttk.Label(resolution_bar, text="启动时分辨率/缩放").pack(side="left")
        start_resolution_field = ttk.Frame(resolution_bar)
        start_resolution_field.pack(side="left", padx=pad(6, 10))
        self.workflow_start_resolution_combo = ttk.Combobox(
            start_resolution_field, textvariable=self.workflow_start_resolution_var,
            state="readonly", width=14, postcommand=self._refresh_workflow_resolution_options,
        )
        self.workflow_start_resolution_combo.pack(fill="x")
        attach_combobox_scrollbar(start_resolution_field, self.workflow_start_resolution_combo)
        self.workflow_start_resolution_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._set_workflow_resolution("start"),
        )
        ttk.Label(resolution_bar, text="结束或停止后恢复为").pack(side="left")
        end_resolution_field = ttk.Frame(resolution_bar)
        end_resolution_field.pack(side="left", padx=pad(8, 0))
        self.workflow_end_resolution_combo = ttk.Combobox(
            end_resolution_field, textvariable=self.workflow_end_resolution_var,
            state="readonly", width=14, postcommand=self._refresh_workflow_resolution_options,
        )
        self.workflow_end_resolution_combo.pack(fill="x")
        attach_combobox_scrollbar(end_resolution_field, self.workflow_end_resolution_combo)
        self.workflow_end_resolution_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._set_workflow_resolution("end"),
        )
        ttk.Button(
            resolution_bar, text="编辑分辨率样式", command=self._configure_resolution_styles,
            style="CompactGhost.TButton",
        ).pack(side="left", padx=pad(10, 0))
        self._refresh_workflow_resolution_options()

        restart_default_bar = ttk.Frame(self.workflow_tab, padding=pad(16, 0, 16, 8), style="Workspace.TFrame")
        restart_default_bar.pack(fill="x")
        ttk.Label(restart_default_bar, text="重新执行默认跳转行").pack(side="left")
        restart_field = ttk.Frame(restart_default_bar)
        restart_field.pack(side="left", padx=pad(8, 0))
        self.workflow_restart_default_combo = ttk.Combobox(
            restart_field, state="readonly", width=22,
        )
        self.workflow_restart_default_combo.pack(fill="x")
        attach_combobox_scrollbar(restart_field, self.workflow_restart_default_combo)
        self.workflow_restart_default_combo.bind(
            "<<ComboboxSelected>>", self._apply_workflow_restart_default,
        )
        ttk.Label(
            restart_default_bar,
            text="「重新执行工作流」动作未指定行时，从这里开始（未设置则第 1 行）",
            style="Muted.TLabel",
        ).pack(side="left", padx=pad(8, 0))
        self._sync_workflow_restart_default_ui()

        # Global modules and workflow steps share a draggable vertical split.
        self.workflow_content_pane = ttk.Panedwindow(self.workflow_tab, orient="vertical")
        self.workflow_content_pane.pack(fill="both", expand=True)

        # Global module box (top, independent numbering from 1)
        global_box = ttk.Frame(self.workflow_content_pane, padding=pad(16, 0, 16, 6), style="Surface.TFrame")
        self.workflow_content_pane.add(global_box, weight=2)
        global_header = ttk.Frame(global_box, style="Surface.TFrame")
        global_header.pack(fill="x", pady=pad(10, 6))
        ttk.Label(global_header, text="工作流全局模块", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(global_header, text="独立编号 · 执行时启用全局检测", style="Muted.TLabel").pack(
            side="left", padx=pad(8, 0),
        )
        global_toolbar = ttk.Frame(global_box, style="Surface.TFrame")
        global_toolbar.pack(fill="x", pady=pad(0, 6))
        ttk.Button(
            global_toolbar, text="添加工作流全局模块", command=self.add_workflow_global_module,
            bootstyle="primary-outline",
        ).pack(side="left")
        ttk.Button(
            global_toolbar, text="添加插件", command=self.add_workflow_plugin,
            bootstyle="primary-outline",
        ).pack(side="left", padx=px(5))
        ttk.Button(
            global_toolbar, text="进程名库", command=lambda: ProcessLibraryDialog(self.root).show(),
            style="CompactGhost.TButton",
        ).pack(side="left")
        ttk.Button(
            global_toolbar, text="编辑选中", command=self.edit_selected_global_module,
            style="CompactGhost.TButton",
        ).pack(side="left", padx=px(5))
        ttk.Button(
            global_toolbar, text="启用/禁用", command=self.toggle_selected_global_module,
            style="CompactGhost.TButton",
        ).pack(side="left")
        ttk.Button(
            global_toolbar, text="删除", command=self.delete_global_module,
            bootstyle="danger-outline",
        ).pack(side="left", padx=px(5))
        self.global_delete_undo_button = ttk.Button(
            global_toolbar, text="↶ 撤销删除", command=self.undo_delete_global_module,
            style="CompactGhost.TButton", state="disabled",
        )
        self.global_delete_undo_button.pack(side="left")
        ttk.Label(
            global_toolbar, text="双击行编辑插件 / 更换模块", style="Muted.TLabel",
        ).pack(side="left", padx=pad(10, 0))
        global_tree_frame = ttk.Frame(global_box, style="Surface.TFrame")
        # 撑满整个上窗格：原来只按 6 行高布局，窗格空着却出现滚动条。
        global_tree_frame.pack(fill="both", expand=True, pady=pad(0, 10))
        self.global_tree = ttk.Treeview(
            global_tree_frame, columns=("index", "module", "status"),
            show="headings", selectmode="extended", style="Workflow.Treeview", height=6,
        )
        self._apply_column_widths(self.global_tree, GLOBAL_TREE_COLUMNS, "module")
        global_scroll = ttk.Scrollbar(global_tree_frame, bootstyle="dark", orient="vertical", command=self.global_tree.yview)
        attach_autohide_scrollbar(self.global_tree, global_scroll)
        self.global_tree.tag_configure("disabled", foreground="#F2B84B", background="#2B2418")
        self.global_tree.tag_configure("global", foreground="#7BC96F", background="#14261B")
        self.global_tree.pack(side="left", fill="both", expand=True)
        global_scroll.pack(side="right", fill="y")
        self.empty_global_hint = ttk.Label(
            global_tree_frame, text="还没有工作流全局模块\n添加模块，或点击“添加插件”配置进程缺失重启",
            style="Empty.TLabel", anchor="center", justify="center",
        )
        self.global_tree.bind("<Double-1>", lambda _event: self.edit_selected_global_module())
        self.global_tree.bind("<Control-a>", self._select_all_global_modules)
        self.global_tree.bind("<Button-3>", self._show_global_context_menu, add="+")

        # Workflow box (bottom, independent numbering from 1)
        workflow_box = ttk.Frame(self.workflow_content_pane, padding=pad(16, 0, 16, 12), style="Surface.TFrame")
        self.workflow_content_pane.add(workflow_box, weight=3)
        workflow_header = ttk.Frame(workflow_box, style="Surface.TFrame")
        workflow_header.pack(fill="x", pady=pad(8, 0))
        ttk.Label(workflow_header, text="工作流", style="PageTitle.TLabel").pack(side="left")
        ttk.Label(workflow_header, text="独立编号 · 从 1 开始", style="Muted.TLabel").pack(
            side="left", padx=pad(8, 0),
        )
        toolbar = ttk.Frame(workflow_box, padding=pad(0, 4, 0, 8), style="Surface.TFrame")
        toolbar.pack(fill="x")
        file_toolbar = ttk.Frame(toolbar, style="Surface.TFrame")
        file_toolbar.pack(fill="x")
        ttk.Button(file_toolbar, text="新建", command=self.new_workflow, style="CompactGhost.TButton").pack(side="left")
        ttk.Button(file_toolbar, text="打开", command=self.open_workflow, style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        workflow_save_button = ttk.Button(file_toolbar, text="保存", command=self.save_current_workflow,
                                         bootstyle="primary", state='disabled')
        workflow_save_button.pack(side="left", padx=pad(5, 0))
        from macroflow.core.models import Workflow
        from macroflow.core.storage import load_workflow
        from macroflow.ui.save_state import SaveState
        saved_workflow = Workflow()
        if self.workflow_path and self.workflow_path.is_file():
            try:
                saved_workflow = load_workflow(self.workflow_path)
            except (OSError, ValueError):
                pass
        self._workflow_save_state = SaveState(file_toolbar, workflow_save_button,
            self._workflow_save_values, baseline=vars(saved_workflow))
        self._workflow_save_state.watch(self.workflow_name_var, self.workflow_start_var,
            self.workflow_start_delay_enabled_var, self.workflow_start_delay_seconds_var)
        ttk.Button(file_toolbar, text="打开工作流目录", command=lambda: self.open_folder(WORKFLOWS_DIR), style="CompactGhost.TButton").pack(side="right")

        add_toolbar = ttk.Frame(toolbar, style="Surface.TFrame")
        add_toolbar.pack(fill="x", pady=pad(7, 0))
        ttk.Label(add_toolbar, text="添加 / 插入", style="Muted.TLabel").pack(side="left", padx=pad(0, 8))
        ttk.Button(add_toolbar, text="添加当前脚本", command=self.add_current_script_step, style="CompactGhost.TButton").pack(side="left")
        ttk.Button(add_toolbar, text="选择已有脚本", command=self.add_script_step, style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(add_toolbar, text="添加模块", command=self.add_workflow_module_step,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Separator(add_toolbar, orient="vertical").pack(side="left", fill="y", padx=px(10))
        ttk.Label(add_toolbar, text="插入位置", style="Muted.TLabel").pack(side="left", padx=pad(0, 6))
        self.workflow_insert_above_button = ttk.Button(
            add_toolbar, text="▲ 上方", width=6,
            command=lambda: self._set_workflow_insert_position(True),
            style="CompactGhost.TButton",
        )
        self.workflow_insert_above_button.pack(side="left")
        self.workflow_insert_below_button = ttk.Button(
            add_toolbar, text="▼ 下方", width=6,
            command=lambda: self._set_workflow_insert_position(False),
            style="CompactGhost.TButton",
        )
        self.workflow_insert_below_button.pack(side="left", padx=pad(5, 0))
        ttk.Button(add_toolbar, text="插入脚本", command=self.insert_workflow_step,
                   bootstyle="primary-outline").pack(side="left", padx=pad(6, 0))
        ttk.Button(add_toolbar, text="插入模块", command=self.insert_workflow_module_step,
                   bootstyle="primary-outline").pack(side="left", padx=pad(4, 0))
        self._set_workflow_insert_position(False)

        edit_toolbar = ttk.Frame(toolbar, style="Surface.TFrame")
        edit_toolbar.pack(fill="x", pady=pad(5, 0))
        ttk.Label(edit_toolbar, text="编辑 / 排序", style="Muted.TLabel").pack(side="left", padx=pad(0, 8))
        ttk.Button(edit_toolbar, text="统一设置参数", command=self.set_all_workflow_step_options,
                   style="CompactGhost.TButton").pack(side="left")
        ttk.Button(edit_toolbar, text="恢复全部次数", command=self.restore_all_workflow_counts,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="启用/禁用", command=self.toggle_selected_workflow_step,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="上移", command=lambda: self.move_workflow_step(-1), style="CompactGhost.TButton").pack(side="left", padx=pad(5, 2))
        ttk.Button(edit_toolbar, text="下移", command=lambda: self.move_workflow_step(1), style="CompactGhost.TButton").pack(side="left")
        ttk.Button(edit_toolbar, text="移到行…", command=self.move_workflow_step_to_row,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="删除", command=self.delete_workflow_step, bootstyle="danger-outline").pack(side="left", padx=px(5))
        self.workflow_delete_undo_button = ttk.Button(
            edit_toolbar, text="↶ 撤销删除", command=self.undo_delete_workflow_step,
            style="CompactGhost.TButton", state="disabled",
        )
        self.workflow_delete_undo_button.pack(side="left")
        ttk.Label(edit_toolbar, text="提示：双击单元格可直接修改", style="Muted.TLabel").pack(side="right")

        frame = ttk.Frame(workflow_box, style="Surface.TFrame")
        frame.pack(fill="both", expand=True)
        self.workflow_tree = ttk.Treeview(
            frame,
            columns=("mark", "index", "script", "original", "repeat", "restore", "before", "interval", "enabled"),
            show="headings", selectmode="extended", style="Workflow.Treeview", height=10,
        )
        self._apply_column_widths(self.workflow_tree, WORKFLOW_TREE_COLUMNS, "script")
        scroll = ttk.Scrollbar(frame, bootstyle="dark", orient="vertical", command=self.workflow_tree.yview)
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
            style="Empty.TLabel", anchor="center", justify="center"
        )
        self.workflow_tree.bind("<ButtonPress-1>", self._workflow_drag_start, add="+")
        self.workflow_tree.bind("<B1-Motion>", self._workflow_drag_motion, add="+")
        self.workflow_tree.bind("<ButtonRelease-1>", self._workflow_drag_end, add="+")
        self.workflow_tree.bind("<ButtonRelease-1>", self._click_workflow_restore, add="+")
        self.workflow_tree.bind("<Double-1>", self._edit_workflow_cell, add="+")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._update_workflow_selection_color, add="+")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._refresh_workflow_segment_bar, add="+")
        self.workflow_tree.bind("<Control-a>", self._select_all_workflow_steps)
        self.workflow_tree.bind("<Button-3>", self._show_workflow_context_menu, add="+")


    def _create_variables(self):
        super()._create_variables()
        self.key_search_var = tk.StringVar(value="")
        self._key_search_query_kind = ""
        self.key_search_var.trace_add("write", self._clear_captured_search_query_kind)
        self.key_search_state_var = tk.StringVar(value="全部")
        self.key_search_delay_var = tk.StringVar(value="0")
        self.key_search_match_var = tk.StringVar(value="")
        self._input_search_capturer = None
