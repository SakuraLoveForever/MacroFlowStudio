"""Offline real-model check using the same OCR directory as a packaged build."""
from __future__ import annotations

import os
import socket
import sys
import time
from importlib.machinery import PathFinder
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
OCR_ROOT = DIST / "rapidocr_ocr"
DEPS = ROOT / ".deps"
SRC = ROOT / "src"


def _reject_network(self, address):
    raise AssertionError(f"OCR attempted network access: {address}")


def main() -> None:
    previous_connect = socket.socket.connect
    previous_dont_write_bytecode = sys.dont_write_bytecode
    had_frozen = hasattr(sys, "frozen")
    previous_frozen = getattr(sys, "frozen", None)
    previous_executable = sys.executable
    previous_path = list(sys.path)
    external_only_finder = None
    socket.socket.connect = _reject_network
    sys.dont_write_bytecode = True
    try:
        sys.path[:0] = [str(DEPS), str(SRC)]
        import cv2
        import numpy as np
        from macroflow.core import ocr

        external_modules_file = OCR_ROOT / "OCR_EXTERNAL_MODULES.txt"
        if not external_modules_file.is_file():
            raise RuntimeError(f"缺少 OCR 外置模块清单：{external_modules_file}")
        external_modules = {
            line.strip().lower()
            for line in external_modules_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

        class ExternalOnlyFinder:
            def find_spec(self, fullname, path=None, target=None):
                root = fullname.partition(".")[0].lower()
                if root not in external_modules:
                    return None
                search_path = [str(OCR_ROOT)] if path is None else path
                spec = PathFinder.find_spec(fullname, search_path, target)
                if spec is None:
                    raise ModuleNotFoundError(
                        f"{fullname} is required from the packaged rapidocr_ocr directory"
                    )
                return spec

        external_only_finder = ExternalOnlyFinder()
        sys.meta_path.insert(0, external_only_finder)
        sys.frozen = True
        sys.executable = str((DIST / "MacroFlowStudio.exe").resolve())

        image = np.full((96, 360, 3), 255, dtype=np.uint8)
        cv2.putText(image, "12345", (10, 72), cv2.FONT_HERSHEY_SIMPLEX,
                    2.0, (0, 0, 0), 3, cv2.LINE_AA)
        started = time.time()
        text, matches = ocr.recognize_image_with_boxes(image)
        print(f"RapidOCR 离线识别完成（{time.time() - started:.1f}s）：{text!r}")
        assert "12345" in text, f"识别结果异常：{text!r}"
        assert matches and matches[0]["score"] > 0, f"缺少有效文字框：{matches!r}"
        print("PASS：打包目录中的 RapidOCR/ONNX Runtime 使用本地模型离线识别")
    finally:
        socket.socket.connect = previous_connect
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if external_only_finder in sys.meta_path:
            sys.meta_path.remove(external_only_finder)
        sys.path[:] = previous_path
        sys.executable = previous_executable
        if had_frozen:
            sys.frozen = previous_frozen
        elif hasattr(sys, "frozen"):
            del sys.frozen


if __name__ == "__main__":
    main()
