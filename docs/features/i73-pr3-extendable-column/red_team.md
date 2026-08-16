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

---
---

# Red-team — #73 / PR3 — Extendable column. Round 2.

Reviewing `868a6a5`, `829319b`, `b230b25` (the fix diff `a7d8190..HEAD`) first,
then the whole feature again over `origin/main..HEAD`. Read-only on the
worktree throughout; every perturbation ran against a scratch copy of the
*installed* share tree at `/tmp/rtscratch2/`, restored after each run.
`git status --short` empty at the end.

## Evidence base

- `pixi run build` + `pixi run test` → **green**: 799 tests, 0 errors, 0
  failures, 0 skipped; audit passed; `robot_description` 27 collected /
  **24 non-lint, `vs-base +0`**.
- 30 further single-edit perturbations in two batches (`/tmp/rtscratch2/p1.py`,
  `p2.py`), each run against the shipped 24-test suite and restored.
- `/tmp/rtsafe/`: `robot_safety` + `robot_brain` shadowed on `PYTHONPATH` to
  raise the real `velocity.column` cap and see what objects.

---

# A. The fix diff, ruling by ruling — all VERIFIED

## R13 / B1 — the datum arithmetic. **Fixed, and now pinned.**

The expansion is unchanged (`rail z 0.78`, `lift z −0.585`, datum
`0.195 → 1.395`), and every document now states it that way: `column.xacro:88-99`
spells out both origins with the derivation, D31 clause 5 states
`column_rail_joint.z + column_lift.origin.z + q = chassis_top +
column_carriage_height + (q − min_column_height) = 0.195 + q` **and records the
defect and why it survived**, and `spec.md`, `README.md` and the roadmap's §PR3
carry the same corrected number. A sweep for every restatement
(`0.78 + q`, "rail joint's z plus", "rail joint's offset plus",
`column_rail_joint.origin.z + q`) finds **no surviving instance** outside
quotations-of-the-defect in `red_team.md` and `status.md` R13.

`test_column_datum_rests_on_the_mast_foot_at_the_lower_limit` is real and
sharp. Sliding `column_lift_origin_z`:

| slide | result |
|---|---|
| +0.005 | **1 red** — the datum test alone |
| +0.01 | **1 red** — datum only |
| +0.02 | **1 red** — datum only (the docstring's claim) |
| +0.05 | **1 red** — datum only; the span test is still green |
| +0.06 | **2 red** — datum **and** span |
| −0.005 | **2 red** — datum and span |

So both numbers in the docstring are exactly right: the span test tolerates
`+0.05` and not `+0.06` (the mast's over-travel), and the datum test catches
`+0.02`. It in fact catches far less — the assertion is an identity to
`PLACEMENT_TOL_M = 1e-9`, so **the smallest slide that still passes everything
is one nanometre**, i.e. none. The docstring understates itself, which is the
right direction.

## R14 / B2 — capability vs policy. **Fixed, and the inequality is gated.**

`column_lift_velocity` is now `0.25`; `SAFETY_COLUMN_SPEED_CAP_MPS = 0.15`.
Broken both ways:

| URDF velocity | result |
|---|---|
| 0.15 (equal to the cap) | **red** — `test_column_lift_can_outrun_the_safety_layers_column_cap` |
| 0.14 (below the cap) | **red** — same test |
| 0.16 (just above) | green |
| 0.0 | **red ×2** — that test *and* the positive-limits test |

The claimed asymmetry with the *height* bounds is factually correct:
`limits.yaml` carries `column: {min_height: 0.0, max_height: 1.2}`, identical to
the URDF's position limits, and the xacro states why that case is the opposite
(a position clamp should stop *at* the mechanical stop). That asymmetry is
stated where a reader meets it — in `column.xacro`'s own property comment, not
only in D31.

R10's premise and `context.md` Q4 both carry a correction block naming the
`docs/design/`-only scope error that produced them.

The inverse risk is real but out of this PR's scope — see **N9** below.

## R15 / B3 — the filtering claim. **Demoted everywhere, with an actionable expiry.**

A sweep for `ACM|fusestatic|parent/child|parent-child` across `docs/design/`
and `src/robot_description/` finds the claim in exactly four places, all
demoted: `column.xacro:44-53` ("*expected*… **unverified and stays so until PR7
builds the MJCF**", `fusestatic` named, "PR7 should check that rather than
inherit it"), D31 clause 3 — whose **headline itself** changed to "because a
carriage rides its mast, and the model should say so", with "that justification
is enough on its own" — the roadmap's §PR3 ("**PR7 owes a check here:** whether
MuJoCo's `fusestatic` folds the fixed-jointed rail into `base_link`"), and
`test_column_lift_is_the_models_only_prismatic_joint`'s docstring ("that half
is **unverified until PR7**… so it is not what this assertion rests on"). The
assertion *message* no longer mentions filtering at all, and neither does
`test_column_rail_stands_on_the_chassis`'s. `spec.md` and `README.md` both say
"expected but **unverified until PR7's MJCF**".

The `fusestatic` caveat is phrased as an obligation on a named PR in the
roadmap, which is where PR7's author will actually look. That is the honest
form.

## R16 / B4 — "and only that test". **Dropped, and the replacement is accurate.**

D31 clause 6 now says "watching **at least** that test fail", names the six
single-cause perturbations, and says the rest red two or three at once. I
re-measured every row of `implementation.md` §5's re-measured table against the
shipped 24-test suite. **All twelve rows I could reproduce match exactly**,
including the three that changed shape when the two new tests landed:

| perturbation | table says | I measured |
|---|---|---|
| mast rooted at `base_link` z = 0 | 3 | **3** (stands / spans / datum) |
| lift origin `rpy=pi/2`, axis `0 0 1` | 3 | **3** (axis / spans / datum) |
| `<limit>` deleted | 7 | **7** (`check_urdf`, RSP, and the five column tests that read a limit) |
| `velocity="0"` | 2 | **2** (positive-limits + outrun) |
| `effort="0"` | 1 | **1** |
| velocity dropped to the cap | 1 | **1** |
| `max_column_height` → 1.00 | 1 | **1** |
| `lower` deleted | 1 | **1** |
| axis `0 1 0` / `<axis>` deleted | 1 / 1 | **1 / 1** |
| mast shortened to 0.6 m | 1 | **1** |
| mast width zeroed | 1 | **1** |
| mast drawn slimmer | 1 | **1** |
| lift origin slid +0.02 | 1 | **1** |
| carriage box centred | 1 | **1** |
| rail mass zeroed | 1 | **1** |

"Fifteen of the nineteen are single-cause" is arithmetically right for the
table as written.

## R17 — the count. **Now correct, and correct by construction.**

`git diff origin/main..HEAD` on the test file: **10** added `def test_`, **0**
removed, **8** added `def _` helpers. `test_baseline.json` 14 → 24 (+10),
reproduced by my own green run as `robot_description … 24 … +0 ok`. D31's "the
gate grew by ten" and `implementation.md`'s "8 new helpers, 10 new tests" both
match the diff. D31's own header still says "Six clauses" and has six
non-rationale bullets plus a `*Rationale:*` bullet — the same shape as D27, D29
and D30, so that count is right too.

## R18 — the five NOTES ruled in for this PR. **All fixed, all verified red-on-break.**

| note | perturbation | result |
|---|---|---|
| **N1** `lower` may be omitted | delete `lower="…"` | **red** — `..._limits_are_the_robot_model_column_bounds`. Also checked the neighbours: deleting `upper` → **red** (same test); deleting `effort` or `velocity` → the URDF no longer parses at all (`check_urdf` red, RSP red, 16 fixture errors), so the hole existed only for `lower`/`upper` and is now closed for both. |
| **N2** degenerate mast | `column_rail_width = 0` | **red** — `..._rail_stands_on_the_chassis` (new guard). `column_rail_depth = 0` → **red** too, so the guard covers all three extents, not just the one I found. |
| **N3** mast visual ≠ collision | draw it half-width | **red** — `test_column_mast_is_drawn_as_it_collides`. Offsetting the visual by 0.1 m → **red** too (the check covers offset as well as size). |
| **N6** ASCII graph | verified **by index**, not by eye | `(done)` markers now on their own line at columns **0 / 7 / 14** = exactly PR1 / PR2 / PR3; the PR3.5 branch `│` is back at column **15** and the `└` beneath it at **15** (they connect); PR6's `└` is at column **23** = PR4's last column, its pre-PR3 position. Both anchors correct; the re-pad did not fix one and break the other. |
| **N7** `spec.md:33` | read | now "PR3.5 **will** add a `head_camera_link` … (which exists as of D31; the camera link does **not** yet)". True as written. |

## R3 — the one piece of round-1 evidence that was the manager's, not mine. **Now VERIFIED by me.**

Reversing the include order in the scratch top-level file:

```
rc = 2
error: name 'chassis_z_offset' is not defined
when evaluating expression 'column_base_z + column_rail_length / 2'
when processing file: …/urdf/column.xacro
```

and the suite goes **6 failed + 16 errors**, led by
`test_xacro_expands_without_error`. The quoted message in D31 clause 4, the
xacro header and the README matches the tool's output exactly, and the claim
"already caught by the gate's first test" is true. Note for completeness that
`test_top_level_includes_every_subassembly` compares a **set**, so nothing gates
the include *order* directly — expansion does, loudly, which is what the docs
claim.

---

# B. The N+1 sweep — clean

Grepped the whole repo for every restatement of the four claim families the fix
round touched:

- **datum arithmetic** — no surviving wrong instance; the corrected form appears
  in `column.xacro`, D31, `spec.md`, `README.md`, the roadmap, the test
  docstring and the assertion message of
  `..._limits_are_the_robot_model_column_bounds` (which round 1 missed and the
  implementer's own sweep found).
- **MoveIt/MuJoCo filtering** — four instances, all demoted; two more
  (a test docstring and an assertion message) that neither the ruling nor my
  round 1 listed were found and fixed by the implementer's sweep.
- **"and only that test"** — no surviving instance outside the ephemeral docs
  that discuss the correction.
- **counts** — every test/helper/clause count in the diff now matches `git diff`
  (checked: D31 "ten" and "Six clauses", `implementation.md` "8 helpers / 10
  tests", `test_baseline.json` 24, `EXPECTED_LINKS` 8).

Checkable claims *introduced by round 2* that I executed rather than read:
the `0.195 + q` range and the `−0.585` origin (both exact); `limits.yaml`'s
column min/max being 0.00/1.20 (exact); the `+0.05` green / `+0.06` red
over-travel measurement (exact); the `fusestatic`/SRDF caveat (correctly stated
as unexecutable); "the safety layer's own suite never reads the URDF" (true);
"this file shipped `velocity="0.15"` for one review round" (true).

---

# C. Full pass over `origin/main..HEAD` — no regressions

- **Invariants:** `MASSLESS_FRAME_LINKS` is still exactly
  `frozenset({'base_link', 'base_footprint'})`; `EXPECTED_LINKS` is still the 8
  links and the model expands to exactly those 8; `setup.py`, `package.xml` and
  `robot.urdf.xacro` are untouched by the whole feature; `src/robot_safety/` and
  `src/robot_backends/` are untouched (PR6 owns the latter).
- **No assertion weakened.** Across the whole feature diff the test file has
  **zero** removed `def`s; the round-2 diff only rewords docstrings/messages,
  adds three tests, one helper, one constant and one guard clause.
- **`robot_state_publisher` still loads the model** — passes in the scratch run
  and in `pixi run test`, and still fails loudly when the model is broken
  (`<limit>` deleted → RSP red).
- **Acceptance criteria** all still have a failing-if-broken test, and two are
  now *strictly* better covered than in round 1: the limits criterion no longer
  accepts a defaulted `lower`, and the "geometry a reviewer cannot eyeball"
  criterion now covers the mast's visual and its degeneracy.
- **Ratchet:** `test_baseline.json` 14 → 24, an increase, reproduced by a green
  run (`+0 ok`); `baseline_blockers()` refuses to re-cut a floor from any run
  with an error or failure.

---

# BLOCK

**None.**

# NOTE

**N9 — the velocity pin is one-directional in a sharper way than N5, and the
constant's comment slightly undersells it — VERIFIED.** With
`robot_safety/limits.yaml`'s `velocity.column` raised to **0.30** (loaded live —
I printed `{COLUMN: 0.3}` from the shadowed package), `robot_description`'s 24
tests stay **green** and `robot_safety`'s 176 stay green, while policy (0.30)
now exceeds capability (0.25) — the inversion `column.xacro` calls "worse" than
the equality this round fixed. The only thing that objects anywhere is
`robot_brain`'s `TestSafetyEnvelope::test_the_stated_envelope_is_exactly_the_shipped_limits`,
and it objects to the *prompt* not matching the cap, not to the cap not matching
the URDF; edit the prompt in lockstep and the inversion is invisible.

This is inherent to R14's transcription choice (which is the right call — a
description package must not grow a dependency on the safety layer), so it is a
NOTE, not a BLOCK, and it belongs with **N5** in the follow-up comment on the
issue. One clause of copy-editing would make the residue exact, though: the
constant's comment says drift "makes the assertion weaker, never wrong", which
is true of the assertion and could be read as true of the *property* it
protects. It is not — a cap **lowered** stays gated, a cap **raised above
capability** is not. Worth naming, since that is the direction that breaks the
thing the test exists for.

*Considered and deliberately not raised:* the three "drawn as it collides"
tests all read `visuals[0]`, so a second stray `<visual>` of the wrong size
would pass — a pre-existing pattern shared with the chassis and the carriage,
not introduced here, and not worth a ratchet. N4, N5 and N8 are deferred by
R18 and are not re-raised.

---

# Verdict

**Round 2 is clean: no BLOCKs.** All four round-1 BLOCKs and all five ruled-in
NOTES are fixed, and each fix was verified by executing a perturbation rather
than by reading the diff — the new datum test pins the identity to a nanometre,
the velocity inequality goes red at 0.15 and at 0.14 and green at 0.16, the
`lower`/`upper` declaration hole is closed in both directions, the degeneracy
and visual guards fire, and the ASCII graph's anchors are back at columns 15 and
23 by index. The N+1 sweep found no surviving instance of any of the four
corrected claims, and the implementation's own re-measured perturbation table
reproduces exactly against the shipped suite. `pixi run test` is green at 799
tests with the floor ratcheted 21 → 24 on a green run.

Per the N+1 rule this is the **first** clean pass, and it directly follows a
round that found four BLOCKs — so it proves the fixes landed. Whether a third
pass is warranted is the manager's call; my own read is that the residual risk
is low and concentrated in prose that has now been swept three times by two
different agents, and that one further pass would most likely be spent
confirming that.

**Mergeable, subject to the manager's judgment on the N+1 third pass**, with N4,
N5, N8 and N9 routed to Sisyphus as follow-ups on the issue.
