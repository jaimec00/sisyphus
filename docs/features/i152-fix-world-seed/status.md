# status.md — issue #152 (fix the world seed so the physical world is default-on)

Worktree `/home/sisyphus/worktrees/i152-fix-world-seed`, branch `feat/i152-fix-world-seed`, off `origin/main` `433525e6`. Manager: Sisyphus subagent (worktree manager).

## Brief (restated)

PR A (#150) materialized collidable world bodies + a finite support surface but gated them behind `world_objects` (default false) because the shipped seed is physically incoherent. This PR makes the seed coherent so the physical world can be **default-on**, without breaking `test_pr3_navigate` (charger→kitchen).

## Verified facts the design rests on (from context.md, all empirically observed)

- Robot whole-body max |y| = **0.2935 m** (arms home), max |x| = 0.4914, mast 0.06×0.06 z 0.115..1.445 (fused into `base_link`); base alone r = 0.15. Arms at column 0.30 sit at z 0.93..1.20.
- The nav corridor is the line **y = 0, x ∈ [0, 2]** (charger (0,0) → kitchen (2,0)). Locations are frozen.
- `world_support` slab (2 m, top z 1.10, centre (2.15, 0)) + `counter_1` (0.6×0.6×0.9, centre (2.15, 0, 0.85), z 0.40..1.30) physically block the corridor (contact from x≈1.12). The **map** does not block it (footprint radius 0.05; the y=0 row is FREE) — the **physics** does.
- Binding `robot_backends` test (`test_mock_world.py:35-60`) requires ≥2 graspables within a **3-D 1.0 m** norm of the `table` stand (0, 2, 0) **and each reachable** (reach radius 0.85, shoulder z 0.80 at seed column 0.30). So **book/cup cannot go to the floor** (a floor object is 0.83 m below the shoulder → unreachable) and **cannot rise above ~z 0.99** (falls out of the 1.0 m window). They must stay ≈ z 0.9 at the table.
- Kitchen cluster (mug/plate/bowl) has no reachability test pinning it; only scene plausibility + R1 + R2 + `test_default_seed.py` pins.
- The two clusters sit at **different heights** (kitchen z 1.15, table z 0.90), so **one** support surface cannot serve both. The converter currently emits exactly one surface.

## Rulings

### R1 — Two support surfaces, derived from graspable-object resting heights (the mechanism)
`collidable_world_bodies_xml` today emits one surface at `SUPPORT_CENTER`/`SUPPORT_TOP_Z`. Extend it minimally and **backward-compatibly**:

1. Add `support_surfaces: Sequence[tuple] | None = None` keyword to `collidable_world_bodies_xml`. When `None`, keep the existing single-surface emission (unchanged — the existing `test_world_bodies.py` stays green). When a list, emit one **static** surface body per entry (each `(top_z, center_x, center_y, half_extent)`), named `world_support_0`, `world_support_1`, … in order, each a finite box of half-thickness `SUPPORT_HALF_THICKNESS` with its top face at `top_z`.
2. Add `derived_support_surfaces(document) -> list[tuple]` in `world_bodies.py`:
   - For each **graspable** object, `top_z = pose.z − vertical_half_extent` (BOX → `dims[2]`, CYLINDER → `dims[1]`).
   - Group graspables by `round(top_z, 6)` (exact in the seed; tolerant for a live world).
   - Per group: `center = (mean x, mean y)`; `half_extent = max(0.20, half_span + 0.15)` where `half_span = max(|x−cx|, |y−cy|)` over the group. Emit in ascending `top_z` order.
   - Non-graspable objects contribute **no** surface (they are static obstacles, not resting places — R1 of the brief).
3. `mujoco.launch.py::_world_bodies_xml` now returns `collidable_world_bodies_xml(document, support_surfaces=derived_support_surfaces(document))`.

### R2 — Corridor clearance (nav must stay green)
Any physical body (object or surface) whose z-span intersects the robot's reach envelope (mast z 0.115..1.445 **or** arms z 0.93..1.20) must keep its horizontal footprint clear of the corridor band **|y| ≤ 0.30** for **x ∈ [−0.5, 2.5]** (margin over max|y| = 0.2935). Concretely: every obstacle's nearest edge at |y| ≥ 0.30. This is the acceptance-2 invariant; the new regression test asserts it geometrically (below).

### R3 — Kitchen cluster (mug/plate/bowl) rests on a kitchen counter surface (top z 1.10)
Move mug/plate/bowl to the kitchen's +y side, clear of the corridor, resting on a derived surface of top z **1.10**. Each object's `z = 1.10 + its vertical half-extent` (exact rest, no float/fall):
- mug (CYL r 0.04, half 0.05) → z **1.15**
- plate (CYL r 0.12, half 0.005) → z **1.105**
- bowl (CYL r 0.08, half 0.025) → z **1.125**

Reference x/y (verify no mutual overlap and corridor clearance): surface centre ≈ (2.0, 0.75), half-extent ≈ 0.4 → spans y 0.35..1.15; mug (2.0, 0.75, 1.15), plate (2.0, 0.50, 1.105), bowl (2.15, 0.90, 1.125). Implementer picks the exact coordinates; the red-team verifies rest + clearance + no self-overlap.

### R4 — Table cluster (book/cup) rests on a table surface (top z 0.87)
book/cup stay at the `table` location (0, 2.0) on a second derived surface of top z **0.87**. `z = 0.87 + half-extent`: book (BOX half 0.015) → **0.885**; cup (CYL half 0.045) → **0.915**. Keep x/y near (0.1, 2.0)/(0.15, 2.0) **and separated so book and cup no longer intersect** (book x-span 0.20; cup diameter 0.07 — the shipped seed overlaps them). Both must remain within a **3-D 1.0 m** norm of the table stand and reachable from it (verified by the existing `robot_backends` test). Reference: book (0.10, 2.00, 0.885), cup (0.30, 2.00, 0.915).

### R5 — The counter moves clear and stands on the floor
`counter_1` is a static kitchen cabinet, **not** a resting surface. Move it clear of the corridor and of every surface/object, and stand it on the floor (bottom at z −0.05 → centre z = **0.40**, top 0.85; fixes its shipped 0.40 m float). Reference: (2.6, 0.70, 0.40) (spans x 2.3..2.9, y 0.4..1.0, z −0.05..0.85 — clear of the kitchen surface z 1.00..1.10 and the corridor). Its `counter` label/geometry (0.6×0.6×0.9) and id stay unchanged.

### R6 — `world_objects` defaults to true
In `mujoco.launch.py`, change `DeclareLaunchArgument('world_objects', default_value='false', …)` → `'true'`, and rewrite the module docstring rationale (the "default false because the shipped seed is incoherent" note now reads "default true; the seed is coherent"). Add/adjust a `robot_bringup` test asserting the default is now `'true'` (the `test_mujoco_launch.py` pattern).

### R7 — Seed edits + the pins they require
Edit `src/robot_world/robot_world/default_world.json`: new poses for mug/plate/bowl/counter/book/cup per R3–R5; **locations and `start_column_height` (0.3) and the object id/label set and `graspable` flags are unchanged**. Update `test_default_seed.py::EXPECTED_OBJECTS` (and only the object poses) to match exactly. `test_static_map.py` needs **no** change (its furniture/location *sets* and its cell/bounds assertions hold under any pose move), but the implementer must run it to confirm. Do **not** touch `robot_backends/`, `robot_mcp/`, or `robot_moveit/` (their counter/mug literals become stale-but-valid; `robot_moveit`'s `OUT_OF_REACH_TARGET=(2.10,0.10,1.15)` is explicitly seed-independent).

### R8 — Tests (acceptance)
1. **Rest regression** (in `robot_description/test/test_world_bodies.py` or a sibling): feed the **shipped seed** through `collidable_world_bodies_xml(default_seed_document(), support_surfaces=derived_support_surfaces(default_seed_document()))` + `write_mjcf_model`, `SETTLE_STEPS=20000`, and assert **every graspable** object settles at its seed pose (|x,y,z − seed| < 1e-3, no NaN) and that each graspable's geom forms a contact pair with a `world_support_*` geom (really resting).
2. **Corridor-clear regression** (geometric, no MuJoCo/sim, always runs): assert no physical object (and no derived surface) intersects the corridor band |y| ≤ 0.30 over x ∈ [−0.5, 2.5] for z overlapping the robot envelope. This is the fast nav-safety guard; `test_pr3_navigate` (now materialized by default) is the heavyweight R2 gate.
3. Update `scripts/test_baseline.json` for whatever floors rise (do **not** lower any floor).
4. `pixi run build` then the full `pixi run test` must be green, including `test_pr3_navigate` (skips only if `mujoco_ros2_control` is absent; on this host it is present — verify).

### R9 — Out of scope / do not touch
`robot_backends/**`, `robot_mcp/**`, `robot_moveit/**`; the gripper (PR B); the pick-and-place chore (PR C). The `0.195 m` Mock-vs-URDF shoulder-datum gap (context §2/§7.7) is a **D31 follow-up**, not fixed here. The sofa's float is a pre-existing non-defect (non-graspable, off-corridor); leave it.

## Implementation order (for the implementer)
1. `world_bodies.py`: `derived_support_surfaces` + `support_surfaces` keyword (backward-compat).
2. `default_world.json` + `test_default_seed.py::EXPECTED_OBJECTS`.
3. `mujoco.launch.py`: flip default + use derived surfaces + docstring.
4. Tests (R8) + `test_mujoco_launch.py` default assertion + ratchet.
5. `pixi run build` && `pixi run test`; commit in small increments; write `implementation.md`.

---

## Implementer escalation (2026-10-12) — R1/R2/R3/R4 are jointly infeasible

Full evidence in `implementation.md` (E1–E5). Summary:

* **The rulings missed the sim's real IK workspace.** `robot_backends.mujoco_backend`
  (and `robot_mcp`'s MuJoCo paths) judge `grasp`/`place` reach with
  `ik.solve_ik` — *not* the Mock's 0.85 m sphere. Probed from the `kitchen`
  stand, the **left arm reaches only `y ∈ [0, +0.50]` and the right only
  `y ∈ [−0.50, 0]`** in the objects' z-band; from the `table` stand, `x ≲ 0.20`.
* **Out-of-scope tests pin the objects' arm sidedness**, so the kitchen trio
  must straddle the corridor: `mug_1` left, `bowl_1` right
  (`test_grasp_both_grippers_occupied_refused`), `plate_1` Mock-right-nearer
  (`test_implicit_side_prefers_the_left_arm…` in `test_mock_skills.py`).
  A straddling trio at one resting top (R3) gives **one** derived surface that
  spans `y = 0` → re-blocks the corridor.
* **Per-side splitting still does not fit.** With R1's constants a surface
  clears the band only if its object sits at `|y| ≥ 0.30 + 0.20 = 0.50` — exactly
  the IK limit (0.55 fails) — and any second object on that surface adds
  `x`-spread that pushes the edge back inside the band.
* **R4's heights also break the IK**: `book_1` at R4's `z = 0.885` (top 0.87) is
  unreachable from the `table` stand; the shipped `z = 0.90` is reachable.
* **R5's counter move breaks a coupled out-of-scope test**
  (`test_clear_the_table.py::test_a_keep_out_region_refuses_the_place_into_it…`
  derives its keep-out box from the counter's own pose).
* **`mujoco_ros2_control` is not installed in this worktree**, so
  `test_pr3_navigate`, `test_nav_localization`, `test_moveit_execution` and the
  `test_mujoco_launch` sim smoke cannot run here (same on `main`).
  **Acceptance 2 is not verifiable on this host.**

Branch state: the converter/launch machinery + tests are committed and green
(`robot_description` 94/0, `robot_world` 71/0, `robot_skills` 109/0,
`robot_safety` 179/0); the seed is at the R3/R5 intent, which leaves 6
`robot_backends` + 10 `robot_mcp` tests red (all green on `main`). Awaiting a
ruling on `implementation.md`'s options 1–4 (recommendation: option 4 — one
lone-object surface per kitchen object at the corridor/IK edge, table top 0.885).

---

## Manager ruling #2 (2026-10-12) — resolves the R1/R3/R4/R5 fork; R1/R3/R4/R5 are SUPERSEDED

The implementer's escalation is **correct and accepted** (VERIFIED by the manager): the Mock's 0.85 m sphere is not the sim's oracle; `ik.solve_ik` is arm-sided (left arm `y ∈ [0, +0.50]`, right arm `y ∈ [−0.50, 0]` from the kitchen stand, z-band 1.05..1.15), and out-of-scope tests pin `mug_1`→left, `bowl_1`→right, `plate_1.y < 0` (Mock right-nearer). The machinery (converter + launch + tests) is committed and correct — keep it. Only the **seed geometry** and the **surface derivation rule** change. `mujoco_ros2_control` is genuinely absent on this host (not a regression); acceptance 2's heavyweight probe (`test_pr3_navigate`) cannot run here — the geometric corridor guard + the settle test are the on-host proxies, and that is recorded, not hidden.

### R1′ — per-object support surfaces (REPLACES R1.2)
Change `derived_support_surfaces` from "group by resting top-z" to **one surface per graspable object** (no grouping): each graspable gets its own pad with `top_z = pose.z − vertical_half_extent`, `center = (pose.x, pose.y)`, `half_extent = max(0.12, footprint_half + 0.03)` where `footprint_half` = BOX `max(dims[0], dims[1])` / CYLINDER `dims[0]`. This lets straddling objects (mug +y, plate/bowl −y) rest on **separate** pads that do not span y=0. Keep the `support_surfaces=None` backward-compat path unchanged. Update the derivation unit tests accordingly.

### R3′/R4′ — keep objects at their *reachable* heights (REPLACES R3/R4 heights)
The sim IK cannot reach R4's lowered `book z=0.885` (VERIFIED: `(0.10, 2.00, 0.885)` solves for no arm; `0.90` does). So:
- Kitchen (all three rest on pads of top **1.10**): `mug z=1.15`, `plate z=1.105`, `bowl z=1.125` (exact rest, same counter height).
- Table: `book z=0.90` (**unchanged from main**), `cup z=0.915` (pad tops 0.885 / 0.87 respectively — per-object, so the two different tops are fine).
- x/y: keep mug near `+y` (left arm) and plate/bowl on `−y` (right arm), **separated so no object overlaps another**, each clear of the mast (`|y|>0.03`) and the arm wrist/jaw bands (`y ∉ [0.165, 0.294]` on the +side, `y ∉ [−0.294, −0.165]` on the −side), within the IK band (`|y| ≤ 0.50`). Reference: `mug (2.10, +0.10, 1.15)`, `plate (2.00, −0.45, 1.105)`, `bowl (2.30, −0.30, 1.125)`, `book (0.10, 2.00, 0.90)`, `cup (0.20, 2.00, 0.915)` — but **the implementer must verify each against the IK oracle and the mutual-overlap/mast/wrist clearances and adjust** (the reference is a starting point, not gospel).

### R5′ — counter stays near the kitchen and stands on the floor (REPLACES R5)
`robot_mcp/test/test_clear_the_table.py` builds its keep-out box from the counter's own pose and then asserts the object carried to **kitchen** is inside it — so the counter's centre must stay within ±0.5 of the kitchen location `(2, 0)` in x **and** y, with `z ≤ ~0.85` so a carried object (z≈1.0) is inside `z ≥ counter.z`. To also clear the corridor physically: stand it on the floor (centre `z = 0.40`, top `0.85`, below the arm z≈0.94) and offset it to `y ∈ (0.15, 0.5]` (clear of the base r=0.15 and the mast, still ≤0.5). Reference: `counter (2.15, +0.45, 0.40)`. Verify the keep-out test still passes (its box follows the counter's pose; the assert is the carried object at kitchen ∈ box).

### R10 — the out-of-scope tests are the ORACLE
The seed must make `robot_backends` **and** `robot_mcp` fully green (they are green on `main`). Iterate x/y/z empirically (IK probe via `MuJoCoBackend._position_ik`, settle probe, and the mast/arm contact probe) until every graspable is: IK-reachable by its pinned arm, resting at its seed pose, off the corridor, and non-overlapping. Update `test_default_seed.py::EXPECTED_OBJECTS` to match the final poses. Commit the ratchet (`scripts/test_baseline.json`) only after a green full run. **Do not** edit `robot_backends/**`, `robot_mcp/**`, `robot_moveit/**`. If after honest iteration a placement cannot make all out-of-scope tests green, STOP and report the specific infeasible constraint (name the test + the geometry) — do not silently weaken a test.

---

## Implementer report #2 (2026-10-12, resume after ruling #2) — applied; 3 out-of-scope tests are INFEASIBLE

**Done.** R1′ (one pad per graspable in `derived_support_surfaces`), R3′/R4′/R5′
seed poses, `EXPECTED_OBJECTS` re-pinned, the corridor guard re-modelled to the
robot's real envelopes, and the docstrings updated. Detailed evidence in
`implementation.md` (final poses, IK table, settle, corridor, per-package
results).

**Final seed** (pad `top_z/cx/cy/half` derives per object):

| object | pose | notes |
|---|---|---|
| `mug_1` | (2.15, **+0.45**, 1.150) | left-reachable; pad edge `y=0.33` clears the arm band 0.294 |
| `plate_1` | (2.00, **−0.48**, 1.105) | right-reachable; pad edge `−0.33` |
| `bowl_1` | (2.25, **−0.44**, 1.125) | right-reachable (not at −0.46: no solve); pad edge `−0.32` |
| `counter_1` | (2.15, **+0.47**, 0.400) | floor-standing, top 0.85 < arm z 0.94; near face `y=0.17` clears the base 0.15 |
| `book_1` | (0.10, 2.00, 0.900) | left-reachable (unchanged from main) |
| `cup_1` | (0.20, **2.20**, 0.915) | left-reachable; clear of the book box by 0.13 m |
| `sofa_1` | unchanged | |

Verified: every final pose solves IK for its pinned arm (`mug` left, `plate`/
`bowl` right, `book`/`cup` left); the shipped seed settles at its poses
(20 000 steps, |Δ| ≈ 2e−5 m, contact with its own pad); no object/pad intersects
the base, mast or arm envelope; no two objects overlap.

**Suite:** `robot_backends` **116 / 0 failed** (the six red tests are green —
with no edit to the package). `robot_description` 95/0, `robot_world` 71/0,
`robot_skills` 109/0, `robot_safety` 179/0, `robot_moveit_config` 12/0.
`robot_mcp` 105 with **3 failed** — all three in
`test_clear_the_table.py`'s `mujoco` parameter, and infeasible:

**The infeasible constraint (R10's STOP case).** Those three tests place at
`(counter.x, counter.y [+0.15], counter.z + 0.10)` with the **left** gripper.
Measured on this host, from the `kitchen` stand at column 0.30:

1. the arm's workspace floor is **z = 0.885** — FK over 6 000 random joint
   vectors inside `MuJoCoBackend._arm_ranges` (jaw midpoint via `_arm_fk`)
   never goes below 0.8852, and `_position_ik` solves nothing at z ≤ 0.85 — so
   the drop needs `counter.z ≥ 0.785`;
2. `test_a_keep_out_region_refuses_…` (its keep-out box is built from the
   counter's own pose and must contain the Mock's carried object at
   `(2.2, 0.0, 0.885)`) needs `|counter.y| ≤ 0.5` and `counter.z ≤ 0.885`;
3. the counter is a 0.6×0.6×0.9 m box, so `counter.z ∈ [0.785, 0.885]` gives a
   z-span `[0.335, 1.335]` that overlaps the arm envelope (`z 0.94..1.106`);
   clearing it needs `|counter.y| ≥ 0.294 + 0.30 = 0.594`. And the *second*
   drop needs `counter.y + 0.15` inside the left arm's y-limit at that height
   (FK max 0.454), i.e. `counter.y ≤ ~0.30`.

`0.594 ≤ |counter.y| ≤ 0.5` is empty. The corridor-clean alternative
(`counter.z ≤ 0.49`) puts the drop below the arm's floor; above the arms
(`counter.z ≥ 1.556`) breaks the keep-out box and the arm's ceiling (1.42).

**Confirmed both ways, by running it:** with `counter = (2.15, 0.47, 0.40)`
(shipped) the corridor guard passes and those three `mujoco` tests fail; with
`counter = (2.15, 0.00, 0.85)` (main's pose) all 11 `test_clear_the_table.py`
tests pass and the corridor guard fails with exactly
`counter_1 (y −0.30..0.30, z 0.40..1.30)`.

**Options for the manager** (implementation.md has the long form):
(1) authorize an out-of-scope edit so the drop is derived from the counter's
*top* — necessary but not sufficient, the drop's `y` still comes from the
counter's `y`; (2) give the chore a `counter`-labelled *object* at arm height
off the corridor; (3) accept the counter as an obstacle Nav2 plans around
(loses acceptance 2 as written); (4) **recommended as the shippable state** —
keep the corridor-clean seed and file those three tests as a follow-up (their
helper treats furniture's *centre* as a support surface).

**Also pre-existing on this host (not seed-related):** `robot_nav` 1 failure
and `robot_bringup`'s `test_nav_localization`, and `robot_moveit`'s
`test_moveit_execution` **hangs** (it launches the sim + move_group stack) —
all because `ros2 pkg prefix mujoco_ros2_control` → "Package not found";
`robot_brain` 2 failures (installed OpenClaw 2026.10.2 rejects the config
fragment). `pixi run test` therefore never reaches its final summary here;
the per-package XML results above are from that run.
