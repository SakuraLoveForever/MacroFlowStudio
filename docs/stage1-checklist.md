# 阶段 1 执行清单（包入口与测试减耦）

状态：进行中。当前已提交到 `fa7e302`（阶段 0/2 脚本编辑切片）。

## 1. 包入口减重

- [x] `src/macroflow/ui/app/main.py`：`MacroFlowApp` 的组合定义移出包入口
- [ ] `src/macroflow/ui/app/__init__.py`：只转出 `MacroFlowApp`（去掉 ~150 个兼容重导出）
- [x] `startup.main()` 改为 `from .main import MacroFlowApp`
- [ ] `src/macroflow/ui/dialogs/__init__.py`：去掉兼容重导出
- [ ] `src/macroflow/execution/player/__init__.py`：去掉兼容重导出
- [ ] 更新 `verify_build.py` 的符号定位（包入口不再聚合，符号要在真实模块里找）

## 2. 调用方改从真实模块导入

- [x] `src/macroflow/ui/app/*.py` 的 dialogs 导入已指向 `dialogs.<功能>` 子模块
- [ ] `tests/*.py` 去掉 `from tests.common import *`
- [ ] `tests/common.py` 只保留导入路径配置与共用替身

## 3. 局部 patch 取代全模块扫描

- [ ] 删除 `_PackagePatch` / `_PACKAGE_SUBMODULES` / `patch_app` / `patch_dialogs` / `patch_player`
- [ ] `tests/helpers/patches.py`：显式的 名字 → 实现模块 表（只 patch 真的会调用的模块）
- [ ] 重写 781 处 `patch_*("名字")` 调用点
- [ ] 直接 `patch("macroflow.ui.app.<名字>")` 的写法改到真实模块

## 4. 依赖方向静态检查

- [x] `tests/test_import_boundaries.py`：干净子进程检查传递导入闭包
- [ ] 在 `test_module_integrity.py` 增加依赖方向检查（解析 Import/ImportFrom）
- [ ] 证明每个新增边界检查都能抓住一段有意违规的最小示例

## 5. 收尾

- [ ] 全量测试
- [ ] `.\build.ps1`
- [ ] `python verify_build.py`
- [ ] 更新 `docs/refactor-progress.md`
