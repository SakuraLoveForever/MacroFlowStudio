import unittest
from unittest.mock import Mock, patch

from macroflow.execution.player import MacroPlayer


class GuardModuleLogTests(unittest.TestCase):
    def player(self):
        player = MacroPlayer()
        player.guard_settle_ms = 0
        player.on_log = Mock()
        player.on_trace_line = Mock()
        player._wait = Mock()
        player._click_module_point = Mock()
        player._play_guard_actions = Mock()
        player._execute_second_match = Mock()
        return player

    def test_each_click_event_identifies_the_triggering_module(self):
        player = self.player()
        for name in ('关闭广告', '确认奖励'):
            player.handle_guard_hit({'log_subject': f'模块[{name}] · 按钮.png',
                                     'click': (749, 677), 'button': 'left'})
        clicks = [call.args[0] for call in player.on_log.call_args_list]
        self.assertEqual(clicks, [
            '全局检测处理动作：模块[关闭广告] · 按钮.png · 点击 left @ (749, 677)。',
            '全局检测处理动作：模块[确认奖励] · 按钮.png · 点击 left @ (749, 677)。',
        ])

    def test_processing_details_include_module_for_each_action(self):
        player = self.player()
        subject = '模块[主线奖励] · 奖励.png'
        with patch('macroflow.execution.player.guards.is_window', return_value=True), \
                patch('macroflow.execution.player.guards.activate_window', return_value=True):
            player.handle_guard_hit({'log_subject': subject, 'delay_ms': 25, 'activation_hwnd': 123,
                'second': {'second_match_template': '确认.png'},
                'actions': [{'type': 'delay', 'ms': 10}, {'type': 'key_press', 'name': 'E', 'vk': 69}]})
        details = [call.args[0] for call in player.on_trace_line.call_args_list
                   if '处理动作' in call.args[0] or '处理段动作' in call.args[0]]
        self.assertEqual(len(details), 5)
        self.assertTrue(all(subject in text for text in details), details)

    def test_timeout_click_keeps_its_module_identity(self):
        player = self.player()
        player.handle_guard_hit({'kind': 'timeout', 'log_subject': '模块[等待主线] · 主线.png',
                                 'click': (1190, 213)})
        self.assertTrue(all('模块[等待主线]' in call.args[0] for call in player.on_log.call_args_list))
