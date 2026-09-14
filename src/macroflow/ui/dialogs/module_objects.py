from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL,
    ensure_action_ids, recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
    special_action_label,
)
from macroflow.core.storage import (
    BASE_DIR, DIRECTION_SCRIPTS_DIR, IMAGES_DIR, SCRIPTS_DIR, display_path,
    DEFAULT_MODULE_INTERVAL_MS, DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    load_app_settings, load_module_images_dir, load_module_objects,
    load_script, load_template_regions,
    module_image_inventory, module_objects_by_category,
    registered_module_object, resolve_path, save_module_images_dir, save_module_objects,
    save_template_regions, save_script, script_category_for_path, update_module_object,
)
from PIL import Image, ImageEnhance, ImageTk
from pathlib import Path
from macroflow.core.image_match import capture_bgr
import copy
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk
import uuid

from .base import (
    AFTER_ACTION_LABELS,
    AFTER_ACTION_VALUES,
    CATEGORY_LABELS,
    COLOR_BG,
    COLOR_BLUE_SELECTION,
    COLOR_MUTED,
    COLOR_SURFACE,
    COLOR_TEXT,
    DIALOG_FRAME_MARGIN,
    FALLBACK_ON_MATCH_LABELS,
    FALLBACK_ON_MATCH_VALUES,
    FONT_BODY,
    FONT_FAMILY,
    FONT_SUBTITLE,
    MODULE_RESULT_OPTIONS,
    ModalDialog,
    SCRIPT_CATEGORY_LABELS,
    SECOND_MATCH_CLICK_TARGET_LABELS,
    SECOND_MATCH_CLICK_TARGET_VALUES,
    Tooltip,
    app_windows,
    clamp_to_work_area,
    dark_checkbutton,
    duration_var,
    fit_scrollable_window_to_content,
    fit_window_to_content,
    monitor_work_area_for,
    pad,
    px,
    scrollable_dialog_body,
    show_floating_notice,
)
from .helpers import (
    bind_wheel_to_scroll_tree,
    configure_module_list_scrollbar,
    configure_module_tree_styles,
    image_jump_target_options,
    module_manager_label,
    module_manager_special_action_summary,
    module_manager_tag,
    module_result_option_label,
    module_result_option_value,
    pinyin_sort_key,
    registered_template_options,
    select_jump_target_label,
)
from .screen_pickers import (
    ScreenOffsetPicker,
    ScreenPointPicker,
    ScreenRegionPicker,
)
from .segments import (
    FailureSegmentMixin,
    SegmentEditorMixin,
    module_action_for_key,
    module_reference_binding,
)


def _valid_scripts_in(root: Path) -> list[Path]:
    """Return valid JSON scripts directly under a directory (recursively)."""
    if not root.is_dir():
        return []
    paths: dict[str, Path] = {}
    for path in root.rglob("*.json"):
        resolved = path.resolve()
        try:
            load_script(resolved)
        except Exception:
            continue
        paths[str(resolved).casefold()] = resolved
    return sorted(paths.values(), key=lambda path: pinyin_sort_key(path.stem))


def direction_script_files() -> list[Path]:
    """Return valid JSON scripts from the hotkey direction folder (scripts/方向)."""
    return _valid_scripts_in(resolve_path(DIRECTION_SCRIPTS_DIR))


def configured_script_files(settings: dict | None = None) -> list[Path]:
    """Return valid JSON scripts from every configured script directory."""
    settings = settings or load_app_settings()
    roots = []
    for setting_key, default in (
        ("level_scripts_dir", "scripts/关卡"),
        ("level_pack_scripts_dir", "scripts/关卡封装"),
        ("switch_scripts_dir", "scripts/切换"),
        ("direction_scripts_dir", DIRECTION_SCRIPTS_DIR),
    ):
        roots.append(resolve_path(str(settings.get(setting_key, default))))
    paths: dict[str, Path] = {}
    for root in roots:
        for path in _valid_scripts_in(root):
            paths[str(path.resolve()).casefold()] = path
    return sorted(paths.values(), key=lambda path: pinyin_sort_key(path.stem))


def prepend_module_to_scripts(key: str, category: str,
                              script_paths: list[str | Path]) -> tuple[int, list[Path], list[tuple[Path, str]]]:
    """Insert one module at row 1 of selected scripts and report added/skipped/errors."""
    added = 0
    skipped: list[Path] = []
    errors: list[tuple[Path, str]] = []
    module_obj = registered_module_object(key)
    if module_obj is not None and module_obj.get("recognize") == "number":
        return 0, [], [
            (Path(raw_path), "读取数字需要为每个脚本行设置比较值和两路跳转，不能批量加入")
            for raw_path in script_paths
        ]
    if module_obj is not None and not module_obj.get("enabled", True):
        return 0, [], [
            (Path(raw_path), "模块已禁用，不能插入到脚本")
            for raw_path in script_paths
        ]
    for raw_path in script_paths:
        path = Path(raw_path)
        try:
            script = load_script(path)
            if category in ("workflow_global", "script_global", "global") and (
                    script.is_global or str(script.settings.get("category", "")) in (
                        "global", "workflow_global", "script_global",
                    )
                    or bool(script.settings.get("trigger"))):
                skipped.append(path)
                continue
            ensure_action_ids(script.actions)
            action = module_action_for_key(key, category)
            if category in ("script_global", "global"):
                action["jump_row"] = 2
                if script.actions:
                    action["jump_action_id"] = str(
                        script.actions[0].get(ACTION_ID_KEY, "")
                    ).strip()
            script.actions.insert(0, action)
            ensure_action_ids(script.actions)
            save_script(script, path)
            added += 1
        except Exception as exc:
            errors.append((path, str(exc)))
    return added, skipped, errors


def remove_module_from_scripts(key: str,
                               script_paths: list[str | Path]) -> tuple[int, list[Path], list[tuple[Path, str]]]:
    """Remove every action row referencing one module from selected scripts.

    Return (scripts_changed, scripts_untouched, errors). Untouched scripts
    either never used the module or (for nested references) only reference it
    inside another module's code segment; only top-level rows are removed.
    """
    removed = 0
    untouched: list[Path] = []
    errors: list[tuple[Path, str]] = []
    for raw_path in script_paths:
        path = Path(raw_path)
        try:
            script = load_script(path)
            before = len(script.actions)
            script.actions = [
                action for action in script.actions
                if str(action.get("module_key", "")).strip() != key
            ]
            if len(script.actions) == before:
                untouched.append(path)
                continue
            ensure_action_ids(script.actions)
            save_script(script, path)
            removed += 1
        except Exception as exc:
            errors.append((path, str(exc)))
    return removed, untouched, errors


def find_module_references(key: str,
                           script_paths: list[str | Path]) -> list[dict]:
    """Return every top-level script action that references ``key`` in order."""
    references: list[dict] = []
    for raw_path in script_paths:
        path = Path(raw_path)
        try:
            script = load_script(path)
        except Exception:
            continue
        for index, action in enumerate(script.actions):
            if (action.get("module_ref")
                    and str(action.get("module_key", "")).strip() == key):
                references.append({"path": path, "index": index, "action": action})
    return references


def remove_module_references(references: list[dict]) -> tuple[int, list[Path], list[tuple[Path, str]]]:
    """Remove only the supplied reference locations, grouped by script."""
    grouped: dict[Path, list[int]] = {}
    for reference in references:
        grouped.setdefault(Path(reference["path"]), []).append(int(reference["index"]))
    changed = 0
    untouched: list[Path] = []
    errors: list[tuple[Path, str]] = []
    for path, indices in grouped.items():
        try:
            script = load_script(path)
            valid = {index for index in indices if 0 <= index < len(script.actions)}
            if not valid:
                untouched.append(path)
                continue
            script.actions = [
                action for index, action in enumerate(script.actions)
                if index not in valid
            ]
            ensure_action_ids(script.actions)
            save_script(script, path)
            changed += len(valid)
        except Exception as exc:
            errors.append((path, str(exc)))
    return changed, untouched, errors


class TemplateRegionFormDialog(SegmentEditorMixin, ModalDialog):
    """新增 / 编辑模块对象表单：模板图片 + 框选区域 + 行为属性。

    保存时校验图片、区域、识别成功后动作相关字段都有效，通过后把
    ``(old_key, key, object_dict)`` 写入 ``self.result``，``show()`` 返回该
    结果；取消返回 ``None``。old_key 非空表示编辑旧条目（更换图片时移除旧
    条目）；object_dict 提供时按它初始化所有字段（对象实时引用编辑）。
    """

    def __init__(self, parent, old_key: str = "", region: list[int] | None = None,
                 category: str = "switch", object_dict: dict | None = None,
                 segment_depth: int = 0, initial_image: str = "",
                 images_dir: str | Path | None = None):
        super().__init__(
            parent, "编辑模块对象" if old_key else "新增模块对象", 620, 760,
            align_top=True, defer_show=True,
        )
        obj = dict(object_dict or {})
        if obj.get("category") not in CATEGORY_LABELS:
            obj["category"] = category if category in CATEGORY_LABELS else "switch"
        self.old_key = old_key
        self.module_enabled = bool(obj.get("enabled", True))
        self.images_dir = Path(images_dir) if images_dir else load_module_images_dir()
        self.segment_depth = segment_depth
        self.picker = None
        pure_edit = bool(obj.get("pure_action"))
        self.category_choices = (
            [CATEGORY_LABELS["special"]]
            if pure_edit else [
                CATEGORY_LABELS["switch"], CATEGORY_LABELS["workflow_global"],
                CATEGORY_LABELS["script_global"],
            ]
        )
        legacy_template = (
            old_key if old_key and not old_key.startswith("module:") else ""
        )
        image_source = str(obj.get("template") or initial_image or legacy_template)
        if pure_edit:
            # 纯动作特殊模块（无图片，如「重新执行工作流」）：用名称做 key，
            # 图片留空才能走纯动作保存分支。
            obj["category"] = "special"
            self.image_var = tk.StringVar(value="")
        else:
            self.image_var = tk.StringVar(value=image_source)
        raw_name = str(obj.get("name") or "").strip()
        default_name = self._default_name_for_image(image_source)
        # 旧版本曾把完整图片路径当作名称；打开编辑时自动迁回干净文件名。
        legacy_names = {str(image_source).strip(), Path(str(image_source).replace("\\", "/")).name}
        if not raw_name or raw_name in legacy_names:
            raw_name = default_name
            self._auto_name_value = default_name
        else:
            self._auto_name_value = raw_name if raw_name == default_name else ""
        self.name_var = tk.StringVar(value=raw_name)
        stored_region = region if region is not None else obj.get("region", [0, 0, 0, 0])
        self.region_var = tk.StringVar(
            value=",".join(map(str, stored_region))
            if len(stored_region) == 4 and (stored_region[2] > 0 or stored_region[3] > 0)
            else "",
        )
        self.category_var = tk.StringVar(value=CATEGORY_LABELS[obj["category"]])
        self.recognize_var = tk.StringVar(value={
            "text": "识别文字",
            "number": "读取数字",
            "none": "无需识图",
        }.get(obj.get("recognize"), "模板图片"))
        self.expected_text_var = tk.StringVar(value=str(obj.get("expected_text", "")))
        self.match_mode_var = tk.StringVar(
            value="等于" if obj.get("match_mode") == "equals" else "包含",
        )
        self.wait_text_absent_var = tk.BooleanVar(
            value=bool(obj.get("wait_text_absent", False)),
        )
        self.ocr_offset_up_var = tk.StringVar(value=str(obj.get("ocr_offset_up", 0)))
        self.ocr_offset_down_var = tk.StringVar(value=str(obj.get("ocr_offset_down", 0)))
        self.ocr_offset_left_var = tk.StringVar(value=str(obj.get("ocr_offset_left", 0)))
        self.ocr_offset_right_var = tk.StringVar(value=str(obj.get("ocr_offset_right", 0)))
        self.threshold_var = tk.StringVar(value=str(obj.get("threshold", 0.85)))
        # 新建模块的「检测间隔」默认 1 秒；编辑已有模块时沿用对象里保存的值。
        self.interval_var = duration_var(obj.get("interval_ms", DEFAULT_MODULE_INTERVAL_MS))
        self.start_delay_var = duration_var(obj.get("start_delay_ms", 0))
        fallback_objects = load_module_objects()
        fallback_key = str(obj.get("fallback_module_key", "")).strip()
        self.fallback_module_keys = {"（不启用）": ""}
        for key, value in fallback_objects.items():
            if key == old_key or value.get("recognize") in ("number", "none") or value.get("pure_action"):
                continue
            name = str(value.get("name", "")).strip() or Path(key.replace("\\", "/")).stem
            label = name if name not in self.fallback_module_keys else f"{name} · {key}"
            self.fallback_module_keys[label] = key
        fallback_label = next(
            (label for label, key in self.fallback_module_keys.items() if key == fallback_key),
            "（不启用）",
        )
        self.fallback_module_key_var = tk.StringVar(value=fallback_label)
        fallback_on_match = str(obj.get("fallback_on_match", "")).strip()
        if fallback_on_match not in FALLBACK_ON_MATCH_LABELS:
            fallback_on_match = "click_continue" if bool(obj.get("fallback_click", False)) else "continue"
        self.fallback_on_match_var = tk.StringVar(
            value=FALLBACK_ON_MATCH_LABELS[fallback_on_match],
        )
        self.fallback_click_var = tk.BooleanVar(value=fallback_on_match.startswith("click_"))
        self.fallback_click_count_var = tk.StringVar(value=str(obj.get("fallback_click_count", 1)))
        self.fallback_click_interval_var = duration_var(obj.get("fallback_click_interval_ms", 100))
        self.ignore_background_var = tk.BooleanVar(
            value=bool(obj.get("ignore_background", False)),
        )
        self.blocking_var = tk.BooleanVar(value=bool(obj.get("blocking", False)))
        self.hold_enabled_var = tk.BooleanVar(value=bool(obj.get("hold_enabled", False)))
        self.hold_var = duration_var(obj.get("hold_ms", 1000))
        self.delay_var = duration_var(obj.get("delay_ms", 0))
        after_action = str(obj.get("after_action", "click_match"))
        if obj.get("recognize") == "text" and after_action == "second_match":
            # 识别文字方式没有二次图片匹配：编辑旧对象时回落为点击识别区域。
            after_action = "click_match"
        self.after_action_var = tk.StringVar(
            value=AFTER_ACTION_LABELS.get(after_action, "点击识别区域"),
        )
        self.run_code_after_action_var = tk.BooleanVar(
            value=bool(obj.get("run_code_after_action", after_action == "run_actions")),
        )
        self.button_var = tk.StringVar(value=obj.get("button", "left"))
        self.click_count_var = tk.StringVar(value=str(obj.get("click_count", 1)))
        click_point = obj.get("click_point") or []
        self.click_point_var = tk.StringVar(
            value=",".join(map(str, click_point)) if len(click_point) == 2 else "",
        )
        self.second_template_var = tk.StringVar(value=str(obj.get("second_match_template", "")))
        # 二次识别直接使用所选模板对象登记的区域，不再单独维护另一份区域。
        self.second_timeout_var = duration_var(obj.get("second_match_timeout_ms", 3000))
        second_click_target = str(obj.get("second_match_click_target", "second"))
        self.second_click_target_var = tk.StringVar(
            value=SECOND_MATCH_CLICK_TARGET_LABELS.get(
                second_click_target, "第二次识别位置",
            ),
        )
        second_click_region = obj.get("second_match_click_region") or []
        self.second_click_region_var = tk.StringVar(
            value=",".join(map(str, second_click_region))
            if len(second_click_region) == 4 and second_click_region[2] > 0 else "",
        )
        self.segment = [dict(item) for item in obj.get("on_success_actions") or []]
        self.run_code_on_timeout_var = tk.BooleanVar(
            value=bool(obj.get("run_code_on_timeout", False)),
        )
        self.not_found_timeout_var = duration_var(
            obj.get("not_found_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS),
        )
        self.timeout_segment = [dict(item) for item in obj.get("on_timeout_actions") or []]
        # 表单行数多，小屏 / 高 DPI（打包版按真实 DPI 渲染）下固定高度窗口会把
        # 底部的延时、识别成功后动作、点击按钮等行挤出窗口且没有滚动条（用户
        # 报告“相似度、检测间隔、延时、动作、点击按钮等都没有输入的地方”）。
        # 用 Canvas + 滚动条包住表单，所有行始终可滚动到达。
        canvas = tk.Canvas(self, background=COLOR_BG, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body = ttk.Frame(canvas, padding=px(12))
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        self.body = body
        self._canvas = canvas
        self._scrollbar = scrollbar

        def update_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def stretch_body(event):
            canvas.itemconfigure(body_window, width=event.width)

        body.bind("<Configure>", update_scrollregion)
        canvas.bind("<Configure>", stretch_body)
        # 窗口初次映射时再校正一次滚动区域（避免首帧布局未完成导致的
        # 视口与内容不匹配，行被裁掉）。
        canvas.bind("<Map>", update_scrollregion)
        self.bind("<MouseWheel>", self._scroll_form)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        row = 0
        ttk.Label(
            body, text="模块对象设置", foreground=COLOR_TEXT,
            font=(FONT_FAMILY, 14, "bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        ttk.Label(
            body, text="只填写当前模块需要的内容；将鼠标停在 ? 上可查看说明。",
            foreground=COLOR_MUTED,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=pad(2, 12))
        row += 1
        self.basic_section_heading = self._section_heading(body, row, "基本信息")
        row += 1
        self.row_category = self._labeled_row(
            body, row, "模块类别",
            lambda m: self._row_combo(
                m, self.category_var, self.category_choices,
                width=14, set_attr="category_combo",
            ),
            "切换模块用于普通识图；工作流全局用于工作流常驻检测；脚本全局用于脚本内检测；特殊模块为固定动作。",
        )
        self.category_combo.bind("<<ComboboxSelected>>", self._toggle_sections)
        row += 1
        self.row_name = self._labeled_row(
            body, row, "名称",
            lambda m: ttk.Entry(m, textvariable=self.name_var, width=24),
            "模块名称，脚本里插入后以此显示。特殊模块纯动作只需填名称，"
            "行为固定为「重新执行工作流」。",
        )
        row += 1
        self.row_image = self._labeled_row(
            body, row, "模板图片", self._image_picker_row,
            "“选择图片…”登记已有图片文件；“截图新建…”框选屏幕区域生成新模板图片，"
            "截图区域同时作为默认搜索区域。特殊模块纯动作不需要图片。",
        )
        row += 1
        self.row_region = self._labeled_row(
            body, row, "框选区域 (x,y,w,h)",
            lambda m: self._entry_button_row(
                m, readonly=True, textvariable=self.region_var,
                button_text="框选区域…", command=self._pick_region, expand=True,
            ),
            "识别时在屏幕上搜索的区域，留空 = 全屏搜索。",
        )
        row += 1
        self.detect_section_heading = self._section_heading(body, row, "识别设置")
        row += 1
        self.row_recognize = self._labeled_row(
            body, row, "识别方式",
            lambda m: self._row_combo(
                m, self.recognize_var, ("模板图片", "识别文字", "读取数字", "无需识图"),
                width=14, set_attr="recognize_combo",
            ),
            "模板图片：按图像匹配；识别文字：截取区域做 OCR；读取数字：把指定区域"
            "内的数字从左到右拼成整数，并由脚本行判断；无需识图：模块执行到时直接运行。",
        )
        self.recognize_combo.bind("<<ComboboxSelected>>", self._toggle_sections)
        row += 1
        self.row_expected_text = self._labeled_row(
            body, row, "期望文字",
            lambda m: ttk.Entry(m, textvariable=self.expected_text_var, width=24),
            "识别区域里应出现的文字，支持“包含 / 等于”匹配；留空 = 识别到任意文字即命中。",
        )
        row += 1
        self.row_match_mode = self._labeled_row(
            body, row, "匹配方式",
            lambda m: self._row_combo(m, self.match_mode_var, ("包含", "等于"), width=10),
            "包含：识别结果里出现期望文字即命中；等于：去掉首尾空白后整体相同（不区分大小写）。",
        )
        row += 1
        self.row_wait_text_absent = self._labeled_row(
            body, row, "等待目标消失",
            lambda m: dark_checkbutton(
                m, "直到区域内检测不到期望文字才完成", self.wait_text_absent_var,
            ),
            "模板图片和识别文字均可使用。勾选后，每次找到目标都会执行成功动作并重新识别；"
            "直到区域内检测不到目标才完成当前模块。F12 仍可紧急停止。",
        )
        row += 1
        self.row_threshold = self._labeled_row(
            body, row, "相似度 (0.1–1.0)",
            lambda m: ttk.Entry(m, textvariable=self.threshold_var, width=14),
            "图像匹配相似度阈值，越高越严格，越低越容易误识别。",
        )
        row += 1
        self.row_ignore_background = self._labeled_row(
            body, row, "忽略背景",
            lambda m: dark_checkbutton(m, "只识别字，忽略背景颜色", self.ignore_background_var),
            "开启后只按模板上的文字笔画匹配，背景颜色、纹理、高亮变化都不影响识别。"
            "适合按钮背景会变色/高亮/变灰的场景；背景无法自动识别时自动回退普通匹配。",
        )
        row += 1
        self.row_interval = self._labeled_row(
            body, row, "检测间隔",
            lambda m: ttk.Entry(m, textvariable=self.interval_var, width=14),
            "每两次识别之间的等待毫秒数，越小响应越快、越耗资源；新建模块默认 1 秒。",
        )
        row += 1
        self.row_start_delay = self._labeled_row(
            body, row, "进入模块前延时",
            lambda m: ttk.Entry(m, textvariable=self.start_delay_var, width=14),
            "进入该模块后、开始识别前的等待毫秒数；和下面「延时」（识别成功后才等、执行动作前）不是一回事。"
            "脚本全局模块表示脚本开始执行后先等这段再开始识别。支持 ms / s / min。",
        )
        row += 1
        self.row_fallback_module = self._labeled_row(
            body, row, "备用识别模块",
            lambda m: self._row_combo(
                m, self.fallback_module_key_var, tuple(self.fallback_module_keys), width=32,
                set_attr="fallback_module_combo",
            ),
            "主模块等待且尚未超时期间，每轮先识别主模块，再同时识别这里选择的备用图片或文字模块。主命中立即结束；备用命中后继续等待主模块。",
        )
        self.fallback_module_combo.bind("<<ComboboxSelected>>", self._toggle_sections)
        row += 1
        self.row_fallback_click = self._labeled_row(
            body, row, "备用命中后",
            lambda m: self._row_combo(
                m, self.fallback_on_match_var,
                tuple(FALLBACK_ON_MATCH_LABELS.values()), width=32,
                set_attr="fallback_on_match_combo",
            ),
            "可选择不点击或点击备用命中位置，并决定继续识别主模块还是直接退出主模块识别。备用持续存在时只处理一次，消失后再次出现才会再次处理。",
        )
        self.fallback_on_match_combo.bind("<<ComboboxSelected>>", self._toggle_sections)
        row += 1
        self.row_fallback_click_settings = self._labeled_row(
            body, row, "备用点击参数",
            lambda m: self._entry_pair_row(
                m, self.fallback_click_count_var, self.fallback_click_interval_var,
                "次数", "间隔 ms",
            ),
            "备用模块命中后连续点击的次数，以及两次点击之间的等待毫秒数。",
        )
        row += 1
        self.row_blocking = self._labeled_row(
            body, row, "阻塞识别",
            lambda m: dark_checkbutton(m, "启用", self.blocking_var),
            "开启：识别不到就一直等，直到识别成功才继续；"
            "关闭：识别不到直接跳过。",
        )
        row += 1
        self.row_hold = self._labeled_row(
            body, row, "持续超过",
            self._build_hold_control,
            "工作流全局和脚本全局模块使用。勾选后，命中状态持续达到设定时长才触发；"
            "不勾选则第一次识别命中就立即执行。",
        )
        row += 1
        self.row_delay = self._labeled_row(
            body, row, "延时",
            lambda m: ttk.Entry(m, textvariable=self.delay_var, width=14),
            "识别成功后、执行动作前的等待毫秒数。",
        )
        row += 1
        self.action_section_heading = self._section_heading(body, row, "成功后动作")
        row += 1
        self.row_after = self._labeled_row(
            body, row, "识别成功后执行",
            lambda m: self._row_combo(
                m, self.after_action_var, list(AFTER_ACTION_LABELS.values()),
                width=18, set_attr="after_action_combo",
            ),
            "识别成功后的行为：点击识别区域 / 点击自定义位置 / 成功后继续 / "
            "二次识别后点击。需要追加代码时，在下方启用附加代码段。",
        )
        self.after_action_combo.bind("<<ComboboxSelected>>", self._toggle_sections)
        row += 1
        self.row_button = self._labeled_row(
            body, row, "点击按钮",
            lambda m: self._row_combo(
                m, self.button_var, ("left", "right", "middle"), width=10,
            ),
            "点击使用的鼠标键：左键 / 右键 / 中键。",
        )
        row += 1
        self.row_click_count = self._labeled_row(
            body, row, "点击次数",
            lambda m: ttk.Entry(m, textvariable=self.click_count_var, width=10),
            "识别成功后在所选位置连续点击多少下，默认 1；"
            "点击识别区域、自定义位置和二次识别点击均使用该次数。",
        )
        row += 1
        self.row_ocr_offset = self._labeled_row(
            body, row, "文字点击偏移 (px)",
            self._build_ocr_offset_control,
            "以识别到的文字框中心为基准。分别填写向上、向下、向左、向右的像素；"
            "也可点“拖拽选取…”：在起点按住左键，拖到终点后松开，自动计算偏移。",
        )
        row += 1
        self.row_click_point = self._labeled_row(
            body, row, "点击位置 (x,y)",
            lambda m: self._entry_button_row(
                m, textvariable=self.click_point_var, width=14,
                button_text="幕布选取…", command=self.start_click_point_selection,
            ),
            "点击的屏幕坐标，可点“幕布选取…”在屏幕上选取。",
        )
        row += 1
        self.row_second_template = self._labeled_row(
            body, row, "二次识别模板",
            lambda m: self._row_combo(
                m, self.second_template_var,
                registered_template_options(str(obj.get("second_match_template", ""))),
            ),
            "识别成功后，再识别另一个已登记模板，两个都识别到才执行。",
        )
        row += 1
        self.row_second_region = None
        self.row_second_timeout = self._labeled_row(
            body, row, "二次识别超时",
            lambda m: ttk.Entry(m, textvariable=self.second_timeout_var, width=14),
            "等待二次识别的最大毫秒数；开启阻塞识别时无限等待。",
        )
        row += 1
        self.row_second_click_target = self._labeled_row(
            body, row, "二次识别后点击位置",
            lambda m: self._row_combo(
                m, self.second_click_target_var,
                list(SECOND_MATCH_CLICK_TARGET_LABELS.values()), width=18,
                set_attr="second_click_target_combo",
            ),
            "二次识别成功后，可点击第一次识别中心、第二次识别中心或自定义框选区域中心。",
        )
        self.second_click_target_combo.bind("<<ComboboxSelected>>", self._toggle_sections)
        row += 1
        self.row_second_click_region = self._labeled_row(
            body, row, "自定义点击区域 (x,y,w,h)",
            lambda m: self._entry_button_row(
                m, textvariable=self.second_click_region_var, width=14,
                button_text="框选…", command=self._pick_second_click_region,
            ),
            "仅选择“自定义框选区域”时使用；识别成功后点击该区域中心。",
        )
        row += 1
        self.segment_section_heading = self._section_heading(body, row, "附加代码段")
        row += 1
        self.row_run_code_after_action = self._labeled_row(
            body, row, "动作完成后再执行代码段",
            lambda m: dark_checkbutton(
                m, "启用", self.run_code_after_action_var,
                command=self._toggle_sections,
            ),
            "可选。先完成上面的点击、继续或二次识别动作，再依次执行代码段；"
            "代码段完成后才继续原脚本或工作流。",
        )
        row += 1
        self.segment_frame = self._build_segment_panel(body, row)
        row += 1
        self.timeout_section_heading = self._section_heading(body, row, "未识别超时")
        row += 1
        self.row_run_code_on_timeout = self._labeled_row(
            body, row, "超过时限未识别执行代码段",
            lambda m: dark_checkbutton(
                m, "启用", self.run_code_on_timeout_var,
                command=self._toggle_sections,
            ),
            "与成功后代码段完全独立。连续未识别达到下方时限后，执行超时代码段。",
        )
        row += 1
        self.row_not_found_timeout = self._labeled_row(
            body, row, "未识别时限",
            lambda m: ttk.Entry(m, textvariable=self.not_found_timeout_var, width=14),
            "切换模块达到该时限后向当前脚本行返回失败；若启用下方代码段，会先执行代码段。",
        )
        row += 1
        self.timeout_segment_frame = self._build_segment_panel(
            body, row, segment_attr="timeout_segment",
            listbox_attr="timeout_segment_listbox", title="未识别超时后执行的代码段",
        )
        row += 1
        ttk.Separator(body).grid(row=row, column=0, columnspan=2, sticky="ew", pady=pad(16, 0))
        row += 1
        ttk.Label(
            body,
            text="保存后所有引用该模块的脚本自动生效。",
            foreground=COLOR_MUTED, wraplength=px(560),
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=pad(12, 0))
        row += 1
        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=pad(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存模块", command=self.save).pack(side="right", padx=px(8))
        self._toggle_sections()
        fit_scrollable_window_to_content(
            self, parent, body, self._scrollbar, align_top=True,
        )

    def _build_hold_control(self, master):
        frame = ttk.Frame(master)
        dark_checkbutton(
            frame, "启用持续延时", self.hold_enabled_var,
            command=self._toggle_hold_control,
        ).pack(side="left")
        self.hold_entry = ttk.Entry(frame, textvariable=self.hold_var, width=14)
        self.hold_entry.pack(side="left", padx=pad(10, 0))
        return frame

    def _build_ocr_offset_control(self, master):
        frame = ttk.Frame(master)
        for label, variable in (
            ("上", self.ocr_offset_up_var), ("下", self.ocr_offset_down_var),
            ("左", self.ocr_offset_left_var), ("右", self.ocr_offset_right_var),
        ):
            ttk.Label(frame, text=label).pack(side="left", padx=(8 if frame.winfo_children() else 0, 3))
            ttk.Entry(frame, textvariable=variable, width=6).pack(side="left")
        ttk.Button(
            frame, text="拖拽选取…", command=self._pick_ocr_offset,
        ).pack(side="left", padx=pad(10, 0))
        return frame

    def _pick_ocr_offset(self):
        self.picker = ScreenOffsetPicker(
            self, self.master, self._apply_ocr_offset,
            hidden_windows=self._ancestors_to_hide(),
            tip_text="在起点按住鼠标左键，不要松开；拖到终点后松开，自动记录两点偏移；Esc 取消",
        )
        self.picker.start()

    def _apply_ocr_offset(self, start_x, start_y, end_x, end_y):
        dx, dy = int(end_x) - int(start_x), int(end_y) - int(start_y)
        self.ocr_offset_left_var.set(str(max(0, -dx)))
        self.ocr_offset_right_var.set(str(max(0, dx)))
        self.ocr_offset_up_var.set(str(max(0, -dy)))
        self.ocr_offset_down_var.set(str(max(0, dy)))

    def _toggle_hold_control(self):
        entry = getattr(self, "hold_entry", None)
        if entry is not None:
            entry.configure(state="normal" if self.hold_enabled_var.get() else "disabled")

    def _labeled_row(self, body, row, label, control_factory, tip: str = ""):
        """字段行：label 在左、控件在右。

        control_factory(frame) 以本行 frame 为 master 创建控件 —— 控件是行的
        真实子控件。不能用 grid(in_=frame) 把 body 的子控件放进行的网格：
        那种布局下控件的窗口没有落在声明的位置，真实点击进不了输入框、焦点
        也进不去（v1.82.6，用户反馈「只有分类和模板图片能改」）。v1.82.1 曾
        因直接 grid 到 body 的 (0,1) 与分类下拉重叠，故必须由工厂创建。
        """
        frame = ttk.Frame(body)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=pad(10, 0))
        frame.columnconfigure(0, minsize=px(180))
        frame.columnconfigure(1, weight=1)
        name_box = ttk.Frame(frame)
        name_box.grid(row=0, column=0, sticky="w", padx=pad(0, 14))
        ttk.Label(name_box, text=label).pack(side="left")
        if tip:
            help_badge = tk.Label(
                name_box, text="?", width=2, cursor="hand2",
                background=COLOR_BLUE_SELECTION, foreground="#EAF4FF",
                font=(FONT_FAMILY, FONT_BODY, "bold"), relief="flat",
            )
            help_badge.pack(side="left", padx=pad(6, 0))
            Tooltip(help_badge, tip, anchor=frame)
        control = control_factory(frame)
        control.grid(row=0, column=1, sticky="ew")
        return frame

    @staticmethod
    def _section_heading(body, row: int, text: str):
        frame = ttk.Frame(body)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=pad(8, 0))
        ttk.Label(
            frame, text=text, foreground="#79BFFF",
            font=(FONT_FAMILY, FONT_SUBTITLE, "bold"),
        ).pack(side="left")
        ttk.Separator(frame).pack(side="left", fill="x", expand=True, padx=pad(10, 0))
        return frame

    def _image_picker_row(self, frame):
        row = ttk.Frame(frame)
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.image_var, state="readonly").grid(
            row=0, column=0, sticky="ew",
        )
        ttk.Button(row, text="选择图片…", command=self._choose_image).grid(
            row=0, column=1, padx=pad(8, 0),
        )
        ttk.Button(row, text="截图新建…", command=self._capture).grid(
            row=0, column=2, padx=pad(8, 0),
        )
        return row

    @staticmethod
    def _default_name_for_image(value: str | Path) -> str:
        """Use the final path component without its extension as module name."""
        return Path(str(value).replace("\\", "/")).stem

    def _set_image_and_default_name(self, value: str | Path) -> None:
        display_value = display_path(value)
        current_name = self.name_var.get().strip()
        previous_auto_name = getattr(self, "_auto_name_value", "")
        self.image_var.set(display_value)
        if not current_name or current_name == previous_auto_name:
            default_name = self._default_name_for_image(display_value)
            self.name_var.set(default_name)
            self._auto_name_value = default_name

    def _row_combo(self, frame, var, values, width: int | None = None,
                   set_attr: str | None = None):
        """行内只读下拉框（真实子控件）。"""
        combo = ttk.Combobox(
            frame, textvariable=var, values=list(values),
            state="readonly", **({"width": width} if width else {}),
        )
        combo.grid(row=0, column=1, sticky="w")
        if set_attr:
            setattr(self, set_attr, combo)
        return combo

    def _entry_button_row(self, frame, *, readonly=False, textvariable,
                          width=14, button_text="", command=None,
                          expand=False):
        """行内输入框 + 按钮（真实子控件）；expand 时输入框撑满本行。"""
        entry = ttk.Entry(
            frame, textvariable=textvariable,
            state="readonly" if readonly else "normal", width=width,
        )
        entry.grid(row=0, column=1, sticky="we" if expand else "w")
        if button_text:
            ttk.Button(frame, text=button_text, command=command).grid(
                row=0, column=2, padx=pad(8, 0), sticky="w",
            )
        return entry

    def _entry_pair_row(self, frame, first_var, second_var, first_label, second_label):
        """Two compact numeric entries used for count plus interval settings."""
        row = ttk.Frame(frame)
        ttk.Entry(row, textvariable=first_var, width=8).grid(row=0, column=0, sticky="w")
        ttk.Label(row, text=first_label).grid(row=0, column=1, padx=pad(6, 12), sticky="w")
        ttk.Entry(row, textvariable=second_var, width=10).grid(row=0, column=2, sticky="w")
        ttk.Label(row, text=second_label).grid(row=0, column=3, padx=pad(6, 0), sticky="w")
        return row

    def _fallback_on_match_value(self) -> str:
        variable = getattr(self, "fallback_on_match_var", None)
        if variable is not None:
            value = FALLBACK_ON_MATCH_VALUES.get(variable.get())
            if value:
                return value
        return "click_continue" if bool(self.fallback_click_var.get()) else "continue"

    def _toggle_sections(self, _event=None):
        after = self.after_action_var.get()
        category = self.category_var.get()
        # 特殊模块 = 纯动作（无图片）：只保留 分类 + 名称，隐藏全部检测/行为行。
        pure = category == "特殊模块"
        text_mode = not pure and self.recognize_var.get() == "识别文字"
        number_mode = not pure and self.recognize_var.get() == "读取数字"
        direct_mode = not pure and self.recognize_var.get() == "无需识图"
        direct_global = direct_mode and category in (
            "工作流全局模块", "脚本全局模块",
        )
        if hasattr(self, "after_action_combo"):
            self.after_action_combo.configure(values=(
                ("点击自定义位置", "成功后继续")
                if direct_mode else ("成功后继续",)
                if number_mode else tuple(AFTER_ACTION_LABELS.values())
            ))
        if number_mode and after != "成功后继续":
            self.after_action_var.set("成功后继续")
            after = "成功后继续"
        elif direct_mode and after not in ("点击自定义位置", "成功后继续"):
            self.after_action_var.set("成功后继续")
            after = "成功后继续"
        if text_mode and after == "二次识别后点击":
            # 识别文字方式没有二次图片匹配，强制回落到点击识别区域。
            self.after_action_var.set("点击识别区域")
            after = "点击识别区域"
        # 名称行对普通模块和特殊模块（纯动作，只保留 分类+名称）都可见；
        # 传 pure 会把普通模块的名称行也 grid_remove 掉（v1.82.1 回归）。
        self._set_row(self.row_name, True)
        self._set_row(self.row_image, not pure and not text_mode and not number_mode and not direct_mode)
        self._set_row(self.row_region, not pure and not direct_mode)
        self._set_row(self.detect_section_heading, not pure)
        self._set_row(self.row_recognize, not pure)
        self._set_row(self.row_expected_text, text_mode)
        self._set_row(self.row_match_mode, text_mode)
        self._set_row(self.row_wait_text_absent, not pure and not number_mode and not direct_mode)
        self._set_row(self.row_threshold, not pure and not text_mode and not number_mode and not direct_mode)
        self._set_row(self.row_ignore_background, not pure and not text_mode and not number_mode and not direct_mode)
        self._set_row(self.row_interval, not pure and not direct_mode)
        self._set_row(self.row_start_delay, not pure)
        fallback_supported = not pure and not number_mode and not direct_mode
        self._set_row(self.row_fallback_module, fallback_supported)
        self._set_row(
            self.row_fallback_click,
            fallback_supported and bool(
                getattr(self, "fallback_module_keys", {}).get(
                    self.fallback_module_key_var.get(),
                    self.fallback_module_key_var.get().strip(),
                )
            ),
        )
        self._set_row(
            getattr(self, "row_fallback_click_settings", None),
            fallback_supported and self._fallback_on_match_value().startswith("click_") and bool(
                getattr(self, "fallback_module_keys", {}).get(
                    self.fallback_module_key_var.get(), self.fallback_module_key_var.get().strip(),
                )
            ),
        )
        self._set_row(self.row_blocking, not pure and not direct_mode)
        self._set_row(self.row_delay, not pure and not number_mode and not direct_global)
        self._set_row(self.action_section_heading, not pure and not number_mode and not direct_global)
        self._set_row(self.row_after, not pure and not number_mode and not direct_global)
        self._set_row(
            self.row_hold,
            not direct_mode and category in ("工作流全局模块", "脚本全局模块"),
        )
        self._toggle_hold_control()
        self._set_row(
            self.row_button,
            not pure and after in ("点击识别区域", "点击自定义位置", "二次识别后点击"),
        )
        self._set_row(
            self.row_click_count,
            not pure and after in ("点击识别区域", "点击自定义位置", "二次识别后点击"),
        )
        self._set_row(
            self.row_ocr_offset,
            text_mode and after == "点击识别区域",
        )
        self._set_row(self.row_click_point, not pure and after == "点击自定义位置")
        show_second = not pure and not text_mode and after == "二次识别后点击"
        self._set_row(self.row_second_template, show_second)
        self._set_row(self.row_second_timeout, show_second)
        self._set_row(self.row_second_click_target, show_second)
        self._set_row(
            self.row_second_click_region,
            show_second and self.second_click_target_var.get() == "自定义框选区域",
        )
        self._set_row(self.segment_section_heading, not pure and not number_mode and not direct_global)
        self._set_row(self.row_run_code_after_action, not pure and not number_mode and not direct_global)
        self._set_row(
            self.segment_frame,
            not pure and not number_mode and not direct_global
            and bool(self.run_code_after_action_var.get()),
        )
        self._set_row(self.timeout_section_heading, not pure and (not direct_mode or direct_global))
        self._set_row(
            self.row_run_code_on_timeout,
            not pure and not number_mode and (not direct_mode or direct_global),
        )
        timeout_enabled = (
            not pure and not number_mode and (not direct_mode or direct_global)
            and bool(self.run_code_on_timeout_var.get())
        )
        switch_failure_timeout = (
            category == "切换模块" and not direct_mode
            and not bool(self.blocking_var.get())
        )
        self._set_row(
            self.row_not_found_timeout, timeout_enabled or switch_failure_timeout,
        )
        self._set_row(self.timeout_segment_frame, timeout_enabled)
        self._resize_for_content()

    def _set_row(self, row, visible: bool):
        if row is None:
            return
        if visible:
            row.grid()
        else:
            row.grid_remove()

    def _resize_for_content(self):
        # 内容在 Canvas 里滚动，窗口自身 reqsize 不再反映内容尺寸：从 body 的
        # 实际需求尺寸计算窗口大小；超出显示器可用区域时封顶（内容可滚动到达）。
        # winfo_* 返回的已经是当前 DPI 下的像素，不能再过一次 px()——那会在
        # 200% 缩放下把窗口放大一倍并越过屏幕右下角。
        try:
            self.update_idletasks()
            area = monitor_work_area_for(self.master)
            width = min(
                max(
                    self.winfo_width(),
                    self.body.winfo_reqwidth() + self._scrollbar.winfo_reqwidth(),
                ),
                max(1, int(area["width"]) - px(DIALOG_FRAME_MARGIN)),
            )
            height = min(
                max(self.winfo_height(), self.body.winfo_reqheight() + 4),
                max(1, int(area["height"]) - px(DIALOG_FRAME_MARGIN)),
            )
            # 放大后仍留在同一块屏内：位置按新尺寸收进可用区域，不跳屏。
            x, y = clamp_to_work_area(area, width, height, self.winfo_x(), self.winfo_y())
            self.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass

    def _scroll_form(self, event):
        """滚轮滚动整个表单；列表自身已有滚轮绑定，跳过避免双重滚动。"""
        if not event.delta or isinstance(event.widget, tk.Listbox):
            return
        self._canvas.yview_scroll(-int(event.delta / 120), "units")

    def _choose_image(self):
        self.images_dir.mkdir(parents=True, exist_ok=True)
        path = filedialog.askopenfilename(
            parent=self, title="选择模板图片",
            initialdir=str(self.images_dir),
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")],
        )
        if path:
            self._set_image_and_default_name(path)
            self._toggle_sections()

    def _ancestors_to_hide(self):
        """从管理器对话框向上到应用主窗口的所有窗口。

        框选区域 / 截图新建时整条窗口链都要隐藏，否则主窗口被幕布之外的其他
        软件窗口遮挡无法看清要框选的区域，截图也会截进本程序自己的窗口。
        """
        return app_windows(self.master)

    def _capture(self):
        """框选屏幕区域截图存为新模板图片，并同时填入图片与区域两项。"""

        def on_result(region):
            try:
                self.images_dir.mkdir(parents=True, exist_ok=True)
                screen, _origin = capture_bgr(tuple(int(part) for part in region))
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:23]
                path = self.images_dir / f"template_{stamp}.png"
                Image.fromarray(screen[:, :, ::-1]).save(path)
            except Exception as exc:
                show_floating_notice(self, "截图失败", str(exc))
                return
            self._set_image_and_default_name(path)
            self.region_var.set(",".join(map(str, region)))
            self._toggle_sections()

        self.picker = ScreenRegionPicker(
            self, self.master, on_result,
            hidden_windows=self._ancestors_to_hide(),
            tip_text="按住鼠标左键，从左上角向右下角拖动框选要截图成模板的区域；松开完成，Esc 取消",
        )
        self.picker.start()

    def _pick_region(self):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: self.region_var.set(",".join(map(str, region))),
            hidden_windows=self._ancestors_to_hide(),
            tip_text="按住鼠标左键，从左上角向右下角拖动框选该模板的默认搜索区域；松开完成，Esc 取消",
        )
        self.picker.start()

    def _pick_second_click_region(self):
        self.picker = ScreenRegionPicker(
            self, self.master,
            lambda region: self.second_click_region_var.set(",".join(map(str, region))),
            hidden_windows=self._ancestors_to_hide(),
            tip_text="框选二次识别成功后要点击的区域；松开后将点击该区域中心，Esc 取消",
        )
        self.picker.start()

    def start_click_point_selection(self):
        """幕布选取点击位置：整条窗口链都要让开。

        只藏本表单与其直接父窗口是不够的：本表单可能是从「模块管理」里打开的，
        而主窗口还在它上面一层，幕布上就仍然看得见软件自己的界面（也会被一起
        截进幕布）。统一走 ScreenPointPicker，用 _ancestors_to_hide() 把从父窗口
        到应用主窗口的每一层都藏掉，与框选区域 / 截图新建同一套行为。
        """
        self.picker = ScreenPointPicker(
            self, self.master, self._apply_picked_click_point,
            tip_text="点击要执行操作的位置；只记录坐标，不会点击下方窗口；Esc 取消",
            hidden_windows=self._ancestors_to_hide(),
        )
        self.picker.start()

    def _apply_picked_click_point(self, x, y):
        self.click_point_var.set(f"{int(x)},{int(y)}")

    def save(self):
        if self.category_var.get() == "特殊模块":
            # 特殊模块 = 纯动作（无图片）：名称做 key，行为固定为「重新执行工作流」。
            name = self.name_var.get().strip()
            if not name:
                show_floating_notice(self, "缺少名称", "特殊模块纯动作需要填写名称。")
                return
            self.result = (
                self.old_key, name,
                {"category": "special", "name": name, "pure_action": True},
            )
            self.destroy()
            return
        text_mode = self.recognize_var.get() == "识别文字"
        number_mode = self.recognize_var.get() == "读取数字"
        direct_mode = self.recognize_var.get() == "无需识图"
        direct_global = direct_mode and self.category_var.get() in (
            "工作流全局模块", "脚本全局模块",
        )
        template_key = self.image_var.get().strip()
        if number_mode and self.category_var.get() != "切换模块":
            show_floating_notice(self, "类别不适用", "读取数字模块只能保存为切换模块。")
            return
        if not text_mode and not number_mode and not direct_mode and not template_key:
            show_floating_notice(self, "缺少模板图片", "请先“选择图片…”或“截图新建…”。")
            return
        region: list[int] = []
        if not text_mode and not direct_mode and not self.region_var.get().strip():
            # 模板图片和数字读取必须指定区域；识别文字方式留空表示全屏。
            show_floating_notice(self, "缺少框选区域", "请先“框选区域…”。")
            return
        if self.region_var.get().strip():
            parts = self.region_var.get().split(",")
            if len(parts) != 4:
                show_floating_notice(self, "缺少框选区域", "请先“框选区域…”。")
                return
            try:
                region = [int(part) for part in parts]
            except ValueError:
                show_floating_notice(
                    self, "区域格式错误", f"“{self.region_var.get()}”不是有效的 x,y,w,h 数字。",
                )
                return
            if region[2] <= 0 or region[3] <= 0:
                show_floating_notice(self, "区域无效", "框选区域的宽高必须大于 0。")
                return
        expected_text = self.expected_text_var.get().strip()
        match_mode = "equals" if self.match_mode_var.get() == "等于" else "contains"
        threshold = 0.85
        if not text_mode and not number_mode and not direct_mode:
            try:
                threshold = float(self.threshold_var.get())
            except ValueError:
                show_floating_notice(self, "相似度格式错误", "相似度必须是数字，例如 0.85。")
                return
            if not 0.1 <= threshold <= 1:
                show_floating_notice(self, "相似度无效", "相似度必须在 0.1 到 1.0 之间。")
                return
        try:
            interval = max(50, int(self.interval_var.get()))
        except ValueError:
            show_floating_notice(self, "检测间隔格式错误", "检测间隔必须是不小于 50 的整数（毫秒）。")
            return
        try:
            start_delay = max(0, min(86400000, int(self.start_delay_var.get())))
        except ValueError:
            show_floating_notice(self, "开始识别前延时格式错误", "延时必须是大于等于 0 的时间。")
            return
        try:
            delay = max(0, int(self.delay_var.get()))
            hold = max(0, int(self.hold_var.get()))
            click_count = max(1, min(9999, int(self.click_count_var.get())))
            fallback_count_var = getattr(self, "fallback_click_count_var", None)
            fallback_interval_var = getattr(self, "fallback_click_interval_var", None)
            fallback_click_count = max(1, min(9999, int(fallback_count_var.get()) if fallback_count_var else 1))
            fallback_click_interval = max(0, min(60000, int(fallback_interval_var.get()) if fallback_interval_var else 100))
        except ValueError:
            show_floating_notice(
                self, "数字格式错误",
                "延时和持续时间必须是大于等于 0 的整数；点击次数必须是大于等于 1 的整数。",
            )
            return
        after_value = AFTER_ACTION_VALUES.get(self.after_action_var.get(), "click_match")
        if direct_mode and after_value not in ("click_custom", "continue"):
            after_value = "continue"
        try:
            ocr_offsets = {
                "ocr_offset_up": max(0, int(self.ocr_offset_up_var.get())),
                "ocr_offset_down": max(0, int(self.ocr_offset_down_var.get())),
                "ocr_offset_left": max(0, int(self.ocr_offset_left_var.get())),
                "ocr_offset_right": max(0, int(self.ocr_offset_right_var.get())),
            }
        except ValueError:
            show_floating_notice(
                self, "文字点击偏移格式错误",
                "向上、向下、向左、向右偏移必须是大于等于 0 的整数像素。",
            )
            return
        click_point: list[int] = []
        if after_value == "click_custom":
            click_point = self._parse_point(
                self.click_point_var.get(), "点击位置",
                "请输入自定义点击坐标 x,y（逗号分隔），或点“幕布选取…”。",
            )
            if click_point is None:
                return
        second_template = ""
        second_region: list[int] = []
        second_timeout = 3000
        second_click_target = "second"
        second_click_region: list[int] = []
        if after_value == "second_match":
            second_template = self.second_template_var.get().strip()
            if not second_template:
                show_floating_notice(self, "缺少二次识别模板", "请选择二次识别要识别的模板。")
                return
            try:
                second_timeout = max(0, int(self.second_timeout_var.get()))
            except ValueError:
                show_floating_notice(
                    self, "二次识别超时格式错误",
                    "二次识别超时必须是大于等于 0 的整数（毫秒）。",
                )
                return
            second_click_target = SECOND_MATCH_CLICK_TARGET_VALUES.get(
                self.second_click_target_var.get(), "second",
            )
            if second_click_target == "custom_region":
                second_click_region = self._parse_region_or_empty(
                    self.second_click_region_var.get(),
                    label="自定义点击区域", empty_hint="必须框选一个区域",
                )
                if second_click_region is None:
                    return
                if not second_click_region:
                    show_floating_notice(
                        self, "缺少自定义点击区域",
                        "请选择“框选…”设置二次识别成功后要点击的区域。",
                    )
                    return
        run_code_after_action = (
            not number_mode and not direct_global and bool(self.run_code_after_action_var.get())
        )
        if run_code_after_action:
            if not self.segment:
                show_floating_notice(
                    self, "代码段为空",
                    "已启用“动作完成后再执行代码段”，请至少添加一个动作。",
                )
                return
            ensure_action_ids(self.segment)
        run_code_on_timeout = (
            not number_mode and (not direct_mode or direct_global)
            and bool(self.run_code_on_timeout_var.get())
        )
        try:
            not_found_timeout = max(0, int(self.not_found_timeout_var.get()))
        except ValueError:
            show_floating_notice(
                self, "未识别时限格式错误",
                "未识别时限必须是大于等于 0 的整数（毫秒）。",
            )
            return
        if run_code_on_timeout:
            if not self.timeout_segment:
                show_floating_notice(
                    self, "超时代码段为空",
                    "已启用“超过时限未识别执行代码段”，请至少添加一个动作。",
                )
                return
            ensure_action_ids(self.timeout_segment)
        name = self.name_var.get().strip() or (
            "识别文字" if text_mode else
            "读取数字" if number_mode else
            "无需识图" if direct_mode else self._default_name_for_image(template_key)
        )
        module_key = self.old_key or f"module:{uuid.uuid4().hex}"
        module_dict = {
            "category": {
                "切换模块": "switch",
                "工作流全局模块": "workflow_global",
                "脚本全局模块": "script_global",
            }.get(self.category_var.get(), "special"),
            "enabled": getattr(self, "module_enabled", True),
            "name": name,
            "template": "" if number_mode else template_key,
            "region": region,
            "threshold": threshold,
            "interval_ms": interval,
            "start_delay_ms": start_delay,
            "fallback_module_key": (
                getattr(self, "fallback_module_keys", {}).get(
                    self.fallback_module_key_var.get(), self.fallback_module_key_var.get().strip(),
                )
                if not number_mode and not direct_mode else ""
            ),
            "fallback_on_match": self._fallback_on_match_value(),
            "fallback_click": self._fallback_on_match_value().startswith("click_"),
            "fallback_click_count": fallback_click_count,
            "fallback_click_interval_ms": fallback_click_interval,
            "ignore_background": bool(self.ignore_background_var.get()),
            "blocking": False if direct_mode else bool(self.blocking_var.get()),
            "hold_enabled": bool(self.hold_enabled_var.get()),
            "hold_ms": hold,
            "delay_ms": 0 if number_mode else delay,
            "after_action": "continue" if number_mode else after_value,
            "run_code_after_action": run_code_after_action,
            "click_point": click_point,
            **ocr_offsets,
            "button": self.button_var.get()
            if self.button_var.get() in ("left", "right", "middle") else "left",
            "click_count": click_count,
            "second_match_template": second_template,
            "second_match_region": second_region,
            "second_match_timeout_ms": second_timeout,
            "second_match_click_target": second_click_target,
            "second_match_click_region": second_click_region,
            "on_success_actions": [] if number_mode else self.segment,
            "run_code_on_timeout": run_code_on_timeout,
            "not_found_timeout_ms": not_found_timeout,
            "on_timeout_actions": [] if number_mode else self.timeout_segment,
            "wait_text_absent": False if direct_mode or number_mode else bool(self.wait_text_absent_var.get()),
        }
        if text_mode:
            module_dict["recognize"] = "text"
            module_dict["expected_text"] = expected_text
            module_dict["match_mode"] = match_mode
        elif number_mode:
            module_dict["recognize"] = "number"
        elif direct_mode:
            module_dict["recognize"] = "none"
        self.result = (self.old_key, module_key, module_dict)
        self.destroy()

    def _parse_point(self, text: str, label: str, hint: str) -> list[int] | None:
        text = text.strip()
        parts = [part.strip() for part in text.split(",")]
        if len(parts) != 2:
            show_floating_notice(self, f"缺少{label}", hint)
            return None
        try:
            return [int(part) for part in parts]
        except ValueError:
            show_floating_notice(self, f"{label}格式错误", f"“{text}”不是有效的 x,y 数字。")
            return None

    def _parse_region_or_empty(self, text: str, label: str = "二次识别区域",
                               empty_hint: str = "留空表示全屏") -> list[int] | None:
        text = text.strip()
        if not text:
            return []
        parts = [part.strip() for part in text.split(",")]
        if len(parts) != 4:
            show_floating_notice(
                self, f"{label}格式错误",
                f"“{label}”需要 x,y,w,h 四个数字（{empty_hint}）。",
            )
            return None
        try:
            region = [int(part) for part in parts]
        except ValueError:
            show_floating_notice(self, f"{label}格式错误", f"“{text}”不是有效的 x,y,w,h 数字。")
            return None
        if region[2] <= 0 or region[3] <= 0:
            show_floating_notice(
                self, f"{label}无效", f"{label}的宽高必须大于 0（{empty_hint}）。",
            )
            return None
        return region


class ModuleImageInventoryDialog(ModalDialog):
    """Show which files in the image directory are used by module objects."""

    @staticmethod
    def _category_label(category: str) -> str:
        return {
            "switch": "切换",
            "workflow_global": "工作流全局",
            "script_global": "脚本全局",
            "special": "特殊",
        }.get(str(category), "—")

    def __init__(self, parent):
        super().__init__(parent, "图像采用情况", 900, 520)
        self.objects: dict[str, dict] = load_module_objects()
        self.images_dir = load_module_images_dir()
        self.images_dir_var = tk.StringVar(value=str(self.images_dir))
        self.inventory_items: dict[str, dict[str, str]] = {}
        self.inventory_filter = "all"
        self.sort_direction = "asc"
        self.current = "images"
        self.trees: dict[str, ttk.Treeview] = {}
        self.inventory_filter_buttons: dict[str, tk.Button] = {}
        self.module_tree_style = ttk.Style(self)
        configure_module_tree_styles(self.module_tree_style)

        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="查看图片是否已被模块采用；双击已采用图片可编辑对应模块。",
            foreground=COLOR_MUTED, wraplength=px(720),
        ).pack(anchor="w")
        directory_row = ttk.Frame(body)
        directory_row.pack(fill="x", pady=pad(10, 0))
        ttk.Label(directory_row, text="识图文件夹").pack(side="left")
        ttk.Entry(
            directory_row, textvariable=self.images_dir_var, state="readonly",
        ).pack(side="left", fill="x", expand=True, padx=pad(10, 8))
        ttk.Button(
            directory_row, text="选择目录…", command=self._choose_images_dir,
        ).pack(side="left")
        ttk.Button(
            directory_row, text="刷新", command=self._refresh_inventory,
        ).pack(side="left", padx=pad(8, 0))
        self.inventory_summary_var = tk.StringVar(value="")
        ttk.Label(
            body, textvariable=self.inventory_summary_var, foreground=COLOR_MUTED,
        ).pack(anchor="w", pady=pad(5, 0))

        filter_row = ttk.Frame(body)
        filter_row.pack(fill="x", pady=pad(8, 2))
        ttk.Label(filter_row, text="查看：", foreground=COLOR_MUTED).pack(side="left")
        for value, label in (("all", "全部图片"), ("adopted", "已采用"), ("unused", "未采用")):
            button = tk.Button(
                filter_row, text=label,
                command=lambda item=value: self._set_inventory_filter(item),
                background=COLOR_BLUE_SELECTION if value == "all" else COLOR_SURFACE,
                foreground="#FFFFFF" if value == "all" else COLOR_TEXT,
                activebackground=COLOR_BLUE_SELECTION, activeforeground="#FFFFFF",
                relief="flat", borderwidth=0, padx=px(12), pady=px(4), cursor="hand2",
                font=(FONT_FAMILY, FONT_BODY),
            )
            button.pack(side="left", padx=pad(0, 6))
            self.inventory_filter_buttons[value] = button

        # 按钮行先按 side="bottom" 占位：屏幕放不下时最后挂的控件先被裁，
        # 按钮排在列表后面就会被挤出窗口底部。
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=pad(12, 0))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True, pady=pad(4, 0))
        tree = ttk.Treeview(
            list_frame, columns=("status", "kind"), show="tree headings", height=10,
            style="ModuleManagerNeutral.Treeview",
        )
        tree.heading("#0", text="图片文件")
        tree.heading("status", text="采用情况")
        tree.heading("kind", text="模块类型")
        tree.column("#0", width=px(480))
        tree.column("status", width=px(135), anchor="center")
        tree.column("kind", width=px(150), anchor="center")
        tree.tag_configure("adopted", foreground="#7BC96F")
        tree.tag_configure("unused", foreground="#F2B84B")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        configure_module_list_scrollbar(tree, scrollbar)
        tree.bind("<Double-1>", lambda _event: self._open_inventory_item())
        tree.bind("<<TreeviewSelect>>", self._update_action_buttons)
        self.trees["images"] = tree
        self._apply_sort_heading(tree)
        bind_wheel_to_scroll_tree(self, lambda: self.trees.get("images"))

        self.add_button = ttk.Button(
            buttons, text="采用为模块", command=lambda: self._open_inventory_item(require_unused=True),
        )
        self.add_button.pack(side="left")
        self.edit_button = ttk.Button(
            buttons, text="编辑模块", command=self._open_inventory_item,
        )
        self.edit_button.pack(side="left", padx=pad(8, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")
        self._reload_tree()
        self._update_action_buttons()
        fit_window_to_content(self, parent)

    def _apply_sort_heading(self, tree):
        arrow = "↑" if self.sort_direction == "asc" else "↓"
        tree.heading(
            "#0", text=f"图片文件 {arrow}", command=self._toggle_sort_direction,
        )

    def _reload_tree(self):
        tree = self.trees["images"]
        tree.delete(*tree.get_children())
        inventory = module_image_inventory(self.images_dir, self.objects)
        self.inventory_items = {item["path"]: item for item in inventory}
        adopted_count = sum(bool(item["module_key"]) for item in inventory)
        self.inventory_summary_var.set(
            f"共 {len(inventory)} 张图片：已采用 {adopted_count}，未采用 {len(inventory) - adopted_count}"
        )
        current_filter = self.inventory_filter
        visible = [
            item for item in inventory
            if current_filter == "all"
            or (current_filter == "adopted" and bool(item["module_key"]))
            or (current_filter == "unused" and not item["module_key"])
        ]
        if current_filter != "all":
            self.inventory_summary_var.set(
                self.inventory_summary_var.get() + f"；当前显示 {len(visible)} 张"
            )
        visible.sort(
            key=lambda item: pinyin_sort_key(Path(item["path"].replace("\\", "/")).stem),
            reverse=self.sort_direction == "desc",
        )
        for item in visible:
            keys = item.get("module_keys") or ([item["module_key"]] if item["module_key"] else [])
            categories = {
                self._category_label(self.objects.get(key, {}).get("category"))
                for key in keys
            }
            category = "/".join(sorted(categories)) if categories else "—"
            tree.insert(
                "", "end", iid=item["path"],
                text=str(Path(item["path"].replace("\\", "/")).name),
                values=(item["status"], category),
                tags=("adopted" if item["module_key"] else "unused",),
            )

    def _set_inventory_filter(self, value: str):
        if value not in ("all", "adopted", "unused"):
            return
        self.inventory_filter = value
        for key, button in self.inventory_filter_buttons.items():
            selected = key == value
            button.configure(
                background=COLOR_BLUE_SELECTION if selected else COLOR_SURFACE,
                foreground="#FFFFFF" if selected else COLOR_TEXT,
            )
        self._reload_tree()

    def _toggle_sort_direction(self):
        self.sort_direction = "desc" if self.sort_direction == "asc" else "asc"
        self._apply_sort_heading(self.trees["images"])
        self._reload_tree()

    def _choose_images_dir(self):
        selected = filedialog.askdirectory(
            parent=self, title="选择识图文件夹", initialdir=str(self.images_dir),
        )
        if not selected:
            return
        self.images_dir = save_module_images_dir(selected)
        self.images_dir_var.set(str(self.images_dir))
        self._reload_tree()

    def _refresh_inventory(self):
        self.objects = load_module_objects()
        self._reload_tree()

    def _selected_inventory_item(self) -> dict | None:
        tree = self.trees["images"]
        selection = tree.selection()
        return self.inventory_items.get(selection[0]) if selection else None

    def _open_inventory_item(self, require_unused: bool = False):
        item = self._selected_inventory_item()
        if not item:
            show_floating_notice(self, "请先选择图片", "先在图片采用情况中选择一张图片。")
            return
        module_keys = list(item.get("module_keys") or [])
        module_key = item.get("module_key", "")
        if require_unused:
            self._open_form(category="switch", initial_image=item["path"])
            return
        if len(module_keys) > 1:
            show_floating_notice(
                self, "图片被多个模块使用",
                f"这张图片已被 {len(module_keys)} 个独立模块使用，请到模块对象管理中按模块名称编辑。",
            )
            return
        if module_key:
            self._open_form(module_key, self.objects.get(module_key))
            return
        self._open_form(category="switch", initial_image=item["path"])

    def _open_form(self, key: str = "", object_dict: dict | None = None,
                   category: str = "switch", initial_image: str = ""):
        form_kwargs = {"object_dict": object_dict, "category": category}
        if initial_image:
            form_kwargs["initial_image"] = initial_image
        if self.images_dir:
            form_kwargs["images_dir"] = self.images_dir
        result = TemplateRegionFormDialog(self, key, **form_kwargs).show()
        if result is None:
            return
        old_key, new_key, obj = result
        self.objects = update_module_object(new_key, obj, old_key=old_key)
        self._reload_tree()
        try:
            self.trees["images"].selection_set(new_key)
            self.trees["images"].see(new_key)
        except tk.TclError:
            pass

    def _update_action_buttons(self, _event=None):
        item = self._selected_inventory_item() if self.trees.get("images") else None
        adopted = bool(item and item.get("module_key"))
        self.add_button.configure(
            text="采用为模块", state="normal" if item and not adopted else "disabled",
        )
        self.edit_button.configure(
            text="编辑模块" if adopted else "采用并设置",
            state="normal" if item else "disabled",
        )


class TemplateRegionManagerDialog(ModalDialog):
    """Manage the module-object registry (template image + region + behavior).

    五个可点击切换的页签：全部 / 切换 / 工作流全局 / 脚本全局 / 特殊。
    每个条目是结构化模块对象（类别 + 行为属性），模块选择窗口和 module_ref
    脚本动作运行时实时引用这些对象。新增 / 编辑走
    :class:`TemplateRegionFormDialog`；双击普通模块可直接编辑。
    """

    TAB_KEYS = ("all", "switch", "workflow_global", "script_global", "special")

    def __init__(self, parent, app=None):
        super().__init__(parent, "模块对象管理", 1040, 520)
        self.app = app or getattr(parent, "_macroflow_app", None)
        self.objects: dict[str, dict] = load_module_objects()
        self.current = "all"
        self.trees: dict[str, ttk.Treeview] = {}
        self.sort_direction = "asc"
        self.module_tree_style = ttk.Style(self)
        configure_module_tree_styles(self.module_tree_style)
        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="双击普通模块直接编辑；特殊模块为固定动作，不提供编辑设置。",
            foreground=COLOR_MUTED, wraplength=px(670),
        ).pack(anchor="w")
        # 按钮行先按 side="bottom" 占位再挂列表：pack 按顺序分配空间，屏幕放
        # 不下时最后挂的控件先被裁——按钮行排在列表后面就会被挤出窗口底部。
        buttons = ttk.Frame(body)
        self.buttons_frame = buttons
        buttons.pack(side="bottom", fill="x", pady=pad(12, 0))
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True, pady=pad(10, 0))
        for tab_key in self.TAB_KEYS:
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=self._tab_label(tab_key))
            self._build_tab(tab_key, tab)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        # 初始化选中的是第一个页签（全部），但该选择不触发 TabChanged 事件，
        # 显式同步一次，保证 self.current 与当前可见页签一致（编辑 / 移除都
        # 操作当前页签的树，不同步会导致"全部"页签下按钮静默失效）。
        self._on_tab_changed()
        self._reload_trees()
        # 移除撤销栈：(key, object_dict)；"移除所选模块"后可用按钮或 Ctrl+Z 恢复。
        self._undo_stack: list[tuple[str, dict]] = []
        self.add_button = ttk.Button(buttons, text="新增模块", command=self._open_add)
        self.add_button.pack(side="left")
        self.edit_button = ttk.Button(buttons, text="编辑选中", command=self._open_edit)
        self.edit_button.pack(side="left", padx=pad(8, 0))
        self.enabled_button = ttk.Button(
            buttons, text="禁用选中", command=self._toggle_selected_enabled,
        )
        self.enabled_button.pack(side="left", padx=pad(8, 0))
        self.remove_button = ttk.Button(buttons, text="移除所选模块", command=self._remove_selected)
        self.remove_button.pack(side="left", padx=pad(8, 0))
        self.undo_button = ttk.Button(
            buttons, text="撤销移除", command=self._undo_remove, state="disabled",
        )
        self.undo_button.pack(side="left", padx=pad(8, 0))
        self.batch_button = ttk.Button(
            buttons, text="批量加入脚本…", command=self._batch_add_selected,
        )
        self.batch_button.pack(side="left", padx=pad(8, 0))
        self.batch_remove_button = ttk.Button(
            buttons, text="批量从脚本删除…", command=self._batch_remove_from_scripts,
        )
        self.batch_remove_button.pack(side="left", padx=pad(8, 0))
        self.reference_button = ttk.Button(
            buttons, text="查看引用位置", command=self._show_references,
        )
        self.reference_button.pack(side="left", padx=pad(8, 0))
        self.remove_all_references_button = ttk.Button(
            buttons, text="删除全部引用", command=self._remove_all_references,
        )
        self.remove_all_references_button.pack(side="left", padx=pad(8, 0))
        ttk.Button(
            buttons, text="图像采用情况…", command=self._open_image_inventory,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")
        self.bind("<Control-z>", self._undo_remove)
        # 滚轮在窗口任意位置都能翻当前页签的模块列表（不必把光标停在表格上）。
        bind_wheel_to_scroll_tree(self, self._current_tree)
        self._update_action_buttons()

        # 固定 470 高度在打包后的 EXE（按真实 DPI 渲染）里会装不下内容：
        # 高 DPI 下列表和按钮行的实际需求高度超过窗口，按钮行被挤出窗口底部
        # （按钮完全看不见，用户报告"什么按钮都没有"）。这里按内容实际需求
        # 重设窗口尺寸（geometry 的宽高即内容区大小）并重新居中。
        self._fit_window_to_content(parent)

    def _fit_window_to_content(self, parent):
        """按内容实际需求重设窗口尺寸并居中（防高 DPI 下按钮行被裁掉）。"""
        fit_window_to_content(self, parent)

    @staticmethod
    def _tab_label(tab_key: str) -> str:
        return {
            "all": "全部模块", "switch": "切换",
            "workflow_global": "工作流全局", "script_global": "脚本全局",
            "special": "特殊",
        }[tab_key]

    def _build_tab(self, tab_key: str, tab: ttk.Frame):
        list_frame = ttk.Frame(tab)
        list_frame.pack(fill="both", expand=True, padx=px(4), pady=pad(4, 0))
        if tab_key == "special":
            tree = ttk.Treeview(
                list_frame, columns=("kind",), show="tree headings", height=10,
                style="ModuleManagerNeutral.Treeview",
            )
            tree.heading("#0", text="名称")
            tree.heading("kind", text="类型")
            tree.column("#0", width=px(380))
            tree.column("kind", width=90, anchor="center")
            tree.tag_configure("disabled", foreground="#707B85")
        else:
            tree = ttk.Treeview(
                list_frame, columns=("region", "special_actions"), show="tree headings", height=10,
                style="ModuleManagerNeutral.Treeview",
            )
            tree.heading("#0", text="模块名称")
            tree.heading("region", text="框选区域 (x,y,w,h)")
            tree.heading("special_actions", text="代码段特殊模块")
            tree.column("#0", width=px(310))
            tree.column("region", width=px(190), anchor="center")
            tree.column("special_actions", width=px(420))
            tree.tag_configure("blocking", foreground="#F2B84B")
            tree.tag_configure("special_action", foreground="#FF8DE1")
            tree.tag_configure("disabled", foreground="#707B85")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        configure_module_list_scrollbar(tree, scrollbar)
        tree.bind(
            "<Double-1>",
            lambda _event: self._open_edit(),
        )
        tree.bind("<Button-3>", self._show_module_context_menu, add="+")
        tree.bind("<<TreeviewSelect>>", self._update_action_buttons)
        self.trees[tab_key] = tree
        self._apply_sort_heading(tab_key, tree)

    @staticmethod
    def _sort_heading_label(tab_key: str) -> str:
        return {
            "all": "模块名称",
            "switch": "模块名称", "workflow_global": "模块名称",
            "script_global": "模块名称", "special": "名称",
        }[tab_key]

    def _apply_sort_heading(self, tab_key: str, tree):
        arrow = "↑" if getattr(self, "sort_direction", "asc") == "asc" else "↓"
        tree.heading(
            "#0", text=f"{self._sort_heading_label(tab_key)} {arrow}",
            command=self._toggle_sort_direction,
        )

    def _on_tab_changed(self, _event=None):
        self.current = self.TAB_KEYS[self.notebook.index(self.notebook.select())]
        self._update_action_buttons()

    def _current_tree(self):
        """当前页签的模块列表（滚轮绑定的目标）。"""
        return self.trees.get(getattr(self, "current", ""))

    def _reload_trees(self):
        for tab_key, tree in self.trees.items():
            self._reload_tree(tab_key, tree)

    def _reload_tree(self, tab_key: str, tree: ttk.Treeview):
        tree.delete(*tree.get_children())
        items = sorted(
            self.objects.items(),
            key=lambda item: pinyin_sort_key(
                str(item[1].get("name") or Path(item[0].replace("\\", "/")).stem)
            ),
            reverse=getattr(self, "sort_direction", "asc") == "desc",
        )
        for key, obj in items:
            pure = bool(obj.get("pure_action"))
            if tab_key == "all":
                if pure:
                    tag = module_manager_tag(obj)
                    tree.insert(
                        "", "end", iid=key, text=module_manager_label(key, obj),
                        values=("—", "固定特殊模块"), tags=((tag,) if tag else ()),
                    )
                else:
                    region = obj.get("region", [0, 0, 0, 0])
                    text = ",".join(map(str, region)) if region[2] > 0 else "未设置区域（全屏）"
                    tree.insert(
                        "", "end", iid=key, text=module_manager_label(key, obj),
                        values=(text, module_manager_special_action_summary(obj) or "—"),
                        tags=((module_manager_tag(obj),) if module_manager_tag(obj) else ()),
                    )
            elif tab_key in ("switch", "workflow_global", "script_global"):
                if obj.get("category") != tab_key or pure:
                    continue
                region = obj.get("region", [0, 0, 0, 0])
                text = ",".join(map(str, region)) if region[2] > 0 else "未设置区域（全屏）"
                tree.insert(
                    "", "end", iid=key, text=module_manager_label(key, obj),
                    values=(text, module_manager_special_action_summary(obj) or "—"),
                    tags=((module_manager_tag(obj),) if module_manager_tag(obj) else ()),
                )
            else:  # special
                if obj.get("category") != "special":
                    continue
                tag = module_manager_tag(obj)
                tree.insert(
                    "", "end", iid=key, text=module_manager_label(key, obj),
                    values=("特殊",), tags=((tag,) if tag else ()),
                )

    def _set_sort_direction(self, value: str):
        if value not in ("asc", "desc"):
            return
        self.sort_direction = value
        for tab_key, tree in self.trees.items():
            self._apply_sort_heading(tab_key, tree)
        self._reload_trees()

    def _toggle_sort_direction(self):
        self._set_sort_direction("desc" if self.sort_direction == "asc" else "asc")

    def _open_add(self):
        if self.current == "special":
            show_floating_notice(self, "固定特殊模块", "特殊模块由软件提供，不能新增或编辑。")
            return
        category = "switch" if self.current == "all" else self.current
        self._open_form("", category=category)

    def _open_edit(self):
        tree = self.trees[self.current]
        selection = tree.selection()
        if not selection:
            show_floating_notice(self, "请先选择模块", "先在列表里选中一个模块，再编辑。")
            return
        key = selection[0]
        obj = self.objects.get(key)
        if not obj or obj.get("category") == "special" or obj.get("pure_action"):
            show_floating_notice(self, "固定特殊模块", "该模块行为固定，无需也不能编辑。")
            return
        self._open_form(key, obj)

    def _show_module_context_menu(self, event):
        """Show move/copy actions for workflow/script global modules."""
        tree = event.widget
        key = tree.identify_row(event.y)
        if not key:
            return
        obj = self.objects.get(key)
        category = str(obj.get("category", "")) if obj else ""
        if category not in ("workflow_global", "script_global"):
            return
        tree.selection_set(key)
        tree.focus(key)
        target = "script_global" if category == "workflow_global" else "workflow_global"
        target_label = "脚本全局" if target == "script_global" else "工作流全局"
        menu = tk.Menu(
            self, tearoff=0, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            activebackground=COLOR_BLUE_SELECTION, activeforeground="#FFFFFF",
        )
        menu.add_command(
            label=f"改成{target_label}",
            command=lambda: self._change_global_module_category(key, target, copy_object=False),
        )
        menu.add_command(
            label=f"复制成{target_label}",
            command=lambda: self._change_global_module_category(key, target, copy_object=True),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _change_global_module_category(self, key: str, target: str,
                                       copy_object: bool = False):
        """Move or independently clone one global module into the other category."""
        obj = self.objects.get(key)
        if not obj or target not in ("workflow_global", "script_global"):
            return
        source = str(obj.get("category", ""))
        if source not in ("workflow_global", "script_global") or source == target:
            return
        changed = copy.deepcopy(obj)
        changed["category"] = target
        changed_key = f"module:{uuid.uuid4().hex}" if copy_object else key
        if copy_object and not str(changed.get("name") or "").strip():
            # 旧对象可能没有 name（过去以图片路径为键，靠文件名兜底显示）；
            # 复制后换成 module:<uuid> 键，兜底会退化成 uuid，复制时按模板文件名补名。
            changed["name"] = Path(
                str(changed.get("template") or key).replace("\\", "/")
            ).stem
        self.objects[changed_key] = changed
        save_module_objects(self.objects)
        self._reload_trees()
        target_label = "脚本全局" if target == "script_global" else "工作流全局"
        action_label = "复制" if copy_object else "移动"
        name = str(changed.get("name") or Path(key.replace("\\", "/")).stem)
        show_floating_notice(
            self, f"已{action_label}为{target_label}",
            f"“{name}”已{action_label}为{target_label}模块。",
        )

    def _open_form(self, key: str = "", object_dict: dict | None = None,
                   category: str = "switch", initial_image: str = ""):
        """打开新增 / 编辑表单；保存后更新对象仓库并持久化。

        表单结果 (old_key, new_key, object_dict)：old_key 非空且更换了图片时
        移除旧条目；图片不变则只更新对象属性。
        """
        form_kwargs = {"object_dict": object_dict, "category": category}
        if initial_image:
            form_kwargs["initial_image"] = initial_image
        form = TemplateRegionFormDialog(self, key, **form_kwargs)
        result = form.show()
        if result is None:
            return
        old_key, new_key, obj = result
        self.objects = update_module_object(new_key, obj, old_key=old_key)
        self._reload_trees()
        tree = self.trees[self.current]
        try:
            tree.selection_set(new_key)
            tree.see(new_key)
        except tk.TclError:
            # 编辑时改了类别，新条目不在当前页签树里（如 特殊→切换）。
            pass

    def _open_image_inventory(self):
        ModuleImageInventoryDialog(self).show()

    def _remove_selected(self):
        tree = self.trees[self.current]
        selection = tree.selection()
        if not selection:
            return
        key = selection[0]
        obj = self.objects.get(key)
        if obj is None:
            return
        self._undo_stack.append((key, dict(obj)))
        self.objects.pop(key, None)
        save_module_objects(self.objects)
        self._reload_trees()
        self._update_undo_button()

    def _selected_module(self) -> tuple[str, dict] | None:
        tree = self.trees.get(self.current)
        selection = tree.selection() if tree is not None else ()
        key = selection[0] if selection else ""
        obj = self.objects.get(key)
        return (key, obj) if key and obj else None

    def _toggle_selected_enabled(self):
        tree = self.trees.get(self.current)
        selection = tree.selection() if tree is not None else ()
        row_id = selection[0] if selection else ""
        selected = self._selected_module()
        if not selected:
            show_floating_notice(self, "请先选择模块", "先选择一个模块，再启用或禁用。")
            return
        key, obj = selected
        enabled = not bool(obj.get("enabled", True))
        obj["enabled"] = enabled
        save_module_objects(self.objects)
        self._reload_trees()
        if tree is not None:
            try:
                target = row_id or key
                tree.selection_set(target)
                tree.see(target)
            except tk.TclError:
                pass
        self._update_action_buttons()

    def _reference_paths(self):
        return configured_script_files()

    def _show_references(self):
        selected = self._selected_module()
        if not selected:
            show_floating_notice(self, "请先选择模块", "先选中一个模块，再查看它的引用位置。")
            return
        key, obj = selected
        references = find_module_references(key, self._reference_paths())
        if not references:
            show_floating_notice(self, "没有引用", "当前配置的脚本中没有找到这个模块的引用。")
            return
        name = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
        ModuleReferenceDialog(
            self, name, references,
            on_jump=getattr(self.app, "jump_to_module_reference", None),
            on_delete_all=self._remove_all_references,
            on_delete_selected=self._remove_selected_references,
        ).show()

    def _remove_selected_references(self, references, dialog=None):
        if not references:
            show_floating_notice(self, "未选择引用", "请先在引用位置列表中选择要删除的行。")
            return
        if not messagebox.askyesno(
                "删除选中引用", f"确定删除选中的 {len(references)} 个引用位置吗？",
                parent=self,
        ):
            return
        removed, untouched, errors = remove_module_references(references)
        if dialog is not None:
            dialog.destroy()
        self._reload_trees()
        self._update_action_buttons()
        detail = f"已删除 {removed} 个引用位置。"
        if untouched:
            detail += f"\n{len(untouched)} 个脚本未发生变化。"
        if errors:
            detail += "\n失败：" + "；".join(
                f"{path.name}：{message}" for path, message in errors[:3]
            )
        show_floating_notice(self, "删除选中引用完成", detail, 7000)

    def _remove_all_references(self):
        selected = self._selected_module()
        if not selected:
            show_floating_notice(self, "请先选择模块", "先选中一个模块，再删除它的全部引用。")
            return
        key, obj = selected
        paths = self._reference_paths()
        references = find_module_references(key, paths)
        if not references:
            show_floating_notice(self, "没有引用", "当前配置的脚本中没有找到这个模块的引用。")
            return
        name = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
        if not messagebox.askyesno(
                "删除全部引用", f"确定从 {len(references)} 个引用位置删除“{name}”吗？",
                parent=self,
        ):
            return
        removed, untouched, errors = remove_module_from_scripts(key, paths)
        self._reload_trees()
        self._update_action_buttons()
        detail = f"已从 {removed} 个脚本删除“{name}”的全部引用。"
        if untouched:
            detail += f"\n{len(untouched)} 个脚本未发生变化。"
        if errors:
            detail += "\n失败：" + "；".join(
                f"{path.name}：{message}" for path, message in errors[:3]
            )
        show_floating_notice(self, "删除引用完成", detail, 7000)

    def _batch_add_selected(self):
        selected = self._selected_module()
        if not selected:
            show_floating_notice(self, "请先选择模块", "先选中一个已采用的模块，再批量加入脚本。")
            return
        key, obj = selected
        if not obj.get("enabled", True):
            show_floating_notice(
                self, "模块已禁用", "请先启用该模块，再把它加入脚本。",
            )
            return
        if obj.get("recognize") == "number":
            show_floating_notice(
                self, "不能批量加入",
                "读取数字需要为每个脚本行分别设置比较数字和两路跳转，请在脚本编辑器中逐行插入。",
            )
            return
        category = str(obj.get("category", "switch"))
        if category == "workflow_global":
            show_floating_notice(
                self, "不能加入脚本",
                "工作流全局模块只用于工作流；请使用脚本全局模块加入脚本。",
            )
            return
        name = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
        paths = configured_script_files()
        if not paths:
            show_floating_notice(self, "没有脚本", "配置的脚本目录中没有可用脚本。")
            return
        chosen = BatchModuleScriptDialog(self, name, paths).show()
        if not chosen:
            return
        added, skipped, errors = prepend_module_to_scripts(key, category, chosen)
        detail = f"已将“{name}”加入 {added} 个脚本的开头。"
        if skipped:
            detail += f"\n跳过 {len(skipped)} 个全局脚本（不能嵌套全局模块）。"
        if errors:
            detail += f"\n失败 {len(errors)} 个：" + "；".join(
                f"{path.name}：{message}" for path, message in errors[:3]
            )
        show_floating_notice(self, "批量加入完成", detail, 7000)

    def _batch_remove_from_scripts(self):
        selected = self._selected_module()
        if not selected:
            show_floating_notice(self, "请先选择模块", "先选中一个已采用的模块，再批量从脚本删除。")
            return
        key, obj = selected
        if obj.get("category") == "special" or obj.get("pure_action"):
            show_floating_notice(
                self, "不能从脚本删除",
                "特殊模块是固定动作，脚本里不保存模块引用，无法按模块批量删除。"
                "请直接在脚本编辑器删除对应动作行。",
            )
            return
        name = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
        paths = configured_script_files()
        if not paths:
            show_floating_notice(self, "没有脚本", "配置的脚本目录中没有可用脚本。")
            return
        chosen = BatchModuleScriptDialog(self, name, paths, mode="remove", module_key=key).show()
        if not chosen:
            return
        removed, untouched, errors = remove_module_from_scripts(key, chosen)
        detail = f"已从 {removed} 个脚本移除“{name}”的动作行。"
        if untouched:
            detail += f"\n{len(untouched)} 个脚本没有该模块，未改动。"
        if errors:
            detail += f"\n失败 {len(errors)} 个：" + "；".join(
                f"{path.name}：{message}" for path, message in errors[:3]
            )
        show_floating_notice(self, "批量删除完成", detail, 7000)

    def _undo_remove(self, _event=None):
        """撤销最近一次"移除所选模块"：恢复条目、保存并选中它。"""
        if not self._undo_stack:
            return
        key, obj = self._undo_stack.pop()
        self.objects[key] = obj
        save_module_objects(self.objects)
        self._reload_trees()
        tree = self.trees[self.current]
        try:
            tree.selection_set(key)
            tree.see(key)
        except tk.TclError:
            # 恢复的条目不在当前页签（理论上不会发生：类别未变）。
            pass
        self._update_undo_button()

    def _update_undo_button(self):
        self.undo_button.configure(
            state="normal" if self._undo_stack else "disabled",
        )

    def _update_action_buttons(self, _event=None):
        """Keep edit/add affordances aligned with the active module category."""
        tree = self.trees.get(self.current)
        selection = tree.selection() if tree is not None else ()
        obj = self.objects.get(selection[0]) if selection else None
        editable = bool(obj) and obj.get("category") != "special" and not obj.get("pure_action")
        self._update_selection_highlight(obj)
        edit_button = getattr(self, "edit_button", None)
        if edit_button is not None:
            edit_button.configure(text="编辑选中")
            edit_button.configure(state="normal" if editable else "disabled")
        add_button = getattr(self, "add_button", None)
        if add_button is not None:
            add_button.configure(text="新增模块")
            add_button.configure(state="disabled" if self.current == "special" else "normal")
        remove_button = getattr(self, "remove_button", None)
        if remove_button is not None:
            remove_button.configure(state="normal" if editable else "disabled")
        batch_button = getattr(self, "batch_button", None)
        if batch_button is not None:
            batch_button.configure(
                state="normal" if obj and obj.get("enabled", True) else "disabled",
            )
        enabled_button = getattr(self, "enabled_button", None)
        if enabled_button is not None:
            enabled_button.configure(
                text="禁用选中" if obj and obj.get("enabled", True) else "启用选中",
                state="normal" if obj else "disabled",
            )
        batch_remove_button = getattr(self, "batch_remove_button", None)
        if batch_remove_button is not None:
            batch_remove_button.configure(state="normal" if editable else "disabled")

    def _update_selection_highlight(self, obj: dict | None):
        tree = getattr(self, "trees", {}).get(getattr(self, "current", ""))
        if tree is None:
            return
        if obj is None:
            style_name = "ModuleManagerNeutral.Treeview"
        elif module_manager_special_action_summary(obj) and obj.get("enabled", True):
            style_name = "ModuleManagerSpecial.Treeview"
        elif obj.get("enabled", True):
            style_name = "ModuleManagerEnabled.Treeview"
        else:
            style_name = "ModuleManagerDisabled.Treeview"
        tree.configure(style=style_name)


class ModuleReferenceDialog(ModalDialog):
    """Navigate through every script location that references one module."""

    def __init__(self, parent, module_name: str, references: list[dict],
                 on_jump=None, on_delete_all=None, on_delete_selected=None):
        super().__init__(parent, "模块引用位置", 760, 430)
        self.references = list(references)
        self.on_jump = on_jump
        self.on_delete_all = on_delete_all
        self.on_delete_selected = on_delete_selected
        self.position = 0
        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body, text=f"模块“{module_name}”共有 {len(self.references)} 个引用位置。",
            foreground=COLOR_TEXT,
        ).pack(anchor="w")
        self.location_var = tk.StringVar()
        ttk.Label(body, textvariable=self.location_var, foreground=COLOR_MUTED).pack(
            anchor="w", pady=pad(6, 8),
        )
        # 按钮行先按 side="bottom" 占位，屏幕放不下时按钮不会被挤出窗口。
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=pad(12, 0))
        list_frame = ttk.Frame(body)
        list_frame.pack(fill="both", expand=True, pady=pad(4, 0))
        self.tree = ttk.Treeview(
            list_frame, columns=("path", "row"), show="headings", height=12,
            selectmode="extended",
        )
        self.tree.heading("path", text="脚本")
        self.tree.heading("row", text="引用行")
        self.tree.column("path", width=px(590))
        self.tree.column("row", width=90, anchor="center")
        # 引用可能有几十处，必须能滚动，否则超出可视行数的引用点不到。
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for index, reference in enumerate(self.references):
            self.tree.insert(
                "", "end", iid=str(index),
                values=(display_path(reference["path"]), reference["index"] + 1),
            )
        self.tree.bind("<Double-1>", lambda _event: self._jump_to(self.position))
        ttk.Button(buttons, text="上一个", command=self._previous).pack(side="left")
        ttk.Button(buttons, text="下一个", command=self._next).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            buttons, text="删除选中引用", command=self._delete_selected,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(
            buttons, text="删除全部引用", command=self._delete_all,
        ).pack(side="left", padx=pad(8, 0))
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")
        self._select_current()
        fit_window_to_content(self, parent)

    def _select_current(self):
        if not self.references:
            return
        self.position = max(0, min(self.position, len(self.references) - 1))
        self.tree.selection_set(str(self.position))
        self.tree.focus(str(self.position))
        self.tree.see(str(self.position))
        reference = self.references[self.position]
        self.location_var.set(
            f"第 {self.position + 1}/{len(self.references)} 个："
            f"{display_path(reference['path'])} · 第 {reference['index'] + 1} 行"
        )

    def _jump_to(self, position: int):
        if not self.references:
            return
        self.position = max(0, min(position, len(self.references) - 1))
        self._select_current()
        reference = self.references[self.position]
        if callable(self.on_jump):
            self.on_jump(reference["path"], reference["index"])

    def _next(self):
        self._jump_to((self.position + 1) % len(self.references))

    def _previous(self):
        self._jump_to((self.position - 1) % len(self.references))

    def _delete_all(self):
        if callable(self.on_delete_all):
            self.on_delete_all()

    def _delete_selected(self):
        selected = [int(item) for item in self.tree.selection()]
        references = [self.references[index] for index in selected if 0 <= index < len(self.references)]
        if callable(self.on_delete_selected):
            self.on_delete_selected(references, self)


class BatchModuleScriptDialog(ModalDialog):
    """Checkbox-style multi-selection of scripts for batch module insertion.

    ``mode="add"`` 勾选后把模块插入脚本第 1 行；``mode="remove"`` 勾选后从
    脚本删除该模块的所有引用行——此时列出每个脚本的引用行数（未使用的标
    "未使用"）并预勾选含该模块的脚本。
    """

    def __init__(self, parent, module_name: str, script_paths: list[Path],
                 mode: str = "add", module_key: str = ""):
        self.mode = mode
        super().__init__(parent, "批量从脚本删除" if mode == "remove" else "批量加入脚本", 700, 560)
        self.script_paths = list(script_paths)
        settings = load_app_settings()
        # 分类列与脚本编辑器“类别”下拉框共用同一套判定（见 script_category_for_path）。
        self.script_categories = [
            script_category_for_path(path, settings) for path in self.script_paths
        ]
        self.current_filter = "all"
        self.checked: set[int] = set()
        if mode == "remove":
            self.usage_counts = self._count_module_usage(module_key)
            self.checked = {
                index for index, count in self.usage_counts.items() if count
            }
        else:
            self.usage_counts = {}
        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=(f"选择要移除“{module_name}”的脚本："
                  if mode == "remove"
                  else f"选择要在第 1 行加入“{module_name}”的脚本："),
            foreground=COLOR_TEXT,
        ).pack(anchor="w")
        if mode == "remove":
            ttk.Label(
                body,
                text="只移除脚本中引用该模块的动作行，脚本其余内容不变。",
                foreground=COLOR_MUTED,
            ).pack(anchor="w", pady=pad(4, 0))
        self.filter_buttons: dict[str, tk.Button] = {}
        filter_row = ttk.Frame(body)
        filter_row.pack(fill="x", pady=pad(10, 0))
        ttk.Label(filter_row, text="分类：", foreground=COLOR_MUTED).pack(side="left")
        for category in SCRIPT_CATEGORY_LABELS:
            count = (
                len(self.script_paths) if category == "all"
                else self.script_categories.count(category)
            )
            button = tk.Button(
                filter_row,
                text=f"{SCRIPT_CATEGORY_LABELS[category]} {count}",
                command=lambda value=category: self._set_filter(value),
                background=COLOR_BLUE_SELECTION if category == "all" else COLOR_SURFACE,
                foreground="#FFFFFF" if category == "all" else COLOR_TEXT,
                activebackground=COLOR_BLUE_SELECTION, activeforeground="#FFFFFF",
                relief="flat", borderwidth=0, padx=px(10), pady=px(4), cursor="hand2",
                font=(FONT_FAMILY, FONT_BODY),
            )
            button.pack(side="left", padx=pad(0, 6))
            self.filter_buttons[category] = button
        # 按钮行先按 side="bottom" 占位，屏幕放不下时按钮不会被挤出窗口。
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=pad(12, 0))
        frame = ttk.Frame(body)
        frame.pack(fill="both", expand=True, pady=pad(10, 0))
        self.tree = ttk.Treeview(
            frame, columns=("checked", "category", "path"), show="headings", height=16,
            selectmode="extended",
        )
        self.tree.heading("checked", text="勾选")
        self.tree.heading("category", text="分类")
        self.tree.heading("path", text="脚本")
        self.tree.column("checked", width=60, anchor="center", stretch=False)
        self.tree.column("category", width=85, anchor="center", stretch=False)
        self.tree.column("path", width=px(470))
        self._reload_visible_scripts()
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._toggle_selected)
        self.tree.bind("<space>", self._toggle_selected)
        self.tree.bind("<Control-a>", self._select_all)
        ttk.Button(buttons, text="全选", command=self._select_all).pack(side="left")
        ttk.Button(buttons, text="全不选", command=self._clear_all).pack(side="left", padx=pad(8, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self._save).pack(side="right", padx=pad(0, 8))
        fit_window_to_content(self, parent)

    def _count_module_usage(self, module_key: str) -> dict[int, int]:
        """Per-script count of top-level actions referencing the module."""
        counts: dict[int, int] = {}
        for index, path in enumerate(self.script_paths):
            count = 0
            try:
                script = load_script(path)
                count = sum(
                    1 for action in script.actions
                    if str(action.get("module_key", "")).strip() == module_key
                )
            except Exception:
                pass
            counts[index] = count
        return counts

    def _path_display(self, index: int) -> str:
        text = display_path(self.script_paths[index])
        if self.mode != "remove":
            return text
        count = self.usage_counts.get(index, 0)
        return f"{text}（{count} 行）" if count else f"{text}（未使用）"

    def _set_checked(self, index: int, checked: bool):
        if checked:
            self.checked.add(index)
        else:
            self.checked.discard(index)
        if self.tree.exists(str(index)):
            category = SCRIPT_CATEGORY_LABELS.get(self.script_categories[index], "关卡")
            self.tree.item(
                str(index),
                values=("☑" if checked else "☐", category, self._path_display(index)),
            )

    def _visible_indices(self) -> list[int]:
        return [
            index for index, category in enumerate(self.script_categories)
            if self.current_filter == "all" or category == self.current_filter
        ]

    def _reload_visible_scripts(self):
        self.tree.delete(*self.tree.get_children())
        for index in self._visible_indices():
            category = SCRIPT_CATEGORY_LABELS.get(self.script_categories[index], "关卡")
            self.tree.insert(
                "", "end", iid=str(index),
                values=("☑" if index in self.checked else "☐", category, self._path_display(index)),
            )

    def _set_filter(self, category: str):
        if category not in SCRIPT_CATEGORY_LABELS:
            return
        self.current_filter = category
        for key, button in self.filter_buttons.items():
            selected = key == category
            button.configure(
                background=COLOR_BLUE_SELECTION if selected else COLOR_SURFACE,
                foreground="#FFFFFF" if selected else COLOR_TEXT,
            )
        self._reload_visible_scripts()

    def _toggle_indices(self, indices: list[int]):
        for index in indices:
            self._set_checked(index, index not in self.checked)

    def _on_click(self, event):
        if self.tree.identify_column(event.x) != "#1":
            return
        item = self.tree.identify_row(event.y)
        if item:
            self._toggle_indices([int(item)])
            return "break"

    def _toggle_selected(self, _event=None):
        indices = [int(item) for item in self.tree.selection()]
        if indices:
            self._toggle_indices(indices)
        return "break"

    def _select_all(self, _event=None):
        for index in self._visible_indices():
            self._set_checked(index, True)
        return "break"

    def _clear_all(self):
        for index in self._visible_indices():
            self._set_checked(index, False)

    def _save(self):
        if not self.checked:
            show_floating_notice(self, "尚未勾选", "请至少勾选一个脚本。")
            return
        self.result = [self.script_paths[index] for index in sorted(self.checked)]
        self.destroy()


class ModulePickerDialog(ModalDialog):
    """Pick a module object or special action to insert into a script.

    脚本编辑器显示切换 / 脚本全局 / 特殊；工作流入口只显示工作流全局。
    新建同步进仓库；特殊模块是无需图片的纯动作；nested 时隐藏特殊页签）。
    ``show()`` 返回要插入的脚本动作 dict；取消返回 ``None``。
    """

    @staticmethod
    def _allowed_categories(categories: tuple[str, ...] | None = None) -> tuple[str, ...]:
        allowed = categories or ("switch", "workflow_global", "script_global", "special")
        return tuple(
            category for category in allowed
            if category in ("switch", "workflow_global", "script_global", "special")
        ) or ("switch",)

    def __init__(self, parent, actions: list[dict] | None = None,
                 nested: bool = False, segment_depth: int = 0,
                 categories: tuple[str, ...] | None = None,
                 multi_select: bool = False, allow_number: bool | None = None,
                 selection_only: bool = False):
        # 附加代码段允许插入固定特殊模块（例如“重新执行工作流”）；nested
        # 仍用于限制模块代码段递归深度，不再隐藏特殊模块页签。
        self.allowed_categories = self._allowed_categories(categories)
        title = (
            "选择工作流全局模块"
            if self.allowed_categories == ("workflow_global",) else "插入模块"
        )
        super().__init__(parent, title, 560, 460)
        self.actions = actions or []
        self.nested = nested
        self.allow_number = (not nested) if allow_number is None else bool(allow_number)
        self.segment_depth = segment_depth
        self.multi_select = bool(multi_select)
        self.selection_only = bool(selection_only)
        self.objects: dict[str, dict] = load_module_objects()
        self.category_keys: dict[str, list[str]] = {
            "switch": [], "workflow_global": [], "script_global": [], "special": [],
        }
        self.listboxes: dict[str, tk.Listbox] = {}
        self.empty_labels: dict[str, ttk.Label] = {}
        self.tab_special: ttk.Frame | None = None
        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text=("可用 Ctrl 多选、Shift 连选、Ctrl+A 全选；点击“选择”批量添加。"
                  if self.multi_select
                  else "双击选择模块对象；这里只显示模块仓库中的工作流全局模块。"
                  if self.allowed_categories == ("workflow_global",)
                  else "双击选择模块；特殊模块为固定动作，插入后无需配置。"),
            foreground=COLOR_MUTED, wraplength=px(520),
        ).pack(anchor="w")
        # 按钮行先按 side="bottom" 占位，屏幕放不下时按钮不会被挤出窗口。
        buttons = ttk.Frame(body)
        buttons.pack(side="bottom", fill="x", pady=pad(12, 0))
        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True, pady=pad(10, 0))
        if "switch" in self.allowed_categories:
            tab_switch = ttk.Frame(notebook)
            notebook.add(tab_switch, text="切换模块")
            self._build_category_tab("switch", tab_switch)
        if "workflow_global" in self.allowed_categories:
            tab_global = ttk.Frame(notebook)
            notebook.add(tab_global, text="工作流全局模块")
            self._build_category_tab("workflow_global", tab_global)
        if "script_global" in self.allowed_categories:
            tab_script_global = ttk.Frame(notebook)
            notebook.add(tab_script_global, text="脚本全局模块")
            self._build_category_tab("script_global", tab_script_global)
        if "special" in self.allowed_categories:
            self.tab_special = ttk.Frame(notebook)
            notebook.add(self.tab_special, text="特殊模块")
            self._build_category_tab("special", self.tab_special)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        fit_window_to_content(self, parent)

    def _build_category_tab(self, category: str, tab: ttk.Frame):
        # 按钮行先占位（见上），列表自身带滚动条，列表再长也点得到。
        buttons = ttk.Frame(tab)
        buttons.pack(side="bottom", fill="x", padx=px(10), pady=pad(10, 10))
        list_frame = ttk.Frame(tab)
        list_frame.pack(fill="both", expand=True, padx=px(10), pady=pad(10, 0))
        listbox = tk.Listbox(
            list_frame, background=COLOR_SURFACE, foreground=COLOR_TEXT,
            selectbackground=COLOR_BLUE_SELECTION,
            font=(FONT_FAMILY, FONT_SUBTITLE), relief="flat", borderwidth=0,
            selectmode="extended" if self.multi_select else "browse",
            exportselection=False,
        )
        listbox.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scroll.set)
        listbox.bind("<Double-1>", lambda _event: self._choose_category(category))
        if self.multi_select:
            listbox.bind(
                "<Control-a>",
                lambda _event, key=category: self._select_all_category(key),
            )
        self.listboxes[category] = listbox
        empty_label = ttk.Label(
            tab,
            text=("暂无可用的固定特殊模块" if category == "special"
                  else "该分类还没有模块，点“新建模块…”创建"),
            foreground=COLOR_MUTED,
        )
        empty_label.pack(anchor="w", padx=px(10), pady=pad(6, 0))
        self.empty_labels[category] = empty_label
        ttk.Button(
            buttons, text="选择", command=lambda: self._choose_category(category),
        ).pack(side="left")
        if category != "special":
            ttk.Button(
                buttons, text="新建模块…", command=lambda: self._new_object(category),
            ).pack(side="left", padx=pad(8, 0))
        self._refresh_category(category)

    def _refresh_category(self, category: str):
        keys = [
            key for key, obj in self.objects.items()
            if obj.get("category") == category and obj.get("enabled", True)
            and (getattr(self, "allow_number", True) or obj.get("recognize") != "number")
        ]
        self.category_keys[category] = keys
        listbox = self.listboxes[category]
        listbox.delete(0, "end")
        for key in keys:
            obj = self.objects[key]
            label = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
            if obj.get("recognize") == "text":
                label += " · 识别文字"
                if obj.get("wait_text_absent"):
                    label += " · 持续执行至文字消失"
            elif obj.get("recognize") == "number":
                label += " · 读取数字"
            elif obj.get("wait_text_absent"):
                label += " · 持续执行至模板消失"
            listbox.insert("end", label)
        empty_label = self.empty_labels[category]
        if keys:
            empty_label.pack_forget()
        else:
            empty_label.pack(anchor="w", padx=px(10), pady=pad(6, 0))

    def _refresh_lists(self):
        self.objects = load_module_objects()
        for category in getattr(
                self, "allowed_categories", ("switch", "script_global", "special")):
            self._refresh_category(category)

    def _choose_category(self, category: str):
        selection = self.listboxes[category].curselection()
        if not selection:
            show_floating_notice(self, "请先选择模块", "先选中一个模块再点“选择”。")
            return
        keys = self.category_keys[category]
        selected_keys = [keys[index] for index in selection if index < len(keys)]
        if not selected_keys:
            return
        if getattr(self, "multi_select", False):
            self.result = [self._action_for_key(key, category) for key in selected_keys]
            self.destroy()
            return
        self._choose_key(selected_keys[0], category)

    def _select_all_category(self, category: str):
        listbox = self.listboxes[category]
        listbox.selection_set(0, "end")
        return "break"

    def _choose_key(self, key: str, category: str):
        obj = getattr(self, "objects", {}).get(key)
        if (
            obj and obj.get("recognize") == "number"
            and not getattr(self, "allow_number", True)
        ):
            show_floating_notice(
                self, "此处不能插入",
                "读取数字需要脚本行提供比较数字和两路跳转，只能在脚本编辑器中插入。",
            )
            return
        action = self._action_for_key(key, category)
        self.result = [action] if getattr(self, "multi_select", False) else action
        self.destroy()

    def _action_for_key(self, key: str, category: str) -> dict:
        objects = getattr(self, "objects", {})
        if getattr(self, "selection_only", False):
            return module_reference_binding(key, objects.get(key))
        return module_action_for_key(key, category, objects.get(key))

    def _new_object(self, category: str):
        if category == "special":
            return
        form = TemplateRegionFormDialog(
            self, category=category, segment_depth=self.segment_depth + 1,
        )
        result = form.show()
        if result is None:
            return
        old_key, key, obj = result
        update_module_object(key, obj, old_key=old_key)
        self._refresh_lists()
        self._choose_key(key, category)


class ModuleReferenceDelayDialog(FailureSegmentMixin, ModalDialog):
    """Replace a module reference or edit its per-reference result branches."""

    def __init__(self, parent, action: dict, actions: list[dict] | None = None):
        result_routes = action.get("type") == "image_match"
        key = str(action.get("module_key") or action.get("template", ""))
        obj = registered_module_object(key)
        number_routes = bool(result_routes and obj and obj.get("recognize") == "number")
        self.blocking_module = bool(obj and obj.get("blocking", False))
        super().__init__(
            parent, "编辑数字读取" if number_routes else "编辑模块引用",
            680, 640 if number_routes else 570 if result_routes else 330,
        )
        self.action = dict(action)
        self.result_routes_enabled = result_routes
        self.number_routes_enabled = number_routes
        self.delay = duration_var(action.get("delay_ms", 0))
        self.after_delay = duration_var(action.get("after_delay_ms", 0))
        self.blocking_timeout_enabled_var = tk.BooleanVar(
            value=bool(action.get("blocking_timeout_enabled", False)),
        )
        self.blocking_timeout_var = duration_var(
            action.get("blocking_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS),
        )
        self.jump_options = image_jump_target_options(actions or [])
        self.jump_target_ids = dict(self.jump_options)
        self.on_success = tk.StringVar(
            value=module_result_option_label(str(action.get("on_found", "continue"))),
        )
        self.on_failure = tk.StringVar(
            value=module_result_option_label(str(action.get("on_timeout", "continue"))),
        )
        self.success_target = tk.StringVar(value=select_jump_target_label(
            str(action.get("found_jump_action_id", "")).strip(),
            max(1, int(action.get("found_jump_row", 1))), self.jump_options,
        ))
        self.failure_target = tk.StringVar(value=select_jump_target_label(
            str(action.get("timeout_jump_action_id", "")).strip(),
            max(1, int(action.get("timeout_jump_row", 1))), self.jump_options,
        ))
        self.expected_number = tk.StringVar(
            value="" if action.get("expected_number") is None
            else str(action.get("expected_number")),
        )
        # 脚本行级“失败后执行代码段”：与模块对象里的超时代码段同一套编辑器。
        self._init_failure_segment(action)
        name = str(obj.get("name") or Path(key.replace("\\", "/")).stem) if obj else Path(key.replace("\\", "/")).stem
        self.module_name = tk.StringVar(value=name or "未设置")

        body, self._form_canvas, form_scrollbar = scrollable_dialog_body(self, padding=14)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="引用模块").grid(row=0, column=0, sticky="w", pady=px(8))
        module_row = ttk.Frame(body)
        module_row.grid(row=0, column=1, sticky="ew", pady=px(8))
        module_row.columnconfigure(0, weight=1)
        ttk.Label(
            module_row, textvariable=self.module_name, foreground=COLOR_MUTED,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            module_row, text="替换模块…", command=self.replace_reference,
        ).grid(row=0, column=1, padx=pad(8, 0))
        for row, (label, variable) in enumerate((
            ("进入模块前延时", self.delay),
            ("模块完成后延时", self.after_delay),
        ), start=1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=px(8))
            ttk.Spinbox(
                body, from_=0, to=86400000, increment=100,
                textvariable=variable, width=12,
            ).grid(row=row, column=1, sticky="ew", pady=px(8))
        next_row = 3
        if self.blocking_module:
            ttk.Label(body, text="阻塞超时后跳过").grid(
                row=next_row, column=0, sticky="w", pady=px(8),
            )
            blocking_timeout_row = ttk.Frame(body)
            blocking_timeout_row.grid(row=next_row, column=1, sticky="ew", pady=px(8))
            ttk.Checkbutton(
                blocking_timeout_row, text="启用",
                variable=self.blocking_timeout_enabled_var,
            ).pack(side="left")
            ttk.Spinbox(
                blocking_timeout_row, from_=0, to=86400000, increment=100,
                textvariable=self.blocking_timeout_var, width=12,
            ).pack(side="left", padx=pad(10, 0))
            next_row += 1
        if number_routes:
            ttk.Label(body, text="比较数字").grid(
                row=next_row, column=0, sticky="w", pady=px(8),
            )
            ttk.Entry(body, textvariable=self.expected_number).grid(
                row=next_row, column=1, sticky="ew", pady=px(8),
            )
            next_row += 1
        if result_routes:
            option_labels = tuple(label for label, _value in MODULE_RESULT_OPTIONS)
            target_labels = tuple(label for label, _action_id in self.jump_options)
            ttk.Label(body, text="数字等于时" if number_routes else "模块成功后").grid(
                row=next_row, column=0, sticky="w", pady=px(8),
            )
            ttk.Combobox(
                body, textvariable=self.on_success, values=option_labels,
                state="readonly",
            ).grid(row=next_row, column=1, sticky="ew", pady=px(8))
            next_row += 1
            ttk.Label(body, text="等于后跳转到" if number_routes else "成功跳转到").grid(
                row=next_row, column=0, sticky="w", pady=px(8),
            )
            self.success_target_combo = ttk.Combobox(
                body, textvariable=self.success_target, values=target_labels,
                state="disabled",
            )
            self.success_target_combo.grid(row=next_row, column=1, sticky="ew", pady=px(8))
            next_row += 1
            ttk.Label(
                body, text="数字不等于或未读取到时" if number_routes else "模块失败后",
            ).grid(
                row=next_row, column=0, sticky="w", pady=px(8),
            )
            ttk.Combobox(
                body, textvariable=self.on_failure, values=option_labels,
                state="readonly",
            ).grid(row=next_row, column=1, sticky="ew", pady=px(8))
            next_row += 1
            ttk.Label(body, text="不等于后跳转到" if number_routes else "失败跳转到").grid(
                row=next_row, column=0, sticky="w", pady=px(8),
            )
            self.failure_target_combo = ttk.Combobox(
                body, textvariable=self.failure_target, values=target_labels,
                state="disabled",
            )
            self.failure_target_combo.grid(row=next_row, column=1, sticky="ew", pady=px(8))
            next_row += 1
            self.on_success.trace_add("write", self._update_result_target_states)
            self.on_failure.trace_add("write", self._update_result_target_states)
            self._update_result_target_states()
        if result_routes:
            next_row = self._build_failure_segment_controls(body, row=next_row)
        ttk.Label(
            body,
            text=(
                "读取到数字后立即比较；等于走成功分支，不等于走失败分支。未读取到数字会按模块的阻塞和未识别时限重试，超时后先执行失败代码段，再走失败分支。"
                if number_routes else
                "结果分支与失败代码段只属于当前脚本行；阻塞模块还可在此单独开启“阻塞超时后跳过”。识别方式、区域、相似度、阻塞、未识别时限、点击和模块级代码段仍在“模块管理…”统一设置。"
                if result_routes else
                "此处只设置当前引用的进入/完成延时；检测和触发行为统一到“模块管理…”修改。"
            ),
            foreground=COLOR_MUTED, wraplength=px(610),
        ).grid(row=next_row, column=0, columnspan=2, sticky="w", pady=pad(10, 0))
        next_row += 1
        buttons = ttk.Frame(body)
        buttons.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=pad(18, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="确定", command=self.save).pack(side="right", padx=pad(0, 8))
        fit_scrollable_window_to_content(
            self, parent, body, form_scrollbar, align_top=True,
        )

    def _update_result_target_states(self, *_args):
        if not self.result_routes_enabled:
            return
        self.success_target_combo.configure(
            state="readonly"
            if module_result_option_value(self.on_success.get()) == "jump" else "disabled",
        )
        self.failure_target_combo.configure(
            state="readonly"
            if module_result_option_value(self.on_failure.get()) == "jump" else "disabled",
        )

    def replace_reference(self):
        category = str(self.action.get("module_category", "switch"))
        if category == "global":
            category = "script_global"
        if category not in ("switch", "script_global"):
            category = "switch"
        replacement = ModulePickerDialog(
            self, categories=(category,),
        ).show()
        if not isinstance(replacement, dict):
            return
        replacement_key = str(
            replacement.get("module_key") or replacement.get("template", "")
        )
        replacement_obj = registered_module_object(replacement_key)
        replacement_is_number = bool(
            replacement_obj and replacement_obj.get("recognize") == "number"
        )
        if replacement_is_number != bool(getattr(self, "number_routes_enabled", False)):
            show_floating_notice(
                self, "模块类型不同",
                "读取数字模块和普通模块的行级设置不同，请删除当前行后重新插入。",
            )
            return
        preserved = {
            key: self.action[key]
            for key in (
                ACTION_ID_KEY, "delay_ms", "after_delay_ms",
                "jump_row", "jump_action_id",
                "on_found", "found_jump_action_id",
                "on_timeout", "timeout_jump_action_id",
                "expected_number",
                "blocking_timeout_enabled", "blocking_timeout_ms",
            )
            if key in self.action
        }
        self.action = dict(replacement)
        self.action.update(preserved)
        key = str(self.action.get("module_key") or self.action.get("template", ""))
        obj = registered_module_object(key)
        name = (
            str(obj.get("name") or Path(key.replace("\\", "/")).stem)
            if obj else Path(key.replace("\\", "/")).stem
        )
        self.module_name.set(name or "未设置")

    def save(self):
        try:
            delay = max(0, min(86400000, int(self.delay.get())))
            after_delay = max(0, min(86400000, int(self.after_delay.get())))
        except ValueError:
            show_floating_notice(self, "参数错误", "执行前延时和执行后延时必须是整数。")
            return
        result = dict(self.action)
        result["delay_ms"] = delay
        result["after_delay_ms"] = after_delay
        blocking_module = bool(
            getattr(self, "blocking_module", self.action.get("blocking", False)),
        )
        timeout_enabled_var = getattr(self, "blocking_timeout_enabled_var", None)
        timeout_var = getattr(self, "blocking_timeout_var", None)
        blocking_timeout_enabled = bool(
            blocking_module and (
                timeout_enabled_var.get()
                if timeout_enabled_var is not None
                else self.action.get("blocking_timeout_enabled", False)
            )
        )
        blocking_timeout_ms = DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS
        if timeout_var is not None:
            try:
                blocking_timeout_ms = max(0, int(timeout_var.get()))
            except (TypeError, ValueError):
                if blocking_timeout_enabled:
                    show_floating_notice(
                        self, "阻塞超时格式错误",
                        "阻塞超时必须是大于等于 0 的整数（毫秒）。",
                    )
                    return
        result["blocking_timeout_enabled"] = blocking_timeout_enabled
        result["blocking_timeout_ms"] = blocking_timeout_ms
        if getattr(self, "result_routes_enabled", False):
            self._failure_segment_fields(result)
        else:
            result["failure_segment_enabled"] = False
            result["failure_actions"] = []
        if bool(getattr(self, "number_routes_enabled", False)):
            try:
                expected_number = int(self.expected_number.get())
            except (TypeError, ValueError):
                show_floating_notice(self, "比较数字无效", "比较数字必须是大于等于 0 的整数。")
                return
            if expected_number < 0:
                show_floating_notice(self, "比较数字无效", "比较数字必须是大于等于 0 的整数。")
                return
            result["expected_number"] = expected_number
        else:
            result.pop("expected_number", None)
        if self.result_routes_enabled:
            success = module_result_option_value(self.on_success.get())
            failure = module_result_option_value(self.on_failure.get())
            success_target_id = self.jump_target_ids.get(self.success_target.get(), "")
            failure_target_id = self.jump_target_ids.get(self.failure_target.get(), "")
            if success == "jump" and not success_target_id:
                show_floating_notice(self, "缺少目标", "请选择模块成功后要跳转的行对象。")
                return
            if failure == "jump" and not failure_target_id:
                show_floating_notice(self, "缺少目标", "请选择模块失败后要跳转的行对象。")
                return
            result["on_found"] = success
            result["found_jump_action_id"] = success_target_id
            result["on_timeout"] = failure
            result["timeout_jump_action_id"] = failure_target_id
        self.result = result
        self.destroy()
