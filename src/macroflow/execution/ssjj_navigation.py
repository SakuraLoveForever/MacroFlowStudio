"""Taught routes in SSJJ raw coordinates (XY horizontal, Z height)."""
from __future__ import annotations

import argparse
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .memory_plugin import MemoryPluginUnavailable, plugin


@dataclass
class Route:
    map_id: int
    points: list[tuple[float, float, float]]

    def __post_init__(self):
        if type(self.map_id) is not int or self.map_id <= 0:
            raise ValueError('路线需要有效的地图 ID')
        if not isinstance(self.points, list) or not 2 <= len(self.points) <= 100000:
            raise ValueError('路线需要 2 到 100000 个坐标点')
        for point in self.points:
            if not isinstance(point, (list, tuple)) or len(point) != 3 or not all(
                type(v) in (int, float) and math.isfinite(v) and abs(v) < 1e8 for v in point
            ):
                raise ValueError('路线坐标必须是三个有限数值')
        self.points = [tuple(point) for point in self.points]
        if any(math.dist(a, b) > 1000 for a, b in zip(self.points, self.points[1:])):
            raise ValueError('相邻路线点超过 1000，可能发生传送或采样中断')

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace only after serialization succeeds; an interrupted write keeps the old route.
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(json.dumps({'map_id': self.map_id, 'points': self.points},
            ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) != {'map_id', 'points'}:
            raise ValueError('无效的路线文件')
        return cls(data['map_id'], data['points'])


def steering(sample, target):
    dx, dy = target[0] - sample.position[0], target[1] - sample.position[1]
    heading = math.degrees(math.atan2(dy, dx))
    return math.dist(sample.position, target), (heading - sample.yaw + 180) % 360 - 180


# 松开 W 后角色不一定立刻停下：2026-10-14 的录制轨迹（满速 871）显示松键后仍会滑行
# 约 210 单位。滑行时间常数由代码在停稳后实测，这里只给出点按微调的时长范围。
PULSE_MAX = .12
PULSE_MIN = .02


def _valid(sample, map_id):
    if sample.map_id != map_id:
        # 换图/换场景了：这一局的路线已经无效，调用方可以跳过而不是整场失败。
        raise plugin.GameUnavailable('地图已变化，停止跑图')
    if sample.dead:
        raise RuntimeError('角色已死亡，停止跑图')
    if not all(math.isfinite(v) for v in (*sample.position, sample.yaw, sample.pitch)):
        raise RuntimeError('角色坐标无效，停止跑图')


def _at_heading(sample, yaw=None, pitch=None):
    return all(
        target is None or abs((target - actual + 180) % 360 - 180) <= 1
        for target, actual in ((yaw, sample.yaw), (pitch, sample.pitch)))


class _TurnFeedback:
    """Wait for a mouse response, then two fresh samples with a stable angle."""
    def __init__(self, sample, axis, now, expected=None):
        self.axis = axis
        self.expected = expected
        self.angle = getattr(sample, axis)
        self.tick = sample.client_time
        self.started = self.changed_at = now
        self.responded = False
        self.stable_ticks = 0

    def update(self, sample, now):
        if sample.client_time != self.tick:
            angle = getattr(sample, self.axis)
            if abs((angle - self.angle + 180) % 360 - 180) >= .001:
                self.responded = True
                self.changed_at = now
                self.stable_ticks = 0
            elif self.responded:
                self.stable_ticks += 1
            self.angle, self.tick = angle, sample.client_time
        return (self.responded and self.stable_ticks >= 2 and
                now - self.changed_at >= .05 - 1e-8 and
                (self.expected is None or abs((self.angle - self.expected + 180) % 360 - 180) <= 1 or
                 now - self.started >= 1 - 1e-8))


class RouteContext:
    """Parent-owned continuous input, released before pauses and guard handlers."""
    def __init__(self, player, reader, map_id):
        self.player, self.reader, self.map_id = player, reader, map_id
        self.forward = False
        self._braking = False
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._deadline = 0.0
        self._error = None
        self._input_trace = []
        self._watchdog = threading.Thread(target=self._watch, name='ssjj-input-watchdog', daemon=True)
        self._watchdog.start()

    def release(self):
        from macroflow.input.wininput import send_key
        with self._lock:
            if self.forward:
                send_key(87, False)
                self.forward = False
                self.player._held_keys.discard(87)
                self._input_trace.append('导航输入：W 松开（VK=87）')
            if self._braking:
                send_key(16, False)
                self._braking = False
                self.player._held_keys.discard(16)
                self._input_trace.append('导航输入：Shift 松开（VK=16）')

    def brake(self):
        """Normal route stops tap Shift; interruption cleanup only releases inputs."""
        from macroflow.input.wininput import send_key
        self.release()
        try:
            with self._lock:
                if (self._closed.is_set() or self._error is not None or self.player.paused or
                        self.player.stop_event.is_set() or
                        self.player._held_keys.intersection({16, 160, 161})):
                    return
                self.reader.require_foreground(activate=False)
                if self.player.paused or self.player.stop_event.is_set():
                    return
                self._deadline = time.monotonic() + .12
                self._braking = True
                self.player._held_keys.add(16)
                send_key(16, True)
                self._input_trace.append('导航刹停：Shift 短按 40 ms（VK=16）')
            self.wait(ms=40)
        finally:
            self.release()
            self._flush_input_trace()

    def _flush_input_trace(self):
        # The watchdog only queues messages: a slow logger must not block release.
        with self._lock:
            messages, self._input_trace = self._input_trace, []
        for message in messages:
            self.player._trace(message, module_detail=True)

    def _watch(self):
        # A stalled guard/reader must not keep W down while the controller is blocked.
        while not self._closed.wait(.01):
            try:
                with self._lock:
                    if not self.forward and not self._braking:
                        continue
                    if self._braking:
                        self.reader.require_foreground(activate=False)
                    else:
                        self.reader.require_foreground()
                    if (self.player.paused or self.player.stop_event.is_set() or
                            time.monotonic() >= self._deadline):
                        self.release()
            except Exception as exc:
                self._error = exc
                try:
                    self.release()
                finally:
                    return

    def close(self):
        self._closed.set()
        self._watchdog.join()
        self.release()
        self._flush_input_trace()

    def move(self, *, forward=False, dx=0, dy=0):
        from macroflow.input.wininput import send_key, send_move_relative
        from .player.control import PlaybackStopped
        if type(forward) is not bool or any(type(v) is not int or not -32767 <= v <= 32767 for v in (dx, dy)):
            raise ValueError('无效的路线输入')
        if not forward:
            self.release()
        if not forward and not (dx or dy):
            self._flush_input_trace()
            return  # Release must work even when telemetry or focus is unavailable.
        try:
            if self._error is not None:
                raise self._error
            _valid(self.reader.snapshot_stable(), self.map_id)
            self.reader.require_foreground()
            if self.player.stop_event.is_set():
                raise PlaybackStopped()
            if self.player.paused:
                self.release()
                return
            with self._lock:
                if self._error is not None:
                    raise self._error
                self.reader.require_foreground()
                if self.player.paused or self.player.stop_event.is_set():
                    self.release()
                    return
                if forward:
                    self._deadline = time.monotonic() + .12
                    if not self.forward:
                        self.player._held_keys.add(87)
                        self.forward = True
                        send_key(87, True)
                        self._input_trace.append('导航输入：W 按下已提交（VK=87）')
                if dx or dy:
                    send_move_relative(dx, dy)
                if self.player.paused or self.player.stop_event.is_set():
                    self.release()
        except BaseException:
            self.release()
            raise
        finally:
            self._flush_input_trace()

    def checkpoint(self):
        from .player.control import PlaybackStopped
        if self._error is not None:
            raise self._error
        if self.player.paused or self.player.stop_event.is_set():
            self.release()
        self._flush_input_trace()
        self.player.wait_while_paused()
        if self.player.stop_event.is_set():
            raise PlaybackStopped()
        self.player._poll_guards(before_handle=self.release)
        self.player._settle_after_guard()

    def wait(self, ms=0):
        from .player.control import PlaybackStopped
        if self.player.stop_event.wait(ms / 1000):
            self.release()
            raise PlaybackStopped()

    def log(self, message, *, detail=False):
        if detail:
            self.player._trace(message, module_detail=True)
        else:
            self.player._log_event(message)


def _corner(points, index):
    if index == 0 or index == len(points) - 1:
        return True
    before, point, after = points[index - 1:index + 2]
    incoming = math.atan2(point[1] - before[1], point[0] - before[0])
    outgoing = math.atan2(after[1] - point[1], after[0] - point[0])
    angle = (outgoing - incoming + math.pi) % (2 * math.pi) - math.pi
    return abs(angle) > math.radians(25)


def _passed(position, before, target):
    """Only pass the current segment, never pick a nearby later leg of a route."""
    dx, dy = target[0] - before[0], target[1] - before[1]
    length = math.hypot(dx, dy)
    if length == 0 or abs(position[2] - target[2]) > 25:
        return False
    x, y = position[0] - target[0], position[1] - target[1]
    return x * dx + y * dy >= 0


def follow_route(ctx, route, *, reader=None, progress_timeout=3.0, final_yaw=None,
                 final_pitch=None, on_ready=None, on_progress=None, strict_path=False, stop_indices=(),
                 start_index=0):
    """Track fresh state continuously; Python scripts delegate the entire action."""
    if not math.isfinite(progress_timeout) or progress_timeout <= 0:
        raise ValueError('进展超时必须大于零')
    if type(start_index) is not int or not 0 <= start_index < len(route.points):
        raise ValueError('无效的路线起始点索引')
    if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v))
           for v in (final_yaw, final_pitch)):
        raise ValueError('终点朝向必须是有限数值')
    if reader is None:
        action = {'type': 'ssjj_route', 'map_id': route.map_id, 'points': route.points}
        if final_yaw is not None:
            action['final_yaw'] = final_yaw
        if final_pitch is not None:
            action['final_pitch'] = final_pitch
        return ctx.action(action)
    suspended = 0.0

    def clock():
        return time.monotonic() - suspended

    def sample():
        nonlocal suspended
        start = time.monotonic()
        ctx.checkpoint()
        suspended += max(0, time.monotonic() - start)
        reader.require_foreground()
        value = reader.snapshot_stable()
        _valid(value, route.map_id)
        return value

    def fresh(previous):
        deadline = clock() + 1
        while True:
            value = sample()
            if value.client_time != previous.client_time:
                return value
            if clock() >= deadline:
                raise RuntimeError('角色状态快照未更新，停止跑图')
            ctx.wait(ms=10)

    def settled(previous, axis, *, calibration=False, expected=None):
        feedback = _TurnFeedback(previous, axis, clock(), expected)
        while True:
            value = sample()
            now = clock()
            ready = feedback.update(value, now)
            # A quiet gap can separate fragments. Unknown calibration gain
            # requires the complete bounded observation window, not the first gap.
            expired = now - feedback.started >= 1 - 1e-8
            if ready and (not calibration or expired):
                return value
            if expired:
                if value.client_time == previous.client_time:
                    raise RuntimeError('角色状态快照未更新，停止跑图')
                if feedback.responded:
                    raise RuntimeError('鼠标转向响应持续变化，1 秒内未稳定，已停止跑图')
                return value
            ctx.wait(ms=10)

    fresh_mark = None

    def stopped(previous, *, timeout=1.5):
        """Wait until the character really stops moving.

        松开 W 之后角色还会滑行一段时间；这段时间里的读数不能用来判断“到点”或
        “继续走”，否则控制器会追着自己滑行的轨迹反复折返。遥测停更时按原规则报错。
        """
        nonlocal fresh_mark
        stable = 0
        value = previous
        fresh_mark = None
        started = clock()
        deadline = started + timeout
        while True:
            value = sample()
            if value.client_time != previous.client_time:
                if fresh_mark is None:
                    fresh_mark = value
                started = clock()
                if math.dist(value.position, previous.position) <= 1:
                    stable += 1
                    if stable >= 2:
                        return value
                else:
                    stable = 0
                previous = value
            elif clock() - started >= 1:
                raise RuntimeError('角色状态快照未更新，停止跑图')
            if clock() >= deadline:
                return value
            ctx.wait(ms=10)

    moving = False
    try:
        current = sample()
        if not current.on_ground:
            raise RuntimeError('请落地后开始跑图')
        # A replay releases its recorded keys before correction, but momentum
        # survives the release. Never accept a waypoint from that moving state.
        ctx.move()
        # A recording reuses this context across legs. Calibrate once, before any
        # recorded jump/fire can run, and keep the aligned heading between legs.
        sensitivity = getattr(ctx, '_route_sensitivity', None)
        if sensitivity is not None:
            ctx.brake()
            current = stopped(current)
        if sensitivity is None:
            ctx.move(dx=16)
            measured = settled(current, 'yaw', calibration=True)
            sensitivity = ((measured.yaw - current.yaw + 180) % 360 - 180) / 16
            if abs(sensitivity) < .01:
                raise RuntimeError('鼠标朝向校准失败：1 秒内未读到朝向变化，请确认游戏内可正常转动视角')
            if abs(sensitivity) > 5:
                raise RuntimeError(f'鼠标朝向校准失败：测得灵敏度 {sensitivity:.3f} 度/单位，超出有效范围')
            ctx._route_sensitivity = sensitivity
            current = measured
            ctx.log(f'水平转向校准完成：{sensitivity:.5f} 度/单位（已等待响应稳定）')
        updated = clock()
        speed = 600.0
        index = start_index
        best = best_angle = math.inf
        progressed = started = updated
        move_deadline = updated
        turn_pending = None
        diagnostics_at = updated
        wrong_way = 0.0
        wrong_way_ticks = 0
        settled_now = True
        goal_stop = False
        pulse_length = 0.0
        pulse_state = None
        release_state = None
        coast_tau = getattr(ctx, '_route_coast_tau', 0.0)

        def stop_and_settle(value):
            """Wait for the slide to end, then learn from the stop and from the pulse.

            松开 W 之后角色可能继续滑行；停稳前的读数不能用来判定到点或继续走。
            停稳后：用这次滑行实测出滑行时间常数，用这次点按的实际位移校准下一次时长。
            """
            nonlocal pulse_length, pulse_state, release_state, coast_tau
            value = stopped(value)
            if release_state is not None:
                _, released_at_speed = release_state
                # 只统计松键后“观测到”的位移：松键瞬间的采样可能已经落后一个节拍，
                # 那段位移属于采样延迟，不是滑行。
                mark = fresh_mark if fresh_mark is not None else value
                coast = math.dist(value.position, mark.position)
                if coast > 1 and released_at_speed > 1:
                    coast_tau = min(1.5, max(coast_tau, coast / released_at_speed))
                    ctx._route_coast_tau = coast_tau
                release_state = None
            if pulse_state is not None:
                _, origin, seconds = pulse_state
                remaining = math.dist(value.position, target)
                traveled = math.dist(value.position, origin)
                if traveled > 1 and seconds > 0:
                    # 按“本次点按走了多远”重新瞄准下一次点按，冲过头就自动变短。
                    want = max(remaining - tolerance / 2, 0.0)
                    pulse_length = min(PULSE_MAX, max(
                        PULSE_MIN, seconds * want / traveled))
                pulse_state = None
            return value
        ctx.log(f'SSJJ 路线：地图 {route.map_id}，{len(route.points)} 个点')
        while index < len(route.points):
            now = clock()
            if on_progress is not None:
                before_progress = time.monotonic()
                current, next_index = on_progress(current, index)
                suspended += max(0, time.monotonic() - before_progress)
                _valid(current, route.map_id)
                if next_index > index:
                    # A completed dwell consumes its waypoint even when a push
                    # changed the position while the recorded actions ran.
                    index = next_index
                    moving = ctx.forward
                    turn_pending = None
                    best = best_angle = math.inf
                    progressed = started = clock()
                    continue
            if turn_pending is not None:
                if turn_pending.update(current, now):
                    turn_pending = None
                elif now - turn_pending.started >= 1:
                    raise RuntimeError('鼠标转向未响应或未稳定，已停止跑图')
            target = route.points[index]
            terminal = index == len(route.points) - 1
            corner = index in stop_indices or _corner(route.points, index)
            tolerance = 20 if terminal else (12 if strict_path else 25 if corner else 80)
            distance, error = steering(current, target)
            passed = (not terminal and not corner and index > 0 and
                      _passed(current.position, route.points[index - 1], target))
            reached = distance <= tolerance and abs(current.position[2] - target[2]) <= 25
            # 终点要停稳：先松开 W，等角色真正停下，再判定到点或补一小步。滑行距离
            # 由上一次停稳实测（coast_tau）。全速掠过容差窗口时采样落不进去，因此
            # 一旦发现“已经越过最近点”也立刻松键，让角色先停下来再微调。
            coast = speed * coast_tau
            crossed = terminal and moving and math.isfinite(best) and distance > best + 2
            if terminal:
                if moving and (reached or crossed or distance <= coast + tolerance):
                    release_state = (current.position, speed)
                    ctx.brake()
                    moving = False
                    settled_now = False
                    updated = clock()
                    continue
                if not moving and not settled_now:
                    current = stop_and_settle(current)
                    settled_now = True
                    goal_stop = True
                    # 掠过目标点时的最近距离不再代表进展：从停稳后的位置重新计。
                    best = best_angle = math.inf
                    progressed = updated = clock()
                    continue
            if (reached or passed) and turn_pending is None:
                # Verify the endpoint after releasing, using a stopped state snapshot.
                if terminal and moving:
                    release_state = (current.position, speed)
                    ctx.brake()
                    moving = False
                    settled_now = False
                    updated = clock()
                    continue
                if terminal and not settled_now:
                    current = stop_and_settle(current)
                    settled_now = True
                    goal_stop = True
                    best = best_angle = math.inf
                    progressed = updated = clock()
                    continue
                ctx.log(f'到达路线点 {index + 1}/{len(route.points)}')
                index += 1
                best = best_angle = math.inf
                progressed = started = clock()
                continue
            if distance < best - 2:
                best, progressed = distance, now
            if not moving and abs(error) < best_angle - 1:
                best_angle, progressed = abs(error), now
            failure = ('超时' if now - started > 30 else
                       '无进展，可能卡住' if now - progressed > progress_timeout else None)
            if failure:
                raise RuntimeError(f'路线点 {index + 1} {failure}，已停止；'
                    f'实际坐标 {tuple(round(v, 2) for v in current.position)}，'
                    f'目标坐标 {tuple(round(v, 2) for v in target)}，'
                    f'剩余距离 {distance:.1f}，高度差 {target[2] - current.position[2]:.1f}，'
                    f'水平朝向差 {error:.1f}°')
            # Small turns happen while walking; large turns and wrong-height arrivals stop.
            walk_angle = 35 if moving else 1
            forward = abs(error) <= walk_angle and math.dist(
                current.position[:2], target[:2]) > 10
            forward = forward and (moving or turn_pending is None) and not reached
            if terminal and not settled_now:
                # 松键后的滑行还没结束：既不补 W，也不用滑行中的读数做决定。
                forward = False
            # 停在终点附近之后只用短点按微调：一次点按 + 滑行覆盖剩余距离，时长按
            # 上次点按的实测结果调整，几步内收敛进容差。点按必须按毫秒释放：等下一次
            # 遥测节拍才松键的话，最短点按也有一百多毫秒，一步就冲过容差。
            creep = terminal and goal_stop and forward and settled_now and not moving
            if creep:
                budget = pulse_length or min(PULSE_MAX, max(
                    PULSE_MIN, (distance - tolerance / 2) / max(speed, 1.0)))
            else:
                # A short renewable movement lease stops on missing updates. Near the
                # goal or a corner, measured speed limits travel before the next tick;
                # the endpoint also yields the slide measured after the last release.
                budget = min(.12, max(.005, (
                    distance - tolerance / 2 - (coast if terminal else 0)) / speed)) if (
                    terminal or corner) else .12
            dx = (round(error / sensitivity)
                  if abs(error) > 1 and turn_pending is None and not reached else 0)
            if dx:
                turn_pending = _TurnFeedback(current, 'yaw', now, current.yaw + dx * sensitivity)
            if creep:
                ctx.move(forward=True, dx=dx)
                if ctx.forward:
                    settled_now = False
                    pulse_state = (distance, current.position, budget)
                    ctx.wait(ms=max(1, int(round(budget * 1000))))
                    ctx.move()
                moving = ctx.forward
            else:
                ctx.move(forward=forward, dx=dx)
                moving = ctx.forward
                if moving:
                    pulse_state = None
            if moving and on_ready is not None:
                # The local action clock starts after turning and pressing W.
                on_ready()
                on_ready = None
            if now >= diagnostics_at:
                ctx.log(f'导航反馈：路线点 {index + 1}/{len(route.points)}，'
                        f'W={"按住" if moving else "松开"}，转向输入 {dx}，'
                        f'yaw {current.yaw:.2f}°，方向差 {error:.2f}°，距离 {distance:.1f}，'
                        f'坐标 {tuple(round(v, 2) for v in current.position)}，'
                        f'{"等待转向反馈" if turn_pending is not None else "转向已稳定"}', detail=True)
                diagnostics_at = now + .5
            move_deadline = clock() + budget
            previous = current
            travel_time = 0.0
            while True:
                before_wait = clock()
                was_moving = moving
                ctx.wait(ms=10)
                current = sample()
                now = clock()
                if was_moving:
                    travel_time += now - before_wait
                if current.client_time != previous.client_time:
                    displacement = math.dist(current.position, previous.position)
                    if travel_time > 0 and displacement > 1:
                        toward = tuple(b - a for a, b in zip(previous.position[:2], target[:2]))
                        delta = tuple(b - a for a, b in zip(previous.position[:2], current.position[:2]))
                        dot = sum(a * b for a, b in zip(toward, delta))
                        away = dot < -.5 * math.hypot(*toward) * math.hypot(*delta)
                        wrong_way = wrong_way + math.hypot(*delta) if away else 0.0
                        wrong_way_ticks = wrong_way_ticks + 1 if away else 0
                        if math.dist(current.position, target) > math.dist(previous.position, target) + 2:
                            # A knockback changes the origin, not the target. Restart
                            # distance progress so a large offset can be walked back.
                            best, progressed = math.dist(current.position, target), now
                        if wrong_way_ticks >= 3 and wrong_way >= 40:
                            raise RuntimeError('实际移动方向与目标相反，已停止；请核对游戏朝向和输入响应')
                    if travel_time > 0 and displacement > 1:
                        speed = max(100., displacement / travel_time)
                    updated = now
                    break
                if moving and now >= move_deadline:
                    if terminal:
                        release_state = (current.position, speed)
                        settled_now = False
                    ctx.brake()
                    moving = False
                if now - updated >= 1:
                    raise RuntimeError('角色状态快照未更新，停止跑图')
        if on_ready is not None:
            # Repeated-position marks have no walking phase but still own actions.
            on_ready()
        if final_yaw is not None or final_pitch is not None:
            # Re-read after stopping, even when the endpoint required no movement.
            ctx.move()
            current = fresh(current)
            heading_started = clock()
            pitch_sensitivity = getattr(ctx, '_route_pitch_sensitivity', None)
            for axis, target, gain in (('yaw', final_yaw, sensitivity),
                                       ('pitch', final_pitch, pitch_sensitivity)):
                if (axis == 'pitch' and target is not None and gain is None and
                        abs((target - current.pitch + 180) % 360 - 180) > 1):
                    # Calibrate after yaw alignment: the view may change during that turn.
                    # Vertical input has its own inversion and can hit a view limit.
                    for probe in (16, -16):
                        previous = current
                        ctx.move(dy=probe)
                        current = settled(previous, 'pitch', calibration=True)
                        measured = ((current.pitch - previous.pitch + 180) % 360 - 180) / probe
                        if abs(measured) >= .01:
                            if abs(measured) > 5:
                                raise RuntimeError('俯仰角校准失败：灵敏度超出有效范围')
                            # A probe toward the view limit can be clipped. The
                            # opposite probe measures the full response instead.
                            if gain is None or abs(measured) > abs(gain):
                                gain = measured
                    if gain is None:
                        raise RuntimeError('俯仰角校准失败：未读到视角变化，已停止')
                    ctx._route_pitch_sensitivity = gain
                while target is not None:
                    error = (target - getattr(current, axis) + 180) % 360 - 180
                    if abs(error) <= 1:
                        break
                    delta = round(error / gain)
                    if delta == 0:
                        break
                    if clock() - heading_started > 30:
                        raise RuntimeError('目标朝向对齐超时，已停止')
                    previous = current
                    ctx.move(**{'dx' if axis == 'yaw' else 'dy': delta})
                    current = settled(previous, axis, expected=getattr(previous, axis) + delta * gain)
                    if abs((getattr(current, axis) - getattr(previous, axis) + 180) % 360 - 180) < .001:
                        raise RuntimeError('目标朝向未响应，已停止')
            # A displacement during turning becomes the next leg's starting position.
            if not _at_heading(current, final_yaw, final_pitch):
                raise RuntimeError('目标朝向精度不足，已停止；请降低鼠标灵敏度')
        ctx.log('SSJJ 路线完成')
        return current
    finally:
        ctx.move()


def record_route(reader, seconds, *, spacing=100):
    """Read only: the user moves the player; no keyboard/mouse actions are sent."""
    if not math.isfinite(seconds) or not 1 <= seconds <= 3600:
        raise ValueError('录制时长需要在 1 到 3600 秒之间')
    initial = reader.snapshot_stable()
    _valid(initial, initial.map_id)
    points = [initial.position]
    end = time.monotonic() + seconds
    previous = initial
    latest = initial
    while time.monotonic() < end:
        time.sleep(0.1)
        latest = reader.snapshot_stable()
        _valid(latest, initial.map_id)
        if math.dist(previous.position, latest.position) > 1000:
            raise RuntimeError('录制中发生传送或坐标跳变，请重录')
        previous = latest
        if math.dist(points[-1], latest.position) >= spacing:
            points.append(latest.position)
    if math.dist(points[-1], latest.position) > 1:
        points.append(latest.position)
    return Route(initial.map_id, points)


def run_route_action(player, action):
    """Execute an embedded route through the normal script player.

    人不在这一局游戏里（没开、没进对局、不在前台、换图）时跳过这条路线并记一条
    日志：不把输入发给别的窗口，脚本继续往下执行，而不是整场异常收尾。
    """
    route = Route(action['map_id'], action['points'])
    try:
        reader = plugin.SSJJReader()
    except MemoryPluginUnavailable as exc:
        player._log_event(f'跳过坐标跑图：{exc}')
        return None
    except plugin.GameUnavailable as exc:
        player._log_event(f'跳过坐标跑图：{exc}')
        return None
    with reader as active:
        try:
            active.require_foreground()
        except plugin.GameUnavailable as exc:
            player._log_event(f'跳过坐标跑图：{exc}')
            return None
        context = RouteContext(player, active, route.map_id)
        try:
            follow_route(context, route, reader=active, final_yaw=action.get('final_yaw'),
                         final_pitch=action.get('final_pitch'))
        except plugin.GameUnavailable as exc:
            player._log_event(f'坐标跑图中断：{exc}；跳过剩余路径，脚本继续执行')
            return None
        finally:
            context.close()


def main():
    parser = argparse.ArgumentParser(description='SSJJ 只读路线录制；手动走路线')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--delay', type=float, default=5)
    args = parser.parse_args()
    if not math.isfinite(args.delay) or not 0 <= args.delay <= 60:
        parser.error('delay 必须在 0 到 60 秒之间')
    print(f'{args.delay:g} 秒后开始录制，请切回游戏，手动走一段平地路线。', flush=True)
    time.sleep(args.delay)
    with plugin.SSJJReader() as reader:
        route = record_route(reader, args.seconds)
    route.save(args.output)
    print(f'已保存地图 {route.map_id} 的 {len(route.points)} 个点：{args.output}', flush=True)


if __name__ == '__main__':
    main()
