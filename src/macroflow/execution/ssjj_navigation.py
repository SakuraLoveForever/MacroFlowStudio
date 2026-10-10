"""Taught routes in SSJJ raw coordinates (XY horizontal, Z height)."""
from __future__ import annotations

import argparse
import json
import math
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


def send_route_input(player, reader, map_id, *, dx=None, hold_ms=None):
    """Parent-process boundary: recheck after RPC waits/guards; never hold across pause."""
    from macroflow.input.wininput import send_key, send_move_relative
    from .player.control import PlaybackStopped

    if (dx is None) == (hold_ms is None):
        raise ValueError('需要且只能指定转向或前进')
    if dx is not None and (type(dx) is not int or not -200 <= dx <= 200):
        raise ValueError('转向超出范围')
    if hold_ms is not None and (type(hold_ms) is not int or not 1 <= hold_ms <= 150):
        raise ValueError('按键时长超出范围')
    _valid(reader.snapshot(), map_id)
    reader.require_foreground()
    if player.stop_event.is_set():
        raise PlaybackStopped()
    if player.paused:
        return  # The script loop pauses before accepting another request.
    if dx is not None:
        send_move_relative(dx, 0)
        return
    player._held_keys.add(87)
    try:
        send_key(87, True)
        deadline = time.monotonic() + hold_ms / 1000
        while time.monotonic() < deadline:
            if player.stop_event.wait(min(0.01, max(0, deadline - time.monotonic()))):
                raise PlaybackStopped()
            if player.paused:
                break
            reader.require_foreground()
    finally:
        send_key(87, False)
        player._held_keys.discard(87)


def follow_route(ctx, route, *, reader=None, progress_timeout=3.0):
    """Follow once using input checked in Macro's parent process."""
    if not math.isfinite(progress_timeout) or progress_timeout <= 0:
        raise ValueError('进展超时必须大于零')
    owned = reader is None
    reader = reader or SSJJReader()
    try:
        suspended = 0.0

        def rpc(call, active_seconds=0):
            nonlocal suspended
            start = time.monotonic()
            result = call()
            # Parent may pause or run guards before accepting the request.
            suspended += max(0, time.monotonic() - start - active_seconds)
            return result

        def clock():
            return time.monotonic() - suspended

        def wait(ms):
            rpc(lambda: ctx.wait(ms=ms), ms / 1000)

        def sample():
            rpc(ctx.checkpoint)
            reader.require_foreground()
            value = reader.snapshot()
            _valid(value, route.map_id)
            return value

        def route_input(*, dx=None, hold_ms=None):
            before = sample()
            rpc(lambda: ctx.ssjj_input(route.map_id, dx=dx, hold_ms=hold_ms),
                (hold_ms or 0) / 1000)
            deadline = clock() + 1
            wait(150)
            after = sample()
            while after.client_time == before.client_time:
                if clock() >= deadline:
                    raise RuntimeError('角色状态快照未更新，停止跑图')
                wait(20)
                after = sample()
            return after

        current = sample()
        if math.dist(current.position, route.points[0]) > 250:
            raise RuntimeError('距离路线起点超过 250，请先回到起点')
        if not current.on_ground:
            raise RuntimeError('请落地后开始跑图')
        # Measure signed sensitivity rather than assume game settings or OS pointer speed.
        measured = route_input(dx=16)
        delta = (measured.yaw - current.yaw + 180) % 360 - 180
        sensitivity = delta / 16
        if not 0.01 <= abs(sensitivity) <= 5:
            raise RuntimeError('鼠标朝向校准失败，请关闭菜单并保持游戏前台')
        ctx.log(f'SSJJ 路线：地图 {route.map_id}，{len(route.points)} 个点')
        for index, target in enumerate(route.points):
            best = math.inf
            progressed = started = clock()
            while True:
                current = sample()
                distance, error = steering(current, target)
                if distance <= 80:
                    break
                now = clock()
                if distance < best - 5:
                    best, progressed = distance, now
                if now - started > 30:
                    raise RuntimeError(f'路线点 {index + 1} 超时，已停止')
                if abs(error) > 8:
                    route_input(dx=max(-200, min(200, round(error / sensitivity))))
                    progressed = clock()
                    continue
                if now - progressed > progress_timeout:
                    raise RuntimeError(f'路线点 {index + 1} 无进展，可能卡住，已停止')
                sample()
                route_input(hold_ms=max(20, min(150, round(distance / 2))))
            ctx.log(f'到达路线点 {index + 1}/{len(route.points)}')
        ctx.log('SSJJ 路线完成')
    finally:
        if owned:
            reader.close()


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
    from .player.control import PlaybackStopped

    route = Route(action['map_id'], action['points'])
    with SSJJReader() as reader:
        class Context:
            def checkpoint(self):
                player.wait_while_paused()
                if player.stop_event.is_set():
                    raise PlaybackStopped()
                player._poll_guards()
                player._settle_after_guard()

            def wait(self, ms=0):
                self.checkpoint()
                player._wait(ms)

            def log(self, message):
                player._log_event(message)

            def ssjj_input(self, map_id, *, dx=None, hold_ms=None):
                self.checkpoint()
                return send_route_input(player, reader, map_id, dx=dx, hold_ms=hold_ms)

        follow_route(Context(), route, reader=reader)


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
