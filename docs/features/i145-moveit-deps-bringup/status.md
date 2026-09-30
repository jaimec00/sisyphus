# status.md — manager rulings (PR145)

Authoritative rulings on the open questions. Binding for the implementer; the
red-team treats each as a review target to DISPROVE empirically.

## R1 — Package layout: two new packages
- `robot_moveit_config` (ament_python, data + launch + tests): the SRDF +
  MoveIt config (`config/sisyphus.srdf`, `config/kinematics.yaml`,
  `config/joint_limits.yaml`, `config/ompl_planning.yaml`,
  `.setup_assistant`) + `launch/move_group.launch.py` (headless move_group).
- `robot_moveit` (ament_python, node + tests): the planning-scene bridge node
  (world -> CollisionObject) + the label->geometry table + tests.
Rationale: the config is what a PiPER macro swap touches; the bridge node is
arm-agnostic and stays put. Matches MoveIt's config/runtime convention.

## R2 — Planning groups (per side, mirroring the xacro `${name}` naming)
SRDF `<robot name="sisyphus">`. For each side `s` in {left, right}:
- Group `s_arm` — chain base_link=`s_shoulder_link`, tip_link=`s_gripper_base_link`
  (5 revolute joints: `s_shoulder_pan, s_shoulder_lift, s_elbow_flex,
  s_wrist_flex, s_wrist_roll`; the fixed `s_gripper_mount_joint` sits in the
  chain between `s_wrist_roll_link` and `s_gripper_base_link`).
- Group `s_gripper` — chain base_link=`s_gripper_base_link`,
  tip_link=`s_gripper_upper_jaw_link` (1 driven joint: `s_gripper`).
- End effector name `s_eef`: parent_link=`s_gripper_base_link`,
  group=`s_gripper`, parent_group=`s_arm`.
Tip_link == parent_link == the gripper frame (`s_gripper_base_link`) is what
"end-effector = gripper frame" means; the grasp midpoint (a derived point, not a
link) is deferred to PR2/PR3 if a dedicated tool frame is ever needed.

## R3 — Mimic joint excluded from groups
`s_gripper_mirror` is a URDF `<mimic joint="s_gripper" multiplier="-1"/>`. It
must NOT appear in any SRDF group or `<passive_joint>`. MoveIt reads the mimic
from URDF and applies it. VERIFY move_group loads the robot model with the
mimic joint present (red-team empirical check).

## R4 — End-effector link
End-effector parent_link = `s_gripper_base_link` (the gripper mount frame,
identity-mounted on `s_wrist_roll_link`). The fingertip tip frames and the
grasp midpoint are NOT end-effector frames in this PR.

## R5 — Collision geometry derived from label (ESTIMATED)
`robot_world` carries only `label` + `pose` (no geometry). The bridge maps
label -> primitive shape + dimensions via a small ESTIMATED table (documented
as such, D29 style — "a guess, not a datasheet number"). Minimum coverage for
the shipped seed objects: mug, plate, bowl, cup, book, counter, sofa, remote.
Unknown label -> default box (0.05 m cube) + a WARN log, never a crash.
Suggested shapes (implementer may refine, but must document every number as
estimated): mug=cylinder r0.04 h0.10; cup=cylinder r0.035 h0.09;
plate=cylinder r0.12 h0.01; bowl=cylinder r0.08 h0.05; book=box 0.20x0.14x0.03;
remote=box 0.18x0.05x0.02; counter=box 0.60x0.60x0.90; sofa=box 1.80x0.80x0.40.

## R6 — Planning-scene bridge mechanism
New node `planning_scene_bridge` in `robot_moveit`: calls `/world_query/get_world`
(service), parses via `robot_world` (`document_from_text`), and for each object
adds a `moveit_msgs/CollisionObject` (id = object_id, primitive from R5, pose
from the object pose) to the planning scene via moveit-py's
`PlanningSceneInterface`. The pure transform (WorldDocument -> list of
CollisionObject) is a separate importable function so it is unit-testable
without a graph. D30/D35 held: the bridge reads the world through the service,
never imports robot_world into the brain backend, and never becomes a second
source of truth.

## R7 — IK solver
KDL only: `kinematics.yaml` declares
`kinematics_solver: kdl_kinematics_plugin/KDLKinematicsPlugin` for `left_arm`
and `right_arm` (verified class name). TRAC-IK is NOT source-built this PR; the
`kinematics.yaml` may carry a comment noting it as the fallback if KDL
convergence proves inadequate.

## R8 — move_group bringup
`launch/move_group.launch.py` uses `MoveItConfigsBuilder("sisyphus",
package_name="robot_moveit_config")` to load the URDF (from robot_description
via `.setup_assistant`), SRDF, kinematics, joint_limits and ompl planning
pipeline, then starts the `move_group` node headless. `.setup_assistant`
points `urdf.package=robot_description`, `urdf.relative_path=urdf/robot.urdf.xacro`,
`srdf.relative_path=config/sisyphus.srdf`.

## R9 — setup-assistant NOT a runtime dep
`ros-jazzy-moveit-setup-assistant` (GUI) is NOT added to pixi.toml; the SRDF is
hand-authored (small, 5-DOF arm). Noted as a deviation from the brief's
"Needed" list, justified by "add what's actually required" (headless bringup +
hand-authored SRDF needs no GUI generator).

## R10 — Test strategy
1. (robot_moveit_config) Unit: parse `config/sisyphus.srdf` (srdfdom) and assert
   the groups/joints/end-effectors from R2 exist; cross-check every SRDF
   link/joint name against the xacro-expanded URDF so SRDF/URDF cannot drift.
2. (robot_moveit_config) Launch: start `move_group.launch.py` headless, wait for
   `/get_planning_scene` (moveit_msgs/GetPlanningScene), assert it responds and
   `robot_model_name == "sisyphus"`.
3. (robot_moveit) Unit: `WorldDocument -> [CollisionObject]` for the seed world
   (ids + primitives + poses match).
4. (robot_moveit) Launch e2e: world_query + move_group + bridge; poll
   `/get_planning_scene` until the seed objects appear as collision objects.
All non-skipping; follow `test_world_launch.py`'s private-domain + killpg
pattern. Commit `scripts/test_baseline.json` when the floor rises.

## Red-team verdict (i145-redteam, 2026-09-30) — 2 BLOCKs, 2 NOTEs
- Acceptance #1 (move_group headless + SRDF group loads) PASS (VERIFIED); #2 (planning_scene_bridge populates scene) PASS (VERIFIED).
- CPU busy-wait + teardown-leak: NOT reproduced (monitoring artifacts, not node defects).
- **BLOCK 1 (VERIFIED):** OMPL planner config names in `config/ompl_planning.yaml` (`RRTConnectkConfigDefault` / `RRTstarkConfigDefault` / `PRMkConfigDefault`) do not exist — the merged `ompl_defaults.yaml` defines them WITHOUT the `kConfigDefault` suffix (`RRTConnect`/`RRTstar`/`PRM`). `move_group` errors on every launch; `/plan_kinematic_path` → error 99999, 0 points. No test asserts planner availability, so suite stays green.
- **BLOCK 2 (VERIFIED):** SRDF ships no `<disable_collisions>`; neutral (all-zero) pose self-collides (8 contacts, incl column_rail_link-column_top) → `CheckStartStateCollision` rejects every plan.
- NOTE: `future.result()` re-raises on exception (timeout guard only covers hang). NOTE: e2e doesn't assert planner availability.

## Fix rulings (Sisyphus driving the loop directly — manager died)
- R-fix1: make ompl_planning.yaml planner names consistent with the shipped ompl_defaults.yaml (probe it first; rename references OR ship a consistent ompl_defaults.yaml). Verify /get_planner_params non-empty + /plan_kinematic_path no longer fails on missing-planner.
- R-fix2: add <disable_collisions> to sisyphus.srdf for the self-colliding pairs + standard adjacent pairs. Verify /check_state_validity neutral pose → valid.
- R-fix3: add a regression assertion that the default_planner_config name resolves on the param server.
