from __future__ import annotations

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
from macroflow.ui.dialogs import (
    ClickDialog, GameSetupNoteDialog, GlobalDetectDialog, TurnActionDialog,
    HotkeyScriptsDialog,
    JsonActionDialog, JumpActionDialog, KeyActionDialog,
    RepeatClickDialog, CloseAppDialog, OcrCompareActionDialog, MultiConditionClickDialog,
    RowListConditionClickDialog,
    RowListDiagnosticResultDialog,
    RowRecognitionResultDialog,
    ModulePickerDialog,
    MouseMoveDialog, ResolutionStylesDialog, ScheduleDialog, ScrollDialog,
    SetResolutionActionDialog,
    OpenAppDialog, ScriptDirectoriesDialog, TemplateRegionFormDialog,
    TemplateRegionManagerDialog, WindowPicker,
    WorkflowBatchSettingsDialog, WorkflowRepeatDialog,
    DurationDialog, DurationVar, TIME_UNITS, Tooltip, edit_action,
    key_to_vk, recorded_action_description, vk_to_key_name,
    show_floating_notice, workflow_step_label,
)
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
import os
import tkinter as tk
import ttkbootstrap as ttk

from .base import (
    pad,
    px,
)
from .constants import (
    COLOR_SURFACE,
)

class WindowBindingMixin:
    """目标窗口绑定、前置窗口与相对转向。"""

    def choose_window(self):
        selected = WindowPicker(self.root).show()
        if selected:
            self.bound_window = selected
            self.saved_window_signature = {
                "title": selected.title,
                "class_name": selected.class_name,
                "process_path": selected.process_path,
                "window_rect": list(selected.window_rect),
                "client_size": list(selected.client_size),
            }
            self.bind_label_var.set(selected.title)
            self._persist_sidebar_settings()
            self._refresh_coordinate_scale_status()
            self._log(f"已绑定并保存目标窗口：{selected.label}；下次启动会自动恢复。")
            self._log("智能录制会在目标窗口激活时记录原始相对轨迹，离开目标窗口后记录普通坐标。")
    def unbind_window(self):
        self.bound_window = None
        self.saved_window_signature = None
        self.bind_label_var.set("未绑定窗口")
        self._persist_sidebar_settings()
        self._refresh_coordinate_scale_status()
        self._log("已解除窗口绑定。")
    def _activation_settings_from_script(self) -> tuple[bool, dict[str, str] | None]:
        """Read the pre-window config stored in the current script's settings."""
        enabled = bool(self.script.settings.get("activation_window_enabled", False))
        signature = self.script.settings.get("activation_window")
        if isinstance(signature, dict) and signature.get("title"):
            signature = {
                "title": str(signature.get("title", "")),
                "class_name": str(signature.get("class_name", "")),
                "process_path": str(signature.get("process_path", "")),
            }
        else:
            signature = None
        return enabled, signature
    def _activation_settings_from_workflow_step(
        self, step: dict,
    ) -> tuple[bool, dict[str, str] | None]:
        """Read pre-window settings from a workflow step's script file."""
        if step.get("kind") == "module":
            return False, None
        path = resolve_path(step.get("script", ""))
        if not path.is_file():
            return False, None
        try:
            script = load_script(path)
        except Exception:
            return False, None
        enabled = bool(script.settings.get("activation_window_enabled", False))
        signature = script.settings.get("activation_window")
        if isinstance(signature, dict) and signature.get("title"):
            signature = {
                "title": str(signature.get("title", "")),
                "class_name": str(signature.get("class_name", "")),
                "process_path": str(signature.get("process_path", "")),
            }
        else:
            signature = None
        return enabled, signature
    def _persist_activation_to_script(self):
        """Write the pre-window config into the current script's settings."""
        self.script.settings["activation_window_enabled"] = bool(self.activation_enabled_var.get())
        self.script.settings["activation_window"] = (
            dict(self.saved_activation_signature) if self.saved_activation_signature else None
        )
        self.script.settings["activation_window_configured"] = True
        self._mark_dirty()
    def _sync_activation_ui_from_script(self):
        """Refresh the sidebar pre-window controls from the current script."""
        configured = self.script.settings.get("activation_window_configured")
        has_script_config = (
            bool(configured)
            if configured is not None
            else bool(
                self.script.settings.get("activation_window_enabled")
                or self.script.settings.get("activation_window")
            )
        )
        if has_script_config:
            enabled, signature = self._activation_settings_from_script()
        else:
            # 老脚本/未配置脚本继承最近保存值。被动打开脚本不能清除全局记忆。
            enabled = bool(getattr(self, "activation_draft_enabled", False))
            draft = getattr(self, "activation_draft_signature", None)
            signature = dict(draft) if draft else None
        self.activation_enabled_var.set(enabled)
        self.saved_activation_signature = signature
        if enabled and signature:
            self._restore_saved_activation_window(signature)
        else:
            self.activation_window = None
        self._refresh_activation_label()
    def _toggle_activation_enabled(self):
        self._persist_activation_to_script()
        self._remember_activation_draft()
        self._refresh_activation_label()
        self._persist_sidebar_settings()
    def _refresh_activation_label(self):
        if not self.saved_activation_signature:
            self.activation_label_var.set("跟随目标窗口")
            return
        title = self.saved_activation_signature["title"]
        if not self.activation_enabled_var.get():
            self.activation_label_var.set(f"{title}（已停用）")
        elif self.activation_window and is_window(self.activation_window.hwnd):
            self.activation_label_var.set(title)
        else:
            self.activation_label_var.set(f"已保存，等待窗口：{title}")
    def choose_activation_window(self):
        selected = WindowPicker(self.root).show()
        if not selected:
            return
        self.activation_window = selected
        self.saved_activation_signature = {
            "title": selected.title,
            "class_name": selected.class_name,
            "process_path": selected.process_path,
        }
        self.activation_enabled_var.set(True)
        self._refresh_activation_label()
        self._persist_activation_to_script()
        self._remember_activation_draft()
        self._persist_sidebar_settings()
        self._log(f"已为本脚本设置执行前置窗口：{selected.label}；下次启动会自动恢复。")
    def unbind_activation_window(self):
        self.activation_window = None
        self.saved_activation_signature = None
        self.activation_enabled_var.set(False)
        self._refresh_activation_label()
        self._persist_activation_to_script()
        self._remember_activation_draft()
        self._persist_sidebar_settings()
        self._log("已清除本脚本的执行前置窗口，执行时跟随目标窗口。")
    def _restore_saved_activation_window(self, signature: dict[str, str] | None) -> bool:
        """Find the pre-window matching signature; fills self.activation_window only."""
        if not signature:
            return False
        title = str(signature.get("title", ""))
        class_name = str(signature.get("class_name", ""))
        process_path = os.path.normcase(str(signature.get("process_path", ""))).casefold()
        foreground = get_foreground_window_info()

        def matches(item: WindowInfo, require_title: bool = True) -> bool:
            item_path = os.path.normcase(item.process_path).casefold() if item.process_path else ""
            return (
                (not require_title or not title or item.title == title)
                and (not class_name or item.class_name == class_name)
                and (not process_path or not item_path or item_path == process_path)
            )

        windows = enum_windows()
        exact = [item for item in windows if matches(item)]
        if exact:
            selected = next((item for item in exact if foreground and item.hwnd == foreground.hwnd), exact[0])
        else:
            compatible = [item for item in windows if matches(item, require_title=False)]
            selected = foreground if foreground and matches(foreground, require_title=False) else (
                compatible[0] if len(compatible) == 1 else None
            )
        if selected:
            self.activation_window = selected
            return True
        self.activation_window = None
        return False
    def _execution_activation_hwnd(self, target_hwnd: int | None,
                                   enabled: bool, signature: dict[str, str] | None) -> int | None:
        """Resolve the pre-window hwnd from a script's own settings."""
        if not enabled or not signature:
            return None
        # 工作流会连续执行不同脚本，不能仅因缓存 HWND 仍有效就复用上一个
        # 脚本的窗口；每次都按当前签名重新核对并解析。
        if not self._restore_saved_activation_window(signature):
            raise RuntimeError("脚本的前置窗口当前未打开。")
        return self.activation_window.hwnd
    def _activate_execution_window_before_ocr(self, hwnd: int | None) -> bool:
        """Activate a resolved pre-window before any OCR engine import begins."""
        if not hwnd:
            return False
        if not is_window(hwnd):
            self._ui(self._log, "前置窗口已关闭，已跳过前置窗口，继续执行。")
            return False
        if activate_window(hwnd):
            self._ui(self._log, "已在 OCR 准备前激活前置窗口。")
            return True
        self._ui(self._log, "前置窗口激活失败，将在脚本开始时重试。")
        return False
    def toggle_cursor_tracking(self):
        if self.cursor_tracking:
            self._stop_cursor_tracking()
            return
        self.cursor_tracking = True
        self.cursor_position_button.configure(text="停止实时读取")
        self.cursor_position_var.set("移动鼠标以读取外部坐标…")
        self._show_cursor_tracking_mini()
        self.root.withdraw()
        self.main_hidden_for_cursor_tracking = True
        self._set_status("正在实时读取光标坐标，再次点击按钮停止", "warning")
        self._poll_cursor_position()
    def _show_cursor_tracking_mini(self):
        if self.cursor_tracking_mini and self.cursor_tracking_mini.winfo_exists():
            return
        mini = tk.Toplevel(self.root)
        self.cursor_tracking_mini = mini
        mini.overrideredirect(True)
        mini.attributes("-topmost", True)
        mini.configure(background=COLOR_SURFACE)
        width, height = px(280), px(62)
        area = get_monitor_work_area_for_window(self._app_window_hwnd()) \
            or get_primary_screen_rect()
        x = area["left"] + area["width"] - width - px(18)
        y = area["top"] + px(18)
        mini.geometry(f"{width}x{height}+{max(area['left'], x)}+{y}")
        body = ttk.Frame(mini, padding=pad(12, 9), style="Surface.TFrame")
        body.pack(fill="both", expand=True)
        ttk.Label(
            body, textvariable=self.cursor_tracking_mini_var,
            style="MiniTime.TLabel",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            body, text="停止", command=self.toggle_cursor_tracking,
            bootstyle="danger", width=7,
        ).pack(side="right")
        mini.update_idletasks()
        make_window_no_activate(mini.winfo_id())
        set_rounded_window(mini.winfo_id(), px(8))
    def _hide_cursor_tracking_mini(self):
        if self.cursor_tracking_mini and self.cursor_tracking_mini.winfo_exists():
            self.cursor_tracking_mini.destroy()
        self.cursor_tracking_mini = None
    def _poll_cursor_position(self):
        if not self.cursor_tracking:
            return
        x, y = get_cursor_pos()
        screen = get_virtual_screen_rect()
        self.cursor_position_var.set(f"({x}, {y}) · {screen['width']}×{screen['height']}")
        self.cursor_tracking_mini_var.set(f"X: {x}    Y: {y}")
        self.cursor_tracking_after_id = self.root.after(50, self._poll_cursor_position)
    def _stop_cursor_tracking(self):
        self.cursor_tracking = False
        if self.cursor_tracking_after_id is not None:
            self.root.after_cancel(self.cursor_tracking_after_id)
            self.cursor_tracking_after_id = None
        self.cursor_position_button.configure(text="开始实时读取")
        self._hide_cursor_tracking_mini()
        if self.main_hidden_for_cursor_tracking:
            self.main_hidden_for_cursor_tracking = False
            if not self.exiting:
                self._restore_main_window()
        self._set_status(f"已停止读取，最后坐标：{self.cursor_position_var.get()}", "success")
        self._log(f"停止实时读取光标坐标；最后结果：{self.cursor_position_var.get()}。")
    def _restore_saved_window_binding(self, update_display: bool = True) -> bool:
        signature = self.saved_window_signature
        if not signature:
            return False
        # A persisted target is the signal for the desktop-to-game workflow:
        # keep the recorder in automatic mode even before the game is opened.
        title = signature.get("title", "")
        class_name = signature.get("class_name", "")
        process_path = str(signature.get("process_path", ""))
        expected_path = os.path.normcase(process_path).casefold() if process_path else ""
        expected_rect = tuple(signature.get("window_rect", (0, 0, 0, 0)))
        expected_client = tuple(signature.get("client_size", (0, 0)))
        windows = enum_windows()
        foreground = get_foreground_window_info()

        def path_matches(item):
            return not expected_path or not item.process_path or os.path.normcase(item.process_path).casefold() == expected_path

        def identity_matches(item):
            return (
                (not class_name or item.class_name == class_name)
                and (not title or item.title == title)
                and path_matches(item)
                and (not any(expected_rect) or tuple(item.window_rect) == expected_rect)
                and (not any(expected_client) or tuple(item.client_size) == expected_client)
            )

        def shape_matches(item):
            return (
                (not class_name or item.class_name == class_name)
                and path_matches(item)
            )

        # Runtime window handles change whenever a game restarts. Resolve the
        # persisted target by its stable window
        # identity instead, preferring the current foreground window when all
        # saved properties match. Volatile runtime identifiers are deliberately
        # not part of the saved identity.
        exact = [item for item in windows if identity_matches(item)]
        if exact:
            selected = next((item for item in exact if foreground and item.hwnd == foreground.hwnd), exact[0])
        else:
            same_class = [item for item in windows if shape_matches(item)]
            if foreground and shape_matches(foreground):
                selected = foreground
            elif len(same_class) == 1:
                selected = same_class[0]
            else:
                same_title = [item for item in windows if title and item.title == title]
                selected = same_title[0] if len(same_title) == 1 else None
        if selected:
            self.bound_window = selected
            if update_display:
                self.bind_label_var.set(selected.title)
                self._refresh_coordinate_scale_status()
            return True
        self.bound_window = None
        if update_display:
            self.bind_label_var.set(f"已保存，等待窗口：{title}")
            self._refresh_coordinate_scale_status()
        return False
    def _bound_hwnd(self, update_display: bool = True) -> int | None:
        foreground = get_foreground_window_info()
        if foreground and not is_current_process_window(foreground.hwnd) \
                and self._foreground_matches_target(foreground):
            # 已保存的旧 HWND 可能仍有效但已不是当前游戏窗口。当前前台明确
            # 符合目标身份时优先重新绑定，窗口区域识别才能截取正确画面。
            previous_hwnd = int(self.bound_window.hwnd) if self.bound_window else 0
            self.bound_window = foreground
            bind_label_var = getattr(self, "bind_label_var", None)
            if update_display and bind_label_var is not None:
                bind_label_var.set(foreground.title)
            if update_display and previous_hwnd and previous_hwnd != int(foreground.hwnd):
                ui = getattr(self, "_ui", None)
                if ui is not None and getattr(self, "root", None) is not None:
                    ui(
                        self._log,
                        f"检测到当前前台目标窗口已变化，已从 HWND={previous_hwnd} "
                        f"重新绑定到 HWND={foreground.hwnd}。",
                    )
            return foreground.hwnd
        if not self.bound_window:
            if not self._restore_saved_window_binding(update_display=update_display):
                return None
        if not is_window(self.bound_window.hwnd):
            self.bound_window = None
            if not self._restore_saved_window_binding(update_display=update_display):
                if update_display:
                    self._log("已保存的目标窗口当前未打开。")
                return None
        return self.bound_window.hwnd
