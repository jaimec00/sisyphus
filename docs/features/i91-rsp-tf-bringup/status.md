# PR8a — RSP + TF bringup (issue #91) — worktree manager status

## Brief
First launch layer: a `robot_bringup` launch file that starts
`robot_state_publisher` against the installed `robot_description`, publishing
the full TF tree (D27/D24: ament-index installed copy, no source-tree
fallback). Test asserts the published TF tree is complete, correctly rooted
(base_link root), tree-connected, and respects D29's odometry note
(odom -> base_link, never odom -> base_footprint).

## Rulings (binding; downstream agents escalate in-process rather than deviate)
- **R1 — robot_description injection**: the launch file injects the description
  as `ParameterValue(Command(['xacro ', <ament-resolved path>]), value_type=str)`.
  A bare `Command` in a param dict fails launch with "Unable to parse the value
  of parameter robot_description as yaml" (the multi-line xacro output is
  re-parsed as YAML); `ParameterValue(value_type=str)` is the required fix.
  VERIFIED by execution (15-s `ros2 launch` probe).
- **R2 — params file content**: params/robot_state_publisher.yaml holds ONLY
  RSP tuning knobs (publish_frequency, use_tf_static). The description stays in
  the xacro (D27 single-source-of-truth); keeping XML out of the params file
  avoids a hand-maintained copy (D24).
- **R3 — expected link set is DERIVED, not typed**: the test expands the
  INSTALLED robot.urdf.xacro (via get_package_share_directory + installed xacro
  CLI) and parses it with urdf_parser_py to obtain the expected link set. No
  hard-coded list (D24). Single root asserted == base_link.
- **R4 — dict YAML key ordering**: use params_file first, then the
  robot_description override, so the file supplies defaults and the Command
  overrides description (order matters for rcl param precedence).
- **R5 — test drives the REAL launch file**: test launches RSP via
  `ros2 launch robot_bringup robot.launch.py` (installed) as a subprocess, then
  reads TF with a tf2_ros buffer. This exercises the shipped launch end-to-end
  (acceptance criterion "launch file loads and RSP publishes TF"). Tear-down by
  process group (D29 lesson). No launch_testing (plugin incompatible with
  pytest>=8; pytest.ini disables it).

## Verification already performed (API probes, not recall)
- `pixi run build` green after adding launch/params.
- `ros2 launch robot_bringup robot.launch.py` -> RSP logs "Robot initialized".
- tf2_ros Buffer reads all_frames_as_string()/as_yaml(); URDF has 32 links,
  single root base_link.
- Domain 91 used for probes; tests must use a private domain.

## Additional discovery during probing (recorded)
- **Stale-build gotcha**: during rapid iterative editing of package.xml + rebuilds,
  colcon intermittently produced a robot_bringup `package.dsv` WITHOUT the
  `ament_prefix_path` hook, dropping robot_bringup from AMENT_PREFIX_PATH and
  breaking `ros2 launch robot_bringup ...` ("Package 'robot_bringup' not
  found"). A clean `rm -rf build/<pkg> install/<pkg>` + rebuild restores it.
  CONFIRMED unblocked after clean rebuild; final package.xml works. If the
  anomaly ever reappears in CI, clean-rebuild robot_bringup first.

## Ruling R6 (added during probing) — joint_state_publisher is required
Without a /joint_states source RSP published only the static tree; the
movable-joint frames (wheels, column_lift, arm joints) returned LookupException
on a live probe, so the brief's "every link present / tree connects" could not
hold. The launch therefore adds joint_state_publisher (empty source_list = URDF
initial values), which is RSP's canonical companion and NOT a controller (PR8b).
pixi.toml/pixi.lock add ros-jazzy-joint-state-publisher 2.4.1 (both arches);
package.xml exec_depend joint_state_publisher. VERIFIED: all 32/32 links
published after the change.
