# PR1 — ROS 2 world query service: implementation

Issue #113 (PROJECT.md step 4, PR 1 of 3). Branch `i113-pr1-world-query-service`,
based on `origin/main` @ `f8a5279`. Rulings followed: D35 (new package over the
pure-Python `robot_world` store), D23 (JSON store is the single source of
truth; thin adapter), D30 (no ROS at runtime below the seam), D24 (no
hand-maintained lists), D28 (test ratchet). status.md rulings R1–R4 are
binding and were implemented as written; deviations are noted below.

## What was built

### `src/robot_world_ros_interfaces` (ament_cmake) — the srv definitions

- `package.xml` (format 3): `ament_cmake` buildtool, `rosidl_default_generators`
  build dep, `rosidl_default_runtime` exec dep, `rosidl_interface_packages`
  group, `ament_cmake` build type.
- `CMakeLists.txt`: `rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/GetWorld.srv")`, `ament_export_dependencies(rosidl_default_runtime)`.
- `srv/GetWorld.srv`: empty request; response `string world_json` (R2).
- `test/test_interfaces.py`: imports the generated `GetWorld` and asserts
  `GetWorld.Response.world_json` exists, plus a request-empty / response
  round-trip check. **Two non-linter tests**, so the gate sees a real result
  (a first-party package with no result is audited `no-result` → red).
- `pytest.ini` with `-p no:launch_testing -p no:launch_ros` (RoboStack pytest≥8
  plugin issue).

**Deviation from the brief's literal CMake scaffolding (built to a probing
finding, recorded not hidden):** the workspace sets `-DBUILD_TESTING=OFF`
globally in `colcon_defaults.yaml` (a workaround for the vendored
`mujoco_ros2_control` stack, whose upstream test binaries do not link against
the conda glfw). Under that flag ament_cmake never calls `enable_testing()`,
so a test placed inside the conventional `if(BUILD_TESTING)` guard registers
nothing and the package produces **no JUnit XML** — probed: `colcon test`
returned 0 with an empty `Testing/Temporary/LastTest.log` and no result file.
The fix is local to this package's `CMakeLists.txt`: call `enable_testing()`
**unconditionally**, then `ament_add_pytest_test(test_interfaces
test/test_interfaces.py)` outside the guard. No shared config was changed; the
workspace-wide flag is untouched. `ament_add_pytest_test` (not a bare
`add_test`) is used because it writes the JUnit XML the gate reads.

### `src/robot_world_ros` (ament_python) — the node

- `package.xml`: `ament_python`, deps `rclpy`, `robot_world`,
  `robot_world_ros_interfaces`, test deps the three ament linters +
  `python3-pytest`.
- `setup.py` / `setup.cfg` / `pytest.ini` / `resource/robot_world_ros` cribbed
  from `robot_world`/`robot_bringup`; entry point
  `world_query_node = robot_world_ros.world_query_node:main`.
- `robot_world_ros/world_query_node.py` — `WorldQueryNode` per R3/R4:
  - Node **`world_query`** in the **`/world_query`** namespace, service
    **`get_world`** of type `robot_world_ros_interfaces/srv/GetWorld` →
    resolves to `/world_query/get_world` (R3).
  - Params `world_state_path` (default `''` → `$ROBOT_WORLD_STATE` → loud
    startup failure) and `world_seed_path` (default `''` →
    `$ROBOT_WORLD_SEED` → `None` = shipped seed).
  - `FileWorldStore(live_path, seed_path_or_None)` constructed **once** in
    `__init__`; handler sets
    `response.world_json = document_text(self._store.document())`, a **fresh
    snapshot per call**, read-only (no `robot_world` mutation).
  - `main()`: `rclpy.init()` / `rclpy.spin` / `rclpy.shutdown()`.
  - `rclpy` imported **only** in this file.
- `test/`: `test_copyright.py`, `test_flake8.py`, `test_pep257.py` cribbed
  verbatim from `robot_world/test/` (byte-identical); `test_world_query.py`
  with the two acceptance tests.
- `README.md`: service name, srv type, params, and the JSON-string rationale.

## Verification performed

All commands run on node `olivia` in the worktree, with
`export PATH="/home/sisyphus/.pixi/bin:$PATH"`.

### 1. Full build — green
```
pixi run build
```
Result: `Summary: 11 packages finished [10.3s]` (both new packages built;
`robot_world_ros_interfaces` 6.5s, `robot_world_ros` 1.5s).

### 2. Interface generates and imports — confirmed
```
pixi run bash -lc 'source install/setup.bash && python -c "from robot_world_ros_interfaces.srv import GetWorld; r=GetWorld.Response(); r.world_json=\"x\"; print(r.world_json)"'
```
Result: prints `x` — the generated Python srv module exists and its response
field is settable/readable.

### 3. Package tests — green
```
pixi run bash -lc 'source install/setup.bash && colcon test --packages-select robot_world_ros robot_world_ros_interfaces --event-handlers console_direct+ && colcon test-result --all --verbose'
```
Result:
```
build/robot_world_ros/pytest.xml: 5 tests, 0 errors, 0 failures, 0 skipped
build/robot_world_ros_interfaces/test_results/robot_world_ros_interfaces/test_interfaces.xunit.xml: 2 tests, 0 errors, 0 failures, 0 skipped
Summary: 8 tests, 0 errors, 0 failures, 0 skipped
```
`robot_world_ros` = 5 tests (3 linters + 2 acceptance), 2 non-linter.
`robot_world_ros_interfaces` = 2 tests, 2 non-linter. Both parse cleanly.

`pixi run python scripts/check_test_integrity.py --audit-only` reports both new
packages `ok` (2 non-linter each); it notes both are new and will get baseline
entries on the first full `pixi run test` (the test-runner's job).

### 4. ROS-free packages untouched — confirmed
```
grep -rn "import rclpy\|from rclpy" src/robot_world src/robot_backends src/robot_mcp
git diff --name-only HEAD -- src/robot_world src/robot_backends src/robot_mcp
```
No runtime rclpy imports were added; every match is inside those packages'
own `test/test_no_ros_runtime.py` (sample strings / linter fixtures, pre-existing).
`git diff` against those trees is empty — they were not modified.

## Ruling deviations

1. **`CMakeLists.txt` `enable_testing()` outside the `if(BUILD_TESTING)`
   guard** (see above). Not a deviation from R1–R4 in substance — R1 requires
   the interfaces package to "ship exactly one smoke test so the gate sees a
   real, non-linter result", which this is; the guard placement is an
   environment-forced adaptation (probed: BUILD_TESTING=OFF is global in this
   workspace). No shared config touched. Flagging it because the brief's
   literal scaffold placed the test under `if(BUILD_TESTING)`.

2. **Node namespace.** R3 gives "node name `world_query`, service name
   `get_world` (→ `/world_query/get_world`)". Those three are only
   simultaneously true in ROS 2 if the node sits in the `/world_query`
   namespace. The node is therefore `world_query` in namespace `world_query`,
   so the node FQN is `/world_query/world_query` and the service FQN is
   `/world_query/get_world` as R3's arrow states. The duplicate name is the
   literal-reading cost; the acceptance path (`/world_query/get_world`) is
   pinned by the headless test's client. If the manager prefers node FQN
   `/world_query` with service `/get_world`, that is a one-line change to
   `__init__`.

## Acceptance tests (R4 plan)

- `test_get_world_returns_seed_document_exactly` — builds the node through its
  real constructor against a temp live file, invokes the handler, asserts
  `world_json == document_text(store.document())` byte-for-byte, round-trips
  through `WorldDocument.from_dict`, and checks the seed's 4 locations
  (charger/kitchen/table/living_room) and 7 objects
  (mug_1/plate_1/bowl_1/counter_1/book_1/cup_1/sofa_1).
- `test_node_builds_and_responds_headless` — `rclpy.init()`, real constructor,
  unique `ROS_DOMAIN_ID=113`, `SingleThreadedExecutor` spin in a thread, client
  against `/world_query/get_world`, asserts the response parses to the seed.
