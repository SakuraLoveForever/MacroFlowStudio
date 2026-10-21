from unittest import TestCase
from unittest.mock import Mock, patch
from macroflow.ui.app.main import MacroFlowApp


class DisplayRelayoutTests(TestCase):
    def test_monitor_changes_relayout_once_even_at_same_fitted_scale(self):
        for area, dpi in (
            ({'left': 1920, 'top': 0, 'width': 1920, 'height': 1040}, 96),
            ({'left': 0, 'top': 0, 'width': 1600, 'height': 860}, 96),
            ({'left': 0, 'top': 0, 'width': 1920, 'height': 1040}, 144),
        ):
            with self.subTest(area=area, dpi=dpi):
                app = MacroFlowApp.__new__(MacroFlowApp)
                app.root = Mock()
                app.root.winfo_id.return_value = 123
                app._display_work_area = {'left': 0, 'top': 0, 'width': 1920, 'height': 1040}
                app._display_dpi = 96
                app._ui_scaling_dpi = Mock(return_value=96)
                app._apply_display_dpi = Mock()
                app._adapt_execution_mini_position = Mock()
                with patch.dict(app._watch_display_dpi.__func__.__globals__, {
                    'get_window_dpi': Mock(return_value=dpi),
                    'get_monitor_work_area_for_window': Mock(return_value=area),
                }):
                    app._watch_display_dpi()
                    app._watch_display_dpi()
                app._apply_display_dpi.assert_called_once_with(96)
                app._adapt_execution_mini_position.assert_called_once_with(area)
