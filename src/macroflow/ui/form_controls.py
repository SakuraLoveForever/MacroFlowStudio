"""Compact standard ttk controls shared by dialog forms."""
from math import ceil
import tkinter.font as tkfont
from tkinter import ttk


def bind_vertical_mousewheel(widget, target):
    """Scroll the target instead of changing a control's value under the pointer."""
    def scroll(event):
        delta = int(getattr(event, "delta", 0))
        if delta:
            units = max(1, abs(delta) // 120)
            target.yview_scroll(-units if delta > 0 else units, "units")
        return "break"

    widget.bind("<MouseWheel>", scroll)


def combobox_width(font, current, values, maximum):
    """Return ttk character units using the actual font's pixel measurements."""
    texts = (str(current),) if current else tuple(str(value) for value in values)
    longest = max((font.measure(text) for text in texts), default=0)
    width = ceil(longest / max(1, font.measure("0"))) + 1
    return max(1, min(maximum, max(4, width)))


class compact_combobox(ttk.Combobox):
    """Fit the selected text and resize when choices or the bound value change."""

    def __init__(self, parent, *, values=(), textvariable=None, max_width=26, **kwargs):
        self._compact_font = None
        self._max_width = min(max_width, int(kwargs.pop("width", max_width)))
        super().__init__(parent, values=tuple(values), textvariable=textvariable, **kwargs)
        self._compact_font = tkfont.Font(root=self, font=self.cget("font"))
        self._variable = textvariable
        self._trace = textvariable.trace_add("write", self._resize) if textvariable is not None else None
        self.bind("<<ComboboxSelected>>", self._resize, add="+")
        self.bind("<Destroy>", self._remove_trace, add="+")
        self._resize()

    def configure(self, cnf=None, **kwargs):
        result = super().configure(cnf, **kwargs)
        if self._compact_font is not None and (isinstance(cnf, dict) or kwargs):
            self._resize()
        return result

    config = configure

    def _resize(self, *_args):
        current = self._variable.get() if self._variable is not None else self.get()
        width = combobox_width(self._compact_font, current, self.cget("values"), self._max_width)
        super().configure(width=width)

    def _remove_trace(self, event):
        if event.widget is self and self._trace is not None:
            self._variable.trace_remove("write", self._trace)
            self._trace = None


def integer_spinbox(parent, *, textvariable, from_=0, to=2147483647, **kwargs):
    """Use ttk's built-in arrows and keyboard bindings for integer steps."""
    return ttk.Spinbox(
        parent, textvariable=textvariable, from_=from_, to=to, increment=1, **kwargs,
    )


def integer_seconds_spinbox(parent, *, textvariable, from_=1, to=60, **kwargs):
    """Integer-seconds spinbox: arrows step one second and decimals are rejected."""
    def accept(proposed: str) -> bool:
        return proposed == "" or proposed.isdigit()

    validate = (parent.register(accept), "%P")
    kwargs.setdefault("width", 10)
    return ttk.Spinbox(
        parent, textvariable=textvariable, from_=from_, to=to, increment=1,
        validate="key", validatecommand=validate, **kwargs,
    )
