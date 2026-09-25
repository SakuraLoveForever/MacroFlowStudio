# RapidOCR OCR 引擎迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 将 MacroFlow Studio OCR 推理后端换成 RapidOCR，让离线 OCR 部署更轻，同时保持现有接口、结果语义和其他功能不变。

**Architecture:** 在现有 OCR 包装模块中惰性加载 RapidOCR v3.9.2，并将其文本、四点框和分数转换成应用现有的结果字典。使用 ONNX Runtime CPU 和本地 wheel 内的 PP-OCRv6 small 检测/识别模型及 PP-OCRv4 mobile 方向分类模型；将 RapidOCR、运行时和模型作为单独的离线目录打包，不保留 Paddle 回退。

**Tech Stack:** Python 3.13, RapidOCR 3.9.2, ONNX Runtime CPU 1.24.2, NumPy/OpenCV, PyInstaller, PowerShell, unittest.

**Spec:** docs/superpowers/specs/2026-09-25-rapidocr-migration-design.md

**Implementation references:** [RapidOCR v3.9.2 API](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/), [RapidOCR local models](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/how_to_use_offline_model/), [ONNX Runtime 1.24.2 wheels](https://pypi.org/project/onnxruntime/1.24.2/).

## Global Constraints

- 用户选择 RapidOCR，并要求其他功能保持不动。
- OCR 仍按需初始化，支持源码启动和 Windows 打包版离线运行。
- 现有 OCR 调用接口、工作流行为、数据格式、数字提取、条件比较和点击坐标语义保持不变。
- 使用 RapidOCR Python 包，固定到 v3.9.2；使用 CPU 版 ONNX Runtime，不增加 GPU/CUDA 要求。
- 使用 PP-OCRv6 small 中文检测与识别模型，以及同一发行配置中的文字方向分类模型。
- 不在运行时联网获取权重；软件随本地模型文件运行。
- 检测图像长边限制沿用当前最大 960 像素策略。
- 保留 ocr.py 中已有的用户未提交改动；尤其不能覆盖数字 0 混淆处理。
- 不改输入、录制/回放、图像匹配、UI、脚本/工作流存储或其他无关模块。
- 构建、全量测试、verify_build.py 和重大修改的 zip 打包按仓库 AGENTS.md 执行；不启动或操作可见界面。

## Review Focus

- **无检测文字：** RapidOCR 返回 None 或空框/文本时，应返回 ("", [])；在 Task 1 添加相应适配器测试。
- **空框与不完整分数：** 有文本但没有对应框时只拼接文本；缺少分数时 score 使用现有默认值 1.0；在 Task 1 测试。
- **旋转方向：** 推理配置必须保留默认文字行方向分类阶段；在 Task 2 验证传入 Cls 参数并通过带方向分类的实际模型初始化。
- **负屏幕坐标和浮点四点框：** 每个轴向下取整左/上、向上取整右/下，再加区域原点；在 Task 1 锁定测试。
- **离线与模型缺失：** 三个模型路径必须在引擎创建前存在，缺失时立即给可读错误，正常加载不触网；在 Task 2 和 Task 4 验证。

---

## 文件结构

- **Create — tests/test_ocr_engine.py:** RapidOCR 适配、路径、惰性单例和故障语义的独立测试；避免触碰用户已经修改的 tests/test_recognition.py。
- **Modify — src/macroflow/core/ocr.py:** 只替换 Paddle 引擎初始化和结果格式适配，保留该文件现有业务解析及未提交修改。
- **Create — requirements-ocr.txt:** 固定 RapidOCR、ONNX Runtime 和源码运行所需的 OCR 依赖；复用主程序已有的 NumPy、OpenCV 和 Pillow，不再安装第二份 OpenCV。
- **Modify — build/ocr_deps_setup.py, build.ps1:** 计算/同步 RapidOCR 外置依赖、模型和原生 DLL，停止构建 Paddle 运行目录。
- **Modify — MacroFlowStudio.spec:** 排除需要运行时外置加载的 RapidOCR/ONNX Runtime，同时保留其他应用依赖收集方式。
- **Modify — verify_build.py:** 静态检查 RapidOCR 目录、模型、运行时 DLL 及 exe 中不含 OCR 引擎。
- **Modify — pack.ps1, AGENTS.md:** 将发布 zip 的 OCR 目录从 Paddle 路径改为 RapidOCR 路径；AGENTS.md 只改这条路径说明。
- **Modify — tests/test_import_boundaries.py, tests/check_packaged_ocr.py:** 检查纯模块不导入新 OCR 后端；以本地生成图像和离线网络拦截验证打包 OCR。
- **Modify — README.md, README_CN.md, README_EN.md, CHANGELOG.md, run.bat:** 更新当前 OCR 引擎、源码依赖和打包目录说明；保留 RapidOCR/模型/运行时所需许可和来源信息。保留历史发布说明，不对历史文档做全局替换。

## Task 1: 先锁定 OCR 结果适配契约

**Files:**
- Create: tests/test_ocr_engine.py
- Test: src/macroflow/core/ocr.py public result adapter

**Interfaces:**
- Consumes: recognize_image_with_boxes(screen: np.ndarray, origin: tuple[int, int] = (0, 0))
- Produces: (str, list[dict])，每个字典含 text、x、y、width、height、center_x、center_y、score。

- [ ] **Step 1: 写 RapidOCR 返回结构的失败测试**

在新测试文件添加下面的导入和测试类，锁定识别顺序、浮点框向外取整、负屏幕原点和 score：

~~~python
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from macroflow.core.ocr import recognize_image_with_boxes


class RapidOCRAdapterTests(unittest.TestCase):
    def test_adapts_rapidocr_boxes_and_negative_origin(self):
        output = SimpleNamespace(
            boxes=np.array([[
                [5.2, 4.1], [45.6, 4.2], [45.4, 24.8], [5.0, 24.0],
            ]], dtype=np.float32),
            txts=("可领取",),
            scores=(0.98,),
        )
        engine = Mock(return_value=output)

        with patch("macroflow.core.ocr._get_engine", return_value=engine):
            text, matches = recognize_image_with_boxes(
                np.zeros((40, 60, 3), dtype=np.uint8), (-1920, 1080),
            )

        self.assertEqual(text, "可领取")
        self.assertEqual(
            {key: matches[0][key] for key in (
                "text", "x", "y", "width", "height",
                "center_x", "center_y", "score",
            )},
            {
                "text": "可领取", "x": -1915, "y": 1084,
                "width": 41, "height": 21,
                "center_x": -1895, "center_y": 1094, "score": 0.98,
            },
        )
        engine.assert_called_once()

    def test_empty_output_returns_empty_text_and_matches(self):
        output = SimpleNamespace(boxes=None, txts=None, scores=None)
        with patch("macroflow.core.ocr._get_engine", return_value=Mock(return_value=output)):
            text, matches = recognize_image_with_boxes(
                np.zeros((10, 10, 3), dtype=np.uint8),
            )
        self.assertEqual((text, matches), ("", []))

    def test_text_without_box_is_kept_but_has_no_match(self):
        output = SimpleNamespace(boxes=None, txts=("文字",), scores=(0.9,))
        with patch("macroflow.core.ocr._get_engine", return_value=Mock(return_value=output)):
            text, matches = recognize_image_with_boxes(
                np.zeros((10, 10, 3), dtype=np.uint8),
            )
        self.assertEqual((text, matches), ("文字", []))

    def test_missing_score_uses_existing_default(self):
        output = SimpleNamespace(
            boxes=np.array([[[1, 1], [8, 1], [8, 6], [1, 6]]], dtype=np.float32),
            txts=("A",), scores=(),
        )
        with patch("macroflow.core.ocr._get_engine", return_value=Mock(return_value=output)):
            _text, matches = recognize_image_with_boxes(
                np.zeros((10, 10, 3), dtype=np.uint8),
            )
        self.assertEqual(matches[0]["score"], 1.0)

    def test_text_and_matches_preserve_rapidocr_result_order(self):
        output = SimpleNamespace(
            boxes=np.array([
                [[50, 1], [60, 1], [60, 8], [50, 8]],
                [[5, 1], [15, 1], [15, 8], [5, 8]],
            ], dtype=np.float32),
            txts=("先", "后"), scores=(0.9, 0.8),
        )
        with patch("macroflow.core.ocr._get_engine", return_value=Mock(return_value=output)):
            text, matches = recognize_image_with_boxes(
                np.zeros((10, 70, 3), dtype=np.uint8),
            )
        self.assertEqual(text, "先后")
        self.assertEqual([item["text"] for item in matches], ["先", "后"])

    def test_engine_exception_keeps_ocr_error_prefix_and_cause(self):
        engine = Mock(side_effect=ValueError("bad model"))
        with patch("macroflow.core.ocr._get_engine", return_value=engine):
            with self.assertRaisesRegex(RuntimeError, "^OCR 识别失败：") as caught:
                recognize_image_with_boxes(np.zeros((10, 10, 3), dtype=np.uint8))
        self.assertIsInstance(caught.exception.__cause__, ValueError)
~~~

再添加两个文本框的结果顺序测试，断言拼接顺序与 RapidOCR 的 txts 顺序相同，不按坐标重排。

- [ ] **Step 2: 运行测试并确认旧 API 失败**

Run: python -m unittest tests.test_ocr_engine.RapidOCRAdapterTests

Expected: RapidOCR 假引擎是可调用对象而不是 Paddle 的 predict 对象，因此至少坐标适配测试失败；新增测试本身能被 unittest 正确发现。

- [ ] **Step 3: 写最小结果适配实现**

在 recognize_image_with_boxes 中调用 _get_engine()(screen)；从结果对象读取 txts、boxes、scores。文本按 txts 顺序直接拼接；对有文本且有有效四点框的项计算 min/max、floor/ceil 和原点偏移；分数缺失使用 1.0。对 None/空序列归一化成空文本和空列表；保留现有异常包装。

- [ ] **Step 4: 重跑适配器测试和现有 OCR 业务测试**

Run:
- python -m unittest tests.test_ocr_engine.RapidOCRAdapterTests
- python -m unittest tests.test_recognition

Expected: 两条命令均通过；既有 matches_expected、数字读取和坐标点击行为保持原样。

## Task 2: 替换惰性引擎初始化和源码依赖

**Files:**
- Modify: src/macroflow/core/ocr.py
- Create: requirements-ocr.txt
- Modify: run.bat
- Test: tests/test_ocr_engine.py

**Interfaces:**
- Consumes: Task 1 的 RapidOCR adapter。
- Produces: _get_engine() 返回可调用 RapidOCR 单例；无论源码还是冻结版都使用相同的本地模型文件和参数。

- [ ] **Step 1: 为初始化写失败测试**

在 tests/test_ocr_engine.py 中添加下列结构的测试，假包让测试不要求当前开发环境安装 RapidOCR；临时文件验证离线路径校验。另用 ThreadPoolExecutor 同时调用 _get_engine()，断言只构造一个引擎实例。

~~~python
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from macroflow.core import ocr


class RapidOCREngineInitializationTests(unittest.TestCase):
    def test_engine_uses_local_models_and_cpu_configuration(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {name: root / f"{name}.onnx" for name in ("det", "cls", "rec")}
            for path in paths.values():
                path.write_bytes(b"model fixture")
            fake_package = ModuleType("rapidocr")
            engine = Mock()
            factory = Mock(return_value=engine)
            fake_package.RapidOCR = factory
            progress = Mock()

            with patch.object(ocr, "_engine", None), \
                 patch.object(ocr, "_progress_callback", progress), \
                 patch.object(ocr, "_rapidocr_model_paths", return_value=paths), \
                 patch.dict("sys.modules", {"rapidocr": fake_package}):
                first = ocr._get_engine()
                second = ocr._get_engine()

            self.assertIs(first, second)
            factory.assert_called_once()
            params = factory.call_args.kwargs["params"]
            self.assertEqual(params["Det.model_path"], str(paths["det"]))
            self.assertEqual(params["Cls.model_path"], str(paths["cls"]))
            self.assertEqual(params["Rec.model_path"], str(paths["rec"]))
            self.assertEqual(params["Det.engine_type"], "onnxruntime")
            self.assertEqual(params["Det.ocr_version"], "PP-OCRv6")
            self.assertEqual(params["Cls.model_type"], "mobile")
            self.assertEqual(params["Cls.ocr_version"], "PP-OCRv4")
            self.assertEqual(params["Rec.model_type"], "small")
            self.assertEqual(params["Rec.ocr_version"], "PP-OCRv6")
            self.assertFalse(params["EngineConfig.onnxruntime.use_cuda"])
            self.assertEqual(params["Global.max_side_len"], 960)
            self.assertEqual(params["Det.limit_type"], "max")
            progress.assert_any_call("OCR 引擎已加载", 100)

    def test_concurrent_first_call_creates_only_one_engine(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {name: root / f"{name}.onnx" for name in ("det", "cls", "rec")}
            for path in paths.values():
                path.write_bytes(b"model fixture")
            fake_package = ModuleType("rapidocr")
            factory = Mock(return_value=Mock())
            fake_package.RapidOCR = factory

            with patch.object(ocr, "_engine", None), \
                 patch.object(ocr, "_rapidocr_model_paths", return_value=paths), \
                 patch.dict("sys.modules", {"rapidocr": fake_package}):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(ocr._get_engine) for _ in range(8)]
                    engines = [future.result() for future in futures]

            self.assertTrue(all(engine is engines[0] for engine in engines))
            factory.assert_called_once()

    def test_missing_model_fails_before_engine_construction(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {name: root / f"{name}.onnx" for name in ("det", "cls", "rec")}
            paths["det"].write_bytes(b"model fixture")
            fake_package = ModuleType("rapidocr")
            factory = Mock()
            fake_package.RapidOCR = factory

            with patch.object(ocr, "_engine", None), \
                 patch.object(ocr, "_rapidocr_model_paths", return_value=paths), \
                 patch.dict("sys.modules", {"rapidocr": fake_package}):
                with self.assertRaisesRegex(RuntimeError, "cls.onnx"):
                    ocr._get_engine()

            factory.assert_not_called()
~~~

初始化单例测试还要 patch _progress_callback 为 Mock，并断言 RapidOCR 初始化成功后收到 ("OCR 引擎已加载", 100)。每项测试前将 ocr._engine patch 为 None，防止测试间共享状态。

- [ ] **Step 2: 运行初始化测试并确认失败**

Run: python -m unittest tests.test_ocr_engine.RapidOCREngineInitializationTests

Expected: 旧代码导入 paddleocr，RapidOCR 假模块和本地 ONNX 模型路径测试失败。

- [ ] **Step 3: 实现 RapidOCR 初始化与离线模型校验**

只改 ocr.py 的引擎常量、路径助手和 _get_engine()。新增 _rapidocr_model_paths()，从冻结版 exe 同级 rapidocr_ocr/rapidocr 的本地 wheel 模型映射和源码 .deps/rapidocr 的同一模型映射返回 det/cls/rec 路径；逐一检查三个文件存在。构造参数按下面配置，不调用下载器：

~~~python
params = {
    "Det.engine_type": "onnxruntime",
    "EngineConfig.onnxruntime.use_cuda": False,
    "Det.lang_type": "ch",
    "Det.model_type": "small",
    "Det.ocr_version": "PP-OCRv6",
    "Det.model_path": str(paths["det"]),
    "Det.limit_side_len": 960,
    "Det.limit_type": "max",
    "Cls.engine_type": "onnxruntime",
    "Cls.lang_type": "ch",
    "Cls.model_type": "mobile",
    "Cls.ocr_version": "PP-OCRv4",
    "Cls.model_path": str(paths["cls"]),
    "Rec.engine_type": "onnxruntime",
    "Rec.lang_type": "ch",
    "Rec.model_type": "small",
    "Rec.ocr_version": "PP-OCRv6",
    "Rec.model_path": str(paths["rec"]),
    "Global.max_side_len": 960,
}
_engine = RapidOCR(params=params)
~~~

源码从 run.bat 已加入的 .deps 导入 RapidOCR；冻结版只在首次调用时把 exe 同级 rapidocr_ocr 加入 sys.path。保留 _engine_lock、进度回调和现有用户文本匹配/数字解析代码；将缺失依赖错误改成 RapidOCR/ONNX Runtime 诊断信息。

- [ ] **Step 4: 增加 OCR 依赖安装清单**

创建 requirements-ocr.txt，固定 rapidocr==3.9.2 和 onnxruntime==1.24.2，并列出 RapidOCR/ONNX Runtime 运行所需且主程序 requirements.txt 未提供的包及版本。按两个 wheel 的 METADATA 递归核对传递依赖；排除主 requirements 已提供的 NumPy、Pillow 和 six，并用 opencv-python-headless 提供 RapidOCR 所需 cv2 模块，避免安装第二份 opencv-python。OCR requirements 的安装命令带 --no-deps，因为该文件会逐项列出经核对的完整依赖集合。更新 run.bat 注释和 README 源码安装命令，不再提 Paddle。安装和导入验证命令为：

~~~powershell
& C:\Python313\python.exe -m pip install --target .deps --no-deps -r requirements-ocr.txt
& C:\Python313\python.exe -c "import rapidocr, onnxruntime; print(rapidocr.__file__, onnxruntime.__version__)"
~~~

- [ ] **Step 5: 运行 OCR 引擎测试**

Run: python -m unittest tests.test_ocr_engine

Expected: adapter、单例、配置、模型缺失、离线路径和异常包装测试均通过；同时运行 python -m unittest tests.test_recognition，确认用户已有 OCR 比较改动仍通过。

## Task 3: 重建轻量离线打包链路

**Files:**
- Modify: build/ocr_deps_setup.py
- Modify: build.ps1
- Modify: MacroFlowStudio.spec
- Modify: verify_build.py
- Modify: pack.ps1
- Modify: tests/test_import_boundaries.py
- Test: tests/check_packaged_ocr.py

**Interfaces:**
- Consumes: Task 2 的 rapidocr_ocr 路径、本地模型映射和固定依赖。
- Produces: dist/rapidocr_ocr/；该目录独立包含 RapidOCR、CPU ONNX Runtime、真正未被 exe 提供的依赖、模型数据和运行需要的 DLL。

- [ ] **Step 1: 调整依赖闭包工具**

更新 build/ocr_deps_setup.py 的输出为 dist/rapidocr_ocr；从 .deps 中读取 rapidocr/onnxruntime 的已安装 Distribution.requires，递归解析依赖名和环境 marker，减去 exe 已提供的包后复制运行闭包、dist-info、.libs 和包内 DLL。模型 wheel 随 RapidOCR 包目录同步。核心集合和共享包边界如下：

~~~python
OCR_OUT = ROOT / "dist" / "rapidocr_ocr"
OCR_ROOTS = {"rapidocr", "onnxruntime"}
EXE_SHARED_MODULES = {"numpy", "cv2", "PIL"}
SHARED_DISTRIBUTION_PROVIDERS = {"opencv-python": "opencv-python-headless"}
~~~

依赖闭包将 RapidOCR wheel 声明的 opencv-python 映射到主程序的 opencv-python-headless/cv2 提供项，不把缺失的 opencv-python 误判为运行时缺件。其余不复制 Paddle、PaddleX、PDF 工具链、云下载客户端或已有 exe 中的依赖。每个外置 closure package 都必须能在 .deps 找到源码目录和 dist-info；缺文件时终止，不静默跳过。

- [ ] **Step 2: 更新 PyInstaller 排除清单和构建同步**

在 MacroFlowStudio.spec 中排除 Paddle 家族和外置加载的 rapidocr/onnxruntime，不改其他模块收集逻辑：

~~~python
excludes=[
    "pip", "networkx", "hf_xet",
    "paddle", "paddleocr", "paddlex",
    "rapidocr", "onnxruntime",
],
~~~

在 build.ps1 中停止同步 Paddle 包和 paddle_models，改为同步依赖闭包和 RapidOCR 本地模型到 dist/rapidocr_ocr，并检查 rapidocr/__init__.py、onnxruntime/capi/onnxruntime_pybind11_state*.pyd、三个模型文件和原生 DLL 的标记文件。不要删除被 .gitignore 忽略的本地 Paddle 缓存或旧 dist 目录。

- [ ] **Step 3: 更新静态产物校验和导入边界**

在 verify_build.py 中检查 exe 不包含 paddle、paddleocr、paddlex、rapidocr 或 onnxruntime；检查 exe 同级 rapidocr_ocr 目录含 RapidOCR 包、ONNX Runtime CPU DLL、三个模型及必要元数据。将 OCR 符号清单中的 _models_root 和 MODEL_DIRS 改为 _rapidocr_model_paths，并保留现有全部其他应用符号检查。更新 tests/test_import_boundaries.py，新增 rapidocr 和 onnxruntime 禁止项，保持原有 Paddle 禁止项和所有非 OCR 检查不动。

- [ ] **Step 4: 首次运行构建并静态验证打包结构**

Run:
- .\build.ps1
- python verify_build.py

Expected: build 成功；verify 通过新的 RapidOCR 外置目录校验，exe 中不含 OCR 引擎；输出 dist/rapidocr_ocr 的字节数并与原目录约 477 MiB 作对比。构建失败时先依据错误修正依赖闭包或 DLL 收集，再重复本步骤。

## Task 4: 离线真实模型烟测、文档和最终交付

**Files:**
- Modify: tests/check_packaged_ocr.py
- Modify: README.md
- Modify: README_CN.md
- Modify: README_EN.md
- Modify: CHANGELOG.md
- Modify: AGENTS.md
- Test: tests/test_ocr_engine.py, tests/test_recognition.py, tests/test_import_boundaries.py

**Interfaces:**
- Consumes: Task 3 生成的 dist/rapidocr_ocr/ 与验证通过的 exe。
- Produces: 可离线识别的发布 zip，OCR 目录名、源码安装说明和构建校验一致。

- [ ] **Step 1: 改写打包 OCR 后台烟测**

将 tests/check_packaged_ocr.py 改为用当前 RapidOCR 模型路径和 dist/rapidocr_ocr。脚本将项目 src 加入 sys.path，先把网络连接替换成失败函数，再临时设置 sys.frozen=True 并将 sys.executable 指向 dist/MacroFlowStudio.exe，让 _get_engine() 从发布目录初始化；随后调用应用 wrapper。不读写或覆盖根目录 _ocr_test.png。核心步骤如下：

~~~python
old_connect = socket.socket.connect

def reject_network(self, address):
    raise AssertionError(f"OCR attempted network access: {address}")

socket.socket.connect = reject_network
try:
    sys.path.insert(0, os.path.abspath(".deps"))
    sys.path.insert(0, os.path.abspath("src"))
    sys.frozen = True
    sys.executable = os.path.abspath("dist/MacroFlowStudio.exe")
    from macroflow.core.ocr import recognize_image_with_boxes

    image = np.full((96, 360, 3), 255, dtype=np.uint8)
    cv2.putText(image, "12345", (10, 72), cv2.FONT_HERSHEY_SIMPLEX,
                2.0, (0, 0, 0), 3, cv2.LINE_AA)
    text, matches = recognize_image_with_boxes(image)
    assert "12345" in text, text
    assert matches and matches[0]["score"] > 0
finally:
    socket.socket.connect = old_connect
~~~

实际脚本还要保存 sys.frozen/sys.executable 的原值并在 finally 恢复；若原本没有 sys.frozen 属性，则 finally 中删除测试临时属性，避免污染进程。

脚本只在后台运行，不创建或操作软件界面；模型加载使用 dist/rapidocr_ocr 中固定的三个本地路径。

- [ ] **Step 2: 运行离线烟测**

Run: python tests/check_packaged_ocr.py

Expected: RapidOCR 与 CPU ONNX Runtime 从发布目录加载，本地模型完成识别，socket 拦截未发现连接尝试，输出 PASS。

- [ ] **Step 3: 更新活跃文档与目录名**

更新三份 README 中的功能引擎名、首次 OCR 加载说明、源码依赖安装、发布目录树和构建命令；在 CHANGELOG.md 顶部添加 RapidOCR 迁移记录。将 pack.ps1 收集项切换到 dist/rapidocr_ocr；在 AGENTS.md 只替换 zip 内容说明中的 OCR 目录名。不要批量改历史 CHANGELOG 条目、历史计划、旧发行说明或其它功能描述。

- [ ] **Step 4: 执行交付门禁**

Run:
- python -m unittest tests.test_ocr_engine
- python -m unittest tests.test_recognition
- python -m unittest tests.test_import_boundaries
- .\build.ps1
- python -m unittest discover -s tests -t .
- python verify_build.py
- python tests/check_packaged_ocr.py

Expected: 所有命令退出码为 0；无可见界面操作。记录 exe、RapidOCR 目录、zip 三项的实际体积。若 docs 改动不触发 exe 重建，仍须确认最后一次 build 成功并把最新 README/CHANGELOG 复制到 dist。

- [ ] **Step 5: 重打包并检查 zip 清单**

Run: .\pack.ps1

然后以 Python zipfile 后台检查 zip 中含 MacroFlowStudio.exe、rapidocr_ocr/、README.md、CHANGELOG.md，且不含 paddle_ocr/。不解压覆盖任何现有用户文件。

- [ ] **Step 6: 检查并提交本次文件**

Run: git status --short

只暂存本计划涉及的 OCR 源码、依赖、构建、测试和文档路径；确保用户原先修改的 resolution.py、播放器、输入、UI 和相关测试仍保持原样，ocr.py 中的文本比较工作也完整保留。将本次代码和文档以有范围的提交交付。

---

## 预期改造成本与风险

- 估计 1–3 个开发日；主要工时在 Windows/Python 3.13 的 ONNX Runtime 动态库闭包、模型离线定位和 PyInstaller 外置打包。
- RapidOCR 替换不是应用域准确率的已证明提升；仓库没有游戏截图 OCR 黄金集，报告时区分功能烟测和准确率基线。
- 若模型未随固定 wheel 按预期位置提供，构建应失败并修正本地资源定位/校验；禁止改成运行期下载兜底。
- 若 ONNX Runtime 的 Windows DLL 不在 RapidOCR 外置目录可加载，修正外置 DLL/依赖清单；不要把整个推理栈打入主 exe 来绕开检查。
- 在实测 OCR 目录显著小于现有 477 MiB、离线真实模型烟测通过、zip 清单正确后才交付。
