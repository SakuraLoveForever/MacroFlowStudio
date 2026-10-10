# SSJJ route following implementation plan

Goal: record and follow a taught route using the local player's live coordinates.

Design: a read-only external Mono reader resolves classes and field offsets from
loaded metadata. A coordinate controller consumes only the local player's
position, yaw, death/ground state and map ID. Macro's existing Python action
interface owns input and emergency stopping. No injected code or game memory
writes. Static map exports provide bounds/version evidence, not walkability.

Scope: current verified x64 Mono binary only (SHA256 checked). Route teaching and
following on one map; no automatic navigation-mesh generation, combat, or UI
changes. Stop on an invalid snapshot, death, map change, lost focus, excessive
starting distance, timeout, or lack of progress. Do not build/package or operate
the visible game for verification, per AGENTS.md.

Files:
- `src/macroflow/execution/ssjj_state.py`: process, metadata and local state reads.
- `src/macroflow/execution/ssjj_navigation.py`: route validation, recording, follower.
- `examples/python/ssjj_follow_route.py`: existing Macro Python script entry.
- `examples/python/ssjj_route.json`: user-taught map 100001 route.
- `src/macroflow/execution/python_script.py`: checked parent-side input RPC.
- `tests/test_ssjj_navigation.py`: headless metadata and navigation regression tests.
- `docs/ssjj-navigation.md`: source commands, supported version, limitations.

Steps:
- [x] Add failing tests for route validation, steering, arrival and failure handling.
- [x] Implement the reader and verify actual read-only samples from the running game.
- [x] Implement recording and following through existing input; run headless tests.
- [x] Review failure handling, run relevant source tests and compilation checks.
- [x] Commit only these task files locally; user explicitly cancelled pushing.

Ruling: use the current checkout with only new task files, because the workspace
contains unrelated pending changes; never stage those changes. User's continuous
execution authorization supersedes per-step approval gates.

Evidence: process 47868, battle/1_64, Mono SHA256
7cf37b3415c2a168c724163be78109323ca6f75112eb549f2197a528eaebb4c9.
Map ID 100001 matches cached revision 9a81a2a5059f8b732da623f373b38c81.
Manual movement changed raw-game XY and yaw. The later 20-second sample was
stationary; it must not be described as a moving trajectory.

2026-10-11: recorded a real 30-second taught route: 39 points, length 4188.6,
constant Z 519.789. First recording attempt correctly stopped because no battle
client was running. Second user-authorized attempt completed. Route start/end
are in the usage document.

Independent review found generic key_press holds W across pause and child-side
validation can precede an arbitrarily delayed parent action. Added ssjj_input
RPC using existing wininput functions, parent-side fresh state/focus checks,
bounded interruptible W pulses, and per-script reader cleanup. Control clocks
exclude RPC blocking; turning has its own total timeout, and every action waits
for a newer state timestamp. Reviewer re-reviewed these fixes without blocking
findings. Targeted tests: 27 passed; no UI launched for verification.

After the user explicitly said to start, the backend executor followed all 39
waypoints successfully. Initial distance to start 199.35; independent final
snapshot distance to finish 66.79 (<80), alive. No screenshot, visible UI
inspection, software UI launch or build occurred. This confirms the single
taught route, not general obstacle avoidance or arbitrary-map pathfinding.

Delivery change: user explicitly said "不要推送" after the run. Keep a local
commit only; do not push, overriding the repository default delivery rule.
