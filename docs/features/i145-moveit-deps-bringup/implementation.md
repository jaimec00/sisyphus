# implementation.md — PR145 (issue #145) MoveIt deps + move_group bringup + planning scene

Foundation PR (PR1 of the MoveIt roadmap). Adds the MoveIt 2 deps, stands
`move_group` up headless against a hand-authored arm SRDF, and feeds the
planning scene from `robot_world`. **No arm motion** (PR2), **no pick-and-place**
(PR3). All manager rulings R1–R10 were followed; deviations are flagged below.

## What shipped

| Path | What |
| --- | --- |
| `pixi.toml` | 8 × `ros-jazzy-moveit-*` (2.12.4) pinned; stale "unverified" TODO block replaced with a D39 note. |
| `src/robot_moveit_config/` | NEW ament_python package: `config/sisyphus.srdf`, `kinematics.yaml`, `joint_limits.yaml`, `ompl_planning.yaml`, `.setup_assistant`, `launch/move_group.launch.py`, 6 tests. |
| `src/robot_moveit/` | NEW ament_python package: `planning_scene_bridge` node, `scene_geometry.py` (R5 table), `world_to_scene.py` (pure transform), `launch/planning_scene_bridge.launch.py`, 10 tests. |
| `scripts/test_baseline.json` | `robot_moveit_config: 5`, `robot_moveit: 7` (non-linter). |

## Acceptance criteria

1. **`pixi install` + full `pixi run build` green** — 18 packages build, including
   the two new ones. VERIFIED.
2. **`move_group` starts headless; the arm planning group loads from the SRDF** —
   VERIFIED by `test_move_group_launch.py::test_move_group_starts_and_loads_arm_groups`:
   `robot_model_name == "sisyphus"`, and each R2 group is recognised by
   `/compute_ik` (NO_IK_SOLUTION, not INVALID_GROUP_NAME). The URDF names no
   groups, so a recognised group can only have come through the SRDF.
3. **The planning scene contains the world's objects** — VERIFIED by
   `test_planning_scene_bridge_e2e.py::test_seed_objects_appear_in_the_planning_scene`:
   launches world_query + move_group + bridge, polls `/get_planning_scene`
   until all seven seed object ids appear with the R5 primitive kinds.

## Design decisions and tradeoffs

### SRDF authored by chain, not by explicit joint lists (R2)
Each per-side group is a `<chain>` (`s_shoulder_link` → `s_gripper_base_link`),
so the fixed `s_gripper_mount_joint` is inside the chain automatically and the
group's joints cannot drift from the URDF's chain. The joint *sets* are asserted
against the expanded URDF in `test_srdf.py`. A PiPER swap is a macro swap (D26):
rename the macro instances, and the same SRDF names still resolve.

### Mimic joint excluded everywhere (R3)
`s_gripper_mirror` appears in no group and no `<passive_joint>`; MoveIt reads the
`<mimic>` from the URDF and propagates it. The unit test asserts the exclusion
*and* that the mimic joint exists in the URDF (so the exclusion is not vacuous).
NOTE: the mimic joint *does* appear in the model's DOF/`joint_state` list that
`/get_planning_scene` returns (MoveIt keeps passive joints in the model); the
earlier draft asserted its absence there and was wrong. R3 is about group
membership, which the SRDF unit test proves.

### IK plugin: KDL only (R7)
`kinematics.yaml` declares `kdl_kinematics_plugin/KDLKinematicsPlugin` for
`left_arm`/`right_arm`; verified present as `libmoveit_kdl_kinematics_plugin.so`.
TRAC-IK is absent on the channel (D39) and is noted in the file as the fallback
if KDL convergence proves inadequate in PR2/PR3.

### DEVIATION 1 (R6): `/apply_planning_scene` service, not moveit-py `PlanningSceneInterface`
R6 says "via moveit-py's `PlanningSceneInterface`". The installed
`ros-jazzy-moveit-py` 2.12.4 does **not** expose that Python class: `moveit.planning`
offers `MoveItPy`, `PlanningComponent`, `PlanningSceneMonitor`, and the locked-scene
context managers; the `planning_scene_interface` headers ship as C++ only
(VERIFIED by inspecting `moveit/planning.pyi`, `moveit/core/*.pyi`, and the stub
surface). The equivalent operation with the `MoveItPy` binding is
`PlanningSceneMonitor.process_collision_object`, but constructing a `MoveItPy`
loads a *second* full moveit_cpp stack (its own robot model and planning scene)
rather than talking to the `move_group` this PR stands up — heavier, and coupled
to a second model load. The bridge therefore applies a diff `PlanningScene`
through `/apply_planning_scene` (`moveit_msgs/srv/ApplyPlanningScene`), which is
exactly the operation `PlanningSceneInterface::applyCollisionObjects` wraps. The
*effect* R6 asks for is unchanged. Flagged, not silently substituted.

### DEVIATION 2 (not in a ruling): a static `world` → `base_link` TF in the bridge launch
The bridge labels objects with frame `world`. `move_group` rejects any collision
object in a frame it cannot resolve ("Unknown frame: world" — VERIFIED by
probe). So the bridge launch publishes a static identity `world` → `base_link`
transform. At the shipped start pose this is exactly right: the seed's
`start_location: charger` is the (0,0,0) location, so the robot's `base_link`
coincides with `world`. A later PR replaces this with the nav stack's dynamic
`map`→`base_link` once the base moves; until then it is the honest "robot at
origin" statement. Without it, criterion 3 cannot hold.

### Pipelines pinned to OMPL
`MoveItConfigsBuilder.planning_pipelines()` defaults to merging **every** pipeline
template on the channel (OMPL, CHOMP, STOMP, Pilz); the Pilz template then demands
a `pilz_cartesian_limits.yaml` this package does not ship, which fails the launch
outright (VERIFIED: first launch attempt died with `ParameterBuilderFileNotFoundError`
for that file). The launch pins `planning_pipelines(pipelines=['ompl'])`.

### Unknown label → default box + WARN (R5)
`spec_for_label` returns a 5 cm box for any label the table does not know; the
node logs the fallback once per label. A new world label must not require a code
change. All table entries carry `estimated=True` so a reviewer can see at a
glance that every number is a guess.

## ESTIMATED numbers (R5 and joint limits) — a guess, not a datasheet number

All geometric dimensions below are ESTIMATED eyeballs of typical household
objects (D29 style). The joint velocity/acceleration figures carry the URDF's own
ESTIMATED STS3215-class stand-in (3.5 rad/s, arm.xacro).

| label | shape | dimensions (m) | ESTIMATED |
| --- | --- | --- | --- |
| mug | cylinder | r=0.04, h=0.10 | yes — typical ceramic mug |
| cup | cylinder | r=0.035, h=0.09 | yes — typical cup |
| plate | cylinder | r=0.12, h=0.01 | yes — dinner plate |
| bowl | cylinder | r=0.08, h=0.05 | yes — cereal bowl |
| book | box | 0.20 × 0.14 × 0.03 | yes — closed hardback |
| remote | box | 0.18 × 0.05 × 0.02 | yes — handheld remote |
| counter | box | 0.60 × 0.60 × 0.90 | yes — counter section |
| sofa | box | 1.80 × 0.80 × 0.40 | yes — 2-seat sofa |
| table | box | 0.80 × 0.80 × 0.40 | yes — small coffee table |
| chair | box | 0.45 × 0.45 × 0.90 | yes — dining chair |
| shelf | box | 1.20 × 0.40 × 0.75 | yes — shelf unit |
| cabinet | box | 0.35 × 0.35 × 1.70 | yes — tall cabinet |
| bin | box | 0.25 × 0.35 × 0.30 | yes — trash bin |
| drum | cylinder | r=0.20, h=0.30 | yes — storage drum |
| *unknown* | box | 0.05 × 0.05 × 0.05 | yes — default fallback |

Joint limits (`joint_limits.yaml`): max_velocity 3.5 rad/s for all 10 arm
joints, max_acceleration 3.5 rad/s² (ESTIMATED, carried from the URDF's own
stand-in; owed a real actuator model in PR6/PR7). `has_jerk_limits: false`
everywhere.

## Test coverage (R10)

- `robot_moveit_config` (non-linter 5): SRDF structure vs. R2/R3/R4; SRDF joint
  sets vs. the expanded URDF; SRDF↔URDF name drift; launch structural check;
  `move_group` headless + groups loaded via `/compute_ik`.
- `robot_moveit` (non-linter 7): seed transform → CollisionObjects; unknown-label
  fallback; every table label builds a valid primitive; `diff_scene` removals;
  JSON round-trip; bridge launch structure; e2e objects-arrive poll.

Both new packages pass ament copyright/flake8/pep257.

## Open items / notes for the red-team

- DEVIATION 1 and DEVIATION 2 above are the two things to attack first.
- `test_planning_scene_bridge_e2e` does **not** re-assert object *positions* from
  the scene: `GetPlanningScene` reports accepted objects transformed into
  `base_link` with the object-frame pose flattened, so a world position is not a
  faithful round trip through the service. The exact pose copy is proven in the
  unit test instead; the e2e asserts id arrival + primitive kind. This is a
  deliberate limitation, stated so it is not mistaken for an oversight.
- The static TF is identity, which is only correct at the start pose; a moving
  base would make it wrong. That is the one place this PR is explicitly a
  foundation rather than a finished feature.
