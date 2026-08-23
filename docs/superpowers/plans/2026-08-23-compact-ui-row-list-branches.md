# Compact UI and Row-List Result Branches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MacroFlow Studio denser and more polished while adding success/failure result branches to list row condition clicks.

**Architecture:** Keep the existing Tk/ttkbootstrap UI and action schema. Add shared result-route fields to the row-list action, resolve routes in the player using action IDs, collapse inactive condition rows instead of merely disabling them, and tune the shared theme plus native Windows top-level corner preference.

**Tech Stack:** Python 3.13, Tk/ttkbootstrap, unittest, PyInstaller.

**Spec:** User request in the current task and attached reference image.

## Global Constraints

- Preserve existing module references and list scanning behavior.
- Do not launch or visually inspect the application; verify with backend tests, compilation, build, and static checks.
- Rebuild `dist\MacroFlowStudio.exe` immediately after every source modification.
- Do not create a zip for this ordinary feature/UI change.

---

### Task 1: Define row-list result behavior with failing tests

**Files:**
- Modify: `tests/test_core.py`

**Steps:**
- [ ] Add tests for successful row click routes: continue, jump to action ID, and end current script.
- [ ] Add tests for failed scan routes with the same three options.
- [ ] Add tests that the dialog schema persists both route fields and jump targets.
- [ ] Run the focused tests and confirm they fail for the missing behavior.

### Task 2: Implement row-list result branches and compact condition layout

**Files:**
- Modify: `src/macroflow/execution/player.py`
- Modify: `src/macroflow/ui/dialogs.py`
- Modify: `src/macroflow/ui/app.py`
- Modify: `tests/test_core.py`

**Steps:**
- [ ] Add route resolution after a matched click and after a completed no-match scan, preserving retry behavior.
- [ ] Add success/failure controls using the existing three-option result vocabulary and action-ID jump targets.
- [ ] Pass current script actions into the row-list dialog for readable jump target choices.
- [ ] Hide inactive image/text/number condition rows so each condition panel only occupies the relevant controls.
- [ ] Compact the row-list dialog spacing and action panel.
- [ ] Run focused tests and the relevant player/dialog test groups.

### Task 3: Apply the compact visual system

**Files:**
- Modify: `src/macroflow/ui/app.py`
- Modify: `src/macroflow/input/wininput.py`
- Modify: `tests/test_core.py`

**Steps:**
- [ ] Reduce default font, input padding, button padding, notebook spacing, and tree row height in the shared theme.
- [ ] Reduce the main window default/minimum footprint while keeping the existing responsive layout.
- [ ] Apply native Windows rounded top-level corners through DWM when available.
- [ ] Add backend tests for the new constants/attribute call path without starting a visible window.
- [ ] Run compile, targeted tests, build, `verify_build.py`, and `git diff --check`.

---
