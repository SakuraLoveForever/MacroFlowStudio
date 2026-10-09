"""Exercise the real BAT parser against a harmless module in an isolated folder."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows batch launcher')
class RunBatTests(unittest.TestCase):
    def test_launcher_runs_cleanly_under_windows_codepages(self):
        launcher = (Path(__file__).resolve().parents[1] / 'run.bat').read_bytes()
        with tempfile.TemporaryDirectory(prefix='macro launcher ') as folder:
            root = Path(folder)
            package = root / 'src' / 'macroflow' / 'ui' / 'app'
            package.mkdir(parents=True)
            for directory in (package, package.parent, package.parent.parent):
                (directory / '__init__.py').write_text('', encoding='utf-8')
            (package / '__main__.py').write_text('print("MACROFLOW_STUB_OK")\n', encoding='utf-8')
            (root / 'run.bat').write_bytes(launcher)
            for codepage in (936, 65001):
                with self.subTest(codepage=codepage):
                    harness = root / 'test.bat'
                    harness.write_bytes(f'@echo off\r\nchcp {codepage} >nul\r\ncall "%~dp0run.bat"\r\n'.encode('ascii'))
                    result = subprocess.run(['cmd', '/d', '/c', str(harness)], cwd=Path(folder).parent,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stderr, b'')
                    self.assertEqual(result.stdout.splitlines(), [b'MACROFLOW_STUB_OK'])
