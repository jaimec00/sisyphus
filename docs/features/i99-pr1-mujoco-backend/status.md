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
   unchanged. `Observations must be same shape as Mock.`

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

## Manager rulings (recorded before dispatch)

- **R-1 (open, discovery) — scene insertion mechanism.** A compiled
  `mujoco.MjModel` cannot have bodies appended post-compile. The backend must place
  world objects into the sim BEFORE compile. Discovery must determine the least
  invasive path to give MuJoCo a robot+scene MjModel that still satisfies
  "load ~robot_description~'s MJCF through load_mjcf_model()",
  e.g. leave `load_mjcf_model()` unchanged and add a scene-aware compile path that
  reuses the existing merge helpers + splices body/geom blocks for world objects,
  OR give the backend an XML-level entry point that mirrors `load_mjcf_model()`.
  Resolver records the concrete mechanism in status.md before/in implementation.md.
  Constraint: don't modify `load_mjcf_model()`'s public contract; keep robot-body
  derivation shared/DNY rebuild.
- **R-2 — frame/coordinate mapping.** World JSON `Pose`s are in the apartment (map)
  frame; MJCF is robot-centric with its own frame. The observation must report world
  (map) coordinates so it matches the seed world within tolerance and matches Mock's
  `Observation` shape. If MJCF world origin == seed `start_location` (charger at
  (0,0,0)) with the same +z-up axes and floor, coordinates overlay directly; verify by
  probe, else record the transform. Expect: use MuJoCo free joints + `mjData.qpos` for
  objects/robot. Object `z` coordinates are above their support (e.g. counter at
  z≈0.45, mug on it at z≈0.9); if floor/furniture is not actually modelled with
  height, settle on the observation-matches-seed contract per R-5 and record.
- **R-3 — `robot_mcp --backend` seam.** Add `--backend {mock,mujoco}` flag (env
  fallback mirroring existing `--world-state`/`--world-seed` style) parsed in
  `parse_args`; thread through `backend_from_options` and `main`. Default remains the
  current Mock behaviour. `MuJoCoBackend` needs no live state file for the in-memory
  seed case (matches default_world), matching the "serve same wire schema as Mock"
  test. Follow whichever existing convention (flag/env env var names) the code already
  uses for other flags.
- **R-4 — D30 gate.** The new `robot_backends` module and any new `robot_mcp` code must
  keep a clean static import of `robot_backends`/`robot_mcp` free of ROS packages. The
  `mujoco` import must not transitively pull `rclpy`. Implementer verifies
  `test_no_ros_runtime` and the static scan.
- **R-5 — object height/floor contract.** The seed objects sit at absolute z in world
  space (some above furniture). The acceptance test asserts observation object/location
  poses match the seed within tolerance. Record how the mjData-derived pose maps to the
  seed coordinate/pose (identity IF the MJCF world frame is the map frame with the
  floor at z=0 and free-join objects placed at their JSON pose). Where the seed has no
  explicit floor/furniture height offset captured in the MJCF, prefer an identity
  mapping so mjData qpos == JSON pose and document any deviation in implementation.md.
- **R-6 — robot proprioception.** `RobotState` needs pose/column_height/grippers.
  PR1 does not command skills; read robot base pose from its joint/body in mjData,
  column height from the prismatic joint, grippers/held_object_id empty (no skills).
  Keep the `Observation` field population identical in kind to Mock (empty grippers at
  reset, both grippers present per Side order).

## Open questions to be resolved by worker (record resolution back here + implementation.md)

- Q-1: exact MuJoCo scene-build entry point that reuses robot-body derivation (R-1).
- Q-2: axis/frame verification between MJCF and world coords (R-2).
- Q-3: how `--backend mujoco` behaves when a `--world-state` file is or is not given
  (does MuJoCo need a store? It must be able to run in-memory like the Mock default).

## Role log

(Worktree manager records dispatch/red-team/test events and findings here as the loop
runs. Red-team findings labelled VERIFIED/UNVERIFIED.)
