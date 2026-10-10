# 战斗端断线检测

通过根目录 `run.bat` 启动 Macro，在左侧「执行设置」勾选「战斗端断线检测」。
开关默认关闭，仅本次运行生效；取消勾选或退出 Macro 后停止检测。
功能独立于脚本播放，启用后空闲、暂停和托盘运行时都会检查。

## 检测规则

按照用户提供的检测仪的静态分析结果实现：

- 每 5 秒检查一次 `SSJJ_BattleClient_Unity.exe`，进程名不区分大小写。
- 进程运行超过 20 秒后才检查连接；20 秒是启动宽限，不是连续断线计时。
- 读取目标进程的 IPv4 TCP 连接，排除远端地址 `127.0.0.1`、远端端口 `80`。
- 剩余连接中任意一条不是 `ESTABLISHED`，或没有剩余连接，强制结束该战斗进程。
- 不结束其子进程，不启动游戏，不执行重连脚本；后续行为由游戏决定。
- 连接读取失败不会视为断线；权限不足时记录错误，进程已退出时跳过。
- 结束进程前使用 psutil 的进程身份校验，避免 PID 被复用后误杀其他程序。

同一进程既有已建立连接又有异常连接，仍会触发；该规则有意保持与原软件一致。

## 开源复用调研

核验日期：2026-10-10。范围限定为 Windows/Python 进程连接监控；离线运行，
沿用项目依赖，不要求抓包、驱动或额外软件。

| 候选 | Star / 核验状态 | 维护与发布 | 许可证 | 可复用能力 / 接入成本 | 取舍与风险 |
|---|---|---|---|---|---|
| [psutil](https://github.com/giampaolo/psutil) | 页面约 11.3k | 最近实质维护和最新发布日期未核实 | [BSD-3-Clause](https://github.com/giampaolo/psutil/blob/master/LICENSE) | 已有依赖 7.2.2；进程枚举、创建时间、TCP 状态及终止；低 | 采用；读取受系统权限限制，失败只记日志 |
| [pywin32](https://github.com/mhammond/pywin32) | 页面约 5.6k | 最近实质维护和最新发布日期未核实 | [仓库说明为多种许可证](https://github.com/mhammond/pywin32#licenses)，逐组件授权未核实 | 已有依赖；提供 Windows 进程接口；中 | 不增加此方向实现；完整 TCP/PID 监控封装能力未核实，psutil 已满足需求 |
| [Scapy](https://github.com/secdev/scapy) | 未核实 | 最近实质维护和最新发布日期未核实 | [GPL-2.0](https://github.com/secdev/scapy/blob/master/LICENSE) | [Python 抓包与协议分析](https://github.com/secdev/scapy/blob/master/README.md)；高 | 不采用；抓包不能直接替代进程连接状态查询，也无需引入其分发授权义务 |

GitHub API 本次返回 403 rate limit exceeded，页面未提供可靠的最新提交/发布日期，
因此未把近期维护、归档状态或精确 Star 数写成已核实事实。
所选方案复用已安装的 psutil API，不复制上述项目源码，不新增包；其 BSD 分发声明仍需保留。
Windows 适配依据：[psutil Windows 后端](https://github.com/giampaolo/psutil/blob/master/psutil/_pswindows.py)。

## 后台验证

使用 `run.bat` 相同的 Python 3.13 与 `.deps;src` 导入路径运行：

```powershell
$env:PYTHONPATH="$PWD\.deps;$PWD\src"
& C:\Python313\python.exe -m unittest tests.test_disconnect_monitor
```

测试替换进程读取与结束接口，覆盖宽限边界、连接筛选、混合状态、读取失败、
进程消失、启停及窗口销毁回调。测试不创建窗口、不终止真实进程。
