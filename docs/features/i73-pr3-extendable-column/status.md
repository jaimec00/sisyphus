# Status — i73 / PR3 — Extendable column (prismatic STS3215 lift, Nori crib)

- **Issue:** #73
- **Branch:** `feat/i73-pr3-extendable-column-prismatic-sts3215`
- **Base:** `origin/main` @ `a34fcb2`
- **Owned paths:** `src/robot_description/` (+ `docs/design/decisions.md`,
  `docs/design/spec.md`, `docs/design/urdf-mjcf-pr-breakdown.md` for the durable
  decision and the flattened current state; + this ephemeral `docs/features/`
  dir). **Not** `src/robot_backends/` — PR6 owns that.

## Phase

| phase | state |
|---|---|
| 0. sync | DONE — worktree at `origin/main` (`a34fcb2`), clean |
| 1. brief | DONE — issue #73 read (non-empty, acceptance criteria present) |
| 2. provisioning | DONE — **no new third-party dependency** |
| 3. context-explorer | DONE — `context.md`, 7 open questions |
| 4. manager rulings | DONE — R1–R12 below |
| 5. implementer | pending |
| 6. red-team | pending |
| 7. fix rounds | pending |
| 8. test-runner | pending |
| 9. PR | pending |

## Step 2 — provisioning ruling

PR3 adds **no new third-party dependency**. `xacro`, `urdfdom`, `urdfdom_py`,
`robot_state_publisher`, `ament_index_python` are already declared in
`src/robot_description/package.xml` and pinned in `pixi.toml` by PR1/PR2. The
column is authored from xacro primitives exactly as PR2's base was (D29:
primitives, no vendored meshes). No `pixi add`; no worktree mutation was needed
before the context-explorer ran.

## Manager rulings (binding, but not assumed correct)

These lock the design before implementation. A downstream agent that believes a
ruling is **wrong** must **escalate to the manager in-process** — neither
silently deviate nor comply into a bug. Each is stated so the red-team can try
to falsify it *empirically*.

### R1 — Link topology: **two new solid links, no new massless frame.**

```
base_link ──fixed  (column_rail_joint)──▶ column_rail_link   [static mast]
column_rail_link ──prismatic (column_lift)──▶ column_top     [moving carriage]
```

- `column_rail_link` — the **static** linear rail / mast. Solid: visual +
  collision + computed inertial.
- `column_top` — the **moving carriage**, and simultaneously the arm/head mount
  datum. Solid: visual + collision + computed inertial. **Its link frame origin
  IS the mount datum** (the top mounting surface); its `<visual>`,
  `<collision>` and `<inertial>` carry their own `<origin>` offsets describing
  the carriage body *below* that datum. This is ordinary URDF and is what makes
  the name `column_top` literally true.
- `EXPECTED_LINKS` grows by exactly these two, to 8 links.
- `MASSLESS_FRAME_LINKS` is **unchanged** (see R6).

*Why not a separate massless `column_top` frame fixed to a `column_carriage_link`?*
Because that third link is a **fixed intermediate link that corresponds to no
body** — precisely the modelling artifact D29 refused to import from LeKiwi
("a `drive_motor_mount → ST3215_Servo_Motor → omni_wheel_mount` chain of
intermediate links per wheel that is a modelling artifact rather than
kinematics… would put six fixed links into TF, into `EXPECTED_LINKS` and into
PR7's MJCF for zero information", `decisions.md:106`). Putting the carriage
link's *frame* at the mount datum buys the same semantics for one link instead
of two.

*Why is a static rail link required at all (the issue names only `column_top`)?*
A linear-rail lift with 1.20 m of travel has a mast that is physically present
at **every** joint value. Modelling only the carriage yields a robot with a
block floating up to 1.2 m in the air with nothing underneath it — collision-
invisible to Nav2/MoveIt and structurally absent from PR7's MJCF. D27/D29's
stated justification for gating geometry at all is that "geometry is the part a
reviewer cannot eyeball"; a missing mast is exactly that. This is an **addition**
to the issue's link list, not a contradiction of it — the issue itself says
`EXPECTED_LINKS` grows "to include the new column link(**s**) + `column_top`".

### R2 — Parent of the prismatic joint: **`column_rail_link`, not `base_link`.**

> **Ruling upheld, justification superseded by R15.** The red-team measured the
> mast/carriage overlap and upheld the parenting, but falsified the
> filtering argument below: it is unexecutable in this repo, and MuJoCo's
> `fusestatic` may make it moot. Read R15 for what the shipped docs now say.

The issue's "prismatic joint `column_lift` from `base_link`" is honored in
*intent* — the column assembly hangs off `base_link` via
`column_rail_joint`, and the same bullet says "the column mounts on the PR2
base". Within the column, the carriage rides the **rail**.

The reason is concrete, not stylistic, and is the same failure D29's red-team
round found for the chassis/wheels: **the carriage and the rail are in contact
by construction.** Made siblings under `base_link`, that contact is filtered
*nowhere* — MoveIt's default ACM disables *adjacent* pairs, and MuJoCo (PR7)
excludes parent/child body contacts by default; neither applies to siblings.
Parenting the carriage to the rail makes the pair adjacent and the problem
disappears structurally instead of being dodged by carefully non-overlapping
solids. Cf. `decisions.md:111` ("They are siblings under `base_link`, so this
contact is not filtered anywhere").

Kinematically the two choices are identical while `column_rail_joint` is fixed,
so nothing downstream (limits, TF, PR6) is affected.

### R3 — Mount height: **computed from the base's own properties, not a literal.**

`column_rail_joint`'s origin puts the rail's **bottom face** on the chassis
puck's **top surface**, computed in `column.xacro` from `base.xacro`'s
properties: `chassis_z_offset + chassis_height / 2` (= 0.115 m today). Rooting
the rail at `base_link`'s z = 0 (axle height) would bury its lower 0.115 m
inside the chassis solid — the D29 chassis/wheel bug again.

**Verified empirically** (manager, `/tmp/xacroprobe`) that a property defined in
`base.xacro` is visible in `column.xacro`: with `base.xacro` included first,
`${chassis_z_offset + chassis_height / 2}` expands to `0.115`; with the includes
**reversed**, xacro aborts with
`error: name 'chassis_z_offset' is not defined … when processing file: b.xacro`.
So the coupling is real, and its failure mode is loud and already caught by
`test_xacro_expands_without_error`. That is an acceptable price for making a
retuned chassis move the column automatically instead of silently leaving the
column behind.

### R4 — Joint limits: **exactly `lower="0.00"`, `upper="1.20"`, as properties.**

`column.xacro` declares xacro properties named **`min_column_height` and
`max_column_height`** — deliberately the same identifiers as
`RobotModel`'s fields (`mock_world.py`), so PR6's author greps one name and
finds both ends of the correspondence.

**The limits are the carriage's *travel along the rail*, measured from
`column_lift`'s own origin — not an absolute height above `base_link` or the
floor.**

> **Corrected by R13 (this ruling was wrong).** The datum's height is *both*
> joint origins plus the joint value, not the rail joint's alone:
>
> ```
> column_rail_joint.z + column_lift.origin.z + q
>   = chassis_top + column_carriage_height + (q - min_column_height)
>   = 0.195 + q          ->  0.195 m ... 1.395 m above base_link
> ```
>
> `column_lift`'s origin is -0.585 because the rail's box is centred in its
> link frame, so the omitted term is worth 585 mm. The gate now pins the
> identity underneath it (the carriage rests on the mast's foot at the lower
> limit).

Do **not** fold the mount offset into the limits: the roadmap
table (`urdf-mjcf-pr-breakdown.md:25-26`) maps `min/max_column_height` onto the
lower/upper **limit values** with no additive term, and the issue's acceptance
criterion asserts the literal 0.00 / 1.20.

This resolves Q3, which the docs genuinely do not answer. The residue —
whether `RobotModel.column_height` is a travel or an absolute height — is
**PR6's** to reconcile, and R11 requires PR3 to write that down rather than
leave it implied.

### R5 — Pinning to the outside contract: **transcribed constants, `DRIVER_*`-style.**

`test_description.py` gains module constants (name them for what they are, e.g.
`ROBOT_MODEL_MIN_COLUMN_HEIGHT_M = 0.0` / `ROBOT_MODEL_MAX_COLUMN_HEIGHT_M =
1.20`) with a comment citing `robot_backends/robot_backends/mock_world.py`'s
`RobotModel.min_column_height` / `.max_column_height` by name — exactly the
shape the `DRIVER_*` constants already use for the LeRobot driver
(`test_description.py:110-143`).

**Do not** add `robot_backends` as a `<test_depend>` of `robot_description`.
Two reasons: D30 declined the analogous cross-seam edge and its caution stands;
and PR6 inverts this dependency (`robot_backends` will read the URDF), so an
edge landed now would have to be torn out then, on the package that is meant to
be the foundation of this roadmap.

The transcription is a hand-typed copy and can therefore drift — the same
weakness D30 records for `SUPERSEDED_BODY_CLAIMS`. Say so in the constants'
comment, and name PR6 as the thing that closes it (by making `RobotModel` read
the URDF, after which the copy disappears rather than being maintained).

### R6 — `MASSLESS_FRAME_LINKS` stays exactly `{base_link, base_footprint}`.

Both new links are physical bodies and get real geometry and computed inertias.
The admission rule, to be written into the set's comment so it stops being a
question every PR: **a link joins that set only if it corresponds to no physical
body and exists to serve an outside convention** (`base_link` = the URDF root
frame; `base_footprint` = the ground projection consumers expect). "I did not
want to compute an inertia for it" is never a reason. Growing this set is how a
link escapes *both* `test_moving_links_have_inertia` and
`test_solid_links_have_visual_and_collision_geometry` with no other signal in
the suite — it is the suite's one rug, and PR3 does not sweep under it.

### R7 — Column/chassis clearance is asserted, relationally.

Answering Q7: **yes**, add it. Assert `column_rail_link`'s bottom face
(rail joint origin z, minus half the rail collision length read off the model)
is `>=` the chassis top face (chassis joint origin z, plus half the chassis
collision height read off the model). Numbers **read off the parsed model**, as
a *relationship*, never as the literal that satisfies it today — the pattern
`test_description.py:725-738` already establishes and D29 insists on. Reuse
`_collision_cylinder` if the rail is a cylinder; if it is a box, add the
box-shaped sibling helper rather than special-casing inside the test.

### R8 — The rail must actually span the travel.

Assert `column_rail_link`'s collision length `>=` (`column_lift.limit.upper` −
`column_lift.limit.lower`). A mast shorter than the travel is a carriage that
flies off the end of its own rail: physically incoherent, and green under every
other assertion in the suite. This is a relationship between two numbers the
model already owns, so retuning either keeps it enforced.

### R9 — The lift axis is verified **after** rpy composition.

Assert that `column_lift`'s `<axis>`, rotated through its joint origin's `rpy`
(and the rail joint's, if non-identity), is `(0, 0, 1)` in `base_link` to
`PLACEMENT_TOL_M`. Reuse `_rotation_from_rpy` / `_rotate`
(`test_description.py:235-262`) — do not re-derive them. A column that lifts
sideways satisfies "type is prismatic, limits are 0.00–1.20" perfectly. This is
D29's composed-axis check applied to the column.

Also assert `column_lift` is the model's **only** prismatic joint, by exact set
comparison, so a second lift cannot appear unnoticed.

### R10 — `effort` / `velocity`: mandatory, and honestly marked ESTIMATED.

> **Premise corrected by R14 (this ruling was wrong on the facts).** "No
> STS3215 torque/speed figure, and no lead-screw or belt ratio, is recorded
> anywhere in this repo" is false for speed: `src/robot_safety/robot_safety/
> limits.yaml:36` carries `velocity.column: 0.15`. The premise came from a
> grep scoped to `docs/design/` (`context.md` Q4) whose result was then
> restated as being about the whole repo — a **scope error in the evidence,
> promoted to a fact in a ruling**, which is what let the URDF ship a
> "nothing constrains it" estimate that silently equalled a safety cap.
> `limits.yaml` is *policy*, the URDF `<limit>` is *capability*; see R14 for
> the distinction and the fix.

The context-explorer verified empirically that both `check_urdf` and
`urdf_parser_py` **reject** a prismatic `<limit>` missing either attribute.
No STS3215 torque/speed figure, and no lead-screw or belt ratio, is recorded
anywhere in this repo. Therefore: declare them as xacro properties, mark them
**ESTIMATED** in `base.xacro`'s own convention, and state in the comment that
they are placeholders owed a real actuator model (PR6/PR7). **Do not invent a
datasheet citation** — D29's rule that "a fake citation is worse than an
estimate that says it is one" (`base.xacro:38-40`) is binding here.

The test asserts only that both are **present and strictly positive** — not
their values, which are estimates. A zero or absent velocity limit is a joint
that cannot move and that MoveIt will refuse to plan for, so that much is a
real defect worth gating.

### R11 — PR3 ratifies a decision: **D31**, and the flattened docs are updated.

Following D27 (PR1) and D29 (PR2), PR3 lands a `docs/design/decisions.md` entry
**D31** (D30 is the current highest — verified). It must record, at minimum:
the rail+carriage topology and why the third fixed link was refused (R1); the
prismatic parented to the rail rather than `base_link`, and the sibling-contact
reason (R2); the mount height computed from the base's properties and the
include-order coupling that creates (R3); **the travel-vs-absolute-height
semantics of the limits and the residue PR6 must reconcile** (R4); and the
transcribed `RobotModel` constants as a temporary pin PR6 removes (R5).

Also update, in the same PR:
- `docs/design/urdf-mjcf-pr-breakdown.md` §PR3 → **DONE**, exactly as §PR1/§PR2
  are marked, including any place where D31 contradicts the section's literal
  wording (as D29 did for §PR2's mesh bullet).
- `docs/design/spec.md`'s "Description & packaging" section — it currently says
  "Column, arms, grippers, camera and the MJCF are still to come"
  (`spec.md:123`), which becomes false the moment this merges. This is the
  **exact** failure D30 was filed for: a doc that went on asserting a body the
  robot no longer had. Fix it in this PR, not in a follow-up.
- `src/robot_description/README.md` and the module docstring of
  `test_description.py` if either enumerates what is built so far.

**Do not touch `src/robot_backends/`** — PR6 owns that, and PR3 changing
`RobotModel` would break the roadmap's "no PR changes `robot_backends` runtime
behavior until PR6".

### R12 — Everything else stays as PR1/PR2 left it.

- `setup.py` install globs stay flat and glob-based (D27, D29). PR3 lands no
  meshes, so the `os.walk` rewrite is still owed to the first mesh-bearing PR.
- The `<xacro:include>` list in `robot.urdf.xacro` is **unchanged** — it already
  includes `column.xacro`, and `test_top_level_includes_every_subassembly`
  already gates it. Adding an include here would be a bug, not progress.
- `scripts/test_baseline.json` is **not** hand-edited; `pixi run test` ratchets
  it up on an otherwise-green run (D28). Commit whatever the green run writes.
- Match `base.xacro`'s authoring voice: every dimension an `<xacro:property>`,
  each marked SOURCED (with a real citation) or ESTIMATED, inertias **computed**
  from those same properties so a retuned dimension cannot leave a stale tensor
  behind.
- The Nori Bot crib is **unverified in this repo** — the context-explorer
  confirmed no dimension for it exists anywhere beyond the 0.00–1.20 travel.
  Cite it as the *concept* crib (per D26) and mark every number PR3 invents as
  ESTIMATED. Do not attribute a number to Nori Bot.

## Round-2 rulings (R13–R18) — issued against red-team round 1

Round 1 returned **4 BLOCKs and 8 NOTEs** (`red_team.md`). The shipped *model*
was upheld: topology, geometry and all seven new tests fire under perturbation,
and no acceptance criterion is unguarded. Every BLOCK is in the **durable
prose**, plus one number. Two of my own round-1 rulings were falsified and are
corrected here.

### R13 — B1 + my step-9 finding: correct D31's arithmetic, and pin it with a test.

**My R4 was wrong.** It asserted `column_top`'s height above `base_link` is "the
rail joint's offset plus *q*". The implementer then (correctly, and recorded in
`implementation.md` §4.1) centred the rail box, which puts `column_lift`'s origin
at −0.585 in the rail frame. The true datum height is

```
column_rail_joint.z + column_lift.origin.z + q
  = chassis_top + column_carriage_height + (q − min_column_height)
  = 0.195 + q          →  0.195 m … 1.395 m above base_link
```

The wrong formula (`0.78 + q`) propagated from my ruling into `column.xacro:81`
and into **D31**, on the one sentence PR6 is told to reconcile against
`RobotModel` — a 585 mm error in the append-only log. Fix it in all three places
(D31, the xacro comment, R4 above).

**And pin it**, per the red-team's suggestion: assert the identity the whole
column rests on — at the lower limit the datum sits at `rail_foot +
column_carriage_height`, i.e. the carriage rests on the mast's foot. Nothing
asserts it directly today, which is exactly why the prose could drift from it.

### R14 — B2: `column_lift_velocity` is **capability** and must sit strictly above the safety cap.

R10's premise was false. The grep behind it (`context.md` Q4) covered
`docs/design/` only; `src/robot_safety/robot_safety/limits.yaml:36` has
`velocity.column: 0.15` — **verified by the manager** — and the URDF shipped the
same 0.15.

The ruling: the two numbers are **different quantities** and must not be equal.
URDF `<limit velocity>` is what the mechanism *can* do; `limits.yaml`'s
`velocity.column` is what policy *allows*. Policy equal to capability makes the
safety clamp vacuous the moment anything trusts the URDF — a quiet weakening of
invariant 3, and the reverse (policy above capability) would be worse.

Therefore:
1. Raise `column_lift_velocity` to a value **strictly greater** than the cap
   (0.25 m/s unless the implementer has a better-argued estimate). Keep it
   ESTIMATED — this is still a guess, just no longer a colliding one.
2. Rewrite its comment to say what it is, cite `limits.yaml`'s `velocity.column`
   as the floor it must clear, and state the capability-vs-policy distinction.
3. **Gate the relationship.** Add `SAFETY_COLUMN_SPEED_CAP_MPS = 0.15` to
   `test_description.py` in the `DRIVER_*` / `ROBOT_MODEL_*` transcription style,
   citing `robot_safety/robot_safety/limits.yaml`, and assert
   `column_lift.limit.velocity > SAFETY_COLUMN_SPEED_CAP_MPS`. Same transcription
   residue as R5 — say so in the comment; do **not** add a `robot_safety`
   test dependency.
4. Correct R10 above and `context.md` Q4's "nowhere in this repo" claim, naming
   the scope error that produced it. A wrong premise recorded as fact is the
   thing that made this defect possible.

### R15 — B3: my R2 rationale was partly wrong. Demote it to reasoning with an expiry.

The red-team upheld R2's **premise** (it measured a 0.06 × 0.06 × 0.08 m overlap
prism between mast and carriage at every reachable *q*) and upheld the parenting.
It falsified the *justification* I gave: a **fixed** rail joint is likely fused
into `base_link` at MJCF time (`fusestatic`), in which case the carriage's MuJoCo
parent is `base_link`'s body either way and the parent/child exclusion I credited
buys nothing. The MoveIt half is also softer than I claimed — there is no default
ACM without an SRDF, and the Setup Assistant disables *always-in-collision* pairs
as well as adjacent ones, which would cover siblings too.

Keep the parenting: it is the honest kinematic description of a carriage on a
rail, which is reason enough. Rewrite the justification everywhere it appears
(**D31 clause 3, `column.xacro:44-53`, `README.md`, `spec.md:127-129`**) as
*expected* behavior, explicitly **unverified until PR7 builds the MJCF**, and
name the `fusestatic` caveat so PR7 checks it rather than inherits it. An
unexecutable claim stated as measurement is precisely what D30's rationale warns
about, and it landed in the very next entry.

### R16 — B4: drop the "and only that test" overclaim.

Five of eight perturbations fail 2–5 tests, and the extra failures are *correct*.
Fix D31 clause 6 and `implementation.md` §5 to claim only what was measured:
"at least that test; in the six single-cause cases, only that test."

### R17 — the six/seven count (manager's step-9 finding).

D31 clause 6 says "the gate grew by **six**". It grew by **seven** —
`test_baseline.json` 14 → 21 settles it, and the clause's own enumeration lists
all seven. The red-team's verdict repeats the error, because both were written
from the prose rather than from `git diff`. Fix D31; the note in `red_team.md`
already records it against the report itself.

### R18 — NOTES: fix these five now, defer three.

**Fix in this PR** — all cheap, and this PR either owns the defect or introduced it:
- **N1** — assert `lower` explicitly (URDF defaults an omitted `lower` to 0, so
  deleting it today leaves the suite green and the acceptance criterion is only
  declaratively enforced for `upper`). Read it off the raw expansion.
- **N2** — guard the degenerate mast (`column_rail_width = 0` passes everything;
  the chassis and carriage both already have positive-dimension guards).
- **N3** — assert the mast's `<visual>` matches its `<collision>`, as the chassis
  and carriage already do ("what a reviewer sees must be what the planner hits").
- **N6** — re-pad `urdf-mjcf-pr-breakdown.md:175`; this PR's own `(done)` marker
  shifted the ASCII merge-order graph so it now reads as PR3.5/PR6 hanging off
  PR4/PR5.
- **N7** — `spec.md:33` claims a `head_camera_link` the URDF does not have. PR3 is
  the D30 sweep of that section and `column_top` now exists, which makes the
  sentence read as current fact. Reword to PR3.5's future tense.

**Follow-up comment on the issue, not this PR:**
- **N4** — the mast's foot is *coplanar* with the chassis top (zero margin), and
  the carriage's underside is coplanar with it too at `q = 0`. Not a penetration,
  and structurally permanent via `column_base_z` — but the base gave its wheels
  5 mm for the same reason, and "clears" is the wrong word in `spec.md`/README/
  the test name. Worth a `column_foot_clearance` estimate; not worth blocking.
- **N5** — the transcription pin is one-directional (drifting `RobotModel` to 1.50
  leaves every package green). Inherent to R5, honestly documented, retired by
  PR6. State it in the PR description so nobody misreads it as a live pin.
- **N8** — ~1.495 m tall on a 0.25 m wheel circle, CoM ≈ 0.38 m, static tip angle
  ≈ 18° before PR4's arms. Both drivers are inherited (LeKiwi's `base_radius`,
  `RobotModel`'s travel), so it is not a PR3 defect — but it is a real stability
  question for PR4/PR7 and should not be discovered from a MuJoCo tip-over.

## Blockers

None outstanding. Round-1 BLOCKs B1–B4 are ruled on above (R13–R17) and are in
the fix round.

## Escalations

None. Two manager rulings (R4's arithmetic, R2's MJCF rationale) were falsified
by the red-team and corrected here rather than defended — which is the loop
working as intended, not a fork for Sisyphus.
