from __future__ import annotations

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
from macroflow.core.storage import (
    BASE_DIR, IMAGES_DIR, SCRIPTS_DIR, WORKFLOWS_DIR, archive_overwritten_script,
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    DEFAULT_MODULE_TRIGGER_COOLDOWN_MS,
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
from pathlib import Path
import time

from .constants import (
    DEFAULT_GLOBAL_CLICK_DELAY_MS,
)

class GlobalDetectMixin:
    """全局检测的注册与配置播报（脚本动作 / 工作流全局模块）。"""

    def _enter_script_global_scope(self, actions: list[dict],
                                   origin_row: int = 0,
                                   last_row: int | None = None) -> tuple[str, ...]:
        """Enable the script-global actions that playback can still reach.

        ``origin_row`` 是本次播放的起始行（0 基）：「▶ 从此开始执行」从第 N 行起跑时，
        第 N 行之前的全局模块行不会被执行到，也就不能注册守卫——否则用户明明从第 7
        行开始跑，第 1~2 行的全局模块照样在后台识别并点击（表现为“还是从头执行”）。
        ``last_row`` 是片段循环的末行（含）：片段之外的行本次不会执行到，同样不注册。
        整份脚本从头播放（origin_row=0、last_row=None）、工作流重复与断点恢复都保持
        原来的“全部注册”。
        """
        ensure_action_ids(actions)
        scope_started_at = time.perf_counter()
        scope_action_ids = frozenset(
            str(action.get(ACTION_ID_KEY, "")).strip()
            for action in actions
            if str(action.get(ACTION_ID_KEY, "")).strip()
        )
        first_row = max(0, int(origin_row or 0))
        last_row = None if last_row is None else max(first_row, int(last_row))
        keys: list[str] = []
        skipped_rows: list[int] = []
        for row, action in enumerate(actions):
            if str(action.get("type", "")) != "global_detect":
                continue
            if row < first_row or (last_row is not None and row > last_row):
                skipped_rows.append(row + 1)
                continue
            action_id = str(action.get(ACTION_ID_KEY, "")).strip()
            if not action_id:
                action_id = new_action_id()
                action[ACTION_ID_KEY] = action_id
            key = f"script:{action_id}"
            keys.append(key)
            self._activate_global_detect_from_config(action)
            guards = getattr(self, "global_guards", None)
            if guards is not None:
                lock = getattr(self, "guards_lock", None)
                if lock is None:
                    guard = guards.get(key)
                    if guard is not None:
                        guard["scope_action_ids"] = scope_action_ids
                else:
                    with lock:
                        guard = guards.get(key)
                        if guard is not None:
                            guard["scope_action_ids"] = scope_action_ids
        if skipped_rows:
            rows_text = "、".join(str(row) for row in skipped_rows)
            scope_text = (
                f"从第 {first_row + 1} 行开始执行" if last_row is None
                else f"循环执行片段 第 {first_row + 1}-{last_row + 1} 行"
            )
            self._ui(
                self._log,
                f"{scope_text}：第 {rows_text} 行的全局模块不启用"
                "（这些行本次不会执行到）。要整份脚本监控请按 F9 从头执行。",
            )
        # All script-global module delays belong to the calling script's start,
        # not to the order in which individual guards finish registering.
        guards = getattr(self, "global_guards", None)
        if guards is not None:
            lock = getattr(self, "guards_lock", None)
            if lock is None:
                guarded_items = ((key, guards.get(key)) for key in keys)
                for _key, guard in guarded_items:
                    if guard is not None:
                        guard["start_delay_since"] = scope_started_at
                        guard["start_delay_done"] = False
                        guard["not_found_since"] = scope_started_at
            else:
                with lock:
                    for key in keys:
                        guard = guards.get(key)
                        if guard is not None:
                            guard["start_delay_since"] = scope_started_at
                            guard["start_delay_done"] = False
                            guard["not_found_since"] = scope_started_at
        return tuple(keys)
    def _exit_script_global_scope(self, keys: object) -> None:
        """Remove only the script-global guards owned by the leaving script."""
        cooldowns = getattr(self, "global_detect_cooldown_deadlines", None)
        key_set = {str(key) for key in tuple(keys or ())}
        with self.guards_lock:
            for key in key_set:
                self.global_guards.pop(key, None)
                if cooldowns is not None:
                    cooldowns.pop(key, None)
        pending = getattr(self, "_pending_global_guard_hits", None)
        if pending and key_set:
            pending[:] = [
                hit for hit in pending
                if str(hit.get("guard_key", "")) not in key_set
            ]
        if key_set:
            # 只有真的移除了守卫才需要让在途检测结果失效；否则每次脚本作用域
            # 退出都无谓地丢弃一次已经算好的命中（见 _evaluate_global_guards）。
            self._invalidate_detection_config()
    def _activate_global_detect_from_config(self, config: dict, module: dict | None = None,
                                            standalone_replay: dict | None = None):
        """Register one global-detect guard.

        Workflow-global modules are keyed by step_id. Script-global actions are
        keyed by action_id. 守卫只是数据：由播放器在动作边界/等待期间评估，
        触发时在播放器内联执行处理段，注册阶段不启动任何线程。
        """
        template_path = resolve_path(str(config.get("template", "")))
        # 引用模块：识别参数与动作 B 从对象仓库实时读取（改对象即生效）。
        # 统一用解析后的绝对路径查询，避免启用阶段和评估阶段读到不同配置。
        module_ref = bool(config.get("module_ref"))
        module_ref_key = str(config.get("module_key") or config.get("template", ""))
        if module_ref:
            obj = registered_module_object(module_ref_key)
            if obj is None:
                self._ui(self._log, f"全局检测未启用：引用的模块对象不存在：{module_ref_key or '未设置'}。")
                return
            if not bool(obj.get("enabled", True)):
                module_name = str(obj.get("name", "")).strip() or Path(
                    module_ref_key.replace("\\", "/"),
                ).stem
                self._ui(self._log, f"全局检测未启用：模块管理中的 {module_name or '未命名模块'} 已禁用。")
                return
            if obj is not None:
                config = dict(config)
                if str(obj.get("template", "")).strip():
                    template_path = resolve_path(str(obj["template"]))
                config["threshold"] = obj.get("threshold", 0.85)
                config["interval_ms"] = obj.get("interval_ms", 250)
                config["cooldown_ms"] = obj.get(
                    "cooldown_ms", DEFAULT_MODULE_TRIGGER_COOLDOWN_MS,
                )
                config["start_delay_ms"] = obj.get("start_delay_ms", 0)
                config["fallback_module_key"] = obj.get("fallback_module_key", "")
                config["fallback_on_match"] = obj.get("fallback_on_match", "continue")
                config["fallback_click"] = obj.get("fallback_click", False)
                config["hold_enabled"] = obj.get("hold_enabled", False)
                config["hold_ms"] = obj.get("hold_ms", 1000)
                config["restart_delay_ms"] = obj.get("delay_ms", 0)
                config["ignore_background"] = obj.get("ignore_background", False)
                config["recognize"] = obj.get("recognize", "")
                config["expected_text"] = obj.get("expected_text", "")
                config["match_mode"] = obj.get("match_mode", "contains")
                config["wait_text_absent"] = obj.get("wait_text_absent", False)
                config["click_count"] = obj.get("click_count", 1)
                for field in (
                    "ocr_offset_up", "ocr_offset_down", "ocr_offset_left", "ocr_offset_right",
                ):
                    config[field] = obj.get(field, 0)
        try:
            threshold = max(0.1, min(1.0, float(config.get("threshold", 0.85))))
            interval = max(100, min(10000, int(config.get("interval_ms", 500))))
            cooldown = max(0, min(86400000, int(config.get(
                "cooldown_ms", DEFAULT_MODULE_TRIGGER_COOLDOWN_MS,
            ))))
            start_delay = max(0, min(86400000, int(config.get("start_delay_ms", 0))))
            # hold 上限放宽到 10 分钟：长时间“持续可见”判定（如回合间主线界面
            # 卡死 5 分钟才触发的兜底检测）不能被 60 秒截断。
            hold = max(0, min(600000, int(config.get("hold_ms", 1000))))
            restart_delay = max(0, min(60000, int(config.get("restart_delay_ms", DEFAULT_GLOBAL_CLICK_DELAY_MS))))
            jump_row = max(0, int(config.get("jump_row", 0)))
        except (TypeError, ValueError):
            threshold, interval, cooldown, start_delay, hold, restart_delay, jump_row = (
                0.85, 500, DEFAULT_MODULE_TRIGGER_COOLDOWN_MS, 0, 1000, 0, 0,
            )
        region = config.get("region", [])
        # 旧配置没有 region_mode：有区域按自定义区域，否则按全屏。
        region_mode = str(config.get(
            "region_mode", "custom" if isinstance(region, (list, tuple)) and len(region) == 4 else "screen",
        ))
        if region_mode == "template":
            # 模块引用使用该模块自己的区域。同一图片可由多个模块共用，不能按图片路径
            # 反查一个不确定的区域；普通识图动作仍兼容旧的图片区域登记表。
            registered = (
                (obj or {}).get("region")
                if module_ref else registered_template_region(str(config.get("template", "")))
            )
            if registered and registered[2] > 0 and registered[3] > 0:
                region = tuple(int(part) for part in registered)
            else:
                region = None
        click = config.get("click_point", [])
        if module is not None:
            step_id = str(module.get("step_id", "")).strip()
            if not step_id:
                step_id = new_action_id()
                module["step_id"] = step_id
            key = f"workflow:{step_id}"
        else:
            action_id = str(config.get(ACTION_ID_KEY, "")).strip()
            if not action_id:
                action_id = new_action_id()
                config[ACTION_ID_KEY] = action_id
            key = f"script:{action_id}"
        # 引用模块的动作 B 分发参数（实时引用对象，评估器每轮重读对象属性）。
        after_action = "click_match"
        button = "left"
        second = None
        segment: list[dict] = []
        timeout_enabled = False
        not_found_timeout_ms = DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS
        timeout_segment: list[dict] = []
        if module_ref:
            obj = registered_module_object(module_ref_key)
            if obj is not None:
                after_action = str(obj.get("after_action", "click_match"))
                button = str(obj.get("button", "left"))
                if after_action == "click_custom" and len(obj.get("click_point") or []) == 2:
                    click = obj.get("click_point")
                if after_action == "second_match":
                    second = {
                        "template": str(obj.get("second_match_template", "")).strip(),
                        "timeout_ms": max(0, int(obj.get("second_match_timeout_ms", 3000))),
                        "blocking": bool(obj.get("blocking", False)),
                        "click_target": str(obj.get("second_match_click_target", "second")),
                        "click_region": obj.get("second_match_click_region") or [],
                    }
                if bool(obj.get("run_code_after_action", False)) or after_action == "run_actions":
                    segment = list(obj.get("on_success_actions") or [])
                timeout_enabled = bool(obj.get("run_code_on_timeout", False)) and not bool(
                    obj.get("wait_text_absent", False)
                )
                not_found_timeout_ms = max(
                    0, int(obj.get(
                        "not_found_timeout_ms", DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
                    )),
                )
                timeout_segment = list(obj.get("on_timeout_actions") or [])
        if module is not None:
            module_display_name = (
                str(module.get("name", "")).strip()
                or Path(str(module.get("script", ""))).stem
                or "工作流全局模块"
            )
        elif module_ref:
            module_display_name = (
                str((obj or {}).get("name", "")).strip()
                or Path(module_ref_key.replace("\\", "/")).stem
                or "引用全局模块"
            )
        else:
            module_display_name = str(config.get("name", "")).strip() or "脚本全局模块"
        cooldowns = getattr(self, "global_detect_cooldown_deadlines", None)
        if cooldowns is None:
            cooldowns = self.global_detect_cooldown_deadlines = {}
        guard = {
            "key": key,
            "module": dict(module) if module is not None else None,
            "template": template_path,
            "threshold": threshold,
            "interval_ms": interval,
            "cooldown_ms": cooldown,
            "cooldown_until": float(cooldowns.get(key, 0.0)),
            "start_delay_ms": start_delay if module is None else 0,
            "start_delay_since": time.perf_counter(),
            "start_delay_done": False,
            "fallback_module_key": str(config.get("fallback_module_key", "")).strip(),
            "fallback_on_match": str(config.get("fallback_on_match", "continue")).strip(),
            "fallback_click": bool(config.get("fallback_click", False)),
            "fallback_click_count": max(1, min(9999, int(config.get("fallback_click_count", 1)))),
            "fallback_click_interval_ms": max(0, min(60000, int(config.get("fallback_click_interval_ms", 100)))),
            "fallback_present": False,
            "fallback_click_since": 0.0,
            "ignore_background": bool(config.get("ignore_background", False)),
            "recognize": str(config.get("recognize", "")),
            "expected_text": str(config.get("expected_text", "")),
            "match_mode": str(config.get("match_mode", "contains")),
            "wait_text_absent": bool(config.get("wait_text_absent", False)),
            "target_absent_armed": False,
            "click_count": max(1, min(9999, int(config.get("click_count", 1)))),
            "ocr_offset_up": int(config.get("ocr_offset_up", 0)),
            "ocr_offset_down": int(config.get("ocr_offset_down", 0)),
            "ocr_offset_left": int(config.get("ocr_offset_left", 0)),
            "ocr_offset_right": int(config.get("ocr_offset_right", 0)),
            "hold_ms": hold,
            "hold_enabled": bool(config.get("hold_enabled", False)),
            "delay_ms": restart_delay,
            "region_mode": region_mode,
            "region": (
                tuple(int(part) for part in region)
                if isinstance(region, (list, tuple)) and len(region) == 4 else None
            ),
            "click": (
                (int(click[0]), int(click[1]))
                if isinstance(click, (list, tuple)) and len(click) == 2 else None
            ),
            "jump_row": jump_row,
            "jump_action_id": str(config.get("jump_action_id", "")).strip(),
            # 引用模块行没有“启用触发后跳转”开关（行编辑对话框只提供延时），
            # 沿用旧引擎语义：配置了跳转目标即生效；仅非引用行受复选框控制
            # （「启用触发后跳转」默认不勾选）。
            "jump_disabled": (
                not bool(config.get("jump_enabled", True))
                if module_ref else not bool(config.get("jump_enabled", False))
            ),
            "module_ref": module_ref,
            "module_key": module_ref_key,
            "module_display_name": module_display_name,
            "after_action": after_action,
            "button": button,
            "second": second,
            "segment": segment,
            "success_segment": segment,
            "segment_ready": False,
            "timeout_enabled": timeout_enabled,
            "not_found_timeout_ms": not_found_timeout_ms,
            "timeout_segment": timeout_segment,
            "timeout_triggered": False,
            "not_found_since": time.perf_counter(),
            "trigger_kind": "success",
            "was_detected": False,
            "match_since": None,
            "match_data": None,
            "last_ocr_observation": None,
            "last_check_time": 0.0,
            "warned_missing_template": False,
            "warned_find_error": False,
            "warned_missing_module": False,
            "standalone_replay": standalone_replay,
        }
        with self.guards_lock:
            self.global_guards[key] = guard
        self._invalidate_detection_config()
        name = guard["template"].name or "未设置"
        if region_mode == "window":
            region_text = "目标窗口"
        elif region_mode == "template":
            region_text = "模板区域" if guard["region"] else "模板未设置区域，按全屏检测"
        elif region_mode == "custom" and guard["region"]:
            region_text = ",".join(str(part) for part in guard["region"])
        else:
            region_text = "全屏"
        if guard["module"]:
            tail = "先执行模块步骤，再继续原工作流。"
        elif guard.get("jump_disabled"):
            tail = "不跳转，继续执行脚本。"
        elif guard["jump_row"] or guard.get("jump_action_id"):
            if guard.get("jump_action_id") == NEXT_WORKFLOW_STEP_TARGET_ID:
                tail = "结束当前脚本执行（引用脚本进入下一次；顶层脚本进入工作流下一项）。"
            else:
                tail = "跳转到目标行执行，播放到末尾后结束。"
        else:
            tail = "执行脚本动作，再继续检测。"
        hold_text = f"持续超过 {hold} ms" if guard.get("hold_enabled", False) else "识别到立即执行"
        repeat_text = (
            "条件仍存在时按间隔重试，目标消失后停止 · "
            if guard.get("wait_text_absent") else ""
        )
        start_delay_text = (
            f" · {start_delay} ms 后开始识别" if guard.get("start_delay_ms", 0) else ""
        )
        # 每个模块的完整配置属于「这一行脚本做了什么」：进执行明细，
        # 不刷事件日志（工作流全局模块会随每一次重复重新注册）。
        self._ui(
            self._trace_event,
            f"全局检测已启用：模块[{module_display_name}] · {name} · 区域 {region_text} · {hold_text}"
            f"{start_delay_text} · 触发冷却 {cooldown} ms · {repeat_text}"
            f"触发后{tail}",
        )
    @staticmethod
    @staticmethod
    def _global_monitor_subject(guard: dict, subject: str) -> str:
        """给全局检测日志补上实际触发的模块名，避免同图多模块时无法追溯。"""
        module_name = str(guard.get("module_display_name", "")).strip() or "脚本全局模块"
        return f"模块[{module_name}] · {subject}"
