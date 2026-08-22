# implementation.md — PR5 (SO-101 parallel-jaw grippers), issue #85

Implementer's record of what landed and how, for the red-team and reviewer.
Rulings R1–R6 are followed; one ruling detail is corrected (flagged below, in
the spirit of "a downstream agent that believes a ruling is wrong escalates
in-process rather than silently deviates").

## What landed

- `src/robot_description/urdf/gripper.xacro` — a `so101_gripper` macro
  (parameterized by `name`, `mount_xyz`, `mount_rpy`, `meshes_dir`, and the
  fingertip box `tip_x/tip_y/tip_z/tip_mass`), instantiated `left` + `right`
  off each arm's `<side>_wrist_roll_link` via a fixed `<side>_gripper_mount_joint`.
- `src/robot_description/meshes/gripper/` — `Fixed_Jaw.stl`, `Fixed_Jaw_Motor.stl`,
  `Moving_Jaw.stl` (XLeRobot, Apache-2.0) + an attribution `README.md`.
- `src/robot_description/test/test_description.py` — SUBASSEMBLIES + EXPECTED_LINKS
  + ARM_LINKS/ARM_JOINTS grown; three new gate tests (mimic wiring, home offset,
  fingertip parameterization) plus a fourth (presence/follows-`gripper`-key).
- `src/robot_description/urdf/robot.urdf.xacro` — added the `<xacro:include
  filename="gripper.xacro"/>`.

## Joint/link model (per side)

```
<side>_wrist_roll_link —fixed (<side>_gripper_mount_joint, identity)→ <side>_gripper_base_link  [Fixed_Jaw + Fixed_Jaw_Motor]
<side>_gripper_base_link —revolute (<side>_gripper, axis +y, id 6 driven)→ <side>_gripper_upper_jaw_link  [Moving_Jaw]
<side>_gripper_base_link —revolute (<side>_gripper_mirror, axis +y, mimic −1)→ <side>_gripper_lower_jaw_link  [Moving_Jaw]
<side>_gripper_upper_jaw_link —fixed (<side>_gripper_upper_tip_joint)→ <side>_gripper_upper_tip_link  [box]
<side>_gripper_lower_jaw_link —fixed (<side>_gripper_lower_tip_joint)→ <side>_gripper_lower_tip_link  [box]
```

Masses SOURCED (`fixed_jaw_mass` 0.0929859…, `moving_jaw_mass` 0.0202444…, R4/D29);
inertias computed from solid prisms of the mesh bbox extents (PR4 arm rule),
never the vendored CAD tensors. Fingertip mass ESTIMATED 0.01 kg each, box inertias.

## Ruling correction (R1, axis convention)

R1 offers "both `<axis>` anti-parallel (or the mirror origin rotated π)". Taken
literally with `<mimic multiplier="-1">`, anti-parallel axes make the two joints
rotate in the **same** physical direction and never close a gap: composition of
"axis −y by angle −q" is "axis +y by angle +q", identical to the driven joint.
The symmetric parallel jaw is realized instead with **both** jaw axes `+y` and
the mirror jaw's pivot + lever reflected across the x–y grip plane, so
`<mimic multiplier="-1">` gives equal-and-opposite tip motion. This matches
R1's stated *outcome* — one driven + one mimic, multiplier −1, symmetric closing,
tips meet the grasp reference at q=0 — and was verified by composition (see
below). Flagged, not silently deviated: the intent (symmetric −1-mimic parallel
jaw) is honoured exactly.

## home_gripper_offset (R3)

Measured the zero-pose wrist-roll frame from the actual parsed model (forward
composition from `base_link`, not the hand-compose in status.md): `wrist_roll_link`
sits at `(0.3834, ±0.18, 0.7912)` in base, with +x pointing down (−z), +y aft
(−x), +z outboard (±y). The grasp reference — midpoint of the two fingertip
frames at q=0 — therefore lands at wrist-frame `(+0.1462, +0.03337, 0)`, which
is `grip_reach` (down) and `tuck` (aft) in `gripper.xacro`. Composed to
shoulder-relative REP-103 this is exactly `(0.35, 0, −0.05)` for both arms,
asserted at PLACEMENT_TOL_M by
`test_so101_gripper_grasp_reference_matches_home_gripper_offset`.

Note: status.md's R3 numbers (0.146, 0.033 m) are correct; the earlier
"measured" walk-up helper in my scratch was buggy and reproduced a wrong pose —
the model itself is right, and the gate now composes with a correct
`_link_origin_in_base_link` helper (root→tip transform accumulation).

## Joint limits

`<side>_gripper` and `<side>_gripper_mirror` both declare `lower=-1.5 upper=0`
(0 = closed), `effort=3.0`, `velocity=3.5` (ESTIMATED, same order as arm.xacro's
STS3215 stand-ins; not cribbed from upstream's effort=0 velocity=0).

## Verification

- `pixi run build` green.
- `xacro` expansion + `check_urdf` parse green (mimic accepted; full 5+body tree).
- Per-package description suite: 39 passed (35 prior + 4 gripper tests).
- Full `pixi run test` (whole workspace) — see report; ratchet baseline for
  `robot_description` auto-bumped and committed.
