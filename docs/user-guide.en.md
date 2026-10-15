# User guide

[Back to overview](../README_EN.md) · [中文指南](user-guide.md)

[Recording](#recording--playback) · [Editing](#script-editor) · [Recognition](#image-matching--ocr) · [Modules](#module-objects) · [Workflows](#workflows)

## Recording & Playback

- **Smart track recording**: records raw relative movement while the cursor is locked at the window center (game view turning, sampled at ≤16 ms); desktop coordinates resume when the cursor leaves the target window (default interval 20 ms, configurable 10–500 ms)
- Automatically switches the target window to English (US) input method before recording; `F8` re-records directly over the current document (a confirmation appears first when there are unsaved changes); saving over an existing script file asks for confirmation and archives the old version to `backups/overwritten/` (timestamped history) before overwriting
- **Playback**: start from a specific line, repeat counts, breakpoint resume, multi-click, click at current cursor position

## Script Editor

Insert the following actions on the Script Editor page; double-click an action to edit, hold Shift/Ctrl to multi-select:

| Action | Description |
|---|---|
| ⏱️ Delay | Wait a fixed number of milliseconds |
| ⌨️ Keyboard / key press | Press / release / tap keys; key can be auto-detected on insert |
| 📝 Text | Type text |
| 🖱️ Mouse move / button / click / multi-click | Pick coordinates on screen; left / right / middle button |
| ↔️ Drag | Press at the start point, drag to the end point and release (screen-pick or manual coordinates); steps / duration / mouse button configurable |
| 🎡 Wheel | Scroll up / down a chosen number of notches at a given position (toolbar “↕ 滚轮”, coordinates can be picked on screen) |
| 🖼️ Image match | Template matching (see below) |
| 🔤 OCR text | Offline region text recognition (see below) |
| 💬 Floating notice / comment | On-screen reminder / note text |
| 🔗 Script reference | Load and run another script in place (embedded execution, no jump); right-click the row in the action list → "▶ 执行指定次数…" to run just that referenced script 1–N times (default 1) |
| 🚀 Open / close app | Launch a program (with args) / gracefully or forcefully terminate a process |
| 🧭 Jump | Jump to a target action line, script start, or script end |
| 📌 Foreground window | Bring the bound window to the foreground before execution |
| ⭐ Special modules | Restart workflow / end the innermost script and continue / jump to the actual last line |
| 🧰 Module reference | Insert a saved module object (see "Module Objects") |

Editing: undo / redo, move up / move down (a contiguous multi-row selection moves as one block), duplicate down (multi-select supported), run from here (trial run skipping earlier actions), clear undo history after save. Opening or creating another script while the current one has unsaved changes asks first (save / discard / cancel) instead of silently doing nothing.

## Image Matching & OCR

**Template matching** (for fixed patterns):

- **Region**: full screen / bound window / custom region (drag-select with the left button)
- **Threshold**: minimum similarity
- **Timeout**: wait timeout in ms + polling interval; on timeout: continue / jump to a line / stop
- **On match**: continue / click (region center or custom coordinates) / jump to a line / end the current script and move to the next workflow step
- **Secondary match**: optionally confirm with another template after a hit
- The target must be visible in real time; minimum polling interval 50 ms

**OCR text recognition** (reads changing text, complementary to image matching): powered by RapidOCR 3.9.2 with local PP-OCRv6 small models and CPU ONNX Runtime

- Drag-select a region (empty = full screen), or bind a target window
- Expected text matched by "contains / equals"; empty = any recognized text hits
- Polling with timeout, then continue / jump / stop; results shown in the status bar and the execution mini window
- **Run a code segment on failure** (per script line): image match, OCR text, OCR number compare, multi-condition image click and list row-by-row click can each opt in; on a miss / timeout / blocking timeout the segment runs first (add / edit / remove / move, supporting delay, keys, text, clicks, repeated clicks, moves, module references, script references, open-close app, notice, foreground window, jump to script end, end current script), then the failure branch is taken

## Module Objects

The Module Object Manager stores recognition settings as reusable objects, categorized as **Switch / Workflow Global / Script Global / Special**.

- **Unique identity**: referenced by ID rather than image path; one image can back multiple modules without overwriting
- **Recognition settings**: template, region, threshold, hold duration, recognition mode (template image, text, read number, or no recognition)
- **Success action + post-action code segment**: the action executed on a hit, optionally followed by extra actions (jumps, foreground window, etc.)
- **Timeout code segment**: actions run when "continuous miss" exceeds the configured limit
- Repository: pinyin sorting, bulk add / bulk remove from scripts, double-click edit, undo removal

### Reading Numbers Module

Create a Switch module, set the recognition mode to **Read Number**, and drag-select the region containing the digits. At runtime, OCR digit boxes are concatenated left-to-right into an integer (e.g. `0`, `0`, `7` → `7`).

When inserting the module into a script, configure the **compare number** and two result branches: **equal / not-equal-or-not-read**. Both branches can continue to the next line, jump to a stable line object, or end the innermost script; when no number is read, it retries per the module's blocking / timeout settings, and a non-blocking timeout falls to the failure branch.

After inserting a blocking module into a specific script, double-click that reference row to optionally enable **skip after blocking timeout** and set its duration. It is off by default, applies only to that script row, and leaves other references to the same module blocking as configured.

## Workflows

The Workflow page organizes multiple scripts in order; each step supports:

| Setting | Description |
|---|---|
| 📄 Script | Double-click to change |
| 🔁 Repeat count | Remaining-count system (no decrement on failure or manual stop, shows "done" at 0); 0 or **unlimited** (once per round); optional **start from a specific line from the 2nd run** |
| ⏳ Wait before run | Milliseconds to wait before executing |
| ⏱️ Repeat interval | Gap between consecutive runs of the same script, default 1000 ms |
| ✅ Enable / disable | Disabled rows are greyed out and skipped without decrementing |
| 🌐 Global modules | Attach workflow-global modules to a step |

Other operations: select a row to **run from the selected line**; **bulk-set parameters** (counts / wait / interval); step deletion is undoable; rows whose script file is missing are highlighted red with a reason at runtime; **scheduled start** accepts a `YYYY-MM-DD HH:MM:SS` time (empty = run immediately; the app must keep running while waiting). At the top of the page you can **✏️ Rename** the current workflow (the file is renamed on save, never overwriting an existing file) or **⧉ Duplicate as a new workflow** (copies the whole workflow into an independent new file and opens the copy right away, so you can derive a new flow without re-inserting steps).

Workflows are plain files (JSON under `workflows/`): New / Open / Save. The UI shows the file content; saving overwrites it directly.

### Global Detection

During workflow execution, enabled global modules keep scanning (sharing one screenshot per round, no repeated captures) and trigger when their condition is met:

- **Interrupt & resume**: on trigger, pauses the current script and saves a breakpoint, runs the module's success action, then resumes the original script
- **Click**: clicks the region center or custom coordinates; ensures the target window is in the foreground first
- **Restart workflow**: on trigger, restarts the workflow from a configurable row (per-action or per-module, falling back to the global default row; "set as default" in the module form) instead of a single unified jump target

## Execution & Settings

- **Focus lock mode** (off by default): switches to the English input method + system-level mouse/keyboard lock (`BlockInput`) to block misoperation; `F12` remains the emergency stop
- **Foreground restore**: with "activate before execution" enabled the target window is activated once when playback starts (never when it is already foreground, which would make games show the "click to continue" overlay); after every global-detection screenshot the bound window's foreground is verified once and taken back only if it was lost (zero cost otherwise). Click/move coordinates outside the target window are clamped inside it so clicks cannot steal focus
- **Foreground window**: independent toggle, brings the target window to the front before execution
- **Mini windows**: always-on-top mini window for execution / recording (independent toggles) showing progress and the action log
- **System tray**: with "start to tray" enabled the app stays in the tray on boot and a double-click restores the window; the close button saves the draft and quits (minimize to tray first if it must keep running), and the tray menu can restore the window or quit
- **Hotkey sounds**: distinct tones for record start/end, run, complete, and emergency stop; testable
- **Hotkey scripts**: bind a script to a single key (letters, digits, F1–F12, arrows, etc.; F8/F9/F12 and bare modifiers excluded) in the sidebar "Hotkey Scripts → Settings…". During **recording**, pressing the hotkey runs the bound script and its injected keys/mouse get recorded (the hotkey key itself is not); during **execution**, the bound script runs in parallel in its own player without interrupting the current script/workflow; `F12` stops all hotkey scripts too. Bindings persist in `app_settings.json`
- **Read current coordinates**: shows the live cursor position and the window under it for manual coordinate entry
- **Resolution scaling**: scripts store the recording machine's virtual screen size; coordinates are scaled automatically on another machine (legacy scripts assumed 1920×1080)

## Backup & Logs

- **Timed backup**: fixed intervals `1h` / `1 day` / `1 week`; scans the configured script directories into `backups/scripts/`; each script maps to one target file (no history copies)
- **Auto start on boot**: writes the current Windows user's startup entry; unchecking removes it
- **Start to tray / auto-run workflow at startup**: can be combined; after an auto workflow finishes it stays in the tray, double-click the tray icon to restore
- **Run logs**: written to `logs/YYYY-MM-DD/MacroFlow_<start time>_<pid>.log`, one file per launch; "Open log directory" on the log page

## File Layout

Created next to the exe on first launch:

```
MacroFlowStudio/
├── MacroFlowStudio.exe   # Main program
├── rapidocr_ocr/          # RapidOCR + CPU ONNX Runtime + local models/dependencies/licenses (loaded on demand)
├── scripts/              # Scripts
├── workflows/            # Workflows
├── images/               # Recommended location for image-match templates
├── backups/scripts/      # Timed backups
├── logs/                 # Run logs (by date / session)
└── app_settings.json     # All sidebar settings and window binding
```

The "Open" button can load JSON scripts from other directories; put templates under `images` and use relative paths (e.g. `images\start_button.png`) in image-match actions for easy migration.

## Additional controls

Module management and selection support keyword filtering within category tabs. The module's fixed pre-recognition wait runs before detection. Time inputs default to seconds while stored values remain milliseconds. Normal application close stops on timeout instead of escalating to a forced kill; optional related process names must exit before the workflow continues.

Recognition settings separate the target condition (present / absent) from the wait rule (try once / wait with a deadline / wait indefinitely). To delay the first recognition, edit the module reference in the script and set the pre-recognition wait in seconds (5 = 5 seconds). Polling, cooldown, confirmation and fallback options are under advanced recognition settings; collapsing them preserves their values.

## Usage limits

- Track sampling interval: at least 10 ms. Image polling: at least 50 ms. Recording limit: 200,000 actions.
- On stop or abnormal exit, the app attempts to release held keys and mouse buttons.
- Execution suppresses screensavers, display timeout and automatic sleep without changing power settings.
- Check that the target application permits macros and automation.
