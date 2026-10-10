"""Reproduce the battle-client TCP monitor without launching another program."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import psutil


TARGET_PROCESS = "SSJJ_BattleClient_Unity.exe"


class DisconnectMonitor:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self._stop = threading.Event()
        self._stop.set()

    def start(self) -> None:
        if not self._stop.is_set():
            return
        # Each run owns its event: re-enabling cannot revive an old scan.
        self._stop = threading.Event()
        threading.Thread(target=self._run, args=(self._stop,),
                         name="battle-disconnect-monitor", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(5):
            try:
                self.check_once(stop)
            except (psutil.Error, OSError) as exc:
                self.log(f"断线检测失败：{exc}")

    def check_once(self, stop: threading.Event) -> None:
        now = time.time()
        for process in psutil.process_iter(["name", "create_time"]):
            if stop.is_set():
                return
            info = process.info
            if str(info["name"] or "").casefold() != TARGET_PROCESS.casefold():
                continue
            created = info["create_time"]
            if created is None or now - created <= 20:
                continue
            try:
                connections = [
                    item for item in process.net_connections(kind="tcp4")
                    if not item.raddr or (item.raddr.ip != "127.0.0.1" and item.raddr.port != 80)
                ]
                if connections and all(item.status == psutil.CONN_ESTABLISHED
                                       for item in connections):
                    continue
                if stop.is_set():
                    return
                # psutil.kill checks process identity before terminating, preventing PID reuse.
                process.kill()
                states = ", ".join(sorted({item.status for item in connections})) or "无有效 TCP 连接"
                self.log(f"断线检测：已结束战斗端 {TARGET_PROCESS}（PID {process.pid}；{states}）。")
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, OSError) as exc:
                # An unreadable connection table is not evidence of disconnection.
                self.log(f"断线检测：无法检查或结束战斗端 PID {process.pid}：{exc}")
