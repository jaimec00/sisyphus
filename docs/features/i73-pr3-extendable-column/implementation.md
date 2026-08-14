# Implementation — i73 / PR3 — Extendable column (prismatic STS3215 lift)

Branch `feat/i73-pr3-extendable-column-prismatic-sts3215`, base `origin/main`
@ `a34fcb2`. Every ruling R1–R12 is implemented as written except where §4
records an arithmetic reading of R3 that the ruling's own R7 test formula
implies; nothing was silently deviated from, and no ruling was escalated
(none turned out to be wrong).

## 1. What shipped

| path | change |
|---|---|
| `src/robot_description/urdf/column.xacro` | placeholder body replaced with the mast + carriage + prismatic lift |
| `src/robot_description/test/test_description.py` | `EXPECTED_LINKS` → 8; `ROBOT_MODEL_*_COLUMN_HEIGHT_M` constants; `MASSLESS_FRAME_LINKS` admission rule; 5 new helpers; **6 new tests**; module docstring paragraph |
| `docs/design/decisions.md` | **D31** |
| `docs/design/urdf-mjcf-pr-breakdown.md` | §PR3 → DONE, with the two amendments and the recorded contradiction |
| `docs/design/spec.md` | column added to "Description & packaging"; the false `spec.md:123` sentence fixed |
| `src/robot_description/README.md` | column entry, new gate clauses, status line |
| `scripts/test_baseline.json` | `robot_description` 14 → 21, written by the green run (D28), committed as produced |

Untouched, deliberately: `setup.py` (globs unchanged — no meshes),
`robot.urdf.xacro` (already includes `column.xacro`), `package.xml` (no new
dependency), **`src/robot_backends/`** (PR6 owns it).

## 2. The model

```
base_link ──fixed  (column_rail_joint, z = 0.78)──▶ column_rail_link   [mast]
column_rail_link ──prismatic (column_lift, z = -0.585)──▶ column_top   [carriage]
```

- `column_rail_link` — 0.06 × 0.06 × **1.33** m box, centred in its own link
  frame, so its foot sits at z = **0.115** in `base_link` (the chassis puck's
  top surface) and its head at z = 1.445.
- `column_top` — 0.14 × 0.10 × 0.08 m box offset **below** its link frame
  (`<origin xyz="0 0 -0.04">` on visual, collision *and* inertial). The link
  frame origin is the arm/head mount datum; at `column_lift = q` it sits at
  z = 0.195 + q, i.e. 0.195 m to 1.395 m above `base_link`.
- `column_lift` — prismatic, axis `0 0 1`, `lower="0.00" upper="1.20"`,
  `effort="120.0" velocity="0.15"`.

The carriage and the mast **interpenetrate on purpose** (a carriage wraps its
rail). That is the whole reason R2's parenting matters, and it is stated in the
xacro header: as parent/child the pair is filtered by MoveIt's default ACM and
by MuJoCo's parent/child exclusion; as siblings it would be filtered by nothing
and PR7 would start in penetration.

## 3. Every number, and whether it is sourced

**SOURCED** (one citation, in-repo, checked against the file):

| property | value | source |
|---|---|---|
| `min_column_height` | 0.00 m | `RobotModel.min_column_height`, `robot_backends/robot_backends/mock_world.py` |
| `max_column_height` | 1.20 m | `RobotModel.max_column_height`, same file |

**ESTIMATED** (no upstream number exists — the Nori paper is unread and the
repo records nothing; each is marked ESTIMATED in the xacro with the criterion
used):

| property | value | why that number |
|---|---|---|
| `column_rail_width` / `_depth` | 0.06 / 0.06 m | plausible aluminium linear-rail extrusion; square because there is no evidence for asymmetry |
| `column_rail_overtravel` | 0.05 m | rail left above the carriage at full extension — a carriage flush with the rail end has no hard stop and no room for a lead-screw end bearing |
| `column_carriage_width` / `_depth` / `_height` | 0.14 / 0.10 / 0.08 m | wraps the rail; the height is what separates the mount datum from the rail's bottom stop |
| `column_rail_mass` | 2.5 kg | an extruded 1.33 m mast |
| `column_carriage_mass` | 0.8 kg | a plate with a nut block on it |
| `column_lift_effort` | 120.0 N | placeholder, right order of magnitude for holding two arms + payload |
| `column_lift_velocity` | 0.15 m/s | placeholder |

**DERIVED** from the above, so a retune moves the whole column:
`column_base_z = chassis_z_offset + chassis_height/2` (R3 — from
**`base.xacro`'s** properties, not transcribed), `column_travel = max - min`,
`column_rail_length = travel + carriage_height + overtravel`,
`column_rail_joint_z = column_base_z + rail_length/2`, and
`column_lift_origin_z = column_base_z + carriage_height - min_column_height -
column_rail_joint_z`. All inertias are solid-box tensors computed from the same
properties as the shapes (R12), and the carriage's `<inertial>` carries the
same -h/2 origin as its geometry — without it the carriage would swing about a
point outside itself.

**No number is attributed to Nori Bot** (R12). D26's "~600 mm rail" is cited
only as the contradiction it is (§6).

## 4. Judgment calls

1. **R3's arithmetic.** R3 says the rail joint's origin z is
   `chassis_z_offset + chassis_height/2`; R7's test formula says the rail's
   **foot** is "rail joint origin z minus half the rail collision length". Both
   can only be true at once if the rail's collision box is *not* centred in its
   link frame. I implemented R3's stated **intent** — "puts the rail's bottom
   face on the chassis puck's top surface" — with a centred box, so the joint
   origin is `column_base_z + rail_length/2` and the foot lands at exactly
   `chassis_z_offset + chassis_height/2`. R7's assertion is written and passes
   as specified. The alternative (joint origin at 0.115, geometry offset up by
   half a rail) would have put an offset on the mast's shape too, for no gain.
2. **Box, not cylinder, for both column links** — R7 explicitly allowed either
   and asked for a box-shaped sibling helper if I chose boxes. A linear rail is
   an extrusion; and the carriage plate is not round. So `_collision_box` was
   added next to `_collision_cylinder`.
3. **`_collision_box` returns the shape's offset instead of requiring the
   identity.** `_collision_cylinder` requires identity because the base's
   numbers are read as link-frame quantities; the carriage's offset is the
   *point* of R1, so forbidding it would be wrong rather than protective. The
   callers compose the offset — which is what `_require_identity_origin`'s own
   docstring asks a later PR to do instead of deleting it. A **rotation** is
   still refused, since every caller reads a z extent as a height.
4. **`_axis_aligned_joint`** — the same guard, one level up, for joints: the
   column's height arithmetic adds z offsets from three frames, which only
   means anything while they are parallel. Tip the rail joint and a mast can be
   lying inside the chassis with the sums still passing.
5. **`_axis_in_base_link` walks the whole parent chain** (via
   `model.parent_map`) rather than composing one rpy, per R9's "after rpy
   composition" — the lift is two joints below the root and PR3.5/PR4 may add
   more. Reuses `_rotation_from_rpy`/`_rotate` verbatim.
6. **`_lift_limit` guard.** A prismatic joint with no `<limit>` is rejected by
   `check_urdf` and by `robot_state_publisher`; without the guard, three
   assertions reported that one root cause as three `AttributeError`s on
   `None`. Same role as the existing `_require_expansion`.
7. **The span test asserts more than R8.** R8's minimum (rail length ≥ travel)
   is asserted literally, plus the containment it implies physically: at the
   upper limit the carriage's top must still be on the rail, at the lower limit
   its bottom must be. Documented in the docstring as the deliberate **strong**
   form, in the same words the chassis/wheel clearance test uses about itself,
   with the design that would legitimately want it relaxed named.
8. **A sixth test R11 did not ask for:** `test_column_top_is_the_arm_mount_datum`.
   R1's central claim — "the link frame origin IS the mount datum" — is
   otherwise asserted nowhere: centre the carriage box on its frame (the
   obvious way to author a link, and what every other solid in this description
   does) and everything PR3.5/PR4 mounts at the datum starts half-buried, with
   the link set, the inertia check and the geometry check all still green.
9. **Naming.** Every column property is prefixed `column_*` so PR4's arm macro
   cannot collide with it in xacro's flat namespace — **except**
   `min_column_height`/`max_column_height`, which R4 requires to carry
   `RobotModel`'s exact field names. The exception is stated in the file.
10. **`MASSLESS_FRAME_LINKS` unchanged** (R6), with the admission rule written
    into its comment as instructed. Both new links are real solids.

## 5. Tests: what they catch, and the evidence they do

Six new tests (`robot_description` 15 → 21 tests that actually run; the ratchet
raised the floor 14 → 21 on the green run and that write is committed). Each
was confirmed by **perturbing the model and watching that test, and only that
test, fail** — not by argument:

| perturbation | test that failed |
|---|---|
| lift re-parented onto `base_link` | `test_column_lift_is_the_models_only_prismatic_joint` |
| lift retyped `fixed` | same |
| `max_column_height` retuned to 1.00 | `test_column_lift_limits_are_the_robot_model_column_bounds` |
| `effort="0"` / `velocity="0"` | `test_column_lift_declares_positive_effort_and_velocity_limits` |
| axis `0 1 0` | `test_column_lift_axis_is_vertical_in_base_link` |
| axis left `0 0 1` under `rpy="${pi/2} 0 0"` | same |
| `<axis>` deleted (URDF defaults it to `1 0 0`) | same |
| mast rooted at `base_link` z = 0 | `test_column_rail_stands_on_the_chassis` |
| mast shortened to 0.6 m | `test_column_rail_spans_the_carriage_travel` |
| carriage box centred on its link frame | `test_column_top_is_the_arm_mount_datum` |
| carriage visual moved off its collision | same |
| rail mass zeroed | `test_moving_links_have_inertia` (existing, generic) |
| `<limit>` deleted | `test_check_urdf_parses_the_expansion` + the three column limit tests + RSP, each with its own message |

The perturbation harness ran against a temporarily-edited source file and
restored it every time; `git status` is clean and the committed tree is the
unperturbed one.

## 6. Contradictions and residues, recorded rather than resolved

- **D26's "~600 mm linear rail" vs `RobotModel`'s 1.20 m of travel.** A
  single-stage carriage cannot do both. The travel bound wins (it is what the
  Mock, the safety layer and the brain's prompt already enforce), so the mast
  is authored *longer* than the travel — 1.33 m. Whether the real mechanism is
  a telescoping two-stage rail or the bound is optimistic is a question for the
  unread Nori paper and for PR6. In D31, in the roadmap's §PR3 and in the xacro
  header.
- **Does `RobotModel.column_height` mean travel or an absolute height?** No
  document answers it. Per R4 the URDF commits to **travel**; PR6, which makes
  `RobotModel` read this URDF, is where the two must agree. In D31.
- **The transcribed `ROBOT_MODEL_*` constants can drift** (R5). Said where they
  are read, with PR6 named as what retires the copy by deleting it.
- **The include-order coupling** `column.xacro` now has on `base.xacro` (R3). It
  fails loudly at expansion, which is the gate's first test; stated in the xacro
  header, the README and D31.
- **`effort`/`velocity` are placeholders** (R10) and are owed a real actuator
  model by PR6/PR7. The test asserts only presence and sign.

## 7. Where I would look hardest, if I were the red-team

1. **The R3 arithmetic reading in §4.1** — I believe the foot lands exactly on
   0.115 and that this is R3's intent; verify it against the expansion
   (`column_rail_joint` z = 0.78, rail length 1.33 → foot 0.115 = chassis top).
2. **The carriage/mast interpenetration.** It is deliberate, and the argument
   that parent/child filtering makes it safe is a claim about MoveIt's default
   ACM and MuJoCo's defaults that this repo cannot yet execute (no MoveIt, no
   MJCF until PR7). It is D29's own stated reasoning applied one link up, but
   it is reasoning, not a measurement.
3. **`test_column_lift_is_the_models_only_prismatic_joint`'s uniqueness
   clause** — PR5's parallel-jaw gripper may legitimately be a second prismatic
   joint. I made that a deliberate-edit ratchet (like `EXPECTED_LINKS`) and said
   so in the docstring; if you think a future-PR speed bump is the wrong trade,
   that is the place to argue.
4. **Whether the strong containment clause in the span test is too strong** —
   see §4.7.
5. **The estimated masses and the 120 N / 0.15 m/s limits.** Nothing in the
   repo constrains them; they are marked ESTIMATED and only their sign is
   asserted. If any is *implausible* rather than merely unsourced, say so.
6. **`_axis_in_base_link`'s tree walk** — it assumes a single-parent chain
   terminating at `base_link` and asserts rather than `KeyError`s when it does
   not.

## 8. Status

`pixi run build` then `pixi run test`: **green**, 796 tests, 0 skipped, 0
failures, audit passed. No test was removed, renamed, weakened or skipped, so
no `ALLOW_TEST_DECREASE` was needed anywhere. No escalations. No surviving
red-team NOTEs yet (no round has run).
