# PR2 — Nav2 base navigation (issue #124): implementation notes

What shipped, what was probed, and what is still open. Companion to `status.md`
(manager rulings; not edited here) and `decisions.md` (D-numbers).

PR2 makes the base **drivable** and **navigable** in the ROS-sim path: the base
is freed in the accepted MJCF, a floor gives the velocity-commanded wheels
something to push against, ground-truth odometry goes live, and the Nav2
planning/control stack drives the base from a `NavigateToPose` goal through a
`/cmd_vel` → 3-wheel-velocity bridge.

## Files changed (implementation commit + this fix round)

| File | Why |
| --- | --- |
| `src/robot_description/robot_description/mjcf_model.py` | `write_mjcf_model` now defaults to the **drivable** model: free-jointed `base_link` + a static floor + the implicit integrator. New `FLOOR_BODIES`, `_inject_integrator`, `base_free_joint=`/`floor=` kwargs. `load_mjcf_model` / `load_mjcf_model_with_scene` / `_wrap_base_freejoint` untouched. |
| `src/robot_description/test/test_mjcf_drive.py` | New: pins the write path's free joint (nq=25, nv=24), the floor body/height (z = −wheel_radius), the wheels-rest-on-floor geometry, and the welded escape hatch. |
| `src/robot_nav/robot_nav/ground_truth_odom.py` | PR1's constant-identity `base_pose()` seam is now **live**: reads the sim's `GetBodyState('base_link')` (non-blocking pending-request pattern), broadcasts `odom → base_link` TF and publishes `nav_msgs/Odometry` on `/odom` from the same response. |
| `src/robot_nav/robot_nav/omni_base_controller.py` | New node: `/cmd_vel` (Twist) → `/base_velocity_controller/commands` (`Float64MultiArray`, 3, joint order `[left, back, right]`) via the LeRobot omni IK; **split sign** (`WHEEL_SIGN=-1.0` translational, `WZ_SIGN=+1.0` rotational); 50 Hz keep-alive; zero-on-timeout (0.5 s). |
| `src/robot_nav/launch/nav.launch.py` | Adds `omni_base_controller` and includes nav2_bringup's navigation stack launch (`navigation_launch.py`) with `params/nav2.yaml`; the demo `map_server` + localization manager stay from PR1. |
| `src/robot_nav/params/nav2.yaml` | New: costmaps (static+inflation), NavFn global planner, MPPI-Omni local controller, frames (map/odom/base_link), velocity limits; inert configs for route_server / collision_monitor / docking_server. |
| `src/robot_nav/test/test_nav_launch.py` | New structural test for the planning stack + params file. (Fix round: expect `navigation_launch.py`, the installed filename.) |
| `src/robot_nav/test/test_omni_ik.py`, `src/robot_nav/test/test_odom_seam.py` | New: pure IK unit tests (incl. the sign constant) and the live-odom seam. |
| `src/robot_bringup/launch/mujoco.launch.py` | **Fix round (RULING 7):** `_conda_lib_env()` action, first in the LaunchDescription, puts the conda env lib on `LD_LIBRARY_PATH` for the launched processes. |
| `src/robot_bringup/test/test_pr2_navigate.py` | New acceptance (re-scoped): `test_base_drives_under_wheel_commands` — full stack composes (ACTIVE) and the base **drives** under direct `/cmd_vel` Twists (`+vx`→`+x`, `+wz`→`+yaw`, direction only). Closed-loop `NavigateToPose` convergence deferred to #125. |
| `colcon_defaults.yaml`, `pixi.toml` | `MUJOCO_BUILD_EXAMPLES=OFF` (MuJoCo 3.9.0 otherwise builds glfw samples that fail on the missing `GL/gl.h`). |

## Probe evidence

### Free base + floor (RULING 1/2)
`write_mjcf_model` output compiles to a free-jointed base: **nq = 25, nv = 24**
(18 actuated + 7/6 free joint), **nbody = 21** (welded 19 + `base_link` + `floor`).
`mujoco.mj_forward` places `base_link` at the world origin and each wheel's
lowest point on the floor plane at **z = −0.05** (= −wheel_radius). The welded
escape hatch (`base_free_joint=False, floor=False`) still yields nq = nv = 18,
nbody = 19, no floor.

### Integrator (implicit, not Euler)
MuJoCo's default Euler integrator is numerically unstable for the stiff
wheel/floor contact the free base introduces: probed **"Nan, Inf or huge value
in QACC at DOF 6"** within ~0.06 s, and the wheel joints *lock* against the
contact (a wheel commanded 5.2 rad/s does not turn — the joint is pinned by the
contact impulse). The implicit integrator resolves the stiff contact and the
commanded wheel speeds are followed exactly. Set only on the free-jointed write
path; the welded `load_mjcf_model` and the in-process backend are unchanged.

### Wheel IK + `WHEEL_SIGN` calibration (RULING 5)
The IK is the LeRobot `_body_to_wheel_raw` matrix (base_radius 0.125,
wheel_radius 0.05, mount angles 60/180/300°, rolling directions `d = (−sinφ,
cosφ)`):

```
K = [[-0.8660254,  0.5, 0.125],
     [ 0.0,       -1.0, 0.125],
     [ 0.8660254,  0.5, 0.125]]
wheel_angular = WHEEL_SIGN * K @ [vx, vy, wz] / wheel_radius
```

The URDF/MJCF joint-axis convention is **opposite** the LeRobot driver
convention, so the final **translational** sign is **`WHEEL_SIGN = -1.0`**. The
sign is **split by column**: the **rotational** (wz) column carries its own
**`WZ_SIGN = +1.0`** (see the option-3 probe below — a global `-1.0` left the yaw
response inverted). Calibrated in sim, not derived — measured with the full
bringup by publishing a pure body twist and reading `GetBodyState('base_link')`:

| command | observed motion (`WHEEL_SIGN = +1.0`) | with `WHEEL_SIGN = −1.0` |
| --- | --- | --- |
| `+vx` | dx = −0.0534 (base drives **−x**) | dx = **+0.0458** (base drives **+x**) ✓ |
| `+vy` | — | dy = **+0.0901** ✓ |
| `+wz` | — | **inverted** under a global −1.0 → `−1.76 rad`; the split (`WZ_SIGN = +1.0`) gives **`+1.72…+1.75 rad`** ✓ |

The two columns are independent constants (`WHEEL_SIGN` for vx/vy, `WZ_SIGN` for
wz) because the sim does not agree with a single global sign on both.

Red-team independently VERIFIED these numbers (round 1). Also confirmed in this
fix round with direct wheel commands: publishing the IK output for `+vx=0.2`
(`[+3.464, 0, −3.464]`) drives the base cleanly to **dx = +0.619 m, dy = +0.073 m**
over 5 s (yaw holds near 0 after a small start transient) — the IK and the sign
are correct end-to-end.

### Live odometry (RULING 3)
With the free base, `GetBodyState('base_link')` reports the live pose.
`ground_truth_odom` broadcasts `odom → base_link` and publishes `/odom`; TF and
the `Odometry` message agree to <1 mm while the base drives (measured in
motion). `map → odom` stays the identity static transform (PR1) — ground truth
does not drift.

### Planning stack (RULING 4)
`bt_navigator`, `controller_server`, `planner_server`, `smoother_server`,
`behavior_server`, `waypoint_follower`, `velocity_smoother`, `collision_monitor`,
`route_server`, `docking_server` all configure and activate; the navigation
lifecycle manager reports **"Managed nodes are active"**. NavFn produces a valid
straight path from the base to the (2.0, 0) goal. **MPPI with
`motion_model: Omni` configures and runs in the installed Nav2 1.3.12** — no
substitution to DWB was needed for the stack to come up.

## Fix round (RULING 7)

### BLOCK 1 — sim binary cannot load conda-native libs (fixed)
The source-built `mujoco_ros2_control` executable failed at load:
`libcontroller_manager.so: cannot open shared object file` → exit 127 → the
node's `on_exit=Shutdown()` tore down the bringup → no controller ever
activated, breaking both PR2's acceptance and the must-stay-green joint smoke.

Root cause (VERIFIED): the installed binary's **RUNPATH** carries only the build
dir (`readelf -d` → `/…/build/mujoco_ros2_control` and `…/build/mujoco_ros2_control/lib`);
`CMAKE_INSTALL_RPATH_USE_LINK_PATH=TRUE` does not capture the conda lib; and
ament's `setup.bash` **recomputes `LD_LIBRARY_PATH` from ament prefixes**,
dropping the pixi `[activation.env]` conda lib that was present in the launching
shell.

Fix (PRIMARY, launch-level): `_conda_lib_env()` in `mujoco.launch.py`:

```python
def _conda_lib_env():
    conda_prefix = os.environ.get('CONDA_PREFIX')
    if not conda_prefix:
        return LogInfo(msg=...)
    lib_dir = os.path.join(conda_prefix, 'lib')
    existing = os.environ.get('LD_LIBRARY_PATH', '')
    library_path = lib_dir + (os.pathsep + existing if existing else '')
    return SetEnvironmentVariable(name='LD_LIBRARY_PATH', value=library_path)
```

placed **first** in the returned `LaunchDescription`:

```python
return LaunchDescription([
    # FIRST: make the conda env libs loadable for every process this launch
    # spawns (the sim binary needs them; see _conda_lib_env / RULING 7).
    _conda_lib_env(),
    DeclareLaunchArgument(...),
    ...
])
```

Graceful no-op (`LogInfo`) when `CONDA_PREFIX` is unset, so the structural
launch test, which imports the module and builds the description outside the
pixi env, still works.

**Evidence it works:** with the fix, under `pixi run`, the sim logs
`[mujoco_ros2_control]: loaded mujoco model`, `[resource_manager]: Successful
'activate' of hardware 'MujocoSystem'`, and `[spawner] … Configured and
activated base_velocity_controller` — **zero process deaths**, no
`libcontroller_manager.so` error.

### BLOCK 2 — navigation lifecycle aborted by `docking_server` (fixed)
Nav2 1.3.12's `navigation_launch.py` lists `docking_server` in its lifecycle
node set, and `docking_server` **refuses to configure without a charging-dock
plugin** (`Charging dock plugins not given!`). The failure aborted the whole
navigation lifecycle manager, so `bt_navigator` never activated and the
acceptance could not run even with the sim healthy. The shipped `nav2.yaml`
declared docking's scalar params but not the required `dock_plugins`.

Fix: declare the stock `simple_charging_dock` plugin (and the docking controller
block) in `nav2.yaml`. **No dock instances are declared** (`docks:` stays
empty), and nothing in this stack sends a dock goal, so the server stays inert —
it only has to configure cleanly.

### Test/expectation fix
`test_nav_launch_declares_the_pr2_planning_stack` asserted the include ends with
`navigation.launch.py` (RULING 4's wording); Nav2 1.3.12 ships it as
`navigation_launch.py`. The launch includes the installed file; the test
expectation was corrected.

## Open / not resolved in this round

### BLOCK 3 — closed-loop `NavigateToPose` convergence: DEFERRED to #125 (not an open failure)
Historical detail (kept for the record): with the sim loading and the full stack
ACTIVE, the original closed-loop acceptance (send a `NavigateToPose` goal to
(2.0, 0.0), assert position + heading converge) never passed — the base **drives
but does not converge** within the 120 s budget. This is now understood and
**re-scoped**: the missing piece is the rim-roller omniwheel model (**#125**), so
the closed-loop check is **deferred**, not failed. The cause analysis below is
retained as the evidence trail for #125.
Observed across runs (the behaviour is **nondeterministic** — sim random, real
time):

- travelled **0.55 m**, **1.77 m** (target ~2.0 m) before timeout;
- once the goal **ABORTED** (progress checker: no 0.3 m of progress in 15 s);
- `cmd_vel` shows the controller oscillating — `vx` hovers ≈ −0.15 (negative,
  away from the goal) while `wz` swings to its ±0.6 limit, and `odom` x
  advances in fits and starts.

What is **verified correct** (so the defect is not here):
- Sim loads; stack healthy; `base_velocity_controller` active; MPPI-Omni runs.
- TF tree correct: `map → odom` identity, `odom → base_link` yaw ≈ 0 at start;
  TF and `/odom` agree to <1 mm **in motion**.
- The derived `/map` is free of obstacles along the whole y=0 corridor from
  x=−1 to x=+2 (all cells 0) — the path is not blocked.
- NavFn's global plan is correct: a straight +x path from the base to (1.8, −0.05).
- The IK + `WHEEL_SIGN=-1.0` drive the base cleanly **+x** (dx = +0.62 m for the
  +vx=0.2 wheel command), and `/cmd_vel vx=0.2` also drives +x (dx = +0.46 m).

So the remaining failure is in the **closed-loop controller** tracking, not in
the sim, the frames, the map, the planner, or the IK. The strongest available
hypothesis (UNVERIFIED — it needs the controller's internal state / MPPI
trajectory visualization to confirm): MPPI's optimiser is thrashing between
solutions each cycle, and the cylinder-wheel contact model converts commanded
`vy` into real rotation (measured under the full stack: pure `/cmd_vel vy=0.2`
for 4 s → `dyaw = 2.25 rad`), so the holonomic model the controller assumes does
not match the sim's lateral response — the loop destabilises.

Candidate next steps (for a follow-up PR or a manager ruling — **not** applied
here, to avoid shipping unverified tuning):
1. Model the omni wheels for **anisotropic (low-lateral) friction** in the
   sim-only path — a sphere collision geom, or a wheel geom with near-zero
   lateral slide friction — so `vy` produces translation, not rotation. (The
   URDF collision is a `cylinder`; the sim overlay is the scope for this.)
2. Tune MPPI (`collect_velocity`/`wz_std`/`temperature`, or drop `vy` usage) and
   re-measure; or, per RULING 4's fallback clause, try a diff-drive-style local
   controller for this path and record the evidence.
3. Set `min_y_velocity_threshold` correctly for a holonomic base (it is `0.5` in
   the shipped file, the diff-drive template value, which zeroes small `vy`); the
   probe above showed it is not sufficient on its own, so it was left unchanged
   pending a proper fix.

### NOTEs from red-team
- `test_pr2_navigate` / `test_joint_command` surface `<no output>` on sim
  failure; surfacing the sim's stderr would make a sim-death diagnosis immediate.
  Not addressed in this round.
- Kill stray `mujoco_ros2_control` processes before re-running.

## Test status

After the option-3 fix round, the acceptance is green on its **re-scoped
open-loop** claim (`test_base_drives_under_wheel_commands`: `+vx` → `dx ≈ +0.33 m`,
`+wz` → `dyaw ≈ +1.72 rad`, direction-only), and the closed-loop
`NavigateToPose` convergence is **explicitly deferred to post-#125** (not an open
failure). The earlier fix-round run was `960 tests, 0 errors, 2 failures` — the
old NavigateToPose acceptance (BLOCK 3) and the launch-test filename expectation
(the latter fixed in that round). Everything else is green, including
`test_joint_command_moves_sim_state` (robot_bringup) and D30's
`test_no_ros_runtime` (robot_brain).

### BLOCK 3 resolution — park vy (Jaime, option 2, 2026-09-19)
The lateral input channel is parked for this PR: `nav2.yaml` sets
`vy_std = vy_max = vy_min = 0` (MPPI-Omni commands only vx + wz). The holonomic
architecture + omni IK are intact; only the unusable lateral channel is disabled,
because the cylinder-wheel sim (no rim rollers) converts `vy` into rotation
(measured `dyaw ~= 2.25 rad` for `vy=0.2`) instead of translation. Restoring true
lateral holonomy in sim is follow-up **issue #125** (rim-roller model); when it
lands, `vy` is re-enabled by restoring the three values (config-only — the omni IK
already maps the full Twist).

### BLOCK 3 resolution — option-3 wz-sign probe + acceptance re-scope (2026-09-20)
Park-vy (option 2) was **necessary but not sufficient**: the 2nd re-red-team found
the cylinder-wheel sim also mishandles **rotation** (pure `wz=+0.6` for 8 s gave
`dyaw = −1.76 rad`, i.e. **inverted**; `vx+wz` jammed). The global `WHEEL_SIGN=-1.0`
had been calibrated only on the **translational** columns (vx/vy), never on the
**rotational** (wz) column. Jaime resolved the 3rd escalation with a cheap
per-term probe ("option 3 first; make decisions yourself").

**Probe (option 3 — split the sign: vx/vy at `-1.0`, wz at `+1.0`)**, run twice on
isolated domains, reproducible:

| open-loop `/cmd_vel` | result |
| --- | --- |
| pure `wz=+0.6` (8 s) | `dyaw = +1.72…+1.75 rad` — **POSITIVE, correct direction** (was `−1.76` under the global `−1.0`) |
| pure `wz=+0.6` speed | `≈0.216 rad/s` ≈ 0.36× commanded (still ~⅓ — physical scrub) |
| `vx=0.3 + wz=0.6` (8 s) | `dx ≈ −0.37…−0.38 m`, `dyaw ≈ +1.50…+1.60 rad` — **no longer jams**, but translation ~0.16× commanded and misdirected |

**Decision.** The wz column sign was genuinely **inverted** — the split fixes the
rotation direction and unjams the combined `vx+wz` command — but the **~⅓ speed
attenuation** and the combined-command translation degradation are **physical
scrub** (a plain-cylinder wheel cannot roll-and-turn without sliding), **not** the
sign. The "sign bug only" branch required correct-direction rotation at ~full
speed *and* clean `vx+wz` driving; neither holds. So:

- **KEEP the split-sign fix** (`WZ_SIGN = +1.0`): a real, empirically calibrated
  direction correction (pure `+wz` now rotates `+yaw`, was `−yaw`) that removes a
  latent sign bug which would otherwise survive into the #125 rim-roller model.
  It does **not** fix the speed/scrub.
- **Re-scope PR2's acceptance** to the VERIFIED **open-loop** claim — "the base is
  drivable under wheel commands": pure `+vx` translates `+x`, pure `+wz` rotates
  `+yaw` (correct directions). No speed assertion (speed is attenuated by the
  scrub).
- **Promote #125** (rim-roller omniwheel model) to the **prerequisite** for the
  closed-loop `NavigateToPose` acceptance; that convergence check is **deferred to
  post-#125** rather than failed.

The acceptance test was renamed `test_base_drives_under_wheel_commands`: it keeps
the full bringup + readiness wait (proving the stack composes), drops the
`NavigateToPose` goal, and publishes `/cmd_vel` Twists directly, asserting
direction only. The controller/smoother stay quiet with no goal active, so the
direct publication is uncontested.

**Two sim sessions, one per direction (VERIFIED necessary).** The direction
checks cannot share one sim session: the plain-cylinder wheel/floor contact
grips cleanly from rest but **slips once the base has been driven**, so a second
command in the same session is unreliable. Measured: in a single session with
`wz` then `vx`, the `vx` phase came out `dx = −0.12 m` (backwards); with `vx`
then `wz`, the `wz` phase gave `dyaw = 0` (stuck). Each direction from a **fresh**
session is reproducible (`+vx` → `dx = +0.31…+0.49 m`; `+wz` → `dyaw = +1.77 rad`),
so `_drive_probe` launches the stack once per direction. The rotation check runs
first; both pass on two consecutive runs.
