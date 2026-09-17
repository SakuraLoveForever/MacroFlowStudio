from __future__ import annotations

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
from macroflow.ui.dialogs.actions import ClickDialog, CloseAppDialog, DurationDialog, GameSetupNoteDialog, JsonActionDialog, JumpActionDialog, KeyActionDialog, MouseMoveDialog, OpenAppDialog, RepeatClickDialog, ScheduleDialog, ScrollDialog, SetResolutionActionDialog, TurnActionDialog, edit_action
from macroflow.ui.dialogs.app_dialogs import HotkeyScriptsDialog, ResolutionStylesDialog, ScriptDirectoriesDialog, WindowPicker, WorkflowBatchSettingsDialog, WorkflowRepeatDialog
from macroflow.ui.dialogs.base import DurationVar, TIME_UNITS, Tooltip, key_to_vk, show_floating_notice, vk_to_key_name
from macroflow.ui.dialogs.helpers import recorded_action_description, workflow_step_label
from macroflow.ui.dialogs.module_objects import ModulePickerDialog, TemplateRegionFormDialog, TemplateRegionManagerDialog
from macroflow.ui.dialogs.recognition import GlobalDetectDialog, MultiConditionClickDialog, OcrCompareActionDialog, RowListConditionClickDialog, RowListDiagnosticResultDialog, RowRecognitionResultDialog
from macroflow.input.input_guard import (
    FocusInputGuard, InputCapturer, KeyCapturer, RESERVED_HOTKEY_VKS,
)
from macroflow.execution.player import (
    JUMP_CURRENT_SCRIPT_LAST_RESULT, MAX_SCRIPT_REF_DEPTH,
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest,
    JumpToCurrentScriptLastAction, MacroPlayer, PlaybackStopped,
    screen_template_scale,
)
from macroflow.input.recorder import MacroRecorder
from pathlib import Path
from macroflow.ui.update_queue import UIUpdateQueue
from macroflow.input.wininput import (
    WindowInfo, activate_window, enum_windows, get_cursor_pos,
    get_monitor_rect_for_window, get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect, get_virtual_screen_rect,
    get_window_dpi,
    force_english_input, get_foreground_window_info, get_window_rect,
    is_current_process_window, is_window, is_window_process_foreground,
    make_window_no_activate, send_button, send_move_absolute,
    set_dark_titlebar, set_rounded_window, show_window,
    show_window_no_activate,
)
from datetime import datetime
from macroflow.ui import dialogs as dialogs_ui
import os
import pystray
from macroflow.core.resolution import resolution_styles_from_settings
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import ttkbootstrap as ttk

from .base import (
    apply_pointer_cursors,
    attach_autohide_scrollbar,
    bind_tree_hover,
    coordinate_scale_summary,
    default_main_geometry,
    disable_combobox_wheel_selection,
    pad,
    px,
    set_ui_scale,
    split_toolbar_specs,
)
from .constants import (
    ADD_ACTION_MENU_LABEL,
    ACTION_TREE_COLUMNS,
    APP_NAME,
    APP_VERSION,
    BACKUP_INTERVAL_CHOICES,
    COLOR_BG,
    COLOR_BLUE,
    COLOR_BORDER,
    COLOR_FOCUS,
    COLOR_GREEN,
    COLOR_HOVER,
    COLOR_MUTED,
    COLOR_RED,
    COLOR_SIDEBAR,
    COLOR_SURFACE,
    COLOR_SURFACE_ALT,
    COLOR_TEXT,
    FLOATING_NOTICE_POSITIONS,
    PRIMARY_ACTION_COMMANDS,
    FONT_BODY,
    FONT_BRAND,
    FONT_FAMILY,
    FONT_MONO,
    FONT_SMALL,
    FONT_SUBTITLE,
    FONT_TITLE,
    GLOBAL_TREE_COLUMNS,
    MIN_MAIN_HEIGHT,
    MIN_MAIN_WIDTH,
    RECORD_TOOLBAR_BUTTON_LABEL,
    SCRIPT_CATEGORY_VALUES,
    WORKFLOW_TREE_COLUMNS,
)
from .startup import (
    main,
)

class ShellMixin:
    """主窗口壳层：主题、变量、侧栏与各标签页、日志视图。"""

    LOG_VIEW_LIMIT = 200_000
    @staticmethod
    def _actions_need_bound_window(actions: list[dict]) -> bool:
        return any(
            str(action.get("type")) in {"turn", "mouse_move"}
            for action in actions
        )
    def __init__(self):
        ensure_dirs()
        migrate_workflow_templates()
        started_at = datetime.now()
        self.logs_dir = BASE_DIR / "logs"
        session_logs_dir = self.logs_dir / started_at.strftime("%Y-%m-%d")
        session_logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = f"{started_at.strftime('%H-%M-%S-%f')[:-3]}_{os.getpid()}"
        # 两个分开的日志：
        #   MacroFlow_*.log        事件日志：状态变化/边界/异常（重复行合并）
        #   MacroFlow_trace_*.log  执行明细：每一次脚本动作执行一行
        self.session_log_path = session_logs_dir / f"MacroFlow_{stamp}.log"
        self.trace_log_path = session_logs_dir / f"MacroFlow_trace_{stamp}.log"
        self.log_file_lock = threading.Lock()
        self.trace_file_lock = threading.Lock()
        # 当前执行位置（工作流第几步 / 哪个脚本 / 第几次重复）：两级日志都靠它
        # 标注上下文，见 _set_trace_context / _with_event_context。
        self._trace_context_state: dict = {}
        # 事件日志去重：长时间重复的同一句话只留一行 + 次数。
        self._log_dedup_window_ms = 30000
        self._log_dedup_text = ""
        self._log_dedup_count = 0
        self._log_dedup_since = 0.0
        self.root = ttk.Window(themename="darkly")
        # DPI 缩放必须在建界面之前算好：所有像素常量都按它换算。
        set_ui_scale(self.root)
        dialogs_ui.set_ui_scale(self.root)
        self.root._macroflow_app = self
        # 33ms ≈ 30fps：状态/日志刷新的感知延迟更低，CPU 开销仍可忽略。
        self.ui_queue = UIUpdateQueue(self.root, interval_ms=33)
        self.root.title(f"{APP_NAME}  {APP_VERSION}")
        self.root.geometry(default_main_geometry())
        self.root.minsize(px(MIN_MAIN_WIDTH), px(MIN_MAIN_HEIGHT))
        self.root.option_add("*Font", (FONT_FAMILY, FONT_BODY))
        disable_combobox_wheel_selection(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._configure_dark_theme()
        self.root.update_idletasks()
        set_dark_titlebar(self.root.winfo_id())
        # 映射后再设一次：部分 Windows 11 版本只在窗口已有 HWND 后接受
        # DWMWA_WINDOW_CORNER_PREFERENCE（圆角）；最大化时系统本身不圆角。
        self.root.after(120, lambda: set_dark_titlebar(self.root.winfo_id()))

        self.app_settings = load_app_settings()
        self._restore_main_window_geometry()
        self._apply_startup_window_state()
        self._sync_ui_scale_to_monitor()
        # 游戏设置说明（用户可编辑的使用前参数清单）：随 app_settings 持久化。
        self._game_setup_note = self.app_settings.get("game_setup_note")
        # 快捷键脚本绑定：录制与执行过程中按快捷键立即执行绑定的脚本。
        self.hotkey_scripts = self._normalize_hotkey_scripts(
            self.app_settings.get("hotkey_scripts"),
        )
        self._hotkey_vk_map: dict[int, dict] = {}
        self._hotkey_recorder_filter_vks: set[int] = set()
        self._hotkey_pressed: set[int] = set()
        self._hotkey_script_running = False
        self.hotkey_config_open = False
        saved_mini_position = self.app_settings.get("execution_mini_position")
        try:
            self.execution_mini_position = (
                [int(saved_mini_position[0]), int(saved_mini_position[1])]
                if isinstance(saved_mini_position, (list, tuple)) and len(saved_mini_position) == 2
                else []
            )
        except (TypeError, ValueError):
            self.execution_mini_position = []
        self.execution_mini_position_editor = None
        draft_signature = self.app_settings.get("activation_window_draft")
        self.activation_draft_signature: dict[str, str] | None = (
            {
                "title": str(draft_signature.get("title", "")),
                "class_name": str(draft_signature.get("class_name", "")),
                "process_path": str(draft_signature.get("process_path", "")),
            }
            if isinstance(draft_signature, dict) and draft_signature.get("title")
            else None
        )
        self.activation_draft_enabled = bool(
            self.app_settings.get("activation_window_draft_enabled", False)
        )
        self.script = self._blank_script_with_activation_draft()
        self.action_undo_stack: list[list[dict]] = []
        self.action_redo_stack: list[list[dict]] = []
        self.undo_open_stack: list[dict] = []
        self.script_path: Path | None = None
        self.script_requires_new_file = False
        saved_workflow = self.app_settings.get("workflow_draft")
        self.workflow = Workflow.from_dict(saved_workflow) if isinstance(saved_workflow, dict) else Workflow()
        saved_workflow_path = str(self.app_settings.get("workflow_path", "")).strip()
        self.workflow_path: Path | None = resolve_path(saved_workflow_path) if saved_workflow_path else None
        self.workflow_drag_index: int | None = None
        # 左边那根实心竖条已经画到哪一段（None = 没有画过的行）。
        self.action_segment_painted: tuple[int, int] | None = None
        self.workflow_segment_painted: tuple[int, int] | None = None
        self.workflow_was_dragged = False
        self.workflow_delete_undo_stack: list[tuple[int, dict]] = []
        self.global_delete_undo_stack: list[tuple[int, dict]] = []
        self.workflow_draft_after_id = None
        self.workflow_insert_position_var = tk.StringVar(value="below")
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
        self.bound_window: WindowInfo | None = None
        saved_binding = self.app_settings.get("bound_window")
        self.saved_window_signature: dict[str, str] | None = (
            {
                "title": str(saved_binding.get("title", "")),
                "class_name": str(saved_binding.get("class_name", "")),
                "process_path": str(saved_binding.get("process_path", "")),
                "window_rect": tuple(saved_binding.get("window_rect", (0, 0, 0, 0))),
                "client_size": tuple(saved_binding.get("client_size", (0, 0))),
            }
            if isinstance(saved_binding, dict) and saved_binding.get("title")
            else None
        )
        # 执行时仍以脚本自己的设置为准；draft 独立保存最近选择，不能在
        # 被动打开其他脚本时被当前脚本的空设置覆盖。
        self.activation_window: WindowInfo | None = None
        self.saved_activation_signature: dict[str, str] | None = None
        self.recorder = MacroRecorder(self._record_action_callback)
        self.player = MacroPlayer(
            self._player_status_callback,
            on_notice=self._player_notice_callback,
            on_global_detect_request=self._activate_global_detect_from_config,
            on_restart_workflow_request=self._on_restart_workflow_request,
            on_log=lambda text: self._ui(self._log, text),
            on_trace=self._on_player_trace,
            on_trace_line=self._trace_event,
            on_script_scope_enter=self._enter_script_global_scope,
            on_script_scope_exit=self._exit_script_global_scope,
            on_target_window_request=lambda: self._bound_hwnd(update_display=False),
            on_guard_poll=self._evaluate_global_guards,
            on_ocr_engine_wait=self._wait_ocr_ready,
            on_resolution_monitor_request=self._resolution_monitor_hwnd,
        )
        self.input_guard = FocusInputGuard(
            lambda: self._ui(self.stop_all),
            on_hotkey=self._on_hotkey_vk,
        )
        # 快捷键脚本专用播放器：独立于主播放器，可与录制/主脚本执行并行。
        # 只按纯动作方式回放（不注册全局守卫、不操作主执行界面）。
        self.hotkey_player = MacroPlayer(
            on_notice=self._player_notice_callback,
            on_log=lambda text: self._ui(self._log, f"[快捷键] {text}"),
            on_trace=self._on_player_trace,
            on_trace_line=lambda text: self._trace_event(f"[快捷键] {text}"),
            on_ocr_engine_wait=self._hotkey_wait_ocr_ready,
        )
        self.workflow_stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.current_workflow_step_index: int | None = None
        self.current_workflow_repeat_index: int = 0
        self.current_workflow_action_index: int = 0
        # 守卫引擎：全局检测不再是后台线程，而是由播放器在动作边界与等待
        # 期间评估的守卫数据。工作流全局模块按 step_id、脚本全局动作按
        # action_id 登记，互不替换、同时生效；生命周期 = 一次执行。
        self.global_guards: dict[str, dict] = {}
        self.guards_lock = threading.Lock()
        self._detection_run_id = 0
        self._guard_config_version = 0
        self._detection_request: tuple[int, int] | None = None
        self._detection_worker = None
        # 同一共享截图命中的守卫按注册顺序排队，播放器逐个执行处理段。
        self._pending_global_guard_hits: list[dict] = []
        # 触发后跨执行保留的重新武装锁；新守卫确认图片消失后才允许再次触发。
        self.global_detect_rearm_locks: set[str] = set()
        self.global_detect_trigger_count = 0
        # 单独执行（F9）全局脚本时的语句体回放参数：触发条件满足后重新播放语句体。
        self.standalone_global_replay: dict | None = None
        # 特殊模块「重新执行工作流」：标志 + 目标行（1 基，运行时按
        # 动作 → 工作流默认解析）。重启沿用当前步骤对象中的剩余次数。
        self.workflow_restart_requested = False
        self.workflow_restart_target_row = 1
        self.workflow_test_mode_active = False
        self._row_list_diagnostic_running = False
        self._row_list_diagnostic_kind = ""
        self._row_list_diagnostic_grab_window = None
        self.dirty = False
        self.mini_window: tk.Toplevel | None = None
        self.mini_elapsed_var = tk.StringVar(value="00:00")
        self.mini_count_var = tk.StringVar(value="0 个动作")
        self.mini_ocr_progress_var = tk.DoubleVar(value=0)
        self.mini_ocr_progressbar: ttk.Progressbar | None = None
        self.mini_context_var = tk.StringVar(value="")
        self.mini_window_var = tk.StringVar(value="当前窗口：未知")
        self.mini_mode = ""
        self.mini_steps_text: tk.Text | None = None
        self.mini_update_after_id = None
        self.mini_binding_label = None
        self.bind_label_widget = None
        self.record_started_at = 0.0
        self.execution_started_at = 0.0
        self.execution_progress_text = ""
        self.execution_focus_requested = False
        self.execution_notice_window: tk.Toplevel | None = None
        self.execution_notice_label = None
        self.execution_notice_after_id = None
        self.root._macroflow_notice_callback = self._show_execution_notice
        self.recording_capture_mode = ""
        self.recording_screen: dict[str, int] | None = None
        self.cursor_tracking = False
        self.cursor_tracking_after_id = None
        self.cursor_tracking_mini: tk.Toplevel | None = None
        self.main_hidden_for_cursor_tracking = False
        self.tray_icon: pystray.Icon | None = None
        self._tray_lock = threading.RLock()
        self.tray_warmup_thread: threading.Thread | None = None
        self.main_hidden_to_tray = False
        self.main_hidden_for_recording = False
        self.main_hidden_for_execution = False
        self.execution_should_remain_in_tray = False
        self.backup_after_id = None
        self.backup_running = False
        self.exiting = False

        self._create_variables()
        self._refresh_resolution_styles_summary()
        self.player.set_playback_speed(self.playback_speed_var.get())
        self.hotkey_player.set_playback_speed(self.playback_speed_var.get())
        self._build_ui()
        self.ui_queue.start()
        self._restore_saved_window_binding()
        self._sync_activation_ui_from_script()
        self.rebuild_action_tree()
        self.rebuild_workflow_tree()
        self._start_hotkeys()
        self._apply_hotkey_bindings()
        self._refresh_hotkey_summary()
        self.refresh_script_files()
        self.refresh_workflow_files()
        self._log("应用已就绪。F8 录制/停止，F9 执行当前脚本，F12 紧急停止。")
        self._set_status("就绪", "success")
        self._sync_windows_startup(log_errors=True)
        self._schedule_timed_backup()
        explicit_editor_start = bool(
            self.startup_new_script or self.startup_open_script or self.startup_edit_module
        )
        if self.start_minimized_to_tray_var.get() and not explicit_editor_start:
            self.root.after(120, self._hide_main_to_tray)
        else:
            # Some Windows launchers propagate SW_HIDE to the first created window.
            self.root.after(120, self._ensure_startup_visible)
        self.root.after_idle(self._start_execution_prewarm)
        self.root.after(1200, self._watch_display_dpi)
        if self.startup_open_script:
            self.root.after(300, lambda: self._load_startup_script(resolve_path(self.startup_open_script)))
        if self.startup_edit_module:
            self.root.after(300, lambda: self._open_module_object_editor(self.startup_edit_module))
        if self.startup_run_workflow_var.get() and not explicit_editor_start:
            self.root.after(700, self._run_configured_startup_workflow)
        if not explicit_editor_start:
            # 恢复上次关闭时脚本编辑页的完整状态（含未保存动作）。
            self.root.after(400, self._restore_last_editor_state)
    def _configure_dark_theme(self):
        style = self.root.style
        self.root.configure(background=COLOR_BG)
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Workspace.TFrame", background=COLOR_BG)
        style.configure("Sidebar.TFrame", background=COLOR_SIDEBAR)
        style.configure("Surface.TFrame", background=COLOR_SURFACE)
        style.configure("Toolbar.TFrame", background=COLOR_BG)
        style.configure("Status.TFrame", background=COLOR_BG)

        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                        font=(FONT_FAMILY, FONT_BODY))
        style.configure("Sidebar.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_TEXT)
        style.configure("Brand.TLabel", background=COLOR_SIDEBAR, foreground="#FFFFFF",
                        font=(FONT_FAMILY, FONT_BRAND, "bold"))
        style.configure("Muted.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
                        font=(FONT_FAMILY, FONT_SMALL))
        style.configure("SidebarMuted.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_MUTED,
                        font=(FONT_FAMILY, FONT_SMALL))
        style.configure("Section.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_TEXT,
                        font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
        style.configure("SidebarSection.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_TEXT,
                        font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
        style.configure("PageTitle.TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                        font=(FONT_FAMILY, FONT_TITLE, "bold"))
        style.configure("Empty.TLabel", background=COLOR_SURFACE, foreground=COLOR_MUTED,
                        font=(FONT_FAMILY, FONT_SUBTITLE))
        style.configure("StatusText.TLabel", background=COLOR_BG, foreground=COLOR_GREEN,
                        font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.configure("MiniTitle.TLabel", background=COLOR_SURFACE, foreground=COLOR_RED,
                        font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
        style.configure("MiniText.TLabel", background=COLOR_SURFACE, foreground=COLOR_MUTED,
                        font=(FONT_FAMILY, FONT_SMALL))
        style.configure("MiniTime.TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT,
                        font=(FONT_MONO, FONT_TITLE, "bold"))
        style.configure("MiniWarning.TLabel", background=COLOR_SURFACE, foreground=COLOR_RED,
                        font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.configure("GlobalMarker.TLabel", background=COLOR_BG, foreground="#7BC96F",
                        font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.configure("GlobalTrigger.TFrame", background=COLOR_SURFACE)
        style.configure("GlobalTriggerTitle.TLabel", background=COLOR_SURFACE,
                        foreground=COLOR_TEXT, font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.configure("GlobalTriggerSummary.TLabel", background=COLOR_SURFACE,
                        foreground=COLOR_TEXT, font=(FONT_FAMILY, FONT_BODY))

        style.configure("TEntry", fieldbackground=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
                        insertcolor=COLOR_TEXT, padding=px(3))
        style.map("TEntry", bordercolor=[("focus", COLOR_FOCUS)],
                  lightcolor=[("focus", COLOR_FOCUS)], darkcolor=[("focus", COLOR_FOCUS)])
        style.configure("TSpinbox", fieldbackground=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, arrowcolor=COLOR_MUTED, padding=px(2))
        style.configure("TCombobox", fieldbackground=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, arrowcolor=COLOR_MUTED, padding=px(2))
        style.map("TCombobox", bordercolor=[("focus", COLOR_FOCUS)],
                  lightcolor=[("focus", COLOR_FOCUS)], darkcolor=[("focus", COLOR_FOCUS)])
        # ttkbootstrap 会把 "TSeparator" 当成新样式去派生（内部 element_create 一个
        # 全局元素），第二次 configure 就抛 "Duplicate element Horizontal.Separator
        # .separator"。这里只设与 DPI 无关的颜色，配一次即可——本方法会在 DPI 变化
        # 时重跑（见 _sync_ui_scale_to_monitor / _apply_display_dpi）。
        if not getattr(self, "_separator_style_configured", False):
            self._separator_style_configured = True
            style.configure("TSeparator", background=COLOR_BORDER)
        style.configure("TButton", padding=pad(8, 3), font=(FONT_FAMILY, FONT_BODY))
        style.configure("TCheckbutton", padding=pad(1, 1), font=(FONT_FAMILY, FONT_BODY))
        style.map("TCheckbutton",
                  background=[("active", COLOR_SURFACE_ALT)],
                  foreground=[("active", "#FFFFFF")])
        # ttkbootstrap 的语义按钮（bootstyle=primary/success/danger/…）也要跟着
        # 变紧凑，否则同一界面里两套按钮高度。样式名由 ttkbootstrap 拼接：
        # <Color>.TButton / <Color>.Outline.TButton。
        for color in ("primary", "secondary", "success", "info", "warning", "danger", "light", "dark"):
            style.configure(f"{color.title()}.TButton", padding=pad(8, 3),
                            font=(FONT_FAMILY, FONT_BODY))
            style.configure(f"{color.title()}.Outline.TButton", padding=pad(8, 3),
                            font=(FONT_FAMILY, FONT_BODY))

        style.configure("TNotebook", background=COLOR_BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=COLOR_BG, foreground=COLOR_MUTED,
                        borderwidth=0, padding=pad(12, 5), font=(FONT_FAMILY, FONT_BODY))
        style.map("TNotebook.Tab",
                  # 选中的页签用略亮的卡片底色，层次比“只有文字变色”更清楚。
                  background=[("selected", COLOR_SURFACE), ("active", COLOR_SURFACE_ALT)],
                  foreground=[("selected", COLOR_TEXT), ("active", COLOR_TEXT)],
                  lightcolor=[("selected", COLOR_BLUE)], bordercolor=[("selected", COLOR_BLUE)])

        # 行高按字体实际行高算，避免高 DPI 下文字上下被截（行高必须 > 字体行高）。
        tree_font = tkfont.Font(family=FONT_FAMILY, size=FONT_BODY)
        style.configure("Treeview", background=COLOR_SURFACE, fieldbackground=COLOR_SURFACE,
                        foreground=COLOR_TEXT, bordercolor=COLOR_BORDER,
                        rowheight=tree_font.metrics("linespace") + px(8),
                        font=(FONT_FAMILY, FONT_BODY))
        style.configure("Treeview.Heading", background=COLOR_SURFACE_ALT, foreground=COLOR_MUTED,
                        bordercolor=COLOR_BORDER, relief="flat", padding=pad(6, 4),
                        font=(FONT_FAMILY, FONT_SMALL, "bold"))
        style.map("Treeview", background=[("selected", "#244D78")],
                  foreground=[("selected", "#FFFFFF")])
        style.configure("Workflow.Treeview")
        style.map("Workflow.Treeview", background=[("selected", "#244D78")],
                  foreground=[("selected", "#FFFFFF")])
        style.map("Treeview.Heading", background=[("active", "#24313B")])
        style.configure("Ghost.TButton", background=COLOR_BG, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
                        relief="solid", borderwidth=1, padding=pad(8, 3),
                        font=(FONT_FAMILY, FONT_BODY))
        style.map("Ghost.TButton",
                  background=[("active", COLOR_SURFACE_ALT), ("pressed", "#263541")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")],
                  bordercolor=[("active", "#516170")])
        style.configure("CompactGhost.TButton", background=COLOR_BG, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
                        relief="solid", borderwidth=1, padding=pad(6, 2),
                        font=(FONT_FAMILY, FONT_BODY))
        style.map("CompactGhost.TButton",
                  background=[("active", COLOR_SURFACE_ALT), ("pressed", "#263541")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")],
                  bordercolor=[("active", "#516170")])
        style.configure("ToolGroupTitle.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
                        font=(FONT_FAMILY, FONT_SMALL, "bold"))
        style.configure("SectionCard.TLabelframe", background=COLOR_SURFACE,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER,
                        darkcolor=COLOR_BORDER, relief="solid", borderwidth=1)
        style.configure("SectionCard.TLabelframe.Label", background=COLOR_SURFACE,
                        foreground=COLOR_TEXT, font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.configure("ScriptTool.TButton", background=COLOR_BG, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
                        relief="solid", borderwidth=1, padding=pad(4, 3),
                        font=(FONT_FAMILY, FONT_BODY))
        style.map("ScriptTool.TButton",
                  background=[("active", COLOR_SURFACE_ALT), ("pressed", "#263541")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")],
                  bordercolor=[("active", "#516170")])
        style.configure("AccentScriptTool.TButton", background="#122D48", foreground="#8FC4FF",
                        bordercolor=COLOR_BLUE, lightcolor=COLOR_BLUE, darkcolor=COLOR_BLUE,
                        relief="solid", borderwidth=1, padding=pad(4, 3),
                        font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.map("AccentScriptTool.TButton",
                  background=[("active", "#18426A"), ("pressed", "#205582")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")])
        style.configure("DangerScriptTool.TButton", background=COLOR_BG, foreground="#FF6B6B",
                        bordercolor="#A93636", lightcolor="#A93636", darkcolor="#A93636",
                        relief="solid", borderwidth=1, padding=pad(4, 3),
                        font=(FONT_FAMILY, FONT_BODY))
        style.map("DangerScriptTool.TButton",
                  background=[("active", "#3B1E22"), ("pressed", "#522329")],
                  foreground=[("active", "#FFFFFF")], bordercolor=[("active", COLOR_RED)])
        style.configure("SidebarGhost.TButton", background=COLOR_SIDEBAR, foreground=COLOR_TEXT,
                        bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
                        relief="solid", borderwidth=1, padding=pad(7, 2),
                        font=(FONT_FAMILY, FONT_BODY))
        style.map("SidebarGhost.TButton",
                  background=[("active", COLOR_SURFACE_ALT), ("pressed", "#263541")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")],
                  bordercolor=[("active", "#516170")])

        self.root.option_add("*TCombobox*Listbox*Background", COLOR_SURFACE_ALT)
        self.root.option_add("*TCombobox*Listbox*Foreground", COLOR_TEXT)
    def _create_variables(self):
        # 空白脚本的鼠标轨迹间隔固定从 20 ms 开始。该值属于脚本，不能从
        # 旧版 app_settings 或上一个脚本继承成 100 ms。
        interval = DEFAULT_MOUSE_MOVE_INTERVAL_MS
        try:
            repeat = max(1, min(999999, int(self.app_settings.get("repeat", 1))))
        except (TypeError, ValueError):
            repeat = 1
        self.script_name_var = tk.StringVar(value=self.script.name)
        # Recording is intentionally a single smart mode. Keep the variable so
        # older script/settings files remain compatible without exposing three
        # overlapping choices in the UI.
        self.record_mode_var = tk.StringVar(value="auto")
        self.interval_var = DurationVar(value=interval)
        self.repeat_var = tk.IntVar(value=repeat)
        self.bind_label_var = tk.StringVar(value="未绑定窗口")
        self.activation_enabled_var = tk.BooleanVar(value=False)
        self.activation_label_var = tk.StringVar(value="跟随目标窗口")
        self.cursor_position_var = tk.StringVar(value="光标坐标：尚未读取")
        self.cursor_tracking_mini_var = tk.StringVar(value="X: 0    Y: 0")
        self.status_var = tk.StringVar(value="就绪")
        self.key_search_var = tk.StringVar(value="")
        self._key_search_query_kind = ""
        self.key_search_var.trace_add("write", self._clear_captured_search_query_kind)
        self.key_search_state_var = tk.StringVar(value="全部")
        self.key_search_delay_var = tk.StringVar(value="0")
        self.key_search_match_var = tk.StringVar(value="")
        self._input_search_capturer = None
        self.coordinate_scale_var = tk.StringVar(value=coordinate_scale_summary(
            self.script.settings.get("recorded_screen"), self._playback_reference_screen(),
        ))
        self.record_count_var = tk.StringVar(value="0 个动作")
        self.workflow_name_var = tk.StringVar(value=self.workflow.name)
        self.workflow_start_var = tk.StringVar(value=self.workflow.start_at)
        self.workflow_start_delay_enabled_var = tk.BooleanVar(
            value=bool(self.workflow.start_delay_enabled),
        )
        self.workflow_start_delay_seconds_var = DurationVar(
            value=int(self.workflow.start_delay_seconds) * 1000,
        )
        self.workflow_test_mode_var = tk.BooleanVar(value=False)
        self.sound_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("sound_enabled", True)))
        self.mini_window_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("mini_window_enabled", True)))
        self.execution_mini_enabled_var = tk.BooleanVar(
            value=bool(self.app_settings.get("execution_mini_enabled", True)),
        )
        try:
            playback_speed = round(float(self.app_settings.get("playback_speed", 1.0)), 1)
        except (TypeError, ValueError):
            playback_speed = 1.0
        playback_speed = max(0.5, min(2.0, playback_speed))
        self.playback_speed_var = tk.DoubleVar(value=playback_speed)
        self.playback_speed_label_var = tk.StringVar(value=f"{playback_speed:.1f}×")
        self.focus_mode_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("focus_mode_enabled", False)))
        self.activate_target_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("activate_target_enabled", True)))
        self.resolution_styles = resolution_styles_from_settings(self.app_settings)
        self.resolution_styles_summary_var = tk.StringVar(value="")
        notice_position = str(self.app_settings.get("floating_notice_position", "顶部居中"))
        if notice_position not in FLOATING_NOTICE_POSITIONS:
            notice_position = "顶部居中"
        self.floating_notice_position_var = tk.StringVar(value=notice_position)
        close_action = str(self.app_settings.get("close_action", "exit"))
        self.close_action_var = tk.StringVar(
            value=close_action if close_action in {"exit", "tray"} else "exit",
        )
        self.timed_backup_enabled_var = tk.BooleanVar(
            value=bool(self.app_settings.get("timed_backup_enabled", False)),
        )
        backup_interval = str(self.app_settings.get("backup_interval", "1h"))
        self.backup_interval_var = tk.StringVar(
            value=backup_interval if backup_interval in BACKUP_INTERVAL_CHOICES else "1h",
        )
        self.windows_startup_enabled_var = tk.BooleanVar(
            value=bool(self.app_settings.get("windows_startup_enabled", False)),
        )
        self.start_minimized_to_tray_var = tk.BooleanVar(
            value=bool(self.app_settings.get("start_minimized_to_tray", False)),
        )
        self.startup_run_workflow_var = tk.BooleanVar(
            value=bool(self.app_settings.get("startup_run_workflow", False)),
        )
        self.startup_workflow_path_var = tk.StringVar(
            value=str(self.app_settings.get("startup_workflow_path", "")),
        )
        self.level_scripts_dir_var = tk.StringVar(
            value=str(self.app_settings.get("level_scripts_dir", "scripts/关卡")),
        )
        self.level_pack_scripts_dir_var = tk.StringVar(
            value=str(self.app_settings.get("level_pack_scripts_dir", "scripts/关卡封装")),
        )
        self.switch_scripts_dir_var = tk.StringVar(
            value=str(self.app_settings.get("switch_scripts_dir", "scripts/切换")),
        )
        self.direction_scripts_dir_var = tk.StringVar(
            value=str(self.app_settings.get("direction_scripts_dir", DIRECTION_SCRIPTS_DIR)),
        )
        self.hotkey_summary_var = tk.StringVar(value="")
        self.script_category_var = tk.StringVar(value="关卡")
        self.insert_position_var = tk.StringVar(value="below")
    def _build_ui(self):
        root_frame = ttk.Frame(self.root, style="Workspace.TFrame")
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
        self.notebook.add(self.script_tab, text="脚本编辑")
        self.notebook.add(self.workflow_tab, text="工作流")
        self.notebook.add(self.log_tab, text="运行日志")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_script_tab()
        self._build_workflow_tab()
        self._build_log_tab()
        apply_pointer_cursors(root_frame)
    def _on_tab_changed(self, _event=None):
        """Refresh workflow displays when the workflow tab is shown."""
        if getattr(self, "workflow_tree", None) is None:
            return
        try:
            if self.notebook.index(self.notebook.select()) == 1:
                self.rebuild_workflow_tree()
        except (tk.TclError, ValueError):
            pass
    def _build_sidebar(self, parent):
        # Keep the configuration panel usable on shorter screens. The inner
        # frame keeps its existing layout while the canvas provides vertical
        # scrolling for all controls.
        sidebar_shell = ttk.Frame(parent, width=px(372), style="Sidebar.TFrame")
        sidebar_shell.pack(side="left", fill="y")
        sidebar_shell.pack_propagate(False)
        sidebar_canvas = tk.Canvas(
            sidebar_shell, background=COLOR_SIDEBAR, highlightthickness=0,
            borderwidth=0, width=px(350),
        )
        sidebar_scrollbar = ttk.Scrollbar(sidebar_shell, orient="vertical", command=sidebar_canvas.yview)
        sidebar_canvas.configure(yscrollcommand=sidebar_scrollbar.set)
        sidebar_canvas.pack(side="left", fill="both", expand=True)
        sidebar_scrollbar.pack(side="right", fill="y")
        sidebar = ttk.Frame(sidebar_canvas, width=px(350), padding=pad(14, 14), style="Sidebar.TFrame")
        sidebar_window = sidebar_canvas.create_window((0, 0), window=sidebar, anchor="nw")

        def update_sidebar_scrollregion(_event=None):
            sidebar_canvas.configure(scrollregion=sidebar_canvas.bbox("all"))

        def resize_sidebar_content(event):
            sidebar_canvas.itemconfigure(sidebar_window, width=event.width)

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

        self.record_button = ttk.Button(sidebar, text="开始录制    F8", command=lambda: self.toggle_record(from_ui=True), bootstyle="danger")
        self.record_button.pack(fill="x", ipady=px(3))
        self.run_button = ttk.Button(sidebar, text="执行当前脚本    F9", command=self.run_current_script, bootstyle="success")
        self.run_button.pack(fill="x", ipady=px(3), pady=pad(5, 0))
        ttk.Button(sidebar, text="紧急停止    F12", command=self.stop_all, style="SidebarGhost.TButton").pack(fill="x", pady=pad(5, 0))
        ttk.Button(
            sidebar, text="游戏设置说明…", command=self.open_game_setup_note,
            style="SidebarGhost.TButton",
        ).pack(fill="x", pady=pad(5, 11))

        settings_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        settings_title.pack(fill="x", pady=pad(0, 5))
        ttk.Label(settings_title, text="录制设置", style="SidebarSection.TLabel").pack(side="left")
        ttk.Button(settings_title, text="测试声音", command=self.test_sound,
                   style="SidebarGhost.TButton", width=7).pack(side="right")
        ttk.Button(settings_title, text="保存配置", command=self.save_sidebar_config,
                   style="SidebarGhost.TButton", width=7).pack(side="right", padx=pad(0, 4))
        option_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        option_row.pack(fill="x", pady=pad(0, 10))
        check_style = {
            "anchor": "w", "background": COLOR_SIDEBAR, "activebackground": COLOR_SIDEBAR,
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
        ttk.Button(
            option_row, text="调节录制/执行小窗位置（显示边界）",
            command=self._adjust_execution_mini_position,
            style="SidebarGhost.TButton",
        ).pack(anchor="w", fill="x", pady=pad(2, 6))
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
        ttk.Label(record_mode_title, text="智能录制", style="SidebarSection.TLabel").pack(side="left")
        self._help_badge(
            record_mode_title,
            "桌面自动记录坐标；绑定游戏窗口，或在游戏中按 F8，可自动记录锁中心的视角转向。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(7, 0))
        # 标题与控件分两行：单行会把标题、帮助徽章、数字框、单位框和按钮
        # 全部挤在 318px 内，高 DPI 下整行溢出侧栏，按钮文字被裁切。
        interval_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        interval_title.pack(fill="x", pady=pad(0, 5))
        ttk.Label(interval_title, text="桌面轨迹间隔", style="Sidebar.TLabel").pack(side="left")
        self._help_badge(
            interval_title, "录制桌面鼠标移动时，相邻轨迹点的最小间隔；数值越小记录越细。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
        interval_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        interval_row.pack(fill="x", pady=pad(0, 15))
        self.interval_spin = ttk.Spinbox(
            interval_row, from_=10, to=500, increment=5,
            textvariable=self.interval_var, width=7,
        )
        self.interval_spin.pack(side="left")
        ttk.Combobox(
            interval_row, textvariable=self.interval_var.unit, values=TIME_UNITS,
            state="readonly", width=4,
        ).pack(side="left", padx=pad(5, 0))
        self.interval_edit_button = ttk.Button(
            interval_row, text="修改", width=5, style="SidebarGhost.TButton",
            command=lambda: self._toggle_locked_spinbox(
                self.interval_spin, self.interval_edit_button, self._settings_changed,
            ),
        )
        self.interval_edit_button.pack(side="left", padx=pad(8, 0))
        self.interval_spin.configure(state="disabled")

        ttk.Separator(sidebar).pack(fill="x", pady=px(4))
        target_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        target_title.pack(fill="x", pady=pad(16, 7))
        ttk.Label(target_title, text="目标窗口", style="Section.TLabel").pack(side="left")
        self._help_badge(
            target_title,
            "绑定后，坐标会按目标窗口记录和回放；游戏相对转向保持原始视角幅度，不参与分辨率缩放。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(7, 0))
        # wraplength 必须等于侧栏可用宽度：小于它才会换行，等于更大只会被裁切。
        self.bind_label_widget = ttk.Label(sidebar, textvariable=self.bind_label_var,
                                           wraplength=px(318), style="SidebarMuted.TLabel")
        self.bind_label_widget.pack(anchor="w", fill="x", pady=pad(0, 8))
        row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="选择窗口", command=self.choose_window, bootstyle="primary").pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="清除绑定", command=self.unbind_window, width=7, style="SidebarGhost.TButton").pack(side="left", padx=pad(8, 0))
        coordinate_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        coordinate_row.pack(fill="x", pady=pad(8, 0))
        self.cursor_position_button = ttk.Button(
            coordinate_row, text="开始实时读取", command=self.toggle_cursor_tracking,
            style="SidebarGhost.TButton",
        )
        self.cursor_position_button.pack(side="left")
        ttk.Label(
            coordinate_row, textvariable=self.cursor_position_var,
            style="SidebarMuted.TLabel",
        ).pack(side="left", padx=pad(8, 0))

        ttk.Label(sidebar, text="执行设置", style="SidebarSection.TLabel").pack(anchor="w", pady=pad(18, 7))
        playback_speed_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        playback_speed_title.pack(fill="x", pady=pad(0, 2))
        ttk.Label(playback_speed_title, text="Playback speed", style="Sidebar.TLabel").pack(side="left")
        ttk.Label(
            playback_speed_title, textvariable=self.playback_speed_label_var,
            style="SidebarMuted.TLabel",
        ).pack(side="right")
        playback_speed_scale = ttk.Scale(
            sidebar, from_=0.5, to=2.0, variable=self.playback_speed_var,
            command=self._on_playback_speed_changed,
        )
        playback_speed_scale.pack(fill="x", pady=pad(0, 2))
        playback_speed_scale.bind("<ButtonRelease-1>", self._settings_changed, add="+")
        ttk.Label(
            sidebar, text="0.5x slow  |  1.0x normal  |  2.0x fast (waits only)",
            style="SidebarMuted.TLabel",
        ).pack(anchor="w", pady=pad(0, 8))
        resolution_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        resolution_title.pack(fill="x", pady=pad(4, 2))
        ttk.Label(resolution_title, text="分辨率样式", style="Sidebar.TLabel").pack(side="left")
        self._help_badge(
            resolution_title,
            "脚本中的“分辨率”动作从这里选择样式；参照窗口留空即改当前软件所在的显示器，"
            "选了窗口则只改该窗口所在的显示器。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
        ttk.Button(
            resolution_title, text="设置…", width=7,
            command=self._configure_resolution_styles,
            style="SidebarGhost.TButton",
        ).pack(side="right")
        ttk.Label(
            sidebar, textvariable=self.resolution_styles_summary_var,
            wraplength=px(250), style="SidebarMuted.TLabel",
        ).pack(anchor="w", pady=pad(0, 8))
        focus_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        focus_row.pack(fill="x", pady=pad(0, 6))
        tk.Checkbutton(
            focus_row, text="强制专注模式",
            variable=self.focus_mode_enabled_var,
            command=self._settings_changed,
            **check_style,
        ).pack(side="left")
        self._help_badge(
            focus_row, "执行时锁定实体键鼠，适合需要持续前台操作的目标。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
        activate_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activate_row.pack(fill="x", pady=pad(0, 6))
        tk.Checkbutton(
            activate_row, text="执行时前置目标",
            variable=self.activate_target_enabled_var,
            command=self._toggle_target_activation,
            **check_style,
        ).pack(side="left")
        self._help_badge(
            activate_row, "勾选后每次执行前激活目标窗口；取消勾选只停止前置，不会清除已保存的目标窗口。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
        activation_toggle_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activation_toggle_row.pack(fill="x", pady=pad(0, 4))
        tk.Checkbutton(
            activation_toggle_row, text="启用执行前置窗口",
            variable=self.activation_enabled_var,
            command=self._toggle_activation_enabled,
            **check_style,
        ).pack(side="left")
        self._help_badge(
            activation_toggle_row, "可指定另一个窗口先被激活，再执行当前脚本。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
        ttk.Label(sidebar, text="执行前置窗口", style="SidebarMuted.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar, textvariable=self.activation_label_var,
            wraplength=px(318), style="SidebarMuted.TLabel",
        ).pack(anchor="w", fill="x", pady=pad(2, 6))
        activation_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        activation_row.pack(fill="x", pady=pad(0, 8))
        ttk.Button(
            activation_row, text="选择前置窗口", command=self.choose_activation_window,
            style="SidebarGhost.TButton",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            activation_row, text="跟随目标", command=self.unbind_activation_window,
            width=8, style="SidebarGhost.TButton",
        ).pack(side="left", padx=pad(8, 0))
        # 同“桌面轨迹间隔”：标题与控件分两行，避免高 DPI 下控件被挤出侧栏。
        repeat_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        repeat_title.pack(fill="x", pady=pad(0, 7))
        ttk.Label(repeat_title, text="脚本重复次数", style="Sidebar.TLabel").pack(side="left")
        self._help_badge(
            repeat_title, "执行当前脚本时完整重复的次数。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
        repeat_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        repeat_row.pack(fill="x")
        self.repeat_spin = ttk.Spinbox(
            repeat_row, from_=1, to=999999, textvariable=self.repeat_var, width=7,
        )
        self.repeat_spin.pack(side="left")
        self.repeat_edit_button = ttk.Button(
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
        ttk.Label(notice_title, text="浮动提醒位置", style="Sidebar.TLabel").pack(side="left")
        self._help_badge(
            notice_title, "选择脚本“提醒”动作在屏幕上的显示位置。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(6, 0))
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
        ttk.Label(sidebar, text="启动与备份", style="SidebarSection.TLabel").pack(
            anchor="w", pady=pad(12, 7),
        )
        tk.Checkbutton(
            sidebar, text="启用定时备份", variable=self.timed_backup_enabled_var,
            command=self._startup_backup_settings_changed, **check_style,
        ).pack(anchor="w", pady=px(2))
        backup_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        backup_row.pack(fill="x", pady=pad(5, 8))
        ttk.Label(backup_row, text="备份间隔", style="Sidebar.TLabel").pack(side="left")
        self.backup_interval_box = ttk.Combobox(
            backup_row, textvariable=self.backup_interval_var,
            values=BACKUP_INTERVAL_CHOICES, state="readonly", width=7,
        )
        self.backup_interval_box.pack(side="right")
        self.backup_interval_box.bind(
            "<<ComboboxSelected>>", lambda _event: self._startup_backup_settings_changed(),
        )
        self.backup_interval_box.bind("<MouseWheel>", lambda _event: "break")
        tk.Checkbutton(
            sidebar, text="开机自启动", variable=self.windows_startup_enabled_var,
            command=self._startup_backup_settings_changed, **check_style,
        ).pack(anchor="w", pady=px(2))
        tk.Checkbutton(
            sidebar, text="启动时最小化到托盘", variable=self.start_minimized_to_tray_var,
            command=self._startup_backup_settings_changed, **check_style,
        ).pack(anchor="w", pady=px(2))
        tk.Checkbutton(
            sidebar, text="启动时执行工作流", variable=self.startup_run_workflow_var,
            command=self._startup_backup_settings_changed, **check_style,
        ).pack(anchor="w", pady=px(2))
        startup_workflow_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        startup_workflow_row.pack(fill="x", pady=pad(5, 0))
        ttk.Entry(
            startup_workflow_row, textvariable=self.startup_workflow_path_var,
            state="readonly", width=22,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            startup_workflow_row, text="选择…", width=6, style="SidebarGhost.TButton",
            command=self._choose_startup_workflow,
        ).pack(side="left", padx=pad(6, 0))
        ttk.Label(
            sidebar, text="每个脚本固定覆盖同一份备份，不累计历史副本。",
            wraplength=px(250), style="SidebarMuted.TLabel",
        ).pack(anchor="w", pady=pad(5, 0))

        ttk.Separator(sidebar).pack(fill="x", pady=pad(16, 4))
        hotkey_title = ttk.Frame(sidebar, style="Sidebar.TFrame")
        hotkey_title.pack(fill="x", pady=pad(12, 7))
        ttk.Label(hotkey_title, text="快捷键脚本", style="SidebarSection.TLabel").pack(side="left")
        self._help_badge(
            hotkey_title,
            "把脚本绑定到快捷键：录制或执行脚本的过程中，按下快捷键立即执行该脚本，"
            "例如游戏中按 J 执行“转向左 90°”。",
            background=COLOR_SIDEBAR,
        ).pack(side="left", padx=pad(7, 0))
        ttk.Button(
            hotkey_title, text="设置…", width=7, style="SidebarGhost.TButton",
            command=self._configure_hotkey_scripts,
        ).pack(side="right")
        ttk.Label(
            sidebar, textvariable=self.hotkey_summary_var, wraplength=px(318),
            style="SidebarMuted.TLabel",
        ).pack(anchor="w", pady=pad(0, 4))

        ttk.Label(sidebar, text="专注执行：F12 停止；无响应时按 Ctrl + Alt + Del。",
                  wraplength=px(318), style="SidebarMuted.TLabel").pack(anchor="w", pady=pad(10, 0))

        bind_sidebar_wheel(sidebar)
        update_sidebar_scrollregion()
    @staticmethod
    def _help_badge(parent, text: str, *, background: str = COLOR_BG):
        """Create a visible question-mark badge with a hover description."""
        badge = tk.Label(
            parent, text="?", width=2, cursor="hand2",
            background=COLOR_BLUE, foreground="#EAF4FF",
            activebackground=COLOR_BLUE, activeforeground="#FFFFFF",
            font=(FONT_FAMILY, FONT_BODY, "bold"), relief="flat",
        )
        Tooltip(badge, text, anchor=parent)
        return badge
    def _show_add_action_menu(self):
        """「+ 添加动作 ▾」：低频动作类型的入口。

        菜单项与工具栏按钮来自同一份按钮清单、执行同一个命令函数，
        所以把按钮折进菜单不会少任何一个动作能力。
        """
        menu = tk.Menu(
            self.root, tearoff=False, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        specs = tuple(
            (text, command_name, _style)
            for text, command_name, _style in self._script_action_button_specs()
        )
        overflow = [item for item in specs if item[1] not in set(PRIMARY_ACTION_COMMANDS)]
        for text, command_name, _style in overflow:
            menu.add_command(label=text, command=getattr(self, command_name))
        menu.add_separator()
        menu.add_command(
            label="⇥ 引用脚本（实时读取）", command=lambda: self._insert_script(False),
        )
        menu.add_command(
            label="⇥ 逐行插入脚本", command=lambda: self._insert_script(True),
        )
        self._popup_menu(menu, self.add_action_menu_button)

    def _show_script_more_menu(self):
        """「⋯ 更多」：插入位置与脚本插入等不常按的入口。"""
        menu = tk.Menu(
            self.root, tearoff=False, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        menu.add_command(
            label="▲ 向上插入", command=lambda: self._set_insert_position(True),
        )
        menu.add_command(
            label="▼ 向下插入", command=lambda: self._set_insert_position(False),
        )
        menu.add_separator()
        menu.add_command(label="⇥ 引用脚本（实时读取）", command=lambda: self._insert_script(False))
        menu.add_command(label="⇥ 逐行插入脚本", command=lambda: self._insert_script(True))
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
            ("◷ 延时", "add_delay", "ScriptTool.TButton"),
            ("⌨ 键盘", "add_key", "ScriptTool.TButton"),
            ("T 文本", "add_text", "ScriptTool.TButton"),
            ("i 提醒", "add_notice", "ScriptTool.TButton"),
            ("↖ 移动", "add_mouse_move", "ScriptTool.TButton"),
            ("◉ 点击", "add_click", "ScriptTool.TButton"),
            ("↺ 转向", "add_turn", "ScriptTool.TButton"),
            ("↻ 连点", "add_repeat_click", "ScriptTool.TButton"),
            ("↕ 滚轮", "add_scroll", "ScriptTool.TButton"),
            ("⇄ 数字比较", "add_ocr_compare", "AccentScriptTool.TButton"),
            ("⊞ 多条件识图", "add_multi_condition_click", "AccentScriptTool.TButton"),
            ("▤ 列表逐行点击", "add_row_list_condition_click", "AccentScriptTool.TButton"),
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
        ttk.Button(file_row, text="新建", command=self.new_script, style="Ghost.TButton").pack(side="left")
        ttk.Button(file_row, text="打开", command=self.open_script, style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="关闭", command=self.close_script, style="DangerScriptTool.TButton").pack(side="left", padx=pad(6, 0))
        self.undo_open_button = ttk.Button(
            file_row, text="↩ 撤销打开", command=self.undo_open_script,
            style="Ghost.TButton", state="disabled",
        )
        self.undo_open_button.pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="保存", command=self.save_current_script, bootstyle="primary").pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="新开窗口", command=self.open_new_window, style="Ghost.TButton").pack(side="left", padx=pad(6, 0))
        ttk.Button(file_row, text="模块管理…", command=self.open_template_region_manager,
                   style="Ghost.TButton").pack(side="right")
        ttk.Button(file_row, text="目录设置…", command=self._configure_script_directories,
                   style="Ghost.TButton").pack(side="right", padx=pad(0, 6))
        ttk.Button(file_row, text="打开脚本目录", command=lambda: self.open_folder(self._script_category_dir()),
                   style="Ghost.TButton").pack(side="right", padx=pad(0, 6))

        toolbar = ttk.Frame(self.script_tab, padding=pad(12, 3, 12, 8), style="Toolbar.TFrame")
        toolbar.pack(fill="x")

        # 一行搞定：常用动作常驻，其余动作进「添加动作」菜单。动作类型有 19 个，
        # 全铺出来会占满两行，把动作列表挤下去。
        add_buttons = ttk.Frame(toolbar, style="Toolbar.TFrame")
        add_buttons.pack(side="left", fill="x")
        action_specs = tuple(
            (text, command_name, style_name)
            for text, command_name, style_name in self._script_action_button_specs()
        )
        primary_specs, overflow_specs = split_toolbar_specs(
            action_specs, set(PRIMARY_ACTION_COMMANDS),
        )
        for index, (text, command_name, style_name) in enumerate(primary_specs):
            # padx 必须是单个像素值或二元组：pad(4, 0) 返回元组，Tk 会把
            # "4 0" 当成一个距离解析并报 bad pad value，所以这里只取左间距。
            ttk.Button(
                add_buttons, text=text, command=getattr(self, command_name),
                style=style_name,
            ).pack(side="left", padx=(0 if index == 0 else px(4), 0))
        self.add_action_menu_button = ttk.Button(
            add_buttons, text=ADD_ACTION_MENU_LABEL,
            command=self._show_add_action_menu, style="AccentScriptTool.TButton",
        )
        self.add_action_menu_button.pack(side="left", padx=px(4))

        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=px(8))

        insert_position_menu = tk.Menu(
            self.root, tearoff=False, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground="#1D4358", activeforeground="#FFFFFF",
            borderwidth=1, relief="solid",
        )
        self.insert_above_button = ttk.Button(
            add_buttons, text="▲ 向上插入",
            command=lambda: self._set_insert_position(True),
        )
        self.insert_below_button = ttk.Button(
            add_buttons, text="▼ 向下插入",
            command=lambda: self._set_insert_position(False),
        )

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

        # 「更多」：插入位置、脚本插入、以及低频动作入口都在这里，避免工具栏再长一行。
        self.script_more_menu_button = ttk.Button(
            toolbar, text="⋯ 更多", command=self._show_script_more_menu,
            style="ScriptTool.TButton",
        )
        self.script_more_menu_button.pack(side="right")
        self._script_insert_position_menu = insert_position_menu
        self._script_overflow_specs = overflow_specs
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
        scroll = ttk.Scrollbar(action_tree_shell, orient="vertical", command=self.action_tree.yview)
        horizontal_scroll = ttk.Scrollbar(
            action_tree_shell, orient="horizontal", command=self.action_tree.xview,
        )
        self.action_tree.configure(xscrollcommand=horizontal_scroll.set)
        attach_autohide_scrollbar(self.action_tree, scroll)
        self.action_tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        self.empty_action_hint = ttk.Label(
            frame, text="还没有动作\n按 F8 或点「添加动作」里的「录制」开始，也可用上方按钮逐条添加",
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
        ttk.Label(start_label, text="开始时间").pack(side="left")
        self._help_badge(
            start_label, "留空表示手动运行；设置后到达指定时间自动开始当前工作流。",
        ).pack(side="left", padx=pad(6, 0))
        ttk.Entry(workflow_meta_bar, textvariable=self.workflow_start_var, width=20, state="readonly").pack(side="left", padx=pad(8, 4))
        ttk.Button(workflow_meta_bar, text="📅 选择", command=self.choose_workflow_start,
                   style="CompactGhost.TButton").pack(side="left")
        workflow_action_bar = ttk.Frame(header, style="Workspace.TFrame")
        workflow_action_bar.pack(fill="x", pady=pad(8, 0))
        ttk.Button(
            workflow_action_bar, text="运行工作流", command=self.run_workflow,
            bootstyle="success",
        ).pack(side="right")
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

        restart_default_bar = ttk.Frame(self.workflow_tab, padding=pad(16, 0, 16, 8), style="Workspace.TFrame")
        restart_default_bar.pack(fill="x")
        ttk.Label(restart_default_bar, text="重新执行默认跳转行").pack(side="left")
        self.workflow_restart_default_combo = ttk.Combobox(
            restart_default_bar, state="readonly", width=34,
        )
        self.workflow_restart_default_combo.pack(side="left", padx=pad(8, 5))
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
            global_toolbar, text="双击行更换模块", style="Muted.TLabel",
        ).pack(side="left", padx=pad(10, 0))
        global_tree_frame = ttk.Frame(global_box, style="Surface.TFrame")
        # 撑满整个上窗格：原来只按 6 行高布局，窗格空着却出现滚动条。
        global_tree_frame.pack(fill="both", expand=True, pady=pad(0, 10))
        self.global_tree = ttk.Treeview(
            global_tree_frame, columns=("index", "module", "status"),
            show="headings", selectmode="extended", style="Workflow.Treeview", height=6,
        )
        self._apply_column_widths(self.global_tree, GLOBAL_TREE_COLUMNS, "module")
        global_scroll = ttk.Scrollbar(global_tree_frame, orient="vertical", command=self.global_tree.yview)
        attach_autohide_scrollbar(self.global_tree, global_scroll)
        self.global_tree.tag_configure("disabled", foreground="#F2B84B", background="#2B2418")
        self.global_tree.tag_configure("global", foreground="#7BC96F", background="#14261B")
        self.global_tree.pack(side="left", fill="both", expand=True)
        global_scroll.pack(side="right", fill="y")
        self.empty_global_hint = ttk.Label(
            global_tree_frame, text="还没有工作流全局模块\n点击“添加工作流全局模块”从模块仓库中选择",
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
        ttk.Button(file_toolbar, text="保存", command=self.save_current_workflow, bootstyle="primary").pack(side="left", padx=pad(5, 0))
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
        ttk.Button(edit_toolbar, text="启用/禁用", command=self.toggle_selected_workflow_step,
                   style="CompactGhost.TButton").pack(side="left", padx=pad(5, 0))
        ttk.Button(edit_toolbar, text="上移", command=lambda: self.move_workflow_step(-1), style="CompactGhost.TButton").pack(side="left", padx=pad(5, 2))
        ttk.Button(edit_toolbar, text="下移", command=lambda: self.move_workflow_step(1), style="CompactGhost.TButton").pack(side="left")
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
            columns=("mark", "index", "script", "repeat", "before", "interval", "enabled"),
            show="headings", selectmode="extended", style="Workflow.Treeview", height=10,
        )
        self._apply_column_widths(self.workflow_tree, WORKFLOW_TREE_COLUMNS, "script")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.workflow_tree.yview)
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
        self.workflow_tree.bind("<Double-1>", self._edit_workflow_cell, add="+")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._update_workflow_selection_color, add="+")
        self.workflow_tree.bind("<<TreeviewSelect>>", self._refresh_workflow_segment_bar, add="+")
        self.workflow_tree.bind("<Control-a>", self._select_all_workflow_steps)
        self.workflow_tree.bind("<Button-3>", self._show_workflow_context_menu, add="+")
    def _build_log_tab(self):
        """运行日志标签：与磁盘上两个日志文件一致的两个视图。

        事件日志＝状态变化（重复消息已合并）；执行明细＝每一行脚本动作。
        两个视图各自独立缓冲/清空，都在主线程写入。
        """
        frame = ttk.Frame(self.log_tab, padding=px(16), style="Workspace.TFrame")
        frame.pack(fill="both", expand=True)
        top = ttk.Frame(frame)
        top.pack(fill="x", pady=pad(0, 8))
        ttk.Label(top, text="运行日志", style="PageTitle.TLabel").pack(side="left")
        self.log_view_var = tk.StringVar(value="event")
        self.log_view_buttons: dict[str, tk.Button] = {}
        for value, label in (("event", "事件日志"), ("trace", "执行明细")):
            button = tk.Button(
                top, text=label, command=lambda target=value: self._show_log_view(target),
                relief="flat", borderwidth=0, padx=px(10), pady=px(3), cursor="hand2",
                font=(FONT_FAMILY, FONT_BODY),
            )
            button.pack(side="left", padx=pad(8, 0))
            self.log_view_buttons[value] = button
        ttk.Button(
            top, text="打开日志目录", command=lambda: self.open_folder(self.logs_dir),
            bootstyle="secondary-outline",
        ).pack(side="right", padx=pad(0, 6))
        ttk.Button(top, text="清空", command=self._clear_active_log_view,
                   bootstyle="secondary-outline").pack(side="right")
        hint = ttk.Frame(frame)
        hint.pack(fill="x")
        self.log_view_hint_var = tk.StringVar(value="")
        ttk.Label(hint, textvariable=self.log_view_hint_var, style="Muted.TLabel").pack(
            side="left", pady=pad(0, 6),
        )
        # 两个视图共用一个文本框：切换时按当前标签重放内容。
        self.log_text = tk.Text(frame, wrap="word", state="disabled", background=COLOR_SURFACE,
                                foreground=COLOR_TEXT, insertbackground=COLOR_TEXT,
                                selectbackground="#244D78", relief="flat", bd=0,
                                font=(FONT_MONO, FONT_BODY), padx=px(16), pady=px(14))
        self.log_text.pack(fill="both", expand=True)
        self._trace_view_pending: list[str] = []
        self._trace_view_job = None
        self._event_view_pending: list[str] = []
        self._event_view_job = None
        # 两个视图各自的内容缓冲：切换标签/清空都只动界面，磁盘日志不受影响。
        self._trace_view_buffer: list[str] = []
        self._event_view_buffer: list[str] = []
        self._sync_log_view_buttons()
    def _show_log_view(self, view: str) -> None:
        self.log_view_var.set("trace" if view == "trace" else "event")
        self._sync_log_view_buttons()
        self._render_log_view()
    def _active_log_view(self) -> str:
        return getattr(self, "log_view_var", None) and self.log_view_var.get() or "event"
    def _sync_log_view_buttons(self) -> None:
        active = self._active_log_view()
        for value, button in getattr(self, "log_view_buttons", {}).items():
            if value == active:
                button.configure(background=COLOR_BLUE, foreground="#FFFFFF",
                                 activebackground=COLOR_BLUE, activeforeground="#FFFFFF")
            else:
                button.configure(background=COLOR_SURFACE, foreground=COLOR_TEXT,
                                 activebackground=COLOR_HOVER, activeforeground=COLOR_TEXT)
        hint = getattr(self, "log_view_hint_var", None)
        if hint is None:
            return
        if active == "trace":
            hint.set("执行明细：每执行一行脚本动作一条（行号 · 动作 · 等待/耗时）；文件 MacroFlow_trace_*.log")
        else:
            hint.set("事件日志：状态变化与异常，30 秒内重复的同一句已合并；文件 MacroFlow_*.log")
    def _locked_log_text(self):
        """两个视图共用的 Text：未建好界面时返回 None（测试夹具直接跳过）。"""
        widget = getattr(self, "log_text", None)
        if widget is None:
            return None
        try:
            widget.configure(state="normal")
        except tk.TclError:
            return None
        return widget
    def _log_view_buffer(self, view: str) -> list[str]:
        attr = "_trace_view_buffer" if view == "trace" else "_event_view_buffer"
        buffer = getattr(self, attr, None)
        if buffer is None:
            buffer = []
            setattr(self, attr, buffer)
        return buffer
    def _log_view_text(self, view: str) -> str:
        return "".join(self._log_view_buffer(view))
    def _render_log_view(self) -> None:
        """按当前缓冲重画当前视图（切换标签、清空、超上限裁剪时用）。"""
        widget = self._locked_log_text()
        if widget is None:
            return
        try:
            widget.delete("1.0", "end")
            text = self._log_view_text(self._active_log_view())
            if text:
                widget.insert("end", text)
            widget.see("end")
        finally:
            widget.configure(state="disabled")
    def _insert_log_view(self, text: str, view: str) -> None:
        """只在当前视图就是这一路日志时直接追加（否则等切过去时重画）。"""
        if not text:
            return
        widget = self._locked_log_text()
        if widget is None:
            return
        try:
            if self._active_log_view() == view:
                widget.insert("end", text)
                widget.see("end")
        finally:
            widget.configure(state="disabled")
    def _clear_active_log_view(self) -> None:
        """清空当前视图（磁盘日志文件保留）。"""
        self._log_view_buffer(self._active_log_view()).clear()
        self._render_log_view()
    def _queue_log_view_line(self, text: str, view: str) -> None:
        """把一行日志排队到界面对应视图（后台线程安全）。"""
        if getattr(self, "log_text", None) is None or not text:
            return  # 测试夹具没有界面控件
        # 缓冲按需创建：没有走完整界面初始化的测试夹具也不该在这里炸。
        pending = self._log_view_pending(view)
        pending.append(text)
        job_attr = "_trace_view_job" if view == "trace" else "_event_view_job"
        if getattr(self, job_attr, None) is not None:
            return
        root = getattr(self, "root", None)
        if root is None:
            # 没有事件循环（测试夹具直接调 _log）：立即刷出，行为与原来一致。
            self._flush_log_view(view)
            return
        job = root.after(120, self._flush_log_view, view)
        setattr(self, job_attr, job)
    def _log_view_pending(self, view: str) -> list[str]:
        attr = "_trace_view_pending" if view == "trace" else "_event_view_pending"
        pending = getattr(self, attr, None)
        if pending is None:
            pending = []
            setattr(self, attr, pending)
        return pending
    def _flush_log_view(self, view: str) -> None:
        setattr(self, "_trace_view_job" if view == "trace" else "_event_view_job", None)
        pending = self._log_view_pending(view)
        if not pending:
            return
        text = "".join(pending)
        pending.clear()
        buffer = self._log_view_buffer(view)
        buffer.append(text)
        # 挂机跑几小时会有几十万行：只保留最后一段，避免界面被日志拖垮
        # （磁盘上的日志文件始终是完整的）。
        dropped = 0
        total = sum(len(part) for part in buffer)
        while total > self.LOG_VIEW_LIMIT and len(buffer) > 1:
            dropped += len(buffer[0])
            total -= len(buffer.pop(0))
        try:
            if dropped:
                self._render_log_view()
            else:
                self._insert_log_view(text, view)
        except tk.TclError:
            # 界面正在销毁（退出流程）：丢掉这一批即可，文件日志已经落盘。
            pass
