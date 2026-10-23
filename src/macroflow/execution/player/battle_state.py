"""战斗端内存状态：只读 SSJJReader，读到当前可见的战斗界面。

大厅是 Adobe AIR/Flash，没有可读的页面状态；战斗端是 Unity/Mono，
服务端下发的中文 UI（如“小地图UI”“对战战绩UI”）都挂在
FreeUiManager._uis 里，可见性由 SimpleFreeUI._visible 表示。
读取失败（不在对局、游戏未加载完）不打断脚本：退避一段时间再试。
"""

from __future__ import annotations

import time

from ..memory_plugin import MemoryPluginUnavailable, plugin

BATTLE_STATE_RETRY_S = 3.0


class BattleStateMixin:
    """战斗端内存读取：UI 可见状态与探针。"""

    def _battle_reader(self, *, force: bool = False):
        """惰性创建只读读取器；不在对局时返回 None，并退避重试。"""
        reader = getattr(self, "_battle_reader_cache", None)
        if reader is not None:
            return reader
        now = time.perf_counter()
        if not force and now < getattr(self, "_battle_reader_retry_at", 0.0):
            return None
        try:
            reader = plugin.SSJJReader()
        except MemoryPluginUnavailable as exc:
            self._battle_reader_retry_at = now + BATTLE_STATE_RETRY_S
            self._log_battle_once(f"战斗内存不可用：{exc}")
            return None
        except plugin.GameUnavailable as exc:
            self._battle_reader_retry_at = now + BATTLE_STATE_RETRY_S
            self._log_battle_once(f"战斗内存不可用：{exc}")
            return None
        except Exception as exc:
            self._battle_reader_retry_at = now + BATTLE_STATE_RETRY_S
            self._log_battle_once(f"战斗内存读取失败：{exc}")
            return None
        self._battle_reader_cache = reader
        self._battle_reader_retry_at = 0.0
        return reader

    def _battle_reset(self) -> None:
        """关闭读取器并清空缓存；下次用到时重新连接。"""
        reader = getattr(self, "_battle_reader_cache", None)
        self._battle_reader_cache = None
        self._battle_ui_state_cache = None
        self._battle_reader_retry_at = time.perf_counter() + BATTLE_STATE_RETRY_S
        if reader is not None:
            try:
                reader.close()
            except Exception:
                pass

    def _log_battle_once(self, text: str) -> None:
        seen = getattr(self, "_battle_logged", None)
        if seen is None:
            seen = self._battle_logged = set()
        if text in seen:
            return
        seen.add(text)
        self._log_event(text)

    def _battle_ui_state(self):
        reader = self._battle_reader()
        if reader is None:
            return None
        state = getattr(self, "_battle_ui_state_cache", None)
        if state is None or getattr(state, "reader", None) is not reader:
            state = self._battle_ui_state_cache = plugin.SSJJUIState(reader)
        return state

    def battle_ui_snapshot(self) -> dict[str, bool] | None:
        """当前战斗界面可见状态 {UI 名: 是否可见}；不在对局返回 None。"""
        state = self._battle_ui_state()
        if state is None:
            return None
        try:
            return state.ui_snapshot()
        except plugin.GameUnavailable as exc:
            self._log_battle_once(f"战斗内存不可用：{exc}")
            self._battle_reset()
            return None
        except RuntimeError as exc:
            self._log_battle_once(f"战斗界面读取失败：{exc}")
            return None

    def battle_ui_visible(self, key: str) -> bool:
        snapshot = self.battle_ui_snapshot() or {}
        return bool(snapshot.get(str(key or "").strip()))

    def battle_ui_names(self) -> list[str]:
        snapshot = self.battle_ui_snapshot() or {}
        return [name for name, visible in snapshot.items() if visible]

    def battle_probe(self) -> tuple[dict, dict[str, bool]]:
        """界面探针：返回 (玩家/地图摘要, 全部战斗界面状态)。

        不在对局或读取失败时抛 GameUnavailable/RuntimeError，供界面提示。
        """
        reader = self._battle_reader(force=True)
        if reader is None:
            raise plugin.GameUnavailable("当前不在对局中，读不到战斗内存")
        snapshot = plugin.SSJJUIState(reader).ui_snapshot()
        summary: dict = {}
        try:
            summary["map_id"] = reader.map_id()
        except Exception:
            pass
        try:
            player = reader.snapshot(include_details=True)
            summary["dead"] = player.dead
            details = player.details
            if details is not None:
                summary["hp"] = details.hp
                summary["max_hp"] = details.max_hp
        except Exception:
            pass
        return summary, snapshot