"""局部 patch：只替换被测代码真正会查找依赖的那几个模块。

以前用「扫包包及所有子模块、把同名属性一起换成同一个 Mock」的做法，优点是写起来
短，代价是：依赖包结构变化、看不出测试到底依赖什么、任何一次子模块移动都会
静默改变 patch 覆盖面。

这里换成一张**显式的目标表**（``patch_targets.json``：名字 → import 过它的实现
模块），由 ``scripts/gen_patch_targets.py`` 从实现模块真实的 import 语句生成。
`patch_package("app", "load_script")` 于是只 patch 那些真的持有 `load_script`
绑定的模块——这就是「在被测代码实际查找依赖的位置局部替换」，同时避免了
每个调用点手写几十个 ``patch.object``。

表中的模块一旦 import 失败会直接报错，不会静默缩小覆盖面。
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

_TARGETS_PATH = Path(__file__).resolve().parent / "patch_targets.json"
PATCH_TARGETS: dict[str, list[str]] = json.loads(
    _TARGETS_PATH.read_text(encoding="utf-8")
)


class PatchTargetError(RuntimeError):
    """patch 目标里出现了 import 不进来的模块（目标表已过期）。"""


class _Targets:
    def __init__(self, name: str):
        self.name = name
        self.modules: dict[str, object] = {}

    def module(self, module_name: str):
        if module_name not in self.modules:
            try:
                module = __import__(module_name, fromlist=["_"])
            except ImportError as exc:  # pragma: no cover - 目标表过期时才会触发
                raise PatchTargetError(
                    f"{module_name} 无法导入（{exc}）："
                    f"patch_targets.json 已过期，请重新生成"
                ) from exc
            self.modules[module_name] = module
        return self.modules[module_name]


_LOADED = _Targets("all")


class _PackagePatch:
    """把 `name` 在目标表列出的实现模块里替换成同一个 Mock / 对象。"""

    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.new = args[0] if args else None
        self.kwargs = kwargs

    def __enter__(self):
        targets = PATCH_TARGETS.get(self.name)
        if not targets:
            raise ValueError(f"{self.name} 没有 patch 目标（patch_targets.json）")
        self._mock = self.new if self.new is not None else Mock(**self.kwargs)
        self._stack = ExitStack()
        for module_name in targets:
            module = _LOADED.module(module_name)
            if hasattr(module, self.name):
                self._stack.enter_context(
                    patch.object(module, self.name, new=self._mock),
                )
        return self._mock

    def start(self):
        return self.__enter__()

    def stop(self):
        self.__exit__(None, None, None)

    def __exit__(self, *exc_info):
        return self._stack.__exit__(*exc_info)


def package_patch(_group: str, name, *args, **kwargs):
    """按目标表局部 patch 一个名字。

    ``_group`` 只用于调用点可读性（app / dialogs / player），patch 目标一律
    由 ``patch_targets.json`` 决定。
    """
    return _PackagePatch(name, *args, **kwargs)
