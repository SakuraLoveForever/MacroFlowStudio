from __future__ import annotations

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
from macroflow.core.models import (
    ACTION_ID_KEY, DEFAULT_MOUSE_MOVE_INTERVAL_MS, DEFAULT_RECORDED_SCREEN,
    DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
    END_CURRENT_SCRIPT_LABEL, JUMP_TARGET_KEYS, NEXT_WORKFLOW_STEP_TARGET_ID,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    SCRIPT_START_TARGET_ID,
    MacroScript, Workflow, clone_actions_with_new_ids,
    ensure_action_ids, ensure_workflow_step_ids, is_global_script,
    new_action_id,
    recorded_input_steps,
    script_ref_repeat_count, scroll_clicks, scroll_direction_label,
)
from pathlib import Path
import json

from .base import (
    action_kind_label,
    short_region_text,
    workflow_script_name,
)
from .constants import (
    ACTION_ICONS,
    DEFAULT_GLOBAL_CLICK_DELAY_MS,
    MODULE_CLICK_LABELS,
    REGION_LABELS,
)

def _module_row_result_summary(action: dict, action_rows: dict[str, int] | None,
                               number_mode: bool = False) -> str:
    def describe(behavior_key: str, target_key: str) -> str:
        behavior = str(action.get(behavior_key, "continue"))
        if behavior == "end_current_script":
            return "结束当前最里层脚本"
        if behavior == "jump":
            target_id = str(action.get(target_key, "")).strip()
            target_row = action_rows.get(target_id) if action_rows and target_id else None
            if target_row is not None:
                return f"跳到第 {target_row} 行"
            return "跳转目标已删除" if target_id else "跳转目标未设置"
        return "继续下一行"

    if number_mode:
        expected = action.get("expected_number")
        expected_text = "未设置" if expected is None else str(expected)
        return (
            f"比较 {expected_text} · 等于时{describe('on_found', 'found_jump_action_id')} / "
            f"不等于或未读取到时{describe('on_timeout', 'timeout_jump_action_id')}"
        )
    return (f"结果 成功后{describe('on_found', 'found_jump_action_id')} / "
            f"失败后{describe('on_timeout', 'timeout_jump_action_id')}")
def _module_ref_summary(action: dict, label: str,
                        action_rows: dict[str, int] | None = None) -> tuple[str, str, str]:
    """引用模块动作的实时摘要：运行时从对象仓库读属性渲染。"""
    kind = str(action.get("type", "unknown"))
    key = str(action.get("module_key") or action.get("template", ""))
    obj = registered_module_object(key)
    if obj is None:
        name = Path(key).name or "未设置"
        result_text = (
            f" · {_module_row_result_summary(action, action_rows, 'expected_number' in action)}"
            if kind == "image_match" else ""
        )
        return (
            action_kind_label(kind, label),
            f"引用模块 {name}（对象不存在，按内嵌参数执行）{result_text}",
            f"{int(action.get('delay_ms', 0))} ms",
        )
    name = str(obj.get("name") or Path(key.replace("\\", "/")).stem)
    category = {
        "switch": "切换", "workflow_global": "工作流全局",
        "script_global": "脚本全局",
    }.get(obj.get("category"), "特殊")
    label = {
        "workflow_global": "工作流全局模块",
        "script_global": "脚本全局模块",
    }.get(obj.get("category"), label)
    after = str(obj.get("after_action", "click_match"))
    direct_mode = obj.get("recognize") == "none"
    number_mode = obj.get("recognize") == "number"
    blocking = (
        ("持续执行直到期望文字消失" if obj.get("recognize") == "text" else "持续执行直到模板图片消失")
        if obj.get("wait_text_absent") else
        f"阻塞 {int(action.get('blocking_timeout_ms', DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS))} ms 后跳过当前行"
        if obj.get("blocking") and action.get("blocking_timeout_enabled", False) else
        "阻塞直到出现" if obj.get("blocking") else "等待超时后继续"
    )
    click_text = ""
    if not number_mode:
        if after in MODULE_CLICK_LABELS:
            click_text = MODULE_CLICK_LABELS[after]
            if after != "second_match":
                click_text += f" × {max(1, int(obj.get('click_count', 1)))}"
    # 参数列只写核心功能：区域坐标、阈值、延时、代码段项数这些都在「编辑模块引用」
    # 窗口里，摘要里重复一遍只会把关键信息（模块名、点击方式、跳转目标）埋掉。
    if number_mode:
        detail = f"引用{category}模块 {name} · 读取数字"
    else:
        detail = f"引用{category}模块 {name}"
        if direct_mode:
            detail += " · 无需识图"
        elif click_text:
            detail += f" · {click_text}"
        detail += f" · {blocking}" if not direct_mode else ""
    if obj.get("category") in ("workflow_global", "script_global") \
            and obj.get("hold_enabled", False):
        detail += f" · 持续超过 {int(obj.get('hold_ms', 1000))} ms"
    if kind == "global_detect" and action.get("module_ref"):
        # 引用模块行的“触发后跳转”沿用旧引擎语义：配置了跳转目标即生效
        # （该行没有独立开关），未配置则自然无跳转。
        if action.get("jump_enabled", True) and \
                str(action.get("jump_action_id", "")).strip() == \
                NEXT_WORKFLOW_STEP_TARGET_ID:
            detail += " · 触发后结束当前脚本执行（引用脚本进入下一次；顶层脚本进入工作流下一项）"
        elif action.get("jump_action_id") or action.get("jump_row"):
            target_id = str(action.get("jump_action_id", "")).strip()
            target_row = action_rows.get(target_id) if action_rows and target_id else None
            if target_row is not None:
                detail += f" · 触发后跳转到第 {target_row} 行"
            elif target_id:
                detail += " · 触发后跳转目标已删除"
            else:
                detail += f" · 触发后跳转到第 {max(1, int(action.get('jump_row', 1)))} 行"
    if kind == "image_match":
        detail += f" · {_module_row_result_summary(action, action_rows, number_mode)}"
    delay = int(action.get("delay_ms", 0))
    return action_kind_label(kind, label), detail, f"{delay} ms" if delay else ""
def key_action_matches(
    action: dict, query: str = "", state: str = "all", query_kind: str = "",
) -> bool:
    """Return whether an input action matches a key/mouse query and state.

    ``query_kind`` is set for values captured by the combined key/mouse
    detector. Captured values must stay within their input type and match
    exactly; an empty kind keeps the fuzzy matching used for manual searches.
    """
    kind = str(action.get("type", ""))
    if kind not in {"key", "key_press", "mouse_button", "click", "repeat_click"}:
        return False
    normalized_kind = str(query_kind or "").strip().casefold()
    if normalized_kind not in {"", "key", "mouse"}:
        normalized_kind = ""
    is_mouse_action = kind in {"mouse_button", "click", "repeat_click"}
    if normalized_kind == "key" and is_mouse_action:
        return False
    if normalized_kind == "mouse" and not is_mouse_action:
        return False
    state_aliases = {
        "全部": "all", "按下": "down", "抬起": "up", "Press": "press",
    }
    normalized_state = state_aliases.get(str(state), str(state).casefold())
    if kind in {"key_press", "click", "repeat_click"}:
        action_state = "press"
    else:
        action_state = "down" if bool(action.get("down")) else "up"
    if normalized_state not in {"", "all"} and normalized_state != action_state:
        return False
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    if kind in {"mouse_button", "click", "repeat_click"}:
        button = str(action.get("button", "left")).strip().casefold()
        button_name = {
            "left": "左键", "right": "右键", "middle": "中键",
        }.get(button, button)
        if normalized_kind == "mouse":
            return needle in {button, button_name.casefold()}
        return needle in button or needle in button_name.casefold()
    name = str(action.get("name", "")).strip().casefold()
    vk = str(action.get("vk", "")).strip().casefold()
    if normalized_kind == "key":
        return needle in {name, vk}
    return needle in name or needle in vk
def set_matching_key_action_delays(
    actions: list[dict], query: str, state: str, delay_ms: int, query_kind: str = "",
) -> list[int]:
    """Set delay_ms for matching key actions and return their indices."""
    if not str(query or "").strip():
        return []
    delay = max(0, int(delay_ms))
    changed: list[int] = []
    for index, action in enumerate(actions):
        if key_action_matches(action, query, state, query_kind=query_kind):
            action["delay_ms"] = delay
            changed.append(index)
    return changed
def action_summary(action: dict, action_rows: dict[str, int] | None = None) -> tuple[str, str, str]:
    kind = action.get("type", "unknown")
    delay = f"{int(action.get('delay_ms', 1000 if kind == 'image_match' else 0))} ms"
    if kind == "delay":
        return action_kind_label(kind, "延时"), f"等待 {action.get('ms', 0)} ms", delay
    if kind == "key":
        state = "按下" if action.get("down") else "松开"
        return action_kind_label(kind, "键盘"), f"{state} {action.get('name', action.get('vk'))}", delay
    if kind == "key_press":
        return action_kind_label(kind, "键盘"), f"敲击 {action.get('name', action.get('vk'))}，按住 {action.get('hold_ms', 30)} ms", delay
    if kind == "text":
        text = str(action.get("text", "")).replace("\n", "↵")
        return action_kind_label(kind, "文本"), f"输入 “{text}”", delay
    if kind == "mouse_move":
        if action.get("mode") == "relative":
            return action_kind_label(kind, "转向"), f"ΔX {action.get('dx', 0)}，ΔY {action.get('dy', 0)}", delay
        return action_kind_label(kind, "移动"), f"X {action.get('x', 0)}，Y {action.get('y', 0)}", delay
    if kind == "mouse_button":
        state = "按下" if action.get("down") else "松开"
        return action_kind_label(kind, "点击"), f"{state} {action.get('button', 'left')} @ ({action.get('x', 0)}, {action.get('y', 0)})", delay
    if kind == "click":
        if action.get("pos_mode") == "current":
            return action_kind_label(kind, "点击"), f"{action.get('button', 'left')} @ 鼠标当前位置", delay
        return action_kind_label(kind, "点击"), f"{action.get('button', 'left')} @ ({action.get('x', 0)}, {action.get('y', 0)})", delay
    if kind == "repeat_click":
        return (
            action_kind_label(kind, "连续点击"),
            f"{action.get('button', 'left')} ×{action.get('count', 2)} 次 · "
            f"{action.get('interval_ms', 100)} ms 间隔 @ ({action.get('x', 0)}, {action.get('y', 0)})",
            delay,
        )
    if kind == "turn":
        dx = int(action.get('dx', 0))
        dy = int(action.get('dy', 0))
        return (
            action_kind_label(kind, "转向"),
            f"ΔX={dx}，ΔY={dy}",
            delay,
        )
    if kind == "scroll":
        direction = scroll_direction_label(action.get("dy", 0))
        clicks = scroll_clicks(action.get("dy", 0))
        return (
            action_kind_label(kind, "滚轮"),
            f"{direction} {clicks} 格 @ ({action.get('x', 0)}, {action.get('y', 0)})",
            delay,
        )
    if kind == RECORDED_INPUT_TYPE:
        steps = recorded_input_steps(action)
        detail = (
            f"共 {len(steps)} 步 · 双击展开编辑" if steps
            else "还没有录到内容 · 双击开始录制"
        )
        return action_kind_label(kind, "录制动作"), detail, delay
    if kind == "image_match":
        if action.get("module_ref"):
            return _module_ref_summary(action, "识图模块", action_rows)
        if action.get("on_found") == "jump":
            found_target_id = str(action.get("found_jump_action_id", "")).strip()
            found_target_row = action_rows.get(found_target_id) if action_rows and found_target_id else None
            if found_target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                operation = "找到后结束当前脚本执行（引用脚本进入下一次；顶层脚本进入工作流下一项）"
            elif found_target_row is not None:
                operation = f"找到后跳到第 {found_target_row} 行"
            elif found_target_id:
                operation = "找到后跳转目标已删除"
            else:
                operation = f"找到后跳到第 {max(1, int(action.get('found_jump_row', 1)))} 行（旧格式）"
        elif action.get("on_found", "click") == "click":
            operation = "找到后点击"
        else:
            operation = "等待出现"
        if action.get("click_target", "match") == "custom":
            point = action.get("click_point", [0, 0])
            click_target = f"自定义坐标 ({point[0]}, {point[1]})"
        else:
            click_target = "识图区域中心"
        found_delay = int(action.get("found_delay_ms", 0))
        timeout_ms = int(action.get("timeout_ms", 3000))
        wait_forever = bool(action.get("wait_forever", False))
        timeout_delay = int(action.get("timeout_delay_ms", 0))
        after_delay = int(action.get("after_delay_ms", 0))
        if wait_forever:
            timeout_text = "一直等待直到出现（不超时）"
        else:
            timeout_action = action.get("on_timeout", "continue")
            if timeout_action == "jump":
                target_id = str(action.get("timeout_jump_action_id", "")).strip()
                target_row = action_rows.get(target_id) if action_rows and target_id else None
                if target_row is not None:
                    timeout_text = f"超时跳到第 {target_row} 行目标动作"
                elif target_id:
                    timeout_text = "超时跳转目标已删除"
                else:
                    timeout_text = f"超时跳到第 {max(1, int(action.get('timeout_jump_row', 1)))} 行（旧格式）"
            elif timeout_action == "end_current_script":
                timeout_text = "超时结束当前脚本"
            elif timeout_action == "stop":
                timeout_text = "超时停止"
            else:
                timeout_text = "超时继续"
        timeout_delay_text = f" · 超时后等待 {timeout_delay} ms" if timeout_delay else ""
        after_delay_text = f" · 执行后等待 {after_delay} ms" if after_delay else ""
        notice = " · 浮动提醒" if action.get("show_result_notice") else ""
        fallback_name = str(action.get("fallback_template", "")).strip()
        fallback_text = ""
        if wait_forever and fallback_name:
            fallback_parts = [
                f"超 {int(action.get('fallback_switch_ms', 3000))} ms 换备用 {Path(fallback_name).name}",
                "点击" if action.get("fallback_click", True) else "不点击",
                "出现后退出识别"
                if action.get("fallback_on_match", "回到主模板的检测") == "直接退出识别"
                else "出现后回到主模板检测",
            ]
            fallback_text = " · " + "，".join(fallback_parts)
        timeout_label = "一直等待" if wait_forever else f"等待超时 {timeout_ms} ms"
        return action_kind_label(kind, "识图"), f"{operation} · {click_target} · {timeout_label} · {timeout_text}{timeout_delay_text}{fallback_text} · 成功后等待 {found_delay} ms · {Path(str(action.get('template', ''))).name} · 阈值 {float(action.get('threshold', .85)):.0%}{after_delay_text}{notice}", delay
    if kind == "text_ocr":
        expected = str(action.get("expected_text", "")).strip() or "任意文字"
        match_text = "等于" if action.get("match_mode", "contains") == "equals" else "包含"
        if len(action.get("region", [])) == 4:
            region_text = short_region_text(action.get("region"))
        else:
            region_text = REGION_LABELS.get(
                str(action.get("region_mode", "screen")), "全屏",
            )
        timeout_ms = int(action.get("timeout_ms", 3000))
        timeout_label = "只识别一次" if timeout_ms <= 0 else f"等待超时 {timeout_ms} ms"
        if action.get("on_found", "continue") == "jump":
            target_id = str(action.get("found_jump_action_id", "")).strip()
            target_row = action_rows.get(target_id) if action_rows and target_id else None
            if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                found_text = "找到后结束当前脚本执行（引用脚本进入下一次；顶层脚本进入工作流下一项）"
            elif target_row is not None:
                found_text = f"找到后跳到第 {target_row} 行"
            elif target_id:
                found_text = "找到后跳转目标已删除"
            else:
                found_text = f"找到后跳到第 {max(1, int(action.get('found_jump_row', 1)))} 行（旧格式）"
        else:
            found_text = "找到后继续"
        timeout_action = action.get("on_timeout", "continue")
        if timeout_action == "jump":
            target_id = str(action.get("timeout_jump_action_id", "")).strip()
            target_row = action_rows.get(target_id) if action_rows and target_id else None
            if target_row is not None:
                timeout_text = f"超时跳到第 {target_row} 行目标动作"
            elif target_id:
                timeout_text = "超时跳转目标已删除"
            else:
                timeout_text = f"超时跳到第 {max(1, int(action.get('timeout_jump_row', 1)))} 行（旧格式）"
        elif timeout_action == "stop":
            timeout_text = "超时停止"
        else:
            timeout_text = "超时继续"
        timeout_delay_text = f" · 超时后等待 {int(action.get('timeout_delay_ms', 0))} ms" \
            if int(action.get("timeout_delay_ms", 0)) else ""
        interval_text = f" · 间隔 {int(action.get('interval_ms', 500))} ms"
        return (
            action_kind_label(kind, "识别文字"),
            f"期望 {expected}（{match_text}） · 区域 {region_text} · "
            f"{found_text} · {timeout_label} · {timeout_text}{timeout_delay_text}"
            f"{interval_text} · 找到后等待 {int(action.get('found_delay_ms', 0))} ms"
            f"{' · 浮动提醒' if action.get('show_result_notice') else ''}",
            delay,
        )
    if kind == "ocr_compare":
        separator = str(action.get("separator", "/"))
        region_text = short_region_text(action.get("region"))
        click_region_text = short_region_text(action.get("click_region"), "识别位置")
        equal_action = str(action.get("equal_action", "continue"))
        not_equal_action = str(action.get("not_equal_action", "continue"))
        equal_text = (
            f"点击 {int(action.get('equal_click_count', 1))} 次"
            if equal_action == "click" else
            f"跳到第 {(
                action_rows.get(str(action.get('equal_jump_action_id', '')).strip())
                if action_rows and str(action.get('equal_jump_action_id', '')).strip()
                else None
            ) or '?'} 行"
            if equal_action == "jump" else "继续"
        )
        not_equal_text = (
            f"点击 {int(action.get('not_equal_click_count', 1))} 次"
            if not_equal_action == "click" else
            f"跳到第 {(
                action_rows.get(str(action.get('not_equal_jump_action_id', '')).strip())
                if action_rows and str(action.get('not_equal_jump_action_id', '')).strip()
                else None
            ) or '?'} 行"
            if not_equal_action == "jump" else "继续"
        )
        timeout_ms = int(action.get("timeout_ms", 3000))
        timeout_text = "只识别一次" if timeout_ms <= 0 else f"超时 {timeout_ms} ms"
        return (
            action_kind_label(kind, "数字比较"),
            f"识别区域 {region_text} · 分隔符 {separator} · 点击 {click_region_text} · "
            f"相等：{equal_text} · 不相等：{not_equal_text} · {timeout_text}",
            delay,
        )
    if kind == "multi_condition_click":
        type_labels = {"image": "图片", "ocr": "OCR"}
        condition_text = []
        for index, condition in enumerate(action.get("conditions", [])[:3], start=1):
            if not isinstance(condition, dict) or not condition.get("enabled"):
                condition_text.append(f"条件{index}未启用")
                continue
            condition_kind = str(condition.get("type", ""))
            if condition_kind == "image":
                detail = Path(str(condition.get("template", ""))).name or "未设置模板"
            elif condition_kind == "ocr":
                if str(condition.get("ocr_mode", "text")) == "number":
                    relation = "相等" if condition.get("relation", "equal") == "equal" else "不相等"
                    detail = f"数字比数字（{relation}）"
                else:
                    detail = f"文字:{str(condition.get('expected_text', '')) or '任意文字'}"
            else:
                detail = "未知条件"
            condition_text.append(f"条件{index}{type_labels.get(condition_kind, condition_kind)}:{detail}")
        return (
            action_kind_label(kind, "多条件识图"),
            f"{' · '.join(condition_text) or '未设置条件'} · "
            f"点击 {short_region_text(action.get('click_region'), '识别位置')} · "
            f"连续点击 {int(action.get('click_count', 1))} 次 · 超时 {int(action.get('timeout_ms', 3000))} ms",
            delay,
        )
    if kind == "row_list_condition_click":
        def condition_summary(condition: dict) -> str:
            condition_kind = str(condition.get("type", ""))
            if condition_kind == "image":
                return "图片模块"
            if condition_kind == "text":
                expected = str(condition.get("expected_text", "")).strip() or "任意文字"
                match_mode = "完全相等" if condition.get("match_mode") == "equals" else "包含"
                return f"文字:{expected}（{match_mode}）"
            if condition_kind == "number":
                relation = "相等" if condition.get("relation", "equal") == "equal" else "不相等"
                return f"数字{condition.get('separator', '/')}数字（{relation}）"
            return "未设置"

        left = action.get("left_condition")
        right = action.get("right_condition")
        no_match = "重试" if action.get("no_match_action") == "retry" else "结束"
        def result_summary(behavior_key: str, target_key: str) -> str:
            behavior = str(action.get(behavior_key, "continue"))
            if behavior == "jump":
                target_id = str(action.get(target_key, "")).strip()
                target_row = action_rows.get(target_id) if action_rows and target_id else None
                return f"跳到第 {target_row} 行" if target_row is not None else "跳转目标已删除"
            if behavior == "end_current_script":
                return "结束当前最里层脚本"
            return "继续下一行"
        return (
            action_kind_label(kind, "列表逐行点击"),
            "从上到下 · "
            f"行高 {int(action.get('row_height', 0))} 像素 · "
            f"左:{condition_summary(left if isinstance(left, dict) else {})} · "
            f"右:{condition_summary(right if isinstance(right, dict) else {})} · "
            f"首个匹配即点击，连续点击 {int(action.get('click_count', 1))} 次 · "
            f"成功后:{result_summary('on_found', 'found_jump_action_id')} · "
            f"失败后:{result_summary('on_timeout', 'timeout_jump_action_id')} · 未命中:{no_match}",
            delay,
        )
    if kind == "global_detect":
        if action.get("module_ref"):
            return _module_ref_summary(action, "脚本全局模块", action_rows)
        template_name = Path(str(action.get("template", ""))).name or "未设置"
        region_text = short_region_text(action.get("region"))
        jump_row = action.get("jump_row")
        if jump_row or action.get("jump_action_id"):
            # 普通脚本内嵌全局模块行：跳转目标是脚本里的一行对象（按动作唯一标识引用）。
            if not action.get("jump_enabled", False):
                jump_text = "触发后不跳转，继续执行"
            else:
                target_id = str(action.get("jump_action_id", "")).strip()
                target_row = action_rows.get(target_id) if action_rows and target_id else None
                if target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
                    jump_text = "触发后结束当前脚本执行（引用脚本进入下一次；顶层脚本进入工作流下一项）"
                elif target_row is not None:
                    jump_text = f"触发后跳转到第 {target_row} 行"
                elif target_id:
                    jump_text = "触发后跳转目标已删除"
                else:
                    jump_text = f"触发后跳转到第 {max(1, int(jump_row or 1))} 行"
            # 参数列只写核心：模板、持续时长、跳转目标；区域/阈值/间隔在编辑窗口。
            hold_text = f"持续超过 {int(action.get('hold_ms', 1000))} ms"
            detail = f"脚本全局模块 {template_name} · {hold_text} · {jump_text}"
            return action_kind_label(kind, "脚本全局模块"), detail, delay
        click_text = (
            ",".join(str(int(part)) for part in action.get("click_point", []))
            if len(action.get("click_point", [])) == 2 else "未设置"
        )
        hold_text = f"持续超过 {int(action.get('hold_ms', 1000))} ms"
        detail = (
            f"全局检测 {template_name} · 区域 {region_text} · {hold_text} · 点击 ({click_text}) · "
            f"点击后 {int(action.get('restart_delay_ms', DEFAULT_GLOBAL_CLICK_DELAY_MS))} ms 继续原工作流"
        )
        return action_kind_label(kind, "全局"), detail, delay
    if kind == "notice":
        duration = int(action.get("duration_ms", 3000))
        return action_kind_label(kind, "浮动提醒"), f"{str(action.get('text', ''))} · 显示 {duration} ms", delay
    if kind == "comment":
        return action_kind_label(kind, "注释"), str(action.get("text", "")), delay
    if kind == "script_ref":
        name = workflow_script_name(str(action.get("script", ""))) or "未设置"
        repeats = script_ref_repeat_count(action)
        return action_kind_label(kind, "引用脚本"), f"执行 {repeats} 次：{name}（实时读取原脚本最新内容）", delay
    if kind == "open_app":
        name = Path(str(action.get("path", ""))).name or "未设置"
        args = str(action.get("args", "")).strip()
        detail = f"启动 {name}" + (f"（{args}）" if args else "")
        return action_kind_label(kind, "打开软件"), detail, delay
    if kind == "close_app":
        name = str(action.get("name", "")).strip() or "未设置"
        mode = "优雅优先" if action.get("graceful", True) else "强制"
        extras = [flag for flag, on in (
            ("进程树", action.get("tree")), ("管理员重试", action.get("elevated_retry")),
        ) if on]
        detail = f"结束 {name}（{mode}" + ("、" + "、".join(extras) if extras else "") + "）"
        return action_kind_label(kind, "关闭软件"), detail, delay
    if kind == "set_resolution":
        name = str(action.get("name", "")).strip() or "未设置"
        size = f"{action.get('width', 0)}×{action.get('height', 0)}"
        refresh = int(action.get("refresh_rate", 0) or 0)
        refresh_text = f" · {refresh} Hz" if refresh else " · 沿用当前刷新率"
        scale = int(action.get("scale_percent", 100) or 100)
        signature = action.get("window") or {}
        window_name = str(
            signature.get("title") or signature.get("class_name") or ""
        ).strip()
        target = f"参照窗口：{window_name}" if window_name else "当前软件所在显示器"
        return action_kind_label(kind, "分辨率"), (
            f"切换到 {name}（{size}{refresh_text} · 缩放 {scale}%） · {target}"
        ), delay
    if kind == "restart_workflow":
        try:
            row = max(0, int(action.get("restart_workflow_target_row", 0) or 0))
        except (TypeError, ValueError):
            row = 0
        detail = (
            f"重新执行工作流（跳转到第 {row} 行；独立运行时跳过）" if row
            else "重新执行工作流（默认跳转行；独立运行时跳过）"
        )
        return action_kind_label(kind, "特殊模块"), detail, delay
    if kind == "end_current_script":
        return (
            action_kind_label(kind, "特殊模块"),
            f"{END_CURRENT_SCRIPT_LABEL}（顶层脚本结束后由调用方继续）",
            delay,
        )
    if kind == "jump_current_script_last":
        return (
            action_kind_label(kind, "跳转"),
            "离开模块代码段，从当前脚本实际最后一行动作继续执行",
            delay,
        )
    if kind == "block":
        return action_kind_label(kind, "阻塞"), "等待其他跳转离开", delay
    if kind == "jump":
        target_id = str(action.get("jump_action_id", "")).strip()
        if target_id == SCRIPT_START_TARGET_ID:
            target_text = "脚本开头（第 1 行）"
        elif target_id == NEXT_WORKFLOW_STEP_TARGET_ID:
            target_text = "脚本结尾（立即结束）"
        else:
            target_row = action_rows.get(target_id) if action_rows and target_id else None
            if target_row is not None:
                target_text = f"第 {target_row} 行目标动作"
            elif target_id:
                target_text = "目标动作已删除"
            else:
                target_text = f"第 {max(1, int(action.get('jump_row', 1)))} 行（旧格式）"
        condition = " · 仅第 2 次及以后生效" if action.get("workflow_repeat_at_least_2", True) else ""
        return action_kind_label(kind, "跳转"), f"跳转到{target_text}{condition}", delay
    return action_kind_label(kind, kind), json.dumps(action, ensure_ascii=False)[:100], delay
def action_detail(action: dict) -> str:
    """执行明细日志里的一行动作描述（与动作列表“参数”列同一套措辞）。"""
    try:
        detail = str(action_summary(action)[1] or "").strip()
    except Exception:
        detail = ""
    if not detail:
        return str(action.get("type", "未知动作"))
    # 明细日志一行一条：多行文本动作会把换行摊成多行，这里折成单行。
    return " ".join(detail.replace("\n", "↵").split())
