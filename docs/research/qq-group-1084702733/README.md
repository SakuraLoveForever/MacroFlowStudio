# QQ 群自动跑图资料索引

后续已对用户提供的 7.0.9 安装包开展静态逆向，见 [7.0.9 研究记录](../ssjj-bot-7.0.9/README.md)。下文旧 assets 的二维路线结论不代表新版全部实现；新版另含世界 XYZ 路线和坐标转换接口。

核验日期：2026-10-13。来源为用户已加入的 QQ 群 `1084702733` 的群文件，经本地 NapCat 无头桥接读取。

## 采集结果

- 根目录及 5 个文件夹共 101 个唯一文件，和 `get_group_file_system_info` 返回的总数一致。
- 筛选下载 18 个相关文件，共 269,097,810 字节；逐个核对 QQ 返回的大小，并保存下载后 SHA-256。
- 优先读取路线资源、独立路线 JSON、两份 PDF 教程和一份 `.Q` 脚本。视频、普通截图、虚拟机及分辨率工具未下载。
- `绝境入侵.zip` 获取下载地址失败，QQ 返回 `sendGroupFileDownloadReq -134`，没有伪造下载成功。
- 群文件原件、上传者信息、提取内容及详细清单保存在本地 `研究/QQ群1084702733/`；该目录通过 `.git/info/exclude` 排除，不提交原始群文件。
- 没有运行群内 EXE 或宏，也没有启动游戏或进行界面测试。本次没有修改导航产品代码。

## 自动跑图：优先查阅的材料

| 材料 | 本地位置（相对资料目录） | 实际内容与用途 |
|---|---|---|
| 群内较新 `assets.rar` | `extracted/assets-latest-rar/assets/` | 126 条地图登记、74 份路线 JSON、345 张 PNG；路线文件覆盖 72 个内部地图编号，合计 831 个途经点 |
| `assets 0420.zip` | `extracted/assets-0420-zip/assets/` | 较旧快照。RAR 相比它新增 32 份路线 JSON，另外 6 份同路径 JSON 内容变化；两个版本独立保留 |
| 地图登记表 | `extracted/assets-latest-rar/assets/maps/maps_registry.json` | 地图名称、内部编号、地图图片、路线图片、ROI、朝向偏移及途经点文件引用 |
| 路线目录 | `extracted/assets-latest-rar/assets/maps/routes/` | 行进顺序、行走标记、传送标记、到点循环瞄准数据；逐份摘要见 `路线索引.csv` |
| 深海 2、3 独立路线 | `downloads/` 中 `map_119_waypoints.json`、`map_120_waypoints.json` | “开局裁决 boss 切包”文件夹和根目录/资源替换文件夹中的变体，按来源文件 ID 分开保存，未覆盖资源包路线 |
| 寻路软件教程 | `text/` 中 `软件操作指南和问题解决方法.txt` | 已提取 8 个 PDF 页面文本，包含任务集、加成序列、帧率、移动速度和恢复设置 |
| 固定点击教程与 `.Q` | `text/` 与 `downloads/` | 固定 1920×1080 点击的使用说明，以及白芯片升级/分解的真实脚本文本；对自动寻路优先级较低 |

### 路线数据结构

登记表有 `name`、`map_id`、`base_map_file`、`route_file`、`roi`、`heading_offset`、`mode`、`mainline_group`、`waypoint_file`、`waypoint_files`。

路线 JSON 有以下实际字段；缺失字段、空数组及 `null` 都确实存在，不能假设每条路线包含完整动作：

| 字段 | 已观察到的数据 | 可得出的结论 |
|---|---|---|
| `waypoints` | 有序二维数值对 | 保存了人工指定的途经点顺序；没有给出自由寻路算法 |
| `walk_markers`、`walk_waypoint_indices` | 坐标与对应索引 | 23 份路线含非空标记，可研究需要特殊行走处理的路段；具体按键语义还需控制器代码证实 |
| `portal_markers`、`portal_waypoint_indices` | 坐标与对应索引 | 玛雅 8 有 8 个标记，海岛 5 有 2 个，提示需要区别处理传送/交互节点；不能据此断言动作按键 |
| `aim_cycle_points` | `point`、`waypoint_index`、`interval_s`、`angles` | 29 份路线含非空循环点，角度记录为 `yaw_deg`、`pitch_deg`；可区分行进与到点定向动作 |
| `aim_cycle_point` | 单个对象或 `null` | 与复数字段并存，有时保存同一循环点。仅作为外部格式证据，不要求本项目保留两套兼容结构 |

工业 8 的专用路线有 9 个途经点、两个行走标记和一个末端循环瞄准点。深海 1 有 45 个途经点、两个行走标记，末尾存在重复点。深海 2、3 的独立文件另有原地循环瞄准变体。不得将所有变体简单合并为一条路线。

### 与当前项目的关系

最重要的边界是坐标系。资源包的途经点是二维数值，配套图片约 545×550，登记表同时给出屏幕 ROI；据此推断它们属于小地图图像坐标。此次没有取得控制器源代码来证明完整换算公式。它们**不能直接导入当前 `ssjj_route` / `ssjj_recording` 并当作游戏原生三维坐标**。

资源中的 `map_id` 是该软件登记表的内部编号，未证实与游戏客户端/离线地图资源 ID 一致。先按地图名称与原图核对身份，再考虑坐标标定；不能仅按编号匹配。

对后续开发最有用的是路线拐点、模式区分、特殊节点标记和到点瞄准结构，以及任务设置在加入任务时形成快照的说明。当前项目已有原生坐标实走录制，应先参考这些数据检查覆盖与动作语义，再决定是否需要小地图坐标导入，不应直接替换现有流程。

### 教程中值得保留的说明

以下是原作者教程的使用要求/经验，并非本项目已实测参数：

- 分辨率 1920×1080，最高帧率 150，关闭垂直同步；修改分辨率/缩放后重启软件与游戏。
- 原作者指出过高移动速度容易产生过点往返和楼梯卡住，建议约 100、通常低于 200；这支持后续关注速度与过冲，不能当作本项目硬编码阈值。
- 自定义加成序列区分普通按键间隔、副武器延迟、连续开火时长及循环周期。
- 设置需要先勾选再加入任务；任务集支持保存、覆盖导入和追加导入。
- 原作者描述了保险箱/Boss/撤离条件，但此次只有说明文本，没有执行路径证据，不能当作已复现的通关逻辑。

### 已知缺口

1. 登记表中工业 1 引用了 `assets/maps/routes/waypoints/map_1_waypoints.json`，该 RAR 中实际缺失；不能声称 126 条登记都具备可用途经点路线。
2. 工业 10 的原图尺寸为 549×554，而登记 ROI 宽高为 555×552；不能假设图片与屏幕 ROI 始终一比一对应。
3. 辅助工具 ZIP 的成员主要为加密打包的 EXE，并不是开放的控制器源代码；只保留原件，未运行。
4. `SSJJ_BOT_V1_Setup_7.0.9.exe` 与 `SSJJ_BOT_V1_Beta_6.0.5.zip` 已列入原始目录，但本轮没有下载/反编译。上述资源数据不代表最新安装包的全部实现。
5. PDF 仅提取可读取文本，没有验证嵌入截图里的操作细节；没有实机通关验证。

## 桥接工具选型

本轮通过 GitHub API 查询，日期均为 2026-10-13；`pushed_at` 仅表示仓库推送时间，不等同于已核实的最近实质代码提交。

| 候选 | Star | 推送 / 最新发布 | 许可证与使用方式 | 取舍 |
|---|---:|---|---|---|
| [NapCatQQ](https://github.com/NapNeko/NapCatQQ) | 10,934 | 2026-10-13 / v4.18.38，2026-10-12 | [自定义限制许可证](https://github.com/NapNeko/NapCatQQ/blob/main/LICENSE)，独立本地部署，未将实现代码引入本仓库 | 本轮采用官方 Windows Node 无头包，已有群文件 API，避免修改已安装的 32 位 QQ；部署成本低 |
| [LuckyLilliaBot](https://github.com/LLOneBot/LuckyLilliaBot) | 3,661 | 2026-10-12 / v8.3.2，2026-10-12 | [GPL-2.0](https://github.com/LLOneBot/LuckyLilliaBot/blob/main/LICENSE)，可独立部署 | 提供 OneBot 11/Satori/Milky；本轮无需再部署第二个协议端，未实测群文件能力 |
| [Lagrange.Core](https://github.com/LagrangeDev/Lagrange.Core) | 2,992 | 2026-09-07 / latest-release API 返回 404 | 本轮许可证未核实，C# 协议实现 | 还需确认部署、签名和登录条件，当前 NTQQ 本地桥接路径更直接，未采用 |

NapCat 参考：[官方安装说明](https://napneko.github.io/guide/boot/Shell)、[发布包](https://github.com/NapNeko/NapCatQQ/releases/tag/v4.18.38)、[Node 包组装流程](https://github.com/NapNeko/NapCatQQ/blob/v4.18.38/.github/workflows/release-publish.yml)。本机部署不构成本项目取得其代码商业使用或再分发授权。

使用官方 `NapCat.Shell.Windows.Node.zip`，校验 SHA-256：`b88007a373a30deefb387e7cf17911752ff900bce22c534b3da6b644f5c6d0ae`。WebUI 和 OneBot HTTP 仅监听 `127.0.0.1`，使用不同随机令牌，未配置反向连接或自动回复。桌面 QQ 与同账号无头进程不能重复登录，本轮在用户选择临时退出桌面 QQ 后成功登录。

## 验证与证据

本地 `verification.json`、`download-manifest.json`、`route-analysis.json` 和 `路线索引.csv` 保存统计与来源。已验证 101 个目录条目的唯一性、18 个文件的大小与下载后 SHA-256，以及保存的 JSON 均可解析。下载后摘要用于后续比对，不代表上游签名验证。

旧 ZIP 静态分析使用 REA，未执行目标：

- inventory Evidence：`ev_bdb62bd3c528a119073085e55d70b02c463a6b9cd59e7b30eaa5fc94d91d9396`
- inspect Evidence：`ev_df231d8c9f309ed449731526cf634e2037fc02b7ebbd1b4db2d28c2dfb87412c`
- extract Evidence：`ev_98fa074167e095d1c42c02fcb72b287a0cd80108487ea59ea160eb58faa7e66b`
- manifest：`agm_0f08795790d1941bfb0367ab9335e18472f47d5a06ec9786c7f23e47587152ef`
- 原 ZIP SHA-256：`0b8d02c3dd3be3e1dc61dcce75c0ef640ccdb0f69c524dde7f9507c9578ee025`

较新 RAR 使用 Windows 自带 tar 静态列目录并提取；提取前核对路径无上跳/绝对路径及链接条目。REA 会话已关闭。没有构建或打包本项目。
