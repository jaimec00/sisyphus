# Implementation — i87-robotmodel-reads-from-urdf (PR6)

`RobotModel` reads its six kinematic constants from the shipped URDF (D23
payoff). `robot_backends` now depends on `robot_description` one-way; the
loader lives in `robot_description`, returns plain data, and imports no ROS
runtime, so `robot_backends` keeps its "no ROS import" invariant.

## What changed

### `robot_description/robot_description/robot_model.py` (new)
The loader. Public surface is one dataclass + one function:

- `RobotModelConstants` — frozen dataclass of the five scalars + a
  `(x, y, z)` tuple for `home_gripper_offset`.
- `load_robot_model() -> RobotModelConstants` — reads the five declared
  `<xacro:property>` values from the shipped `arm.xacro` / `column.xacro`
  (stdlib `xml.etree`), then expands `robot.urdf.xacro` via the `xacro` Python
  API and derives `home_gripper_offset` by forward kinematics.

Loader internals:
- `_urdf_dir()` resolves `urdf/` relative to the package's own `__file__`
  (`.resolve()` follows the symlink-install links), so it needs **no
  `ament_index_python`**. This is the key to keeping `robot_backends`
  ROS-free at import time (see D30 / `test_no_ros_runtime`).
- `_read_property()` reads a `<xacro:property>` element literally and fails
  loudly if it is missing or non-numeric — a silent default would be exactly
  the drift D23 retires.
- `_link_origin()` + the rotation helpers mirror the gate's
  `_link_origin_in_base_link`: compose joint `<origin>` (xyz + rpy) from
  `base_link` down, at the zero pose (no joint value). The home offset is the
  midpoint of the two fingertip origins minus the shoulder origin.
- The derived home offset is rounded to 9 decimals (`_ROUND_NDIGITS`), folding
  the one-ULP float noise (~5e-17) of the composition back to the clean
  literals (0.35, 0, -0.05) and matching the gate's `PLACEMENT_TOL_M = 1e-9`.

### `robot_backends/robot_backends/mock_world.py` (edited)
`RobotModel` stays `@dataclass(frozen=True)`. Its six fields switch from
literal defaults to `field(default_factory=<per-field URDF reader>)`, backed by
a module-level `@lru_cache` `_urdf_defaults()` that calls `load_robot_model()`
once. `RobotModel()` loads from the URDF; explicit construction
(`RobotModel(reach_radius=0.5)`) still works, and the `Point` is built here
(where `robot_skills` is already a dependency), not in the loader.

### Package manifests
- `robot_description/package.xml`: `xacro` and `urdfdom_py` become
  `exec_depend` (the loader needs them at runtime). Both are ROS-free Python
  modules, so this does not introduce a ROS runtime edge. The gate's own
  `test_depend`s (`ament_index_python`, `urdfdom`, `xacro`,
  `robot_state_publisher`) are unchanged.
- `robot_backends/package.xml`: adds `<depend>robot_description</depend>`.

### `robot_backends/test/test_urdf_model.py` (new — the golden test)
Three tests, all absolute (D29), not self-consistent:

1. `test_loader_reads_the_urdf_constants_exactly` — `load_robot_model()` equals
   the five literals and `(0.35, 0, -0.05)`.
2. `test_default_robot_model_loads_from_the_urdf` — `RobotModel()` defaults
   equal the literals and the home offset is the correct `Point`.
3. `test_explicit_overrides_still_win_over_urdf_defaults` — a caller-supplied
   field wins; the rest read the URDF.

## Key rulings and why

See `status.md` for the full record. The two that mattered most in the build:

- **R1 (loader in `robot_description`, one-way dep):** confirmed by the build.
  The only trap was `robot_description`'s `ament_index` entry silently
  disappearing when a `--` in a `package.xml` comment made the manifest
  unparseable — colcon then reclassified the package as `python` (not
  `ros.ament_python`) and stopped emitting its `ament_prefix_path` hook, so
  `get_package_share_directory('robot_description')` failed downstream. Fixed
  by removing the `--` from the comment; the loader then stopped needing
  `ament_index` at all (below).
- **`reach_radius` is read as the property, not derived:** confirmed it is
  absent from the expanded URDF except as a comment, so the source property is
  the only home. This is exactly the brief's gotcha.
- **`home_gripper_offset` is derived, not a literal:** FK composition at the
  zero pose reproduced `(0.35, 0, -0.05)` exactly (to 1e-16 before rounding),
  matching the gate's `test_so101_gripper_grasp_reference_matches_home_gripper_offset`.

## Out of scope / notes
- MJCF (PR7), physics, base widening (#80) — untouched.
- The loader resolves `urdf/` via `__file__` (correct for the source checkout
  and `--symlink-install` build this repo uses). A future real-wheel
  deployment that relocates `urdf/` away from the package would switch to
  `importlib.resources` package data; noted in the loader docstring.
- The gate's transcribed `ROBOT_MODEL_*` / `GRIPPER_HOME_OFFSET` constants in
  `test_description.py` are now the *only* remaining hand-typed copy; PR6 does
  not delete them (they are the description's own contract against which the
  loader is now tested), but they are no longer the source of truth for
  `RobotModel`.
