from __future__ import annotations

from macroflow.core.models import (
    ACTION_ID_KEY, END_CURRENT_SCRIPT_LABEL, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID, SCROLL_DOWN_LABEL, SCROLL_UP_LABEL,
    ensure_action_ids, recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
    special_action_label,
)
from pathlib import Path
import copy
from pypinyin import lazy_pinyin
from macroflow.core.storage import (
    BASE_DIR, DIRECTION_SCRIPTS_DIR, IMAGES_DIR, SCRIPTS_DIR, display_path,
    DEFAULT_MODULE_INTERVAL_MS, DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    DEFAULT_MODULE_TRIGGER_COOLDOWN_MS,
    load_app_settings, load_module_images_dir, load_module_objects,
    load_script, load_template_regions,
    module_image_inventory, module_objects_by_category,
    registered_module_object, resolve_path, save_module_images_dir, save_module_objects,
    save_template_regions, save_script, script_category_for_path, update_module_object,
)
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from .base import (
    COLOR_BG,
    COLOR_BLUE_SELECTION,
    COLOR_BORDER,
    COLOR_MUTED,
    COLOR_SURFACE,
    COLOR_SURFACE_ALT,
    COLOR_TEXT,
    FONT_FAMILY,
    FONT_SUBTITLE,
    IMAGE_TIMEOUT_OPTIONS,
    MODULE_RESULT_OPTIONS,
    RESTART_USE_DEFAULT_ROW_LABEL,
    px,
)


def parse_named_region(value) -> list[int]:
    """Parse either named x/y/w/h values or the persisted comma form."""
    if isinstance(value, dict):
        values = [value.get(name, "") for name in ("x", "y", "w", "h")]
    elif isinstance(value, str):
        values = value.split(",")
    else:
        values = value or []
    if len(values) != 4:
        raise ValueError("区域必须包含 x、y、w、h 四个数字")
    try:
        result = [int(str(part).strip()) for part in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("区域必须包含 x、y、w、h 四个数字") from exc
    if result[2] <= 0 or result[3] <= 0:
        raise ValueError("区域宽高必须大于 0")
    return result


def condition_field_visibility(kind: str) -> set[str]:
    return {
        "image": {"module"},
        "text": {"text", "match"},
        "number": {"separator", "relation"},
    }.get(str(kind), set())


def pinyin_sort_key(value: str) -> tuple[str, str]:
    """Stable Chinese-name sort key using pypinyin, then original text."""
    text = str(value).strip()
    return ("".join(lazy_pinyin(text)).casefold(), text.casefold())


def image_action_option_defaults(action: dict) -> tuple[str, bool]:
    """Return new-action defaults while preserving explicitly saved values."""
    return (
        str(action.get("on_found", "click")),
        bool(action.get("show_result_notice", True)),
    )


def _option_value(value: str, options, default: str) -> str:
    """把界面显示值或存储值换算成存储值；未知值回退 default。"""
    return next(
        (stored for label, stored in options if value in {label, stored}),
        default,
    )


def _option_label(value: str, options, default: str) -> str:
    """把存储值换算成界面显示值；未知值回退 default。"""
    return next((label for label, stored in options if stored == value), default)


def module_result_option_label(value: str) -> str:
    return _option_label(value, MODULE_RESULT_OPTIONS, "继续下一行")


def module_result_option_value(value: str) -> str:
    return _option_value(value, MODULE_RESULT_OPTIONS, "continue")


def image_timeout_option_label(value: str) -> str:
    return _option_label(value, IMAGE_TIMEOUT_OPTIONS, "继续执行")


def image_timeout_option_value(value: str) -> str:
    return _option_value(value, IMAGE_TIMEOUT_OPTIONS, "continue")


def image_timeout_option_defaults(action: dict) -> tuple[str, int, int, int, int]:
    """Return timeout behavior, timeout, pre-delay, jump row and post-timeout delay."""
    behavior = str(action.get("on_timeout", "continue"))
    if behavior not in {stored for _label, stored in IMAGE_TIMEOUT_OPTIONS}:
        behavior = "continue"
    return (
        behavior,
        max(0, int(action.get("timeout_ms", 3000))),
        max(0, int(action.get("delay_ms", 1000))),
        max(1, int(action.get("timeout_jump_row", 1))),
        max(0, int(action.get("timeout_delay_ms", 0))),
    )


def image_jump_target_options(actions: list[dict]) -> list[tuple[str, str]]:
    """Return current row labels paired with stable action identities.

    Labels include a concrete, type-specific detail (coordinates, key name,
    template or script filename, delay, …) so the user can tell which row
    a jump target refers to without opening the script.
    """
    kind_labels = {
        "delay": "延时", "rebind_window": "重新绑定目标窗口",
        "key": "键盘", "key_press": "键盘", "text": "文本",
        "mouse_move": "鼠标移动", "mouse_button": "鼠标按键", "click": "点击",
        "repeat_click": "连续点击",
        "scroll": "滚轮", "image_match": "识图", "text_ocr": "识别文字",
        "ocr_compare": "数字比较", "multi_condition_click": "多条件识图",
        "row_list_condition_click": "列表逐行点击",
        "notice": "浮动提醒", "comment": "注释",
        "script_ref": "引用脚本", "open_app": "打开软件",
        "close_app": "关闭软件", "jump": "跳转",
        "global_detect": "全局检测", "restart_workflow": "重启工作流",
        "end_current_script": "结束脚本", "jump_current_script_last": "跳转脚本尾",
        "block": "阻塞",
        "activate_window": "前置窗口", "module_ref": "模块引用",
    }
    button_names = {"left": "左键", "right": "右键", "middle": "中键"}
    clip = lambda text, limit=20: (  # noqa: E731
        s if len(s := str(text).replace("\n", " ").strip()) <= limit
        else s[:limit] + "…"
    )

    def point(action: dict) -> str:
        raw = action.get("click_point")
        if isinstance(raw, (list, tuple)) and len(raw) >= 2:
            try:
                return f"({int(raw[0])},{int(raw[1])})"
            except (TypeError, ValueError):
                pass
        try:
            return f"({int(action.get('x', 0))},{int(action.get('y', 0))})"
        except (TypeError, ValueError):
            return ""

    options: list[tuple[str, str]] = []
    for index, action in enumerate(actions):
        action_id = str(action.get("action_id", "")).strip()
        if not action_id:
            continue
        kind = str(action.get("type", "动作"))
        row_kind_label = kind_labels.get(kind, kind)
        button = button_names.get(str(action.get("button", "left")), str(action.get("button", "left")))
        detail = ""
        if kind == "delay":
            detail = f"等 {int(action.get('ms', 0))} ms"
        elif kind in {"key", "key_press"}:
            key_name = str(action.get("name") or action.get("vk") or "未知")
            detail = ("按下 " if action.get("down") else "松开 ") + key_name if kind == "key" else f"敲击 {key_name}"
        elif kind == "text":
            detail = "输入 " + clip(action.get("text", ""), 16)
        elif kind == "mouse_move":
            if action.get("mode") == "relative":
                detail = f"ΔX {int(action.get('dx', 0))},ΔY {int(action.get('dy', 0))}"
            else:
                detail = f"移动到 {point(action)}"
        elif kind == "mouse_button":
            state = "按下" if action.get("down") else "松开"
            detail = f"{state} {button} {point(action)}"
        elif kind in {"click", "repeat_click"}:
            count = int(action.get("count", 2)) if kind == "repeat_click" else 1
            times = f" ×{count}" if count != 1 else ""
            if kind == "click" and action.get("pos_mode") == "current":
                detail = f"{button} 当前位置"
            else:
                detail = f"{button}{times} {point(action)}"
        elif kind == "scroll":
            detail = (
                f"{scroll_direction_label(action.get('dy', 0))} "
                f"{scroll_clicks(action.get('dy', 0))} 格 {point(action)}"
            )
        elif kind == "image_match":
            if action.get("module_ref"):
                key = str(action.get("module_key") or action.get("template", ""))
                obj = registered_module_object(key)
                name = str(
                    obj.get("name") or Path(key.replace("\\", "/")).stem
                ) if obj else Path(key.replace("\\", "/")).stem
                if obj and obj.get("recognize") == "number":
                    row_kind_label = "读取数字"
                    expected = action.get("expected_number")
                    detail = f"模块 {clip(name, 14)} · 比较 {expected if expected is not None else '未设置'}"
                else:
                    detail = "模块 " + clip(name, 16)
            else:
                detail = clip(Path(str(action.get("template", ""))).name, 16)
        elif kind == "text_ocr":
            detail = clip(action.get("expected_text", ""), 16) or "任意文字"
        elif kind == "ocr_compare":
            separator = str(action.get("separator", "/"))
            detail = f"数字{separator}数字 · 相等:{action.get('equal_action', 'continue')} · 不相等:{action.get('not_equal_action', 'continue')}"
        elif kind == "multi_condition_click":
            enabled = [
                str(condition.get("type", ""))
                for condition in action.get("conditions", [])
                if isinstance(condition, dict) and condition.get("enabled")
            ]
            detail = f"启用 {len(enabled)}/3 个条件 · 点击 {int(action.get('click_count', 1))} 次"
        elif kind == "row_list_condition_click":
            detail = "从上到下查找首个匹配项"
        elif kind == "notice":
            detail = clip(action.get("text", ""), 16)
        elif kind == "comment":
            detail = clip(action.get("text", ""), 16)
        elif kind == "script_ref":
            detail = "执行 " + clip(Path(str(action.get("script", ""))).name or "未设置", 16)
        elif kind == "open_app":
            name = clip(Path(str(action.get("path", ""))).name or "未设置", 16)
            args = str(action.get("args", "")).strip()
            detail = f"启动 {name}" + (f"（{args}）" if args else "")
        elif kind == "close_app":
            detail = "结束 " + clip(action.get("name", "") or "未设置", 16)
        elif kind == "jump":
            jump_id = str(action.get("jump_action_id", "")).strip()
            if jump_id == SCRIPT_START_TARGET_ID:
                detail = "跳到脚本开头"
            elif jump_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                detail = "跳到脚本结尾"
            else:
                detail = f"跳到第 {max(1, int(action.get('jump_row', 1)))} 行"
        elif kind == "activate_window":
            detail = clip(action.get("title") or action.get("name", ""), 16)
        elif kind == "block":
            detail = "等待其他跳转"
        label = f"第 {index + 1} 行 · {row_kind_label}"
        if detail:
            label += f" · {detail}"
        options.append((label, action_id))
    return options


def image_found_jump_target_options(actions: list[dict]) -> list[tuple[str, str]]:
    """Successful recognition can jump to an action or finish this script execution."""
    return [
        ("结束当前脚本执行", NEXT_WORKFLOW_STEP_TARGET_ID),
        *image_jump_target_options(actions),
    ]


def select_jump_target_label(saved_action_id: str, saved_row: int,
                             jump_options: list[tuple[str, str]]) -> str:
    """Pick the row-object label for a saved jump target.

    Stable action id wins (rows may have moved since the target was saved),
    then the saved row number, then the first row as a safe default.
    """
    target = next(
        (label for label, action_id in jump_options if action_id == saved_action_id), "",
    )
    if target:
        return target
    if 1 <= saved_row <= len(jump_options):
        return jump_options[saved_row - 1][0]
    return jump_options[0][0] if jump_options else ""


def image_click_target_defaults(action: dict) -> tuple[str, list[int]]:
    mode = str(action.get("click_target", "match"))
    label = "自定义坐标" if mode == "custom" else "识图区域中心"
    raw = action.get("click_point", [0, 0])
    try:
        point = [int(raw[0]), int(raw[1])] if len(raw) >= 2 else [0, 0]
    except (TypeError, ValueError):
        point = [0, 0]
    return label, point


def registered_template_options(current: str = "") -> list[str]:
    """注册表里的模板列表（图片 display path）；编辑旧动作时把旧值临时加进来保证显示。"""
    options = sorted(load_template_regions().keys())
    if current and current not in options:
        options = [current] + options
    return options


def fallback_template_options(current: str = "") -> list[str]:
    """备用模板下拉选项：第一项"（不启用）"，其余为注册表模板。"""
    options = ["（不启用）"]
    options.extend(registered_template_options(current))
    return options


def segment_action_is_blocking(action: dict) -> bool:
    """Return whether a segment row can wait indefinitely for recognition."""
    if action.get("type") == "block":
        return True
    if action.get("type") not in ("image_match", "global_detect"):
        return False
    if action.get("module_ref"):
        key = str(action.get("module_key") or action.get("template", "")).strip()
        obj = registered_module_object(key) if key else None
        return bool(obj and (
            (obj.get("blocking") and not action.get("blocking_timeout_enabled", False))
            or obj.get("wait_text_absent")
        ))
    return bool(action.get("blocking"))


def workflow_step_label(step: dict) -> str:
    """工作流树里一行对象的显示名（与 app._workflow_step_name 保持一致）。"""
    if step.get("kind") != "module":
        raw = str(step.get("script", ""))
        return Path(raw.replace("\\", "/")).stem or raw or "未设置脚本"
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    special_type = str(action.get("type", ""))
    if special_type == "restart_workflow":
        return "重新执行工作流"
    if special_type == "end_current_script":
        return END_CURRENT_SCRIPT_LABEL
    if special_type == "jump_current_script_last":
        return "跳转到当前脚本最后一行"
    module_key = str(action.get("module_key") or action.get("template") or "").strip()
    module_obj = registered_module_object(module_key)
    name = (
        str(action.get("module_name", "")).strip()
        or (str(module_obj.get("name", "")).strip() if module_obj else "")
        or Path(module_key.replace("\\", "/")).stem
    )
    return f"模块 {name or '未设置'}"


def restart_workflow_row_options(workflow_steps: list[dict], default_row: int = 0,
                                 default_label: str = RESTART_USE_DEFAULT_ROW_LABEL,
                                 ) -> tuple[list[str], dict[str, int]]:
    """构建「重新执行工作流」跳转行下拉选项（第 N 行 · 名称，行号 1 基）。

    返回 (labels, label→row 映射)；row 0 表示使用默认跳转行。default_row 非 0
    时在默认项里标注当前默认行号，方便用户知道不选时跳到哪里。
    """
    default_row = max(0, int(default_row or 0))
    first_label = f"（使用默认跳转行：第 {default_row} 行）" if default_row else default_label
    mapping = {first_label: 0}
    for index, step in enumerate(workflow_steps):
        label = f"第 {index + 1} 行 · {workflow_step_label(step)}"
        mapping[label] = index + 1
    return list(mapping), mapping


def _app_via_parent(parent):
    """沿父窗口链找主应用实例（找不到返回 None）。

    主应用只把 ``_macroflow_app`` 挂在根窗口上，表单、录制这类子窗口要靠
    父链上溯才能拿到它（读取录制设置、已绑定窗口都需要）。
    """
    window = parent
    seen = set()
    while window is not None and len(seen) < 20:
        identity = id(window)
        if identity in seen:
            break
        seen.add(identity)
        app = getattr(window, "_macroflow_app", None)
        if app is not None:
            return app
        try:
            window = window.master
        except (AttributeError, tk.TclError):
            break
    return None


def _app_workflow_steps(parent) -> list[dict]:
    """沿父窗口链找主应用，取当前工作流的行对象列表（不含全局模块行）。"""
    window = parent
    seen = set()
    while window is not None and len(seen) < 20:
        identity = id(window)
        if identity in seen:
            break
        seen.add(identity)
        app = getattr(window, "_macroflow_app", None)
        workflow = getattr(app, "workflow", None)
        if workflow is not None:
            steps = getattr(workflow, "steps", None)
            if isinstance(steps, list):
                return [step for step in steps if step.get("kind") != "global_module"]
        try:
            window = window.master
        except (AttributeError, tk.TclError):
            break
    return []


def _app_workflow_default_row(parent) -> int:
    """取主应用当前工作流统一设置的「重新执行工作流」默认跳转行（0 = 未设置）。"""
    window = parent
    seen = set()
    while window is not None and len(seen) < 20:
        identity = id(window)
        if identity in seen:
            break
        seen.add(identity)
        app = getattr(window, "_macroflow_app", None)
        workflow = getattr(app, "workflow", None)
        if workflow is not None:
            try:
                return max(0, int(getattr(workflow, "restart_default_row", 0) or 0))
            except (TypeError, ValueError):
                return 0
        try:
            window = window.master
        except (AttributeError, tk.TclError):
            break
    return 0


def segment_row_label(action: dict) -> str:
    """代码段列表里一条动作的摘要文本（仅展示用）。"""
    kind = action.get("type", "")
    if kind == "delay":
        return f"延时 {action.get('ms', 0)} ms"
    if kind == "rebind_window":
        return "重新绑定目标窗口"
    if kind in ("key", "key_press"):
        return f"按键 {action.get('key', action.get('name', '?'))}"
    if kind == "text":
        return "输入文本"
    if kind in ("click", "mouse_button"):
        return f"点击（{action.get('button', 'left')}）"
    if kind == "turn":
        return (
            f"转向 ΔX={action.get('dx', 0)}，ΔY={action.get('dy', 0)}"
        )
    if kind == "repeat_click":
        return f"重复点击 {action.get('count', 1)} 次"
    if kind == "mouse_move":
        return "移动鼠标"
    if kind == "scroll":
        return (
            f"滚轮 {scroll_direction_label(action.get('dy', 0))} "
            f"{scroll_clicks(action.get('dy', 0))} 格"
        )
    if kind in ("image_match", "global_detect"):
        template = str(action.get("template", "")).strip()
        name = ""
        if action.get("module_ref"):
            key = str(action.get("module_key") or template).strip()
            obj = registered_module_object(key) if key else None
            name = str((obj or {}).get("name") or "").strip()
            template = str((obj or {}).get("template") or template).strip()
        if not name:
            name = "未找到模块" if template.startswith("module:") else Path(template).stem
        label = f"{'识图' if kind == 'image_match' else '全局检测'} {name}"
        return f"【阻塞等待】{label}" if segment_action_is_blocking(action) else label
    if kind == "script_ref":
        repeats = script_ref_repeat_count(action)
        return f"引用脚本 {Path(str(action.get('script', ''))).stem} · 执行 {repeats} 次"
    if kind == "open_app":
        return f"打开软件 {Path(str(action.get('path', ''))).stem or '?'}"
    if kind == "close_app":
        return f"关闭软件 {action.get('name', '?')}"
    if kind == "notice":
        return f"提醒 {action.get('text', '')}"
    if kind == "restart_workflow":
        try:
            row = max(0, int(action.get("restart_workflow_target_row", 0) or 0))
        except (TypeError, ValueError):
            row = 0
        return "重新执行工作流" + (f"（跳转第 {row} 行）" if row else "（默认跳转行）")
    if kind == "end_current_script":
        return END_CURRENT_SCRIPT_LABEL
    if kind == "jump_current_script_last":
        return "跳转到当前脚本最后一行"
    if kind == "block":
        return "【阻塞等待跳转】阻塞"
    if kind == "jump":
        return "跳转"
    if kind == "activate_window":
        signature = action.get("window") or {}
        return f"前置窗口 {signature.get('title', '未设置')}"
    if kind == "comment":
        return f"注释 {action.get('text', '')}"
    return f"动作 {kind}"


def recorded_action_description(action: dict) -> str:
    """一条录制原始输入（键/鼠）的短描述：录制面板与代码段录制共用。"""
    kind = action.get("type", "unknown")
    if kind == "mouse_move":
        if action.get("mode") == "relative":
            return f"游戏转向：ΔX={action.get('dx', 0)}，ΔY={action.get('dy', 0)}（相对轨迹）"
        return f"鼠标移动到：({action.get('x', 0)}, {action.get('y', 0)})（桌面坐标）"
    if kind == "mouse_button":
        button_names = {"left": "左键", "right": "右键", "middle": "中键"}
        button = button_names.get(str(action.get("button", "left")), str(action.get("button", "left")))
        state = "按下" if action.get("down") else "松开"
        return f"{button}{state}：({action.get('x', 0)}, {action.get('y', 0)})"
    if kind == "scroll":
        return (f"滚轮：横向={action.get('dx', 0)}，纵向={action.get('dy', 0)}，"
                f"位置=({action.get('x', 0)}, {action.get('y', 0)})")
    if kind == "key":
        state = "按下" if action.get("down") else "松开"
        return f"键盘{state}：{action.get('name', action.get('vk', '未知'))}"
    if kind == "key_press":
        return f"敲击按键：{action.get('name', action.get('vk', '未知'))}"
    return segment_row_label(action)


def recorded_input_row_label(action: dict) -> str:
    """折叠的「录制动作」里一步的摘要（预览、编辑列表共用）。"""
    return recorded_action_description(action)


def module_manager_label(key: str, obj: dict) -> str:
    """Return the module-manager name with explicit risk/state markers."""
    name = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
    if not obj.get("enabled", True):
        name = f"【已禁用】{name}"
    if (
        obj.get("blocking")
        or obj.get("wait_text_absent")
    ) and not obj.get("pure_action"):
        name = f"【阻塞识别】{name}"
    if module_manager_special_action_summary(obj):
        name = f"【特殊代码段】{name}"
    return name


def module_manager_special_action_summary(obj: dict) -> str:
    """List fixed special actions in enabled success/timeout code segments.

    The source is included because the same special action has very different
    operational meaning when it runs after a match versus after a timeout.
    """
    parts: list[str] = []
    for source, field, enabled in (
        (
            "附加", "on_success_actions",
            bool(obj.get("run_code_after_action", False))
            or obj.get("after_action") == "run_actions",
        ),
        ("超时", "on_timeout_actions", bool(obj.get("run_code_on_timeout", False))),
    ):
        if not enabled:
            continue
        names: list[str] = []
        for action in obj.get(field) or []:
            if not isinstance(action, dict):
                continue
            name = special_action_label(str(action.get("type", "")))
            if name and name not in names:
                names.append(name)
        if names:
            parts.append(f"{source}：{'、'.join(names)}")
    return "；".join(parts)


def module_manager_tag(obj: dict) -> str:
    """Disabled > special code segment > blocking visual priority."""
    if not obj.get("enabled", True):
        return "disabled"
    if module_manager_special_action_summary(obj):
        return "special_action"
    blocking = obj.get("blocking") or obj.get("wait_text_absent")
    return "blocking" if blocking and not obj.get("pure_action") else ""


def module_manager_selection_colors(obj: dict | None) -> tuple[str, str]:
    """Return foreground/background for the selected module's enabled state."""
    if obj is None:
        return "#FFFFFF", COLOR_BLUE_SELECTION
    if not obj.get("enabled", True):
        return "#FFFFFF", "#7A3434"
    if module_manager_special_action_summary(obj):
        return "#FFFFFF", "#713C78"
    return "#FFFFFF", "#1F6B45"


def configure_module_list_scrollbar(tree, scrollbar) -> None:
    """模块列表的纵向滚动条：内容超出一屏才出现，并且明显可见。

    两个问题一起解决：内容装得下时滚动条仍占着一条空槽；ttkbootstrap 的
    darkly 主题里滚动条凹槽与列表底色几乎一致，内容确实超出一屏时滚动条也
    看不出来（用户报告「全部模块没有滚动条」）。凹槽/滑块改成与列表底有明显
    对比的颜色，并按 tree 的可见范围自动显隐。
    """
    style = ttk.Style(tree)
    style.configure(
        "ModuleList.Vertical.TScrollbar",
        background=COLOR_SURFACE_ALT, troughcolor=COLOR_BG,
        bordercolor=COLOR_BORDER, darkcolor=COLOR_SURFACE_ALT,
        lightcolor=COLOR_SURFACE_ALT, arrowcolor=COLOR_MUTED,
        width=px(14),
    )
    # 明确指定样式：ttkbootstrap 的默认滚动条样式在这里读不出对比度。
    scrollbar.configure(style="ModuleList.Vertical.TScrollbar")
    scrollbar.pack(side="right", fill="y")
    state = {"visible": True}

    def set_visible(visible: bool) -> None:
        if visible == state["visible"]:
            return
        state["visible"] = visible
        if visible:
            scrollbar.pack(side="right", fill="y")
        else:
            scrollbar.pack_forget()

    def refresh(_event=None) -> None:
        try:
            first, last = tree.yview()
        except tk.TclError:
            return
        set_visible(not (first <= 0.0 and last >= 1.0))

    def on_yscroll(first, last) -> None:
        set_visible(not (float(first) <= 0.0 and float(last) >= 1.0))
        scrollbar.set(first, last)

    tree.configure(yscrollcommand=on_yscroll)
    tree.bind("<Configure>", refresh, add="+")


def bind_wheel_to_scroll_tree(widget, tree_getter) -> None:
    """让滚轮在该窗口任意位置都能翻列表（不必先把光标停在表格上）。

    Tk 只在指针位于可滚动控件上时才滚动它；模块列表两侧和按钮行上空滚轮
    原本毫无反应，用户会以为列表「滑不动」。tree_getter 返回当前应滚动的
    列表（模块管理窗口按当前页签取），返回 None 时不做任何事。
    """
    def on_wheel(event):
        tree = tree_getter()
        if tree is None:
            return None
        try:
            delta = int(event.delta)
        except (TypeError, ValueError):
            return None
        if delta == 0:
            return None
        # Windows 的 delta 是 120 的倍数（正数向上滚）；Tk 的 Button-4/5 走
        # 下面单独的分支，这里的单位按「行」自动匹配。列表末尾时保持原样，
        # 让事件继续传给其它绑定（例如外层 Canvas）。
        before = tree.yview()
        tree.yview_scroll(-1 if delta > 0 else 1, "units")
        if tree.yview() != before:
            return "break"
        return None

    def on_wheel_up(_event):
        tree = tree_getter()
        if tree is not None:
            tree.yview_scroll(-1, "units")
        return "break"

    def on_wheel_down(_event):
        tree = tree_getter()
        if tree is not None:
            tree.yview_scroll(1, "units")
        return "break"

    widget.bind("<MouseWheel>", on_wheel, add="+")
    widget.bind("<Button-4>", on_wheel_up, add="+")
    widget.bind("<Button-5>", on_wheel_down, add="+")


def configure_module_tree_styles(style) -> None:
    """Clone the base Treeview layout and keep all manager variants readable."""
    base_tree_layout = style.layout("Treeview")
    # 行高按字体实际行高算（与主窗口列表同一套算法）。写死 42 会让每行比文字
    # 高出一倍多：10 行表格凭空多出两百多像素空白，窗口也被一并撑大。
    rowheight = tkfont.Font(family=FONT_FAMILY, size=FONT_SUBTITLE).metrics("linespace") + px(8)
    for style_name, obj in (
        ("ModuleManagerNeutral.Treeview", None),
        ("ModuleManagerEnabled.Treeview", {"enabled": True}),
        ("ModuleManagerSpecial.Treeview", {
            "enabled": True,
            "run_code_after_action": True,
            "on_success_actions": [{"type": "restart_workflow"}],
        }),
        ("ModuleManagerDisabled.Treeview", {"enabled": False}),
    ):
        if base_tree_layout:
            style.layout(style_name, copy.deepcopy(base_tree_layout))
        style.configure(
            style_name,
            background=COLOR_SURFACE,
            fieldbackground=COLOR_SURFACE,
            foreground=COLOR_TEXT,
            rowheight=rowheight,
            font=(FONT_FAMILY, FONT_SUBTITLE),
        )
        selected_fg, selected_bg = module_manager_selection_colors(obj)
        style.map(
            style_name,
            foreground=[("selected", selected_fg)],
            background=[("selected", selected_bg)],
        )


def multi_condition_field_states(kind: str, ocr_mode: str) -> dict[str, bool]:
    """Return which condition-specific inputs are editable."""
    image = kind == "image"
    text = kind == "ocr" and ocr_mode == "text"
    number = kind == "ocr" and ocr_mode == "number"
    return {
        "image": image,
        "ocr_mode": not image,
        "ocr_text": text,
        "ocr_match": text,
        "separator": number,
        "relation": number,
    }


def row_list_condition_field_states(kind: str) -> dict[str, bool]:
    """Return the editable inputs for one row-list condition type."""
    return {
        "module": kind == "image",
        "text": kind == "text",
        "match_mode": kind == "text",
        "separator": kind == "number",
        "relation": kind == "number",
    }
