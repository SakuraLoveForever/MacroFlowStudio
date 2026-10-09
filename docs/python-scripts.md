# Python 逻辑脚本

在 VS Code 中编辑 `.py` 文件，定义 `main(ctx)`。软件通过“Python 逻辑”动作调用它；也可直接打开 Python 文件，再保存为普通模块脚本，用于工作流和快捷键。每次执行都重新读取源文件。

```python
def main(ctx):
    for i in range(10):
        ctx.press("2")
        ctx.wait(ms=50)
        if ctx.exists("目标.png"):
            ctx.run_module("攻击模块")
        else:
            ctx.press("v")
        ctx.log(f"完成第 {i + 1} 轮")
```

接口：

- `ctx.press(key, hold_ms=30)`：按下并松开字母、数字、F1–F24、Enter、Space、Tab、Esc、方向键或整数 Windows 虚拟键码。
- `ctx.click(x, y, button="left")`：点击屏幕坐标，沿用播放器的坐标缩放。
- `ctx.wait(ms=0, seconds=0)`：可暂停和停止的等待，应使用它替代 `time.sleep`。
- `ctx.log(message)`：写入软件日志；`print` 也进入日志。
- `ctx.exists(template, region=None, threshold=0.85)`：图片是否匹配；图片相对路径按 Python 文件所在目录解析，区域为 `(x, y, w, h)`，省略时识别全屏。
- `ctx.read_text(region=None)`：OCR 返回文字。
- `ctx.run_module(key)`：按模块 ID 或唯一名称执行模块。
- `ctx.run_script(path, repeats=1)`：运行 JSON 或 Python 脚本，相对路径按 Python 文件所在目录解析。
- `ctx.action(action)`：执行现有模块脚本格式的一条动作，例如 `ctx.action({"type": "text", "text": "你好"})`。
- `ctx.checkpoint()`：主动响应暂停，通常无需调用。

代码独立进程执行，F12 停止会结束该进程，包括 Python 死循环。暂停时冻结 Python 语句执行；第三方库内部的阻塞操作在返回 Python 后响应暂停。键鼠、识别和模块操作由现有播放器统一处理，继续使用窗口绑定、暂停、停止与日志。错误显示文件名、行号和完整 Python 异常。

可使用普通 Python 函数、循环、类及同目录的辅助 `.py` 文件；运行目录为入口文件目录。软件使用打包内的 Python 和依赖，不自动使用 VS Code 选中的解释器；额外第三方包需加入软件运行环境后重新构建。

导出包会附带入口同目录的 `.py` 文件。图片、配置、子目录、被调用的 JSON 和模块需在入口顶层声明，导出时一起收集；模块 ID 在导入后自动映射，源码无需修改：

```python
MACROFLOW = {
    "files": ["目标.png", "next.json", "helpers"],
    "modules": ["攻击模块", "module:你的模块ID"],
}
```

`files` 中的文件或目录必须位于入口源码目录内，按相对目录结构保存。Python 运行可访问本机其他文件；导出仅收集明确声明的资源，动态生成的路径也需通过此列表提供。模块名称需唯一。

## 实现与验证

- Python 文件加载为单个 `python_script` 动作，保存为 JSON 引用，避免覆盖源代码。
- 使用标准库 multiprocessing 的 spawn 子进程与 Pipe RPC；子进程执行业务逻辑，父播放器执行动作；暂停使用共享 Event，停止终止并回收进程。
- 先验证文件加载、RPC 动作/返回值、源文件热更新、错误行号、死循环停止和暂停，再验证模块脚本/工作流/快捷键接入；后台测试、构建与静态校验，不启动界面。
