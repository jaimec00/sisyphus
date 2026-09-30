# Roadmap — MoveIt pick-and-place (PROJECT.md step 5, second half): PR breakdown

**Status:** not started. Recorded 2026-09-30 (D39). This is the manipulation
half of PROJECT.md step 5 (classical skills first, D22): stand up MoveIt 2
motion planning + execution on the classical/real track so the brain can command
a real pick-and-place of rigid objects — starting with the committed first
chore: **grab a remote and hand it to the user**.

**Decided (D39):** MoveIt runs on the **classical/real track** (the PR8b
`dfki-ric/mujoco_ros2_control` stack, D33/D36) — the same split Nav2 took — NOT
on the ROS-free brain backend (D34). The arm is authored as a swappable xacro
macro (D26), so the MoveIt config targets SO-101 now and PiPER swaps in later.
**SO-101 is committed as the first hardware purchase** (PiPER stays a later
real-hardware upgrade). The first end-to-end chore is lightweight: **grab a
remote and hand it to the user** — within SO-101's ~0.25–0.5 kg / ~0.4 m
envelope, and a *classical* (not learned) pick-and-place, which is exactly what
MoveIt does.

## Ground truth this must preserve
- The skill-API seam (D9/D21): `move_gripper`/`grasp`/`place` keep their
  signatures and `SkillResult` shape; the brain never sees raw joint trajectories.
- `robot_world` stays the single source of truth for object poses (D23); the
  planning scene is fed from it (via the D35 world-service seam), not a second
  home for scene data.
- The safety layer (D17) still clamps/rejects illegal commands below the tool
  boundary — a MoveIt plan that exceeds arm limits or self-collides is rejected.
- D30 boundary held: `robot_backends`/`robot_mcp` stay ROS-free at runtime.
  MoveIt lives in ROS 2 packages, not in the brain's backend.
- The arm is a swappable xacro macro (D26): the MoveIt config (SRDF) must be
  authored per-side and parameterized so PiPER later drops in as a macro swap,
  not a re-model.

## What is already done (do not rebuild)
- The arm is modeled (URDF + MJCF, PR4/PR7) and the ROS-sim path brings it up
  **position-commandable** under dfki-ric (PR8b/D33): 13-value position +
  3-wheel velocity controllers.
- The brain backend already has DLS-IK + `move_gripper`/`grasp`/`place` (D34,
  PR3/PR4) — that's the *ROS-free* in-process path. MoveIt is the *classical*
  ROS path; they share the derived MJCF but not the driver (D34).
- `robot_world` exposes object + location poses through the D35 query service.

## The PRs

### PR1 — MoveIt deps + move_group bringup + planning scene
- **Probe the real API first** (the open design question): the RoboStack coverage
  preflight (2026-09-30) found MoveIt's *planning* stack is **NOT on the
  RoboStack channel** — only `moveit-core`, `moveit-common`,
  `moveit-configs-utils`, `moveit-hybrid-planning` (and the `moveit` umbrella)
  are present; `moveit-ros-planning`, `moveit-ros-planning-interface`,
  `moveit-planners-ompl`, `moveit-py`, `moveit-kinematics`, and `trac-ik` are
  all absent. So MoveIt planning must be **source-built in-tree** (the D37 MPPI
  pattern: vendored + scalar/patched build), OR a fuller moveit set found on
  another channel. PR1 pins which, and records it.
- Add the MoveIt dependency (source-build or channel) to `pixi.toml` and confirm
  the env builds.
- Author the MoveIt **SRDF** for the arm (planning group over the 5 arm DOF +
  gripper, end-effector link = gripper frame, per-side prefix).
- Feed the planning scene from `robot_world` (D35): object poses + collision
  geometry into MoveIt's planning scene.
- **Test:** `move_group` starts headless; the planning scene contains the world's
  objects; the arm planning group loads with the SRDF.

### PR2 — arm planning + execution (move to a pose)
- Wire MoveIt execution to the dfki-ric ROS control stack: a FollowJointTrajectory
  action → joint trajectory controller for the arm DOF (probe whether dfki-ric
  exposes one, or whether the position controllers are driven directly).
- Plan + execute a Cartesian goal (pre-grasp pose above a known object).
- **Test:** a `move_gripper`-style Cartesian goal actually moves the arm joints in
  sim (not a teleport); `out_of_reach` fires outside the envelope; a
  self-colliding plan is rejected.

### PR3 — pick-and-place skill ("grab the remote")
- Compose grasp + lift + carry + place on the classical track: from a known
  object pose, plan pre-grasp → close gripper → lift → carry to a target → place.
- Map onto the skill seam + the brain: the brain's `grasp`/`place` (or a new
  `pick_and_place`) drives the MoveIt path.
- **Test:** the committed chore — a `remote` object at a known pose is grasped
  and moved to a hand-off location; the brain smoke runs against it.

## Merge order
```
PR1 ─► PR2 ─► PR3
```
Sequential (same track). One dispatch slot.

## Open risks
- **MoveIt source-build (PR1)** — the big one: the planning stack is missing
  from RoboStack, so this is another vendored-build like D37's MPPI. The scope
  and size of the source-build is the unknown.
- **Execution path (PR2)** — whether dfki-ric's ROS-sim exposes a joint
  trajectory controller, or whether MoveIt drives the position controllers
  directly, is unproven.
- **Grasp fidelity (PR3)** — grasp/place on the classical track vs the brain
  backend's attach-on-close (D34 PR4) is the harder physical version (real
  contact, not equality constraints).
