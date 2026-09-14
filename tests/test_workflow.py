"""工作流页：步骤增删、显示、撤销、激活窗口开关。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_workflow.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.common import *  # noqa: E402,F401,F403


class WorkflowInsertTests(unittest.TestCase):
    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._notify = Mock()
        app._script_category_dir = Mock()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._update_workflow_selection_color = Mock()
        app.workflow_tree = Mock()
        app.root = Mock()
        return app

    def test_insert_above_places_step_before_selected(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"kind": "global_module", "script": "g.json", "step_id": "g"},
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "above"
        app.workflow_tree.selection.return_value = ("0",)  # 选中 a.json（全局模块在单独列表）
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value="C:/scripts/新脚本.json"):
            app.insert_workflow_step()
        # 全局模块保持首位，新步骤插在 a.json 之前
        self.assertEqual(
            [Path(s["script"]).name for s in app.workflow.steps],
            ["g.json", "新脚本.json", "a.json", "b.json"],
        )
        app.workflow_tree.selection_set.assert_called_with("0")
        app._persist_workflow_draft.assert_called_once()

    def test_insert_below_places_step_after_selected(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("0",)
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value="C:/x/new.json"):
            app.insert_workflow_step()
        self.assertEqual(
            [Path(s["script"]).name for s in app.workflow.steps],
            ["a.json", "new.json", "b.json"],
        )
        app.workflow_tree.selection_set.assert_called_with("1")

    def test_insert_below_last_row_appends_to_end(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("1",)
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value="C:/x/new.json"):
            app.insert_workflow_step()
        self.assertEqual(
            [Path(s["script"]).name for s in app.workflow.steps],
            ["a.json", "b.json", "new.json"],
        )
        app.workflow_tree.selection_set.assert_called_with("2")

    def test_insert_requires_selected_row(self):
        app = self._app()
        app.workflow = Workflow(steps=[{"script": "a.json", "step_id": "a"}])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "above"
        app.workflow_tree.selection.return_value = ()
        app.insert_workflow_step()
        app._notify.assert_called_once_with("插入脚本", "请先选择插入位置所在的工作流行。")
        self.assertEqual(len(app.workflow.steps), 1)

    def test_insert_cancel_keeps_steps_unchanged(self):
        app = self._app()
        app.workflow = Workflow(steps=[{"script": "a.json", "step_id": "a"}])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("0",)
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value=""):
            app.insert_workflow_step()
        self.assertEqual(len(app.workflow.steps), 1)
        app._persist_workflow_draft.assert_not_called()

    def test_insert_module_below_selected_workflow_row(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("0",)
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "images/switch.png", "template": "images/switch.png",
        }
        with patch_app("ModulePickerDialog") as picker_class:
            picker_class.return_value.show.return_value = action
            app.insert_workflow_module_step()

        self.assertEqual([step.get("kind", "script") for step in app.workflow.steps], [
            "script", "module", "script",
        ])
        self.assertEqual(app.workflow.steps[1]["action"]["module_key"], "images/switch.png")
        self.assertEqual(app.workflow.steps[1]["action"]["module_name"], "switch")
        app.workflow_tree.selection_set.assert_called_with("1")
        picker_class.assert_called_once_with(
            app.root, categories=("switch", "special"), allow_number=False,
        )

    def test_add_multiple_modules_as_workflow_rows(self):
        app = self._app()
        app.workflow = Workflow()
        app._set_status = Mock()
        app.workflow_tree.selection.return_value = ()  # 未选中行：追加到末尾
        actions = [
            {"type": "image_match", "module_ref": True, "module_key": "module:a"},
            {"type": "image_match", "module_ref": True, "module_key": "module:b"},
        ]
        with patch_app("ModulePickerDialog") as picker_class:
            picker_class.return_value.show.return_value = actions
            app.add_workflow_module_step()

        self.assertEqual([step["kind"] for step in app.workflow.steps], ["module", "module"])
        self.assertEqual([step["action"]["module_key"] for step in app.workflow.steps], [
            "module:a", "module:b",
        ])
        picker_class.assert_called_once_with(
            app.root, categories=("switch", "special"), multi_select=True,
            allow_number=False,
        )
        app._persist_workflow_draft.assert_called_once()

    def test_add_script_step_inserts_below_selected(self):
        # “选择已有脚本”也跟随插入位置选项：选中行下方插入，不再总是追加到末尾。
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("0",)
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value="C:/x/new.json"):
            app.add_script_step()
        self.assertEqual(
            [Path(s["script"]).name for s in app.workflow.steps],
            ["a.json", "new.json", "b.json"],
        )
        app.workflow_tree.selection_set.assert_called_with("1")

    def test_add_script_step_inserts_above_selected(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "above"
        app.workflow_tree.selection.return_value = ("1",)
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value="C:/x/new.json"):
            app.add_script_step()
        self.assertEqual(
            [Path(s["script"]).name for s in app.workflow.steps],
            ["a.json", "new.json", "b.json"],
        )
        app.workflow_tree.selection_set.assert_called_with("1")

    def test_add_script_step_appends_without_selection(self):
        # 未选中行：保持“添加”语义，追加到末尾。
        app = self._app()
        app.workflow = Workflow(steps=[{"script": "a.json", "step_id": "a"}])
        app.workflow_insert_position_var = Mock()
        app.workflow_tree.selection.return_value = ()
        with patch("macroflow.ui.app.filedialog.askopenfilename", return_value="C:/x/new.json"):
            app.add_script_step()
        self.assertEqual(
            [Path(s["script"]).name for s in app.workflow.steps],
            ["a.json", "new.json"],
        )

    def test_add_workflow_module_step_inserts_below_selected(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app._set_status = Mock()
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("0",)
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "images/switch.png", "template": "images/switch.png",
        }
        with patch_app("ModulePickerDialog") as picker_class:
            picker_class.return_value.show.return_value = action
            app.add_workflow_module_step()

        self.assertEqual([step.get("kind", "script") for step in app.workflow.steps], [
            "script", "module", "script",
        ])
        self.assertEqual(app.workflow.steps[1]["action"]["module_key"], "images/switch.png")
        app.workflow_tree.selection_set.assert_called_with("1")

    def test_add_workflow_module_multiple_inserts_in_order(self):
        # 多选模块按顺序插入到选中行下方。
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app._set_status = Mock()
        app.workflow_insert_position_var = Mock()
        app.workflow_insert_position_var.get.return_value = "below"
        app.workflow_tree.selection.return_value = ("0",)
        actions = [
            {"type": "image_match", "module_ref": True, "module_key": "module:a"},
            {"type": "image_match", "module_ref": True, "module_key": "module:b"},
        ]
        with patch_app("ModulePickerDialog") as picker_class:
            picker_class.return_value.show.return_value = actions
            app.add_workflow_module_step()
        self.assertEqual(
            [step.get("action", {}).get("module_key") for step in app.workflow.steps],
            [None, "module:a", "module:b", None],
        )
        app.workflow_tree.selection_set.assert_called_with("2")

    def test_set_workflow_insert_position_toggles_buttons(self):
        app = self._app()

        class FakeVar:
            def __init__(self):
                self.value = "below"

            def get(self):
                return self.value

            def set(self, value):
                self.value = value

        app.workflow_insert_position_var = FakeVar()
        app.workflow_insert_above_button = Mock()
        app.workflow_insert_below_button = Mock()
        app._set_workflow_insert_position(True)
        self.assertEqual(app.workflow_insert_position_var.get(), "above")
        app.workflow_insert_above_button.configure.assert_called_with(bootstyle="primary")
        app.workflow_insert_below_button.configure.assert_called_with(bootstyle="secondary")

    def test_add_workflow_global_module_selects_module_object_directly(self):
        app = self._app()
        app.workflow = Workflow()
        app.global_tree = Mock()
        app._set_status = Mock()
        actions = [{
            "type": "global_detect", "template": "images/global.png",
            "module_ref": True, "module_category": "workflow_global",
        }, {
            "type": "global_detect", "template": "images/global2.png",
            "module_ref": True, "module_category": "workflow_global",
        }]
        app._append_global_module = Mock()
        with patch_app("ModulePickerDialog") as picker_class:
            picker_class.return_value.show.return_value = actions
            app.add_workflow_global_module()
        picker_class.assert_called_once_with(
            app.root, categories=("workflow_global",), multi_select=True,
        )
        self.assertEqual(app._append_global_module.call_count, 2)
        app._append_global_module.assert_any_call(config=actions[0], refresh=False)
        app._append_global_module.assert_any_call(config=actions[1], refresh=False)
        app.rebuild_workflow_tree.assert_called_once()
        app._persist_workflow_draft.assert_called_once()


class WorkflowDisplayTests(unittest.TestCase):
    def test_workflow_header_keeps_execution_buttons_in_dedicated_action_bar(self):
        source = inspect.getsource(MacroFlowApp._build_workflow_tab)

        self.assertIn("workflow_action_bar", source)
        self.assertIn('text="从选中行运行"', source)
        self.assertIn('text="运行工作流"', source)

    def test_toolbar_spec_rows_keep_action_buttons_visible(self):
        import macroflow.ui.app as app_module

        specs = tuple((f"button-{index}", None, "ScriptTool.TButton") for index in range(16))
        toolbar_spec_rows = getattr(app_module, "toolbar_spec_rows", None)
        self.assertIsNotNone(toolbar_spec_rows)
        rows = toolbar_spec_rows(specs, row_size=8)

        self.assertEqual([len(row) for row in rows], [8, 8])
        self.assertEqual(tuple(item for row in rows for item in row), specs)

    def test_workflow_tab_uses_resizable_split_and_grouped_toolbars(self):
        source = inspect.getsource(MacroFlowApp._build_workflow_tab)
        self.assertIn("ttk.Panedwindow", source)
        self.assertIn('text="添加 / 插入"', source)
        self.assertIn('text="编辑 / 排序"', source)
        self.assertIn("height=6", source)
        self.assertIn("height=10", source)

    def test_restart_resolved_row_prefers_action_then_workflow_default(self):
        # 「重新执行工作流」跳转行解析：动作级 → 工作流统一默认 → 第 1 行。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(restart_default_row=4)
        self.assertEqual(
            app._restart_workflow_resolved_row({"restart_workflow_target_row": 3}),
            3,
        )
        self.assertEqual(
            app._restart_workflow_resolved_row({"type": "restart_workflow"}),
            4,
        )
        app.workflow.restart_default_row = 0
        self.assertEqual(
            app._restart_workflow_resolved_row({"type": "restart_workflow"}),
            1,
        )
        # 非法值一律视为未设置；未挂工作流对象时按第 1 行处理。
        app.workflow.restart_default_row = 4
        self.assertEqual(
            app._restart_workflow_resolved_row(
                {"restart_workflow_target_row": "oops"},
            ),
            4,
        )
        del app.workflow
        self.assertEqual(
            app._restart_workflow_resolved_row({"type": "restart_workflow"}),
            1,
        )

    def test_workflow_model_has_no_unified_restart_target(self):
        # 旧工作流文件里的 restart_target_step_id 不再进入模型（统一跳转已删除）。
        workflow = Workflow.from_dict({
            "name": "测试",
            "steps": [{"script": "a.json", "step_id": "row-a"}],
            "restart_target_step_id": "row-a",
        })
        self.assertFalse(hasattr(workflow, "restart_target_step_id"))
        self.assertNotIn("restart_target_step_id", workflow.to_dict())

    def test_workflow_restart_default_row_round_trips(self):
        # 默认跳转行是工作流文件字段（工作流页面统一设置），随文件保存。
        workflow = Workflow.from_dict({
            "name": "测试", "restart_default_row": "3",
            "steps": [{"script": "a.json", "step_id": "row-a"}],
        })
        self.assertEqual(workflow.restart_default_row, 3)
        self.assertEqual(workflow.to_dict()["restart_default_row"], 3)
        self.assertEqual(Workflow.from_dict(workflow.to_dict()).restart_default_row, 3)
        # 非法值按未设置处理。
        self.assertEqual(
            Workflow.from_dict({"restart_default_row": "oops"}).restart_default_row, 0,
        )

    def test_workflow_module_name_reads_existing_nested_action_reference(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        step = {
            "kind": "module",
            "action": {
                "module_key": "images/部分/资讯叉叉.png",
                "template": "images/部分/资讯叉叉.png",
            },
        }
        with patch_app("registered_module_object", return_value={"name": "资讯叉叉"}):
            self.assertEqual(app._workflow_step_name(step), "模块 资讯叉叉")

    def test_workflow_module_name_reads_persisted_action_name(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        step = {
            "kind": "module", "action": {
                "module_key": "module:claim", "module_name": "可领取",
            },
        }
        with patch_app("registered_module_object", return_value=None):
            self.assertEqual(app._workflow_module_key(step), "module:claim")
            self.assertEqual(app._workflow_step_name(step), "模块 可领取")

    def test_successful_workflow_repeat_decrements_to_zero_without_disabling(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "a.json", "repeats": 1, "enabled": True,
        }])
        app.workflow_path = Path("flow.json")
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._log = Mock()

        with patch_app("save_workflow", return_value=Path("flow.json")) as save:
            app._consume_workflow_repeat(0)

        self.assertEqual(app.workflow.steps[0]["repeats"], 0)
        self.assertTrue(app.workflow.steps[0]["enabled"])
        save.assert_called_once_with(app.workflow, Path("flow.json"))
        app._persist_workflow_draft.assert_called_once()

    def test_batch_settings_apply_all_three_values_to_every_step(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {"script": "a", "repeats": 1, "before_ms": 0, "repeat_interval_ms": 1000},
            {"script": "b", "repeats": 9, "before_ms": 300, "repeat_interval_ms": 500},
        ])
        app.root = Mock()
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ("1",)
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._set_status = Mock()
        dialog = Mock()
        dialog.show.return_value = {
            "repeats": 4, "before_ms": 1200, "repeat_interval_ms": 2300,
        }

        with patch_app("WorkflowBatchSettingsDialog", return_value=dialog):
            app.set_all_workflow_step_options()

        for step in app.workflow.steps:
            self.assertEqual(step["repeats"], 4)
            self.assertEqual(step["before_ms"], 1200)
            self.assertEqual(step["repeat_interval_ms"], 2300)
        app.workflow_tree.selection_set.assert_called_once_with("1")
        app._persist_workflow_draft.assert_called_once()

    def test_batch_settings_only_change_selected_parameter(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {"script": "a", "repeats": 2, "before_ms": 100, "repeat_interval_ms": 500},
            {"script": "b", "repeats": 7, "before_ms": 900, "repeat_interval_ms": 800},
        ])
        app.root = Mock()
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._set_status = Mock()
        dialog = Mock()
        dialog.show.return_value = {"repeat_interval_ms": 3000}

        with patch_app("WorkflowBatchSettingsDialog", return_value=dialog):
            app.set_all_workflow_step_options()

        self.assertEqual(app.workflow.steps[0]["repeats"], 2)
        self.assertEqual(app.workflow.steps[1]["repeats"], 7)
        self.assertEqual(app.workflow.steps[0]["before_ms"], 100)
        self.assertEqual(app.workflow.steps[1]["before_ms"], 900)
        self.assertEqual([step["repeat_interval_ms"] for step in app.workflow.steps], [3000, 3000])

    def test_single_click_release_does_not_edit_workflow_cell(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_drag_index = 0
        app.workflow_was_dragged = False
        app._persist_workflow_draft = Mock()
        app._edit_workflow_cell = Mock()

        app._workflow_drag_end(Mock())

        app._edit_workflow_cell.assert_not_called()
        app._persist_workflow_draft.assert_called_once()

    def test_editing_interval_cell_only_changes_interval(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "scripts/a.json", "repeats": 3,
            "before_ms": 250, "repeat_interval_ms": 1000,
        }])
        app.workflow_tree = Mock()
        app.workflow_tree.identify_row.return_value = "0"
        app.workflow_tree.identify_column.return_value = "#5"
        app.root = Mock()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()

        with patch_app("DurationDialog") as prompt:
            prompt.return_value.show.return_value = 2400
            app._edit_workflow_cell(Mock(x=500, y=10))

        self.assertEqual(app.workflow.steps[0]["repeat_interval_ms"], 2400)
        self.assertEqual(app.workflow.steps[0]["repeats"], 3)
        self.assertEqual(app.workflow.steps[0]["before_ms"], 250)
        prompt.assert_called_once()

    def test_new_workflow_step_uses_defaults_without_prompts(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        with patch_app("display_path", return_value="scripts/a.json"), \
             patch("macroflow.ui.app.simpledialog.askinteger") as prompt:
            app._append_workflow_step(Path("a.json"))
        step = app.workflow.steps[0]
        self.assertEqual(step["script"], "scripts/a.json")
        self.assertEqual(step["repeats"], 1)
        self.assertEqual(step["before_ms"], 0)
        self.assertEqual(step["repeat_interval_ms"], DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS)
        self.assertFalse(step["unlimited"])
        self.assertTrue(step["enabled"])
        self.assertTrue(step.get("step_id"))
        prompt.assert_not_called()

    def test_workflow_only_shows_script_name(self):
        self.assertEqual(workflow_script_name("scripts/副本循环.json"), "副本循环")
        self.assertEqual(workflow_script_name(r"scripts\每日任务.json"), "每日任务")

    def test_workflow_progress_shows_script_and_repeat_positions(self):
        self.assertEqual(
            workflow_execution_progress(2, 29, "每日任务", 4, 3),
            "工作流 2/29 · 每日任务\n共执行 4 次 · 当前第 3/4 次 · F12 停止",
        )

    def test_drag_reorders_actual_workflow_steps(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"script": "a"}, {"script": "b"}, {"script": "c"}])
        app.workflow_drag_index = 0
        app.workflow_tree = Mock()
        app.workflow_tree.identify_row.return_value = "2"
        app.rebuild_workflow_tree = Mock()

        app._workflow_drag_motion(Mock(y=100))

        self.assertEqual([step["script"] for step in app.workflow.steps], ["b", "c", "a"])
        self.assertEqual(app.workflow_drag_index, 2)

    def test_missing_script_row_is_marked(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"script": "scripts/definitely-missing.json"}])
        app.workflow_tree = Mock()
        app.workflow_tree.get_children.return_value = ()
        app.empty_workflow_hint = Mock()

        app.rebuild_workflow_tree()

        values = app.workflow_tree.insert.call_args.kwargs["values"]
        tags = app.workflow_tree.insert.call_args.kwargs["tags"]
        self.assertIn("文件不存在", values[1])
        self.assertEqual(tags, ("missing",))

    def test_disabled_workflow_row_is_dimmed(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"script": "scripts/disabled.json", "enabled": False}])
        app.workflow_tree = Mock()
        app.workflow_tree.get_children.return_value = ()
        app.empty_workflow_hint = Mock()

        app.rebuild_workflow_tree()

        values = app.workflow_tree.insert.call_args.kwargs["values"]
        tags = app.workflow_tree.insert.call_args.kwargs["tags"]
        self.assertEqual(values[-1], "● 已禁用")
        self.assertEqual(tags, ("disabled",))

    def test_zero_repeat_workflow_row_is_marked_exhausted(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "scripts/exhausted.json", "repeats": 0, "enabled": True,
        }])
        app.workflow_tree = Mock()
        app.workflow_tree.get_children.return_value = ()
        app.empty_workflow_hint = Mock()

        app.rebuild_workflow_tree()

        values = app.workflow_tree.insert.call_args.kwargs["values"]
        tags = app.workflow_tree.insert.call_args.kwargs["tags"]
        self.assertEqual(values[-1], "○ 次数用完")
        self.assertEqual(tags, ("exhausted",))

    def test_unlimited_workflow_row_shows_infinite_and_not_exhausted(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "scripts/unlimited.json", "repeats": 0,
            "unlimited": True, "enabled": True,
        }])
        app.workflow_tree = Mock()
        app.workflow_tree.get_children.return_value = ()
        app.empty_workflow_hint = Mock()

        app.rebuild_workflow_tree()

        values = app.workflow_tree.insert.call_args.kwargs["values"]
        tags = app.workflow_tree.insert.call_args.kwargs["tags"]
        self.assertEqual(values[2], "∞")
        self.assertEqual(values[-1], "✓ 不计次数")
        self.assertEqual(tags, ("unlimited",))

    def test_unlimited_workflow_repeat_is_not_consumed(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "a.json", "repeats": 3, "unlimited": True, "enabled": True,
        }])
        app.workflow_path = Path("flow.json")
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._log = Mock()

        with patch_app("save_workflow") as save:
            app._consume_workflow_repeat(0)

        self.assertEqual(app.workflow.steps[0]["repeats"], 3)
        save.assert_not_called()
        app._persist_workflow_draft.assert_not_called()
        self.assertTrue(any("不计次数" in call.args[0] for call in app._log.call_args_list))

    def test_workflow_progress_unlimited_mode(self):
        self.assertEqual(
            workflow_execution_progress(2, 29, "每日任务", 1, unlimited=True),
            "工作流 2/29 · 每日任务\n不计次数 · 每次到达执行 1 次 · F12 停止",
        )

    def test_editing_repeat_cell_opens_workflow_repeat_dialog(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "scripts/a.json", "repeats": 2, "unlimited": False,
            "before_ms": 0, "repeat_interval_ms": 1000,
        }])
        app.workflow_tree = Mock()
        app.workflow_tree.identify_row.return_value = "0"
        app.workflow_tree.identify_column.return_value = "#3"
        app.root = Mock()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        dialog = Mock()
        dialog.show.return_value = {"repeats": 5, "unlimited": True}

        with patch_app("WorkflowRepeatDialog", return_value=dialog):
            app._edit_workflow_cell(Mock(x=100, y=10))

        self.assertEqual(app.workflow.steps[0]["repeats"], 5)
        self.assertTrue(app.workflow.steps[0]["unlimited"])
        app._persist_workflow_draft.assert_called_once()

    def test_batch_settings_apply_unlimited_to_every_step(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[
            {"script": "a", "repeats": 1, "before_ms": 0, "repeat_interval_ms": 1000},
            {"script": "b", "repeats": 9, "before_ms": 300, "repeat_interval_ms": 500},
        ])
        app.root = Mock()
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._set_status = Mock()
        dialog = Mock()
        dialog.show.return_value = {"unlimited": True}

        with patch_app("WorkflowBatchSettingsDialog", return_value=dialog):
            app.set_all_workflow_step_options()

        self.assertTrue(all(step["unlimited"] for step in app.workflow.steps))

    def test_toggle_selected_workflow_step_preserves_selection(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"script": "a", "enabled": True}])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ("0",)
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._set_status = Mock()

        app.toggle_selected_workflow_step()

        self.assertFalse(app.workflow.steps[0]["enabled"])
        app.workflow_tree.selection_set.assert_called_once_with("0")


class WorkflowDeleteUndoTests(unittest.TestCase):
    def test_workflow_player_receives_current_step_script_name(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "team.json"
            save_script(MacroScript(name="经典团战", actions=[{"type": "delay", "ms": 0}]), script_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)

            app._run_workflow_worker(
                [{"script": str(script_path), "repeats": 1, "before_ms": 0}],
                None, None, False,
            )

            self.assertEqual(app.player.play.call_args.kwargs["script_name"], "经典团战")

    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_tree = Mock()
        app.global_tree = Mock()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._update_workflow_selection_color = Mock()
        app._set_status = Mock()
        app.workflow_delete_undo_stack = []
        app.global_delete_undo_stack = []
        return app

    def test_workflow_delete_selects_neighbor_and_undo_restores_row(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
            {"script": "c.json", "step_id": "c"},
        ])
        app.workflow_tree.selection.return_value = ("1",)

        app.delete_workflow_step()

        self.assertEqual([step["step_id"] for step in app.workflow.steps], ["a", "c"])
        app.workflow_tree.selection_set.assert_called_with("1")
        app.workflow_tree.see.assert_called_with("1")

        app.undo_delete_workflow_step()

        self.assertEqual([step["step_id"] for step in app.workflow.steps], ["a", "b", "c"])
        app.workflow_tree.selection_set.assert_called_with("1")
        self.assertEqual(app.workflow_delete_undo_stack, [])

    def test_workflow_delete_last_row_selects_previous_row(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
        ])
        app.workflow_tree.selection.return_value = ("1",)

        app.delete_workflow_step()

        app.workflow_tree.selection_set.assert_called_once_with("0")

    def test_ctrl_a_selects_all_workflow_and_global_rows(self):
        app = self._app()
        app.workflow_tree.get_children.return_value = ("0", "1", "2")
        app.global_tree.get_children.return_value = ("0", "1")

        self.assertEqual(app._select_all_workflow_steps(), "break")
        self.assertEqual(app._select_all_global_modules(), "break")

        app.workflow_tree.selection_set.assert_called_once_with("0", "1", "2")
        app.global_tree.selection_set.assert_called_once_with("0", "1")

    def test_workflow_multi_delete_removes_all_selected_and_undo_restores(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"script": "a.json", "step_id": "a"},
            {"script": "b.json", "step_id": "b"},
            {"script": "c.json", "step_id": "c"},
        ])
        app.workflow_tree.selection.return_value = ("0", "2")

        app.delete_workflow_step()

        self.assertEqual([step["step_id"] for step in app.workflow.steps], ["b"])
        app.undo_delete_workflow_step()
        app.undo_delete_workflow_step()
        self.assertEqual([step["step_id"] for step in app.workflow.steps], ["a", "b", "c"])

    def test_global_multi_delete_removes_all_selected_and_undo_restores(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"kind": "global_module", "step_id": "g1"},
            {"kind": "global_module", "step_id": "g2"},
            {"kind": "global_module", "step_id": "g3"},
            {"script": "task.json", "step_id": "task"},
        ])
        app.global_tree.selection.return_value = ("0", "2")

        app.delete_global_module()

        self.assertEqual([step["step_id"] for step in app._global_module_steps()], ["g2"])
        app.undo_delete_global_module()
        app.undo_delete_global_module()
        self.assertEqual(
            [step["step_id"] for step in app._global_module_steps()], ["g1", "g2", "g3"],
        )

    def test_main_log_is_appended_to_its_session_file(self):
        with tempfile.TemporaryDirectory() as folder:
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.log_text = Mock()
            app.session_log_path = Path(folder) / "2026-08-11" / "session.log"
            app.session_log_path.parent.mkdir(parents=True)
            with patch_app("get_cursor_pos", return_value=(12, 34)):
                app._log("备份完成")
            self.assertIn("[鼠标 12,34] 备份完成", app.session_log_path.read_text(encoding="utf-8"))

    def test_worker_log_is_written_before_ui_callback_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.root = Mock()
            app.log_text = Mock()
            app.log_file_lock = threading.Lock()
            app.session_log_path = Path(folder) / "2026-08-11" / "session.log"
            with patch_app("get_cursor_pos", return_value=(56, 78)):
                app._ui(app._log, "后台识别完成")
            self.assertIn(
                "[鼠标 56,78] 后台识别完成",
                app.session_log_path.read_text(encoding="utf-8"),
            )
            app.log_text.insert.assert_not_called()
            queued_callback, queued_line = app.root.after.call_args.args[1:]
            queued_callback(queued_line)
            app.log_text.insert.assert_called_once()

    def test_multi_toggle_applies_one_state_to_all_selected_rows(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"kind": "global_module", "step_id": "g1", "enabled": False},
            {"kind": "global_module", "step_id": "g2", "enabled": True},
            {"script": "a.json", "step_id": "a", "enabled": True},
            {"script": "b.json", "step_id": "b", "enabled": True},
        ])
        app.workflow_tree.selection.return_value = ("0", "1")
        app.global_tree.selection.return_value = ("0", "1")

        app.toggle_selected_workflow_step()
        app.toggle_selected_global_module()

        self.assertTrue(all(not step["enabled"] for step in app._workflow_only_steps()))
        self.assertTrue(all(step["enabled"] for step in app._global_module_steps()))
        app.workflow_tree.selection_set.assert_called_once_with("0", "1")
        app.global_tree.selection_set.assert_called_once_with("0", "1")

    def test_global_tree_shows_live_module_registry_disabled_state(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "kind": "global_module", "enabled": True,
            "config": {
                "module_ref": True, "module_key": "module:global-disabled",
                "template": "images/global.png",
            },
        }])
        app.global_tree = Mock()
        app.global_tree.get_children.return_value = ()
        app.empty_global_hint = Mock()
        app._global_module_label = Mock(return_value="◆ 模块对象 · 已禁用全局模块")
        app._autosize_tree_column = Mock()

        with patch_app("registered_module_object", return_value={"enabled": False}):
            app.rebuild_global_tree()

        insert = app.global_tree.insert.call_args
        self.assertEqual(insert.kwargs["values"][2], "● 模块已禁用")
        self.assertEqual(insert.kwargs["tags"], ("disabled",))

    def test_global_modules_can_be_deleted_continuously_and_undone(self):
        app = self._app()
        app.workflow = Workflow(steps=[
            {"kind": "global_module", "step_id": "g1"},
            {"kind": "global_module", "step_id": "g2"},
            {"kind": "global_module", "step_id": "g3"},
            {"script": "task.json", "step_id": "task"},
        ])
        app.global_tree.selection.return_value = ("1",)

        app.delete_global_module()
        self.assertEqual(
            [step["step_id"] for step in app._global_module_steps()], ["g1", "g3"],
        )
        app.global_tree.selection_set.assert_called_with("1")

        app.global_tree.selection.return_value = ("1",)
        app.delete_global_module()
        self.assertEqual([step["step_id"] for step in app._global_module_steps()], ["g1"])
        app.global_tree.selection_set.assert_called_with("0")

        app.undo_delete_global_module()
        app.undo_delete_global_module()

        self.assertEqual(
            [step["step_id"] for step in app._global_module_steps()], ["g1", "g2", "g3"],
        )
        self.assertEqual(app.global_delete_undo_stack, [])

    def test_workflow_global_module_key_only_accepts_module_reference(self):
        self.assertEqual(
            MacroFlowApp._workflow_global_module_key({
                "config": {"module_ref": True, "template": "images/global.png"},
            }),
            "images/global.png",
        )
        self.assertEqual(
            MacroFlowApp._workflow_global_module_key({"config": {"template": "legacy.png"}}),
            "",
        )

    def test_open_global_module_editor_keeps_identity_and_updates_shared_image(self):
        app = self._app()
        app.root = Mock()
        step = {
            "kind": "global_module",
            "config": {"module_ref": True, "template": "images/old.png"},
        }
        obj = {"category": "global", "name": "旧模块", "template": "images/old.png"}
        updated = {"category": "global", "name": "新模块", "template": "images/new.png"}
        form = Mock()
        form.show.return_value = ("images/old.png", "images/old.png", updated)
        with patch_app("registered_module_object", return_value=obj), \
             patch_app("TemplateRegionFormDialog", return_value=form) as form_class, \
             patch_app("update_module_object") as update:
            app._open_module_object_editor("images/old.png", workflow_step=step)

        form_class.assert_called_once_with(
            app.root, "images/old.png", object_dict=obj, category="global",
        )
        update.assert_called_once_with("images/old.png", updated, old_key="images/old.png")
        self.assertEqual(step["config"]["module_key"], "images/old.png")
        self.assertEqual(step["config"]["template"], "images/new.png")
        app.rebuild_workflow_tree.assert_called_once()
        app._persist_workflow_draft.assert_called_once()

    def test_open_global_module_in_new_window_passes_module_key(self):
        app = self._app()
        app._log = Mock()
        step = {
            "kind": "global_module",
            "config": {"module_ref": True, "template": "images/global.png"},
        }
        with patch_app("registered_module_object", return_value={"category": "global"}), \
             patch_app("spawn_new_instance") as spawn:
            app._open_workflow_global_module_in_new_window(step)

        args = spawn.call_args.args[0]
        self.assertEqual(args[-2:], ["--edit-module", "images/global.png"])

    def test_disabled_selection_uses_distinct_highlight_color(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{"script": "a", "enabled": False}])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ("0",)
        app.root = Mock()

        app._update_workflow_selection_color()

        call = app.root.style.map.call_args
        self.assertEqual(call.args[0], "Workflow.Treeview")
        self.assertEqual(call.kwargs["background"], [("selected", "#6B4615")])
        self.assertEqual(call.kwargs["foreground"], [("selected", "#FFE1A3")])

    def test_unlimited_selection_uses_distinct_highlight_color(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(steps=[{
            "script": "a", "enabled": True, "unlimited": True,
        }])
        app.workflow_tree = Mock()
        app.workflow_tree.selection.return_value = ("0",)
        app.root = Mock()

        app._update_workflow_selection_color()

        call = app.root.style.map.call_args
        self.assertEqual(call.args[0], "Workflow.Treeview")
        self.assertEqual(call.kwargs["background"], [("selected", "#1F4D30")])
        self.assertEqual(call.kwargs["foreground"], [("selected", "#7BC96F")])

    def test_execution_skips_missing_script_and_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            valid_path = Path(folder) / "valid.json"
            save_script(MacroScript(name="有效脚本", actions=[{"type": "delay", "ms": 0}]), valid_path)
            missing_path = Path(folder) / "missing.json"

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(missing_path), "repeats": 1, "before_ms": 0},
                {"script": str(valid_path), "repeats": 1, "before_ms": 0},
            ]

            app._run_workflow_worker(steps, None, None, False)

            app.player.play.assert_called_once()
            self.assertEqual(
                app.player.play.call_args.kwargs["repeat_interval_ms"],
                DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
            )
            self.assertTrue(any("跳过工作流第 1/2 行" in call.args[0] for call in app._log.call_args_list))

    def test_execution_skips_disabled_script_and_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            disabled_path = Path(folder) / "disabled.json"
            enabled_path = Path(folder) / "enabled.json"
            save_script(MacroScript(name="禁用项", actions=[{"type": "delay", "ms": 1}]), disabled_path)
            save_script(MacroScript(name="启用项", actions=[{"type": "delay", "ms": 2}]), enabled_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(disabled_path), "enabled": False},
                {"script": str(enabled_path), "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False)

            app.player.play.assert_called_once()
            self.assertEqual(app.player.play.call_args.args[0], [{"type": "delay", "ms": 2}])
            self.assertTrue(any("该任务已禁用" in call.args[0] for call in app._log.call_args_list))

    def test_execution_skips_zero_repeat_script_and_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            exhausted_path = Path(folder) / "exhausted.json"
            enabled_path = Path(folder) / "enabled.json"
            save_script(MacroScript(name="次数用完", actions=[{"type": "delay", "ms": 1}]), exhausted_path)
            save_script(MacroScript(name="仍可执行", actions=[{"type": "delay", "ms": 2}]), enabled_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(exhausted_path), "repeats": 0, "enabled": True},
                {"script": str(enabled_path), "repeats": 1, "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False)

            app.player.play.assert_called_once()
            self.assertEqual(app.player.play.call_args.args[0], [{"type": "delay", "ms": 2}])
            self.assertTrue(any("执行次数已用完" in call.args[0] for call in app._log.call_args_list))

    def test_execution_runs_unlimited_row_even_with_zero_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            unlimited_path = Path(folder) / "unlimited.json"
            save_script(MacroScript(name="不计次数", actions=[{"type": "delay", "ms": 1}]), unlimited_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(unlimited_path), "repeats": 0, "unlimited": True, "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False)

            app.player.play.assert_called_once()
            self.assertEqual(app.player.play.call_args.args[1], 1)
            self.assertFalse(any("执行次数已用完" in call.args[0] for call in app._log.call_args_list))
            self.assertTrue(any("不计次数" in call.args[0] for call in app._log.call_args_list))

    def test_workflow_test_mode_caps_counted_rows_and_keeps_unlimited_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            counted_path = Path(folder) / "counted.json"
            unlimited_path = Path(folder) / "unlimited.json"
            save_script(MacroScript(name="计次", actions=[{"type": "delay", "ms": 1}]), counted_path)
            save_script(MacroScript(name="不计次数", actions=[{"type": "delay", "ms": 2}]), unlimited_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(counted_path), "repeats": 8, "enabled": True},
                {"script": str(unlimited_path), "repeats": 0, "unlimited": True, "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False, test_mode=True)

            self.assertEqual(app.player.play.call_count, 2)
            self.assertEqual([call.args[1] for call in app.player.play.call_args_list], [1, 1])
            self.assertTrue(any("测试执行 1 次" in call.args[0] for call in app._log.call_args_list))
            self.assertTrue(any("不计次数" in call.args[0] for call in app._log.call_args_list))

    def test_workflow_test_mode_runs_unlimited_when_all_counted_rows_are_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            exhausted_path = Path(folder) / "exhausted.json"
            unlimited_path = Path(folder) / "unlimited.json"
            save_script(MacroScript(name="用完", actions=[{"type": "delay", "ms": 1}]), exhausted_path)
            save_script(MacroScript(name="不计次数", actions=[{"type": "delay", "ms": 2}]), unlimited_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)

            app._run_workflow_worker([
                {"script": str(exhausted_path), "repeats": 0, "enabled": True},
                {"script": str(unlimited_path), "repeats": 0, "unlimited": True, "enabled": True},
            ], None, None, False, test_mode=True)

            app.player.play.assert_called_once()
            self.assertEqual(app.player.play.call_args.args[0], [{"type": "delay", "ms": 2}])

    def test_workflow_test_mode_does_not_consume_remaining_count(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        step = {"repeats": 8, "unlimited": False}
        app.workflow_test_mode_active = True
        app._workflow_only_steps = Mock(return_value=[step])
        app._log = Mock()

        app._consume_workflow_repeat(0)

        self.assertEqual(step["repeats"], 8)
        self.assertIn("测试模式，不扣减次数", app._log.call_args.args[0])

    def test_repeat_count_is_consumed_before_next_workflow_step_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            first_path = Path(folder) / "first.json"
            second_path = Path(folder) / "second.json"
            save_script(MacroScript(name="第一步", actions=[{"type": "delay", "ms": 1}]), first_path)
            save_script(MacroScript(name="第二步", actions=[{"type": "delay", "ms": 1}]), second_path)

            steps = [
                {"script": str(first_path), "repeats": 2, "enabled": True},
                {"script": str(second_path), "repeats": 1, "enabled": True},
            ]
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow = Workflow(steps=steps)
            app.workflow_path = None
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._workflow_only_steps = lambda: steps
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            queued_ui_calls = []
            app._ui = lambda callback, *args: queued_ui_calls.append((callback, args))
            observed_remaining = []

            def play(*args, **kwargs):
                observed_remaining.append(steps[0]["repeats"])
                total = int(args[1])
                for current in range(1, total + 1):
                    kwargs["on_repeat_complete"](current, total)

            app.player.play.side_effect = play

            app._run_workflow_worker(steps, None, None, False)

            self.assertEqual(observed_remaining, [2, 0])

    def test_workflow_executes_module_row_without_script_file(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app._enter_focus_mode = Mock()
        app._leave_focus_mode = Mock()
        app._set_status = Mock()
        app._set_execution_progress = Mock()
        app._append_mini_step = Mock()
        app._log = Mock()
        app._sound = Mock()
        app._handle_worker_error = Mock()
        app._finish_execution_visibility = Mock()
        app._ui = lambda callback, *args: callback(*args)
        action = {
            "type": "image_match", "module_ref": True,
            "module_key": "module:claim", "template": "images/claim.png",
        }
        step = {
            "kind": "module", "action": action, "repeats": 1,
            "enabled": True, "before_ms": 0,
        }

        with patch_app("registered_module_object", return_value={
            "name": "领取", "category": "switch", "template": "images/claim.png",
        }):
            app._run_workflow_worker([step], None, None, False)

        app.player.play.assert_called_once()
        self.assertEqual(app.player.play.call_args.args[0], [action])
        self.assertTrue(any("模块 领取" in call.args[0] for call in app._log.call_args_list))

    def test_workflow_ends_when_all_counted_steps_exhausted(self):
        with tempfile.TemporaryDirectory() as folder:
            exhausted_path = Path(folder) / "exhausted.json"
            unlimited_path = Path(folder) / "unlimited.json"
            save_script(MacroScript(name="次数用完", actions=[{"type": "delay", "ms": 1}]), exhausted_path)
            save_script(MacroScript(name="不计次数", actions=[{"type": "delay", "ms": 2}]), unlimited_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.global_guards = {"m1": {"key": "m1"}}
            app.guards_lock = threading.Lock()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(exhausted_path), "repeats": 0, "enabled": True},
                {"script": str(unlimited_path), "repeats": 0, "unlimited": True, "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False)

            app.player.play.assert_not_called()
            self.assertEqual(app.global_guards, {})
            self.assertTrue(any("所有计次脚本已执行完毕" in call.args[0] for call in app._log.call_args_list))
            self.assertTrue(any("工作流结束" in call.args[0] for call in app._append_mini_step.call_args_list))
            app._sound.assert_any_call("run_done")

    def test_workflow_does_not_end_while_counted_steps_remain(self):
        with tempfile.TemporaryDirectory() as folder:
            exhausted_path = Path(folder) / "exhausted.json"
            unlimited_path = Path(folder) / "unlimited.json"
            save_script(MacroScript(name="次数用完", actions=[{"type": "delay", "ms": 1}]), exhausted_path)
            save_script(MacroScript(name="不计次数", actions=[{"type": "delay", "ms": 2}]), unlimited_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.global_guards = {"m1": {"key": "m1"}}
            app.guards_lock = threading.Lock()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(exhausted_path), "repeats": 0, "enabled": True},
                {"script": str(unlimited_path), "repeats": 0, "unlimited": True, "enabled": True},
                {"script": str(exhausted_path), "repeats": 2, "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False)

            self.assertEqual(app.player.play.call_count, 2)
            # 守卫生命周期 = 一次执行：工作流结束后清空，不能残留到下一次执行。
            self.assertEqual(app.global_guards, {})
            self.assertFalse(any("所有计次脚本已执行完毕" in call.args[0] for call in app._log.call_args_list))

    def test_unlimited_only_workflow_does_not_end_prematurely(self):
        with tempfile.TemporaryDirectory() as folder:
            unlimited_path = Path(folder) / "unlimited.json"
            save_script(MacroScript(name="不计次数", actions=[{"type": "delay", "ms": 1}]), unlimited_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.global_guards = {"m1": {"key": "m1"}}
            app.guards_lock = threading.Lock()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(unlimited_path), "repeats": 0, "unlimited": True, "enabled": True},
            ]

            app._run_workflow_worker(steps, None, None, False)

            app.player.play.assert_called_once()
            # 守卫生命周期 = 一次执行：工作流结束后清空，不能残留到下一次执行。
            self.assertEqual(app.global_guards, {})
            self.assertFalse(any("所有计次脚本已执行完毕" in call.args[0] for call in app._log.call_args_list))

    def test_selected_row_unlimited_step_runs_after_prior_steps_are_exhausted(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "selected.json"
            save_script(MacroScript(name="选中行", actions=[{"type": "delay", "ms": 1}]), script_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)

            app._run_workflow_worker(
                [
                    {"script": str(script_path), "repeats": 0, "enabled": True},
                    {
                        "script": str(script_path),
                        "repeats": 0,
                        "unlimited": True,
                        "enabled": True,
                    },
                ],
                None,
                None,
                False,
                start_index=1,
            )

            app.player.play.assert_called_once()

    def test_workflow_can_start_from_selected_row(self):
        with tempfile.TemporaryDirectory() as folder:
            first_path = Path(folder) / "first.json"
            second_path = Path(folder) / "second.json"
            save_script(MacroScript(name="第一项", actions=[{"type": "delay", "ms": 11}]), first_path)
            save_script(MacroScript(name="第二项", actions=[{"type": "delay", "ms": 22}]), second_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app._ui = lambda callback, *args: callback(*args)
            steps = [
                {"script": str(first_path), "repeats": 1, "before_ms": 0},
                {"script": str(second_path), "repeats": 1, "before_ms": 0},
            ]

            app._run_workflow_worker(steps, None, None, False, start_index=1)

            app.player.play.assert_called_once()
            self.assertEqual(app.player.play.call_args.args[0], [{"type": "delay", "ms": 22}])
            self.assertTrue(any("从第 2/2 行开始" in call.args[0] for call in app._log.call_args_list))

    def test_run_workflow_from_selected_uses_shared_test_mode_option(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._selected_workflow_index = Mock(return_value=3)
        app.run_workflow = Mock()

        app.run_workflow_from_selected()

        app.run_workflow.assert_called_once_with(start_index=3)

    def test_new_workflow_run_reads_checked_test_mode_option(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.worker = None
        app.workflow_test_mode_var = Mock()
        app.workflow_test_mode_var.get.return_value = True
        app.workflow_test_mode_active = False
        app._workflow_only_steps = Mock(return_value=[{"script": "unused.json"}])
        app._global_module_steps = Mock(return_value=[])
        app._workflow_snapshot = Mock(side_effect=RuntimeError("stop after mode selection"))

        with self.assertRaisesRegex(RuntimeError, "stop after mode selection"):
            app.run_workflow()

        self.assertTrue(app.workflow_test_mode_active)

    def test_run_workflow_suppress_start_sound_skips_sound(self):
        # 「重新执行工作流」用 suppress_start_sound=True 重启，不能重复播放
        # 开始提示音；该参数曾缺失导致 TypeError（重启直接失败）。
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            script_path = Path(folder) / "plain.json"
            save_script(MacroScript(name="普通脚本", actions=[{"type": "delay", "ms": 1}]),
                        script_path)

            def make_app() -> MacroFlowApp:
                app = MacroFlowApp.__new__(MacroFlowApp)
                app.worker = None
                app.workflow_test_mode_var = Mock()
                app.workflow_test_mode_var.get.return_value = False
                app._workflow_only_steps = Mock(
                    return_value=[{"script": str(script_path), "repeats": 1}],
                )
                app._global_module_steps = Mock(return_value=[])
                app._workflow_snapshot = Mock()
                app._persist_workflow_draft = Mock()
                app.rebuild_workflow_tree = Mock()
                app.workflow_start_var = Mock()
                app.workflow_start_var.get.return_value = ""
                app._bound_hwnd = Mock(return_value=123)
                app._activation_settings_from_script = Mock(return_value=(False, None))
                app._log = Mock()
                app._notify = Mock()
                app.focus_mode_enabled_var = Mock()
                app.focus_mode_enabled_var.get.return_value = False
                app.activate_target_enabled_var = Mock()
                app.activate_target_enabled_var.get.return_value = True
                app.activation_enabled_var = Mock()
                app.activation_enabled_var.get.return_value = False
                app._clear_global_guards = Mock()
                app._clear_global_detect_rearm_locks = Mock()
                app.workflow_stop = threading.Event()
                app._sound = Mock()
                app._hide_main_for_execution = Mock()
                app._reset_execution_clock_for_new_run = Mock()
                app._set_execution_progress = Mock()
                app._show_execution_mini = Mock()
                app._append_mini_step = Mock()
                return app

            app = make_app()
            with patch("macroflow.ui.app.threading.Thread"):
                app.run_workflow(suppress_start_sound=True)
            app._sound.assert_not_called()

            app = make_app()
            with patch("macroflow.ui.app.threading.Thread"):
                app.run_workflow()
            app._sound.assert_called_once_with("run_start")

    def test_workflow_start_delay_settings_are_persisted(self):
        workflow = Workflow.from_dict({
            "name": "延时工作流", "steps": [],
            "start_delay_enabled": True, "start_delay_seconds": 12,
        })
        self.assertTrue(workflow.start_delay_enabled)
        self.assertEqual(workflow.start_delay_seconds, 12)
        self.assertEqual(workflow.to_dict()["start_delay_seconds"], 12)

    def test_workflow_start_delay_subsecond_rounds_up(self):
        # 存储精度是整秒：500ms → 1s、2500ms → 3s，亚秒延时不能被截断成 0。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow = Workflow(name="w", steps=[])
        app.workflow_start_delay_enabled_var = Mock()
        app.workflow_start_delay_enabled_var.get.return_value = True
        app.workflow_start_delay_seconds_var = Mock()
        app.workflow_start_delay_seconds_var.get.return_value = "500"
        self.assertEqual(app._read_workflow_start_delay(validate=False), 1)
        app.workflow_start_delay_seconds_var.get.return_value = "2500"
        self.assertEqual(app._read_workflow_start_delay(validate=False), 3)

    def test_guard_wait_guard_request_skips_wait_and_continues(self):
        # 步骤间隙等待中，守卫处理段要求结束/推进/跳转：无脚本上下文可作用，
        # 必须跳过剩余等待继续工作流，而不是把 False 当终止、静默杀掉工作流。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = threading.Event()
        app.player = Mock()
        app.player.stop_event = threading.Event()
        app.player.handle_guard_hit = Mock(side_effect=AdvanceToNextWorkflowStep())
        app._evaluate_global_guards = Mock(return_value={"kind": "success"})
        app._ui = lambda callback, *args: callback(*args)
        app._log = Mock()

        self.assertTrue(app._guard_wait(5.0))
        self.assertTrue(any(
            "继续工作流" in call.args[0] for call in app._log.call_args_list
        ))

    def test_save_workflow_rename_never_overwrites_existing_file(self):
        # 改名保存曾直接落默认目录：与已有同名工作流文件冲突时静默覆盖
        # （数据丢失），旧文件也遗留成孤儿。修复：改名走“绝不覆盖”去重路径。
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder, \
             patch_app("WORKFLOWS_DIR", Path(folder)):
            existing = Path(folder) / "B.json"
            existing.write_text("existing", encoding="utf-8")
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow = Workflow(name="B", steps=[])
            app.workflow_name_var = Mock()
            app.workflow_name_var.get.return_value = "B"
            app.workflow_start_var = Mock()
            app.workflow_start_var.get.return_value = ""
            app.workflow_path = None
            app._read_workflow_start_delay = Mock(return_value=0)
            app._persist_workflow_draft = Mock()
            app._set_status = Mock()
            app._log = Mock()
            app._notify = Mock()
            app.save_current_workflow()
            self.assertEqual(existing.read_text(encoding="utf-8"), "existing")
            self.assertTrue((Path(folder) / "B (2).json").is_file())

    def test_save_workflow_rename_removes_old_file(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder, \
             patch_app("WORKFLOWS_DIR", Path(folder)):
            old = Path(folder) / "A.json"
            old.write_text("old", encoding="utf-8")
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow = Workflow(name="B", steps=[])
            app.workflow_name_var = Mock()
            app.workflow_name_var.get.return_value = "B"
            app.workflow_start_var = Mock()
            app.workflow_start_var.get.return_value = ""
            app.workflow_path = old
            app._read_workflow_start_delay = Mock(return_value=0)
            app._persist_workflow_draft = Mock()
            app._set_status = Mock()
            app._log = Mock()
            app._notify = Mock()
            app.save_current_workflow()
            self.assertFalse(old.exists())
            self.assertTrue((Path(folder) / "B.json").is_file())

    def _rename_app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.workflow = Workflow(name="A", steps=[])
        app.workflow_name_var = Mock()
        app.workflow_name_var.get.return_value = "A"
        app.save_current_workflow = Mock()
        app._notify = Mock()
        return app

    def test_rename_workflow_applies_name_and_saves(self):
        app = self._rename_app()
        with patch("macroflow.ui.app.simpledialog.askstring", return_value="B"):
            app.rename_workflow()
        app.workflow_name_var.set.assert_called_once_with("B")
        app.save_current_workflow.assert_called_once()

    def test_rename_workflow_cancel_keeps_name(self):
        app = self._rename_app()
        with patch("macroflow.ui.app.simpledialog.askstring", return_value=None):
            app.rename_workflow()
        app.workflow_name_var.set.assert_not_called()
        app.save_current_workflow.assert_not_called()

    def test_rename_workflow_rejects_empty_name(self):
        app = self._rename_app()
        with patch("macroflow.ui.app.simpledialog.askstring", return_value="   "):
            app.rename_workflow()
        app.workflow_name_var.set.assert_not_called()
        app.save_current_workflow.assert_not_called()
        app._notify.assert_called_once()

    def _duplicate_app(self, workflow: Workflow) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = Mock()
        app.workflow = workflow
        app.workflow_path = None
        app.workflow_name_var = Mock()
        app.workflow_name_var.get.return_value = workflow.name
        app.workflow_start_var = Mock()
        app.workflow_start_var.get.return_value = workflow.start_at
        app.workflow_start_delay_enabled_var = Mock()
        app.workflow_start_delay_enabled_var.get.return_value = workflow.start_delay_enabled
        app.workflow_start_delay_seconds_var = Mock()
        app.workflow_start_delay_seconds_var.get.return_value = "5000"
        app.workflow_start_delay_seconds_var.unit = Mock()
        app._read_workflow_start_delay = Mock(return_value=0)
        app._clear_workflow_delete_history = Mock()
        app._toggle_workflow_start_delay_control = Mock()
        app.rebuild_workflow_tree = Mock()
        app._persist_workflow_draft = Mock()
        app._set_status = Mock()
        app._log = Mock()
        app._notify = Mock()
        return app

    def test_duplicate_workflow_creates_new_file_and_opens_copy(self):
        # 复制后生成独立新文件：新名称、步骤 ID 重新分配、不继承定时开始时间，
        # 原工作流文件不受影响，界面立即切换到副本。
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder, \
             patch_app("WORKFLOWS_DIR", Path(folder)):
            original = Workflow(
                name="原流程", start_at="2026-01-01 12:00:00",
                steps=[{"script": "a.json", "step_id": "s1"},
                       {"kind": "global_module", "step_id": "s2"}],
            )
            original_path = Path(folder) / "原流程.json"
            save_workflow(original, original_path)
            original_snapshot = original_path.read_text(encoding="utf-8")
            app = self._duplicate_app(original)
            app.workflow_path = original_path
            with patch("macroflow.ui.app.simpledialog.askstring", return_value="副本"):
                app.duplicate_workflow()
            target = Path(folder) / "副本.json"
            self.assertTrue(target.is_file())
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["name"], "副本")
            self.assertEqual(saved["start_at"], "")
            self.assertEqual(len(saved["steps"]), 2)
            self.assertNotIn("s1", [step["step_id"] for step in saved["steps"]])
            self.assertNotIn("s2", [step["step_id"] for step in saved["steps"]])
            # 原工作流文件保持原样，界面切换到副本。
            self.assertEqual(original_path.read_text(encoding="utf-8"), original_snapshot)
            self.assertEqual(app.workflow.name, "副本")
            self.assertEqual(app.workflow_path, target)
            self.assertEqual(app.workflow.start_at, "")
            self.assertEqual(len(app.workflow.steps), 2)
            app.rebuild_workflow_tree.assert_called_once()
            app._persist_workflow_draft.assert_called_once()

    def test_duplicate_workflow_avoids_name_collision(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder, \
             patch_app("WORKFLOWS_DIR", Path(folder)):
            (Path(folder) / "副本.json").write_text("existing", encoding="utf-8")
            app = self._duplicate_app(Workflow(name="原流程", steps=[]))
            with patch("macroflow.ui.app.simpledialog.askstring", return_value="副本"):
                app.duplicate_workflow()
            self.assertEqual((Path(folder) / "副本.json").read_text(encoding="utf-8"), "existing")
            self.assertTrue((Path(folder) / "副本 (2).json").is_file())

    def test_duplicate_workflow_cancel_keeps_current(self):
        app = self._duplicate_app(Workflow(name="原流程", steps=[]))
        with patch("macroflow.ui.app.simpledialog.askstring", return_value=None):
            app.duplicate_workflow()
        self.assertIsNone(app.workflow_path)
        self.assertEqual(app.workflow.name, "原流程")
        app.rebuild_workflow_tree.assert_not_called()

    def test_duplicate_workflow_rejects_empty_name(self):
        app = self._duplicate_app(Workflow(name="原流程", steps=[]))
        with patch("macroflow.ui.app.simpledialog.askstring", return_value="  "):
            app.duplicate_workflow()
        app._notify.assert_called_once()
        self.assertIsNone(app.workflow_path)

    def test_workflow_worker_waits_for_configured_start_delay(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.workflow_stop = Mock()
        app.workflow_stop.wait.return_value = True
        app._set_status = Mock()
        app._set_execution_progress = Mock()
        app._append_mini_step = Mock()
        app._log = Mock()
        app._leave_focus_mode = Mock()
        app._finish_execution_visibility = Mock()
        app._ui = lambda callback, *args: callback(*args)

        app._run_workflow_worker([], None, None, False, start_delay_seconds=7)

        app.workflow_stop.wait.assert_called_once_with(7)
        self.assertTrue(any("启动延时 7 秒" in call.args[0] for call in app._log.call_args_list))


class ActivationWindowToggleTests(unittest.TestCase):
    SIGNATURE = {"title": "前置窗口", "class_name": "Front", "process_path": "C:/Game/front.exe"}

    def _app(self) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.activation_window = None
        app.saved_activation_signature = dict(self.SIGNATURE)
        app.activation_draft_signature = dict(self.SIGNATURE)
        app.activation_draft_enabled = False
        app.activation_enabled_var = FakeBooleanVar(False)
        app.activation_label_var = Mock()
        app.script = MacroScript(actions=[])
        app._mark_dirty = Mock()
        app._log = Mock()
        app._persist_sidebar_settings = Mock(return_value=True)
        return app

    def test_workflow_start_reads_selected_script_prewindow(self):
        with tempfile.TemporaryDirectory(dir=BASE_DIR) as folder:
            script_path = Path(folder) / "selected.json"
            save_script(MacroScript(
                actions=[],
                settings={
                    "activation_window_enabled": True,
                    "activation_window": dict(self.SIGNATURE),
                },
            ), script_path)
            app = self._app()

            self.assertEqual(
                app._activation_settings_from_workflow_step({"script": str(script_path)}),
                (True, self.SIGNATURE),
            )

    def test_disabled_prewindow_has_no_explicit_activation(self):
        app = self._app()
        app._restore_saved_activation_window = Mock()
        self.assertIsNone(app._execution_activation_hwnd(123, False, self.SIGNATURE))
        app._restore_saved_activation_window.assert_not_called()

    def test_enabled_prewindow_uses_saved_window(self):
        app = self._app()
        app.activation_window = Mock()
        app.activation_window.hwnd = 456
        app._restore_saved_activation_window = Mock(return_value=True)
        self.assertEqual(
            app._execution_activation_hwnd(123, True, self.SIGNATURE), 456,
        )

    def test_enabled_prewindow_without_signature_falls_back(self):
        app = self._app()
        self.assertIsNone(app._execution_activation_hwnd(123, True, None))

    def test_enabled_prewindow_revalidates_cached_window_for_each_script(self):
        app = self._app()
        app.activation_window = Mock()
        app.activation_window.hwnd = 111
        app._restore_saved_activation_window = Mock(return_value=True)
        app._restore_saved_activation_window.side_effect = lambda _signature: setattr(
            app.activation_window, "hwnd", 456,
        ) or True
        self.assertEqual(
            app._execution_activation_hwnd(123, True, self.SIGNATURE), 456,
        )
        app._restore_saved_activation_window.assert_called_once_with(self.SIGNATURE)

    def test_enabled_prewindow_missing_window_raises(self):
        app = self._app()
        app._restore_saved_activation_window = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "前置窗口"):
            app._execution_activation_hwnd(123, True, self.SIGNATURE)

    def test_workflow_entry_skips_missing_prewindow_and_continues_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "plain.json"
            save_script(MacroScript(name="普通脚本", actions=[{"type": "delay", "ms": 1}]), script_path)
            app = MacroFlowApp.__new__(MacroFlowApp)
            app.worker = None
            app.workflow_test_mode_active = False
            app.workflow_test_mode_var = FakeBooleanVar(False)
            app._workflow_only_steps = Mock(return_value=[{"script": str(script_path), "repeats": 1}])
            app._global_module_steps = Mock(return_value=[])
            app._workflow_snapshot = Mock()
            app._persist_workflow_draft = Mock()
            app.rebuild_workflow_tree = Mock()
            app.workflow_start_var = Mock()
            app.workflow_start_var.get.return_value = ""
            app._bound_hwnd = Mock(return_value=123)
            app._activation_settings_from_script = Mock(return_value=(True, dict(self.SIGNATURE)))
            app._execution_activation_hwnd = Mock(side_effect=RuntimeError("前置窗口当前未打开"))
            app._log = Mock()
            app._notify = Mock()
            app.focus_mode_enabled_var = FakeBooleanVar(False)
            app.activate_target_enabled_var = FakeBooleanVar(True)
            app._clear_global_guards = Mock(side_effect=RuntimeError("startup continued"))

            with self.assertRaisesRegex(RuntimeError, "startup continued"):
                app.run_workflow()

            self.assertTrue(any("已跳过前置窗口" in call.args[0] for call in app._log.call_args_list))
            app._notify.assert_not_called()

    def test_choose_activation_window_writes_script_settings(self):
        app = self._app()
        selected = Mock()
        selected.title = "新前置窗口"
        selected.class_name = "NewFront"
        selected.process_path = "C:/Game/new.exe"
        selected.label = "新前置窗口（NewFront）"
        app.root = Mock()
        with patch_app("WindowPicker") as picker, patch_app("is_window", return_value=True):
            picker.return_value.show.return_value = selected
            app.choose_activation_window()
        self.assertEqual(app.saved_activation_signature["title"], "新前置窗口")
        self.assertTrue(app.activation_enabled_var.get())
        self.assertTrue(app.script.settings["activation_window_enabled"])
        self.assertEqual(app.script.settings["activation_window"]["title"], "新前置窗口")
        app._mark_dirty.assert_called_once()
        app._persist_sidebar_settings.assert_called_once()

    def test_blank_script_restores_last_activation_window(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.app_settings = {
            "activation_window_draft_enabled": True,
            "activation_window_draft": dict(self.SIGNATURE),
        }
        script = app._blank_script_with_activation_draft()
        self.assertTrue(script.settings["activation_window_enabled"])
        self.assertEqual(script.settings["activation_window"], self.SIGNATURE)

    def test_unbind_activation_window_clears_script_settings(self):
        app = self._app()
        app.activation_window = Mock()
        app.unbind_activation_window()
        self.assertIsNone(app.saved_activation_signature)
        self.assertFalse(app.activation_enabled_var.get())
        app.activation_label_var.set.assert_called_once_with("跟随目标窗口")
        self.assertFalse(app.script.settings["activation_window_enabled"])
        self.assertIsNone(app.script.settings["activation_window"])
        app._mark_dirty.assert_called_once()

    def test_toggle_disabled_label_shows_deactivated(self):
        app = self._app()
        app._refresh_activation_label()
        app.activation_label_var.set.assert_called_once_with("前置窗口（已停用）")

    def test_toggle_enabled_label_shows_saved_title(self):
        app = self._app()
        app.activation_enabled_var.set(True)
        app.activation_window = None
        app._refresh_activation_label()
        app.activation_label_var.set.assert_called_once_with("已保存，等待窗口：前置窗口")

    def test_sync_activation_ui_reads_script_settings(self):
        app = self._app()
        app.script.settings["activation_window_enabled"] = True
        app.script.settings["activation_window"] = dict(self.SIGNATURE)
        app._restore_saved_activation_window = Mock(return_value=True)
        app._sync_activation_ui_from_script()
        self.assertTrue(app.activation_enabled_var.get())
        self.assertEqual(app.saved_activation_signature, self.SIGNATURE)
        app._restore_saved_activation_window.assert_called_once_with(self.SIGNATURE)

    def test_sync_activation_ui_clears_when_script_has_none(self):
        app = self._app()
        app.script.settings["activation_window"] = None
        app.script.settings["activation_window_enabled"] = False
        app.script.settings["activation_window_configured"] = True
        app._restore_saved_activation_window = Mock()
        app._sync_activation_ui_from_script()
        self.assertFalse(app.activation_enabled_var.get())
        self.assertIsNone(app.saved_activation_signature)
        app._restore_saved_activation_window.assert_not_called()

    def test_sync_unconfigured_script_inherits_saved_draft_without_erasing_it(self):
        app = self._app()
        app.activation_draft_enabled = True
        app.activation_draft_signature = dict(self.SIGNATURE)
        app.saved_activation_signature = None
        app._restore_saved_activation_window = Mock(return_value=True)

        app._sync_activation_ui_from_script()

        self.assertTrue(app.activation_enabled_var.get())
        self.assertEqual(app.saved_activation_signature, self.SIGNATURE)
        self.assertEqual(app.activation_draft_signature, self.SIGNATURE)
        app._restore_saved_activation_window.assert_called_once_with(self.SIGNATURE)

    def test_saved_script_with_empty_legacy_prewindow_inherits_saved_draft(self):
        app = self._app()
        app.activation_draft_enabled = True
        app.activation_draft_signature = dict(self.SIGNATURE)
        app.script.settings["activation_window_enabled"] = False
        app.script.settings["activation_window"] = None
        app._restore_saved_activation_window = Mock(return_value=True)

        app._sync_activation_ui_from_script()

        self.assertTrue(app.activation_enabled_var.get())
        self.assertEqual(app.saved_activation_signature, self.SIGNATURE)
        app._restore_saved_activation_window.assert_called_once_with(self.SIGNATURE)

    def test_toggle_updates_persistent_activation_draft(self):
        app = self._app()
        app.activation_enabled_var.set(True)

        app._toggle_activation_enabled()

        self.assertTrue(app.activation_draft_enabled)
        self.assertEqual(app.activation_draft_signature, self.SIGNATURE)

    def test_current_script_settings_include_activation_config(self):
        app = self._app()
        app.activation_enabled_var.set(True)
        app.interval_var = Mock()
        app.interval_var.get.return_value = "100"
        settings = app._current_script_settings()
        self.assertTrue(settings["activation_window_enabled"])
        self.assertEqual(settings["activation_window"]["title"], "前置窗口")

    def test_workflow_step_uses_its_own_script_prewindow(self):
        with tempfile.TemporaryDirectory() as folder:
            with_front = Path(folder) / "with_front.json"
            no_front = Path(folder) / "no_front.json"
            save_script(MacroScript(
                name="带前置", actions=[{"type": "delay", "ms": 1}],
                settings={"activation_window_enabled": True, "activation_window": dict(self.SIGNATURE)},
            ), with_front)
            save_script(MacroScript(name="无前置", actions=[{"type": "delay", "ms": 2}]), no_front)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app.current_workflow_step_index = None
            app._ui = lambda callback, *args: callback(*args)
            app.activation_window = Mock()
            app.activation_window.hwnd = 456
            app._restore_saved_activation_window = Mock(return_value=True)
            app._run_workflow_worker(
                [
                    {"script": str(with_front), "repeats": 1},
                    {"script": str(no_front), "repeats": 1},
                ],
                None, None, False,
            )
            calls = app.player.play.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].kwargs["activation_hwnd"], 456)
            self.assertIsNone(calls[1].kwargs["activation_hwnd"])

    def test_workflow_step_skips_missing_prewindow_but_still_runs_script(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "missing_front.json"
            save_script(MacroScript(
                name="前置窗口未打开", actions=[{"type": "delay", "ms": 1}],
                settings={"activation_window_enabled": True, "activation_window": dict(self.SIGNATURE)},
            ), script_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app.current_workflow_step_index = None
            app._ui = lambda callback, *args: callback(*args)
            app.activation_window = None
            app._restore_saved_activation_window = Mock(return_value=False)

            app._run_workflow_worker(
                [{"script": str(script_path), "repeats": 1}],
                None, None, False,
            )

            app.player.play.assert_called_once()
            self.assertIsNone(app.player.play.call_args.kwargs["activation_hwnd"])
            self.assertTrue(any("已跳过前置窗口条件" in call.args[0] for call in app._log.call_args_list))

    def test_workflow_uses_editor_prewindow_as_default_for_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "plain.json"
            save_script(MacroScript(
                name="普通脚本", actions=[{"type": "delay", "ms": 1}],
            ), script_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app.current_workflow_step_index = None
            app._ui = lambda callback, *args: callback(*args)
            app._run_workflow_worker(
                [{"script": str(script_path), "repeats": 1}],
                None, None, False, workflow_activation_hwnd=789,
            )
            self.assertEqual(
                app.player.play.call_args.kwargs["activation_hwnd"], 789,
            )

    def test_workflow_worker_activates_prewindow_before_ocr_wait(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "plain.json"
            save_script(MacroScript(
                name="普通脚本", actions=[{"type": "delay", "ms": 1}],
            ), script_path)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app.current_workflow_step_index = None
            app._ui = lambda callback, *args: callback(*args)
            order = []
            app._activate_execution_window_before_ocr = Mock(
                side_effect=lambda hwnd: order.append(("activate", hwnd)) or True,
            )
            app._workflow_needs_ocr = Mock(return_value=True)
            app._ensure_ocr_ready = Mock(
                side_effect=lambda: order.append("ocr") or True,
            )
            app.player.play.side_effect = lambda *args, **kwargs: order.append(
                ("play", kwargs["activation_prepared"]),
            )

            app._run_workflow_worker(
                [{"script": str(script_path), "repeats": 1}],
                None, None, False, workflow_activation_hwnd=789,
            )

            self.assertEqual(order, [("activate", 789), "ocr", ("play", True)])

    def test_workflow_step_own_prewindow_suppressed_when_sidebar_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            with_front = Path(folder) / "with_front.json"
            save_script(MacroScript(
                name="带前置", actions=[{"type": "delay", "ms": 1}],
                settings={"activation_window_enabled": True, "activation_window": dict(self.SIGNATURE)},
            ), with_front)

            app = MacroFlowApp.__new__(MacroFlowApp)
            app.workflow_stop = threading.Event()
            app.player = Mock()
            app.player.stop_event = threading.Event()
            app._enter_focus_mode = Mock()
            app._leave_focus_mode = Mock()
            app._set_status = Mock()
            app._set_execution_progress = Mock()
            app._append_mini_step = Mock()
            app._log = Mock()
            app._sound = Mock()
            app._handle_worker_error = Mock()
            app._finish_execution_visibility = Mock()
            app.current_workflow_step_index = None
            app._ui = lambda callback, *args: callback(*args)
            app.activation_window = None
            app._restore_saved_activation_window = Mock(return_value=True)

            # 侧栏“启用执行前置窗口”未勾选（activation_allowed=False）：
            # 步骤脚本自己保存的前置窗口一律不激活，也不会报“已跳过”。
            app._run_workflow_worker(
                [{"script": str(with_front), "repeats": 1}],
                None, None, False, activation_allowed=False,
            )

            app.player.play.assert_called_once()
            self.assertIsNone(app.player.play.call_args.kwargs["activation_hwnd"])
            app._restore_saved_activation_window.assert_not_called()
            self.assertFalse(any(
                "已跳过前置窗口条件" in call.args[0] for call in app._log.call_args_list
            ))

    def test_run_workflow_forwards_sidebar_activation_toggle(self):
        with tempfile.TemporaryDirectory() as folder:
            script_path = Path(folder) / "plain.json"
            save_script(MacroScript(
                name="普通脚本", actions=[{"type": "delay", "ms": 1}],
            ), script_path)

            def make_app() -> MacroFlowApp:
                app = MacroFlowApp.__new__(MacroFlowApp)
                app.worker = None
                app.workflow_test_mode_var = FakeBooleanVar(False)
                app._workflow_only_steps = Mock(
                    return_value=[{"script": str(script_path), "repeats": 1}],
                )
                app._global_module_steps = Mock(return_value=[])
                app._workflow_snapshot = Mock()
                app._persist_workflow_draft = Mock()
                app.rebuild_workflow_tree = Mock()
                app.workflow_start_var = Mock()
                app.workflow_start_var.get.return_value = ""
                app._bound_hwnd = Mock(return_value=123)
                app._activation_settings_from_script = Mock(return_value=(False, None))
                app._log = Mock()
                app._notify = Mock()
                app.focus_mode_enabled_var = FakeBooleanVar(False)
                app.activate_target_enabled_var = FakeBooleanVar(True)
                app._clear_global_guards = Mock()
                app._clear_global_detect_rearm_locks = Mock()
                app.workflow_stop = threading.Event()
                app._sound = Mock()
                app._hide_main_for_execution = Mock()
                app._reset_execution_clock_for_new_run = Mock()
                app._set_execution_progress = Mock()
                app._show_execution_mini = Mock()
                app._append_mini_step = Mock()
                app.activation_enabled_var = FakeBooleanVar(False)
                return app

            # 侧栏未勾选：工作流总开关关闭，步骤脚本自带前置窗口也不会执行。
            app = make_app()
            with patch("macroflow.ui.app.threading.Thread") as thread_class:
                app.run_workflow()
            worker_args = thread_class.call_args.kwargs["args"]
            self.assertFalse(worker_args[-1])
            self.assertIsNone(worker_args[9])

            # 侧栏勾选且编辑器脚本自带前置窗口：作为工作流默认前置窗口传下去。
            app = make_app()
            app.activation_enabled_var.set(True)
            app._activation_settings_from_script.return_value = (True, dict(self.SIGNATURE))
            app._restore_saved_activation_window = Mock(return_value=True)
            app.activation_window = Mock()
            app.activation_window.hwnd = 456
            with patch("macroflow.ui.app.threading.Thread") as thread_class:
                app.run_workflow()
            worker_args = thread_class.call_args.kwargs["args"]
            self.assertTrue(worker_args[-1])
            self.assertEqual(worker_args[9], 456)

if __name__ == '__main__':
    unittest.main()
