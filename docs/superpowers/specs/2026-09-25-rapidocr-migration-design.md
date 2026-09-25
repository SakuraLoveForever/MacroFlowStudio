# RapidOCR OCR 引擎迁移设计

## 目标

将 MacroFlow Studio 的 OCR 推理后端从 PaddleOCR 换为 RapidOCR，使离线识别部署更轻，同时维持现有 OCR 调用接口和工作流行为。此次变更只涉及 OCR 引擎、OCR 依赖与离线模型的准备/打包/校验，以及必要的 OCR 文档和测试；不调整其他功能模块。

## 已确认的约束

- 用户选择 RapidOCR，并要求其他功能保持不动。
- OCR 仍然按需初始化，并支持源码启动和 Windows 打包版离线运行。
- OCR 调用方继续使用 `recognize_image_with_boxes`、`recognize_image`、`recognize_region`、`recognize_region_with_boxes` 等现有接口。
- 保留现有用户未提交的工作，包括 `ocr.py` 中的 OCR 文本比较改动；实现时只在同一文件内替换引擎接线所需部分，不覆盖或重写该改动。
- 不变更脚本/工作流数据格式、OCR 条件语义、数字提取逻辑、图像匹配、录制/回放、输入、界面或其他模块。
- 不在运行时联网获取权重；软件随本地模型文件运行。

## 当前状态与迁移理由

当前 OCR 包装位于 `src/macroflow/core/ocr.py`，对外接口已经把引擎调用与应用业务解析隔开。它惰性创建 PaddleOCR 单例，接收 BGR 图片，并把识别行和多边形框转换为文本及绝对屏幕坐标。构建脚本把 PaddleOCR、PaddleX、PaddlePaddle、模型和第三方依赖复制到 exe 旁的 `dist/paddle_ocr`；该目录约 477 MiB，模型约 27 MiB。

RapidOCR 的 Python API 接收 NumPy 图像，返回文本、置信度和四点框；项目提供 PP-OCRv6 小模型，文档所述安装 wheel 含默认检测、方向分类和识别模型，约 27.2 MB。运行推理使用 ONNX Runtime。RapidOCR 项目与代码/模型使用 Apache-2.0 授权。由此可望大幅减少 OCR 外置运行时体积，且不需要改写应用业务层。体积缩减是本次迁移的预期目标，最终以 Windows 构建产物实测为准。

参考：

- [RapidOCR GitHub](https://github.com/RapidAI/RapidOCR)
- [RapidOCR v3.9.2 release](https://github.com/RapidAI/RapidOCR/releases/tag/v3.9.2)
- [RapidOCR 安装说明](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/)
- [RapidOCR 使用说明与返回结构](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/)
- [RapidOCR 模型列表](https://rapidai.github.io/RapidOCRDocs/main/en/model_list/)

## 方案

### 后端和模型

- 使用 RapidOCR Python 包，固定到 v3.9.2；使用 CPU 版 ONNX Runtime，不增加 GPU/CUDA 要求。
- 使用 RapidOCR PP-OCRv6 small 中文检测与识别模型，以及同一发行配置中的文字方向分类模型。模型随锁定版本的本地 RapidOCR wheel 提供并由构建校验；不能在 OCR 初始化或识别期间自动下载。
- 初始化时显式提供三个本地模型文件路径，避免默认配置触发外部模型源探测。模型缺失或依赖未安装时，沿用现有清楚、可定位的 OCR 初始化错误路径。
- 保留按需单例与初始化互斥锁；保留粗粒度进度回调。引擎加载完成后，将 BGR NumPy 输入直接交给 RapidOCR。

### 结果适配

RapidOCR 返回的 `boxes`、`txts`、`scores` 由 `recognize_image_with_boxes` 统一适配为现有返回契约：

- 返回值为 `(拼接文本, matches)`；文本仍按引擎识别顺序直接拼接，不额外插入空格。
- 每个带文字的四点框取坐标最小/最大值，向外取整得到左上角和宽高；加上 `origin` 偏移后得到绝对屏幕坐标。
- `matches` 保留 `text`、`x`、`y`、`width`、`height`、`center_x`、`center_y`、`score` 字段。置信度映射为 `score`。
- 无识别结果时返回空字符串和空列表。引擎执行失败仍通过现有 OCR 错误前缀包装异常。
- `recognize_region*` 继续只负责截图和区域原点传递；数字读取、条件比较、OCR 匹配框点击及调用方无需改接口。

检测图像长边限制应沿用当前最大 960 像素策略，避免全屏截图处理开销意外增加。参数名按固定 RapidOCR 版本 API 显式配置并在实现测试中验证。默认分类模型保留现有文字行方向处理能力；不打开文档方向、展平等扫描件功能。

### 离线部署与构建

- 源码运行和打包运行都从本地 RapidOCR 包/模型路径初始化。打包版继续将 OCR 推理依赖和模型放在 exe 同级的独立 OCR 目录，并只在首次使用 OCR 时加入模块搜索路径。
- 外置目录只包含 RapidOCR、ONNX Runtime CPU 及其实际运行依赖和模型；复用应用已打包的 NumPy/OpenCV，不复制 Paddle/PaddleX、PDF、云模型源或无关依赖。
- 构建脚本删除 Paddle 专用复制清单和清理规则，改为 RapidOCR 的精简依赖闭包/固定依赖清单。旧 Paddle 外置目录不作为回退路径；不保留双后端或运行期切换开关。
- 静态构建校验改为检查 exe 未内嵌 Paddle/PaddleOCR/PaddleX，RapidOCR 外置目录、ONNX Runtime 和三个本地模型文件齐全，并保留已有 exe 符号及 GUI 子系统检查。
- 更新启动说明、目录结构、源码环境安装说明和打包说明中的 Paddle 专属名称；只修改与 OCR 迁移直接相关的内容。

## 文件边界

预计实施只触及以下 OCR 相关位置：

- `src/macroflow/core/ocr.py`：替换 Paddle 初始化和结果格式适配；保留当前业务解析与文本比较代码。
- `build.ps1`、`MacroFlowStudio.spec`、`pack.ps1`、`verify_build.py`、`build/ocr_deps_setup.py`：替换 OCR 外置依赖与模型同步、安装包收集项、排除项及静态校验；`AGENTS.md` 只更新安装包中的 OCR 目录名。
- OCR 依赖安装配置、`run.bat`、OCR 模型目录与模型许可/来源说明。
- `README.md`、`README_CN.md`、`README_EN.md` 中 OCR 部署说明，以及 OCR 相关测试。

不改播放器、录制器、输入后端、UI、脚本/工作流存储、图像模板匹配、通用依赖以外的模块。实现过程中先检查当前工作区差异，只提交本次 OCR 迁移所需内容，不覆盖已有未提交修改。

## 验收

### 行为契约

- 保留对外函数和初始化惰性；并发首次调用只初始化一个引擎。
- 用假 RapidOCR 输出覆盖多行顺序、浮点四点框、缺少框/置信度、无命中、负屏幕原点和区域原点偏移。
- 对真实模型做离线冒烟命令验证：从 BGR NumPy 输入得到预期的文本、置信度和坐标形状；不启动或操作可见界面。
- 确认缺模型、缺运行时、模型路径错误和推理异常有可读错误，执行过程中不会访问网络。
- 确认 OCR 数字提取、文本条件、绝对坐标点击等既有测试行为不改变；尤其保留当前工作树中数字 0 混淆处理逻辑。

### 交付验证

- Windows 构建必须成功，OCR 外置目录应显著小于当前约 477 MiB 的 Paddle 目录；报告 exe、OCR 目录和总交付体积实测值，不预先承诺固定压缩比例。
- 运行项目要求的全量单元测试与 `verify_build.py` 静态检查；不做可见界面检查。
- 变更属于 OCR 引擎级替换，交付打包时先完成构建与静态检查，再按项目规则更新 zip。

### 识别质量边界

仓库目前没有应用真实画面的 OCR 基准集，因此不能声称 PP-OCRv6 small 与现有 PP-OCRv5 mobile 在游戏截图上等准确。实现可验证 API/坐标契约、离线加载与打包，但识别质量需要用用户实际场景截图确认；若没有该类样本，只将其报告为尚未建立应用专属准确率基线，不把它当成已经证明的升级。

## 改造成本与风险

- 估计工程成本：1–3 个开发日，主要用于 Windows + Python 3.13 环境下确认 RapidOCR/ONNX Runtime 轮子与模型兼容、精简 PyInstaller 外置依赖、更新离线构建校验，以及完成端到端 OCR 静态/后台验收。
- 识别质量风险：模型从 PP-OCRv5 mobile 换为 PP-OCRv6 small，不同字形、字号、特效背景和低分辨率 HUD 上的准确率尚无本项目基准证明。
- 部署风险：ONNX Runtime Windows DLL 与 PyInstaller 外置加载路径可能出现动态库或架构兼容问题；必须以打包目录离线实测为准。
- 依赖风险：RapidOCR API/配置字段随版本变化，因此锁定版本并测试所用参数；不追随浮动最新版。
- 运行时风险：OCR 仍在 CPU 上运行；线程数和全屏耗时应在真实设备上观察，避免和宏播放输入调度争用资源。
- 授权风险：迁移时随发行包保留 RapidOCR/模型和运行时所需许可文本及来源信息，并审查被携带的 ONNX Runtime 依赖许可。
