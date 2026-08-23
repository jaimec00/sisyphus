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

### R-PR8b-4 — Build mechanism: VENDORED mujoco (REVISED after execution probe)
**REVISED 2026-08-22 in-process:** the original NEVER_VENDOR / unified-3.12.0
ruling was PROBED by execution and is **superseded**. Evidence:
- `-DAMENT_VENDOR_POLICY=NEVER_VENDOR` **does** make mujoco_vendor unvendor and
  export the conda `mujoco` package, and conda mujoco 3.12.0 ships the CMake
  targets + headers + simulate app.
- **BUT the mujoco_ros2_control source is NOT compatible with mujoco 3.12.0
  headers:** `mujoco_extensions/mujoco_3d_lidar/include/mujoco_3d_lidar/3dlidar.h`
  does `#include <mujoco/mjtnum.h>`, and **`mjtnum.h` does not exist in the
  conda mujoco 3.12.0 header set** (verified: `include/mujoco/` has
  ijmdata/mjmodel/mjtype/... but NO mjtnum.h). The upstream source targets the
  OLD mujoco API it vendors. Forcing 3.12.0 headers is a compat fight upstream
  does not support.
- **Vendored path works end-to-end** (probed): mujoco_vendor 0.0.9 vendors
  mujoco **3.4.0**; a C loader compiled against that vendored 3.4.0 lib
  **successfully loads the MJCF produced by PR7's 3.12.0 `load_mjcf_model()`**
  (nq=18 nv=18 nu=18 nbody=19 njoint=18 — identical to 3.12.0). So the MJCF
  format is forward-compatible; the sim (3.4.0) can consume the Python-derived
  (3.12.0) MJCF. This is the **same dual-library reality** as RoboStack's own
  0.0.3 channel build (which linked libmujoco 3.8.x via mujoco_vendor).
- **Ruling:** build `mujoco_vendor` (vendoring mujoco 3.4.0) + the
  `mujoco_ros2_control` stack **without** NEVER_VENDOR, i.e. the standard
  upstream build. Two mujoco libs coexist: vendored 3.4.0 (sim, C++ plugin) and
  conda 3.12.0 (PR7 Python). They do not conflict; the sim loads the derived
  MJCF. Do NOT try to compile the plugin against 3.12.0 headers.
- Build updates needed on top of R-PR8b-1: `pixi add vcstool
  ros-jazzy-ros2-control-cmake libcap glfw lttng-ust` (all VERIFIED on the
  channel/conda-forge; the plugins+core link these natives). The AMENT_VENDOR_POLICY
  env var alone does not reach the CMake branch (needs `-D` cache var); after
  revision we do not set it at all. Add `[activation.env] LIBRARY_PATH =
  "${CONDA_PREFIX}/lib:${LIBRARY_PATH}"` to pixi.toml so the linker finds the
  conda-native libs (lttng-ust, cap, glfw) at build time (VERIFIED: plugins
  linked once LIBRARY_PATH was set).
- **Test/guard implications (VERIFIED from scripts/check_test_integrity.py + git):**
  the vendored mujoco_* packages are git-UNTracked (vcs import drops them under
  src/), so `discover_packages()` classifies them `unowned` and they are NOT
  required to produce tests — build them with `-DBUILD_TESTING=OFF` and they
  won't trip the "zero tests" guard. Only OUR git-owned packages (robot_*, the
  workspace-tooling suite) are demanded to meet the floor. Add these src/ dirs
  to `.gitignore` so they are never accidentally committed.
- The core `libmujoco_ros2_control.so` and `ros2_control_node` build cleanly;
  only upstream's own test binaries (e.g. `test_mujoco_simulation`) fail to
  link glfw — not needed for us, hence BUILD_TESTING=OFF.

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

### R-PR8b-8 — Free base joint DEFERRED to roadmap #4 (revised after build probe)
**REVISED 2026-08-22 in-process:** the original "add a free base joint" ruling
is **scoped OUT of PR8b**.
- The derived MJCF welds the static base trunk into the worldbody (R-PR7-5:
  fusestatic folds base_chassis/rail into world; wheels + column_top attach to
  worldbody). There is no existing base `body` to attach a `<freejoint>` to, and
  re-parenting the worldbody children into a new free body cannot be expressed
  by the overlay's append/insert splice (probed of the derived MJCF structure).
  Un-fusing the base or restructuring the derivation would destabilize PR7's
  established model (nbody=19, R-PR7-5) with real risk to this bringup PR.
- **Ruling:** PR8b ships a world-fixed base. All 16 commandable joints are
  driven by controllers; the acceptance criterion "a joint command moves the
  sim state" is met (wheel velocity spins the wheel joint; arm/column/gripper
  position moves their joints) and "sim spawns / ros2_control loads / mj_step
  no NaN / test green" all hold. Wheel controllers are commandable and
  validated at the joint level.
- **Recorded follow-up (roadmap #4 RobotBackend):** add base locomotion by
  unfreezing the base in the derivation (disable fusestatic for the base trunk
  OR add a freejoint to a floating root body) and configure the plugin's
  `odom_free_joint_name`/odom publishing. This belongs with the RobotBackend
  that actually drives navigation, not bringup.
- This is a scoped-OUT item, not a design fork; it does not require the caller.

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


## BLOCKER - the sim will not spawn: mujoco_ros2_control 0.1.0 throws std::bad_alloc (found, reproduced, unresolved)

PR8b cannot reach READY: the source-built `mujoco_ros2_control` 0.1.0 (`ros2_control_node`) RELIABLY throws `std::bad_alloc` inside `MujocoSimulation::initialize` while loading our derived MJCF, so the MuJoCo hardware interface never initializes, the controllers never activate, and the "sim spawns / ros2_control loads / joints commandable" acceptance criterion is NOT met end-to-end through the shipped launch.

Probing evidence (one line summary, full detail in the branch/logs):
- The node starts, RSP publishes, sim logs "Sim ready" + "Running in HEADLESS mode" + "Loading model...", then `Exception of type : St9bad_alloc ... occurred while initializing hardware MujocoSystem`. Controller never activates.
- Our MJCF is 100

## BLOCKER - the sim will not spawn: mujoco_ros2_control 0.1.0 throws std::bad_alloc (found, reproduced, unresolved)

PR8b cannot reach READY: the source-built `mujoco_ros2_control` 0.1.0
(`ros2_control_node`) RELIABLY throws `std::bad_alloc` inside
`MujocoSimulation::initialize` while loading our derived MJCF, so the MuJoCo
hardware interface never initializes, the controllers never activate, and the
"sim spawns / ros2_control loads / joints commandable" acceptance criterion is
NOT met end-to-end through the shipped launch.

Probing evidence (all VERIFIED by execution on node `olivia`):
- The node starts, RSP publishes, the sim logs (in order) "Sim ready",
  "Running in HEADLESS mode", "Loading model...", then
  `Exception of type : St9bad_alloc ... occurred while initializing hardware
  'MujocoSystem'`. The controller spawners wait forever; the position
  controller never activates.
- Our derived MJCF is 100% valid for the vendored mujoco 3.4.0: a standalone C
  loader linked against vendored `libmujoco.so.3.4.0` loads, makes data, and
  steps it (nq=18 nv=18 nu=16 nbody=19), with and without the 3D-lidar plugin,
  and with the plugin-like double load. Not our model, not URDF/MJCF correctness.
- Not a mujoco version clash: every vendored .so NEED `libmujoco.so.3.4.0`
  (RUNPATH -> install/mujoco_vendor/opt/mujoco_vendor/lib); conda's
  `libmujoco.so.3.12.0` is a distinct SONAME and is not picked up.
- Debug AND Release builds both reproduce (built stack with empty CMAKE_BUILD_TYPE
  and again with Release).
- Race/timing signature: under `gdb catch throw` (serialized load) the node got
  past model load with 0 throws in 240s; normal runs bad_alloc reliably ~0.1s
  after "Loading model...". A threading race in the plugin headless load path,
  not memory pressure (laptop had 12 GiB free).
- Model-specific: pointing the plugin at the upstream demo scene fails with a
  CLEAN "Failed to initialize hardware" (no bad_alloc); our larger model
  (meshes, 18 joints, equality constraints) triggers the race; a tiny demo
  robot does not.

Conclusion: upstream bug in ros-controls/mujoco_ros2_control main (0.1.0)
headless load. Options for the caller:
1. Upstream patch / tiny fork via robot.repos to fix the headless
   MujocoSimulation load race; resume PR8b as-is.
2. Try the older tag 0.0.3 source-built (what RoboStack ships; has the
   transmissions + mimic + actuator-type matrix per probe) - re-verify the
   R-PR8b API against it.
3. Defer PR8b; keep the (real, unit-green) URDF ros2_control + overlay +
   launch delta on the branch.

Everything else in PR8b is validated and unit-green: 16-transmission +
2-mimic URDF block; overlay with 16 typed actuators + 2 gripper-mimic
equalities (nu 16 / neq 2 / nbody 19, mj_step no NaN); controller YAML;
launch; unit tests. Only the sim-spawns half of acceptance is blocked by the
plugin bug. No PR was opened because it would fail its own acceptance
(AGENTS.md: do not force what is not actually green).

## Update 2 (after resolving the urdf_parser_py regression) — suite is green, sim-spawn still BLOCKED

The full `pixi run test` is now GREEN: 835 tests, 0 errors, 0 failures, 1
skipped (the 1 skipped is the sim smoke, @skip with the BLOCKER reason above).
The D28 ratchet auto-raised on commit: robot_bringup 2 -> 4, robot_description
48 -> 52.

Two things surfaced while getting there and were fixed on the branch:
- **urdf_parser_py cannot parse ros2_control `<transmission>` blocks** (it only
  duck-types new_transmission/pr2_transmission). This regressed every owned
  package that calls `URDF.from_xml_string` (test_description, robot_model's
  `_parse_model` -> cascade into robot_backends/mcp/brain/etc.). Fixed by
  stripping the `<transmission>...</transmission>` blocks for the urdf_parser_py
  parse in `robot_model._parse_model` and the description/TF gates, with a
  dedicated test pinning the 16 transmissions ARE in the raw expansion; the
  ros2_control runtime parser validates them (R-PR8b-7).
- The `<robot>` root must keep `name="sisyphus"` or RSP fails "No name given"
  (lost in an early rewrite; restored).

The sim-spawn BLOCKER above (plugin std::bad_alloc) is unchanged and is the
only thing keeping PR8b from READY. The branch is in a coherent, unit-green
state; no PR was opened (it would fail the sim-smoke acceptance).

## Probe result (2026-08-22): the 0.0.3 tag does NOT fix the headless-load bad_alloc — hypothesis DISPROVEN

Tested the released **0.0.3** tag of `mujoco_ros2_control` (commit
`35ba8174b62d9560093614f981a3d4b978a96036`) source-built in-branch, same
`mujoco_vendor` (`ff9e648`), same launch. Result: **same `std::bad_alloc`, same
code path** — the released tag has the SAME headless-load race for our larger
model.

Full 0.0.3 launch sequence (verified on `olivia`, ran inside `pixi run` so the
env libs resolve):
- "Sim ready, continuing initialization..." → "Constructing node and
  executor..." → "Executor thread started." → "Loading model..."
- "Constructing publishers." → "Registering actuators." →
  `[ERROR] Exception of type : St9bad_alloc occurred while initializing
  hardware 'MujocoSystem': std::bad_alloc`
- Controller spawners (joint_state_broadcaster, position, velocity) then sit
  in "Failed to acquire lock / Could not contact service
  /controller_manager/list_controllers" forever — nothing activates.

Only practical diffs vs 0.1.0: 0.0.3 links `libbackward.so` (build needs the
pixi env lib dir on LD_LIBRARY_PATH — launch must run via `pixi run bash -c
source install/setup.bash`); it logs one extra line ("Registering
actuators.") before the throw, purely log-timing variance in the same racy
path. Error token identical: `std::bad_alloc`.

Conclusion: closes option [2] from the BLOCKER list. The fix must come from
option [1]: an upstream patch / tiny fork of the headless
`MujocoSimulation::initialize` load path (recorded as a threading race, has a
clean serialized load under gdb). robot.repos reverted back to 0.1.0
(`57fc674`) after the probe; the branch is unchanged / buildable at 0.1.0.
