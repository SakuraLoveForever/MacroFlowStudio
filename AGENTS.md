# AGENTS.md

## Engineering principles

- Do not preserve backward compatibility. Remove obsolete paths instead of adding compatibility layers, fallbacks, or migrations.
- Choose the simplest implementation that fully meets the current requirements. Avoid speculative abstractions, configuration, and indirection.
- Grow the system in layers. Start from the smallest version that works end to end, and add each new capability on top of a product that already works. Never trade a working product for unfinished complexity.
- Keep components modular and concerns clearly separated.
- Prefer established, well-maintained libraries when they reduce overall complexity or improve reliability. Do not reimplement common functionality without a clear reason.
- Lean on the dependencies already in the project before writing your own implementation or adding packages. Do not assume a library lacks a capability without checking its documentation and types.
- Make architectural decisions for the long term. Do not accept a stopgap that only works for now and is meant to be replaced later.

## 开源复用规则

- 每次新增功能前先检索 GitHub 上高相关、高 Star 的开源项目，结合近期维护、许可证、技术栈和部署/集成成本筛选；优先直接集成成熟项目或复用已有依赖，确认不适合后再自行实现，并简要说明取舍。

## 执行授权规则

- 用户提交任务后，默认授予 Codex 在当前任务范围内连续执行的授权，包括自行选择实现路径、修改文件、运行命令和完成必要验证；不应为每个步骤、命令或中间结果单独请求审批。
- Codex 应自行判断普通技术取舍并持续推进，直到任务完成或确实受阻。仅当操作会明显超出任务范围、产生无法逆转的重大外部影响、缺少用户必须做出的关键决策，或无法安全推断用户意图时，才暂停并询问。
- 构建、测试、打包和安全检查规则是执行约束，不代表需要等待用户逐项确认；Codex 应自行完成这些步骤并报告结果。

## Git 提交与交付规则

- 每次完成修改并通过所需验证后，提交当前任务的改动并 push 到远程仓库；不得只修改本地而不推送。
- 每次提交附带 summary，说明本次修改内容及验证结果；交付回复包含提交标识、推送结果和简短修改摘要。
- 不将与当前任务无关的已有未提交改动混入提交；推送失败时保留本地提交并报告具体原因，不强制推送。

## 构建与打包规则

- **默认使用 BAT 启动源码**：日常开发、修改和交付均以项目根目录的 `run.bat` 为运行入口，用户重启后即可使用最新源码。
- **只有用户明确要求才构建或打包**：用户未要求生成或更新 exe、构建发布产物或打包时，不执行 `.\build.ps1`、`.\pack.ps1` 或 `verify_build.py`；功能完成、重构收尾和交付验证本身不触发构建。不得把已有 exe 或 ZIP 当作最新源码产物交付。
- **后台验证仍需完成**：根据改动范围和风险运行相关单元测试、编译检查；必要时执行全量测试（`python -m unittest discover -s tests -t .`），不以构建 exe 代替源码验证，也不为验证而启动软件界面。
- 用户明确要求生成或更新 exe 时，执行 `.\build.ps1`，完成全量测试与 `verify_build.py` 静态校验；只有用户明确要求 ZIP 打包时才执行 `.\pack.ps1`。
- 任何执行 `.\pack.ps1` 的打包流程，必须先执行并成功完成 `.\build.ps1`，确认 dist 中 exe 与外置 OCR 组件来自最新源码后，才能压缩 zip。

## 模型测试规则

- 如果当前模型是 DeepSeek，不要执行冒烟测试，也不要为了冒烟测试启动应用或打包产物。
- 一律不执行可见界面检查，不得为了检查而操作、点击、截图或目视验证应用界面。
- 一律不得启动软件界面进行测试；所有测试必须通过后台命令完成，例如单元测试、编译检查、打包和产物静态校验。
- 如果当前模型是 ChatGPT，可根据任务需要执行不涉及可见界面操作的单元测试、编译检查、打包和产物静态校验。
- 除上述差异外，仍应按用户要求和改动风险选择必要的非冒烟验证。
- 全量测试命令：`python -m unittest discover -s tests -t .`（按功能拆分的多个测试文件）；只跑相关范围用 `python -m unittest tests.test_<功能>`。
