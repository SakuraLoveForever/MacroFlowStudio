# 工作流统一撤销实现计划

目标：将工作流普通步骤、全局模块和工作流设置的人工修改放进同一条最多100步的撤销历史；一次批量操作或完整拖拽为一步。

- [x] 先写无窗口回归，覆盖添加、移动、编辑、批量删除、拖拽、全局模块、待保存字段和工作流切换。
- [x] 在统一草稿提交边界比较内容快照；无变化不记历史，保存文件不清历史，新建/打开及执行产生的状态变化建立新基准。离散命令开始前提交待处理输入，避免添加与上一次改名称合并。
- [x] 用通用“撤销”按钮和表格 Ctrl+Z 替换两个仅撤销删除的旧入口。撤销同时恢复模型、表单及草稿，运行中禁用撤销。
- [x] 运行相关后台测试、受保护全量测试、编译及差异检查，通过后本地提交；不构建、不推送。

技术取舍：复用现有编辑历史的有界快照方式和 `copy.deepcopy`，不引入表格框架或通用命令总线。工作流撤销还原当前文档，不撤销已执行的外部键鼠动作、共享模块库编辑或文件导入导出。

## 开源复用检索

核验日期：2026-10-23。约束为现有 Tk/ttkbootstrap Treeview、Python 3.13、离线桌面，不更换表格、不增加发布依赖。结论：继续使用仓库已有编辑历史的有界快照思路，以独立工作流快照适配当前就地修改的步骤字典。

| 候选 | Star / 维护与发布 | 许可 | 可复用能力与适配成本 | 取舍 |
|---|---|---|---|---|
| [tksheet](https://github.com/ragardner/tksheet) | 页面显示505；[发布页](https://github.com/ragardner/tksheet/releases)标记7.6.0为Latest，完整日期未核实；最新实质代码提交未核实 | [MIT，图标另有ISC](https://github.com/ragardner/tksheet/blob/master/LICENSE.txt) | [文档](https://github.com/ragardner/tksheet/wiki/Version-7)提供行增删、移动及undo；集成会替换现有表格和事件绑定，成本高 | 不引入整套表格来解决文档撤销；README说明目前仅修复问题，新增功能维护有限 |
| [Thonny](https://github.com/thonny/thonny) | 页面约4.0k；最近实质提交和最新发布完整日期未核实 | [MIT](https://github.com/thonny/thonny/blob/master/LICENSE.txt) | Tk编辑器参考；[实现](https://github.com/thonny/thonny/blob/master/thonny/tktextext.py)绑定文本控件，不能直接替代工作流模型历史，成本高 | 只作同技术栈参考，不集成IDE |
| [py-undo-stack](https://github.com/KitwareMedical/py-undo-stack) | 页面3星、5次提交；最近实质提交、最新发布、归档状态未核实 | 本次[许可页](https://github.com/KitwareMedical/py-undo-stack/blob/main/LICENSE)未成功取得正文，不作为可直接复用代码 | 提供命令栈，仍需为所有工作流动作编写命令；改造成本中 | 相关但非高Star成熟首选，引入后比当前快照增加维护面 |

没有找到三个同时满足“高相关、高Star、低集成成本”的独立撤销库，未用不相关项目凑选型。GitHub未认证API触发速率限制，未把无法核验的提交日期、发布年份或许可写成已确认；Star来自本次网页显示而非实时API精确计数。以上成本为按当前接入范围的定性估计。本次没有复制第三方源码或新增依赖；以无窗口集成回归验证撤销顺序、批量边界和深拷贝隔离。

## 验证结果

Python 3.13：`tools/run_headless_tests.py` 全量2239项，2190项通过，49项依赖真实窗口、抓屏或输入事件的旧测试被保护入口跳过。相关范围161项（160通过、1跳过）；源码编译与 `git diff --check` 通过。10项新增无窗口回归覆盖混合编辑、批量/拖拽、元数据、运行计数边界及快照隔离/100步上限。未构建或推送。
