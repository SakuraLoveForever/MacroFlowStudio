<div align="center">

# MacroFlow Studio

**把重复操作，交给脚本。**

Windows 键鼠录制与自动化工具，用识图和离线 OCR 判断状态，用工作流串联任务。

[快速开始](#快速开始) · [使用指南](docs/user-guide.md) · [发布下载](https://github.com/SakuraLoveForever/MacroFlowStudio-/releases) · [English](README_EN.md)

![Windows 10 / 11](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6?style=flat-square)
![Offline OCR](https://img.shields.io/badge/OCR-Offline-4B8BBE?style=flat-square)

</div>

![MacroFlow Studio 工作流界面](docs/screenshots/macroflow-studio-preview.png)

## 从录制到自动执行

**录下操作 → 编辑脚本 → 添加判断 → 组织工作流**

| 能力 | 你可以做什么 |
| --- | --- |
| 录制与回放 | 记录键盘、鼠标和游戏相对转向，按次数重复执行 |
| 脚本编辑 | 组合点击、输入、延时和跳转，引用已有脚本或 Python 逻辑 |
| 识图与 OCR | 根据图片、文字或数字选择下一步，离线完成识别 |
| 模块复用 | 保存识别条件与处理动作，在多个脚本中复用 |
| 工作流 | 按顺序编排脚本，配置次数、等待和全局检测 |

## 快速开始

**已有源码？** 安装依赖后，双击根目录的 `run.bat`，即可使用当前源码。首次配置见下方说明。

**使用发布版？** 前往 [Releases](https://github.com/SakuraLoveForever/MacroFlowStudio-/releases) 下载，完整解压后运行 `MacroFlowStudio.exe`。保留随包提供的运行库与 `rapidocr_ocr/`，迁移时复制整个文件夹。

1. **选择窗口**：绑定要操作的目标软件。
2. **录制操作**：按 `F8` 开始／结束录制，保存脚本。
3. **运行脚本**：按 `F9` 回放；执行期间按 `F8` 暂停／继续。
4. **串联任务**：在工作流页添加脚本，设置次数，按 `F10` 运行。

> **随时停止：`F12`。** 隐藏到托盘可继续运行；关闭窗口会退出软件。

## 按需深入

| 你想了解 | 文档 |
| --- | --- |
| 编辑动作、设置识别、复用模块、编排工作流 | [使用指南](docs/user-guide.md) |
| 用 `main(ctx)` 编写 Python 逻辑 | [Python 脚本接口与示例](docs/python-scripts.md) |
| 配置开发环境、运行测试、了解构建入口 | [开发指南](docs/development.md) |
| 查找源码与数据所在位置 | [项目目录](docs/project-layout.md) |
| 查看版本变化 | [更新历史](CHANGELOG.md) |

<details>
<summary><strong>首次从源码运行</strong> · Windows / Python 3.13</summary>

在项目根目录安装基础依赖和离线 OCR 依赖：

```powershell
python -m pip install -r requirements.txt
C:\Python313\python.exe -m pip install --target .deps --no-deps -r requirements-ocr.txt
.\run.bat
```

`run.bat` 会加载 `.deps` 与 `src`。脚本、工作流、设置和日志保存在项目根目录；OCR 首次使用时加载本地模型。

</details>
