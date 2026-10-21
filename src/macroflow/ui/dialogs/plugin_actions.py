"""Edit an independent action list using the main script insertion commands."""
import copy
import ttkbootstrap as ttk

from macroflow.core.models import ACTION_ID_KEY, ensure_action_ids
from macroflow.ui.app.summaries import action_summary
from .actions import edit_action
from .base import ModalDialog, pad, px
from .route_action_insert import ScriptActionInserter, route_action_categories


class ActionListEditor(ModalDialog):
    def __init__(self, parent, app, actions):
        super().__init__(parent, "代码块", 840, 560)
        self.resizable(True, True)
        self.app = app
        self.actions = copy.deepcopy(actions)
        ensure_action_ids(self.actions)
        self.inserter = ScriptActionInserter(self, app, self.actions)
        body = ttk.Frame(self, padding=px(12))
        body.pack(fill="both", expand=True)
        palette = ttk.Notebook(body)
        palette.pack(fill="x")
        for category, specs in route_action_categories():
            pane = ttk.Frame(palette, padding=px(4))
            palette.add(pane, text=category)
            for index, (label, command, style) in enumerate(specs):
                ttk.Button(pane, text=label, style=style,
                           command=getattr(self.inserter, command)).grid(
                    row=index // 4, column=index % 4, sticky="w", padx=px(3), pady=px(3))
        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x", pady=pad(10, 6))
        for label, command in (("编辑", self.edit_selected), ("删除", self.delete_selected),
                               ("上移", lambda: self.move_selected(-1)),
                               ("下移", lambda: self.move_selected(1))):
            ttk.Button(toolbar, text=label, command=command, style="CompactGhost.TButton").pack(side="left", padx=pad(0, 5))
        rows = ttk.Frame(body)
        rows.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(rows, columns=("index", "kind", "detail", "delay"), show="headings", selectmode="extended")
        for column, label, width in (("index", "行", 45), ("kind", "动作", 140),
                                      ("detail", "参数", 450), ("delay", "执行前延时", 95)):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=px(width), stretch=column == "detail")
        scrollbar = ttk.Scrollbar(rows, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        self.tree.bind("<Delete>", lambda _event: self.delete_selected())
        footer = ttk.Frame(body)
        footer.pack(fill="x", pady=pad(10, 0))
        ttk.Button(footer, text="确定", command=self.save, bootstyle="primary").pack(side="right")
        ttk.Button(footer, text="取消", command=self.destroy, style="CompactGhost.TButton").pack(side="right", padx=pad(0, 6))
        self.reload()

    def selected_index(self):
        selected = self.tree.selection()
        return int(selected[-1]) if selected else None

    def reload(self, selected=None):
        self.tree.delete(*self.tree.get_children())
        rows = {action[ACTION_ID_KEY]: index + 1 for index, action in enumerate(self.actions)}
        for index, action in enumerate(self.actions):
            self.tree.insert("", "end", iid=str(index), values=(index + 1, *action_summary(action, rows)))
        if selected is not None and self.actions:
            row = str(min(selected, len(self.actions) - 1))
            self.tree.selection_set(row)
            self.tree.see(row)

    def insert_actions(self, index, actions):
        inserted = copy.deepcopy(actions)
        self.actions[index:index] = inserted
        ensure_action_ids(self.actions)
        self.reload(index + len(inserted) - 1)

    def edit_selected(self):
        index = self.selected_index()
        if index is None:
            return
        updated = edit_action(self, copy.deepcopy(self.actions[index]), self.actions,
                              on_row_list_test=self.inserter.test_row_list_condition_click,
                              settings={"resolution_styles": self.inserter.resolution_styles})
        if updated is not None:
            updated[ACTION_ID_KEY] = self.actions[index][ACTION_ID_KEY]
            self.actions[index] = updated
            self.reload(index)

    def delete_selected(self):
        selected = sorted((int(row) for row in self.tree.selection()), reverse=True)
        for index in selected:
            del self.actions[index]
        if selected:
            self.reload(min(selected))

    def move_selected(self, offset):
        index = self.selected_index()
        if index is not None and 0 <= index + offset < len(self.actions):
            self.actions[index], self.actions[index + offset] = self.actions[index + offset], self.actions[index]
            self.reload(index + offset)

    def save(self):
        self.result = copy.deepcopy(self.actions)
        self.destroy()
