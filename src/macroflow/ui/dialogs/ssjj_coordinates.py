"""Non-activating coordinate overlay and taught-route authoring."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
import ttkbootstrap as ttk

from macroflow.execution.ssjj_monitor import SSJJMonitor
from macroflow.execution.ssjj_navigation import Route
from macroflow.input.wininput import make_window_no_activate, show_window_no_activate
from .base import COLOR_SURFACE


class SSJJCoordinatePanel:
    def __init__(self, app):
        self.app = app
        self.monitor = SSJJMonitor()
        self.pending_action = None
        self.after_id = None
        self.closed = False
        window = self.window = tk.Toplevel(app.root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes('-topmost', True)
        window.configure(background=COLOR_SURFACE)
        window.geometry('+30+80')
        body = ttk.Frame(window, padding=12)
        body.pack(fill='both', expand=True)
        title = ttk.Label(body, text='SSJJ 实时游戏坐标 · 拖动移动', font=('Microsoft YaHei UI', 10, 'bold'))
        title.pack(anchor='w')
        title.bind('<ButtonPress-1>', self._drag_start)
        title.bind('<B1-Motion>', self._drag)
        self.coordinates = tk.StringVar(master=window, value='正在连接游戏…')
        ttk.Label(body, textvariable=self.coordinates, font=('Consolas', 11), justify='left').pack(
            fill='x', pady=8)
        self.state = tk.StringVar(master=window, value='')
        ttk.Label(body, textvariable=self.state, wraplength=350).pack(fill='x')
        ttk.Label(body, text='只读显示可在后台工作；自动跑图仍需游戏前台。',
                  wraplength=350).pack(fill='x', pady=(6, 8))
        row = ttk.Frame(body)
        row.pack(fill='x')
        self.start_button = ttk.Button(row, text='开始录制路线', command=self.start)
        self.start_button.pack(side='left', padx=(0, 6))
        self.save_button = ttk.Button(row, text='停止并插入脚本', command=self.save, state='disabled')
        self.save_button.pack(side='left')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(8, 0))
        ttk.Button(row, text='导入路线', command=self.load).pack(side='left', padx=(0, 6))
        ttk.Button(row, text='复制坐标', command=self.copy).pack(side='left', padx=(0, 6))
        ttk.Button(row, text='取消录制', command=self.cancel).pack(side='left', padx=(0, 6))
        ttk.Button(row, text='关闭', command=self.close).pack(side='left')
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.bind('<Destroy>', self._destroyed)
        window.update_idletasks()
        make_window_no_activate(window.winfo_id())
        window.deiconify()
        show_window_no_activate(window.winfo_id())
        self.poll()

    def _drag_start(self, event):
        self.drag_origin = (event.x_root, event.y_root, self.window.winfo_x(), self.window.winfo_y())

    def _drag(self, event):
        x, y, left, top = self.drag_origin
        self.window.geometry(f'{left + event.x_root - x:+d}{top + event.y_root - y:+d}')

    def _editor_ready(self):
        if getattr(self.app.recorder, 'running', False):
            raise RuntimeError('请先停止普通键鼠录制，避免与坐标路线重复')
        if self.app.player.running:
            raise RuntimeError('请先停止脚本执行，再录制或插入路线')
        thread = getattr(self.app, 'workflow_thread', None)
        if thread is not None and thread.is_alive():
            raise RuntimeError('请先停止工作流')
        path = getattr(self.app, 'script_path', None)
        if path and Path(path).suffix.lower() == '.py':
            raise RuntimeError('请新建或打开普通脚本，再插入坐标跑图动作')

    def start(self):
        try:
            self._editor_ready()
            if self.pending_action is not None:
                raise RuntimeError('请先插入或取消已录路线')
            self.monitor.start_recording()
            self.state.set('正在录制：切回游戏手动走路，结束时点击“停止并插入脚本”')
        except Exception as exc:
            self.state.set(str(exc))

    def save(self):
        try:
            self._editor_ready()
            if self.pending_action is None:
                self.pending_action = self.monitor.stop_recording()
            self.app._insert_action(self.pending_action)
            count = len(self.pending_action['points'])
            self.pending_action = None
            self.state.set(f'已插入坐标跑图动作（{count} 点），请保存脚本。')
        except Exception as exc:
            self.state.set(str(exc))

    def load(self):
        try:
            self._editor_ready()
            if self.monitor.status()[2] or self.pending_action is not None:
                raise RuntimeError('请先保存或取消正在录制的路线')
            path = filedialog.askopenfilename(parent=self.window, title='导入 SSJJ 路线',
                                             filetypes=[('路线 JSON', '*.json')])
            if path:
                route = Route.load(path)
                self.app._insert_action({'type': 'ssjj_route', 'map_id': route.map_id,
                                         'points': route.points, 'delay_ms': 0})
                self.state.set(f'已导入 {len(route.points)} 点，请保存脚本。')
        except Exception as exc:
            self.state.set(str(exc))

    def copy(self):
        value = self.monitor.status()[0]
        if value is None:
            self.state.set('当前没有有效坐标')
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(f'地图 {value.map_id}: {value.position}; yaw={value.yaw:.2f}')
        self.state.set('已复制当前坐标')

    def cancel(self):
        self.monitor.cancel_recording()
        self.pending_action = None
        self.state.set('已取消当前路线录制')

    def poll(self):
        if self.closed:
            return
        value, error, recording, count = self.monitor.status()
        if value is None:
            text = error or '等待新的有效坐标…'
        else:
            x, y, z = value.position
            text = (f'地图 {value.map_id}    朝向 {value.yaw:.2f}°\n'
                    f'X {x:10.2f}\nY {y:10.2f}\nZ {z:10.2f}\n'
                    f'{"死亡" if value.dead else "存活"} / {"落地" if value.on_ground else "空中"}')
        if recording:
            text += f'\n录制中：{count} 点'
        self.coordinates.set(text)
        self.start_button.configure(state='disabled' if recording or self.pending_action else 'normal')
        self.save_button.configure(state='normal' if recording or self.pending_action else 'disabled')
        self.after_id = self.window.after(100, self.poll)

    def close(self):
        if self.monitor.status()[2] or self.pending_action is not None:
            self.state.set('还有未保存路线，请先插入脚本或取消录制。')
            return
        self.window.destroy()

    def prepare_exit(self):
        if self.monitor.status()[2] or self.pending_action is not None:
            self.save()
        return not self.monitor.status()[2] and self.pending_action is None

    def _destroyed(self, event):
        if event.widget is not self.window or self.closed:
            return
        self.closed = True
        if self.after_id is not None:
            self.window.after_cancel(self.after_id)
            self.after_id = None
        self.monitor.close()
