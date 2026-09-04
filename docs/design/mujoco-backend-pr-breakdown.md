# Roadmap #4 — MuJoCo RobotBackend (Mock → Sim swap): PR breakdown

**Status:** not started. Recorded 2026-09-04 (D34). Canonical next step now that
the URDF/MJCF body (roadmap #5, PR1–PR8b) is complete and merged.

**Decided (D34):** the `MuJoCoBackend` drives the sim **in-process** via the
Python `mujoco` binding (conda `mujoco >=3.12,<4`, already in `pixi.toml`),
loading the derived MJCF through `robot_description.mjcf_model.load_mjcf_model()`.
It keeps the seam's "no ROS import at runtime" invariant (D30). The PR8b ROS 2
control stack (`dfki-ric/mujoco_ros2_control`, D33) is a **separate layer** — the
Real-hardware / classical-skills track (MoveIt/Nav2, D22) — and is NOT what the
brain's backend drives.

## What is already done (do not rebuild)

- **PR7 (#90)** — MJCF derivation: `load_mjcf_model()` compiles the URDF +
  `mjcf/overlay.xml` into a `mujoco.MjModel` with contact defaults, 18 actuators
  (3 velocity wheels + 15 position incl. both gripper mirrors), and the head
  camera site + framepos sensor at the REP-103 optical frame. Asserted
  `nq=nv=nu=18`, `nbody=19` (fusestatic), `neq=0`, one `mj_step` no-NaN.
- **PR8b (#94)** — sim bringup: model spawns headless under
  `dfki-ric/mujoco_ros2_control`, controllers load at `/controller_manager`,
  all joints commandable (13-value position + 3-wheel velocity). 838 tests green.
- `pixi.toml` pins `mujoco >=3.12` (in-process binding; distinct from the
  plugin's vendored 3.9.0).

## What is missing (the "swap glue")

1. No `MuJoCoBackend` / `SimBackend` class — `robot_backends` ships Mock only;
   `mujoco` is imported only inside `robot_description`.
2. No IK — `robot_model.py` reads `reach_radius` as a safety *constraint*, not a
   solver; the Mock fakes reach with arithmetic.
3. No grasp modeling — no attach/detach of an object when the jaws close.
4. Scene objects are not in the sim — the MJCF is robot-only; `book_1`/`cup_1`/
   `counter_1`/`table`/`sofa` live in `robot_world` JSON.
5. No observation-from-`mjData` mapping.
6. No `--backend mock|mujoco` flag — `robot_mcp` hardcodes `MockBackend`.

## Ground truth this must preserve

- **The seam** (`RobotBackend`: `reset()`, `get_observation()`, `execute()`),
  D9 — the brain never learns which backend.
- **The wire schema** (`Observation` / `SkillResult`, `SCHEMA_VERSION`), D18 —
  identical above the seam, golden fixtures unchanged.
- **The failure vocabulary** (`out_of_reach`, `rejected`, `below_floor`, …),
  D17 — same codes for the same situations.
- **The robot model** — geometry from the URDF (D23 / PR6), not re-derived.
- **The safety layer** stays above the seam, unchanged (invariant 3).
- **"No ROS import at runtime"** in `robot_backends` / `robot_mcp` (D30).

## The PRs

### PR 1 — `MuJoCoBackend` skeleton + scene loading *(foundation, highest risk)*
- New `src/robot_backends/robot_backends/mujoco_backend.py`:
  `MuJoCoBackend(RobotBackend)` — loads `load_mjcf_model()`, owns an `MjData`.
- **Scene insertion:** for each `robot_world` object, spawn a body/geom at its
  seed pose; named locations become reference poses. This is the one genuinely
  new piece — bridging the JSON world → MJCF scene.
- `get_observation()` reads robot + object poses from `mjData` into the existing
  `Observation` schema; `reset()` re-seeds from the world.
- `robot_mcp`: add `--backend mock|mujoco` behind `backend_from_options`
  (Mock stays the default).
- **Test:** load + step; observation matches seed-world positions; `--backend
  mujoco` serves the same wire schema as Mock; `test_no_ros_runtime` still green.
- Unblocks: everything after.

### PR 2 — Column + base (no IK needed)
- `extend_column`: set the prismatic joint target, step, read back the clamped
  height.
- `navigate_to`: teleport the base to the named location first (honest v1, no
  wheel dynamics), wheel velocity control as a follow-up.
- **Test:** column clamp matches Mock (2.0 → 1.2); navigate moves `robot.pose`.

### PR 3 — `move_gripper` + IK
- Minimal IK for the 5-DOF arm (analytic if SO-101 has a closed form, else a
  small Jacobian/CCD solver).
- `move_gripper` + `open/close_gripper` map to joint targets.
- **Test:** in-reach Cartesian pose lands the gripper (± tol); `out_of_reach`
  fires outside reach.

### PR 4 — grasp/place + safety vs. real dynamics
- Grasp modeling: close jaws → attach object to gripper (equality constraint or
  contact-detect), set `grasped`. `place` detaches at the pose.
- Reachability/self-collision into the backend failure path (reuse
  `out_of_reach`), safety layer clamping on top.
- **Test:** the full "clear the table" loop runs in MuJoCo — book + cup move
  table → counter; `extend_column 2.0` clamps; underground pose rejects.

### PR 5 — parity + the brain drives sim
- Parity tests: same skill stream → same schema across Mock and MuJoCo (D18
  golden fixtures).
- Run the brain smoke against `--backend mujoco` (reuse `test_clear_the_table.py`
  driver, then live).
- **Proves:** the swap is invisible above the seam.

## Merge order & dependencies

```
PR1 ─► PR2 ─► PR3 ─► PR4 ─► PR5
```
Strictly sequential (all touch `robot_backends`). One dispatch slot.

## Open risks

- **JSON-world → MJCF-scene bridge (PR 1)** — the highest-info step; object
  geometry/bodies must come from somewhere (primitive geoms keyed off the
  world's object labels), and there is no prior art in the repo yet.
- **IK quality (PR 3)** — a cheap solver may be janky; acceptable for the swap,
  revisit only if it blocks PR 4.
- **Grasp fidelity (PR 4)** — attach-on-close is the pragmatic v1; contact-based
  grasping is the harder, more physical version.
