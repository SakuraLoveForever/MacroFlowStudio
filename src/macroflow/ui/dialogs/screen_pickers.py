from __future__ import annotations

from PIL import Image, ImageEnhance, ImageTk
from macroflow.core.image_match import capture_bgr
from macroflow.input.wininput import (
    WindowInfo, enum_windows, get_cursor_pos,
    get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect,
    get_virtual_screen_rect, is_current_process_window, make_window_no_activate,
    set_dark_titlebar, set_rounded_window, show_window_no_activate,
    window_from_point,
)
import tkinter as tk

from .base import (
    FONT_FAMILY,
    FONT_TITLE,
    drag_selection_region,
    restore_modal_after_overlay,
    show_floating_notice,
)


class ScreenPointPicker:
    """Full-screen screenshot curtain that records one or two screen points.

    Hides the modal dialog and the main window while picking, then reports the
    picked screen coordinate(s) through ``on_result``. With ``two_points`` the
    first click records the start point and the second click the end point.
    """

    def __init__(self, owner, main, on_result, two_points: bool = False,
                 tip_text: str = "",
                 hidden_windows: list | None = None):
        # owner may be None when the picker is started from the main window
        # itself (no modal dialog to hide and restore).
        self.owner = owner
        self.main = main
        self.on_result = on_result
        self.two_points = two_points
        self.tip_text = tip_text
        self.overlay = None
        self.screenshot = None
        self.canvas = None
        self.tip_id = None
        self.main_previous_state = "normal"
        self.hidden_windows = list(hidden_windows or [])
        self.hidden_alphas: list[float] = []
        self.first_point = None
        self.origin = (0, 0)

    def start(self):
        if self.overlay is not None:
            return
        try:
            self.main_previous_state = str(self.main.state())
            self.hidden_alphas = []
            for window in self.hidden_windows:
                try:
                    self.hidden_alphas.append(float(window.attributes("-alpha")))
                except (TypeError, ValueError, tk.TclError):
                    self.hidden_alphas.append(1.0)
                window.attributes("-alpha", 0.0)
            if self.owner is not None:
                self.owner.grab_release()
                self.owner.withdraw()
            self.main.withdraw()
            self.main.update_idletasks()
            self.main.after(100, self._show_curtain)
        except Exception as exc:
            self.close()
            if self.owner is not None:
                show_floating_notice(self.owner, "无法选取位置", str(exc))

    def _show_curtain(self):
        try:
            screen, origin = capture_bgr()
            image = Image.fromarray(screen[:, :, ::-1])
            image = ImageEnhance.Brightness(image).enhance(0.62)
            overlay = tk.Toplevel(self.main)
            self.overlay = overlay
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.configure(background="#000000", cursor="crosshair")
            width, height = image.size
            left, top = int(origin[0]), int(origin[1])
            self.origin = (left, top)
            overlay.geometry(f"{width}x{height}{left:+d}{top:+d}")
            self.screenshot = ImageTk.PhotoImage(image, master=overlay)
            canvas = tk.Canvas(
                overlay, width=width, height=height,
                highlightthickness=0, cursor="crosshair",
            )
            canvas.pack(fill="both", expand=True)
            canvas.create_image(0, 0, image=self.screenshot, anchor="nw")
            self.canvas = canvas
            self.tip_id = canvas.create_text(
                width // 2, 34,
                text=self.tip_text or (
                    "点击要记录的位置；只记录坐标，不会点击下方窗口；Esc 取消"
                    if not self.two_points else
                    "第一次点击记录起点，移动光标到终点后再次点击；Esc 取消"
                ),
                fill="#FFFFFF", font=(FONT_FAMILY, FONT_TITLE, "bold"),
            )
            canvas.bind("<Button-1>", self._on_click)
            overlay.bind("<Escape>", lambda _event: self.close())
            overlay.update_idletasks()
            overlay.lift()
            overlay.focus_force()
            overlay.grab_set()
        except Exception as exc:
            self.close()
            if self.owner is not None:
                show_floating_notice(self.owner, "无法截取幕布", str(exc))

    def _on_click(self, event):
        x, y = int(event.x_root), int(event.y_root)
        if not self.two_points:
            self.close()
            self.on_result(x, y)
            return
        if self.first_point is None:
            self.first_point = (x, y)
            if self.canvas is not None and self.tip_id is not None:
                self.canvas.itemconfigure(
                    self.tip_id,
                    text=f"起点 ({x}, {y}) 已记录，移动光标到终点后再次点击；Esc 取消",
                )
            return
        start_x, start_y = self.first_point
        self.close()
        self.on_result(start_x, start_y, x, y)

    def close(self):
        overlay = self.overlay
        self.overlay = None
        self.screenshot = None
        self.canvas = None
        self.tip_id = None
        self.first_point = None
        if overlay is not None:
            try:
                overlay.grab_release()
                overlay.destroy()
            except tk.TclError:
                pass
        for window, previous_state in zip(
            reversed(getattr(self, "hidden_windows", [])),
            reversed(getattr(self, "hidden_alphas", [])),
        ):
            try:
                window.attributes("-alpha", previous_state)
                window.update_idletasks()
            except (TypeError, ValueError, tk.TclError):
                pass
        if self.owner is not None:
            restore_modal_after_overlay(self.owner, self.main, self.main_previous_state)
            try:
                self.owner.after(30, lambda: restore_modal_after_overlay(
                    self.owner, self.main, self.main_previous_state,
                ))
            except tk.TclError:
                pass
        else:
            try:
                self.main.deiconify()
                if self.main_previous_state == "zoomed":
                    self.main.state("zoomed")
                self.main.update_idletasks()
                show_window_no_activate(int(self.main.winfo_id()))
            except (TypeError, ValueError, tk.TclError):
                pass


class ScreenRegionPicker:
    """Full-screen curtain that records a drag-selected rectangle.

    Mirrors the image-action region picker: press the left button at the
    top-left corner, drag to the bottom-right corner and release. Reports the
    region as ``[x, y, w, h]`` through ``on_result``; Esc cancels.

    While selecting, the whole window chain is hidden — the owner dialog, the
    main window and every ancestor dialog up to the app main window
    (``hidden_windows``) — so the screen is unobstructed for drag selection
    and ``on_result`` can capture a clean screenshot before the windows come
    back.
    """

    def __init__(self, owner, main, on_result, tip_text: str = "",
                 hidden_windows: list | None = None):
        self.owner = owner
        self.main = main
        self.on_result = on_result
        self.tip_text = tip_text
        self.overlay = None
        self.canvas = None
        self.tip_id = None
        self.rectangle_id = None
        self.drag_start = None
        self.main_previous_state = "normal"
        # 框选期间需要一并隐藏的上层窗口（从 main 的父窗口一直数到应用主窗口）。
        self.hidden_windows = list(hidden_windows or [])
        self.hidden_states: list[str] = []

    def start(self):
        if self.overlay is not None:
            return
        try:
            self.main_previous_state = str(self.main.state())
            # 整条窗口链全部隐藏：否则主窗口 / 上级对话框遮挡屏幕，无法框选和截图。
            self.hidden_states = []
            for window in self.hidden_windows:
                try:
                    self.hidden_states.append(str(window.state()))
                except tk.TclError:
                    self.hidden_states.append("normal")
                window.withdraw()
            if self.owner is not None:
                self.owner.grab_release()
                self.owner.withdraw()
            self.main.withdraw()
            self.main.update_idletasks()
            self.main.after(100, self._show_overlay)
        except Exception as exc:
            self.close()
            if self.owner is not None:
                show_floating_notice(self.owner, "无法框选", str(exc))

    def _show_overlay(self):
        try:
            screen = get_virtual_screen_rect()
            left, top = int(screen["left"]), int(screen["top"])
            width, height = int(screen["width"]), int(screen["height"])
            overlay = tk.Toplevel(self.main)
            self.overlay = overlay
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            overlay.attributes("-alpha", 0.32)
            overlay.configure(background="#000000", cursor="crosshair")
            overlay.geometry(f"{width}x{height}{left:+d}{top:+d}")
            canvas = tk.Canvas(
                overlay, background="#000000", highlightthickness=0,
                cursor="crosshair",
            )
            canvas.pack(fill="both", expand=True)
            self.canvas = canvas
            self.tip_id = canvas.create_text(
                width // 2, 34,
                text=self.tip_text or (
                    "按住鼠标左键，从左上角向右下角拖动；松开完成，Esc 取消"
                ),
                fill="#FFFFFF", font=(FONT_FAMILY, FONT_TITLE, "bold"),
            )
            canvas.bind("<ButtonPress-1>", self._drag_begin)
            canvas.bind("<B1-Motion>", self._drag_move)
            canvas.bind("<ButtonRelease-1>", self._drag_finish)
            overlay.bind("<Escape>", lambda _event: self.close())
            overlay.update_idletasks()
            overlay.lift()
            overlay.focus_force()
            overlay.grab_set()
        except Exception as exc:
            self.close()
            if self.owner is not None:
                show_floating_notice(self.owner, "无法框选", str(exc))

    def _drag_begin(self, event):
        self.drag_start = (
            int(event.x_root), int(event.y_root), int(event.x), int(event.y),
        )
        if self.rectangle_id is not None:
            self.canvas.delete(self.rectangle_id)
        self.rectangle_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="#FFB020", width=4,
        )

    def _drag_move(self, event):
        if self.drag_start is None or self.rectangle_id is None:
            return
        _, _, local_x, local_y = self.drag_start
        self.canvas.coords(self.rectangle_id, local_x, local_y, event.x, event.y)
        width, height = event.x - local_x, event.y - local_y
        if self.tip_id is not None:
            self.canvas.itemconfigure(
                self.tip_id,
                text=f"区域：{max(0, width)} × {max(0, height)}　松开完成，Esc 取消",
            )

    def _drag_finish(self, event):
        if self.drag_start is None:
            return
        start_x, start_y, _, _ = self.drag_start
        region = drag_selection_region(
            start_x, start_y, int(event.x_root), int(event.y_root),
        )
        if region is None:
            self.close()
            if self.owner is not None:
                show_floating_notice(
                    self.owner, "框选无效",
                    "请按住左键，从左上角向右下角拖出一个矩形区域。",
                )
            return
        # 先移除幕布、保持应用窗口隐藏，再回调（"截图新建…"时能截到干净屏幕），
        # 最后恢复被隐藏的窗口。
        self._destroy_overlay()
        try:
            self.on_result(region)
        finally:
            self._restore_windows()

    def _destroy_overlay(self):
        overlay = self.overlay
        self.overlay = None
        self.canvas = None
        self.tip_id = None
        self.rectangle_id = None
        self.drag_start = None
        if overlay is not None:
            try:
                overlay.grab_release()
                overlay.destroy()
            except tk.TclError:
                pass

    def _restore_windows(self):
        """Restore the windows hidden while selecting (top-most one last)."""
        for window, previous_state in zip(
            reversed(self.hidden_windows), reversed(self.hidden_states),
        ):
            try:
                window.deiconify()
                if previous_state == "zoomed":
                    window.state("zoomed")
                window.update_idletasks()
                show_window_no_activate(int(window.winfo_id()))
            except (TypeError, ValueError, tk.TclError):
                pass
        if self.owner is not None:
            restore_modal_after_overlay(self.owner, self.main, self.main_previous_state)
            try:
                self.owner.after(30, lambda: restore_modal_after_overlay(
                    self.owner, self.main, self.main_previous_state,
                ))
            except tk.TclError:
                pass
        else:
            try:
                self.main.deiconify()
                if self.main_previous_state == "zoomed":
                    self.main.state("zoomed")
                self.main.update_idletasks()
                show_window_no_activate(int(self.main.winfo_id()))
            except (TypeError, ValueError, tk.TclError):
                pass

    def close(self):
        """Cancel the selection: remove the overlay and restore the hidden windows."""
        self._destroy_overlay()
        self._restore_windows()


class ScreenOffsetPicker(ScreenRegionPicker):
    """Full-screen curtain that records a press-drag-release offset vector."""

    def _drag_begin(self, event):
        self.drag_start = (
            int(event.x_root), int(event.y_root), int(event.x), int(event.y),
        )
        if self.rectangle_id is not None:
            self.canvas.delete(self.rectangle_id)
        self.rectangle_id = self.canvas.create_line(
            event.x, event.y, event.x, event.y,
            fill="#FFB020", width=4, arrow="last",
        )

    def _drag_move(self, event):
        if self.drag_start is None or self.rectangle_id is None:
            return
        start_x, start_y, local_x, local_y = self.drag_start
        self.canvas.coords(self.rectangle_id, local_x, local_y, event.x, event.y)
        dx = int(event.x_root) - start_x
        dy = int(event.y_root) - start_y
        if self.tip_id is not None:
            self.canvas.itemconfigure(
                self.tip_id,
                text=f"偏移：dx={dx:+d}，dy={dy:+d}　松开完成，Esc 取消",
            )

    def _drag_finish(self, event):
        if self.drag_start is None:
            return
        start_x, start_y, _, _ = self.drag_start
        end_x, end_y = int(event.x_root), int(event.y_root)
        if start_x == end_x and start_y == end_y:
            self.close()
            if self.owner is not None:
                show_floating_notice(
                    self.owner, "偏移无效",
                    "请按住左键从起点拖到终点，移动后再松开。",
                )
            return
        self._destroy_overlay()
        try:
            self.on_result(start_x, start_y, end_x, end_y)
        finally:
            self._restore_windows()
