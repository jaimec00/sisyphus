# implementation.md — Nav2 base navigation, PR1 (issue #121)

What this PR ships: the **localization layer** Nav2 needs, headless, alongside
the PR8b sim/control stack — a ground-truth `odom -> base_link` transform and a
static map derived from `robot_world`. Planning / control / costmaps / BT
navigator / AMCL are PR2 (RULING 5, D36).

## Package layout — new ament_python package `robot_nav`

| file | role |
| --- | --- |
| `robot_nav/static_map.py` | **pure** derivation: `WorldDocument` -> `nav_msgs/OccupancyGrid`. No rclpy, no node, no graph — unit-testable with plain pytest. |
| `robot_nav/map_node.py` | lifecycle node: queries `/world_query/get_world`, derives the grid once, publishes `/map` (transient-local) + the identity `map -> odom` static TF. |
| `robot_nav/ground_truth_odom.py` | publishes `odom -> base_link` at a fixed rate, with the base pose behind a one-function seam. |
| `robot_nav/pgm_export.py` | renders a derived grid to the PGM+YAML pair `nav2_map_server` reads (the PR1 demonstration of a *real* Nav2 lifecycle node). |
| `launch/nav.launch.py` | starts the odom node, the map node, and a stock `nav2_map_server` driven ACTIVE by `nav2_lifecycle_manager`. |
| `params/nav.yaml` | the two nodes' parameters written out (values are the node defaults). |
| `test/` | `test_static_map.py` (pure unit tests), `test_nav_launch.py` (graph-free launch-node declaration), plus the three ament linter tests. The heavyweight bringup-composing integration test lives in `robot_bringup`'s `test_nav_localization.py`. |

`robot_bringup` gains only an `IncludeLaunchDescription` of `nav.launch.py` in
`mujoco.launch.py` (after `world.launch.py`, since the map node consumes the
world service) and an `<exec_depend>robot_nav</exec_depend>` (+ `nav_msgs`
test-dep). The map-derivation logic stays out of `robot_bringup`.

## Map derivation rules (RULING 3, D23)

The map is a **consumer** of `robot_world`'s single source of truth, never a
second home for scene data. `static_map.py` is pure; the node is a thin shell:

* **Source** — the world document via the D35 ROS seam (`/world_query/get_world`
  service), never a direct `robot_world` internals import from the node.
* **`locations` are free-space waypoints** (they are the robot's goals, so
  they must be free) — a location's cell is forced free *after* object marking,
  so a location that coincides with a perceived object is still reachable.
* **`objects` are occupied cells** with a small square footprint
  (`footprint_radius` = 5 cm) around their `(x, y)` projection. Both
  non-graspable furniture (`counter_1`, `sofa_1`) and graspable tabletop
  objects are marked occupied — the conservative choice, documented in the
  module docstring (the map cannot see which surface a graspable object sits
  on).
* **Bounds** = every location + every object, padded by `margin` (1 m). Inside
  the bounds every cell is free (0) except object cells (100). `-1` is reserved
  for what the grid cannot describe; the derivation does not write a `-1`
  rectangle.
* **Resolution** ~5 cm (`DEFAULT_RESOLUTION = 0.05`). The resolution is snapped
  to **`float32`** before any cell arithmetic: `OccupancyGrid.info.resolution`
  is a `float32` on the wire, so a producer computing cells at double precision
  would disagree with every consumer reading the published resolution back, and
  a pose on a cell boundary (e.g. `counter_1` at `x = 2.15`, `min_x = -3.0`)
  would be reported in the neighbouring cell. Normalising once, up front, keeps
  the grid's `info` and its cell contents consistent.
* **Frame** = `map` (which by RULING 3 is the world frame; `map -> odom` is
  identity).

Reuse: `nav_msgs/OccupancyGrid` + `MapMetaData`, and `robot_world`'s own
`WorldDocument` strict parser (never hand-rolled) — no custom grid type.

## Base-pose source — RULING 1 (the open design question, PROBED)

The sim's base is **welded at the world origin, NOT floating**. Materializing
the sim MJCF and grep'ing it showed **no `base_link` body and no free joint**:
MuJoCo's `fusestatic` folds the whole static base trunk into the world body.
`joint_state_broadcaster` publishes only the 18 actuated joints because there is
no base joint. The world origin **is** `start_location` (`charger` at
`(0, 0, 0)`, identity), so the ground-truth base pose is the **constant
identity**.

`ground_truth_odom.base_pose()` is the **seam**: PR2 replaces that one function
with a live read of the dfki-ric `GetBodyState` service
(`/mujoco_get_body_state`) once the base is a free body. The node, its rate,
its frame ids and its TF plumbing do not move. Making the base free in
`write_mjcf_model` is **PR2's** problem and is explicitly out of scope here.

## TF tree — RULING 2 (D29 wins over the issue text)

The issue text says `map -> odom -> base_footprint -> base_link -> wheels`
(REP-105 standard). This repo **inverted** that in D29: `base_footprint` is a
*fixed child* of `base_link`, and a TF link has exactly one parent. Publishing
`odom -> base_footprint` here would give `base_footprint` two parents and break
the tree at runtime. So the odometry edge is **`odom -> base_link`**, and the
resulting tree is:

```
map -> odom -> base_link -> base_footprint
                  +-> base_left_wheel_link / base_back_wheel_link / base_right_wheel_link
                  +-> column_rail_link -> column_top -> ... (arms / grippers)
```

Nav2 `robot_base_frame` = `base_link`. `base_footprint` continues to be
published by `robot_state_publisher` as `base_link`'s child (already true).
**This is a documented deviation from the issue text** and is recorded in the PR
description.

## Lifecycle-node choice (RULING 5)

`map_node` subclasses **`rclpy.lifecycle.LifecycleNode`** — the standard ROS 2
lifecycle base, carrying the same `configure` / `activate` transitions and the
same `/<node>/change_state` service a Nav2 lifecycle manager drives.

`nav2_util.lifecycle_node.LifecycleNode` (the Nav2-way managed base) is **not
available**: on RoboStack the `nav2_util` package ships only its C++/CMake
artifacts, with no Python `lifecycle_node` module (verified). The intended
"real Nav2 node" is `map_server`, but it loads its grid from a PGM/YAML pair on
disk; since RULING 3 keeps `robot_world` the single source of truth, writing a
PGM as the *operational* map would be a second home for map data. So PR1 does
both, with the two roles kept distinct:

* **Operational map:** `map_node` (rclpy `LifecycleNode`) derives the grid at
  runtime and publishes `/map` + `map -> odom`. Its startup is explicit: the
  launch emits `configure` on process start and `activate` when the node reports
  INACTIVE (the goal state of `configure`).
* **Nav2 lifecycle proof:** a stock **`nav2_map_server`** lifecycle node is
  brought ACTIVE by **`nav2_lifecycle_manager`** (`autostart`), fed a
  **throwaway** PGM rendered at launch-description build time from the same
  derived grid (the same build-time-materialization trick
  `mujoco.launch.py` uses for the derived MJCF). That PGM is a *rendering* of
  the world, never a second home for map data; it lives under `~/.ros` and is
  regenerated every launch.

`LifecycleNode` note (relevant to anyone extending `map_node`): overriding the
lifecycle callbacks **must** call `super().on_configure()` /
`super().on_activate()` etc. The base implementation drives the managed
entities' callbacks, which is what actually *enables* a
`create_lifecycle_publisher` publisher — without the `super()` call the
publisher stays inactive and `publish()` is a **silent no-op** (this was the
one non-obvious functional bug found while getting the acceptance green).

## Deviations from status.md (and why)

1. **`on_configure` world query uses a dedicated throwaway node.** status.md
   says "query the world on startup with retry". The lifecycle `configure`
   callback runs *inside* the node's own spin, so a `call_async` on the node
   can never be serviced while the callback blocks, and a second executor over
   the *same* node does not help (a node belongs to one executor; the request
   then never completes — reproduced). The query therefore drives a throwaway
   `Node('map_node_world_client')` with its own executor, leaving the lifecycle
   node to its main spin. Behaviour (retry-until-available, timeout -> lifecycle
   `FAILURE`) is unchanged.
2. **The acceptance launches the composed bringup, not `nav.launch.py` alone.**
   The localization layer is *one* concern; the frames `base_footprint` / the
   wheels come from `robot_state_publisher`, and `/world_query/get_world` comes
   from `world_query_node` — both are separate bringup concerns that
   `mujoco.launch.py` composes (it includes `world.launch.py` and brings RSP up
   directly). `test_nav_localization_under_launch` therefore launches
   `robot_bringup robot.launch.py` + `robot_bringup world.launch.py` +
   `robot_nav nav.launch.py` together — the real bringup shape — with the
   shipped `nav.launch.py` still the artifact under test. Making `nav.launch.py`
   bring up RSP / the world service itself would create a **circular package
   dependency** (`robot_bringup` already `exec_depend`s `robot_nav`), so the
   composition lives in the test — and, because that test shells out to
   `ros2 launch robot_bringup`, it lives in **`robot_bringup`'s**
   `test_nav_localization.py`, not `robot_nav`'s: a reverse `test_depend` on
   `robot_bringup` from `robot_nav` is itself a colcon build-order cycle
   (`robot_bringup: ['robot_nav']` / `robot_nav: ['robot_bringup']`, reproduced).

## Tests

* `test_static_map.py` (10 tests) — pure: seed scene shape, locations free,
  furniture occupied, graspables occupied, location-wins-over-object, bounds +
  margin, resolution honoured, `z` ignored, `map` frame, bad parameters
  refused.
* `test_nav_launch.py` (1 test) — the installed launch declares the map node,
  the odom node and a `nav2_map_server` node (the graph-free half).
* `robot_bringup`'s `test_nav_localization.py` (2 tests) — `mujoco.launch.py`
  includes `nav.launch.py`; and the heavyweight acceptance: launch headless on
  an isolated ROS domain, assert the connected TF tree
  (`map -> odom -> base_link -> base_footprint` + wheels, `base_link`'s parent
  `odom` per D29), the `/map` grid's free/occupied cells against the seed scene,
  `map_node` ACTIVE, and the real `nav2_map_server` ACTIVE too.

Green at time of writing: `colcon test --packages-select robot_nav
robot_bringup` -> robot_nav 15 tests (12 non-linter) and robot_bringup 13
tests (9 non-linter), 0 errors, 0 failures, 1 (pre-existing, environment-gated
`mujoco_ros2_control`) skip. `ament_flake8` RC=0 and `ament_pep257
--add-ignore D213` RC=0 for `robot_nav`.
