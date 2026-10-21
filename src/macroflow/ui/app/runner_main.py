"""Standalone Runner build entry; never imports Studio authoring."""
import sys
from pathlib import Path

if __package__ in (None, ""):
    source_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(source_root))

if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    args = [arg for arg in sys.argv if arg != '--runner']
    if len(args) == 4 and args[1] == '--watchdog':
        from macroflow.execution.recovery import run_watchdog
        run_watchdog(Path(args[2]), int(args[3]))
    else:
        from macroflow.ui.app.runner import MacroFlowRunner
        from macroflow.ui.app.startup import run_application
        run_application(MacroFlowRunner)
