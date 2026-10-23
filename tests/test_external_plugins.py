"""外部插件导入 / 发现 / 移除的检查（不启动界面）。"""
import shutil
import tempfile
import unittest
from pathlib import Path

from tests.helpers.core import add_src_to_path

add_src_to_path()

from macroflow.core.external_plugins import ExternalPlugin, discover_plugins, import_plugin, remove_plugin


VALID_PLUGIN = '''"""测试用插件。"""
PLUGIN = {"name": "测试插件", "description": "只是测试"}


def main(ctx):
    ctx.log("hi")
'''

NO_ENTRY = '"""没有入口的插件。"""\nVALUE = 1\n'
BROKEN = 'def main(ctx):\n    return 1\nbroken(\n'


class ExternalPluginTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.plugins = self.root / "plugins"
        self.sources = self.root / "download"

    def write(self, name, text, folder=None):
        path = (folder or self.sources) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_discover_reads_metadata_and_skips_invalid_files(self):
        self.write("valid.py", VALID_PLUGIN, self.plugins)
        self.write("no_entry.py", NO_ENTRY, self.plugins)
        self.write("broken.py", BROKEN, self.plugins)
        plugins = discover_plugins(self.plugins)
        self.assertEqual([(plugin.name, plugin.description) for plugin in plugins], [("测试插件", "只是测试")])
        self.assertEqual(plugins[0].path, self.plugins / "valid.py")

    def test_import_copies_plugin_and_avoids_overwriting(self):
        source = self.write("tool.py", VALID_PLUGIN)
        first = import_plugin(source, self.plugins)
        second = import_plugin(source, self.plugins)
        self.assertEqual(first.path.name, "tool.py")
        self.assertEqual(second.path.name, "tool (2).py")
        self.assertEqual(first.name, "测试插件")
        self.assertTrue((self.plugins / "tool (2).py").is_file())
        self.assertEqual(len(discover_plugins(self.plugins)), 2)

    def test_import_keeps_file_that_already_lives_in_plugin_directory(self):
        source = self.write("local.py", VALID_PLUGIN, self.plugins)
        plugin = import_plugin(source, self.plugins)
        self.assertEqual(plugin.path, source.resolve())
        self.assertEqual(sorted(path.name for path in self.plugins.glob("*.py")), ["local.py"])

    def test_import_rejects_missing_file_non_python_and_missing_entry(self):
        sources = (self.root / "missing.py", self.write("tool.dll", "x"), self.write("no_entry.py", NO_ENTRY))
        for source in sources:
            with self.assertRaises(ValueError):
                import_plugin(source, self.plugins)

    def test_remove_plugin_only_deletes_inside_plugin_directory(self):
        plugin = import_plugin(self.write("tool.py", VALID_PLUGIN), self.plugins)
        remove_plugin(plugin, self.plugins)
        self.assertFalse(plugin.path.exists())
        outside = self.write("outside.py", VALID_PLUGIN)
        with self.assertRaises(ValueError):
            remove_plugin(ExternalPlugin(outside, "外部", ""), self.plugins)
        self.assertTrue(outside.is_file())


if __name__ == "__main__":
    unittest.main()