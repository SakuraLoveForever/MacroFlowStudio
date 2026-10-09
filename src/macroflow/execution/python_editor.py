"""Open source code in an editor, never through a .py execution association."""
from pathlib import Path
import shutil
import subprocess


def open_python_editor(path):
    source = str(Path(path).resolve())
    code = shutil.which("code")
    if code and Path(code).suffix.lower() == ".cmd":
        executable = Path(code).parent.parent / "Code.exe"
        code = str(executable) if executable.is_file() else None
    command = [code, "--reuse-window", source] if code else ["notepad.exe", source]
    subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)
