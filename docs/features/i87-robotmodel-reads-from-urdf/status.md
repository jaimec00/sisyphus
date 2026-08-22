# Status — i87-robotmodel-reads-from-urdf (PR6)

Brief: issue #87. Make `RobotModel` read its kinematic constants from the
shipped URDF (the D23 "source of truth" payoff).

## Rulings (binding for this run; challenge them, don't silently deviate)

### R1 — Loader package placement + dependency direction

The loader lives **in `robot_description`** as
`robot_description/robot_description/robot_model.py`, exposing a single
`load_robot_model() -> RobotModelConstants` function. The dependency direction
is **`robot_backends` → `robot_description`** (one-way; `robot_description`
never imports `robot_backends`, so no cycle and no fragile back-edge).

Why this package, and this direction:

- The URDF already lives in `robot_description`; its gate (`test/test_description.py`)
  already expands and parses it. The loader is a natural sibling of the data it
  reads, not a new boundary package for one function.
- A new `robot_model_from_urdf` package buys nothing and costs a third package
  to wire into colcon, pixi, and the test ratchet. `robot_description` is the
  seam the brief names as the dependency boundary — the hardware description is
  more foundational than the backend that consumes it.
- `robot_description` returns **plain data** (a frozen dataclass of floats + a
  3-tuple), so it adds **no `robot_skills` runtime dependency** and stays the
  pure "description is XML + one loader" package it is. `robot_backends` wraps
  the result into its existing `RobotModel` (building the `Point` there, where
  `robot_skills` is already a dependency).

New dependency edges this adds (all one-way, no cycle):

- `robot_description` gains `exec_depend` on `ament_index_python`,
  `urdfdom_py` and `xacro` (currently only `test_depend`s) — the loader needs
  them at *runtime* to resolve the installed share dir, expand, and parse.
- `robot_backends` gains `<depend>robot_description</depend>`.

### R2 — Constant → URDF mapping

| `RobotModel` field | literal | URDF home | how the loader reads it |
|---|---|---|---|
| `shoulder_offset_y` | 0.18 | `arm.xacro` `<xacro:property name="shoulder_offset_y">` | property value (xacro source) |
| `shoulder_offset_z` | 0.50 | `arm.xacro` `<xacro:property name="shoulder_offset_z">` | property value (xacro source) |
| `reach_radius` | 0.85 | `arm.xacro` `<xacro:property name="reach_radius">` | property value (xacro source) |
| `min_column_height` | 0.00 | `column.xacro` `<xacro:property name="min_column_height">` | property value (xacro source) |
| `max_column_height` | 1.20 | `column.xacro` `<xacro:property name="max_column_height">` | property value (xacro source) |
| `home_gripper_offset` | (0.35, 0, -0.05) | **derived**: grasp midpoint vs shoulder at zero pose | FK over the expanded, parsed `robot.urdf` |

The five scalars are **declared `<xacro:property>` elements** in the installed
`arm.xacro` / `column.xacro`. The loader reads them literally from those
elements (stdlib `xml.etree.ElementTree`), never derives them.

This is load-bearing for `reach_radius`: it is **not referenced by any geometry**
in the expanded URDF — verified — so it appears in the expansion only as a
comment and is unrecoverable from the parsed model. Reading the *property*
(not the arm's actual reach from link lengths) is exactly what the brief's
gotcha demands ("the two cannot silently diverge"). `shoulder_offset_y/z` and
the column bounds *also* exist as properties, so reading all five the same way
keeps one mechanism, and the golden test + the existing gate cross-check the
geometry-derived copies (mount origin / column limit) separately.

`home_gripper_offset` is genuinely **derived**: the midpoint between the two
fingertip link origins minus the shoulder link origin, in `base_link` coords at
the zero pose (all revolute joints at 0). This mirrors the gate's
`test_so101_gripper_grasp_reference_matches_home_gripper_offset` exactly —
compose each joint `<origin>` (xyz + rpy) from the root down to the tip links
and to the shoulder link, no joint *value* (zero pose ⇒ identity rotation). The
loader matches the literal to the same `PLACEMENT_TOL_M` the gate uses.

### R3 — RobotModel stays a dataclass for explicit overrides

`RobotModel` remains `@dataclass(frozen=True)`. Its six fields switch from
literal defaults to `field(default_factory=<per-field URDF reader>)` backed by a
module-level cached `_load_defaults()`. `RobotModel()` therefore loads from the
URDF; `RobotModel(shoulder_offset_y=0.2, ...)` and
`RobotModel(**vars(existing))` still work unchanged — default_factory only
kicks in for fields the caller did not pass.

### R4 — Golden test is absolute (D29)

A new test in `robot_backends` asserts `load_robot_model()` (and the default
`RobotModel()`) equals the five literals and `(0.35, 0, -0.05)` **exactly**
(floats to reasonable `==`). It is *not* a self-consistency check — it pins
parsed/derived values to the literals, not back to the same URDF.
