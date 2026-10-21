"""Reproduce the battle-client TCP monitor without launching another program."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import psutil


TARGET_PROCESS = "SSJJ_BattleClient_Unity.exe"
CHECK_INTERVAL_SECONDS = 5
STARTUP_GRACE_SECONDS = 20
# 连续多少次异常才结束战斗端：单次抖动（加载、切图、服务器瞬断）不算掉线。
CONSECUTIVE_FAILURES = 3


class DisconnectMonitor:
    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self._stop = threading.Event()
        self._stop.set()
        self._target = None
        self._failures = 0
        self._reason = ""

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
        while not stop.wait(CHECK_INTERVAL_SECONDS):
            try:
                self.check_once(stop)
            except (psutil.Error, OSError) as exc:
                self.log(f"断线检测失败：{exc}")

    def _clear(self) -> None:
        self._target = None
        self._failures = 0
        self._reason = ""

    def check_once(self, stop: threading.Event) -> None:
        now = time.time()
        found = False
        for process in psutil.process_iter(["name", "create_time"]):
            if stop.is_set():
                return
            info = process.info
            if str(info["name"] or "").casefold() != TARGET_PROCESS.casefold():
                continue
            found = True
            created = info["create_time"]
            identity = (process.pid, created)
            if identity != self._target:
                # 新启动的战斗端（含结束后的新实例）重新开始计数。
                self._target, self._failures, self._reason = identity, 0, ""
            if created is None or now - created <= STARTUP_GRACE_SECONDS:
                continue
            try:
                connections = [
                    item for item in process.net_connections(kind="tcp4")
                    if not item.raddr or (item.raddr.ip != "127.0.0.1" and item.raddr.port != 80)
                ]
                if connections and all(item.status == psutil.CONN_ESTABLISHED
                                       for item in connections):
                    self._failures, self._reason = 0, ""
                    continue
                self._failures += 1
                self._reason = (", ".join(sorted({item.status for item in connections}))
                                or "无有效 TCP 连接")
                if self._failures < CONSECUTIVE_FAILURES:
                    if self._failures == 1:
                        self.log(f"断线检测：战斗端 {TARGET_PROCESS}（PID {process.pid}）连接异常"
                                 f"（{self._reason}），连续 {CONSECUTIVE_FAILURES} 次异常才结束。")
                    continue
                if stop.is_set():
                    return
                # psutil.kill checks process identity before terminating, preventing PID reuse.
                process.kill()
                self.log(f"断线检测：战斗端 {TARGET_PROCESS} 连续 {self._failures} 次连接异常"
                         f"（PID {process.pid}；{self._reason}），已结束。")
                self._clear()
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, OSError) as exc:
                # An unreadable connection table is not evidence of disconnection.
                self.log(f"断线检测：无法检查或结束战斗端 PID {process.pid}：{exc}")
        if not found:
            # 战斗端当前没在运行：清空连续计数，下次启动重新数。
            self._clear()
