# Row List Condition Click Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a script action that scans a framed visible list from top to bottom, evaluates two independently configurable row conditions, clicks the first matching row, and either exits or retries after a configured delay when no row matches.

**Architecture:** Add a new `row_list_condition_click` action rather than extending the fixed-region multi-condition action. The dialog stores one absolute list region plus first-row condition/click regions relative to the list; the player translates those relative regions by `row_index * row_height` and short-circuits at the first match. Row conditions share one evaluator for image modules, OCR text, and OCR number comparison.

**Tech Stack:** Python 3.13, tkinter/ttkbootstrap, unittest, existing OpenCV template matching and OCR helpers, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-22-row-list-condition-click-design.md`

## Global Constraints

- Scan only visible rows inside the configured list region; do not scroll.
- Left and right conditions are both required and independently choose `image`, `text`, or `number`.
- Stop at and click only the first matching row from top to bottom.
- Image conditions use their module's live template/settings but the list action's row-local region.
- No-match behavior is either immediate exit or interruptible delayed retry from the top.
- Do not add backward-compatibility or migration paths for this new action.
- Do not launch or visually inspect the GUI; all verification is through background tests, compilation, build, and static artifact checks.
- After production code changes, run `./build.ps1` and verify `dist/MacroFlowStudio.exe`.

---

### Task 1: Row geometry and condition execution

**Files:**
- Modify: `src/macroflow/execution/player.py:801-960`
- Modify: `src/macroflow/execution/player.py:1765-1860`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `parse_ocr_number_pair(text: str, separator: str)`, `find_template(...)`, `recognize_region_with_boxes(region)` and `registered_module_object(key)`.
- Produces: `MacroPlayer._row_list_condition_matches(condition: dict, region: tuple[int, int, int, int]) -> bool` and `MacroPlayer._execute_row_list_condition_click(action: dict, hwnd: int | None) -> None`.

- [ ] **Step 1: Write failing tests for translated rows, short-circuiting, and first-match click**

Add tests using literal regions. The first row fails on the right condition, the second row satisfies both, and the third row must never be evaluated:

```python
def test_row_list_clicks_first_matching_row_and_stops_scanning(self):
    player = MacroPlayer()
    player._row_list_condition_matches = Mock(side_effect=[True, False, True, True])
    player._click_module_point = Mock()
    player._execute_row_list_condition_click({
        "type": "row_list_condition_click",
        "list_region": [100, 200, 160, 78], "row_height": 26,
        "left_region": [0, 0, 70, 26], "right_region": [80, 0, 80, 26],
        "click_region": [90, 0, 60, 26],
        "left_condition": {"type": "number"},
        "right_condition": {"type": "text"},
        "button": "left", "no_match_action": "finish",
    }, None)
    self.assertEqual(player._row_list_condition_matches.call_args_list, [
        call({"type": "number"}, (100, 200, 70, 26)),
        call({"type": "text"}, (180, 200, 80, 26)),
        call({"type": "number"}, (100, 226, 70, 26)),
        call({"type": "text"}, (180, 226, 80, 26)),
    ])
    player._click_module_point.assert_called_once_with(220, 239, "left", 1, None)
```

- [ ] **Step 2: Run the first test and verify RED**

Run: `python -m unittest tests.test_core.PlayerTests.test_row_list_clicks_first_matching_row_and_stops_scanning`

Expected: FAIL because `_execute_row_list_condition_click` does not exist.

- [ ] **Step 3: Implement validation, row translation, left-first short circuit, and click**

Add the action branch:

```python
elif kind == "row_list_condition_click":
    return self._execute_row_list_condition_click(action, hwnd)
```

Implement a loop that validates four-element positive regions, computes `row_count = list_height // row_height`, translates each relative region by the list origin and row offset, evaluates left before right, and calls `_click_module_point` with the translated click-region center.

- [ ] **Step 4: Add failing table tests for image, text, and number conditions**

Cover these literal outcomes:

```python
cases = [
    ({"type": "text", "expected_text": "刚开始", "match_mode": "equals"}, "刚开始", True),
    ({"type": "text", "expected_text": "刚开始", "match_mode": "equals"}, "游戏中", False),
    ({"type": "number", "separator": "/", "relation": "not_equal"}, "11/12", True),
    ({"type": "number", "separator": "/", "relation": "not_equal"}, "12/12", False),
    ({"type": "number", "separator": "/", "relation": "equal"}, "无效", False),
]
```

Add a separate image test proving the module's live template and threshold are used while the method's supplied row region is passed to `find_template`; missing modules must raise `RuntimeError`.

- [ ] **Step 5: Run condition tests and verify RED**

Run: `python -m unittest tests.test_core.PlayerTests.test_row_list_text_and_number_conditions tests.test_core.PlayerTests.test_row_list_image_condition_uses_live_module_with_row_region`

Expected: FAIL because `_row_list_condition_matches` does not exist.

- [ ] **Step 6: Implement the three condition branches**

Use:

```python
if kind == "image":
    module = registered_module_object(str(condition.get("module_key", "")).strip())
    if module is None:
        raise RuntimeError("列表逐行条件点击引用的图片模块不存在")
    return find_template(
        resolve_path(str(module.get("template", ""))),
        float(module.get("threshold", 0.85)), region,
        ignore_background=bool(module.get("ignore_background", False)),
        scale=self._template_scale(),
    ) is not None
```

For text, call OCR and existing text-match helpers. For number, parse the pair and compare according to `equal` or `not_equal`.

- [ ] **Step 7: Add failing tests for no-match finish and delayed retry**

The finish test must call neither `_wait` nor click. The retry test uses condition results `[False, False, True, True]`, asserts `_wait(1500)`, then asserts the first row of the second scan is clicked.

- [ ] **Step 8: Implement no-match behavior and verify Task 1 GREEN**

Run all tests whose names contain `row_list`. Expected: all Task 1 tests PASS.

- [ ] **Step 9: Commit Task 1**

```powershell
git add -- src/macroflow/execution/player.py tests/test_core.py
git commit -m "新增列表逐行条件执行"
```

---

### Task 2: Configuration dialog and validation

**Files:**
- Modify: `src/macroflow/ui/dialogs.py:7631-8040`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: existing `ScreenRegionPicker`, `choose_module_binding`, `_option_label`, `_option_value`, `duration_var`, and floating validation notices.
- Produces: `row_list_condition_field_states(kind: str) -> dict[str, bool]` and `RowListConditionClickDialog.show() -> dict | None` returning the spec's action schema.

- [ ] **Step 1: Write failing tests for independent condition field states**

Assert exact states:

```python
self.assertEqual(row_list_condition_field_states("image"), {
    "module": True, "text": False, "match_mode": False,
    "separator": False, "relation": False,
})
self.assertEqual(row_list_condition_field_states("text"), {
    "module": False, "text": True, "match_mode": True,
    "separator": False, "relation": False,
})
self.assertEqual(row_list_condition_field_states("number"), {
    "module": False, "text": False, "match_mode": False,
    "separator": True, "relation": True,
})
```

- [ ] **Step 2: Verify field-state test RED, implement helper, verify GREEN**

Run the single test before and after implementation.

- [ ] **Step 3: Write failing save test for the confirmed screenshot scenario**

Construct the dialog without opening a GUI and set:

```python
list_region = "100,200,160,340"
row_height = "26"
left_region = "100,200,70,26"
right_region = "180,200,80,26"
click_region = "190,200,60,26"
left_condition = {"type": "number", "separator": "/", "relation": "not_equal"}
right_condition = {"type": "text", "expected_text": "刚开始", "match_mode": "equals"}
no_match_action = "retry"
retry_interval_ms = "1500"
```

Assert saved subregions are relative: left `[0, 0, 70, 26]`, right `[80, 0, 80, 26]`, click `[90, 0, 60, 26]`.

- [ ] **Step 4: Verify save test RED**

Expected: FAIL because the dialog class does not exist.

- [ ] **Step 5: Implement dialog layout, pickers, state refresh, relative conversion, and save validation**

Use two reusable condition panels with the same `image/text/number` options. Enforce that first-row absolute subregions lie inside the list region and do not extend below `list_y + row_height`. Save only fields belonging to the selected condition type.

- [ ] **Step 6: Add validation tests**

Cover zero row height, subregion outside the list, empty number separator, missing image module, and retry interval below zero. Each must leave `result` unset and call the existing notice function.

- [ ] **Step 7: Run all row-list dialog tests and verify GREEN**

Run: tests whose names contain `row_list_dialog` or `row_list_condition_fields`.

- [ ] **Step 8: Commit Task 2**

```powershell
git add -- src/macroflow/ui/dialogs.py tests/test_core.py
git commit -m "新增列表逐行条件配置窗口"
```

---

### Task 3: Script editor integration and OCR dependency detection

**Files:**
- Modify: `src/macroflow/ui/app.py:158-175`
- Modify: `src/macroflow/ui/app.py:602-630`
- Modify: `src/macroflow/ui/app.py:1650-1665`
- Modify: `src/macroflow/ui/app.py:3824-3860`
- Modify: `src/macroflow/ui/app.py:5270-5360`
- Modify: `src/macroflow/ui/dialogs.py:900-1020`
- Modify: `src/macroflow/ui/dialogs.py:8080-8140`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `RowListConditionClickDialog`, `action_kind_label`, `new_action_id`, and existing insert/edit action flows.
- Produces: toolbar/menu insertion, edit dispatch, action summary, icon, and OCR startup detection for `row_list_condition_click`.

- [ ] **Step 1: Write failing integration tests**

Test that:

- `action_summary` labels the action “列表逐行点击” and includes “从上到下” plus the two condition summaries.
- `_script_needs_ocr` returns true if either row condition is `text` or `number`, false if both are image.
- the add method inserts the dialog result with a new action ID.
- edit dispatch opens `RowListConditionClickDialog` for this action type.

- [ ] **Step 2: Run integration tests and verify RED**

Expected failures: missing icon/summary/dispatch/add method/OCR branch.

- [ ] **Step 3: Add icon, toolbar action, add method, summary, edit dispatch, and OCR branch**

Use one toolbar label: `▤ 列表逐行点击`. Summaries should describe `左:<condition> · 右:<condition> · 首个匹配即点击 · 未命中:<finish/retry>` without embedding implementation details.

- [ ] **Step 4: Run integration and existing script-editor tests**

Run the new row-list integration tests and the existing multi-condition insertion/edit/OCR tests. Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- src/macroflow/ui/app.py src/macroflow/ui/dialogs.py tests/test_core.py
git commit -m "接入列表逐行动作编辑器"
```

---

### Task 4: Final regression, build, and static artifact verification

**Files:**
- Verify: `src/macroflow/execution/player.py`
- Verify: `src/macroflow/ui/dialogs.py`
- Verify: `src/macroflow/ui/app.py`
- Verify: `tests/test_core.py`
- Build output: `dist/MacroFlowStudio.exe`

**Interfaces:**
- Consumes: completed action runtime, dialog, and editor integration.
- Produces: verified executable synchronized with current source.

- [ ] **Step 1: Run all focused tests**

```powershell
$testNames = (python -c "import ast,pathlib; t=ast.parse(pathlib.Path('tests/test_core.py').read_text(encoding='utf-8')); print(' '.join('tests.test_core.'+c.name+'.'+f.name for c in t.body if isinstance(c,ast.ClassDef) for f in c.body if isinstance(f,ast.FunctionDef) and ('row_list' in f.name or 'multi_condition' in f.name)))") -split ' '
python -m unittest @testNames
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run compilation and diff checks**

```powershell
python -m compileall -q src tests
git diff --check
```

Expected: exit code 0; line-ending warnings are informational.

- [ ] **Step 3: Build the executable**

```powershell
.\build.ps1
```

Expected: `dist/MacroFlowStudio.exe` is rebuilt and the external `dist/paddle_ocr/` component is present.

- [ ] **Step 4: Verify the packaged artifact statically**

```powershell
python verify_build.py dist\MacroFlowStudio.exe
Get-Item dist\MacroFlowStudio.exe
Get-FileHash dist\MacroFlowStudio.exe -Algorithm SHA256
```

Expected: verifier exit code 0, fresh timestamp, non-empty SHA-256.

- [ ] **Step 5: Commit final verification changes if any**

Only add files changed for this feature; do not stage unrelated dirty-worktree files.
