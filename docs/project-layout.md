# 项目目录与运行入口

日常使用根目录的 `run.bat`，它直接运行当前源码。只有明确要求生成 EXE 或 ZIP 时才运行构建、打包脚本。

| 位置 | 用途 |
| --- | --- |
| `src/macroflow/` | 应用源码 |
| `scripts/` | 当前使用的用户脚本 JSON |
| `images/` | 当前使用的识别图片 |
| `workflows/` | 当前使用的工作流 JSON |
| 根目录的配置 JSON | 当前使用的本地设置、模块和识别区域 |
| `tools/` | 开发工具，包括回放计时基准、OCR 构建工具和依赖清单 |
| `tools/local/` | 不提交的个人工具 |
| `docs/assets/` | 文档展示图片和示例页面 |
| `docs/local/` | 不提交的个人文档 |
| `tests/` | 后台自动化测试 |
| `.deps/` | BAT 运行所需的本地依赖，保留 |
| `logs/`、`backups/` | 应用运行时生成的日志和备份 |
| `build/`、`dist/` | 明确要求构建时才生成的发布产物 |

2026-10-10 整理时，旧构建目录、旧分发目录和历史备份移到了项目外相邻的
`../Macro_archive/project-cleanup-20261010_031054/`。归档中的 `plan.json` 记录原位置与新位置，
`manifest.json` 记录移动文件的大小和 SHA-256；清理报告记录已删除的旧 ZIP。
本地仅保留一个旧 Studio 发布 ZIP，存放于归档的 `old-builds/`，它不代表当前源码版本。

根目录的用户脚本、识别图片、工作流和配置没有搬动。归档里的同名文件是历史副本，
其中部分脚本内容不同，因此保留供人工查阅。BAT 始终使用项目根目录的数据。
不要从归档启动旧 EXE，否则会使用 EXE 所在目录的数据，并再次产生另一套文件。

开发计时工具现在使用：

```powershell
python tools/benchmark_playback_timing.py --durations 10,60,600 --events 1000 --json
```

Python 运行和测试可能生成 `__pycache__/`，这些是自动缓存，已被 Git 忽略。
