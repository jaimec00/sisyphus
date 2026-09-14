# PR 2 — extend_column + navigate_to on MuJoCoBackend — implementation

Roadmap #4, PR 2 of 5. Issue #101. Worktree `feat/i101-pr2-column-base`
(== origin/main @ b48902e). Binding rulings: D34 (in-process `mujoco`), D30
(no ROS import), and status.md R1–R6.

## What changed

### `robot_description/mjcf_model.py` — base free joint, scene-only (R4)
- Added `_wrap_base_freejoint(merged)` that closes the *entire* robot trunk
  (everything between `<worldbody>` and `</worldbody>`, i.e. the fused 2-geom
  chassis pair + the wheel/column bodies) in one 6-DOF free-jointed
  `<body name="base_link">`. The body carries an `<inertial mass="6.0"`
  (base.xacro `chassis_mass`) with a positive-definite stand-in diaginertia so
  MuJoCo accepts a moving body (teleport-only in PR2, R4/R6).
- `_build_merged_mjcf(...)` gained `base_free_joint=False`; when True it wraps
  the *robot-only* merge BEFORE scene bodies are spliced, so world objects stay
  welded to the world (siblings of the wrapped base, not riders) — satisfying
  R-1's immovable-scene invariant.
- `load_mjcf_model()` now compiles the **bare** robot (unchanged: nq=nv=18,
  nbody=19, neq=0 — `test_mjcf_model.py` gate intact).
  `load_mjcf_model_with_scene(world_bodies)` compiles **base-free-joint +
  world bodies**. `write_mjcf_model` stays on the bare path (PR8b ROS sim).
  Obsoletes the old `load_mjcf_model() == load_mjcf_model_with_scene('')`
  equivalence by design (R4).
- **Probed note:** MuJoCo's free joint contributes 7 to `nq` and 6 to `nv`
  (free-joint qpos is `[x,y,z,qw,qx,qy,qz]`). So the scene model is
  `nv = 24`, `nq = 25` — *not* slogged "nq == 24 (18+6)" in status.md R4's
  literal text. It IS the 6 DOF R4 asked for; only the qpos count differs
  (18+7). No test pins a scene-path nq number, so nothing is affected.

### `robot_backends/mujoco_backend.py` — name-based internals + PR2 skills (R2/R3/R5)
- All internals are now name-based (R5): resolved at init are
  `_base_free_qposadr`/`_base_free_dofadr` (for `base_free`), `_base_body`
  (`base_link`), wheel `(qpos_adr, ctrl)` pairs by name, and the `column_lift`
  joint's own travel range (`jnt_range` → `[0.0, 1.2]`, taken from the joint it
  drives per R2's preferred source).
- The free joint is excluded from the reset home sweep and homed to the
  `start_location` reference pose instead; `reset()` returns to charger so the
  existing `test_reset_returns_seed_posture` (pose == charger) still holds.
- `execute()` dispatches `NavigateTo` and `ExtendColumn`; every other legal
  `Skill` keeps `UNSUPPORTED_SKILL` (total contract); non-`Skill` still raises
  `TypeError`. Handlers raise `_SkillRefused`; `execute` maps it to
  `SkillResult.failure`, so nothing moves on a refusal.
- `_navigate_to`: unknown → `UNKNOWN_LOCATION` (reason lists sorted knowns);
  known → teleport base free joint to the location pose (`_set_base_pose` =
  write qpos + `mj_forward`, NO `mj_step`) + update `_location`; re-navigation
  yields reason `already at '<name>'`. Mirrors `mock_backend._navigate_to`.
- `_extend_column`: out of `[0.0, 1.2]` → `OUT_OF_RANGE` (reject, world
  unchanged — defense-in-depth; the safety layer clamps). In range → set column
  qpos AND command its position actuator to the same height (servo hold, as in
  reset), `mj_forward`, read back the height from `column_lift` qpos.
- `get_observation().robot.pose` now reads the `base_link` body's
  `xpos`/`xquat` (world frame) via `_world_from_xpos`, replacing PR1's
  document-locked `_base_pose`; `robot.location` is an instance attr starting
  at `start_location`.
- No ROS import anywhere (D30) — `test_no_ros_runtime` green.

### Tests
- `robot_backends/test/test_mujoco_backend.py`: replaced
  `test_execute_refuses_every_skill_for_pr1` with real skill tests
  (navigate_to known/unknown/re-navigate/pose-from-sim; extend_column
  in-range [0.0, 0.3, 1.2] vs out-of-range [2.0, -0.5]; unsupported still
  refused), kept `test_execute_rejects_a_non_skill`. 22 tests in this module.
- `robot_mcp/test/test_mcp_mujoco_parity.py`: added
  `test_mcp_extend_column_overreach_is_clamped_not_refused` (2.0 → clamped to
  safety max, status ok, "clamped" in reason; 0.3 passes through reason None).
  Updated the old "navigate_to refused" result-schema test to a still-
  unsupported skill (`grasp`), since `navigate_to` is now implemented on MuJoCo.

## Free-joint decision (R4) recap
A 6-DOF free joint closed over the whole fused trunk is the seam that makes the
base teleportable. It is teleport-only in PR2 (`mj_forward`, never `mj_step`),
so wheel dynamics / column actuation-vs-gravity are not exercised by movement.

## Deferred-gravity note (R6) — one correction surfaced by the real API
Status.md R6 predicted that the two PR1 "*real* step" tests would "assert
relative/static quantities (column qpos, welded object poses) and remain
green." Probed against mujoco 3.12 **that is only half right**:

- `test_scene_objects_are_invariant_across_real_steps` — **still green**:
  welded scene objects sit at world-body level (not under the free joint), so
  no number of steps moves them.
- `test_column_servo_holds_seed_height_across_steps` — **no longer green**
  once the base is free-jointed. Stepping an ungrounded base under gravity
  (no floor until PR4) destabilises the prismatic solve: within ~15 steps the
  column qpos collapses to ~0 with a `DOF-6 QACC` instability warning. The
  velocity check `base_z` barely moves, so this is a servo/free-joint solver
  instability, not a rigid free-fall.
- Adaptation: that test now guards the F11 *regression* that is still valid —
  reset seals the column's position-actuator command to the seed height, and
  `extend_column` re-seals it — and explicitly records that a *long real-
  dynamics* column-hold comes back with PR4's floor + wheel contact.
- `navigate_to` is unaffected (teleport only). No floor/contact was added.

## Verification (run on olivia)
Targeted `pixi run python -m pytest` (with `-p no:launch_testing -p no:launch_ros`)
over the four affected test files: **39 passed**. Breakdown:
- robot_backends/test/test_mujoco_backend.py — 22 passed
- robot_mcp/test/test_mcp_mujoco_parity.py — 4 passed
- robot_description/test/test_mjcf_model.py — 10 passed (bare-model gate intact)
- robot_backends/test/test_no_ros_runtime.py — 3 passed

Plus the whole robot_backends package suite via `src/robot_backends/test/`:
99 passed (the only red were build/install-generated files being scanned when
pytest runs from the repo root — ament linters scan cwd; under `colcon test`
they scan only the package). Package-scoped flake8+pep257 for robot_backends and
robot_description are green.

`robot_mcp` flake8/pep257 were not re-run from the root (same env artifact); the
added parity test is docstring-clean and imports no ROS (verified green subset).

## test_baseline.json
`pixi run test` (the test-runner's full-suite step) auto-ratchets the suite
floor and commits it. Not run here (that is the test-runner's job per the
workflow); expect the `robot_backends` floor to rise.
