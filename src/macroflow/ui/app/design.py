"""统一视觉系统：设计变量、ttk 样式与可复用的暗色组件。

界面只声明语义（卡片 / 主按钮 / 标签页 / 分段选择 / 动作标签），颜色、间距、
圆角和字体统一从这里取。新增控件前先看本模块是否已有现成组件，避免各页面
自己拼一套颜色。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

import ttkbootstrap as ttk

from .base import pad, px
from .constants import (
    COLOR_ACCENT_SOFT, COLOR_ACCENT_TEXT, COLOR_BG, COLOR_BLUE, COLOR_BORDER,
    COLOR_FOCUS, COLOR_GREEN, COLOR_MUTED, COLOR_RED, COLOR_SIDEBAR,
    COLOR_SURFACE, COLOR_SURFACE_ACTIVE, COLOR_SURFACE_ALT, COLOR_SURFACE_HOVER,
    COLOR_TEXT, COLOR_TEXT_2, FONT_BODY, FONT_BRAND, FONT_FAMILY,
    FONT_MONO, FONT_SMALL, FONT_SUBTITLE, FONT_TITLE,
)

RADIUS = 8
SEGMENT_HEIGHT = 28
NAV_HEIGHT = 36


def _rounded_points(x0, y0, x1, y1, radius):
    """Return a smooth polygon point list for a rounded rectangle."""
    return (
        x0 + radius, y0, x1 - radius, y0, x1, y0,
        x1, y0 + radius, x1, y1 - radius, x1, y1,
        x1 - radius, y1, x0 + radius, y1, x0, y1,
        x0, y1 - radius, x0, y0 + radius, x0, y0,
    )


def draw_rounded(canvas, x0, y0, x1, y1, radius, **options):
    """Draw a rounded rectangle as a smoothed polygon."""
    if x1 <= x0 or y1 <= y0:
        return None
    radius = max(0, min(radius, int((x1 - x0) / 2), int((y1 - y0) / 2)))
    points = _rounded_points(x0, y0, x1, y1, radius)
    return canvas.create_polygon(points, smooth=True, splinesteps=18, **options)


def configure_theme(root) -> None:
    """Apply the complete application style sheet to a ttkbootstrap root."""
    style = root.style
    root.configure(background=COLOR_BG)

    # ---------------------------------------------------------------- surfaces
    for name in ("TFrame", "App.TFrame", "Workspace.TFrame", "Toolbar.TFrame", "Status.TFrame"):
        style.configure(name, background=COLOR_BG)
    style.configure("Sidebar.TFrame", background=COLOR_SIDEBAR)
    style.configure("Rail.TFrame", background=COLOR_SIDEBAR)
    style.configure("Surface.TFrame", background=COLOR_SURFACE)
    style.configure("Card.TFrame", background=COLOR_SURFACE)
    style.configure("Card.Toolbar.TFrame", background=COLOR_SURFACE)
    style.configure("Pane.TFrame", background=COLOR_SURFACE)

    # ------------------------------------------------------------------ labels
    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                    font=(FONT_FAMILY, FONT_BODY))
    style.configure("Sidebar.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_TEXT)
    style.configure("Brand.TLabel", background=COLOR_SIDEBAR, foreground="#FFFFFF",
                    font=(FONT_FAMILY, FONT_BRAND, "bold"))
    style.configure("RailBrand.TLabel", background=COLOR_SIDEBAR, foreground="#FFFFFF",
                    font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
    style.configure("RailCaption.TLabel", background=COLOR_SIDEBAR, foreground="#6F7D8C",
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("RailSection.TLabel", background=COLOR_SIDEBAR, foreground="#7D8B99",
                    font=(FONT_FAMILY, FONT_SMALL, "bold"))
    style.configure("Muted.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("SidebarMuted.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("Section.TLabel", background=COLOR_SIDEBAR, foreground=COLOR_TEXT,
                    font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
    style.configure("SidebarSection.TLabel", background=COLOR_SIDEBAR, foreground="#A9B7C5",
                    font=(FONT_FAMILY, FONT_SMALL, "bold"))
    style.configure("PageTitle.TLabel", background=COLOR_BG, foreground="#F2F6FA",
                    font=(FONT_FAMILY, FONT_TITLE, "bold"))
    style.configure("PageSubtitle.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("Empty.TLabel", background=COLOR_SURFACE, foreground="#5C6B7A",
                    font=(FONT_FAMILY, FONT_SUBTITLE))
    style.configure("StatusDot.TLabel", background=COLOR_BG, foreground=COLOR_GREEN,
                    font=(FONT_FAMILY, FONT_BODY))
    style.configure("StatusText.TLabel", background=COLOR_BG, foreground="#CBD5E1",
                    font=(FONT_FAMILY, FONT_BODY))
    style.configure("StatusCaption.TLabel", background=COLOR_BG, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("CardTitle.TLabel", background=COLOR_SURFACE, foreground="#EDF3F8",
                    font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
    style.configure("CardHint.TLabel", background=COLOR_SURFACE, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("CardLabel.TLabel", background=COLOR_SURFACE, foreground="#C2CEDA",
                    font=(FONT_FAMILY, FONT_BODY))
    style.configure("CardValue.TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT,
                    font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.configure("Pill.TLabel", background=COLOR_SURFACE_ALT, foreground=COLOR_MUTED,
                    padding=pad(7, 2), font=(FONT_FAMILY, FONT_SMALL, "bold"))
    style.configure("PillAccent.TLabel", background=COLOR_ACCENT_SOFT,
                    foreground=COLOR_ACCENT_TEXT, padding=pad(7, 2),
                    font=(FONT_FAMILY, FONT_SMALL, "bold"))
    style.configure("MiniTitle.TLabel", background=COLOR_SURFACE, foreground=COLOR_RED,
                    font=(FONT_FAMILY, FONT_SUBTITLE, "bold"))
    style.configure("MiniText.TLabel", background=COLOR_SURFACE, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL))
    style.configure("MiniTime.TLabel", background=COLOR_SURFACE, foreground=COLOR_TEXT,
                    font=(FONT_MONO, FONT_TITLE, "bold"))
    style.configure("MiniWarning.TLabel", background=COLOR_SURFACE, foreground=COLOR_RED,
                    font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.configure("GlobalMarker.TLabel", background=COLOR_BG, foreground="#56D364",
                    font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.configure("ToolGroupTitle.TLabel", background=COLOR_SURFACE, foreground=COLOR_MUTED,
                    font=(FONT_FAMILY, FONT_SMALL, "bold"))

    # ------------------------------------------------------------- form inputs
    field = dict(fieldbackground=COLOR_SURFACE_ALT, foreground=COLOR_TEXT,
                 bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER,
                 darkcolor=COLOR_BORDER, insertcolor=COLOR_TEXT)
    style.configure("TEntry", padding=pad(6, 3), **field)
    style.map("TEntry", bordercolor=[("focus", COLOR_FOCUS)],
              lightcolor=[("focus", COLOR_FOCUS)], darkcolor=[("focus", COLOR_FOCUS)])
    style.configure("TSpinbox", padding=pad(6, 3), arrowcolor=COLOR_MUTED, **field)
    style.configure("TCombobox", padding=pad(6, 3), arrowcolor=COLOR_MUTED, **field)
    style.map("TCombobox", bordercolor=[("focus", COLOR_FOCUS)],
              lightcolor=[("focus", COLOR_FOCUS)], darkcolor=[("focus", COLOR_FOCUS)])
    for input_style in ("TEntry", "TSpinbox", "TCombobox"):
        style.map(input_style,
                  fieldbackground=[("disabled", "#141B23"), ("readonly", COLOR_SURFACE_ALT)],
                  background=[("disabled", "#141B23"), ("readonly", COLOR_SURFACE_ALT)],
                  foreground=[("disabled", "#5A6673")])
    style.configure("TCheckbutton", padding=pad(2, 1), font=(FONT_FAMILY, FONT_BODY))
    style.map("TCheckbutton",
              background=[("active", COLOR_SURFACE_HOVER)],
              foreground=[("active", "#FFFFFF")])

    # ---------------------------------------------------------------- buttons
    for color in ("primary", "secondary", "success", "info", "warning", "danger", "light", "dark"):
        style.configure(f"{color}.TButton", padding=pad(9, 3), font=(FONT_FAMILY, FONT_BODY))
        style.configure(f"{color}.Outline.TButton", padding=pad(9, 3),
                        font=(FONT_FAMILY, FONT_BODY))
    style.configure("TButton", padding=pad(9, 3), font=(FONT_FAMILY, FONT_BODY),
                    borderwidth=0, relief="flat")

    style.configure("Primary.TButton", background=COLOR_BLUE, foreground="#FFFFFF",
                    bordercolor=COLOR_BLUE, lightcolor=COLOR_BLUE, darkcolor=COLOR_BLUE,
                    relief="flat", borderwidth=0, padding=pad(12, 4),
                    font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.map("Primary.TButton",
              background=[("pressed", "#2F6FD6"), ("active", "#4C8DFF"),
                          ("disabled", COLOR_SURFACE_ALT)],
              foreground=[("disabled", "#5A6673")])

    ghost = dict(background=COLOR_SURFACE, foreground="#D6E0EA",
                 bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER,
                 darkcolor=COLOR_BORDER, relief="flat", borderwidth=1,
                 padding=pad(10, 3), font=(FONT_FAMILY, FONT_BODY))
    ghost_map = dict(background=[("active", COLOR_SURFACE_HOVER), ("pressed", COLOR_SURFACE_ACTIVE)],
                     foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")],
                     bordercolor=[("active", "#3A4959")])
    style.configure("Ghost.TButton", **ghost)
    style.map("Ghost.TButton", **ghost_map)
    compact_ghost = dict(ghost, padding=pad(8, 2))
    style.configure("CompactGhost.TButton", **compact_ghost)
    style.map("CompactGhost.TButton", **ghost_map)
    style.configure("SidebarGhost.TButton", **compact_ghost)
    style.map("SidebarGhost.TButton", **ghost_map)

    # bootstyle 兜底：仍有旧代码用 bootstyle="primary"/"secondary" 表示选中态，
    # 统一切到新配色，避免 ttkbootstrap 默认蓝灰与卡片底色打架。
    style.configure("primary.TButton", background=COLOR_BLUE, foreground="#FFFFFF",
                    bordercolor=COLOR_BLUE, lightcolor=COLOR_BLUE, darkcolor=COLOR_BLUE,
                    relief="flat", borderwidth=0, padding=pad(10, 4),
                    font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.map("primary.TButton",
              background=[("pressed", "#2F6FD6"), ("active", "#4C8DFF")],
              foreground=[("disabled", "#CFE1FF")])
    style.configure("secondary.TButton", **dict(ghost, padding=pad(10, 4)))
    style.map("secondary.TButton", **ghost_map)

    chip = dict(background=COLOR_SURFACE_ALT, foreground="#DCE6F0",
                bordercolor=COLOR_SURFACE_ALT, lightcolor=COLOR_SURFACE_ALT,
                darkcolor=COLOR_SURFACE_ALT, relief="flat", borderwidth=1,
                padding=pad(10, 4), font=(FONT_FAMILY, FONT_BODY))
    style.configure("Chip.TButton", **chip)
    style.map("Chip.TButton",
              background=[("pressed", COLOR_SURFACE_ACTIVE), ("active", COLOR_SURFACE_HOVER)],
              bordercolor=[("active", "#3A4959")],
              foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")])
    style.configure("ChipAccent.TButton", background=COLOR_ACCENT_SOFT,
                    foreground=COLOR_ACCENT_TEXT, bordercolor="#1E3A5C",
                    lightcolor="#1E3A5C", darkcolor="#1E3A5C", relief="flat",
                    borderwidth=1, padding=pad(10, 4),
                    font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.map("ChipAccent.TButton",
              background=[("pressed", "#1B3A60"), ("active", "#17304F")],
              foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")])
    style.configure("ChipDanger.TButton", background="#2A1618", foreground="#F87171",
                    bordercolor="#3B2024", lightcolor="#3B2024", darkcolor="#3B2024",
                    relief="flat", borderwidth=1, padding=pad(10, 4),
                    font=(FONT_FAMILY, FONT_BODY))
    style.map("ChipDanger.TButton",
              background=[("pressed", "#3A1D21"), ("active", "#351A1E")],
              foreground=[("active", "#FFFFFF")])

    # Legacy style names are kept as aliases so existing pages keep one visual
    # language while they are migrated to cards.
    style.configure("ScriptTool.TButton", **chip)
    style.map("ScriptTool.TButton", **style.map("Chip.TButton"))
    style.configure("AccentScriptTool.TButton", **dict(chip, background=COLOR_ACCENT_SOFT,
                    foreground=COLOR_ACCENT_TEXT, bordercolor="#1E3A5C",
                    lightcolor="#1E3A5C", darkcolor="#1E3A5C",
                    font=(FONT_FAMILY, FONT_BODY, "bold")))
    style.map("AccentScriptTool.TButton",
              background=[("active", "#17304F"), ("pressed", "#1B3A60")],
              foreground=[("disabled", "#58646F"), ("active", "#FFFFFF")])
    style.configure("DangerScriptTool.TButton", background="#2A1618", foreground="#F87171",
                    bordercolor="#3B2024", lightcolor="#3B2024", darkcolor="#3B2024",
                    relief="flat", borderwidth=1, padding=pad(10, 4),
                    font=(FONT_FAMILY, FONT_BODY))
    style.map("DangerScriptTool.TButton",
              background=[("active", "#351A1E"), ("pressed", "#3A1D21")],
              foreground=[("active", "#FFFFFF")])

    # --------------------------------------------------------------- scrollbar
    for orientation in ("Horizontal", "Vertical"):
        style.configure(f"dark.{orientation}.TScrollbar", troughcolor=COLOR_BG,
                        background=COLOR_BORDER, bordercolor=COLOR_BG,
                        lightcolor=COLOR_BORDER, darkcolor=COLOR_BORDER,
                        arrowcolor=COLOR_MUTED)

    # ------------------------------------------------------------------ tabs
    style.configure("TNotebook", background=COLOR_SURFACE, borderwidth=0,
                    tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=COLOR_SURFACE, foreground=COLOR_MUTED,
                    borderwidth=0, padding=pad(14, 6), font=(FONT_FAMILY, FONT_BODY))
    style.map("TNotebook.Tab",
              background=[("selected", COLOR_SURFACE_ALT), ("active", COLOR_SURFACE_HOVER)],
              foreground=[("selected", "#FFFFFF"), ("active", COLOR_TEXT)])

    # ------------------------------------------------------------------ trees
    tree_font = tkfont.Font(family=FONT_FAMILY, size=FONT_BODY)
    style.configure("Treeview", background=COLOR_SURFACE, fieldbackground=COLOR_SURFACE,
                    foreground="#DCE6F0", bordercolor=COLOR_BORDER, borderwidth=0,
                    rowheight=tree_font.metrics("linespace") + px(10),
                    font=(FONT_FAMILY, FONT_BODY))
    style.configure("Treeview.Heading", background=COLOR_SURFACE, foreground="#7D8B99",
                    bordercolor=COLOR_BORDER, relief="flat", padding=pad(8, 5),
                    font=(FONT_FAMILY, FONT_SMALL, "bold"))
    style.map("Treeview", background=[("selected", "#1F4C80")],
              foreground=[("selected", "#FFFFFF")])
    style.configure("Workflow.Treeview")
    style.map("Workflow.Treeview", background=[("selected", "#1F4C80")],
              foreground=[("selected", "#FFFFFF")])
    style.map("Treeview.Heading", background=[("active", "#1B2530")])

    # -------------------------------------------------------------- separators
    # ttkbootstrap caches the separator element globally; a second configure in
    # the same process raises "Duplicate element". Color is DPI independent so
    # it only has to be applied once.
    if not getattr(root, "_mf_separator_configured", False):
        root._mf_separator_configured = True
        style.configure("TSeparator", background=COLOR_BORDER)

    # ------------------------------------------------------------- misc styles
    style.configure("SectionCard.TLabelframe", background=COLOR_SURFACE,
                    bordercolor=COLOR_BORDER, lightcolor=COLOR_BORDER,
                    darkcolor=COLOR_BORDER, relief="solid", borderwidth=1)
    style.configure("SectionCard.TLabelframe.Label", background=COLOR_SURFACE,
                    foreground=COLOR_TEXT, font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.configure("GlobalTrigger.TFrame", background=COLOR_SURFACE)
    style.configure("GlobalTriggerTitle.TLabel", background=COLOR_SURFACE,
                    foreground=COLOR_TEXT, font=(FONT_FAMILY, FONT_BODY, "bold"))
    style.configure("GlobalTriggerSummary.TLabel", background=COLOR_SURFACE,
                    foreground=COLOR_MUTED, font=(FONT_FAMILY, FONT_BODY))
    style.configure("TProgressbar", troughcolor=COLOR_SURFACE_ALT,
                    background=COLOR_BLUE, bordercolor=COLOR_SURFACE_ALT)

    root.option_add("*TCombobox*Listbox*Background", COLOR_SURFACE_ALT)
    root.option_add("*TCombobox*Listbox*Foreground", COLOR_TEXT)
    root.option_add("*TCombobox*Listbox*selectBackground", "#1F4C80")
    root.option_add("*TCombobox*Listbox*selectForeground", "#FFFFFF")
    root.option_add("*Font", (FONT_FAMILY, FONT_BODY))
    style.configure(".", font=(FONT_FAMILY, FONT_BODY))


# ---------------------------------------------------------------- primitives
def checkbutton(parent, text, variable, command=None, *, background=COLOR_SURFACE):
    """Dark tk checkbutton that blends into the given surface color."""
    return tk.Checkbutton(
        parent, text=text, variable=variable, command=command,
        background=background, activebackground=background, foreground="#C9D5E1",
        activeforeground="#FFFFFF", selectcolor=COLOR_SURFACE_ALT, anchor="w",
        highlightthickness=0, borderwidth=0, autostyle=False,
        font=(FONT_FAMILY, FONT_BODY),
    )


def radiobutton(parent, text, value, variable, command=None, *, background=COLOR_SURFACE):
    return tk.Radiobutton(
        parent, text=text, value=value, variable=variable, command=command,
        background=background, activebackground=background, foreground="#C9D5E1",
        activeforeground="#FFFFFF", selectcolor=COLOR_SURFACE_ALT,
        highlightthickness=0, borderwidth=0, autostyle=False,
        font=(FONT_FAMILY, FONT_BODY),
    )


def _draw_nav_icon(canvas, name, left, top, color, unit):
    """Draw a 16px line icon for the navigation rail."""
    lw = max(1, px(1.4))

    def line(*coords, **options):
        canvas.create_line(*coords, fill=color, width=lw, capstyle="round",
                           joinstyle="round", **options)

    if name == "script":
        canvas.create_rectangle(left + unit * 0.18, top + unit * 0.10,
                                left + unit * 0.82, top + unit * 0.90,
                                outline=color, width=lw)
        line(left + unit * 0.34, top + unit * 0.40, left + unit * 0.66, top + unit * 0.40)
        line(left + unit * 0.34, top + unit * 0.60, left + unit * 0.58, top + unit * 0.60)
    elif name == "workflow":
        canvas.create_oval(left + unit * 0.10, top + unit * 0.10,
                           left + unit * 0.38, top + unit * 0.38, outline=color, width=lw)
        canvas.create_oval(left + unit * 0.62, top + unit * 0.42,
                           left + unit * 0.90, top + unit * 0.70, outline=color, width=lw)
        canvas.create_oval(left + unit * 0.10, top + unit * 0.68,
                           left + unit * 0.38, top + unit * 0.96, outline=color, width=lw)
        line(left + unit * 0.38, top + unit * 0.24, left + unit * 0.62, top + unit * 0.24,
             left + unit * 0.62, top + unit * 0.56)
        line(left + unit * 0.38, top + unit * 0.82, left + unit * 0.56, top + unit * 0.82,
             left + unit * 0.56, top + unit * 0.56, left + unit * 0.62, top + unit * 0.56)
    elif name == "log":
        canvas.create_rectangle(left + unit * 0.12, top + unit * 0.16,
                                left + unit * 0.88, top + unit * 0.84,
                                outline=color, width=lw)
        line(left + unit * 0.30, top + unit * 0.36, left + unit * 0.42, top + unit * 0.50,
             left + unit * 0.30, top + unit * 0.64)
        line(left + unit * 0.52, top + unit * 0.64, left + unit * 0.72, top + unit * 0.64)
    elif name == "plugins":
        for cx, cy in ((0.16, 0.16), (0.52, 0.16), (0.16, 0.52), (0.52, 0.52)):
            canvas.create_rectangle(left + unit * cx, top + unit * cy,
                                    left + unit * (cx + 0.30), top + unit * (cy + 0.30),
                                    outline=color, width=lw)
    elif name == "settings":
        for index, knob in ((0.28, 0.34), (0.50, 0.66), (0.72, 0.44)):
            line(left + unit * 0.14, top + unit * index, left + unit * 0.86,
                 top + unit * index)
            canvas.create_oval(left + unit * knob, top + unit * (index - 0.12),
                               left + unit * (knob + 0.16), top + unit * (index + 0.04),
                               outline=color, width=lw, fill=COLOR_SIDEBAR)


class NavItem(tk.Canvas):
    """Left navigation entry with vector icon, hover and active states."""

    def __init__(self, parent, *, text, icon, command, background=COLOR_SIDEBAR):
        self._text, self._icon, self._command = text, icon, command
        self._background = background
        self._active = False
        self._hover = False
        super().__init__(parent, height=px(NAV_HEIGHT), background=background,
                         borderwidth=0, highlightthickness=0, takefocus=True,
                         cursor="hand2", autostyle=False)
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", lambda _event: self.invoke())
        self.bind("<Return>", lambda _event: self.invoke())
        self.bind("<space>", lambda _event: self.invoke())
        self.bind("<FocusIn>", self._draw)
        self.bind("<FocusOut>", self._draw)

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._draw()

    def _enter(self, _event=None):
        self._hover = True
        self._draw()

    def _leave(self, _event=None):
        self._hover = False
        self._draw()

    def invoke(self):
        return self._command()

    def _draw(self, _event=None):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        if self._active:
            fill, text, icon = COLOR_SURFACE_ACTIVE, "#FFFFFF", COLOR_ACCENT_TEXT
        elif self._hover:
            fill, text, icon = COLOR_SURFACE_HOVER, COLOR_TEXT, "#C2CEDA"
        else:
            fill, text, icon = self._background, COLOR_TEXT_2, COLOR_MUTED
        if fill != self._background:
            draw_rounded(self, 0, 0, width, height, px(RADIUS),
                         fill=fill, outline=fill)
        if self._active:
            draw_rounded(self, 0, px(7), px(3), height - px(7), px(1), fill=COLOR_BLUE,
                         outline=COLOR_BLUE)
        unit = px(16)
        left = px(12)
        top = (height - unit) / 2
        _draw_nav_icon(self, self._icon, left, top, icon, unit)
        self.create_text(left + unit + px(11), height / 2, text=self._text, anchor="w",
                         fill=text, font=(FONT_FAMILY, FONT_BODY,
                                          "bold" if self._active else "normal"))
        if self.focus_get() == self:
            draw_rounded(self, 1, 1, width - 1, height - 1, px(RADIUS),
                         fill="", outline=COLOR_FOCUS, width=px(1))


class PageStack(ttk.Frame):
    """Tab-like page container driven by the navigation rail.

    The public API mirrors the small subset of ``ttk.Notebook`` the rest of the
    application relies on (``add_page`` / ``select`` / ``index`` / ``current``).
    """

    def __init__(self, parent, *, on_page_change=None):
        super().__init__(parent, style="App.TFrame")
        self._pages = []
        self._current = None
        self._on_page_change = on_page_change

    def add_page(self, text: str):
        frame = ttk.Frame(self, style="App.TFrame")
        self._pages.append({"text": text, "frame": frame})
        return frame

    def add(self, frame, text: str):
        self._pages.append({"text": text, "frame": frame})
        return frame

    def _resolve(self, page):
        for index, entry in enumerate(self._pages):
            if entry["frame"] is page or entry["text"] == page:
                return index
        if isinstance(page, int) and 0 <= page < len(self._pages):
            return page
        return None

    def select(self, page=None, *, notify=True):
        if page is None:
            return self._current
        index = self._resolve(page)
        if index is None:
            return self._current
        entry = self._pages[index]
        if self._current is not entry["frame"]:
            for other in self._pages:
                other["frame"].pack_forget()
            entry["frame"].pack(fill="both", expand=True)
            self._current = entry["frame"]
            if notify and self._on_page_change is not None:
                self._on_page_change(entry["frame"])
        return entry["frame"]

    def index(self, page):
        index = self._resolve(page)
        return index if index is not None else 0

    def current(self):
        return self._current


class Card(ttk.Frame):
    """Surface container with an optional title row.

    ``body`` is the content frame; callers pack their rows into it.
    """

    def __init__(self, parent, *, padding=(14, 12, 14, 12), title=None, hint=None,
                 style="Card.TFrame"):
        super().__init__(parent, style=style, padding=pad(*padding))
        self.body = ttk.Frame(self, style=style)
        self.header = None
        if title:
            self.header = ttk.Frame(self, style=style)
            self.header.pack(fill="x", pady=pad(0, 9))
            ttk.Label(self.header, text=title, style="CardTitle.TLabel").pack(side="left")
            if hint:
                ttk.Label(self.header, text=hint, style="CardHint.TLabel").pack(
                    side="left", padx=pad(9, 0))
        self.body.pack(fill="both", expand=True)


class _Segment(tk.Canvas):
    """One pill inside a :class:`SegmentedControl`."""

    def __init__(self, parent, *, text, command, background):
        self._text, self._command = text, command
        self._background = background
        self._active = False
        self._hover = False
        self._pressed = False
        font = tkfont.Font(family=FONT_FAMILY, size=FONT_BODY)
        width = font.measure(text) + px(26)
        super().__init__(
            parent, width=width, height=px(SEGMENT_HEIGHT), background=background,
            borderwidth=0, highlightthickness=0, takefocus=True, cursor="hand2",
            autostyle=False,
        )
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _event: self.invoke())
        self.bind("<space>", lambda _event: self.invoke())
        self.bind("<FocusIn>", self._draw)
        self.bind("<FocusOut>", self._draw)

    def set_active(self, active: bool) -> None:
        if self._active != active:
            self._active = active
            self._draw()

    def _enter(self, _event=None):
        self._hover = True
        self._draw()

    def _leave(self, _event=None):
        self._hover = False
        self._pressed = False
        self._draw()

    def _press(self, _event=None):
        self._pressed = True
        self.focus_set()
        self._draw()

    def _release(self, event=None):
        pressed, self._pressed = self._pressed, False
        if pressed:
            self.invoke()
        self._draw()

    def invoke(self):
        return self._command()

    def _draw(self, _event=None):
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        if self._active:
            fill, outline, text = COLOR_SURFACE_ACTIVE, "#31547A", "#FFFFFF"
        elif self._hover:
            fill, outline, text = COLOR_SURFACE_HOVER, "#33424F", COLOR_TEXT
        else:
            fill, outline, text = self._background, self._background, COLOR_MUTED
        draw_rounded(self, 1, 1, width - 1, height - 1, px(RADIUS - 2),
                     fill=fill, outline=outline, width=px(1))
        if self.focus_get() == self:
            draw_rounded(self, 2, 2, width - 2, height - 2, px(RADIUS - 2),
                         fill="", outline=COLOR_FOCUS, width=px(1))
        self.create_text(width / 2, height / 2, text=self._text, fill=text,
                         font=(FONT_FAMILY, FONT_BODY, "bold" if self._active else "normal"))


class SegmentedControl(tk.Frame):
    """Pill-shaped exclusive selector used for page tabs and palettes."""

    def __init__(self, parent, *, options, value=None, on_change=None,
                 background=COLOR_SURFACE):
        super().__init__(parent, background=background, borderwidth=0,
                         highlightthickness=0, autostyle=False)
        self._on_change = on_change
        self._segments = []
        for key, label in options:
            segment = _Segment(self, text=label, background=background,
                               command=lambda key=key: self.select(key))
            segment.pack(side="left", padx=(0, px(5)))
            self._segments.append((key, segment))
        self._value = value if value is not None else (options[0][0] if options else None)
        self._apply()

    def value(self):
        return self._value

    def select(self, value, *, notify=True):
        if value not in [key for key, _segment in self._segments]:
            return
        changed = value != self._value
        self._value = value
        self._apply()
        if notify and changed and self._on_change is not None:
            self._on_change(value)

    def segment(self, value):
        for key, segment in self._segments:
            if key == value:
                return segment
        return None

    def _apply(self):
        for key, segment in self._segments:
            segment.set_active(key == self._value)


class FlowFrame(tk.Frame):
    """Wrap children left-to-right, collapsing extra rows behind a toggle."""

    def __init__(self, parent, *, gap=6, row_gap=6, background=COLOR_SURFACE,
                 max_rows=1, toggle_style="Chip.TButton"):
        super().__init__(parent, background=background, borderwidth=0,
                         highlightthickness=0, autostyle=False, height=px(SEGMENT_HEIGHT))
        self._items = []
        self._background = background
        self._gap, self._row_gap = gap, row_gap
        self._max_rows = max_rows
        self._toggle_style = toggle_style
        self._expanded = False
        self._toggle = None
        self._layout_cache = None
        self.bind("<Configure>", lambda _event: self._relayout())

    def add(self, widget):
        self._items.append(widget)
        widget.place(x=-9999, y=-9999)
        return widget

    def finish(self):
        if self._items and self._toggle is None:
            self._toggle = ttk.Button(self, text="更多 ▾", style=self._toggle_style,
                                      command=self.toggle_expanded)
            self._toggle.place(x=-9999, y=-9999)
        self._relayout()

    def toggle_expanded(self):
        self._expanded = not self._expanded
        self._layout_cache = None
        self._relayout()

    def _relayout(self, _event=None):
        width = self.winfo_width()
        if width <= 1 or not self._items:
            return
        gap = px(self._gap)
        chip_height = max(widget.winfo_reqheight() for widget in self._items)
        step = chip_height + px(self._row_gap)
        positions = []
        x = row = 0
        for widget in self._items:
            item_width = widget.winfo_reqwidth()
            if x > 0 and x + item_width > width - px(2):
                x, row = 0, row + 1
            positions.append((x, row * step))
            x += item_width + gap
        total_rows = row + 1
        collapsed = not self._expanded and total_rows > self._max_rows
        visible_rows = self._max_rows if collapsed else total_rows
        last_x = last_y = 0
        for widget, (item_x, item_y) in zip(self._items, positions):
            if item_y // step >= visible_rows:
                widget.place_forget()
                continue
            widget.place(x=item_x, y=item_y)
            if item_y >= last_y:
                last_x, last_y = item_x + widget.winfo_reqwidth(), item_y
        needs_toggle = total_rows > self._max_rows
        if self._toggle is None or not needs_toggle:
            if self._toggle is not None:
                self._toggle.place_forget()
            self.configure(height=last_y + chip_height + px(self._row_gap))
            return
        self._toggle.configure(text="收起 ▴" if self._expanded else "更多 ▾")
        toggle_width = self._toggle.winfo_reqwidth()
        toggle_x, toggle_y = last_x + px(self._gap), last_y
        if toggle_x + toggle_width > width:
            toggle_x, toggle_y = px(2), last_y + step
        self._toggle.place(x=toggle_x, y=toggle_y)
        self.configure(height=toggle_y + chip_height + px(self._row_gap))
