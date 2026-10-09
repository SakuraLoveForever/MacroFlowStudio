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

from macroflow.execution.process_close import (
    close_targets, include_descendants, kill_targets, pending_targets,
)
from .control import (
    PlaybackStopped,
)

class AppsMixin:
    """打开 / 关闭软件与前置窗口动作。"""

    def _execute_close_app(self, action: dict) -> None:
        image_name = str(action.get("name", "")).strip()
        if not image_name:
            raise RuntimeError("关闭软件动作缺少进程名")
        related = action.get("wait_for_processes", [])
        if not isinstance(related, list) or any(not isinstance(name, str) or not name.strip() for name in related):
            raise RuntimeError("一同关闭的进程名称必须是非空名称列表")
        targets = close_targets([image_name, *related])
        if not targets:
            self._log_event(f"{image_name} 及关联进程未在运行，跳过关闭请求")
            return
        if self.stop_event.is_set():
            raise PlaybackStopped()
        deadline = time.perf_counter() + 3.0
        self._log_event(f"正常关闭 {image_name}、子进程及关联进程，共用 3 秒退出时限")
        code, error = kill_targets(targets, timeout=3.0)
        if code == 0:
            while pending_targets(targets):
                if self.stop_event.is_set():
                    raise PlaybackStopped()
                if time.perf_counter() >= deadline:
                    break
                self._wait(50)
        else:
            self._log_event(f"正常关闭请求未全部成功：{error}；强制结束剩余进程")
        if self.stop_event.is_set():
            raise PlaybackStopped()
        remaining = pending_targets(include_descendants(pending_targets(targets)))
        if not remaining:
            self._log_event(f"已结束 {image_name}、子进程及关联进程")
            return
        self._log_event(f"强制结束剩余进程 PID：{', '.join(str(p.pid) for p in remaining)}")
        _code, error = kill_targets(remaining, force=True)
        force_deadline = time.perf_counter() + 1.0
        while pending_targets(remaining) and time.perf_counter() < force_deadline:
            if self.stop_event.is_set():
                raise PlaybackStopped()
            self._wait(50)
        remaining = pending_targets(remaining)
        if remaining and action.get("elevated_retry", False):
            if self.stop_event.is_set():
                raise PlaybackStopped()
            self._status("普通权限无法结束剩余进程，尝试管理员权限结束（可能弹出 UAC 授权窗口）")
            _code, error = kill_targets(remaining, force=True, elevated=True)
            elevated_deadline = time.perf_counter() + 8.0
            while pending_targets(remaining) and time.perf_counter() < elevated_deadline:
                if self.stop_event.is_set():
                    raise PlaybackStopped()
                self._wait(50)
            remaining = pending_targets(remaining)
        if remaining:
            raise RuntimeError(f"无法结束进程 PID：{', '.join(str(p.pid) for p in remaining)}；{error}")
        self._log_event(f"已强制结束 {image_name}、子进程及关联进程")

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
