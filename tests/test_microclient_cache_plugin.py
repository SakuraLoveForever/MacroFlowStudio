"""本地插件「微端缓存清理」的清理规则检查；本机没装该插件时跳过。"""
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN_PATH = Path(__file__).resolve().parents[1] / "plugins" / "微端缓存清理.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("microclient_cache_cleanup_plugin", PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(PLUGIN_PATH.is_file(), "本机未安装「微端缓存清理」插件")
class MicroClientCachePluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin = load_plugin()

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.profile = self.root / "Microclient_64"

    def touch(self, relative):
        path = self.profile / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return path

    def test_cache_targets_keep_login_state(self):
        self.touch("data_0")
        self.touch("data_3")
        self.touch("f_000001")
        self.touch("index")
        self.touch("GPUCache/data_1")
        cookies, journal = self.touch("Cookies"), self.touch("Cookies-journal")
        storage = self.touch("Local Storage/leveldb/LOG")
        names = {path.name for path in self.plugin.cache_targets(self.profile)}
        self.assertEqual(names, {"data_0", "data_3", "f_000001", "index", "GPUCache"})
        for kept in (cookies, journal, storage):
            self.assertTrue(kept.is_file())

    def test_clear_cache_removes_page_cache_and_keeps_cookies(self):
        self.touch("data_0")
        self.touch("f_000002")
        self.touch("index")
        self.touch("GPUCache/data_1")
        cookies = self.touch("Cookies")
        storage = self.touch("Local Storage/leveldb/LOG")
        removed = self.plugin.clear_cache(self.profile)
        self.assertEqual(sorted(removed), sorted(["GPUCache", "data_0", "f_000002", "index"]))
        self.assertTrue(cookies.is_file())
        self.assertTrue(storage.is_file())
        self.assertEqual(self.plugin.cache_targets(self.profile), [])
        self.assertEqual(self.plugin.clear_cache(self.profile), [])

    def test_missing_profile_is_ignored(self):
        missing = self.root / "missing"
        self.assertEqual(self.plugin.cache_targets(missing), [])
        self.assertEqual(self.plugin.clear_cache(missing), [])

    def test_profile_directory_follows_local_appdata(self):
        expected = self.root / "Wooduan" / "Microclient_64"
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(self.root)}):
            self.assertEqual(self.plugin.profile_directory(), expected)


if __name__ == "__main__":
    unittest.main()