"""Taught routes in SSJJ raw coordinates (XY horizontal, Z height)."""
from __future__ import annotations

import argparse
import json
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .ssjj_state import SSJJReader


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


def _valid(sample, map_id):
    if sample.map_id != map_id:
        raise RuntimeError('地图已变化，停止跑图')
    if sample.dead:
        raise RuntimeError('角色已死亡，停止跑图')
    if not all(math.isfinite(v) for v in (*sample.position, sample.yaw)):
        raise RuntimeError('角色坐标无效，停止跑图')


class RouteContext:
    """Parent-owned continuous input, released before pauses and guard handlers."""
    def __init__(self, player, reader, map_id):
        self.player, self.reader, self.map_id = player, reader, map_id
        self.forward = False
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._deadline = 0.0
        self._error = None
        self._watchdog = threading.Thread(target=self._watch, name='ssjj-input-watchdog', daemon=True)
        self._watchdog.start()

    def release(self):
        from macroflow.input.wininput import send_key
        with self._lock:
            if self.forward:
                send_key(87, False)
                self.forward = False
                self.player._held_keys.discard(87)

    def _watch(self):
        # A stalled guard/reader must not keep W down while the controller is blocked.
        while not self._closed.wait(.01):
            try:
                with self._lock:
                    if not self.forward:
                        continue
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

    def move(self, *, forward=False, dx=0):
        from macroflow.input.wininput import send_key, send_move_relative
        from .player.control import PlaybackStopped
        if type(forward) is not bool or type(dx) is not int or not -200 <= dx <= 200:
            raise ValueError('无效的路线输入')
        if not forward:
            self.release()
        if not forward and not dx:
            return  # Release must work even when telemetry or focus is unavailable.
        try:
            if self._error is not None:
                raise self._error
            _valid(self.reader.snapshot(), self.map_id)
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
                if dx:
                    send_move_relative(dx, 0)
                if self.player.paused or self.player.stop_event.is_set():
                    self.release()
        except BaseException:
            self.release()
            raise

    def checkpoint(self):
        from .player.control import PlaybackStopped
        if self._error is not None:
            raise self._error
        if self.player.paused or self.player.stop_event.is_set():
            self.release()
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

    def log(self, message):
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
    return (x * dx + y * dy >= 0 and abs(x * dy - y * dx) / length <= 40)


def follow_route(ctx, route, *, reader=None, progress_timeout=3.0):
    """Track fresh state continuously; Python scripts delegate the entire action."""
    if not math.isfinite(progress_timeout) or progress_timeout <= 0:
        raise ValueError('进展超时必须大于零')
    if reader is None:
        return ctx.action({'type': 'ssjj_route', 'map_id': route.map_id,
                           'points': route.points})
    suspended = 0.0

    def clock():
        return time.monotonic() - suspended

    def sample():
        nonlocal suspended
        start = time.monotonic()
        ctx.checkpoint()
        suspended += max(0, time.monotonic() - start)
        reader.require_foreground()
        value = reader.snapshot()
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

    moving = False
    try:
        current = sample()
        if math.dist(current.position, route.points[0]) > 250:
            raise RuntimeError('距离路线起点超过 250，请先回到起点')
        if not current.on_ground:
            raise RuntimeError('请落地后开始跑图')
        # Calibrate while stationary; steering updates then use each fresh game tick.
        ctx.move(dx=16)
        deadline = clock() + 1
        while True:
            measured = sample()
            sensitivity = ((measured.yaw - current.yaw + 180) % 360 - 180) / 16
            # A new game tick can arrive before the mouse response is published.
            if measured.client_time != current.client_time and abs(sensitivity) >= .01:
                break
            if clock() >= deadline:
                if measured.client_time == current.client_time:
                    raise RuntimeError('角色状态快照未更新，停止跑图')
                raise RuntimeError('鼠标朝向校准失败：1 秒内未读到朝向变化，请确认游戏内可正常转动视角')
            ctx.wait(ms=10)
        if abs(sensitivity) > 5:
            raise RuntimeError(f'鼠标朝向校准失败：测得灵敏度 {sensitivity:.3f} 度/单位，超出有效范围')
        current = measured
        updated = clock()
        speed = 600.0
        index = 0
        best = best_angle = math.inf
        progressed = started = updated
        move_deadline = updated
        ctx.log(f'SSJJ 路线：地图 {route.map_id}，{len(route.points)} 个点')
        while index < len(route.points):
            target = route.points[index]
            terminal = index == len(route.points) - 1
            corner = _corner(route.points, index)
            tolerance = 20 if terminal else (25 if corner else 80)
            distance, error = steering(current, target)
            passed = (not terminal and not corner and index > 0 and
                      _passed(current.position, route.points[index - 1], target))
            reached = distance <= tolerance and abs(current.position[2] - target[2]) <= 25
            if reached or passed:
                # Verify the endpoint after releasing, using a newer state snapshot.
                if terminal and moving:
                    ctx.move()
                    moving = False
                    current = fresh(current)
                    updated = clock()
                    continue
                ctx.log(f'到达路线点 {index + 1}/{len(route.points)}')
                index += 1
                best = best_angle = math.inf
                progressed = started = clock()
                continue
            now = clock()
            if distance < best - 2:
                best, progressed = distance, now
            if not moving and abs(error) < best_angle - 1:
                best_angle, progressed = abs(error), now
            if now - started > 30:
                raise RuntimeError(f'路线点 {index + 1} 超时，已停止')
            if now - progressed > progress_timeout:
                raise RuntimeError(f'路线点 {index + 1} 无进展，可能卡住，已停止')
            # Small turns happen while walking; large turns and wrong-height arrivals stop.
            forward = abs(error) <= (35 if moving else 20) and math.dist(
                current.position[:2], target[:2]) > 10
            dx = max(-200, min(200, round(error / sensitivity))) if abs(error) > 1 else 0
            ctx.move(forward=forward, dx=dx)
            moving = forward
            # A short renewable movement lease stops on missing updates. Near a corner
            # or the goal, measured speed limits travel before the next telemetry tick.
            budget = min(.12, max(.005, (distance - tolerance / 2) / speed)) if (
                terminal or corner) else .12
            move_deadline = now + budget
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
                    if displacement > 1000:
                        raise RuntimeError('跑图中发生传送或坐标跳变，已停止')
                    if travel_time > 0 and displacement > 1:
                        speed = max(100., displacement / travel_time)
                    updated = now
                    break
                if moving and now >= move_deadline:
                    ctx.move()
                    moving = False
                if now - updated >= 1:
                    raise RuntimeError('角色状态快照未更新，停止跑图')
        ctx.log('SSJJ 路线完成')
    finally:
        ctx.move()


def record_route(reader, seconds, *, spacing=100):
    """Read only: the user moves the player; no keyboard/mouse actions are sent."""
    if not math.isfinite(seconds) or not 1 <= seconds <= 3600:
        raise ValueError('录制时长需要在 1 到 3600 秒之间')
    initial = reader.snapshot()
    _valid(initial, initial.map_id)
    points = [initial.position]
    end = time.monotonic() + seconds
    previous = initial
    latest = initial
    while time.monotonic() < end:
        time.sleep(0.1)
        latest = reader.snapshot()
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
    """Execute an embedded route through the normal script player."""
    route = Route(action['map_id'], action['points'])
    with SSJJReader() as reader:
        context = RouteContext(player, reader, route.map_id)
        try:
            follow_route(context, route, reader=reader)
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
    with SSJJReader() as reader:
        route = record_route(reader, args.seconds)
    route.save(args.output)
    print(f'已保存地图 {route.map_id} 的 {len(route.points)} 个点：{args.output}', flush=True)


if __name__ == '__main__':
    main()
