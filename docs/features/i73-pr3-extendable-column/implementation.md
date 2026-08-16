# Implementation — i73 / PR3 — Extendable column (prismatic STS3215 lift)

Branch `feat/i73-pr3-extendable-column-prismatic-sts3215`, base `origin/main`
@ `a34fcb2`. Every ruling R1–R12 is implemented as written except where §4
records an arithmetic reading of R3 that the ruling's own R7 test formula
implies; nothing was silently deviated from.

**Fix round 1 (red-team round 1: 4 BLOCKs, 8 NOTEs → R13–R18).** The model was
upheld; every BLOCK was in the durable prose, plus one number. Two of the
round-1 rulings turned out to be wrong on the facts (R4's height formula, R10's
"nothing in this repo constrains the velocity" premise) and are corrected in
`status.md` rather than defended.

**Fix round 2 (red-team round 3: 1 BLOCK, 1 NOTE → R19–R20).** Round 3 proved
the round-2 fixes landed (the manager's comment-only commit verified by AST
identity) and then attacked an axis nobody had: **the column's lateral
placement was ungated at all four of its translations**, so a mast five metres
from the robot passed the whole suite with `test_column_rail_stands_on_the_
chassis` green. Fixed by two relational clauses; see §9.

**§9 is the changelog for both fix rounds** — read it first if you reviewed an
earlier pass.

## 1. What shipped

| path | change |
|---|---|
| `src/robot_description/urdf/column.xacro` | placeholder body replaced with the mast + carriage + prismatic lift |
| `src/robot_description/test/test_description.py` | `EXPECTED_LINKS` → 8; `ROBOT_MODEL_*_COLUMN_HEIGHT_M` and `SAFETY_COLUMN_SPEED_CAP_MPS` constants; `MASSLESS_FRAME_LINKS` admission rule; **9 new helpers**; **11 new tests** (7 in round 1, 3 in fix round 1, 1 in fix round 2); module docstring paragraph. Both counts are `git diff origin/main \| grep '^+def '`, not prose |
| `docs/design/decisions.md` | **D31** |
| `docs/design/urdf-mjcf-pr-breakdown.md` | §PR3 → DONE, with the two amendments and the recorded contradiction |
| `docs/design/spec.md` | column added to "Description & packaging"; the false `spec.md:123` sentence fixed |
| `src/robot_description/README.md` | column entry, new gate clauses, status line |
| `scripts/test_baseline.json` | `robot_description` 14 → 21 → 24 → **25**, written by the green runs (D28), committed as produced |

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
  `effort="120.0" velocity="0.25"`.

The datum height is **both joint origins plus q** — `0.78 + (-0.585) + q` =
`0.195 + q` — and `test_column_datum_rests_on_the_mast_foot_at_the_lower_limit`
now pins the identity behind it (at zero travel the carriage rests on the
mast's foot, so the datum is one carriage-height above it). Leaving the lift
joint's own origin out of that sum is a 585 mm error, and it is the one this
PR's durable docs shipped in round 1 (B1).

The carriage and the mast **interpenetrate on purpose** (a carriage wraps its
rail: a measured 0.06 × 0.06 × 0.08 m prism at every joint value). That is why
R2's parenting is the honest description — and, per R15, that is the *whole*
justification the docs now give. The claim that the arrangement is also what
gets the pair filtered by MoveIt and MuJoCo is now written everywhere as an
**expectation, unverified until PR7 builds the MJCF**, with the `fusestatic`
caveat named: a fixed rail joint may be fused into `base_link`, in which case
the carriage's MuJoCo parent is `base_link` either way.

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
| `column_lift_velocity` | **0.25 m/s** | placeholder **with a constraint on it**: it is *capability*, and must stay strictly above `robot_safety`'s `velocity.column` *policy* cap of 0.15 m/s or that cap can never bind (R14/B2). Round 1 shipped 0.15 — the cap's exact value, guessed independently |

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
8. **A seventh test R11 did not ask for:** `test_column_top_is_the_arm_mount_datum`.
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

**Eleven** new tests (`robot_description` 14 → 25 tests that actually run; the
ratchet raised the floor 14 → 21 → 24 → 25 on green runs, and every write is
committed). Seven landed in round 1, three in fix round 1, one in fix round 2;
the count is from `git diff origin/main`, not from this sentence — round 1 said
"six" in four places because it was copied from prose (R17).

Each was confirmed by **perturbing the model and watching at least that test
fail** — not by argument. Round 1 claimed "and only that test", which was an
overclaim (B4/R16); the table below is re-measured against the shipped 24-test
suite and gives the **full** failing set for each perturbation, so a "1" is a
claim and a "3" is one too. The extra failures are correct — a rotated lift
origin really does break the height arithmetic three tests read.

| perturbation | n | tests that fail |
|---|---|---|
| lift re-parented onto `base_link` | 1 | `..._lift_is_the_models_only_prismatic_joint` |
| lift retyped `fixed` | 1 | same |
| `max_column_height` retuned to 1.00 | 1 | `..._lift_limits_are_the_robot_model_column_bounds` |
| `lower` attribute deleted (defaults to 0) | 1 | same |
| `effort="0"` | 1 | `..._lift_declares_positive_effort_and_velocity_limits` |
| `velocity="0"` | 2 | that one **and** `..._lift_can_outrun_the_safety_layers_column_cap` |
| velocity dropped to the cap (0.15) | 1 | `..._lift_can_outrun_the_safety_layers_column_cap` |
| axis `0 1 0` | 1 | `..._lift_axis_is_vertical_in_base_link` |
| `<axis>` deleted (URDF defaults it to `1 0 0`) | 1 | same |
| axis left `0 0 1` under `rpy="${pi/2} 0 0"` | 3 | that one, plus the span and datum tests (their `_axis_aligned_joint` guard) |
| mast rooted at `base_link` z = 0 | 3 | `..._rail_stands_on_the_chassis`, `..._rail_spans_the_carriage_travel`, `..._datum_rests_on_the_mast_foot_at_the_lower_limit` |
| mast shortened to 0.6 m | 1 | `..._rail_spans_the_carriage_travel` |
| mast width zeroed | 1 | `..._rail_stands_on_the_chassis` (its degeneracy guard) |
| mast drawn slimmer than it collides | 1 | `..._mast_is_drawn_as_it_collides` |
| lift origin slid up 0.02 m | 1 | `..._datum_rests_on_the_mast_foot_at_the_lower_limit` |
| carriage box centred on its link frame | 1 | `..._top_is_the_arm_mount_datum` |
| carriage visual moved off its collision | 1 | same |
| rail mass zeroed | 1 | `test_moving_links_have_inertia` (existing, generic) |
| `<limit>` deleted | 7 | `check_urdf`, RSP, and all five column tests that read a limit — each with its own message via `_lift_limit` |
| **mast joint `x=0.5`** | 1 | `..._rail_stands_on_the_chassis` (footprint clause) |
| **mast joint `x=5.0`** | 1 | same |
| **mast joint `y=0.5`** | 1 | same |
| **mast joint `x=0.13`** (just past the rim) | 1 | same — the gate is a relationship, not a pin at zero |
| **mast joint `x=0.08`** (set back, still on the puck) | 0 | **green, deliberately**: an off-centre column is a legal design |
| **lift joint `x=0.5`** | 1 | `..._carriage_wraps_the_mast` |
| **carriage shape offset `x=0.5`** | 1 | same |
| **carriage `0.02 × 0.02` on a `0.06` rail** | 1 | same |
| **rail shape offset `x=0.5`** | 2 | both lateral clauses |

Twenty-three of the twenty-eight are single-cause; one is green on purpose.
The nine lateral rows are red-team round 3's own perturbation list, re-run
against the fixed gate. Also measured, because the datum
test's value depends on it: the span test tolerates sliding the lift origin by
up to the mast's over-travel (+0.05 m green, +0.06 m red), while the datum test
catches +0.02 m.

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
- **`effort`/`velocity` are placeholders** (R10 as corrected by R14) and are
  owed a real actuator model by PR6/PR7. `effort` is asserted only for presence
  and sign. `velocity` now also has to clear the safety layer's policy cap —
  but 0.25 m/s is still a guess, and **if a real STS3215 through a real lead
  screw turns out slower than the 0.15 m/s cap, it is the cap or the mechanism
  that needs revisiting**, not this estimate sliding back under it. Recorded in
  the xacro and in D31.
- **The MoveIt/MuJoCo filtering the parenting is expected to buy is
  unverified** and stays so until PR7 builds the MJCF; a fixed rail joint is a
  candidate for `fusestatic`, which would make the carriage's MuJoCo parent
  `base_link` either way. The parenting stands on kinematic honesty alone
  (R15). Named in D31, the roadmap, the xacro, the README and spec.md so PR7
  checks it.
- **`SAFETY_COLUMN_SPEED_CAP_MPS` is a second hand-typed copy** with the same
  drift residue as the `ROBOT_MODEL_*` ones, and no PR6-shaped thing retires it.
  Drift can only make that assertion *weaker* (comparing against a cap that is
  no longer policy), never wrong.

## 7. Where I would look hardest, if I were the red-team

1. **The R3 arithmetic reading in §4.1** — I believe the foot lands exactly on
   0.115 and that this is R3's intent; verify it against the expansion
   (`column_rail_joint` z = 0.78, rail length 1.33 → foot 0.115 = chassis top).
2. **The carriage/mast interpenetration.** Deliberate; measured by round 1 and
   now *asserted* by `test_column_carriage_wraps_the_mast` (round 3 showed the
   measurement was silently falsifiable in x and y). The filtering argument
   that once justified it is written everywhere as an expectation with an
   expiry (R15) — check that no restatement of it survived as fact.
3. **`test_column_lift_is_the_models_only_prismatic_joint`'s uniqueness
   clause** — PR5's parallel-jaw gripper may legitimately be a second prismatic
   joint. I made that a deliberate-edit ratchet (like `EXPECTED_LINKS`) and said
   so in the docstring; if you think a future-PR speed bump is the wrong trade,
   that is the place to argue.
4. **Whether the strong containment clause in the span test is too strong** —
   see §4.7.
5. **The estimated masses and the 120 N effort.** Nothing in the repo
   constrains them; they are ESTIMATED and only their sign is asserted. If any
   is *implausible* rather than merely unsourced, say so — and note that the
   velocity was exactly this kind of "unconstrained" number until round 1 found
   the constraint in `robot_safety`. I asked B2's question of `effort` this
   time and searched the whole of `src/`, not one directory: the only force
   number in the repo is `limits.yaml`'s `gripper.max_force: 40.0`, which is a
   jaw force and has nothing to do with the lift. `effort` is genuinely
   unconstrained. Falsify that if you can — it is the shape of claim that was
   wrong last round.
6. **`_axis_in_base_link`'s tree walk** — it assumes a single-parent chain
   terminating at `base_link` and asserts rather than `KeyError`s when it does
   not.
7. **"Which coordinate does the gate still not know about?"** — round 3's
   question, which found the whole lateral axis, and the one I would ask again
   rather than re-checking z. My own honest list of what is still ungated after
   this round, offered so the answer is a starting point instead of a search:
   the **inertia tensors** are checked only for positive `ixx/iyy/izz`, never
   against the geometry they are supposed to be computed from, so a rail with a
   wheel's tensor passes (a PR2-inherited limit, not introduced here, and it
   would bite in PR7's MJCF rather than anywhere before it); every "drawn as it
   collides" test reads `visuals[0]`/`collisions[0]` only, so a second shape on
   a link is invisible (round 3 saw this and correctly called it pre-existing);
   the carriage's own footprint may overhang the deck without complaint; and
   nothing pins the column's lateral position to a *value* — deliberately, but
   that decision is worth re-taking when PR4 puts shoulders at
   ±`shoulder_offset_y` off this carriage.

## 8. Status

`pixi run build` then `pixi run test`: **green**, 800 tests, 0 skipped, 0
failures, audit passed; `robot_description` 25 non-lint, floor ratcheted
14 → 21 → 24 → 25. No test was removed, renamed, weakened or skipped in any
round, so no `ALLOW_TEST_DECREASE` was needed anywhere. No escalations — the
falsified rulings (R4's arithmetic, R2's MJCF rationale) were the manager's own
and were corrected by it in R13/R15.

**Surviving NOTEs, deferred by R18 to a follow-up comment on the issue (not
fixed here):**

- **N4** — the mast's foot is *coplanar* with the chassis top (zero margin),
  and so is the carriage's underside at `q = 0`. Not a penetration, and
  structurally permanent through `column_base_z`, but the base gave its wheels
  5 mm for the same reason, and "clears" is the wrong word in the docs and the
  test name. Wants a `column_foot_clearance` estimate and an explicit
  carriage-vs-chassis clause.
- **N5** — the `ROBOT_MODEL_*` transcription pin is **one-directional**:
  drifting `RobotModel.max_column_height` to 1.50 leaves every package in the
  workspace green. The URDF is pinned to the copy; the copy is pinned to
  nothing. Inherent to R5, documented where the constants are read, retired by
  PR6 — and worth saying in the PR description so nobody reads "the limits are
  asserted against `RobotModel`" as a live pin.
- **N9** — deferred with the others by the manager: the transcribed safety cap is one-directional in the same way N5's is, and a cap *raised above* the URDF's capability inverts policy and capability with this suite still green. The constant's comment now says so, and names the option that would close it without a dependency edge here (a cross-check from a third place that already depends on both — workspace tooling, or PR8's bringup). Nobody owns that.
- **N8** — the robot as now described is ~1.495 m tall on a 0.25 m wheel
  circle; CoM ≈ 0.38 m at full extension gives a static tip angle ≈ 18°, before
  PR4 adds two arms and a payload at 1.4 m. Both drivers are inherited (LeKiwi's
  `base_radius`, `RobotModel`'s travel), so it is not a PR3 defect, but it is a
  real stability question for PR4/PR7.

## 9. Round-2 changelog (fixes for red-team round 1)

| ruling | what changed |
|---|---|
| **R13 / B1** | The datum-height formula was wrong by 585 mm (`column_lift`'s own origin omitted) in D31, `column.xacro` and `status.md` R4. Corrected in all three, plus two places the manager's list did not name: the failure message of `test_column_lift_limits_...` and the roadmap's §PR3 amendment. **Pinned** by a new test — at the lower limit the datum sits one carriage-height above the mast's foot. |
| **R14 / B2** | `column_lift_velocity` 0.15 → **0.25** m/s. Its comment now states the capability-vs-policy distinction, cites `limits.yaml`'s `velocity.column` as the floor it must clear, notes that the *height* bounds are deliberately the opposite case (policy == capability is right for a position stop), and records the residue if a real STS3215 turns out slower than the cap. New constant `SAFETY_COLUMN_SPEED_CAP_MPS` + new test asserting the strict inequality. No `robot_safety` dependency. R10 and `context.md` Q4 corrected, naming the `docs/design/`-only grep that produced the false premise. |
| **R15 / B3** | The MoveIt/MuJoCo filtering claim is demoted from fact to *expectation, unverified until PR7*, with the `fusestatic` caveat named, in D31 clause 3, the xacro header, README, spec.md — and in the roadmap and **two test docstrings**, which the ruling did not list. The parenting itself is unchanged and now rests on kinematic honesty plus the measured overlap prism. |
| **R16 / B4** | "and only that test" dropped from D31 and §5; §5's table re-measured and now reports the *full* failing set per perturbation (15 of 19 single-cause). |
| **R17** | "the gate grew by six" → **ten** (14 → 24 by `git diff`, not by prose). Round 1 also said "six" in `implementation.md` twice; both fixed. |
| **R18 / N1** | `lower` and `upper` are now also asserted as *declared attributes of the raw expansion* (`_limit_attributes`) — URDF defaults an omitted `lower` to 0, which left the suite green. |
| **R18 / N2** | The mast gets the positive-dimension guard the chassis and carriage already had; a zero-width mast passed everything. |
| **R18 / N3** | New test: the mast's `<visual>` must match its `<collision>`, as the chassis's and carriage's already must. |
| **R18 / N6** | The merge-order graph's `(done)` markers moved to their own line, restoring the branch anchors to their original columns (verified by column index, not by eye). |
| **R18 / N7** | `spec.md`'s head-camera row no longer claims a `head_camera_link` that does not exist; it is PR3.5's, in future tense. |

**The N+1 sweep** (three of four BLOCKs were one claim wrong in several
places, so each fix was followed by a grep for every restatement). It found
four more instances the rulings did not list: the datum formula inside a test's
own failure message; the filtering claim in
`test_column_lift_is_the_models_only_prismatic_joint`'s docstring, in that
test's assertion message, and in the roadmap's §PR3 amendment. It also caught a
claim I wrote *during* this round — the new datum test's docstring asserted the
span test would tolerate a 200 mm slide of the lift origin; measured, it does
not (it tolerates the 50 mm of over-travel and no more), so the docstring now
states the measured numbers.

### Fix round 2 — red-team round 3 (R19–R20)

| ruling | what changed |
|---|---|
| **R19 / B5** | **Every x and y in the column was ungated.** Orientation was gated at all four of the column's frames (`_axis_aligned_joint`, `_box_geometry`) and translation at none of them, so seven single-edit perturbations — mast at `x=0.5`, at `x=5.0`, at `y=0.5`; lift joint at `x=0.5`; rail shape offset; carriage shape offset; a `0.02 × 0.02` carriage on a `0.06` rail — each left **all 24 tests green**, with a test *named* `..._rail_stands_on_the_chassis` passing for a mast in the next room. Two relational clauses fix it: the mast's collision footprint must lie inside the chassis puck's radius (true corner distance `hypot(\|x\| + w/2, \|y\| + d/2)` for an axis-aligned box — not `hypot(centre) + half-diagonal`, which would reject a legal mast near the rim), and the new `test_column_carriage_wraps_the_mast` requires the carriage's cross-section to contain the rail's, composed through the lift joint's origin. All seven perturbations now red; a legal off-centre mast stays green. |
| **R20 / N10** | The safety-cap comment cited a ranking (`the inversion column.xacro calls worse than the equality`) that `column.xacro` did not make — it listed both failure modes without ranking them — and the ranking's real source, `status.md` R14, is deleted at merge. Resolved durably: **`column.xacro` now states the ranking and why** (equality is a guard that has stopped guarding; the inversion is a guard that is *wrong about the machine*, making an unaudited description number the effective limiter and turning a permitted command into actuator saturation). The same comment no longer says a live cross-check is impossible before PR6 — it is impossible *from here*, and a third place that already depends on both packages could do it. Named as an option nobody owns, not as a plan. |

**What I asked of my own new assertions**, per round 3's lesson: the footprint
clause constrains the *mast* only and is a containment rather than a placement
(an off-centre column is legal; a cantilevered one would fail it and should
relax it), it assumes nothing about the chassis being centred — it **asserts**
that instead — and the carriage's own footprint is bounded only indirectly
through the wrap clause. The wrap clause is the strong (full-containment) form,
which a C-profile guide block would legitimately fail, and it is q-independent
*because* the axis test pins the travel direction to +z, a dependency the
docstring names rather than leaving to be noticed. All of that is in the two
docstrings.
