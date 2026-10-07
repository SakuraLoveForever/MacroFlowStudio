"""Fixed-size Treeview item pools for large, category-filtered lists."""

from __future__ import annotations

from dataclasses import dataclass, replace
import tkinter as tk

from macroflow.ui.animation import ease_back, ease_out
from .base import COLOR_SURFACE, COLOR_TEXT


@dataclass(frozen=True)
class VirtualRow:
    key: str
    text: str
    values: tuple
    tags: tuple[str, ...] = ()
    kind: str = "normal"
    color: str = COLOR_TEXT


def blend_color(start: str, end: str, progress: float) -> str:
    channels = []
    for index in (1, 3, 5):
        first = int(start[index:index + 2], 16)
        last = int(end[index:index + 2], 16)
        channels.append(f"{max(0, min(255, round(first + (last - first) * progress))):02X}")
    return "#" + "".join(channels)


def visible_range(total: int, first: int, viewport: int, buffer: int = 3) -> tuple[int, int]:
    first = max(0, min(int(first), max(0, total - viewport)))
    return first, min(total, first + viewport + buffer)


class VirtualTreeRows:
    """Reuse Treeview items as slots; data keys never become Tk item IDs."""

    def __init__(self, tree, scrollbar, *, row_height: int = 24, animator=None):
        self.tree = tree
        self.scrollbar = scrollbar
        self.row_height = row_height
        self.animator = animator
        self.rows: list[VirtualRow] = []
        self._index_by_key: dict[str, int] = {}
        self._kind_counts: dict[str, int] = {}
        self.start = 0
        self._pool: dict[str, list[str]] = {}
        self._bound: dict[str, VirtualRow] = {}
        self._selected: set[str] = set()
        self._idle_job = None
        self._binding = False
        self._animate_next = False
        self._entry_duration = 180
        self._commit_ui = None
        self._hover_iid = None
        self._selected_iids: set[str] = set()
        self._row_backgrounds: dict[str, str] = {}
        scrollbar.configure(command=self.yview)
        tree.bind("<Configure>", lambda _event: self.schedule(), add="+")
        tree.bind("<<TreeviewSelect>>", self._remember_selection, add="+")
        tree.bind("<MouseWheel>", self._wheel, add="+")
        tree.bind("<Button-4>", lambda _event: self._scroll_wheel(-1), add="+")
        tree.bind("<Button-5>", lambda _event: self._scroll_wheel(1), add="+")
        tree.bind("<KeyPress>", self._key_scroll, add="+")
        tree.bind("<Motion>", self._hover, add="+")
        tree.bind("<Leave>", self._leave, add="+")
        tree.bind("<Destroy>", self._destroy, add="+")

    def _viewport(self) -> int:
        height = self.tree.winfo_height()
        return max(1, height // self.row_height) if height > 1 else max(1, int(self.tree.cget("height")))

    def set_rows(self, rows: list[VirtualRow], *, reset_scroll: bool = True,
                 animate: bool = False, commit_ui=None) -> None:
        self.rows = rows
        self._index_by_key = {row.key: index for index, row in enumerate(rows)}
        self._kind_counts = {}
        for row in rows:
            self._kind_counts[row.kind] = self._kind_counts.get(row.kind, 0) + 1
        if reset_scroll:
            self.start = 0
            self._selected.clear()
        self._animate_next = self._animate_next or animate
        self._commit_ui = commit_ui
        if animate and self.animator is not None and self._bound:
            self._entry_duration = 120
            self._animate_exit()
        else:
            self._entry_duration = 180
            self.schedule()

    def _animate_exit(self) -> None:
        visible = tuple(list(self._bound.items())[:self._viewport()])

        def frame(progress: float) -> None:
            for iid, row in visible:
                if self._bound.get(iid) is not row:
                    continue
                self.tree.tag_configure(
                    iid, foreground=blend_color(row.color, COLOR_SURFACE, progress),
                )
                if row.text:
                    self.tree.item(iid, text=" " * round(progress) + row.text)
            if progress >= 1:
                self.schedule()

        self.animator.animate((id(self), "exit"), 90, frame, ease_out)

    def update_values(self, key: str, values: tuple) -> None:
        index = self._index_by_key.get(key)
        if index is not None:
            self.rows[index] = replace(self.rows[index], values=values)
            self.schedule()

    def schedule(self) -> None:
        if self._idle_job is None:
            self._idle_job = self.tree.after_idle(self._commit)

    def _commit(self) -> None:
        self._idle_job = None
        viewport = self._viewport()
        self.start, end = visible_range(len(self.rows), self.start, viewport)
        visible = self.rows[self.start:end]
        self._binding = True
        try:
            if self._commit_ui is not None:
                callback, self._commit_ui = self._commit_ui, None
                callback()
            for kind, count in self._kind_counts.items():
                pool = self._pool.setdefault(kind, [])
                while len(pool) < min(count, viewport + 3):
                    iid = f"virtual:{kind}:{len(pool)}"
                    self.tree.insert("", "end", iid=iid)
                    self.tree.detach(iid)
                    pool.append(iid)
            for iid in self._bound:
                self.tree.detach(iid)
            self._bound.clear()
            used: dict[str, int] = {}
            for row in visible:
                slot = used.get(row.kind, 0)
                used[row.kind] = slot + 1
                iid = self._pool[row.kind][slot]
                self.tree.move(iid, "", "end")
                self.tree.item(iid, text=row.text, values=row.values,
                               tags=(*row.tags, iid))
                self.tree.tag_configure(iid, foreground=row.color, background=COLOR_SURFACE)
                self._bound[iid] = row
                self._row_backgrounds[iid] = COLOR_SURFACE
            selected = tuple(iid for iid, row in self._bound.items() if row.key in self._selected)
            self.tree.selection_set(selected)
            self._selected_iids = set(selected)
            self._hover_iid = None
            self.tree.yview_moveto(0)
            total = len(self.rows)
            self.scrollbar.set(
                self.start / total if total else 0,
                min(1.0, (self.start + viewport) / total) if total else 1,
            )
        finally:
            self._binding = False
        if self._animate_next:
            self._animate_next = False
            self.animate_entry()

    def animate_entry(self) -> None:
        if self.animator is None:
            return
        visible = tuple(list(self._bound.items())[:self._viewport()])
        if not visible:
            return

        def frame(progress: float) -> None:
            offset = " " * round(2 * (1 - progress))
            for iid, row in visible:
                if self._bound.get(iid) is not row:
                    continue
                if row.text:
                    self.tree.item(iid, text=offset + row.text)
                else:
                    self.tree.item(iid, values=(offset + str(row.values[0]), *row.values[1:]))
                self.tree.tag_configure(
                    iid, foreground=blend_color(COLOR_SURFACE, row.color, progress),
                )

        frame(0)
        self.animator.animate((id(self), "entry"), self._entry_duration, frame, ease_out)

    def key_for_iid(self, iid: str) -> str | None:
        row = self._bound.get(iid)
        return row.key if row else None

    def selected_keys(self) -> tuple[str, ...]:
        return tuple(row.key for row in self.rows if row.key in self._selected)

    def select_key(self, key: str) -> None:
        for index, row in enumerate(self.rows):
            if row.key == key:
                viewport = self._viewport()
                if not self.start <= index < self.start + viewport:
                    self.start = max(0, index - viewport + 1)
                self._selected = {key}
                self.schedule()
                return

    def _remember_selection(self, _event=None) -> None:
        if not self._binding:
            selected = set(self.tree.selection())
            # 复用行时 Tk 的选择事件会延后到达；这些事件不能清掉屏幕外的选择。
            if selected == self._selected_iids:
                return
            self._selected = {self._bound[iid].key for iid in selected if iid in self._bound}
            for iid in self._selected_iids | selected:
                if iid in self._bound:
                    self._emphasize(iid, iid in selected)
            self._selected_iids = selected

    def _emphasize(self, iid: str, selected: bool = False) -> None:
        if self.animator is None:
            return
        row = self._bound[iid]
        target = "#315D89" if selected else "#26394B" if iid == self._hover_iid else COLOR_SURFACE
        start = self._row_backgrounds.get(iid, COLOR_SURFACE)

        def frame(progress: float) -> None:
            if self._bound.get(iid) is not row:
                return
            color = blend_color(start, target, progress)
            self._row_backgrounds[iid] = color
            self.tree.tag_configure(iid, background=color)

        self.animator.animate((id(self), "emphasis", iid), 160, frame,
                              ease_back if selected else ease_out)

    def _hover(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid == self._hover_iid:
            return
        previous = self._hover_iid
        self._hover_iid = iid
        for slot in (previous, iid):
            if slot in self._bound:
                self._emphasize(slot, slot in self._selected_iids)

    def _leave(self, _event=None) -> None:
        previous = self._hover_iid
        self._hover_iid = None
        if previous in self._bound:
            self._emphasize(previous, previous in self._selected_iids)

    def yview(self, *args):
        if not args:
            total = len(self.rows)
            viewport = self._viewport()
            return (self.start / total if total else 0,
                    min(1.0, (self.start + viewport) / total) if total else 1)
        if args[0] == "moveto":
            self.start = round(float(args[1]) * len(self.rows))
        elif args[0] == "scroll":
            amount = int(args[1])
            self.start += amount * (self._viewport() if args[2] == "pages" else 1)
        self.schedule()

    def yview_scroll(self, amount: int, what: str) -> None:
        self.yview("scroll", amount, what)

    def _scroll_wheel(self, amount: int):
        self.yview_scroll(amount, "units")
        return "break"

    def _wheel(self, event):
        return self._scroll_wheel(-1 if event.delta > 0 else 1)

    def _key_scroll(self, event):
        amount = {"Up": -1, "Down": 1, "Prior": -self._viewport(),
                  "Next": self._viewport()}.get(event.keysym)
        if amount is not None:
            return self._scroll_wheel(amount)
        if event.keysym == "Home":
            self.yview("moveto", 0)
            return "break"
        if event.keysym == "End":
            self.yview("moveto", 1)
            return "break"
        return None

    def _destroy(self, _event=None) -> None:
        if self._idle_job is not None:
            try:
                self.tree.after_cancel(self._idle_job)
            except tk.TclError:
                pass
            self._idle_job = None
