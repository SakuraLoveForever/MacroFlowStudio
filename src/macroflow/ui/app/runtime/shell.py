from __future__ import annotations

from macroflow.core.storage import BASE_DIR, ensure_dirs, migrate_workflow_templates, DIRECTION_SCRIPTS_DIR, load_app_settings, resolve_path
from macroflow.core.models import Workflow
from macroflow.ui.dialogs.base import DurationVar
from macroflow.input.input_guard import FocusInputGuard
from macroflow.execution.player import MacroPlayer
from pathlib import Path
from macroflow.ui.update_queue import UIUpdateQueue
from macroflow.input.wininput import WindowInfo, set_dark_titlebar
from datetime import datetime
from macroflow.ui.dialogs import base as dialogs_ui
import os
import pystray
from macroflow.core.resolution import resolution_styles_from_settings
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
import ttkbootstrap as ttk

from ..base import coordinate_scale_summary, default_main_geometry, disable_combobox_wheel_selection, pad, px, set_ui_scale
from ..titlebar import TitlebarHideButton
from ..constants import APP_NAME, APP_VERSION, COLOR_BG, COLOR_BLUE, COLOR_BORDER, COLOR_FOCUS, COLOR_GREEN, COLOR_HOVER, COLOR_MUTED, COLOR_RED, COLOR_SIDEBAR, COLOR_SURFACE, COLOR_SURFACE_ALT, COLOR_TEXT, FLOATING_NOTICE_POSITIONS, FONT_BODY, FONT_BRAND, FONT_FAMILY, FONT_MONO, FONT_SMALL, FONT_SUBTITLE, FONT_TITLE, MIN_MAIN_HEIGHT, MIN_MAIN_WIDTH

class RuntimeShellMixin:
    """Shared shell behavior used by both clients."""

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
        self.global_log_path = session_logs_dir / f"MacroFlow_global_{stamp}.log"
        self.log_file_lock = threading.Lock()
        self.trace_file_lock = threading.Lock()
        self.global_file_lock = threading.Lock()
        # 当前执行位置（工作流第几步 / 哪个脚本 / 第几次重复）：两级日志都靠它
        # 标注上下文，见 _set_trace_context / _with_event_context。
        self._trace_context_state: dict = {}
        # 事件日志去重：长时间重复的同一句话只留一行 + 次数。
        self._log_dedup_window_ms = 30000
        self._log_dedup_text = ""
        self._log_dedup_count = 0
        self._log_dedup_since = 0.0
        self.root = self._create_main_window()
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
        from macroflow.execution.ssjj_aim import set_follow_interval_ms
        set_follow_interval_ms(self.app_settings.get('ssjj_aim_interval_ms', 2))
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
        self._hotkey_script_lock = threading.Lock()
        self._hotkey_cancel = threading.Event()
        self._hotkey_active_binding = None
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
        self.activation_draft_interval_ms = int(
            self.app_settings.get("activation_window_draft_interval_ms", 0)
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
        self._workflow_history = None
        self.workflow_draft_after_id = None
        self.workflow_insert_position_var = tk.StringVar(value="below")
        self._configure_client_startup()
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
        self._initialize_authoring_state()
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
            on_heartbeat=self._workflow_heartbeat,
            on_key_sequence_state=self._key_sequence_state,
            on_key_sequence_countdown=self._key_sequence_countdown,
        )
        self.input_guard = FocusInputGuard(
            self._emergency_stop_from_hook,
            on_hotkey=self._on_hotkey_vk,
        )
        # 快捷键脚本专用播放器：独立于主播放器，可与录制/主脚本执行并行。
        # 只按纯动作方式回放（不注册全局守卫、不操作主执行界面）。
        self.hotkey_player = MacroPlayer(
            on_notice=self._player_notice_callback,
            on_log=lambda text: self._ui(self._log, f"[快捷键] {text}"),
            on_trace=self._on_player_trace,
            on_trace_line=lambda text: self._trace_event(f"[快捷键] {text}"),
            on_target_window_request=lambda: self._bound_hwnd(update_display=False),
            on_ocr_engine_wait=self._hotkey_wait_ocr_ready,
            on_heartbeat=self._hotkey_playback_heartbeat,
            on_key_sequence_state=self._hotkey_sequence_state,
            on_key_sequence_countdown=self._hotkey_sequence_countdown,
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
        # 触发后的冷却截止时间；工作流重启时按模块 key 保留，避免绕过冷却。
        self.global_detect_cooldown_deadlines: dict[str, float] = {}
        self.global_detect_trigger_count = 0
        # 单独执行（F9）全局脚本时的语句体回放参数：触发条件满足后重新播放语句体。
        self.standalone_global_replay: dict | None = None
        # 特殊模块「重新执行工作流」：标志 + 目标行（1 基，运行时按
        # 动作 → 工作流默认解析）。重启沿用当前步骤对象中的剩余次数。
        self.workflow_restart_requested = False
        self.workflow_restart_target_row = 1
        self.workflow_segment = None
        self.workflow_segment_rounds_remaining = 0
        self.workflow_segment_total_rounds = 0
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
        self.mini_window_var = tk.StringVar(value="目标 | 未设置")
        self.mini_event_var = tk.StringVar(value="等待执行信息")
        self.mini_mode = ""
        self.mini_update_after_id = None
        self.mini_binding_label = None
        self.bind_label_widget = None
        self.record_started_at = 0.0
        self.execution_started_at = 0.0
        self.execution_progress_text = ""
        self.execution_focus_requested = False
        self._focus_state_lock = threading.RLock()
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
        self.exiting = False

        self._create_variables()
        self._refresh_resolution_styles_summary()
        self._build_ui()
        self._titlebar_hide_button = TitlebarHideButton(self)
        self.ui_queue.start()
        self._restore_saved_window_binding()
        self._sync_activation_ui_from_script()
        self.rebuild_action_tree()
        self.rebuild_workflow_tree()
        self._start_hotkeys()
        self._apply_hotkey_bindings()
        self._refresh_hotkey_summary()
        self.refresh_script_files()
        from macroflow.core.client_mode import is_runner
        self._log("执行端已就绪。F8 暂停/继续，F9 执行当前脚本，F10 执行当前工作流，F12 紧急停止。"
                  if is_runner() else "应用已就绪。F8 空闲时录制/停止、执行时暂停/继续，F9 执行当前脚本，F10 执行当前工作流，F12 紧急停止。")
        self._set_status("就绪", "success")
        explicit_editor_start = bool(
            self.startup_new_script or self.startup_open_script or self.startup_edit_module
        )
        if "--recover-workflow" in sys.argv and not explicit_editor_start:
            self.root.after(120, self._hide_main_to_tray)
        else:
            # Some Windows launchers propagate SW_HIDE to the first created window.
            self.root.after(120, self._ensure_startup_visible)
        self.root.after_idle(self._start_execution_prewarm)
        self.root.after(1200, self._watch_display_dpi)
        self._schedule_client_startup(explicit_editor_start)

    def _create_main_window(self):
        """创建主窗口，界面建好之前保持隐藏。

        Windows 会先用窗口类的背景色把客户区画出来：窗口一创建就已经映射，而
        ``__init__`` 里建界面、读配置要花上几百毫秒，用户看到的就是一块半白半黑的
        空窗（“卡顿”）再突然补齐。先 withdraw，界面建完后再由 ``_ensure_startup_visible``
        （或托盘/隐藏启动路径）决定何时显示，用户只会看到完整界面。
        """
        window = ttk.Window(themename="darkly")
        window.withdraw()
        return window

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
        for input_style in ("TEntry", "TSpinbox", "TCombobox"):
            style.map(input_style,
                      fieldbackground=[("disabled", COLOR_SURFACE), ("readonly", COLOR_SURFACE_ALT)],
                      background=[("disabled", COLOR_SURFACE), ("readonly", COLOR_SURFACE_ALT)],
                      foreground=[("disabled", COLOR_MUTED)])

        style.configure("TButton", padding=pad(6, 2), font=(FONT_FAMILY, FONT_BODY))
        style.configure("TCheckbutton", padding=pad(1, 1), font=(FONT_FAMILY, FONT_BODY))
        style.map("TCheckbutton",
                  background=[("active", COLOR_SURFACE_ALT)],
                  foreground=[("active", "#FFFFFF")])
        # ttkbootstrap 的语义按钮（bootstyle=primary/success/danger/…）也要跟着
        # 变紧凑，否则同一界面里两套按钮高度。样式名由 ttkbootstrap 拼接：
        # <Color>.TButton / <Color>.Outline.TButton。
        for color in ("primary", "secondary", "success", "info", "warning", "danger", "light", "dark"):
            style.configure(f"{color}.TButton", padding=pad(6, 2),
                            font=(FONT_FAMILY, FONT_BODY))
            style.configure(f"{color}.Outline.TButton", padding=pad(6, 2),
                            font=(FONT_FAMILY, FONT_BODY))

        for orientation in ("Horizontal", "Vertical"):
            style.configure(f"dark.{orientation}.TScrollbar", troughcolor=COLOR_BG,
                            background=COLOR_BG, bordercolor=COLOR_BG,
                            lightcolor=COLOR_BG, darkcolor=COLOR_BG,
                            arrowcolor=COLOR_MUTED)

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
                        relief="solid", borderwidth=1, padding=pad(6, 2),
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
                        relief="solid", borderwidth=1, padding=pad(6, 2),
                        font=(FONT_FAMILY, FONT_BODY))
        style.map("ScriptTool.TButton",
                  background=[("active", COLOR_SURFACE_ALT), ("pressed", "#263541")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")],
                  bordercolor=[("active", "#516170")])
        style.configure("AccentScriptTool.TButton", background="#122D48", foreground="#8FC4FF",
                        bordercolor=COLOR_BLUE, lightcolor=COLOR_BLUE, darkcolor=COLOR_BLUE,
                        relief="solid", borderwidth=1, padding=pad(6, 2),
                        font=(FONT_FAMILY, FONT_BODY, "bold"))
        style.map("AccentScriptTool.TButton",
                  background=[("active", "#18426A"), ("pressed", "#205582")],
                  foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")])
        style.configure("DangerScriptTool.TButton", background=COLOR_BG, foreground="#FF6B6B",
                        bordercolor="#A93636", lightcolor="#A93636", darkcolor="#A93636",
                        relief="solid", borderwidth=1, padding=pad(6, 2),
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
        try:
            repeat = max(1, min(999999, int(self.app_settings.get("repeat", 1))))
        except (TypeError, ValueError):
            repeat = 1
        self.script_name_var = tk.StringVar(value=self.script.name)
        # Recording is intentionally a single smart mode. Keep the variable so
        # older script/settings files remain compatible without exposing three
        # overlapping choices in the UI.
        self.record_mode_var = tk.StringVar(value="auto")
        self.repeat_var = tk.IntVar(value=repeat)
        self.bind_label_var = tk.StringVar(value="未绑定窗口")
        self.activation_enabled_var = tk.BooleanVar(value=False)
        self.activation_label_var = tk.StringVar(value="跟随目标窗口")
        self.activation_interval_var = tk.StringVar(
            value=f"{int(self.script.settings.get('activation_window_interval_ms', 0)) / 1000:.3f}".rstrip("0").rstrip("."),
        )
        self.cursor_position_var = tk.StringVar(value="光标坐标：尚未读取")
        self.cursor_tracking_mini_var = tk.StringVar(value="X: 0    Y: 0")
        self.status_var = tk.StringVar(value="就绪")
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
        self.workflow_start_resolution_var = tk.StringVar(
            value=str((self.workflow.start_resolution or {}).get("name") or "不修改"),
        )
        self.workflow_end_resolution_var = tk.StringVar(
            value=str((self.workflow.end_resolution or {}).get("name") or "不修改"),
        )
        self.workflow_test_mode_var = tk.BooleanVar(value=False)
        self.sound_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("sound_enabled", True)))
        self.mini_window_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("mini_window_enabled", True)))
        self.execution_mini_enabled_var = tk.BooleanVar(
            value=bool(self.app_settings.get("execution_mini_enabled", True)),
        )
        self.focus_mode_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("focus_mode_enabled", False)))
        self.activate_target_enabled_var = tk.BooleanVar(value=bool(self.app_settings.get("activate_target_enabled", True)))
        self.partial_script_globals_var = tk.BooleanVar(value=bool(self.app_settings.get("partial_script_globals", True)))
        self.partial_workflow_globals_var = tk.BooleanVar(value=bool(self.app_settings.get("partial_workflow_globals", True)))
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
        for value, label in (("event", "事件日志"), ("trace", "执行明细"), ("global", "全局模块")):
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
        self.global_log_pane = tk.PanedWindow(frame, orient="vertical", sashwidth=5)
        self.global_log_widgets = {}
        for scope, title in (("workflow", "工作流全局"), ("script", "脚本全局")):
            section = ttk.Frame(self.global_log_pane)
            ttk.Label(section, text=title, style="PageTitle.TLabel").pack(anchor="w")
            widget = tk.Text(section, wrap="word", state="disabled", background=COLOR_SURFACE,
                             foreground=COLOR_TEXT, font=(FONT_MONO, FONT_BODY), relief="flat")
            widget.pack(fill="both", expand=True)
            self.global_log_pane.add(section, stretch="always")
            self.global_log_widgets[scope] = widget
        self._global_view_buffers = {"workflow": [], "script": []}
        self._trace_view_pending: list[str] = []
        self._trace_view_job = None
        self._event_view_pending: list[str] = []
        self._event_view_job = None
        # 两个视图各自的内容缓冲：切换标签/清空都只动界面，磁盘日志不受影响。
        self._trace_view_buffer: list[str] = []
        self._event_view_buffer: list[str] = []
        self._sync_log_view_buttons()

    def _show_log_view(self, view: str) -> None:
        self.log_view_var.set(view if view in {"event", "trace", "global"} else "event")
        if view == "global":
            self.log_text.pack_forget()
            self.global_log_pane.pack(fill="both", expand=True)
            self._render_global_view()
        else:
            self.global_log_pane.pack_forget()
            self.log_text.pack(fill="both", expand=True)
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
        if active == "global":
            hint.set("全局模块：上方工作流全局，下方脚本全局；文件 MacroFlow_global_*.log")
        elif active == "trace":
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
                self._insert_colored_log(widget, text)
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
                self._insert_colored_log(widget, text)
                widget.see("end")
        finally:
            widget.configure(state="disabled")

    @staticmethod
    def _insert_colored_log(widget, text: str) -> None:
        widget.tag_configure("global_trigger", foreground="#FF6B6B")
        for line in text.splitlines(keepends=True):
            highlighted = any(marker in line for marker in (
                "全局检测触发：", "全局检测超时：", "全局检测处理", "全局模块步骤已执行：",
                "全局检测：",
            ))
            widget.insert("end", line, ("global_trigger",) if highlighted else ())

    def _clear_active_log_view(self) -> None:
        """清空当前视图（磁盘日志文件保留）。"""
        if self._active_log_view() == "global":
            self._global_view_buffers = {"workflow": [], "script": []}
            self._render_global_view()
            return
        self._log_view_buffer(self._active_log_view()).clear()
        self._render_log_view()

    def _queue_global_view_line(self, line: str, scope: str) -> None:
        buffers = getattr(self, "_global_view_buffers", None)
        if buffers is None:
            buffers = self._global_view_buffers = {"workflow": [], "script": []}
        buffer = buffers[scope]
        buffer.append(line)
        while sum(map(len, buffer)) > self.LOG_VIEW_LIMIT and len(buffer) > 1:
            buffer.pop(0)
        if getattr(self, "global_log_widgets", None):
            self._render_global_view(scope)

    def _render_global_view(self, only: str | None = None) -> None:
        for scope, widget in getattr(self, "global_log_widgets", {}).items():
            if only is not None and scope != only:
                continue
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            self._insert_colored_log(widget, "".join(self._global_view_buffers[scope]))
            widget.see("end")
            widget.configure(state="disabled")

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

