from __future__ import annotations

from macroflow.ui.dialogs.actions import ClickDialog, CloseAppDialog, DurationDialog, GameSetupNoteDialog, JsonActionDialog, JumpActionDialog, KeyActionDialog, MouseMoveDialog, OpenAppDialog, RepeatClickDialog, ScheduleDialog, ScrollDialog, SetResolutionActionDialog, TurnActionDialog, edit_action
from macroflow.ui.dialogs.app_dialogs import HotkeyScriptsDialog, ResolutionStylesDialog, ScriptDirectoriesDialog, WindowPicker, WorkflowBatchSettingsDialog, WorkflowRepeatDialog
from macroflow.ui.dialogs.base import DurationVar, TIME_UNITS, Tooltip, key_to_vk, show_floating_notice, vk_to_key_name
from macroflow.ui.dialogs.helpers import recorded_action_description, workflow_step_label
from macroflow.ui.dialogs.module_objects import ModulePickerDialog, TemplateRegionFormDialog, TemplateRegionManagerDialog
from macroflow.ui.dialogs.recognition import GlobalDetectDialog, MultiConditionClickDialog, OcrCompareActionDialog, RowListConditionClickDialog, RowListDiagnosticResultDialog, RowRecognitionResultDialog
from pathlib import Path
from macroflow.execution.player import (
    JUMP_CURRENT_SCRIPT_LAST_RESULT, MAX_SCRIPT_REF_DEPTH,
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest,
    JumpToCurrentScriptLastAction, MacroPlayer, PlaybackStopped,
    screen_template_scale,
)
from macroflow.input.input_guard import (
    FocusInputGuard, InputCapturer, KeyCapturer, RESERVED_HOTKEY_VKS,
)
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
from macroflow.core.display_power import allow_display_sleep, keep_display_awake
from pynput import keyboard
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
import sys
import threading
import tkinter as tk
import traceback

from .base import (
    _key_vk,
)

class HotkeysMixin:
    """快捷键绑定、启动与退出生命周期。"""

    def _ensure_startup_visible(self):
        if self.exiting or self.main_hidden_to_tray or self.main_hidden_for_recording \
                or self.main_hidden_for_execution or self.main_hidden_for_cursor_tracking:
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.update_idletasks()
        hwnd = int(self.root.winfo_id())
        show_window(hwnd)
        activate_window(hwnd)
    def _start_hotkeys(self):
        # pynput 会把“注入标志”作为第二个参数传给回调（Windows 端），
        # 用它跳过脚本回放产生的注入按键，避免快捷键脚本被自己触发。
        def on_press(key, injected=False):
            try:
                if injected:
                    return
                if self.input_guard.active:
                    # 专注模式下实体输入由 FocusInputGuard 统一拦截，快捷键
                    # 在守卫钩子线程里识别触发，这里只保留 F12 紧急停止。
                    if key == keyboard.Key.f12:
                        self._ui(self.stop_all)
                    return
                if key == keyboard.Key.f8:
                    self._ui(self.toggle_record, False)
                elif key == keyboard.Key.f9:
                    self._ui(self.run_current_script)
                elif key == keyboard.Key.f12:
                    self._ui(self.stop_all)
                else:
                    vk = _key_vk(key)
                    binding = self._hotkey_vk_map.get(vk)
                    if binding is not None:
                        if vk in self._hotkey_pressed:
                            return  # 按住自动重复：只触发一次。
                        self._hotkey_pressed.add(vk)
                        self._trigger_hotkey_script(binding)
            except Exception:
                # 回调异常不能杀死 pynput 监听器，否则 F8/F9/F12 全部失效、
                # 紧急停止也无从谈起。记录后继续监听。
                try:
                    self._log("热键回调异常：" + traceback.format_exc())
                except Exception:
                    pass

        def on_release(key):
            try:
                self._hotkey_pressed.discard(_key_vk(key))
            except Exception:
                pass

        self.hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.hotkey_listener.start()
        if not self.hotkey_listener.running:
            self._log(
                "热键监听器启动失败：F8/F9/F12 可能无法使用，"
                "请以管理员身份运行或检查安全软件是否拦截了低级键盘钩子。"
            )
    @staticmethod
    def _normalize_hotkey_scripts(raw) -> list[dict]:
        """校验并归一化保存的快捷键绑定：[{"key", "vk", "script"}, ...]"""
        bindings: list[dict] = []
        if not isinstance(raw, (list, tuple)):
            return bindings
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip().upper()
            script = str(item.get("script", "")).strip()
            if not key or not script:
                continue
            try:
                vk = int(item.get("vk") or 0)
            except (TypeError, ValueError):
                vk = 0
            if vk <= 0:
                try:
                    vk, _ = key_to_vk(key)
                except ValueError:
                    continue
            bindings.append({"key": key, "vk": vk, "script": script})
        return bindings
    def _apply_hotkey_bindings(self):
        """把当前绑定重建为 虚键码 → 绑定 的映射，并同步守卫与录制过滤。"""
        vk_map: dict[int, dict] = {}
        for binding in self.hotkey_scripts:
            vk = int(binding.get("vk") or 0)
            if vk <= 0 or vk in RESERVED_HOTKEY_VKS:
                continue
            vk_map[vk] = binding
        self._hotkey_vk_map = vk_map
        self._hotkey_recorder_filter_vks = set(vk_map)
        guard = getattr(self, "input_guard", None)
        if guard is not None:
            guard.set_hotkeys(set(vk_map))
        recorder = getattr(self, "recorder", None)
        if recorder is not None:
            # 录制中改绑定也要立刻生效：recorder 持有的是 start() 时的拷贝。
            recorder.set_filter_vks(self._hotkey_recorder_filter_vks)
    def _refresh_hotkey_summary(self):
        var = getattr(self, "hotkey_summary_var", None)
        if var is None:
            return
        if not self.hotkey_scripts:
            var.set("未设置；可把脚本绑定到快捷键，录制/执行时一键调用。")
            return
        parts = []
        for item in self.hotkey_scripts:
            name = Path(str(item.get("script", ""))).stem or "?"
            parts.append(f"{item.get('key', '?')} → {name}")
        var.set("，".join(parts))
    def _configure_hotkey_scripts(self):
        dialog = HotkeyScriptsDialog(self.root, list(self.hotkey_scripts))
        self.hotkey_config_open = True
        try:
            result = dialog.show()
        finally:
            self.hotkey_config_open = False
        if result is None:
            return
        self.hotkey_scripts = self._normalize_hotkey_scripts(result)
        self._apply_hotkey_bindings()
        self._refresh_hotkey_summary()
        self._persist_sidebar_settings()
        self._set_status(f"已保存 {len(self.hotkey_scripts)} 个快捷键脚本绑定", "success")
        self._log(
            "快捷键脚本已保存：录制或执行过程中按对应按键即可执行绑定的脚本"
            f"（共 {len(self.hotkey_scripts)} 个）。"
        )
    def _on_hotkey_vk(self, vk: int):
        """专注模式守卫钩子线程触发的快捷键回调。"""
        binding = self._hotkey_vk_map.get(int(vk))
        if binding is None:
            self._ui(
                self._log,
                f"守卫触发快捷键 vk={vk}，但当前没有对应绑定"
                f"（已同步：{sorted(self._hotkey_vk_map)}）。",
            )
            return
        self._trigger_hotkey_script(binding)
    def _trigger_hotkey_script(self, binding: dict):
        if self.exiting or self.hotkey_config_open:
            return
        if self._hotkey_script_running:
            self._ui(
                self._log,
                f"快捷键 {binding.get('key', '?')} 触发被忽略：上一个快捷键脚本仍在执行。",
            )
            return
        self._hotkey_script_running = True
        threading.Thread(
            target=self._hotkey_script_worker,
            args=(dict(binding),),
            name="MacroFlowHotkeyScript",
            daemon=True,
        ).start()
    def _hotkey_script_worker(self, binding: dict):
        """独立线程回放快捷键绑定的脚本（与录制/主脚本执行并行）。"""
        key_name = str(binding.get("key", "?"))
        try:
            # 快捷键脚本同样是「脚本在执行」：识图等待期间也不能让屏保起来。
            keep_display_awake()
            script_path = resolve_path(str(binding.get("script", "")))
            if not script_path.is_file():
                self._ui(
                    self._log,
                    f"快捷键 {key_name}：脚本文件不存在：{binding.get('script')}",
                )
                return
            script = load_script(script_path)
            if not script.actions:
                self._ui(self._log, f"快捷键 {key_name}：脚本 {script.name} 没有动作，已跳过。")
                return
            self._ui(
                self._log,
                f"快捷键 {key_name} 触发脚本：{script.name}"
                f"（{len(script.actions)} 个动作，F12 可停止）",
            )
            source_screen = dict(script.settings.get("recorded_screen", {})) or None
            # 含相对转向/相对移动动作的快捷键脚本：先把目标游戏窗口带到前台
            # 并复位光标。录制转向时游戏在前台且锁定光标，ΔX/ΔY 从中心起算；
            # 回放若游戏不在前台（MacroFlow 窗口挡住游戏等），游戏不锁定光标，
            # 转向位移只会把桌面光标推到屏幕边缘，游戏不会转向。
            target_hwnd = None
            if self._actions_need_bound_window(script.actions):
                target_hwnd = self._bound_hwnd(update_display=False)
                if not target_hwnd:
                    self._ui(
                        self._log,
                        f"快捷键 {key_name}：未找到绑定的目标窗口，相对转向可能无法生效，"
                        "请在侧栏重新绑定游戏窗口。",
                    )
            self.hotkey_player.play(
                list(script.actions), repeats=1, hwnd=target_hwnd,
                source_screen=source_screen,
                activate_target=bool(target_hwnd),
            )
            if not self.hotkey_player.stop_event.is_set():
                self._ui(self._log, f"快捷键脚本执行完成：{script.name}")
        except PlaybackStopped:
            self._ui(self._log, f"快捷键脚本已停止：{key_name}")
        except Exception as exc:
            self._ui(self._log, f"快捷键脚本执行失败：{key_name}：{exc}")
        finally:
            allow_display_sleep()
            self._hotkey_script_running = False
    def on_close(self):
        """点主窗口关闭按钮：按设置直接退出，或收进托盘继续在后台运行。"""
        if getattr(self, "close_action_var", None) is not None \
                and self.close_action_var.get() == "tray":
            self._persist_workflow_draft()
            if self._hide_main_to_tray():
                return
            # 托盘图标建不出来时绝不留下既无窗口又无图标的隐藏进程。
        self._quit_app()
    def _quit_app(self):
        if self.exiting:
            return
        self.exiting = True
        if self.recorder.running:
            # The recorder owns the newest actions until it is stopped. Move
            # them into the editor before taking the shutdown snapshot.
            self.stop_recording(sound=False)
        self._persist_workflow_draft()
        self._finish_search_capture()
        if self.backup_after_id is not None:
            try:
                self.root.after_cancel(self.backup_after_id)
            except tk.TclError:
                pass
            self.backup_after_id = None
        self.workflow_stop.set()
        self.player.stop()
        hotkey_player = getattr(self, "hotkey_player", None)
        if hotkey_player is not None:
            hotkey_player.stop()
        self._clear_global_guards()
        self._shutdown_detection_worker()
        if self.cursor_tracking:
            self._stop_cursor_tracking()
        self.input_guard.stop()
        self._hide_recording_mini()
        if self.hotkey_listener:
            self.hotkey_listener.stop()
        self._stop_tray()
        self._flush_log_dedup()
        self.ui_queue.flush()
        self.ui_queue.close()
        self.root.destroy()
    def run(self):
        self.root.mainloop()
        if getattr(sys, "frozen", False):
            # _quit_app 已保存草稿并释放钩子/托盘；即便收尾路上出了岔子，也绝不
            # 把窗口关掉却让进程（原生 OCR、pystray 等非守护线程）留在后台。
            os._exit(0)
