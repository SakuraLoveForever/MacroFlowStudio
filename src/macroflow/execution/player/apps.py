from __future__ import annotations

from macroflow.input.wininput import (
    activate_window, get_cursor_pos, get_foreground_window_info,
    get_display_resolution_for_window, get_display_scaling_for_window,
    get_monitor_rect_for_window, get_primary_screen_rect, get_virtual_screen_rect,
    get_window_rect, is_window,
    is_window_process_foreground, resolve_window_signature, send_button, send_key,
    send_move_absolute, send_move_relative, send_scroll,
    set_display_resolution_for_window, set_display_scaling_for_window,
    send_text, set_cursor_pos,
)
import time

from .base import (
    elevated_taskkill,
    is_process_running,
    taskkill_process,
)
from .control import (
    PlaybackStopped,
)

class AppsMixin:
    """打开 / 关闭软件与前置窗口动作。"""

    def _close_process(self, image_name: str, graceful: bool = True,
                       graceful_wait_ms: int = 2000, tree: bool = False,
                       elevated_retry: bool = False) -> None:
        """End a process by image name; graceful close first, force with retries
        as fallback, then optionally an elevated kill. Raises RuntimeError when
        the process survives every attempt."""
        if not is_process_running(image_name):
            return
        if graceful:
            code, _err = taskkill_process(image_name, force=False, tree=tree)
            if code != 0:
                self._status(f"{image_name} 关闭请求失败（权限不足或进程异常），改为强制结束")
            else:
                deadline = time.perf_counter() + max(0, int(graceful_wait_ms)) / 1000
                while is_process_running(image_name):
                    if self.stop_event.is_set():
                        raise PlaybackStopped()
                    if time.perf_counter() >= deadline:
                        break
                    self._wait(50)
                if not is_process_running(image_name):
                    self._log_event(f"已结束 {image_name}")
                    return
                self._log_event(f"{image_name} 未响应关闭请求，强制结束")
        else:
            self._log_event(f"强制结束 {image_name}")
        for _ in range(3):
            code, _err = taskkill_process(image_name, force=True, tree=tree)
            if code != 0:
                # taskkill 本身失败（如权限不足），轮询等待没有意义
                self._wait(200)
                continue
            deadline = time.perf_counter() + 1000 / 1000
            while True:
                if not is_process_running(image_name):
                    self._log_event(f"已强制结束 {image_name}")
                    return
                if self.stop_event.is_set():
                    raise PlaybackStopped()
                if time.perf_counter() >= deadline:
                    break
                self._wait(50)
            self._wait(200)
        if elevated_retry:
            self._status(f"{image_name} 普通权限无法结束，尝试以管理员权限结束（可能弹出 UAC 授权窗口）")
            if elevated_taskkill(image_name, tree=tree):
                deadline = time.perf_counter() + 8000 / 1000
                while True:
                    if not is_process_running(image_name):
                        self._status(f"已以管理员权限结束 {image_name}")
                        return
                    if self.stop_event.is_set():
                        raise PlaybackStopped()
                    if time.perf_counter() >= deadline:
                        break
                    self._wait(50)
            self._status("管理员权限结束失败或授权被取消")
        raise RuntimeError(f"无法结束进程：{image_name}")
    def _execute_close_app(self, action: dict) -> None:
        image_name = str(action.get("name", "")).strip()
        if not image_name:
            raise RuntimeError("关闭软件动作缺少进程名")
        if not is_process_running(image_name):
            self._log_event(f"{image_name} 未在运行，跳过")
            return
        self._close_process(
            image_name,
            graceful=bool(action.get("graceful", True)),
            graceful_wait_ms=int(action.get("graceful_wait_ms", 2000)),
            tree=bool(action.get("tree", False)),
            elevated_retry=bool(action.get("elevated_retry", False)),
        )
    def _execute_activate_window(self, action: dict) -> None:
        """Resolve a saved stable window signature and bring that live window forward."""
        signature = action.get("window") or {}
        selected = resolve_window_signature(signature)
        if selected is None or not activate_window(selected.hwnd):
            title = str(signature.get("title", "")).strip()
            class_name = str(signature.get("class_name", "")).strip()
            process_path = str(signature.get("process_path", "")).strip()
            raise RuntimeError(f"要前置的窗口当前未打开：{title or class_name or process_path}")
        self._relative_target_hwnd = int(selected.hwnd)
        self._status(f"已前置窗口：{selected.title}")
