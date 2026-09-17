"""脚本编辑的纯业务层：列表行更新与撤销记录。

这一层**不许**碰 Tk：没有控件、Tk Variable、messagebox，也不调 root.after。
它只做三件事：

* ``reconcile_action_rows`` —— 由「旧动作列表 + 新动作列表」算出更新 Treeview
  所需的最小操作（删 / 移 / 插）与需要重画的行。
* ``action_row_values`` —— 一行动作在列表里显示的四个值。
* ``ActionEditHistory`` —— 按动作 ID 记录撤销/重做，只保存真正改过的行。

界面层（``ScriptsMixin``）只负责把这里的结果落到控件上。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

from macroflow.core.models import ACTION_ID_KEY


# ---------------------------------------------------------------------------
# 行索引：一次刷新只读一次模块配置
# ---------------------------------------------------------------------------


class ActionRowIndex:
    """一次刷新的行索引：动作 ID → 行号，以及模块配置快照。

    ``module_objects`` 是本次刷新读到的模块仓库快照。摘要里查模块时用它，
    不再逐行调 ``registered_module_object``——那会为每一行引用模块重新
    读盘 + 解析一次 ``template_regions.json``。
    """

    __slots__ = ("rows", "module_objects")

    def __init__(self, actions: list[dict], module_objects: dict):
        self.rows: dict[str, int] = {
            str(action.get(ACTION_ID_KEY, "")): index + 1
            for index, action in enumerate(actions)
            if action.get(ACTION_ID_KEY)
        }
        self.module_objects = module_objects

    def row_of(self, action_id) -> int | None:
        raw = str(action_id or "").strip()
        return self.rows.get(raw) if raw else None

    def refresh(self, actions: list[dict], module_objects: dict | None = None) -> None:
        """动作列表顺序变了以后重建行号表（模块快照默认沿用本次的值）。"""
        self.rows = {
            str(action.get(ACTION_ID_KEY, "")): index + 1
            for index, action in enumerate(actions)
            if action.get(ACTION_ID_KEY)
        }
        if module_objects is not None:
            self.module_objects = module_objects


def module_objects_snapshot() -> dict:
    """读一次模块仓库快照（唯一允许的一次读盘）。

    旧脚本里存的是图片相对路径而不是模块 ID：把模板路径与它的项目相对路径
    也映射到同一个对象，摘要层就不必再走 ``registered_module_object``
    （那条路会为每一行引用模块重新读盘一次）。
    """
    from macroflow.core.storage import display_path, load_module_objects

    resolved: dict = {}
    for key, obj in load_module_objects().items():
        resolved[key] = obj
        template = str(obj.get("template", "")).strip()
        if not template:
            continue
        resolved.setdefault(template, obj)
        try:
            resolved.setdefault(display_path(template), obj)
        except (OSError, ValueError):
            pass
    return resolved


# ---------------------------------------------------------------------------
# 行渲染
# ---------------------------------------------------------------------------


def action_row_values(action: dict, index: int, action_rows: ActionRowIndex) -> tuple:
    """一行动作在列表里显示的值：(片段竖条, 行号, 动作, 参数, 执行前延时)。"""
    from macroflow.ui.app.summaries import action_summary

    kind, detail, delay = action_summary(action, action_rows.rows, action_rows.module_objects)
    if action.get("failure_segment_enabled"):
        detail += f" · 失败后执行代码段 {len(action.get('failure_actions') or [])} 项"
    return ("", index + 1, kind, detail, delay)


def action_id_of(action: dict) -> str:
    return str(action.get(ACTION_ID_KEY, "")).strip()


# ---------------------------------------------------------------------------
# 列表与数据的对齐
# ---------------------------------------------------------------------------


@dataclass
class RowEdit:
    """把 Treeview 从旧顺序对齐到新顺序所需的最小操作。"""

    delete: list[int] = field(default_factory=list)
    insert: list[tuple[int, int]] = field(default_factory=list)   # (新行下标, 旧行下标)
    move: list[tuple[int, int]] = field(default_factory=list)     # (新行下标, 移动前下标)
    changed: list[int] = field(default_factory=list)              # 需要重画的行

    @property
    def structural(self) -> bool:
        return bool(self.delete or self.insert or self.move)

    def __bool__(self) -> bool:
        return self.structural or bool(self.changed)


def reconcile_action_rows(old_ids: list[str], new_ids: list[str]) -> RowEdit:
    """算出最小的行删/移/插操作。

    以**动作 ID 序列**做匹配：ID 用尽时按 ``new:`` / ``old:`` 值键退化匹配，
    保证没有 ID 的旧数据也不会错位。返回的 ``move`` 是「把 still-alive 的行移动
    到新位置」的净清单——已经是正确相对顺序的行一个都不动。
    """
    edit = RowEdit()
    if not old_ids and not new_ids:
        return edit

    # 1) 新顺序里的每一行对应旧顺序的哪一行（ID 优先，其次值键退化匹配）。
    old_positions: dict[str, list[int]] = {}
    for position, key in enumerate(old_ids):
        old_positions.setdefault(key, []).append(position)
    consumed: dict[str, int] = {}
    new_to_old: list[int | None] = []
    for key in new_ids:
        bucket = old_positions.get(key) or []
        taken = consumed.get(key, 0)
        new_to_old.append(bucket[taken] if taken < len(bucket) else None)
        consumed[key] = taken + 1

    matched_old = {position for position in new_to_old if position is not None}
    edit.delete = [position for position in range(len(old_ids)) if position not in matched_old]
    edit.insert = [
        (new_position, old_position)
        for new_position, old_position in enumerate(new_to_old)
        if old_position is None
    ]

    # 2) 已在正确相对顺序上的行保持不动（最长上升子序列），其余移动到目标位置。
    order = [position for position in new_to_old if position is not None]
    rank_of = {old_position: rank for rank, old_position in enumerate(order)}
    keep = _longest_increasing(order)
    for new_position, old_position in enumerate(new_to_old):
        if old_position is None:
            continue
        if rank_of[old_position] in keep:
            continue
        edit.move.append((new_position, old_position))

    # 3) 需要重画的行：结构变化波及到的整段（行号与跳转提示会跟着变）。
    edit.changed = _touched_rows(old_ids, new_ids, edit)
    return edit


def _longest_increasing(values: list[int]) -> set[int]:
    """返回最长严格上升子序列的下标集合（O(n log n)）。"""
    import bisect

    if not values:
        return set()
    tails: list[int] = []          # tails[k] = 长度为 k+1 的上升子序列的最小结尾
    tails_index: list[int] = []    # 对应在 values 里的下标
    previous: list[int] = [-1] * len(values)
    for index, value in enumerate(values):
        slot = bisect.bisect_left(tails, value)
        if slot == len(tails):
            tails.append(value)
            tails_index.append(index)
        else:
            tails[slot] = value
            tails_index[slot] = index
        previous[index] = tails_index[slot - 1] if slot else -1
    keep: set[int] = set()
    cursor = tails_index[-1] if tails_index else -1
    while cursor != -1:
        keep.add(cursor)
        cursor = previous[cursor]
    return keep


def _touched_rows(old_ids: list[str], new_ids: list[str], edit: RowEdit) -> list[int]:
    """结构变化后需要重画的行范围。

    行的显示文本只依赖「自己的参数」和「它引用的动作在第几行」。插入 / 删除 /
    移动会让一段行的行号整体平移，这些行的 ``#`` 列与跳转提示都要跟着改；
    改动范围之外的行不动。
    """
    if not edit.structural:
        return []
    positions = [new_position for new_position, _ in edit.insert]
    positions += [new_position for new_position, _ in edit.move]
    positions += [position for position, _ in edit.move]
    positions += [position for position in edit.delete]
    if not positions:
        return []
    last = max(0, len(new_ids) - 1)
    start = max(0, min(min(positions), last))
    return list(range(start, len(new_ids)))


# ---------------------------------------------------------------------------
# 撤销记录
# ---------------------------------------------------------------------------


class ActionEditHistory:
    """脚本编辑的撤销 / 重做历史。

    记录分两种：

    * ``("whole", [动作, ...])`` —— 整表快照，用于插入 / 删除 / 移动这类
      结构变化，撤销时整体装回。
    * ``("rows", {行号: 动作})`` —— 行级记录，只保存被改动的那几行，成本与
      动作总数无关；撤销时把这几行整行装回去。

    编辑只会替换整行 dict、不会就地改写已有 dict，所以行级浅拷贝是安全的
    ——正因如此撤销必须整行装回，不能只在旧行上改字段。

    ``undo`` / ``redo`` 就是主窗口上的 ``action_undo_stack`` / ``action_redo_stack``。
    """

    LIMIT = 100

    def __init__(self) -> None:
        self.undo: list = []
        self.redo: list = []

    def __bool__(self) -> bool:
        return bool(self.undo)

    def clear(self) -> None:
        self.undo = []
        self.redo = []

    def checkpoint(self, actions: list[dict]) -> bool:
        """记录整表快照（结构变化用）。"""
        return self._push(("whole", [dict(action) for action in actions]))

    def checkpoint_row(self, actions: list[dict], *rows: int) -> bool:
        """记录单行 / 少数行的编辑：只保存这些行。"""
        saved = {
            int(row): dict(actions[int(row)])
            for row in rows if 0 <= int(row) < len(actions)
        }
        if not saved:
            return self.checkpoint(actions)
        return self._push(("rows", saved))

    def step(self, actions: list[dict], redo: bool):
        """撤销 / 重做一步，返回 ``(装回后的动作列表, 是否整表替换)``。

        没有历史时返回 None。行级记录装配时，记录里没有的行沿用当前动作
        （行数因此保持不变）。
        """
        source = self.redo if redo else self.undo
        if not source:
            return None
        record = source.pop()
        target = self.undo if redo else self.redo
        target.append(("whole", [dict(action) for action in actions]))
        kind, payload = record
        if kind == "whole":
            return [dict(action) for action in payload], True
        installed = [dict(action) for action in actions]
        for index, saved in payload.items():
            if index < len(installed):
                installed[index] = dict(saved)
        return installed, False

    # --- 内部 ---------------------------------------------------------
    def _push(self, record) -> bool:
        if self.undo and self.undo[-1] == record:
            return False
        self.undo.append(record)
        if len(self.undo) > self.LIMIT:
            del self.undo[:-self.LIMIT]
        # 新的编辑使“重做”历史失效：撤销之后改动作，重做栈作废。
        self.redo = []
        return True

