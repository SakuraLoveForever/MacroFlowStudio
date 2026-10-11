"""Read-only worker and incremental route capture; independent of Tk."""
from __future__ import annotations

import math
import threading
import time

from .ssjj_navigation import Route, _valid
from .ssjj_state import SSJJReader


class RouteCapture:
    def __init__(self, initial):
        _valid(initial, initial.map_id)
        self.map_id = initial.map_id
        self.points = [initial.position]
        self.latest = initial
        self.error = ''

    def feed(self, sample):
        if self.error:
            raise RuntimeError(self.error)
        try:
            _valid(sample, self.map_id)
            if math.dist(self.latest.position, sample.position) > 1000:
                raise RuntimeError('录制中发生坐标跳变，请取消后重录')
            self.latest = sample
            if math.dist(self.points[-1], sample.position) >= 100:
                if len(self.points) >= 99999:
                    raise RuntimeError('路线点数量过多，请分段录制')
                self.points.append(sample.position)
        except Exception as exc:
            self.error = str(exc)
            raise

    def finish(self):
        if self.error:
            raise RuntimeError(self.error)
        points = list(self.points)
        if math.dist(points[-1], self.latest.position) > 1:
            points.append(self.latest.position)
        route = Route(self.map_id, points)
        return {'type': 'ssjj_route', 'map_id': route.map_id,
                'points': route.points, 'delay_ms': 0}


class SSJJMonitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.latest = None
        self.updated = 0.0
        self.error = '正在连接游戏…'
        self.capture = None
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
                    sample = reader.snapshot(include_details=True)
                    if sample.client_time != client_time:
                        client_time, progressed = sample.client_time, time.monotonic()
                    elif time.monotonic() - progressed > 1.5:
                        raise RuntimeError('游戏状态时间未更新，等待游戏恢复')
                    with self.lock:
                        self.latest, self.updated, self.error = sample, time.monotonic(), ''
                        if self.capture is not None:
                            self.capture.feed(sample)
                except Exception as exc:
                    with self.lock:
                        self.latest, self.error = None, str(exc)
                        if self.capture is not None:
                            self.capture.error = str(exc)
                    if reader is not None:
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
            count = len(self.capture.points) if self.capture is not None else 0
            return latest, self.error, self.capture is not None, count

    def start_recording(self):
        with self.lock:
            if self.capture is not None:
                raise RuntimeError('已有路线正在录制')
            if self.latest is None or time.monotonic() - self.updated >= .75:
                raise RuntimeError(self.error or '当前坐标已过期，请等待游戏连接')
            self.capture = RouteCapture(self.latest)

    def stop_recording(self):
        with self.lock:
            if self.capture is None:
                raise RuntimeError('尚未开始路线录制')
            if time.monotonic() - self.updated >= .75:
                raise RuntimeError('坐标已过期，请取消后重录')
            action = self.capture.finish()
            self.capture = None
            return action

    def cancel_recording(self):
        with self.lock:
            self.capture = None

    def close(self):
        self.stop.set()
        self.thread.join(timeout=.5)
