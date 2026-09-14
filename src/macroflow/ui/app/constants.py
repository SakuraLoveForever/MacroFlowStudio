from __future__ import annotations


APP_NAME = "MacroFlow Studio"
APP_VERSION = "1.0.0"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
WINDOWS_RUN_VALUE = "MacroFlowStudio"
DEFAULT_GLOBAL_CLICK_DELAY_MS = 1000
MAX_TREE_ROWS = 20_000
BACKUP_INTERVAL_CHOICES = ("1h", "1天", "1周")
BACKUP_INTERVAL_MS = {
    "1h": 60 * 60 * 1000,
    "1天": 24 * 60 * 60 * 1000,
    "1周": 7 * 24 * 60 * 60 * 1000,
}
FLOATING_NOTICE_POSITIONS = ("左上", "顶部居中", "右上", "左下", "底部居中", "右下")
SCRIPT_CATEGORY_VALUES = ("关卡", "关卡封装", "切换", "方向")
FLOATING_NOTICE_WIDTH = 360
FLOATING_NOTICE_HEIGHT = 68
MIN_MAIN_WIDTH = 1180
MIN_MAIN_HEIGHT = 640
COLOR_BG = "#0E1419"
COLOR_SIDEBAR = "#131B22"
COLOR_SURFACE = "#182129"
COLOR_SURFACE_ALT = "#1D2831"
COLOR_BORDER = "#2D3943"
COLOR_TEXT = "#E8EDF2"
COLOR_MUTED = "#94A1AD"
COLOR_BLUE = "#2F80ED"
COLOR_RED = "#E04444"
COLOR_GREEN = "#18A66F"
COLOR_HOVER = "#1E2A35"
COLOR_FOCUS = "#2F80ED"
FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"
FONT_SMALL = 8
FONT_BODY = 9
FONT_SUBTITLE = 10
FONT_TITLE = 12
FONT_BRAND = 15
ACTION_TREE_COLUMNS = (
    ("index", "#", 46, "center"), ("kind", "动作", 96, "w"),
    ("detail", "参数", 420, "w"), ("delay", "执行前延时", 92, "center"),
)
RECORD_TOOLBAR_BUTTON_LABEL = "⏺ 录制"
ACTION_ICONS = {
    "delay": "◷",
    "key": "⌨",
    "key_press": "⌨",
    "text": "T",
    "mouse_move": "↖",
    "mouse_button": "◉",
    "click": "◉",
    "repeat_click": "↻",
    "turn": "↺",
    "scroll": "↕",
    "image_match": "▣",
    "ocr_compare": "⇄",
    "multi_condition_click": "⊞",
    "row_list_condition_click": "▤",
    "notice": "i",
    "comment": "≡",
    "script_ref": "⇄",
    "open_app": "▶",
    "close_app": "✕",
    "set_resolution": "▣",
    "jump": "⇢",
    "jump_current_script_last": "⇥",
    "block": "⏸",
    "recorded_input": "⏺",
}
WORKFLOW_TREE_COLUMNS = (
    ("index", "步骤", 50, "center"), ("script", "脚本 / 模块", 320, "w"),
    ("repeat", "执行次数", 76, "center"), ("before", "开始前等待", 92, "center"),
    ("interval", "重复间隔", 92, "center"), ("enabled", "状态", 64, "center"),
)
GLOBAL_TREE_COLUMNS = (
    ("index", "步骤", 50, "center"),
    ("module", "全局检测模块", 420, "w"),
    ("status", "状态", 80, "center"),
)
SCRIPT_CATEGORY_LABELS = {
    "level": "关卡", "level_pack": "关卡封装",
    "switch": "切换", "direction": "方向",
}
MODULE_CLICK_LABELS = {
    "click_match": "点击识别位置",
    "click_custom": "点击自定义位置",
    "second_match": "二次识别后点击",
}
REGION_LABELS = {
    "screen": "全屏", "window": "绑定窗口", "template": "模板区域",
}
EVENT_LOG_HEADER = (
    "# 事件日志：脚本/工作流边界、识别命中、输入与窗口状态变化、异常。\n"
    "# 30 秒内完全相同的消息合并成一行并标注次数；逐行执行细节见同目录 "
    "MacroFlow_trace_*.log。\n"
)
TRACE_LOG_HEADER = (
    "# 执行明细：先写一行 ▶ 层级标题（工作流第几步 · 哪个脚本 / 代码段 · 第几次重复），\n"
    "# 下面每执行一行脚本动作写一条：时间 | 行号 | 动作参数 | 等待/耗时。\n"
    "# 模块/识别内部细节以 “└ ” 开头，只在实际发生时写；等待≥100ms 或耗时≥50ms 才写时间。\n"
)
