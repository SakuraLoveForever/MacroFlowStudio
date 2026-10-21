"""Compact execution view sharing the Studio controller and playback engine."""
from pathlib import Path
import tkinter as tk
import ttkbootstrap as ttk

from macroflow.core import storage
from macroflow.core.models import Workflow
from .controller import ExecutionController


class MacroFlowRunner(ExecutionController):
    def _apply_startup_window_state(self):
        self._display_work_area = dict(self._startup_monitor_area())

    def _sync_ui_scale_to_monitor(self):
        super()._sync_ui_scale_to_monitor()
        self.root.minsize(480, 480)

    def _restore_main_window_geometry(self):
        self.root.minsize(480, 480)
        self.root.geometry(self.app_settings.get("main_window_geometry") or "620x680")

    def _build_ui(self):
        self.root.title("MacroFlow 执行端")
        self.root_frame = ttk.Frame(self.root, padding=12)
        self.root_frame.pack(fill="both", expand=True)
        self.notebook = ttk.Notebook(self.root_frame)
        self.notebook.pack(fill="both", expand=True)
        self.script_tab = ttk.Frame(self.notebook, padding=10)
        self.workflow_tab = self.script_tab
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.script_tab, text="执行")
        self.notebook.add(self.log_tab, text="日志")
        bar = ttk.Frame(self.script_tab)
        bar.pack(fill="x")
        ttk.Button(bar, text="导入包…", command=self.import_portable_bundle).pack(side="left")
        ttk.Button(bar, text="导出选中项目…", command=self._export_runner_selection).pack(side="left", padx=6)
        ttk.Button(bar, text="刷新", command=self.refresh_script_files).pack(side="right")
        self.runner_library = ttk.Treeview(self.script_tab, columns=("kind", "name"), show="headings", height=6)
        self.runner_library.heading("kind", text="类型")
        self.runner_library.heading("name", text="名称")
        self.runner_library.column("kind", width=85, stretch=False)
        self.runner_library.column("name", width=350)
        self.runner_library.pack(fill="both", expand=True, pady=8)
        self.runner_library.bind("<<TreeviewSelect>>", self._select_runner_item)
        self.runner_kind = "script"
        self.runner_items = {}
        self.global_tree = None
        self.action_tree = None
        self.workflow_tree = None
        self.runner_title_var = tk.StringVar(value="先导入并选择脚本或工作流")
        ttk.Label(self.script_tab, textvariable=self.runner_title_var).pack(anchor="w")
        options = ttk.Frame(self.script_tab)
        options.pack(fill="x", pady=6)
        ttk.Label(options, text="脚本重复次数").pack(side="left")
        ttk.Spinbox(options, from_=1, to=999999, textvariable=self.repeat_var, width=7).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="强制专注", variable=self.focus_mode_enabled_var).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="激活目标", variable=self.activate_target_enabled_var).pack(side="left")
        binding = ttk.Frame(self.script_tab)
        binding.pack(fill="x", pady=5)
        ttk.Button(binding, text="绑定窗口…", command=self.choose_window).pack(side="left")
        ttk.Label(binding, textvariable=self.bind_label_var, wraplength=350).pack(side="left", padx=8)
        prewindow = ttk.Frame(self.script_tab)
        prewindow.pack(fill="x", pady=5)
        ttk.Checkbutton(prewindow, text="启用前置窗口", variable=self.activation_enabled_var,
                        command=self._toggle_activation_enabled).pack(side="left")
        ttk.Button(prewindow, text="选择…", command=self.choose_activation_window).pack(side="left", padx=5)
        ttk.Label(prewindow, text="间隔（秒）").pack(side="left")
        interval = ttk.Entry(prewindow, textvariable=self.activation_interval_var, width=6)
        interval.pack(side="left", padx=5)
        interval.bind("<FocusOut>", self._save_activation_interval)
        controls = ttk.Frame(self.script_tab)
        controls.pack(fill="x", pady=8)
        ttk.Button(controls, text="运行", command=self._run_runner_selection, bootstyle="success").pack(side="left")
        self.script_pause_button = ttk.Button(controls, text="暂停 / 继续", command=self.toggle_execution_pause)
        self.script_pause_button.pack(side="left", padx=6)
        ttk.Button(controls, text="停止 F12", command=self.stop_all, bootstyle="danger").pack(side="left")
        self.runner_progress_var = tk.StringVar(value="等待运行")
        ttk.Label(self.script_tab, textvariable=self.runner_progress_var, wraplength=540).pack(anchor="w", pady=4)
        self.status_dot = ttk.Label(self.script_tab, text="●")
        self.status_dot.pack(side="left", anchor="s")
        ttk.Label(self.script_tab, textvariable=self.status_var, wraplength=500).pack(side="left", anchor="s")
        self._build_log_tab()

    def refresh_script_files(self):
        library = getattr(self, "runner_library", None)
        if library is None:
            return
        selected_path = (self.runner_kind, self.workflow_path if self.runner_kind == "workflow" else self.script_path)
        library.delete(*library.get_children())
        self.runner_items = {}
        for folder, kind in ((storage.SCRIPTS_DIR, "script"), (storage.WORKFLOWS_DIR, "workflow"),
                             (storage.BASE_DIR / "imports", None)):
            for path in sorted((*folder.rglob("*.json"), *folder.rglob("*.py"))):
                if kind is None:
                    kind_for_path = "workflow" if path.parent.name == "workflows" else "script" if path.parent.name == "scripts" else None
                    if kind_for_path is None:
                        continue
                else:
                    kind_for_path = kind
                if path.suffix.lower() == ".py" and kind_for_path != "script":
                    continue
                key = str(len(self.runner_items))
                self.runner_items[key] = (kind_for_path, path)
                library.insert("", "end", iid=key, values=("工作流" if kind_for_path == "workflow" else "脚本", path.stem))
                if selected_path == (kind_for_path, path):
                    library.selection_set(key)

    def _select_runner_item(self, _event=None):
        selected = self.runner_library.selection()
        if not selected:
            return
        kind, path = self.runner_items[selected[0]]
        current = (self.runner_kind, self.workflow_path if self.runner_kind == "workflow" else self.script_path)
        if (kind, path) == current:
            return
        if self._bundle_is_busy():
            self.runner_library.selection_remove(*selected)
            for key, item in self.runner_items.items():
                if item == current:
                    self.runner_library.selection_set(key)
                    break
            return
        try:
            if kind == "script":
                self.load_script_into_editor(path)
            else:
                self._switch_to_workflow(storage.load_workflow(path), path, f"选择工作流：{path.name}")
        except Exception as exc:
            self._notify("打开失败", str(exc))

    def load_script_into_editor(self, path):
        self.script = storage.load_script(path)
        self.script_path = Path(path)
        self.script_name_var.set(self.script.name)
        self.dirty = False
        self.runner_kind = "script"
        self._sync_activation_ui_from_script()
        self.rebuild_action_tree()
        return True

    def _switch_to_workflow(self, workflow: Workflow, path: Path, log_text: str):
        self.workflow = workflow
        self.workflow_path = path
        self.workflow_name_var.set(workflow.name)
        self.workflow_start_var.set(workflow.start_at)
        self.workflow_start_delay_enabled_var.set(workflow.start_delay_enabled)
        self.workflow_start_delay_seconds_var.unit.set("ms")
        self.workflow_start_delay_seconds_var.set(str(workflow.start_delay_seconds * 1000))
        self.runner_kind = "workflow"
        self.rebuild_workflow_tree()
        self._persist_workflow_draft()
        self._log(log_text)

    def _run_runner_selection(self):
        if self.runner_kind == "workflow":
            self.run_workflow()
        else:
            self.run_current_script()

    def _export_runner_selection(self):
        self.export_portable_bundle(self.runner_kind)

    def rebuild_action_tree(self):
        if hasattr(self, "runner_title_var") and self.runner_kind == "script":
            self.runner_title_var.set(f"脚本：{self.script.name} · {len(self.script.actions)} 个动作")

    def rebuild_workflow_tree(self):
        if hasattr(self, "runner_title_var") and self.runner_kind == "workflow":
            self.runner_title_var.set(f"工作流：{self.workflow.name} · {len(self._workflow_only_steps())} 行")

    def _refresh_one_workflow_row(self, _index):
        self.rebuild_workflow_tree()

    def _set_execution_progress(self, text):
        super()._set_execution_progress(text)
        self.runner_progress_var.set(self.execution_progress_text)

    def _restore_last_editor_state(self):
        raw = self.app_settings.get("last_script_path", "")
        if raw and storage.resolve_path(raw).is_file():
            self.load_script_into_editor(storage.resolve_path(raw))

    def toggle_record(self, from_ui=False):
        # F8 is pause/resume in this client; recording is never available.
        self.toggle_execution_pause()

    def _rebuild_ui_for_display_change(self):
        # The runner uses responsive layout; there are no fixed editor tables.
        self._reposition_operation_mini()
