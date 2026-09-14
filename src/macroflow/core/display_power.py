"""执行期间阻止屏保 / 熄屏 / 自动睡眠。

等待识图只截屏、不发任何输入，用户此时也没有操作，空闲一旦计满屏保时间，
屏保就会接管显示；此后 BitBlt 对普通进程返回「拒绝访问」，脚本里的识图、
识别文字会成片失败（表现为「执行着执行着突然全都识别不到」）。

这里用 Windows 自己的 SetThreadExecutionState：只要线程活着并持有
ES_DISPLAY_REQUIRED，系统就持续重置显示空闲计时，屏保不启动、显示器不熄、
系统也不睡（ES_SYSTEM_REQUIRED）。这是播放器「看视频时不要黑屏」用的同一
套声明，进程崩溃或线程结束由系统自动撤销，不需要也不应该去改用户自己的
屏保 / 电源设置。
"""
from __future__ import annotations

import ctypes

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


def keep_display_awake() -> bool:
    """声明「本线程执行期间需要显示器保持点亮」，成功返回 True。

    声明挂在线程上：由执行脚本的播放线程调用，线程结束即自动失效；
    长驻线程请在本轮执行结束时配对调用 allow_display_sleep。
    """
    return _set_thread_execution_state(
        ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)


def allow_display_sleep() -> bool:
    """撤销 keep_display_awake，恢复系统默认的熄屏 / 屏保行为。"""
    return _set_thread_execution_state(ES_CONTINUOUS)


def _set_thread_execution_state(flags: int) -> bool:
    try:
        return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))
    except Exception:
        # 非 Windows 或 API 不可用时退化成「不阻止屏保」，绝不能让执行路径崩掉。
        return False
