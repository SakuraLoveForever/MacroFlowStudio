"""Run the unittest suite without creating windows, screenshots, or input events."""
import sys
import tkinter as tk
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT))


def main():
    import mss
    import ttkbootstrap  # Install Tk constructor wrappers before guarding creation.
    from macroflow.ui import detect_overlay
    from macroflow.input import wininput

    original_ensure = detect_overlay._ensure_window
    native_user32 = detect_overlay._user32

    def ensure_mock_window(*args, **kwargs):
        if detect_overlay._user32 is native_user32:
            raise unittest.SkipTest('requires a real overlay window')
        return original_ensure(*args, **kwargs)

    def unavailable(*args, **kwargs):
        raise unittest.SkipTest('requires a real window, screenshot, or input event')

    with ExitStack() as stack:
        stack.enter_context(patch.object(tk.Tk, '__init__', side_effect=unavailable))
        stack.enter_context(patch.object(mss, 'mss', side_effect=unavailable))
        stack.enter_context(patch.object(mss, 'MSS', side_effect=unavailable))
        stack.enter_context(patch.object(wininput.user32, 'SendInput', side_effect=unavailable))
        stack.enter_context(patch.object(detect_overlay, '_ensure_window', side_effect=ensure_mock_window))
        suite = (unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if sys.argv[1:]
                 else unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT)))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
