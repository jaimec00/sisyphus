# Red-team — #65 PR2: mobile base URDF (3-omniwheel holonomic, LeKiwi crib)

Reviewed: `git diff origin/main...HEAD` (`e69cf96..c621941`), issue #65,
`context.md`, `implementation.md`, `status.md` (R1–R14), D26/D27/D28/**D29**,
`urdf-mjcf-pr-breakdown.md` §PR2.

Every finding is labelled **VERIFIED** (command + output below it) or
**UNVERIFIED**. All perturbation experiments ran on `git archive HEAD` copies
under `/tmp/rt_*`, never in the worktree; the copies are deleted and
`git status --porcelain` is empty.

**Baseline established first:** `pixi run build` → rc 0, `pixi run test` → rc 0,
**754 tests, 0 errors, 0 failures, 0 skipped**, `robot_description` 15 collected
/ 12 non-lint, `vs-base +0`. `scripts/test_baseline.json` moved only the
`robot_description` entry, 7 → 12 (R13 ✅). Scope (R14) holds: the diff touches
no `robot_backends`/`robot_brain`, no `column.xacro`/`arm.xacro`, no
`robot.urdf.xacro`, no `check_test_integrity.py`, and `pixi.lock` is unchanged.

---

## Verdict on the rulings you asked me to attack

| Claim | Result |
| --- | --- |
| **C1** mounts are 60/180/300; the driver's `[240,0,120]` is rolling directions | **Upheld.** Re-derived three independent ways. No defensible alternative. |
| **C2** the 120° test makes the wheel layout a gate | **Broken. Your hypothesis was right — BLOCK-1.** Two permutations verified green. |
| **C3** chassis and wheels interpenetrate | **Confirmed. BLOCK-3.** 50 % of every wheel's collision volume is inside the chassis puck. |
| **C4** R3's mesh deferral is recorded and the asset gate stays live | **Upheld** (six places; the assertion fires on an injected mesh). Two stale sentences → NOTE. |
| **C5** `base_footprint` is a fixed child at −`wheel_radius` | **Upheld.** Confirmed on the live `/tf_static`. |
| **C6** the `robot_state_publisher` test | **Upheld.** No hang, no leak, no false pass, no domain collision. One unguarded call → NOTE. |
| **C7** the sign correction is right and complete | **Substance upheld** (re-derived from the shipped expansion). One N+1th place found → NOTE. |
| — | **Additionally: BLOCK-2**, the issue's own first acceptance criterion is entirely ungated. |

---

# BLOCK

## B1 — The wheel gate does not pin *which* wheel is where; a name↔angle permutation is fully green

**VERIFIED.**
`src/robot_description/test/test_description.py:403-468`
(`test_wheel_mounts_are_120_degrees_apart`), `src/robot_description/urdf/base.xacro:83-85`.

Your hypothesis (C2) is correct, and it is worse than you framed it: **three**
distinct classes of mis-mount survive the gate, not just the name swap.

### Repro 1 — swap `base_left_wheel` and `base_right_wheel`

```
git archive HEAD | tar -x -C /tmp/rt_c2          # scratch copy, .pixi symlinked
# base.xacro: wheel_angle_left 60 -> 300, wheel_angle_right 300 -> 60
colcon build --symlink-install --packages-select robot_description
colcon test --packages-select robot_description && colcon test-result --all --verbose
  -> build/robot_description/pytest.xml: 15 tests, 0 errors, 0 failures, 0 skipped
```

The expansion really did change (checked, not assumed):

```
<joint name="base_left_wheel"  ...> <origin ... xyz="0.0625 -0.10825 0"/>   # front-RIGHT
<joint name="base_right_wheel" ...> <origin ... xyz="0.0625  0.10825 0"/>   # front-LEFT
```

Every wheel is still on the circle, still 120° apart, and each axis is still the
outward radial *at its own origin* — so nothing fires. What the robot now does,
solving the driver's commanded wheel speeds against the physical mounts:

```
cmd forward +x=1   -> actual body [vx,vy,w] = [-1.  0.  0.]     # drives BACKWARD
cmd left    +y=1   -> actual body [vx,vy,w] = [ 0.  1.  0.]
cmd yaw ccw  w=1   -> actual body [vx,vy,w] = [ 0.  0.  1.]
```

### Repro 2 — cyclic +120° (left=180, back=300, right=60)

```
/tmp/rt_c2b: 15 tests, 0 errors, 0 failures, 0 skipped
cmd forward     -> [-0.5    0.866  0.]     # drives 120 deg off
cmd strafe left -> [-0.866 -0.5    0.]
```

That is *literally* the failure D29's rationale claims the gate prevents —
"a robot that drives sideways when told to drive forward" — and it is green.

### Repro 3 — global rotation of all three mounts by +40°

Same class (all relationships preserved). Not built, but arithmetically
identical to repro 2: `forward -> [0.766, 0.643, 0]`.

### Why the gate misses it

`_wheel_placements` (`:215-234`) *measures* the angle off the expansion, and
every subsequent assertion compares measured quantities to each other:
equal radii, pairwise 120° gaps, and `axis == outward radial at its own
measured angle`. All three are invariant under any rotation or permutation of
the mount set. The name↔angle mapping — the one fact PR6 will trust and the one
number nobody can eyeball — is asserted **nowhere**. The docstring's claim that
"nothing here is compared against a literal dimension … while breaking the
*relationship* between the wheels is [a test edit]" is exactly the design flaw:
the contract with the LeRobot driver is not a relationship between the wheels,
it is an absolute mapping from motor key to body-frame direction.

Additionally ungated in the same test: nothing asserts
`joint.child == joint.name + '_link'`. Cross-wiring the two would also be green
(the macro makes it unlikely, but the gate should not depend on that).

### Fix direction

Assert the actual contract, not an internal relationship. The strongest form is
~8 lines and needs no new dependency — rebuild the driver's kinematic matrix
from the parsed model, in the driver's own row order, and compare it to the
constant:

```
rows in order (base_left_wheel, base_back_wheel, base_right_wheel):
   model row = (-sin phi_i, cos phi_i, radius_i)     # phi, radius measured off the expansion
   driver row = (cos a_i, sin a_i, 0.125)            # a = radians([240, 0, 120] - 90)
   assert allclose(model, driver)
```

I verified this comparison passes on the shipped model to `max|diff| = 1.8e-16`
and fails on all three repros above. It subsumes the existing radius/spacing/
axis clauses, states the PR6 contract literally, and keeps the "retuning is not
a test edit" property for everything except the two sourced numbers (which
*should* be a test edit — they are the driver's).

Minimum acceptable alternative: assert each named joint's measured angle equals
its own named property (`base_left_wheel` → 60, `base_back_wheel` → 180,
`base_right_wheel` → 300) within `ANGLE_TOL_DEG`.

### Doc consequence (must land with the fix)

- `docs/design/decisions.md:110`, D29 fifth clause, *Rationale:* — "asserting
  the *relationships* between them (rather than the literals) … **makes
  mis-mounting it a test failure**" is false as shipped. Either the gate becomes
  true to it or the sentence goes. D29 is append-only and the longest-half-life
  artifact in the diff; #55's lesson applies exactly here.
- `test_description.py:411-415` — "a wheel that moves here without the driver
  moving with it is a robot that drives sideways when told to drive forward"
  asserts the same false fact in the docstring of the test that does not catch
  it.

---

## B2 — The issue's own first acceptance criterion (collision + visual geometry) is entirely ungated

**VERIFIED.**
`src/robot_description/test/test_description.py` (no test covers it),
`src/robot_description/urdf/base.xacro:99-110` (chassis), `:167-176` (wheels).

Issue #65's first scope bullet is "`base_link` collision + visual geometry".
R1 correctly reinterprets *where* that geometry lives (`base_chassis_link`) —
I agree with R1: D27 (`decisions.md:90`) fixes `base_link` in the top level,
xacro cannot merge two `<link>`s of one name, and CLAUDE.md makes
`decisions.md` win. But the criterion itself was never turned into an assertion.

### Repro 1 — delete the chassis's `<visual>` **and** `<collision>` entirely

```
/tmp/rt_c8: base_chassis_link reduced to <link><inertial>…</inertial></link>
  -> build/robot_description/pytest.xml: 15 tests, 0 errors, 0 failures, 0 skipped
```

### Repro 2 — delete the wheels' `<visual>`

```
/tmp/rt_c9: omni_wheel macro's <visual> block removed
  -> build/robot_description/pytest.xml: 15 tests, 0 errors, 0 failures, 0 skipped
```

So the description can ship a robot with **no visual geometry at all** and with
**no body collision geometry**, and the gate — whose entire justification (D27,
D29) is "geometry is the part a human reviewer cannot eyeball" — stays green.
Wheel *collision* is gated only incidentally, as a side effect of
`test_base_footprint_is_the_ground_projection:482-490` reading `collisions[0]`
to recover the wheel radius; nothing gates the chassis, and nothing gates any
visual.

This is the "weak/inadequate tests — don't cover the acceptance criteria" branch
of the rubric, and it is the same shape as the hole D27's own red-team pass
closed for `<texture>`.

### Fix direction

One small test in house style: every link not in `MASSLESS_FRAME_LINKS` has at
least one `<visual>` and at least one `<collision>`, and (cheap, and it makes the
chassis's dimensions reviewable) the chassis collision is a cylinder whose
`radius`/`length` match the `chassis_radius`/`chassis_height` recovered from the
model. Pairs naturally with `test_moving_links_have_inertia`, which already
walks the same link set for the same reason.

---

## B3 — Chassis and wheel collision geometry interpenetrate by a full wheel radius

**VERIFIED (geometry).** **UNVERIFIED (sim consequence — `mujoco` is not in this
env: `ModuleNotFoundError: No module named 'mujoco'`).**
`src/robot_description/urdf/base.xacro:53-62` (properties), `:96` (chassis
joint origin), `:99-110` (chassis geometry), `:159-176` (wheel geometry).

Computed from the shipped properties:

```
chassis puck : radial [0, 0.150],  z in [0.000, 0.060]
wheel disc   : axle at radius 0.125, half-thickness 0.0150 -> radial band [0.110, 0.140]
wheel z extent: [-0.050, 0.050]
wheel max |xy| from origin: 0.1487   (< chassis_radius 0.150)
=> penetration depth 0.050 m (= one full wheel_radius)
=> buried volume 1.178e-04 m^3 of 2.356e-04 m^3  = 50% of every wheel
```

`chassis_z_offset = 0.03` with `chassis_height = 0.06` puts the puck's underside
at **exactly z = 0**, the axle plane — so the entire upper half of each wheel is
inside the chassis solid. The comment at `:60-62` ("lifts the plate to sit just
above the wheels' *axles*") is literally what the number does; it is the
consequence that is wrong.

`base_chassis_link` and the wheel links are **siblings** (both children of
`base_link` — one fixed, three continuous), not a parent/child pair, so this is
not a contact a physics engine filters as a matter of course. In RViz it renders
as three wheels sunk to their axles in the body; in MoveIt's SRDF generation the
pair is permanently "always in collision" and gets disabled, which silently
masks a *real* chassis↔wheel collision if a later PR moves either; and in PR7's
MJCF, if the fixed chassis link survives as its own body rather than being
lumped into `base_link`, sibling contacts are not excluded and the base starts
in deep penetration.

Nothing in the suite sees any of this — every assertion is about joints,
frames, masses and names.

I am calling this BLOCK rather than NOTE because it is a correctness bug in the
one artifact this PR exists to produce, it is invisible to the gate, and it is
inherited by PR3 (which mounts on the chassis) and PR7. The counter-argument —
that all three numbers are declared `ESTIMATED` and are placeholders — is real;
if the manager downgrades it, the *comment* must at least stop implying the
wheels clear the body.

### Fix direction

`chassis_z_offset` ≥ `wheel_radius + chassis_height/2` = **0.08** clears it
exactly (puck underside at z = 0.05 = wheel top). Whatever value is chosen, make
the relationship a gate rather than a coincidence — the constraint
`chassis_z_offset - chassis_height/2 >= wheel_radius` is one assertion in the
same test as B2's, and it is precisely the kind of number-nobody-can-eyeball
that D29 argues should be asserted rather than commented.

---

# NOTE

## N1 — The N+1th place for the sign over-claim: R6 itself

`docs/features/i65-pr2-mobile-base/status.md:269-272` still reads "a **positive**
joint velocity drives the contact point along `+d`, **the same sign the LeRobot
driver's `wheel_linear_speeds` uses**" — the exact clause that was corrected in
`base.xacro`'s macro comment, `base.xacro`'s header, and the test's assertion
message. The Phase 2 section of the same file (`:118-126`) carries an explicit
correction note; the **ruling** does not, and the ruling is what a fix-round
agent reads as binding. Ephemeral doc, so NOTE, not BLOCK.

**The substance is right, and I re-derived it rather than taking it.** From the
shipped expansion, for `theta_dot = +1`:

```
base_left_wheel   phi= 60.000  axis_in_base=[0.5, 0.8660254, 0]  d_hat=[-0.866025, 0.5, 0]
                  contact-material v = 1.0 * r_w * d_hat  =>  body v along -d
base_back_wheel   phi=180.000  ...                                =>  body v along -d
base_right_wheel  phi=300.000  ...                                =>  body v along -d
model-derived kinematic matrix == driver m : True   max|d| = 1.8369701987210297e-16
```

D29's statement (`decisions.md:107`) — contact-patch material along `+d̂`, body
along `−d̂`, driver's positive means body along `+d̂`, physical motor direction is
a calibration fact — is correct as written. No other file in the diff or in the
durable docs repeats the old version (swept `docs/`, `src/` for
`wheel_linear_speeds`, `same sign`, `contact point`, `no sign fix`).

## N2 — Two sentences still say PR2 lands the first `.stl`

`src/robot_description/test/test_description.py:60-61` (module docstring: "they
cost nothing until PR2 adds the first `.stl`") and `:636-641`
(`test_every_asset_reference_resolves`: "costing nothing until PR2 imports the
first LeRobot .stl"). R3/D29 rule the opposite, and every *other* location was
updated (`README.md`, `meshes/README.md`, `setup.py:22-31`, `spec.md:118-122`,
breakdown §PR2 and §PR4, D29 fourth clause — six places, so C4(a) is otherwise
well covered).

**The assertion itself is not vacuous** — VERIFIED by injecting a mesh
reference on a scratch copy:

```
/tmp/rt_c4: chassis <visual> geometry -> <mesh filename="package://robot_description/meshes/chassis.stl"/>
  -> 15 tests, 0 errors, 1 failure
  AssertionError: 1 of 1 asset reference(s) do not resolve to a file on disk ...
```

It will wake up on PR4's first `.stl` exactly as designed.

## N3 — D29's heading does not name D27 as the entry it amends

House convention (`decisions.md:38`, `:74`) puts the pointer in the `##` heading
("supersedes the brain location in D16", "supersedes the base + lift specifics
of D1"). D29's heading (`:103`) says only "The base is authored from LeKiwi's
numbers, not copied from its files"; the D27 amendment lives inside the fourth
clause. Meanwhile `decisions.md:90` (D27, append-only, uneditable) still reads
"**PR2**, which imports the first real mesh set, is where the `os.walk` version
gets written". A PR4 author reading `decisions.md` top-down or scanning headings
can land on the stale claim and never reach the amendment. Cheap fix: add
"(amends D27's PR2 mesh bullet)" to the D29 heading.

## N4 — The "wheels do not stick out past the body" comment states the wrong criterion

`src/robot_description/urdf/base.xacro:54-57`: "Radius is deliberately a little
larger than `base_radius + wheel_width/2`". The binding quantity is the wheel's
farthest xy point, `hypot(base_radius + wheel_width/2, wheel_radius) = 0.1487`,
not `0.140`. As shipped the conclusion happens to hold with 1.3 mm of margin,
but retuning `wheel_radius` to 0.06 breaks the stated intent with no comment and
no test noticing.

## N5 — `base_radius = 0.125` is the driver's number and disagrees with upstream's mechanics

Resolving upstream `SIGRobotics-UIUC/LeKiwi`'s `URDF/LeKiwi.urdf` wheel joints
through its CAD link chain by forward kinematics (my own script, independent of
the implementer's):

```
base_left_wheel   pos=[-0.0862  0.0491 0.0179]  r_xy=0.0992  phi=150.35  axis.rhat=+1.0000
base_back_wheel   pos=[ 0.0006 -0.1192 0.0179]  r_xy=0.1192  phi=270.29  axis.rhat=+1.0000
base_right_wheel  pos=[ 0.0863  0.0505 0.0179]  r_xy=0.1000  phi= 30.35  axis.rhat=+1.0000
```

Upstream's three wheels are **not on a common circle** (0.0992 / 0.1192 /
0.1000 m) and none of them is at 0.125. The model is right to follow the
*driver* (the driver is the contract PR6 cashes in), but D29's first clause
calls 0.125 a "sourced dimension" without recording that upstream's own
mechanical layout disagrees with it by 5–25 %. Worth one sentence somewhere
durable, because PR6/PR7 will hit it.

## N6 — One unguarded blocking call on the `robot_state_publisher` teardown path

`src/robot_description/test/test_description.py:627-630`. `reader.join(timeout=10)`
is bounded; the `process.stdout.close()` immediately after it is not. If the
reader thread is still blocked inside `read()` — the exact deadlock the
`_terminate_group` docstring describes — `close()` waits on the buffer lock
forever and the test hangs rather than failing. Reachable only if
`_group_alive` returns a false negative (`:267-273` treats `PermissionError` as
"dead") or a descendant escaped the process group.

**I could not reach it.** Everything I tried behaved correctly:

- **Timeout path** (marker changed to an unreachable string on a scratch copy):
  fails cleanly at `30.21s` with the full captured output in the message
  (`real 0m30.499s`); `pgrep -af robot_state_publisher` afterwards → nothing.
- **Early-exit path**: `robot_description: ""` makes the node abort rc 250
  (`robot_description parameter must not be empty`), i.e. the failure branch is
  live and fast.
- **False pass**: the marker `Robot initialized` is emitted only after the KDL
  tree is built; an empty or unparseable model aborts instead. No path to a
  green result without the node accepting *this* model.
- **`ROS_DOMAIN_ID = 77` collision**: three concurrent runs of the test on
  domain 77 — `1 passed` × 3, no leak. Not a practical hazard.
- **`ros2 run` really does fork** (`ros2run/api/__init__.py:64`:
  `process = subprocess.Popen(cmd)`), so the process-group teardown is
  necessary, not defensive. That claim in D29 checks out.
- Marker latency measured at **0.166 s** on this machine, against a 30 s
  deadline — ample headroom.

Cheap hardening: `if not reader.is_alive(): process.stdout.close()`, or wrap it
in `contextlib.suppress`.

## N7 — `MASSLESS_FRAME_LINKS` *asserts* `base_link` stays massless

`test_description.py:533-535`. Correct today. Flagging only because PR7 may need
`base_link` to carry inertia if it becomes the MJCF free-floating root body and
the fixed chassis link is not lumped into it; that PR will have to change the
assertion, not just add to it. Not a defect.

---

# Things I checked that are fine

- **C1 — the mount mapping, derived independently three ways.** (a) From the
  driver directly: `angles = radians([240,0,120]-90) = [150°, -90°, 30°]`; the
  row `(cos a, sin a)` is the direction of body velocity that produces positive
  wheel speed, i.e. the rolling direction `d̂ = ẑ × r̂ = (cos(φ+90), sin(φ+90))`,
  so `φ = a − 90` = **60 / 180 / 300**, matching the driver's `m` to
  `1.8e-16`. The alternative reading `φ = a + 90` gives exactly the trap
  `[240, 0, 120]` — and is **ruled out** because it forces the third column to be
  `−base_radius`, while the driver has `+base_radius`. (b) The wheel *named*
  `base_back_wheel` sits at CAD `270.29°`; under `φ = a + 90` the rear wheel
  would be at the front. (c) Upstream's arm (`arm_shoulder_pan`) sits at CAD
  `90.00°`, diametrically opposite the back wheel — so CAD `+y` is forward, the
  CAD→REP-103 offset is `+90.35°`, and `150.35/270.29/30.35 − 90.35` =
  **60 / 180 / 300**. No defensible alternative puts `base_left_wheel` anywhere
  but front-left at 60°. R6/C1 upheld.
- **C5 — booted `robot_state_publisher` on the expanded model and read the live
  `/tf_static`:**
  ```
  frame_id: base_link  child_frame_id: base_chassis_link   z: 0.03   rot: identity
  frame_id: base_link  child_frame_id: base_footprint      z: -0.05  rot: identity
  ```
  Direction and offset are exactly what the URDF claims: `base_footprint` is the
  child, at `−wheel_radius`. R5 upheld, and the recorded consequence
  (`odom → base_link`, never `odom → base_footprint`) follows correctly.
- **R1** — I agree the issue's literal "`base_link` collision + visual geometry"
  is unbuildable under D27, and `base_chassis_link` honours the intent. (What is
  *not* honoured is that the geometry ended up untested — B2.)
- **R8 inertias are correct for the axis convention used.** URDF `<cylinder>` is
  along the link's +z, and the wheel's joint axis is `0 0 1` in that same frame,
  so `izz = m r²/2` is the spin moment and `ixx = iyy = m(3r² + h²)/12` are the
  transverse ones — right formulas on the right axis. Both blocks omit
  `<origin>`, and both links' geometry is centred at the link origin, so the
  tensors are stated about the correct point. Chassis: `ixx = iyy = 0.03555`,
  `izz = 0.0675` — `izz = ixx + iyy` to 4 dp, correct for a thin-ish disc and
  physically admissible. Wheel: `ixx = iyy = 1.05e-4`, `izz = 1.875e-4`,
  admissible.
- **Tolerances.** `PLACEMENT_TOL_M = 1e-9` / `ANGLE_TOL_DEG = 1e-6` are right,
  not flaky: the quantities compared are `atan2`/`hypot` round-trips of xacro's
  own `cos`/`sin` output through the URDF text (`0.06250000000000001`,
  `1.5308084989341915e-17`), whose error is ~1e-16 — seven orders of margin.
  Nothing here can drift into the tolerance; conversely 1e-9 m is 1 nm, far
  tighter than any real mis-mount. Good choices.
- **The other four new tests do real work.** I re-confirmed the implementer's
  sabotage set independently in spirit: an unreachable marker fails the RSP
  test at 30 s (not a hang), a dangling child link aborts the node, and a
  removed asset reference fires the resolution test. `test_moving_links_have_
  inertia` and `test_wheel_joints_are_exactly_three_continuous` are not
  tautological — both assert facts the model could plausibly lose (a `revolute`
  retype, a rename off the LeRobot motor keys, a zero mass) that no other
  assertion sees. `test_base_footprint_is_the_ground_projection` deriving the
  wheel radius from the model's own collision cylinders instead of a literal is
  the right call and is the only thing currently gating wheel collision geometry
  at all.
- **Acceptance criteria vs. what landed:** xacro expands ✅; `check_urdf` parses
  ✅; exactly 3 `continuous` wheel joints with the expected names ✅;
  `EXPECTED_LINKS` grown deliberately to 6 ✅; loadable by
  `robot_state_publisher` ✅ (booted it myself); full suite green ✅; ratchet up
  7 → 12, `robot_description` only ✅. The one criterion **not** met is
  "`base_link` collision + visual geometry" — present in the model, absent from
  the gate (B2).
- **D29 against the diff as it landed:** every factual clause holds except the
  fifth clause's closing rationale (B1). The mount mapping, the axis
  composition, the `base_footprint` direction and offset, the two sourced
  dimensions, the `ESTIMATED` markings, the params-file rationale, the
  process-group rationale, and the "verified numerically" claims are all true
  and all re-verified above. D29 does not claim any verification that was not
  done.
- **Worktree left exactly as found:** `git status --porcelain` empty; all
  `/tmp/rt_*` scratch copies removed; no leaked `robot_state_publisher`
  processes (`pgrep` clean after every experiment).

---

## Summary

**BLOCK: 3** — B1 (the wheel gate does not pin the name↔angle mapping; D29 and a
test docstring over-claim it), B2 (the base's collision and visual geometry —
the issue's first acceptance criterion — is entirely ungated), B3 (chassis and
wheels interpenetrate by a full wheel radius).

**NOTE: 7** — N1 (R6 still carries the corrected sign claim), N2 (two "PR2 lands
the first `.stl`" sentences in the test file), N3 (D29's heading does not name
D27), N4 (the "wheels don't stick out" criterion is stated wrong), N5
(`base_radius` disagrees with upstream's mechanics), N6 (unguarded
`stdout.close()`), N7 (PR7 will have to change the massless-`base_link` assert).

B1 and B2 are the same shape and share a fix round: the model is right, and the
gate does not know it. B3 is the only finding where the *model* is wrong.
