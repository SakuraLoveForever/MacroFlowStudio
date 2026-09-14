from __future__ import annotations

from pathlib import Path
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
from macroflow.core.storage import (
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS, load_script, registered_module_object,
    registered_template_region, resolve_path,
)
import time

from .base import (
    scale_screen_point,
    screen_template_scale,
)

class WindowMixin:
    """显示分辨率 / 缩放、前置窗口与坐标缩放。"""

    def _ensure_display_resolution(self, hwnd: int, width: int, height: int,
                                   refresh_rate: int, label: str) -> bool:
        """确保显示器处于目标分辨率；已经是目标值就跳过（不再重复切换）。"""
        current = get_display_resolution_for_window(hwnd)
        if current is not None and current[0] == width and current[1] == height \
                and (not refresh_rate or current[2] == refresh_rate):
            self._log_event(f"{label}已经是 {width}×{height}，无需切换分辨率。")
            return True
        if set_display_resolution_for_window(hwnd, width, height, refresh_rate):
            self._log_event(f"{label}已切换到 {width}×{height}。")
            return True
        # 个别驱动对"设置成当前值"返回失败：只要最终状态正确就算成功。
        current = get_display_resolution_for_window(hwnd)
        if current is not None and current[0] == width and current[1] == height:
            self._log_event(f"{label}已经是 {width}×{height}，无需切换分辨率。")
            return True
        return False
    def _ensure_display_scaling(self, hwnd: int, scale_percent: int, label: str) -> None:
        """确保缩放为目标值。

        缩放已经正确就跳过；部分显示器不支持程序化修改缩放（CCD 的
        SET_DPI_SCALE 返回失败），此时只记录提示、不中断工作流——坐标按物理
        像素计算，缩放不影响回放坐标。
        """
        current = get_display_scaling_for_window(hwnd)
        if current == scale_percent:
            self._log_event(f"{label}缩放已经是 {scale_percent}%，无需切换。")
            return
        if set_display_scaling_for_window(hwnd, scale_percent):
            self._log_event(f"{label}缩放已切换到 {scale_percent}%。")
            return
        current = get_display_scaling_for_window(hwnd)
        if current == scale_percent:
            return
        self._log_event(
            f"{label}缩放当前为 {current if current is not None else '未知'}%，"
            f"无法自动切换到 {scale_percent}%（该显示器不支持程序化修改缩放），已跳过。"
        )
    def _request_resolution_monitor(self) -> int | None:
        """分辨率动作要改哪块屏：应用层返回的"软件所在显示器"窗口句柄。"""
        if self.on_resolution_monitor_request is None:
            return None
        try:
            hwnd = self.on_resolution_monitor_request()
        except Exception:
            return None
        return int(hwnd) if hwnd and is_window(hwnd) else None
    def _scale_point(self, x: int, y: int) -> tuple[int, int]:
        return scale_screen_point(x, y, self._source_screen, self._target_screen)
    def _template_scale(self) -> float:
        """当前播放上下文的模板缩放系数（录制屏幕 → 当前屏幕宽度比）。"""
        return screen_template_scale(self._source_screen, self._target_screen)
    def _scale_region(self, region: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x, y, width, height = region
        left, top = self._scale_point(x, y)
        right, bottom = self._scale_point(x + width, y + height)
        return left, top, max(1, right - left), max(1, bottom - top)
    def _input_target_hwnd(self, hwnd: int | None) -> int | None:
        """当前输入的目标窗口：相对轨迹目标优先，否则绑定窗口；仅返回有效窗口。"""
        candidate = self._relative_target_hwnd or hwnd
        return int(candidate) if candidate and is_window(candidate) else None
    def _log_foreground_thief(self) -> None:
        """把当前占据前台的窗口写进日志（限频 10s），便于排查失焦来源。

        例如第三方工具定时弹出的黑色命令行窗口抢走游戏前台时，日志会
        记录它的标题与进程名，用户据此定位是哪个程序在抢焦点。
        """
        now = time.perf_counter()
        if now - self._last_thief_log_time < 10.0:
            return
        self._last_thief_log_time = now
        info = get_foreground_window_info()
        if info is None:
            return
        title = (info.title or info.class_name or "无标题窗口").strip()
        image = Path(info.process_path).name if info.process_path else ""
        desc = f"「{title}」({image})" if image else f"「{title}」"
        self._log_event(f"执行期间前台不在目标窗口：当前为 {desc}，已自动抢回。")
    def _ensure_foreground_for_input(self, hwnd: int | None) -> None:
        """发送输入前确保目标窗口在前台（开启执行前置时）。

        前台守卫只做进程级比较：目标窗口不在前台才激活，避免每个动作都
        SetForegroundWindow 拖慢播放。焦点一旦被抢（其他软件弹窗、误点
        桌面、小窗/通知闪现），下一个输入动作前自动把目标抢回前台，
        游戏不会再进入“点击游戏画面继续操作”的失焦暂停。
        """
        if not self._activate_target:
            return
        target = self._input_target_hwnd(hwnd)
        if target and not is_window_process_foreground(target):
            self._log_foreground_thief()
            if not activate_window(target):
                self._status("未能重新前置目标窗口，将继续尝试发送输入")
            else:
                self._relative_target_hwnd = target
    def _center_cursor_for_turn(self, hwnd: int | None) -> None:
        """Move the cursor to the target window's center before a relative turn.

        录制转向时视角以锁中心的光标为起点，ΔX/ΔY 从屏幕中心起算；回放若从
        屏幕边缘出发，同样的位移会被桌面边界截短，游戏收到的转向量不足
        （上次转向停下的边缘、界面操作把光标留在角落都会触发）。先从中心
        起转即可完整重现录制时的位移；锁中心类游戏会自行把光标拉回中心，
        不受影响。
        """
        if not self._activate_target:
            return
        target = self._input_target_hwnd(hwnd)
        if not target:
            return
        rect = get_window_rect(target)
        if not rect or rect[2] <= 2 or rect[3] <= 2:
            return
        set_cursor_pos(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
    def _clamp_click_point(self, x: int, y: int, hwnd: int | None) -> tuple[int, int]:
        """把绝对鼠标坐标（点击 / 移动 / 滚轮位置）收敛进目标窗口，防止操作越界抢走游戏焦点。

        坐标越界时夹到窗口边缘并提示；未启用执行前置或没有目标窗口时原样返回。
        """
        if not self._activate_target:
            return x, y
        target = self._input_target_hwnd(hwnd)
        if not target:
            return x, y
        rect = get_window_rect(target)
        if not rect or rect[2] <= 2 or rect[3] <= 2:
            return x, y
        left, top, width, height = rect
        x, y = int(x), int(y)
        clamped_x = max(left + 1, min(x, left + width - 2))
        clamped_y = max(top + 1, min(y, top + height - 2))
        if clamped_x != x or clamped_y != y:
            self._status(
                f"坐标 ({x}, {y}) 超出目标窗口，已收敛到窗口内 ({clamped_x}, {clamped_y})",
            )
        return clamped_x, clamped_y
    def _restore_target_foreground(self, hwnd: int | None) -> None:
        """点击后立即确认目标窗口仍在前台且未被最小化。

        点击若命中窗口自身的最小化 / 窗口化 / 关闭按钮、任务栏或桌面等
        控件，激活窗口会被抢走，游戏随即弹出“点击游戏画面继续操作”的
        失焦遮罩。这里在每次点击后立刻检查并恢复（activate_window 会顺带
        把最小化的窗口 SW_RESTORE），把失焦时间压缩到几十毫秒内，让游戏
        来不及进入失焦暂停。
        """
        if not self._activate_target:
            return
        target = self._input_target_hwnd(hwnd)
        if target and not is_window_process_foreground(target):
            self._log_foreground_thief()
            if not activate_window(target):
                self._status("点击后未能恢复目标窗口前台，游戏可能已失焦。")
    def _template_region(self, template_path: Path) -> tuple[int, int, int, int] | None:
        """已登记模板的检测区域（模板登记表实时读取）；未登记/未设置区域则全屏并告警。"""
        region = registered_template_region(template_path)
        if region and region[2] > 0 and region[3] > 0:
            return self._scale_region(tuple(map(int, region)))
        self._status(f"模板 {Path(template_path).name} 未设置区域，按全屏识别")
        return None
