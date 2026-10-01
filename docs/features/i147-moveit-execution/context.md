# Feature i147 — MoveIt 2 execution wired to the dfki-ric control stack

**Worktree:** `/home/sisyphus/worktrees/feat/i147-moveit-execution` (branch `feat/i147-moveit-execution`, cut from `origin/main` @ `97754c5`; PR1 #146 merged).
**Owned paths:** `src/robot_moveit/`, `src/robot_moveit_config/`, `pixi.toml`, `scripts/test_baseline.json`.
**Off-limits:** `src/robot_backends/`, `src/robot_mcp/` (read for semantics only).

Labels used below: **[OBS]** = empirically observed (command run, output quoted); **[SRC]** = inferred from reading source.

---

## 1. Feature + acceptance criteria

Wire MoveIt 2 execution to the ROS 2 control stack so a **Cartesian goal actually moves the arm joints in sim** — a real joint trajectory, NOT a teleport.

1. A `move_gripper`-style Cartesian goal moves arm joints in sim **along a trajectory** (not a teleport).
2. `out_of_reach` fires outside the SO-101 envelope.
3. A **self-colliding plan is REJECTED**.

Plus two binding research items the manager must rule on:
- **R1** — does dfki-ric expose a `joint_trajectory_controller` / `FollowJointTrajectory` for the arm DOF, or do we drive the position controllers directly?
- **R2** — revisit PR1's `disable_collisions`; re-enable pre-emptive cross-side and column pairs; keep only genuine `Never`.

---

## 2. Repo map (relevant modules)

| Path | What it is |
|---|---|
| `src/robot_bringup/params/controllers.yaml` | 3 controllers on the dfki-ric CM: `joint_state_broadcaster`, `arm_gripper_position_controller` (JointGroupPositionController, **13 joints** = column_lift + 10 arm + 2 grippers), `base_velocity_controller` (3 wheels). **[SRC]** |
| `src/robot_bringup/launch/mujoco.launch.py` | Spawns the sim + those 3 controllers onto `/controller_manager`. Includes `ros2_control.xacro`-declared interfaces. **[SRC]** |
| `src/robot_description/urdf/ros2_control.xacro` | **All 13 position-commanded joints expose a `position` command interface** (wheels `velocity`). Grippers/mirrors: driven joints have position command; mirrors state-only. **[SRC]** |
| `src/robot_description/urdf/arm.xacro` | SO-101 arm macro. `reach_radius = 0.85 m` (L86), `shoulder_offset_y = 0.18` (L80), `shoulder_offset_z = 0.50` (L81). 5 revolute joints/side: `shoulder_pan/shoulder_lift/elbow_flex/wrist_flex/wrist_roll`. **[SRC]** |
| `src/robot_description/urdf/column.xacro` | Prismatic `column_lift` (travel 0.00–1.20 m), carriage `column_top` (0.14 × 0.10 × 0.08 m). **[SRC]** |
| `src/robot_moveit_config/config/sisyphus.srdf` | 144 lines, **43 `disable_collisions`** entries. Groups `left_arm`/`right_arm` (chains), `left_gripper`/`right_gripper`, EEFs. **[SRC]** |
| `src/robot_moveit_config/launch/move_group.launch.py` | Headless single-node `move_group`. `allow_trajectory_execution=true`, **no controller-manager config, no controllers, no sim**. **[SRC]** |
| `src/robot_moveit_config/config/{kinematics,joint_limits,ompl_planning}.yaml` | KDL IK (R7), planner vel/accel overrides, OMPL pipeline. **[SRC]** |
| `src/robot_moveit/robot_moveit/planning_scene_bridge.py` | Polls `/world_query/get_world` (`robot_world_ros_interfaces/srv/GetWorld`, field `world_json`) → parses with `WorldDocument` → diffs → `/apply_planning_scene`. **[SRC]** |
| `src/robot_moveit/robot_moveit/world_to_scene.py` | ROS-free `WorldDocument` → `SceneObject(object_id, label, shape, frame_id='world', position, orientation)`. **[SRC]** |
| `src/robot_world/robot_world/default_world.json` | Seed objects: `mug_1` (2.1, 0.1, 1.15), `plate_1` (2.1, −0.1, 1.15), `bowl_1` (2.2, 0, 1.15), `counter_1` (2.15, 0, 0.85), `book_1`/`cup_1` (~0.1, 2.0, 0.9), `sofa_1`. Poses in `world` frame. **[OBS]** |

---

## 3. R1 finding — execution path **[OBS]**

### 3.1 Is a `joint_trajectory_controller` package available on the channel? — YES

```
$ pixi search joint_trajectory_controller
Error:  × No packages found matching 'joint_trajectory_controller'

$ pixi search ros-jazzy-joint-trajectory-controller
Version             4.42.1
Build               he8cfe8b_21
Subdir              linux-aarch64
File Name           ros-jazzy-joint-trajectory-controller-4.42.1-he8cfe8b_21.conda
URL                 https://prefix.dev/robostack-jazzy/linux-aarch64/ros-jazzy-joint-trajectory-controller-4.42.1-he8cfe8b_21.conda
Dependencies: - ros2-joint-trajectory-controller ==4.42.1
Other Versions: 4.40.0, 4.37.0, 4.36.0, 4.33.1, 4.32.0 (+5 more)
```

**Verdict: the RoboStack Jazzy channel offers `ros-jazzy-joint-trajectory-controller` at 4.42.1.** (Note: `pixi search` for the bare name `joint_trajectory_controller` returns nothing — the package is prefixed. D39's note about not using `--limit` was honoured.)

### 3.2 Is it already installed (transitive dep)? — NO

```
$ pixi run bash -c 'ls $CONDA_PREFIX/lib/libjoint_trajectory_controller* ; \
  find $CONDA_PREFIX -name "*joint_trajectory_controller*" -maxdepth 6'
(no lib*.so)
(find only finds control_msgs/JointTrajectoryControllerState msgs + a gz-sim world sdf)

$ ls $CONDA_PREFIX/share | grep -iE 'trajectory|controller'
diff_drive_controller
forward_command_controller
controller_interface, controller_manager, controller_manager_msgs
moveit_simple_controller_manager
nav2_* controllers
position_controllers
velocity_controllers
trajectory_msgs
```

**Verdict: NOT installed.** `position_controllers`, `velocity_controllers`, `forward_command_controller`, `diff_drive_controller` are present; **no `joint_trajectory_controller`**. So adding it requires a `pixi add` (owned path: `pixi.toml`).

### 3.3 What MoveIt's execution needs (moveit_simple_controller_manager)

`msscm` version 2.12.4. **Controller-handle types it supports** (headers in `$CONDA_PREFIX/include/moveit_simple_controller_manager/moveit_simple_controller_manager/`): **[SRC]**

| Handle | Action interface |
|---|---|
| `FollowJointTrajectoryControllerHandle` | `control_msgs/action/FollowJointTrajectory` |
| `GripperCommandControllerHandle` | `control_msgs/action/GripperCommand` |
| `ParallelGripperCommandControllerHandle` | `control_msgs/action/ParallelGripperCommand` |
| `EmptyControllerHandle` | (no-op) |

The follow-joint-trajectory handle's doc comment states it is "generally used for arms, or anything using a control_msgs/FollowJointTrajectoryAction".

**The action name it dials** (`action_based_controller_handle.hpp:204-215`) is:
```
if namespace_ empty: name_          else: name_ + "/" + namespace_
```
i.e. a controller config with `name: arm_gripper_position_controller` and no `action_ns` dials the action literal `arm_gripper_position_controller` (not `.../follow_joint_trajectory`). **This is the yaml-key shape the implementer needs** (upstream `moveit_controllers.yaml` schema: `moveit_controller_manager`, `moveit_simple_controller_manager.<name>.{type, action_ns, joints, default, parallel}`). **[SRC]** — the exact yaml example files are not shipped in the conda share dir (only headers + the plugin XML); the implementer should confirm the key shape against `move_it/…/moveit_controllers.yaml` upstream docs and the plugin's `initialize` param reads before relying on it.

What move_group needs to use it (upstream wiring, none present in PR1's launch):
- `moveit_controller_manager: moveit_simple_controller_manager/MoveItSimpleControllerManager` param
- `moveit_simple_controller_manager:` block with one entry per MoveIt-controller (name → joints)
- the corresponding `moveit_controllers.yaml` loaded into move_group's params

### 3.4 Does dfki-ric's embedded CM load arbitrary ros2_controllers plugins? — YES **[OBS]**

`mujoco_ros2_control/src/mujoco_ros2_control_plugin.cpp` (source at `~/worktrees/i145-moveit-deps-bringup/src/mujoco_ros2_control`) **constructs a standard `controller_manager::ControllerManager`** (`init_controller_manager()`, L366-399):

```cpp
controller_manager_.reset(new controller_manager::ControllerManager(
    std::move(resource_manager_), executor_, "controller_manager", "", cm_node_options));
...
long cm_update_rate = controller_manager_->get_parameter("update_rate").as_int();
control_period_ = ... 1.0/cm_update_rate;
if (control_period_ < mujoco_period_) { RCLCPP_ERROR(...); control_period_ = mujoco_period_; }
```

It already loads standard `ros2_controllers` plugins (`joint_state_broadcaster/JointStateBroadcaster`, `position_controllers/JointGroupPositionController`, `velocity_controllers/JointGroupVelocityController`) via the standard `controller_manager/spawner`. **There is nothing dfki-ric-specific that prevents loading `joint_trajectory_controller/JointTrajectoryController`.** The hardware exposes a **`position` command interface** on every arm joint — exactly what JTC's `command_interfaces: [position]` requires. **[SRC from ros2_control.xacro + OBS from plugin source]**

**Caveat the implementer must verify empirically:** the CM `update_rate` is 100 Hz and the sim period is 1/500 s; the plugin clamps a control period faster than the sim period. JTC interpolates on its own `update()` tick — confirm a JTC spawned onto this embedded CM actually ticks (sim-time / period semantics).

### 3.5 Claim-conflict analysis (the central R1 decision surface)

The 10 arm joints are currently owned by `arm_gripper_position_controller`. A JTC over the same joints **cannot coexist** (ros2_control enforces single command-interface ownership → second claim fails to activate).

**(a) Is anything in-repo publishing to `/arm_gripper_position_controller/commands`?** — grep of the whole `src/` tree: **NO**. The only arm/base command publisher in the repo is `src/robot_nav/robot_nav/omni_base_controller.py:129` → `/base_velocity_controller/commands`. Nothing drives `arm_gripper_position_controller`. **[OBS]**

**(b) Cleanest restructure options:**

- **Option A — add per-arm JTCs and shrink the group controller.** Add `left_arm_controller` + `right_arm_controller` (`joint_trajectory_controller/JointTrajectoryController`, `command_interfaces: [position]`, 5 joints each), and reduce `arm_gripper_position_controller` to `column_lift` + `left_gripper` + `right_gripper` (3 joints). MoveIt's `moveit_controllers.yaml` then maps `left_arm`/`right_arm` groups to the JTCs. **This edits `src/robot_bringup/params/controllers.yaml` — OUTSIDE the owned paths.**
- **Option B — keep `robot_bringup` untouched; own the controller config on the MoveIt side.** A new launch in `robot_moveit_config` includes the sim (`mujoco.launch.py` or a sim-only sub-launch) but supplies its **own** controller params + spawners (its own `moveit_controllers.yaml` + a `controllers.yaml` copy). But the embedded CM reads the whole `controllers.yaml` as a **sim node parameter** (`mujoco.launch.py` passes the file straight to the simulator Node) and the spawners are registered on `OnProcessStart(simulator)` inside `mujoco.launch.py` — so a MoveIt-side launch cannot cleanly replace the controller set without either duplicating the sim launch or refactoring `mujoco.launch.py` to accept an override. **[SRC]**
- **Option C — hybrid:** the JTC config + spawners live in a new `robot_moveit_config` launch that includes the **sim-only** portion, and the shared `controllers.yaml` gains the JTC blocks. This still touches `robot_bringup`'s yaml unless the sim launch is parameterised.

**Trajectory-execution-manager note:** MoveIt's `TrajectoryExecutionManager` only takes action when move_group has a controller manager configured. PR1's `move_group.launch.py` deliberately left `allow_trajectory_execution=true` but supplied no controller manager — "PR3 adds execution". **[SRC]**

**→ This is the manager's ruling to make:** whether the PR may touch `src/robot_bringup/` (and if not, which override mechanism the MoveIt launch must use).

---

## 4. R2 finding — collision pairs

### 4.1 Per-entry classification (43 entries)

Geometry facts **[OBS]** (forward kinematics at all-zeros, base frame; link origins):
- `base_link` (0,0,0); `base_chassis_link` (0,0,0.085); `column_carriage` (`column_top`) at (0,0,0.195).
- Left shoulder (0, **+0.18**, 0.695), right shoulder (0, **−0.18**, 0.695) → **0.36 m apart in y**.
- Left fingertip (0.35, +0.185, 0.645); right fingertip (0.35, −0.175, 0.645) → **~0.36 m apart**.
- Nearest arm link to `column_top`: `left_shoulder_link` / `right_shoulder_link`, 0.531 m centre-to-centre; carriage half-extents 0.07×0.05×0.04 m.

| Entry | PR1 reason | Classification | Action |
|---|---|---|---|
| per-side chain `Adjacent` (shoulder↔pitch, pitch↔upper, upper↔lower, lower↔wrist, wrist↔wrist_roll, gripper_base↔wrist_roll, gripper_base↔jaws) | Adjacent | genuinely adjacent (joined by a joint, always touching) | **KEEP** |
| `<side>_gripper_base_link`↔`<side>_wrist_link` | Default | two links of a compact fixed chain — fold can bring them close | **KEEP**, candidate for re-enable review |
| `<side>_gripper_lower_tip_link`↔`<side>_gripper_upper_tip_link` | Default | the two fingertips of one gripper (open/close) — can genuinely touch at closed | **KEEP** (real contact) |
| `<side>_gripper_{upper,lower}_jaw_link`↔`<side>_gripper_{upper,lower}_tip_link` | Never | **rigid fixed pairs** (`*_tip_joint` is fixed) | **KEEP** |
| `column_rail_link`↔`column_top` | Adjacent | prismatic carriage on the rail — always adjacent | **KEEP** |
| `base_link`↔`base_chassis_link` | Adjacent | fixed mount | **KEEP** |
| `base_link`↔`base_footprint` | Never | fixed, footprint is a virtual frame | **KEEP** |
| cross-side `left_shoulder_link`↔`right_shoulder_link` | Default | **RE-ENABLE candidate** — 0.36 m apart at neutral; each arm has 0.85 m reach → arms CAN reach each other's space | **RE-ENABLE per R2** (verify neutral first) |
| cross-side `left_upper_arm_link`↔`right_upper_arm_link` | Default | same | **RE-ENABLE per R2** |
| cross-side `left_wrist_roll_link`↔`right_wrist_roll_link` | Default | same | **RE-ENABLE per R2** |
| cross-side `left_gripper_base_link`↔`right_gripper_base_link` | Default | same | **RE-ENABLE per R2** |
| `column_top` ↔ **every** arm link (12 `Never` pairs) | Never | **RE-ENABLE per R2** — the arm can fold back into the column; PR1's own comment says "the two arms are mounted on the same column and can fold into each other and into the column". 0.53 m clearance at neutral does NOT mean unreachable. | **RE-ENABLE per R2** |

(The 12 `Never` column pairs and 4 cross-side `Default` pairs are exactly the "pre-emptive" disables PR1's SRDF comment admits: *"these pairs are not collision-avoidable goals in PR1 (the arms have no motion yet: PR2 owns reachable-space collision handling), so they are disabled rather than left to reject every plan."*)

### 4.2 ★ Neutral-pose risk (the key R2 hazard)

PR1 test `test_move_group_neutral_pose_is_collision_free` asserts the all-zeros arm pose passes `/check_state_validity`. Re-enabling the cross-side/column pairs **could** make the neutral pose self-collide.

**Geometry reasoning [OBS]:** at all-zeros the arms are 0.36 m apart laterally and the nearest arm link is 0.53 m from `column_top` (carriage half-extents ~0.07×0.05×0.04 m). **The all-zeros pose does NOT bring left/right arms or arm/column into contact.** The PR1 disables were pre-emptive (the file comments say so explicitly). **Confidence: high that the neutral pose stays collision-free with the pairs re-enabled**, but the collision meshes are STLs (not the mass-box extents I used), so:

**→ The implementer MUST run this empirical check:** re-enable the pairs, launch move_group, call `/check_state_validity` (`moveit_msgs/srv/GetStateValidity`) at the all-zeros pose of each arm, and **print `response.contacts`** (each `contact_body_1`/`contact_body_2`) if `valid=False`. `test_move_group_neutral_pose_is_collision_free` already does exactly this and reports contacts — **extend it, don't replace it.**

**Envelope note:** the SO-101 arm's `reach_radius = 0.85 m` around **each shoulder** (`arm.xacro:86`). The "~0.4 m" figure in D39 prose is the *task* envelope, not the arm reach. The two 0.85 m spheres centred 0.36 m apart **overlap heavily** → cross-side collisions are physically reachable and must not be masked.

### 4.3 Home / neutral pose definition

- The "neutral" pose MoveIt plans from is the **all-zeros arm joint vector** (used by PR1's tests). **[OBS]**
- The **actual home** used by the sim backends is also arm-joints-at-zero: `mujoco_backend.py` (L519-527) writes `ctrl = 0` for arm joints; **grippers home at joint zero being OPEN** (`gripper.xacro` / `mujoco_backend.py` L522-528: "their home is not joint zero"). **[SRC]**
- `RobotModel.home_gripper_offset = (0.35, 0, −0.05)` relative to the shoulder, **derived** from the URDF (`robot_model.py:222` `_home_gripper_offset`), asserted by `test_so101_gripper_grasp_reference_matches_home_gripper_offset`. **[SRC]**
- **`out_of_reach` semantics (do not re-invent):** the Mock tests a **sphere of `reach_radius` (0.85 m) around each shoulder** (`mock_world.py:150` `shoulder()`, `mock_backend.py:450-465` `_reach_offset`/`_require_reachable`); the MuJoCo backend instead asks `robot_backends.ik.solve_ik` in **position-only** mode (`mujoco_backend.py` L88-91, L293-296). The MoveIt side must reproduce "distance from the correct shoulder > 0.85 m → `out_of_reach`". **[SRC]**

---

## 5. MoveIt API surface

### 5.1 `moveit_py` surface **[OBS]**

`moveit.planning` exports: `MoveItPy`, `PlanningComponent`, `PlanningSceneMonitor`, `TrajectoryExecutionManager`, `PlanRequestParameters`, `MultiPipelinePlanRequestParameters`, `LockedPlanningSceneContextManagerRW/RO`.

`PlanningComponent`: `plan`, `execute`, `get_start_state`, `set_goal_state`, `set_start_state`, `set_start_state_to_current_state`, `set_path_constraints`, `set_workspace`, `unset_workspace`, `named_target_states`, `get_named_target_state_values`, `planning_group_name`.
`MoveItPy`: `execute`, `get_planning_component`, `get_planning_scene_monitor`, `get_robot_model`, **`get_trajectory_execution_manager`**, `shutdown`.
`TrajectoryExecutionManager`: `execute`, `execute_and_wait`, `push`, `stop_execution`, `wait_for_execution`, `is_managing_controllers`, `are_controllers_active`, `ensure_active_controllers*`, `set_execution_velocity_scaling`, `set_allowed_start_tolerance`, `get_last_execution_status`.

`set_goal_state(...)` accepts: `configuration_name` (named target), `robot_state` (`moveit_py.core.RobotState`), `pose_stamped_msg` (+`pose_link`), or `motion_plan_constraints`. **[OBS via docstring]**

`PlanningComponent.plan`/`execute` are C++ bindings — `inspect.signature` raises `ValueError: no signature found for builtin`. The implementer should read `moveit_py`'s Python `.pyi`/docs for exact kwargs. **[OBS]**

`moveit_py` needs a full MoveIt config (URDF+SRDF+kinematics+**controller manager config**) — plan it as an alternative to the service path, but it requires the controller-manager wiring too.

### 5.2 Service path + error-code map

Available services (from `moveit_msgs`):
- `/plan_kinematic_path` (`GetMotionPlan`) — used by PR1 test with a joint-space goal.
- `/compute_cartesian_path` (`GetCartesianPath`) — the Cartesian path helper.
- `/check_state_validity` (`GetStateValidity`) — `valid` + `contacts[]`.
- `/compute_ik` (`GetPositionIK`) — discriminates a loaded group (`NO_IK_SOLUTION`) from an unknown one (`INVALID_GROUP_NAME`).
- `/execute` — `moveit_msgs/action/ExecuteTrajectory` (**NOTE:** upstream move_group exposes this only when a controller manager is configured).

`MoveItErrorCodes` exact ints **[OBS]** (via `from moveit_msgs.msg import MoveItErrorCodes`):

| Constant | Value | Meaning for us |
|---|---|---|
| `SUCCESS` | **1** | plan/exec fine |
| `FAILURE` | 99999 | generic; **PR1's "no planner config" signature** |
| `PLANNING_FAILED` | **−1** | planner failed (incl. collision-wrapped failures) |
| `START_STATE_IN_COLLISION` | **−10** | *self-collision at start* |
| `GOAL_IN_COLLISION` | **−12** | *goal in collision* |
| `START_STATE_INVALID` | −26 | bad start state |
| `GOAL_STATE_INVALID` | −27 | bad goal state |
| `NO_IK_SOLUTION` | **−31** | only returned from IK-reachability checks |
| `GOAL_CONSTRAINTS_VIOLATED` | −14 | constraints unsatisfiable (Cartesian goal outside reach often lands here or as NO_IK_SOLUTION) |
| `INVALID_GROUP_NAME` | −15 | group not loaded |

**Mapping for the three acceptance criteria:**
- **out_of_reach** → `PLANNING_FAILED` (−1) or `GOAL_CONSTRAINTS_VIOLATED` (−14) or `NO_IK_SOLUTION` (−31). The **envelope test must be checked BEFORE planning** (distance from shoulder > 0.85 m) since −1 is ambiguous (it is also returned for collisions). **[SRC — ambiguity is real; recommend an explicit pre-check so out_of_reach is distinguishable from collision]**
- **self-collision (start or goal)** → `START_STATE_IN_COLLISION` (−10) / `GOAL_IN_COLLISION` (−12); a mid-path collision comes back as `PLANNING_FAILED` (−1) with a contact list on the response.
- **SUCCESS** → 1.

### 5.3 Reading object poses (reuse PR1)

Service `/world_query/get_world` (`robot_world_ros_interfaces/srv/GetWorld`) returns **one field: `string world_json`**, byte-identical to `FileWorldStore`'s on-disk document (`GetWorld.srv`). Parse with `robot_world.WorldDocument` then `robot_moveit.world_to_scene` → `SceneObject(object_id, label, shape, frame_id='world', position, orientation)`. **Reuse `planning_scene_bridge._read_world_json` / `diff_scene`.** **[SRC]**

The `world`→`base_link` identity TF is published by PR1's bridge launch; object poses are already in the `world` frame. **[SRC]**

---

## 6. Existing test patterns to follow

| Pattern | File:line | What it does |
|---|---|---|
| `_MoveGroupProbe` context manager | `src/robot_moveit_config/test/test_move_group_launch.py:295-363` | `ros2 launch` in its own process group on a private `ROS_DOMAIN_ID` (`118`), rclpy `SingleThreadedExecutor`, `wait_for_client`/`call`, `killpg` teardown. **Use this for any execution test.** |
| Neutral-pose validity + contact listing | `test_move_group_launch.py:477-511` | `/check_state_validity` at all-zeros per arm; **extend this for R2.** |
| Plan-for-an-arm (joint-space) | `test_move_group_launch.py:513-570` | `/plan_kinematic_path`, asserts `error_code == SUCCESS` + non-empty trajectory. **Model the Cartesian-goal test on this.** |
| Group-loaded discriminator | `test_move_group_launch.py:183+` | `/compute_ik` → `NO_IK_SOLUTION` vs `INVALID_GROUP_NAME`. |
| SRDF/URDF structural checks | `src/robot_moveit_config/test/test_srdf.py` | Parses SRDF + expanded URDF, asserts group/joint names. **Add R2 assertions here** (no `Never` on the re-enabled pairs). |
| 3-node e2e stack + polling | `src/robot_moveit/test/test_planning_scene_bridge_e2e.py` | Launches the stack, polls `/get_planning_scene`. |
| `MOVE_GROUP_DOMAIN_ID` | `test_move_group_launch.py:60` | `118` (taken: 112 tf_tree, 113 mujoco, 115 world_write, 117 world_launch, 119 bridge_e2e). **Pick a fresh domain for an execution e2e.** |
| Test baseline ratchet | `scripts/test_baseline.json` | `robot_moveit`: 7, `robot_moveit_config`: 8. `pixi run test` raises the floor; commit the bumped counts with the new tests. |

---

## 7. Known gotchas

1. **Claim conflict is a hard blocker for JTC.** The arm joints are owned by `arm_gripper_position_controller`; a JTC cannot activate over the same joints. Restructure first (§3.5).
2. **`joint_trajectory_controller` is not installed** — `pixi add ros-jazzy-joint-trajectory-controller` is required (`pixi.toml` is owned).
3. **The embedded CM reads `controllers.yaml` as a *sim node parameter*** and the spawners live inside `mujoco.launch.py`'s `OnProcessStart(simulator)` handler — a MoveIt-side launch cannot simply append a controller without either copying the sim launch or parameterising it. **[SRC]**
4. **MoveIt's action name for a controller** is `name` when `action_ns` is empty (not `name/follow_joint_trajectory`) — get the yaml right. **[SRC]**
5. **−1 (`PLANNING_FAILED`) is ambiguous** (collision AND unreachable). Do an explicit envelope pre-check for `out_of_reach`.
6. **CM update_rate (100 Hz) vs sim period (1/500 s)** — the plugin clamps control period to the sim period; verify JTC ticks on sim time. **[SRC/OBS]**
7. **Do NOT use `pixi search --limit`** (D39 truncates the list).
8. **`move_group.launch.py` is deliberately controller-manager-free.** Execution wiring belongs in a new/composed launch (or new params), keeping PR1's single-node launch testable.
9. **The STL collision meshes** mean FK-on-link-origins (what I computed) is an approximation; the neutral-pose assertion must be empirical.
10. **`robot_backends` and `robot_mcp` are off-limits** — read the reach semantics, don't edit them.

---

## 8. Open questions for the manager (do NOT resolve here)

1. **R1 restructure:** may the PR edit `src/robot_bringup/params/controllers.yaml` (Option A), or must the MoveIt side own an override without touching bringup (Option B/C)? If B/C, is parameterising `mujoco.launch.py` permitted (it is outside the owned paths)?
2. **JTC vs position-bridge:** commit to `FollowJointTrajectory` (add the JTC package) or drive `arm_gripper_position_controller` directly (no JTC)? The JTC path gives a real joint trajectory + MoveIt-native execution; the bridge avoids the claim conflict but is a custom MoveIt controller handle.
3. **`arm_gripper_position_controller` fate:** shrink to column+grippers, or remove entirely?
4. **R2 scope:** exactly which 16 pre-emptive pairs get re-enabled, and does the neutral-pose test get extended or must the home pose change?
5. **Cartesian-goal target object:** which seed object + side (left/right arm) for the acceptance test? (`mug_1` is reachable from the kitchen/counter; the sim base must be positioned, since the arm alone cannot reach x≈2.1 from the charger.)
6. **`out_of_reach` discriminator:** explicit envelope pre-check in the MoveIt node, or rely on error codes? (−1 is ambiguous.)
7. **Base positioning:** is a `navigate_to`/teleport to the kitchen in scope, or does the test assume the base is already at the object?
8. **New test domain id** (118 is taken).
