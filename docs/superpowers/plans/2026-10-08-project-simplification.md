# Project simplification implementation plan

**Goal:** Tidy generated project clutter and let Studio and Runner use one execution controller without Runner importing editor/recording implementations.

**Spec:** User approved items 1, 2 and 3: organize the project, extract the shared runtime, keep JSON and one engine. Preserve scripts, images, workflows, modules and settings. No AHK/Maa engine, migrations, compatibility layers or visible UI testing.

**Architecture:** Existing MacroPlayer stays the playback engine. Move shared controller and presentation lifecycle methods out of editor mixins; Studio adds authoring mixins and Runner adds its compact view. Use explicit imports and classes, with no dynamic method copying at runtime. Startup imports the selected client only.

**Constraints:** Existing uncommitted changes remain. Build/pack entry commands stay usable. Retain user files inside dist and the extracted runner. Only delete verified disposable artifacts. All verification runs in background.

- [x] Inventory and clean disposable files; organize developer documentation/tools; document data and distribution directories.
- [x] Add failing import-boundary and shared-method identity tests.
- [x] Separate shared controller initialization, workflow execution and helper methods from authoring; remove eager editor imports from runtime modules and package entry.
- [x] Ensure Runner never creates a recorder or loads editor drafts; preserve run/pause/stop, guards, recheck, foreground behavior, recovery and progress.
- [x] Run affected tests and inspect callback completeness without opening Tk windows.
- [x] Run full tests, build both clients, verify executable archives and package both releases.
- [x] Review the final diff, fix important findings and verify package hashes/user-data exclusion.

**Review:** Removed obsolete refresh_workflow_files callback in Studio display/DPI rebuild. All 1451 tests pass; independent executable build, archive checks and ZIP integrity/hash/user-data exclusion checks passed. Cleanup used reversible archive after automatic approval rejected deletion.
