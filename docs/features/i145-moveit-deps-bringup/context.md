# context.md — PR145 (issue #145) MoveIt deps + move_group bringup + planning scene

## Goal (foundation PR only)
Stand up MoveIt 2 on the classical/real track. This PR adds MoveIt deps, brings
up `move_group` headless, authors the arm SRDF, and feeds a planning scene from
`robot_world`. NO arm motion (PR2), NO pick-and-place (PR3), NO TRAC-IK
source-build (fallback only, later).

## Acceptance criteria
1. `pixi install` + full `pixi run build` green with MoveIt deps added.
2. `move_group` starts headless; the arm planning group loads from the SRDF.
3. The planning scene contains the world's objects (from robot_world).

## Key decisions already made (do not violate)
- MoveIt runs on the classical/real track (dfki-ric ROS 2 stack, D33/D36/D39),
  NOT the ROS-free brain backend (D34). D30 holds: `robot_backends`/`robot_mcp`
  stay ROS-free at runtime. -> the MoveIt code is ROS 2 packages, never imported
  by the brain backend.
- Arm is a swappable xacro macro (D26): SRDF authored per-side, tied to the
  macro's `${name}` (left/right) naming so PiPER drops in as a macro swap.
- `robot_world` stays the single source of truth for object poses (D23/D35):
  read through the `/world_query/get_world` service, not a second store.

## Relevant existing code (verified, cite these)
### The arm (robot_description)
- `src/robot_description/urdf/robot.urdf.xacro` — top entry point (expands
  base/column/arm/gripper/transmissions/ros2_control). `<robot name="sisyphus">`.
- `src/robot_description/urdf/arm.xacro` — `so101_arm` macro, one per side
  (`left`/`right`). Joints per side (prefix `left_`/`right_`):
  `shoulder_pan, shoulder_lift, elbow_flex, wrist_flex, wrist_roll` (5 revolute
  arm DOF). Links: `shoulder_link, shoulder_pitch_link, upper_arm_link,
  lower_arm_link, wrist_link, wrist_roll_link`.
- `src/robot_description/urdf/gripper.xacro` — `so101_gripper` macro. Joints:
  `<side>_gripper` (driven revolute), `<side>_gripper_mirror` (revolute with
  `<mimic joint="<side>_gripper" multiplier="-1"/>`). Links:
  `gripper_base_link` (fixed-mounted on wrist_roll_link, identity mount),
  `gripper_upper_jaw_link`, `gripper_lower_jaw_link`, `*_tip_link`.
- Description ships to `share/robot_description/urdf/` + `share/.../meshes/`
  (setup.py data_files). xacro expands `package://robot_description/...`.

### robot_world / D35 seam
- `src/robot_world/robot_world/document.py` — `WorldDocument` (locations,
  start_location, objects, start_column_height) + `WorldObject` (object_id,
  label, pose: `Pose{position{x,y,z}, orientation{x,y,z,w}}`, graspable,
  held_by). NO geometry — only label + pose. `document_from_text`/`document_text`
  in `src/robot_world/robot_world/store.py` (check exact names).
- `src/robot_world_ros/robot_world_ros/world_query_node.py` — node `world_query`
  (namespace `/world_query`) serving `/world_query/get_world` -> `world_json`
  string (canonical JSON).
- `src/robot_world_ros_interfaces/srv/GetWorld.srv` — `string world_json`.
- Launch: `src/robot_bringup/launch/world.launch.py` (standalone world node).

### Test patterns to follow
- `src/robot_bringup/test/test_world_launch.py` — the canonical launch-based
  pattern: structural LaunchDescription import checks (cheap) + a real
  `ros2 launch` subprocess on a private `ROS_DOMAIN_ID`, probe with a dedicated
  `rclpy.Context` + `SingleThreadedExecutor`, poll service readiness, assert,
  then `os.killpg` teardown. Non-skipping.
- `src/robot_bringup/test/test_mujoco_launch.py` — conditional
  `@pytest.mark.skipif` for the source-built sim dep.
- Test ratchet: `scripts/test_baseline.json` (per-package non-skipped non-linter
  test counts); `pixi run test` raises the floor on new tests — COMMIT the
  updated baseline. New packages are picked up automatically.

## Provisioned dependency facts (execute-verified against installed env)
- MoveIt added to `pixi.toml` (all `ros-jazzy-moveit-*`, 2.12.4): ros-planning,
  ros-planning-interface, ros-move-group, planners-ompl, py, kinematics,
  simple-controller-manager, configs-utils. `pixi install` green.
- `move_group` executable at
  `.pixi/envs/default/lib/moveit_ros_move_group/move_group`.
- KDL IK plugin present: `libmoveit_kdl_kinematics_plugin.so`, plugin class
  `kdl_kinematics_plugin/KDLKinematicsPlugin` (library
  `moveit_kdl_kinematics_plugin`). TRAC-IK ABSENT (the cached-ik plugin xml
  has its `<class>` commented out) — confirmed the D39 fallback-only note.
- OMPL planner plugin: `libmoveit_ompl_interface.so` /
  `libmoveit_ompl_planner_plugin.so`.
- `srdfdom` python module (2.0.7) present — can parse/validate SRDF in tests.
- `moveit-py` present: `moveit/` python package (PlanningSceneInterface etc.).
- `MoveItConfigsBuilder(robot_name, robot_description='robot_description',
  package_name=None)` in `moveit_configs_utils`. Expects a config package
  (default `{robot_name}_moveit_config`, or pass `package_name=`) with
  `config/{robot_name}.srdf`, `config/kinematics.yaml`,
  `config/joint_limits.yaml`, `config/*_planning.yaml` (e.g. ompl_planning.yaml),
  and an optional `.setup_assistant` YAML pointing the URDF
  (`moveit_setup_assistant_config.urdf.{package,relative_path}`) and SRDF
  (`...srdf.relative_path`). Defaults (no .setup_assistant): URDF at
  `config/{robot_name}.urdf`, SRDF at `config/{robot_name}.srdf`.
- `moveit_configs_utils` ships `default_configs/ompl_planning.yaml` template.
- MoveItConfigsBuilder import needs the ament index -> run under a sourced env
  (`AMENT_PREFIX_PATH` set; `pixi run` / `ros2 launch` provides it).

## Likely touch points (owned paths)
- `pixi.toml` (deps added; clean the now-stale TODO comment about moveit).
- NEW `src/robot_moveit_config/` — SRDF + kinematics.yaml + joint_limits.yaml +
  ompl_planning.yaml + `.setup_assistant` + `launch/move_group.launch.py` +
  tests.
- NEW `src/robot_moveit/` — planning-scene bridge node + label->geometry mapping
  + tests.
- `scripts/test_baseline.json` (commit the ratchet bump).
- `docs/design/` NOT touched (decisions.md is append-only; the breakdown doc is
  read-only here).

## Gotchas
- The URDF includes `ros2_control.xacro` (MuJoCo hardware block) and
  `transmissions.xacro`; MoveIt's robot-model loader parses the URDF and ignores
  `<ros2_control>`/`<transmission>` — but VERIFY move_group loads it cleanly
  (non-standard URDF extensions; move_group should tolerate them).
- Mimic joint `<side>_gripper_mirror` is in URDF `<mimic>`; MoveIt reads mimic
  from URDF and applies it — it must NOT be listed in any SRDF planning group.
- `robot.urdf.xacro` has `<xacro:arg name="mujoco_model_path"
  default="/tmp/derived_scene.xml"/>`; it is unused by the control macro, so a
  plain xacro expansion (no arg) is fine.
- Never read STL/mesh contents into context (context hygiene).
