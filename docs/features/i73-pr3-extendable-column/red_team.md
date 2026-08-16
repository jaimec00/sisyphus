# Red-team — #73 / PR3 — Extendable column. Round 1.

> Persisted by the worktree manager. The round-1 pass produced this report while
> the permission layer was denying all writes, so the red-team could not create
> this file itself; it is reproduced here verbatim from the agent's return so the
> run stays resumable. Round 2 appends below.

## Evidence base

- `pixi run build` + `pixi run test` → **green**: 796 tests, 0 errors, 0 failures, 0 skipped; audit passed; `robot_description` 24 collected / **21 non-lint, `vs-base +0`**.
- Perturbation harness `/tmp/rtscratch/`: dereferenced copy of `install/robot_description` prepended to `AMENT_PREFIX_PATH`, running the **shipped** `test_description.py`. 22 single-edit perturbations, each restored. Worktree never touched.
- `/tmp/rtbk/`: `robot_backends` shadowed on `PYTHONPATH` to drift `RobotModel` itself.

## Ruling verdicts

Upheld: **R1, R2, R3, R6, R7, R8, R9, R12**. **BLOCK against R4** (its own height formula is wrong — B1). **BLOCK against R10** (its premise is false — B2). **R5 upheld with its residue confirmed and quantified** (N5). **R11 partially failed** — three of the four durable docs carry claims the shipped files do not support (B1, B3, B4).

---

# BLOCK

## B1 — D31 and `column.xacro` state the carriage's height above `base_link` with the lift joint's own origin omitted; the number is wrong by 585 mm — **VERIFIED**

`docs/design/decisions.md:130`, `src/robot_description/urdf/column.xacro:81`, and `status.md` R4:

> `column_top`'s height above `base_link` at joint value *q* is the rail joint's z plus *q* (plus nothing else, since the carriage's link frame is the mount datum).

Expanded from the installed tree:

```
column_rail_joint  base_link -> column_rail_link   xyz [0, 0, 0.78]
column_lift        column_rail_link -> column_top  xyz [0, 0, -0.585]   q in [0, 1.2]
```

Datum height above `base_link` = `0.78 + (-0.585) + q` = **`0.195 + q`**, i.e. **0.195 m → 1.395 m** (0.245 → 1.445 above `base_footprint`). The documented formula gives `0.78 + q` = 0.78 → 1.98 m. Off by exactly `column_lift_origin_z = -0.585`.

This is the *one sentence PR6 is told to reconcile against `RobotModel`*, sitting in the append-only log. A PR6 author who trusts it mounts the shoulders 585 mm too high and carries that error into every reach computation.

No test catches it: the tests compose correctly (`test_column_rail_spans_the_carriage_travel` uses `_joint_z(rail_joint) + _joint_z(lift_joint) + limit.upper`). Only the prose is wrong, which is why it survived.

This is verbatim the **#55 failure mode**: `implementation.md` §2 states the *correct* number ("z = 0.195 + q, i.e. 0.195 m to 1.395 m") and §4.1 records that the implementer changed the arithmetic R4 assumed (centred rail box → lift origin -0.585). The correction landed in the ephemeral doc; the decision log kept the sentence the correction invalidated.

**Fix:** in D31, `column.xacro:81` and `status.md` R4, state what the xacro computes: datum height = `column_rail_joint.z + column_lift.origin.z + q` = `chassis_top + column_carriage_height + (q - min_column_height)` = `0.195 + q` today. Consider asserting that identity (`datum at lower limit == chassis_top + carriage_height`) — it is what the whole column's arithmetic rests on and nothing pins it directly.

## B2 — The "ESTIMATED, nothing in this repo constrains it" lift velocity is exactly the safety layer's column speed cap, which the cap can then never bind — **VERIFIED**

`src/robot_description/urdf/column.xacro:118` declares `column_lift_velocity = 0.15` m/s, ESTIMATED, "placeholders". R10's premise: "No STS3215 torque/speed figure … is recorded anywhere in this repo." `context.md` Q4 records the search that established it — a grep of **`docs/design/` only**.

`src/robot_safety/robot_safety/limits.yaml:36`:

```yaml
velocity:
  base: 0.6
  # A lift column moves a heavy mass through the height where hands are: slow
  # enough that a pinch is a nudge, not an injury.
  column: 0.15
```

quoted to the LLM at `src/robot_brain/robot_brain/openclaw/AGENTS.md:137` and gated live by `test_prompt_drift.py`. The repo does own a column speed number; the URDF now carries the same number; nothing says so.

Two problems, the second substantive:

1. **The record is wrong.** Not an estimate with no owner — a transcription (coincidental or not) of `robot_safety`'s cap, unattributed and unpinned. Retune `limits.yaml` and they diverge silently in either direction.
2. **A policy cap equal to the stated actuator maximum can never bind.** URDF `<limit velocity>` is *capability* (what MoveIt/`ros2_control` plan and clamp against); `limits.yaml`'s `velocity.column` is *policy*, and its own comment plainly intends it to sit **below** capability. Setting capability = policy makes the safety layer's column clamp vacuous the moment anything believes the URDF — a quiet weakening of invariant 3, introduced by a number nobody thought was load-bearing.

Failure scenario: PR6/PR7 wire a controller to the URDF; the column cap is later lowered to 0.10 m/s after a pinch; the URDF still says 0.15; MoveIt keeps planning 0.15 m/s column moves and the safety layer clamps/rejects them at runtime, with nothing red anywhere. Structurally the D29 wheel-radius bug the suite already guards.

**Fix:** decide which quantity the URDF number is and say so. Cheapest honest form: keep ESTIMATED, cite `robot_safety/.../limits.yaml`'s `velocity.column`, and set the estimate strictly *above* it (an STS3215 lead-screw lift that can only just achieve the policy cap is also a poor estimate). If pinning it, the `ROBOT_MODEL_*` pattern is right there: `column_lift.limit.velocity >= SAFETY_COLUMN_SPEED_CAP_MPS`. Correct R10's premise and `context.md` Q4's "nowhere in this repo" — the grep was scoped to `docs/design/`; the number lives in `src/`.

## B3 — D31 (and the xacro header, README, spec.md) assert MoveIt's and MuJoCo's filtering as fact; the MuJoCo half is probably false for this model, and neither is executable here — premise **VERIFIED**, filtering claim **UNVERIFIED**

The premise holds, and I checked it rather than assuming: over the whole travel the rail's collision box (x,y ∈ [-0.03, 0.03], z ∈ [0.115, 1.445]) passes **entirely through** the carriage's (x ∈ [-0.07, 0.07], y ∈ [-0.05, 0.05], z ∈ [0.115+q, 0.195+q]) — a full 0.06 × 0.06 × 0.08 m overlap prism at every q ∈ [0, 1.2]. **C1(a) does not falsify R2**; the parenting is the honest kinematic description regardless.

C1(b) does bite. `decisions.md:129`:

> MoveIt's default ACM disables adjacent pairs, MuJoCo excludes parent/child bodies, and neither covers siblings.

Neither clause is executable in this repo (no SRDF, no MoveIt config, no MJCF until PR7; `mujoco` is not importable in the pixi env — I checked). That alone makes it reasoning presented as measurement in the append-only log. Worse, the MuJoCo half is likely wrong *for this model*: `column_rail_link` is attached by a **fixed** joint, so on the usual URDF→MJCF paths it is fused into `base_link`'s body (MuJoCo's compiler has `fusestatic` for exactly this). If the rail is not a body, the carriage's MuJoCo parent is `base_link`'s body **whether the lift is parented to the rail or to `base_link`** — and the mechanism D31 credits with removing the contact changes nothing in MJCF. The MoveIt half is softer too: there is no "default ACM" without an SRDF, and the Setup Assistant that generates one disables *adjacent* **and** *always-in-collision* pairs — the second of which would also cover siblings.

BLOCK on the decision log, not the code. Same claim repeated at `column.xacro:44-53`, `README.md` ("filtered as a parent-child pair rather than as unfiltered siblings"), `spec.md:127-129`.

**Fix:** demote to reasoning with an expiry — "as parent/child the pair is adjacent, the arrangement MoveIt's generated ACM and MuJoCo's parent/child exclusion are *expected* to filter; **unverified until PR7 builds the MJCF**, and note a fixed rail joint may be fused into `base_link` at MJCF time, in which case the filtering comes from body fusion rather than from the parenting." Keep the parenting — it is right on kinematic-honesty grounds alone.

## B4 — D31 claims each new test was confirmed to fail "and only that test"; five of the perturbations it refers to fail two to five tests — **VERIFIED**

`decisions.md:131`: "Each was confirmed by perturbing the model and watching that test — and only that test — fail." `implementation.md` §5 tabulates one test per perturbation.

Measured (scratch copy, shipped 21-test suite):

| perturbation | tests that actually fail |
|---|---|
| mast rooted at `base_link` z = 0 | `..._rail_stands_on_the_chassis`, `..._rail_spans_the_carriage_travel` |
| mast shortened to 0.6 m | `..._rail_spans_the_carriage_travel` (only — as claimed) |
| lift origin `rpy="${pi/2} 0 0"`, axis left `0 0 1` | `..._lift_axis_is_vertical_in_base_link`, `..._rail_spans_the_carriage_travel` |
| lift re-parented onto `base_link` | `..._lift_is_the_models_only_prismatic_joint` (only) |
| lift retyped `fixed` | same (only) |
| `effort="0"` / `velocity="0"` | `..._declares_positive_effort_and_velocity_limits` (only) |
| carriage box centred on its frame | `..._top_is_the_arm_mount_datum` (only) |
| `<limit>` deleted | **5**: `check_urdf`, both limit tests, span test, `robot_state_publisher` |

The extra failures are *correct* (a rotated lift origin really does break the span arithmetic), so this is not a test defect — it is a false verification claim in the durable log, in the clause whose job is to tell a later reader how precise the signal is. Landing an overclaim of exactly the shape D30's own rationale warns about ("a finding labelled VERIFIED is verified for its own claim, not for an inference drawn from it") in the very next entry is worth one word.

**Fix:** drop "and only that test" (or: "at least that test; in the six single-cause cases, only that test"). Same edit to `implementation.md` §5.

---

# NOTE

**N1 — `lower` can be omitted entirely and all 21 tests stay green — VERIFIED.** Removing `lower="${min_column_height}"` from `<limit>` leaves the suite fully green: URDF defaults `lower` to 0, which equals `ROBOT_MODEL_MIN_COLUMN_HEIGHT_M`. Benign today (the model still *means* 0), but the acceptance criterion "limit lower/upper equal the column bounds" is only *declaratively* enforced for `upper`. Cheap hardening: read `lower` off the raw expansion, or assert it once `min_column_height` ever becomes non-zero.

**N2 — a degenerate mast passes everything — VERIFIED.** `column_rail_width = 0.0` (a zero-thickness rail box) leaves all 21 green. The chassis has an explicit guard (`assert chassis_radius > 0 and chassis_height > 0`) and the carriage has `assert size[2] > 0`; the mast has neither. One line in `test_column_rail_stands_on_the_chassis`.

**N3 — the mast's `<visual>` is never compared to its `<collision>` — VERIFIED by reading (no code path reads `column_rail_link.visuals`).** The chassis and the carriage both carry a "drawn as X, collides as Y" assertion under the stated principle "what a reviewer sees must be what the planner hits". The new solid does not. Both boxes come from the same properties so divergence needs a deliberate edit — but that is equally true of the carriage, which *is* gated.

**N4 — zero-margin contact between the column and the chassis, and "clears" is the wrong word — VERIFIED.** `rail_foot == chassis_top == 0.115` exactly, and at `q = 0` the carriage's underside is *also* exactly 0.115 — coplanar with the chassis puck's top face. Not a penetration (and the coupling through `column_base_z` makes it structurally permanent, so it cannot decay into one). But `column_top` and `base_chassis_link` are in different subtrees — precisely the non-adjacent arrangement D31 invokes to justify the parenting — and the base deliberately gave its wheels 5 mm of clearance for the same reason ("0.085 leaves 5 mm of clearance … on top of the 0.08 minimum"). `spec.md`/README/test-name say the mast "clears" the chassis; it touches it. Consider a `column_foot_clearance` estimate (mirroring the base's precedent) and an explicit carriage-vs-chassis clause.

**N5 — R5's residue, quantified — VERIFIED.** With `RobotModel.max_column_height` drifted 1.20 → 1.50 (shadowed on `PYTHONPATH`), **every package's suite stays green**: `robot_backends` 74, `robot_safety` 176, `robot_skills` 106, `robot_brain` 57, `robot_mcp` 82, `robot_description` 21, `robot_world` 61 — zero failures. The transcribed constant pins the URDF to the copy, never the copy to `RobotModel`. This is exactly what R5 predicted and what the constants' comment says out loud ("a hand-typed copy … can drift from its source without anything noticing"), so the code is honest and no BLOCK is warranted — but the asymmetry is worth stating in the PR description so nobody reads "the limits are asserted against `RobotModel`" as a live pin.

**N6 — the merge-order ASCII graph is now misaligned and shows the wrong dependencies — VERIFIED by column count.** `urdf-mjcf-pr-breakdown.md:175`: inserting the second `(done)` shifted the branch anchors right by 7 columns. The `│` that connected PR3 to `└─► PR3.5` on line 176 now sits at column 22 (under **PR4**) while the `└` on line 176 is still at column 15; `└─► PR6`, which used to sit under PR4, now sits under **PR5**. The diagram reads as "PR3.5 and PR6 hang off PR4/PR5". Re-pad line 175.

**N7 — `spec.md:33` still claims a link the URDF does not have.** "URDF reserves a `head_camera_link` + REP-103 optical frame on `column_top`" — there is no `head_camera_link` (`EXPECTED_LINKS` is 8 and contains none). Pre-existing text, but PR3 is the D30 sweep of this exact section and `column_top` now exists, which makes the sentence read as a statement about the current model. One word ("will reserve" / "PR3.5 adds").

**N8 — design consequence worth surfacing (C2).** The robot as now described is **1.495 m tall** (mast top 1.445 above `base_link`, `base_link` 0.05 above the floor) on a **0.25 m wheel circle** with a 0.30 m chassis puck. Full-extension mass distribution (chassis 6 kg @ 0.085, mast 2.5 kg @ 0.78, carriage 0.8 kg @ 1.355) puts the CoM at z ≈ 0.38 m → static tip angle ≈ **18°**, before PR4 adds two arms and a payload at 1.4 m. Both drivers are inherited (`base_radius` SOURCED from LeKiwi; 1.20 m travel from `RobotModel`), so this is not a PR3 defect — but "1.2 m of travel on a 0.125 m wheel radius" is a real stability question that PR4/PR7 will meet, and it belongs in the follow-up list rather than being discovered from a MuJoCo tip-over.

---

# C7 — acceptance criteria → the test that fails if broken

| criterion (issue #73) | gating test | fires? |
|---|---|---|
| `xacro` expands without error | `test_xacro_expands_without_error` | yes (undefined property aborts expansion) |
| `check_urdf` / `urdfdom_py` parse it | `test_check_urdf_parses_the_expansion`, `test_link_set_is_exactly_the_expected_links` | **VERIFIED** (`<limit>` deleted → red) |
| `column_lift` is **prismatic** | `test_column_lift_is_the_models_only_prismatic_joint` | **VERIFIED** (retype `fixed` → red) |
| limits **equal** the column bounds 0.00/1.20 | `test_column_lift_limits_are_the_robot_model_column_bounds` | **VERIFIED** (upper→1.00 red; both shifted +0.5 red). Caveat N1 (`lower` omitted → green) |
| asserted against the model, not the raw file | transcribed `ROBOT_MODEL_*` constants | yes, but see N5 (one-directional) |
| `EXPECTED_LINKS` grows deliberately (+ `column_top`) | `test_link_set_is_exactly_the_expected_links` | **VERIFIED** (extra link → red) |
| loadable by `robot_state_publisher` | `test_model_loads_in_robot_state_publisher` | **VERIFIED** (`<limit>` deleted → RSP exits, red) |
| bounds as xacro `<property>`, not magic numbers | `test_column_rail_spans_the_carriage_travel` (rail length derived from travel) | **VERIFIED** (mast 0.6 m → red) |
| include list intact | `test_top_level_includes_every_subassembly` | yes (set equality, unchanged) |
| install rules stay glob-based | `setup.py` untouched; `test_share_layout_is_installed` | yes |
| full suite green; floor **ratchets up** | `scripts/test_baseline.json` 14 → 21 | **VERIFIED**: my green run reproduced 21 with `vs-base +0`; `baseline_blockers()` refuses to re-cut from a run with any error/failure, so the value is green-run-consistent |

**No acceptance criterion is unguarded.** Additional perturbations confirming the new relational guards: rail joint `rpy` rotated → 3 red (incl. `_axis_aligned_joint` firing as R4's judgment-call #4 claimed); carriage collision `rpy` rotated → 2 red; **rotated intermediate link inserted between `base_link` and the rail → `test_column_lift_axis_is_vertical_in_base_link` red**, so `_axis_in_base_link`'s parent-chain walk genuinely composes (C4's untested case); carriage `<inertial>` zeroed → `test_moving_links_have_inertia` red; carriage `<visual>` removed → 2 red; carriage geometry swapped to a cylinder → 2 red with legible messages. **C5 confirmed**: `MASSLESS_FRAME_LINKS` is unchanged in the diff and neither new link escapes either gate. No existing PR1/PR2 assertion was removed or weakened — the test diff deletes exactly two comment lines.

---

# Not covered (say so rather than let silence read as a pass)

- **R3's include-order reversal** was not re-executed by me (writes were denied mid-pass). The manager verified it in `status.md` R3, and the mechanism is deterministic (an undefined xacro property is a hard expansion error), so I rate the risk nil — but it is the manager's evidence, not mine. Note separately that `test_top_level_includes_every_subassembly` compares a **set**, so nothing gates the include *order* itself; only expansion does.
- **MoveIt/MuJoCo filtering** (B3) — not executable in this repo.
- **N3** was established by reading every use of `column_rail_link` in the test file, not by a perturbation.
- **Second adversarial pass.** This is round 1. Per the N+1 rule, after these fixes land the whole implementation (not just the fix diff) needs another full pass, and readiness requires a clean pass *following* a clean pass.

---

# Verdict

**Not mergeable as-is.** The shipped **model** is sound — the geometry closes exactly (mast foot 0.115 = chassis top; 0.05 m overtravel; datum 0.195 → 1.395), the topology is right, the six new tests are real and each one fires — but three of the four durable documents this PR ratifies carry claims the shipped files do not support, including a **585 mm arithmetic error in D31 on the single sentence PR6 is told to reconcile** (B1), and one estimated number silently equals a safety-layer cap in a way that makes that cap non-binding (B2). B1–B4 are all cheap: three prose corrections and one decision about what `column_lift_velocity` is. Fix those, re-green, and run at least one more full adversarial pass before calling it ready.

Relevant paths: `docs/design/decisions.md` (D31, lines 129–131), `src/robot_description/urdf/column.xacro` (lines 44–53, 81, 118), `src/robot_safety/robot_safety/limits.yaml` (line 36), `docs/design/spec.md` (lines 33, 120–136), `docs/design/urdf-mjcf-pr-breakdown.md` (line 175), `src/robot_description/test/test_description.py`, `docs/features/i73-pr3-extendable-column/status.md` (R4, R10).

> **Manager's note on the verdict's own arithmetic:** the verdict says "the six new
> tests are real". There are **seven** — the count is wrong in the same way D31's
> clause 6 is wrong, and for the same reason (both were written from the clause's
> prose rather than from `git diff`). `scripts/test_baseline.json` settles it:
> `robot_description` 14 → 21, i.e. **+7**. Folded into the round-2 fix list as R13.
