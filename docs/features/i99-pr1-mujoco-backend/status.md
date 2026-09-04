# Feature status — PR 1: MuJoCoBackend skeleton + scene loading (issue #99)

Worktree: `~/worktrees/i99-pr1-mujoco-backend` (branch `feat/i99-pr1-mujoco-backend`),
created from `origin/main` 9c4ef6c. Manager: worktree manager (OpenClaw subagent).

## Binding rulings (from main — D34/D30, not re-litigated)

1. **D34 — in-process MuJoCo.** `MuJoCoBackend` drives MuJoCo in-process via the
   Python `mujoco` binding (`>=3.12,<4`, already in `pixi.toml`), loading the
   derived MJCF through `robot_description.mjcf_model.load_mjcf_model()`, owning an
   `mujoco.MjData`. NOT the PR8b / dfki-ric ROS 2 control stack.
2. **D30 — no ROS import at runtime** in `robot_backends` / `robot_mcp`. The new
   backend imports `mujoco` but never `rclpy` / `ament_index_python`. Keep
   `test_no_ros_runtime` green.
3. **Primitive geoms v1.** Scene objects get primitive geoms keyed off each world
   object's label/pose (book/cup/bowl/mug/plate/sofa/counter). No meshes for scene
   objects yet. (Recording the issue's ruling, which is binding.)
4. The seam (`RobotBackend.reset/get_observation/execute`), the wire schema
   (`Observation`/`SkillResult`, `SCHEMA_VERSION`), and the failure vocabulary are
   unchanged. Observation must be same shape as Mock.

## Scope for this PR (from issue #99 body — authoritative)

1. `src/robot_backends/robot_backends/mujoco_backend.py`:
   `MuJoCoBackend(RobotBackend)` — loads `load_mjcf_model()`, owns `mj.MjData`.
2. Scene insertion: each `robot_world` seed object → body/geom at its seed pose;
   named locations → reference poses.
3. `get_observation()`: read robot + object poses from `mjData` into existing
   `Observation` schema (same shape as Mock); `reset()` re-seeds.
4. `robot_mcp`: add `--backend mock|mujoco` behind `backend_from_options` (Mock stays
   default).

Out of scope (later PRs 2–5): execute() skills, IK, grasp/place, parity/brain swap.

## Acceptance

- `pixi run test` green on laptop (full suite, ratchet committed).
- New test: load + step; `get_observation` object/location poses match seed world
  within tolerance.
- New test: `--backend mujoco` serves same `Observation`/`SkillResult` wire schema as
  Mock.
- `test_no_ros_runtime` still green.
- `test_baseline.json` ratcheted.
- NOTE: full `pixi run test` rewrites `scripts/test_baseline.json` UP on green; commit
  the bump as part of the PR.

## Model facts (probed on mujoco 3.12.0, olivia — 2026-09-04)

- Derived MJCF has a **fixed (fused-static) base**: no planar/free robot base joint.
  Kinematic root is the world body; 3 omniwheel hinges (velocity), `column_lift`
  prismatic (z, range ~[0,1.2], position actuator), left/right arm joints + grippers
  and gripper mirrors (position), all hanging off `column_top`. `nq/nv=18`, `nu=18`.
- `column_top` body origin is at z=0.195; `column_lift` changes it from there.
- Robot base (fused) sits at world origin = map origin = seed `start_location`
  `charger` (0,0,0). So frame overlay is identity (see R-2).
- Wheel/arm/gripper actuators: wheels `<velocity>`, column/arms/gripper `<position
  kp>` (per overlay.xml). At reset, position actuators default ctrl 0.

## Manager rulings (final — recorded before dispatch)

- **R-1 → R-1-resolved (FIXED): scene objects = static (welded) bodies.** A compiled
  `MjModel` cannot gain bodies post-compile, so world objects are spliced into the
  MJCF/assembly BEFORE `load_mjcf_model()` compiles (the scene-aware build must reuse
  the PR7 merge machinery in `robot_description.mjcf_model` that produces the merged
  XML/`MjSpec`, extending it with body/geom blocks for the JSON world objects). Each
  scene object becomes a **static body with NO joint** (a `<body pos=...>` under the
  world, holding its primitive geom(s)) at its seed frame → poses are exact and
  invariant across any number of `mj_step` calls (no gravity collapse, deterministic,
  matches seed acceptance trivially). Graspable/furniture distinction still recorded
  via `graspable`; PR5 (grasp/place) replaces these with free joints resting on a
  modelled floor/surface — out of scope here. Document this explicitly in
  implementation.md so PR5 knows the seam.
- **R-2 (FIXED) — identity frame.** MuJoCo world frame == apartment map frame == seed
  `start_location` `charger` at origin (0,0,0). MJCF +z == map +z. The fused robot
  base at origin IS the robot standing at the charger. Object seed `Pose.position`
  (x,y,z) maps directly onto the MJCF body `pos` (x,y,z). Locations = the four map
  frames (all `z≈0`). No extra transform. Observation `Pose` reports these MJCF/MAP
  coordinates directly.
- **R-3 (FIXED) — robot column height on reset.** Seed `start_column_height: 0.3`.
  On `reset()` set `d.qpos[column_lift_slot] = 0.3` AND set the matching position
  actuator `d.ctrl` so the servo **holds** 0.3 across `mj_step` (position actuators
  drive toward their ctrl; a ctrl of 0 would drag the column back to 0). Arms/wheels
  at home: qpos 0 / velocity ctrl 0. Report `column_height` read from the column qpos
  slot. If the probe shows the column cannot be servoed to a stable 0.3 without a
  controller fight, record it and use the closest stable posture, but EXPECT 0.3 to
  hold (position actuator + matching ctrl).
- **R-4 (carried) — D30 gate.** New `robot_backends.mujoco_backend` and any new
  `robot_mcp` code must keep clean static import free of ROS packages (`mujoco` is
  fine; `rclpy`/`ament_index_python`/`xacro` are NOT). Verify `test_no_ros_runtime`.
- **R-5 (FIXED) — object/location pose report.** Read object positions from mjData
  body frames (xpos/xquat → Pose), identity-mapped to map coords so qpos/xpos == JSON
  pose. Objects sorted by object_id; known_locations sorted. Empty grippers
  (open, not grasped, no held object). Robot `RobotState.location` = `"charger"`
  (the start location; base is fixed so it never leaves).
- **R-6 (carried) — robot proprioception.** Report base pose (identity, at origin),
  column_height (R-3), grippers present for both `SIDE_ORDER` sides, all empty/open.
  Robot `pose` = start-location frame identity.
- **R-7 — `robot_mcp --backend`.** Add `--backend {mock,mujoco}` (argparse) with env
  fallback env var following the existing `--world-state`/`--world-seed` convention;
  thread through `backend_from_options` and `main`. `mock` (and no flag at all) =
  today's Mock behaviour (build_server-injected default). `mujoco` = construct a
  `MuJoCoBackend` over the shipped seed world. Ruling: a `--backend mujoco` server may
  run with no `--world-state` (in-memory seed, matching the "serve same wire schema as
  Mock" test); it should also accept a caller-supplied seed via the existing
  `--world-seed` when one matters. Implementer keeps wiring minimal & consistent with
  the existing D23 helpers.

## Open questions to be resolved by implementer (record resolution back here + implementation.md)

- Q-1: exact scene-aware assembly entry point (which function in
  `robot_description.mjcf_model` to extend/reuse so robot body derivation is not
  duplicated and `load_mjcf_model()`'s public contract is unchanged). R-1 says splice
  before compile; details of how to reuse the merge helpers are implementer's probe.
- Q-2: column/arm home + actuator ctrl values that keep posture stable over
  `mj_step` (R-3) — verify with a probe step.
- Q-3: gripper-pose report source (body frame of each gripper link) + exact home joint
  config, and what to report for `GripperState` at reset (OPEN vs Mock's reset state —
  mirror MockBackend's reset gripper state exactly).
- Q-4 (R-1 carryover): whether scene bodies get a shared default/contact block or a
  separate inert default; keep contact OFF or non-interfering for PR1 scene objects so
  a step can't perturb them, consistent with them being static.

## Role log

- 2026-09-04: worktree created from origin/main 9c4ef6c; pixi env + install-openclaw +
  build done. Rulings R-1..R-6 recorded. First implementer dispatch
  (session pr1-implementer #1) spent its entire budget on code reading + framework
  understanding and hit its context/output ceiling BEFORE writing any code (no commits,
  no files). Its useful validated discovery (model facts above) is preserved; ruling,
  not re-derivation, is the fix. Manager finalized the mechanism (R-1/FIXED..R-7) and
  is re-dispatching a sharpen-scoped implementer with these decisions pre-baked.

## Open-question resolutions (implementer #2 — session pr1-implementer2, 2026-09-04)

- **Q-1 — scene-aware assembly seam.** Entry point is a new public function in
  `robot_description/mjcf_model.py`: `load_mjcf_model_with_scene(world_bodies_xml)`,
  which reuses the single PR7 assembly `_build_merged_mjcf(pkg, world_bodies='')`.
  The robot derivation is never duplicated: `_build_merged_mjcf` produces the
  merged MJCF (URDF import + overlay splice) as before, then the new
  `_insert_world_bodies()` splices the caller's static `<body>` blocks in as the
  LAST sibling inside the derived MJCF's single `<worldbody>...</worldbody>`
  (before its close). `load_mjcf_model()` now delegates to
  `load_mjcf_model_with_scene('')` and is byte-identical (public contract and
  behavior unchanged); `write_mjcf_model()` still calls
  `_build_merged_mjcf(_package_dir())` (no scene). The geom/label rendering lives
  in `robot_backends/mujoco_backend.py` (`_world_bodies_xml`), so
  `robot_description` stays a purely generic world-body splice and never learns
  mug/counter specifics. Verified by probe: compile + 200 `mj_step`s keep object
  xpos bit-identical to seed.
- **Q-2 — column/arm home that holds.** Verified against mujoco 3.12.0: wheels are
  velocity actuators (ctrl 0 → no motion), `column_lift` is a position actuator.
  On reset, set `qpos[column]==start_column_height` (0.3) AND `ctrl[column]==0.3`
  → the kp=100 servo holds ~0.3 (0.29989 after 200 steps). Arms are position
  actuators with ctrl 0 = home target; under gravity their joints drift a little
  over many steps (shoulder_lift ~-0.028 rad over 200 steps) because kp=100 is a
  weak spring vs. gravity torque. That is fine for PR1 (scene is static; no arm
  skills; column [the R-3 focus] holds). PR2 owns real arm control — do not tune
  gains here.
- **Q-3 — gripper-pose source & reset report.** Report `GripperState.OPEN`,
  `held_object_id=None`, `grasped=False` on reset, mirroring Mock's reset. The
  reported `pose` is the world-frame midpoint of the two open jaws
  (`{side}_gripper_upper_jaw_link` / `{side}_gripper_lower_jaw_link`) read from
  `mjData.xpos`, with the upper jaw's `xquat` (converted wxyz→xyzw) as
  orientation. There is no single palm body (the URDF gripper base is folded into
  the wrist by fusestatic), so the symmetric jaw midpoint is the honest
  grasp-centre stand-in; PR2 arm kinematics will report true commanded poses. The
  pose is a *sim* value, so it legitimately differs from the Mock's shoulder+offset
  model (parity is about shape/fields, not pose equality).
- **Q-4 — scene contact.** Each scene geom carries `contype="0" conaffinity="0"`,
  making it collision-inert. Together with the static weld this means a step can
  never perturb an object via contact or dynamics; objects stay at seed across any
  number of `mj_step`s. Objects are metres from the robot, so no robot-contact
  interference either. No separate default class needed.

## Role log (implementer #2)

- 2026-09-04: Implementation committed (see `git log origin/main..HEAD`):
  (1) `mjcf_model.py` scene seam + `load_mjcf_model_with_scene`;
  (2) `robot_backends/mujoco_backend.py` `MuJoCoBackend(RobotBackend)`;
  (3) `robot_mcp` `--backend mock|mujoco` (+`$ROBOT_BACKEND`) wiring;
  (4) tests `robot_backends/test/test_mujoco_backend.py` + `robot_mcp/test/
  test_mcp_mujoco_parity.py`; (5) ament-lint fixes. Scoped authoritative run:
  `pixi run -- python scripts/check_test_integrity.py --packages-select
  robot_backends robot_mcp` → 175 tests, 0 failures, AUDIT PASSED;
  `test_no_ros_runtime` green in both packages. Baseline ratcheted 77→84 /
  82→85. Full `pixi run test` (whole workspace, test-runner role) still pending.
