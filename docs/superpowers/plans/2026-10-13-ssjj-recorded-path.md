# SSJJ 实走通路与空间动作

依据 2026-10-13 审计和用户“请你进行修改”，在当前工作区连续实施；不启动界面、不构建、不混入既有改动。

## 设计与验收

- 录制保存 `trajectory`（单调 at_ms、XYZ），包括每个标记点；不从两个标记猜实际走法。缺少轨迹拒绝回放并提示重录，不做旧格式迁移。
- 点间沿原轨迹顺序前进，保留转弯和高差；进度只投影当前路段，不搜索后续相邻/交叉路线。起点仅允许近距离定位，未知远距离起点拒绝自动直冲。
- travel 的动作按原录制位置对应的弧长触发，释放动作保持同段按下到释放的时长；暂停/守卫/转向不耗动作时钟。stationary 和终点局部动作保持原顺序及间隔。
- 增加反向移动诊断，保留卡住停止、死亡/失焦/过期状态停止和双输入 watchdog。没有地图可达性证据时不实施随机侧移或跳跃。
- 后台验收：录制绕墙轨迹端到端回放；不同速度在相同空间位置按空格；重复坐标与交叉路线不跳段；坏数据在打开输入边界前拒绝；按键释放及暂停/守卫回归。

## 实施顺序

1. 新增失败测试：轨迹保留、绕墙、空间动作、旧格式拒绝。
2. 添加独立轨迹数据/进度计算，接入 RouteCapture 和 SegmentPlayback；复用 follow_route，添加受限的采样回调。
3. 加首点距离和方向诊断，更新旧测试中已改变的录制契约；补异常和安全回归。
4. 更新界面说明与当前文档，运行相关及全量后台测试，审查后仅提交本任务文件。

## GitHub 复用调研（2026-10-13）

| 项目 | 本次页面 Star | 许可/来源 | 可复用与代价 |
|---|---|---|---|
| [PythonRobotics](https://github.com/AtsushiSakai/PythonRobotics) | 约30.7k | [MIT](https://github.com/AtsushiSakai/PythonRobotics/blob/master/LICENSE) | 路径跟随样例；pure pursuit 采用车辆运动模型与前视目标，不能直接控制本游戏键鼠。仅参考顺序路径进度思想，整库集成成本高。 |
| [Recast Navigation](https://github.com/recastnavigation/recastnavigation) | 约8k | [Zlib](https://github.com/recastnavigation/recastnavigation/blob/main/License.txt) | 原生 C++ 网格构建/寻路，仍须验证游戏碰撞几何与角色通行参数；需原生编译与绑定，当前成本高。 |
| [MaaFramework](https://github.com/MaaXYZ/MaaFramework) | 约5k | 仓库标示 LGPL-3.0，许可正文请求失败，本轮未完整核实分发条款 | 图像识别自动化与流程，不能提供本游戏轨迹/空间触发。需接入其运行时与状态接口，当前成本高。 |

仓库原页及 PythonRobotics pure_pursuit.py、前两项许可正文已读取。GitHub API 返回403限流；三项最近实质维护、最新发布及归档状态未核实，不声称其特定日期版本已验证。本轮不分发候选代码或安装新包。已有 Python 控制器、math/bisect、psutil、ctypes 满足录制通路修正，选择局部实现。

## 实施记录

- 审计已有98项测试通过，但没有保留绕墙路径和空间动作测试。
- 本轮交付是实走路线跟随改进；自由探索未知地图、自动战斗与动态绕障需要额外的通路/敌我/可交互状态证据，不伪装成已实现能力。
- 已实现轨迹保存/验证与顺序进度、空间动作与独立释放时钟、停留点等待、按键重复去重、远起点拒绝、反向移动诊断及三次有界快照一致性检查。
- 独立复审复现了待释放键阻塞空间动作、停留时离开动作位置、重复 keydown 在释放后重新按住三项问题。各项均新增失败用例后修复并转绿；另覆盖击退后错过动作位置停止、终点未标记继续录制及标记回调延迟的时间倒退。
- 最终相关命令：`C:/Python313/python.exe -m unittest tests.test_ssjj_recorded_path tests.test_ssjj_waypoint_playback tests.test_ssjj_continuous tests.test_ssjj_status tests.test_ssjj_integration tests.test_ssjj_input_recording tests.test_ssjj_navigation`，128项通过；6个产品模块编译检查通过。修改后的后台复现脚本在速度300/600/1200时跳跃位置为306/306/312，目标位置300；未知直线障碍仍会明确停止并释放W。
- 全量 `python -m unittest discover -s tests -t .` 最近一次运行1801项，仅 `tests.test_ui_contract.PackageEntryTests.test_app_package_entry_does_not_aggregate_client_implementations` 失败，额外名称为 `disconnect_monitor`。只导入 `tests.test_sidebar_persistence` 再运行该检查也可复现，不加载跑图模块；本轮不修改已有UI契约或其工作区改动。之后新增的终点/边界/标记时间回归包含在上述128项相关测试中。
- 未启动软件或游戏界面、未发送真实输入、未构建或打包。保留所有既有未提交改动，只提交本任务文件。
