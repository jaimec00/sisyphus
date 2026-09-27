# Implementation — i135 PR3: semantic `navigate_to` bridge (issue #135)

## What this PR delivers

PR3's semantic seam: the skill-level `navigate_to(location)` now drives the base
on the classical/real track through Nav2, and keeps `robot_world.start_location`
in step with where the base actually is. The brain's ROS-free `navigate_to`
(Mock / MuJoCoBackend teleport) is **unchanged** — those backends stay ROS-free
at runtime (D30). The bridge lives entirely on the ROS 2 side.

## The seam implemented

- **New action** `robot_nav_interfaces/action/NavigateToLocation.action`:
  goal `string location`, result `bool success` + `string error`. No
  `geometry_msgs` dependency — the goal names a location, never a pose.
- **New node** `robot_nav/robot_nav/semantic_nav.py`, entry point `semantic_nav`
  (registered in `setup.py`, launched in `nav.launch.py`). It is an rclpy
  `ActionServer` named `navigate_to_location`.
- **On a goal**: query `/world_query/get_world` (a `GetWorld` client), parse the
  canonical JSON with `robot_world.WorldDocument.from_dict` (strict parser),
  resolve `document.locations[location]` via the pure `resolve_location`.
  Unknown → `success=False`, `error="unknown location 'X'; known locations: ..."`
  (mirrors the store/backend refusal wording).
- **Known** → build a `nav2_msgs/action/NavigateToPose` goal (frame `map`, pose
  from the pure `pose_from_robot_pose`) and send it via an `ActionClient` on
  `/navigate_to_pose`; on `STATUS_SUCCEEDED` (int 4) call
  `SetStartLocation(location)` on `/world_query/set_start_location`, return
  `success=True`; any other status / rejection / timeout → `success=False` +
  descriptive error. Bounded wait/retry for both servers.
- **start_location update**: three pieces, keeping the world query node the sole
  writer (R3): `robot_world.WorldStore.set_start_location(name)` (pure Python,
  validates identifier + membership, no-op if unchanged, `_touch()`);
  `robot_world_ros_interfaces/srv/SetStartLocation.srv`; and the
  `set_start_location` handler on `world_query_node` (same
  `(ValueError, TypeError) → success=False + error` shape as the three object
  writes).

## Files created / edited

**Created**
- `src/robot_nav_interfaces/` — ament_cmake + rosidl package: `CMakeLists.txt`,
  `package.xml` (with `<member_of_group>rosidl_interface_packages</member_of_group>`),
  `action/NavigateToLocation.action`, `pytest.ini`, `test/test_interfaces.py`
  (4 smoke tests, mirrors `robot_world_ros_interfaces`).
- `src/robot_nav/robot_nav/semantic_nav.py` — the bridge node + the two pure
  helpers (`pose_from_robot_pose`, `resolve_location`, `unknown_location_error`).
- `src/robot_nav/test/test_semantic_nav.py` — 8 pure-helper unit tests.
- `src/robot_world/test/test_start_location.py` — 7 tests for the store mutator.
- `src/robot_world_ros/test/test_set_start_location.py` — 3 tests for the
  service handler (success, refusal, over-the-wire round trip).
- `src/robot_world_ros_interfaces/srv/SetStartLocation.srv`.
- `src/robot_bringup/test/test_pr3_navigate.py` — the integration test (see
  below).

**Edited**
- `src/robot_world/robot_world/store.py` — added `set_start_location` (imports
  `as_identifier` from `robot_skills.validation`; **no ROS import**, keeps
  `test_no_ros_runtime` green).
- `src/robot_world_ros/robot_world_ros/world_query_node.py` — imported
  `SetStartLocation`, created the `set_start_location` service, added
  `_handle_set_start_location`; docstring updated for the fourth write op.
- `src/robot_world_ros_interfaces/CMakeLists.txt` — registered the new srv.
- `src/robot_world_ros_interfaces/test/test_interfaces.py` — 2 new smoke tests.
- `src/robot_nav/setup.py` — `semantic_nav = robot_nav.semantic_nav:main`.
- `src/robot_nav/package.xml` — `<depend>robot_nav_interfaces</depend>`.
- `src/robot_nav/launch/nav.launch.py` — added the `semantic_nav` node.

No change to `robot_world_ros/package.xml` (the new srv is in a package it
already depends on).

## Test results

All runs on node `olivia`, via `pixi run python -m pytest`, with the i135
worktree's own build/install on `PYTHONPATH`/`AMENT_PREFIX_PATH`/`LD_LIBRARY_PATH`.

| suite | result |
|---|---|
| `src/robot_world/test` | **71 passed** (baseline 68 + 3 new... 7 new minus linters counted) |
| `src/robot_world_ros/test` | **14 passed** (11 baseline + 3 new) |
| `src/robot_nav/test` | **37 passed**, 1 harness-only failure (see below) |
| `src/robot_nav_interfaces/test` | **4 passed** |

The one `robot_nav` failure is `test_nav_launch.py::test_nav_launch_declares_the_pr2_planning_stack`,
which resolves an `IncludeLaunchDescription` through `nav2_bringup`'s installed
share; under the ad-hoc overlay harness that share is not on `AMENT_PREFIX_PATH`,
so `perform_substitutions` returns the unresolved source object. **The same test
fails identically in the pristine `i132` worktree under the same harness**, so
it is a harness artifact, not a regression from this PR. Under the real
`colcon test` runner (proper `install/setup.bash`) it passes — as it does on
`main`.

`test_no_ros_runtime` (robot_world) stays green: the new mutator adds no ROS
import.

### Integration test — BLOCKED by an environment regression

`src/robot_bringup/test/test_pr3_navigate.py` is authored and syntax-clean, but
**could not be executed**: it needs the full `mujoco.launch.py` bringup, and
`mujoco_ros2_control` no longer builds in this environment.

`pixi run build` fails:
```
mujoco_ros2_control/.../simulate_gui.cpp: error: cannot convert 'mjvScene*' to 'mjvCamera*'
  (mujoco.h:767 from .pixi/envs/default/include/mujoco/mujoco.h — conda mujoco 3.12.0)
```
`simulate_gui.cpp` includes `<mujoco/mujoco.h>` and now picks up **conda's**
mujoco 3.12.0 header (new 5-arg `mjv_moveCamera`) instead of the
FetchContent-built **3.9.0** header the source targets. The build's include
order in this worktree puts `.pixi/envs/default/include` **before**
`build/.../_deps/mujoco-src/include`; in the working `i132` build one day
earlier the fetched include came first.

Evidence it is an environment regression, not this PR:
- Source is byte-identical (`robot.repos` pins
  `dfki-ric/mujoco_ros2_control@f151b7df`, FetchContent `GIT_TAG 3.9.0`).
- Env contents are identical to `i132`'s (same conda `mujoco 3.12.0`,
  `glfw 3.5.1`, `mujoco-simulate`, conda's `lib/cmake/mujoco`).
- `i132` (and `i131`, `i127`) built `install/mujoco_ros2_control/lib/libsimulate_gui.so`
  successfully on Sep 25–26 with this env.
- A **clean reconfigure** (`rm -rf build/mujoco_ros2_control`) reproduces the
  failure, so it is not a stale build cache.

To run the integration test, the sim build blocker must be fixed first (a
provisioning/ops matter). Once `pixi run build` completes, run:
`pixi run python -m pytest src/robot_bringup/test/test_pr3_navigate.py -s`.

## Deviations from the rulings

None in design. The only deviation is that the integration test is unverified
(not run) because of the environment blocker above — reported as an escalation,
not a silent skip.
