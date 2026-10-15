# 开发指南 / Development guide

[项目首页](../README.md) · [English overview](../README_EN.md)

## 源码运行 / Run from source

Windows，Python 3.13。在项目根目录执行 / Run in the project root:

```powershell
python -m pip install -r requirements.txt
C:\Python313\python.exe -m pip install --target .deps --no-deps -r requirements-ocr.txt
.\run.bat
```

日常使用 `run.bat`，重启即可加载最新源码。它设置 `.deps` 与 `src` 的模块搜索路径；离线 OCR 使用本地模型，首次识别时按需加载。

Use `run.bat` for daily use; restarting loads the current source. The launcher adds `.deps` and `src` to the module search path. Offline OCR loads local models on demand.

## 后台验证 / Background verification

按功能运行相关测试，必要时运行全量测试。所有验证通过后台命令完成，不启动或操作软件界面。

Run tests for the affected area, or the full suite when needed. Verify through background commands without launching or operating the application UI.

```powershell
python -m unittest tests.test_workflow
python -m unittest discover -s tests -t .
```

## 构建与打包 / Build and package

仅在明确需要 EXE 时运行 `build.ps1`，并完成全量测试与 `verify_build.py` 静态校验。需要 ZIP 时，先成功构建，再运行 `pack.ps1`。日常源码修改不需要构建。

Run `build.ps1` only when an EXE is explicitly requested, then complete the full tests and `verify_build.py` static checks. For a ZIP, build successfully before running `pack.ps1`. Source changes alone do not require a build.

## 目录与协作 / Layout and collaboration

| 位置 / Path | 用途 / Purpose |
| --- | --- |
| `src/macroflow/core/` | 数据模型与存储 / Models and storage |
| `src/macroflow/input/` | 键鼠输入 / Keyboard and mouse input |
| `src/macroflow/execution/` | 回放与识别执行 / Playback and recognition |
| `src/macroflow/ui/` | 界面与执行控制 / UI and execution control |
| `tests/` | 后台测试 / Background tests |
| `docs/` | 使用与开发文档 / User and developer documentation |

运行数据与构建目录见 [项目目录](project-layout.md)，完整协作要求以 [AGENTS.md](../AGENTS.md) 为准。

See [Project layout (中文)](project-layout.md) for runtime data and build directories. [AGENTS.md](../AGENTS.md) defines the contributor rules.
