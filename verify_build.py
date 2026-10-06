"""Post-build verification for MacroFlowStudio.exe via CArchiveReader.

Never executes extracted code — only inspects co_names / co_consts.
"""
import marshal
import os
import struct
import sys
import zlib

sys.path.insert(0, ".deps")
import pefile  # noqa: E402
from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

EXE = sys.argv[1] if len(sys.argv) > 1 else "dist/MacroFlowStudio.exe"
EXPECT_VERSION = "1.0.0"
EXPECT_SYMBOLS = {
    "macroflow.execution.recovery": ["WorkflowHealth", "WorkflowSupervisor", "CaptureUnavailable",
                                    "watchdog_action", "run_watchdog"],
    "macroflow.ui.app.recovery": ["_arm_workflow_recovery", "_emergency_stop_from_hook",
                                 "_restart_recovered_workflow", "_restore_recovery_workflow"],
    # 主窗口是按功能拆开的包（macroflow.ui.app：constants / base / summaries /
    # startup + 11 个 mixin），打包入口是它的 __main__.py → 归档里的 __main__。
    "macroflow.ui.app": ["open_template_region_manager", "add_module", "add_jump", "add_ocr_compare", "add_multi_condition_click",
            "_on_restart_workflow_request", "_poll_workflow_stop_for_restart_workflow",
            "_launch_workflow_restart", "_restart_workflow_resolved_row",
            "_evaluate_global_guards", "_evaluate_one_guard", "_build_guard_hit",
            "_clear_global_guards", "_guard_wait",
            "_workflow_restart_default_options", "_sync_workflow_restart_default_ui",
            "_apply_workflow_restart_default",
            "_write_log_line", "_read_workflow_start_delay",
            "_workflow_global_module_registry_state",
            "_update_redo_button", "_update_action_edit_button",
            "_select_all_actions",
            "_toggle_target_activation", "undo_delete_workflow_step",
            "undo_delete_global_module", "_update_workflow_delete_undo_buttons",
            "_select_all_workflow_steps", "_select_all_global_modules",
            "_restore_workflow_scan_foreground",
            "add_workflow_global_module", "add_scroll",
            "recognize_region_with_boxes",
            "_run_timed_backup", "_run_configured_startup_workflow",
            "_sync_windows_startup",
            "rename_workflow", "duplicate_workflow", "_switch_to_workflow",
            "_restore_workflow_scan_foreground",
            "run_single_action_with_count", "run_workflow_step_with_count",
            "run_action_segment", "run_workflow_segment",
            "run_referenced_script_alone", "run_module_object_test"],
    "macroflow.ui.dialogs": ["TemplateRegionManagerDialog", "TemplateRegionFormDialog",
                "ScreenOffsetPicker", "ScrollDialog",
                "ModulePickerDialog", "BatchModuleScriptDialog", "JumpActionDialog",
                "ModuleReferenceDelayDialog", "OcrCompareActionDialog", "MultiConditionClickDialog",
                "fit_window_to_content", "fit_scrollable_window_to_content",
                "monitor_work_area_for",
                "clamp_to_work_area", "DIALOG_FRAME_MARGIN",
                "segment_action_is_blocking", "segment_row_label", "module_manager_label",
                "module_manager_tag", "_toggle_selected_enabled",
                "module_manager_selection_colors", "_update_selection_highlight",
                "configure_module_tree_styles",
                "registered_template_options", "fallback_template_options",
                "open_template_region_manager", "_open_add", "_open_edit",
                "_open_form", "_remove_selected", "_update_action_buttons",
                 "_undo_remove", "_update_undo_button", "_fit_window_to_content",
                 "_labeled_row", "_section_heading", "_entry_button_row", "_row_combo",
                 "_choose_images_dir", "_refresh_inventory", "_open_inventory_item",
                 "_set_inventory_filter", "_set_sort_direction", "_toggle_sort_direction",
                 "_apply_sort_heading", "pinyin_sort_key",
                 "SECOND_MATCH_CLICK_TARGET_LABELS", "_pick_second_click_region",
                 "image_found_jump_target_options",
                 "capture_custom_template", "configured_script_files",
                 "prepend_module_to_scripts", "script_category_for_path",
                 "_batch_add_selected", "_show_module_context_menu",
                 "_test_module_with_count",
                 "_change_global_module_category", "_set_filter", "_visible_indices",
                 "_select_all_segment_items",
                 "_select_all_category", "_action_for_key",
                 "_toggle_sections", "recognize_var", "expected_text_var",
                 "match_mode_var", "recognize_combo"],
    # 高亮框必须画成空心细边框（选空画刷），否则会填成白块挡住识别目标。
    "macroflow.ui.detect_overlay": ["NULL_BRUSH", "GetStockObject", "Rectangle"],
    "macroflow.core.storage": ["TEMPLATE_REGIONS_PATH", "load_module_objects",
                "save_module_objects", "registered_module_object",
                "module_objects_by_category", "update_module_object",
                 "load_template_regions", "save_template_regions",
                 "registered_template_region", "load_module_images_dir",
                 "save_module_images_dir", "module_image_inventory"],
    "macroflow.execution.player": ["registered_module_object", "on_restart_workflow_request",
               "_execute_second_match", "_execute_ocr_compare", "_execute_multi_condition_click", "_multi_condition_matches", "AdvanceToNextWorkflowStep",
               "ocr_match_center", "recognize_region_with_boxes", "matches_expected",
               "CAPTURE_ERRORS", "_note_capture_failure", "_clear_capture_failure"],
    "macroflow.core.models": ["NEXT_WORKFLOW_STEP_TARGET_ID", "SCRIPT_START_TARGET_ID",
                "scroll_direction_label", "scroll_clicks"],
    # 执行期间阻止屏保：声明必须进 exe，否则挂机时屏保一起来截图就全废。
    "macroflow.core.display_power": ["SetThreadExecutionState", "keep_display_awake",
                "allow_display_sleep", "ES_DISPLAY_REQUIRED", "ES_CONTINUOUS"],
    "macroflow.core.image_match": ["find_template", "find_template_in_image",
                    "_estimate_background_color", "_build_ignore_background_mask",
                    "_match_with_mask", "CAPTURE_ERRORS", "CAPTURE_ATTEMPTS"],
    "macroflow.core.ocr": ["recognize_region", "recognize_image", "recognize_image_with_boxes",
            "recognize_region_with_boxes", "find_expected_match", "matches_expected",
            "format_ocr_observation", "extract_ocr_integer", "parse_ocr_number_pair", "ocr_match_center",
            "_get_engine", "_rapidocr_model_paths", "RAPIDOCR_MODEL_FILES"],
    "macroflow.input.input_guard": ["BlockInput", "WM_MACROFLOW_INPUT", "_dispatch_input",
                    "_drain_input_requests"],
    "macroflow.input.wininput": ["set_input_dispatcher", "_send_input_direct",
                 "MACROFLOW_INPUT_TAG", "resolve_window_signature"],
}
PYZ_ITEM_MODULE = 0
PYZ_ITEM_PKG = 1
ERRORS = []


def names_of(code):
    yield from code.co_names
    for const in code.co_consts:
        if hasattr(const, "co_names"):
            yield from names_of(const)


def literals_of(code):
    """Recursively yield literal constants (strings / numbers) embedded in code."""
    for const in code.co_consts:
        if isinstance(const, (str, int, float)):
            yield const
        elif isinstance(const, (tuple, list, frozenset)):
            for item in const:
                if isinstance(item, (str, int, float)):
                    yield item
        elif hasattr(const, "co_names"):
            yield from literals_of(const)


def read_pyz(data: bytes) -> dict:
    """Parse an in-memory PYZ archive (mirror of ZlibArchiveReader)."""
    if data[:4] != b"PYZ\0":
        raise ValueError("PYZ magic mismatch")
    toc_offset = struct.unpack("!i", data[8:12])[0]
    return dict(marshal.loads(data[toc_offset:]))


def extract_pyz_entry(data: bytes, entry) -> bytes:
    _typecode, offset, length = entry
    return zlib.decompress(data[offset:offset + length])


archive = CArchiveReader(EXE)
image = pefile.PE(EXE, fast_load=True)
if image.OPTIONAL_HEADER.Subsystem != pefile.SUBSYSTEM_TYPE["IMAGE_SUBSYSTEM_WINDOWS_GUI"]:
    ERRORS.append("EXE 不是 Windows GUI 子系统")
image.parse_data_directories([
    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
])
for entry in getattr(image, "DIRECTORY_ENTRY_IMPORT", []):
    if entry.dll.lower() == b"comctl32.dll" and any(
        item.ordinal == 380 for item in entry.imports
    ):
        ERRORS.append("EXE 仍导入 COMCTL32 序数 380")
image.close()
# 其余模块在 PYZ.pyz 里（含主窗口模块 macroflow.ui.app）。
pyz_data = archive.extract("PYZ.pyz")
if isinstance(pyz_data, tuple):
    pyz_data = pyz_data[0]
pyz_toc = read_pyz(pyz_data)
if "pypinyin" not in pyz_toc:
    ERRORS.append("缺少拼音排序依赖 pypinyin")
# OCR 推理栈外置：exe 不应包含旧 Paddle 或 RapidOCR / ONNX Runtime。
for ocr_module in ("paddle", "paddleocr", "paddlex", "rapidocr", "onnxruntime"):
    if ocr_module in pyz_toc:
        ERRORS.append(f"OCR 引擎未外置：exe 仍包含 {ocr_module}")
    for entry in archive.toc:
        normalized_entry = entry.replace("\\", "/").replace(".", "/").lower()
        if normalized_entry == ocr_module or normalized_entry.startswith(ocr_module + "/"):
            ERRORS.append(f"OCR 引擎未外置：exe 归档仍包含 {entry}")
exe_dir = os.path.dirname(os.path.abspath(EXE))
ocr_root = os.path.join(exe_dir, "rapidocr_ocr")
if not os.path.isdir(ocr_root):
    ERRORS.append(f"缺少外置 OCR 组件目录 {ocr_root}")
else:
    required_ocr_files = {
        "rapidocr/__init__.py": "RapidOCR 包",
        "onnxruntime/__init__.py": "ONNX Runtime 包",
        "onnxruntime/capi/onnxruntime.dll": "ONNX Runtime CPU DLL",
        "onnxruntime/capi/onnxruntime_providers_shared.dll": "ONNX Runtime CPU provider DLL",
        "rapidocr/models/PP-OCRv6_det_small.onnx": "PP-OCRv6 检测模型",
        "rapidocr/models/ch_ppocr_mobile_v2.0_cls_mobile.onnx": "文字方向分类模型",
        "rapidocr/models/PP-OCRv6_rec_small.onnx": "PP-OCRv6 识别模型",
        "rapidocr-3.9.2.dist-info/METADATA": "RapidOCR 版本元数据",
        "onnxruntime-1.24.2.dist-info/METADATA": "ONNX Runtime 版本元数据",
        "RapidOCR-LICENSE.txt": "RapidOCR 许可证",
        "THIRD_PARTY_DEPENDENCIES.txt": "OCR 依赖清单",
        "OCR_EXTERNAL_MODULES.txt": "离线 smoke 外置模块清单",
        "onnxruntime/LICENSE": "ONNX Runtime 许可证",
        "onnxruntime/ThirdPartyNotices.txt": "ONNX Runtime 第三方许可证",
    }
    for rel, desc in required_ocr_files.items():
        if not os.path.isfile(os.path.join(ocr_root, rel)):
            ERRORS.append(f"缺少 {desc}（{rel}）")
    state_extensions = [
        name for name in os.listdir(os.path.join(ocr_root, "onnxruntime", "capi"))
        if name.startswith("onnxruntime_pybind11_state") and name.endswith(".pyd")
    ] if os.path.isdir(os.path.join(ocr_root, "onnxruntime", "capi")) else []
    if not state_extensions:
        ERRORS.append("缺少 ONNX Runtime Python CPU 扩展")


def unmarshal(obj):
    return marshal.loads(obj) if isinstance(obj, bytes) else obj


def module_code(module):
    raw = extract_pyz_entry(pyz_data, pyz_toc[module])
    return marshal.loads(raw)


def module_codes(module):
    """取一个模块的代码对象；模块是包时连同全部子模块一起取。

    macroflow.ui.app 与 macroflow.ui.dialogs 都是按功能拆开的包（app：mixin +
    constants / base / summaries / startup；dialogs：base / helpers / segments /
    module_objects / actions / recognition / app_dialogs），符号与常量散在各子
    模块里，所以按包校验时要取并集。
    """
    names = [key for key in pyz_toc if key == module or key.startswith(module + ".")]
    if module not in names:
        names.append(module)
    return [module_code(key) for key in names if key in pyz_toc]


# 打包入口（src/macroflow/ui/app/__main__.py）在 CArchive 根目录，模块名 __main__。
# 入口用 `from macroflow.ui.app.startup import main` 导入，所以基名里应当出现
# "macroflow.ui.app.startup" 与 "main"。
ENTRY_NAME = "__main__" if "__main__" in archive.toc else "app"
entry_names = set(names_of(unmarshal(archive.extract(ENTRY_NAME))))
if "main" not in entry_names or "macroflow.ui.app.startup" not in entry_names:
    ERRORS.append(f"打包入口 {ENTRY_NAME} 没有指向 macroflow.ui.app.startup.main")

# 主窗口类必须真的进包：包入口只转出它，所以查包入口的 co_names。
app_entry_names = set(names_of(module_code("macroflow.ui.app")))
if "MacroFlowApp" not in app_entry_names:
    ERRORS.append("macroflow.ui.app 包入口没有转出 MacroFlowApp")

# 版本号随主窗口包一起校验。
if EXPECT_VERSION not in {
        lit for code in module_codes("macroflow.ui.app") for lit in literals_of(code)}:
    ERRORS.append(f"macroflow.ui.app 缺少 APP_VERSION 常量 {EXPECT_VERSION}")

for module, symbols in EXPECT_SYMBOLS.items():
    names = [name for code in module_codes(module) for name in names_of(code)]
    for symbol in symbols:
        if symbol not in names:
            ERRORS.append(f"{module} 缺少符号 {symbol}")

for module, expected_literals in {
    "macroflow.ui.app": ["关卡", "关卡封装", "切换", "workflow_global", "script_global",
            "wait_text_absent", "ocr_offset_up", "ocr_offset_down",
            "ocr_offset_left", "ocr_offset_right",
            "▶ 从此行开始运行", "▶ 单独执行此动作…",
            "▶ 单独执行此步骤…", "▶ 循环执行片段…"],
    # 动作列表 / 工作流表格右键菜单：新菜单项必须真的进了 exe。
    "macroflow.ui.dialogs": ["工作流全局模块", "脚本全局模块", "读取数字", "expected_number",
                "▶ 测试指定次数…",
                "wait_text_absent",
                "ocr_offset_up", "ocr_offset_down", "ocr_offset_left", "ocr_offset_right"],
    "macroflow.core.storage": ["workflow_global", "script_global", "number", "workflow_templates.migrated.json",
                "wait_text_absent",
                "ocr_offset_up", "ocr_offset_down", "ocr_offset_left", "ocr_offset_right"],
    "macroflow.execution.player": ["expected_number", "number", "wait_text_absent", "ocr_offset_up", "ocr_offset_down",
               "ocr_offset_left", "ocr_offset_right"],
}.items():
    literals = {lit for code in module_codes(module) for lit in literals_of(code)}
    for expected in expected_literals:
        if expected not in literals:
            ERRORS.append(f"{module} 缺少分类常量 {expected}")
if ERRORS:
    print("验证失败：")
    for error in ERRORS:
        print(" -", error)
    sys.exit(1)
print(f"OK：版本 {EXPECT_VERSION}，GUI 子系统、序数 380、所有符号与游戏级输入锁检查通过")
