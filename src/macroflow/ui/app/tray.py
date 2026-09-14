from __future__ import annotations

from PIL import Image, ImageDraw
from macroflow.execution.player import (
    JUMP_CURRENT_SCRIPT_LAST_RESULT, MAX_SCRIPT_REF_DEPTH,
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, GuardJumpRequest,
    JumpToCurrentScriptLastAction, MacroPlayer, PlaybackStopped,
    screen_template_scale,
)
from pathlib import Path
from macroflow.core.ocr import (
    _get_engine, find_expected_match, format_ocr_observation, matches_expected,
    ocr_match_center, recognize_image_with_boxes, recognize_region_with_boxes,
    set_progress_callback,
)
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
import os
from macroflow.core.alerts import play_alert, prewarm_alert
import pystray
import threading
import time

from .constants import (
    APP_NAME,
)

class TrayMixin:
    """系统托盘图标与托盘菜单。"""

    @staticmethod
    def _create_tray_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (30, 41, 59, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((5, 5, 59, 59), radius=13, fill=(37, 99, 235, 255))
        draw.ellipse((17, 17, 47, 47), fill=(255, 255, 255, 255))
        draw.ellipse((24, 24, 40, 40), fill=(217, 45, 32, 255))
        return image
    def _ensure_tray(self, visible: bool = True) -> bool:
        if getattr(self, "exiting", False):
            return False
        tray_lock = getattr(self, "_tray_lock", None)
        if tray_lock is None:
            tray_lock = threading.RLock()
            self._tray_lock = tray_lock
        with tray_lock:
            if getattr(self, "exiting", False):
                return False
            if self.tray_icon is not None:
                # 图标已创建：可见性由调用方（_hide_main_to_tray/_restore_main_window）
                # 同步设置并确认，这里只负责保证图标对象存在。
                return True
            try:
                ready = threading.Event()
                setup_errors: list[Exception] = []

                def setup(icon: pystray.Icon):
                    try:
                        if visible:
                            icon.visible = True
                    except Exception as exc:
                        setup_errors.append(exc)
                    finally:
                        ready.set()

                menu = pystray.Menu(
                    pystray.MenuItem("显示窗口", self._tray_restore, default=True),
                    pystray.MenuItem("退出", self._tray_exit),
                )
                self.tray_icon = pystray.Icon(
                    "MacroFlowStudio", self._create_tray_image(), APP_NAME, menu
                )
                self.tray_icon.run_detached(setup=setup)
                if not ready.wait(timeout=3):
                    raise RuntimeError("系统托盘启动超时")
                if setup_errors:
                    raise setup_errors[0]
                return True
            except Exception as exc:
                icon = self.tray_icon
                self.tray_icon = None
                if icon is not None:
                    self._stop_detached_tray_icon(icon)
                self._ui(self._log, f"创建系统托盘图标失败：{exc}")
                return False
    def _set_tray_visible(self, visible: bool) -> bool:
        """Synchronously set the tray icon visibility; False when no usable icon."""
        icon = self.tray_icon
        if icon is None:
            return False
        try:
            icon.visible = visible
            return bool(icon.visible) == visible
        except Exception:
            return False
    def _tray_visible(self) -> bool:
        """Whether the tray icon is currently shown (usable restore entry)."""
        icon = self.tray_icon
        return icon is not None and bool(icon.visible)
    def _start_execution_prewarm(self):
        """Prepare first-run-only resources without blocking the Tk thread."""
        if self.exiting:
            return
        prewarm_alert("run_start")
        # 托盘图标推到首屏绘制之后创建：启动瞬间不抢资源，窗口先可交互。
        self.root.after(800, self._start_tray_warmup)
    def _start_tray_warmup(self):
        if self.exiting:
            return

        def prepare_tray():
            if not self.exiting:
                # 启动即显示托盘图标：图标在 pystray 消息循环就绪时创建，
                # 比执行时才异步显示可靠得多，避免窗口藏进托盘后图标未显示
                # 造成"既无窗口又无托盘图标的隐藏进程"。
                self._ensure_tray(visible=True)

        self.tray_warmup_thread = threading.Thread(
            target=prepare_tray, name="MacroFlowTrayWarmup", daemon=True,
        )
        self.tray_warmup_thread.start()

        # OCR 引擎首次导入 paddle 全家可能耗时数十秒（杀软扫描外置目录时更久）。
        # 线程立刻创建（OCR 就绪等待要看到它，才能被 F12 中断），但先睡 5 秒，
        # 让窗口先把首屏绘制完，避免启动瞬间的 CPU/磁盘争抢。
        def prepare_ocr():
            time.sleep(5)
            if self.exiting:
                return
            self._ui(self._set_status, "OCR 引擎正在后台加载 · 可在执行小窗查看进度...", "warning")
            try:
                _get_engine()
            except Exception as exc:
                self._ui(self._log, f"OCR 引擎预加载失败（首次使用时会重试）：{exc}")
                return
            self.ocr_engine_ready = True
            self._ui(self._log, "OCR 引擎已就绪（离线文字识别可用）。")
            self._ui(self._set_status, "就绪", "success")

        set_progress_callback(
            lambda stage, percent: self._ui(self._on_ocr_progress, stage, percent),
        )
        self.ocr_warmup_thread = threading.Thread(
            target=prepare_ocr, name="MacroFlowOcrWarmup", daemon=True,
        )
        self.ocr_warmup_thread.start()
    def _wait_ocr_ready(self) -> bool:
        """等待 OCR 引擎就绪（可中断轮询）；返回 False 表示用户已请求停止。

        预加载线程导入期间（可能数十秒）轮询检查就绪标志、不抢初始化锁，
        因此 F12 随时能中止；没有预加载线程或预加载已结束但未就绪（加载
        失败）时立即返回 True，由调用方决定同步重试或继续。
        """
        warmup = getattr(self, "ocr_warmup_thread", None)
        workflow_stop = getattr(self, "workflow_stop", None)
        player = getattr(self, "player", None)
        player_stop = getattr(player, "stop_event", None) if player is not None else None
        while not getattr(self, "ocr_engine_ready", False):
            if (workflow_stop is not None and workflow_stop.is_set()) \
                    or (player_stop is not None and player_stop.is_set()):
                return False
            if warmup is None or not warmup.is_alive():
                return True
            if workflow_stop is not None:
                workflow_stop.wait(0.1)
            else:
                time.sleep(0.1)
        return True
    def _hotkey_wait_ocr_ready(self) -> bool:
        """快捷键播放器的 OCR 就绪等待：以快捷键播放器自己的停止信号为准。"""
        warmup = getattr(self, "ocr_warmup_thread", None)
        hotkey_player = getattr(self, "hotkey_player", None)
        stop = hotkey_player.stop_event if hotkey_player is not None else None
        while not getattr(self, "ocr_engine_ready", False):
            if stop is not None and stop.is_set():
                return False
            if warmup is None or not warmup.is_alive():
                return True
            time.sleep(0.1)
        return True
    def _ensure_ocr_ready(self) -> bool:
        """播放开始前确保 OCR 引擎已加载（可中断等待）。

        预加载在启动时后台进行；这里轮询等待其完成（不抢初始化锁），
        等待期间按 F12 可中止执行。预加载失败时同步重试一次。
        仅在脚本动作树确实用到文字识别时调用（见 _script_needs_ocr）。
        """
        if getattr(self, "ocr_engine_ready", False):
            return True
        self._ui(self._set_execution_progress, "正在加载 OCR 引擎 · 进度条显示加载阶段 · 按 F12 中止")
        self._ui(self._log, "脚本包含文字识别动作：等待 OCR 引擎加载（首次可能数十秒，按 F12 可中止）。")
        if not self._wait_ocr_ready():
            return False
        if not getattr(self, "ocr_engine_ready", False):
            try:
                _get_engine()
                self.ocr_engine_ready = True
            except Exception as exc:
                self._ui(self._log, f"OCR 引擎加载失败：{exc}")
        return not (self.workflow_stop.is_set() or self.player.stop_event.is_set())
    def _script_needs_ocr(self, actions, seen=None, seen_modules=None,
                          module_cache=None, depth=0) -> bool:
        """保守判断动作树是否依赖 OCR 引擎（文字识别）。

        任一分支用到 OCR 即返回 True：text_ocr 动作、文字识别全局守卫
        （recognize == "text"）、引用模块的成功/超时代码段、备用识别
        模块，以及递归展开的 script_ref 引用脚本。文件缺失/解析失败按
        最坏情况返回 True——宁可在播放开始前多等，也不把不可中断的
        引擎导入留到播放中途。纯键鼠/模板匹配脚本返回 False，可直接
        跳过 OCR 等待立即执行。
        """
        if depth > MAX_SCRIPT_REF_DEPTH or not actions:
            return False
        if seen is None:
            seen = set()
        if seen_modules is None:
            seen_modules = set()
        if module_cache is None:
            module_cache = {}
        for action in actions:
            if not isinstance(action, dict):
                continue
            kind = str(action.get("type", "")).strip()
            if kind in ("text_ocr", "ocr_compare"):
                return True
            if kind == "multi_condition_click" and any(
                isinstance(condition, dict)
                and condition.get("enabled")
                and condition.get("type") == "ocr"
                for condition in action.get("conditions", [])
            ):
                return True
            if kind == "row_list_condition_click" and any(
                isinstance(action.get(f"{side}_condition"), dict)
                and action[f"{side}_condition"].get("type") in {"text", "number"}
                for side in ("left", "right")
            ):
                return True
            # 任意动作/配置携带 recognize == "text" 都走 OCR 识别。
            if str(action.get("recognize", "")).strip() == "text":
                return True
            segment = action.get("failure_actions")
            if isinstance(segment, list) and self._script_needs_ocr(
                segment, seen, seen_modules, module_cache, depth + 1,
            ):
                return True
            for field in ("module_key", "fallback_module_key"):
                module_key = str(action.get(field, "")).strip()
                if not module_key or module_key in seen_modules:
                    continue
                seen_modules.add(module_key)
                if module_key not in module_cache:
                    try:
                        module_cache[module_key] = registered_module_object(module_key)
                    except Exception:
                        module_cache[module_key] = None
                if self._module_needs_ocr(
                    module_cache[module_key], seen, seen_modules, module_cache, depth,
                ):
                    return True
            if kind == "script_ref":
                script_value = str(action.get("script", "")).strip()
                if not script_value:
                    continue
                try:
                    script_path = resolve_path(script_value)
                    if not script_path.is_file():
                        continue
                    resolved = str(script_path.resolve())
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    if self._script_needs_ocr(
                        load_script(script_path).actions,
                        seen, seen_modules, module_cache, depth + 1,
                    ):
                        return True
                except Exception:
                    # 引用脚本解析失败：播放时必然报错，保守按需要 OCR 处理。
                    return True
        return False
    def _module_needs_ocr(self, module, seen, seen_modules, module_cache, depth=0) -> bool:
        """模块对象是否依赖 OCR：文字识别模块本体或成功/超时代码段。"""
        if not isinstance(module, dict):
            return False
        if str(module.get("recognize", "")).strip() == "text":
            return True
        for field in ("on_success_actions", "on_timeout_actions"):
            segment = module.get(field)
            if isinstance(segment, list) and self._script_needs_ocr(
                segment, seen, seen_modules, module_cache, depth,
            ):
                return True
        return False
    def _workflow_needs_ocr(self, steps, global_modules) -> bool:
        """工作流是否依赖 OCR：扫描全部步骤（脚本/模块）与全局检测模块。"""
        seen: set[str] = set()
        seen_modules: set[str] = set()
        module_cache: dict = {}
        for module in global_modules or []:
            if not isinstance(module, dict):
                continue
            if self._module_needs_ocr(module, seen, seen_modules, module_cache):
                return True
            config = module.get("config")
            if isinstance(config, dict) and self._script_needs_ocr(
                [config], seen, seen_modules, module_cache,
            ):
                return True
            script_value = str(module.get("script", "")).strip()
            if script_value:
                try:
                    script_path = resolve_path(script_value)
                    if script_path.is_file():
                        resolved = str(script_path.resolve())
                        if resolved not in seen:
                            seen.add(resolved)
                            if self._script_needs_ocr(
                                load_script(script_path).actions,
                                seen, seen_modules, module_cache, 1,
                            ):
                                return True
                except Exception:
                    return True
        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("kind") == "module":
                action = dict(step.get("action") or {})
                module_key = self._workflow_module_key(step)
                if module_key and not action.get("module_key"):
                    action["module_key"] = module_key
                if self._script_needs_ocr([action], seen, seen_modules, module_cache):
                    return True
                if module_key and module_key not in seen_modules:
                    seen_modules.add(module_key)
                    if module_key not in module_cache:
                        try:
                            module_cache[module_key] = registered_module_object(module_key)
                        except Exception:
                            module_cache[module_key] = None
                    if self._module_needs_ocr(
                        module_cache[module_key], seen, seen_modules, module_cache,
                    ):
                        return True
            else:
                script_value = str(step.get("script", "")).strip()
                if not script_value:
                    continue
                try:
                    script_path = resolve_path(script_value)
                    if not script_path.is_file():
                        continue
                    resolved = str(script_path.resolve())
                    if resolved in seen:
                        continue
                    seen.add(resolved)
                    if self._script_needs_ocr(
                        load_script(script_path).actions,
                        seen, seen_modules, module_cache, 1,
                    ):
                        return True
                except Exception:
                    return True
        return False
    @staticmethod
    def _stop_detached_tray_icon(icon) -> None:
        """Stop pystray and wait for its non-daemon detached loop thread."""
        try:
            icon.stop()
        except Exception:
            pass
        runner = getattr(icon, "_thread", None)
        if runner is not None and runner is not threading.current_thread():
            try:
                runner.join(timeout=3.0)
            except Exception:
                pass
    def _stop_tray(self):
        tray_lock = getattr(self, "_tray_lock", None)
        if tray_lock is None:
            tray_lock = threading.RLock()
            self._tray_lock = tray_lock
        with tray_lock:
            icon = self.tray_icon
            self.tray_icon = None
            if icon is not None:
                self._stop_detached_tray_icon(icon)
        warmup = getattr(self, "tray_warmup_thread", None)
        if warmup is not None and warmup is not threading.current_thread():
            try:
                warmup.join(timeout=3.0)
            except Exception:
                pass
        self.tray_warmup_thread = None
    def _tray_restore(self, _icon=None, _item=None):
        self._ui(self._restore_main_window)
    def _tray_exit(self, _icon=None, _item=None):
        self._ui(self._quit_app)
    def _hide_main_to_tray(self, for_recording: bool = False) -> bool:
        if not self._ensure_tray():
            return False
        # 先同步确认托盘图标已显示，再隐藏主窗口：图标不可用时拒绝隐藏，
        # 调用方退回普通隐藏并在执行结束后恢复主窗口，绝不留下不可达的进程。
        if not self._set_tray_visible(True):
            return False
        was_visible = self.root.state() != "withdrawn"
        if for_recording and was_visible:
            self.main_hidden_for_recording = True
        self.root.withdraw()
        self.main_hidden_to_tray = True
        return True
    def _restore_main_window(self):
        if self.recorder.running:
            # 录制期间主窗口保持隐藏：开了悬浮小窗就继续用小窗显示进度，
            # 没开就什么都不弹（用户正在别的窗口里操作，突然跳出的主界面会
            # 抢走前台，也会被录进去）。录制结束由 stop_recording 无条件恢复。
            if self.mini_window_enabled_var.get():
                self._show_operation_mini("recording")
            return
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self.main_hidden_to_tray = False
        self.main_hidden_for_recording = False
        if self.tray_icon is not None:
            self._set_tray_visible(False)
    def _hide_main_for_recording(self) -> bool:
        """录制期间隐藏主窗口；返回是否必须显示悬浮小窗（托盘不可用时的兜底）。

        录制是在**别的窗口上**做键鼠操作：主界面留在屏幕上既挡住要录的内容，
        又会被录进鼠标轨迹里（点到自己窗口的点击也会被录下来）。所以录制一开始
        就必须把主窗口移开，与「录制时显示悬浮小窗」那个选项无关——那个选项只
        决定要不要留一个不抢焦点的小窗看已录条数。

        主窗口收进托盘后，停止录制只剩 F8 可用；托盘图标建不出来时连 F8 以外
        的退路都没有了，此时强制显示悬浮小窗（它带「停止录制」按钮），保证用户
        任何时候都能停下来。
        """
        if self.root.state() == "withdrawn":
            # 已经在托盘里（或已被藏起来）：保持原状，结束后按同样状态恢复。
            self.main_hidden_for_recording = self.main_hidden_to_tray
            return False
        if not self._hide_main_to_tray(for_recording=True):
            # 托盘图标建不出来时也必须让开屏幕，结束时无条件恢复主窗口。
            self.root.withdraw()
            self.main_hidden_for_recording = True
            return True
        return False
    def _hide_main_for_execution(self):
        if self.root.state() == "withdrawn":
            self.execution_should_remain_in_tray = self.main_hidden_to_tray
            self.main_hidden_for_execution = True
            return
        self.execution_should_remain_in_tray = False
        if not self._hide_main_to_tray():
            # A tray icon is helpful, but execution must still be unobstructed
            # when the shell refuses to create one.
            self.root.withdraw()
        self.main_hidden_for_execution = True
    def _finish_execution_visibility(self):
        self.execution_progress_text = ""
        if not self.main_hidden_for_execution:
            return
        self.main_hidden_for_execution = False
        self._hide_execution_mini()
        if getattr(self, "execution_should_remain_in_tray", False):
            self.execution_should_remain_in_tray = False
            if self._tray_visible():
                self._log_tray_still_running()
                return
            self._restore_main_window()
            return
        # 执行结束后不再把主窗口抢回前台：托盘图标确认可见时保持隐藏在
        # 托盘，需要时用户通过托盘图标手动恢复；托盘图标不可用时必须
        # 恢复主窗口，避免出现既无窗口又无托盘图标的隐藏进程。
        if self.main_hidden_to_tray and self._tray_visible():
            self._log_tray_still_running()
            return
        self._restore_main_window()
    def _log_tray_still_running(self) -> None:
        """窗口留在托盘时明确告诉用户：软件没关，快捷键也还在生效。"""
        self._log(
            "主窗口已留在系统托盘，软件仍在运行：全局快捷键照旧生效；"
            "要显示界面请右键托盘图标选“显示窗口”，要彻底关闭请选“退出”。"
        )
    def open_folder(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)
