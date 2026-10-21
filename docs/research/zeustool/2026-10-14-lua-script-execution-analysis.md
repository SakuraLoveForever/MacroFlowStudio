# ZeusTool 脚本执行原理逆向分析（REA）

分析日期：2026-10-14。对象：`研究/挂机脚本/ZeusTool.exe`（SHA256 `039f7146...d75579`，与 2026-10-12 报告记录的文件哈希一致）。
本轮全部为静态分析，未启动目标程序、未启动游戏、未修改任何原件，也未改动 MacroFlow 产品源码。

工具链：REA 6.4.0（`rea` CLI）+ Ghidra 12.1.4 引擎 + Capstone 5.0.7。证据清单见
[evidence/lua-execution-evidence.json](evidence/lua-execution-evidence.json)。

---

## 结论（先看这一段）

**ZeusTool 的“脚本”就是 Lua 5.4.8 脚本。** 它把一个**未修改的上游 Lua 5.4.8 静态库**嵌进主程序，用 Lua 的 C API 注册自己的函数，然后从外部读取 `.txt` 里的 Lua 源码并执行。

脚本本身**不含走路逻辑**。脚本只做三件事：调用 `mi_*` 接口下发意图、用 `sleep()` 等时间、用 `mi_getPos()` / `mi_isRunning()` 轮询状态。真正的“把角色走到目标点”由主程序另一条原生线程完成：读游戏共享内存里的坐标 → 按 50 个游戏坐标单位的平面距离判定到点 → 把 0x78 字节命令块写进共享内存 → 游戏进程内的 `plugin64.dll` 把命令变成游戏读到的按键状态。

所以它的执行原理是**“Lua 下发意图 + 原生循环闭环控制”**，不是键鼠时间轴回放。

---

## 1. 脚本引擎：确认是原版 Lua 5.4.8

运行态镜像里存在成组的、未被改写的 Lua 上游字符串，长度和措辞都与官方一致：

| 地址 | 内容 | 含义 |
|---|---|---|
| `0xb268dc` | `$LuaVersion: Lua 5.4.8  Copyright (C) 1994-2025 Lua.org, PUC-Rio $` | `luaL_checkversion_` 的版本横幅 |
| `0xb92dbc` | `version mismatch: app. needs %f, Lua core provides %f` | `luaL_checkversion_` |
| `0xb92d70` | `PANIC: unprotected error in call to Lua API (%s)` | panic 处理 |
| `0xb928f0` | `luaopen_%s` | `lua.c` / `lauxlib.c` 的 C 模块命名 |
| `0xb929f8` | `LUA_PATH` | `package` 库 |
| `0xb927fc` | `lua_debug> ` | `liolib.c` 的 io.read 调试提示符 |
| `0xb91938` | `Lua 5.4` | `luaB_openlibs` 用来填 `_VERSION` |

**排除项**：`luajit` / `LuaJIT` / `sol2` / `sol::` / `IL2CPP` / `il2cpp` / `Py_` / `python3` / `nuitka` / `Angelscript` / `quickjs` / `duktape` 在镜像里**各 0 次命中**。所以这不是 LuaJIT、不是 sol2 绑定、不是 Python 打包产物、也不是自研 DSL。

已定位的 Lua 核心锚点（供后续复核）：

| VA | 函数 | 依据 |
|---|---|---|
| `0x45ce00` | `luaL_gsub(s, p, r)` | 被 `ll_require` 在 `0x45ace8` 调用 |
| `0x45d100` | `luaL_checkversion_` | 压入 `0xb92dbc` |
| `0x45a6d0` | `lua_callk`（stdcall 包装） | `ll_require` 里第三个参数与 2 比较，两个不同调用点 |
| `0x45ad18` / `0x45ad37` | `ll_require` 构造 `luaopen_<name>` | 两次压入 `0xb928f0` |

---

## 2. 脚本如何被执行：外部源码 + 阻塞式 Lua + 原生 sleep

用户脚本是**外部的 Lua 源文件**，不是预编译字节码。样本 `工业1.txt` 就是标准 Lua：

```lua
mi_start()
--  require("input")  这一句没有用。但是必须存在
sleep(94);
local x, y, z = 0, 0, 0
for waited = 1, 150 do
    x, y, z = mi_getPos()
    if x ~= 0 or y ~= 0 then break end
    sleep(100)
end
mi_run("jiacheng.txt")
mi_clearPath()
mi_addPoint(7572.23, 1747.45, 519.79, 0 )
...
mi_startPath()
while mi_isRunning() do
    local x, y, z = mi_getPos()
    local idx = mi_getPathIdx()
    if idx > 16 then break end
    sleep(1000)
end
mi_shutdown()
```

镜像里还内嵌了**同一套写法的生成模板**（VA `0xa3fbf0`），被路线导出函数引用（`0x4c6025`）：

```
mi_start()
--  require("input")  这一句没有用。但是必须存在
sleep(94);
local x, y, z = 0, 0, 0
for waited = 1, 150 do
    x, y, z = mi_getPos()
...
mi_run("jiacheng.txt")
mi_clearPath()
mi_startPath()
while mi_isRunning() do
    sleep(100)
end
mi_shutdown()
```

模板里的 `mi_addPoint(%.2f, %.2f, %.2f, %d )%s\r\n`（VA `0xa3fe18`，引用点 `0x4c613b`）就是导出时逐点填坐标的格式串。这解释了 `工业1.txt` 的来历：**脚本骨架由主程序生成，路线点由快捷键采点填充**。

### sleep 的真实语义（关键）

`input.sleep` / `input.delay` 都注册到 `0x693fe0`，反汇编为：

```
push ebp / mov ebp,esp / push esi / mov esi,[ebp+8]
test esi,esi ; jns 0x693ff4
mov eax,esi ; cdq ; mov esi,eax ; xor esi,edx ; sub esi,edx   ; esi = abs(ms)
mov al,[0xb8afcc] ; test al,al ; je 0x694018
push 1 ; call dword ptr [0xa3dc00]     ; 调试开关 A
push esi ; call dword ptr [0xa3d554]   ; Sleep(ms)
push 1 ; call dword ptr [0xa3dc04]     ; 调试开关 B
ret
0x694018: push esi ; call dword ptr [0xa3d554] ; ret
```

即**直接调用导入的 Windows `Sleep`，真实阻塞调用线程**。`enableDebug` 只是在前后各加一次开关切换；参数会取绝对值（`0xa43108` = `sleep: 需要1个参数(毫秒)`，`0xa43124` = `sleep: 参数必须是数字`）。

**推论（高置信）**：`sleep(1000)` 就是让脚本线程真的睡 1 秒，不是协程让出、也不是游戏帧对齐。这正好解释了脚本为什么必须**轮询** `mi_isRunning()` / `mi_getPos()` 而不是自己推进——路线推进在别的线程上并行发生。

---

## 3. Lua 模块注册机制（已完整还原 `input` 模块）

脚本开头那句 `require("input")` 注释说“没有用但是必须存在”，实际是把 `input` C 模块载入。

C 函数注册函数在 `0x4e4de0`（签名即 `int f(lua_State* L)`，返回 1）。它的模式是**栈上 `{名称地址, 函数地址}` 记录表 + 循环注册**：

```
mov dword ptr [ebp-0x198], <名称VA>
mov dword ptr [ebp-0x194], <函数VA>
... 以 8 字节步长重复 ...
0x4e5194: push 0 ; push [ebp+eax-0x194]   ; 函数指针
0x4e519e: call 0x44f6d0                   ; lua_pushcclosure(L, fn, 0)
0x4e51a5: call 0x44fc60                   ; lua_setfield(L, -2, name)
0x4e51b0: 取下一条名称 ; test esi,esi ; jne 0x4e5194
epilogue: mov eax,1
```

该表共 **50 条**，短名与长名成对注册（互为别名）：

| 短名 | 长名 | 函数 VA | 短名 | 长名 | 函数 VA |
|---|---|---|---|---|---|
| `kd` | `keyDown` | `0x4e09d0` | `mmd` | `mouseMiddleDown` | `0x4e4050` |
| `ku` | `keyUp` | `0x4e0c30` | `mmu` | `mouseMiddleUp` | `0x4e40e0` |
| `kp` | `keyPress` | `0x4e0b30` | `mmc` | `mouseMiddleClick` | `0x4e4020` |
| `combo` | `comboKey` | `0x4ded10` | `mms` | `mouseWheel` | `0x4e4260` |
| `mld` | `mouseLeftDown` | `0x4e3fe0` | `mmv` | `mouseMoveV` | `0x4e4070` |
| `mlu` | `mouseLeftUp` | `0x4e4000` | `mmh` | `mouseMoveH` | `0x4e4100` |
| `mlc` | `mouseLeftClick` | `0x4e3fb0` | `mset` | `setMousePos` | `0x4e41e0` |
| `mrd` | `mouseRightDown` | `0x4e41a0` | `mget` | `getMousePos` | `0x4e3f70` |
| `mru` | `mouseRightUp` | `0x4e41c0` | `sleep` | — | `0x4e4cf0` |
| `mrc` | `mouseRightClick` | `0x4e4170` | `delay` | — | `0x4e4cf0` |
| `findPic` | — | `0x4e4390` | `getColor` | — | `0x4e42d0` |
| `findPicb64` | — | `0x4e4740` | `oldsc`（表名） | — | `0x4e4b30` |
| `enableDebug` | — | `0x4dee90` | `getDebug` | — | `0x4def30` |
| `allocConsole` / `allocateConsole` | — | `0x4deb70` | `freeConsole` | — | `0x4deee0` |

另有 `debug` 表名（`0xa42e20`）。

**独立交叉验证**：这份反汇编出来的表，与随软件分发的 `help.html` 文档所列函数**逐项吻合**（`kd/ku/kp/combo`、`mld…mmc`、`mms`、`mmh/mmv/mset/mget`、`sleep`、`findPic`、`getColor`、`debug/enableDebug/getDebug`、`allocConsole/freeConsole/allocateConsole`）。两条互不依赖的证据链互相确认，这一层是确证的。

参数校验也有对应字符串：`0xa43108`（`sleep: 需要1个参数(毫秒)`）、`0xa43124`（`sleep: 参数必须是数字`）、`0xa42f7c`（keyDown 家族）、`0xa42fcc`（keyPress 家族）。

---

## 4. `mi_*` 脚本接口：74 个函数，覆盖四类能力

脚本真正用来跑图的是 `mi_*` 命名空间，共 **74 个**（VA `0xa48504`–`0xa489d4`）：

| 类别 | 函数 |
|---|---|
| **路线控制** | `mi_start` `mi_shutdown` `mi_addPoint` `mi_clearPath` `mi_startPath` `mi_stopPath` `mi_goto` `mi_isRunning` `mi_getPathIdx` `mi_getPos` `mi_isDead` `mi_run` |
| **战斗** | `mi_startFire` `mi_stopFire` `mi_setAutoFire` `mi_attackTo` `mi_stopAttack` `mi_setAutoAttack` `mi_hasEnemy` `mi_enemyCount` `mi_nearestDist` `mi_hasBoss` `mi_isEnemyClear` `mi_setEnemyDist` `mi_setEnemyClearWait` `mi_playerCount` |
| **卡住/状态** | `mi_setStuckEnabled` `mi_setStuckParams` `mi_isStuckPending` `mi_clearStuckPending` `mi_setHoldPos` `mi_showAim` `mi_setCombatEnabled` `mi_setPowerEEnabled` `mi_setAutoReloadEnabled` |
| **输入队列** | `mi_keySeqAdd` `mi_keySeqMouse` `mi_keySeqClear` `mi_keySeqCount` `mi_keySeqCooldown` `mi_forceKeySeq` `mi_bossKeySeq*` `mi_forceBossKeySeq` `mi_keyDown` `mi_keyUp` `mi_mouseDown` `mi_mouseUp` `mi_scroll` |
| **图寻路** | `mi_setGraphMode` `mi_isGraphMode` `mi_loadGraph` `mi_loadGraphStr` `mi_graphLoaded` `mi_setGraphGoal` `mi_setGraphGoalNode` `mi_stopGraphGoal` `mi_graphIsArrived` `mi_graphNodeCount` `mi_graphRouteNodeCount` `mi_getBossNode` `mi_getExitNode` `mi_hasExit` |
| **目标读取** | `mi_getTarget` `mi_targetType` `mi_targetCount` `mi_targetBegin` `mi_getSecondTarget` `mi_targetDist` `mi_targetDetectPwd` `mi_getMoveObjectCount` |

同区域还发现 `navgraph.json`、`graph file not found`、`MiniInject2026`、`file not found: %s`、`%s: %s`、`%s[%d]: %s` —— 这些说明图寻路走 `navgraph.json`，且 `mi_*` 名字很可能被用作报错标识（`%s[%d]: %s` 正好是“函数名[行号]: 消息”的形态）。

---

## 5. 完整的执行链路（五层）

```
① Lua 脚本层      外部 .txt 里的 Lua 5.4 源码；阻塞式执行；sleep() 真实占时；
                  脚本只下发意图 + 轮询状态，不自己控制角色移动
        ↓ mi_* / input.*
② Lua 绑定层      按模块用 {名称,函数} 表 + lua_pushcclosure/lua_setfield 注册
                  （input 模块 50 条已确证；mi_* 注册点未定位）
        ↓
③ 主机原生运行时   主循环 0x4a1550 读共享内存状态块 → 路线推进 0x4a5f80
                  到点判定 = 前两轴平面距离 < 50.0 游戏坐标单位
                  命令发布 0x4a4ac0 写 0x78 字节命令块到映射区 +0x110（标志 +0x100、长度 +0x104）
                  卡住监测 0x4a71d0 独立状态机
        ↓
④ 进程桥           共享内存映射（CreateFileMappingW/MapViewOfFile）
                  + RegisterWindowMessageW("ZeusMessage")
                  + 事件 ssjj_input_ready_%u 做同步
        ↓
⑤ 游戏进程内插件   plugin64.dll 挂钩 user32/win32u，让游戏读到的
                  GetAsyncKeyState / GetKeyState / GetKeyboardState /
                  NtUserGetRawInputData 返回脚本合成的按键鼠标状态；
                  另含 Mono 注入能力（mono_get_runtime_domain / mono_runtime_invoke / CMonoInject）
```

③④⑤ 层来自同镜像哈希的 2026-10-12 报告（`evidence/manifest.json` 记录的主镜像 SHA256 与本轮复核一致），本轮把它与①②层接起来。

### 对上一轮原生层结论的独立复核

为避免把上一轮结论当既定事实照搬，本轮对 8 个关键 VA 做了独立反汇编复核。**全部都是真实函数**（标准 MSVC 序言 `push ebp / mov ebp,esp / and esp,-16`，其中导出函数还有 SEH 帧 `push -1 / push <handler>`）：

| VA | 上一轮命名 | 本轮独立复核所见 |
|---|---|---|
| `0x4a1550` | 主循环 | `sub esp,0x188` + 栈 Cookie `mov eax,[0xb91300] / xor eax,esp` |
| `0x4a2aa0` | 运行时加点 | `movss xmm0,[ebp+8]`，与“接受三项 float + 一项整数”一致 |
| `0x4a2b50` | 启动路线 | 立即读 `[0xbb1bb8]` / `[0xbb1bbc]`，与上一轮记录的数组边界全局一致 |
| `0x4a3390` | 读坐标缓存 | `movsd xmm0,[0xbb1c30]` + `cvtpd2ps xmm0,xmm0`，与“读三个 double 再转 float”逐字一致 |
| `0x4a4ac0` | 发布移动命令 | `mov dword ptr [eax+0x100], 1`，与上一轮记录的标志偏移一致 |
| `0x4a5f80` | 路线推进 | 标准序言 + `mov esi,[edx+4]` |
| `0x4a71d0` | 卡住监测 | `cmp byte ptr [0xb94369],0`，与上一轮“可变参数快照”区域一致 |
| `0x4c5d60` | 路线导出 | SEH 帧，内部引用内嵌模板 `0xa3fbf0` 与格式串 `0xa3fe18` |

坐标读取与命令发布两处的寄存器/偏移细节与上一轮报告逐字吻合，因此 ③④⑤ 层可以作为可信基础继续使用。

---

## 6. 未解决项与置信度

| 问题 | 状态 | 说明 |
|---|---|---|
| 脚本引擎是 Lua 5.4.8 | **已确认** | 上游字符串成组出现，且排他性检查全为 0 |
| `input` / `debug` 模块注册机制与 50 个函数 | **已确认** | 反汇编表 + `help.html` 双向吻合 |
| `sleep()` 阻塞真实线程 | **已确认** | 导入 `Sleep` 直接调用 |
| 脚本由外部载入、骨架由程序生成 | **已确认** | 外部样本 + 内嵌模板 + 导出格式串 |
| `mi_*` 的 Lua 绑定调用点 | **未定位**（中等置信推断） | 见下 |
| 它是否读游戏资源文件 | **已确认：不读** | 游戏/引擎产物名全镜像 0 命中；走截图匹配 + 共享内存 |
| 录制 DSL 的生成模板与解析正则 | **已提取** | 见第 8 节；`2/4/6/X` 四个正则的选中含义未定 |
| 0x78 字节命令块与状态块的逐字段布局 | **未解决** | 沿用上一轮结论 |
| 游戏侧朝向（yaw/pitch）如何确定 | **未解决** | 到点判定只验证平面位置 |

### 关于 `mi_*` 绑定点的有界检索记录

为定位 `mi_*` 的 Lua 注册调用，做了四次互不重复的检索，均无结果：

1. 对 74 个名称 VA 做全镜像 dword 引用扫描 —— 没有对齐的 `luaL_Reg` 成对表；命中的是“名称指针 + 落在 SQLite 文本区的指针”这类假阳性，以及大量非对齐单字节巧合。
2. 搜 `mov dword ptr [ebp-disp], 0xa4xxxx`（`input` 模块所用的同一模式）—— 整个镜像 117 处命中、29 个簇，**只有 `0x4e4dec`–`0x4e5178` 一个是真的注册表**，没有任何 `mi_*` 簇。
3. 哈希派发假设（djb2 / fnv1a / crc32 各名称取 dword 存储）—— 命中 1/74、0/74、3/74，且命中位置都在无关数据的非对齐处，**假设不成立**。
4. 对注册函数 `0x4e4de0` 及其助手 `0x44f6d0`/`0x44fc60` 做 `call rel32` 交叉引用 —— **0 处**，说明注册函数是通过存储的函数指针被间接调用的，静态 xref 扫描追不到。

**推断（中等置信）**：`mi_*` 的绑定表位于 VMProtect 虚拟化区域，或在运行时构造；`0xa48504` 起的名称更可能是 `mi_*` 实现内部报错用的参数串（配合 `0xa48a0c` 的 `%s[%d]: %s`），而不是字面量 `luaL_Reg` 表。

**这不妨碍执行原理的结论**：第 1、2、3 层的行为已经由脚本源码、内嵌模板、`input` 模块注册表三条独立证据确定；剩下的只是“哪个函数把 `mi_*` 塞进 Lua 全局”这一个地址没命名。

---

## 7. 它读不读游戏文件？

**结论：没有证据表明它读游戏的 Unity 资源文件。它读的是自己的配置/数据/脚本文件；游戏状态走共享内存拿。**

全镜像搜游戏/引擎产物名，**全部 0 命中**：`.assets`、`.bundle`、`.resS`、`.resource`、`resources.assets`、`Assembly-CSharp`、`UnityPlayer`、`mono-2.0`、`.dll`、`.dat`、`.bin`、`.db`、`.ini`、`.json`、`.png`、`.exe`、`.txt`、`.lua`。注意连 `.txt`、`.json`、`.dll` 这种通用后缀都是 0 —— 属于"代码被虚拟化 + 路径在运行时拼接"的特征，所以这是**对已解包区域的有界否定证据**，不等于"它从不打开任何文件"。

而它自己的文件确实有代码引用：

| VA | 字符串 | 引用点 | 含义 |
|---|---|---|---|
| `0xa48448` | `navgraph.json` | `0x4e1c33` | 图寻路数据从 JSON 加载 |
| `0xa48458` | `graph file not found` | `0x4e1c5b` | 加载失败会报错 |
| `0xa484e8` | `file not found: %s` | `0x4e318f` | 通用文件打开失败 |
| `0xa484a0` | `GameConfig` | 5 处（`0x4e2149`…） | 配置键命名空间 |
| `0xa484ac` / `0xa484b8` | `Max_GoldBox` / `OpenBox_Weapon` | `0x4e2599` / `0x4e25be` | 挂在 GameConfig 下的具体键 |
| `0xa6d5bc` | `%s.%04d%02d%02d.log` | 3 处 | 按日期写日志 |

所以它读三类**自己的**文件：脚本（Lua `.txt`）、路线与配置（`navgraph.json`、`GameConfig` 键、`极速关卡自动坐标.json`、`ssjj_scs.ini`）、以及按状态分目录的模板 PNG（配合 `findPic`/`getColor`，用 `FindPic.dll`/`libpng.dll`/`libjpg.dll`）。

**游戏状态从哪来**：不是文件，是①屏幕截图做模板匹配，②注入的 `plugin64.dll` 写的共享内存。Mono 那几个 API（`mono_image_open_from_data_full`、`mono_assembly_load_from_full`）是从**内存缓冲**加载程序集，不是从磁盘读游戏 DLL——上一轮报告也确认镜像里找不到明文 CLR 元数据。

**判断（高置信）**：它是**外部黑盒控制器**，不是游戏文件解析器。这对你很重要——意味着它做不到的事（读游戏内部数据）你也别指望它；但它的"截图匹配 + 注入共享内存"路线不依赖游戏文件格式，抗版本更新能力反而更强。

---

## 8. 下面还有第三层语言：录制 DSL

除了 Lua 层，主程序还内嵌了一整套**录制记号的中间表示 + 代码生成器**，就是 `公用指令集.txt` 里那套 `_MLD{10}_MLU{400}E{500}`。

**生成模板**（把时间轴还原成脚本文本）：

| VA | 模板 |
|---|---|
| `0xa5be00` | `_MDLY{%d}\r\n鼠标左键按下\r\n_MLD{1}\r\n` |
| `0xa5be24` | `_MDLY{%d}\r\n鼠标左键抬起\r\n_MLU{1}\r\n` |
| `0xa5be48` | `_MDLY{%d}\r\n_MMX{%u}\r\n` |
| `0xa5be78` | `_MDLY{%d}\r\n鼠标中间滚动\r\n_MMS{%s}\r\n` |
| `0xa5beec` | `_MDLY{%d}\r\n鼠标向%s移动\r\n_MMH{%d}\r\n` |
| `0xa5bf18` | `_MDLY{%d}\r\n鼠标向%s移动\r\n_MMV{%d}\r\n` |
| `0xa5bf5c` | `_MDLY{%d}\r\n%s\r\n%s{%d}\r\n` |
| `0xa5c0d0` | `0\r\n_MSDT{0}\r\n\r\n\r\n` |
| `0xa3e6a4` | `_mthrd_AutoPassE{1}\r\nfunc PassEB8DA04CC7()\r\n` |
| `0xa3e6f4` | `end\r\n\r\nfunc mthrd_AutoPassE()\r\n_PassEB8DA04CC7{99999}\r\nend\r\n` |

**解析正则**：`(.*?)\{([-\d]+)\}`（`0xa5bf7c`）、`([A-Z0-9]*4[A-Z0-9]*)\{([0-9+-.]+)\}`（`0xa3e754`）、`...6...`（`0xa3e77c`）、`...X...`（`0xa3e7a4`）、`...2...`（`0xa3e864`），以及 `_MMS\{...\}` / `_MMD\{...\}` / `_MMU\{...\}`。

**语义**（由模板本身确定）：录制得到的时间轴先被归一化成 `<--TOKEN--值-->` 标记（`0xa3e742`/`0xa3e7ce`/`0xa3e7f6`/`0xa3e81e`），再重新生成 `_MDLY{增量}` + `_TOKEN{n}` 文本——**绝对时间被重写成"先延时、再动作"**，而且 `{1}` 是"保持 1 个 tick"而不是"1 毫秒"。生成器还会产出真正的脚本结构：`func <名>()` 函数体、`end`、以及把别的函数当宏调用的包装函数（`_PassEB8DA04CC7{99999}`）。

**这层和 Lua 层怎么共存**：宏名 `_PassEB8DA04CC7` 的 `_` 前缀对应 `input` 模块的短名机制（`mld`/`mlu`/`mms`…），所以录制文本和 `input.*` 调用最终落在**同一个原生输入队列**上。也就是说：

```
录制时间轴 ──归一化成标记──> _MDLY{dt}+_TOKEN{n} 文本 ─┐
                                                      ├─> 原生输入队列(按键,时长,鼠标)
战斗宏 / Lua script ── mi_keySeqAdd / input.*  ────────┘        ↓ 每 tick 推进 50
                                                    游戏(plugin64 Hook 读到合成按键状态)
```

**未解决**：`2/4/6/X` 四个正则各自选择哪一类字符没有确定——"四者靠这个字符区分"是数据事实，但它的含义（键类还是变体）没有证据支持。

---

## 9. 对 MacroFlow 的可复用要点

1. **脚本层与执行层分离**：脚本只表达意图（走哪条路、何时开火），闭环控制在原生层。这比“把录制的键鼠时间轴照着播”稳健得多，值得沿用在跑图设计上。
2. **轮询式状态反馈**：`mi_getPos` / `mi_isRunning` / `mi_getPathIdx` 是可查询的只读量，脚本自己决定验收时机。比回调式更易调试。
3. **到点容差只用平面距离**（50 游戏坐标单位，且不验证朝向）——这正是你要求“必须严格到达且转向正确”时**不能照抄**的地方。上一轮报告已给出建议：必达点须做位置 + 朝向 + 停稳的联合验收。
4. **`sleep` 是真实阻塞**，所以脚本时间轴与游戏帧率、与原生推进线程都不同步；任何依赖“睡 N 毫秒后角色一定到位”的写法都不可靠，必须轮询状态。
5. **输入模拟走游戏进程内 Hook**，不是桌面级 SendInput；但上一轮已证实部分按键查询会保留真实输入、可能叠加，所以“后台执行与前台互不干扰”不能承诺。

---

## 10. 复现方式

```powershell
# 运行态镜像哈希复核
Get-FileHash -Algorithm SHA256 `
  "$env:LOCALAPPDATA\Temp\zeustool-static-research\ZeusTool-loaded-image.bin"

# 镜像内 VA 到文件偏移：file_offset = VA - 0x400000
# 例：0xa3fbf0 模板 = 偏移 0x63fbf0；0x4e4de0 注册函数 = 偏移 0xe4de0
```

`input` 模块注册表的完整 50 条记录、Lua 核心锚点、`mi_*` 全量名称与地址、以及四次有界检索的结论，都记录在
[evidence/lua-execution-evidence.json](evidence/lua-execution-evidence.json)。

### 工具链可用性说明

`rea doctor` 显示本机原先没有配置任何原生分析引擎（Hopper 为 macOS 专用、IDA 无 MCP 注册、`GHIDRA_INSTALL_DIR` 未设置）。本工作区内已存在 Ghidra 12.1.4，因此本轮把它接上 REA 使用（配 `JAVA_HOME` = PyCharm 自带 JBR 21.0.6，满足 Ghidra 12.1.4 的 `application.java.min=21`）。

对 7.7 MB 影像段（VA `0x400000`–`0xBA0000`）跑了 Ghidra 头less 自动分析，但进程停在交互提示符时项目数据库尚未落盘，**未保留 Ghidra Evidence ID**。因此本报告所有结论均基于 REA 认证的镜像 + Capstone 定向反汇编，而非 Ghidra 会话。这一点在证据文件里也已如实标注，后续若要拿到函数级反编译，需要重跑并确保正常退出保存。
