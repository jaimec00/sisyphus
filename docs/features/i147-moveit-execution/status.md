# i147 — MoveIt 2 execution → dfki-ric control stack (PR2) — MANAGER RULINGS

Worktree: `/home/sisyphus/worktrees/feat/i147-moveit-execution` (branch `feat/i147-moveit-execution`, off `origin/main` @ 97754c5).
Context map: `docs/features/i147-moveit-execution/context.md` (the context-explorer's VERIFIED findings).
These rulings are binding on the implementer; a worker who believes one is wrong escalates in-process (never silently deviates).

---

## R1a — Execution path: FollowJointTrajectory (JTC), not a position bridge

**VERIFIED.** The RoboStack channel ships `ros-jazzy-joint-trajectory-controller` 4.42.1; `pixi add` succeeded and the plugin is installed:
- lib: `.pixi/envs/default/lib/libjoint_trajectory_controller.so`
- plugin XML: `share/joint_trajectory_controller/joint_trajectory_plugin.xml` → class `joint_trajectory_controller/JointTrajectoryController` (library `joint_trajectory_controller`).

**Ruling:** wire MoveIt execution through a real `joint_trajectory_controller` per arm, driven by MoveIt's `moveit_simple_controller_manager` (FollowJointTrajectory handle). **No position-command bridge** — acceptance requires a *real time-parameterised joint trajectory*, which only the JTC provides (a JointGroupPositionController is a teleport).

## R1b — Scope expansion to robot_bringup: APPROVED (minimal)

The brief's owned paths are `robot_moveit/`, `robot_moveit_config/`, `pixi.toml`, `scripts/test_baseline.json`, with `robot_backends/`+`robot_mcp/` hard-forbidden. `robot_bringup` is neither owned nor forbidden, and the arm control stack (the wiring seam for this PR) lives there.

**Ruling:** the PR MAY touch `src/robot_bringup/params/controllers.yaml` + `src/robot_bringup/launch/mujoco.launch.py` + the 2 affected `robot_bringup` tests, *minimally and only to wire the arm JTCs*. Do NOT touch `robot_backends/` or `robot_mcp/`. Rationale: the alternative (duplicating the sim launch inside `robot_moveit_config`) is fragile drift-prone duplication; `robot_bringup` is the single source of truth for controllers. **Flag this scope expansion in the PR description.**

**VERIFIED safe:** `robot_backends/mujoco_backend.py` drives MuJoCo *directly* (writes `data.qpos`/`data.ctrl` by name), with **zero** references to `/arm_gripper_position_controller/commands` or any ROS controller topic. The two sim integrations are independent; shrinking the group controller cannot affect the off-limits backends.

## R1c — Controller restructure (exact)

In `controllers.yaml`:
- **Add** `left_arm_controller` + `right_arm_controller`, each `joint_trajectory_controller/JointTrajectoryController`, `command_interfaces: [position]`, `state_interfaces: [position]`, joints = the 5 revolute joints per side (shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll).
- **Shrink** `arm_gripper_position_controller` to `column_lift + left_gripper + right_gripper` (3 joints). Keep the name (historical; add a comment) to minimise churn — a rename is a NOTE, not a blocker.
- Keep `joint_state_broadcaster` + `base_velocity_controller` unchanged.
- Declare the two new JTCs in BOTH the top-level `controller_manager.ros__parameters` block AND their own `ros__parameters` blocks (the dfki-ric franka-pattern, see `mujoco.launch.py` R-PR8b-14 comment).

In `mujoco.launch.py`: add `_spawner('left_arm_controller', controllers_file)` + `_spawner('right_arm_controller', controllers_file)` to the `OnProcessStart(simulator)` list.

**Blast radius (must update, all in robot_bringup/test):**
1. `test_controllers_yaml_declares_position_and_velocity_groups` — 13 joints → 3 for the group controller; add JTC assertions (type + 5 joints each).
2. `test_joint_command_moves_sim_state` — `SMOKE_JOINT='left_shoulder_pan'` is now owned by `left_arm_controller`. Change `SMOKE_JOINT` to `column_lift` (still in the group controller).

## R2 — Collision pairs: re-enable exactly these 16 entries

Remove from `sisyphus.srdf` (delete the lines, update the `Cross-side / body pairs` comment):

- 4 cross-side `Default`: `left_shoulder_link↔right_shoulder_link`, `left_upper_arm_link↔right_upper_arm_link`, `left_wrist_roll_link↔right_wrist_roll_link`, `left_gripper_base_link↔right_gripper_base_link`.
- 12 `column_top` `Never`: `column_top` ↔ each of `{left,right}_{shoulder_link, upper_arm_link, lower_arm_link, wrist_link, wrist_roll_link, gripper_base_link}`.

**KEEP everything else**, including `column_rail_link↔column_top` (Adjacent), `base_link↔base_chassis_link` (Adjacent), `base_link↔base_footprint` (Never — genuine), and all per-side arm/gripper entries.

**Neutral-pose verification (mandatory):** PR1's `test_move_group_neutral_pose_is_collision_free` asserts the all-zeros pose is collision-free. Geometry FK (context.md §4.2) says the arms are 0.36 m apart and 0.53 m from the carriage at neutral, so re-enabling should keep it green — but STL meshes ≠ link-origin FK. **Extend that test (do not replace) to print `response.contacts` on failure**, and run it after re-enabling. If the neutral pose is genuinely colliding (unexpected), STOP and escalate — that means the home pose itself is a self-collision, which is a bigger ruling.

## R3 — out_of_reach discriminator: explicit envelope pre-check (never error codes)

`PLANNING_FAILED` (−1) is ambiguous (collision *and* unreachable). The node MUST reproduce the backend reach check: distance from the correct `{left,right}_shoulder_link` origin (in `base_link` frame, via TF2 — robust to column_lift) to the target > `reach_radius = 0.85` m → `OUT_OF_REACH`, *before* planning. This mirrors `mock_backend._require_reachable` (reach_radius 0.85 m sphere around each shoulder; `arm.xacro:86`). Note: the ~0.4 m in D39 prose is the *task* envelope, not arm reach — 0.85 m is authoritative.

## R4 — Test geometry: base/nav OUT of scope

No seed object is within 0.85 m of a shoulder from the charger base (mug_1 is ~2.15 m away). Ruling:
- **Moves-arm test:** a reachable target (within 0.85 m of the left shoulder, e.g. ~(0.4, 0.18, 0.75)); seed it as a synthetic world object OR pass the pose directly. No navigation/teleport.
- **out_of_reach test:** target clearly outside the envelope (e.g. `mug_1` at x≈2.1).
- **self-collision test:** a target pose *inside* the robot's own column/body (e.g. gripper to ~(0, 0, 0.3), which the arm cannot reach without folding into the column) → the plan must be REJECTED. This is the proof R2's re-enabled pairs actually detect the collision.

## R5 — Cartesian-goal node (new, in robot_moveit)

- `src/robot_moveit/robot_moveit/cartesian_goal.py` (+ a `.srv` in `srv/`). Interface: request {object_id (empty ⇒ use pose), target_pose (geometry_msgs/Pose, optional), arm ('left'|'right')}; response {success, error_code (moveit_msgs MoveItErrorCodes val), status (enum SUCCESS/OUT_OF_REACH/COLLISION/FAILURE)}.
- Behaviour: (1) resolve target pose — object_id ⇒ `/world_query/get_world` via `robot_world.WorldDocument` + `world_to_scene`, else direct pose; (2) transform to `base_link`; (3) envelope pre-check (R3); (4) plan via **moveit_py** `PlanningComponent` `set_goal_state(pose_stamped_msg=..., pose_link='{arm}_gripper_base_link')` → `plan()` (OMPL pipeline, full collision checking); (5) map plan error: collision (−10/−12/−1 w/ contacts) ⇒ COLLISION, else FAILURE; (6) `execute()` → execution manager → msscm → JTC.
- **Probe moveit_py empirically** before coding (the plan/execute signatures are C++ bindings — read the installed `.pyi`/docs; never train-recalled API). If moveit_py proves unusable, fall back to raw services (`/plan_kinematic_path` + `/execute` action) — but keep the envelope pre-check either way.

## R6 — move_group controller config + composed launch

- Add `src/robot_moveit_config/config/moveit_controllers.yaml` (standard msscm schema):
  `moveit_controller_manager: moveit_simple_controller_manager/MoveItSimpleControllerManager` + `moveit_simple_controller_manager.controller_names: [left_arm_controller, right_arm_controller]`, each `type: FollowJointTrajectory`, `action_ns: follow_joint_trajectory`, `default: true`, `joints: [5 per side]`.
- **Keep PR1's `move_group.launch.py` intact** (headless, controller-free, testable). Add a NEW composed launch `moveit_execution.launch.py` in `robot_moveit_config` that includes `mujoco.launch.py` (sim + JTCs) + move_group loaded WITH the controller-manager config + `planning_scene_bridge` + the `cartesian_goal` node.
- **Verify the JTC's FollowJointTrajectory action name empirically** after spawning (should be `/<name>/follow_joint_trajectory`); context.md §3.3 flagged an empty-`action_ns` ambiguity in msscm's handle — confirm the exact dialled action and set `action_ns` accordingly.

## R7 — Tests + domain id

- New execution e2e test in `robot_moveit_config/test/` (launch the full `moveit_execution.launch.py`, drive `cartesian_goal` service, subscribe `/joint_states`). Follow `test_joint_command_moves_sim_state` (sim-in-test) + `_MoveGroupProbe` (move_group-in-test) patterns.
- **"not a teleport" assertion:** during execution, observe ≥1 `/joint_states` sample strictly between start and goal (proves it moved *through*, not snapped), and assert the final arm joints ≈ goal within tolerance.
- **Fresh ROS domain: `120`** (in use: 112,113,115,116,117,118,119,121,131).
- **Bump `scripts/test_baseline.json`** counts with the new tests (`pixi run test` raises the floor).

## Open risks (implementer: verify, escalate if they fail)
1. Neutral pose stays collision-free after R2 (R2 note above).
2. JTC actually ticks on the embedded CM (update_rate 100 Hz vs sim 1/500 s — context.md §7.6); the moves-arm test proves this.
3. msscm action-name (R6 note) — confirm empirically.

