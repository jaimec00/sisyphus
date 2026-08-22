# PR8b — MuJoCo sim bringup (issue #92) — worktree manager status

Branch `feat/i92-pr8b-mujoco-sim-bringup`, worktree
`~/worktrees/pr8b-mujoco-sim-bringup` on laptop node `olivia`, from `origin/main`
@ `669fd1e` (PR8a #93 merged).

## Brief
Spawn the robot in MuJoCo under `mujoco_ros2_control` so its joints are
commandable through the standard ROS 2 control stack (the seam roadmap #4's
MuJoCo `RobotBackend` will drive). Transmissions + `<ros2_control>` tags in the
URDF; `mujoco_ros2_control` source-built via `robot.repos`; controller YAML +
`joint_state_broadcaster` + position/velocity controllers; launch in
`robot_bringup`. Acceptance: sim spawns, ros2_control loads, joints
commandable, one `mj_step` smoke no NaN, full `pixi run test` green with a
ratchet floor that ratchets up.

## Rulings (binding; downstream agents escalate in-process rather than deviate)
All rulings below are PROBED against the real source / installed packages on
node `olivia`, not recalled API.

### R-PR8b-1 — D15 RESOLVED: mujoco_ros2_control IS on RoboStack but UNUSABLE with our mujoco pin → MUST source-build (probed)
- `pixi search ros-jazzy-mujoco-ros2-control` (robostack-jazzy channel) **DOES
  return 0.0.3** (linux-64 + aarch64), as do `-msgs` 0.0.3 and `-plugins` 0.0.3.
  D15's "verify RoboStack coverage" TODO is therefore answered: coverage exists.
- **BUT the prebuilt 0.0.3 cannot be installed** in this env: adding it fails the
  solver because `ros-jazzy-mujoco-ros2-control-plugins 0.0.3` → `mujoco_vendor`
  → `libmujoco <3.8.2` (0.0.8) or `<3.5.1` (0.0.6), which **conflicts with our
  `mujoco >=3.12.0,<4`** pin (PR7's `load_mjcf_model()` needs the MjSpec /
  URDF-import APIs of 3.12.0). So the channel package forces a downgrade we
  cannot take.
- **Ruling: source-build the mujoco_ros2_control stack** from `robot.repos`
  (ros-controls org), pinned at exact commits, linked against the SAME conda
  mujoco 3.12.0 the Python derivation uses — one mujoco lib for both the sim
  (C++) and `load_mjcf_model()` (Python). `AMENT_VENDOR_POLICY=NEVER_VENDOR`
  is the mechanism (see R-PR8b-3).

### R-PR8b-2 — Pin & repo layout (robot.repos)
- `mujoco_ros2_control` → `https://github.com/ros-controls/mujoco_ros2_control.git`
  @ **`57fc6744844902d4532160b403fa95840c1d6f96`** (main, 0.1.0). This revision
  has full transmissions (`get_joint_actuator_names`, `register_transmissions`),
  ros2_control mimic-joint support, and the position/velocity/effort command↔
  actuator-type matrix (verified in source).
- `mujoco_vendor` → `https://github.com/pal-robotics/mujoco_vendor.git`
  @ **`ff9e648e1af418c555c37cb6b4fcc42885057421`** (0.0.9). This is the
  canonical vendor shim that, with `AMENT_VENDOR_POLICY`, exports the *system*
  (conda) `mujoco` package instead of vendoring its own old libmujoco.
- `mujoco_ros2_control_msgs` and `mujoco_ros2_control_plugins` are packages in
  the SAME mujoco_ros2_control repo, imported via the same repos entry.
- The core package's CMake does `find_package(mujoco_ros2_control_plugins
  REQUIRED)`, so the plugins package is required (it also bundles the
  `mujoco_3d_lidar_plugin`; the separate `mujoco_3d_lidar` extension package is
  NOT needed).

### R-PR8b-3 — Amending the brief: source-build is REQUIRED, not conditional
The brief said "source-build in the pixi env if it's not on the channel". The
probe shows it IS on the channel but unusable due to the mujoco version clash
(R-PR8b-1), so source-build triggers for a different, concrete reason. Recorded
here so the implementer does not spend time trying to install the channel
package.

### R-PR8b-4 — Build mechanism: `AMENT_VENDOR_POLICY=NEVER_VENDOR` + conda mujoco 3.12.0
- `mujoco_vendor/CMakeLists.txt` (0.0.9) has an explicit branch: if
  `AMENT_VENDOR_POLICY` is `NEVER_VENDOR` (or `NEVER_VENDOR_IGNORE_SATISFIED_CHECK`)
  it runs `ament_export_dependencies(mujoco)` and returns — i.e. it uses the
  *system/conda* `mujoco` package instead of `FetchContent`-downloading mujoco
  3.4.0. This is the same path RoboStack uses for its channel builds.
- Conda `mujoco` 3.12.0 ships everything the plugin needs:
  - CMake package at `<prefix>/lib/cmake/mujoco/mujocoConfig.cmake` exporting
    **`mujoco::mujoco` and `mujoco::libmujoco_simulate`** (verified).
  - Headers at `<prefix>/include/mujoco/` **and** the simulate app at
    `include/simulate/simulate.h` (verified) → `MUJOCO_SIMULATE_DIR` in
    mujoco_ros2_control's CMake resolves, no source-compiled simulate needed.
- `mujoco_ros2_control`'s CMake resolves `MUJOCO_ROOT` via a fallback that
  lands on the conda prefix when the vendored `opt/mujoco_vendor` dir isn't
  present (probe of CMakeLists: `if(NOT EXISTS <root>/opt/mujoco_vendor/...)
  set(MUJOCO_ROOT ${MUJOCO_PREFIX})`). Under NEVER_VENDOR the vendored dir is
  absent, so MUJOCO_ROOT → conda prefix.
- Build command (implementer, in-worktree): set `AMENT_VENDOR_POLICY=NEVER_VENDOR`
  for the colcon build, e.g. `AMENT_VENDOR_POLICY=NEVER_VENDOR pixi run build`.
  The implementer must verify by EXECUTION that the plugin resolves
  `mujoco::mujoco` to 3.12.0 and that `ros2 control list_hardware_interfaces`
  / plugin loading works. If the CMake path resolution needs a small tweak
  (e.g. MUJOCO_ROOT explicitly pointed at the conda prefix), the implementer
  may set it via the colcon env/`CMAKE_PREFIX_PATH`, but must record whatever
  was actually needed. **Probe-first: run the build, don't assume the fallback
  path works.**

### R-PR8b-5 — How the plugin consumes the model (the seam the RobotBackend will drive)
VERIFIED from source (`mujoco_system_interface.cpp`):
- The plugin loads the sim model from a file at the **`mujoco_model`**
  hardware param (path), or a topic (`mujoco_model_topic`). **It does NOT run
  PR7's `load_mjcf_model()`** — it loads an MJCF file. **Reconciliation: our
  `robot_bringup` launch must hand the plugin a concrete MJCF file.**
- It maps ros2_control `<joint>` (from the URDF `<ros2_control>` tag) ↔ MJCF
  actuators **by name**, optionally through `<transmission>` tags: a
  transmission maps a joint to named actuators; without one, the fallback is a
  1:1 match on the MJCF actuator's target joint name.
- Command interface ↔ required MJCF actuator type (verified matrix):
  - `position` → MJCF `<position>` actuator (native) [or `<velocity>`/`<motor>`
    + position PID]
  - `velocity` → MJCF `<velocity>`/`<intvelocity>` actuator (native) [or
    `<motor>` + velocity PID]
  - `effort` → MJCF `<motor>`/`<general>` (native)
- Mimic joints: a ros2_control joint with a `mimic` param is treated as a
  mimic — its `command_interfaces` are CLEARED (not commandable) and its
  command/state are derived from the mimicked joint. A MuJoCo joint with NO
  actuator that is a mimic joint is registered passive.

### R-PR8b-6 — THE RECONCILIATION: PR7's 18 overlay motors → typed+named actuators, 16 commandable (recorded divergence)
**Probed facts that drive this:**
- MuJoCo's URDF import does **NOT** turn `<mimic>` into an equality constraint:
  compiled model has `neq == 0`; `left_gripper_mirror` / `right_gripper_mirror`
  are **independent DOFs** (nq=nv=18 includes them).
- PR7's overlay adds 18 **unnamed** `<motor>` actuators (verified: `name=None`,
  all target joints, dyn/gain/bias all MOTOR-type) => `nu == 18`.
- The plugin matches actuators by **name**, so unnamed overlay actuators rely on
  the 1:1 target-joint-name fallback — this is why the brief says "reconcile".

**Ruling (what the MJCF overlay becomes):** Replace the 18 placeholder
`<motor>`s with **16 named, correctly-typed actuators**, and add 2 equality
constraints for the gripper mimics:
- **3 wheels** → `<intvelocity name="base_left_wheel" joint="base_left_wheel"/>`
  etc. (native **velocity** command).
- **column_lift** → `<position name="column_lift" joint="column_lift" .../>`
  (**position** command).
- **10 arm joints** (`left/right_shoulder_pan/lift, elbow_flex, wrist_flex,
  wrist_roll`) → `<position name="<joint>" joint="<joint>" .../>`.
- **2 driven grippers** (`left_gripper`, `right_gripper`) → `<position .../>`.
- **2 mirror grippers get NO independent actuator**; instead an `<equality>`
  block links each driven↔mirror pair (polycoef `0 -1`, matching the URDF's
  `<mimic multiplier="-1">`) so the gripper physically anti-follows. The
  driver joint's state is observable; the mirror joint reports via the
  equality constraint.
- Result: **`nq=nv=18` (unchanged; both gripper DOFs remain), `nu=16`,
  `neq=2`.** This is a **recorded, deliberate divergence** from PR7's `nu=18`
  (which asserted 18 per-joint `<motor>`s). PR7's `test_mjcf_model.py` asserts
  `nu == 18`; it must be **updated** to `nu == 16`/`neq == 2` with a comment
  pointing at this ruling. The install-side definition lives in the URDF
  `<ros2_control>` + `<transmission>` tags (fixes D15 TODO note).
- Contact defaults + head camera blocks from PR7's overlay are **kept
  unchanged** (they are orthogonal to actuation).
- The overlay is the ONLY hand-authored MJCF text; the loader
  (`mjcf_model.py`) is adapted to emit typed actuators + equality, still
  splicing from the overlay fragment (extend `overlay.xml`, do not check in a
  generated MJCF).

### R-PR8b-7 — Transmissions in the URDF (the ros2_control convention the brief asks for)
- Add one **`<transmission_interface/SimpleTransmission>`** per commanded
  joint (16), wired joint→actuator 1:1 with `mechanical_reduction=1.0`
  (direct-drive; the URDF/MJCF joint IS the STS3215 output). Named
  `sts3215_<joint_name>`. This is the single "STS3215 transmission type"
  instantiated across the actuated joints.
- The 2 mirror joints have **no transmission** (no actuator).
- The `<ros2_control name="MujocoSystem" type="system">` block declares the
  hardware plugin `mujoco_ros2_control/MujocoSystemInterface` + the
  `mujoco_model` param (file path), and one `<joint>` entry per commanded
  joint with the matching `command_interface` (`velocity` for wheels,
  `position` for column/arm/driven-grippers) + `position`/`velocity`/`effort`
  `state_interface`s. The 2 mirror joints get the `mimic` param (and NO command
  interface).
- These tags live in the xacro (URDF = source of truth, D27). Recommended home:
  a new `urdf/ros2_control.xacro` (and `urdf/transmissions.xacro`) included from
  `robot.urdf.xacro`, mirroring how the subassemblies are pulled in.

### R-PR8b-8 — Scope-in: free base joint so "the robot moves in sim" (recorded)
- The derived MJCF welds the base into the worldbody (R-PR7-5: fusestatic folds
  the static trunk into world; nq=18 has NO free base DOF) → the base cannot
  translate; wheels would only spin in place. The brief's goal ("the robot
  moves in sim") requires a free/floating base so the 3-wheel velocity control
  is meaningful.
- **Ruling: add a minimal free base joint in the MJCF overlay** (sim-only, NOT
  in the URDF TF; consistent with PR7's "overlay carries sim-only physics").
  Configure the plugin's `odom_free_joint_name` accordingly. Keep it minimal —
  this is bringup, not the RobotBackend (roadmap #4). The implementer must
  probe how to add the free joint (unfuse base_link, or add a free joint
  before the root via the overlay) and record the exact mechanism + resulting
  nq/nv/body counts.

### R-PR8b-9 — Controller config + launch (canonical mujoco_ros2_control pattern)
- Controller YAML follows the demos' `controllers.yaml`: `controller_manager`
  (update_rate), `joint_state_broadcaster`, a **`position` forward-command
  controller** (via `forward_command_controller/ForwardCommandController`,
  interface `position`) covering column + arm + driven-grippers, and a
  **`velocity` forward-command controller** covering the 3 wheels.
- Launch (`robot_bringup`, extends PR8a's `robot.launch.py` or a new
  `mujoco.launch.py`): RSP + `mujoco_ros2_control`'s `ros2_control_node`
  (+ `use_sim_time:true` + `ParameterFile(controllers.yaml)`) + controller
  spawners. Provide the `mujoco_model` file deterministically (the launch
  must expand `load_mjcf_model()`-equivalent output to a file, or point at the
  installed overlay-derived MJCF — probe the cleanest path; the acceptance
  requires "sim spawns, ros2_control loads" via an actual launch).
- Headless mode default for tests (`headless:=true`); no Foxglove/visualizer
  (deferred per brief).

### R-PR8b-10 — Tests + ratchet
- New tests must cover: (a) the derived MJCF is loadable, `nq=nv=18`, `nu=16`,
  `neq=2`, one `mj_step` no NaN (extend `robot_description/test/test_mjcf_model.py`);
  (b) the URDF expands with viable `<ros2_control>` + transmissions (xacro →
  parses, 18 joint tags, 2 mimic, 16 transmissions);
  (c) `robot_bringup` launch generates the promised nodes (RSP, ros2_control
  node, spawners) — extend `test_tf_tree.py`-style launch test; and ideally a
  runtime smoke that `ros2_control` loads the MuJoCo hardware interface and a
  joint command moves the sim state (probe how far a headless test can go; a
  full launch+runtime smoke is ideal but must be reliable in this env).
- `scripts/test_baseline.json` floor for `robot_bringup` (currently 2) and
  `robot_description` (currently 48) **ratchets UP** with the change. Committed
  with the PR. Test-count floor rises, never falls.

## Out of scope (do not implement)
MuJoCo `RobotBackend` (D9/roadmap #4), MoveIt/Nav2, perception, RGB-D render,
real STS3215 gains + contact/friction tuning, Foxglove bridge/viewer.

## Build order (implementer)
1. Probe build path in-worktree: colcon with the ros2_control deps installed
   (done above), source-import mujoco_ros2_control + mujoco_vendor via
   `robot.repos` (populate the `repositories:` block), set
   `AMENT_VENDOR_POLICY=NEVER_VENDOR`, build, verify plugin loads against mujoco
   3.12.0. Record the URL/commit, any pixi env additions (`ros-jazzy-*` deps
   already added in R-PR8b-1), and any CMake env adjustments.
2. Author overlay/URDF/l launch/config per R-PR8b-6/7/8/9.
3. Run code, red-team, fix loop, then full `pixi run test` (N+1 rule).

## Escalation policy
Manager disputes these rulings in-process (via the implementer) per AGENTS.md;
silent deviation is not allowed. No fork so far requiring the caller — the
free-base-joint (R-PR8b-8) is a recorded scoped-in item, not a fork.
