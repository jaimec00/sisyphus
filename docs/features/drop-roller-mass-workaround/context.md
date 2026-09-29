# context.md — i141: drop the #138 roller-mass workaround

## Brief (issue #141)
The base-slip defect (base delivered ~0.2x commanded speed at vx >= ~0.14 m/s)
was fixed in #138 by retuning `_ROLLER_MASS` 0.01 -> 0.16 kg — a **workaround**
for the then-live vendored MuJoCo 3.9 solver, not a physical value (a real
omniwheel barrel roller is grams). Root cause (R9) was a MuJoCo 3.9-vs-3.12
solver-generation delta. The split is now resolved (#139/#140 merged; the ROS
sim builds+links **MuJoCo 3.12.0**).

**Task:** plain value edit (NOT `git revert`) `_ROLLER_MASS` 0.16 -> 0.01 in
`src/robot_description/robot_description/mjcf_model.py`; re-run the fidelity
regression `test_base_delivers_commanded_vx_through_the_ros_chain`
(0.10 / 0.14 / 0.20 m/s) through the full ROS chain on the 3.12 sim; full
`pixi run test`.

**Acceptance (binding):**
- >= 0.85x commanded AND |dyaw| <= 0.30 at all three speeds with 0.01 kg on
  3.12 -> workaround dropped, PR lands.
- If it slips at 0.01 kg on 3.12 -> **do NOT keep 0.16, DEBUG the contact model**.
- No mitigation (no vx/vy caps); do not reintroduce 3.9.

## Owned paths
- `src/robot_description/robot_description/mjcf_model.py` (the one-line edit +
  its doc-comment).

## Current code (empirically observed, worktree @ c0568e4 = origin/main)
- `mjcf_model.py:165` — `_ROLLER_MASS = 0.16`.
- `mjcf_model.py:169-172` — inertia recomputed from `_ROLLER_MASS`:
  `_ROLLER_I_AXIS = _ROLLER_MASS * _ROLLER_RADIUS**2 / 2.0`,
  `_ROLLER_I_TRANS = _ROLLER_MASS * (3*_ROLLER_RADIUS**2 + (2*_ROLLER_HALF_LENGTH)**2)/12.0`.
  So a mass edit propagates to inertia automatically — no manual tensor edit.
- `mjcf_model.py:146-164` — the comment block above `_ROLLER_MASS` documents the
  #137 workaround rationale (0.01 -> 0.16 for the vendored 3.9 solver). After the
  revert this comment is stale/contradictory and must be updated.

## The fidelity regression (acceptance gate)
- `src/robot_bringup/test/test_pr2_navigate.py:1019`
  `test_base_delivers_commanded_vx_through_the_ros_chain(vx)`,
  parametrized `VX_FIDELITY_COMMANDS = (0.10, 0.14, 0.20)`.
- Asserts (a) `delivered = dx/(vx*3.0) >= MIN_VX_FIDELITY_RATIO = 0.85` and
  (b) `|dyaw| <= MAX_VX_FIDELITY_DYAWR = 0.30`, measured via ground-truth
  `GetBodyState('base_link')` displacement over 3 s **through the shipped
  `mujoco.launch.py` ROS chain** (sim + controllers + Nav2), one fresh sim
  session + ROS domain per speed.
- Skips if `mujoco_ros2_control` not installed (`_have_package`); it IS
  installed (source-build via robot.repos, D33), and it now links MuJoCo 3.12.0
  (D38) — verified by `objdump` in #140.

## History (from decisions.md D38, #138/#140 commit messages)
- #138: `c992bd5` raised 0.01->0.16; `6a4d51f` added the ROS-chain fidelity
  regression + "stabilize rim-roller contact in vendored 3.9".
- #140 (`c0568e4`): aligned the ROS sim onto MuJoCo 3.12.0 via
  `FETCHCONTENT_SOURCE_DIR_MUJOCO` override; `robot.repos` pins `google-deepmind/mujoco`
  @ `13827e9` (3.12.0 tag) and `dfki-ric/mujoco_ros2_control` @ `f151b7d`.
- **D38 explicitly notes the #138 0.16 kg is "now orthogonal to the split" and
  flags "test 0.01 kg on the now-unified 3.12 path" as the *optional follow-up*** —
  this issue is exactly that follow-up.
- #138's commit message: "Direct 3.12 test_mjcf_drive.py stays green" (0.01 kg
  holds on the *direct* 3.12 Python path); the open question is the **ROS-chain**
  3.12 path, which is what this PR verifies.

## Test baseline
- `scripts/test_baseline.json`: `robot_bringup` = 18 (14 -> 17 from #138's 3 new
  parametrized probes, -> 18 from #140's version-split test). No test count change
  is expected from this value-only PR (no tests added/removed), so the ratchet
  file should be **unchanged** — but re-run `pixi run test` and commit it only if
  the floor actually moves.

## Gotchas
- `pixi run build` requires `src/mujoco` + `src/mujoco_ros2_control` (vcs import /
  copied from a sibling worktree) — the build task passes
  `-DFETCHCONTENT_SOURCE_DIR_MUJOCO=$PWD/src/mujoco`.
- The fidelity test spawns a full ROS sim per speed — slow; run via the
  test-runner with `nohup ... &`, poll short.
- `pixi run test` runs `check_test_integrity.py` (ratchets test counts) and
  `check_provisioning.py` (fails if `install-openclaw` missing).
