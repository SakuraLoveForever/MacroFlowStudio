from __future__ import annotations

from macroflow.core.storage import (
    BASE_DIR, IMAGES_DIR, SCRIPTS_DIR, WORKFLOWS_DIR, archive_overwritten_script,
    DEFAULT_MODULE_NOT_FOUND_TIMEOUT_MS,
    available_script_path, backup_script,
    display_path, ensure_dirs, migrate_workflow_templates, safe_name,
    DIRECTION_SCRIPTS_DIR,
    load_app_settings, load_script, load_workflow,
    script_category_for_path,
    registered_module_object, registered_template_region, remap_hotkey_script_bindings,
    resolve_path, save_app_settings,
    save_script, save_workflow,
    update_module_object,
)
from pathlib import Path
import os
from macroflow.ui.dialogs.actions import ClickDialog, CloseAppDialog, DurationDialog, GameSetupNoteDialog, JsonActionDialog, JumpActionDialog, KeyActionDialog, MouseMoveDialog, OpenAppDialog, RepeatClickDialog, ScheduleDialog, ScrollDialog, SetResolutionActionDialog, TurnActionDialog, edit_action
from macroflow.ui.dialogs.app_dialogs import HotkeyScriptsDialog, ResolutionStylesDialog, ScriptDirectoriesDialog, WindowPicker, WorkflowBatchSettingsDialog, WorkflowRepeatDialog
from macroflow.ui.dialogs.base import DurationVar, TIME_UNITS, Tooltip, key_to_vk, show_floating_notice, vk_to_key_name
from macroflow.ui.dialogs.helpers import recorded_action_description, workflow_step_label
from macroflow.ui.dialogs.module_objects import ModulePickerDialog, TemplateRegionFormDialog, TemplateRegionManagerDialog
from macroflow.ui.dialogs.recognition import GlobalDetectDialog, MultiConditionClickDialog, OcrCompareActionDialog, RowListConditionClickDialog, RowListDiagnosticResultDialog, RowRecognitionResultDialog
import subprocess
import sys
import tkinter as tk
import traceback

from .constants import (
    WINDOWS_RUN_KEY,
    WINDOWS_RUN_VALUE,
)

def windows_startup_command() -> str:
    """Build the quoted command stored in the current-user Windows Run key."""
    parts = [str(Path(sys.executable).resolve())]
    if not getattr(sys, "frozen", False):
        # 源码模式指向包入口（__main__.py 自己会把 src/ 加进导入路径）。
        parts.append(str(Path(__file__).resolve().parent / "__main__.py"))
    return subprocess.list2cmdline(parts)
def set_windows_startup(enabled: bool) -> None:
    """Enable or disable per-user startup without requiring administrator rights."""
    import winreg

    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            winreg.SetValueEx(key, WINDOWS_RUN_VALUE, 0, winreg.REG_SZ, windows_startup_command())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, WINDOWS_RUN_VALUE)
    except FileNotFoundError:
        pass
def spawn_new_instance(args: list[str]):
    """Launch another MacroFlow instance.

    Reset every PyInstaller onefile extraction hint.  The Windows bootloader
    uses ``_MEIPASS2`` (not only ``_MEIPASS``) for inherited child processes;
    reusing that directory can leave Tcl/Tk looking for a deleted ``tcl_data``
    folder.  The reset flag makes the child extract a fresh private directory.
    """
    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in {"_MEIPASS", "_MEIPASS2"}
    }
    if getattr(sys, "frozen", False):
        clean_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(args, cwd=str(BASE_DIR), env=clean_env)
def main():
    from .main import MacroFlowApp

    try:
        MacroFlowApp().run()
    except Exception:
        error = traceback.format_exc()
        try:
            Path(BASE_DIR / "crash.log").write_text(error, encoding="utf-8")
            root = tk.Tk()
            root.withdraw()
            show_floating_notice(
                root,
                "MacroFlow 启动失败",
                f"错误信息已保存到 crash.log\n{error[-900:]}",
                6000,
            )
            root.after(6200, root.destroy)
            root.mainloop()
        except Exception:
            pass
        raise
