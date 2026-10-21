"""键鼠连续路线执行期间自动放出游戏监测浮窗。

执行开始时（``_on_action_start`` 收到 ``ssjj_recording``）自动显示，执行结束
（``_finish_execution_visibility``）只收回这次由执行自动打开的那一个；用户自己
打开的浮窗始终不受影响。这里只用 mock 验证挂点与标记，不创建 Tk 窗口。
"""
import unittest
from unittest.mock import Mock


class RoutePanelAutoShowTests(unittest.TestCase):
    def app(self):
        from macroflow.ui.app.main import MacroFlowApp
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._ssjj_panel_auto_opened = False
        return app

    def test_route_action_start_opens_the_panel(self):
        app = self.app()
        app._ui = lambda callback, *args: callback(*args)
        app.mini_action_var = Mock()
        app.show_ssjj_panel_for_route = Mock()
        app._on_action_start(dict(script="工业3跑图", index=1, total=3,
                                  action=dict(type="ssjj_recording", map_id=100003)))
        app.show_ssjj_panel_for_route.assert_called_once_with()

    def test_other_actions_do_not_touch_the_panel(self):
        app = self.app()
        app._ui = lambda callback, *args: callback(*args)
        app.mini_action_var = Mock()
        app.show_ssjj_panel_for_route = Mock()
        app._on_action_start(dict(script="工业3跑图", index=0, total=3,
                                  action=dict(type="wait", delay_ms=500)))
        app.show_ssjj_panel_for_route.assert_not_called()

    def test_route_start_creates_the_panel_and_marks_it_auto_opened(self):
        from macroflow.ui.app.scripts import ScriptsMixin
        app = self.app()
        app._ssjj_coordinates = None
        created = Mock()
        app._ensure_ssjj_coordinate_panel = Mock(
            side_effect=lambda: setattr(app, '_ssjj_coordinates', created))
        ScriptsMixin.show_ssjj_panel_for_route(app)
        app._ensure_ssjj_coordinate_panel.assert_called_once_with()
        self.assertIs(app._ssjj_coordinates, created)
        self.assertTrue(app._ssjj_panel_auto_opened)

    def test_a_user_panel_stays_open_across_the_run(self):
        from macroflow.ui.app.scripts import ScriptsMixin
        app = self.app()
        panel = Mock()
        panel.window.winfo_exists.return_value = True
        app._ssjj_coordinates = panel
        app._ensure_ssjj_coordinate_panel = Mock()
        ScriptsMixin.show_ssjj_panel_for_route(app)
        panel.show.assert_called_once_with()
        app._ensure_ssjj_coordinate_panel.assert_not_called()
        self.assertFalse(app._ssjj_panel_auto_opened)

    def test_manual_open_makes_the_panel_a_user_panel(self):
        from macroflow.ui.app.scripts import ScriptsMixin
        app = self.app()
        app._ssjj_panel_auto_opened = True
        app._ensure_ssjj_coordinate_panel = Mock()
        ScriptsMixin.open_ssjj_coordinates(app)
        app._ensure_ssjj_coordinate_panel.assert_called_once_with()
        self.assertFalse(app._ssjj_panel_auto_opened)

    def test_run_end_closes_the_auto_opened_panel(self):
        from macroflow.ui.app.scripts import ScriptsMixin
        app = self.app()
        panel = Mock(closed=False)
        app._ssjj_coordinates = panel
        app._ssjj_panel_auto_opened = True
        ScriptsMixin.close_ssjj_panel_after_run(app)
        panel.close.assert_called_once_with()
        self.assertFalse(app._ssjj_panel_auto_opened)

    def test_run_end_leaves_a_user_panel_alone(self):
        from macroflow.ui.app.scripts import ScriptsMixin
        app = self.app()
        panel = Mock(closed=False)
        app._ssjj_coordinates = panel
        ScriptsMixin.close_ssjj_panel_after_run(app)
        panel.close.assert_not_called()

    def test_execution_end_hook_closes_the_auto_opened_panel(self):
        from macroflow.ui.app.tray import TrayMixin
        app = self.app()
        app.player = None
        app.main_hidden_for_execution = False
        app.close_ssjj_panel_after_run = Mock()
        TrayMixin._finish_execution_visibility(app)
        app.close_ssjj_panel_after_run.assert_called_once_with()
