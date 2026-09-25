import ast
import importlib.util
import sys
import unittest
import warnings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "build" / "ocr_deps_setup.py"
SPEC = importlib.util.spec_from_file_location("ocr_deps_setup_test", SCRIPT)
BUILD_SETUP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILD_SETUP
SPEC.loader.exec_module(BUILD_SETUP)


class OCRBuildRequirementsTests(unittest.TestCase):
    def test_stdlib_manifest_omits_unresolvable_module_aliases(self):
        manifest = set((ROOT / "build" / "ocr_closure_modules.txt").read_text(encoding="utf-8").split())

        self.assertTrue({"collections.abc", "typing.io"}.isdisjoint(manifest))

    def test_ocr_requirements_pin_shared_dependency_metadata(self):
        pins = BUILD_SETUP._read_requirements(BUILD_SETUP.OCR_REQUIREMENTS)
        required = {"numpy", "pillow", "six", "opencv-python-headless"}
        missing = required - pins.keys()

        self.assertFalse(missing, f"OCR requirements omit shared dependency metadata: {sorted(missing)}")

    def test_stdlib_closure_manifest_covers_rapidocr_package_imports(self):
        pins = BUILD_SETUP._read_requirements(BUILD_SETUP.MAIN_REQUIREMENTS)
        pins.update(BUILD_SETUP._read_requirements(BUILD_SETUP.OCR_REQUIREMENTS))
        distributions = BUILD_SETUP._distributions()
        closure = BUILD_SETUP._resolve_closure(
            distributions, pins,
            {"numpy", "cv2", "pil", "six", "ttkbootstrap", "ttkcreator", "pynput", "pystray", "mss"},
        )
        stdlib = set(sys.stdlib_module_names)
        imported = set()
        unresolvable_aliases = {"collections.abc", "typing.io"}

        def add_if_stdlib(name):
            if name and name not in unresolvable_aliases and name.split(".", 1)[0] in stdlib:
                imported.add(name)

        for key, distribution in closure.items():
            if key in {"numpy", "pillow", "six", "opencv-python", "opencv-python-headless"}:
                continue
            for relative in distribution.files:
                source = BUILD_SETUP._safe_member_path(relative)
                if source is None or source.suffix != ".py" or not source.is_file():
                    continue
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    try:
                        tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            add_if_stdlib(alias.name)
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        add_if_stdlib(node.module)
                        for alias in node.names:
                            candidate = f"{node.module}.{alias.name}"
                            try:
                                if candidate.split(".", 1)[0] in stdlib and importlib.util.find_spec(candidate):
                                    add_if_stdlib(candidate)
                            except (ImportError, ModuleNotFoundError, AttributeError, ValueError):
                                pass

        manifest_path = ROOT / "build" / "ocr_closure_modules.txt"
        manifest = set(manifest_path.read_text(encoding="utf-8").split())
        invalid = sorted(name for name in manifest if name.split(".", 1)[0] not in stdlib)
        missing = sorted(imported - manifest)
        self.assertFalse(invalid, f"closure manifest contains non-stdlib names: {invalid[:10]}")
        self.assertFalse(missing, f"closure manifest omits RapidOCR stdlib imports: {missing}")


if __name__ == "__main__":
    unittest.main()
