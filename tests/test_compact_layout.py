"""Compact layout checks without creating a Tk window."""
import re
from unittest import TestCase
from unittest.mock import patch

from macroflow.ui.app.constants import (
    MIN_MAIN_HEIGHT, MIN_MAIN_WIDTH, PAGE_PAD_X, RAIL_WIDTH,
    TARGET_MAIN_HEIGHT, TARGET_MAIN_WIDTH, WORKFLOW_TREE_COLUMNS,
)
from macroflow.ui.app.runtime.helpers import RuntimeHelpersMixin


class CompactLayoutTests(TestCase):
    def test_workflow_columns_fit_beside_the_rail(self):
        # Include both card edges and a visible vertical scrollbar.
        available = TARGET_MAIN_WIDTH - RAIL_WIDTH - PAGE_PAD_X * 2 - 28 - 16
        self.assertLessEqual(sum(column[2] for column in WORKFLOW_TREE_COLUMNS), available)

    def test_window_and_minimum_fit_monitor_work_area_at_fitted_dpi(self):
        app = RuntimeHelpersMixin()
        for width, height, dpi in (
            (1920, 1040, 96), (1366, 728, 144), (1024, 720, 120),
            (800, 560, 96), (2560, 1400, 192),
        ):
            with self.subTest(width=width, height=height, dpi=dpi):
                area = dict(left=-1920, top=40, width=width, height=height)
                fitted_dpi = app._fitted_display_dpi(dpi, area)
                scale = fitted_dpi / 96
                with patch('macroflow.ui.app.runtime.helpers.px',
                           side_effect=lambda value: round(value * scale)):
                    geometry = app._fixed_main_geometry(area)
                match = re.fullmatch(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', geometry)
                self.assertIsNotNone(match)
                win_width, win_height, left, top = map(int, match.groups())
                self.assertLessEqual(win_width, width)
                self.assertLessEqual(win_height + 32, height)
                self.assertGreaterEqual(win_width, round(MIN_MAIN_WIDTH * scale))
                self.assertGreaterEqual(win_height, round(MIN_MAIN_HEIGHT * scale))
                self.assertGreaterEqual(left, area['left'])
                self.assertGreaterEqual(top, area['top'])
                self.assertLessEqual(left + win_width, area['left'] + width)
                self.assertLessEqual(top + win_height + 32, area['top'] + height)

    def test_standard_window_does_not_fill_large_screens(self):
        app = RuntimeHelpersMixin()
        with patch('macroflow.ui.app.runtime.helpers.px', side_effect=lambda value: value):
            geometry = app._fixed_main_geometry(dict(left=0, top=0, width=1920, height=1040))
        self.assertTrue(geometry.startswith(f'{TARGET_MAIN_WIDTH}x{TARGET_MAIN_HEIGHT}+'))
