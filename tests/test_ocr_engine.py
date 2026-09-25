import unittest
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from macroflow.core import ocr
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
        self.assertEqual(text, "文字")
        self.assertEqual(matches, [])

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


class RapidOCREngineInitializationTests(unittest.TestCase):
    @staticmethod
    def _model_files(root):
        paths = {name: root / f"{name}.onnx" for name in ("det", "cls", "rec")}
        for path in paths.values():
            path.write_bytes(b"model fixture")
        return paths

    @staticmethod
    def _fake_modules(factory):
        class EngineType(Enum):
            ONNXRUNTIME = "onnxruntime"

        class ModelType(Enum):
            MOBILE = "mobile"
            SMALL = "small"

        class OCRVersion(Enum):
            PPOCRV4 = "PP-OCRv4"
            PPOCRV6 = "PP-OCRv6"

        package = ModuleType("rapidocr")
        package.__path__ = []
        package.RapidOCR = factory
        utils = ModuleType("rapidocr.utils")
        utils.__path__ = []
        typings = ModuleType("rapidocr.utils.typings")
        typings.EngineType = EngineType
        typings.ModelType = ModelType
        typings.OCRVersion = OCRVersion
        return package, {
            "rapidocr": package,
            "rapidocr.utils": utils,
            "rapidocr.utils.typings": typings,
        }

    def test_engine_uses_local_models_and_cpu_configuration(self):
        with TemporaryDirectory() as temp:
            paths = self._model_files(Path(temp))
            engine = Mock()
            factory = Mock(return_value=engine)
            _, fake_modules = self._fake_modules(factory)
            progress = Mock()

            with patch.object(ocr, "_engine", None), \
                 patch.object(ocr, "_progress_callback", progress), \
                 patch.object(ocr, "_rapidocr_model_paths", return_value=paths), \
                 patch.dict("sys.modules", fake_modules):
                first = ocr._get_engine()
                second = ocr._get_engine()

            self.assertIs(first, second)
            factory.assert_called_once()
            params = factory.call_args.kwargs["params"]
            self.assertEqual(params["Det.model_path"], str(paths["det"]))
            self.assertEqual(params["Cls.model_path"], str(paths["cls"]))
            self.assertEqual(params["Rec.model_path"], str(paths["rec"]))
            self.assertEqual(params["Det.engine_type"].value, "onnxruntime")
            self.assertEqual(params["Det.model_type"].value, "small")
            self.assertEqual(params["Det.ocr_version"].value, "PP-OCRv6")
            self.assertEqual(params["Cls.model_type"].value, "mobile")
            self.assertEqual(params["Cls.ocr_version"].value, "PP-OCRv4")
            self.assertEqual(params["Rec.model_type"].value, "small")
            self.assertEqual(params["Rec.ocr_version"].value, "PP-OCRv6")
            self.assertFalse(params["EngineConfig.onnxruntime.use_cuda"])
            self.assertEqual(params["Global.max_side_len"], 960)
            self.assertEqual(params["Det.limit_side_len"], 960)
            self.assertEqual(params["Det.limit_type"], "max")
            progress.assert_any_call("OCR 引擎已加载", 100)

    def test_concurrent_first_call_creates_only_one_engine(self):
        with TemporaryDirectory() as temp:
            paths = self._model_files(Path(temp))
            factory = Mock(return_value=Mock())
            _, fake_modules = self._fake_modules(factory)

            with patch.object(ocr, "_engine", None), \
                 patch.object(ocr, "_rapidocr_model_paths", return_value=paths), \
                 patch.dict("sys.modules", fake_modules):
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
            factory = Mock()
            _, fake_modules = self._fake_modules(factory)

            with patch.object(ocr, "_engine", None), \
                 patch.object(ocr, "_rapidocr_model_paths", return_value=paths), \
                 patch.dict("sys.modules", fake_modules):
                with self.assertRaisesRegex(RuntimeError, "cls.onnx"):
                    ocr._get_engine()

            factory.assert_not_called()

    def test_frozen_models_resolve_inside_external_rapidocr_package(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(ocr, "_ocr_component_root", return_value=root):
                paths = ocr._rapidocr_model_paths()

        self.assertEqual(paths["det"], root / "rapidocr" / "models" / "PP-OCRv6_det_small.onnx")
        self.assertEqual(paths["cls"], root / "rapidocr" / "models" / "ch_ppocr_mobile_v2.0_cls_mobile.onnx")
        self.assertEqual(paths["rec"], root / "rapidocr" / "models" / "PP-OCRv6_rec_small.onnx")
