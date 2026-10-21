# Portable bundles and execution client implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task in the existing checkout. Preserve existing user changes. No visible UI testing.

**Goal:** Both applications import/export a script or workflow with all dependencies; provide an execution client without editor widgets.

**Architecture:** A shared standard-library ZIP/JSON bundle service gathers scripts, module objects and images recursively. Imports validate the whole archive before installing into an isolated imports directory and remap module identities without overwriting existing data. The execution client uses the existing controller and player with a dedicated compact, read-only view.

**Tech Stack:** Python, zipfile, JSON, tkinter/ttkbootstrap, existing MacroPlayer and workflow controllers, PyInstaller.

**Spec:** Approved conversation: separate scripts/workflows/images directories, relative references, single-script and workflow bundles, import/export on both clients, existing runtime behavior.

## Global constraints
- No new dependencies or alternative execution engine.
- No visible UI launch/check; verify through background unit tests and archive inspection.
- Build Studio and Runner, run full unittest discovery and verify_build.py; update distribution ZIP.
- Never overwrite user modules or personal scripts when importing.

## Review focus
- Nested/cyclic script references and fallback/second-match modules.
- Missing image or script aborts export rather than silently producing an incomplete bundle.
- ZIP traversal, duplicate members, invalid references and expansion limits must fail before installation.
- Module identity collisions must not change existing scripts.
- Runner preserves global guards, workflow restart, emergency stop and progress behavior.

### Task 1: Portable bundle service
Files: core/bundles.py, tests/test_bundles.py.
Interfaces: export_bundle(destination, *, script=None, workflow=None, script_path=None); import_bundle(source) -> (kind, entry_path).
- [x] Write/run failing dependency closure and hostile archive regression tests.
- [x] Implement deterministic relative paths, recursive module inclusion and validated isolated imports.
- [x] Run tests.test_bundles.

### Task 2: Shared import/export controls and compact runner
Files: ui/app/bundles.py, ui/app/runner.py, main.py, shell.py, startup.py; tests/test_bundle_ui.py.
- [x] Test import selection, export of current snapshots and runner routing without constructing Tk windows.
- [x] Add Studio import/export controls; Runner supplies read-only script/workflow library, execution options, window binding, run/pause/stop, progress and log.
- [x] Reuse all current player/workflow/global detection behavior.

### Task 3: Delivery
Files: MacroFlowStudio.spec, build.ps1, pack.ps1, verify_build.py, README.md, CHANGELOG.md.
- [x] Build both executable entry modes, document package layout and runtime dependencies.
- [x] Run full tests, build.ps1, verify both archives, pack.ps1 and inspect ZIP contents.
