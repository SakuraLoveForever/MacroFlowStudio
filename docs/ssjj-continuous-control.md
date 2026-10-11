# 连续跑图控制

2026-10-11。按用户确认的五项要求，在已有本人坐标读取、录制路线和原生 `ssjj_route` 动作上改造：直路连续移动、边走边纠偏、推进已通过的中间点、终点精确收敛、根据游戏状态更新控制。

## 实现与限制

- 控制器和输入生命周期统一由播放器进程持有。Python 的 `follow_route(ctx, route)` 通过现有 `ctx.action` 执行原生路线，不在子进程重复读取游戏或逐次请求键鼠输入。
- 新鲜状态续期前进按键；同一状态时间不重复转向。10ms 轮询只用于等待状态和检查停止条件，替代旧的每次输入后固定等待 150ms。
- 小角度边走边转；大角度先释放 W。只允许推进当前已通过的直路段，结合横向偏离与高度判断，不根据全局最近点跳过弯路。较大的拐角保持单独检查点。
- 直路点、拐角、终点分别使用 80、25、20 单位容差。接近拐角或终点，使用采样间实际移动时间和位移估计速度，缩短前进授权；即使授权先于状态更新结束，仍保留这段移动时间用于速度估计。终点释放按键后，用新的状态确认。
- 独立输入监控线程每约 10ms 检查暂停、停止、失焦和 120ms 授权期限；识别或守卫回调阻塞时也能释放 W。失焦错误锁存，输入锁内再次检查，避免已释放按键被并发重新按下。正常采样、错误、结束和守卫分支均释放输入并关闭资源。
- 保留切图、死亡、起点距离、落地、快照冻结、传送、无进展和单点超时检查。没有新增依赖。

本次仍跟随实走录制路线，不生成可通行区域或自动绕障碍，不提供失焦/最小化输入。没有启动应用界面或游戏执行实机跑图；20 单位是算法成功判定阈值，不能等同于所有地图和客户端下已实测的定位精度。

## GitHub 开源复用调研

查询日期：2026-10-11。仓库 API 核对 Star、归档状态、最近提交和 Release API；直接读取许可证及相关实现。只参考控制思路，没有复制第三方代码或分发其组件。

| 项目 | Star / 归档 | 最近实质代码维护 / 最新 Release | 许可 | 可复用部分 | 接入、缺口与成本 |
|---|---|---|---|---|---|
| [PythonRobotics](https://github.com/AtsushiSakai/PythonRobotics) | 30,664 / 否 | 2026-10-04 / API 无 Release | [MIT](https://github.com/AtsushiSakai/PythonRobotics/blob/master/LICENSE) | [Pure Pursuit](https://github.com/AtsushiSakai/PythonRobotics/blob/master/PathTracking/pure_pursuit/pure_pursuit.py) 的路径推进和状态反馈思路 | Python 示例使用 NumPy/Matplotlib 和车辆运动模型，不能直接驱动 SSJJ；直接移植需适配运动模型、输入与暂停。参考思路成本低，整套移植无收益。复制代码需保留许可声明。 |
| [Nav2](https://github.com/ros-navigation/navigation2) | 4,787 / 否 | 2026-10-09 / 1.5.2，2026-09-16 | 目标控制器 [Apache-2.0](https://github.com/ros-navigation/navigation2/blob/main/nav2_regulated_pure_pursuit_controller/package.xml)，仓库其他包许可不同 | [Regulated Pure Pursuit](https://github.com/ros-navigation/navigation2/tree/main/nav2_regulated_pure_pursuit_controller) 的转弯约束与近终点控制 | C++/ROS 2，需要机器人消息、坐标变换、成本地图和运动接口；这些都不对应现有 Python/Win32 输入。整套集成成本高；只参考转弯和终点控制，不引入 ROS。 |
| [teb_local_planner](https://github.com/rst-tu-dortmund/teb_local_planner) | 1,355 / 否 | 2026-09-21 / 0.1.3，2015-05-20 | 主项目 [BSD-3-Clause](https://github.com/rst-tu-dortmund/teb_local_planner/blob/noetic-devel/LICENSE) | 连续轨迹与执行时间优化 | C++/ROS，需要成本地图、障碍输入及优化依赖。README 另列 Eigen、g2o/CSparse 等不同授权组件；维护较稀疏，集成成本高。当前没有已验证的可通行地图，不适合引入。 |

成本为基于当前 Python/Win32 项目的相对估算。三个候选都不能替代已核实的 SSJJ 状态来源和输入通道。推荐复用现有播放器、状态读取、Win32 输入及 Python 标准库，仅实现与现有输入模型相符的连续控制逻辑。

## 后台验证

`tests/test_ssjj_continuous.py` 使用按时间推进的移动模型与离散状态更新，不依赖游戏、桌面焦点或系统输入。覆盖路线推进、途中纠偏、直角、折返、闭环、不同速度/更新频率、低灵敏度、跨楼层、暂停、切图、死亡、失焦和冻结。输入边界测试模拟 OS 发送，覆盖监控线程、回调阻塞、守卫释放及失焦竞争；Python 集成测试实际经过子进程和父进程原生动作分发。

最终相关测试 53 项通过，速度与更新频率矩阵覆盖 120 个场景。全量工作区测试运行 1,679 项，有一项无关失败：`tests.test_ui_contract.PackageEntryTests.test_app_package_entry_does_not_aggregate_client_implementations` 报告 `disconnect_monitor` 导入泄漏。另在已提交源码 `3c742dc` 加本次改动的临时副本中验证，该导入问题同样存在（旧测试名为 `test_app_package_entry_only_exports_the_app_class`）；旧版 `tests.test_click_offsets.RecognitionClickOffsetTests.test_offset_control_available_for_image_and_text_recognition` 的七个子项还缺少 `row_start_delay` 测试对象字段，且副本未包含 `.deps` 导致 `tests.test_ocr_build.OCRBuildRequirementsTests.test_stdlib_closure_manifest_covers_rapidocr_package_imports` 报依赖缺失。这些问题不在本次跑图改动中修复；未将其他任务的修改混入提交。

同一模拟模型比较提交 `3c742dc` 的旧控制器与新控制器：路线 `(0,0,0) → (600,0,0) → (1200,0,0)`，速度 600 单位/秒，状态每 50ms 更新。旧控制器耗时 4.735 秒、终点误差 71.757、前进按下 16 次；新控制器耗时 2.05 秒、终点误差 6、前进按下 1 次。该案例模拟耗时减少约 57%，不代表实机速度提升保证。

运行相关测试：

```powershell
$env:PYTHONPATH="$PWD/.deps;$PWD/src"
& 'C:/Python313/python.exe' -m unittest tests.test_ssjj_continuous tests.test_ssjj_navigation tests.test_ssjj_integration tests.test_python_scripts tests.test_pause
```

日常仍通过 `run.bat` 重启使用最新源码。本任务没有构建或打包。
