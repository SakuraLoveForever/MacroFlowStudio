"""Workflow edit boundaries and bounded, independent document snapshots."""
import copy
from functools import wraps


def workflow_edit(command):
    """Finish debounced form edits before starting a separate edit command."""
    @wraps(command)
    def edit(self, *args, **kwargs):
        if self.__dict__.get('workflow_draft_after_id') is not None:
            self._persist_workflow_draft()
        return command(self, *args, **kwargs)
    return edit


class WorkflowEditHistory:
    LIMIT = 100

    def __init__(self, current):
        self.current = copy.deepcopy(current)
        self.undo = []

    def record(self, current):
        if current == self.current:
            return False
        self.undo.append(self.current)
        del self.undo[:-self.LIMIT]
        self.current = copy.deepcopy(current)
        return True

    def step(self):
        if not self.undo:
            return None
        self.current = self.undo.pop()
        return copy.deepcopy(self.current)
