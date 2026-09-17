"""无界面测试基座：控件替身、数据集构造与耗时统计。

只依赖标准库。这里**不允许**导入 tkinter / cv2 / numpy / OCR —— 纯业务测试
（脚本编辑、工作流、存储、模型）都从这里取夹具，必须能在没有 Tk、没有截图
依赖的干净进程里跑起来。
"""
from __future__ import annotations

import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


def add_src_to_path() -> Path:
    """把项目根与 src 放进导入路径，返回项目根。

    直接跑单个测试文件（python tests/test_xxx.py）时也要能解析 macroflow.*
    与 tests.helpers.*，所以放在这个最底层模块里。
    """
    root = Path(__file__).resolve().parent.parent.parent
    for entry in (str(root / "src"), str(root)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return root


add_src_to_path()


class FakeVar:
    """Tk Variable 替身：只保留 get / set 语义。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeSettingVar(FakeVar):
    """设置项变量替身（与 FakeVar 同语义，名字更贴近用途）。"""


class FakeBooleanVar(FakeVar):
    """Tk BooleanVar 替身：set 时按布尔归一。"""

    def __init__(self, value: bool = False):
        super().__init__(bool(value))

    def set(self, value) -> None:
        self._value = bool(value)


class FakeWidget:
    """最小控件替身：吞掉 configure / pack / state 一类外观调用。

    `exists=False` 时把它当成“这个窗口里没有这个控件”——被测代码用
    ``getattr(self, name, None)`` 探测可选控件，替身不该假装控件一定在。
    """

    def __init__(self, **kwargs):
        self.configured = []
        self.kwargs = dict(kwargs)

    def configure(self, **kwargs):
        self.configured.append(kwargs)

    config = configure

    def pack(self, **kwargs):
        self.configured.append(("pack", kwargs))

    def pack_forget(self):
        self.configured.append(("pack_forget",))

    def place(self, **kwargs):
        self.configured.append(("place", kwargs))

    def place_forget(self):
        self.configured.append(("place_forget",))

    def bind(self, *args, **kwargs):
        pass

    def cget(self, key):
        return self.kwargs.get(key, "")

    def destroy(self):
        pass


@dataclass
class TreeCounters:
    """一次测试里 Treeview 调用次数：用来断言“只更新受影响的行”。"""

    insert: int = 0
    delete: int = 0
    set: int = 0
    move: int = 0
    see: int = 0
    item: int = 0
    delete_rows: int = 0

    def reset(self) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, 0)


class FakeTree:
    """ttk.Treeview 的替身，供无界面测试驱动真实的行更新代码。

    行为与真实控件一致的部分：iid 显式给出、``values`` / ``tags`` 按列顺序
    存储、``delete()`` 不带参数删除全部子项、``selection`` 返回当前选中集合、
    ``exists`` 只对已插入的 iid 为真。

    刻意不模拟的部分：几何、滚动、焦点、样式。因此这里的耗时**不能**当作
    真实 Tk 渲染性能，只能用来验证算法复杂度（插入了多少行、移动了几行）。
    """

    def __init__(self, columns=()):
        self.columns = tuple(columns)
        self.rows: dict[str, tuple] = {}
        self.tags: dict[str, tuple] = {}
        self._order: list[str] = []
        self._selection: list[str] = []
        self._focus = ""
        self.counters = TreeCounters()
        self.column_widths: dict[str, int] = {}

    # --- 内部 ---------------------------------------------------------
    def _column_names(self) -> tuple[str, ...]:
        return tuple(column[0] if isinstance(column, (tuple, list)) else column
                     for column in self.columns)

    # --- Treeview 协议 ------------------------------------------------
    def delete(self, *items) -> None:
        self.counters.delete += 1
        targets = list(items) or list(self._order)
        for iid in targets:
            if iid in self.rows:
                self.rows.pop(iid, None)
                self.tags.pop(iid, None)
                self._order.remove(iid)
                self.counters.delete_rows += 1
        self._selection = [iid for iid in self._selection if iid in self.rows]

    def insert(self, parent, index, iid=None, values=(), tags=(), **kwargs) -> str:
        self.counters.insert += 1
        if iid is None:
            iid = f"I{len(self.rows)}"
        iid = str(iid)
        if iid in self._order:
            # 真实 Treeview 的 iid 必须唯一：重复的 iid 是调用方 bug，
            # 这里直接暴露出来（老实现是静默覆盖，问题会被藏起来）。
            raise ValueError(f"重复的 iid：{iid!r}")
        parsed = self._parse(tags) if isinstance(tags, str) else tuple(tags)
        self.rows[iid] = tuple(values)
        self.tags[iid] = parsed
        if index in ("end", len(self._order)) or int(index) >= len(self._order):
            self._order.append(iid)
        else:
            self._order.insert(int(index), iid)
        return iid

    def _parse(self, tags) -> tuple:
        if isinstance(tags, str):
            return tuple(tags.split())
        return tuple(tags)

    def move(self, iid, parent, index) -> None:
        self.counters.move += 1
        if iid not in self.rows:
            return
        self._order.remove(iid)
        if index in ("end", len(self._order)):
            self._order.append(iid)
        else:
            self._order.insert(int(index), iid)

    def set(self, iid, column=None, value=None) -> dict:
        self.counters.set += 1
        if iid not in self.rows:
            return {}
        if column is None:
            return dict(zip(self._column_names(), self.rows[iid]))
        row = list(self.rows[iid])
        names = self._column_names()
        if column in names:
            row[names.index(column)] = value
            self.rows[iid] = tuple(row)
            return {column: value}
        return {}

    def get_children(self, item=None) -> tuple:
        return tuple(self._order)

    def exists(self, iid) -> bool:
        return str(iid) in self.rows

    def selection(self) -> tuple:
        return tuple(self._selection)

    def selection_set(self, *items) -> None:
        if len(items) == 1 and isinstance(items[0], (list, tuple, set)):
            items = tuple(items[0])
        self._selection = [str(item) for item in items if str(item) in self.rows]

    def selection_add(self, *items) -> None:
        for item in items:
            if str(item) in self.rows and str(item) not in self._selection:
                self._selection.append(str(item))

    def selection_remove(self, *items) -> None:
        drop = {str(item) for item in items}
        self._selection = [item for item in self._selection if item not in drop]

    def selection_toggle(self, *items) -> None:
        for item in items:
            item = str(item)
            if item in self._selection:
                self._selection.remove(item)
            elif item in self.rows:
                self._selection.append(item)

    def focus(self, item=None):
        if item is None:
            return self._focus
        self._focus = str(item)
        return self._focus

    def see(self, item) -> None:
        self.counters.see += 1

    def item(self, iid, option=None, **kwargs):
        self.counters.item += 1
        if option is None:
            return {"values": self.rows.get(str(iid), ()),
                    "tags": self.tags.get(str(iid), ())}
        return self.rows.get(str(iid), ())

    def column(self, column, **kwargs):
        if "width" in kwargs:
            self.column_widths[str(column)] = kwargs["width"]
        return {"width": self.column_widths.get(str(column), 0)}

    def heading(self, *args, **kwargs):
        pass

    def bind(self, *args, **kwargs):
        pass

    def tag_configure(self, *args, **kwargs):
        pass

    def identify_row(self, y) -> str:
        return self._order[0] if self._order else ""

    def yview(self, *args):
        pass

    def xview(self, *args):
        pass

    def winfo_children(self) -> tuple:
        return ()

    # --- 断言辅助 -----------------------------------------------------
    def value(self, iid, column: str):
        names = self._column_names()
        row = self.rows.get(str(iid), ())
        return row[names.index(column)] if column in names and len(row) > names.index(column) else None

    def index_of(self, iid) -> int:
        return self._order.index(str(iid))


@dataclass
class Sample:
    """一个构造好的测试数据集：动作列表 + 工作流步骤 + 说明。"""

    label: str
    actions: list[dict] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.actions)

    @property
    def module_ref_rows(self) -> int:
        return sum(1 for action in self.actions if action.get("type") == "image_match")

    @property
    def jump_rows(self) -> int:
        return sum(1 for action in self.actions if action.get("type") == "jump")

    @property
    def nested_rows(self) -> int:
        return sum(1 for action in self.actions if action.get("failure_actions"))


_ACTION_CYCLE = (
    "delay", "key", "click", "image_match", "jump", "recorded_input",
    "script_ref", "comment", "scroll", "text",
)


def build_actions(count: int, *, modules: int = 40, referenced_scripts: int = 6,
                  seed: int = 0, label: str = "") -> list[dict]:
    """构造 count 条混合动作：跳转引用、嵌套动作、重复引用模块、混合类型。

    每一行都有稳定的 action_id；跳转行的目标指向同一份数据里真实存在的
    另一行（用 ``jump_action_id``），这样行号摘要与重映射逻辑都被真实覆盖。
    """
    from macroflow.core.models import ACTION_ID_KEY, new_action_id

    actions: list[dict] = []
    for index in range(count):
        kind = _ACTION_CYCLE[(index + seed) % len(_ACTION_CYCLE)]
        action: dict = {"type": kind, "delay_ms": index % 250, "action_id": new_action_id()}
        if kind == "delay":
            action["ms"] = 50 + (index % 500)
        elif kind == "key":
            action.update({"vk": 65 + index % 26, "name": f"k{index % 26}", "down": True})
        elif kind == "click":
            action.update({"x": 100 + index % 800, "y": 200 + index % 600, "button": "left"})
        elif kind == "image_match":
            # 少量模块被大量行重复引用：模块配置读取次数必须与引用行数无关。
            action.update({
                "module_key": f"mod{index % max(1, modules)}",
                "template": f"images/mod{index % max(1, modules)}.png",
                "region": [10, 20, 30, 40],
                "threshold": 0.85,
            })
        elif kind == "jump":
            action["jump_row"] = (index + 3) % max(1, count) + 1
        elif kind == "recorded_input":
            action["steps"] = [
                {"type": "key", "vk": 65, "name": "a", "down": True, "delay_ms": 0},
                {"type": "mouse_button", "button": "left", "down": True,
                 "x": 5, "y": 6, "delay_ms": 120},
            ]
        elif kind == "script_ref":
            action.update({
                "script": f"scripts/ref{index % max(1, referenced_scripts)}.json",
                "repeats": 1,
            })
        elif kind == "comment":
            action["text"] = f"备注 {index}"
        elif kind == "scroll":
            action.update({"clicks": 3, "x": 640, "y": 360})
        elif kind == "text":
            action["text"] = f"输入内容 {index}"
        if index % 7 == 3:
            action["failure_segment_enabled"] = True
            action["failure_actions"] = [
                {"type": "delay", "ms": 10, "action_id": new_action_id()},
            ]
        actions.append(action)

    # 跳转目标改成动作 ID（真实语义），只在确实存在的行上设置。
    if actions:
        for index, action in enumerate(actions):
            if action.get("type") == "jump":
                target_index = min((index + 3) % len(actions), len(actions) - 1)
                action.pop("jump_row", None)
                action["jump_action_id"] = actions[target_index][ACTION_ID_KEY]
    return actions


def build_workflow(count: int, *, modules: int = 8, global_modules: int = 3,
                   seed: int = 0) -> list[dict]:
    """构造 count 个普通步骤 + global_modules 个全局模块（同一集合两个视图）。"""
    from macroflow.core.models import ensure_workflow_step_ids

    steps: list[dict] = []
    for index in range(count):
        if index % 6 == 5:
            steps.append({
                "kind": "module",
                "script": f"modules/mod{index % max(1, modules)}.json",
                "action": {"type": "image_match", "module_key": f"mod{index % max(1, modules)}"},
                "repeats": 1 + index % 5,
                "before_ms": index % 900,
                "repeat_interval_ms": 1000 + index % 500,
                "enabled": index % 11 != 0,
                "unlimited": index % 13 == 0,
            })
        else:
            steps.append({
                "kind": "script",
                "script": f"scripts/step{index % 40}.json",
                "repeats": 1 + index % 5,
                "before_ms": index % 900,
                "repeat_interval_ms": 1000 + index % 500,
                "enabled": index % 11 != 0,
                "unlimited": index % 13 == 0,
            })
    for index in range(global_modules):
        steps.append({
            "kind": "global_module",
            "script": f"modules/global{index}.json",
            "action": {"type": "global_detect", "module_key": f"global{index}"},
            "repeats": 1,
            "enabled": True,
        })
    ensure_workflow_step_ids(steps)
    return steps


def sample(count: int, *, seed: int = 0) -> Sample:
    return Sample(
        label=f"{count} 条动作",
        actions=build_actions(count, seed=seed),
        steps=build_workflow(min(count, 400), seed=seed),
    )


@dataclass
class Stats:
    """一组样本的耗时统计（毫秒）。"""

    label: str
    samples: int
    p50: float
    p95: float
    minimum: float
    maximum: float

    def as_row(self) -> str:
        return (f"{self.label:<28} n={self.samples:<4} "
                f"p50={self.p50:8.2f}ms  p95={self.p95:8.2f}ms  "
                f"min={self.minimum:8.2f}ms  max={self.maximum:8.2f}ms")


def measure(label: str, operation, *, warmup: int = 2, samples: int = 12) -> Stats:
    """预热 warmup 次、采样 samples 次，返回 p50/p95。"""
    for _ in range(max(0, warmup)):
        operation()
    timings = []
    for _ in range(max(1, samples)):
        started = time.perf_counter()
        operation()
        timings.append((time.perf_counter() - started) * 1000)
    return Stats(
        label=label,
        samples=len(timings),
        p50=statistics.median(timings),
        p95=_percentile(timings, 95),
        minimum=min(timings),
        maximum=max(timings),
    )


def _percentile(values: list[float], percent: float) -> float:
    """最近秩百分位（与测试断言口径一致，样本少时也不会插值出假数据）。"""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, int(round(percent / 100 * len(ordered) + 0.5)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def peak_memory_mb() -> float:
    """当前进程峰值内存（MB）；取不到时返回 -1。"""
    try:
        import resource  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        return float(usage)
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb,
        )
        return counters.PeakWorkingSetSize / (1024 * 1024)
    except Exception:
        return -1.0
