# 渐进式重构进度与基线

对应 `docs/agent-refactor-prompt.md`。本文只记录**实测数据**与**未验证项**；
目标是核对「哪些数字是量出来的、哪些还没量」。

## 测量环境

| 项目 | 值 |
| --- | --- |
| Python | 3.14.7 (MSC v.1944 64bit) |
| 操作系统 | Windows 11 (10.0.26200) |
| ttkbootstrap | 1.20.3 |
| opencv-python | 5.0.0.93 |
| numpy | 2.5.1 |
| mss | 10.2.0 |
| Pillow | 12.2.0 |
| PyInstaller | 6.20.0 |
| 分支 / 起点 | `master`，重构起点 commit `a7c2c21` |

固定数据集：`tests/helpers/core.py` 的 `build_actions()`——按固定周期混合
延时 / 按键 / 点击 / 引用模块 / 跳转 / 录制动作 / 引用脚本 / 注释 / 滚轮 / 文本，
跳转目标指向真实存在的另一行，每 7 行带 1 个失败代码段；模块被大量行重复引用。
工作流数据集用同文件 `build_workflow()`（普通步骤 + 全局模块同一集合）。

## 阶段 0 基线（重构前）

### 全量测试

```
python -m unittest discover -s tests -t .
Ran 1195 tests in 30.330s
OK
```

### 全新子进程导入耗时（各自独立进程，3 次采样取代表值）

| 模块 | 耗时 | 新增模块数 | 传递闭包里的重依赖 |
| --- | --- | --- | --- |
| `macroflow.core.models` | 21 ms | 28 | 无 |
| `macroflow.core.storage` | 44 ms | 53 | 无 |
| `macroflow.core.image_match` | 117 ms | 177 | cv2, mss, numpy |
| `macroflow.core.ocr` | 113 ms | 179 | cv2, mss, numpy |
| `macroflow.execution.player` | 144 ms | 223 | cv2, mss, numpy |
| `macroflow.ui.dialogs` | 380 ms | 364 | PIL, cv2, mss, numpy, pypinyin, tkinter, ttkbootstrap |
| `macroflow.ui.app` | 411 ms | 407 | + pystray |

### 发布产物体积（重构前）

| 路径 | 大小 |
| --- | --- |
| `dist/MacroFlowStudio.exe` | 80,616,047 B |
| `dist/paddle_ocr/` | 500,636,922 B |
| `MacroFlowStudio_latest_win64.zip` | 241,814,225 B |
| `build/` | 296,308,868 B |
| `dist/` 全部（含用户数据） | 约 763 MB |

`dist/` 里另有 `MacroFlowStudio_prev.exe`(81,393,392 B)、`logs/`(42 MB)、
`backups/`(21 MB)、`images/`(118 MB)、`scripts/`、`workflows/`、
`app_settings.json`、`myeasylog.log` 等用户数据——阶段 5 的分离依据。

### 编辑路径基线（FakeTree 端点）

| 数据规模 | 单行编辑（全量重建实现） | 单次重建插入行数 |
| --- | --- | --- |
| 1,000 | 17.4 ms（= 一次重建） | 4,000 |
| 10,000 | 789.6 ms（= 一次重建） | 40,000 |
| 20,000 | 3,324 ms（= 一次重建） | 100,000 |

重构前单行编辑走 `rebuild_action_tree()`，因此耗时就是一次全量重建；
且 `MAX_TREE_ROWS = 20,000` 之后的行**没有 iid**，20,000 条以上的动作无法选中。

## 已完成：脚本 / 工作流编辑切片

### 改动

- 新增 `src/macroflow/ui/app/script_edit.py`（纯业务，不碰 Tk）：
  `ActionRowIndex`（一次刷新读一次模块配置 + 动作 ID→行号）、
  `reconcile_action_rows()`（按动作 ID 算出最小行删/移/插）、
  `action_row_values()`（一行显示值）、`ActionEditHistory`（整表快照 + 行级记录）。
- `ScriptsMixin` 只负责把结果落到控件：`_sync_action_rows` 采用**局部更新**，
  `_set_action_row` 只写一行的单元格，`rebuild_action_tree` 仅用于打开 / 替换数据集。
- 撤销记录从「每次深拷贝整份动作列表」改为**整表浅拷贝 + 单行编辑只存那一行**。
- `summaries.action_summary()` 接受模块配置快照，不再逐行读盘。
- 列宽测量字体按 (family, size, DPI) 缓存；普通编辑不重新量整列。
- 删除 `MAX_TREE_ROWS` 截断：20,000 条以上全部可定位、可编辑、可执行。
- 工作流步骤的单元格编辑 / 启用切换 / 次数扣减只重画受影响的行，
  不再重建整张表与全局模块表。

### 实测（重构后）

| 数据规模 | 单行编辑 p50 | 单行编辑 p95 | 控件调用 | 单次重建对照 |
| --- | --- | --- | --- | --- |
| 1,000 | 0.01 ms | 0.02 ms | insert=0 delete=0 set=4 | 21.4 ms |
| 10,000 | 0.01 ms | 0.02 ms | insert=0 delete=0 set=4 | 1,063 ms |
| 20,000 | 0.02 ms | 0.02 ms | insert=0 delete=0 set=4 | 2,996 ms |
| 25,000 | 0.02 ms | 0.02 ms | insert=0 delete=0 set=4 | 4,866 ms |

端点：`FakeTree`；含业务修改 + 撤销记录 + 摘要计算 + 行更新调度。
**这不是真实 Tk 渲染性能**，真实窗口指标见「未验证项」。

复现：`python tests\test_edit_performance.py --benchmark`

### 回归

```
python -m unittest discover -s tests -t .
Ran 1210 tests in 39.312s
OK
```

## 未验证项（不得当作已完成）

- **真实 GUI 单行编辑 < 50ms、页签切换 / 按钮反馈 < 100ms**：未测量。
  FakeTree 不含 Tcl 调用与重绘，两者不可互相替代。
- **真实 GUI 冷启动 < 3 秒**：未测量。本任务不启动 GUI，也不得填造数字。
  源码入口内的计时不含 one-file 解包与解释器初始化，端到端冷启动需进程外计时。
- **视觉效果与窗口布局**：未验收。只做了布局参数与命令绑定的无 GUI 检查。
- **并发编辑期间的界面表现**、**大列表分批填充的交互手感**：未验证。
- 创建真实 Tk 窗口的测试（见下）在新约束下不应运行；本次改造**没有**把它们
  转成可运行状态，也没有把它们记为「已通过」。

## 测试安全边界盘点（阶段 0）

会创建真实 Tk 窗口的测试位置：

| 文件 | 行 | 说明 |
| --- | --- | --- |
| `tests/test_log_view.py` | 128, 169, 225 | `tk.Tk()` + `attributes("-alpha", 0.0)` |
| `tests/test_module_objects.py` | 149, 1125 | `tk.Tk()` / `Toplevel` + `withdraw()` |
| `tests/test_recognition.py` | 439, 455, 847, 1087, 1570, 1611 | `tk.Tk()` + `withdraw()` |
| `tests/test_recording.py` | 475 | `withdraw()` |
| `tests/test_script_editing.py` | 2069, 2771, 2836, 2896 | `tk.Tk()` / `Toplevel` |
| `tests/test_workflow.py` | 2262 | `tk.Tk()` + `withdraw()` |

这些测试目前在测试进程里创建（隐藏的）Tk 窗口。后续阶段会把能改为纯逻辑的
改为无 GUI 测试，确实需要真实控件的在创建窗口之前跳过并单独报告。
