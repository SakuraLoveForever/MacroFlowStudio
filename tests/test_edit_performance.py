"""脚本 / 工作流编辑路径的性能与无界面验证。

本文件**不**导入 tests.common：它自带控件替身夹具，因此可以在没有 Tk 窗口、
没有 cv2 / numpy / OCR 的干净进程里单独运行：

    python -m unittest tests.test_edit_performance

基准（默认不跑，避免拖慢全量测试）：

    python tests/test_edit_performance.py --benchmark

夹具用 FakeTree 驱动**真实的** rebuild_action_tree / delete_actions / move_action
等代码路径，所以这里测出的耗时包含业务修改、撤销记录、摘要计算与行更新调度，
但不包含真实 Tk 的渲染与重绘——真实窗口性能必须由用户实际运行确认。
"""
from __future__ import annotations

import inspect
import io
import os
import sys
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.helpers.core import (  # noqa: E402
    FakeTree, FakeVar, FakeWidget, Stats, build_actions, build_workflow,
    measure, peak_memory_mb,
)

from macroflow.core import storage  # noqa: E402
from macroflow.core.models import ACTION_ID_KEY, MacroScript, Workflow  # noqa: E402
from macroflow.ui.app.helpers import HelpersMixin  # noqa: E402
from macroflow.ui.app.scripts import ScriptsMixin  # noqa: E402
from macroflow.ui.app.workflow import WorkflowMixin  # noqa: E402
from macroflow.ui.app.constants import ACTION_TREE_COLUMNS, WORKFLOW_TREE_COLUMNS  # noqa: E402
from macroflow.ui.app.summaries import action_summary  # noqa: E402

# 只把编辑路径真正用到的实现方法绑到夹具上。ShellMixin.__init__ 会创建真实
# Tk 窗口，所以主窗口类在这里不能用——本文件必须能在无界面环境里跑。
_BOUND_METHODS = (
    "_action_rows", "_action_history", "action_undo_stack", "action_redo_stack",
    "_has_undo_steps", "_has_redo_steps",
    "_reset_row_segment_bar", "_refresh_row_segment_bar", "_selected_row_segment",
    "rebuild_action_tree", "_reload_action_row_index", "_finish_action_tree_refresh",
    "_set_action_row", "_refresh_action_rows", "_refresh_one_action_row",
    "_sync_action_rows", "_apply_row_edit",
    "_sync_global_script_marker",
    "_update_action_edit_button", "_selected_action_index",
    "_checkpoint_action_edit", "_undo_redo_action_edit",
    "_clear_action_undo", "_update_undo_button", "_update_redo_button",
    "delete_actions", "copy_selected_actions_down", "move_action",
    "_insert_action", "edit_selected_action",
    "_select_all_actions", "_insert_script_position",
    "rebuild_workflow_tree", "rebuild_global_tree",
    "_workflow_row_values", "_set_workflow_row", "_refresh_one_workflow_row",
    "_workflow_only_steps", "_global_module_steps",
    "_workflow_module_key", "_workflow_module_enabled", "_module_object",
    "_workflow_step_name", "_global_module_label",
    "_measure_text_width", "_measure_font", "_autosize_tree_column",
    "_selected_workflow_index", "_selected_workflow_indices",
    "move_workflow_step", "delete_workflow_step",
    "undo_delete_workflow_step", "toggle_selected_workflow_step",
    "_clear_workflow_delete_history", "_update_workflow_delete_undo_buttons",
)


def _bound_methods() -> dict:
    methods = {}
    for mixin in (HelpersMixin, ScriptsMixin, WorkflowMixin):
        for name in _BOUND_METHODS:
            function = getattr(mixin, name, None)
            if function is None:
                continue
            if isinstance(inspect.getattr_static(mixin, name), staticmethod):
                # 静态方法绑成普通函数，别把 self 当第一个参数传进去。
                methods[name] = staticmethod(function)
            else:
                methods[name] = function
    missing = set(_BOUND_METHODS) - set(methods)
    if missing:
        raise AssertionError(f"实现 mixin 里找不到：{sorted(missing)}")
    return methods


class EditFixture:
    """尚未创建窗口的主窗口替身：真实业务方法 + 替身控件。"""

    def __init__(self, *args, **kwargs):
        self._setup_edit_fixture()

    def _setup_edit_fixture(self):
        self.root = FakeWidget()
        self.action_tree = FakeTree(ACTION_TREE_COLUMNS)
        self.workflow_tree = FakeTree(WORKFLOW_TREE_COLUMNS)
        self.global_tree = FakeTree()
        self.record_count_var = FakeVar("")
        self.insert_position_var = FakeVar("below")
        self.empty_action_hint = FakeWidget()
        self.empty_workflow_hint = FakeWidget()
        self.empty_global_hint = FakeWidget()
        self.global_script_marker = FakeWidget()
        self.edit_action_button = FakeWidget()
        self.undo_button = FakeWidget()
        self.redo_button = FakeWidget()
        self.workflow_name_var = FakeVar("")
        self.workflow_start_var = FakeVar("")
        self.workflow_start_delay_enabled_var = FakeVar(False)
        self.workflow_start_delay_seconds_var = FakeVar("5000")
        self.workflow_restart_default_var = FakeVar("")
        self.workflow_insert_position_var = FakeVar("below")
        self.status_var = FakeVar("")
        self.dirty = False
        self.script_path = None
        self.script = MacroScript(name="基准脚本")
        self.workflow = None
        self.action_undo_stack: list = []
        self.action_redo_stack: list = []
        self.workflow_delete_stack: list = []
        self.global_delete_stack: list = []
        self.undo_open_stack: list = []
        self.notices: list[tuple] = []
        self.logs: list[str] = []
        self.statuses: list[tuple] = []
        self.resolution_styles = {}
        self.workflow_restart_rows = {}

    # --- 被测代码会调到的少量外壳 -------------------------------------
    def _notify(self, title, message=""):
        self.notices.append((title, message))

    def _log(self, text):
        self.logs.append(text)

    def _set_status(self, text, style="normal"):
        self.statuses.append((text, style))

    def _mark_dirty(self):
        self.dirty = True

    def _ui(self, callback, *args):
        return callback(*args)

    def _sync_workflow_restart_default_ui(self):
        pass

    def _workflow_restart_default_options(self):
        return [], {}

    def _schedule_workflow_draft_save(self, _event=None):
        pass

    def _persist_workflow_draft(self):
        pass

    def _workflow_global_module_registry_state(self, step):
        return None

    def _refresh_global_trigger_section(self):
        pass


EditImpl = type("EditFixtureImpl", (EditFixture,), _bound_methods())


class IsolatedStorageMixin(unittest.TestCase):
    """把数据目录指到临时目录，避免读到项目里的真实脚本 / 模块配置。"""

    def setUp(self):
        super().setUp()
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = os.path.abspath(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for name, relative in (
            ("BASE_DIR", ""),
            ("SCRIPTS_DIR", "scripts"),
            ("WORKFLOWS_DIR", "workflows"),
            ("IMAGES_DIR", "images"),
            ("SETTINGS_PATH", "app_settings.json"),
            ("SCRIPT_BACKUPS_DIR", os.path.join("backups", "scripts")),
            ("OVERWRITTEN_BACKUPS_DIR", os.path.join("backups", "overwritten")),
            ("TEMPLATE_REGIONS_PATH", "template_regions.json"),
            ("MODULE_SETTINGS_PATH", "module_settings.json"),
        ):
            original = getattr(storage, name)
            self.addCleanup(setattr, storage, name, original)
            setattr(storage, name, storage.Path(root) / relative if relative
                    else storage.Path(root))
        storage.ensure_dirs()

    def write_module_objects(self, count: int) -> None:
        import json
        payload = {}
        for index in range(count):
            key = f"mod{index}"
            payload[key] = {
                "category": "switch",
                "name": f"模块{index}",
                "template": f"images/mod{index}.png",
                "region": [10, 20, 30, 40],
                "recognize": "template",
                "after_action": "click_match",
                "click_count": 1,
                "blocking": False,
            }
        storage.TEMPLATE_REGIONS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8",
        )


class ScriptEditPathTests(IsolatedStorageMixin):
    """脚本编辑：行更新、撤销记录、跳转摘要。"""

    def make_app(self, count: int = 200, modules: int = 40) -> EditImpl:
        app = EditImpl()
        self.write_module_objects(modules)
        app.script.actions = build_actions(count, modules=modules)
        app.rebuild_action_tree()
        return app

    def test_initial_rebuild_fills_every_row(self):
        app = self.make_app(count=200)
        self.assertEqual(len(app.action_tree.get_children()), 200)
        self.assertEqual(app.action_tree.value("0", "index"), 1)
        self.assertEqual(app.action_tree.value("199", "index"), 200)

    def test_row_iid_is_the_data_index_not_a_position_token(self):
        app = self.make_app(count=50)
        # iid 必须能反推数据下标（右键菜单、跳转、执行高亮都依赖这一点）。
        for iid in ("0", "17", "49"):
            self.assertTrue(app.action_tree.exists(iid))
        self.assertFalse(app.action_tree.exists("50"))
        rows = {
            str(action.get(ACTION_ID_KEY, "")): index + 1
            for index, action in enumerate(app.script.actions)
            if action.get(ACTION_ID_KEY)
        }
        self.assertEqual(
            app.action_tree.value("17", "kind"),
            action_summary(app.script.actions[17], rows)[0],
        )
        self.assertIn(
            action_summary(app.script.actions[17], rows)[1],
            app.action_tree.value("17", "detail"),
        )

    def test_jump_row_summary_shows_the_live_target_row(self):
        app = self.make_app(count=30)
        jump_index = next(
            index for index, action in enumerate(app.script.actions)
            if action.get("type") == "jump"
        )
        rows = {
            str(action.get(ACTION_ID_KEY, "")): index + 1
            for index, action in enumerate(app.script.actions)
            if action.get(ACTION_ID_KEY)
        }
        detail = app.action_tree.value(str(jump_index), "detail")
        target_row = rows[app.script.actions[jump_index]["jump_action_id"]]
        # 跳转提示里的行号必须是目标动作当前所在的行（删行 / 移动后要跟着变）。
        self.assertIn(f"第 {target_row} 行", detail)

    def test_delete_and_undo_restore_the_same_actions(self):
        app = self.make_app(count=60)
        before = [dict(action) for action in app.script.actions]
        app.action_tree.selection_set("10", "11")
        app.delete_actions()
        self.assertEqual(len(app.script.actions), 58)
        app._undo_redo_action_edit(redo=False)
        self.assertEqual(len(app.script.actions), 60)
        self.assertEqual(
            [action[ACTION_ID_KEY] for action in app.script.actions],
            [action[ACTION_ID_KEY] for action in before],
        )

    def test_copy_down_remaps_internal_jump_references(self):
        app = self.make_app(count=4)
        # 造一段“内部自引用”的动作：第 2 行跳回第 1 行。复制后副本里的跳转
        # 必须指向副本内部的第 1 行，而不是原件。
        target_id = app.script.actions[0][ACTION_ID_KEY]
        app.script.actions[1].update({
            "type": "jump", "jump_action_id": target_id,
        })
        app.rebuild_action_tree()
        app.action_tree.selection_set("0", "1")
        app.copy_selected_actions_down()
        self.assertEqual(len(app.script.actions), 6)
        copy_head, copy_jump = app.script.actions[2], app.script.actions[3]
        self.assertEqual(copy_jump["type"], "jump")
        self.assertEqual(copy_jump["jump_action_id"], copy_head[ACTION_ID_KEY])
        self.assertNotEqual(copy_jump["jump_action_id"], target_id)
        ids = [action[ACTION_ID_KEY] for action in app.script.actions]
        self.assertEqual(len(ids), len(set(ids)), "复制后动作 ID 必须唯一")
        # 原件仍指回原件。
        self.assertEqual(app.script.actions[1]["jump_action_id"], target_id)

    def test_move_up_and_down_keep_action_ids(self):
        app = self.make_app(count=20)
        ids = [action[ACTION_ID_KEY] for action in app.script.actions]
        app.action_tree.selection_set("5")
        app.move_action(1)
        self.assertEqual(
            [action[ACTION_ID_KEY] for action in app.script.actions],
            ids[:5] + [ids[6], ids[5]] + ids[7:],
        )
        app.move_action(-1)
        self.assertEqual(
            [action[ACTION_ID_KEY] for action in app.script.actions], ids,
        )


class EditPipelineBenchmark(IsolatedStorageMixin):
    """复杂度检查与基准：单行编辑不得全量重建、不得复制整个动作集合。"""

    SIZES = (1_000, 10_000, 20_000, 25_000)
    # 可自动验证的目标：10,000 条数据下单行编辑整体 p95 低于 50ms。
    TARGET_P95_MS = 50.0

    def _app(self, count: int):
        app = EditImpl()
        self.write_module_objects(40)
        app.script.actions = build_actions(count, modules=40)
        app.rebuild_action_tree()
        return app

    def _edit_cycle(self, app, row: int = 0):
        """一次真实单行编辑：业务修改 + 撤销记录 + 摘要计算 + 行更新调度。"""
        actions = app.script.actions
        updated = dict(actions[row])
        updated["delay_ms"] = int(updated.get("delay_ms", 0)) + 1
        app._checkpoint_action_edit([row])
        actions[row] = updated
        app._refresh_one_action_row(row)

    def test_single_row_edit_stays_within_the_p95_budget(self):
        """10,000 条数据下单行编辑整体 p95 必须低于 50ms（可自动验证的目标）。"""
        app = self._app(10_000)
        stats = measure(
            "单行编辑", lambda: self._edit_cycle(app),
            warmup=3, samples=25,
        )
        self.assertLess(
            stats.p95, self.TARGET_P95_MS,
            f"10,000 条动作下单行编辑 p95={stats.p95:.2f}ms 超过 "
            f"{self.TARGET_P95_MS}ms 目标（p50={stats.p50:.2f}ms）",
        )

    def test_single_row_edit_does_not_rebuild_every_row(self):
        """单行编辑不得清空/重插所有节点。"""
        app = self._app(10_000)
        app.action_tree.counters.reset()
        self._edit_cycle(app)
        counters = app.action_tree.counters
        self.assertEqual(
            counters.insert, 0,
            f"单行编辑插入了 {counters.insert} 行，等于全量重建",
        )
        self.assertEqual(
            counters.delete, 0,
            f"单行编辑删除了 {counters.delete_rows} 行",
        )
        # 只写这一行的单元格（列数固定），不是整表。
        self.assertLess(counters.set, 20, f"单行编辑写了 {counters.set} 个单元格")

    def test_single_row_edit_does_not_copy_the_whole_action_list(self):
        """撤销记录按行稀疏：单行编辑只保存这一行，与动作总数无关。"""
        app = self._app(5_000)
        app._checkpoint_action_edit([7])
        kind, saved = app.action_undo_stack[-1]
        self.assertEqual(kind, "rows")
        self.assertEqual(list(saved), [7])
        self.assertEqual(saved[7], app.script.actions[7])
        self.assertIsNot(saved[7], app.script.actions[7], "记录必须是独立快照")
        # 嵌套列表（录制动作的 steps / 失败代码段）与动作对象共享，不加倍复制。
        nested_row = next(
            index for index, action in enumerate(app.script.actions)
            if action.get("steps")
        )
        app._checkpoint_action_edit([nested_row])
        _kind, saved = app.action_undo_stack[-1]
        self.assertIs(
            saved[nested_row].get("steps"),
            app.script.actions[nested_row].get("steps"),
        )

    def test_row_level_undo_restores_only_that_row(self):
        """行级撤销把这一行整行装回去，其它行不动。"""
        app = self._app(50)
        original = dict(app.script.actions[3])
        app._checkpoint_action_edit([3])
        app.script.actions[3] = {**original, "delay_ms": 9999}
        app._undo_redo_action_edit(redo=False)
        self.assertEqual(app.script.actions[3], original)
        app._undo_redo_action_edit(redo=True)
        self.assertEqual(app.script.actions[3]["delay_ms"], 9999)

    def test_module_config_is_read_once_per_refresh(self):
        """一次刷新只读一次模块配置，不随引用行数线性增长。"""
        app = self._app(3_000)
        reads = {"count": 0}
        original = storage.load_module_objects

        def counting():
            reads["count"] += 1
            return original()

        storage.load_module_objects = counting
        try:
            app.rebuild_action_tree()
        finally:
            storage.load_module_objects = original
        refs = sum(1 for action in app.script.actions
                   if action.get("type") == "image_match")
        self.assertGreater(refs, 100)
        self.assertLessEqual(
            reads["count"], 2,
            f"{refs} 行引用模块触发了 {reads['count']} 次模块配置读取",
        )

    def test_autosize_does_not_measure_every_row_on_every_edit(self):
        """自动列宽只在打开文档 / 首次显示时跑，普通编辑不再全量测量。"""
        app = self._app(2_000)
        measured = {"count": 0}
        original = app._measure_text_width

        def counting(text):
            measured["count"] += 1
            return original(text)

        app._measure_text_width = counting
        app._refresh_one_action_row(5)
        self.assertLess(
            measured["count"], 50,
            f"单行编辑测量了 {measured['count']} 段文本",
        )


class WorkflowEditPathTests(IsolatedStorageMixin):
    """工作流编辑：混合集合的行映射与全局模块表。"""

    def make_app(self, count: int = 60) -> EditImpl:
        from macroflow.core.models import Workflow
        app = EditImpl()
        self.write_module_objects(20)
        app.workflow = Workflow(name="基准工作流")
        app.workflow.steps = build_workflow(count, modules=20, global_modules=3)
        app.rebuild_workflow_tree()
        return app

    def test_global_modules_are_not_workflow_rows(self):
        app = self.make_app(count=60)
        visible = len(app.workflow_tree.get_children())
        self.assertEqual(visible, len(app._workflow_only_steps()))
        self.assertEqual(len(app.global_tree.get_children()),
                         len(app._global_module_steps()))
        self.assertLess(visible, len(app.workflow.steps))

    def test_workflow_row_iids_map_to_the_workflow_only_view(self):
        app = self.make_app(count=60)
        only = app._workflow_only_steps()
        for iid in ("0", str(len(only) - 1)):
            self.assertTrue(app.workflow_tree.exists(iid))
        self.assertFalse(app.workflow_tree.exists(str(len(only))))
        self.assertEqual(
            app.workflow_tree.value("0", "index"), 1,
        )

    def test_plain_step_change_does_not_rebuild_the_global_module_table(self):
        app = self.make_app(count=60)
        app.global_tree.counters.reset()
        app._refresh_one_workflow_row(0)
        self.assertEqual(
            app.global_tree.counters.insert, 0,
            "普通工作流行变化不应该无条件重建全局模块表",
        )


def run_benchmark() -> None:
    """打印基线 / 实测对照表（自带隔离数据目录）。"""
    from tests.test_edit_performance import EditPipelineBenchmark as _Bench

    holder = _Bench(methodName="run")
    holder.setUp()
    try:
        print(f"Python {sys.version.split()[0]} · 峰值内存 {peak_memory_mb():.0f} MB")
        print("端点：FakeTree（业务修改 + 撤销记录 + 摘要计算 + 行更新调度），"
              "不含真实 Tk 渲染")
        print()
        for count in _Bench.SIZES:
            app = holder._app(count)
            rows = len(app.action_tree.get_children())
            print(f"--- {count} 条动作（视图中 {rows} 行）---")
            stats = measure(
                "单行编辑（业务+撤销+刷新）",
                lambda app=app: holder._edit_cycle(app),
                warmup=3, samples=15,
            )
            print("  " + stats.as_row())
            app.action_tree.counters.reset()
            holder._edit_cycle(app)
            counters = app.action_tree.counters
            print(f"  单行编辑的控件调用：insert={counters.insert} "
                  f"delete_rows={counters.delete_rows} set={counters.set}")
            rebuilt = measure(
                "全量重建对照",
                lambda app=app: app.rebuild_action_tree(),
                warmup=1, samples=3,
            )
            print("  " + rebuilt.as_row())
            print(f"  单次重建插入行数：{app.action_tree.counters.insert}")
    finally:
        holder.tearDown()


if __name__ == "__main__":
    if "--benchmark" in sys.argv:
        run_benchmark()
    else:
        unittest.main()
