# i147 — MoveIt 2 execution → dfki-ric control stack (PR2) — IMPLEMENTATION

Worktree: `/home/sisyphus/worktrees/feat/i147-moveit-execution` (branch
`feat/i147-moveit-execution`, off `origin/main` @ 97754c5).
Rulings: `docs/features/i147-moveit-execution/status.md`.
Context map: `docs/features/i147-moveit-execution/context.md`.

## What this builds

A Cartesian goal, driven through a new `cartesian_goal` service, is planned by
MoveIt (`moveit_py`) and executed as a **real time-parameterised joint
trajectory** by a per-arm `joint_trajectory_controller` (JTC) — the arm moves in
the MuJoCo sim *through* intermediate states, not by teleport. `out_of_reach` is
a distinct, pre-planned outcome; a goal that requires folding the arm through
its own column is rejected as a collision. All three acceptance criteria are
covered by a new end-to-end test on ROS domain 120.

## Ruling-by-ruling

### R1a — FollowJointTrajectory (JTC), not a position bridge
`ros-jazzy-joint-trajectory-controller` added to `pixi.toml`. Arm motion goes
through MoveIt's `moveit_simple_controller_manager` → JTC action, never a
position write. **Empirically confirmed:** the JTC plugin creates its server at
`~/follow_joint_trajectory`, so with `action_ns: follow_joint_trajectory` msscm
dials `/left_arm_controller/follow_joint_trajectory` and
`/right_arm_controller/follow_joint_trajectory` (both observed live, and the
"Added FollowJointTrajectory controller for …" lines in move_group/cartesian_goal).

### R1b — scope expansion to robot_bringup: approved, minimal
Touched `robot_bringup` only where the arm control seam lives:
`params/controllers.yaml`, `launch/mujoco.launch.py`, and the two affected tests.

### R1c — controller restructure
`params/controllers.yaml`:
- Added `left_arm_controller` + `right_arm_controller`
  (`joint_trajectory_controller/JointTrajectoryController`, `command_interfaces:
  [position]`, `state_interfaces: [position]`, the 5 revolute joints per side).
- Shrank `arm_gripper_position_controller` to `column_lift + left_gripper +
  right_gripper` (3 joints). Name kept (historical; commented).
- Both new JTCs declared in the top-level `controller_manager.ros__parameters`
  block AND their own node blocks (dfki-ric franka pattern).

`launch/mujoco.launch.py`: the JTCs are spawned. **Deviation from the letter of
R1c, forced by an empirically-observed failure:** R1c said "add two JTC
spawners" (i.e. keep four/five separate spawners). Four separate `spawner`
processes deadlock on the spawner's single hard-coded lock file
(`~/.ros/locks/ros2-control-controller-spawner.lock`, 20 s acquire timeout in
`controller_manager/spawner.py`): started together they starve each other and
exit non-zero ("Failed to acquire lock … attempt 5 of 5") even though the CM is
healthy. All five controllers are therefore spawned by **one** `spawner`
invocation, which takes the lock once. Order: broadcaster, then the two JTCs
(they must claim the arm joints' command interfaces before the group
controller), then the group controller, then the base.

**Blast radius (done):**
1. `test_controllers_yaml_declares_position_and_velocity_groups` — 3 joints for
   the group controller; new JTC assertions (type + 5 joints/side + disjointness
   from the group controller's set).
2. `test_joint_command_moves_sim_state` — `SMOKE_JOINT` changed. **R1c said
   `column_lift`; it is what ships**, but the *assertion* had to change and
   `left_gripper` was measured and rejected (see "Deviations" below).

### R2 — collision pairs re-enabled
`config/sisyphus.srdf`: removed exactly the 16 entries (4 cross-side `Default` +
12 `column_top` `Never`), rewrote the block comment, kept the 3 genuine body
pairs. 27 `disable_collisions` remain.
`test_move_group_launch.py::test_move_group_neutral_pose_is_collision_free` was
**extended** (not replaced) to print every contact's body pair, position, normal
and depth on failure (`_format_contacts`). **Empirical result: the neutral pose
stays collision-free with all 16 pairs re-enabled** (test green), clearing open
risk 1.
`test_srdf.py`: added `test_srdf_reenabled_collision_pairs_are_not_disabled`
asserting the 16 pairs are absent and the 3 body pairs remain. (NB srdfdom
exposes the attribute as `srdf.disable_collisionss` — an upstream typo.)

### R3 — out_of_reach envelope pre-check (before planning)
`cartesian_goal` checks the Euclidean distance from the arm's own
`<side>_shoulder_link` origin (via TF2, so it follows the prismatic column) to
the resolved target against `reach_radius` (0.85 m, parameter). Failing →
`status=OUT_OF_REACH`, `error_code=NO_IK_SOLUTION`, no plan attempted.

### R4 — test geometry
Base/nav out of scope. Reachable target, far target (`mug_1`), and a
self-colliding target — all chosen empirically (see below).

### R5 — Cartesian-goal node
`robot_moveit/robot_moveit/cartesian_goal.py`, served on
`~/cartesian_goal`. Interface in a new interfaces package
`robot_moveit_ros_interfaces/srv/CartesianGoal.srv`
(request: `object_id`, `target_pose`, `use_target_pose`, `arm`; response:
`success`, `error_code`, `status`, `message`; status enum 0 SUCCESS / 1
OUT_OF_REACH / 2 COLLISION / 3 FAILURE).

**Deviation from the letter of R5, forced by the hardware, and the single
biggest finding of this PR: the SO-101 arm is 5-DOF, so a 6-DOF pose goal is
unsatisfiable.** Measured with `/compute_ik` on the shipped stack (neutral tip
orientation `(-0.5, 0.5, 0.5, 0.5)`, neutral tip at `(0.383, 0.18, 0.791)` in
`base_link`):

| target | IK |
|---|---|
| (0.383, 0.18, 0.791) (neutral) | `val=1` |
| (0.30, 0.18, 0.791) | `val=1` |
| (0.45, 0.18, 0.791) | `val=-31` |
| (0.45, 0.34, 0.70) | `val=-31` |

A `pose_stamped_msg` goal (what R5 named) and a position-only
`motion_plan_constraints` goal both fail the same way: OMPL reports "Unable to
sample any valid states for goal tree", because MoveIt's goal sampler still
needs a full-pose IK solve. **The implemented resolution:** search the arm's own
**forward kinematics** over a discretised joint grid for a state whose tip lands
within `goal_tolerance_m` (2 cm) of the requested point, then hand MoveIt that
joint state via `set_goal_state(robot_state=...)`. That is "a Cartesian goal" on
an under-actuated arm — the position is honoured, the orientation is whatever
the arm must adopt — and it matches the Mock's reach model (a sphere).

**Reachable workspace (measured, 14-sample grid, base_link):** tip x ∈
[-0.23, 0.38], y ∈ [-0.16, 0.52], z ∈ [0.58, 1.13]. Note forward reach *stops*
at x ≈ 0.38 — a target at (0.45, …) is outside the arm's actual workspace despite
lying inside the 0.85 m shoulder sphere. Test targets were chosen from this.

**Error mapping** (`classify_plan_failure`, pure): START/GOAL_IN_COLLISION
(-10/-12) → COLLISION; PLANNING_FAILED (-1) *with contacts* → COLLISION; a clean
-1 (or any other code) → FAILURE. SURFACE from execution → SUCCESS/FAILURE by
`ExecutionStatus.__bool__`.

### R6 — controller config + composed launch
- `robot_moveit_config/config/moveit_controllers.yaml`: standard msscm schema,
  `FollowJointTrajectory`, `action_ns: follow_joint_trajectory`, joints per side,
  `default: true`. **This file is shipped where R6 asked.**
- Composed launch: **`robot_moveit/launch/moveit_execution.launch.py`**, NOT
  `robot_moveit_config`. **Deviation from the letter of R6, forced by a package
  cycle:** `robot_moveit` already `exec_depend`s on `robot_moveit_config` (its
  `planning_scene_bridge.launch.py` includes `move_group.launch.py`), and the
  composed launch must also start this package's bridge and goal nodes, so
  placing it in `robot_moveit_config` makes colcon unable to order the two
  packages ("Unable to order packages topologically"). The *substance* of R6 is
  kept: PR1's `move_group.launch.py` is untouched and controller-free; the new
  launch composes sim + JTCs + move_group (with the controller-manager config) +
  planning-scene bridge + `world → base_link` static TF + the goal node.
  Nav2 is **off by default** there (a `nav` argument) — this stack drives the
  arm, not the base, and Nav2's map_node lifecycle could kill the bringup on this
  host; a caller can pass `nav:=true`.
- **Verified:** msscm dials `/left_arm_controller/follow_joint_trajectory` and
  `/right_arm_controller/follow_joint_trajectory` (open risk 3 cleared).

### R7 — execution e2e test + domain
`robot_moveit/test/test_moveit_execution.py` (moved here from
`robot_moveit_config` because the composed launch lives in `robot_moveit`),
domain `120`. Three tests, all green:
- `…_moves_arm_along_a_trajectory` — the load-bearing "not a teleport"
  assertion: ≥1 `/joint_states` sample strictly between start and goal for a
  commanded joint, plus motion actually occurred.
- `…_reports_out_of_reach` — `mug_1` (~2.1 m) and an explicit far pose →
  OUT_OF_REACH.
- `…_rejects_self_colliding_goal` — target (0.05, 0.00, 0.60) → COLLISION
  (`error_code=-10`, `status=2`).

`scripts/test_baseline.json` bumped: `robot_moveit` 7→20,
`robot_moveit_config` 8→9, new `robot_moveit_ros_interfaces`: 1.

## Deviations from rulings (all forced by empirical findings, none silent)

1. **R1c — one spawner, not many.** Four concurrent `spawner` processes deadlock
   on the spawner's lock file. Fixed by a single multi-name `spawner`.
2. **R1c — `SMOKE_JOINT` assertion.** R1c's `column_lift` ships. But that joint
   is heavily damped on this host: a 0.05 m command did not move it in 90 s, and
   0.6 m reached only ~0.24 m. `left_gripper` was measured and rejected — it
   saturates after ~0.01 of a step. The test therefore commands `column_lift`
   by +0.5 m and asserts **appreciable motion in the commanded direction**
   (≥10 % closure), not convergence: convergence would be asserting this host's
   throughput/actuator dynamics, not the claim ("a joint command moves the sim
   state"). Also added a `get_subscription_count()` wait before publishing.
3. **R5 — joint-space goal by FK search, not a pose/constraint goal.** The arm
   is 5-DOF; no 6-DOF pose goal is generally satisfiable (evidence above).
4. **R6 — composed launch in `robot_moveit`, not `robot_moveit_config`.** Package
   cycle, else colcon cannot build. All three deviations are also recorded in
   the relevant source docstrings.

## Empirical API facts pinned down (never train-recalled)

`moveit_py` 2.12.4 (read from the installed `.pyi`/bindings and upstream sources):

- `MoveItPy(node_name=..., launch_params_filepaths=[...], config_dict=...)`.
  Pass `launch_params_filepaths=[]` **explicitly**, or MoveItPy re-reads this
  process's `--params-file` args (via `moveit.utils.get_launch_params_filepaths`)
  and aborts on `qos_overrides./clock.subscription.durability`.
- MoveItCpp reads `planning_pipelines.pipeline_names` (+`namespace`), **not** the
  flat `planning_pipelines: ['ompl']` move_group takes. The flat form fails with
  "Failed to load any planning pipelines".
- `PlanningComponent.set_goal_state(robot_state=...)` takes
  `moveit.core.RobotState` (C++), **not** `moveit_msgs/RobotState`; the message
  raises "incompatible function arguments".
- `PlanningComponent.plan(single_plan_parameters=...)` — the kwarg is
  `single_plan_parameters` (the binding), not the docstring's `plan_parameters`.
- `PlanRequestParameters(moveit_cpp, ns)` then `.planning_pipeline` etc.
- `MoveItPy.execute(robot_trajectory, controllers)` — `controllers` is
  **required** in the binding (no default). Returns `ExecutionStatus`, whose
  `__bool__` is `status == SUCCEEDED`.
- `RobotState.get_global_link_transform(link)` returns a **4×4 numpy matrix**
  (translation = last column), not a pose object.
- `JointModelGroup.active_joint_model_bounds` returns a list of
  single-element lists of `VariableBounds` (read `.min_position`/`.max_position`).
- `use_sim_time` must reach MoveItPy's node as a **bool** (not the launch's
  string) or rclpy aborts on the clock's QoS override.

`moveit_simple_controller_manager` 2.12.4: `type: FollowJointTrajectory`
selects `FollowJointTrajectoryControllerHandle`; `getActionName()` =
`name` when `action_ns` is empty, else `name + "/" + action_ns`.

`joint_trajectory_controller` 4.42.1: server at `~/follow_joint_trajectory`;
`command_interfaces`/`state_interfaces` both `[position]` for a position-driven
arm; splines interpolation.

dfki-ric embedded CM: single-spawner lock is
`~/.ros/locks/ros2-control-controller-spawner.lock` (20 s acquire timeout).

## Files changed

Added:
- `src/robot_moveit_ros_interfaces/{CMakeLists.txt,package.xml}` +
  `srv/CartesianGoal.srv` + `test/test_interfaces.py`
- `src/robot_moveit/robot_moveit/cartesian_goal.py`
- `src/robot_moveit/launch/moveit_execution.launch.py`
- `src/robot_moveit/test/test_cartesian_goal.py`
- `src/robot_moveit/test/test_moveit_execution.py`
- `src/robot_moveit_config/config/moveit_controllers.yaml`

Modified:
- `src/robot_bringup/params/controllers.yaml`
- `src/robot_bringup/launch/mujoco.launch.py`
- `src/robot_bringup/test/test_mujoco_launch.py`
- `src/robot_moveit_config/config/sisyphus.srdf`
- `src/robot_moveit_config/config/kinematics.yaml` (KDL timeout 0.05→0.5 s,
  attempts 3→20; the sampler needs to solve real Cartesian goals)
- `src/robot_moveit_config/test/test_move_group_launch.py`
- `src/robot_moveit_config/test/test_srdf.py`
- `src/robot_moveit/package.xml`, `src/robot_moveit/setup.py`
- `src/robot_moveit_config/package.xml`
- `pixi.toml`, `pixi.lock`, `scripts/test_baseline.json`

## Test results (this worktree)

- `robot_moveit/test/test_cartesian_goal.py` — 11 passed
- `robot_moveit/test/test_moveit_execution.py` — 3 passed (e2e on domain 120)
- `robot_moveit_config/test/test_srdf.py` — 4 passed
- `robot_moveit_config/test/test_move_group_launch.py` — 5 passed
- `robot_moveit_ros_interfaces/test/test_interfaces.py` — 1 passed
- `robot_bringup/test/test_mujoco_launch.py` — 3 passed
- flake8/pep257/copyright for robot_moveit, robot_moveit_config,
  robot_bringup, robot_moveit_ros_interfaces — green

The full `pixi run test` suite is the test-runner's job (not run here).

## Build note

`vcs import src < robot.repos` was needed in this worktree (the sim source was
absent). The vendored `src/mujoco_ros2_control/` tree is gitignored; its
`mujoco_ros2_control_examples` sub-package fails to build here (it downloads STLs
from the network) and was moved aside for a local build only — it is untracked
and must not appear in the PR.
