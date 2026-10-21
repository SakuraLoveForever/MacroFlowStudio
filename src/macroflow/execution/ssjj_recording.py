"""Replay a recorded key/mouse route; correct the position only at F7 marks."""
from __future__ import annotations

import contextlib
import math
import threading
import time

from .ssjj_navigation import Route, RouteContext, _valid, follow_route
from .ssjj_state import GameUnavailable, SSJJReader
from .player.control import PlaybackStopped
from macroflow.core.models import SCRIPT_START_TARGET_ID, NEXT_WORKFLOW_STEP_TARGET_ID
from .player.control import (
    AdvanceToNextWorkflowStep, EndCurrentScriptRequest, EndCurrentScriptRepeatRequest,
    JumpToCurrentScriptLastAction,
    GuardJumpRequest,
)


def _number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def turns_camera(event):
    """这一步回放的是录制镜头（会与锁定跟随抢镜头）。"""
    kind = event.get('type')
    if kind == 'turn':
        return True
    return kind == 'mouse_move' and event.get('mode', 'relative') == 'relative'


def mark_offsets(marks):
    """纠正点在录制时间轴上的绝对时刻：纠正点只记「距上一点的间隔」，这里累加出来。

    间隔都是非负数，所以列表顺序就是时间顺序；编辑时改一个间隔只影响它自己的时刻，
    后面的纠正点跟着顺延，不会被别的纠正点时间牵着走。
    """
    offsets, at = [], 0.0
    for mark in marks:
        at += float(mark.get('gap_ms', 0) or 0)
        offsets.append(at)
    return offsets


def distinct_presses(inputs):
    """One down per physical hold; OS key repeat must not become a new hold."""
    held = set()
    for event in inputs:
        if not event.get('route_script_action') and event['type'] in ('key', 'mouse_button'):
            key = (event['type'], event.get('vk', event.get('button')))
            if event['down']:
                if key in held:
                    continue
                held.add(key)
            else:
                held.discard(key)
        yield event


def validate_recording(action):
    """Reject malformed/untrusted script data before opening the input boundary."""
    duration = action.get('duration_ms')
    inputs = action.get('inputs')
    if (type(action.get('map_id')) is not int or action['map_id'] <= 0 or
            not _number(duration, 1, 3_600_000) or
            not isinstance(inputs, list) or len(inputs) > 200_000 or 'checkpoints' in action):
        raise ValueError('无效的连续状态录制；旧标记路线请重新录制')
    marks = action.get('marks')
    if not isinstance(marks, list) or not 1 <= len(marks) <= 10_000:
        raise ValueError('路线缺少 F7 纠正点，请重新录制')
    at = 0.0
    for mark in marks:
        if not isinstance(mark, dict):
            raise ValueError('无效的纠正点')
        if 'gap_ms' not in mark and 'at_ms' in mark:
            raise ValueError('纠正点只记「距上一点的间隔」：这条路线还是旧的绝对时间格式，请重新录制')
        gap, point = mark.get('gap_ms'), mark.get('position')
        if (not _number(gap, 0, duration) or
                not isinstance(point, (list, tuple)) or len(point) != 3 or
                not all(_number(v, -99_999_999, 99_999_999) for v in point) or
                not all(_number(mark.get(axis), -1e8, 1e8) for axis in ('yaw', 'pitch'))):
            raise ValueError('无效的纠正点间隔、坐标或朝向')
        at += gap
        if at > duration:
            raise ValueError('纠正点间隔超出录制时长')
    if marks[0]['gap_ms'] != 0:
        raise ValueError('第一个纠正点必须是录制起点（间隔固定为 0），请重新录制')
    trajectory = action.get('trajectory')
    if not isinstance(trajectory, list) or not 2 <= len(trajectory) <= 100_000:
        raise ValueError('路线缺少连续状态轨迹，请重新录制')
    previous = -1
    last_position = None
    for sample in trajectory:
        if not isinstance(sample, dict):
            raise ValueError('无效的路线轨迹')
        at, point = sample.get('at_ms'), sample.get('position')
        if (not _number(at, 0, duration) or at <= previous or
                not isinstance(point, (list, tuple)) or len(point) != 3 or
                not all(_number(v, -99_999_999, 99_999_999) for v in point) or
                not all(_number(sample.get(axis), -1e8, 1e8) for axis in ('yaw', 'pitch')) or
                type(sample.get('client_time')) is not int or
                type(sample.get('dead')) is not bool or sample['dead'] or
                type(sample.get('on_ground')) is not bool):
            raise ValueError('无效的路线轨迹时间、坐标或内存状态')
        details = sample.get('details')
        if details is not None and (not isinstance(details, dict) or any(
                v is not None and type(v) not in (bool, int, float) or
                type(v) is float and not math.isfinite(v) for v in details.values())):
            raise ValueError('无效的角色详细状态')
        if last_position is not None and math.dist(point, last_position) > 1000:
            raise ValueError('轨迹采样间距超过 1000，请重新录制')
        previous, last_position = at, point
    if trajectory[0]['at_ms'] != 0 or trajectory[-1]['at_ms'] != duration:
        raise ValueError('连续轨迹必须完整覆盖开始到停止的时间')
    if not trajectory[0]['on_ground'] or not trajectory[-1]['on_ground']:
        raise ValueError('请落地后开始和停止路线录制')
    previous = -1
    for event in inputs:
        if not isinstance(event, dict):
            raise ValueError('无效的路线输入')
        at, kind = event.get('recorded_at_ms'), event.get('type')
        if not _number(at, 0, duration) or at < previous:
            raise ValueError('无效的路线输入时间')
        previous = at
        if event.get('route_script_action') is True:
            valid = (isinstance(kind, str) and bool(kind) and
                     _number(event.get('delay_ms', 0), 0, 86_400_000))
        elif 'route_script_action' in event and type(event['route_script_action']) is not bool:
            valid = False
        elif kind == 'key':
            valid = type(event.get('vk')) is int and 1 <= event['vk'] <= 255 and type(event.get('down')) is bool
        elif kind == 'mouse_button':
            valid = event.get('button') in ('left', 'right', 'middle', 'x1', 'x2') and type(event.get('down')) is bool
        elif kind == 'scroll':
            valid = all(type(event.get(axis)) is int and abs(event[axis]) <= 1000 for axis in ('dx', 'dy'))
        elif kind == 'mouse_move':
            valid = all(type(event.get(axis)) is int and abs(event[axis]) <= 32767 for axis in ('dx', 'dy'))
        else:
            valid = False
        if not valid:
            raise ValueError(f'无效的路线键鼠输入：{kind}')
    if 'start_step_index' in action:
        start_index = action['start_step_index']
        if type(start_index) is not int or not 0 <= start_index < len(inputs):
            raise ValueError('路线试跑起点必须是路线内的步骤')


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
        self.interrupted_at = None
        self.interrupted_phase = ''
        self.phase = '按录制时间回放'
        self.previous_tick = None
        self.progressed_at = 0.0
        self.sent_count = 0
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
                        self.interrupted_at = time.perf_counter()
                        self.interrupted_phase = self.phase
                        self.release()
                except Exception as exc:
                    self.error = exc
                    self.release()

    def renew(self):
        with self.lock:
            if self.error:
                raise self.error
            self.deadline = time.perf_counter() + .3

    def recover(self, map_id):
        """An expired input lease releases holds; validate before resuming them."""
        with self.lock:
            if self.error:
                raise self.error
            interrupted_at = self.interrupted_at
            phase = self.interrupted_phase
        if interrupted_at is None:
            return None
        # No input is held while this read or its logging callback may block.
        self.check_state(map_id)
        self.player._log_event(
            f'回放调度停顿（阶段：{phase}），已释放按键；游戏状态核验通过，继续回放')
        with self.lock:
            if self.error:
                raise self.error
            self.interrupted_at = None
            self.interrupted_phase = ''
        return interrupted_at

    def check_state(self, map_id):
        self.phase = '读取角色状态'
        self.reader.require_foreground()
        current = self.reader.snapshot_stable()
        _valid(current, map_id)
        now = time.perf_counter()
        if current.client_time != self.previous_tick:
            self.previous_tick, self.progressed_at = current.client_time, now
        elif now - self.progressed_at >= 1:
            raise RuntimeError('角色状态快照未更新，停止键鼠路线回放')
        return current

    def send(self, event):
        from macroflow.input.wininput import send_button, send_key, send_move_relative, send_scroll
        with self.lock:
            if self.error:
                raise self.error
            if self.interrupted_at is not None:
                return False
            self.reader.require_foreground()
            if self.player.stop_event.is_set():
                raise PlaybackStopped()
            if self.player.paused:
                self.release()
                return False
            kind = event['type']
            detail = None
            if kind in ('key', 'mouse_button'):
                keyboard = kind == 'key'
                value = event['vk'] if keyboard else event['button']
                held = self.keys if keyboard else self.buttons
                wanted = self.wanted_keys if keyboard else self.wanted_buttons
                parent = self.player._held_keys if keyboard else self.player._held_buttons
                down = event['down']
                (wanted.add if down else wanted.discard)(value)
                if down and value in held:
                    return True  # Keep the timeline row/jump identity, ignore OS repeat.
                # Track before the OS call so failure still attempts the matching release.
                if down:
                    held.add(value)
                    parent.add(value)
                (send_key if keyboard else send_button)(value, down)
                if not down:
                    held.discard(value)
                    parent.discard(value)
                detail = (f'VK={value} {"按下" if down else "松开"}' if keyboard else
                          f'{value} {"按下" if down else "松开"}')
            elif kind == 'scroll':
                send_scroll(event['dx'], event['dy'])
                detail = f'滚轮 ({event["dx"]}, {event["dy"]})'
            elif kind == 'mouse_move':
                send_move_relative(event['dx'], event['dy'])
                detail = f'镜头 ({event["dx"]}, {event["dy"]})'
            self.sent_count += 1
            trace = f'录制输入 #{self.sent_count}：{detail}'
        # Log callbacks may block; the watchdog must still be able to release.
        self.player._trace(trace, module_detail=True)
        return True

    def restore(self):
        with self.lock:
            for vk in self.wanted_keys - self.keys:
                self.send({'type': 'key', 'vk': vk, 'down': True})
            for button in self.wanted_buttons - self.buttons:
                self.send({'type': 'mouse_button', 'button': button, 'down': True})

    def close(self):
        self.closed.set()
        self.thread.join()
        self.release()


class RecordedReplay:
    """F7 纠正点之间直接重放录制的键鼠；到点再用内存坐标纠正位置和朝向。

    这就是「两点之间照录制的按键和鼠标执行」：中间不做逐点坐标跟踪，所以不会因为
    一个采样点冲过头就掉头折线；偏差留到用户自己按 F7 标出来的纠正点统一修正。
    """
    def __init__(self, player, reader, session, context, action, *, hwnd=None, script_stack=None, depth=0):
        self.player, self.reader, self.session, self.context = player, reader, session, context
        self.map_id = action['map_id']
        self.marks = action['marks']
        # 纠正点只记间隔，回放要的绝对时刻由间隔累加出来（时间顺序就是列表顺序）。
        self.mark_times = mark_offsets(self.marks)
        self.inputs = [dict(event) for event in action['inputs']]
        self.samples = action['trajectory']
        self.duration_ms = action['duration_ms']
        # 试跑起点：从中间某一步开始执行时跳过的步骤不再回放，路线时钟按该步对齐。
        self.start_index = int(action.get('start_step_index', 0))
        # 路线里的光标锁定动作：只有后面不再回放录制镜头时才把镜头交给锁定。
        self.aim_takes_camera = False
        self.aim_warned = False
        self.stage = '走到试跑起点并校正朝向' if self.start_index else '走到起点并校正朝向'
        self.hwnd, self.script_stack, self.depth = hwnd, script_stack, depth

    def script_action(self, event):
        """Release recorded holds and run the shared dispatcher outside route time."""
        self.session.release()
        self.context.brake()
        suspended = getattr(self.player, '_ssjj_aim_suspended', False)
        aim_owned_before = self.aim_takes_camera
        single_action = self.player._single_action
        self.player._single_action = False
        action = {key: value for key, value in event.items()
                  if key not in ('recorded_at_ms', 'route_script_action')}
        self.player._ssjj_aim_suspended = action['type'] not in {
            'delay', 'ssjj_wait_mobs_absent', 'block', 'ssjj_aim'}
        self.player._log_event(f'路线脚本动作：{action["type"]}')
        try:
            self.context.checkpoint()
            self.player._wait(int(action.get('delay_ms', 1000 if action['type'] == 'image_match' else 0)))
            result = self.player._execute_action(action, self.hwnd, self.script_stack, self.depth)
            if action['type'] == 'ssjj_aim':
                self.update_aim_ownership(event)
            after_delay = max(0, int(action.get('after_delay_ms', 0)))
            if after_delay:
                self.player._wait(after_delay)
            return result
        finally:
            # 路线默认独占镜头；锁定动作争到镜头后就不再还回去，直到被解锁。
            self.player._ssjj_aim_suspended = (
                (suspended or aim_owned_before) and not self.aim_takes_camera)
            self.player._single_action = single_action

    def update_aim_ownership(self, event):
        """路线里的光标锁定动作：只有后面不再回放录制镜头时才让它接管镜头。

        路线独占镜头是为了让录制的转向照原样生效；插入锁定动作后如果后面还有
        录制镜头，两者会互相抢镜头（录制的转向被锁定不断纠正回去），所以只有
        后面没有镜头输入时才交出去，并把原因写进日志。
        """
        controller = getattr(self.player, '_ssjj_aim_lock', None)
        if not bool(getattr(controller, 'enabled', False)):
            self.aim_takes_camera = False
            return
        at = float(event.get('recorded_at_ms', 0) or 0)
        remaining = sum(1 for step in self.inputs
                        if step['recorded_at_ms'] > at and turns_camera(step))
        self.aim_takes_camera = not remaining
        if self.aim_takes_camera:
            self.player._log_event(
                '路线里的光标锁定已开启：后面不再有录制镜头，锁定跟随立即生效')
        elif not self.aim_warned:
            self.player._log_event(
                f'路线里的光标锁定已开启，但后面还有 {remaining} 条录制镜头输入：'
                '为避免和路线抢镜头，路线继续独占镜头；把锁定动作放到最后一段镜头输入之后即可立即跟随')
            self.aim_warned = True

    @contextlib.contextmanager
    def navigation_camera(self):
        """导航（走近纠正点、终点对齐）期间临时把镜头让给导航。"""
        if not self.aim_takes_camera:
            yield
            return
        self.player._ssjj_aim_suspended = True
        try:
            yield
        finally:
            self.player._ssjj_aim_suspended = False

    def jump_index(self, result):
        kind, target = result
        if kind == 'end_current_script':
            raise EndCurrentScriptRequest()
        if kind == 'next_workflow_step':
            if self.depth > 0:
                raise EndCurrentScriptRepeatRequest()
            raise AdvanceToNextWorkflowStep()
        if kind == 'action_id':
            if target == SCRIPT_START_TARGET_ID:
                return 0
            if target == NEXT_WORKFLOW_STEP_TARGET_ID:
                raise AdvanceToNextWorkflowStep()
            for index, event in enumerate(self.inputs):
                if event.get('action_id') == target:
                    return index
            raise RuntimeError('路线内跳转目标已被删除，请重新选择')
        index = int(target) - 1
        if not 0 <= index < len(self.inputs):
            raise RuntimeError('路线内跳转行号无效')
        return index

    def run(self):
        current = self.session.check_state(self.map_id)
        if self.start_index:
            label = (f'从第 {self.start_index + 1}/{len(self.inputs)} 步开始试跑：' +
                     '距该步录制位置 {distance:.1f}，直线接近后校正朝向；之前的步骤不再回放')
        else:
            label = ('出生位置接入：距录制起点 {distance:.1f}，直线接近后校正朝向；' +
                     '接近期间不执行录制动作')
        self.correct(current, self.start_mark(), label=label)
        self.stage = '按键鼠标回放'
        scope = None
        managed = self.player._script_scope_managed
        try:
            if getattr(self.player, '_enable_script_globals', True) and self.player.on_script_scope_enter:
                scope = self.player.on_script_scope_enter(self.inputs)
                self.player._script_scope_managed = True
            self.replay()
        finally:
            self.player._script_scope_managed = managed
            if scope is not None and self.player.on_script_scope_exit:
                self.player.on_script_scope_exit(scope)
        self.stage = '终点验收'
        self.verify_end()

    def start_mark(self):
        """试跑起点的参考姿态：从中间某步开始时用该步时刻的轨迹采样坐标与朝向。"""
        if not self.start_index:
            return self.marks[0]
        at = float(self.inputs[self.start_index]['recorded_at_ms'])
        sample = self.samples[0]
        for candidate in self.samples:
            if float(candidate['at_ms']) > at:
                break
            sample = candidate
        return {'position': list(sample['position']),
                'yaw': float(sample['yaw']), 'pitch': float(sample['pitch'])}

    def correct(self, current, mark, *, label):
        """走近参考点并按录制姿态对齐；返回耗时（回放时钟暂停）。"""
        target = tuple(mark['position'])
        distance = math.dist(current.position, target)
        steps = max(1, math.ceil(distance / 500))
        if steps >= 100_000:
            raise RuntimeError('纠正点距离异常，停止')
        route = Route(self.map_id, [
            tuple(a + (b - a) * i / steps for a, b in zip(current.position, target))
            for i in range(steps + 1)])
        self.player._log_event(label.format(distance=distance))
        started = time.perf_counter()
        follow_route(self.context, route, reader=self.reader,
                     final_yaw=mark['yaw'], final_pitch=mark['pitch'])
        return time.perf_counter() - started

    def restore_branch_holds(self, index):
        self.session.release()
        self.session.wanted_keys.clear()
        self.session.wanted_buttons.clear()
        for event in distinct_presses(self.inputs[:index]):
            if event.get('route_script_action') or event['type'] not in ('key', 'mouse_button'):
                continue
            held = self.session.wanted_keys if event['type'] == 'key' else self.session.wanted_buttons
            value = event['vk'] if event['type'] == 'key' else event['button']
            (held.add if event['down'] else held.discard)(value)

    def branch_mark(self, target_time):
        return next((i for i, at in enumerate(self.mark_times)
                     if i > 0 and at >= target_time), len(self.marks))

    def replay(self):
        index, mark_index, offset = 0, 1, 0
        if self.start_index:
            # 试跑：跳过前面的步骤，时钟从这一步的录制时刻起算，并恢复此前按住的键鼠。
            index = self.start_index
            offset = self.inputs[index]['recorded_at_ms']
            mark_index = self.branch_mark(offset)
            self.restore_branch_holds(index)
        while True:
            try:
                return self._replay(index, mark_index, offset)
            except GuardJumpRequest as request:
                route_ids = frozenset(str(event['action_id']) for event in self.inputs if event.get('action_id'))
                if request.scope_action_ids:
                    if request.scope_action_ids != route_ids:
                        raise
                elif not request.jump_action_id or request.jump_action_id not in route_ids:
                    raise
                result = (('next_workflow_step', '') if request.jump_action_id == NEXT_WORKFLOW_STEP_TARGET_ID
                          else ('action_id', request.jump_action_id) if request.jump_action_id
                          else ('row', request.jump_row))
                index = self.jump_index(result)
                self.restore_branch_holds(index)
                offset = self.inputs[index]['recorded_at_ms']
                mark_index = self.branch_mark(offset)

    def _replay(self, index, mark_index, offset):
        """按录制时间轴发送键鼠；到纠正点时间就停下修正，再接着往下放。"""
        started = time.perf_counter() - offset / 1000
        suspended = 0.0

        def elapsed_ms():
            return (time.perf_counter() - started - suspended) * 1000

        self.session.renew()
        while True:
            # 暂停 / F12 / 守卫：先把录制的按住输入放掉再等恢复，恢复后补按回去
            # （回放时钟暂停，按住时长不会因为暂停被拉长）。
            before = time.perf_counter()
            self.session.phase = '检查暂停与全局检测'
            if self.player.paused or self.player.stop_event.is_set():
                self.session.release()
            self.context.checkpoint()
            interrupted_at = self.session.recover(self.map_id)
            if interrupted_at is not None:
                before = min(before, interrupted_at)
            suspended += max(0.0, time.perf_counter() - before)
            self.session.renew()
            self.session.phase = '恢复按住的键鼠'
            self.session.restore()
            input_due = (self.inputs[index]['recorded_at_ms']
                         if index < len(self.inputs) else math.inf)
            mark_due = (self.mark_times[mark_index]
                        if mark_index < len(self.marks) else math.inf)
            due = min(input_due, mark_due, self.duration_ms)
            elapsed = elapsed_ms()
            if elapsed + .001 < due:
                self.session.phase = '等待下一条录制输入'
                self.context.wait(ms=min(10, max(1, int(due - elapsed))))
                continue
            if mark_due <= input_due and mark_due <= self.duration_ms:
                self.session.release()
                with self.navigation_camera():
                    suspended += self.correct(
                        self.session.check_state(self.map_id), self.marks[mark_index],
                        label=(f'纠正点 {mark_index + 1}/{len(self.marks)}：' +
                               '距记录位置 {distance:.1f}，走近后重新对齐朝向，再继续回放'),
                    )
                mark_index += 1
                continue
            if index >= len(self.inputs):
                break
            if self.inputs[index].get('route_script_action'):
                before = time.perf_counter()
                try:
                    result = self.script_action(self.inputs[index])
                except JumpToCurrentScriptLastAction:
                    result = ('row', len(self.inputs))
                suspended += time.perf_counter() - before
                index += 1
                if result is not None:
                    index = self.jump_index(result)
                    self.restore_branch_holds(index)
                    # A branch rebases route time instead of waiting for the old
                    # timeline to catch up or firing skipped inputs immediately.
                    target_time = self.inputs[index]['recorded_at_ms']
                    started = time.perf_counter() - target_time / 1000
                    suspended = 0.0
                    mark_index = self.branch_mark(target_time)
                continue
            self.session.phase = f'发送录制输入第 {index + 1}/{len(self.inputs)} 条'
            if not self.session.send(self.inputs[index]):
                continue
            index += 1
            if index % 20 == 0:
                self.session.check_state(self.map_id)
        self.session.release()

    def verify_end(self):
        try:
            current = self.session.check_state(self.map_id)
            last = self.samples[-1]
            with self.navigation_camera():
                follow_route(self.context, Route(self.map_id, [current.position, current.position]),
                             reader=self.reader, final_yaw=last['yaw'], final_pitch=last['pitch'])
            current = self.session.check_state(self.map_id)
        except GameUnavailable as exc:
            # 终点验收只是收尾校验：人不在这局游戏里（切出去/换图/退出）就跳过，
            # 不把整条脚本打断。
            self.player._log_event(f'终点验收跳过：{exc}')
            return
        if not current.on_ground:
            raise RuntimeError('终点尚未落地，停止')


def run_recording_action(player, action, hwnd=None, script_stack=None, depth=0):
    """按 F7 纠正点回放一段内存录制的键鼠路线。

    人不在这一局游戏里（没开、没进对局、不在前台、换图/退出）时跳过这条路线并
    记一条日志：输入不会发给别的窗口，脚本继续往下执行，而不是整场异常收尾。
    """
    validate_recording(action)
    from .player.base import MAX_SCRIPT_REF_DEPTH
    route_depth = getattr(player, '_ssjj_recording_depth', 0)
    if depth > MAX_SCRIPT_REF_DEPTH or route_depth >= MAX_SCRIPT_REF_DEPTH:
        raise RuntimeError('路线脚本动作嵌套过深')
    total = len(action['inputs'])
    try:
        reader = SSJJReader()
    except GameUnavailable as exc:
        player._log_event(f'跳过键鼠连续路线：{exc}')
        return None
    with reader as active:
        try:
            active.require_foreground()
        except GameUnavailable as exc:
            player._log_event(f'跳过键鼠连续路线：{exc}')
            return None
        session = ReplayInputs(player, active)
        context = RouteContext(player, active, action['map_id'])
        replay = RecordedReplay(player, active, session, context, action,
                                hwnd=hwnd, script_stack=script_stack, depth=depth)
        player._ssjj_recording_depth = route_depth + 1
        try:
            replay.run()
            player._log_event(f'路线回放完成，已发送 {session.sent_count}/{total} 条录制输入')
        except PlaybackStopped:
            player._log_event(f'已停止路线回放：{replay.stage}，已发送 {session.sent_count}/{total}')
            raise
        except GameUnavailable as exc:
            player._log_event(
                f'路线中断（已发送 {session.sent_count}/{total} 条输入）：{exc}；'
                '跳过剩余步骤，脚本继续执行')
            return None
        except (RuntimeError, ValueError, OSError) as exc:
            player._log_event(
                f'{replay.stage}未完成（已发送 {session.sent_count}/{total} 条输入）：{exc}；'
                '跳过剩余路线，脚本和工作流继续执行')
            return None
        finally:
            try:
                context.close()
            finally:
                try:
                    session.close()
                finally:
                    player._ssjj_recording_depth = route_depth
