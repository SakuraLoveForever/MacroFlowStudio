# 当前游戏页面只读调查（2026-10-14）

用户在本次调查中启动游戏并确认停在大厅。Win32_Process 实际观察到 WDlauncher.exe、MicroClient.exe、AirLobbyPreloader.exe，路径均位于 E:/SSJJ-4399；未发现 SSJJ_BattleClient_Unity.exe。未启动、点击、截图或切换游戏界面，未写游戏内存或调用游戏函数。

REA 对 E:/SSJJ-4399/Lobby/AirLobbyPreloader.exe 的只读分类证据 ev_2dd6bd96b770f253fb80d7098d5a941305b3e7d87639805437de3930489c0979：x86 PE，无 CLI 目录，不是 Mono/.NET 托管程序集；SHA256 f59d1fb6a1d7410f58d596db82bac236539702e0e4755edd11cb94fba590815b。目录中有 Adobe AIR 与 AirLobbyPreloader.swf。只能验证运行的是大厅运行时，不能据此断言其具体子页面。

现有历史导出 ActionScript 的 AirLobbyPreloader/Mini.as 显示 Lobby.swf 动态加载；ModuleLoader.as 经 URLLoader/Loader 加载大厅内容。当前导出只有预加载器，未核实运行中的 Lobby.swf 页面管理器或稳定页面 ID，不能把战斗端 Mono 字段读取方案套用于大厅。

历史导出战斗代码 Assets/Sources/Free/UI/FreeUiManager.cs 有 _uis 字典、Uis/FreeUIs 属性与 SimpleFreeUI.Visible，可作为后续只读枚举可见战斗 UI 的候选；多个 UI 可以同时可见，不能简单视为唯一页面 ID。既有 SSJJReader 仅核实玩家与地图状态。这些历史符号尚未与本次运行版本完成布局核验。

结论：存在进一步读取战斗 UI 状态的线索，大厅具体页面尚无验证过的读取接口。当前不提供「自动恢复原游戏页面」承诺或写入调用；工作流代码位置与剩余等待的暂停/恢复已经支持。后续要实现游戏页面恢复，需要分别核验大厅/战斗状态，以及各页面之间可验证的返回动作。
