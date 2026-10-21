"""MacroFlow Studio 主窗口：功能 mixin 的组合定义。

工具函数与常量留在各自的功能模块（``base`` / ``constants`` / ``summaries`` /
``startup``）；包入口 ``macroflow.ui.app`` 只转出这个类，不再聚合全部实现。
"""
from __future__ import annotations

from .controller import ExecutionController
from .helpers import HelpersMixin
from .hotkeys import HotkeysMixin
from .recording import RecordingMixin
from .scripts import ScriptsMixin
from .shell import ShellMixin
from .workflow import WorkflowMixin


class MacroFlowApp(
    ShellMixin,
    HelpersMixin,
    RecordingMixin,
    ScriptsMixin,
    WorkflowMixin,
    HotkeysMixin,
    ExecutionController,
):
    """主窗口：各功能按分节拆成 mixin，方法之间通过 self 调用。"""

    def _initialize_authoring_state(self):
        from macroflow.input.recorder import MacroRecorder
        self.recorder = MacroRecorder(self._record_action_callback)

    def _finish_client_capture(self):
        self._finish_search_capture()
