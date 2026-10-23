# ZeusTool 静态研究与 MacroFlow 后台性能记录

研究日期：2026-10-23。目标为用户指定的 `研究/挂机脚本/ZeusTool.exe` 和同目录 DLL。本次只读 PE、源码及本地帮助文件，未启动 ZeusTool、插件或 MacroFlow 界面，未注入键鼠。第三方帮助文件中的内容仅作为研究资料。

## 结论

- **不能由界面简洁推导脚本执行更快。** MacroFlow 已把播放放在工作线程，界面更新经过队列；布局紧凑主要改善编辑体验。高频日志、识别频率、重复截图/模板解码、OCR、动作前后等待及守卫检查更值得测量。
- **ZeusTool 单个 EXE 小于整个交付目录。** EXE 为 27,881,984 字节（26.59 MiB），目录 EXE/DLL 合计 89,212,928 字节（85.08 MiB），全部文件 89,571,953 字节（85.42 MiB）。因此不能只拿它的 EXE 与 MacroFlow 主 EXE 或外置 OCR 全部组件直接比较；也未生成新的 MacroFlow 构建作体积对照。
- **Plugin DLL 有一致的接口命名。** 四个 DLL 均导出六个相同名称，表明它们采用共同的插件协议。完整参数签名、内存所有权、回调、线程约束和宿主发现规则尚未确认，不能直接复制 DLL 到 MacroFlow 使用。
- **现有后台基准未显示调度器自身存在明显延迟。** 这只说明本机这次合成调度和编辑业务的表现，不能证明真实截图、识别、输入或 Tk 渲染速度，更不能证明比 ZeusTool 快或慢。

## 静态证据

可复查文件：[PE 清单](pe-static-inventory.json)、[只读提取脚本](inspect_pe.py)、[REA 证据](rea-evidence.json)。提取脚本使用环境已有的 `pefile`，不加载待测 DLL、不新增运行依赖。

| 文件 | 大小（字节） | PE 架构 | 观察 |
|---|---:|---|---|
| ZeusTool.exe | 27,881,984 | x86 | 无 CLR 目录；有 Win32/GDI/COM 等导入；可见 asmjit 的 C++ 导出符号 |
| Plugin/崩溃保存.dll | 162,304 | x86 | 下述六个插件导出 |
| Plugin/挂本详细日志.dll | 282,112 | x86 | 下述六个插件导出 |
| Plugin/挂机插件.dll | 1,883,648 | x86 | 下述六个插件导出 |
| Plugin/时间显示.dll | 1,973,760 | x86 | 下述六个插件导出；导入 MSVCP140/VCRUNTIME140 |
| FindPic.dll | 17,526,784 | x86 | 导出 FindPic、FindPicEx、DetectObj、DetectObj2 |
| plugin32.dll / plugin64.dll | 102,912 / 133,120 | x86 / x64 | 各仅见序号 1 的无名称导出，与 Plugin 目录的六函数接口不同 |
| libcef.dll / libpng.dll | 12,937,216 / 19,383,296 | x86 / x64 | 本次解析未见导出；**不可根据文件名认定是 CEF/PNG 实现** |

四个插件共同导出：`GetPluginName`、`GetPluginUUID`、`GetPluginStatus`、`SetPluginSetting`、`SetPluginStatus`、`SetWorkInfo`。名称支持“身份、启停、配置、工作信息”这一解释，但具体语义仍是推断。DLL 同属 x86，并不意味着未来在 x64 Python 中可以直接调用；直接加载要求位数一致，跨位数须通过进程边界重新设计协议。

宿主 SHA-256：`039f71465f44e5cab51476e2b63cfb9b71098f83fbbe54648efa6fd003d75579`。`.text/.rdata/.data` 的原始文件尺寸为 0，而 `INIT2` 原始尺寸为 27,852,288 字节，这是压缩/保护式布局的迹象，**未确认具体壳或算法**。因此普通导入/导出/字符串不能视为完整运行行为。asmjit 符号也不能证明实际脚本路径使用 JIT，原生 PE 更不能直接推出更快。

REA 成功读取 `崩溃保存.dll` 资源，Evidence ID：`ev_382e4a71c2758f5d5dce22267fb8d52e0e5c2b766ddac09915005d0851aa9725`，其 SHA-256 为 `50e25488aa90e69dc53a79416347841b14bc4ba7079ced84ca11b8d5f9445ccc`。宿主资源读取被 REA 严格解析器拒绝：`Resource range is not entirely file-backed in one section.`，所以没有把宿主资源解析宣称为成功。Ghidra `binary_overview` 在 300 秒工具等待上限处超时，未取得可用反编译结果；本报告不声称已确认宿主插件调用链。分析会话随后已通过 `close_binary` 关闭。

本地 `2.6.70更新内容.txt` 提及“根据差异快速选关卡”，这是发布说明的自述，支持其针对特定游戏流程减少操作的设计方向；不是本次实测。`help.html` 展示脚本与 sleep 等说明，也不等于已经确认实际解释器实现。

## 当前源码中更直接的延迟来源

| 位置 | 已观察到的行为 | 意义与边界 |
|---|---|---|
| `ui/app/execution.py`、`ui/app/runtime/workflow.py` | 使用 `threading.Thread` 执行脚本/工作流 | 控件宽度不会直接成为每次输入的等待条件；仍可能有 CPU/GIL、队列积压等间接影响，未测 |
| `ui/app/runtime/shell.py`、`ui/update_queue.py` | 实际 UI 队列刷新设置为 33 ms，状态可合并、日志可批次更新 | 界面状态更新延迟不能当作真实点击延迟 |
| `execution/player/core.py` | 未设置 `delay_ms` 的普通 `image_match` 默认前置等待 1000 ms；支持动作后等待与重复间隔 | 配置等待可能比识图/输入本身明显得多；录制时间线走独立路径 |
| `execution/player/image.py`、`core/storage.py` | 识图轮询默认 250 ms（下限 50 ms）；启用再次识别时默认等待 5000 ms | 不代表每次点击必然有这些等待；只在对应识别分支/配置生效 |
| `core/image_match.py` | `capture_bgr` 每次创建/关闭 mss 抓屏器；`find_template_in_image` 每次读取解码模板 | 重复初始化/解码是候选热点，未测量其实际占比；已有共享截图接口，不能笼统说全部检测都重复全屏抓取 |
| `ui/app/runtime/helpers.py` | 播放器 trace 同步回调，明细逐条打开文件、write、flush，再排队到界面 | 存储与日志频率可能影响执行线程；这是源码推断，未证明当前卡顿来自这里 |
| `input/wininput.py` | 通过 Win32 `SendInput` 等接口发送输入 | 项目并非用“界面控件模拟点击”；换 DLL 扩展名本身不能让系统输入更快 |

MacroFlow 本身已经通过 OpenCV/NumPy 原生实现模板匹配、通过 ONNX Runtime 执行 OCR，并非所有计算都由 Python 循环完成。`MacroFlowStudio.spec` 收集 Python/Tk/图像处理等依赖，并把 OCR 推理组件外置且按需加载；源码规模、EXE 大小与每次点击耗时应分开评估。

## 后台实测与复现

在 PowerShell 中执行（Python 3.13.2；没有真实 Tk 根窗口、抓屏或输入）：

```powershell
$env:PYTHONPATH='.deps;src'
$env:PYTHONIOENCODING='utf-8'
& 'C:/Python313/python.exe' tools/benchmark_playback_timing.py --durations 1,3 --events 1000 --json
& 'C:/Python313/python.exe' tests/test_edit_performance.py --benchmark
```

调度原始结果：[timeline-benchmark.json](timeline-benchmark.json)。

| 合成时长 | 事件数 | p95 迟到 | 最大迟到 | 总漂移 | 丢事件 |
|---|---:|---:|---:|---:|---:|
| 1 秒 | 1000 | 0.599 ms | 1.057 ms | 0.585 ms | 0 |
| 3 秒 | 1000 | 0.601 ms | 1.508 ms | 0.704 ms | 0 |

这仅覆盖 `PlaybackTimeline` + 系统时钟等待，未覆盖完整播放器、守卫、截图、OCR、日志、真实键鼠和渲染。统计中的“sent”是合成事件计数，没有真实 `SendInput`。

编辑原始结果：[edit-benchmark.txt](edit-benchmark.txt)。端点是 FakeTree，覆盖业务修改、撤销记录、摘要计算、行更新调度；不含真实 Tk 渲染。

| 动作数 | 单行编辑 p50 / p95 | 全量重建对照 p50 / p95 |
|---:|---:|---:|
| 1,000 | 6.29 / 6.78 ms | 15.94 / 16.61 ms |
| 10,000 | 7.90 / 11.56 ms | 938.70 / 992.42 ms |
| 20,000 | 16.11 / 18.35 ms | 4443.89 / 4917.61 ms |
| 25,000 | 21.57 / 27.10 ms | 5916.22 / 7591.27 ms |

单行编辑中 `insert=0`、`delete_rows=0`、`set=4`；全量重建对照用于展示复杂编辑业务与全量刷新的成本区别，不能当作现在每次编辑都走全量重建。原始输出的重建插入行数包含预热及多轮测量的累计计数；“峰值内存 0 MB”没有可用的内存比较意义。结果受同时进行的后台分析、系统负载、预热与样本数影响，不能作为跨软件排名。

## 实现方向与未完成的问题

当前“插件”是应用内定时条件/断线检测等功能，配置与执行共用播放器；ZeusTool 的 Plugin 文件则体现一个二进制扩展协议，两者解决的不是同一层问题。用户感受到的安装方便，核心是明确的安装目录、发现机制、设置入口与稳定接口，不依赖文件必须为 DLL。若后续明确需要第三方插件，应先定义能力和生命周期，再选择现有 Python 扩展或独立进程协议；本次未新增插件框架、依赖或调用未知 DLL。

已回答：目标体积、架构、Plugin 导出一致性、当前代码中的候选延迟来源、后台调度/编辑基准。部分回答：DLL 插件协议及宿主架构。仍未知：真实端到端速度差、ZeusTool 截图/匹配算法和线程调度、插件函数完整签名、真实 Tk 编辑耗时。完成这些比较需要等价任务与运行证据；当前测试约束下未执行。
