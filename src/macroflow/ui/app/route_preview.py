"""从键鼠连续路线里选中的某一步开始单独试跑。

路线编辑器右键「▶ 从此行开始执行」用它验证中段步骤：动作只是带
``start_step_index`` 的临时副本，不写回脚本；执行仍然走「单独执行此动作」
同一套入口（专注模式、执行小窗、F12 停止）。
"""
from __future__ import annotations

import threading
import time


def run_route_step_test(app, action: dict, step_index: int, repeats: int = 1):
    """UI 入口：从第 step_index 步（0 基）开始试跑这条路线。"""
    return app._run_detection_entrypoint(
        _run_route_step_test, app, dict(action), int(step_index), max(1, int(repeats)),
    )


def _run_route_step_test(app, action: dict, step_index: int, repeats: int = 1):
    """Start a route-only run from one recorded step without editing the script."""
    if app.recorder is not None and app.recorder.running:
        app.stop_recording()
    if app.worker and app.worker.is_alive():
        app._notify("正在运行", "已有脚本或工作流正在执行。")
        return
    app._cancel_workflow_recovery()
    app._save_activation_interval()

    action = dict(action, start_step_index=int(step_index))
    label = f"路线试跑：从第 {step_index + 1} 步开始"
    repeats = max(1, int(repeats))
    app._begin_detection_run()
    app._ensure_detection_worker()
    hwnd = app._bound_hwnd()
    activation_enabled, activation_signature = app._activation_settings_from_script()
    activation_hwnd = None
    try:
        activation_hwnd = app._execution_activation_hwnd(
            hwnd, activation_enabled, activation_signature,
        )
    except RuntimeError:
        app._log("前置窗口未打开，已跳过前置窗口，继续执行路线试跑。")
    focus_enabled = bool(app.focus_mode_enabled_var.get())
    activate_target = bool(app.activate_target_enabled_var.get())
    app.execution_focus_requested = focus_enabled
    source_screen = dict(app.script.settings.get("recorded_screen", {})) or None
    app.workflow_stop.clear()
    app.execution_started_at = time.perf_counter()
    app._set_execution_progress(f"{label} · 共执行 {repeats} 次 · 正在准备 · F12 停止")
    app.worker = threading.Thread(
        target=app._run_script_worker,
        args=([action], repeats, hwnd, activation_hwnd, source_screen,
              focus_enabled, activate_target, 0),
        kwargs={
            "trigger": {}, "script_name": label,
            "single_action": True,
            "activation_interval_ms": int(
                app.script.settings.get("activation_window_interval_ms", 0)),
        },
        daemon=True,
    )
    app.worker.start()
    app._sound("run_start")
    app._hide_main_for_execution()
    app._show_execution_mini()
    app._append_mini_step(f"路线试跑：从第 {step_index + 1} 步开始，共 {repeats} 次。")
