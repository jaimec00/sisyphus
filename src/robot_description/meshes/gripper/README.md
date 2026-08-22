# meshes/gripper — SO-101 parallel-jaw gripper STL meshes

Visual and collision STL geometry for the two SO-101 parallel-jaw grippers (one
file set shared by both `left` and `right` instantiations in `urdf/gripper.xacro`).

## Source & license
Copied verbatim from **`Vector-Wangel/XLeRobot`**:
`simulation/Maniskill/assets/xlerobot/meshes/*.stl`, licensed under the
**Apache License 2.0** (see the repo's `LICENSE`). Same substrate and rule as the
arm meshes in `meshes/arm/` (D26/D29: cribbed as facts/files with attribution,
native metre scale — verified by bounding-box probe, e.g. `Fixed_Jaw.stl`
extent ~0.065 × 0.106 × 0.048 m, `Moving_Jaw.stl` ~0.022 × 0.092 × 0.048 m).

PR5 uses the visual STLs for BOTH `<visual>` and `<collision>` (the arm PR4
convention); the upstream `.ply` convex collision files are not vendored. The
**fingertips** are NOT meshed here — they are a macro-parameterized swappable
link authored as rigid primitives (see `urdf/gripper.xacro` and issue #85), so a
fin-ray/compliant fingertip is a later geometry swap rather than a re-model.

## Files
| Link (in XLeRobot / gripper base) | meshes                          |
|-----------------------------------|---------------------------------|
| `Fixed_Jaw`  (gripper base body)  | `Fixed_Jaw.stl`, `Fixed_Jaw_Motor.stl` |
| `Moving_Jaw` (jaw body)           | `Moving_Jaw.stl`                |

The arm meshes live in `meshes/arm/`; the base/column are primitives (no meshes,
D29).
