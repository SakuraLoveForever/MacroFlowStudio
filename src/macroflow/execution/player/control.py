from __future__ import annotations


class PlaybackStopped(Exception):
    """播放被停止（F12/全局模块中断等）。

    referenced_actions / referenced_source_screen 只在停止瞬间正处于
    被引用脚本（script_ref 嵌套）内部时设置，供"结束当前脚本"跳到
    最内层脚本最后一行使用。
    """

    def __init__(self):
        super().__init__()
        self.referenced_actions: list[dict] | None = None
        self.referenced_source_screen: dict | None = None
class JumpToCurrentScriptLastAction(Exception):
    """Leave a nested module segment and continue at the outer script's last action."""

    def __init__(self):
        super().__init__()
        self.current_index: int | None = None
class AdvanceToNextWorkflowStep(Exception):
    """Finish the current top-level script and let its workflow advance."""

    pass
class EndCurrentScriptRequest(Exception):
    """Leave nested code segments and stop at the nearest script boundary."""

    def __init__(self, repeat_only: bool = False):
        super().__init__()
        # 全局模块处理段处于当前脚本重复内部，只结束本次执行，不能跳过
        # 当前脚本后续重复。
        self.repeat_only = bool(repeat_only)
class EndCurrentScriptRepeatRequest(Exception):
    """Finish this invocation of a nested script and continue its next repeat."""

    pass
class GuardJumpRequest(Exception):
    """全局守卫触发脚本内跳转，并携带所属脚本的动作身份。"""

    def __init__(self, jump_action_id: str = "", jump_row: int = 1,
                 scope_action_ids: list[str] | tuple[str, ...] | None = None):
        super().__init__()
        self.jump_action_id = str(jump_action_id or "")
        self.jump_row = max(1, int(jump_row or 1))
        self.scope_action_ids = frozenset(
            str(action_id).strip()
            for action_id in (scope_action_ids or ())
            if str(action_id).strip()
        )
