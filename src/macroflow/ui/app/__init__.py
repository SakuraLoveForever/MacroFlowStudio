"""MacroFlow Studio 主窗口包：只转出主窗口类与启动入口。

实现按功能拆在子模块里（``shell`` / ``helpers`` / ``scripts`` / ``workflow`` /
``execution`` / ``guards`` / ``global_detect`` / ``recording`` / ``tray`` /
``window_binding`` / ``hotkeys``，加上 ``base`` / ``constants`` / ``summaries`` /
``startup`` / ``main``）。这个入口**不再聚合**这些实现：需要 ``px`` /
``action_summary`` / ``ClickDialog`` 一类工具时，从它们真正的定义模块导入。

理由：包入口一旦聚合实现，子模块的移动会被重导出掩盖，而且
「import 主窗口包」会连带拉起整套界面与截图依赖。
"""
from __future__ import annotations

from .main import MacroFlowApp

__all__ = ["MacroFlowApp"]
