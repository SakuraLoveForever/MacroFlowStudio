"""运行日志视图：事件 / 明细两层、滚动条自动显隐。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接运行本文件（python tests/test_log_view.py）：先把项目根挂上，才能导入 tests.common。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.common import *  # noqa: E402,F401,F403


class LogStreamsTests(unittest.TestCase):
    """两个分开的日志：事件日志（去重）+ 执行明细（每行动作一条）。"""

    def _app(self, folder: Path) -> MacroFlowApp:
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.session_log_path = Path(folder) / "MacroFlow_test.log"
        app.trace_log_path = Path(folder) / "MacroFlow_trace_test.log"
        app.log_file_lock = threading.Lock()
        app.trace_file_lock = threading.Lock()
        app._log_dedup_window_ms = 30000
        app._log_dedup_text = ""
        app._log_dedup_count = 0
        app._log_dedup_since = 0.0
        return app

    def test_event_log_merges_identical_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self._app(Path(folder))
            for _ in range(6):
                app._write_log_line("[10:00:00] [鼠标 1,1] 全局检测触发：模块[A]。\n", "全局检测触发：模块[A]。")
            app._write_log_line("[10:00:00] [鼠标 2,2] 脚本结束\n", "脚本结束")
            app._flush_log_dedup()
            text = app.session_log_path.read_text(encoding="utf-8")
        # 合并提示里会带上被合并那一行，所以按“以该消息结尾的行”计数。
        self.assertEqual(sum(1 for line in text.splitlines() if line.endswith("全局检测触发：模块[A]。")), 1)
        self.assertIn("重复 5 次", text)

    def test_event_log_keeps_different_messages(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self._app(Path(folder))
            app._write_log_line("[10:00:00] 第 1 行\n", "第 1 行")
            app._write_log_line("[10:00:01] 第 2 行\n", "第 2 行")
            app._flush_log_dedup()
            text = app.session_log_path.read_text(encoding="utf-8")
        self.assertIn("第 1 行", text)
        self.assertIn("第 2 行", text)
        self.assertNotIn("重复", text)

    def test_trace_log_writes_one_line_per_action(self):
        with tempfile.TemporaryDirectory() as folder:
            app = self._app(Path(folder))
            app._on_player_trace({
                "script": "工业4", "depth": 0, "index": 10, "total": 813,
                "action": {"type": "key", "vk": 69, "name": "E", "down": True},
                "elapsed_ms": 2.0, "waited_ms": 1000.0,
            })
            app._on_player_trace({
                "script": "背包", "depth": 1, "index": 0, "total": 3,
                "action": {"type": "delay", "ms": 500},
                "elapsed_ms": 501.0, "waited_ms": 0.0,
            })
            text = app.trace_log_path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if not line.startswith("#")]
        self.assertEqual(len(lines), 2)
        self.assertIn("第 11/813 行", lines[0])
        self.assertIn("按下 E", lines[0])
        self.assertIn("等待 1000ms", lines[0])
        self.assertIn("代码段[背包] 第 1/3 行", lines[1])
        self.assertIn("耗时 501ms", lines[1])

    def test_trace_context_writes_hierarchy_header_before_action_lines(self):
        # 两级日志：明细先写一行 ▶ 层级标题（工作流第几步 · 哪个脚本 · 第几次），
        # 下面才是逐行动作；事件日志每条也带上同样的位置，便于定位执行到哪了。
        with tempfile.TemporaryDirectory() as folder:
            app = self._app(Path(folder))
            app._set_trace_context(
                step=3, steps=31, script="工业4封装", total=12, repeat=4, repeats=100,
            )
            app._on_player_trace({
                "script": "工业4封装", "depth": 0, "index": 2, "total": 12,
                "action": {"type": "click", "button": "left", "x": 1, "y": 2},
                "elapsed_ms": 1.0, "waited_ms": 0.0,
            })
            app._trace_event("模块 专注 执行结果：成功", module_detail=True)
            text = app.trace_log_path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if not line.startswith("#")]
        header = app._trace_context_header()
        self.assertEqual(
            header.replace("  ", " "),
            "脚本[工业4封装]（12 行） · 工作流第 3/31 步 · 第 4/100 次",
        )
        self.assertIn(header, lines[0])
        self.assertIn("第 3/12 行", lines[1])
        # 模块内部说明缩进，一眼区分“脚本行”和“这一行做了什么”。
        self.assertIn("└ 模块 专注 执行结果：成功", lines[2])
        # 事件日志按上下文标注行号与脚本名。
        self.assertEqual(
            app._with_event_context("步骤开始"), "工作流第 3/31 步 · 脚本[工业4封装]：步骤开始",
        )

    def test_event_context_is_empty_outside_execution(self):
        app = MacroFlowApp.__new__(MacroFlowApp)
        self.assertEqual(app._with_event_context("应用已就绪。"), "应用已就绪。")


class LogTabViewTests(unittest.TestCase):
    """界面「运行日志」标签：事件日志 / 执行明细两个视图，内容各自独立。"""

    def _app(self, root):
        app = MacroFlowApp.__new__(MacroFlowApp)
        app.root = root
        app.logs_dir = BASE_DIR / "logs"
        app.log_tab = tk.Frame(root)
        app.ui_queue = None
        app.session_log_path = None
        app.trace_log_path = None
        app.log_file_lock = threading.Lock()
        app.trace_file_lock = threading.Lock()
        app._log_dedup_window_ms = 30000
        app._log_dedup_text = ""
        app._log_dedup_count = 0
        app._log_dedup_since = 0.0
        return app

    def test_two_log_views_show_their_own_stream(self):
        root = tk.Tk()
        # 不 withdraw：tk.Text 需要真实布局才能断言内容；用全透明避免闪窗。
        root.attributes("-alpha", 0.0)
        self.addCleanup(root.destroy)
        app = self._app(root)
        app._build_log_tab()

        app._log("脚本开始执行：工业4")
        app._on_player_trace({
            "script": "工业4", "depth": 0, "index": 10, "total": 813,
            "action": {"type": "key", "vk": 69, "name": "E", "down": True},
            "elapsed_ms": 2.0, "waited_ms": 1000.0,
        })
        app._flush_log_view("event")
        app._flush_log_view("trace")

        event_text = app.log_text.get("1.0", "end")
        self.assertIn("脚本开始执行：工业4", event_text)
        self.assertNotIn("第 11/813 行", event_text)

        app._show_log_view("trace")
        trace_text = app.log_text.get("1.0", "end")
        self.assertIn("第 11/813 行", trace_text)
        self.assertIn("按下 E", trace_text)
        self.assertNotIn("脚本开始执行：工业4", trace_text)

        # 切回事件日志：内容还在，没被明细顶掉。
        app._show_log_view("event")
        self.assertIn("脚本开始执行：工业4", app.log_text.get("1.0", "end"))


class AutohideScrollbarTests(unittest.TestCase):
    """列表滚动条自动显隐：空的隐藏、内容超一屏必须真的显示出来。"""

    ROWS = 200

    def _build(self):
        from tkinter import ttk

        from macroflow.ui.app import attach_autohide_scrollbar

        root = tk.Tk()
        # 布局尺寸要按真实（已映射）窗口算，所以不能 withdraw；用全透明代替，
        # 测试期间屏幕上不会真的闪出一个窗口。
        root.attributes("-alpha", 0.0)
        root.geometry("700x400")
        self.addCleanup(root.destroy)
        shell = ttk.Frame(root)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        tree = ttk.Treeview(shell, columns=("a",), show="headings", height=8)
        scroll = ttk.Scrollbar(shell, orient="vertical", command=tree.yview)
        # 与 _build_ui 相同的顺序：先 attach（滚动条还没进布局），再 grid。
        attach_autohide_scrollbar(tree, scroll)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        root.update()
        return root, tree, scroll

    def _insert_rows(self, root, tree, count):
        for index in range(count):
            tree.insert("", "end", iid=str(index), values=(index,))
        root.update()

    def test_scrollbar_appears_when_rows_overflow(self):
        # 回归：attach 时滚动条还没被布局管理器接管，显示路径曾走成 pack，
        # 在 grid 管理的外壳里 Tk 报 "cannot use geometry manager pack"，
        # 滚动条再也不出现——打开长脚本也看不到、拖不动。
        root, tree, scroll = self._build()
        self.assertFalse(scroll.winfo_ismapped())  # 空列表：自动隐藏
        self._insert_rows(root, tree, self.ROWS)
        self.assertEqual(scroll.winfo_manager(), "grid")
        self.assertTrue(scroll.winfo_ismapped())
        # 滚动条要排在列表右边，不能盖在列表上或掉到底部。
        self.assertEqual(scroll.winfo_x(), tree.winfo_x() + tree.winfo_width())
        self.assertEqual(scroll.winfo_y(), tree.winfo_y())
        first, last = tree.yview()
        self.assertLess(last - first, 1.0)  # 内容确实超出一屏
        tree.yview_moveto(1.0)
        root.update()
        self.assertGreaterEqual(tree.yview()[1], 1.0)  # 能滚到底

    def test_scrollbar_fits_without_rows(self):
        root, tree, scroll = self._build()
        self._insert_rows(root, tree, 3)
        self.assertFalse(scroll.winfo_ismapped())


class ModuleListScrollbarTests(unittest.TestCase):
    """模块列表滚动条：装得下不出空槽，超出一屏要真的显示出来。"""

    ROWS = 200

    def _build(self):
        from tkinter import ttk

        root = tk.Tk()
        # 与 AutohideScrollbarTests 同一套做法：布局尺寸要按已映射的窗口算，
        # 所以不能 withdraw；用全透明代替，屏幕上不会真的闪出窗口。
        root.attributes("-alpha", 0.0)
        root.geometry("700x400")
        self.addCleanup(root.destroy)
        list_frame = ttk.Frame(root)
        list_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(list_frame, columns=("region",), height=8)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        # 与 _build_tab 相同的顺序：列表先 pack，再交给滚动条助手。
        configure_module_list_scrollbar(tree, scroll)
        root.update()
        return root, tree, scroll

    def _insert_rows(self, root, tree, count):
        for index in range(count):
            tree.insert("", "end", iid=str(index), values=(index,))
        root.update()

    def test_scrollbar_hidden_while_rows_fit(self):
        root, tree, scroll = self._build()
        self._insert_rows(root, tree, 3)
        self.assertFalse(scroll.winfo_ismapped())

    def test_scrollbar_appears_and_is_visible_when_rows_overflow(self):
        root, tree, scroll = self._build()
        self._insert_rows(root, tree, self.ROWS)
        self.assertTrue(scroll.winfo_ismapped())
        # 滚动条要排在列表右边（不能盖在列表上或掉到底部）。
        self.assertEqual(scroll.winfo_x(), tree.winfo_x() + tree.winfo_width())
        self.assertEqual(scroll.winfo_y(), tree.winfo_y())
        # 明显的对比色：凹槽与滑块都不是列表底色，否则看起来就像「没有滚动条」。
        self.assertNotEqual(str(scroll.cget("style")), "")
        first, last = tree.yview()
        self.assertLess(last - first, 1.0)
        tree.yview_moveto(1.0)
        root.update()
        self.assertGreaterEqual(tree.yview()[1], 1.0)

if __name__ == '__main__':
    unittest.main()
