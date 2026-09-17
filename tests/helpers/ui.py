"""无界面 UI 夹具：真实业务方法 + 控件替身。

允许导入 tkinter 相关模块（被测代码本身依赖它们），但**不允许创建真实窗口**：
夹具用 ``MacroFlowApp.__new__`` 绕过 ``__init__``（那里面会建 Tk 窗口），
再注入 ``FakeTree`` / ``FakeVar``。这样脚本编辑与工作流的行更新逻辑全部走
真实实现，却不需要渲染任何窗口。
"""
from __future__ import annotations

from unittest.mock import Mock

from tests.helpers.core import (
    FakeTree, FakeVar, add_src_to_path,
)

add_src_to_path()

from macroflow.core.models import (  # noqa: E402
    MacroScript, Workflow, ensure_action_ids, ensure_workflow_step_ids,
)
from macroflow.ui.app.constants import (  # noqa: E402
    ACTION_TREE_COLUMNS, GLOBAL_TREE_COLUMNS, WORKFLOW_TREE_COLUMNS,
)


def node_objects(count: int = 0) -> dict:
    """构造模块仓库快照（供摘要层查询，不落盘）。"""
    objects = {}
    for index in range(count):
        objects[f"mod{index}"] = {
            "category": "switch", "name": f"模块{index}", "template": f"images/mod{index}.png",
            "region": [10, 20, 30, 40], "recognize": "template",
            "after_action": "click_match", "click_count": 1, "blocking": False,
        }
    return objects


def patch_module_objects(module_objects: dict):
    """在被测代码真正查找模块对象的位置局部替换。

    摘要层通过 ``summaries.registered_module_object`` 读模块配置，所以只需要
    patch 这一个绑定；不需要扫描包与子模块。
    """
    from unittest.mock import patch

    from macroflow.ui.app import summaries

    return patch.object(
        summaries, "registered_module_object",
        side_effect=lambda key: module_objects.get(str(key)),
    )


def make_edit_app(*, actions=None, steps=None, module_objects: dict | None = None,
                   workflow: bool = False):
    """一个未创建窗口的主窗口替身，动作列表 / 工作流表格用 FakeTree。"""
    from macroflow.ui.app import MacroFlowApp

    app = MacroFlowApp.__new__(MacroFlowApp)
    app.root = Mock()
    app.action_tree = FakeTree(ACTION_TREE_COLUMNS)
    app.workflow_tree = FakeTree(WORKFLOW_TREE_COLUMNS)
    app.global_tree = FakeTree(GLOBAL_TREE_COLUMNS)
    app.record_count_var = FakeVar("")
    app.insert_position_var = FakeVar("below")
    app.workflow_insert_position_var = FakeVar("below")
    app.workflow_name_var = FakeVar("")
    app.workflow_start_var = FakeVar("")
    app.workflow_start_delay_enabled_var = FakeVar(False)
    app.workflow_start_delay_seconds_var = FakeVar("5000")
    app.workflow_restart_default_var = FakeVar("")
    app.empty_action_hint = Mock()
    app.empty_workflow_hint = Mock()
    app.empty_global_hint = Mock()
    app.global_script_marker = Mock()
    app.edit_action_button = Mock()
    app.undo_button = Mock()
    app.redo_button = Mock()
    app.status_var = FakeVar("")
    app.status_dot = Mock()
    app.script_path = None
    app.script_requires_new_file = False
    app.dirty = False
    app.script = MacroScript(actions=list(actions or []))
    # 真实编辑器里的每个动作都有稳定 ID（打开脚本时会补齐），行对齐靠它。
    ensure_action_ids(app.script.actions)
    app.workflow = Workflow(name="测试工作流") if workflow or steps else None
    if steps is not None:
        app.workflow.steps = list(steps)
        ensure_workflow_step_ids(app.workflow.steps)
    app.resolution_styles = []
    app.undo_open_stack = []
    app.logs = []
    app.notices = []
    app.statuses = []
    app._notify = Mock(side_effect=lambda *args: app.notices.append(args))
    app._log = Mock(side_effect=lambda text: app.logs.append(text))
    app._set_status = Mock(side_effect=lambda *args: app.statuses.append(args))
    app._mark_dirty = Mock(side_effect=lambda: setattr(app, "dirty", True))
    # 草稿落盘要读整个侧栏设置；夹具只关心「有没有触发保存」，所以默认拦下来，
    # 需要验证真实保存路径的测试自己替换。
    app._persist_workflow_draft = Mock()
    app._ui = lambda callback, *args: callback(*args)
    if module_objects is not None:
        app.module_objects_patcher = patch_module_objects(module_objects)
        app.module_objects_patcher.start()
    return app
