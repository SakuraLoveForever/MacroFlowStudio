"""A runtime route failure must not end its enclosing script/workflow."""
import unittest
from unittest.mock import patch

from macroflow.execution.player import MacroPlayer, PlaybackStopped
from macroflow.execution import ssjj_recording as route
from tests.test_ssjj_waypoint_playback import straight_recording


class RouteContinueTests(unittest.TestCase):
    def test_failed_route_stages_release_resources_and_execute_the_next_action(self):
        for stage in ('按键鼠标回放', '终点验收'):
            with self.subTest(stage=stage):
                player = MacroPlayer()
                logs = []
                player.on_log = logs.append
                next_action = {'type': 'comment', 'text': '继续工作流中的后续动作'}
                def fail(replay):
                    replay.stage = stage
                    raise RuntimeError('角色状态快照未更新，停止跑图')
                with patch.object(route, 'SSJJReader'), \
                        patch.object(route, 'ReplayInputs') as inputs, \
                        patch.object(route, 'RouteContext') as context, \
                        patch.object(route.RecordedReplay, 'run', autospec=True, side_effect=fail), \
                        patch.object(player, '_execute_action', wraps=player._execute_action) as execute:
                    inputs.return_value.sent_count = 1
                    player.play([straight_recording(), next_action])
                self.assertIn(next_action, [call.args[0] for call in execute.call_args_list])
                self.assertTrue(any(stage in text and '继续执行' in text for text in logs), logs)
                inputs.return_value.close.assert_called_once()
                context.return_value.close.assert_called_once()
                self.assertEqual(player._ssjj_recording_depth, 0)

    def test_explicit_stop_is_still_propagated_and_releases_resources(self):
        with patch.object(route, 'SSJJReader'), \
                patch.object(route, 'ReplayInputs') as inputs, \
                patch.object(route, 'RouteContext') as context, \
                patch.object(route.RecordedReplay, 'run', side_effect=PlaybackStopped()):
            with self.assertRaises(PlaybackStopped):
                route.run_recording_action(MacroPlayer(), straight_recording())
        inputs.return_value.close.assert_called_once()
        context.return_value.close.assert_called_once()
