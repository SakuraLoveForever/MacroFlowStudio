from __future__ import annotations

from PIL import Image, ImageEnhance, ImageTk
from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL,
    ensure_action_ids, recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
    special_action_label,
)
from pathlib import Path
from macroflow.core.image_match import capture_bgr
from datetime import datetime, timedelta
from macroflow.core.storage import (
    BASE_DIR, DIRECTION_SCRIPTS_DIR, IMAGES_DIR, SCRIPTS_DIR, display_path,
    DEFAULT_MODULE_INTERVAL_MS, DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    load_app_settings, load_module_images_dir, load_module_objects,
    load_script, load_template_regions,
    module_image_inventory, module_objects_by_category,
    registered_module_object, resolve_path, save_module_images_dir, save_module_objects,
    save_template_regions, save_script, script_category_for_path, update_module_object,
)
from tkinter import filedialog, messagebox, simpledialog, ttk
from macroflow.input.wininput import (
    WindowInfo, enum_windows, get_cursor_pos,
    get_monitor_work_area_for_point,
    get_monitor_work_area_for_window, get_primary_screen_rect,
    get_virtual_screen_rect, is_current_process_window, make_window_no_activate,
    set_dark_titlebar, set_rounded_window, show_window_no_activate,
    window_from_point,
)
import threading
import tkinter as tk

from .base import (
    COLOR_BG,
    COLOR_BLUE_SELECTION,
    COLOR_MUTED,
    COLOR_SURFACE,
    COLOR_TEXT,
    DIALOG_FRAME_MARGIN,
    FONT_BODY,
    FONT_MONO,
    GLOBAL_SCRIPT_END_LABEL,
    IMAGE_TIMEOUT_OPTIONS,
    MODULE_RESULT_OPTIONS,
    ModalDialog,
    activate_main_after_modal,
    app_windows,
    dark_checkbutton,
    duration_var,
    fit_scrollable_window_to_content,
    monitor_work_area_for,
    pad,
    place_window_on_parent,
    px,
    scrollable_dialog_body,
    show_floating_notice,
)
from .helpers import (
    _option_label,
    _option_value,
    fallback_template_options,
    image_action_option_defaults,
    image_click_target_defaults,
    image_found_jump_target_options,
    image_jump_target_options,
    image_timeout_option_defaults,
    image_timeout_option_label,
    image_timeout_option_value,
    module_result_option_label,
    module_result_option_value,
    multi_condition_field_states,
    registered_template_options,
    row_list_condition_field_states,
    select_jump_target_label,
)
from .module_objects import (
    TemplateRegionManagerDialog,
)
from .screen_pickers import (
    ScreenPointPicker,
    ScreenRegionPicker,
)
from .segments import (
    FailureSegmentMixin,
    action_with_live_module_binding,
    choose_module_binding,
    module_display_name,
    module_reference_binding,
)


class RowListDiagnosticResultDialog:
    """Show a completed row-list recognition scan without taking a modal grab."""

    def __init__(self, parent, result_lines: list[str], error: Exception | None = None):
        self.parent = parent
        self.result_lines = list(result_lines)
        self.error = error
        self.window = None

    def show(self):
        window = tk.Toplevel(self.parent)
        self.window = window
        window.title("列表逐行识别结果")
        window.configure(background=COLOR_BG)
        # 尺寸按父窗口所在显示器封顶，并摆在同一块屏上：新建顶层窗口默认落在
        # 主屏，扩展屏上打开的识别结果窗口会跑到笔记本屏幕上。
        area = monitor_work_area_for(self.parent)
        width = min(px(780), max(px(320), int(area["width"]) - px(DIALOG_FRAME_MARGIN)))
        height = min(px(520), max(px(220), int(area["height"]) - px(DIALOG_FRAME_MARGIN)))
        window.geometry(f"{width}x{height}")
        window.minsize(min(px(560), width), min(px(320), height))
        place_window_on_parent(window, self.parent, width, height)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

        body = ttk.Frame(window, padding=px(14))
        body.pack(fill="both", expand=True)
        body.rowconfigure(1, weight=1)
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body, text="识别已完成，以下结果不会执行点击。",
            foreground=COLOR_TEXT,
        ).grid(row=0, column=0, sticky="w", pady=pad(0, 8))

        text_frame = ttk.Frame(body)
        text_frame.grid(row=1, column=0, sticky="nsew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        output = tk.Text(
            text_frame, wrap="word", state="normal", background=COLOR_SURFACE,
            foreground=COLOR_TEXT, insertbackground=COLOR_TEXT,
            selectbackground=COLOR_BLUE_SELECTION, relief="flat", bd=0,
            font=(FONT_MONO, FONT_BODY), padx=px(12), pady=px(10),
        )
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=output.yview)
        output.configure(yscrollcommand=scrollbar.set)
        output.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        content = "\n".join(self.result_lines) or "没有诊断输出。"
        if self.error is not None:
            content = f"{content}\n\n识别失败：{self.error}"
        output.insert("1.0", content)
        output.configure(state="disabled")

        ttk.Button(body, text="关闭", command=window.destroy).grid(
            row=2, column=0, sticky="e", pady=pad(10, 0),
        )
        window.update_idletasks()
        set_dark_titlebar(window.winfo_id())
        window.lift()
        window.focus_force()
        return window


class RowRecognitionResultDialog:
    """Show a diagnostic image with each recognition cell's text drawn inside it."""

    def __init__(self, parent, result: dict, error: Exception | None = None):
        self.parent = parent
        self.result = dict(result or {})
        self.subject = str(self.result.get("subject") or "列表逐行")
        self.error = error
        self.window = None
        self.photo = None
        self.canvas = None
        self.toggle_button = None
        self.toolbar = None
        self._toolbar_drag_start = None
        self.overlay_visible = True
        self.zoom_level = 1.0
        self._source_image = None
        self._image_size = (0, 0)
        self._viewport_size = (1, 1)
        self._image_origin = (0, 0)
        self._cells = []
        self._display_scale = 1.0
        self._display_size = (0, 0)

    @staticmethod
    def cell_label(cell: dict) -> str:
        """Format the text shown over one diagnostic cell."""
        row = cell.get("row", "?")
        column = cell.get("column", "?")
        text = str(cell.get("text") or "未识别到文字")
        return f"第{row}行第{column}列\n{text}"

    @staticmethod
    def cell_text(cell: dict) -> str:
        """Return only OCR text so row and column headers do not crowd the cell."""
        return str(cell.get("text") or "未识别到文字")

    @staticmethod
    def visible_result_columns(result: dict) -> list[int]:
        """Return left/right recognition columns, excluding the click column."""
        try:
            click_column = int(result.get("click_column"))
        except (TypeError, ValueError):
            click_column = None
        columns = []
        for key in ("left_column", "right_column"):
            try:
                column = int(result.get(key))
            except (TypeError, ValueError):
                continue
            if column != click_column and column not in columns:
                columns.append(column)
        return columns

    @staticmethod
    def display_scale(
        image_size: tuple[int, int],
        viewport_size: tuple[int, int],
        zoom: float = 1.0,
    ) -> float:
        """Fit the complete selected image, then apply a uniform zoom factor."""
        image_width, image_height = map(float, image_size)
        viewport_width, viewport_height = map(float, viewport_size)
        if image_width <= 0 or image_height <= 0:
            return 1.0
        fit_scale = min(
            1.0,
            viewport_width / image_width if viewport_width > 0 else 1.0,
            viewport_height / image_height if viewport_height > 0 else 1.0,
        )
        bounded_zoom = min(4.0, max(0.25, float(zoom)))
        return max(0.01, fit_scale * bounded_zoom)

    @staticmethod
    def resolve_viewport_size(
        canvas_size: tuple[int, int], window_size: tuple[int, int],
    ) -> tuple[int, int]:
        """Use the real canvas size, or a safe geometry fallback before layout."""
        canvas_width, canvas_height = map(int, canvas_size)
        if canvas_width > 2 and canvas_height > 2:
            return canvas_width, canvas_height
        window_width, window_height = map(int, window_size)
        return max(1, window_width - 80), max(1, window_height - 150)

    @staticmethod
    def next_zoom(current_zoom: float, delta: int) -> float:
        """Return the next bounded zoom level for one mouse-wheel event."""
        if delta == 0:
            return min(4.0, max(0.25, float(current_zoom)))
        step = 1.15 if delta > 0 else 1 / 1.15
        return min(4.0, max(0.25, float(current_zoom) * step))

    def _start_toolbar_drag(self, event):
        if self.toolbar is None:
            return
        self._toolbar_drag_start = (
            event.x_root, event.y_root,
            self.toolbar.winfo_x(), self.toolbar.winfo_y(),
        )

    def _drag_toolbar(self, event):
        if self.toolbar is None or self._toolbar_drag_start is None:
            return
        start_x, start_y, origin_x, origin_y = self._toolbar_drag_start
        max_x = max(0, self.window.winfo_width() - self.toolbar.winfo_width())
        max_y = max(0, self.window.winfo_height() - self.toolbar.winfo_height())
        x = max(0, min(max_x, origin_x + event.x_root - start_x))
        y = max(0, min(max_y, origin_y + event.y_root - start_y))
        self.toolbar.place(x=x, y=y)

    def _toggle_display_mode(self):
        """Toggle the OCR/grid overlay while keeping the selected image visible."""
        self.overlay_visible = not self.overlay_visible
        state = "normal" if self.overlay_visible else "hidden"
        self.canvas.itemconfigure("grid-result", state=state)
        self.toggle_button.configure(
            text="显示原图" if self.overlay_visible else "显示识别结果",
        )

    def _render_canvas(self, scale: float):
        """Render the original image and the aligned grid overlay at one scale."""
        if self.canvas is None or self._source_image is None:
            return
        image_width, image_height = self._image_size
        display_size = (
            max(1, round(image_width * scale)),
            max(1, round(image_height * scale)),
        )
        image = self._source_image
        if display_size != image.size:
            image = image.resize(display_size, Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(image, master=self.window)
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, *display_size))
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        visible_columns = set(self.visible_result_columns(self.result))
        for cell in self._cells:
            try:
                column_index = int(cell.get("column")) - 1
            except (TypeError, ValueError):
                continue
            if column_index not in visible_columns:
                continue
            x, y, width, height = map(int, cell["region"])
            x1 = round((x - self._image_origin[0]) * scale)
            y1 = round((y - self._image_origin[1]) * scale)
            x2 = round((x + width - self._image_origin[0]) * scale)
            y2 = round((y + height - self._image_origin[1]) * scale)
            matched = cell.get("matched")
            color = "#52D273" if matched is True else "#FF6978" if matched is False else "#F2C94C"
            self.canvas.create_rectangle(
                x1, y1, x2, y2, fill=COLOR_SURFACE,
                outline=color, width=2, tags="grid-result",
            )
            cell_width = max(1, x2 - x1)
            cell_height = max(1, y2 - y1)
            font_size = max(8, min(18, round(min(cell_width, cell_height) * 0.24)))
            self.canvas.create_text(
                (x1 + x2) // 2, (y1 + y2) // 2,
                text=self.cell_text(cell), fill=color,
                width=max(20, cell_width - 10),
                font=("Microsoft YaHei UI", font_size, "bold"),
                justify="center", tags="grid-result",
            )
        self.canvas.itemconfigure(
            "grid-result", state="normal" if self.overlay_visible else "hidden",
        )
        self._display_scale = scale
        self._display_size = display_size

    def _on_mouse_wheel(self, event):
        """Zoom around the pointer while keeping the selected image's aspect ratio."""
        delta = int(getattr(event, "delta", 0) or 0)
        if self.canvas is None or self._source_image is None or delta == 0:
            return "break"
        next_zoom = self.next_zoom(self.zoom_level, delta)
        if next_zoom == self.zoom_level:
            return "break"
        old_scale = self._display_scale
        pointer_x = self.canvas.canvasx(event.x)
        pointer_y = self.canvas.canvasy(event.y)
        self.zoom_level = next_zoom
        new_scale = self.display_scale(
            self._image_size, self._viewport_size, self.zoom_level,
        )
        self._render_canvas(new_scale)
        self.canvas.update_idletasks()
        image_x = pointer_x / old_scale
        image_y = pointer_y / old_scale
        target_x = image_x * new_scale - event.x
        target_y = image_y * new_scale - event.y
        horizontal_range = max(1, self._display_size[0] - self.canvas.winfo_width())
        vertical_range = max(1, self._display_size[1] - self.canvas.winfo_height())
        self.canvas.xview_moveto(max(0.0, min(1.0, target_x / horizontal_range)))
        self.canvas.yview_moveto(max(0.0, min(1.0, target_y / vertical_range)))
        return "break"

    def _on_canvas_configure(self, event):
        """Refresh the fit scale once Tk has assigned the actual canvas size."""
        if self.canvas is None or self._source_image is None:
            return
        viewport_size = self.resolve_viewport_size(
            (event.width, event.height),
            (self.window.winfo_width(), self.window.winfo_height()),
        )
        if viewport_size == self._viewport_size:
            return
        self._viewport_size = viewport_size
        self._render_canvas(
            self.display_scale(self._image_size, viewport_size, self.zoom_level),
        )

    def show(self):
        window = tk.Toplevel(self.parent)
        self.window = window
        window.configure(background=COLOR_BG)
        # 不用 Tk 的 -fullscreen：多屏下它按主屏/虚拟桌面铺满，识别结果窗口会
        # 跑到另一块屏上。直接铺满"父窗口所在显示器"的可用区域，与主界面同屏。
        area = monitor_work_area_for(self.parent)
        window.overrideredirect(True)
        window.geometry(
            f"{int(area['width'])}x{int(area['height'])}"
            f"+{int(area['left'])}+{int(area['top'])}"
        )
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.bind("<Escape>", lambda _event: window.destroy())
        try:
            window.grab_set()
        except tk.TclError:
            pass

        canvas_frame = ttk.Frame(window)
        canvas_frame.pack(fill="both", expand=True)
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        canvas = tk.Canvas(
            canvas_frame, background=COLOR_SURFACE, highlightthickness=0,
        )
        horizontal_scrollbar = ttk.Scrollbar(
            canvas_frame, orient="horizontal", command=canvas.xview,
        )
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(
            xscrollcommand=horizontal_scrollbar.set, yscrollcommand=scrollbar.set,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal_scrollbar.grid(row=1, column=0, sticky="ew")
        self.canvas = canvas
        canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        canvas.bind("<Configure>", self._on_canvas_configure)

        toolbar = tk.Frame(
            window, background=COLOR_BG, highlightbackground=COLOR_TEXT,
            highlightthickness=1, bd=0, padx=px(6), pady=px(5),
        )
        self.toolbar = toolbar
        toolbar.place(x=18, y=18)
        handle = tk.Label(
            toolbar, text=f"☰ {self.subject}识别", background=COLOR_BG,
            foreground=COLOR_TEXT, cursor="fleur", padx=px(6),
        )
        handle.grid(row=0, column=0, padx=pad(0, 8))
        handle.bind("<ButtonPress-1>", self._start_toolbar_drag)
        handle.bind("<B1-Motion>", self._drag_toolbar)
        self.toggle_button = ttk.Button(
            toolbar, text="显示原图", command=self._toggle_display_mode,
        )
        self.toggle_button.grid(row=0, column=1, padx=pad(0, 6))
        ttk.Button(toolbar, text="关闭", command=window.destroy).grid(
            row=0, column=2,
        )

        try:
            array = self.result.get("image_array")
            if array is not None:
                # 屏幕截图直接用内存里的 BGR 数组，避免为一次测试落临时文件。
                self._source_image = Image.fromarray(array[:, :, ::-1]).convert("RGB")
            else:
                self._source_image = Image.open(self.result["image_path"]).convert("RGB")
            self._image_size = self._source_image.size
            self._cells = list(self.result.get("cells", []))
            self._image_origin = tuple(map(int, self.result.get("image_origin", (0, 0))))
            window.update_idletasks()
            canvas.update_idletasks()
            self._viewport_size = (
                self.resolve_viewport_size(
                    (canvas.winfo_width(), canvas.winfo_height()),
                    (
                        max(window.winfo_width(), window.winfo_reqwidth()),
                        max(window.winfo_height(), window.winfo_reqheight()),
                    ),
                )
            )
            self.zoom_level = 1.0
            self._render_canvas(self.display_scale(self._image_size, self._viewport_size))
        except Exception as exc:
            error_text = self.error or exc
            ttk.Label(
                toolbar, text=f"无法显示{self.subject}底图：{error_text}",
                foreground="#FF6978",
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=pad(5, 0))

        if self.error is not None:
            ttk.Label(
                toolbar, text=f"识别失败：{self.error}", foreground="#FF6978",
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=pad(5, 0))
        window.update_idletasks()
        set_dark_titlebar(window.winfo_id())
        toolbar.lift()
        window.lift()
        window.focus_force()
        return window


class GlobalDetectDialog(ModalDialog):
    """Configure a global-detection trigger.

    require_click=False 为脚本"触发条件"模式：只配置识别设置，点击等操作写在
    脚本语句体里，触发后依次执行语句体。

    jump=True 为普通脚本内嵌"全局模块"行模式：播放到该行时启用检测，
    触发后跳转到脚本第 N 行继续执行，无点击。
    """

    def __init__(self, parent, action: dict | None = None, require_click: bool = True,
                 jump: bool = False, actions: list[dict] | None = None):
        self.jump = bool(jump)
        self.require_click = bool(require_click) and not self.jump
        if self.jump:
            title, height = "添加全局模块", 450
        elif not self.require_click:
            title, height = "设置触发条件", 410
        else:
            title, height = "添加全局检测", 470
        super().__init__(parent, title, 580, height)
        action = action_with_live_module_binding(action)
        region = action.get("region", [])
        try:
            region_text = ",".join(str(int(part)) for part in region) if len(region) == 4 else ""
        except (TypeError, ValueError):
            region_text = ""
        click_point = action.get("click_point", [])
        try:
            click_text = ",".join(str(int(part)) for part in click_point) if len(click_point) == 2 else ""
        except (TypeError, ValueError):
            click_text = ""
        # 旧配置没有 region_mode：有区域按自定义区域，否则按全屏。
        default_mode = "custom" if len(region) == 4 else "screen"
        self.region_mode = tk.StringVar(value=str(action.get("region_mode", default_mode)))
        saved_module_key = str(action.get("module_key", "")).strip()
        saved_module = registered_module_object(saved_module_key) if saved_module_key else None
        self.module_key = tk.StringVar(value=saved_module_key)
        self.module_name = tk.StringVar(value=module_display_name(saved_module_key, saved_module))
        self.template = tk.StringVar(value=str(action.get("template", "")))
        self.threshold = tk.StringVar(value=str(action.get("threshold", 0.85)))
        self.interval = duration_var(action.get("interval_ms", 500))
        self.region = tk.StringVar(value=region_text)
        self.hold = duration_var(action.get("hold_ms", 1000))
        self.click_point = tk.StringVar(value=click_text)
        # 旧配置没有 restart_delay_ms：默认 1000 ms（与 app.DEFAULT_GLOBAL_CLICK_DELAY_MS 一致）。
        self.restart_delay = duration_var(action.get("restart_delay_ms", 1000))
        try:
            jump_row = max(1, int(action.get("jump_row", 1)))
        except (TypeError, ValueError):
            jump_row = 1
        self.jump_row = tk.StringVar(value=str(jump_row))
        self.jump_enabled_var = tk.BooleanVar(value=bool(action.get("jump_enabled", False)))
        self.jump_target_combo = None
        self.picker = None

        body, self._form_canvas, form_scrollbar = scrollable_dialog_body(self, padding=14)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="模块" if self.jump else "模板").grid(
            row=0, column=0, sticky="w", pady=px(8),
        )
        template_row = ttk.Frame(body)
        template_row.grid(row=0, column=1, sticky="ew")
        if self.jump:
            ttk.Label(
                template_row, textvariable=self.module_name, foreground=COLOR_MUTED,
            ).pack(side="left", fill="x", expand=True)
            ttk.Button(
                template_row, text="选择模块…", command=self.select_image_module,
            ).pack(side="left", padx=pad(6, 0))
        else:
            self.template_combo = ttk.Combobox(
                template_row, textvariable=self.template,
                values=registered_template_options(str(action.get("template", ""))),
                state="readonly",
            )
            self.template_combo.pack(side="left", fill="x", expand=True)
            self.template_combo.bind(
                "<<ComboboxSelected>>", lambda _event: self._clear_image_module_binding(),
            )
            ttk.Button(
                template_row, text="选择模块…", command=self.select_image_module,
            ).pack(side="left", padx=pad(6, 0))
            ttk.Button(template_row, text="模板区域…", command=self.open_template_region_manager).pack(
                side="left", padx=pad(6, 0),
            )

        rows = [
            ("相似度", self.threshold, 0.1, 1.0, 0.05),
            ("检测间隔", self.interval, 100, 10000, 100),
            ("持续超过", self.hold, 0, 60000, 100),
        ]
        for offset, (label, variable, low, high, increment) in enumerate(rows, start=1):
            ttk.Label(body, text=label).grid(row=offset, column=0, sticky="w", pady=px(8))
            ttk.Spinbox(
                body, from_=low, to=high, increment=increment,
                textvariable=variable, width=10,
            ).grid(row=offset, column=1, sticky="ew")
        if self.require_click:
            ttk.Label(body, text="点击后延时").grid(row=4, column=0, sticky="w", pady=px(8))
            ttk.Spinbox(
                body, from_=0, to=60000, increment=100,
                textvariable=self.restart_delay, width=10,
            ).grid(row=4, column=1, sticky="ew")

        jump_row_index = None
        if self.jump:
            # 全局模块行：触发后可跳转到脚本的某一行对象（按动作唯一标识引用），
            # 从该行继续播放到脚本末尾后结束；也可取消勾选只触发不跳转。
            jump_row_index = 5 if self.require_click else 4
            ttk.Checkbutton(
                body, text="启用触发后跳转", variable=self.jump_enabled_var,
                command=self._sync_jump_target_state,
            ).grid(row=jump_row_index, column=0, sticky="w", pady=px(8))
            jump_row_frame = ttk.Frame(body)
            jump_row_frame.grid(row=jump_row_index, column=1, sticky="ew")
            self.jump_target_ids: dict[str, str] = {}
            self.jump_row_numbers: dict[str, int] = {}
            action_list = actions or []
            normal_options = image_jump_target_options(action_list)
            jump_options = normal_options + [
                (GLOBAL_SCRIPT_END_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID),
            ]
            if jump_options:
                for row_number, (label, action_id) in enumerate(normal_options, start=1):
                    self.jump_target_ids[label] = action_id
                    self.jump_row_numbers[label] = row_number
                self.jump_target_ids[GLOBAL_SCRIPT_END_LABEL] = NEXT_WORKFLOW_STEP_TARGET_ID
                self.jump_row_numbers[GLOBAL_SCRIPT_END_LABEL] = len(action_list) + 1
                saved_target_id = str(action.get("jump_action_id", "")).strip()
                if saved_target_id == NEXT_WORKFLOW_STEP_TARGET_ID or (
                        not saved_target_id and jump_row > len(action_list)):
                    selected_target = GLOBAL_SCRIPT_END_LABEL
                elif normal_options:
                    selected_target = select_jump_target_label(
                        saved_target_id, jump_row, normal_options,
                    )
                else:
                    selected_target = GLOBAL_SCRIPT_END_LABEL
                self.jump_row = tk.StringVar(value=selected_target)
                self.jump_target_combo = ttk.Combobox(
                    jump_row_frame, textvariable=self.jump_row,
                    values=[label for label, _ in jump_options], state="readonly",
                    width=48,
                )
                self.jump_target_combo.pack(side="left")
                ttk.Label(
                    jump_row_frame,
                    text="（引用脚本结束本次并进入下一次；顶层脚本进入工作流下一项）",
                    foreground=COLOR_MUTED,
                ).pack(side="left", padx=pad(6, 0))
            else:
                # 脚本里没有可跳转的行（防御）：退回数字行号输入。
                self.jump_target_combo = ttk.Spinbox(
                    jump_row_frame, from_=1, to=99999, textvariable=self.jump_row, width=8,
                )
                self.jump_target_combo.pack(side="left")
                ttk.Label(
                    jump_row_frame, text="行", foreground=COLOR_MUTED,
                ).pack(side="left", padx=pad(6, 0))
                ttk.Label(
                    jump_row_frame, text="（跳转后继续播放到脚本末尾）", foreground=COLOR_MUTED,
                ).pack(side="left", padx=pad(6, 0))
            self._sync_jump_target_state()

        hint_row_index = 6 if self.require_click else (5 if self.jump else 4)
        if self.require_click:
            ttk.Label(body, text="点击位置 (x,y) 留空=点识别处").grid(row=5, column=0, sticky="w", pady=px(8))
            click_row = ttk.Frame(body)
            # 与标签同行（row 5）；按钮行在 hint_row_index + 1 = row 7，
            # 若放在 row 7 会与按钮行重叠，输入框和“点击屏幕选取…”被遮住。
            click_row.grid(row=5, column=1, sticky="ew")
            ttk.Entry(click_row, textvariable=self.click_point, state="readonly").pack(
                side="left", fill="x", expand=True,
            )
            ttk.Button(click_row, text="点击屏幕选取…", command=self.pick_click_point).pack(
                side="left", padx=pad(6, 0),
            )
            hint_text = (
                "该模块会启用全局检测：所选模板在检测区域内持续出现超过设定时长后，点击指定位置并延时；"
                "检测区域来自模板（模板图片 + 框选区域），可在“模板区域…”中管理；"
                "作为全局模块时，触发后先执行模块步骤，再继续原工作流。"
            )
        elif self.jump:
            hint_text = (
                "该行是全局模块：脚本播放到本行时启用全局检测，所选模板在检测区域内持续出现超过"
                "设定时长后，跳转到所选的行继续执行脚本；播放到末尾后脚本结束。"
            )
        else:
            hint_text = (
                "该脚本是全局脚本：所选模板在检测区域内持续出现超过设定时长后触发，"
                "依次执行脚本内的所有动作（语句体）；执行完继续检测，触发条件仍满足则再次触发。"
            )
        ttk.Label(
            body,
            text=hint_text,
            foreground=COLOR_MUTED, wraplength=px(480),
        ).grid(row=hint_row_index, column=0, columnspan=2, sticky="w", pady=pad(12, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=hint_row_index + 1, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        fit_scrollable_window_to_content(
            self, parent, body, form_scrollbar, align_top=True,
        )

    def open_template_region_manager(self):
        TemplateRegionManagerDialog(self).show()
        self._refresh_template_options()

    def _clear_image_module_binding(self):
        module_key = getattr(self, "module_key", None)
        if module_key is not None:
            module_key.set("")
        module_name = getattr(self, "module_name", None)
        if module_name is not None:
            module_name.set("未选择模块")

    def select_image_module(self):
        categories = (
            ("workflow_global", "script_global")
            if getattr(self, "jump", False)
            else ("switch", "workflow_global", "script_global")
        )
        binding = choose_module_binding(
            self, categories=categories,
        )
        if not binding:
            return
        module_key = str(binding["module_key"])
        template = str(binding["template"])
        region = list(binding.get("region") or [])
        self.module_key.set(module_key)
        self.template.set(template)
        self.region_mode.set("template")
        self.region.set(",".join(map(str, region)))
        obj = registered_module_object(module_key) or {}
        self.module_name.set(module_display_name(module_key, obj))
        template_combo = getattr(self, "template_combo", None)
        if template_combo is not None:
            template_combo.configure(values=registered_template_options(template))

    def _refresh_template_options(self):
        current = self.template.get()
        if current and current not in load_template_regions():
            # 管理器里已删除/改名的模板不再可用：清空，由保存校验提示重选。
            self.template.set("")
            current = ""
        self.template_combo.configure(values=registered_template_options(current))

    def pick_click_point(self):
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_click_point,
            tip_text="点击全局检测触发时要点击的位置；只记录坐标；Esc 取消",
            # 本窗口可能是从别的对话框（模块表单 / 代码段）里打开的：
            # 整条窗口链都要让开，否则主窗口留在幕布上。
            hidden_windows=app_windows(self.master),
        )
        self.picker.start()

    def _apply_click_point(self, x, y):
        self.click_point.set(f"{int(x)},{int(y)}")

    def _sync_jump_target_state(self):
        """取消勾选“启用触发后跳转”时禁用目标选择控件。"""
        combo = self.jump_target_combo
        if combo is None:
            return
        combo.configure(
            state="readonly" if self.jump_enabled_var.get() else "disabled",
        )

    def save(self):
        try:
            module_key_var = getattr(self, "module_key", None)
            module_key = module_key_var.get().strip() if module_key_var is not None else ""
            module_binding = None
            if getattr(self, "jump", False):
                module_obj = registered_module_object(module_key) if module_key else None
                if module_obj is None or module_obj.get("category") not in (
                    "workflow_global", "script_global",
                ):
                    raise ValueError("添加全局模块只能选择工作流全局模块或脚本全局模块")
                module_binding = module_reference_binding(module_key, module_obj)
            elif module_key:
                module_obj = registered_module_object(module_key)
                if module_obj is None:
                    raise ValueError("所选图片模块已不存在，请重新选择")
                module_binding = module_reference_binding(module_key, module_obj)
            template = (
                str(module_binding["template"])
                if module_binding is not None else self.template.get().strip()
            )
            if not template:
                raise ValueError("请从列表中选择模板")
            threshold = max(0.1, min(1.0, float(self.threshold.get())))
            interval = max(100, min(10000, int(self.interval.get())))
            hold = max(0, min(60000, int(self.hold.get())))
            if module_binding is not None:
                region_mode = "template"
                region = list(module_binding["region"])
            elif template in load_template_regions():
                # 引用已登记模板：区域运行时从模板登记表实时读取。
                region_mode, region = "template", []
            else:
                # 编辑旧动作且未改动模板：保留原有区域配置。
                region_mode = self.region_mode.get()
                region = (
                    [int(part.strip()) for part in self.region.get().split(",")]
                    if self.region.get().strip() else []
                )
            if region and (len(region) != 4 or region[2] <= 0 or region[3] <= 0):
                raise ValueError("检测区域需要 x,y,w,h 四个正整数")
            if self.require_click:
                restart_delay = max(0, min(60000, int(self.restart_delay.get())))
                click_point = (
                    [int(part.strip()) for part in self.click_point.get().split(",")]
                    if self.click_point.get().strip() else []
                )
                if click_point and len(click_point) != 2:
                    raise ValueError("点击位置需要 x,y 两个整数")
            else:
                # 触发条件 / 全局模块行模式：没有点击，点击等操作写在语句体里
                # （模块行模式为触发后跳转行，见下方 jump_row）。
                restart_delay = 0
                click_point = None
            jump_action_id = ""
            if getattr(self, "jump", False):
                jump_target_ids = getattr(self, "jump_target_ids", None) or {}
                if jump_target_ids:
                    # 行对象模式：所选行映射回行号，并保存稳定的动作标识。
                    label = self.jump_row.get()
                    jump_row = self.jump_row_numbers.get(label, 1)
                    jump_action_id = jump_target_ids.get(label, "")
                else:
                    jump_row = max(1, int(self.jump_row.get()))
        except ValueError as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        result = {
            "type": "global_detect",
            "template": template,
            "threshold": threshold,
            "interval_ms": interval,
            "region_mode": region_mode,
            "region": region,
            "hold_ms": hold,
            "click_point": click_point,
            "restart_delay_ms": restart_delay,
            "delay_ms": 0,
        }
        if module_binding is not None:
            result.update({
                "module_ref": True,
                "module_key": module_key,
                "module_category": str(module_binding.get("module_category") or "switch"),
            })
        if getattr(self, "jump", False):
            jump_enabled_var = getattr(self, "jump_enabled_var", None)
            result["jump_enabled"] = (
                bool(jump_enabled_var.get()) if jump_enabled_var is not None else False
            )
            result["jump_row"] = jump_row
            if jump_action_id:
                result["jump_action_id"] = jump_action_id
        self.result = result
        self.destroy()


class ImageActionDialog(FailureSegmentMixin, ModalDialog):
    def __init__(self, parent, action: dict | None = None, actions: list[dict] | None = None):
        super().__init__(parent, "添加识图动作", 650, 950)
        action = action_with_live_module_binding(action)
        self._init_failure_segment(action)
        default_on_found, default_result_notice = image_action_option_defaults(action)
        default_click_target, default_click_point = image_click_target_defaults(action)
        (default_on_timeout, default_timeout, default_delay, default_jump_row,
         default_timeout_delay) = image_timeout_option_defaults(action)
        jump_options = image_jump_target_options(actions or [])
        found_jump_options = image_found_jump_target_options(actions or [])
        self.jump_target_ids = {
            label: action_id for label, action_id in found_jump_options
        }
        saved_target_id = str(action.get("timeout_jump_action_id", "")).strip()
        selected_target = next(
            (label for label, action_id in jump_options if action_id == saved_target_id), "",
        )
        if not selected_target and not saved_target_id:
            if "timeout_jump_row" in action and 1 <= default_jump_row <= len(jump_options):
                selected_target = jump_options[default_jump_row - 1][0]
            elif jump_options:
                selected_target = jump_options[0][0]
        saved_found_target_id = str(action.get("found_jump_action_id", "")).strip()
        selected_found_target = next(
            (label for label, action_id in found_jump_options if action_id == saved_found_target_id), "",
        )
        if not selected_found_target and not saved_found_target_id:
            try:
                found_legacy_row = int(action.get("found_jump_row", 0))
            except (TypeError, ValueError):
                found_legacy_row = 0
            if 1 <= found_legacy_row <= len(jump_options):
                selected_found_target = jump_options[found_legacy_row - 1][0]
            elif jump_options:
                selected_found_target = jump_options[0][0]
            else:
                selected_found_target = found_jump_options[0][0]
        region = action.get("region", [0, 0, 0, 0])
        saved_module_key = str(action.get("module_key", "")).strip()
        saved_module = registered_module_object(saved_module_key) if saved_module_key else None
        self.module_key = tk.StringVar(value=saved_module_key)
        self.module_name = tk.StringVar(value=(
            str((saved_module or {}).get("name") or "").strip()
            or (Path(saved_module_key.replace("\\", "/")).stem if saved_module_key else "未选择模块")
        ))
        self.template = tk.StringVar(value=str(action.get("template", "")))
        self.threshold = tk.StringVar(value=str(action.get("threshold", 0.85)))
        self.timeout = duration_var(default_timeout)
        self.interval = duration_var(action.get("interval_ms", 250))
        self.region_mode = tk.StringVar(value=str(action.get("region_mode", "screen")))
        self.region = tk.StringVar(value=",".join(map(str, region)))
        self.on_found = tk.StringVar(value=default_on_found)
        self.found_jump_target = tk.StringVar(value=selected_found_target)
        self.found_delay = duration_var(action.get("found_delay_ms", 0))
        self.click_target_mode = tk.StringVar(value=default_click_target)
        self.click_point = tk.StringVar(value=",".join(map(str, default_click_point)))
        self.on_timeout = tk.StringVar(value=image_timeout_option_label(default_on_timeout))
        self.timeout_jump_target = tk.StringVar(value=selected_target)
        self.timeout_delay = duration_var(default_timeout_delay)
        self.wait_forever = tk.BooleanVar(value=bool(action.get("wait_forever", False)))
        self.fallback_template = tk.StringVar(value=str(action.get("fallback_template", "")))
        self.fallback_switch_ms = duration_var(action.get("fallback_switch_ms", 3000))
        fallback_region = action.get("fallback_region", [0, 0, 0, 0])
        self.fallback_region_mode = tk.StringVar(value=str(action.get("fallback_region_mode", "screen")))
        self.fallback_region = tk.StringVar(value=",".join(map(str, fallback_region)))
        self.fallback_click = tk.BooleanVar(value=bool(action.get("fallback_click", True)))
        fallback_on_match = str(action.get("fallback_on_match", "回到主模板的检测"))
        if fallback_on_match not in ("回到主模板的检测", "直接退出识别"):
            fallback_on_match = "回到主模板的检测"
        self.fallback_on_match = tk.StringVar(value=fallback_on_match)
        self.button = tk.StringVar(value=str(action.get("button", "left")))
        self.delay = duration_var(default_delay)
        self.after_delay = duration_var(action.get("after_delay_ms", 0))
        self.show_result_notice = tk.BooleanVar(value=default_result_notice)
        body, _form_canvas, form_scrollbar = scrollable_dialog_body(self)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="模板").grid(row=0, column=0, sticky="w", pady=px(8))
        template_row = ttk.Frame(body)
        template_row.grid(row=0, column=1, sticky="ew")
        self.template_combo = ttk.Combobox(
            template_row, textvariable=self.template,
            values=registered_template_options(str(action.get("template", ""))),
            state="readonly",
        )
        self.template_combo.pack(side="left", fill="x", expand=True)
        self.template_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._clear_image_module_binding(),
        )
        ttk.Button(
            template_row, text="选择模块…", command=self.select_image_module,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            template_row, text="框选新建…", command=self.capture_custom_template,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(template_row, text="模板区域…", command=self.open_template_region_manager).pack(
            side="left", padx=pad(8, 0),
        )
        rows = [
            ("相似度 (0.1–1.0)", self.threshold, None),
            ("等待超时", self.timeout, None),
            ("检测间隔", self.interval, None),
            ("找到后", self.on_found, ("continue", "click", "jump")),
            ("找到后跳转目标动作", self.found_jump_target,
             tuple(label for label, _ in found_jump_options)),
            ("识别成功后等待", self.found_delay, None),
            ("点击位置", self.click_target_mode, ("识图区域中心", "自定义坐标")),
            ("自定义点击坐标 x,y", self.click_point, None),
            ("超时后", self.on_timeout, tuple(label for label, _value in IMAGE_TIMEOUT_OPTIONS)),
            ("超时后等待", self.timeout_delay, None),
            ("超时跳转目标动作", self.timeout_jump_target, tuple(label for label, _ in jump_options)),
            ("点击按钮", self.button, ("left", "right", "middle")),
            ("执行前延时", self.delay, None),
            ("执行后延时", self.after_delay, None),
        ]
        for offset, (label, variable, options) in enumerate(rows, start=1):
            ttk.Label(body, text=label).grid(row=offset, column=0, sticky="w", pady=px(8))
            if variable is self.click_point:
                point_row = ttk.Frame(body)
                point_row.grid(row=offset, column=1, sticky="ew")
                self.click_point_entry = ttk.Entry(point_row, textvariable=variable)
                self.click_point_entry.pack(side="left", fill="x", expand=True)
                self.click_point_button = ttk.Button(
                    point_row, text="幕布选取…", command=self.start_click_point_selection,
                )
                self.click_point_button.pack(side="left", padx=pad(8, 0))
            elif options or variable in (self.timeout_jump_target, self.found_jump_target):
                combo = ttk.Combobox(body, textvariable=variable, values=options, state="readonly")
                combo.grid(row=offset, column=1, sticky="ew")
                if variable is self.click_target_mode:
                    combo.bind("<<ComboboxSelected>>", self._click_target_changed)
                elif variable is self.on_found:
                    combo.bind("<<ComboboxSelected>>", self._found_action_changed)
                elif variable is self.on_timeout:
                    combo.bind("<<ComboboxSelected>>", self._timeout_action_changed)
                    self.timeout_combo = combo
                elif variable is self.found_jump_target:
                    self.found_jump_entry = combo
                elif variable is self.timeout_jump_target:
                    self.timeout_jump_entry = combo
            else:
                entry = ttk.Entry(body, textvariable=variable)
                entry.grid(row=offset, column=1, sticky="ew")
                if variable is self.timeout:
                    self.timeout_entry = entry
                elif variable is self.timeout_delay:
                    self.timeout_delay_entry = entry
        self._update_click_point_controls()
        self._update_found_jump_control()
        self._update_timeout_jump_control()
        dark_checkbutton(
            body,
            text="一直等待直到出现（不超时）",
            variable=self.wait_forever,
            command=self._update_wait_forever_controls,
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=pad(8, 0))
        ttk.Label(body, text="备用模板").grid(row=16, column=0, sticky="w", pady=px(6))
        fallback_row = ttk.Frame(body)
        fallback_row.grid(row=16, column=1, sticky="ew")
        self.fallback_combo = ttk.Combobox(
            fallback_row, textvariable=self.fallback_template,
            values=fallback_template_options(str(action.get("fallback_template", ""))),
            state="readonly",
        )
        self.fallback_combo.pack(side="left", fill="x", expand=True)
        ttk.Label(body, text="备用切换超时").grid(row=17, column=0, sticky="w", pady=px(6))
        self.fallback_switch_entry = ttk.Entry(body, textvariable=self.fallback_switch_ms)
        self.fallback_switch_entry.grid(row=17, column=1, sticky="ew")
        self.fallback_click_button = dark_checkbutton(
            body,
            text="备用模板出现后点击它（不勾选则只检测不点击）",
            variable=self.fallback_click,
        )
        self.fallback_click_button.grid(row=18, column=0, columnspan=2, sticky="w", pady=pad(6, 0))
        ttk.Label(body, text="备用出现后").grid(row=19, column=0, sticky="w", pady=px(6))
        self.fallback_action_combo = ttk.Combobox(
            body, textvariable=self.fallback_on_match,
            values=("回到主模板的检测", "直接退出识别"), state="readonly", width=18,
        )
        self.fallback_action_combo.grid(row=19, column=1, sticky="w")
        self._update_wait_forever_controls()
        dark_checkbutton(
            body,
            text="显示识别结果浮动提醒",
            variable=self.show_result_notice,
        ).grid(row=20, column=0, columnspan=2, sticky="w", pady=pad(6, 0))
        ttk.Label(body, text="一直等待时：主模板超过切换超时未出现则改用备用模板，在模板的检测区域里识别；备用模板出现时可选是否点击，出现后回到主模板检测或直接退出识别。幕布选取只记录坐标；最小检测间隔为 50 ms。", foreground=COLOR_MUTED, wraplength=px(560)).grid(row=21, column=0, columnspan=2, sticky="w", pady=pad(10, 0))
        self._build_failure_segment_controls(body, row=22)
        buttons = ttk.Frame(body)
        buttons.grid(row=24, column=0, columnspan=2, sticky="ew", pady=pad(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        fit_scrollable_window_to_content(
            self, parent, body, form_scrollbar, align_top=True,
        )

    def open_template_region_manager(self):
        TemplateRegionManagerDialog(self).show()
        self._refresh_template_options()

    def _clear_image_module_binding(self):
        module_key = getattr(self, "module_key", None)
        if module_key is not None:
            module_key.set("")
        module_name = getattr(self, "module_name", None)
        if module_name is not None:
            module_name.set("未选择模块")

    def select_image_module(self):
        binding = choose_module_binding(self, categories=("switch",))
        if not binding:
            return
        module_key = str(binding["module_key"])
        template = str(binding["template"])
        region = list(binding.get("region") or [])
        self.module_key.set(module_key)
        self.template.set(template)
        self.region_mode.set("template")
        self.region.set(",".join(map(str, region)))
        obj = registered_module_object(module_key) or {}
        self.module_name.set(
            str(obj.get("name") or "").strip()
            or Path(module_key.replace("\\", "/")).stem
        )
        self.template_combo.configure(values=registered_template_options(template))

    def _ancestors_to_hide(self):
        """Return windows above the image dialog so the capture is unobstructed."""
        return app_windows(self.master)

    def capture_custom_template(self):
        """Capture an ad-hoc template and region for this image action only."""

        def on_result(region):
            try:
                images_dir = load_module_images_dir()
                images_dir.mkdir(parents=True, exist_ok=True)
                screen, _origin = capture_bgr(tuple(int(part) for part in region))
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:23]
                path = images_dir / f"recognition_{stamp}.png"
                Image.fromarray(screen[:, :, ::-1]).save(path)
                template = display_path(path)
            except Exception as exc:
                show_floating_notice(self, "截图失败", str(exc))
                return
            self.template.set(template)
            self._clear_image_module_binding()
            self.region_mode.set("custom")
            self.region.set(",".join(map(str, region)))
            self.template_combo.configure(values=registered_template_options(template))

        self.picker = ScreenRegionPicker(
            self, self.master, on_result,
            hidden_windows=self._ancestors_to_hide(),
            tip_text=(
                "按住鼠标左键框选要识别的画面；松开后自动保存图片，"
                "并将该框作为此动作的检测区域，Esc 取消"
            ),
        )
        self.picker.start()

    def _refresh_template_options(self):
        current = self.template.get()
        if current and current not in load_template_regions() \
                and not resolve_path(current).is_file():
            # 管理器里已删除/改名的模板不再可用：清空，由保存校验提示重选。
            self.template.set("")
            current = ""
        self.template_combo.configure(values=registered_template_options(current))
        fallback = self.fallback_template.get()
        if fallback and fallback != "（不启用）" and fallback not in load_template_regions():
            self.fallback_template.set("（不启用）")
            fallback = "（不启用）"
        self.fallback_combo.configure(values=fallback_template_options(fallback))

    def _click_target_changed(self, _event=None):
        self._update_click_point_controls()
        if self.click_target_mode.get() == "自定义坐标":
            self.start_click_point_selection()

    def _update_click_point_controls(self):
        state = "normal" if self.click_target_mode.get() == "自定义坐标" else "disabled"
        self.click_point_entry.configure(state=state)
        self.click_point_button.configure(state=state)

    def _timeout_action_changed(self, _event=None):
        self._update_timeout_jump_control()

    def _found_action_changed(self, _event=None):
        self._update_found_jump_control()

    def _update_found_jump_control(self):
        self.found_jump_entry.configure(
            state="normal" if self.on_found.get() == "jump" else "disabled",
        )

    def _update_timeout_jump_control(self):
        if self.wait_forever.get():
            self.timeout_jump_entry.configure(state="disabled")
            return
        self.timeout_jump_entry.configure(
            state="normal" if image_timeout_option_value(self.on_timeout.get()) == "jump" else "disabled",
        )

    def _update_wait_forever_controls(self, _event=None):
        state = "disabled" if self.wait_forever.get() else "normal"
        self.timeout_entry.configure(state=state)
        self.timeout_delay_entry.configure(state=state)
        self.timeout_combo.configure(state=state)
        fallback_state = "normal" if self.wait_forever.get() else "disabled"
        self.fallback_combo.configure(state=fallback_state)
        self.fallback_switch_entry.configure(state=fallback_state)
        self.fallback_click_button.configure(state=fallback_state)
        self.fallback_action_combo.configure(state=fallback_state)
        self._update_timeout_jump_control()

    def start_click_point_selection(self):
        """幕布选取点击位置：整条窗口链都要让开（含主窗口与上级对话框）。"""
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_click_point,
            tip_text="点击要执行操作的位置；只记录坐标，不会点击下方窗口；Esc 取消",
            hidden_windows=self._ancestors_to_hide(),
        )
        self.picker.start()

    def _apply_picked_click_point(self, x, y):
        self.click_point.set(f"{int(x)},{int(y)}")
        self.click_target_mode.set("自定义坐标")
        self._update_click_point_controls()

    def save(self):
        try:
            module_key_var = getattr(self, "module_key", None)
            module_key = module_key_var.get().strip() if module_key_var is not None else ""
            module_binding = None
            if module_key:
                module_obj = registered_module_object(module_key)
                if module_obj is None:
                    raise ValueError("所选图片模块已不存在，请重新选择")
                module_binding = module_reference_binding(module_key, module_obj)
            template = (
                str(module_binding["template"])
                if module_binding is not None else self.template.get().strip()
            )
            if not template:
                raise ValueError("请从列表中选择模板，或使用“框选新建…”")
            threshold = float(self.threshold.get())
            if not 0.1 <= threshold <= 1:
                raise ValueError("相似度必须在 0.1 到 1.0 之间")
            timeout = max(0, int(self.timeout.get()))
            interval = max(50, int(self.interval.get()))
            found_delay = max(0, int(self.found_delay.get()))
            timeout_delay = max(0, int(self.timeout_delay.get()))
            registry = load_template_regions()
            if module_binding is not None:
                region_mode = "template"
                region = list(module_binding["region"])
            elif template in registry:
                # 引用已登记模板：区域运行时从模板登记表实时读取。
                region_mode, region = "template", []
            else:
                # 编辑旧动作且未改动模板：保留原有区域配置。
                region_mode = self.region_mode.get()
                region = [int(part.strip()) for part in self.region.get().split(",")]
                if len(region) != 4:
                    raise ValueError("自定义区域需要四个整数：x,y,w,h")
            click_target = "custom" if self.click_target_mode.get() == "自定义坐标" else "match"
            if click_target == "custom":
                click_point = [int(part.strip()) for part in self.click_point.get().split(",")]
                if len(click_point) != 2:
                    raise ValueError("自定义点击坐标需要两个整数：x,y")
            else:
                click_point = [0, 0]
            delay = max(0, int(self.delay.get()))
            after_delay = max(0, int(self.after_delay.get()))
            fallback_template = self.fallback_template.get().strip()
            if fallback_template == "（不启用）":
                fallback_template = ""
            fallback_switch_ms = max(0, int(self.fallback_switch_ms.get()))
            if fallback_template and fallback_template in registry:
                # 备用模板引用已登记模板：区域运行时读取。
                fallback_region_mode, fallback_region = "template", []
            else:
                # 未启用备用，或编辑旧动作未改动备用模板：保留原有区域配置。
                fallback_region_mode = self.fallback_region_mode.get()
                fallback_region = [int(part.strip()) for part in self.fallback_region.get().split(",")]
                if len(fallback_region) != 4:
                    raise ValueError("备用自定义区域需要四个整数：x,y,w,h")
            timeout_action = image_timeout_option_value(self.on_timeout.get())
            timeout_jump_action_id = self.jump_target_ids.get(self.timeout_jump_target.get(), "")
            if timeout_action == "jump" and not timeout_jump_action_id:
                raise ValueError("请选择超时后要跳转的目标动作")
            found_jump_action_id = self.jump_target_ids.get(self.found_jump_target.get(), "")
            if self.on_found.get() == "jump" and not found_jump_action_id:
                raise ValueError("请选择找到后要跳转的目标动作")
        except ValueError as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = {
            "type": "image_match", "template": template, "threshold": threshold,
            "timeout_ms": timeout, "interval_ms": interval,
            "region_mode": region_mode, "region": region,
            "on_found": self.on_found.get(), "on_timeout": timeout_action,
            "found_jump_action_id": found_jump_action_id,
            "timeout_jump_action_id": timeout_jump_action_id,
            "found_delay_ms": found_delay,
            "timeout_delay_ms": timeout_delay,
            "wait_forever": bool(self.wait_forever.get()),
            "fallback_template": fallback_template,
            "fallback_switch_ms": fallback_switch_ms,
            "fallback_region_mode": fallback_region_mode,
            "fallback_region": fallback_region,
            "fallback_click": bool(self.fallback_click.get()),
            "fallback_on_match": self.fallback_on_match.get(),
            "click_target": click_target, "click_point": click_point,
            "button": self.button.get(), "delay_ms": delay,
            "after_delay_ms": after_delay,
            "show_result_notice": bool(self.show_result_notice.get()),
        }
        if module_binding is not None:
            self.result.update({
                "module_ref": True,
                "module_key": module_key,
                "module_category": str(module_binding.get("module_category") or "switch"),
            })
        self._failure_segment_fields(self.result)
        main = self.master
        try:
            main.after_idle(lambda root=main: activate_main_after_modal(root))
        except tk.TclError:
            pass
        self.destroy()


class OcrActionDialog(FailureSegmentMixin, ModalDialog):
    """识别文字动作表单：识别区域 + 期望文字 + 找到/超时行为。

    OCR 每次识别约几百毫秒，适合一次性判断或慢速轮询；期望文字留空时
    只要识别到任意文字就算命中。跳转目标复用识图动作的同一套机制。
    """

    REGION_MODE_OPTIONS = (("全屏", "screen"), ("自定义区域", "custom"), ("绑定窗口", "window"))
    MATCH_MODE_OPTIONS = (("包含", "contains"), ("等于", "equals"))
    ON_FOUND_OPTIONS = (("继续执行", "continue"), ("跳转到目标动作", "jump"))
    ON_TIMEOUT_OPTIONS = (("继续执行", "continue"), ("跳转到目标动作", "jump"), ("停止脚本", "stop"))

    def __init__(self, parent, action: dict | None = None, actions: list[dict] | None = None):
        super().__init__(parent, "识别文字动作", 650, 950)
        action = action or {}
        self._init_failure_segment(action)
        jump_options = image_jump_target_options(actions or [])
        found_jump_options = image_found_jump_target_options(actions or [])
        self.jump_target_ids = {
            label: action_id for label, action_id in found_jump_options
        }
        saved_target_id = str(action.get("timeout_jump_action_id", "")).strip()
        selected_target = next(
            (label for label, action_id in jump_options if action_id == saved_target_id), "",
        )
        if not selected_target and not saved_target_id and jump_options:
            selected_target = jump_options[0][0]
        saved_found_target_id = str(action.get("found_jump_action_id", "")).strip()
        selected_found_target = next(
            (label for label, action_id in found_jump_options
             if action_id == saved_found_target_id), "",
        )
        if not selected_found_target and not saved_found_target_id:
            selected_found_target = (
                found_jump_options[0][0] if found_jump_options else "继续执行"
            )
        region = action.get("region", [0, 0, 0, 0])
        saved_mode = str(action.get("region_mode", "screen"))
        saved_mode_label = next(
            (label for label, value in self.REGION_MODE_OPTIONS if value == saved_mode),
            "全屏",
        )
        saved_match = str(action.get("match_mode", "contains"))
        saved_match_label = next(
            (label for label, value in self.MATCH_MODE_OPTIONS if value == saved_match),
            "包含",
        )
        saved_on_found = str(action.get("on_found", "continue"))
        saved_on_found_label = next(
            (label for label, value in self.ON_FOUND_OPTIONS if value == saved_on_found),
            "继续执行",
        )
        saved_on_timeout = str(action.get("on_timeout", "continue"))
        saved_on_timeout_label = next(
            (label for label, value in self.ON_TIMEOUT_OPTIONS if value == saved_on_timeout),
            "继续执行",
        )
        self.region_mode = tk.StringVar(value=saved_mode_label)
        self.region = tk.StringVar(value=",".join(map(str, region)))
        self.expected_text = tk.StringVar(value=str(action.get("expected_text", "")))
        self.match_mode = tk.StringVar(value=saved_match_label)
        self.timeout = duration_var(action.get("timeout_ms", 3000))
        self.interval = duration_var(action.get("interval_ms", 500))
        self.on_found = tk.StringVar(value=saved_on_found_label)
        self.found_jump_target = tk.StringVar(value=selected_found_target)
        self.found_delay = duration_var(action.get("found_delay_ms", 0))
        self.on_timeout = tk.StringVar(value=saved_on_timeout_label)
        self.timeout_delay = duration_var(action.get("timeout_delay_ms", 0))
        self.timeout_jump_target = tk.StringVar(value=selected_target)
        self.show_result_notice = tk.BooleanVar(
            value=bool(action.get("show_result_notice", True))
        )
        self.picker = None
        body, _form_canvas, form_scrollbar = scrollable_dialog_body(self)
        body.columnconfigure(1, weight=1)

        def combo_row(row, label, variable, options, bind=None):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            combo = ttk.Combobox(body, textvariable=variable, values=options,
                                 state="readonly")
            combo.grid(row=row, column=1, sticky="ew")
            if bind:
                combo.bind("<<ComboboxSelected>>", bind)
            return combo

        def entry_row(row, label, variable):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            entry = ttk.Entry(body, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew")
            return entry

        combo_row(0, "识别区域模式", self.region_mode,
                  tuple(label for label, _ in self.REGION_MODE_OPTIONS))
        ttk.Label(body, text="识别区域 x,y,w,h").grid(row=1, column=0, sticky="w", pady=px(8))
        region_row = ttk.Frame(body)
        region_row.grid(row=1, column=1, sticky="ew")
        self.region_entry = ttk.Entry(region_row, textvariable=self.region)
        self.region_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            region_row, text="框选区域…", command=self.start_region_selection,
        ).pack(side="left", padx=pad(8, 0))
        entry_row(2, "期望文字（留空 = 识别到任意文字）", self.expected_text)
        combo_row(3, "匹配方式", self.match_mode,
                  tuple(label for label, _ in self.MATCH_MODE_OPTIONS))
        self.timeout_entry = entry_row(4, "等待超时（0 = 只识别一次）", self.timeout)
        entry_row(5, "检测间隔（未命中时多久再试一次）", self.interval)
        combo_row(6, "找到后", self.on_found,
                  tuple(label for label, _ in self.ON_FOUND_OPTIONS),
                  bind=lambda _event: self._update_jump_controls())
        self.found_jump_combo = combo_row(
            7, "找到后跳转目标动作", self.found_jump_target,
            tuple(label for label, _ in found_jump_options),
        )
        entry_row(8, "找到后等待", self.found_delay)
        combo_row(9, "超时后", self.on_timeout,
                  tuple(label for label, _ in self.ON_TIMEOUT_OPTIONS),
                  bind=lambda _event: self._update_jump_controls())
        self.timeout_delay_entry = entry_row(10, "超时后等待", self.timeout_delay)
        self.timeout_jump_combo = combo_row(
            11, "超时跳转目标动作", self.timeout_jump_target,
            tuple(label for label, _ in jump_options),
        )
        dark_checkbutton(
            body, text="显示识别结果浮动提醒", variable=self.show_result_notice,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=pad(8, 0))
        ttk.Label(
            body,
            text="识别区域留空表示全屏；“绑定窗口”在播放时对绑定目标窗口的区域做识别，"
            "没有绑定窗口时回退全屏。每次识别约需几百毫秒，检测间隔不建议小于 200 ms。"
            "期望文字支持“包含 / 等于”，等于时忽略大小写与首尾空白。",
            foreground=COLOR_MUTED, wraplength=px(560),
        ).grid(row=13, column=0, columnspan=2, sticky="w", pady=pad(10, 0))
        self._update_jump_controls()
        self._build_failure_segment_controls(body, row=14)
        buttons = ttk.Frame(body)
        buttons.grid(row=16, column=0, columnspan=2, sticky="ew", pady=pad(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        fit_scrollable_window_to_content(
            self, parent, body, form_scrollbar, align_top=True,
        )

    def _ancestors_to_hide(self):
        return app_windows(self.master)

    def start_region_selection(self):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: (
                self.region.set(",".join(map(str, region))),
                self.region_mode.set("自定义区域"),
            ),
            hidden_windows=self._ancestors_to_hide(),
            tip_text="按住鼠标左键，从左上角向右下角拖动框选要识别文字的区域；"
            "松开完成，Esc 取消（留空表示全屏）",
        )
        self.picker.start()

    def _update_jump_controls(self, _event=None):
        self.found_jump_combo.configure(
            state="normal" if self.on_found.get() == "跳转到目标动作" else "disabled",
        )
        self.timeout_jump_combo.configure(
            state="normal" if self.on_timeout.get() == "跳转到目标动作" else "disabled",
        )

    def save(self):
        def value_of(label, options, fallback):
            return next((v for l, v in options if l == label), fallback)

        try:
            # 留空 = 全屏（与行内提示、README 一致）：空区域是合法配置，
            # 只有填了内容才要求四个非负整数。
            region = [int(part.strip()) for part in self.region.get().split(",")
                      if part.strip()]
            if region and (len(region) != 4 or any(part < 0 for part in region)):
                raise ValueError("识别区域需要四个非负整数：x,y,w,h（留空表示全屏）")
            expected_text = self.expected_text.get().strip()
            timeout = max(0, int(self.timeout.get()))
            interval = max(200, int(self.interval.get()))
            found_delay = max(0, int(self.found_delay.get()))
            timeout_delay = max(0, int(self.timeout_delay.get()))
            found_jump_action_id = self.jump_target_ids.get(
                self.found_jump_target.get(), ""
            )
            if self.on_found.get() == "跳转到目标动作" and not found_jump_action_id:
                raise ValueError("请选择找到后要跳转的目标动作")
            timeout_jump_action_id = self.jump_target_ids.get(
                self.timeout_jump_target.get(), ""
            )
            if self.on_timeout.get() == "跳转到目标动作" and not timeout_jump_action_id:
                raise ValueError("请选择超时后要跳转的目标动作")
        except ValueError as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = {
            "type": "text_ocr",
            "region_mode": value_of(self.region_mode.get(), self.REGION_MODE_OPTIONS, "screen"),
            "region": region,
            "expected_text": expected_text,
            "match_mode": value_of(self.match_mode.get(), self.MATCH_MODE_OPTIONS, "contains"),
            "timeout_ms": timeout,
            "interval_ms": interval,
            "on_found": value_of(self.on_found.get(), self.ON_FOUND_OPTIONS, "continue"),
            "found_jump_action_id": found_jump_action_id,
            "found_delay_ms": found_delay,
            "on_timeout": value_of(self.on_timeout.get(), self.ON_TIMEOUT_OPTIONS, "continue"),
            "timeout_jump_action_id": timeout_jump_action_id,
            "timeout_delay_ms": timeout_delay,
            "show_result_notice": bool(self.show_result_notice.get()),
        }
        self._failure_segment_fields(self.result)
        main = self.master
        try:
            main.after_idle(lambda root=main: activate_main_after_modal(root))
        except tk.TclError:
            pass
        self.destroy()


class OcrCompareActionDialog(FailureSegmentMixin, ModalDialog):
    """Compare two OCR integers around a configurable separator."""

    BRANCH_OPTIONS = (("继续执行", "continue"), ("连续点击", "click"), ("跳转到目标动作", "jump"))
    TIMEOUT_OPTIONS = (("继续执行", "continue"), ("跳转到目标动作", "jump"), ("停止脚本", "stop"))

    def __init__(self, parent, action: dict | None = None, actions: list[dict] | None = None):
        super().__init__(parent, "识别数字比较动作", 700, 820)
        action = action or {}
        self._init_failure_segment(action)
        jump_options = image_jump_target_options(actions or [])
        self.jump_target_ids = dict(jump_options)

        def target_label(target_id: str) -> str:
            return next((label for label, value in jump_options if value == target_id), "")

        self.region = tk.StringVar(
            value=",".join(map(str, action.get("region", [])))
            if len(action.get("region", [])) == 4 else "",
        )
        self.separator = tk.StringVar(value=str(action.get("separator", "/")))
        self.click_region = tk.StringVar(
            value=",".join(map(str, action.get("click_region", [])))
            if len(action.get("click_region", [])) == 4 else "",
        )
        self.button = tk.StringVar(value=str(action.get("button", "left")))
        self.equal_action = tk.StringVar(
            value=_option_label(str(action.get("equal_action", "continue")), self.BRANCH_OPTIONS, "继续执行"),
        )
        self.equal_click_count = tk.StringVar(value=str(action.get("equal_click_count", 1)))
        self.equal_jump_target = tk.StringVar(
            value=target_label(str(action.get("equal_jump_action_id", "")).strip()),
        )
        self.not_equal_action = tk.StringVar(
            value=_option_label(str(action.get("not_equal_action", "continue")), self.BRANCH_OPTIONS, "继续执行"),
        )
        self.not_equal_click_count = tk.StringVar(value=str(action.get("not_equal_click_count", 1)))
        self.not_equal_jump_target = tk.StringVar(
            value=target_label(str(action.get("not_equal_jump_action_id", "")).strip()),
        )
        self.timeout = duration_var(action.get("timeout_ms", 3000))
        self.interval = duration_var(action.get("interval_ms", 500))
        self.on_timeout = tk.StringVar(
            value=_option_label(str(action.get("on_timeout", "continue")), self.TIMEOUT_OPTIONS, "继续执行"),
        )
        self.timeout_jump_target = tk.StringVar(
            value=target_label(str(action.get("timeout_jump_action_id", "")).strip()),
        )
        self.show_result_notice = tk.BooleanVar(value=bool(action.get("show_result_notice", True)))
        self.picker = None

        body, _form_canvas, form_scrollbar = scrollable_dialog_body(self)
        body.columnconfigure(1, weight=1)

        def entry_row(row, label, variable, button_text=None, command=None):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            holder = ttk.Frame(body)
            holder.grid(row=row, column=1, sticky="ew", pady=px(8))
            holder.columnconfigure(0, weight=1)
            ttk.Entry(holder, textvariable=variable).grid(row=0, column=0, sticky="ew")
            if button_text:
                ttk.Button(holder, text=button_text, command=command).grid(row=0, column=1, padx=pad(8, 0))

        def combo_row(row, label, variable, options, bind=None):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            combo = ttk.Combobox(body, textvariable=variable, values=options, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=px(8))
            if bind:
                combo.bind("<<ComboboxSelected>>", bind)
            return combo

        entry_row(0, "识别区域 (x,y,w,h)", self.region, "框选区域…", self.start_region_selection)
        entry_row(1, "分隔符", self.separator)
        entry_row(2, "点击区域 (x,y,w,h)", self.click_region, "框选区域…", self.start_click_region_selection)
        combo_row(3, "点击按钮", self.button, ("left", "right", "middle"))

        self.equal_action_combo = combo_row(
            4, "相等时", self.equal_action,
            tuple(label for label, _value in self.BRANCH_OPTIONS),
            lambda _event: self._update_branch_controls(),
        )
        entry_row(5, "相等点击次数", self.equal_click_count)
        self.equal_jump_combo = combo_row(
            6, "相等跳转目标", self.equal_jump_target,
            tuple(label for label, _value in jump_options),
        )

        self.not_equal_action_combo = combo_row(
            7, "不相等时", self.not_equal_action,
            tuple(label for label, _value in self.BRANCH_OPTIONS),
            lambda _event: self._update_branch_controls(),
        )
        entry_row(8, "不相等点击次数", self.not_equal_click_count)
        self.not_equal_jump_combo = combo_row(
            9, "不相等跳转目标", self.not_equal_jump_target,
            tuple(label for label, _value in jump_options),
        )
        combo_row(
            10, "识别超时后", self.on_timeout,
            tuple(label for label, _value in self.TIMEOUT_OPTIONS),
            lambda _event: self._update_timeout_controls(),
        )
        entry_row(11, "等待超时", self.timeout)
        entry_row(12, "检测间隔", self.interval)
        self.timeout_jump_combo = combo_row(
            13, "超时跳转目标", self.timeout_jump_target,
            tuple(label for label, _value in jump_options),
        )
        dark_checkbutton(
            body, text="显示识别结果浮动提醒", variable=self.show_result_notice,
        ).grid(row=14, column=0, columnspan=2, sticky="w", pady=pad(8, 0))
        ttk.Label(
            body,
            text="识别区域和点击区域分别框选；例如识别到 12/34 时比较两侧数字。"
            "相等与不相等分支可分别连续点击或跳转到行对象。",
            foreground=COLOR_MUTED, wraplength=px(620),
        ).grid(row=15, column=0, columnspan=2, sticky="w", pady=pad(12, 0))
        self._update_branch_controls()
        self._update_timeout_controls()
        self._build_failure_segment_controls(body, row=16)
        buttons = ttk.Frame(body)
        buttons.grid(row=18, column=0, columnspan=2, sticky="ew", pady=pad(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        fit_scrollable_window_to_content(
            self, parent, body, form_scrollbar, align_top=True,
        )

    def _ancestors_to_hide(self):
        return app_windows(self.master)

    def start_region_selection(self):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: (
                self.region.set(",".join(map(str, region))),
            ),
            hidden_windows=self._ancestors_to_hide(),
            tip_text="框选要识别数字比较的区域，松开完成，Esc 取消",
        )
        self.picker.start()

    def start_click_region_selection(self):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: (
                self.click_region.set(",".join(map(str, region))),
            ),
            hidden_windows=self._ancestors_to_hide(),
            tip_text="框选相等/不相等分支要点击的区域，松开完成，Esc 取消",
        )
        self.picker.start()

    def _update_branch_controls(self, _event=None):
        self.equal_jump_combo.configure(
            state="readonly" if self.equal_action.get() == "跳转到目标动作" else "disabled",
        )
        self.not_equal_jump_combo.configure(
            state="readonly" if self.not_equal_action.get() == "跳转到目标动作" else "disabled",
        )

    def _update_timeout_controls(self, _event=None):
        self.timeout_jump_combo.configure(
            state="readonly" if self.on_timeout.get() == "跳转到目标动作" else "disabled",
        )

    @staticmethod
    def _parse_region(value: str, label: str) -> list[int]:
        try:
            region = [int(part.strip()) for part in value.split(",") if part.strip()]
        except ValueError as exc:
            raise ValueError(f"{label}需要四个整数：x,y,w,h") from exc
        if len(region) != 4 or any(part < 0 for part in region) or region[2] <= 0 or region[3] <= 0:
            raise ValueError(f"{label}需要有效的 x,y,w,h 框选区域")
        return region

    def save(self):
        def branch(prefix, behavior_var, count_var, target_var):
            behavior = _option_value(behavior_var.get(), self.BRANCH_OPTIONS, "continue")
            count = max(1, min(9999, int(count_var.get())))
            target_id = self.jump_target_ids.get(target_var.get(), "")
            if behavior == "jump" and not target_id:
                raise ValueError(f"请选择{prefix}时要跳转的行对象")
            return behavior, count, target_id

        try:
            region = self._parse_region(self.region.get(), "识别区域")
            click_region = self._parse_region(self.click_region.get(), "点击区域")
            separator = self.separator.get().strip()
            if not separator:
                raise ValueError("分隔符不能为空")
            timeout = max(0, int(self.timeout.get()))
            interval = max(200, int(self.interval.get()))
            equal_behavior, equal_count, equal_target = branch(
                "相等", self.equal_action, self.equal_click_count, self.equal_jump_target,
            )
            not_equal_behavior, not_equal_count, not_equal_target = branch(
                "不相等", self.not_equal_action, self.not_equal_click_count, self.not_equal_jump_target,
            )
            timeout_behavior = _option_value(self.on_timeout.get(), self.TIMEOUT_OPTIONS, "continue")
            timeout_target = self.jump_target_ids.get(self.timeout_jump_target.get(), "")
            if timeout_behavior == "jump" and not timeout_target:
                raise ValueError("请选择超时后要跳转的行对象")
        except (TypeError, ValueError) as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = {
            "type": "ocr_compare",
            "region_mode": "custom",
            "region": region,
            "separator": separator,
            "click_region": click_region,
            "button": self.button.get(),
            "equal_action": equal_behavior,
            "equal_click_count": equal_count,
            "equal_jump_action_id": equal_target,
            "not_equal_action": not_equal_behavior,
            "not_equal_click_count": not_equal_count,
            "not_equal_jump_action_id": not_equal_target,
            "timeout_ms": timeout,
            "interval_ms": interval,
            "on_timeout": timeout_behavior,
            "timeout_jump_action_id": timeout_target,
            "show_result_notice": bool(self.show_result_notice.get()),
        }
        self._failure_segment_fields(self.result)
        main = self.master
        try:
            main.after_idle(lambda root=main: activate_main_after_modal(root))
        except tk.TclError:
            pass
        self.destroy()


class MultiConditionClickDialog(FailureSegmentMixin, ModalDialog):
    """Fixed three-slot image/OCR condition click action."""

    CONDITION_TYPES = (("图片识别", "image"), ("OCR识别", "ocr"))
    OCR_MODES = (("文字匹配", "text"), ("数字比较", "number"))
    MATCH_MODES = (("包含", "contains"), ("完全相等", "equals"))
    RELATIONS = (("相等", "equal"), ("不相等", "not_equal"))
    TIMEOUT_OPTIONS = (("继续执行", "continue"), ("停止脚本", "stop"))

    def __init__(self, parent, action: dict | None = None):
        super().__init__(parent, "多条件识图点击", 820, 760)
        action = action or {}
        self._init_failure_segment(action)
        saved = action.get("conditions", [])
        saved = saved if isinstance(saved, list) else []
        self.picker = None
        self.condition_enabled = []
        self.condition_type = []
        self.condition_region = []
        self.condition_module_key = []
        self.condition_template = []
        self.condition_threshold = []
        self.condition_ocr_mode = []
        self.condition_expected = []
        self.condition_match_mode = []
        self.condition_separator = []
        self.condition_relation = []
        self.condition_field_widgets = []

        canvas = tk.Canvas(self, background=COLOR_BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body = ttk.Frame(canvas, padding=px(12))
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        self._form_canvas = canvas

        def update_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def stretch_body(event):
            canvas.itemconfigure(body_window, width=event.width)

        body.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", stretch_body)
        canvas.bind("<Map>", update_scrollregion)
        self.bind("<MouseWheel>", self._scroll_form)
        canvas.bind("<MouseWheel>", self._scroll_form, add="+")
        canvas.after_idle(update_scrollregion)
        body.columnconfigure(0, weight=1)
        type_labels = tuple(label for label, _value in self.CONDITION_TYPES)
        ocr_mode_labels = tuple(label for label, _value in self.OCR_MODES)
        match_labels = tuple(label for label, _value in self.MATCH_MODES)
        relation_labels = tuple(label for label, _value in self.RELATIONS)

        ttk.Label(
            body,
            text="固定三个条件槽位：勾选后才参与判断；启用的条件必须全部满足，才会执行下方连续点击。",
            foreground=COLOR_MUTED, wraplength=px(760),
        ).pack(anchor="w", pady=pad(0, 10))
        for index in range(3):
            condition = saved[index] if index < len(saved) and isinstance(saved[index], dict) else {}
            frame = ttk.LabelFrame(body, text=f"条件 {index + 1}", padding=px(10))
            frame.pack(fill="x", pady=px(5))
            frame.columnconfigure(1, weight=1)
            enabled = tk.BooleanVar(value=bool(condition.get("enabled", index == 0)))
            kind = str(condition.get("type", "image"))
            module_key = str(condition.get("module_key", "")).strip()
            module_obj = registered_module_object(module_key) if module_key else None
            if module_obj is not None:
                condition = dict(condition)
                condition["template"] = str(module_obj.get("template", ""))
                condition["region"] = list(module_obj.get("region") or [])
            self.condition_enabled.append(enabled)
            self.condition_type.append(tk.StringVar(value=_option_label(kind, self.CONDITION_TYPES, "图片识别")))
            region = condition.get("region", [])
            self.condition_region.append(tk.StringVar(
                value=",".join(map(str, region)) if len(region) == 4 else "",
            ))
            self.condition_module_key.append(tk.StringVar(value=module_key))
            self.condition_template.append(tk.StringVar(value=str(condition.get("template", ""))))
            self.condition_threshold.append(tk.StringVar(value=str(condition.get("threshold", 0.85))))
            self.condition_ocr_mode.append(tk.StringVar(
                value=_option_label(str(condition.get("ocr_mode", "text")), self.OCR_MODES, "文字匹配"),
            ))
            self.condition_expected.append(tk.StringVar(value=str(condition.get("expected_text", ""))))
            self.condition_match_mode.append(tk.StringVar(
                value=_option_label(str(condition.get("match_mode", "contains")), self.MATCH_MODES, "包含"),
            ))
            self.condition_separator.append(tk.StringVar(value=str(condition.get("separator", "/"))))
            self.condition_relation.append(tk.StringVar(
                value=_option_label(str(condition.get("relation", "equal")), self.RELATIONS, "相等"),
            ))
            dark_checkbutton(frame, text="启用", variable=enabled).grid(row=0, column=0, sticky="w", padx=pad(0, 10))
            ttk.Label(frame, text="类型").grid(row=0, column=1, sticky="w")
            type_combo = ttk.Combobox(
                frame, textvariable=self.condition_type[-1], values=type_labels,
                state="readonly", width=12,
            )
            type_combo.grid(row=0, column=2, sticky="w", padx=pad(8, 0))
            type_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, slot=index: self._refresh_condition_fields(slot),
            )
            ttk.Label(frame, text="识别区域 (x,y,w,h)").grid(row=1, column=0, sticky="w", pady=pad(8, 0))
            region_row = ttk.Frame(frame)
            region_row.grid(row=1, column=1, columnspan=2, sticky="ew", pady=pad(8, 0))
            region_row.columnconfigure(0, weight=1)
            ttk.Entry(region_row, textvariable=self.condition_region[-1]).grid(row=0, column=0, sticky="ew")
            ttk.Button(
                region_row, text="框选区域…",
                command=lambda slot=index: self.start_condition_region_selection(slot),
            ).grid(row=0, column=1, padx=pad(8, 0))
            ttk.Label(frame, text="图片模板").grid(row=2, column=0, sticky="w", pady=pad(8, 0))
            template_combo = ttk.Combobox(
                frame, textvariable=self.condition_template[-1],
                values=registered_template_options(str(condition.get("template", ""))),
                state="readonly", width=48,
            )
            template_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=pad(8, 0))
            template_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, slot=index: self.condition_module_key[slot].set(""),
            )
            module_button = ttk.Button(
                frame, text="选择模块…",
                command=lambda slot=index: self.select_condition_module(slot),
            )
            module_button.grid(row=2, column=3, sticky="e", padx=pad(8, 0), pady=pad(8, 0))
            ttk.Label(frame, text="相似度").grid(row=3, column=0, sticky="w", pady=pad(8, 0))
            threshold_entry = ttk.Entry(frame, textvariable=self.condition_threshold[-1], width=12)
            threshold_entry.grid(row=3, column=1, sticky="w", pady=pad(8, 0))
            ttk.Label(frame, text="OCR模式").grid(row=4, column=0, sticky="w", pady=pad(8, 0))
            ocr_mode_combo = ttk.Combobox(
                frame, textvariable=self.condition_ocr_mode[-1], values=ocr_mode_labels,
                state="readonly", width=12,
            )
            ocr_mode_combo.grid(row=4, column=1, sticky="w", pady=pad(8, 0))
            ocr_mode_combo.bind(
                "<<ComboboxSelected>>",
                lambda _event, slot=index: self._refresh_condition_fields(slot),
            )
            ttk.Label(frame, text="OCR文字").grid(row=5, column=0, sticky="w", pady=pad(8, 0))
            expected_entry = ttk.Entry(frame, textvariable=self.condition_expected[-1])
            expected_entry.grid(row=5, column=1, columnspan=2, sticky="ew", pady=pad(8, 0))
            ttk.Label(frame, text="OCR匹配").grid(row=6, column=0, sticky="w", pady=pad(8, 0))
            match_combo = ttk.Combobox(
                frame, textvariable=self.condition_match_mode[-1], values=match_labels,
                state="readonly", width=12,
            )
            match_combo.grid(row=6, column=1, sticky="w", pady=pad(8, 0))
            ttk.Label(frame, text="数字分隔符").grid(row=7, column=0, sticky="w", pady=pad(8, 0))
            separator_entry = ttk.Entry(frame, textvariable=self.condition_separator[-1], width=12)
            separator_entry.grid(row=7, column=1, sticky="w", pady=pad(8, 0))
            ttk.Label(frame, text="数字关系").grid(row=7, column=2, sticky="w", padx=pad(20, 0), pady=pad(8, 0))
            relation_combo = ttk.Combobox(
                frame, textvariable=self.condition_relation[-1], values=relation_labels,
                state="readonly", width=12,
            )
            relation_combo.grid(row=7, column=3, sticky="e", pady=pad(8, 0))
            self.condition_field_widgets.append({
                "image": ((template_combo, "readonly"), (module_button, "normal"),
                          (threshold_entry, "normal")),
                "ocr_mode": ((ocr_mode_combo, "readonly"),),
                "ocr_text": ((expected_entry, "normal"),),
                "ocr_match": ((match_combo, "readonly"),),
                "separator": ((separator_entry, "normal"),),
                "relation": ((relation_combo, "readonly"),),
            })
            self._refresh_condition_fields(index)

        click_frame = ttk.LabelFrame(body, text="满足条件后的操作", padding=px(10))
        click_frame.pack(fill="x", pady=pad(10, 5))
        click_frame.columnconfigure(1, weight=1)
        self.click_region = tk.StringVar(
            value=",".join(map(str, action.get("click_region", [])))
            if len(action.get("click_region", [])) == 4 else "",
        )
        self.button = tk.StringVar(value=str(action.get("button", "left")))
        self.click_count = tk.StringVar(value=str(action.get("click_count", 1)))
        self.timeout = duration_var(action.get("timeout_ms", 3000))
        self.interval = duration_var(action.get("interval_ms", 500))
        self.on_timeout = tk.StringVar(
            value=_option_label(str(action.get("on_timeout", "continue")), self.TIMEOUT_OPTIONS, "继续执行"),
        )
        self.show_result_notice = tk.BooleanVar(value=bool(action.get("show_result_notice", True)))
        self._entry_row(click_frame, 0, "点击区域 (x,y,w,h)", self.click_region, self.start_click_region_selection)
        self._entry_row(click_frame, 1, "连续点击次数", self.click_count)
        self._combo_row(click_frame, 2, "点击按钮", self.button, ("left", "right", "middle"))
        self._entry_row(click_frame, 3, "等待超时", self.timeout)
        self._entry_row(click_frame, 4, "检测间隔", self.interval)
        self._combo_row(
            click_frame, 5, "超时后", self.on_timeout,
            tuple(label for label, _value in self.TIMEOUT_OPTIONS),
        )
        dark_checkbutton(
            click_frame, text="显示识别结果浮动提醒", variable=self.show_result_notice,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=pad(8, 0))
        self._build_failure_segment_controls(body, pack=True)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(12, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))

    @staticmethod
    def _parse_region(value: str, label: str) -> list[int]:
        try:
            region = [int(part.strip()) for part in value.split(",") if part.strip()]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}需要四个整数：x,y,w,h") from exc
        if len(region) != 4 or any(part < 0 for part in region) or region[2] <= 0 or region[3] <= 0:
            raise ValueError(f"{label}需要有效的 x,y,w,h 框选区域")
        return region

    def _entry_row(self, parent, row, label, variable, picker=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=px(6))
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=1, sticky="ew", pady=px(6))
        holder.columnconfigure(0, weight=1)
        ttk.Entry(holder, textvariable=variable).grid(row=0, column=0, sticky="ew")
        if picker:
            ttk.Button(holder, text="框选区域…", command=picker).grid(row=0, column=1, padx=pad(8, 0))

    @staticmethod
    def _combo_row(parent, row, label, variable, values):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=px(6))
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(
            row=row, column=1, sticky="w", pady=px(6),
        )

    def _scroll_form(self, event):
        if not event.delta:
            return "break"
        self._form_canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def _refresh_condition_fields(self, slot: int):
        kind = _option_value(self.condition_type[slot].get(), self.CONDITION_TYPES, "image")
        ocr_mode = _option_value(self.condition_ocr_mode[slot].get(), self.OCR_MODES, "text")
        states = multi_condition_field_states(kind, ocr_mode)
        for field, widgets in self.condition_field_widgets[slot].items():
            for widget, enabled_state in widgets:
                widget.configure(state=enabled_state if states[field] else "disabled")

    def _ancestors_to_hide(self):
        return app_windows(self.master)

    def start_condition_region_selection(self, slot):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: self.condition_region[slot].set(",".join(map(str, region))),
            hidden_windows=self._ancestors_to_hide(),
            tip_text=f"框选条件 {slot + 1} 的识别区域，松开完成，Esc 取消",
        )
        self.picker.start()

    def select_condition_module(self, slot: int):
        binding = choose_module_binding(self, categories=("switch",))
        if not binding:
            return
        self.condition_module_key[slot].set(str(binding["module_key"]))
        self.condition_template[slot].set(str(binding["template"]))
        self.condition_region[slot].set(",".join(map(str, binding.get("region") or [])))
        self.condition_type[slot].set("图片识别")
        self._refresh_condition_fields(slot)

    def start_click_region_selection(self):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: self.click_region.set(",".join(map(str, region))),
            hidden_windows=self._ancestors_to_hide(),
            tip_text="框选满足条件后要连续点击的区域，松开完成，Esc 取消",
        )
        self.picker.start()

    def save(self):
        try:
            conditions = []
            for index in range(3):
                enabled = bool(self.condition_enabled[index].get())
                kind = _option_value(self.condition_type[index].get(), self.CONDITION_TYPES, "image")
                module_key = (
                    self.condition_module_key[index].get().strip()
                    if index < len(getattr(self, "condition_module_key", [])) else ""
                )
                module_binding = None
                if kind == "image" and module_key:
                    module_obj = registered_module_object(module_key)
                    if module_obj is None:
                        raise ValueError(f"条件 {index + 1} 所选图片模块已不存在")
                    module_binding = module_reference_binding(module_key, module_obj)
                    region = list(module_binding["region"])
                else:
                    raw_region = self.condition_region[index].get().strip()
                    region = self._parse_region(
                        raw_region, f"条件 {index + 1} 识别区域",
                    ) if raw_region else [0, 0, 0, 0]
                condition = {"enabled": enabled, "type": kind, "region": region}
                if enabled and region[2] <= 0:
                    raise ValueError(f"请设置条件 {index + 1} 的识别区域")
                if kind == "image":
                    template = (
                        str(module_binding["template"])
                        if module_binding is not None
                        else self.condition_template[index].get().strip()
                    )
                    if enabled and not template:
                        raise ValueError(f"请设置条件 {index + 1} 的图片模板")
                    try:
                        threshold = float(self.condition_threshold[index].get() or 0.85)
                    except (TypeError, ValueError):
                        if enabled:
                            raise ValueError(f"条件 {index + 1} 的图片相似度必须是数字")
                        threshold = 0.85
                    if enabled and not 0.1 <= threshold <= 1:
                        raise ValueError(
                            f"条件 {index + 1} 的图片相似度必须在 0.1 到 1.0 之间"
                        )
                    condition.update(template=template, threshold=threshold)
                    if module_binding is not None:
                        condition.update({
                            "module_ref": True,
                            "module_key": module_key,
                            "region_mode": "template",
                        })
                elif kind == "ocr":
                    ocr_mode = _option_value(
                        self.condition_ocr_mode[index].get(), self.OCR_MODES, "text",
                    )
                    condition["ocr_mode"] = ocr_mode
                    if ocr_mode == "text":
                        condition.update(
                            expected_text=self.condition_expected[index].get(),
                            match_mode=_option_value(
                                self.condition_match_mode[index].get(), self.MATCH_MODES, "contains",
                            ),
                        )
                    else:
                        separator = self.condition_separator[index].get().strip()
                        if enabled and not separator:
                            raise ValueError(f"请设置条件 {index + 1} 的数字分隔符")
                        condition.update(
                            separator=separator or "/",
                            relation=_option_value(
                                self.condition_relation[index].get(), self.RELATIONS, "equal",
                            ),
                        )
                conditions.append(condition)
            if not any(condition["enabled"] for condition in conditions):
                raise ValueError("至少需要启用一个条件")
            click_region = self._parse_region(self.click_region.get(), "点击区域")
            click_count = max(1, min(9999, int(self.click_count.get())))
            timeout = max(0, int(self.timeout.get()))
            interval = max(200, int(self.interval.get()))
            on_timeout = _option_value(self.on_timeout.get(), self.TIMEOUT_OPTIONS, "continue")
        except (TypeError, ValueError) as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return
        self.result = {
            "type": "multi_condition_click",
            "conditions": conditions,
            "click_region": click_region,
            "button": self.button.get(),
            "click_count": click_count,
            "timeout_ms": timeout,
            "interval_ms": interval,
            "on_timeout": on_timeout,
            "show_result_notice": bool(self.show_result_notice.get()),
        }
        self._failure_segment_fields(self.result)
        try:
            self.master.after_idle(lambda root=self.master: activate_main_after_modal(root))
        except tk.TclError:
            pass
        self.destroy()


class RowListConditionClickDialog(FailureSegmentMixin, ModalDialog):
    """Configure a click on the first list row satisfying two conditions."""

    CONDITION_TYPES = (("图片识别", "image"), ("文字识别", "text"), ("数字比较", "number"))
    MATCH_MODES = (("包含", "contains"), ("完全相等", "equals"))
    RELATIONS = (("相等", "equal"), ("不相等", "not_equal"))
    NO_MATCH_ACTIONS = (("结束", "finish"), ("重试", "retry"))

    def __init__(self, parent, action: dict | None = None,
                 actions: list[dict] | None = None, on_test=None):
        super().__init__(parent, "列表逐行条件点击", 700, 640)
        action = action or {}
        self.on_test = on_test
        self.test_cancel_event = threading.Event()
        self._test_generation = 0
        self._destroyed = False
        self.picker = None
        self.condition_field_widgets = {}
        self.jump_target_ids = dict(image_jump_target_options(actions or []))
        self._init_failure_segment(action)
        list_region = action.get("list_region", [])
        self.list_region = tk.StringVar(
            value=",".join(map(str, list_region)) if len(list_region) == 4 else "",
        )
        self.left_region = tk.StringVar(value=self._absolute_region_text(action, "left_region"))
        self.right_region = tk.StringVar(value=self._absolute_region_text(action, "right_region"))
        self.click_region = tk.StringVar(value=self._absolute_region_text(action, "click_region"))
        self.row_height = tk.StringVar(value=str(action.get("row_height", "") or ""))
        self.source_image = tk.StringVar(value=str(action.get("screenshot_path", "")))
        self.button = tk.StringVar(value=str(action.get("button", "left")))
        self.click_count = tk.StringVar(value=str(action.get("click_count", 1) or 1))
        self.no_match_action = tk.StringVar(value=_option_label(
            str(action.get("no_match_action", "finish")), self.NO_MATCH_ACTIONS, "结束",
        ))
        self.retry_interval = duration_var(action.get("retry_interval_ms", 500))
        self.on_success = tk.StringVar(value=module_result_option_label(
            str(action.get("on_found", "continue")),
        ))
        self.success_target = tk.StringVar(value=select_jump_target_label(
            str(action.get("found_jump_action_id", "")).strip(),
            max(1, int(action.get("found_jump_row", 1))),
            list(self.jump_target_ids.items()),
        ))
        self.on_failure = tk.StringVar(value=module_result_option_label(
            str(action.get("on_timeout", "continue")),
        ))
        self.failure_target = tk.StringVar(value=select_jump_target_label(
            str(action.get("timeout_jump_action_id", "")).strip(),
            max(1, int(action.get("timeout_jump_row", 1))),
            list(self.jump_target_ids.items()),
        ))

        for side in ("left", "right"):
            condition = action.get(f"{side}_condition", {})
            condition = condition if isinstance(condition, dict) else {}
            kind = str(condition.get("type", "text"))
            setattr(self, f"{side}_condition_type", tk.StringVar(
                value=_option_label(kind, self.CONDITION_TYPES, "文字识别"),
            ))
            module_key = str(condition.get("module_key", "")).strip()
            setattr(self, f"{side}_module_key", tk.StringVar(value=module_key))
            setattr(self, f"{side}_module_name", tk.StringVar(
                value=module_display_name(module_key),
            ))
            setattr(self, f"{side}_expected_text", tk.StringVar(value=str(condition.get("expected_text", ""))))
            setattr(self, f"{side}_match_mode", tk.StringVar(value=_option_label(
                str(condition.get("match_mode", "contains")), self.MATCH_MODES, "包含",
            )))
            setattr(self, f"{side}_separator", tk.StringVar(value=str(condition.get("separator", "/"))))
            setattr(self, f"{side}_relation", tk.StringVar(value=_option_label(
                str(condition.get("relation", "equal")), self.RELATIONS, "相等",
            )))

        canvas = tk.Canvas(self, background=COLOR_BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body = ttk.Frame(canvas, padding=px(12))
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        self._form_canvas = canvas

        def update_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def stretch_body(event):
            canvas.itemconfigure(body_window, width=event.width)

        body.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", stretch_body)
        canvas.bind("<Map>", update_scrollregion)
        self.bind("<MouseWheel>", self._scroll_form)
        canvas.bind("<MouseWheel>", self._scroll_form, add="+")
        canvas.after_idle(update_scrollregion)
        body.columnconfigure(0, weight=1)
        self._build_region_panel(body)
        self._build_condition_panel(body, "left", "左侧条件")
        self._build_condition_panel(body, "right", "右侧条件")
        self._build_action_panel(body)
        self._build_failure_segment_controls(body, pack=True)
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=pad(8, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=px(8))
        ttk.Button(
            buttons, text="测试当前屏幕（不点击）",
            command=lambda: self.test_recognition("screen"),
        ).pack(side="left")
        ttk.Button(
            buttons, text="测试选择的图片（不点击）",
            command=lambda: self.test_recognition("image"),
        ).pack(side="left", padx=pad(8, 0))

    def _absolute_region_text(self, action: dict, key: str) -> str:
        list_region = action.get("list_region", [])
        region = action.get(key, [])
        if len(list_region) != 4 or len(region) != 4:
            return ""
        try:
            x, y, _width, _height = (int(value) for value in list_region)
            relative_x, relative_y, width, height = (int(value) for value in region)
        except (TypeError, ValueError):
            return ""
        return f"{x + relative_x},{y + relative_y},{width},{height}"

    def _build_region_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="列表与首行区域", padding=px(8))
        frame.pack(fill="x", pady=pad(0, 4))
        frame.columnconfigure(1, weight=1)
        self._entry_row(frame, 0, "列表区域 (x,y,w,h)", self.list_region, "list", "框选列表区域")
        self._entry_row(frame, 1, "左侧识别区域 (x,y,w,h)", self.left_region, "left", "框选首行左侧识别区域")
        self._entry_row(frame, 2, "右侧识别区域 (x,y,w,h)", self.right_region, "right", "框选首行右侧识别区域")
        self._entry_row(
            frame, 3, "点击区域 (x,y,w,h)", self.click_region, "click",
            "框选首行点击区域",
        )
        ttk.Label(frame, text="行高（像素）").grid(row=4, column=0, sticky="w", pady=px(3))
        row_height_holder = ttk.Frame(frame)
        row_height_holder.grid(row=4, column=1, sticky="ew", pady=px(3))
        row_height_holder.columnconfigure(0, weight=1)
        ttk.Entry(row_height_holder, textvariable=self.row_height).grid(
            row=0, column=0, sticky="ew",
        )
        ttk.Button(
            row_height_holder, text="框选第二行基准…",
            command=self.start_second_row_selection,
        ).grid(row=0, column=1, padx=pad(8, 0))
        ttk.Label(frame, text="测试图片").grid(row=5, column=0, sticky="w", pady=px(3))
        image_holder = ttk.Frame(frame)
        image_holder.grid(row=5, column=1, sticky="ew", pady=px(3))
        image_holder.columnconfigure(0, weight=1)
        ttk.Entry(
            image_holder, textvariable=self.source_image, state="readonly",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            image_holder, text="选择图片…", command=self.choose_test_image,
        ).grid(row=0, column=1, padx=pad(8, 0))

    def choose_test_image(self):
        """Pick a full-screen image for the image-based recognition test."""
        path = filedialog.askopenfilename(
            parent=self, title="选择列表识别测试图片",
            filetypes=(("图片文件", "*.png;*.jpg;*.jpeg;*.bmp"), ("所有文件", "*.*")),
        )
        if path:
            self.source_image.set(path)

    def _build_condition_panel(self, parent, side: str, title: str):
        frame = ttk.LabelFrame(parent, text=title, padding=px(8))
        frame.pack(fill="x", pady=px(4))
        frame.columnconfigure(1, weight=1)
        type_var = getattr(self, f"{side}_condition_type")
        module_var = getattr(self, f"{side}_module_key")
        expected_var = getattr(self, f"{side}_expected_text")
        match_var = getattr(self, f"{side}_match_mode")
        separator_var = getattr(self, f"{side}_separator")
        relation_var = getattr(self, f"{side}_relation")
        ttk.Label(frame, text="类型").grid(row=0, column=0, sticky="w", pady=px(3))
        type_combo = ttk.Combobox(
            frame, textvariable=type_var,
            values=tuple(label for label, _value in self.CONDITION_TYPES), state="readonly", width=12,
        )
        type_combo.grid(row=0, column=1, sticky="w", pady=px(3))
        type_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_condition_fields(side))
        module_label = ttk.Label(frame, text="图片模块")
        module_label.grid(row=1, column=0, sticky="w", pady=px(3))
        module_row = ttk.Frame(frame)
        module_row.grid(row=1, column=1, sticky="ew", pady=px(3))
        module_row.columnconfigure(0, weight=1)
        module_name_var = getattr(self, f"{side}_module_name")
        module_entry = ttk.Entry(module_row, textvariable=module_name_var, state="readonly")
        module_entry.grid(row=0, column=0, sticky="ew")
        module_button = ttk.Button(module_row, text="选择模块…", command=lambda: self.select_condition_module(side))
        module_button.grid(row=0, column=1, padx=pad(8, 0))
        expected_label = ttk.Label(frame, text="期望文字")
        expected_label.grid(row=2, column=0, sticky="w", pady=px(3))
        expected_entry = ttk.Entry(frame, textvariable=expected_var)
        expected_entry.grid(row=2, column=1, sticky="ew", pady=px(3))
        match_label = ttk.Label(frame, text="文字匹配")
        match_label.grid(row=3, column=0, sticky="w", pady=px(3))
        match_combo = ttk.Combobox(
            frame, textvariable=match_var,
            values=tuple(label for label, _value in self.MATCH_MODES), state="readonly", width=12,
        )
        match_combo.grid(row=3, column=1, sticky="w", pady=px(3))
        separator_label = ttk.Label(frame, text="数字分隔符")
        separator_label.grid(row=4, column=0, sticky="w", pady=px(3))
        separator_entry = ttk.Entry(frame, textvariable=separator_var, width=12)
        separator_entry.grid(row=4, column=1, sticky="w", pady=px(3))
        relation_label = ttk.Label(frame, text="数字关系")
        relation_label.grid(row=5, column=0, sticky="w", pady=px(3))
        relation_combo = ttk.Combobox(
            frame, textvariable=relation_var,
            values=tuple(label for label, _value in self.RELATIONS), state="readonly", width=12,
        )
        relation_combo.grid(row=5, column=1, sticky="w", pady=px(3))
        self.condition_field_widgets[side] = {
            "module": ((module_label, None), (module_row, None)),
            "text": ((expected_label, None), (expected_entry, None)),
            "match_mode": ((match_label, None), (match_combo, None)),
            "separator": ((separator_label, None), (separator_entry, None)),
            "relation": ((relation_label, None), (relation_combo, None)),
        }
        self._refresh_condition_fields(side)

    def _build_action_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="点击与结果分支", padding=px(8))
        frame.pack(fill="x", pady=px(4))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        ttk.Label(frame, text="点击按钮").grid(row=0, column=0, sticky="w", pady=px(3))
        ttk.Combobox(
            frame, textvariable=self.button,
            values=("left", "right", "middle"), state="readonly", width=10,
        ).grid(row=0, column=1, sticky="w", pady=px(3))
        ttk.Label(frame, text="连续点击次数").grid(row=0, column=2, sticky="w", padx=pad(18, 0), pady=px(3))
        ttk.Spinbox(
            frame, from_=1, to=9999, increment=1,
            textvariable=self.click_count, width=8,
        ).grid(row=0, column=3, sticky="w", pady=px(3))

        result_labels = tuple(label for label, _value in MODULE_RESULT_OPTIONS)
        target_labels = tuple(self.jump_target_ids)
        ttk.Label(frame, text="成功后").grid(row=1, column=0, sticky="w", pady=px(3))
        ttk.Combobox(
            frame, textvariable=self.on_success,
            values=result_labels, state="readonly", width=18,
        ).grid(row=1, column=1, sticky="ew", pady=px(3))
        ttk.Label(frame, text="成功跳转到").grid(row=1, column=2, sticky="w", padx=pad(18, 0), pady=px(3))
        self.success_target_combo = ttk.Combobox(
            frame, textvariable=self.success_target, values=target_labels,
            state="disabled", width=28,
        )
        self.success_target_combo.grid(row=1, column=3, sticky="ew", pady=px(3))

        ttk.Label(frame, text="失败后").grid(row=2, column=0, sticky="w", pady=px(3))
        ttk.Combobox(
            frame, textvariable=self.on_failure,
            values=result_labels, state="readonly", width=18,
        ).grid(row=2, column=1, sticky="ew", pady=px(3))
        ttk.Label(frame, text="失败跳转到").grid(row=2, column=2, sticky="w", padx=pad(18, 0), pady=px(3))
        self.failure_target_combo = ttk.Combobox(
            frame, textvariable=self.failure_target, values=target_labels,
            state="disabled", width=28,
        )
        self.failure_target_combo.grid(row=2, column=3, sticky="ew", pady=px(3))

        ttk.Label(frame, text="无匹配时").grid(row=3, column=0, sticky="w", pady=px(3))
        ttk.Combobox(
            frame, textvariable=self.no_match_action,
            values=tuple(label for label, _value in self.NO_MATCH_ACTIONS), state="readonly", width=12,
        ).grid(row=3, column=1, sticky="w", pady=px(3))
        ttk.Label(frame, text="重试间隔").grid(row=3, column=2, sticky="w", padx=pad(18, 0), pady=px(3))
        ttk.Entry(frame, textvariable=self.retry_interval, width=12).grid(
            row=3, column=3, sticky="w", pady=px(3),
        )
        self.on_success.trace_add("write", self._update_result_target_states)
        self.on_failure.trace_add("write", self._update_result_target_states)
        self._update_result_target_states()

    def _update_result_target_states(self, *_args):
        self.success_target_combo.configure(
            state="readonly"
            if module_result_option_value(self.on_success.get()) == "jump" else "disabled",
        )
        self.failure_target_combo.configure(
            state="readonly"
            if module_result_option_value(self.on_failure.get()) == "jump" else "disabled",
        )

    def _entry_row(self, parent, row: int, label: str, variable, picker_key: str | None = None,
                   picker_tip: str = ""):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=px(3))
        holder = ttk.Frame(parent)
        holder.grid(row=row, column=1, sticky="ew", pady=px(3))
        holder.columnconfigure(0, weight=1)
        ttk.Entry(holder, textvariable=variable).grid(row=0, column=0, sticky="ew")
        if picker_key:
            ttk.Button(
                holder, text="框选区域…",
                command=lambda: self.start_region_selection(picker_key, picker_tip),
            ).grid(row=0, column=1, padx=pad(8, 0))

    def _scroll_form(self, event):
        if not event.delta:
            return "break"
        self._form_canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def _refresh_condition_fields(self, side: str):
        kind = _option_value(
            getattr(self, f"{side}_condition_type").get(), self.CONDITION_TYPES, "text",
        )
        states = row_list_condition_field_states(kind)
        for field, widgets in self.condition_field_widgets[side].items():
            for widget, _unused_state in widgets:
                if states[field]:
                    widget.grid()
                else:
                    widget.grid_remove()

    def _ancestors_to_hide(self):
        return app_windows(self.master)

    def start_region_selection(self, key: str, tip_text: str):
        variable = getattr(self, f"{key}_region")
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: variable.set(",".join(map(str, region))),
            hidden_windows=self._ancestors_to_hide(), tip_text=tip_text,
        )
        self.picker.start()

    def start_second_row_selection(self):
        self.picker = ScreenRegionPicker(
            self, self.master, self._apply_second_row_baseline,
            hidden_windows=self._ancestors_to_hide(),
            tip_text="框选第二行中与首行点击区域处于相同位置的区域",
        )
        self.picker.start()

    def _apply_second_row_baseline(self, region):
        try:
            first = self._parse_region(self.click_region.get(), "首行点击区域")
            second = [int(value) for value in region]
            first_center_y = first[1] + first[3] / 2
            second_center_y = second[1] + second[3] / 2
            row_height = round(second_center_y - first_center_y)
            if row_height <= 0:
                raise ValueError("第二行基准必须位于首行点击区域下方")
        except (TypeError, ValueError) as exc:
            show_floating_notice(self, "无法计算行高", str(exc))
            return
        self.row_height.set(str(row_height))

    def select_condition_module(self, side: str):
        binding = choose_module_binding(self, categories=("switch",))
        if not binding:
            return
        module_key = str(binding["module_key"])
        getattr(self, f"{side}_module_key").set(module_key)
        getattr(self, f"{side}_module_name").set(
            module_display_name(module_key, registered_module_object(module_key)),
        )
        getattr(self, f"{side}_condition_type").set("图片识别")
        self._refresh_condition_fields(side)

    @staticmethod
    def _parse_region(value: str, label: str) -> list[int]:
        try:
            region = [int(part.strip()) for part in value.split(",")]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}需要四个整数：x,y,w,h") from exc
        if len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            raise ValueError(f"{label}需要有效的 x,y,w,h 框选区域")
        return region

    def _relative_first_row_region(self, value: str, label: str, list_region: list[int]) -> list[int]:
        region = self._parse_region(value, label)
        list_x, list_y, list_width, list_height = list_region
        x, y, width, height = region
        if (
            x < list_x or y < list_y
            or x + width > list_x + list_width
            or y + height > list_y + list_height
        ):
            raise ValueError(f"{label}必须位于列表区域内")
        return [x - list_x, y - list_y, width, height]

    def _condition_value(self, side: str) -> dict:
        kind = _option_value(
            getattr(self, f"{side}_condition_type").get(), self.CONDITION_TYPES, "text",
        )
        if kind == "image":
            module_key = getattr(self, f"{side}_module_key").get().strip()
            if not module_key:
                raise ValueError(f"请选择{side}侧图片模块")
            return {"type": "image", "module_ref": True, "module_key": module_key}
        if kind == "text":
            return {
                "type": "text",
                "expected_text": getattr(self, f"{side}_expected_text").get(),
                "match_mode": _option_value(
                    getattr(self, f"{side}_match_mode").get(), self.MATCH_MODES, "contains",
                ),
            }
        separator = getattr(self, f"{side}_separator").get().strip()
        if not separator:
            raise ValueError(f"{side}侧数字分隔符不能为空")
        return {
            "type": "number",
            "separator": separator,
            "relation": _option_value(
                getattr(self, f"{side}_relation").get(), self.RELATIONS, "equal",
            ),
        }

    def _build_action(self):
        try:
            list_region = self._parse_region(self.list_region.get(), "列表区域")
            left_region = self._relative_first_row_region(
                self.left_region.get(), "左侧识别区域", list_region,
            )
            right_region = self._relative_first_row_region(
                self.right_region.get(), "右侧识别区域", list_region,
            )
            click_region = self._relative_first_row_region(
                self.click_region.get(), "点击区域", list_region,
            )
            row_height = int(self.row_height.get())
            if not 0 < row_height <= list_region[3]:
                raise ValueError("行高必须大于零且不能超过列表高度")
            click_count = int(self.click_count.get())
            if not 1 <= click_count <= 9999:
                raise ValueError("连续点击次数必须是 1 到 9999 之间的整数")
            retry_interval = int(self.retry_interval.get())
            if retry_interval < 0:
                raise ValueError("重试间隔不能小于零")
            left_condition = self._condition_value("left")
            right_condition = self._condition_value("right")
            on_found = module_result_option_value(self.on_success.get())
            on_timeout = module_result_option_value(self.on_failure.get())
            found_jump_action_id = self.jump_target_ids.get(self.success_target.get(), "")
            timeout_jump_action_id = self.jump_target_ids.get(self.failure_target.get(), "")
            if on_found == "jump" and not found_jump_action_id:
                raise ValueError("请选择成功后要跳转的行对象")
            if on_timeout == "jump" and not timeout_jump_action_id:
                raise ValueError("请选择失败后要跳转的行对象")
        except (TypeError, ValueError) as exc:
            show_floating_notice(self, "参数错误", str(exc))
            return None
        result = {
            "type": "row_list_condition_click",
            "list_region": list_region,
            "left_region": left_region,
            "right_region": right_region,
            "click_region": click_region,
            "row_height": row_height,
            "screenshot_path": self.source_image.get().strip(),
            "left_condition": left_condition,
            "right_condition": right_condition,
            "button": self.button.get(),
            "click_count": click_count,
            "no_match_action": _option_value(
                self.no_match_action.get(), self.NO_MATCH_ACTIONS, "finish",
            ),
            "retry_interval_ms": retry_interval,
            "on_found": on_found,
            "found_jump_action_id": found_jump_action_id,
            "on_timeout": on_timeout,
            "timeout_jump_action_id": timeout_jump_action_id,
        }
        self._failure_segment_fields(result)
        return result

    def test_recognition(self, source: str = "screen"):
        action = self._build_action()
        if action is None:
            return
        if source == "image" and not self.source_image.get().strip():
            show_floating_notice(
                self, "缺少测试图片", "请先点「选择图片…」选一张整屏截图，再测试图片识别。",
            )
            return
        if self.on_test is not None:
            self.on_test(action, source=source)

    def save(self):
        action = self._build_action()
        if action is None:
            return
        self.result = action
        try:
            self.master.after_idle(lambda root=self.master: activate_main_after_modal(root))
        except tk.TclError:
            pass
        self.destroy()
