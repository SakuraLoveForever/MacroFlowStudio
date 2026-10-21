# 当前游戏页面只读调查（2026-10-14）

用户在本次调查中启动游戏并确认停在大厅。Win32_Process 实际观察到 WDlauncher.exe、MicroClient.exe、AirLobbyPreloader.exe，路径均位于 E:/SSJJ-4399；未发现 SSJJ_BattleClient_Unity.exe。未启动、点击、截图或切换游戏界面，未写游戏内存或调用游戏函数。

REA 对 E:/SSJJ-4399/Lobby/AirLobbyPreloader.exe 的只读分类证据 ev_2dd6bd96b770f253fb80d7098d5a941305b3e7d87639805437de3930489c0979：x86 PE，无 CLI 目录，不是 Mono/.NET 托管程序集；SHA256 f59d1fb6a1d7410f58d596db82bac236539702e0e4755edd11cb94fba590815b。目录中有 Adobe AIR 与 AirLobbyPreloader.swf。只能验证运行的是大厅运行时，不能据此断言其具体子页面。

现有历史导出 ActionScript 的 AirLobbyPreloader/Mini.as 显示 Lobby.swf 动态加载；ModuleLoader.as 经 URLLoader/Loader 加载大厅内容。当前导出只有预加载器，未核实运行中的 Lobby.swf 页面管理器或稳定页面 ID，不能把战斗端 Mono 字段读取方案套用于大厅。

历史导出战斗代码 Assets/Sources/Free/UI/FreeUiManager.cs 有 _uis 字典、Uis/FreeUIs 属性与 SimpleFreeUI.Visible，可作为后续只读枚举可见战斗 UI 的候选；多个 UI 可以同时可见，不能简单视为唯一页面 ID。既有 SSJJReader 仅核实玩家与地图状态。这些历史符号尚未与本次运行版本完成布局核验。

结论：存在进一步读取战斗 UI 状态的线索，大厅具体页面尚无验证过的读取接口。当前不提供「自动恢复原游戏页面」承诺或写入调用；工作流代码位置与剩余等待的暂停/恢复已经支持。后续要实现游戏页面恢复，需要分别核验大厅/战斗状态，以及各页面之间可验证的返回动作。

## 后续落地：整页截图页面识别（2026-10-21）

大厅既没有可读的页面状态接口，就改用「整屏截图 → 64×36 标准化灰度特征 → 与页面库矩阵相乘」做页面判定：
`images/全屏` 下的 84 张整屏截图就是页面库，文件名即页面名（如 `全屏_回到大厅`）。
实测 1920×1080 单帧匹配全部页面约 0.2 ms 矩阵乘（含抓屏与特征约 20 ms），首次加载页面库约 2.1 s；
当前大厅实时帧与 `全屏_点击游戏画面` 相似度 0.9954，次优页面 0.8883，阈值 0.97 + margin 0.02 有充分余量。
84 张里有 6 组 15 页是「同底图 + 局部元素差异」的真近似页面，整页特征分不开，按页面组处理。

已实现 `src/macroflow/core/page_state.py`（特征、页面库、节流追踪）与
`src/macroflow/execution/player/page.py`（页面动作、执行期页面追踪），界面侧新增「页面」动作
对话框，Python 逻辑可用 `ctx.page()` / `ctx.page_is()`。详细用法与风险见
`docs/page-recognition.md`。战斗端 `FreeUiManager` 只读读取器仍待进入对局核验。
