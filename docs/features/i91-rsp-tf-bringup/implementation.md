# PR8a — RSP + TF bringup (issue #91) — implementation

## What shipped
The first *launch* layer: a `robot_bringup` launch file that makes the
assembled robot observable in TF before any sim exists, plus the gate that
proves the *published* tree (not just the model) is complete and correctly
rooted.

- **`launch/robot.launch.py`** — starts `robot_state_publisher` against the
  *installed* `robot_description` (ament index, no source-tree fallback; D27)
  and its standard companion `joint_state_publisher` (see Ruling R6 below).
  The description is expanded at launch time with the xacro CLI via a
  `Command` substitution wrapped in `ParameterValue(value_type=str)` (see
  Ruling R1). A `use_sim_time` launch arg defaults to `false`.
- **`params/robot_state_publisher.yaml`** — carries only RSP's own tuning knobs
  (`publish_frequency`, `use_tf_static`). The description stays in the xacro
  (single source of truth, D27; no hand-maintained XML copy, D24).
- **`package.xml`** — `exec_depend` on `robot_description`,
  `robot_state_publisher`, `joint_state_publisher`, `xacro`, `launch`,
  `launch_ros`, `ament_index_python`; `test_depend` on `ament_index_python`,
  `tf2_ros`.
- **`setup.py`** — globs `launch/*` and `params/*` into the install tree
  (D24: a new launch/params file needs no registration here).
- **`test/test_tf_tree.py`** (2 tests) — imports the installed launch module
  and checks it generates the two promised nodes; and launches the real
  bringup (`ros2 launch robot_bringup robot.launch.py`) and asserts the
  published TF tree is complete, rooted at `base_link`, tree-connected, and
  respects D29's odometry note.
- **`pixi.toml`/`pixi.lock`** — add `ros-jazzy-joint-state-publisher`
  (2.4.1, linux-64 + aarch64).
- **`scripts/test_baseline.json`** — robot_bringup floor auto-raised 0 -> 2
  (D28 ratchet; committed with the change).

## Rulings (recorded in status.md; binding)
- **R1 — description injection.** A bare `Command(...)` in a Node `parameters`
  dict fails launch with "Unable to parse the value of parameter
  robot_description as yaml" because the multi-line xacro output is re-parsed
  as a YAML scalar. Wrapping in `ParameterValue(Command(...), value_type=str)`
  is the required fix. VERIFIED by execution.
- **R6 — joint_state_publisher is required for the brief's own criterion.**
  Without a `/joint_states` source, RSP publishes only the *static* tree
  (fixed-joint frames) and leaves the movable-joint frames (wheels,
  `column_lift`, arm joints) unpublished — `base_left_wheel_link`,
  `left_upper_arm_link`, etc. returned `LookupException` on a live probe.
  Since the brief requires "every link from the URDF present" and "the tree
  connects", the launch adds JSP with an empty `source_list` (reads the URDF's
  initial joint values), which makes the whole tree observable at the home
  config. JSP is RSP's canonical companion and is *not* a controller (those
  are PR8b), so it is in-scope for bringup.
- **R3 / R4** — expected link set is DERIVED from the installed URDF (xacro +
  urdf_parser_py), not hand-typed (D24); params file precedes the
  `robot_description` override in the parameters list.

## Verification (performed by execution, not recall)
- `pixi run build` green.
- `ros2 launch robot_bringup robot.launch.py` under the installed
  `install/setup.bash` starts RSP ("Robot initialized") and JSP ("Got
  description, configuring robot") with **no** source-tree fallback.
- Live TF read (`tf2_ros` Buffer): all **32/32** URDF links present with the
  correct parent; single root `base_link`; `base_footprint` is a child of
  `base_link` (D29), never the root; no orphan frames; no extra frames.
- `colcon test --packages-select robot_bringup` green (5/5).
- Full `pixi run test` green: 828 tests, 0 failures; the test-integrity audit
  passes with `robot_bringup` floor 0 -> 2 auto-raised.

## Stale-build gotcha (recorded)
During rapid iterative editing of `package.xml` + rebuilds, colcon
intermittently produced a `robot_bringup` `package.dsv` WITHOUT the
`ament_prefix_path` hook, dropping robot_bringup from `AMENT_PREFIX_PATH` and
breaking `ros2 launch robot_bringup ...`. A clean
`rm -rf build/<pkg> install/<pkg>` + rebuild restores it. Final state verified
working. If it reappears in CI, clean-rebuild robot_bringup first.

## Out of scope (confirmed not touched)
`mujoco_ros2_control` / controllers / transmissions (PR8b, #92); Foxglove
bridge + viewer (deferred); MuJoCo `RobotBackend` (#4); MoveIt/Nav2;
perception.
