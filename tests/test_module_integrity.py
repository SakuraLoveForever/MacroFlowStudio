"""静态完整性检查：模块里引用的全局名必须在模块级存在。

拆分巨文件成包时最容易出的错是「某个名字忘了 import」——运行时才 NameError，
而单元测试未必覆盖到那条路径（本次就是打包版 main() 里的 MacroFlowApp）。
"""
from __future__ import annotations

import builtins
import symtable
import unittest
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
IGNORED = {"__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
           "__builtins__", "__debug__", "__annotations__"}


def undefined_globals(path: Path) -> dict[str, str]:
    """返回「被引用但模块级不存在」的全局名 → 首次出现的 scope。"""
    table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
    defined = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace()
    }
    missing: dict[str, str] = {}

    def walk(scope_table, scope: str):
        for symbol in scope_table.get_symbols():
            name = symbol.get_name()
            if name in IGNORED or name in defined or hasattr(builtins, name):
                continue
            if symbol.is_global() and not symbol.is_assigned():
                missing.setdefault(name, scope or "<module>")
        for child in scope_table.get_children():
            walk(child, (scope + "." if scope else "") + child.get_name())

    walk(table, "")
    return missing


class ModuleIntegrityTests(unittest.TestCase):
    def test_every_module_resolves_its_global_names(self):
        problems = {}
        for path in sorted(SRC_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            missing = undefined_globals(path)
            if missing:
                problems[str(path.relative_to(SRC_ROOT))] = missing
        self.assertEqual(problems, {}, f"存在未定义全局名：{problems}")

    def test_checker_detects_a_missing_import(self):
        # 检查器自身也要有把关能力，否则这条测试永远是绿的。
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "broken.py"
            path.write_text("def run():\n    return MissingName()\n", encoding="utf-8")
            self.assertEqual(undefined_globals(path), {"MissingName": "run"})


if __name__ == "__main__":
    unittest.main()
