"""Select the client before loading its persisted settings or widgets."""
from pathlib import Path
import sys


def is_runner(argv=None, executable=None):
    argv = sys.argv if argv is None else argv
    executable = sys.executable if executable is None else executable
    return "--runner" in argv or Path(executable).stem.casefold() == "macroflowrunner"
