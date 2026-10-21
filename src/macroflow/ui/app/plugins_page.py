"""Studio plugin configuration."""
import copy
from decimal import Decimal, InvalidOperation
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.scrolled import ScrolledFrame

from macroflow.core.storage import registered_module_object
from macroflow.ui.dialogs.base import ModalDialog
from macroflow.ui.dialogs.plugin_actions import ActionListEditor
from macroflow.ui.dialogs.segments import choose_module_binding, module_display_name
from .base import pad, px
from .disconnect_monitor import add_disconnect_monitor, get_disconnect_monitor, set_disconnect_monitor_enabled
from .constants import (COLOR_BG, COLOR_SURFACE, COLOR_HOVER, COLOR_BORDER,
                        COLOR_BLUE, COLOR_TEXT, COLOR_MUTED, COLOR_GREEN,
                        FONT_FAMILY, FONT_BODY)


INTERVAL_UNITS = {"h": 3600000, "min": 60000, "s": 1000}


def interval_ms(value, unit):
    try:
        number, multiplier = Decimal(str(value)), INTERVAL_UNITS[unit]
        if not number.is_finite() or not Decimal(1000) / multiplier <= number <= Decimal(31536000000) / multiplier:
            raise ValueError
        return int(number * multiplier)
    except (InvalidOperation, KeyError, ValueError, OverflowError):
        raise ValueError("间隔须为 1 秒至 365 天") from None


def interval_display(milliseconds):
    for unit, multiplier in INTERVAL_UNITS.items():
        if milliseconds % multiplier == 0:
            return str(milliseconds // multiplier), unit
    return str(Decimal(milliseconds) / 1000), "s"


def period_ms(hours, minutes):
    try:
        hours, minutes = Decimal(str(hours)), Decimal(str(minutes))
        if (not hours.is_finite() or not minutes.is_finite()
                or not 0 <= hours <= 8760 or not 0 <= minutes < 60):
            raise ValueError
        total = hours * 3600000 + minutes * 60000
        if not 1000 <= total <= 31536000000:
            raise ValueError
        return int(total.to_integral_value())
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError("周期须为 1 秒至 365 天，分钟须小于 60") from None


def period_display(milliseconds):
    hours, remaining = divmod(milliseconds, 3600000)
    minutes = Decimal(remaining) / 60000
    return str(hours), format(minutes.normalize(), 'f')


def recognition_condition(binding):
    key = str(binding.get("module_key") or "")
    module = registered_module_object(key)
    if not module or module.get("recognize", "image") not in ("image", "text") or module.get("category", "switch") not in ("recognition", "switch", "workflow_global", "script_global"):
        raise ValueError("请选择图片或文字识别模块。")
    return {**copy.deepcopy(binding), "type": "ocr_match" if module.get("recognize") == "text" else "image_match"}


class IntervalPluginForm:
    def __init__(self, parent, app, plugin_id):
        from .plugins_runtime import get_interval_plugins
        self.app, self.parent = app, parent
        self.plugin = get_interval_plugins(app)[plugin_id]
        self.draft = copy.deepcopy(self.plugin.config)
        self.name = tk.StringVar(master=app.root, value=self.draft["name"])
        self.enabled = tk.BooleanVar(master=app.root, value=self.draft["enabled"])
        hours, minutes = period_display(self.draft["interval_ms"])
        self.hours = tk.StringVar(master=app.root, value=hours)
        self.minutes = tk.StringVar(master=app.root, value=minutes)
        retry_number, retry_unit = interval_display(self.draft["recheck_ms"])
        self.recheck_number = tk.StringVar(master=app.root, value=retry_number)
        self.recheck_unit = tk.StringVar(master=app.root, value=retry_unit)
        self.trigger_on = tk.StringVar(master=app.root, value="成功" if self.draft['trigger_on'] == 'success' else "失败")
        self.condition_name = tk.StringVar(master=app.root)
        self.actions_name = tk.StringVar(master=app.root)
        self.status = tk.StringVar(master=app.root)
        self.job = None
        card = ttk.Frame(parent, padding=px(14))
        card.pack(fill="both", expand=True)
        heading = ttk.Frame(card)
        heading.pack(fill="x", pady=pad(0, 12))
        ttk.Label(heading, text="名称").pack(side="left")
        ttk.Entry(heading, textvariable=self.name).pack(side="left", fill="x", expand=True, padx=pad(10, 0))
        controls = ttk.Frame(card)
        controls.pack(fill="x")
        ttk.Checkbutton(controls, text="启用", variable=self.enabled, command=self.save).pack(side="left")
        ttk.Label(controls, text="周期").pack(side="left", padx=pad(18, 6))
        ttk.Entry(controls, textvariable=self.hours, width=5).pack(side="left")
        ttk.Label(controls, text="h").pack(side="left", padx=pad(4, 8))
        ttk.Entry(controls, textvariable=self.minutes, width=5).pack(side="left")
        ttk.Label(controls, text="min").pack(side="left", padx=pad(4, 0))
        save_button = ttk.Button(controls, text="保存", command=self.save, bootstyle="primary")
        save_button.pack(side="right")
        retry = ttk.Frame(card)
        retry.pack(fill="x", pady=pad(8, 0))
        ttk.Label(retry, text="间隔检测").pack(side="left")
        ttk.Entry(retry, textvariable=self.recheck_number, width=6).pack(side="left", padx=pad(6, 4))
        ttk.Combobox(retry, textvariable=self.recheck_unit, values=tuple(INTERVAL_UNITS), state="readonly", width=4).pack(side="left")
        ttk.Label(retry, text="识别").pack(side="left", padx=pad(12, 4))
        ttk.Combobox(retry, textvariable=self.trigger_on, values=("成功", "失败"), state="readonly", width=4).pack(side="left")
        ttk.Label(retry, text="时执行").pack(side="left", padx=pad(4, 0))
        condition = ttk.Frame(card)
        condition.pack(fill="x", pady=pad(12, 0))
        ttk.Label(condition, text="条件").pack(side="left")
        ttk.Label(condition, textvariable=self.condition_name, wraplength=px(280)).pack(side="left", padx=pad(10, 0))
        ttk.Button(condition, text="选择模块", command=self.select_condition,
                   style="CompactGhost.TButton").pack(side="right")
        actions = ttk.Frame(card)
        actions.pack(fill="x", pady=pad(12, 0))
        ttk.Label(actions, textvariable=self.actions_name).pack(side="left")
        ttk.Button(actions, text="编辑", command=self.edit_actions,
                   style="CompactGhost.TButton").pack(side="right")
        ttk.Label(card, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=pad(12, 0))
        self.refresh_labels()
        from macroflow.ui.save_state import SaveState
        self.save_state = SaveState(parent, save_button, self.save_values)
        self.save_state.watch(self.name, self.enabled, self.hours, self.minutes,
                              self.recheck_number, self.recheck_unit, self.trigger_on)
        parent.bind("<Destroy>", self.destroyed, add="+")
        self.refresh_status()

    def save_values(self):
        return (self.name.get().strip(), bool(self.enabled.get()), self.hours.get(),
                self.minutes.get(), self.recheck_number.get(), self.recheck_unit.get(),
                self.trigger_on.get(), self.draft)

    def refresh_labels(self):
        condition = self.draft.get("condition") or {}
        self.condition_name.set(module_display_name(str(condition.get("module_key") or "")))
        self.actions_name.set(f"代码块（{len(self.draft['actions'])} 行）")

    def select_condition(self):
        binding = choose_module_binding(self.parent, categories=("recognition", "switch", "workflow_global", "script_global"))
        if binding is None:
            return
        try:
            self.draft["condition"] = recognition_condition(binding)
        except ValueError as exc:
            self.app._notify("无法选择模块", str(exc))
            return
        self.refresh_labels()

    def edit_actions(self):
        actions = ActionListEditor(self.parent, self.app, self.draft["actions"]).show()
        if actions is not None:
            self.draft["actions"] = actions
            self.refresh_labels()

    def save(self):
        from .plugins_runtime import save_interval_plugin
        try:
            config = {**copy.deepcopy(self.draft), "name": self.name.get().strip(), "enabled": bool(self.enabled.get()),
                      "interval_ms": period_ms(self.hours.get(), self.minutes.get()),
                      "recheck_ms": interval_ms(self.recheck_number.get(), self.recheck_unit.get()),
                      "trigger_on": {"成功": "success", "失败": "failure"}[self.trigger_on.get()]}
            if config["enabled"] and config.get("condition") is not None:
                config["condition"] = recognition_condition(config["condition"])
            save_interval_plugin(self.app, config)
        except (ValueError, OSError) as exc:
            self.enabled.set(bool(self.plugin.config["enabled"]))
            self.app._notify("无法保存插件", str(exc))
            return
        self.draft = copy.deepcopy(self.plugin.config)
        self.status.set(self.plugin.status())
        state = self.__dict__.get('save_state')
        if state is not None:
            state.saved()

    def refresh_status(self):
        self.enabled.set(bool(self.plugin.config["enabled"]))
        self.status.set(self.plugin.status())
        self.job = self.parent.after(1000, self.refresh_status)

    def destroyed(self, event):
        if event.widget is self.parent and self.job is not None:
            self.parent.after_cancel(self.job)
            self.job = None


class PluginSettingsDialog(ModalDialog):
    def __init__(self, parent, app, plugin_id):
        from .plugins_runtime import get_interval_plugins
        title = "战斗端断线检测" if plugin_id == "disconnect" else get_interval_plugins(app)[plugin_id].config['name']
        super().__init__(parent, title, width=560, height=370, defer_show=True)
        if plugin_id != "disconnect":
            self.form = IntervalPluginForm(self, app, plugin_id)
            ttk.Button(self, text="删除插件", bootstyle="danger-outline",
                       command=lambda: self.delete_plugin(app, plugin_id)).pack(anchor="e", padx=px(14), pady=px(8))
        else:
            body = ttk.Frame(self, padding=px(14))
            body.pack(fill="both", expand=True)
            add_disconnect_monitor(body, app)
            ttk.Button(body, text="保存", command=app.save_sidebar_config,
                       style="CompactGhost.TButton", state='disabled').pack(anchor="e")

    def delete_plugin(self, app, plugin_id):
        from .plugins_runtime import delete_interval_plugin
        if not messagebox.askyesno("删除插件", "确定删除这个定时插件？", parent=self):
            return
        try:
            delete_interval_plugin(app, plugin_id)
        except OSError as exc:
            app._notify("无法删除插件", str(exc))
            return
        self.destroy()


class PluginCard(tk.Canvas):
    def __init__(self, parent, title, edit, toggle):
        super().__init__(parent, width=px(228), height=px(84), background=COLOR_BG, autostyle=False,
                         highlightthickness=0, borderwidth=0, takefocus=True, cursor="hand2")
        self.title, self.edit, self.toggle = title, edit, toggle
        self.detail, self.status, self.enabled = "", "未启用", False
        self.hover = False
        self.enabled_var = tk.BooleanVar(master=self, value=False)
        self.checkbox = ttk.Checkbutton(self, variable=self.enabled_var, style="Plugin.TCheckbutton",
                                        command=lambda: self.toggle(bool(self.enabled_var.get())))
        self.checkbox.place(x=px(12), y=px(12))
        self.checkbox.bind("<Button-3>", lambda _event: self.edit())
        self.bind("<Configure>", self._draw)
        self.bind("<Button-3>", lambda _event: self.edit())
        self.bind("<Return>", lambda _event: self.edit())
        self.bind("<space>", lambda _event: self.edit())
        self.bind("<Enter>", lambda _event: self._hover(True))
        self.bind("<Leave>", lambda _event: self._hover(False))
        self.bind("<FocusIn>", self._draw)
        self.bind("<FocusOut>", self._draw)

    def _hover(self, value):
        self.hover = value
        self._draw()

    def update_status(self, detail, status, enabled):
        state = (detail, status, enabled)
        if state != (self.detail, self.status, self.enabled):
            self.detail, self.status, self.enabled = state
            self.enabled_var.set(enabled)
            self.checkbox.configure(style="PluginEnabled.TCheckbutton" if enabled else "Plugin.TCheckbutton")
            self._draw()

    def _draw(self, _event=None):
        self.delete("all")
        width, radius, inset = self.winfo_width(), px(12), px(12)
        if width < 2:
            return
        # Use the actual themed checkbox width; its indicator changes with DPI.
        title_x = inset + self.checkbox.winfo_reqwidth() + px(8)
        title = self.create_text(title_x, inset, anchor="nw", text=self.title,
                                 width=max(1, width-title_x-inset), fill=COLOR_TEXT,
                                 font=(FONT_FAMILY, FONT_BODY))
        title_bottom = max(self.bbox(title)[3], inset + self.checkbox.winfo_reqheight())
        detail = self.create_text(inset, title_bottom+px(6), anchor="nw", text=self.detail,
                                 width=max(1, width-2*inset), fill=COLOR_MUTED,
                                 font=(FONT_FAMILY, FONT_BODY))
        status = self.create_text(inset, self.bbox(detail)[3]+px(3), anchor="nw", text=self.status,
                                 width=max(1, width-2*inset),
                                 fill=COLOR_GREEN if self.enabled else COLOR_MUTED,
                                 font=(FONT_FAMILY, FONT_BODY))
        height = self.bbox(status)[3] + inset
        if int(self.cget("height")) != height:
            self.configure(height=height)
        points = (radius, 1, width-radius, 1, width-1, 1, width-1, radius,
                  width-1, height-radius, width-1, height-1, width-radius, height-1,
                  radius, height-1, 1, height-1, 1, height-radius, 1, radius, 1, 1)
        background = self.create_polygon(points, smooth=True, splinesteps=20,
                                         fill=COLOR_HOVER if self.hover else ("#142C25" if self.enabled else COLOR_SURFACE),
                                         outline=COLOR_BLUE if self.focus_get() == self else (COLOR_GREEN if self.enabled else COLOR_BORDER),
                                         width=px(1))
        self.tag_lower(background)



class PluginsPage(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=px(16))
        from .plugins_runtime import get_interval_plugins
        self.app, self.plugins = app, get_interval_plugins(app)
        get_disconnect_monitor(app)
        ttk.Style().configure("Plugin.TCheckbutton", background=COLOR_SURFACE)
        ttk.Style().configure("PluginEnabled.TCheckbutton", background="#142C25")
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=pad(0, 10))
        ttk.Button(toolbar, text="新增定时插件", command=self.add_plugin,
                   style="CompactGhost.TButton").pack(side="left")
        self.grid_body = ScrolledFrame(self, autohide=True, padding=0, bootstyle="dark")
        self.grid_body.pack(fill="both", expand=True)
        self._columns, self.job, self.cards = 0, None, []
        self.grid_body.bind("<Configure>", self.relayout, add="+")
        self.bind("<Destroy>", self.destroyed, add="+")
        self.rebuild_cards()
        self.refresh_status()

    def rebuild_cards(self):
        for card in self.cards:
            card.destroy()
        self.card_ids = [*self.plugins, 'disconnect']
        self.cards = [PluginCard(self.grid_body,
                                "战斗端断线检测" if key == 'disconnect' else self.plugins[key].config['name'],
                                lambda key=key: self.edit(key),
                                lambda enabled, key=key: self.toggle_enabled(key, enabled))
                      for key in self.card_ids]
        for column in range(3):
            self.grid_body.columnconfigure(column, weight=0)
        self._columns = 0
        self.relayout()
        self.refresh_cards()

    def add_plugin(self):
        from .plugins_runtime import add_interval_plugin
        try:
            key = add_interval_plugin(self.app)
        except (ValueError, OSError) as exc:
            self.app._notify("无法新增插件", str(exc))
            return
        self.rebuild_cards()
        self.edit(key)

    def edit(self, plugin_id):
        PluginSettingsDialog(self.app.root, self.app, plugin_id).show()
        self.rebuild_cards()

    def toggle_enabled(self, plugin_id, enabled):
        from .plugins_runtime import save_interval_plugin
        try:
            if plugin_id == 'disconnect':
                set_disconnect_monitor_enabled(self.app, enabled)
                self.app.save_sidebar_config()
            else:
                save_interval_plugin(self.app, {**self.plugins[plugin_id].config, 'enabled': enabled})
        except (ValueError, OSError) as exc:
            self.app._notify("无法保存插件", str(exc))
        self.refresh_cards()

    def relayout(self, event=None):
        width = event.width if event is not None else self.grid_body.winfo_width()
        columns = max(1, min(3, (width-px(12)) // px(240)))
        if columns == self._columns:
            return
        for column in range(max(columns, self._columns)):
            self.grid_body.columnconfigure(column, weight=0)
        self._columns = columns
        for index, card in enumerate(self.cards):
            card.grid(row=index // columns, column=index % columns, sticky="nw",
                      padx=px(6), pady=px(6))

    def refresh_cards(self):
        for key, card in zip(self.card_ids, self.cards):
            if key == 'disconnect':
                enabled = bool(self.app.disconnect_monitor_enabled)
                card.update_status("战斗端连接", "已启用" if enabled else "未启用", enabled)
            else:
                plugin = self.plugins[key]
                title = plugin.config['name']
                if card.title != title:
                    card.title = title
                    card._draw()
                hours, minutes = period_display(plugin.config['interval_ms'])
                card.update_status(f"每 {hours} h {minutes} min", plugin.status(), plugin.config['enabled'])

    def refresh_status(self):
        self.refresh_cards()
        self.job = self.after(1000, self.refresh_status)

    def destroyed(self, event):
        if event.widget is self and self.job is not None:
            self.after_cancel(self.job)
            self.job = None


def build_plugins_page(parent, app):
    app.plugins_page = PluginsPage(parent, app)
    app.plugins_page.pack(fill="both", expand=True)
