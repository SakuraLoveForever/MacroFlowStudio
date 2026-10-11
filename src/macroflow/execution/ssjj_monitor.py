"""Telemetry worker and synchronized key/mouse route recording; independent of Tk."""
from __future__ import annotations

import math
import threading
import time

from .ssjj_navigation import _valid
from .ssjj_recording import validate_recording
from .ssjj_state import SSJJReader


class RouteCapture:
    def __init__(self, initial):
        _valid(initial, initial.map_id)
        self.map_id = initial.map_id
        self.started_at = time.perf_counter()
        self.samples = [{'at_ms': 0, 'position': list(initial.position), 'yaw': initial.yaw}]
        self.inputs = []
        self.latest = initial
        self.error = ''

    def feed(self, sample, *, when=None):
        if self.error:
            raise RuntimeError(self.error)
        try:
            _valid(sample, self.map_id)
            if math.dist(self.latest.position, sample.position) > 1000:
                raise RuntimeError('录制中发生坐标跳变，请取消后重录')
            self.latest = sample
            at = round(((time.perf_counter() if when is None else when) - self.started_at) * 1000, 3)
            if at > 3_600_000 or len(self.samples) >= 99999:
                raise RuntimeError('路线录制过长，请分段录制')
            self.samples.append({'at_ms': at, 'position': list(sample.position), 'yaw': sample.yaw})
        except Exception as exc:
            self.error = str(exc)
            raise

    def record_input(self, action, *, when=None):
        if self.error:
            return
        at = round(((time.perf_counter() if when is None else when) - self.started_at) * 1000, 3)
        event = {key: action[key] for key in ('type', 'vk', 'down', 'button', 'mode', 'dx', 'dy') if key in action}
        event['recorded_at_ms'] = max(0, at)
        if action['type'] == 'turn':
            # Injected hotkey turns are aggregated by MacroRecorder; preserve their duration.
            duration = max(0, float(action.get('pulse_duration_ms', 0)))
            steps = max(1, min(500, math.ceil(duration / 10)))
            for index in range(steps):
                self.inputs.append({'type': 'mouse_move', 'mode': 'relative',
                    'dx': round(action['dx'] * (index + 1) / steps) - round(action['dx'] * index / steps),
                    'dy': round(action['dy'] * (index + 1) / steps) - round(action['dy'] * index / steps),
                    'recorded_at_ms': round(max(0, at) + duration * index / steps, 3)})
        else:
            self.inputs.append(event)
        if len(self.inputs) > 200_000:
            self.error = '键鼠动作过多，请分段录制'

    def finish(self):
        if self.error:
            raise RuntimeError(self.error)
        duration = max(1, round((time.perf_counter() - self.started_at) * 1000, 3))
        inputs = sorted(self.inputs, key=lambda event: event['recorded_at_ms'])
        action = {'type': 'ssjj_recording', 'map_id': self.map_id,
                  'inputs': inputs, 'samples': list(self.samples), 'duration_ms': duration, 'delay_ms': 0}
        action['samples'].append({'at_ms': duration, 'position': list(self.latest.position), 'yaw': self.latest.yaw})
        validate_recording(action)
        return action


class SSJJMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.latest = None
        self.updated = 0.0
        self.error = '正在连接游戏…'
        self.capture = None
        self.input_recorder = None
        self.reader = None
        self.exclude_rect = None
        self.thread = threading.Thread(target=self._run, name='ssjj-coordinates', daemon=True)
        self.thread.start()

    def _run(self):
        reader = None
        client_time = None
        progressed = time.monotonic()
        try:
            while not self.stop.is_set():
                try:
                    if reader is None:
                        reader = SSJJReader()
                        self.reader = reader
                    sample = reader.snapshot(include_details=True)
                    if sample.client_time != client_time:
                        client_time, progressed = sample.client_time, time.monotonic()
                    elif time.monotonic() - progressed > 1.5:
                        raise RuntimeError('游戏状态时间未更新，等待游戏恢复')
                    with self.lock:
                        self.latest, self.updated, self.error = sample, time.monotonic(), ''
                        if self.capture is not None:
                            if self.capture.inputs:
                                reader.require_foreground()
                            self.capture.feed(sample)
                except Exception as exc:
                    with self.lock:
                        self.latest, self.error = None, str(exc)
                        if self.capture is not None:
                            self.capture.error = str(exc)
                    if self.input_recorder is not None:
                        self.input_recorder.stop()
                    if reader is not None:
                        self.reader = None
                        reader.close()
                        reader = None
                    if self.stop.wait(1):
                        break
                if self.stop.wait(.1):
                    break
        finally:
            if reader is not None:
                reader.close()

    def status(self):
        with self.lock:
            latest = self.latest if time.monotonic() - self.updated < .75 else None
            count = len(self.capture.samples) if self.capture is not None else 0
            return latest, self.error, self.capture is not None, count

    def _record_input(self, action):
        with self.lock:
            if self.capture is None or self.reader is None or self.input_recorder is None:
                return
            try:
                self.reader.require_foreground()
            except RuntimeError:
                # The panel can start capture before the user returns to the game.
                if self.capture.inputs:
                    self.capture.error = '录制期间游戏失去前台，请取消后重录'
                return
            if self.exclude_rect and action['type'] in ('mouse_button', 'scroll'):
                left, top, right, bottom = self.exclude_rect
                if left <= action.get('x', -1) < right and top <= action.get('y', -1) < bottom:
                    return
            when = self.input_recorder._recording_started_at + action.get('recorded_at_ms', 0) / 1000
            self.capture.record_input(action, when=when)

    def start_recording(self, *, filter_vks=None):
        from macroflow.input.recorder import MacroRecorder
        with self.lock:
            if self.capture is not None:
                raise RuntimeError('已有路线正在录制')
            if self.latest is None or time.monotonic() - self.updated >= .75:
                raise RuntimeError(self.error or '当前坐标已过期，请等待游戏连接')
            self.capture = RouteCapture(self.latest)
            self.input_recorder = MacroRecorder(on_action=self._record_input)
            recorder = self.input_recorder
        try:
            recorder.start('relative', filter_vks=filter_vks)
        except Exception:
            self.cancel_recording()
            raise

    def stop_recording(self):
        # Listener.stop/join must happen outside the callback's monitor lock.
        if self.input_recorder is not None:
            self.input_recorder.stop()
        with self.lock:
            if self.capture is None:
                raise RuntimeError('尚未开始路线录制')
            if time.monotonic() - self.updated >= .75:
                raise RuntimeError('坐标已过期，请取消后重录')
            if self.input_recorder is not None:
                if self.input_recorder.limit_reached or self.input_recorder.unsupported_buttons:
                    raise RuntimeError('录制包含过多动作或不支持的鼠标侧键，请取消后重录')
            action = self.capture.finish()
            self.capture = None
            self.input_recorder = None
            return action

    def cancel_recording(self):
        if self.input_recorder is not None:
            self.input_recorder.stop()
        with self.lock:
            self.capture = None
            self.input_recorder = None

    def close(self):
        self.cancel_recording()
        self.stop.set()
        self.thread.join(timeout=.5)
