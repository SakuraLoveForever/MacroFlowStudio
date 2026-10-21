"""Shared bundle commands for the authoring and execution clients."""
from pathlib import Path
from tkinter import filedialog

from macroflow.core.bundles import export_bundle, import_bundle
from macroflow.core.models import MacroScript
from macroflow.core.storage import load_workflow


class BundlesMixin:
    def _bundle_is_busy(self):
        if (self.worker and self.worker.is_alive()) or (self.recorder is not None and self.recorder.running):
            self._notify("正在运行", "请先停止执行或录制，再导入或导出。")
            return True
        return False

    def import_portable_bundle(self):
        if self._bundle_is_busy():
            return
        if self.dirty and not self._confirm_discard_unsaved_edits("导入"):
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="导入脚本或工作流包", filetypes=[("MacroFlow 导出包", "*.zip")],
        )
        if not path:
            return
        try:
            kind, entry = import_bundle(Path(path))
            # Unsaved edits were already resolved before installing anything.
            self.dirty = False
            if kind == "script":
                self.load_script_into_editor(entry)
                self.notebook.select(self.script_tab)
            else:
                self._switch_to_workflow(load_workflow(entry), entry, f"导入工作流：{entry.name}")
                self.notebook.select(self.workflow_tab)
            self.refresh_script_files()
            self._set_status("导入完成，可重新绑定本机窗口后执行", "success")
            self._log(f"导入包：{path}；数据：{entry.parent.parent}")
        except Exception as exc:
            self._notify("导入失败", str(exc))

    def export_portable_bundle(self, kind="script"):
        if self._bundle_is_busy():
            return
        obj = self.script if kind == "script" else self.workflow
        path = filedialog.asksaveasfilename(
            parent=self.root, title="导出脚本包" if kind == "script" else "导出工作流包",
            initialfile=f"{obj.name}.zip", defaultextension=".zip",
            filetypes=[("MacroFlow 导出包", "*.zip")],
        )
        if not path:
            return
        try:
            if kind == "script":
                script = MacroScript.from_dict(self.script.to_dict())
                script.name = self.script_name_var.get().strip() or script.name
                script.settings = self._current_script_settings()
                export_bundle(Path(path), script=script, script_path=self.script_path)
            else:
                self._workflow_snapshot()
                export_bundle(Path(path), workflow=self.workflow)
            self._set_status("导出完成，已包含引用脚本、模块和图片", "success")
            self._log(f"导出包：{path}")
        except Exception as exc:
            self._notify("导出失败", str(exc))
