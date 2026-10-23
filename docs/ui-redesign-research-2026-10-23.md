# MacroFlow Studio UI 重设计调研报告

- 调研日期：2026-10-23（以下所有 Stars、提交时间、Release 均为当日查询结果）
- 调研范围：信息密集型 Windows 桌面工具（tkinter/ttkbootstrap 单主窗口），不涉及网页仪表盘
- 产出边界：仅本报告，不改 `src/`，不提交 git
- 数据来源：GitHub REST API、PyPI JSON、厂商官方文档、raw.githubusercontent.com；GitHub 网页在本机网络不可直连，故以 API 为准
- 说明：工作区当前有未提交改动（`design.py` 未跟踪；`constants.py`、`app/shell.py`、`app/runtime/shell.py` 已修改），且调研期间正在并行重构：左栏 372px → `RAIL_WIDTH=212`（`constants.py:50`），主区改 `PageStack`（`design.py:411`），脚本页 5 类动作已改 `SegmentedControl` + `FlowFrame` 芯片（`shell.py:647`）
- 口径：「调研起点」指用户反馈时的形态（372px 固定侧栏 + Notebook 四页签 + 脚本页内 5 类页签）；引用现有代码的行号截至 2026-10-23 13:45，重构中可能漂移

---

## 1. 结论先行：最值得借鉴的 3 条策略

### 策略 A：压缩导航层级——单层导航 + 页面级工具栏 + 卡片分组（收益最高）

信息密集型工具的"丑"通常不是配色，而是**层级嵌套与控件堆叠**。调研起点是 4 个主标签里再嵌 5 类动作的 Notebook（旧版 `_build_action_palette`），工具栏按钮按宽度手排折行，侧栏 372px 内塞满设置控件；调研期间实现层已开始把左栏收成 212px 导航轨（`_build_rail`，`shell.py:307`）、主区改无页签的 `PageStack`（`design.py:411`）、动作区改 `SegmentedControl`（`shell.py:647`）。VS Code 官方 UX 指南要求"Keep the number of Views to a minimum"、复用既有图标而非造新控件（[8]）。据此的做法：

- 左侧导航轨作为**唯一导航**（4–6 项，图标+文字，选中态一条强调线），主区一次只显示一个页面（当前 Rail 212px 已符合此结构）；
- 每页顶部固定一条**页面级工具栏**（≤6 个可见控件，分主/次/更多三组，其余进「⋯」菜单）；
- 页内用**卡片**表达分组，而不是再开一层页签；
- 脚本页 5 类动作已改分段筛选器（SegmentedControl），后续只需把筛选结果与表格/操作区对齐，不要回到页签。

### 策略 B：用一套设计变量（tokens）统一全应用，而不是逐页 configure

桌面工具"看起来不一致"的根因是颜色/圆角/间距/字号散落在各页的 `style.configure` 里。可借鉴 WinUI 的尺度：控件圆角 4px、浮层圆角 8px（[4]）；Windows 11 字号梯度 12/16、14/20、18/24、20/28（[5]）；4px 间距基数（Tailwind `--spacing: 0.25rem`，[10]）；shadcn 用单一 `--radius: 0.625rem` 派生整套圆角（[11]）。落到本项目：`constants.py` 只放颜色/字号/窗口常量，`design.py` 的 `configure_theme(root)`（已接入 `runtime/shell.py:314`）统一定义组件样式，页面只消费语义化样式名。

### 策略 C：密度靠排版层级，不靠边框堆叠

信息密集型桌面界面应把"分组"交给**背景色差 + 留白 + 字重**，而不是每个区域都加 1px 描边、每种状态都加色块。VS Code 的活动栏只用 1px 右边框、状态栏 22px 高 12px 字（[6][7]），层级几乎全靠对比度与位置。对本项目的直接结论：

- 只保留一种边框（1px，`COLOR_BORDER`），卡片不叠双层描边；
- 强调色（`COLOR_BLUE`）只用于选中项、主按钮、焦点环，一屏不超过 3 处；
- 表格行高 28–30px，比现状略高，靠行距而不是斑马纹区分；
- 工具栏/状态栏用"图标 + 12px 标签"的紧凑组合，不放大按钮。

---

## 2. 候选项目比较（Stars/日期查询于 2026-10-23）

| 项目 | 仓库链接 | Stars | 最近提交 | 最新 Release | 许可证 | 技术栈 | 可直接借鉴的点 | 集成成本 |
|---|---|---|---|---|---|---|---|---|
| ttkbootstrap | https://github.com/israel-dryer/ttkbootstrap | 2,662 | 2026-09-12 | v2.2.3（2026-09-12） | MIT | Python / Tk ttk 主题 | 语义化配色与 Style 体系、整套主题换肤；本项目已依赖（固定 1.20.3） | 低：已是依赖；1.20.3→2.2.3 有 API 差异，升级需回归 |
| CustomTkinter | https://github.com/TomSchimansky/CustomTkinter | 13,585 | 2026-06-24 | 6.0.0（PyPI，2026-06-24；GitHub 无 Release） | MIT（PyPI 元数据标 CC0，以仓库 LICENSE 为准） | Python / Tk Canvas 自绘控件 | 圆角 6px、按钮高 28px 的默认尺度；Canvas 自绘圆角/hover/focus 的范式；深色主题 JSON 结构 | 中高：独立控件体系，与 ttk 混用会两套观感，不建议整体替换 |
| PyQt-Fluent-Widgets | https://github.com/zhiyiYo/PyQt-Fluent-Widgets | 8,139 | 2026-10-17 | GitHub v1.7.0（2024-10-20）；PyPI 1.11.3（2026-08-01） | GPL-3.0 | Python / PyQt5 | Win11 Fluent 的导航、设置卡片、InfoBar、分段控件的视觉规格与信息密度 | 不可集成（GPL-3.0 + Qt）；仅作视觉参照，不复制代码 |
| maliang（原 tkintertools） | https://github.com/Xiaokang2022/maliang | 1,102 | 2026-10-06 | 3.2.0（2026-10-06） | MIT | Python / Tk，全部 Canvas 自绘 | 纯 Canvas 实现现代控件的完整工程（圆角、动画、主题切换、自适应） | 高：整套框架替换等于重写 UI；可摘绘制与状态管理思路 |
| TkForge | https://github.com/Axorax/tkforge | 477 | 2025-08-27 | 未能核实（GitHub API 限流） | Apache-2.0 | Python / Tk + Figma 插件 | Figma 设计稿 → Tkinter 代码的交付链路，可快速生成布局骨架 | 低（设计工具链）；不改善运行时观感 |
| shadcn/ui | https://github.com/shadcn-ui/ui · https://ui.shadcn.com | 126,261 | 2026-10-22 | shadcn@4.22.1（2026-10-18） | MIT | React / Tailwind（源码复制进项目） | Token 命名法（background/foreground/muted/border/ring）、单一 `--radius` 派生、focus ring、空状态模式 | 低：只借规范，无运行时依赖 |
| Linear | https://linear.app/method（无公开 UI 仓库） | 不适用 | 不适用 | 不适用 | 专有 | Web | 键盘优先、命令面板、列表密度、极少分隔线；一页一个主动作 | 低：视觉/交互参照 |
| Raycast | https://www.raycast.com · https://github.com/raycast/extensions | 产品不适用；扩展仓库 7,805 | 扩展仓库 2026-10-23 | 不适用（产品闭源） | 专有（产品）/ MIT（扩展仓库） | Swift / React | 命令面板 + 列表/操作面板的分组标题、快捷键提示、深色层级 | 低：视觉/交互参照 |
| VS Code | https://github.com/microsoft/vscode | 194,223 | 2026-10-23 | 1.143.1（2026-10-22） | MIT | TypeScript / Electron | 官方 UX Guidelines（Views 最小化、图标复用、Tree View 用法）；状态栏 22px/12px、活动栏 48px/1px 边框的实测规格 | 低：规范与数值参照 |

选型结论：

- **运行时只保留 ttkbootstrap**（已依赖），升级到 2.2.3 作为可选后续动作；不建议引入 CustomTkinter 或 Qt 重写。
- **视觉规格优先抄 qfluentwidgets**（Fluent 的导航/卡片/分段控件），但只抄"长什么样"，不引入 GPL 代码。
- **Canvas 自绘的技术路线参考 maliang 与 CustomTkinter**：它们证明圆角、hover、focus 在 Tk 里可行，代价是每个控件要自己画/自己测。
- **信息密度与工具栏分组抄 VS Code**，**token 命名抄 shadcn**，**键盘优先与命令面板抄 Linear/Raycast**。

---

## 3. 设计要点清单（每个数字给出处；查不到的标「未核实」）

| 要点 | 建议值 / 做法 | 依据 |
|---|---|---|
| 颜色 | 3 层表面：`#0D1217`（底）/`#18222B`（卡片）/`#1F2A34`（悬浮）；文字 `#E6EDF3`/`#A7B4C0`/`#82909D`；强调 `#4C8DFF`，语义红/绿/黄 `#F85149`/`#3FB950`/`#D29922`；强调色一屏 ≤3 处 | 本项目 `constants.py:19-38`（[1]）；VS Code 状态栏指南"不要添加自定义颜色"（[9]） |
| 边框 | 全应用只用 1px；区域分隔 `#2E3B47`；卡片不叠描边，用背景色差表达层级 | `constants.py`（[1]）；VS Code 活动栏 `border-right-width: 1px` 实测（[7]） |
| 圆角 | 控件 4px，卡片/浮层 8px，分段控件内 pill 6px（=8-2） | WinUI `ControlCornerRadius` 默认 4、`OverlayCornerRadius` 默认 8（[4]）；项目 `RADIUS = 8`、`SegmentedControl` 用 `RADIUS - 2`（[3]）；shadcn 10px 偏大，桌面端不采用（[11]） |
| 间距节奏 | 4px 基数：4/8/12/16/24/32；卡片内边距 12–14，卡片间距 12；控件间隙 6–8 | Tailwind `--spacing: 0.25rem`（[10]）；WinUI 行高 16/20/24 与 4 对齐（[5]）；项目 `Card` 默认 `padding=(14,12,14,12)`、`pady=pad(0,9)`（[3]） |
| 字号层级 | 次要 12px、正文 13–14px、区块标题 16px、页面标题 20px；用负数字号（像素），弃用 8/9/10/13pt 混排 | WinUI 梯度 12/16、14/20、18/24、20/28（[5]）；Tk 字体"正数=点、负数=像素"（[14]）；现状 `FONT_SMALL=8/FONT_BODY=9/FONT_SUBTITLE=10/FONT_TITLE=13`（[1]），按 96 DPI 约合 10.7/12/13.3/17.3px |
| 表格行高 | 28–30px（正文行高 20 + 上下各 4px）；表头 padding 8/5 可保留；列宽给"名称"最小 160、"详情"自适应 | 推算自 WinUI Body 14/20（[5]）；现状 `rowheight=linespace+px(10)`≈25–26px、表头 `padding=pad(8,5)`（[3]） |
| 工具栏分组 | 单行 ≤6 个可见控件；分「主操作（左）/ 次操作（中，图标）/ 更多（右，⋯）」；溢出项进菜单；图标按钮 20px，文字按钮高 28px | VS Code UX"Views 数量最小化""复用现有图标"（[8]）；CustomTkinter 按钮默认高 28px（[12]）；现状动作芯片用 `FlowFrame` 折行（`shell.py:660`）、页头 actions 单行（[2]） |
| 空状态 | 居中：图标 32px + 标题（14–16px）+ 一行说明（12px）+ 1 个主按钮；上下留 24–32px；复用 `Empty.TLabel` | 设计约定，无官方统一规格 —— **未核实**；样式已存在（[3]） |
| 状态栏 | 高 22–24px、12px 字；左侧主状态（录制/执行/目标窗口），右侧次要信息（版本、DPI、快捷键提示）；不加背景色块，只用 1px 上边框 | VS Code 状态栏 `height: 22px; font-size: 12px; line-height: 22px` 实测（[6]）；现状 `padding=pad(18,5,18,5)`、高度随内容（[2]） |
| 图标策略 | 统一 16/20px 单色线条图标；优先 Segoe MDL2 Assets / Segoe Fluent Icons 字形；关键彩色状态用 Pillow 生成 PNG（走 `@2x`）；禁止 emoji 与线宽混排 | Windows 符号字体（[15]）；现状 `ACTION_ICONS` 为 Unicode 符号、菜单混用 📂/⬇（[1][2]）；无矢量/SVG 能力（见 4.3） |
| 焦点环 | 2px `#4C8DFF`，内缩 1px 绘制；所有可 Tab 控件必须可见 | 项目 `SidebarButton` 2px 焦点描边、`design.py` 内嵌 1px 焦点环（[2][3]）；键盘可达要求为设计约定（未核实） |

> 「未核实」的定义：没有找到官方或源码级出处，按经验给的建议值；落地时应以试用稿评审为准。

---

## 4. 对本项目的落地建议

### 4.1 目标结构：单层导航 + 页面级工具栏 + 卡片分组（已落地大半）

```
Rail（212px，唯一导航；可折叠为 56px 图标轨）
├─ 品牌区（logo + MacroFlow / 自动化工作台）
├─ 导航项：脚本编辑 / 工作流 / 运行日志 / 插件 / 设置
└─ 全局动作：开始录制 / 执行 / 停止
主区（PageStack，无页签条）
├─ PageHeader：标题（FONT_TITLE 粗）+ 副标题（FONT_SMALL）+ 右侧主操作
├─ 页面工具栏：主操作 / 次操作 / ⋯ 更多
└─ 内容：Card 分组（内边距 12–14px）+ 表格（28–30px 行高）
```

对照：单层导航（`_build_rail` + `PageStack`）与脚本页动作分段控件已落地；仍待统一的是页面工具栏、卡片节奏、状态栏与空状态。

现状到目标还需做的事：

1. **页面级工具栏归一**：脚本页现在是「PageHeader（新建/打开/撤销/保存/⋯）→ 脚本信息卡 → 添加动作卡」三段，建议把高频编辑动作（插入/删除/上移/下移/复制）收进一条页面工具栏，页头只留页面级动作（新建/打开/保存/更多），降低首屏高度。
2. **设置页卡片化**：设置已拆为独立页（`shell.py:227`），方向正确；检查页内是否同样用 `Card` 分组，避免又出现一列长表单。
3. **动作芯片分组**：5 类动作 + 芯片折行已替代嵌套页签（`_build_action_palette`，`shell.py:647`）；建议每组加 `ToolGroupTitle` 分隔（样式已有），芯片最多 2 行，超出进「更多」。
4. **状态栏与日志页**：状态栏固定 22–24px；运行日志页用「工具栏 + 表格 + 空状态」三件套，与脚本页同构。
5. **侧栏宽度**：212px 已接近建议的 200–220px；不要回到 372px，后续需要二级列表时再用 56px 图标轨折叠。

### 4.2 能在 tkinter/ttk 里直接实现

- **Canvas 圆角**：已有 `draw_rounded()`（`design.py`，`smooth` 多边形）与 `SidebarButton` 自绘先例，可复用到卡片、分段控件、徽标、焦点环。
- **1px 卡片边框**：`tk.Frame(highlightthickness=1, highlightbackground=COLOR_BORDER)`；不需要 ttk 样式支持。
- **表面分层/文字层级**：背景色、前景色、粗体/常规字重、Treeview `rowheight`/`heading` padding/列宽均可直接配置（`design.py:224-246` 已有样式骨架）。
- **工具栏与状态栏**：普通 Frame + Label/Button 即可，分组、左右对齐、固定高度都能做到。
- **键盘焦点**：Canvas `takefocus` + `FocusIn/FocusOut` 重绘（`design.py` 的 `_Segment` 已实现）；Tooltip 已有现成实现（`shell.py:1321`）。
- **空状态**：Label + Canvas 图标 + 按钮组合，无需新依赖。

### 4.3 不能直接做，以及替代方案

| 做不到 | 原因 | 替代方案 |
|---|---|---|
| 原生圆角/阴影/毛玻璃 | ttk Frame/Entry/Button 是矩形，Tk 无合成器级效果 | Canvas 画圆角 + 1px 内描边 + 背景色差；阴影直接不做或退化为 1px 浅色描边（不做 2 层以上） |
| 矢量图标 / SVG | Tk 无 SVG 渲染 | Segoe MDL2 Assets / Segoe Fluent Icons 字形（16/20px，单色）；彩色图标用 Pillow 生成 PNG（项目已依赖 Pillow 12.2.0），按 DPI 出 `@2x` |
| 原生过渡动画 | ttk 无动画 API | `after()` 步进改色/改几何，或直接省略（信息型工具不需要） |
| 自绘 Notebook 标签形状 | ttk Notebook 的 tab 由主题元素绘制，形状不可完全重定义 | **不用 Notebook 做导航**：Rail（`NavItem`）+ `PageStack` + 分段控件切换 |
| 控件内边距/行高自由控制 | Entry/Combobox 高度受字体与 `padding` 约束 | 统一字号与 padding，必要时 Canvas 自绘（仅用于关键控件） |
| 高 DPI 精确对齐 | 像素随缩放变化 | 沿用现有 `px()`/`pad()` 换算；字号用负数值（像素）；图标按 DPI 出图 |
| 系统级字体替换 | 需安装字体 | 正文 `Microsoft YaHei UI`（现状），可回退 `Segoe UI Variable Text`；等宽 `Consolas`（现状） |

### 4.4 剩余动作（按已落地部分排序）

1. **tokens 收尾**：`configure_theme(root)` 已接管全局样式；把尚未覆盖的 `TNotebook`/`TSeparator`/`TScrollbar` 清干净，页面里不再出现硬编码颜色。
2. **页面工具栏归一**：脚本页把插入/编辑动作压到一条工具栏（见 4.1 第 1 条），「更多」菜单承接低频操作。
3. **密度收尾**：表格行高 28–30px、状态栏固定 22–24px（12px 字）、卡片间距 10–12px，全部对齐 4px 网格。
4. **图标统一**：动作用 Unicode 或 Segoe MDL2 字形二选一，去掉菜单里的 emoji；关键状态用 Pillow 出 PNG。

### 4.5 验收指标（可量化）

- 脚本页首屏控件层级：2 层页签 → 1 层筛选 + 1 行工具栏；动作区芯片 ≤2 行；
- 全应用字号种类 ≤4 档、圆角种类 ≤3 档（4/6/8）、边框粗细只有 1px 一种；
- 1024×600 最小窗口与 125%/150% 缩放下无截断；
- 键盘 Tab 顺序合理、焦点环全部可见；
- 不允许出现：emoji 与线条图标混排、同页两种强调色、卡片双层描边、16px 以上圆角。

---

## 5. 数据核验与未核实项

- Stars、最近提交、Release：均为 2026-10-23 通过 GitHub REST API / PyPI JSON 读取；Linear 无公开 UI 仓库，Raycast 产品闭源（扩展仓库 MIT，7,805★）。
- **未核实**：TkForge 的最新 Release（查询时 API 限流，未取到）；Linear/Raycast 的精确像素规格；tkforge 的当前维护状态仅依据最近提交 2025-08-27。
- qfluentwidgets 的 GitHub Release（v1.7.0，2024-10-20）明显落后于 PyPI（1.11.3，2026-08-01），选型时以 PyPI 版本为准。
- 本仓库 `design.py` 为 2026-10-23 新建的未提交文件，已由 `runtime/shell.py:314` 调用 `configure_theme`；本报告引用的是当日 13:45 的重构中间态（`RAIL_WIDTH=212`、`PageStack`、`SegmentedControl` 已接入），行号会随后续提交变化。
- 全量测试/编译验证不适用：本次只新增文档，未改代码。

---

## 6. 参考链接（访问日期均为 2026-10-23）

[1] 本项目 `src/macroflow/ui/app/constants.py:19-50`（颜色、字体、窗口尺寸、`PAGE_PAD_X/Y`、`RAIL_WIDTH`、`ACTION_ICONS`）
[2] 本项目 `src/macroflow/ui/app/shell.py`（行号截至 2026-10-23 13:45）：`:79` SidebarButton、`:204` 状态栏、`:221` PageStack 实例、`:307` `_build_rail`、`:647` `_build_action_palette`、`:692` 更多菜单、`:735` 动作按钮定义
[3] 本项目 `src/macroflow/ui/app/design.py`（未提交）：`:23` `RADIUS=8`、`:38` `draw_rounded()`、`:47` `configure_theme()`、`:224-246` Notebook/Treeview 样式、`:411` `PageStack`、`:460` `Card`、`:560` `SegmentedControl`、`:600` `FlowFrame`；接入点 `runtime/shell.py:314`
[4] WinUI 圆角规格：https://learn.microsoft.com/en-us/windows/apps/design/style/rounded-corner（`ControlCornerRadius` 4px、`OverlayCornerRadius` 8px）
[5] Windows 11 字体梯度：https://learn.microsoft.com/en-us/windows/apps/design/style/typography（12/16、14/20、18/24、20/28 等）
[6] VS Code 状态栏样式：https://raw.githubusercontent.com/microsoft/vscode/main/src/vs/workbench/browser/parts/statusbar/media/statusbarpart.css（`height: 22px`、`font-size: 12px`）
[7] VS Code 活动栏样式：https://raw.githubusercontent.com/microsoft/vscode/main/src/vs/workbench/browser/parts/activitybar/media/activitybarpart.css（宽 48px、`border-right-width: 1px`）
[8] VS Code UX Guidelines：https://code.visualstudio.com/api/ux-guidelines/views（Views 最小化、图标复用、Tree View 用法）
[9] VS Code 状态栏 UX：https://code.visualstudio.com/api/ux-guidelines/status-bar（左侧主状态、右侧次要、图标按需、勿自定义颜色）
[10] Tailwind 间距基数：https://github.com/tailwindlabs/tailwindcss/blob/main/packages/tailwindcss/theme.css（`--spacing: 0.25rem`）
[11] shadcn/ui 主题与圆角：https://ui.shadcn.com/docs/theming（`--radius: 0.625rem` 及派生规则）
[12] CustomTkinter 控件默认值：https://github.com/TomSchimansky/CustomTkinter/blob/master/customtkinter/windows/widgets/ctk_button.py（`height: int = 28`）、https://raw.githubusercontent.com/TomSchimansky/CustomTkinter/master/customtkinter/assets/themes/dark-blue.json（CTkButton `corner_radius=6`/`border_width=0`，CTkEntry `border_width=2`/`corner_radius=6`）
[13] ttkbootstrap 1.20.3 源码（本机 site-packages）：`style.py` 仅在 Switch/Checkbutton 等自绘元素使用 `rounded_rectangle`，`themes/standard.py` 定义主题色，`icons.py` 为 base64 图标 + emoji，无图标字体
[14] Tk 字体字号语义：https://docs.python.org/3/library/tkinter.font.html（正数为点、负数为像素）
[15] Windows 符号字体：https://learn.microsoft.com/en-us/windows/apps/design/style/segoe-ui-symbol-font（Segoe MDL2 Assets；Windows 11 对应 Segoe Fluent Icons）
