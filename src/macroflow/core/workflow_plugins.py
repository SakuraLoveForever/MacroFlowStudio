"""Built-in workflow plugins expressed through the existing guard engine."""

PROCESS_ABSENCE_RESTART = "process_absence_restart"


def process_absence_config(process_name: str, duration_ms: int = 60000) -> dict:
    name = str(process_name).strip()
    if not name or any(char in name for char in '/\\\x00\r\n'):
        raise ValueError("请从进程名库选择进程；可先在库中从运行列表选取或使用鼠标抓取。")
    try:
        duration = int(duration_ms)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("连续未检测到的时长请输入大于 0、最多 1440 分钟的时间。") from None
    if isinstance(duration_ms, bool) or not 1 <= duration <= 86400000:
        raise ValueError("连续未检测到的时长请输入大于 0、最多 1440 分钟的时间。")
    return {"plugin": PROCESS_ABSENCE_RESTART, "process_name": name, "duration_ms": duration}


def process_absence_guard_config(config: dict) -> dict:
    settings = process_absence_config(config.get("process_name", ""), config.get("duration_ms", 60000))
    return {
        "name": "进程缺失重启", "recognize": "process",
        "process_name": settings["process_name"], "interval_ms": 500,
        "run_code_on_timeout": True,
        "not_found_timeout_ms": settings["duration_ms"],
        "on_timeout_actions": [{"type": "restart_workflow", "restart_workflow_target_row": 1}],
        "plugin": PROCESS_ABSENCE_RESTART,
    }


def process_absence_label(config: dict) -> str:
    settings = process_absence_config(config.get("process_name", ""), config.get("duration_ms", 60000))
    minutes = settings["duration_ms"] / 60000
    return f"插件 · 进程缺失重启 · {settings['process_name']} · 连续 {minutes:g} min 未检测到 → 从第 1 行重启"
