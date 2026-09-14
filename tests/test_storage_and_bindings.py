"""存储、窗口绑定、分辨率动作与 DPI 缩放。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_storage_and_bindings.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.common import *  # noqa: E402,F401,F403


class StorageTests(unittest.TestCase):
    def test_relative_display_path_is_based_on_app_dir_not_process_cwd(self):
        with tempfile.TemporaryDirectory() as app_folder, \
             tempfile.TemporaryDirectory() as launch_folder:
            base = Path(app_folder)
            registry = base / "template_regions.json"
            key = str(Path("images") / "部分" / "专注.png")
            registry.write_text(json.dumps({
                key: {
                    "category": "switch", "name": "专注",
                    "region": [1, 2, 30, 40],
                },
            }, ensure_ascii=False), encoding="utf-8")
            previous_cwd = Path.cwd()
            try:
                os.chdir(launch_folder)
                with patch("macroflow.core.storage.BASE_DIR", base), \
                     patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", registry):
                    self.assertEqual(display_path(key), key)
                    obj = registered_module_object(key)
            finally:
                os.chdir(previous_cwd)

            self.assertIsNotNone(obj)
            self.assertEqual(obj["name"], "专注")

    def test_module_image_directory_round_trip_and_inventory_status(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            images = base / "images" / "部分"
            images.mkdir(parents=True)
            adopted = images / "已采用.png"
            unused = images / "未采用.jpg"
            ignored = images / "说明.txt"
            adopted.write_bytes(b"png")
            unused.write_bytes(b"jpg")
            ignored.write_text("x", encoding="utf-8")
            settings_path = base / "module_settings.json"
            with patch("macroflow.core.storage.BASE_DIR", base), \
                 patch("macroflow.core.storage.MODULE_SETTINGS_PATH", settings_path):
                saved = save_module_images_dir(images)
                self.assertEqual(load_module_images_dir(), saved)
                rows = module_image_inventory(
                    saved,
                    {"images/部分/已采用.png": {"category": "switch"}},
                )
            self.assertEqual([row["status"] for row in rows], ["已采用（1 个）", "未采用"])
            self.assertEqual(rows[0]["module_key"], "images/部分/已采用.png")

    def test_same_image_can_back_independent_switch_and_global_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "template_regions.json"
            objects = {
                "module:switch-id": {
                    "category": "switch", "name": "切换入口",
                    "template": "images/shared.png", "region": [1, 2, 30, 40],
                    "after_action": "click_match",
                },
                "module:global-id": {
                    "category": "global", "name": "全局保护",
                    "template": "images/shared.png", "region": [5, 6, 70, 80],
                    "after_action": "continue", "hold_ms": 2500,
                },
            }
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", registry):
                save_module_objects(objects)
                loaded = load_module_objects()

            self.assertEqual(loaded["module:switch-id"]["template"], "images/shared.png")
            self.assertEqual(loaded["module:global-id"]["template"], "images/shared.png")
            self.assertEqual(loaded["module:switch-id"]["region"], [1, 2, 30, 40])
            self.assertEqual(loaded["module:global-id"]["region"], [5, 6, 70, 80])
            self.assertEqual(loaded["module:switch-id"]["after_action"], "click_match")
            self.assertEqual(loaded["module:global-id"]["after_action"], "continue")

    def test_global_module_can_disable_hold_delay_without_losing_value(self):
        with tempfile.TemporaryDirectory() as folder:
            registry = Path(folder) / "template_regions.json"
            objects = {
                "module:instant": {
                    "category": "workflow_global", "name": "立即执行",
                    "template": "images/shared.png", "hold_enabled": False,
                    "hold_ms": 2500,
                },
            }
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", registry):
                save_module_objects(objects)
                loaded = load_module_objects()["module:instant"]
        self.assertFalse(loaded["hold_enabled"])
        self.assertEqual(loaded["hold_ms"], 2500)

    def test_module_objects_backfill_missing_name_from_template(self):
        # 旧对象可能没有 name（过去以图片路径为键靠文件名兜底）；复制成
        # module:<uuid> 键后兜底会退化成 uuid，加载时按模板文件名补名。
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                save_module_objects({
                    "module:legacy": {
                        "category": "workflow_global",
                        "template": "images/legacy.png", "region": [1, 2, 30, 40],
                    },
                })
                loaded = load_module_objects()["module:legacy"]
        self.assertEqual(loaded["name"], "legacy")
        self.assertEqual(loaded["template"], "images/legacy.png")

    def test_old_list_module_entries_get_name_from_key(self):
        # 旧格式 [x,y,w,h] 列表条目没有名字字段：用图片路径文件名兜底。
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "template_regions.json"
            path.write_text(json.dumps({"images/old.png": [1, 2, 30, 40]}),
                            encoding="utf-8")
            with patch("macroflow.core.storage.TEMPLATE_REGIONS_PATH", path):
                loaded = load_module_objects()["images/old.png"]
        self.assertEqual(loaded["name"], "old")

    def test_action_ids_survive_reorder_and_legacy_jump_is_migrated(self):
        actions = [
            {"type": "comment", "text": "A"},
            {"type": "image_match", "on_timeout": "jump", "timeout_jump_row": 1},
        ]
        self.assertTrue(ensure_action_ids(actions))
        target_id = actions[0][ACTION_ID_KEY]
        self.assertEqual(actions[1]["timeout_jump_action_id"], target_id)

        actions.insert(0, {"type": "comment", "text": "新插入"})
        ensure_action_ids(actions)

        self.assertEqual(actions[2]["timeout_jump_action_id"], target_id)
        self.assertEqual(actions[1]["text"], "A")

    def test_cloned_actions_get_new_ids_and_internal_jump_is_remapped(self):
        actions = [
            {"type": "comment", "text": "目标", ACTION_ID_KEY: "target"},
            {
                "type": "image_match", ACTION_ID_KEY: "jump",
                "on_timeout": "jump", "timeout_jump_action_id": "target",
            },
        ]

        clones = clone_actions_with_new_ids(actions)

        self.assertNotEqual(clones[0][ACTION_ID_KEY], "target")
        self.assertNotEqual(clones[1][ACTION_ID_KEY], "jump")
        self.assertEqual(clones[1]["timeout_jump_action_id"], clones[0][ACTION_ID_KEY])

    def test_cloned_unconditional_jump_is_remapped(self):
        actions = [
            {"type": "comment", "text": "目标", ACTION_ID_KEY: "target"},
            {"type": "jump", ACTION_ID_KEY: "jump", "jump_action_id": "target"},
        ]
        clones = clone_actions_with_new_ids(actions)
        self.assertEqual(clones[1]["jump_action_id"], clones[0][ACTION_ID_KEY])

    def test_default_move_interval_is_20_ms(self):
        self.assertEqual(DEFAULT_MOUSE_MOVE_INTERVAL_MS, 20)
        self.assertEqual(
            MacroScript().settings["move_interval_ms"], DEFAULT_MOUSE_MOVE_INTERVAL_MS,
        )
        self.assertEqual(MacroScript().settings["recorded_screen"], DEFAULT_RECORDED_SCREEN)

    def test_script_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.json"
            value = MacroScript(name="测试", actions=[{"type": "delay", "ms": 50}])
            save_script(value, path)
            loaded = load_script(path)
            self.assertEqual(loaded.name, "测试")
            self.assertEqual(loaded.actions[0]["ms"], 50)

    def test_available_script_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder, patch("macroflow.core.storage.SCRIPTS_DIR", Path(folder)):
            (Path(folder) / "已有脚本.json").write_text("old", encoding="utf-8")
            self.assertEqual(available_script_path("已有脚本").name, "已有脚本 (2).json")

    def test_available_script_path_uses_custom_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "目标.json").write_text("x", encoding="utf-8")
            path = available_script_path("目标", base)
            self.assertEqual(path, base / "目标 (2).json")

    def test_renaming_direction_script_remaps_only_matching_hotkey_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old_path = base / "scripts" / "方向" / "原方向.json"
            new_path = base / "scripts" / "方向" / "新方向.json"
            old_path.parent.mkdir(parents=True)
            bindings = [
                {"key": "J", "script": "scripts/方向/原方向.json"},
                {"key": "K", "script": str(old_path)},
                {"key": "L", "script": "scripts/方向/其他方向.json"},
                {"key": "M", "script": "scripts/关卡/原方向.json"},
            ]

            with patch("macroflow.core.storage.BASE_DIR", base):
                updated = remap_hotkey_script_bindings(bindings, old_path, new_path)
                expected_path = display_path(new_path)

            self.assertEqual(updated, 2)
            self.assertEqual(bindings[0]["script"], expected_path)
            self.assertEqual(bindings[1]["script"], expected_path)
            self.assertEqual(bindings[2]["script"], "scripts/方向/其他方向.json")
            self.assertEqual(bindings[3]["script"], "scripts/关卡/原方向.json")

    def test_script_backup_overwrites_one_stable_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            scripts = base / "scripts"
            backups = base / "backups"
            scripts.mkdir()
            source = scripts / "领取.json"
            source.write_text('{"version": 1}', encoding="utf-8")
            with patch("macroflow.core.storage.SCRIPTS_DIR", scripts), patch("macroflow.core.storage.SCRIPT_BACKUPS_DIR", backups):
                first = backup_script(source)
                source.write_text('{"version": 2}', encoding="utf-8")
                second = backup_script(source)
            self.assertEqual(first, second)
            self.assertEqual(second.read_text(encoding="utf-8"), '{"version": 2}')
            self.assertEqual(list(backups.rglob("*.json")), [second])

    def test_source_startup_command_quotes_python_and_app(self):
        with patch("macroflow.ui.app.sys.frozen", False, create=True):
            command = windows_startup_command()
        self.assertIn(Path(os.sys.executable).name, command)
        # 源码模式下指向包入口 __main__.py（拆分前是单文件 app.py）。
        self.assertIn(str(Path("macroflow") / "ui" / "app" / "__main__.py"), command)

    def test_workflow_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "flow.json"
            value = Workflow(name="流程", steps=[{"script": "x.json", "repeats": 3}])
            save_workflow(value, path)
            loaded = load_workflow(path)
            self.assertEqual(loaded.steps[0]["repeats"], 3)
            self.assertEqual(
                loaded.steps[0]["repeat_interval_ms"],
                DEFAULT_WORKFLOW_REPEAT_INTERVAL_MS,
            )
            self.assertTrue(loaded.steps[0]["enabled"])
            self.assertFalse(loaded.steps[0]["unlimited"])

    def test_migrate_workflow_templates_writes_workflow_files_once(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / "workflow_templates.json"
            old.write_text(json.dumps({"templates": {
                "日常": Workflow(name="日常", steps=[{
                    "script": "scripts/a.json", "repeats": 3, "before_ms": 200,
                }]).to_dict(),
                "活动": Workflow(name="活动", steps=[{
                    "kind": "global_module", "config": {"module_key": "module:event"},
                }, {
                    "script": "scripts/b.json", "unlimited": True,
                    "repeat_interval_ms": 1500,
                }]).to_dict(),
            }}, ensure_ascii=False), encoding="utf-8")
            with patch("macroflow.core.storage.BASE_DIR", base), \
                 patch("macroflow.core.storage.WORKFLOWS_DIR", base / "workflows"), \
                 patch("macroflow.core.storage.SCRIPTS_DIR", base / "scripts"), \
                 patch("macroflow.core.storage.IMAGES_DIR", base / "images"), \
                 patch("macroflow.core.storage.SCRIPT_BACKUPS_DIR", base / "backups" / "scripts"):
                migrated = migrate_workflow_templates()
                # 幂等：第二次调用不再迁移。
                second = migrate_workflow_templates()
            self.assertEqual(migrated, 2)
            self.assertEqual(second, 0)
            daily = json.loads((base / "workflows" / "日常.json").read_text(encoding="utf-8"))
            self.assertEqual(daily["steps"][0]["repeats"], 3)
            event = json.loads((base / "workflows" / "活动.json").read_text(encoding="utf-8"))
            self.assertEqual(event["steps"][0]["config"]["module_key"], "module:event")
            self.assertTrue(event["steps"][1]["unlimited"])
            self.assertTrue((base / "workflow_templates.migrated.json").is_file())
            self.assertFalse(old.is_file())

    def test_migrate_workflow_templates_skips_existing_files_and_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "workflows").mkdir(parents=True)
            (base / "workflows" / "日常.json").write_text(
                json.dumps({"name": "日常", "steps": []}), encoding="utf-8",
            )
            old = base / "workflow_templates.json"
            old.write_text(json.dumps({"templates": {
                "日常": Workflow(name="日常", steps=[]).to_dict(),
                "新模板": Workflow(name="新模板", steps=[]).to_dict(),
            }}, ensure_ascii=False), encoding="utf-8")
            with patch("macroflow.core.storage.BASE_DIR", base), \
                 patch("macroflow.core.storage.WORKFLOWS_DIR", base / "workflows"), \
                 patch("macroflow.core.storage.SCRIPTS_DIR", base / "scripts"), \
                 patch("macroflow.core.storage.IMAGES_DIR", base / "images"), \
                 patch("macroflow.core.storage.SCRIPT_BACKUPS_DIR", base / "backups" / "scripts"):
                migrated = migrate_workflow_templates()
            self.assertEqual(migrated, 1)
            self.assertTrue((base / "workflows" / "新模板.json").is_file())
            # 已有文件未被覆盖。
            self.assertEqual(
                json.loads((base / "workflows" / "日常.json").read_text(encoding="utf-8")),
                {"name": "日常", "steps": []},
            )
            with patch("macroflow.core.storage.BASE_DIR", base), \
                 patch("macroflow.core.storage.WORKFLOWS_DIR", base / "workflows"), \
                 patch("macroflow.core.storage.SCRIPTS_DIR", base / "scripts"), \
                 patch("macroflow.core.storage.IMAGES_DIR", base / "images"), \
                 patch("macroflow.core.storage.SCRIPT_BACKUPS_DIR", base / "backups" / "scripts"):
                self.assertEqual(migrate_workflow_templates(), 0)  # 无旧文件
        with tempfile.TemporaryDirectory() as empty:
            with patch("macroflow.core.storage.BASE_DIR", Path(empty)):
                self.assertEqual(migrate_workflow_templates(), 0)

    def test_sidebar_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "app_settings.json"
            value = {
                "sound_enabled": False,
                "mini_window_enabled": True,
                "close_action": "tray",
                "focus_mode_enabled": False,
                "activate_target_enabled": False,
                "floating_notice_position": "右下",
                "activation_window": {
                    "title": "执行窗口", "class_name": "RunWindow",
                    "process_path": "C:/Game/run.exe",
                },
                "activation_window_enabled": True,
                "activation_window_draft": {
                    "title": "最近前置窗口", "class_name": "DraftWindow",
                    "process_path": "C:/Game/draft.exe",
                },
                "activation_window_draft_enabled": True,
                "record_mode": "relative",
                "move_interval_ms": 125,
                "repeat": 7,
                "bound_window": {"title": "测试窗口", "class_name": "TestWindow"},
                "workflow_draft": Workflow(
                    name="上次流程", steps=[{"script": "scripts/a.json", "repeats": 2}]
                ).to_dict(),
                "backup_interval": "1周",
                "backup_interval_minutes": 30,
            }
            with patch("macroflow.core.storage.SETTINGS_PATH", path):
                save_app_settings(value)
                loaded = load_app_settings()
            self.assertEqual(loaded["move_interval_ms"], 125)
            self.assertEqual(loaded["repeat"], 7)
            self.assertEqual(loaded["bound_window"]["title"], "测试窗口")
            self.assertEqual(loaded["workflow_draft"]["name"], "上次流程")
            self.assertEqual(loaded["workflow_draft"]["steps"][0]["repeats"], 2)
            self.assertFalse(loaded["focus_mode_enabled"])
            self.assertFalse(loaded["activate_target_enabled"])
            self.assertEqual(loaded["floating_notice_position"], "右下")
            self.assertNotIn("activation_window", loaded)
            self.assertNotIn("activation_window_enabled", loaded)
            self.assertTrue(loaded["activation_window_draft_enabled"])
            self.assertEqual(loaded["activation_window_draft"]["title"], "最近前置窗口")
            self.assertNotIn("execution_mode", loaded)
            self.assertEqual(loaded["backup_interval"], "1周")
            self.assertNotIn("backup_interval_minutes", loaded)

    def test_last_script_path_defaults_to_empty(self):
        # 旧版设置文件没有 last_script_path 字段：默认空字符串（不恢复脚本）。
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "app_settings.json"
            path.write_text(json.dumps({"sound_enabled": True}), encoding="utf-8")
            with patch("macroflow.core.storage.SETTINGS_PATH", path):
                loaded = load_app_settings()
        self.assertEqual(loaded["last_script_path"], "")
        self.assertEqual(loaded["main_window_geometry"], "")
        self.assertIsNone(loaded["editor_draft"])

    def test_backup_interval_is_limited_to_three_fixed_choices(self):
        self.assertEqual(BACKUP_INTERVAL_CHOICES, ("1h", "1天", "1周"))
        self.assertEqual(BACKUP_INTERVAL_MS["1h"], 3_600_000)
        self.assertEqual(BACKUP_INTERVAL_MS["1天"], 86_400_000)
        self.assertEqual(BACKUP_INTERVAL_MS["1周"], 604_800_000)

    def test_from_dict_migrates_legacy_global_detect_action_to_trigger(self):
        # 旧脚本：全局检测是一条动作。迁移后进入 settings["trigger"]，动作被移除。
        raw = {
            "name": "旧全局",
            "actions": [
                {"type": "global_detect", "template": "images/g.png",
                 "hold_ms": 1500, "region": [10, 20, 30, 40]},
                {"type": "delay", "delay_ms": 100},
            ],
        }
        script = MacroScript.from_dict(raw)
        trigger = script.settings["trigger"]
        self.assertEqual(trigger["template"], "images/g.png")
        self.assertEqual(trigger["hold_ms"], 1500)
        self.assertEqual(trigger["region"], [10, 20, 30, 40])
        self.assertNotIn("type", trigger)
        self.assertEqual(script.actions, [{"type": "delay", "delay_ms": 100}])
        self.assertTrue(is_global_script(script.to_dict()))

    def test_from_dict_skips_migration_when_trigger_already_present(self):
        raw = {
            "name": "新全局",
            "settings": {"trigger": {"template": "images/g.png"}},
            "actions": [{"type": "delay", "delay_ms": 100}],
        }
        script = MacroScript.from_dict(raw)
        self.assertEqual(script.settings["trigger"]["template"], "images/g.png")
        self.assertEqual(len(script.actions), 1)
        self.assertTrue(is_global_script(script.to_dict()))

    def test_script_trigger_config_prefers_settings_and_falls_back_to_action(self):
        # 新格式：settings["trigger"] 优先。
        new_script = MacroScript(actions=[], settings={"trigger": {"template": "new.png"}})
        self.assertEqual(
            MacroFlowApp._script_trigger_config(new_script)["template"], "new.png",
        )
        # 回退：只有全局检测动作（未迁移的旧 JSON）。
        old_script = MacroScript(actions=[
            {"type": "global_detect", "template": "old.png"},
            {"type": "delay", "delay_ms": 1},
        ])
        self.assertEqual(
            MacroFlowApp._script_trigger_config(old_script)["template"], "old.png",
        )
        # 普通脚本：没有触发配置。
        plain = MacroScript(actions=[{"type": "delay", "delay_ms": 1}])
        self.assertEqual(MacroFlowApp._script_trigger_config(plain), {})

    def test_from_dict_keeps_embedded_module_row_in_normal_script(self):
        # v1.68：普通脚本内嵌全局模块行（global_detect + jump_row）不能迁移为触发条件。
        raw = {
            "name": "普通",
            "actions": [
                {"type": "global_detect", "template": "images/g.png", "jump_row": 3},
                {"type": "delay", "delay_ms": 100},
            ],
        }
        script = MacroScript.from_dict(raw)
        self.assertEqual(script.settings["trigger"], {})
        self.assertEqual(len(script.actions), 2)
        self.assertEqual(script.actions[0]["jump_row"], 3)
        self.assertFalse(is_global_script(script.to_dict()))

    def test_from_dict_never_migrates_module_row_even_when_marked_global(self):
        # 即使脚本被标记为全局，带 jump_row 的模块行也保留在语句体中。
        raw = {
            "name": "标记全局",
            "is_global": True,
            "actions": [
                {"type": "global_detect", "template": "x.png", "jump_row": 2},
            ],
        }
        script = MacroScript.from_dict(raw)
        self.assertEqual(script.settings["trigger"], {})
        self.assertEqual(len(script.actions), 1)
        self.assertTrue(is_global_script(script.to_dict()))

    def test_script_trigger_config_skips_embedded_module_rows(self):
        # v1.68：普通脚本内嵌全局模块行（带 jump_row）不是触发条件，回退扫描跳过。
        script = MacroScript(actions=[
            {"type": "global_detect", "template": "module.png", "jump_row": 2},
        ])
        self.assertEqual(MacroFlowApp._script_trigger_config(script), {})


class BindingTests(unittest.TestCase):
    def test_unbind_window_persists_cleared_binding(self):
        # 解除绑定必须持久化，否则重启后旧绑定被恢复，录制/执行又对准
        # 用户已明确清除的窗口。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.bound_window = Mock()
        app.saved_window_signature = {"title": "游戏"}
        app.bind_label_var = Mock()
        app._persist_sidebar_settings = Mock()
        app._log = Mock()
        app.unbind_window()
        app._persist_sidebar_settings.assert_called_once()
        self.assertIsNone(app.bound_window)
        self.assertIsNone(app.saved_window_signature)

    def test_region_overlay_restores_main_without_activating_or_moving_it(self):
        main = Mock()
        main.winfo_id.return_value = 123
        dialog = Mock()
        with patch_dialogs("show_window_no_activate", return_value=True) as show:
            restored = restore_modal_after_overlay(dialog, main, "zoomed")
        self.assertTrue(restored)
        show.assert_called_once_with(123)
        main.deiconify.assert_called_once()
        main.state.assert_called_once_with("zoomed")
        dialog.deiconify.assert_called_once()
        dialog.grab_set.assert_called_once()

    def test_confirming_image_action_returns_focus_to_main_window(self):
        main = Mock()
        self.assertTrue(activate_main_after_modal(main))
        main.deiconify.assert_called_once()
        main.lift.assert_called_once()
        main.focus_force.assert_called_once()

    def test_drag_selection_region_reads_upper_left_to_lower_right_rectangle(self):
        self.assertEqual(drag_selection_region(120, 80, 620, 380), [120, 80, 500, 300])
        self.assertIsNone(drag_selection_region(620, 380, 120, 80))
        self.assertIsNone(drag_selection_region(120, 80, 121, 81))

    def test_window_picker_excludes_self_and_duplicate_handles(self):
        windows = [
            WindowInfo(10, "MacroFlow", "TkTopLevel"),
            WindowInfo(20, "Game", "GameWindow"),
            WindowInfo(20, "Game duplicate", "GameWindow"),
        ]
        with patch_dialogs("is_current_process_window", side_effect=lambda hwnd: hwnd == 10):
            result = selectable_target_windows(windows)
        self.assertEqual([(item.hwnd, item.title) for item in result], [(20, "Game")])

    def test_live_cursor_reader_updates_at_any_screen_position(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.cursor_tracking = True
        app.cursor_tracking_after_id = None
        app.cursor_position_var = Mock()
        app.cursor_tracking_mini_var = Mock()
        app.root = Mock()
        app.root.after.return_value = "poll-id"
        with patch_app("get_cursor_pos", side_effect=[(30, 40), (960, 540)]), \
             patch_app("get_virtual_screen_rect", return_value=DEFAULT_RECORDED_SCREEN):
            app._poll_cursor_position()
            app._poll_cursor_position()
        self.assertEqual(
            [call.args[0] for call in app.cursor_position_var.set.call_args_list],
            ["(30, 40) · 1920×1080", "(960, 540) · 1920×1080"],
        )
        self.assertEqual(
            [call.args[0] for call in app.cursor_tracking_mini_var.set.call_args_list],
            ["X: 30    Y: 40", "X: 960    Y: 540"],
        )

    def test_every_main_log_line_contains_current_cursor_position(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.log_text = Mock()

        with patch_app("get_cursor_pos", return_value=(958, 415)):
            app._log("全局检测已点击")

        inserted = app.log_text.insert.call_args.args[1]
        self.assertRegex(
            inserted,
            r"^\[\d{2}:\d{2}:\d{2}\] \[鼠标 958,415\] 全局检测已点击\n$",
        )

    def test_every_floating_log_line_contains_current_cursor_position(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_steps_text = Mock()
        app.mini_steps_text.winfo_exists.return_value = True
        app.mini_steps_text.index.return_value = "2.0"

        with patch_app("get_cursor_pos", return_value=(640, 360)):
            app._append_mini_step("工作流继续")

        inserted = app.mini_steps_text.insert.call_args.args[1]
        self.assertRegex(
            inserted,
            r"^\d{2}:\d{2}:\d{2}  \[鼠标 640,360\] 工作流继续\n$",
        )

    def test_saved_binding_rebinds_restarted_window_by_foreground_class(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.saved_window_signature = {"title": "Game - old session", "class_name": "GameWindow"}
        app.record_mode_var = Mock()
        app.bind_label_var = Mock()
        app.bound_window = None
        current = WindowInfo(222, "Game - new session", "GameWindow")
        with patch_app("enum_windows", return_value=[current]), \
             patch_app("get_foreground_window_info", return_value=current):
            self.assertTrue(app._restore_saved_window_binding())
        self.assertEqual(app.bound_window.hwnd, 222)
        app.bind_label_var.set.assert_called_once_with("Game - new session")

    def test_disabling_target_activation_preserves_saved_binding(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        signature = {"title": "Game", "class_name": "GameWindow"}
        target = WindowInfo(222, "Game", "GameWindow")
        app.saved_window_signature = signature
        app.bound_window = target
        app.activate_target_enabled_var = Mock()
        app.activate_target_enabled_var.get.return_value = False
        app._persist_sidebar_settings = Mock(return_value=True)
        app._log = Mock()

        app._toggle_target_activation()

        self.assertIs(app.saved_window_signature, signature)
        self.assertIs(app.bound_window, target)
        app._persist_sidebar_settings.assert_called_once_with()
        self.assertIn("目标窗口绑定仍然保留", app._log.call_args.args[0])

    def test_foreground_target_status_matches_current_hwnd(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.bound_window = WindowInfo(222, "Game", "GameWindow")
        app.saved_window_signature = {
            "title": "Game", "class_name": "GameWindow", "process_path": "C:/Game/game.exe",
            "window_rect": (0, 0, 1920, 1080), "client_size": (1920, 1080),
        }
        self.assertTrue(app._foreground_matches_target(WindowInfo(
            333, "Game", "GameWindow", "C:/Game/game.exe",
            (0, 0, 1920, 1080), (1920, 1080),
        )))
        self.assertTrue(app._foreground_matches_target(WindowInfo(
            333, "Game", "GameWindow", "C:/Game/game.exe",
            (0, 0, 1280, 720), (1280, 720),
        )))
        self.assertFalse(app._foreground_matches_target(WindowInfo(
            333, "Game", "OtherWindow", "C:/Game/game.exe",
        )))

    def test_foreground_target_allows_dynamic_title_with_stable_identity(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.bound_window = WindowInfo(222, "旧关卡", "GameWindow", "C:/Game/game.exe")
        app.saved_window_signature = {
            "title": "旧关卡", "class_name": "GameWindow",
            "process_path": "C:/Game/game.exe",
        }

        self.assertTrue(app._foreground_matches_target(WindowInfo(
            333, "新关卡", "GameWindow", "C:/Game/game.exe",
        )))

    def test_bound_hwnd_prefers_matching_foreground_over_still_valid_old_hwnd(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.bound_window = WindowInfo(111, "旧窗口", "GameWindow", "C:/Game/game.exe")
        app.saved_window_signature = {
            "title": "旧窗口", "class_name": "GameWindow",
            "process_path": "C:/Game/game.exe",
        }
        app.bind_label_var = Mock()
        foreground = WindowInfo(222, "当前游戏", "GameWindow", "C:/Game/game.exe")

        with patch_app("get_foreground_window_info", return_value=foreground), \
             patch_app("is_current_process_window", return_value=False):
            hwnd = app._bound_hwnd()

        self.assertEqual(hwnd, 222)
        self.assertEqual(app.bound_window.hwnd, 222)
        app.bind_label_var.set.assert_called_once_with("当前游戏")

    def test_execution_clock_resets_only_for_new_run(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.execution_started_at = 123.0
        app.mini_elapsed_var = Mock()

        with patch("macroflow.ui.app.time.perf_counter", return_value=456.0):
            app._reset_execution_clock_for_new_run(None)
        self.assertEqual(app.execution_started_at, 456.0)
        app.mini_elapsed_var.set.assert_called_once_with("00:00")

        app.mini_elapsed_var.reset_mock()
        with patch("macroflow.ui.app.time.perf_counter", return_value=999.0):
            app._reset_execution_clock_for_new_run(4)
        self.assertEqual(app.execution_started_at, 456.0)
        app.mini_elapsed_var.set.assert_not_called()

    def test_reused_execution_mini_restarts_stopped_refresh_loop(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.mini_window = Mock()
        app.mini_window.winfo_exists.return_value = True
        app.mini_window.winfo_ismapped.return_value = True
        app.mini_mode = "execution"
        app.mini_update_after_id = None
        app._update_operation_mini = Mock()

        app._show_operation_mini("execution")

        app._update_operation_mini.assert_called_once_with()

    def test_restore_scan_foreground_restores_binding_when_not_foreground(self):
        # 截图后的前台恢复：主绑定窗口不在前台时激活它。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._bound_hwnd = Mock(return_value=100)
        with patch_app("is_window_process_foreground", return_value=False) as is_fore, \
             patch_app("activate_window") as activate:
            app._restore_workflow_scan_foreground()
        is_fore.assert_called_once_with(100)
        activate.assert_called_once_with(100)

    def test_restore_scan_foreground_skips_when_binding_foreground(self):
        # 主绑定窗口已在前台：零开销跳过。
        app = MacroFlowApp.__new__(MacroFlowApp)
        app._bound_hwnd = Mock(return_value=100)
        with patch_app("is_window_process_foreground", return_value=True) as is_fore, \
             patch_app("activate_window") as activate:
            app._restore_workflow_scan_foreground()
        is_fore.assert_called_once_with(100)
        activate.assert_not_called()


class ResolutionActionTests(unittest.TestCase):
    def test_normalize_resolution_styles_keeps_valid_named_presets(self):
        styles = normalize_resolution_styles([
            {"name": "游戏 1080p", "width": "1920", "height": 1080},
            {"name": "游戏 1080p", "width": 1280, "height": 720},
            {"name": "坏配置", "width": 0, "height": 720},
        ])

        self.assertEqual(styles, [
            {
                "name": "游戏 1080p", "width": 1920, "height": 1080,
                "refresh_rate": 0, "scale_percent": 100,
            },
        ])

    def test_resolve_resolution_style_returns_a_copy_from_settings(self):
        settings = {"resolution_styles": DEFAULT_RESOLUTION_STYLES}

        style = resolve_resolution_style(settings, DEFAULT_RESOLUTION_STYLES[0]["name"])

        self.assertEqual(style, DEFAULT_RESOLUTION_STYLES[0])
        self.assertIsNot(style, DEFAULT_RESOLUTION_STYLES[0])
        with self.assertRaises(KeyError):
            resolve_resolution_style(settings, "不存在的样式")

    def test_player_changes_resolution_on_selected_window_monitor_and_refreshes_screen(self):
        before = {"left": 0, "top": 0, "width": 2560, "height": 1440}
        after = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        player = MacroPlayer()
        player._target_screen = before
        player._wait = Mock()
        action = {
            "type": "set_resolution",
            "name": "游戏 1080p",
            "width": 1920,
            "height": 1080,
            "refresh_rate": 0,
            "scale_percent": 125,
            "window": {
                "title": "扩展屏应用",
                "class_name": "ExternalWindow",
                "process_path": "C:/Apps/external.exe",
            },
        }
        selected_window = WindowInfo(456, "扩展屏应用", "ExternalWindow", "C:/Apps/external.exe")

        with patch_player("resolve_window_signature", return_value=selected_window) as resolve, \
             patch_player("get_display_resolution_for_window",
                   return_value=(2560, 1440, 60)), \
             patch_player("get_display_scaling_for_window", return_value=100), \
             patch_player("set_display_resolution_for_window", return_value=True) as set_mode, \
             patch_player("set_display_scaling_for_window", return_value=True) as set_scale, \
             patch_player("get_playback_screen_rect", return_value=after) as get_screen:
            player._execute_action(action, None)

        resolve.assert_called_once_with(action["window"])
        set_mode.assert_called_once_with(456, 1920, 1080, 0)
        set_scale.assert_called_once_with(456, 125)
        player._wait.assert_called_once()
        get_screen.assert_called_once_with(456)
        self.assertEqual(player._target_screen, after)

    def test_player_rejects_resolution_action_without_reference_or_monitor(self):
        player = MacroPlayer()

        with self.assertRaisesRegex(RuntimeError, "无法确定要修改的显示器"):
            player._execute_action({
                "type": "set_resolution", "width": 1920, "height": 1080,
            }, None)

    def test_player_changes_resolution_of_app_monitor_without_reference_window(self):
        # 没有参照窗口时直接改"软件所在显示器"，不需要游戏窗口打开。
        player = MacroPlayer(on_resolution_monitor_request=lambda: 999)
        player._wait = Mock()
        after = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with patch_player("is_window", return_value=True), \
             patch_player("get_display_resolution_for_window",
                   return_value=(1280, 720, 60)), \
             patch_player("get_display_scaling_for_window", return_value=125), \
             patch_player("set_display_resolution_for_window", return_value=True) as set_mode, \
             patch_player("set_display_scaling_for_window", return_value=True) as set_scale, \
             patch_player("get_playback_screen_rect", return_value=after) as get_screen:
            player._execute_action({
                "type": "set_resolution", "name": "1080p",
                "width": 1920, "height": 1080, "refresh_rate": 60, "scale_percent": 100,
            }, None)

        set_mode.assert_called_once_with(999, 1920, 1080, 60)
        set_scale.assert_called_once_with(999, 100)
        get_screen.assert_called_once_with(999)
        self.assertEqual(player._target_screen, after)

    def test_player_falls_back_to_app_monitor_when_reference_window_is_closed(self):
        # 参照窗口没打开时不再中断工作流：改为修改软件所在显示器并记一条提示。
        player = MacroPlayer(on_resolution_monitor_request=lambda: 999)
        player._wait = Mock()
        logs = []
        player.on_log = logs.append
        after = {"left": 0, "top": 0, "width": 1920, "height": 1080}
        with patch_player("resolve_window_signature", return_value=None), \
             patch_player("is_window", return_value=True), \
             patch_player("get_display_resolution_for_window",
                   return_value=(1280, 720, 60)), \
             patch_player("get_display_scaling_for_window", return_value=100), \
             patch_player("set_display_resolution_for_window", return_value=True) as set_mode, \
             patch_player("set_display_scaling_for_window", return_value=True), \
             patch_player("get_playback_screen_rect", return_value=after):
            player._execute_action({
                "type": "set_resolution",
                "width": 1920,
                "height": 1080,
                "window": {"title": "已关闭窗口"},
            }, None)

        set_mode.assert_called_once_with(999, 1920, 1080, 0)
        self.assertTrue(any("参照窗口未找到" in text for text in logs))

    def test_player_skips_resolution_change_when_already_at_target(self):
        # 已经是目标分辨率/缩放：不重复切换，也不因为"缩放改不了"而中断工作流。
        player = MacroPlayer(on_resolution_monitor_request=lambda: 999)
        player._wait = Mock()
        logs = []
        player.on_log = logs.append
        with patch_player("is_window", return_value=True), \
             patch_player("get_display_resolution_for_window",
                   return_value=(1920, 1080, 60)), \
             patch_player("get_display_scaling_for_window", return_value=200), \
             patch_player("set_display_resolution_for_window") as set_mode, \
             patch_player("set_display_scaling_for_window",
                   return_value=False) as set_scale, \
             patch_player("get_playback_screen_rect",
                   return_value={"left": 0, "top": 0, "width": 1920, "height": 1080}):
            player._execute_action({
                "type": "set_resolution",
                "width": 1920, "height": 1080, "refresh_rate": 60, "scale_percent": 100,
            }, None)

        set_mode.assert_not_called()
        set_scale.assert_called_once_with(999, 100)
        self.assertTrue(any("无需切换分辨率" in text for text in logs))
        self.assertTrue(any("不支持程序化修改缩放" in text for text in logs))

    def test_player_rejects_resolution_action_without_any_resolvable_monitor(self):
        player = MacroPlayer()

        with patch_player("resolve_window_signature", return_value=None), \
             self.assertRaisesRegex(RuntimeError, "无法确定要修改的显示器"):
            player._execute_action({
                "type": "set_resolution",
                "width": 1920,
                "height": 1080,
                "window": {"title": "已关闭窗口"},
            }, None)

    def test_display_scaling_uses_relative_value_from_recommended_scale(self):
        adapter_id = wininput_module._LUID()
        adapter_id.LowPart = 1
        adapter_id.HighPart = 2
        with patch(
                "macroflow.input.wininput.get_display_device_name_for_window",
                return_value="\\\\.\\DISPLAY2",
        ), patch(
                "macroflow.input.wininput._display_config_source_for_device",
                return_value=(adapter_id, 7),
        ), patch(
                "macroflow.input.wininput._display_scale_info",
                return_value=(150, 100, 150, 500),
        ), patch.object(
                wininput_module.user32, "DisplayConfigSetDeviceInfo", return_value=0,
        ) as set_info:
            self.assertTrue(set_display_scaling_for_window(456, 175))

        packet = ctypes.cast(
            set_info.call_args.args[0],
            ctypes.POINTER(wininput_module._DISPLAYCONFIG_SOURCE_DPI_SCALE_SET),
        ).contents
        self.assertEqual(packet.header.type, -4)
        self.assertEqual(packet.header.id, 7)
        self.assertEqual(packet.scaleRel, 1)

    def test_build_resolution_action_snapshots_selected_style(self):
        action = build_resolution_action(
            {"resolution_styles": [
            {"name": "扩展屏 1080p", "width": 1920, "height": 1080, "refresh_rate": 60},
            ]},
            "扩展屏 1080p",
        )

        self.assertEqual(action, {
            "type": "set_resolution",
            "name": "扩展屏 1080p",
            "width": 1920,
            "height": 1080,
            "refresh_rate": 60,
            "scale_percent": 100,
            "delay_ms": 0,
            "after_delay_ms": 0,
        })

    def test_action_summary_describes_resolution_style(self):
        kind, detail, delay = action_summary({
            "type": "set_resolution", "name": "扩展屏 1080p",
            "width": 1920, "height": 1080, "refresh_rate": 60,
        })

        self.assertIn("分辨率", kind)
        self.assertIn("扩展屏 1080p", detail)
        self.assertIn("1920×1080", detail)
        self.assertIn("缩放 100%", detail)
        self.assertEqual(delay, "0 ms")

    def test_resolution_action_does_not_require_the_game_target_window(self):
        self.assertFalse(MacroFlowApp._actions_need_bound_window([
            {"type": "set_resolution"},
        ]))

    def test_resolution_action_saves_its_selected_window_signature(self):
        dialog = SetResolutionActionDialog.__new__(SetResolutionActionDialog)
        dialog.settings = {
            "resolution_styles": [
                {"name": "扩展屏 1080p", "width": 1920, "height": 1080, "refresh_rate": 60},
            ],
        }
        dialog.name = Mock()
        dialog.name.get.return_value = "扩展屏 1080p"
        dialog.window_signature = {
            "title": "扩展屏应用",
            "class_name": "ExternalWindow",
            "process_path": "C:/Apps/external.exe",
        }
        dialog.delay = Mock()
        dialog.delay.get.return_value = 0
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = 0
        dialog.destroy = Mock()

        dialog.save()

        self.assertEqual(dialog.result["window"], dialog.window_signature)
        self.assertEqual(dialog.result["type"], "set_resolution")
        dialog.destroy.assert_called_once()

    def test_resolution_dialog_saves_action_without_reference_window(self):
        dialog = SetResolutionActionDialog.__new__(SetResolutionActionDialog)
        dialog.settings = {
            "resolution_styles": [
                {"name": "本机 1080p", "width": 1920, "height": 1080, "refresh_rate": 60},
            ],
        }
        dialog.name = Mock()
        dialog.name.get.return_value = "本机 1080p"
        dialog.window_signature = {}
        dialog.delay = Mock()
        dialog.delay.get.return_value = 0
        dialog.after_delay = Mock()
        dialog.after_delay.get.return_value = 0
        dialog.destroy = Mock()

        dialog.save()

        self.assertNotIn("window", dialog.result)
        self.assertEqual(dialog.result["width"], 1920)
        dialog.destroy.assert_called_once()

    def test_resolution_dialog_uses_content_fit_for_long_window_details(self):
        source = inspect.getsource(dialog_module.SetResolutionActionDialog)

        self.assertIn("fit_window_to_content(", source)

    def test_app_settings_provide_default_resolution_styles(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("macroflow.core.storage.SETTINGS_PATH", Path(folder) / "settings.json"):
                settings = load_app_settings()

        self.assertEqual(settings["resolution_styles"], DEFAULT_RESOLUTION_STYLES)


class DpiScaleTests(unittest.TestCase):
    def test_dpi_scale_helpers_convert_design_pixels(self):
        # 打包版进程是 DPI 感知的：Tk 的字体按真实 DPI 放大，像素常量必须同步
        # 换算，否则高 DPI 下文字会撑破行高/列宽（被裁切或上下行重叠）。
        from macroflow.ui import app as app_module

        class _FakeRoot:
            class tk:
                @staticmethod
                def call(*_args):
                    return 192 / 72  # 200% 缩放

        previous = app_module._UI_SCALE
        try:
            self.assertAlmostEqual(app_module.set_ui_scale(_FakeRoot()), 2.0)
            self.assertEqual(app_module.px(25), 50)
            self.assertEqual(app_module.px(0), 0)
            self.assertEqual(app_module.pad(14, 10, 14, 8), (28, 20, 28, 16))
        finally:
            app_module._UI_SCALE = previous

    def test_dpi_scale_is_identity_at_96_dpi(self):
        from macroflow.ui import app as app_module

        class _FakeRoot:
            class tk:
                @staticmethod
                def call(*_args):
                    return 96 / 72

        previous = app_module._UI_SCALE
        try:
            self.assertAlmostEqual(app_module.set_ui_scale(_FakeRoot()), 1.0)
            self.assertEqual(app_module.px(25), 25)
            self.assertEqual(app_module.pad(8, 3), (8, 3))
        finally:
            app_module._UI_SCALE = previous

if __name__ == '__main__':
    unittest.main()
