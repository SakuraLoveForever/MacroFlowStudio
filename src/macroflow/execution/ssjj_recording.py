"""Replay captured game input on its original timeline; telemetry only checks it."""
from __future__ import annotations

import bisect
import math
import threading
import time

from .ssjj_navigation import _valid
from .ssjj_state import SSJJReader
from .player.control import PlaybackStopped

POSITION_TOLERANCE = 150
START_YAW_TOLERANCE = 15


def _number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def validate_recording(action):
    """Reject malformed/untrusted script data before opening the input boundary."""
    duration = action.get('duration_ms')
    inputs, samples = action.get('inputs'), action.get('samples')
    if (type(action.get('map_id')) is not int or action['map_id'] <= 0 or
            not _number(duration, 1, 3_600_000) or
            not isinstance(inputs, list) or not 1 <= len(inputs) <= 200_000 or
            not isinstance(samples, list) or not 2 <= len(samples) <= 100_000):
        raise ValueError('无效的键鼠路线录制')
    previous = -1
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError('无效的路线状态')
        at, point = sample.get('at_ms'), sample.get('position')
        if (not _number(at, 0, duration) or at < previous or
                not isinstance(point, (list, tuple)) or len(point) != 3 or
                not all(_number(v, -1e8, 1e8) for v in point) or
                not _number(sample.get('yaw'), -1e8, 1e8)):
            raise ValueError('无效的路线状态时间或坐标')
        previous = at
    if samples[0]['at_ms'] != 0 or samples[-1]['at_ms'] != duration:
        raise ValueError('路线状态必须覆盖完整录制时长')
    previous = -1
    for event in inputs:
        if not isinstance(event, dict):
            raise ValueError('无效的路线输入')
        at, kind = event.get('recorded_at_ms'), event.get('type')
        if not _number(at, 0, duration) or at < previous:
            raise ValueError('无效的路线输入时间')
        previous = at
        if kind == 'key':
            valid = type(event.get('vk')) is int and 1 <= event['vk'] <= 255 and type(event.get('down')) is bool
        elif kind == 'mouse_button':
            valid = event.get('button') in ('left', 'right', 'middle') and type(event.get('down')) is bool
        elif kind in ('mouse_move', 'scroll'):
            limit = 1_000_000 if kind == 'mouse_move' else 1000
            valid = all(type(event.get(axis)) is int and abs(event[axis]) <= limit for axis in ('dx', 'dy'))
            valid = valid and (kind != 'mouse_move' or event.get('mode') == 'relative')
        else:
            valid = False
        if not valid:
            raise ValueError(f'无效的路线键鼠输入：{kind}')


class ReplayInputs:
    """Own held inputs, including independent release during blocked callbacks."""
    def __init__(self, player, reader):
        self.player, self.reader = player, reader
        self.keys, self.buttons = set(), set()
        self.wanted_keys, self.wanted_buttons = set(), set()
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.deadline = 0.0
        self.error = None
        self.thread = threading.Thread(target=self._watch, name='ssjj-replay-inputs', daemon=True)
        self.thread.start()

    def release(self):
        from macroflow.input.wininput import send_key, send_button
        with self.lock:
            for held, parent, send in (
                (self.keys, self.player._held_keys, send_key),
                (self.buttons, self.player._held_buttons, send_button),
            ):
                for value in list(held):
                    try:
                        send(value, False)
                    except Exception as exc:
                        self.error = exc
                    finally:
                        held.discard(value)
                        parent.discard(value)

    def _watch(self):
        while not self.closed.wait(.01):
            with self.lock:
                if not (self.keys or self.buttons):
                    continue
                try:
                    self.reader.require_foreground()
                    if self.player.paused or self.player.stop_event.is_set():
                        self.release()
                    elif time.perf_counter() >= self.deadline:
                        self.error = RuntimeError('游戏状态读取超时，已停止键鼠路线回放')
                        self.release()
                except Exception as exc:
                    self.error = exc
                    self.release()

    def renew(self):
        with self.lock:
            if self.error:
                raise self.error
            self.deadline = time.perf_counter() + .3

    def send(self, event, *, restoring=False):
        from macroflow.input.wininput import send_key, send_button, send_move_relative, send_scroll
        with self.lock:
            if self.error:
                raise self.error
            self.reader.require_foreground()
            if self.player.stop_event.is_set():
                raise PlaybackStopped()
            if self.player.paused:
                self.release()
                return False
            kind = event['type']
            if kind in ('key', 'mouse_button'):
                keyboard = kind == 'key'
                value = event['vk'] if keyboard else event['button']
                held = self.keys if keyboard else self.buttons
                wanted = self.wanted_keys if keyboard else self.wanted_buttons
                parent = self.player._held_keys if keyboard else self.player._held_buttons
                down = event['down']
                if not restoring:
                    (wanted.add if down else wanted.discard)(value)
                # Track before the OS call so failure still attempts the matching release.
                if down:
                    held.add(value)
                    parent.add(value)
                (send_key if keyboard else send_button)(value, down)
                if not down:
                    held.discard(value)
                    parent.discard(value)
            elif kind == 'mouse_move':
                send_move_relative(event['dx'], event['dy'])
            elif kind == 'scroll':
                send_scroll(event['dx'], event['dy'])
            return True

    def restore(self):
        with self.lock:
            for vk in self.wanted_keys - self.keys:
                self.send({'type': 'key', 'vk': vk, 'down': True}, restoring=True)
            for button in self.wanted_buttons - self.buttons:
                self.send({'type': 'mouse_button', 'button': button, 'down': True}, restoring=True)

    def close(self):
        self.closed.set()
        self.thread.join()
        self.release()


def _expected_position(samples, times, elapsed_ms):
    index = min(len(samples) - 2, max(0, bisect.bisect_right(times, elapsed_ms) - 1))
    before, after = samples[index:index + 2]
    span = after['at_ms'] - before['at_ms']
    fraction = min(1, max(0, (elapsed_ms - before['at_ms']) / span)) if span else 1
    return tuple(a + (b - a) * fraction for a, b in zip(before['position'], after['position']))


def run_recording_action(player, action):
    validate_recording(action)
    samples, inputs = action['samples'], action['inputs']
    times = [sample['at_ms'] for sample in samples]
    with SSJJReader() as reader:
        reader.require_foreground()
        current = reader.snapshot()
        _valid(current, action['map_id'])
        if math.dist(current.position, samples[0]['position']) > POSITION_TOLERANCE:
            raise RuntimeError('距离录制起点过远，请先回到起点')
        if abs((current.yaw - samples[0]['yaw'] + 180) % 360 - 180) > START_YAW_TOLERANCE:
            raise RuntimeError('起始朝向与录制不一致，请先对齐录制时的朝向')
        session = ReplayInputs(player, reader)
        started = time.perf_counter()
        progressed = 0.0
        suspended = 0.0
        next_check, index = 0.0, 0
        previous_tick = current.client_time
        try:
            player._log_event(f'键鼠路线回放：{len(inputs)} 个输入，{action["duration_ms"] / 1000:g} 秒')
            while True:
                pause_started = time.perf_counter()
                was_paused = player.paused
                if was_paused or player.stop_event.is_set():
                    session.release()
                paused_for = player.wait_while_paused()
                suspended += time.perf_counter() - pause_started if was_paused else paused_for
                if player.stop_event.is_set():
                    raise PlaybackStopped()
                guard_started = None

                def suspend_for_guard():
                    nonlocal guard_started
                    if guard_started is None:
                        guard_started = time.perf_counter()
                    session.release()

                player._poll_guards(before_handle=suspend_for_guard)
                player._settle_after_guard()
                if guard_started is not None:
                    suspended += time.perf_counter() - guard_started
                elapsed = (time.perf_counter() - started - suspended) * 1000
                if (elapsed >= next_check or elapsed >= action['duration_ms'] or
                        session.wanted_keys != session.keys or session.wanted_buttons != session.buttons):
                    reader.require_foreground()
                    current = reader.snapshot()
                    _valid(current, action['map_id'])
                    elapsed = (time.perf_counter() - started - suspended) * 1000
                    if current.client_time != previous_tick:
                        previous_tick, progressed = current.client_time, elapsed
                    elif elapsed - progressed >= 1000:
                        raise RuntimeError('角色状态快照未更新，停止键鼠路线回放')
                    error = math.dist(current.position, _expected_position(samples, times, elapsed))
                    if error > POSITION_TOLERANCE:
                        raise RuntimeError(f'偏离录制路线 {error:.1f}，超过 {POSITION_TOLERANCE}，已停止')
                    session.renew()
                    session.restore()
                    next_check = elapsed + 50
                while index < len(inputs) and inputs[index]['recorded_at_ms'] <= elapsed + .001:
                    if not session.send(inputs[index]):
                        break
                    index += 1
                if elapsed >= action['duration_ms'] and index == len(inputs):
                    player._log_event('键鼠路线回放完成')
                    return
                next_input = inputs[index]['recorded_at_ms'] if index < len(inputs) else action['duration_ms']
                seconds = min(10, max(.1, next_input - elapsed), max(.1, action['duration_ms'] - elapsed)) / 1000
                if player.stop_event.wait(seconds):
                    raise PlaybackStopped()
        finally:
            session.close()
