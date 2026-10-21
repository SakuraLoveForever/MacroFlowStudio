"""Enable a save control only while its form differs from saved content."""
import copy
import tkinter as tk


_CURRENT = object()


class SaveState:
    def __init__(self, owner, button, snapshot, *, baseline=_CURRENT, new=False):
        self.owner, self.button, self.snapshot = owner, button, snapshot
        self.baseline = copy.deepcopy(snapshot() if baseline is _CURRENT else baseline)
        self.new = new
        self.after_id = None
        self.closed = False
        self.state = None
        self.traces = []
        owner.bind('<Destroy>', self.destroyed, add='+')
        self.watch(*(value for value in vars(owner).values() if isinstance(value, tk.Variable)))
        self.tick()

    def watch(self, *variables):
        for variable in variables:
            token = variable.trace_add('write', lambda *_: self.refresh())
            self.traces.append((variable, token))

    def refresh(self):
        if self.closed:
            return
        try:
            try:
                changed = self.new or self.snapshot() != self.baseline
            except (TypeError, ValueError, tk.TclError):
                changed = True
            state = 'normal' if changed else 'disabled'
            if state != self.state:
                self.button.configure(state=state)
                self.state = state
        except tk.TclError:
            self.close()

    def saved(self, value=_CURRENT):
        self.baseline = copy.deepcopy(self.snapshot() if value is _CURRENT else value)
        self.new = False
        self.refresh()

    def tick(self):
        self.after_id = None
        self.refresh()
        if not self.closed:
            self.after_id = self.owner.after(100, self.tick)

    def close(self):
        self.closed = True
        for variable, token in self.traces:
            variable.trace_remove('write', token)
        self.traces.clear()
        if self.after_id is not None:
            self.owner.after_cancel(self.after_id)
            self.after_id = None

    def destroyed(self, event):
        if event.widget is self.owner:
            self.close()
