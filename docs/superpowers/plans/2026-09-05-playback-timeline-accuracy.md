# Playback Timeline Accuracy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace cumulative per-action playback waits with absolute recorded-time scheduling and make mouse button, relative movement, injected turns, and cleanup behavior deterministic.

**Architecture:** Add a pure `PlaybackTimeline` with injectable clock and wait functions, then integrate it into `MacroPlayer` only at sequence boundaries. `MacroRecorder` writes high-precision relative timestamps, explicit mouse modes, and injected pulse durations; the player consumes those fields while keeping explicit waits and recognition/guard processing as rebase boundaries.

**Tech Stack:** Python, `time.perf_counter`, `threading.Event`, existing `pynput`/Win32 input helpers, `unittest`, PyInstaller build scripts.

**Spec:** `docs/superpowers/specs/2026-09-05-playback-timeline-accuracy-design.md`

## Global Constraints

- Do not launch the application or perform visible UI inspection; verification must use background tests, compilation, builds, and static artifact checks.
- Do not add compatibility migrations or a second playback fallback for the replaced recorded-delay path.
- Preserve the distinction between recorded event time, explicit waits, input hold durations, recognition waits, and guard processing.
- Absolute button events must position from their own recorded coordinates; relative button events must not perform absolute positioning.
- A late event is sent immediately; no key, button, scroll, or relative movement event may be discarded.
- Severe lateness rebases future scheduling instead of emitting a burst of overdue inputs.
- Every production-code edit is followed immediately by `.\build.ps1`; confirm `dist\MacroFlowStudio.exe` exists after each build.
- Do not run `.\pack.ps1` until all P0/P1 work and final static verification are complete; packaging must follow a successful final build.
- Keep unrelated existing baseline test failures separate from new regression failures; do not broaden the feature scope to repair unrelated mocks or optional OCR installation.

---

### Task 1: Add the pure playback timeline and metrics core

**Files:**
- Create: `src/macroflow/execution/timeline.py`
- Create: `tests/test_timeline.py`

**Interfaces:**
- Consumes: a monotonic `now() -> float` function and a `wait(seconds: float) -> None` function.
- Produces: `PlaybackTimeline.start(first_offset_ms: float = 0.0)`, `PlaybackTimeline.wait_until(offset_ms: float) -> TimingSample`, `PlaybackTimeline.mark_boundary()`, `PlaybackTimeline.rebase(offset_ms: float | None = None)`, and `TimingMetrics.snapshot() -> dict[str, float | int]`.

- [ ] **Step 1: Write the fake clock test utility**

Add a test-only clock that advances only when the timeline asks it to wait:

```python
class FakeClock:
    def __init__(self):
        self.value = 100.0
        self.waits = []

    def now(self):
        return self.value

    def wait(self, seconds):
        self.waits.append(seconds)
        self.value += seconds
```

- [ ] **Step 2: Write the failing test for absolute targets**

```python
def test_wait_until_uses_absolute_recorded_offset(self):
    clock = FakeClock()
    timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
    timeline.start()

    first = timeline.wait_until(100.0)
    clock.value += 0.003
    second = timeline.wait_until(250.0)

    self.assertEqual(clock.waits, [0.1, 0.147])
    self.assertAlmostEqual(first.lateness_ms, 0.0, places=3)
    self.assertAlmostEqual(second.planned_offset_ms, 250.0, places=3)
```

Run: `python -m unittest tests.test_timeline.PlaybackTimelineTests.test_wait_until_uses_absolute_recorded_offset -v`

Expected: FAIL because `src/macroflow/execution/timeline.py` does not exist.

- [ ] **Step 3: Add the minimal timeline and metrics implementation**

Implement the following data shape:

```python
@dataclass(frozen=True)
class TimingSample:
    planned_offset_ms: float
    actual_offset_ms: float
    lateness_ms: float
    severe: bool

@dataclass
class TimingMetrics:
    scheduled_count: int = 0
    sent_count: int = 0
    severe_lateness_count: int = 0
    rebase_count: int = 0
    explicit_wait_ms: float = 0.0
    recognition_ms: float = 0.0
    stop_cleanup_ms: float = 0.0
    lateness_ms: list[float] = field(default_factory=list)

    def snapshot(self) -> dict:
        values = sorted(self.lateness_ms)
        return {
            "scheduled_count": self.scheduled_count,
            "sent_count": self.sent_count,
            "severe_lateness_count": self.severe_lateness_count,
            "rebase_count": self.rebase_count,
            "explicit_wait_ms": self.explicit_wait_ms,
            "recognition_ms": self.recognition_ms,
            "stop_cleanup_ms": self.stop_cleanup_ms,
            "average_lateness_ms": sum(values) / len(values) if values else 0.0,
            "p95_lateness_ms": values[max(0, int(len(values) * 0.95) - 1)] if values else 0.0,
            "p99_lateness_ms": values[max(0, int(len(values) * 0.99) - 1)] if values else 0.0,
            "max_lateness_ms": values[-1] if values else 0.0,
        }
```

`PlaybackTimeline.wait_until` must calculate the target from the current base wall time and base recorded offset, wait only for positive remaining time, record lateness, and call `rebase` when lateness exceeds `severe_lateness_ms` (default `100.0`).

Run immediately after this production edit: `.\build.ps1`

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python -m unittest tests.test_timeline.PlaybackTimelineTests.test_wait_until_uses_absolute_recorded_offset -v`

Expected: PASS with no warnings.

- [ ] **Step 5: Add failing tests for boundaries and severe lateness**

Add tests proving:

```python
def test_boundary_keeps_future_events_from_bursting_after_explicit_wait(self):
    clock = FakeClock()
    timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
    timeline.start()
    timeline.wait_until(100.0)
    clock.value += 0.4
    timeline.mark_boundary()
    timeline.wait_until(150.0)
    self.assertEqual(clock.waits, [0.1, 0.05])

def test_severe_lateness_rebases_future_target(self):
    clock = FakeClock()
    timeline = PlaybackTimeline(now=clock.now, wait=clock.wait, severe_lateness_ms=20)
    timeline.start()
    timeline.wait_until(10.0)
    clock.value += 0.05
    sample = timeline.wait_until(20.0)
    timeline.wait_until(30.0)
    self.assertTrue(sample.severe)
    self.assertEqual(timeline.metrics.rebase_count, 1)
    self.assertEqual(clock.waits, [0.01, 0.01])
```

Run the two tests and expect FAIL until boundary and severe-lateness behavior is implemented.

- [ ] **Step 6: Implement boundary/rebase behavior and run all timeline tests**

`mark_boundary()` must retain the last recorded offset while moving the wall-clock base to `now()`. `rebase(offset_ms)` must use the supplied offset or the last scheduled offset and increment `rebase_count`. `wait_until` must increment `scheduled_count` and `sent_count` for every event, including overdue events.

Run: `python -m unittest tests.test_timeline -v`

Expected: PASS.

Run immediately after this production edit: `.\build.ps1`

- [ ] **Step 7: Build immediately after the new production module is complete**

Run: `.\build.ps1`

Expected: exit code `0`; `dist\MacroFlowStudio.exe` has a fresh timestamp and remains present.

- [ ] **Step 8: Commit Task 1**

```powershell
git add -- src/macroflow/execution/timeline.py tests/test_timeline.py
git commit -m "feat: add absolute playback timeline"
```

---

### Task 2: Record high-precision event timestamps and ordered mouse data

**Files:**
- Modify: `src/macroflow/input/recorder.py:26-283`
- Modify: `src/macroflow/core/models.py:1-20`
- Create: `tests/test_recorder_timeline.py`
- Modify: `tests/test_recorder_injected.py`

**Interfaces:**
- Consumes: the existing recorder callbacks, `current_mode()`, and shared action-field constants from `macroflow.core.models`.
- Produces: recorded actions with `recorded_at_ms`, `mode`, and injected `pulse_duration_ms`; click/scroll callbacks flush pending movement before appending their own action.

- [ ] **Step 1: Write the failing timestamp test**

Construct a running recorder without starting OS listeners, set `_recording_started_at = 50.0`, patch `time.perf_counter` to return `50.123456`, append a key action, and assert:

```python
self.assertAlmostEqual(rec.actions[0]["recorded_at_ms"], 123.456, places=3)
self.assertIn("recorded_at_ms", rec.actions[0])
self.assertEqual(rec.actions[0][RECORDED_AT_KEY], rec.actions[0]["recorded_at_ms"])
```

Run: `python -m unittest tests.test_recorder_timeline.RecorderTimelineTests.test_append_saves_relative_high_precision_timestamp -v`

Expected: FAIL because the recorder does not define `_recording_started_at` or `recorded_at_ms`.

- [ ] **Step 2: Implement the shared action-field constants and recording-start timing**

In `core/models.py`, define `RECORDED_AT_KEY = "recorded_at_ms"`, `INPUT_MODE_KEY = "mode"`, `PULSE_STARTED_AT_KEY = "pulse_started_at_ms"`, and `PULSE_DURATION_KEY = "pulse_duration_ms"`. Set `_recording_started_at = time.perf_counter()` in `start()`. In `_append`, capture `now` before lock/filters are finished, compute `(now - _recording_started_at) * 1000`, and write it as a float rounded only for JSON readability (three decimal places). Do not use the timestamp to recompute `delay_ms`; keep `delay_ms` only for existing display summaries until the player integration removes it from the recorded-event schedule.

- [ ] **Step 3: Run the timestamp test and rebuild**

Run: `python -m unittest tests.test_recorder_timeline.RecorderTimelineTests.test_append_saves_relative_high_precision_timestamp -v`

Expected: PASS.

Run immediately after the production edit: `.\build.ps1`

- [ ] **Step 4: Write failing tests for click mode and flush order**

Add two tests:

```python
def test_absolute_click_keeps_coordinates_and_mode(self):
    rec = make_running_recorder(mode="absolute")
    rec._on_click(320, 240, mouse.Button.left, True)
    self.assertEqual(rec.actions[-1]["mode"], "absolute")
    self.assertEqual((rec.actions[-1]["x"], rec.actions[-1]["y"]), (320, 240))

def test_relative_click_flushes_pending_move_before_button(self):
    rec = make_running_recorder(mode="relative")
    rec._raw_dx, rec._raw_dy = 4, -2
    rec._raw_last_flush = 0.0
    rec._on_click(900, 700, mouse.Button.left, True)
    self.assertEqual([action["type"] for action in rec.actions], ["mouse_move", "mouse_button"])
    self.assertEqual((rec.actions[0]["dx"], rec.actions[0]["dy"]), (4, -2))
```

Run both tests and expect FAIL because click events do not save mode and `_on_click` does not flush `_raw_dx/_raw_dy`.

- [ ] **Step 5: Implement ordered click/scroll flushing and explicit modes**

In `_on_click` and `_on_scroll`, flush injected data first, then flush raw relative data when the current capture mode is relative, then append the button/scroll event. Store `mode` on button events and the event coordinates already supplied by `pynput`. Do not transform relative button events into absolute moves.

Run immediately after this production edit: `.\build.ps1`

- [ ] **Step 6: Run recorder ordering tests and rebuild**

Run: `python -m unittest tests.test_recorder_timeline -v`

Expected: PASS.

Run immediately after the production edit: `.\build.ps1`

- [ ] **Step 7: Write failing injected-pulse duration tests**

Patch `time.perf_counter` with known values for the first injected sample and the final sample, flush the pulse, and assert:

```python
self.assertEqual(action["pulse_started_at_ms"], 0.0)
self.assertAlmostEqual(action["pulse_duration_ms"], 24.0, places=3)
```

Extend the existing injected tests to assert the new fields while preserving burst merging and injection-before-physical-event ordering.

- [ ] **Step 8: Implement pulse start/end fields and run the full recorder subset**

Track the first and last injection timestamps. When `_flush_injected` or a new pulse boundary emits the turn, write `pulse_started_at_ms` relative to recording start and `pulse_duration_ms = max(0, last_injected - first_injected) * 1000`. A zero-duration pulse is valid and must not gain a default duration.

Run: `python -m unittest tests.test_recorder_timeline tests.test_recorder_injected -v`

Expected: PASS.

- [ ] **Step 9: Rebuild and commit Task 2**

Run: `.\build.ps1`

Then:

```powershell
git add -- src/macroflow/input/recorder.py tests/test_recorder_timeline.py tests/test_recorder_injected.py
git commit -m "feat: record precise input timeline data"
```

---

### Task 3: Integrate absolute scheduling into the player sequence loop

**Files:**
- Modify: `src/macroflow/execution/player.py:226-335`
- Modify: `src/macroflow/execution/player.py:519-843`
- Modify: `tests/test_core.py` in `PlayerTests`

**Interfaces:**
- Consumes: `PlaybackTimeline`, `TimingMetrics`, recorder `recorded_at_ms`, existing `_wait`, `stop_event`, guard polling, and action sequence recursion.
- Produces: player-owned timeline state, `on_timing` completion callback, and sequence scheduling that uses absolute recorded offsets for recorded events and explicit waits for non-timeline actions.

- [ ] **Step 1: Write the failing player test for non-cumulative execution cost**

Use a fake clock and a player action executor that advances the fake clock by 3ms per action:

```python
def test_recorded_actions_use_absolute_targets_when_execution_takes_time(self):
    clock = FakeClock()
    player = MacroPlayer()
    player._timeline = PlaybackTimeline(now=clock.now, wait=clock.wait)
    player._wait = lambda milliseconds: clock.wait(milliseconds / 1000)

    def execute(_action, _hwnd, _stack=None, _depth=0):
        clock.value += 0.003

    player._execute_action = execute
    player._run_action_sequence([
        {"type": "key", "recorded_at_ms": 0.0},
        {"type": "key", "recorded_at_ms": 100.0},
        {"type": "key", "recorded_at_ms": 200.0},
    ], None)

    self.assertEqual(clock.waits, [0.097, 0.097])
```

Run the test and expect FAIL because `_run_action_sequence` currently waits per-action `delay_ms` and does not consume `recorded_at_ms`.

- [ ] **Step 2: Add timeline lifecycle to `MacroPlayer`**

Add an optional final constructor parameter `on_timing: Callable[[dict], None] | None = None`, initialize `self._timeline = PlaybackTimeline(wait=lambda seconds: self._wait(seconds * 1000))`, and reset timeline/metrics at the start of each `play()` call. Do not change the existing positional callback order.

Run immediately after this production edit: `.\build.ps1`.

- [ ] **Step 3: Implement recorded-event scheduling in `_run_action_sequence`**

At the start of each sequence, call `self._timeline.start(first_offset_ms)` where `first_offset_ms` is the first action's `recorded_at_ms` or `0.0`. For each action:

```python
if "recorded_at_ms" in action:
    self._timeline.wait_until(float(action["recorded_at_ms"]))
else:
    self._wait(self._scaled_delay(int(action.get("delay_ms", default_delay))))
    self._timeline.mark_boundary()
```

Keep guard polling before the schedule wait. Do not call `_wait` with the old recorded `delay_ms` when `recorded_at_ms` is present.

Run immediately after this production edit: `.\build.ps1`.

- [ ] **Step 4: Run the timing test and verify the integrated behavior**

Run: `python -m unittest tests.test_core.PlayerTests.test_recorded_actions_use_absolute_targets_when_execution_takes_time -v`

Expected: PASS after the integration. Confirm the executable exists from the builds in Steps 2 and 3.

- [ ] **Step 5: Add failing tests for explicit boundaries**

Add tests for a recorded action followed by an explicit `delay` action and then a recorded action. Assert that the explicit delay is fully waited and the next recorded event is scheduled from the boundary rather than released in a burst. Add a nested sequence test proving entry and exit of a referenced/guard action sequence call `mark_boundary()`.

Add a 1,000-event fake-clock case with a fixed 3ms executor cost and assert the final send offset is the last recorded target plus only the executor's current-event cost, not 3ms multiplied by every prior event.

- [ ] **Step 6: Implement boundary calls, detection timing, and completion reporting**

After `after_delay_ms`, `delay`, hold/click waits, image/OCR waits, repeat intervals, jump target changes, guard processing, and nested `_run_action_sequence` returns, call `self._timeline.mark_boundary()` or `rebase()` at the point where the control flow changes. Around each synchronous guard poll, measure `time.perf_counter()` before and after the poll and add the elapsed milliseconds to `TimingMetrics.recognition_ms`. In `play()`'s final cleanup, call `on_timing(self._timeline.metrics.snapshot())` after releasing inputs. Preserve existing guard exception routing and stop behavior.

Run immediately after this production edit: `.\build.ps1`.

Run immediately after this production edit: `.\build.ps1`

- [ ] **Step 7: Run timing and existing player wait tests**

Run:

```powershell
python -m unittest tests.test_timeline tests.test_core.PlayerTests.test_recorded_input_actions_keep_each_recorded_delay tests.test_core.PlayerTests.test_playback_speed_scales_waits_without_changing_hold_time tests.test_core.PlayerTests.test_stop_interrupts_wait -v
```

Expected: the new absolute-timeline test and existing explicit-wait tests pass. If an existing assertion assumes recorded `delay_ms` is the timeline, update that test to assert the new `recorded_at_ms` contract rather than restoring the replaced scheduling path. Include a test that a slow guard poll increments `recognition_ms` without changing the input executor order.

- [ ] **Step 8: Rebuild and commit Task 3**

Run: `.\build.ps1`, then:

```powershell
git add -- src/macroflow/execution/player.py tests/test_core.py
git commit -m "feat: schedule playback by absolute recorded time"
```

---

### Task 4: Make player mouse actions exact and use recorded turn durations

**Files:**
- Modify: `src/macroflow/execution/player.py:966-1110`
- Modify: `src/macroflow/execution/player.py:2765-2785`
- Modify: `tests/test_core.py` in `PlayerTests`

**Interfaces:**
- Consumes: action `mode`, `x`, `y`, `pulse_duration_ms`, `steps`, existing screen scaling, clamping, relative target setup, and held-input sets.
- Produces: exact absolute button positioning, mode-preserving relative buttons, duration-aware turns, and cleanup timing statistics.

- [ ] **Step 1: Write the failing absolute-button coordinate test**

```python
def test_absolute_mouse_button_positions_from_its_own_coordinates(self):
    player = MacroPlayer()
    player._source_screen = {"left": -1920, "top": 0, "width": 3840, "height": 2160}
    player._target_screen = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    player._wait = Mock()
    with patch("macroflow.execution.player.send_move_absolute") as move, \
         patch("macroflow.execution.player.send_button") as button:
        player._execute_action({
            "type": "mouse_button", "mode": "absolute", "x": -960, "y": 540,
            "button": "left", "down": True,
        }, None)
    move.assert_called_once_with(480, 270)
    button.assert_called_once_with("left", True)
```

Run and expect FAIL because `mouse_button` currently does not move the cursor.

- [ ] **Step 2: Implement absolute and relative button branches**

For `mode == "absolute"`, scale and clamp the event coordinates, call `send_move_absolute`, then send the button state. For `mode == "relative"`, send only the button state. Update `_held_buttons` exactly as today.

Run immediately after this production edit: `.\build.ps1`

- [ ] **Step 3: Add the failing relative-button and paired-release tests**

Assert a relative button calls no absolute move and that `player.play` with a down event followed by stop still sends one matching release through `_release_all`.

- [ ] **Step 4: Run coordinate/release tests and rebuild**

Run the new tests plus:

```powershell
python -m unittest tests.test_core.PlayerTests.test_click_stop_during_hold_releases_button tests.test_core.PlayerTests.test_absolute_coordinates_scale_to_current_resolution -v
```

Expected: PASS. Run immediately afterward: `.\build.ps1`.

- [ ] **Step 5: Write the failing injected-turn duration test**

Configure a recorded turn with `pulse_duration_ms=0` and assert no implicit 10ms wait; configure another with `pulse_duration_ms=24` and assert the injected movement steps consume 24ms total. Use `send_move_relative` and a wait mock so no OS input is sent. A manually configured turn with no `pulse_duration_ms` continues to use its explicit `duration_ms` field.

- [ ] **Step 6: Implement duration selection for turns**

Use `pulse_duration_ms` when present, clamp it to non-negative, divide it across `steps`, and use zero waits for zero duration. Do not introduce a new default wait for recorded turns. Keep manually configured turns on the existing explicit duration field.

Run immediately after this production edit: `.\build.ps1`.

- [ ] **Step 7: Add cleanup timing and release coverage**

Record the stop-request timestamp in `stop()`, measure the elapsed time when `finally` invokes `_release_all`, and include `stop_cleanup_ms` in `TimingMetrics.snapshot()`. Add a test with held key and button sets that asserts both releases are attempted and both sets are empty even when one release helper raises.

Run immediately after this production edit: `.\build.ps1`

- [ ] **Step 8: Run the complete focused player subset and rebuild**

Run:

```powershell
python -m unittest tests.test_core.PlayerTests tests.test_recorder_injected tests.test_recorder_timeline -v
```

Expected: all new P0/P1 tests and existing player/recorder tests pass except any pre-existing baseline failures explicitly identified before this work. Run `.\build.ps1` immediately after the final production edit.

- [ ] **Step 9: Commit Task 4**

```powershell
git add -- src/macroflow/execution/player.py tests/test_core.py
git commit -m "fix: preserve mouse coordinates and turn timing"
```

---

### Task 5: P0/P1 regression, static verification, and final package

**Files:**
- Verify: `src/macroflow/execution/timeline.py`
- Verify: `src/macroflow/input/recorder.py`
- Verify: `src/macroflow/execution/player.py`
- Verify: `tests/test_timeline.py`
- Verify: `tests/test_recorder_timeline.py`
- Verify: `tests/test_recorder_injected.py`
- Verify: `tests/test_core.py`
- Create: `scripts/benchmark_playback_timing.py`
- Build output: `dist/MacroFlowStudio.exe`

**Interfaces:**
- Consumes: the completed P0/P1 implementation and the pre-change baseline report.
- Produces: a freshly built executable, focused regression evidence, and a static artifact report. The final zip is created only after the final build succeeds.

- [ ] **Step 1: Run all new focused tests**

```powershell
python -m unittest tests.test_timeline tests.test_recorder_timeline tests.test_recorder_injected -v
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the relevant existing player tests**

```powershell
python -m unittest tests.test_core.PlayerTests -v
```

Expected: no new failures attributable to P0/P1. Compare any failures with the recorded pre-change baseline before deciding whether they are in scope.

- [ ] **Step 3: Add the real-clock backend benchmark script**

Create a command-line-only script that schedules evenly spaced synthetic events through `PlaybackTimeline` using `time.perf_counter` and `time.sleep`, then prints JSON metrics:

```python
def run_case(duration_s: float, event_count: int) -> dict:
    timeline = PlaybackTimeline()
    timeline.start()
    for index in range(event_count):
        offset_ms = duration_s * 1000 * index / max(1, event_count - 1)
        timeline.wait_until(offset_ms)
    result = timeline.metrics.snapshot()
    result["duration_s"] = duration_s
    result["event_count"] = event_count
    return result
```

The script must accept `--durations 10,60,600`, `--events 1000`, and `--json`. It must not import tkinter or start the application.

- [ ] **Step 4: Run the real-clock benchmark**

Run:

```powershell
python scripts/benchmark_playback_timing.py --durations 10,60,600 --events 1000 --json
```

Expected: one JSON result per duration containing total drift, average/P95/P99/max lateness, and no dropped-event count. Record the machine load conditions with the result; do not treat this command as a unit-test pass/fail gate.

- [ ] **Step 5: Run compile and diff checks**

```powershell
python -m compileall -q src tests
git diff --check
```

Expected: both commands exit `0`.

- [ ] **Step 6: Run the final build**

```powershell
.\build.ps1
```

Expected: exit `0`; `dist\MacroFlowStudio.exe` exists and is newer than the final source commit; `dist\paddle_ocr\` remains present.

- [ ] **Step 7: Run static artifact verification**

```powershell
python verify_build.py dist\MacroFlowStudio.exe
Get-Item dist\MacroFlowStudio.exe
Get-FileHash dist\MacroFlowStudio.exe -Algorithm SHA256
```

Expected: verifier exit `0`, non-empty SHA-256, and no missing required packaged component.

- [ ] **Step 8: Record the P0/P1 verification report and defer packaging**

Do not package this phase yet. The final zip is deferred until the later P2/P3 UI and detection phases are complete and the final build has succeeded.





```powershell
git status --short --branch
git log -5 --oneline
```

Report focused test counts, build exit code, artifact paths, and any unchanged baseline failures separately. Do not claim visual UI verification because the project explicitly forbids starting the interface for tests.

---

## P2/P3 continuation: detection isolation and unified refresh/UI

The first five tasks complete the P0/P1 playback line. The following tasks implement the second line without starting the GUI: recognition work leaves the input scheduler, UI callbacks are batched, and the grid-condition dialog adopts the shared spacing and semantic control rules.

### Task 6: Isolate global detection work behind a bounded worker

**Files:**
- Create: `src/macroflow/execution/detection_worker.py`
- Modify: `src/macroflow/ui/app.py`
- Create: `tests/test_detection_worker.py`
- Modify: `tests/test_core.py` only for app integration seams

- [ ] Add a worker with `submit(run_id, config_version)`, `poll()`, and `close()`; it owns one daemon thread, one active request, and one coalesced pending request. Results carry run id, config version, submitted/completed monotonic timestamps, hit/error, and never call Tk directly.
- [ ] Move the existing synchronous guard evaluator behind a private sync method. The real app's `_evaluate_global_guards` submits/polls the worker and rejects stale run/config results; `MacroFlowApp.__new__` test fixtures without a worker retain the sync seam.
- [ ] Increment a detection run id at execution start and a config version whenever guards are activated/cleared. Preserve ordered guard handling in the player thread: recognition only produces a result; `handle_guard_hit` remains on the player/execution thread.
- [ ] Ensure worker shutdown is invoked from app close and execution cleanup; bounded queues must not accumulate requests. Add fake evaluator tests for coalescing, metadata, stale-result rejection, exceptions, and close.
- [ ] After every production edit run `.`\\build.ps1`; run focused worker/global-guard tests, compileall, and diff check.

### Task 7: Batch Tk notifications and normalize the grid-condition dialog

**Files:**
- Create: `src/macroflow/ui/update_queue.py`
- Modify: `src/macroflow/ui/app.py`
- Modify: `src/macroflow/ui/dialogs.py`
- Create: `tests/test_update_queue.py`
- Modify: `tests/test_core.py` only for dialog pure helpers/seams

- [ ] Add a thread-safe UI update queue scheduled at 50 ms (20 Hz), coalescing status updates by key while preserving log/action order. Urgent error/stop updates may flush immediately. Keep log file writes synchronous and batch Text widget insertion.
- [ ] Route `_ui` through the queue and flush it during close; recording callbacks continue to submit data while action-tree/mini-window updates are batched. Do not change recorder/player semantics.
- [ ] Add shared dialog layout constants (4/8/12/16/24 spacing, field/button widths, semantic primary/secondary styles) and use one palette for the grid-condition dialog.
- [ ] Rework `GridRowConditionClickDialog`: split `x,y,w,h` into named fields with frame-picker entry; show image name with optional path detail; arrange left/right conditions in a responsive two-column/stacked layout; hide irrelevant type fields without blank rows; display columns from 1; map buttons to 左键/右键/中键; rename 确定 to 保存动作; show testing progress/result/取消 state and prevent duplicate tests.
- [ ] Keep the existing non-visual data contract and add pure tests for named-region parsing, 1-based column labels, condition field visibility decisions, and test-state transitions. After each production edit run `.`\\build.ps1`; run focused queue/dialog tests, compileall, and diff check.

### Task 8: Final P2/P3 verification and package

**Files:**
- Verify all modified source/tests and `dist/MacroFlowStudio.exe`
- Create final `MacroFlowStudio_latest_win64.zip`

- [ ] Run focused P0/P1, detection-worker, UI-queue, dialog, and existing player tests; compare baseline failures separately.
- [ ] Run compileall and diff checks; run `.`\\build.ps1`, then `python verify_build.py dist\\MacroFlowStudio.exe` and static artifact/hash checks. The pre-existing `_default_global_jump` verifier failure must remain explicitly recorded unless independently repaired in scope.
- [ ] Only after the successful final build and static checks run `.`\\pack.ps1`; verify the zip contains the current EXE, `dist\\paddle_ocr\\`, README.md, and CHANGELOG.md. Never launch the GUI or claim visual verification.
