"""在 VS Code 中编辑；在软件里打开本文件，或用“Py 逻辑”动作引用。"""


def main(ctx):
    for index in range(3):
        ctx.log(f"Python 逻辑运行成功：第 {index + 1} 次")
        ctx.wait(seconds=1)

    # for key in "2v3e6e4v4e":
    #     ctx.press(key)
    #     ctx.wait(ms=50)

    # if ctx.exists("目标.png"):
    #     ctx.run_module("你的模块名称")
