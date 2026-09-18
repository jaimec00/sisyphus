# PR3 implementation — `move_gripper` + IK on `MuJoCoBackend` (issue #105)

Status: implementation complete, scoped-authoritative run green. PR 3 of 5 on
roadmap #4 (parent #98). Manager rulings R1–R8 in `status.md` are binding and
were implemented exactly; deviations are listed at the end.

## What this PR delivers

1. **`robot_backends/ik.py`** — a pure, ROS-free, numpy-only damped-least-squares
   IK solver for one 5-DOF arm (`solve_ik`), returning the solved joint vector or
   `None` (the honest reachability signal).
2. **`mujoco_backend.py`** — name-based arm/gripper joint tables, one
   `_gripper_frame` helper, `_shoulder` off the shared `RobotModel`, and three new
   skill handlers: `_move_gripper` (IK), `_open_gripper`, `_close_gripper`. Reset
   now homes the grippers **open** instead of at joint zero.
3. **Tests** — six new acceptance tests in `test/test_mujoco_backend.py`.

## 1. The solver (`ik.py`, R1)

Implemented exactly as the verified recipe in `status.md` R1:

- **Rotation matrices, never hand-rolled quaternion products.** The task frame
  comes from the sim's `xquat`; the target arrives as a skill-API `Quaternion`
  (x, y, z, w). Both are converted to 3×3 in the backend (`_wxyz_to_matrix`,
  `_quat_to_matrix`) and passed into the solver, so the solver itself never sees
  a quaternion.
- **Task error** `e = [p_t − p_c ; log(R_t R_cᵀ)]` with the rotation-vector
  log-map (`θ = acos(clip((tr R − 1)/2, ±1))`, small-angle branch at `θ < 1e-8`,
  `vee` of the skew part).
- **Forward-difference Jacobian**, `eps = 1e-6`, over the five arm joints only.
- **DLS step** `dq = −Jᵀ (J Jᵀ + λ² I₆)⁻¹ e` — leading minus, `λ` start 1e-3,
  halved on acceptance (floor 1e-7), doubled on rejection.
- **Clamp to joint ranges**, accept iff `‖e_new‖ < ‖e‖`.
- **Multi-start**: `q = 0`, then 8 uniform-random configs in range from a seeded
  `default_rng(0)`; first converged start wins, else `None`. 400 iters per start.
- **Convergence**: `‖e[:3]‖ ≤ 1e-4` **and** `‖e[3:]‖ ≤ 1e-4`.
- **Side-effect-free**: the caller snapshots the five arm qpos and hands the
  solver a `restore()` callback; the solver's `finally` runs it on *every* exit
  path, so a failed solve leaves `mjData` bit-identical to entry (verified).

The solver takes `fk`, `joint_adrs`, `lower`, `upper`, `restore` as keyword
arguments rather than a `mujoco.MjModel`/`MjData` pair: that keeps it free of any
`mujoco` import (the pure/numpy-only requirement) and lets the backend own all
index resolution.

## 2. Backend changes (R2–R7)

- **Name-based joint tables (R5).** `_arm_joints[side]` maps each of the five
  arm joints to `(qpos_adr, ctrl_id)`; `_gripper_joints[side]` pairs `driven` and
  `mirror` the same way; `_arm_ranges` / `_gripper_ranges` hold the travel
  limits. Nothing assumes a slot index.
- **`self._robot = RobotModel()`** (from `mock_world`) — the shared
  body-constants holder, reused so the refusal reason matches the Mock's.
- **`_shoulder(side)`** = `self._robot.shoulder(self._base_pose(),
  qpos[column], side)`, ignoring base orientation exactly as the Mock does.
- **One frame helper (R2).** `_task_frame(side)` returns the jaw midpoint +
  upper-jaw rotation matrix from `mjData`; `_gripper_frame(side)` wraps it into
  `(Point, Quaternion)`; `_gripper_pose` and `_gripper_observation` both read the
  latter, so the IK target and the reported pose cannot disagree.
- **Reset homes open (R6).** Arm joints still home to zero; each gripper's driven
  jaw homes to its lower limit (−1.5) and the mirror to +1.5, each with its
  position actuator commanded to the same value. `_gripper_state` now *derives*
  OPEN/CLOSED from the driven qpos vs the midpoint of its travel (−0.75), so the
  state is observable instead of hardcoded.
- **`_move_gripper` (R3).** Converts the target pose to (position, rotation
  matrix), solves. On `None` → `_SkillRefused(OUT_OF_REACH, reason)` with the
  Mock's exact reason shape (`{d:.2f} m`, `0.85 m`, `repr(self._location)`). On
  success → write the five arm qpos **and** the five matching position actuators
  (servo hold), `mj_forward`, return `None`. No `mj_step` (R4).
- **`_open_gripper` / `_close_gripper` (R5).** Write driven **and** mirror qpos +
  ctrls (open: −1.5 / +1.5; close: 0 / 0), `mj_forward`. Return the Mock's
  idempotent informational reason when already in that state, else `None`. Never
  refuse.
- **`execute()` (R7).** Dispatches `MoveGripper` / `OpenGripper` / `CloseGripper`
  alongside `NavigateTo` / `ExtendColumn`; `Grasp` / `Place` stay
  `UNSUPPORTED_SKILL`. Module docstring updated.

## 3. Tests (R8)

- `test_move_gripper_in_reach_lands_the_gripper` — reset, write a moderate arm
  config `[0.3, 0.4, 0.9, 0.0, 0.1]` via the hook, read the gripper pose, reset,
  `MoveGripper` → `OK`; position within 5e-3 m, orientation within 2e-2 rad; the
  five arm ctrls equal the solved qpos (servo hold).
- `test_move_gripper_to_current_pose_is_a_noop` — command the post-reset pose →
  `OK`, pose unmoved (the fixed point).
- `test_move_gripper_out_of_reach_is_refused` — a pose 2.0 m out → `FAILED` /
  `OUT_OF_REACH`; reason carries the distance, `0.85 m`, and `'charger'`; the
  observation is byte-identical to before (refused up front).
- `test_close_then_open_gripper_flips_the_state` — close → `CLOSED` (driven qpos
  0), open → `OPEN` (driven qpos −1.5); the jaws move (reported orientation
  changes); both idempotent second calls succeed with the Mock's reason strings.
- `test_reset_homes_grippers_open` — after a close, reset leaves driven qpos at
  −1.5 and state `OPEN`.
- Grasp → `UNSUPPORTED_SKILL` and `test_no_ros_runtime` stay green.

**Verification command** (olivia):

```
cd ~/worktrees/i105-pr3-move-gripper-ik/src/robot_backends
source ../../install/setup.bash
pixi run python -m pytest test/test_mujoco_backend.py test/test_no_ros_runtime.py \
    test/test_flake8.py test/test_pep257.py test/test_copyright.py -q
# 33 passed
```

`pixi run build` must be re-run after a source change, then `source
install/setup.bash` re-sourced.

## 4. Solver robustness (measured, honest numbers)

- The R8 primary target (left arm, config above) converges to pos err ~1e-5 m /
  rot err ~1e-7 rad.
- Stress probe, 10 random in-range configs per side (both arms, seed 42): **18/20
  converge within R8 tolerance** through the full `execute()` path.
- The 2 misses are **not** unreachable poses — the FK error at the source config
  is exactly zero. They are a **search-budget** limitation: the 9 starts R1/R8
  specify (q = 0 + 8 seeded random) miss the basin, while the same seeded RNG
  drawing 32 starts finds them on the first seed. So the solver reports
  `out_of_reach` for a pose that is in fact reachable — a false refusal, not a
  wrong solution.
- Recorded as a **NOTE** (see `status.md` R1 start-count is binding; raising the
  start count or seeding from the previous posture is a natural PR4 follow-up,
  when real grasp loops may want repeatable nearby solutions anyway).

## 5. Deviations from the rulings

None. Every R1–R8 mechanism is implemented as specified. Two points worth
flagging as *reported facts*, not deviations:

1. `solve_ik`'s signature takes `fk` / `joint_adrs` / `lower` / `upper` /
   `restore` keywords instead of `(model, data, ...)`. The brief explicitly
   allows this ("You may design a slightly different signature, but keep it
   pure, ROS-free, numpy-only").
2. Two test assertions were written around the sim's actual geometry rather than
   an assumed one: the out-of-reach reason reads `2.01 m` (the target sits at
   column + 0.5, `sqrt(2² + 1.5²)` from the shoulder), and the jaw-midpoint pose
   does not translate on open/close because that midpoint lies on the wrist-roll
   axis — so the "jaws moved" assertion checks the reported *orientation*
   instead of position. Both are properties of the model, not weak assertions.

## Recorded NOTE for the manager

- **Mirror joint range (R5)**: `left/right_gripper_mirror` declares `[-1.5, 0]`,
  copy-pasted from the driven joint, yet the symmetric open posture commands it
  to +1.5 — outside its own `<limit>`. `mj_forward` does not clamp qpos, so this
  is kinematically fine today, but it is a latent URDF bug (the range should be
  `[0, 1.5]`). Not fixed in PR3 per R5.
