"""测试共享基座：统一导入面 + 跨文件共用的测试夹具。

全量测试：python -m unittest discover -s tests -t .；单个文件：python tests/test_xxx.py。"""
from __future__ import annotations

import sys
from pathlib import Path

# 源码包位于项目根/src：从 tests/ 运行时把项目根与 src 加入导入路径，
# 才能解析 macroflow.* 包与 tests.common。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "src"))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import ctypes
import inspect
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, call, patch

import cv2
import numpy as np
import ttkbootstrap
import ttkbootstrap.publisher
import macroflow.core.display_power as display_power_module
import macroflow.core.image_match as image_match_module
import macroflow.input.input_guard as input_guard_module
import macroflow.input.wininput as wininput_module
import macroflow.ui.dialogs as dialog_module

from macroflow.core.alerts import play_alert
from macroflow.ui.app.main import (
    BACKUP_INTERVAL_CHOICES, BACKUP_INTERVAL_MS, MacroFlowApp,
    RECORD_TOOLBAR_BUTTON_LABEL,
    SEGMENT_BAR,
    action_summary, coordinate_scale_summary,
    disable_combobox_wheel_selection,
    key_action_matches,
    set_matching_key_action_delays,
    floating_notice_xy,
    windows_startup_command, workflow_execution_progress, workflow_script_name,
    spawn_new_instance,
)
from macroflow.ui.dialogs import (
    KEY_HINT_CAPTURING, BatchModuleScriptDialog, ClickDialog, CloseAppDialog, GlobalDetectDialog,
    DurationVar, ImageActionDialog, JumpActionDialog, KeyActionDialog, ModalDialog,
    ModulePickerDialog, ModuleReferenceDelayDialog, MultiConditionClickDialog,
    MouseMoveDialog, OcrActionDialog, OcrCompareActionDialog, OpenAppDialog, RepeatClickDialog, RestartWorkflowTargetDialog,
    ScreenPointPicker, ScriptRefDialog, ScrollDialog,
    ScreenOffsetPicker, ScreenRegionPicker, ScriptDirectoriesDialog, SetResolutionActionDialog, TemplateRegionFormDialog,
    TemplateRegionManagerDialog, TextActionDialog, activate_main_after_modal, ancestor_windows,
    drag_selection_region, edit_action,
    configure_module_list_scrollbar,
    configure_module_tree_styles,
    FONT_FAMILY, FONT_SUBTITLE, px,
    fallback_template_options, fit_window_to_content,
    image_action_option_defaults, image_click_target_defaults,
    image_found_jump_target_options, image_jump_target_options,
    image_timeout_option_label, image_timeout_option_value,
    image_timeout_option_defaults, key_to_vk,
    module_action_for_key, module_manager_label, module_manager_selection_colors,
    module_manager_special_action_summary, module_manager_tag,
    pinyin_sort_key, prepend_module_to_scripts,
    remove_module_from_scripts, script_category_for_path,
    registered_template_options, restart_workflow_row_options, select_jump_target_label, vk_to_key_name,
    restore_modal_after_overlay, show_floating_notice,
    SegmentEditorMixin, RecordedInputDialog,
    recorded_action_description,
    segment_action_is_blocking, segment_row_label,
    selectable_target_windows,
)
from macroflow.core.image_match import find_template, find_template_in_image
from mss.exception import ScreenShotError
from macroflow.core.ocr import (
    extract_ocr_integer, find_expected_match, format_ocr_observation, matches_expected,
    parse_ocr_number_pair, recognize_image_with_boxes,
)
from macroflow.input.input_guard import (
    FocusInputGuard, InputCapturer, KBDLLHOOKSTRUCT, KeyCapturer, LLKHF_INJECTED,
    LLMHF_INJECTED, RESERVED_HOTKEY_VKS, VK_ESCAPE, VK_F12, VK_F9,
    MSLLHOOKSTRUCT, MouseCapturer, WH_KEYBOARD_LL, WH_MOUSE_LL, WM_KEYDOWN,
    WM_LBUTTONDOWN, WM_RBUTTONDOWN,
    WM_SYSKEYDOWN, should_block_keyboard, should_block_mouse,
)
from macroflow.core.models import (
    ACTION_ID_KEY, DEFAULT_MOUSE_MOVE_INTERVAL_MS, DEFAULT_RECORDED_SCREEN,
    RECORDED_INPUT_STEPS_KEY, RECORDED_INPUT_TYPE,
    DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
    NEXT_WORKFLOW_STEP_TARGET_ID, SCRIPT_START_TARGET_ID,
    SCROLL_DOWN_LABEL, SCROLL_UP_LABEL, MacroScript, Workflow,
    clone_actions_with_new_ids, ensure_action_ids,
    ensure_workflow_step_ids, is_global_script,
)
from macroflow.execution.player import (
    AdvanceToNextWorkflowStep, CLICK_DEDUP_WINDOW_S, CLICK_SOURCE_ACTION,
    CLICK_SOURCE_GUARD, CLICK_SOURCE_MODULE, EndCurrentScriptRequest,
    GUARD_SETTLE_MS, GuardJumpRequest, JUMP_CURRENT_SCRIPT_LAST_RESULT,
    MacroPlayer, PlaybackStopped,
    get_playback_screen_rect, scale_screen_point, screen_template_scale,
)
from macroflow.core.resolution import (
    DEFAULT_RESOLUTION_STYLES, normalize_resolution_styles,
    build_resolution_action, resolve_resolution_style,
)
from macroflow.execution.detection_worker import DetectionEvaluation, DetectionResult, DetectionWorker
from macroflow.execution.timeline import PlaybackTimeline
from macroflow.input.rawinput import RawMouseListener
from macroflow.input.recorder import MacroRecorder
from macroflow.core.storage import (
    BASE_DIR, available_script_path, backup_script, display_path, load_app_settings,
    load_module_images_dir, load_module_objects, load_script,
    load_template_regions, load_workflow,
    migrate_workflow_templates,
    module_image_inventory,
    registered_module_object, registered_template_region, remap_hotkey_script_bindings,
    resolve_path, save_app_settings, save_script,
    save_module_images_dir, save_module_objects,
    save_template_regions, save_workflow,
)
from macroflow.input.wininput import (
    DWMWA_WINDOW_CORNER_PREFERENCE, MACROFLOW_INPUT_TAG, WindowInfo, activate_window,
    force_english_input, is_cursor_near_window_center, resolve_window_signature,
    send_move_relative, set_dark_titlebar, set_display_scaling_for_window,
    set_input_dispatcher, show_window,
    show_window_no_activate,
)


class FakeBooleanVar:
    """Links set()/get() like a real Tk BooleanVar (set only updates get)."""

    def __init__(self, value: bool = False):
        self._value = bool(value)

    def get(self) -> bool:
        return self._value

    def set(self, value: bool) -> None:
        self._value = bool(value)


_PACKAGE_SUBMODULES = {
    "macroflow.ui.dialogs": (
        "base", "helpers", "screen_pickers", "segments",
        "module_objects", "actions", "recognition", "app_dialogs",
    ),
    "macroflow.ui.app": (
        "constants", "base", "summaries", "startup", "shell", "helpers",
        "global_detect", "guards", "tray", "window_binding", "recording",
        "scripts", "execution", "workflow", "hotkeys",
    ),
    "macroflow.execution.player": (
        "control", "base", "core", "guards", "window", "apps", "image",
        "modules", "ocr", "row_list",
    ),
}


def _package_modules(package_name: str):
    """返回包本身 + 它的全部实现子模块。"""
    import importlib
    package = importlib.import_module(package_name)
    out = [package]
    for name in _PACKAGE_SUBMODULES[package_name]:
        out.append(importlib.import_module(package_name + "." + name))
    return out


class _PackagePatch:
    """把一个名字在包里及其所有子模块里同时替换成同一个 Mock。

    界面模块按功能拆包后（macroflow.ui.dialogs / macroflow.ui.app），同一个名字
    （show_floating_notice、resolve_path、MacroFlowApp 的若干依赖…）在多个子模块
    里各有一份绑定；只 patch 包属性会打偏，拦不住真正的实现模块，所以统一替换。
    """

    def __init__(self, package_name, name, *args, **kwargs):
        self.package_name = package_name
        self.name = name
        # 与 mock.patch 一致：第一个位置参数是 new（替换值），其余走关键字。
        self.new = args[0] if args else None
        self.kwargs = kwargs

    def __enter__(self):
        self._mock = self.new if self.new is not None else Mock(**self.kwargs)
        self._stack = ExitStack()
        for module in _package_modules(self.package_name):
            if hasattr(module, self.name):
                self._stack.enter_context(
                    patch.object(module, self.name, new=self._mock),
                )
        return self._mock

    def start(self):
        """支持手工 start()/stop()（与 unittest.mock.patch 同样的用法）。"""
        return self.__enter__()

    def stop(self):
        self.__exit__(None, None, None)

    def __exit__(self, *exc_info):
        return self._stack.__exit__(*exc_info)


def patch_dialogs(name, *args, **kwargs):
    """替代 patch("macroflow.ui.dialogs.<name>")：包与所有子模块共用同一个 Mock。"""
    return _PackagePatch("macroflow.ui.dialogs", name, *args, **kwargs)


def patch_app(name, *args, **kwargs):
    """替代 patch("macroflow.ui.app.<name>")：包与所有子模块共用同一个 Mock。"""
    return _PackagePatch("macroflow.ui.app", name, *args, **kwargs)


def patch_player(name, *args, **kwargs):
    """替代 patch("macroflow.execution.player.<name>")：包与所有子模块共用同一个 Mock。"""
    return _PackagePatch("macroflow.execution.player", name, *args, **kwargs)



class FakeSettingVar:
    """set()/get() 桩，模拟 Tk StringVar 的读写（不依赖 Tk）。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
