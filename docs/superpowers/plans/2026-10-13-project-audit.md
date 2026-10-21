# Project correctness and simplification plan

**Goal:** 检查项目功能实现，修复可复现错误，复用已有实现并保持原有功能。
**Architecture:** 保留现有模块边界；先用后台回归用例证明问题，再做最小修改。
**Tech Stack:** Python 3.13、unittest、现有 Windows/Tk 依赖。
**Spec:** 本次用户请求及项目根目录 AGENTS.md。

## Constraints

- 保留任务开始时的全部未提交内容；仅提交本次增量。
- 不创建或操作真实界面，不构建 exe、不打包、不推送。
- 修复与精简不引入依赖或新增产品功能。
- 使用 run.bat 指定的 Python 3.13 和 .deps；系统默认 Python 3.14 与现有二进制依赖不兼容。

## Review focus

- 存储写入中断、快速连续覆盖时，旧文件及历史版本仍完整。
- 更新回调异常及不同回调批处理时，后续状态和日志仍交付。
- OCR 区域越过截图边界时，仅识别真实交集。
- 分辨率设置失败时，不把错误刷新率当作设置成功。
- 脚本展开插入时，复制独立、内部跳转引用仍对应复制后的动作。

## Tasks

- [x] 记录工作区基线；检查源码清单、入口、核心执行/录制/识别/存储/工作流/执行端链路。
- [x] 执行全量后台基线测试；真实 Tk 窗口用例明确跳过。
- [x] 在 tests/test_project_audit.py 添加并运行失败回归用例。
- [x] 修复 src/macroflow/ui/update_queue.py 的回调合并及异常恢复。
- [x] 修复 src/macroflow/ui/app/guards.py 的 OCR 裁剪边界。
- [x] 修复 src/macroflow/execution/player/window.py 的刷新率成功判断。
- [x] 将 src/macroflow/core/storage.py 的主要 JSON 写入统一为同目录临时文件原子替换，并防止覆盖历史备份重名。
- [x] 在 src/macroflow/ui/app/scripts.py 复用 clone_actions_with_new_ids；清理已跟踪文件中的重复导入及截图异常包装。
- [x] 修复 tests/test_ui_contract.py 中依赖测试加载顺序的子模块白名单。
- [x] 运行相关测试、全量后台测试、编译与差异检查，记录审查结果并只提交本次增量。

## Initial evidence

- 自有 Python 文件：185 个（src/tests/tools），71763 行；应用源码 104 个模块。
- 正确解释器基线：1804 tests，1 failure，37 skipped。
- 基线失败：PackageEntryTests.test_app_package_entry_does_not_aggregate_client_implementations；disconnect_monitor 是合法子模块，静态白名单漏项。
- 安全测试入口在工作区外临时目录，拦截 tkinter.Tk.__init__ 并抛出 SkipTest；不创建真实窗口。

## Execution record

持续执行已由 AGENTS.md 授权。本次在现有工作区执行，以保留尚未提交的新运行时模块；不创建与用户当前源码脱节的干净副本。

最终结果：1817 tests，0 failures/errors，37 UI skips；新增 13 项回归测试，源码净减少 14 行。详细记录见 docs/project-audit-2026-10-13.md。

补充发现：零间隔文本缺少停止检查，以及播放器初始化/收尾异常后的状态和输入释放问题；均先观察回归失败，再完成最小修复。未跟踪 runtime/workflow.py 的重复导入清理已撤回，避免把既有未提交的新模块混入本次提交。
