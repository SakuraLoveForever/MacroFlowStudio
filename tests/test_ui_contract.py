"""阶段 1/3/4 的边界检查：包入口、窗口尺寸目标、按需导入的依赖方向。

这些都不需要创建 Tk 窗口，也不导入整套界面实现。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))


class PackageEntryTests(unittest.TestCase):
    """包入口只转出主窗口类：不再聚合工具函数与实现。"""

    def test_app_package_entry_only_exports_the_app_class(self):
        import macroflow.ui.app as package

        exported = {name for name in dir(package) if not name.startswith("_")}
        # 只允许主窗口类与子模块名（导入子模块时会挂上）。
        allowed_extra = {
            "main", "base", "constants", "summaries", "startup", "shell",
            "helpers", "global_detect", "guards", "tray", "window_binding",
            "recording", "scripts", "execution", "workflow", "hotkeys",
            "script_edit", "annotations",
        }
        leaked = sorted(exported - {"MacroFlowApp"} - allowed_extra)
        self.assertEqual(
            leaked, [],
            f"macroflow.ui.app 又聚合了这些名字：{leaked}；"
            "调用方应从真正的定义模块导入",
        )

    def test_main_module_defines_the_app_class(self):
        from macroflow.ui.app.main import MacroFlowApp

        self.assertEqual(MacroFlowApp.__module__, "macroflow.ui.app.main")


class MainWindowSizeTests(unittest.TestCase):
    """窗口尺寸按逻辑像素设计，且随 DPI 缩放。"""

    def test_default_geometry_is_the_compact_target(self):
        from macroflow.ui.app.base import default_main_geometry
        from macroflow.ui.app.constants import TARGET_MAIN_HEIGHT, TARGET_MAIN_WIDTH

        width, height = default_main_geometry().split("x")
        self.assertLessEqual(int(width), TARGET_MAIN_WIDTH)
        self.assertLessEqual(int(height), TARGET_MAIN_HEIGHT)

    def test_minimum_size_is_usable_at_1024_logical_pixels(self):
        from macroflow.ui.app.constants import MIN_MAIN_HEIGHT, MIN_MAIN_WIDTH

        self.assertLessEqual(MIN_MAIN_WIDTH, 1024)
        self.assertLessEqual(MIN_MAIN_HEIGHT, 640)

    def test_geometry_never_exceeds_a_small_screen(self):
        """小屏（1024×768）上窗口不得超过屏幕，也不能小于最小尺寸。"""
        from unittest.mock import patch

        from macroflow.ui.app import base
        from macroflow.ui.app.constants import MIN_MAIN_WIDTH

        with patch.object(base, "get_virtual_screen_rect",
                          return_value={"left": 0, "top": 0, "width": 1024, "height": 768}):
            width, height = base.default_main_geometry().split("x")
        self.assertLessEqual(int(width), 1024)
        self.assertGreaterEqual(int(width), MIN_MAIN_WIDTH)
        self.assertLessEqual(int(height), 768)


if __name__ == "__main__":
    unittest.main()
