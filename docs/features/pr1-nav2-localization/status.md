# status.md — Nav2 base navigation, PR1 (issue #121): deps + ground-truth localization

Manager rulings for the implementer. Read these; they are binding. A worker
that believes a ruling is wrong escalates in-process (never silently deviates).

## Branch / worktree
- `feat/i121-pr1-nav2-localization`, worktree `/home/sisyphus/worktrees/i121-pr1-nav2-localization`, cut from `origin/main` @ `06cbdcd`.
- Run everything on node `olivia` (`host=node` / `ssh olivia`). `pixi` is on PATH only in a login shell or via `/home/sisyphus/.pixi/bin/pixi`; use `bash -c 'export PATH=... && ...'`.

## RULING 1 — The base-pose source (the open design question) — PROBED
The brief's premise ("the base is a floating body") is **false** in the current
PR8b ROS-sim. Verified by materializing the sim MJCF (`write_mjcf_model`) and
grep'ing it: **there is no `base_link` body and no free joint.** MuJoCo's
`fusestatic` (ON in the URDF→MJCF import) folds the entire static base trunk
(`base_link`, `base_chassis_link`, `base_footprint`, `column_rail_link`) into
the world body. Only the movable bodies remain (3 wheel links, `column_top`,
arms, grippers), welded at the world origin. So:

- `joint_state_broadcaster` publishes only the 18 actuated joints **because there is no base joint** — the base is welded, not actuated.
- The base pose is the constant world-origin **identity**; it cannot move in the current sim.

What the dfki-ric plugin *does* provide (verified in plugin source): the
**`GetBodyState` service** (`/mujoco_get_body_state`), which returns any named
body's world-frame `pose` + `twist` (from `mujoco_data_->xpos`/`xquat` +
`mj_objectVelocity`). This is the correct ground-truth base-pose source **once
the base is a free body**. The in-process `MuJoCoBackend` already wraps the base
in a 6-DOF free joint (`_wrap_base_freejoint` in `robot_description.mjcf_model`),
but the **ROS-sim path (`write_mjcf_model`) does not** — and making it free in
the sim drags in the no-floor/gravity-fall limitation the backend records as
deferred to PR4. That is **PR2's** problem (driving the base), NOT PR1's.

**Therefore:** PR1's ground-truth `odom → base_link` is a constant transform:
the robot's start pose. The base is welded at the world origin, which **is**
`start_location` (`charger` at (0,0,0), identity). The odometry node publishes
that pose as ground truth, with its pose source behind a small seam (a function
returning the base pose) so PR2 can swap in the live `GetBodyState` pose without
re-plumbing. Do **not** add the free joint to `write_mjcf_model` in this PR.

## RULING 2 — TF tree: `odom → base_link`, NOT `odom → base_footprint` (D29 wins)
The issue/breakdown text "`map → odom → base_footprint → base_link → wheels`"
describes REP-105's standard tree. This repo **inverted** it: D29 made
`base_footprint` a *fixed child* of `base_link` (a TF link has exactly one
parent). The odometry edge must therefore be **`odom → base_link`**; publishing
`odom → base_footprint` would give `base_footprint` two parents and break the
tree at runtime (D29 says this verbatim).

Resulting tree (what the acceptance must actually assert):
```
map → odom → base_link → base_footprint
                  └→ base_left_wheel_link / base_back_wheel_link / base_right_wheel_link
                  └→ column_rail_link → column_top → … (arms/grippers)
```
Nav2 `robot_base_frame` = `base_link`. `base_footprint` continues to be
published by RSP as `base_link`'s child (already true). Record this deviation
from the issue text in the PR description.

## RULING 3 — Static map: a consumer derived from `robot_world` (D23)
The static map is a `nav_msgs/OccupancyGrid` derived **at runtime** from
`robot_world`'s single source of truth. `robot_world` stays authoritative; the
map is a pure consumer and never a second home for location data.

- Source: the world document via the **`world_query`/`get_world` service** (the D35 ROS seam) — do NOT import `robot_world` internals from a node; a node is a consumer of the service. (The existing `world_query_node` is launched by `mujoco.launch.py` already; the map node queries it on startup with retry.)
- Derivation: project `locations` (reference poses) and `objects` (poses) onto a 2D grid at z=0. `locations` become free-space waypoints (NOT obstacles); `objects` (esp. non-graspable furniture like `counter_1`, `sofa_1`) become occupied cells with a small footprint. Map bounds = all locations + objects ± a margin. Resolution ~0.05 m/cell. Everything outside bounds = unknown (-1); inside = free (0) except occupied object cells (100).
- Map frame = world frame. `map → odom` is an **identity static transform** (ground truth: odom does not drift, and map ≡ world ≡ MuJoCo world).
- Publish the grid on `/map` (transient-local, latched) and the `map → odom` static TF.

## RULING 4 — Package structure: new package `robot_nav`
Hold the ROS-facing Nav2 localization layer in a **new ament_python package
`robot_nav`**:
- `robot_nav/static_map.py` — pure derivation: world document JSON → `OccupancyGrid` (+ bounds/resolution). Unit-testable without a graph.
- `robot_nav/ground_truth_odom.py` — the `odom → base_link` publisher node (pose source behind a seam per RULING 1).
- `robot_nav/map_node.py` (or combined) — queries `get_world`, builds the grid, publishes `/map` + `map → odom`.
- `launch/nav.launch.py` — starts the map node + odom node + Nav2 lifecycle localization nodes headless; `robot_bringup` `mujoco.launch.py` **includes** it (exactly like it already includes `world.launch.py`).
- `params/` — Nav2 localization params as needed.
- Follow `robot_world_ros` as the template (package.xml format-3 ament_python, `test/`, `pytest.ini` with `-p no:launch_testing -p no:launch_ros`).

`robot_bringup` gains only: the `nav.launch.py` include + a `<exec_depend>robot_nav</exec_depend>` (+ `nav2_msgs`/`nav_msgs` test-dep if its tests import them). Keep the map-derivation logic out of `robot_bringup`.

## RULING 5 — "Nav2 lifecycle nodes start headless" (PR1 scope)
PR1's Nav2 surface is the **localization** subset only. Bring up headless
alongside the PR8b sim/control stack:
- the map node (publishes `/map` + `map → odom`),
- the ground-truth odom node (`odom → base_link`),
- Nav2's `map_server` **lifecycle** node fed the derived map (prove a real Nav2
  lifecycle node configures/activates headless), OR — if `map_server` insists on
  a PGM file — document that choice and stand up the equivalent lifecycle node.
The **planner / controller / costmap / bt_navigator / AMCL** nodes are PR2 and
out of scope. AMCL stays deferred (nothing produces a scan, D36).

## Acceptance (what the tests must prove)
- `pixi run test` green (full suite), including the ratchet bump in `scripts/test_baseline.json`.
- `test_no_ros_runtime` (D30) stays green: `robot_nav` is a ROS package, but it must **not** be imported by `robot_backends`/`robot_mcp`/`robot_world` (which stay ROS-free). Keep `robot_world` untouched.
- New tests: (a) `static_map.py` derivation is a pure unit test (world doc → grid: locations free, furniture occupied, bounds correct); (b) a launch/integration test that the map + odom nodes come up headless and publish a **complete connected** TF tree `map → odom → base_link → base_footprint` + wheels, with `base_link`'s parent = `odom` and `base_footprint`'s parent = `base_link`; (c) the map is published on `/map` with expected occupied/free cells.

## Out of scope (do not touch)
PR2 (planning + holonomic control), PR3 (`navigate_to` semantic bridge), MoveIt,
the safety layer, any change to `write_mjcf_model`/free-joint base, AMCL/scan
localization.

## Note (added during the loop) — where the bringup-composing test lives
The heavyweight integration test (`test_nav_localization_under_launch`) composes
the full bringup and so shells out to `ros2 launch robot_bringup ...`. It
therefore lives in **`robot_bringup`**'s `test/test_nav_localization.py`, not
`robot_nav`'s: giving `robot_nav` a `<test_depend>robot_bringup</test_depend>`
is a colcon build-order cycle (`robot_bringup: ['robot_nav']` /
`robot_nav: ['robot_bringup']`), because `robot_bringup` already
`<exec_depend>`s `robot_nav` (RULING 4). PR2/PR3 integration tests that compose
the bringup go in `robot_bringup` for the same reason. (The graph-free
`test_nav_launch_generates_expected_nodes` stays in `robot_nav`: it imports only
`nav.launch.py`.)
