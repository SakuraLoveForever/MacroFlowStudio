from __future__ import annotations

from macroflow.core.storage import BASE_DIR
from pathlib import Path
import os
from macroflow.ui.dialogs.base import show_floating_notice
import subprocess
import sys
import tkinter as tk
import traceback


def instance_command() -> list[str]:
    """Command for this installed executable or source entrypoint."""
    parts = [str(Path(sys.executable).resolve())]
    if not getattr(sys, "frozen", False):
        # 源码模式指向包入口（__main__.py 自己会把 src/ 加进导入路径）。
        parts.append(str(Path(__file__).resolve().parent / "__main__.py"))
        from macroflow.core.client_mode import is_runner
        if is_runner():
            parts.append("--runner")
    return parts

def spawn_new_instance(args: list[str], *, background: bool = False):
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
    return subprocess.Popen(args, cwd=str(BASE_DIR), env=clean_env,
                            creationflags=subprocess.CREATE_NO_WINDOW if background else 0)
def run_application(app_class):
    try:
        app = app_class()
        args = [arg for arg in sys.argv if arg != "--runner"]
        if len(args) == 3 and args[1] == "--recover-workflow":
            app.root.after(0, app._restore_recovery_workflow, Path(args[2]))
        app.run()
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
