"""回放引擎包：MacroPlayer 由各功能 mixin 组合，模块级工具与常量在 base / control。

对外导入面与拆分前一致：from macroflow.execution.player import MacroPlayer, PlaybackStopped, ...
"""
from __future__ import annotations

from .control import (
    AdvanceToNextWorkflowStep,
    EndCurrentScriptRepeatRequest,
    EndCurrentScriptRequest,
    GuardJumpRequest,
    JumpToCurrentScriptLastAction,
    PlaybackStopped,
)
from .base import (
    CAPTURE_FAILURE_GRACE_S,
    CLICK_DEDUP_RADIUS_PX,
    CLICK_DEDUP_WINDOW_S,
    CLICK_SOURCE_ACTION,
    CLICK_SOURCE_GUARD,
    CLICK_SOURCE_MODULE,
    GUARD_SETTLE_MS,
    INPUT_ACTION_KINDS,
    JUMP_CURRENT_SCRIPT_LAST_RESULT,
    MAX_SCRIPT_REF_DEPTH,
    TH32CS_SNAPPROCESS,
    _PROCESSENTRY32W,
    elevated_taskkill,
    get_playback_screen_rect,
    is_process_running,
    running_process_names,
    scale_screen_point,
    screen_template_scale,
    taskkill_process,
)

from .core import CoreMixin
from .guards import GuardsMixin
from .window import WindowMixin
from .apps import AppsMixin
from .image import ImageMixin
from .modules import ModuleResultMixin
from .ocr import OcrMixin
from .row_list import RowListMixin

from .control import (  # 原先由本模块导入的名字，保持可导入
    annotations,
)
from .base import (  # 原先由本模块导入的名字，保持可导入
    activate_window,
    ctypes,
    get_cursor_pos,
    get_display_resolution_for_window,
    get_display_scaling_for_window,
    get_foreground_window_info,
    get_monitor_rect_for_window,
    get_primary_screen_rect,
    get_virtual_screen_rect,
    get_window_rect,
    is_window,
    is_window_process_foreground,
    resolve_window_signature,
    send_button,
    send_key,
    send_move_absolute,
    send_move_relative,
    send_scroll,
    send_text,
    set_cursor_pos,
    set_display_resolution_for_window,
    set_display_scaling_for_window,
    subprocess,
    wintypes,
)
from .core import (  # 原先由本模块导入的名字，保持可导入
    ACTION_ID_KEY,
    Callable,
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    END_CURRENT_SCRIPT_LABEL,
    NEXT_WORKFLOW_STEP_TARGET_ID,
    Path,
    PlaybackTimeline,
    SCRIPT_START_TARGET_ID,
    load_script,
    os,
    recorded_input_steps,
    registered_module_object,
    registered_template_region,
    resolve_path,
    script_ref_repeat_count,
    threading,
    time,
)
from .image import (  # 原先由本模块导入的名字，保持可导入
    CAPTURE_ERRORS,
    capture_bgr,
    extract_ocr_integer,
    find_expected_match,
    find_template,
    find_template_in_image,
    format_ocr_observation,
    load_image,
    matches_expected,
    ocr_match_center,
    parse_ocr_number_pair,
    recognize_image_with_boxes,
    recognize_region,
    recognize_region_with_boxes,
    show_overlay,
    stabilize_row_offsets,
)
from .row_list import (  # 原先由本模块导入的名字，保持可导入
    cv2,
)


class MacroPlayer(CoreMixin, GuardsMixin, WindowMixin, AppsMixin, ImageMixin, ModuleResultMixin, OcrMixin, RowListMixin):
    """脚本回放器：状态与动作分发在 core，各动作族按 mixin 分开。"""

