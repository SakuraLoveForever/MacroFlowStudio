"""先按 docs/ssjj-navigation.md 录制同目录下的 ssjj_route.json，再由 Macro 执行。"""
from pathlib import Path

from macroflow.execution.ssjj_navigation import Route, follow_route


def main(ctx):
    route = Route.load(Path(__file__).with_name('ssjj_route.json'))
    ctx.log('5 秒后开始跑图，请切回游戏并站在路线起点')
    ctx.wait(seconds=5)
    follow_route(ctx, route)
