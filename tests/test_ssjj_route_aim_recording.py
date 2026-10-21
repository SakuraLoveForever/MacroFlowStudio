"""录制键鼠连续路线时按 F6 / 鼠标后侧键：这次锁定要写进路线，回放时同一时刻生效。

以前 F6 只是软件快捷键，路线里不留痕迹，回放时路线独占镜头、锁定也不会打开；
现在录制中每次切换锁定都在路线上补一条「光标锁定/解锁」动作。
"""
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_monitor import RouteCapture, SSJJMonitor
from tests.test_ssjj_waypoint_playback import state


class RouteAimStepCaptureTests(unittest.TestCase):
    def capture(self):
        """已经在关卡里录到第一帧：起点时刻记为 base。"""
        capture = RouteCapture(state())
        self.base = time.perf_counter()
        capture.feed(state(), when=self.base)
        capture.feed(state(60, tick=2), when=self.base + .5)
        return capture

    def test_lock_writes_a_route_action_at_the_current_time(self):
        capture = self.capture()
        step = capture.record_aim_step('boss', True, when=self.base + .7)
        self.assertEqual(step['type'], 'ssjj_aim')
        self.assertEqual(step['operation'], 'lock')
        self.assertEqual(step['target'], 'boss')
        self.assertTrue(step['route_script_action'])
        self.assertEqual(step['delay_ms'], 0)
        self.assertAlmostEqual(step['recorded_at_ms'], 700, delta=.001)
        self.assertTrue(step['action_id'])

    def test_unlocking_and_the_side_button_record_their_own_operation(self):
        capture = self.capture()
        self.assertEqual(capture.record_aim_step('boss', False)['operation'], 'unlock')
        self.assertEqual(capture.record_aim_step('competitive', True)['target'], 'competitive')

    def test_nothing_is_recorded_before_the_first_match_sample(self):
        capture = RouteCapture(None)
        self.assertIsNone(capture.record_aim_step('boss', True))
        self.assertEqual(capture.inputs, [])

    def test_the_recorded_step_keeps_the_recording_valid(self):
        capture = self.capture()
        capture.record_aim_step('boss', True, when=self.base + .3)
        action = capture.finish(truncated=True)
        self.assertEqual([step['type'] for step in action['inputs']], ['ssjj_aim'])
        self.assertTrue(action['inputs'][0]['route_script_action'])

    def test_the_recorded_step_survives_reordered_inputs(self):
        # 收尾时输入按时间排序：补录的锁定动作要和录制的键鼠排在同一条时间轴上。
        capture = self.capture()
        capture.record_input({'type': 'key', 'vk': 87, 'down': True, 'name': 'W'},
                             when=self.base + .48)
        capture.record_aim_step('boss', True, when=self.base + .3)
        action = capture.finish(truncated=True)
        self.assertEqual([step['recorded_at_ms'] for step in action['inputs']], [300.0, 480.0])
        self.assertEqual(action['inputs'][0]['operation'], 'lock')


class MonitorAimStepTests(unittest.TestCase):
    def monitor(self):
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = None
        return monitor

    def test_nothing_happens_when_no_route_is_recording(self):
        self.assertIsNone(self.monitor().record_aim_step('boss', True))

    def test_the_toggle_is_forwarded_to_the_active_recording(self):
        monitor = self.monitor()
        capture = Mock()
        monitor.capture = capture
        monitor.record_aim_step('competitive', True)
        capture.record_aim_step.assert_called_once_with('competitive', True)


if __name__ == '__main__':
    unittest.main()
