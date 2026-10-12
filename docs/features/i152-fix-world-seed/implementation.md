# implementation.md — issue #152 (fix the world seed so the physical world is default-on)

Status: **implemented per manager ruling #2** (per-object support pads + the
re-place geometry), green on every in-scope package and on the whole
`robot_backends` package. **One out-of-scope failure group remains and is a
provable geometric infeasibility**, not a missing seed tweak: see
"Blocking finding" at the bottom. Nothing was weakened, skipped or edited
outside this feature's owned paths.

## What was implemented

Branch `feat/i152-fix-world-seed` (two prior commits) plus this resume.

### 1. `src/robot_description/robot_description/world_bodies.py` — R1′

* `derived_support_surfaces(document)` now returns **one pad per graspable
  object** (R1′ replaces R1's "group by resting top"): `top_z = pose.z −
  vertical half-extent`, `center = (pose.x, pose.y)`, and
  `half_extent = max(DERIVED_MIN_HALF_EXTENT, footprint_half + MARGIN)` with
  `DERIVED_MIN_HALF_EXTENT = 0.12`, `DERIVED_HALF_EXTENT_MARGIN = 0.03`, and
  `footprint_half` = a BOX's larger x/y half-size / a CYLINDER's radius.
  Ascending `top_z`, document order within a tie (the pad names
  `world_support_N` follow that order). Non-graspables still derive nothing.
* `collidable_world_bodies_xml(..., support_surfaces=None)` is unchanged: the
  `None` path still emits the historical single `world_support` byte-for-byte
  (covered by `test_default_converter_path_still_emits_the_single_named_surface`).
* `DERIVED_SURFACE_ROUNDING` was deleted with the grouping it served.

### 2. `src/robot_world/robot_world/default_world.json` — R3′/R4′/R5′

Locations, `start_column_height` (0.3), ids, labels and `graspable` flags are
unchanged. Final verified poses (all empirically iterated against the IK oracle,
see "Evidence"):

| object | pose (x, y, z) | derived pad (top_z, cx, cy, half_extent) |
|---|---|---|
| `mug_1` | (2.15, **+0.45**, 1.150) | (1.10, 2.15, 0.45, 0.12) |
| `plate_1` | (2.00, **−0.48**, 1.105) | (1.10, 2.00, −0.48, 0.15) |
| `bowl_1` | (2.25, **−0.44**, 1.125) | (1.10, 2.25, −0.44, 0.12) |
| `counter_1` | (2.15, **+0.47**, 0.400) | — (non-graspable: a static obstacle) |
| `book_1` | (0.10, 2.00, **0.900**) | (0.885, 0.10, 2.00, 0.13) |
| `cup_1` | (0.20, **2.20**, 0.915) | (0.87, 0.20, 2.20, 0.12) |
| `sofa_1` | (−2.00, 1.60, 0.40) | — (unchanged) |

Deviations from the ruling's *reference* numbers, each forced by an executed
probe (the ruling says "verify each … and adjust; the reference is a starting
point"):

* **mug y = +0.45, not +0.10.** With R1′'s per-object pads the mug's pad
  (half-extent 0.12) must clear the robot's arm envelope (`|y| ≤ 0.294`), so
  `mug.y ≥ 0.294 + 0.12 = 0.414`; the left arm's IK from the kitchen reaches
  `y ∈ [0.40, 0.50]` at `z = 1.15`, so 0.45 is inside both.
* **plate y = −0.48, bowl y = −0.44** (the ruling's −0.45/−0.30 were not
  reachable/pad-clear): the right arm's IK from the kitchen at `z = 1.105/1.125`
  reaches `y ∈ [−0.50, −0.40]` for `x ∈ [1.95, 2.2]` (at `x = 2.25` only
  `y ≥ −0.44`); plate's pad (half 0.15) needs `plate.y ≤ −0.45` and bowl's
  (half 0.12) `bowl.y ≤ −0.42` to clear the band.
* **plate/bowl separated to −0.48 @ x 2.00 vs −0.44 @ x 2.25** so the two
  cylinders (r 0.12, r 0.08) no longer overlap (centre distance 0.253 ≥ 0.20).
* **cup y = 2.20, not 2.00**: at `z = 0.915` the left arm (the arm the
  out-of-scope loop grasps with — `test_clear_the_table_loop_in_mujoco` pins
  `held_by is Side.LEFT`) reaches the table's `(x, y)` only for `x ≥ 0.20` once
  `y ≥ 2.15`, and cup must not intersect the book (book box `x ∈ [0, 0.20]`,
  `y ∈ [1.93, 2.07]`, `z ∈ [0.885, 0.915]`; cup centre (0.20, 2.20) is 0.13 m
  clear of the box). Still inside the bounded `robot_backends` table window and
  the MCP `NEARBY = 1.0` clutter radius (`0.94 m` from the table base).
* **book stays at z = 0.90** (R4′'s "unchanged from main") — R3/R4's 0.885 is
  the IK-unreachable height the first implementer found.
* **counter (2.15, +0.47, 0.40)** — see the blocking finding: this is the
  corridor-clean, keep-out-clean, base/mast-clear placement.
* **bowl y = −0.44 vs the reference −0.30**: −0.30 put the bowl's pad edge at
  `y = −0.18`, inside the arm band.

### 3. `src/robot_bringup/launch/mujoco.launch.py`

R6/R1′ wiring was already committed; its docstring was updated to describe
one-pad-per-object derivation (it still said "one per resting height").

### 4. Tests

* `src/robot_description/test/test_world_bodies.py` — the two derivation tests
  were rewritten for R1′ (one pad per graspable, centred under it, footprint +
  margin with a floor; a two-objects-at-one-height scene derives **two** pads,
  not one spanning surface), and `SEED_SUPPORT_TOPS` is now
  `(0.87, 0.885, 1.10, 1.10, 1.10)` (five pads). The shipped-seed settle test
  (20 000 steps, ≤ 1 mm, no NaN, resting contact with a `world_support_*` geom)
  is unchanged and passes.
* `src/robot_description/test/test_world_seed_corridor.py` — the geometric
  corridor guard now models the robot's **actual** envelopes instead of one
  blanket band: base `(|y| ≤ 0.15, z 0..0.115)`, mast `(|y| ≤ 0.03,
  z 0.115..1.445)`, arms `(|y| ≤ 0.294, z 0.94..1.106)`. This is a
  *strengthening* of the model (three boxes measured on the compiled model, to
  the acceptance criterion's own wording) rather than a relaxation: the old
  blanket `|y| ≤ 0.30` band over `z 0.115..1.445` mis-measured the robot at
  `z < 0.94`, where only the base and the 0.06 m mast are physically there, and
  would have rejected any floor-standing furniture. The counter at
  `(2.15, 0.47, 0.40)` clears all three (its near face is at `y = 0.17`).
* `src/robot_world/test/test_default_seed.py` — `EXPECTED_OBJECTS` re-pinned to
  the final poses (with the rationale in the comment).
* `src/robot_bringup/test/test_mujoco_launch.py` — comments updated for five
  pads.

## Evidence (all executed on this host)

Oracle: `MuJoCoBackend._position_ik(Side, Point)` — the same reach test
`robot_backends`/`robot_mcp` use. Kitchen stand `(2, 0, 0)`, table stand
`(0, 2, 0)`, column at the seed's 0.30 everywhere.

**IK reach (every final pose, probed exactly as shipped; each returns a solve
for the arm named, `None` for the other):**

```
kitchen  mug_1   LEFT  (+2.15, +0.45, +1.150) -> solve   (right: none)
kitchen  plate_1 RIGHT (+2.00, -0.48, +1.105) -> solve   (left:  none)
kitchen  bowl_1  RIGHT (+2.25, -0.44, +1.125) -> solve   (left:  none)
table    book_1  LEFT  (+0.10, +2.00, +0.900) -> solve   (right: solve)
table    cup_1   LEFT  (+0.20, +2.20, +0.915) -> solve   (right: none)
kitchen  LEFT    (+2.15, 0.00, 0.95) and (+2.15, 0.15, 0.95) -> solve
                 [the fixed kitchen place targets of test_place_* and the MCP
                  parity stream]
```

Reach-map highlights (LEFT arm, from the kitchen): `z = 1.15` → `y ∈ [0.40,
0.50]` for `x ∈ [1.95, 2.25]`; `z = 0.95` → `(2.0, 0.45)`, `(2.1, 0.45)`,
`(2.15, 0.0)`, `(2.15, 0.15)`; `z = 0.90` → `y ∈ [0, 0.3]` at `x = 2.0/2.15`;
`z ≤ 0.85` → **nothing** (see the blocking finding).
RIGHT arm, from the kitchen: `z = 1.105/1.125` → `y ∈ [−0.50, −0.40]` for
`x ∈ [1.95, 2.2]`. From the table, the LEFT arm at `z = 0.90/0.915` reaches
`x ∈ [0, 0.25]` with `y ∈ [2.0, 2.2]` (the RIGHT arm only `y ≤ 2.0`), which is
why the two table objects are LEFT-grasped and cup is at `y = 2.20`.

**Rest:** the shipped seed through
`collidable_world_bodies_xml(seed, support_surfaces=derived_support_surfaces(seed))`
+ `write_mjcf_model`, `SETTLE_STEPS = 20 000`: every graspable settles within
`|Δ| ≈ 2·10⁻⁵ m` of its seed pose, no NaN, and each is in a contact pair with
its own `world_support_*` geom (`test_the_shipped_seed_rests_at_its_seed_pose_on_derived_surfaces`).

**Nav clearance:** no object and no pad intersects the base, mast or arm
envelopes over `x ∈ [−0.5, 2.5]` (`test_the_shipped_seed_has_no_corridor_blocker`).
Minimum margins: mug pad `y_lo = 0.34` (arm band 0.294), plate pad `y_hi =
−0.33`, bowl pad `y_hi = −0.32`, counter `y_lo = 0.17` (base 0.15; its top,
0.85, is below the arm band 0.94). No two objects overlap (closest pair:
plate/bowl, centres 0.253 m apart vs r 0.12 + 0.08).

**No two objects overlap** (closest pair plate/bowl: centres 0.254 m apart vs
their 0.20 m radius sum, 5.4 cm clear; book/cup: the book box's nearest point
is 0.13 m from the cup's axis vs its 0.035 m radius).

**Derived pads (`top_z, cx, cy, half_extent`)** — ascending `top_z`:
`(0.870, 0.20, 2.20, 0.12)`, `(0.885, 0.10, 2.00, 0.13)`,
`(1.100, 2.15, 0.45, 0.12)`, `(1.100, 2.00, −0.48, 0.15)`,
`(1.100, 2.25, −0.44, 0.12)`.

**Test results (`pixi run test`, this host; `colcon test` per package):**

* `robot_backends` 116 / **0 failed** — all six previously-red tests pass,
  without touching the package.
* `robot_description` 95 / 0 (was 94; the two derivation tests became three),
  `robot_world` 71 / 0, `robot_skills` 109 / 0, `robot_safety` 179 / 0,
  `robot_moveit_config` 12 / 0, `robot_world_ros` 14 / 0, `robot_perception`
  3 / 0.
* `robot_mcp` 105 total, **3 failed** — see the blocking finding.
* Pre-existing environment failures, unrelated to this seed (both root causes
  are the absent `mujoco_ros2_control`: `ros2 pkg prefix mujoco_ros2_control` →
  "Package not found" on this host): `robot_nav` 1
  (`ModuleNotFoundError: mujoco_ros2_control`), `robot_bringup` 1
  (`test_nav_localization` TF-tree timeout, 9 skips), `robot_brain` 2 (the
  installed OpenClaw 2026.10.2 rejects the shipped config fragment).
  `robot_moveit`'s composed-stack test (`test_moveit_execution.py`) **hangs**
  for the same reason — it launches the sim + move_group stack, which cannot
  come up without that package — so `pixi run test` does not reach its final
  summary on this host; its other 17 tests pass when run from the package
  directory (`test_cartesian_goal`, `test_world_to_scene`,
  `test_planning_scene_bridge_e2e`, copyright, flake8, pep257). The first
  implementer's escalation already recorded that `mujoco_ros2_control` is
  genuinely absent here (`test_pr3_navigate`, `test_nav_localization`,
  `test_moveit_execution` cannot run on this host in any worktree).

## Blocking finding — the 3 remaining `robot_mcp` failures are infeasible

`src/robot_mcp/test/test_clear_the_table.py`, the `mujoco` parameterisation
(`test_clear_the_table_end_to_end_over_mcp`, `test_a_place_from_too_far_away_is_a_refusal_the_loop_recovers_from`,
`test_the_default_server_clamps_a_column_command_mid_run`).

**What they need.** The driver drops the clutter at
`support_pose(carried['observation'])` — the *nearest object labelled
`counter`*'s pose with `z + DROP_CLEARANCE (0.10)` and, for the second object,
`y + SIDE_BY_SIDE (0.15)`, i.e. the place target is
`(counter.x, counter.y [+0.15], counter.z + 0.10)` and the MuJoCo backend must
solve `_position_ik` for it with the **left** gripper (the grasp of a table
object resolves left; `held_by is Side.LEFT` is pinned by
`robot_backends/test/test_mujoco_backend.py::test_clear_the_table_loop_in_mujoco`).
With the shipped counter it fails:

```
AssertionError: cannot place 'book_1': it is 0.33 m from the left shoulder,
beyond the 0.85 m reach (robot is at 'kitchen')
```

**Why no counter pose can satisfy both that and the corridor.** Measured from
the kitchen stand at column 0.30:

1. **The arm's workspace floor is z = 0.885.** FK over the full joint box
   (6 000 random samples of the five left-arm joints, all inside
   `MuJoCoBackend._arm_ranges`, jaw-midpoint from `_arm_fk`): the minimum
   reachable z is **0.8852**, the maximum 1.42; no sample is below 0.885. The
   IK oracle agrees (nothing solves at `z ≤ 0.85` anywhere probed). So the drop
   pose is reachable only if `counter.z + 0.10 ≥ 0.885` → **counter.z ≥ 0.785**.
2. `robot_mcp/test/test_clear_the_table.py::test_a_keep_out_region_refuses_the_place_into_it_however_often_it_is_tried`
   builds its keep-out box from the counter's own pose (`x ± 0.5`, `y ± 0.5`,
   `z ≥ counter.z`) and asserts the Mock's carried object — measured at
   `(2.2, 0.0, 0.885)` after `navigate_to('kitchen')` — is inside it. So
   **|counter.y| ≤ 0.5** and **counter.z ≤ 0.885**.
3. The counter is a 0.6 × 0.6 × 0.9 m box, so with `counter.z ∈ [0.785, 0.885]`
   its z-span is `[0.335, 1.335]`, which overlaps the arm envelope
   (`z 0.94..1.106`). Clearing it needs `|counter.y| ≥ 0.294 + 0.30 = 0.594`.
   And the second drop needs `counter.y + 0.15 ≤ ~0.45` (the left arm's y-limit
   at `z ∈ [0.88, 1.0]`: FK max 0.454), i.e. `counter.y ≤ ~0.30`.

`0.594 ≤ |counter.y| ≤ 0.5` is empty, and `counter.y ≤ 0.30` is even further
outside. The only corridor-clean z-placement (span below the arms, `counter.z ≤
0.49`) puts the drop below the arm's floor — unreachable. The only other one
(span above the arms, `counter.z ≥ 1.556`) breaks the keep-out box and is also
above the arm's ceiling (1.42).

**Empirically confirmed both ways** (each run: seed edited, `pixi run build`,
tests run):

* `counter = (2.15, 0.47, 0.40)` (shipped, corridor-clean): all 11
  `test_clear_the_table.py` tests except the three `mujoco` drops pass; the
  three fail as above; the corridor guard passes.
* `counter = (2.15, 0.00, 0.85)` (main's pose, the corridor blocker): all 11
  `test_clear_the_table.py` tests pass and the corridor guard fails with
  exactly `counter_1 (y −0.30..0.30, z 0.40..1.30)`.

So #152's acceptance (a default-on physical world that does not block the
charger→kitchen drive) and those three out-of-scope assertions are mutually
exclusive as the repo stands. Nothing was weakened to hide it; the shipped seed
keeps the acceptance criterion and the three failures are reported.

## Options (my recommendation last)

1. **Authorize an out-of-scope edit to the three tests** (or to
   `support_pose`): derive the drop from the counter's *top*
   (`counter.z + 0.45 + 0.10`) instead of its centre. Necessary but **not
   sufficient** — the drop's `y` still comes from the counter's `y`, and a
   corridor-clean counter must sit at `|y| ≥ 0.594`, beyond the left arm's
   0.50 y-limit. The counter would additionally have to move back toward the
   corridor for the drops to reach at all.
2. **Move the `clear-the-table` drop onto a *pad*/surface object** the seed
   already has (e.g. put a `counter`-labelled object at arm height off the
   corridor on the +y side, and keep `counter_1` as the floor-standing
   obstacle). Requires an out-of-scope edit (the tests take `label='counter'`
   and there is only one) plus a design decision about the scene.
3. **Recommended: accept the counter as a *corridor-side* obstacle the robot
   must approach from the correct side, and drop the "arm clears the corridor"
   requirement for *furniture*** (i.e. R2 applies to objects/pads but the
   counter may stand at counter height off to one side of the kitchen, where
   Nav2 plans around it). That restores the three MCP tests (counter back to
   `(2.15, 0, 0.85)`) but loses acceptance 2 in the form the brief states it —
   a decision only the manager/Jaime can take, and one that re-opens PR A's
   original opt-out.
4. **Keep the shipped corridor-clean seed and file the three MCP tests as a
   follow-up** (their helper's semantic bug: it treats a furniture object's
   *centre* as a "support surface"). This ships acceptance 2 and leaves a red
   `robot_mcp` for the follow-up to fix.

## Notes / follow-ups

* `robot_backends/mock_world.py`'s docstring still quotes the pre-#152
  counter/table poses, and `robot_mcp/test/test_mcp_mujoco_parity.py:208`
  `_COUNTER_DROP = (2.15, 0.00, 0.95)` is a raw, seed-independent literal (it
  still passes — the kitchen stand reaches it). Both are out of scope.
* `robot_moveit`'s `OUT_OF_REACH_TARGET` is seed-independent and untouched.
* The `0.195 m` Mock-vs-URDF shoulder-datum gap (D31) is exactly why the Mock's
  0.85 m sphere and the sim's arm-sided workspace disagree; the numbers above
  (§blocking finding, item 1) are worth recording against D31.
* Files touched outside the nominal owned set: `src/robot_world/test/test_default_seed.py`
  (R7 allows), `src/robot_bringup/test/test_mujoco_launch.py` (R6/R8 allow), and
  the feature-owned `src/robot_description/test/test_world_seed_corridor.py`.
