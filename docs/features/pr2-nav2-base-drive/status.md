# status.md — Nav2 base navigation, PR2 (issue #124): drivable base + planning + holonomic control

Manager rulings for the implementer. Read these; they are binding. A worker that
believes a ruling is wrong escalates in-process (never silently deviates).

## Branch / worktree
- `feat/i124-pr2-nav2-base-drive`, worktree `/home/sisyphus/worktrees/i124-pr2-nav2-base-drive`, cut from `origin/main` @ `f589561` (PR1 #122 + #123 amend merged).
- Run everything on node `olivia` (`host=node` / `ssh olivia`). `pixi` is on PATH only in a login shell or via `/home/sisyphus/.pixi/bin/pixi`.
- Provision first: `pixi install` + `pixi run install-openclaw`; `pixi run build` BEFORE anything else. Long tests via `nohup pixi run test`.
- PR1 shipped the localization layer (`src/robot_nav/`, plus the `nav.launch.py` include in `mujoco.launch.py` and `test_nav_localization.py`). Read PR1's `docs/features/pr1-nav2-localization/status.md` rulings (RULING 1..5) before implementing; they are carried forward and only amended here, not re-litigated.

## RULING 1 — Free the base in the ROS-sim path (`write_mjcf_model` only)
PR1 (RULING 1) proved the base is **welded**: `fusestatic` folds the static trunk into the world body, so `write_mjcf_model` (the PR8b ROS-sim path) produces **no** `base_link` body and **no** free joint (nq=nv=18). The in-process backend (`load_mjcf_model_with_scene`) already wraps the base in `_wrap_base_freejoint`.

- Change **`write_mjcf_model`** to materialize the **free-jointed** base + floor, i.e. call `_build_merged_mjcf(pkg, world_bodies=_FLOOR_BODIES, base_free_joint=True)` (see RULING 2 for the floor). Give `write_mjcf_model` a keyword `base_free_joint=True` (default True now) and `floor=True` so callers/tests can still request the bare welded model if they must, but the **default** the sim launch uses is the drivable model.
- **Do NOT touch** `load_mjcf_model`, `load_mjcf_model_with_scene`, or `_wrap_base_freejoint`. The in-process `MuJoCoBackend` path (`load_mjcf_model_with_scene`) is **untouched and must stay green** (acceptance). It has no floor (gravity-fall deferred) — that is correct and out of scope here.
- `test_mjcf_model.py` calls `load_mjcf_model()` (the bare welded model) and asserts nq=nv=18, nbody=19; those assertions stay valid because the bare loader is unchanged. **New** tests assert the *write* path is now free-jointed (see RULING 6).

## RULING 2 — Floor + wheel contact (the physics that makes the base drivable)
The wheels are velocity actuators (`<velocity kv="10"/>`, overlay.xml) and become movable bodies only after the free-joint wrap. For a velocity-commanded wheel to *move the base* it must contact a floor with friction.

- **Floor**: a static body spliced via `_build_merged_mjcf`'s `world_bodies` seam (the same seam the in-process backend uses for scene objects). Because `_build_merged_mjcf` wraps the base **before** splicing `world_bodies`, the floor stays welded to the world (a sibling of the wrapped `base_link`), never riding on the base. Define a module-level `_FLOOR_BODIES` fragment in `mjcf_model.py`: a `<body name="floor">` with a `<geom type="plane" ...>` (or a large box) whose **top surface is at world z = -wheel_radius = -0.05**.
  - Why -0.05: after the wrap the free joint starts at `pos="0 0 0"` (base_link at world origin, axle height), and the wheels sit at base_link z = 0 with `wheel_radius = 0.05`, so the wheel bottoms are at world z = -0.05. The chassis underside is at z = 0.085 - 0.06/2 = 0.055 (well above the floor) — only the 3 wheels touch. A plane is preferred (infinite support, no fall-off edge, inherits the overlay friction); a 20x20 m box is an acceptable fallback if contact tuning needs a finite body.
- **Contact/friction**: the overlay already splices `<default><geom friction="1.0 0.4 0.02" solref="0.01 1.0" priority="1"/></default>` (all geoms inherit it). Do **not** add a second contact model; verify the wheels engage contact with the floor. The base rests on 3 wheels (a stable tripod), so it settles rather than tips.
- **PROBE (implementer, in sim)**: the base is now free, so on startup it settles onto the floor under gravity. Verify (a) the free-jointed MJCF compiles and the dfki-ric sim spawns headless, (b) `GetBodyState('base_link')` reports a finite pose, (c) a wheel velocity command actually moves the base (drives, not spins in place / falls through). Tune `kv` (velocity-gain) and/or friction **only if** the base slips or won't move; the default `kv=10` + `friction 1.0` is the starting point.

## RULING 3 — Live ground-truth odometry (`GetBodyState` + an `/odom` topic)
PR1 shipped `ground_truth_odom.base_pose()` as the constant world-origin identity behind a seam. Make it live:

- The pose source is the dfki-ric **`GetBodyState` service** `/mujoco_get_body_state` (`mujoco_ros2_control/srv/GetBodyState`): request `string body_name`, response `bool success, string message, geometry_msgs/Pose pose, geometry_msgs/Twist twist`. Read with `body_name = "base_link"` (the body `_wrap_base_freejoint` names; on the *welded* model there is no such body — it only exists after RULING 1 frees the base).
- Keep the **seam**: `base_pose` remains the one function to replace. Restructure minimally: the node owns a `GetBodyState` client; a `_read_base_state()` method returns the live `(Pose, Twist)` (or the last-known / the identity default on `success=false` or service-unavailable, logging once so odom never NaNs). `_broadcast` publishes TF `odom -> base_link` from the live pose exactly as today.
- **Also publish `nav_msgs/Odometry` on `/odom`** (twist from the same response). Nav2's controller/velocity smoother consume `/odom` for velocity feedback; TF alone leaves them velocity-blind. This is a small additive change to the same node, not a new node.
- The `map -> odom` identity static TF (PR1 RULING 3) is **unchanged**: ground truth means odom does not drift.

## RULING 4 — Nav2 planning stack (costmaps + NavFn + MPPI holonomic)
Extend the localization layer with the Nav2 **planning** half. Bring up headless alongside the sim/control stack, composed in `nav.launch.py` (included by `mujoco.launch.py`, as PR1 did for localization).

- **Compose** the planning nodes by including **`nav2_bringup`'s `navigation.launch.py`** with our params file (`params_file`), `use_sim_time`, and `map_subscribe_transient_local:=true`. This gives controller_server, smoother_server, planner_server, behavior_server, bt_navigator, waypoint_follower, velocity_smoother, and the navigation lifecycle manager the Nav2-way (the PR1 "real Nav2 lifecycle node" pattern). Do **not** include `nav2_bringup`'s `localization.launch.py`/`bringup_launch.py` (they assume map_server+AMCL; our localization is ground-truth, PR1).
- **Frames** (PR1 RULING 2/3 carry): `global_frame: map`, `robot_base_frame: base_link`, `odom` frame = `odom`, `odom_topic: /odom`. `map -> odom` is identity.
- **Global planner**: **NavFn** (`nav2_navfn_planner/NavfnPlanner`, the GridBased default). Simple, reliable, and right-sized for a small derived grid. (SmacPlannerHybrid is acceptable but unnecessary.)
- **Local controller**: **MPPI** (`mppi_controller::MPPIController`) with **`motion_model: Omni`**. Rationale (recorded per the brief's "pick one and say why"): the base is **holonomic** (3-omniwheel, D26/D29), so the controller must command independent `vx, vy, wz`. MPPI natively supports an Omni motion model that outputs a full 3-component Twist and re-plans against a forward-simulated model each cycle — better matched to omni drive and to the velocity-servoed-wheel contact dynamics than DWB, whose holonomic support is weaker (DWB's KinematicHandler is diff-drive-oriented). DWB is the fallback **only** if MPPI's Omni model misbehaves in the installed Nav2 1.3.12; that switch must be recorded in `implementation.md` with the evidence.
- **Costmaps**: global costmap = `static_layer` (our derived `/map`, transient-local) + `inflation_layer`; local costmap = `static_layer` + `inflation_layer` (no obstacle/voxel layer — nothing produces a scan, D36; AMCL and scan localization are out of scope). Set a conservative `robot_radius` (chassis 0.15 → use 0.20 m) or an explicit `footprint`. Set `resolution 0.05` to match the derived grid.
- **Velocity limits**: pick modest maxima (e.g. 0.3 m/s linear, 0.6 rad/s angular) and put them in the params file; the safety layer (D17) is out of scope and must NOT be modified.

## RULING 5 — `cmd_vel` -> 3 wheel velocities (omni IK)
MPPI outputs a `geometry_msgs/Twist` on `/cmd_vel`. Convert it to the 3 wheel speeds the PR8b `base_velocity_controller` already accepts (`Float64MultiArray` of 3 on `/base_velocity_controller/commands`, joint order `[base_left_wheel, base_back_wheel, base_right_wheel]`).

- **New node** in `robot_nav`: `omni_base_controller.py` (node `omni_base_controller`). Subscribes `/cmd_vel` (Twist); publishes `/base_velocity_controller/commands` (Float64MultiArray, 3 values in the controller's joint order). Publish on each `cmd_vel` plus a keep-alive re-publish at ~50 Hz so the group controller does not time out.
- **The IK** (LeRobot `lekiwi.py` `_body_to_wheel_raw`, base_radius=0.125, wheel_radius=0.05; mount angles left/back/right = 60/180/300 deg, rolling directions d = (-sin φ, cos φ)):
  ```
  K = [[-sin(60°),  cos(60°),  base_radius],
       [-sin(180°), cos(180°), base_radius],
       [-sin(300°), cos(300°), base_radius]]
    = [[-0.8660254,  0.5,      0.125],
       [ 0.0,       -1.0,      0.125],
       [ 0.8660254,  0.5,      0.125]]
  wheel_angular = (1.0 / wheel_radius) * K @ [vx, vy, wz]
  ```
  Explicitly: `wl = (-0.866*vx + 0.5*vy + 0.125*wz)/0.05`, `wb = (-vy + 0.125*wz)/0.05`, `wr = (0.866*vx + 0.5*vy + 0.125*wz)/0.05`.
- **SIGN — must be calibrated in sim, not assumed.** The LeRobot driver convention (positive wheel speed → body moves along +d) is **opposite** the URDF/MJCF joint-axis convention (positive joint velocity → body moves along **-d**; base.xacro states this verbatim). So the LeRobot matrix result is expected to be **negated** for the MJCF joints. Implement the result behind a single `WHEEL_SIGN = -1.0` constant with a comment, then **verify empirically**: publish pure `+vx` (or `-vy`), observe the base's real motion via `GetBodyState('base_link')`/`/odom`, and flip `WHEEL_SIGN` so `+vx` drives the base +x. This is a calibration fact, not a geometric derivation.
- **Zero on timeout**: if no `cmd_vel` arrives for a short window (e.g. 0.5 s), publish zeros so a stale Twist cannot keep the base driving (a minimal, local guard — the safety layer proper is out of scope).

## RULING 6 — Test strategy + ratchet
- `robot_description` (new unit tests in `test_mjcf_model.py` or a sibling): `write_mjcf_model` output now carries a `base_link` free-jointed body + a `floor` world body, and its compiled model has `nq = 18+7`, `nv = 18+6`; `load_mjcf_model_with_scene` and `load_mjcf_model` are unchanged (welded bare model still nq=nv=18, nbody=19).
- `robot_nav`: (a) unit test the omni IK pure function (known Twists → expected wheel vectors, incl. the sign constant); (b) unit test the live-odom seam (a fake `base_pose`/response feeds TF correctly) and the `base_pose` read path shape.
- `robot_bringup` (composing the full bringup, same reason as PR1's `test_nav_localization.py`): a `NavigateToPose` integration test — launch `mujoco.launch.py` headless, bring the Nav2 nodes ACTIVE, send a `NavigateToPose` action goal to a nearby reachable pose, and assert the base **drives** there (position **and** heading converge within tolerance) via `GetBodyState`/`/odom` — explicitly **not** a teleport (assert the base passes through intermediate poses / takes >0 time). Use a generous timeout (driving at ≤0.3 m/s over ~1 m is ~10+ s; budget 90–120 s) and a distinct ROS_DOMAIN_ID. Keep `test_joint_command_moves_sim_state` green — the arm position command must still move the arm with the base now free.
- **Ratchet**: bump `scripts/test_baseline.json` for `robot_description`, `robot_nav`, `robot_bringup` to the new counts (`pixi run test` will tell you the exact numbers).
- **D30**: `robot_backends`/`robot_mcp`/`robot_world` stay ROS-free and untouched; all new ROS code lives in `robot_nav` (+ `robot_description` which already imports no ROS runtime). `test_no_ros_runtime` must stay green.

## Out of scope (do not touch)
Semantic `navigate_to` bridge (PR3), MoveIt arm planning, the safety layer, AMCL/scan localization, any change to `load_mjcf_model_with_scene` / `_wrap_base_freejoint` / the in-process `MuJoCoBackend`.

## Open questions for the implementer to pin in `implementation.md` (not re-ruled)
- Exact floor height/z and any `kv`/friction tuning needed to make the base actually drive (evidence: sim logs + observed base motion).
- `WHEEL_SIGN` final value after empirical calibration.
- Whether MPPI-Omni works as ruled or DWB had to be substituted (with evidence).
