from __future__ import annotations

from macroflow.ui.dialogs.base import key_to_vk
from pathlib import Path
from macroflow.execution.player import PlaybackStopped
from macroflow.input.input_guard import RESERVED_HOTKEY_VKS, VK_F7, VK_F8, VK_F10
from macroflow.input.wininput import activate_window, show_window
from macroflow.core.display_power import allow_display_sleep, keep_display_awake
from pynput import keyboard
from macroflow.core.storage import load_script, resolve_path
import os
import sys
import threading
import tkinter as tk
import traceback

from ..base import _key_vk

class RuntimeHotkeysMixin:
    """Shared hotkeys behavior used by both clients."""

    def _ensure_startup_visible(self):
        if "--recover-workflow" in sys.argv or self.exiting or self.main_hidden_to_tray or self.main_hidden_for_recording \
                or self.main_hidden_for_execution or self.main_hidden_for_cursor_tracking:
            return
        # 先把待处理的布局与重绘跑完再显示：否则窗口会先闪出一帧空白/半成品界面。
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.state("normal")
        self.root.update_idletasks()
        hwnd = int(self.root.winfo_id())
        show_window(hwnd)
        activate_window(hwnd)

    def mark_route_point(self):
        """F7：给正在录制的坐标路线标记一个纠正点。"""
        panel = getattr(self, '_ssjj_coordinates', None)
        if panel is None or panel.closed:
            return
        panel.mark()

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
                        self._emergency_stop_from_hook()
                    return
                if key == keyboard.Key.f7:
                    # F7：给正在录制的坐标路线打一个纠正点（回放时只在这些点纠正）。
                    self._ui(self.mark_route_point)
                elif key == keyboard.Key.f8:
                    if VK_F8 in self._hotkey_pressed:
                        return
                    self._hotkey_pressed.add(VK_F8)
                    if self.worker and self.worker.is_alive():
                        self._ui(self.toggle_execution_pause)
                    else:
                        self._ui(self.toggle_record, False)
                elif key == keyboard.Key.f9:
                    self._ui(self.run_current_script)
                elif key == keyboard.Key.f10:
                    if VK_F10 in self._hotkey_pressed:
                        return
                    self._hotkey_pressed.add(VK_F10)
                    self._ui(self.run_workflow)
                elif key == keyboard.Key.f12:
                    self._emergency_stop_from_hook()
                else:
                    vk = _key_vk(key)
                    binding = self._hotkey_vk_map.get(vk)
                    if binding is not None:
                        if vk in self._hotkey_pressed:
                            return  # 按住自动重复：只触发一次。
                        self._hotkey_pressed.add(vk)
                        self._trigger_hotkey_script(binding)
            except Exception:
                # 回调异常不能杀死 pynput 监听器，否则 F8/F9/F10/F12 全部失效、
                # 紧急停止也无从谈起。记录后继续监听。
                try:
                    self._log("热键回调异常：" + traceback.format_exc())
                except Exception:
                    pass

        def on_release(key, injected=False):
            if injected:
                return
            try:
                self._hotkey_pressed.discard(
                    VK_F8 if key == keyboard.Key.f8 else VK_F7 if key == keyboard.Key.f7
                    else _key_vk(key))
            except Exception:
                pass

        self.hotkey_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.hotkey_listener.start()
        if not self.hotkey_listener.running:
            self._log(
                "热键监听器启动失败：F8/F9/F10/F12 可能无法使用，"
                "请以管理员身份运行或检查安全软件是否拦截了低级键盘钩子。"
            )

    def _stop_hotkeys(self):
        listener = getattr(self, 'hotkey_listener', None)
        if listener is not None:
            listener.stop()

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
            bindings.append({"key": key, "vk": vk, "script": script,
                             "enabled": bool(item.get("enabled", True))})
        return bindings

    def _apply_hotkey_bindings(self):
        """把当前绑定重建为 虚键码 → 绑定 的映射，并同步守卫与录制过滤。"""
        vk_map: dict[int, dict] = {}
        for binding in self.hotkey_scripts:
            if not binding.get("enabled", True):
                continue
            vk = int(binding.get("vk") or 0)
            if vk <= 0 or vk in RESERVED_HOTKEY_VKS:
                continue
            vk_map[vk] = binding
        self._hotkey_vk_map = vk_map
        self._hotkey_recorder_filter_vks = set(vk_map)
        active = getattr(self, "_hotkey_active_binding", None)
        if active and active.get("vk") not in vk_map:
            self._stop_hotkey_script()
        guard = getattr(self, "input_guard", None)
        if guard is not None:
            guard.set_hotkeys(set(vk_map) | {VK_F8})
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
            suffix = "" if item.get("enabled", True) else "（已禁用）"
            parts.append(f"{item.get('key', '?')} → {name}{suffix}")
        var.set("，".join(parts))

    def _on_hotkey_vk(self, vk: int):
        """专注模式守卫钩子线程触发的快捷键回调。"""
        if int(vk) == VK_F8:
            self._ui(self.toggle_execution_pause)
            return
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
        if self.exiting or self.hotkey_config_open or not binding.get("enabled", True):
            return
        with self._hotkey_script_lock:
            if self._hotkey_script_running:
                if binding.get("vk") == self._hotkey_active_binding.get("vk"):
                    self._stop_hotkey_script()
                else:
                    self._ui(self._log, f"快捷键 {binding.get('key', '?')} 触发被忽略：上一个快捷键脚本仍在执行。")
                return
            self._hotkey_script_running = True
            self._hotkey_active_binding = dict(binding)
            self._hotkey_cancel.clear()
        from ..plugins_runtime import arm_interval_plugins
        arm_interval_plugins(self)
        threading.Thread(
            target=self._hotkey_script_worker,
            args=(dict(binding),),
            name="MacroFlowHotkeyScript",
            daemon=True,
        ).start()

    def _hotkey_playback_heartbeat(self):
        # play() 会重置播放器停止标记，独立取消信号保证快速再次按键也能停止。
        if self._hotkey_cancel.is_set():
            raise PlaybackStopped()

    def _stop_hotkey_script(self):
        cancel = getattr(self, "_hotkey_cancel", None)
        if cancel is not None:
            cancel.set()
        player = getattr(self, "hotkey_player", None)
        if player is not None:
            player.stop()

    def _show_key_sequence_notice(self, text, duration_ms=4500):
        self._show_execution_notice(text, duration_ms, position="左下", passive=True)

    def _key_sequence_state(self, running: bool, seconds: float, key: str = ""):
        prefix = f"{key}：" if key else "组合按键："
        mode = f"每 {seconds:g} 秒循环" if seconds else "仅执行一次"
        text = f"{prefix}{'开始' if running else '结束'} · {mode}"
        self._ui(self._show_key_sequence_notice, text, 0 if running else 4500)

    def _key_sequence_countdown(self, remaining: int, key: str = ""):
        prefix = f"{key}：" if key else "组合按键："
        self._ui(self._show_key_sequence_notice, f"{prefix}开始 · 倒计时 {remaining} 秒", 0)

    def _hotkey_sequence_countdown(self, remaining: int):
        binding = self._hotkey_active_binding or {}
        self._key_sequence_countdown(remaining, str(binding.get("key", "")))

    def _hotkey_sequence_state(self, running: bool, seconds: float):
        binding = self._hotkey_active_binding or {}
        self._key_sequence_state(running, seconds, str(binding.get("key", "")))

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
                self._trace_event,
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
            with self._hotkey_script_lock:
                self._hotkey_active_binding = None
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
        self._cancel_workflow_recovery()
        if self.recorder is not None and self.recorder.running:
            # The recorder owns the newest actions until it is stopped. Move
            # them into the editor before taking the shutdown snapshot.
            self.stop_recording(sound=False)
        self._persist_workflow_draft()
        self._finish_client_capture()
        self.workflow_stop.set()
        self.player.stop()
        self._stop_hotkey_script()
        self._clear_global_guards()
        self._shutdown_detection_worker()
        if self.cursor_tracking:
            self._stop_cursor_tracking()
        self.input_guard.stop()
        self._hide_recording_mini()
        self._stop_hotkeys()
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

