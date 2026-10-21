"""游戏外开始/结束路线录制，以及末尾采样中断时的收尾。

停止不要求游戏在前台（读内存与焦点无关，前台只决定要不要把实体输入录进去），
中断过但游戏再没发布新状态时按已录数据收尾；只有「中断之后游戏又推进了」才说明
断档落在路线中间、后面的输入已经漏掉，才必须整条作废。
"""
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from tests.helpers.core import add_src_to_path
from macroflow.execution.ssjj_monitor import RouteCapture, SSJJMonitor
from macroflow.execution.ssjj_recording import validate_recording
from tests.test_ssjj_waypoint_playback import state

READ_FAILURE = '游戏状态读取失败；游戏可能已退出或切换场景'


class TruncatedCaptureTests(unittest.TestCase):
    def interrupted_capture(self):
        capture = RouteCapture(state())
        base = time.perf_counter()
        capture.feed(state(), when=base)
        capture.feed(state(120, tick=2), when=base + .5)
        capture.mark(when=base + .5)
        capture.feed_keys({87: True}, when=base + .5)
        capture.feed_keys({87: False}, when=base + .7)
        self.assertIn('按键采样中断', capture.error)
        return capture

    def test_finish_can_close_a_recording_that_ended_at_the_last_sample(self):
        capture = self.interrupted_capture()
        action = capture.finish(truncated=True)
        validate_recording(action)
        self.assertEqual(action['duration_ms'], capture.trajectory[-1]['at_ms'])
        self.assertEqual(action['trajectory'][-1]['position'], [120, 0, 0])
        self.assertEqual([mark['gap_ms'] for mark in action['marks']], [0, 500])

    def test_live_finish_still_refuses_an_interrupted_recording(self):
        capture = self.interrupted_capture()
        with self.assertRaisesRegex(RuntimeError, '按键采样中断'):
            capture.finish()

    def test_finish_covers_inputs_recorded_after_the_last_state_sample(self):
        """游戏停更后按键还在录：时间轴要补到停止这一刻，否则校验报「无效的路线输入时间」。"""
        capture = RouteCapture(state())
        base = time.perf_counter()
        capture.feed(state(), when=base - .5)
        capture.feed(state(120, tick=2), when=base - .2)
        capture.record_input({'type': 'key', 'vk': 69, 'name': 'E', 'down': True}, when=base - .1)
        capture.record_input({'type': 'key', 'vk': 69, 'name': 'E', 'down': False}, when=base)
        action = capture.finish(truncated=True)
        validate_recording(action)
        self.assertGreaterEqual(action['duration_ms'],
                                max(event['recorded_at_ms'] for event in action['inputs']))
        self.assertEqual([event['vk'] for event in action['inputs']], [69, 69])


class StopRecordingTests(unittest.TestCase):
    def monitor(self, *, reader, interrupted=False):
        monitor = SSJJMonitor.__new__(SSJJMonitor)
        monitor.lock = threading.Lock()
        monitor.capture = monitor.input_recorder = None
        monitor.latest, monitor.updated, monitor.error = state(), time.monotonic(), ''
        monitor.stop_note, monitor.reader, monitor.exclude_rect = '', reader, None
        monitor._start_key_sampling = Mock()
        monitor._stop_key_sampling = Mock()
        self.addCleanup(monitor.cancel_recording)
        with patch('macroflow.input.recorder.keyboard.Listener'), patch(
                'macroflow.input.recorder.mouse.Listener'):
            monitor.start_recording()
        capture = monitor.capture
        base = time.perf_counter()
        capture.feed(state(), when=base - .5)
        capture.feed(state(120, tick=2), when=base - .2)
        capture.mark(when=base - .2)
        if interrupted:
            # 按键采样中断 200 ms（>100 ms）：录制被标记失败，最后记录的是 tick=2 的状态。
            capture.feed_keys({87: True}, when=base - .2)
            capture.feed_keys({87: False}, when=base)
            self.assertIn('按键采样中断', capture.error)
        return monitor

    def test_stop_from_outside_the_game_does_not_require_or_take_focus(self):
        reader = Mock()
        reader.snapshot.return_value = state(160, tick=3)
        monitor = self.monitor(reader=reader)
        # 读取线程正在重建、坐标显示是旧的，也不该拦住停止。
        monitor.updated = time.monotonic() - 5
        action = monitor.stop_recording()
        validate_recording(action)
        self.assertEqual(action['trajectory'][-1]['position'], [160, 0, 0])
        self.assertEqual(monitor.stop_note, '')
        reader.require_foreground.assert_not_called()
        self.assertIsNone(monitor.capture)
        monitor._stop_key_sampling.assert_called_once()

    def test_stop_reads_a_fresh_reader_while_the_read_loop_rebuilds(self):
        # 读取线程读失败后会丢掉 reader 并等 1 秒再试：停止不能因此卡住或误判中断。
        reader = Mock()
        reader.snapshot.return_value = state(160, tick=3)
        monitor = self.monitor(reader=None)
        monitor.updated = time.monotonic() - 5
        with patch('macroflow.execution.ssjj_monitor.SSJJReader', return_value=reader):
            action = monitor.stop_recording()
        validate_recording(action)
        self.assertEqual(action['trajectory'][-1]['position'], [160, 0, 0])
        self.assertEqual(monitor.stop_note, '')
        reader.close.assert_called_once_with()

    def test_stop_keeps_the_recording_when_the_battle_client_is_gone(self):
        monitor = self.monitor(reader=None, interrupted=True)
        with patch('macroflow.execution.ssjj_monitor.SSJJReader',
                   side_effect=RuntimeError('需要且只能运行一个 SSJJ_BattleClient_Unity.exe')):
            action = monitor.stop_recording()
        validate_recording(action)
        self.assertIn('末尾中断', monitor.stop_note)
        self.assertIsNone(monitor.capture)

    def test_stop_keeps_the_recording_when_the_game_can_no_longer_be_read(self):
        reader = Mock()
        reader.snapshot.side_effect = RuntimeError(READ_FAILURE)
        monitor = self.monitor(reader=reader, interrupted=True)
        action = monitor.stop_recording()
        validate_recording(action)
        self.assertIn('末尾中断', monitor.stop_note)
        reader.require_foreground.assert_not_called()

    def test_a_game_that_stopped_updating_outside_the_game_ends_at_the_last_frame(self):
        reader = Mock()
        reader.snapshot.return_value = state(120, tick=2)      # 失焦后不再更新：还是最后一帧
        monitor = self.monitor(reader=reader)
        action = monitor.stop_recording()
        validate_recording(action)
        self.assertEqual(action['trajectory'][-1]['position'], [120, 0, 0])
        self.assertIn('没有读到新的游戏状态', monitor.stop_note)
        self.assertIsNone(monitor.capture)

    def test_a_mid_route_gap_while_the_game_kept_running_is_still_refused(self):
        reader = Mock()
        reader.snapshot.return_value = state(240, tick=4)      # 断档期间游戏又推进了
        monitor = self.monitor(reader=reader, interrupted=True)
        with self.assertRaisesRegex(RuntimeError, '按键采样中断'):
            monitor.stop_recording()
        self.assertIsNotNone(monitor.capture)
        self.assertEqual(monitor.stop_note, '')

    def test_a_recording_that_cannot_be_validated_stays_available_for_cancel(self):
        reader = Mock()
        reader.snapshot.side_effect = RuntimeError(READ_FAILURE)
        monitor = self.monitor(reader=reader, interrupted=True)
        # 收尾会用最后一帧状态补一帧：末帧不落地时整条仍然不合格。
        monitor.capture.trajectory[-1]['on_ground'] = False
        monitor.capture.latest = replace(monitor.capture.latest, on_ground=False)
        with self.assertRaisesRegex(ValueError, '落地'):
            monitor.stop_recording()
        self.assertIsNotNone(monitor.capture)
        self.assertEqual(monitor.stop_note, '')

    def test_a_stop_after_the_game_died_still_inserts_the_recorded_inputs(self):
        """对战端退出时的收尾：最后一条输入晚于最后一帧状态也要能停止并插入。"""
        monitor = self.monitor(reader=None)
        capture = monitor.capture
        base = time.perf_counter()
        capture.record_input({'type': 'key', 'vk': 69, 'name': 'E', 'down': True}, when=base - .1)
        capture.record_input({'type': 'key', 'vk': 69, 'name': 'E', 'down': False}, when=base - .05)
        with patch('macroflow.execution.ssjj_monitor.SSJJReader',
                   side_effect=RuntimeError('需要且只能运行一个 SSJJ_BattleClient_Unity.exe')):
            action = monitor.stop_recording()
        validate_recording(action)
        self.assertEqual([event['vk'] for event in action['inputs']], [69, 69])
        self.assertIn('收尾', monitor.stop_note)
        self.assertIsNone(monitor.capture)


if __name__ == '__main__':
    unittest.main()
