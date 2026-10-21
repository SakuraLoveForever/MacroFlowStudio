# 等待小怪消失

脚本编辑器的「流程」分类新增「等待小怪消失」。插入或双击编辑时设置持续时长，默认 **5 s**，支持 ms/s 单位。动作位于选中行之后。

规则：游戏内存统计连续指定时间为 0 才进入下一动作；任一轮出现小怪就重新计时并继续阻塞。小怪与「游戏监测」面板采用同一口径：包含精英，排除 BOSS、友军、本人、已死亡或血量为零的实体。

数据未知、读取失败或过期不等于没有小怪。暂停、守卫处理或换地图后重新计时；F12 可以停止等待。无需图像模板、OCR 或额外依赖。

## 复用与验证

直接复用项目已有的 `SSJJEnemyMonitor` / `read_enemy_counts`、`sustained_detection`、`DurationDialog` 和播放器的暂停/守卫机制。新动作只组合现有能力，没有重新实现怪物识别或计时算法。

2026-10-14 通过 GitHub API 检索 `SSJJ language:Python` 并按 Star 排序，13 个结果中前三项分别为 [ssjj2-auto_click_balls](https://github.com/YinanMGI/ssjj2-auto_click_balls)（3 Star，无许可证）、[ssjjScript](https://github.com/gujunfenzhan/ssjjScript)（1 Star，无许可证）、[Autoplay_scripts_ssjj](https://github.com/yzq38/Autoplay_scripts_ssjj)（1 Star，API 标记 MIT，授权正文与维护日期本次未核实）。没有找到高 Star、提供本游戏已验证敌人内存计数的合格候选；不为凑数引入其他游戏或桌面识图框架，也不复制未核实授权的代码。

后台单测覆盖连续无怪、重新出现、BOSS 排除、未知数据、换图、暂停、守卫、停止、默认值、编辑与执行分发。导航回归另外覆盖已有惯性、松键后停稳，以及复用实测滑行参数以减少后续重复纠正；纠正点按自己的录制时间触发，最后一条输入后仍等到录制结束，并在终点验收前释放录制输入。惯性、碰撞和输入反馈仍需验收，不能保证所有环境都只用一次输入完成精准对齐。
