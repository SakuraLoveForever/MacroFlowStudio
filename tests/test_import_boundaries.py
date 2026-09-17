"""依赖方向：纯模块的传递导入闭包里不许出现 Tk / cv2 / numpy / OCR。

只看直接 import 是不够的——真正决定启动成本与可测性的是**传递闭包**：一个
纯模型模块只要 import 到某个会拉起 cv2 的模块，测试就再也不能在无依赖环境里
跑。所以这里用**全新子进程**检查：先记录 ``sys.modules``，再导入目标模块，
最后看新增的顶层包里有没有重依赖。
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / "src"

# 纯逻辑：模型、存储、编辑业务。导入它们必须完全不碰图形 / 截图 / OCR。
PURE_MODULES = (
    "macroflow.core.models",
    "macroflow.core.resolution",
    "macroflow.core.storage",
    "macroflow.core.alerts",
    "macroflow.input.recorder",
)

# 顶层包名 -> 为什么它不该出现在纯模块的闭包里。
FORBIDDEN = {
    "tkinter": "Tk 图形界面",
    "cv2": "OpenCV 截图/匹配",
    "numpy": "NumPy 数组",
    "mss": "屏幕截图",
    "PIL": "Pillow 图像",
    "paddle": "PaddlePaddle OCR 引擎",
    "paddleocr": "PaddleOCR 引擎",
    "paddlex": "PaddleX 引擎",
    "pystray": "托盘图标",
    "ttkbootstrap": "Tk 主题",
}

_PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[2])
before = set(sys.modules)
import importlib
importlib.import_module(sys.argv[1])
roots = sorted({name.split(".")[0] for name in set(sys.modules) - before})
print(json.dumps({"roots": roots, "added": len(set(sys.modules) - before)}))
'''


def imported_roots(module: str) -> tuple[list[str], int]:
    """在全新子进程里导入 module，返回 (新增顶层包, 新增模块数)。"""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, module, str(SRC_ROOT)],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{module} 在干净子进程里导入失败：{result.stderr.strip()[-600:]}"
        )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    return payload["roots"], payload["added"]


class PureModuleImportClosureTests(unittest.TestCase):
    """纯模块的导入闭包里不允许出现界面 / 截图 / OCR 依赖。"""

    def test_pure_modules_do_not_pull_gui_or_imaging_dependencies(self):
        for module in PURE_MODULES:
            with self.subTest(module=module):
                roots, _added = imported_roots(module)
                hits = sorted(
                    f"{name}（{FORBIDDEN[name]}）"
                    for name in roots if name in FORBIDDEN
                )
                self.assertEqual(
                    hits, [],
                    f"{module} 的传递导入闭包里出现了重依赖：{hits}",
                )

    def test_checker_can_actually_catch_a_violation(self):
        """检查器自己也要有把关能力：故意导入 Tk 的模块必须被抓住。"""
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "leaky.py"
            path.write_text("import tkinter\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-c", _PROBE, "leaky", folder],
                capture_output=True, text=True, cwd=str(PROJECT_ROOT),
            )
            self.assertEqual(result.returncode, 0, result.stderr[-400:])
            roots = json.loads(result.stdout.strip().splitlines()[-1])["roots"]
            self.assertIn("tkinter", roots)


if __name__ == "__main__":
    unittest.main()
