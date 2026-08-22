# meshes/arm — SO-101 arm STL meshes

Collision and visual STL geometry for the two SO-101 arms (one file set shared by
both `left` and `right` instantiations in `urdf/arm.xacro`).

## Source & license
All 10 meshes here are copied verbatim from **`Vector-Wangel/XLeRobot`**:
`simulation/Maniskill/assets/xlerobot/meshes/*.stl`, which is licensed under the
**Apache License 2.0** (see the repo's `LICENSE`). The arm links in XLeRobot's
`xlerobot.urdf` reference these meshes with **no scale factor** — they are native
metre scale (verified by bounding-box probe: e.g. `Upper_Arm.stl` extent
~0.07 × 0.14 × 0.03 m, `Lower_Arm.stl` ~0.06 × 0.04 × 0.13 m).

These were not re-authored from the base/LeKiwi mesh set (those raskog STLs use an
mm→m scale and are not arm geometry).

## Files
Per arm link, a `_chassis` (printed housing) + `_motor` (Feetech STS3215) mesh, both
used in `<visual>` and `<collision>`:

| Link (in XLeRobot)         | meshes                              |
|----------------------------|-------------------------------------|
| `Base` (shoulder housing)  | `Base.stl`, `Base_Motor.stl`        |
| `Rotation_Pitch`           | `Rotation_Pitch.stl`, `Rotation_Pitch_Motor.stl` |
| `Upper_Arm`                | `Upper_Arm.stl`, `Upper_Arm_Motor.stl` |
| `Lower_Arm`                | `Lower_Arm.stl`, `Lower_Arm_Motor.stl` |
| `Wrist_Pitch_Roll`         | `Wrist_Pitch_Roll.stl`, `Wrist_Pitch_Roll_Motor.stl` |

The `meshes/` parent `README.md` documents that this `arm/` subdirectory is what
activated the `os.walk` install rewrite in `setup.py` (a nested subdir cannot be
copied by the flat `glob('meshes/*')`, D27/D29). Gripper jaw meshes are **PR5**,
not here.

## Masses
Link masses are sourced from the same XLeRobot URDF (Base 0.193, Rotation_Pitch
0.119, Upper_Arm 0.162, Lower_Arm 0.148, Wrist_Pitch_Roll 0.066 kg) and copied into
`arm.xacro` as properties. The **inertia tensors** are *not* copied from the CAD
export (opaque, no provenance); `arm.xacro` computes each link's inertia from a
solid box matching its mesh extents — see `arm.xacro`'s header for the rule.
