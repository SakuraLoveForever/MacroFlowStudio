"""阶段 1/3/4 的边界检查：包入口、窗口尺寸目标、按需导入的依赖方向。

这些都不需要创建 Tk 窗口，也不导入整套界面实现。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))


class PackageEntryTests(unittest.TestCase):
    """包入口只转出主窗口类：不再聚合工具函数与实现。"""

    def test_app_package_entry_does_not_aggregate_client_implementations(self):
        import macroflow.ui.app as package

        exported = {
            name for name, value in vars(package).items()
            if not name.startswith("_")
            and not (isinstance(value, ModuleType)
                     and value.__name__.startswith(package.__name__ + "."))
        }
        leaked = sorted(exported)
        self.assertEqual(
            leaked, [],
            f"macroflow.ui.app 又聚合了这些名字：{leaked}；"
            "调用方应从真正的定义模块导入",
        )

    def test_main_module_defines_the_app_class(self):
        from macroflow.ui.app.main import MacroFlowApp

        self.assertEqual(MacroFlowApp.__module__, "macroflow.ui.app.main")


class MainWindowSizeTests(unittest.TestCase):
    """窗口是固定尺寸的紧凑窗口，且随 DPI 缩放。"""

    def test_default_geometry_is_the_fixed_compact_size(self):
        from macroflow.ui.app.base import default_main_geometry, px
        from macroflow.ui.app.constants import TARGET_MAIN_HEIGHT, TARGET_MAIN_WIDTH

        self.assertEqual(
            default_main_geometry(),
            f"{px(TARGET_MAIN_WIDTH)}x{px(TARGET_MAIN_HEIGHT)}",
        )

    def test_minimum_size_is_usable_at_1024_logical_pixels(self):
        from macroflow.ui.app.constants import MIN_MAIN_HEIGHT, MIN_MAIN_WIDTH

        self.assertLessEqual(MIN_MAIN_WIDTH, 1024)
        self.assertLessEqual(MIN_MAIN_HEIGHT, 640)

    def test_fixed_window_fits_a_1024x768_screen(self):
        from macroflow.ui.app.base import default_main_geometry

        width, height = (int(value) for value in default_main_geometry().split("x"))
        self.assertLessEqual(width, 1024)
        self.assertLessEqual(height, 768)


if __name__ == "__main__":
    unittest.main()
