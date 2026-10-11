# SSJJ 键鼠路线录制与回放

> 执行：使用 executing-plans，在当前已授权任务内逐项实现并验证。

**目标：** 按用户选择，原样回放键鼠及时间间隔，使用同期坐标检查偏差。

**设计：** 游戏坐标面板复用 MacroRecorder 的相对鼠标、键盘、按键、滚轮监听。
新增 `ssjj_recording` 动作，包含 map_id、duration_ms、inputs、samples。
inputs 保留 recorded_at_ms，samples 保存 at_ms、position、yaw；两者共用 perf_counter 时间轴。
每个样本含静止状态，不按距离丢弃等待和停留。旧坐标导航仍是独立的 ssjj_route 能力，不把旧路线伪装成键鼠录制。

回放发送原始输入，不另行校准/转向/前进。起点坐标偏差不得超过 150、朝向偏差不得超过 15°。
沿录制时间插值检查坐标，偏差超过 150 停止；该容差包含采样和游戏反馈延迟。
失焦、切图、死亡、快照冻结和读取错误停止。暂停/守卫先释放输入并暂停时间轴，恢复时重建应按住的键鼠。
输入监控线程负责停止/失焦/暂停和阻塞时释放，避免连续按键遗留。
F8 停止并插入；普通录制和路线录制互斥。面板范围内的点击和滚轮不录入；
游戏前台且光标居中时才接收原始相对移动，避免将桌面光标移动误录成游戏转向。

**文件：** 新增 execution/ssjj_recording.py（校验、回放），扩展 ssjj_monitor.py（时间轴采集/录制器生命周期），
player/core.py（动作分发）、ssjj_coordinates.py（状态及控制）、app/recording.py（互斥/F8）、
app/execution.py（阻止边录边执行、F12 停止）、summaries.py（摘要）。

**约束：** 无新依赖；只做后台测试，不启动应用或操作界面；不构建/打包；只提交本任务改动，不推送。

## 实施与验证

- [x] 先补失败测试：坐标+键鼠同轴采集、静止录制、保存加载、原输入发送及顺序、偏差停止、错误释放。
- [x] 实现输入/坐标动作模型与检查、采集器、监听生命周期和回放安全边界。
- [x] 接入面板、F8、播放器、摘要；后台测试面板替身，不创建窗口。
- [x] 运行相关测试、编译检查及全量测试，报告既有失败；审查只提交本任务差异。

## 开源复用核验（2026-10-11）

GitHub API 本次核对；均未归档。维护日期来自最近提交，最近实质代码维护另列。

| 候选 | Star | 最新提交 / 发布 | 许可证 | 能力和接入成本 |
|---|---:|---|---|---|
| [pynput](https://github.com/moses-palmer/pynput) | 2,174 | 2026-05-12，Release 1.8.2 提交；GitHub 最新 Release 未核实 | [LGPL-3.0](https://github.com/moses-palmer/pynput/blob/master/COPYING.LGPL) | 已有依赖，键鼠监听已有 MacroRecorder 封装，复用成本低；原始相对位移沿用现有 RawMouseListener，不复制第三方源码。分发仍遵循现有依赖许可。最近实质代码维护日期未核实。 |
| [keyboard](https://github.com/boppreh/keyboard) | 3,972 | 2023-01-31 README 提交；v0.13.5 / 2020-03-23 | [MIT](https://github.com/boppreh/keyboard/blob/master/LICENSE.txt) | 能记录/回放键盘，不能单独覆盖鼠标和游戏坐标；还需第二套输入生命周期，改造成本中，不采用。最近实质代码维护日期未核实。 |
| [Airtest](https://github.com/AirtestProject/Airtest) | 9,597 | 2025-12-04 v1.4.3 合并与发布 | [Apache-2.0](https://github.com/AirtestProject/Airtest/blob/master/LICENSE) | 完整 UI 自动化，Windows 输入依赖 pywinauto；不能直接提供当前游戏状态。引入完整框架成本高，不采用。最近独立实质代码提交未核实。 |

能力依据：[pynput 监听文档](https://github.com/moses-palmer/pynput/blob/master/docs/mouse-usage.rst)、
[keyboard 录制 API](https://github.com/boppreh/keyboard/blob/master/README.md)、
[Airtest Windows 实现](https://github.com/AirtestProject/Airtest/blob/master/airtest/core/win/win.py)。
推荐只复用项目现有录制器、Win32 输入和标准库；成本集中在共同时钟、坐标偏差检查、停止释放与持久化验证。

## 执行记录

- 回归测试先失败，再实现。审查发现普通守卫识别耗时被错误排除、工具栏录制遗留回调、录制期间可执行脚本的问题，均先复现后修复。
- 只在输入已经释放的暂停/守卫处理段内暂停时间轴；普通识别开销保持在录制时序内。
- 坐标导航 CLI 是独立能力，保留纯坐标语义；新键鼠录制不读取旧格式、不迁移旧数据。
- 选择当前工作区执行以保留用户在此工作区已有的运行能力；为修改前文件保存临时副本，提交时只提取本任务差异。
- 验证：260 项相关测试通过；全量 1709 项仅既有 `test_app_package_entry_does_not_aggregate_client_implementations` 失败（`disconnect_monitor` 导出）。修改文件编译检查通过。
- 将暂存区独立导出后，70 项 SSJJ 测试通过，确认提交不依赖工作区其他未提交改动；复审确认三个重要问题已修复，无新增重要发现。

## 同日实际录制反馈修复

- 用户报告转向但不走。只读检查实际草稿与日志：23.62 秒路线包含 251 条键盘、579 条鼠标移动、2 条按钮事件，键鼠并未缺录；两次回放约两秒时偏差停止。未启动界面或发送实际输入。
- 开头 0.17–0.83 秒横向原始位移累计 1198，游戏快照朝向到 1.26 秒仍未改变；W 在 1.514 秒按下，录制时 1.6 秒后确实移动。非激活浮窗使游戏仍是前台，前台过滤不足以排除恢复游戏控制之前的桌面鼠标移动。
- 原始实体鼠标移动复用已有窗口光标居中检测；键盘和松开事件不依赖光标居中。缓冲的注入转向可能在退出视角控制后才刷出，仍保留其原始时序和位移，不按刷出时的光标位置丢弃。增加分类计数与偏差时刻/坐标/发送进度日志，不放宽偏差阈值。
- 回归先复现桌面移动被录入，再修复；262 项相关后台测试通过。修复后实际游戏效果尚未验证，不能把键鼠已录入或单元测试通过视为实机路线成功。
