"""Elapsed-time gate followed by minute-spaced image checks and one execution."""
import copy
import threading
import time


class IntervalConditionPlugin:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.config = {'enabled': False, 'interval_ms': 3_600_000, 'condition': None, 'actions': [], 'trigger_on': 'success', 'recheck_ms': 60000}
        # armed 只是运行状态：是否启用由 config['enabled'] 表示（用户的偏好，会落盘）。
        # F12 停止只卸下 armed，绝不改写 config['enabled']，否则停止一次再保存设置，
        # 下次打开软件插件就变成未启用了。
        self.armed = False
        self.last_result = None
        self.generation = 0
        self.ready = self.running = False
        self.next_check = float('inf')

    def configure(self, config):
        if not isinstance(config, dict):
            raise ValueError('插件设置无效')
        value = copy.deepcopy({**self.config, **config})
        interval = value['interval_ms']
        if type(interval) is not int or not 1000 <= interval <= 31_536_000_000:
            raise ValueError('间隔须为 1 秒至 365 天')
        if value['trigger_on'] not in ('success', 'failure'):
            raise ValueError('请选择识别成功或失败时执行')
        if type(value['recheck_ms']) is not int or not 1000 <= value['recheck_ms'] <= 31_536_000_000:
            raise ValueError('间隔检测须为 1 秒至 365 天')
        if type(value['enabled']) is not bool or not isinstance(value['actions'], list):
            raise ValueError('插件设置无效')
        if value['condition'] is not None and not isinstance(value['condition'], dict):
            raise ValueError('识别条件无效')
        if any(not isinstance(action, dict) or not action.get('type') for action in value['actions']):
            raise ValueError('执行动作无效')
        with self.lock:
            self.config = value
            self.armed = bool(value['enabled'])
            self.generation += 1
            self.ready = self.running = False
            self.next_check = self.clock() + interval / 1000 if self.armed else float('inf')

    def arm(self):
        """执行开始时恢复被 F12 卸下的插件；仍在计时的插件保持原计时不动。"""
        with self.lock:
            if self.armed:
                return
            self.armed = bool(self.config['enabled'])
            if not self.armed:
                return
            self.generation += 1
            self.ready = self.running = False
            self.next_check = self.clock() + self.config['interval_ms'] / 1000

    def disable(self):
        """临时卸下运行状态（F12/退出）：保留 config['enabled'] 用户偏好。"""
        with self.lock:
            self.armed = False
            self.generation += 1
            self.ready = self.running = False
            self.next_check = float('inf')

    def _active(self):
        return bool(self.config['enabled']) and self.armed

    def check(self, matcher):
        with self.lock:
            now = self.clock()
            if not self._active() or not self.config['condition'] or not self.config['actions'] or self.ready or self.running or now < self.next_check:
                return
            generation = self.generation
            condition = copy.deepcopy(self.config['condition'])
            self.next_check = now + self.config['recheck_ms'] / 1000
            trigger_on = self.config['trigger_on']
        matched = bool(matcher(condition))
        with self.lock:
            if self.generation == generation and self._active():
                self.last_result = matched
            if (matched == (trigger_on == 'success')) and self.generation == generation and self._active():
                self.ready = True
                self.ready_at = self.clock()

    def claim(self):
        with self.lock:
            if not self._active() or not self.ready or self.running:
                return None
            if self.clock() - self.ready_at > min(60, self.config['recheck_ms'] / 1000):
                self.ready = False
                self.next_check = self.clock()
                return None
            self.ready, self.running = False, True
            return {'generation': self.generation, 'actions': copy.deepcopy(self.config['actions']), 'condition_result': self.last_result}

    def valid(self, generation):
        with self.lock:
            return self._active() and generation == self.generation

    def finish(self, generation, success):
        with self.lock:
            if generation != self.generation:
                return
            self.running = False
            self.next_check = self.clock() + (self.config['interval_ms'] / 1000 if success else self.config['recheck_ms'] / 1000)

    def status(self):
        with self.lock:
            if not self.config['enabled']:
                return '未启用'
            if not self.config['condition'] or not self.config['actions']:
                return '待配置'
            if not self.armed:
                return '已停止，下次执行时恢复'
            if self.running:
                return '正在执行'
            if self.ready:
                return '条件满足，等待执行'
            remaining = max(0, int(self.next_check - self.clock()))
            return f'下次检查 {remaining // 3600:02}:{remaining // 60 % 60:02}:{remaining % 60:02}'
