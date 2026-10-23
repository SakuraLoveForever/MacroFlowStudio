"""机器本地的 SSJJ 内存插件桥。

仓库里不包含任何读取游戏内存的实现。读取代码放在本机的插件包里：

    %LOCALAPPDATA%\\MacroFlowStudio\\local_plugins\\ssjj_memory_plugin\\

插件包需要导出 ``SSJJReader`` 等符号；本模块只负责按需导入，缺少插件时
抛出 :class:`MemoryPluginUnavailable`，由界面显示为“未安装本地内存插件”。
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


PLUGIN_MODULE = "ssjj_memory_plugin"
PLUGIN_HINT = (
    "未安装本地 SSJJ 内存插件：请把 ssjj_memory_plugin 放到 "
    "%LOCALAPPDATA%\\MacroFlowStudio\\local_plugins。"
)


class MemoryPluginUnavailable(RuntimeError):
    """本机没有可用的 SSJJ 内存插件。"""


def plugin_dirs():
    """按优先级返回可能存放插件的目录。"""
    override = os.environ.get("MACROFLOW_MEMORY_PLUGIN")
    if override:
        yield Path(override)
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        yield Path(local) / "MacroFlowStudio" / "local_plugins"
    yield Path(__file__).resolve().parents[3] / "local_plugins"


def import_plugin():
    """导入本机内存插件包；找不到或导入失败时抛出可读错误。"""
    for directory in plugin_dirs():
        package = directory / PLUGIN_MODULE
        if not (package / "__init__.py").is_file():
            continue
        parent = str(directory)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        try:
            return importlib.import_module(PLUGIN_MODULE)
        except Exception as exc:  # noqa: BLE001 - 插件第三方代码，错误统一转译
            raise MemoryPluginUnavailable(f"本地 SSJJ 内存插件加载失败：{exc}") from exc
    raise MemoryPluginUnavailable(PLUGIN_HINT)


class _PluginProxy:
    """延迟加载的插件代理，供公共代码以 ``plugin.SSJJReader`` 方式使用。"""

    def __init__(self):
        self._module = None

    def _load(self):
        module = self._module
        if module is None:
            module = self._module = import_plugin()
        return module

    def __getattr__(self, name):
        try:
            return getattr(self._load(), name)
        except AttributeError:
            raise AttributeError(f"{PLUGIN_MODULE} 未提供 {name}") from None

    def is_available(self):
        try:
            self._load()
        except MemoryPluginUnavailable:
            return False
        return True


plugin = _PluginProxy()