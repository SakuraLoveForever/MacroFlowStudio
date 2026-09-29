"""Windows display mode and scaling option discovery."""
from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))
sys.path.insert(0, str(_PROJECT_ROOT))

import macroflow.input.wininput as wininput
import macroflow.core.resolution as resolution


class DisplaySettingsTests(unittest.TestCase):
    def test_display_mode_groups_keep_every_refresh_rate_per_resolution(self):
        group_modes = getattr(resolution, "group_display_modes", None)
        self.assertTrue(callable(group_modes), "缺少显示模式分组接口")

        grouped = group_modes([
            (1920, 1080, 60),
            (1280, 720, 60),
            (1920, 1080, 144),
            (1920, 1080, 60),
        ])

        self.assertEqual(grouped, [
            (1920, 1080, (60, 144)),
            (1280, 720, (60,)),
        ])

    def test_display_modes_are_unique_sorted_and_only_contain_usable_modes(self):
        get_modes = getattr(wininput, "get_display_modes_for_window", None)
        self.assertTrue(callable(get_modes), "缺少显示器模式枚举接口")
        raw_modes = [
            (1920, 1080, 144, 32),
            (1280, 720, 60, 32),
            (1920, 1080, 60, 32),
            (1920, 1080, 144, 32),
            (800, 600, 60, 8),
            (1024, 768, 1, 32),
        ]

        def enum_mode(_device, index, pointer):
            if index >= len(raw_modes):
                return False
            width, height, hz, bits = raw_modes[index]
            mode = ctypes.cast(pointer, ctypes.POINTER(wininput._DEVMODEW)).contents
            mode.dmPelsWidth = width
            mode.dmPelsHeight = height
            mode.dmDisplayFrequency = hz
            mode.dmBitsPerPel = bits
            return True

        with patch.object(
                wininput, "get_display_device_name_for_window",
                return_value=r"\\.\DISPLAY2",
        ), patch.object(wininput.user32, "EnumDisplaySettingsW", side_effect=enum_mode):
            modes = get_modes(123)

        self.assertEqual(modes, [
            (1280, 720, 60),
            (1920, 1080, 60),
            (1920, 1080, 144),
        ])

    def test_scaling_options_are_limited_to_the_selected_monitor_range(self):
        get_options = getattr(wininput, "get_display_scaling_options_for_window", None)
        self.assertTrue(callable(get_options), "缺少显示器缩放选项枚举接口")
        adapter_id = wininput._LUID()

        with patch.object(
                wininput, "get_display_device_name_for_window",
                return_value=r"\\.\DISPLAY2",
        ), patch.object(
                wininput, "_display_config_source_for_device",
                return_value=(adapter_id, 7),
        ), patch.object(
                wininput, "_display_scale_info",
                return_value=(150, 100, 150, 300),
        ):
            options = get_options(123)

        self.assertEqual(options, (100, 125, 150, 175, 200, 225, 250, 300))

    def test_resolution_change_rejects_a_mode_the_monitor_does_not_report(self):
        get_modes = getattr(wininput, "get_display_modes_for_window", None)
        self.assertTrue(callable(get_modes), "缺少显示器模式枚举接口")

        with patch.object(
                wininput, "get_display_device_name_for_window",
                return_value=r"\\.\DISPLAY1",
        ), patch.object(
                wininput, "get_display_modes_for_window",
                return_value=[(1920, 1080, 60)],
        ), patch.object(wininput.user32, "ChangeDisplaySettingsExW") as change:
            changed = wininput.set_display_resolution_for_window(123, 2560, 1440, 60)

        self.assertFalse(changed)
        change.assert_not_called()

    def test_resolution_change_tests_then_applies_and_verifies_the_mode(self):
        get_modes = getattr(wininput, "get_display_modes_for_window", None)
        self.assertTrue(callable(get_modes), "缺少显示器模式枚举接口")

        def current_mode(_device, _index, pointer):
            mode = ctypes.cast(pointer, ctypes.POINTER(wininput._DEVMODEW)).contents
            mode.dmPelsWidth = 2560
            mode.dmPelsHeight = 1440
            mode.dmDisplayFrequency = 60
            mode.dmBitsPerPel = 32
            return True

        with patch.object(
                wininput, "get_display_device_name_for_window",
                return_value=r"\\.\DISPLAY1",
        ), patch.object(
                wininput, "get_display_modes_for_window",
                return_value=[(1920, 1080, 60), (1920, 1080, 144)],
        ), patch.object(
                wininput.user32, "EnumDisplaySettingsW", side_effect=current_mode,
        ), patch.object(
                wininput.user32, "ChangeDisplaySettingsExW", return_value=0,
        ) as change, patch.object(
                wininput, "get_display_resolution_for_window",
                return_value=(1920, 1080, 60),
        ):
            changed = wininput.set_display_resolution_for_window(123, 1920, 1080, 60)

        self.assertTrue(changed)
        self.assertEqual(
            [item.args[3] for item in change.call_args_list],
            [0x00000002, wininput.CDS_UPDATEREGISTRY],
        )

    def test_resolution_verification_accepts_59_hz_driver_alias_for_60_hz(self):
        def current_mode(_device, _index, pointer):
            mode = ctypes.cast(pointer, ctypes.POINTER(wininput._DEVMODEW)).contents
            mode.dmPelsWidth = 2560
            mode.dmPelsHeight = 1440
            mode.dmDisplayFrequency = 60
            mode.dmBitsPerPel = 32
            return True

        with patch.object(
                wininput, "get_display_device_name_for_window",
                return_value=r"\\.\DISPLAY1",
        ), patch.object(
                wininput, "get_display_modes_for_window",
                return_value=[(1920, 1080, 60)],
        ), patch.object(
                wininput.user32, "EnumDisplaySettingsW", side_effect=current_mode,
        ), patch.object(
                wininput.user32, "ChangeDisplaySettingsExW", return_value=0,
        ), patch.object(
                wininput, "get_display_resolution_for_window",
                return_value=(1920, 1080, 59),
        ):
            changed = wininput.set_display_resolution_for_window(123, 1920, 1080, 60)

        self.assertTrue(changed)


if __name__ == "__main__":
    unittest.main()
