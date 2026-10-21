# Waypoint recording implementation plan

> **For agentic workers:** Use superpowers:executing-plans inline. Steps use checkbox tracking.

**Goal:** F7 captures explicit route checkpoints; keyboard, buttons and scroll replay on a timeline restarted at each arrival, with navigation owning movement and turning.

**Architecture:** Replace dense `samples` with ordered `checkpoints` (time, position, yaw), retaining all non-turn input events. Reuse the existing coordinate follower and held-input watchdog. During each leg, replay the preceding checkpoint's local input segment; never begin the next segment until arrival and completion of the preceding segment's events.

**Tech Stack:** Python, existing pynput, Tk authoring panel, current SSJJ reader/controller.

**Spec:** User-approved design in this chat, 2026-10-11.

## Global constraints

- No compatibility branch for old dense recordings; they must be re-recorded.
- Only explicit F7 marks persist positions; require at least two marks. Ignore pre-mark input.
- Capture WASD but exclude it from input playback; the coordinate controller owns movement.
- F7 is physical, edge-triggered, excluded from recorded inputs, and cannot conflict with a bound hotkey script.
- Require a nearby first checkpoint (250 units); do not attempt a distant straight-line return through unknown geometry.
- No GUI launch/checks, build, packaging or push. Commit only this task's changes.

## Review focus

- Death, focus loss, stale state, pause, guard handling and exceptions release both navigation and action inputs.
- Held action keys spanning a checkpoint resume after arrival/alignment; original movement keys never compete with navigation.
- Sparse points may be farther than 1000 units; subdivision preserves the marked straight leg without claiming obstacle avoidance.
- Stationary repeated-position checkpoints execute in recorded order; stopping without enough marks preserves an actionable error.
- Next-point actions wait for actual arrival, including when walking is slower than recording; no global recording clock advances them early.

### Task 1: Capture and authoring

Files: `input/recorder.py`, `execution/ssjj_monitor.py`, `ui/dialogs/ssjj_coordinates.py`, `tests/test_ssjj_input_recording.py`.

- [x] Write failing tests for explicit marks, physical key edges, no turn capture, pre-mark filtering, freshness and stop validation.
- [x] Replace dense capture, reuse recorder key callback/buttons mode, add F7 instructions and point/input counts.
- [x] Run capture and ordinary recorder regressions.

### Task 2: Arrival-synchronized playback

Files: `execution/ssjj_recording.py`, new `tests/test_ssjj_waypoint_playback.py`, summary hunk in `ui/app/summaries.py`.

- [x] Write failing simulation tests for slower/faster navigation, movement ownership, sparse points, yaw alignment, held action keys, pauses/guards and stop/focus/stale/death failures.
- [x] Validate explicit checkpoint data; replace global interpolation replay with local segment scheduling and existing follower contexts.
- [x] Run SSJJ and recorder suites; run full headless discovery with Tk creation prohibited; compile changed files.
- [x] Request an independent focused review, resolve findings, and commit task-only changes.

## Reuse research — checked 2026-10-11

| Candidate | Stars | Maintenance / release | License | Reuse and cost |
|---|---:|---|---|---|
| [pynput](https://github.com/moses-palmer/pynput) | 2173 | pushed 2026-05-12; no GitHub latest release | LGPL-3.0 [license](https://github.com/moses-palmer/pynput/blob/master/COPYING.LGPL) | Already installed; reuse keyboard/button/scroll listeners, low integration cost, retain existing licensing obligations. |
| [Nav2](https://github.com/ros-navigation/navigation2) | 4788 | pushed 2026-10-09; release 1.5.2 on 2026-09-16 | Repository is mixed-license; package-specific license applies | [Waypoint tasks](https://github.com/ros-navigation/navigation2/blob/main/nav2_waypoint_follower/README.md) illustrate arrival gating. ROS2/C++ deployment cost is high for Windows/Python game input; design reference only, no copied code. |
| [py_trees](https://github.com/splintered-reality/py_trees) | 641 | pushed 2026-09-17; release 2.6.0 on 2026-09-10 | BSD-3-Clause [license](https://github.com/splintered-reality/py_trees/blob/devel/LICENSE) | Python task orchestration is usable, but supplies neither game navigation nor recording synchronization. Adds unnecessary tree integration and maintenance; not selected. |

All three repositories are unarchived. Dates above are GitHub push metadata, not independently established last substantive code-change dates (unverified). Reuse existing dependencies and local navigation; no new packages or copied upstream implementation.

## Execution ledger

- Execute inline under AGENTS.md continuous authorization; no extra approval checkpoint.
- Approved contract: no automatic start/end marks. Mark start and finish with F7; F8 saves the explicit points and final stationary action segment.
- Ruling: retain full within-segment event timing; transit tails after the last action need not delay an already-arrived next checkpoint. Preserve the final stationary segment duration — avoids replaying the old travel duration after faster arrival — cost if wrong: add an explicit timed action for an intentional pause at an intermediate point.
- Ruling: reuse measured sensitivity within one recording and start the segment only after navigation is ready — prevents jump/fire during calibration — cost if wrong: changing game mouse sensitivity during replay requires restarting playback.
- Task 1: complete. Explicit capture tests observed failing before implementation; incomplete F8 listener regression observed RED then GREEN. Related capture/navigation/input/recording suites: 189 tests, 1 prohibited GUI test skipped, remainder passed.
- Task 2: complete implementation. Shared freshness regression across many stationary points observed RED then GREEN; inputs partitioned once in timestamp order. Focused suite: 44 passed before final review.
- Full discovery before final review fix: 1720 tests, 37 prohibited GUI tests skipped, one pre-existing failure: `tests.test_ui_contract.PackageEntryTests.test_app_package_entry_does_not_aggregate_client_implementations` (`disconnect_monitor` aggregation). Matches the pre-task baseline; unrelated application/package refactor left untouched.
- Final review: independent read-only reviewer found one Important issue (airborne arrival aborted alignment), no Critical or Minor findings. Regression `test_airborne_arrival_waits_with_inputs_released_before_next_actions` observed RED then GREEN; boundary stop/focus/stale/landing-timeout regression also RED then GREEN. Focused suite after fix: 46 passed. Both owners stay released until fresh grounded telemetry; maximum active landing wait is 5 seconds.
- Final review scope: obstacle avoidance is explicitly excluded; legs follow straight lines between marks, so mark corners/doors/jumps. Visible UI inspection is prohibited by AGENTS.md. Unrelated working-tree changes and the baseline package-import failure are excluded from this task.
- Final verification (2026-10-12): full headless discovery ran 1722 tests in 47.643s, OK with 38 skips (37 GUI creation tests prohibited by AGENTS.md, plus the explicitly isolated pre-existing package-entry failure reproduced above). Remaining 1684 tests passed. Changed Python files passed `py_compile`; task diff passed `git diff --check`. No application launch, build, package or push. Only the task's summary line is staged from the already-dirty summaries file; other existing work remains outside the commit.
