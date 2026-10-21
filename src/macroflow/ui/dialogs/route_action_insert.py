"""Reuse the script editor's insertion commands without changing its script."""
import copy
from types import SimpleNamespace

from macroflow.core.models import ensure_action_ids
from macroflow.core.storage import SCRIPTS_DIR
from macroflow.ui.app.scripts import ScriptsMixin
from macroflow.ui.app.shell import ShellMixin
from macroflow.ui.app.action_palette import action_category_specs
from .base import show_floating_notice


def route_action_categories():
    specs = tuple(spec for spec in ShellMixin._script_action_button_specs()
                  if spec[1] not in {'open_ssjj_coordinates', '_toggle_record_from_toolbar'})
    return tuple((label, primary + extra) for label, primary, extra in action_category_specs(specs))


class ScriptActionInserter(ScriptsMixin):
    def __init__(self, dialog, app, actions):
        self.dialog = self.root = dialog
        self.app = app
        self.script = SimpleNamespace(actions=actions, settings={})
        self.resolution_styles = getattr(app, 'resolution_styles', [])
        self.insert_position_var = SimpleNamespace(get=lambda: 'below')

    def __getattr__(self, name):
        if self.app is not None:
            return getattr(self.app, name)
        raise AttributeError(name)

    def _notify(self, title, text):
        show_floating_notice(self.dialog, title, text)

    def _script_category_dir(self):
        return self.app._script_category_dir() if self.app is not None else SCRIPTS_DIR

    def _insert_action(self, action):
        self.dialog.insert_actions(self._insert_script_position(), [action])

    def _insert_script_position(self):
        index = self.dialog.selected_index()
        return len(self.script.actions) if index is None else index + 1

    def _insert_script_actions(self, insert_at, actions):
        self.dialog.insert_actions(insert_at, actions)

    def _insert_script_reference(self):
        self._insert_script(False)

    def _insert_script_inline(self):
        self._insert_script(True)


class RouteActionInserter(ScriptActionInserter):
    def __init__(self, dialog, app):
        super().__init__(dialog, app, dialog.inputs)

    def _insert_action(self, action):
        step = copy.deepcopy(action)
        ensure_action_ids([step])
        step.update(route_script_action=True, recorded_at_ms=self.dialog._insert_offset())
        self.dialog._place_input(step)

    def _insert_script_position(self):
        return self.dialog._insertion_context()[1]

    def _insert_script_actions(self, insert_at, actions):
        for action in actions:
            self._insert_action(action)
