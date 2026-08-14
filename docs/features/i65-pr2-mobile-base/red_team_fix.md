# Red-team — #65 PR2, **fix pass** (`git diff c621941..HEAD`)

Scope: commits `9b6fe96`, `a7cf283`, `861f9c8` only. Round 1 (`red_team.md`)
covered the rest of the PR; nothing here re-reviews it.

Every finding is **VERIFIED** (command + output below it) or **UNVERIFIED**.
All perturbations ran on a `git archive HEAD` copy at `/tmp/rt2` with the
worktree's `.pixi` symlinked in — never in place. Scratch copies deleted;
`git status --porcelain` empty apart from this report.

**Baseline, run first, in the worktree:** `pixi run build` rc 0 (9 packages,
10.3 s); `pixi run test` rc 0 — **756 tests, 0 errors, 0 failures, 0 skipped**,
all ten packages `ok`, `vs-base +0`, `robot_description` 17 collected / **14**
non-lint. `scripts/test_baseline.json` moved exactly one line, 12 → 14. Nothing
outside the owned paths changed (`docs/design/{decisions,spec}.md`,
`docs/features/i65-pr2-mobile-base/*`, `scripts/test_baseline.json`,
`src/robot_description/{README.md,test,urdf}`); `pixi.lock`,
`check_test_integrity.py`, `robot.urdf.xacro` untouched.

## The three round-1 BLOCKs: all genuinely closed

| Round-1 repro | Before | Now | Test that fires |
| --- | --- | --- | --- |
| B1 left/right swap (60↔300) | green | **1 failure** | `test_wheel_mounts_match_the_lerobot_driver_matrix` |
| B1 cyclic +120° (180/300/60) | green | **1 failure** | same |
| B1 global +40° (100/220/340) | green | **1 failure** | same |
| B2 chassis `<visual>`+`<collision>` deleted | green | **1 failure** | `test_solid_links_have_visual_and_collision_geometry` |
| B2 wheel `<visual>` deleted | green | **1 failure** | same |
| B3 `chassis_z_offset` back to 0.03 | green | **1 failure** | same |
| B3 boundary, `chassis_z_offset = 0.0799` | green | **1 failure** | same |

All seven **VERIFIED** by me, not taken from `implementation.md`
(`colcon build --symlink-install --packages-select robot_description` +
`colcon test` + `colcon test-result --all --verbose` per variant, in `/tmp/rt2`).

The transcription of the driver's matrix is **correct** — I fetched
`huggingface/lerobot@main:src/lerobot/robots/lekiwi/lekiwi.py` and read it:

```
lekiwi.py:268-271  angles = np.radians(np.array([240, 0, 120]) - 90)
                   m = np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])
lekiwi.py:290-294  return {"base_left_wheel": wheel_raw[0],
                           "base_back_wheel":  wheel_raw[1],
                           "base_right_wheel": wheel_raw[2]}
lekiwi.py:237-238  wheel_radius = 0.05, base_radius = 0.125   (defaults)
lekiwi.py:397      _body_to_wheel_raw(x, y, theta)            (no overrides at the call site)
```

`DRIVER_ROLLING_ANGLES_DEG` (`test_description.py:120-126`) and
`DRIVER_BASE_RADIUS_M = 0.125` match row-for-row, key-for-key, and the
dict-not-list choice is right — `_wheel_raw_to_body` (`:296-341`) uses the same
row order in the inverse direction, so the key↔row pairing really is the fact
under test.

**Not circular, and not over-tight.** `phi` and `radius` are measured off the
expansion (`_wheel_placements`); the only thing the row formula assumes is the
physical model `d = ẑ × r̂`, which is not derived from the artifact. Measured on
the shipped model, `max|model_row − driver_row| = 1.837e-16` against
`PLACEMENT_TOL_M = 1e-9` — seven orders of margin, so it cannot flake; the
D29 sentence quoting "1.8e-16" is accurate to the digit. I also re-derived
D29's new upstream-CAD numbers independently (fetched
`SIGRobotics-UIUC/LeKiwi@main:URDF/LeKiwi.urdf`, own FK script): wheels at
`r_xy = 0.0992 / 0.1192 / 0.1000`, `phi = 150.35 / 270.29 / 30.35`,
`arm_shoulder_pan` at `90.00` — exactly what the new first clause of D29 now
records. That clause is true.

---

# BLOCK

## BF1 — The N+1th driver constant: `wheel_radius` is pinned nowhere, and a retune is fully green

**VERIFIED.** `src/robot_description/test/test_description.py:127`,
`:574-577`, `:581-593`; `src/robot_description/urdf/base.xacro:44`.

B1's lesson was "the contract with the driver is absolute, assert it". The fix
asserts two thirds of that contract. The driver's kinematics is built from
**three** hard constants — the mount angles, `base_radius = 0.125`, and
`wheel_radius = 0.05` — and the third is the one the driver *divides* by:

```
lekiwi.py:275   wheel_angular_speeds = wheel_linear_speeds / wheel_radius     # command
lekiwi.py:329   wheel_linear_speeds  = wheel_radps * wheel_radius             # odometry
```

Nothing in the suite compares the model's wheel radius to `0.05`.
`test_base_footprint_is_the_ground_projection` reads it off the model and
checks a *relationship*; `_wheel_radius` checks the three agree *with each
other*; the inertias are computed from it. Every one of those is invariant
under retuning the property — which is the exact shape of hole B1 was raised to
close.

```
/tmp/rt2: base.xacro  wheel_radius 0.05 -> 0.04
  -> build/robot_description/pytest.xml: 17 tests, 0 errors, 0 failures, 0 skipped
```

Consequence, and it is not cosmetic: commanded body velocity `v` becomes
`ω = (m·v)/0.05` at the motor, but the wheel is 0.04, so the base actually
moves at **0.8 ×** the commanded speed in every direction, and
`_wheel_raw_to_body` reports the same 1.25 × error back as odometry — a silent,
uniform scale error on both the command and the estimate, exactly the
"self-consistent and wrong" failure D29's new clause is written about.

An increase is *incidentally* caught above 0.055 by the new clearance assert
(`wheel_radius = 0.06` → underside 0.055 < 0.06), which is luck, not a gate.

The docstring makes the omission a stated claim, not just a gap
(`:574-577`): *"the two sourced numbers are deliberately compared against
literals here: `base_radius` and the mount angles"*. But D29
(`decisions.md:106`) and `spec.md:116-118` both name the two **sourced**
numbers as `wheel_radius = 0.05` and `base_radius = 0.125` — the mount angles
are *derived*, not sourced. So the test enumerates a different pair than the
decision log does, and the one number both documents call sourced is the one
left free.

**Fix direction:** a `DRIVER_WHEEL_RADIUS_M = 0.05` constant next to
`DRIVER_BASE_RADIUS_M`, and one clause in the same test comparing
`_wheel_radius(parsed_model)` to it (the helper already exists and is already
imported into that test's neighbourhood). Then correct the docstring to name
the two numbers it actually pins.

## BF2 — The new clearance assert measures an approximation of the chassis, not the chassis; two burials pass it

**VERIFIED.** `src/robot_description/test/test_description.py:692-702`,
helper `:270-285`.

The assert is

```python
chassis_z  = chassis_joints[0].origin.xyz[2]
underside  = chassis_z - chassis_height / 2.0
assert underside >= wheel_radius
```

This is the true underside only if (a) the collision geometry is centred on the
link origin and (b) the chassis joint's rotation is identity. Neither is
asserted anywhere, and both are ordinary things for a URDF to do. Round 1's B3
observed that the *comment*'s stated criterion was subtly wrong; the same class
of error is now in the assertion.

```
/tmp/rt2: chassis <collision> given its own <origin xyz="0 0 -0.06"/>
          (real underside z = -0.005; the puck now engulfs all three wheels)
  -> 17 tests, 0 errors, 0 failures, 0 skipped        # GREEN

/tmp/rt2: base_chassis_joint rpy 0 0 0 -> ${pi/2} 0 0
          (puck on its side: world-z extent [-0.065, +0.235], wheels fully inside)
  -> 17 tests, 0 errors, 0 failures, 0 skipped        # GREEN
```

The same blind spot sits under `_wheel_radius` / `_collision_cylinder`
generally: every dimension this file reads is treated as being expressed in the
link frame at the link origin, and a `<origin>` on any `<visual>`/`<collision>`
silently invalidates that. The chassis clearance is where it bites hardest,
because that assert exists precisely to catch a burial nobody can eyeball.

For contrast, the clauses that *are* tight — I perturbed them and they fire:
chassis visual replaced by a `<box>` → `test_solid_links_have_visual_and_
collision_geometry` fails on the drawn-vs-collided clause; a `<visual>` with no
`<geometry>` → 6 errors / 2 failures at parse time. So the geometry test is
good work; it is the clearance clause specifically that is under-measured.

**Fix direction:** fold the geometry origin into the computation
(`underside = chassis_z + collision.origin.xyz[2] - length/2`) and assert the
chassis joint's `rpy` and the collision's `rpy` are identity — or, cheaper and
more general, one assertion that no link's `<visual>`/`<collision>` carries a
non-identity `<origin>`, stating that every dimension this suite reads is
measured from the link frame. Either is a few lines in the test already there.

## BF3 — D29 now claims a verification that was not performed

**VERIFIED** (by reading both rounds' own evidence lists).
`docs/design/decisions.md:110`, last sentence.

Round 1 read: *"Each assertion was confirmed by perturbation (mis-spaced
mounts, a flipped axis, a dangling child link, a zero wheel mass), each failing
exactly its own test with a legible message."* The fix rewrote it to
*"**Every** assertion was confirmed by perturbation, each failing exactly its
own test with a legible message."* — a strictly stronger claim with the
supporting enumeration removed.

The union of every perturbation recorded in `implementation.md` (round 1: a–d;
round 2: B1 ×3, B2 ×2, B3 ×2, cross-wiring) leaves these shipped clauses never
perturbed by anyone: the `off_root` parent check, the `out_of_plane` z check,
the equal-radii check, the `continuous`-type check, the joint-count check, the
"exactly one joint parenting `base_chassis_link`" check, the degenerate-cylinder
check, the drawn-vs-collided check (I perturbed that one — it does fire), the
mismatched-wheel-radii check, and the `MASSLESS_FRAME_LINKS` must-stay-massless
clause. So the sentence is false as written.

This matters more than a normal doc nit for the reason the round-1 report gave:
`decisions.md` is append-only and wins over every other doc, this PR's whole
fix round exists because D29 previously asserted a property the gate did not
have, and the #55 lesson is cited in the same entry. Restoring the enumerated
form ("confirmed by perturbation — mis-spaced mounts, a flipped axis, a
left/right swap, a deleted `<visual>`, a buried chassis, a cross-wired joint —
each failing exactly its own test") costs one line and is true.

Same clause, same sentence-level care: **"The gate grew by seven, and half of
that growth is the red-team's."** The growth of seven is correct (non-lint 7 →
14, confirmed against `test_baseline.json` and the audit table). The red team's
share of it is **two** of those seven tests (the driver-matrix test and the
geometry test), plus two clauses inside pre-existing tests. "Half" is not
supportable in the unit the sentence itself just used.

---

# NOTE

## NF1 — The driver-matrix test never reads `<axis>`, so it is only correct because its neighbour is

`test_description.py:581-593`. The row is rebuilt as `(-sin φ, cos φ, r)` from
the *mount position* alone; the rolling direction `d = ẑ × r̂` is assumed. A
wheel at the right angle with a tangential or vertical spin axis produces a
completely different matrix and passes this test — it is caught only by the
axis clause in `test_wheel_mounts_are_120_degrees_apart:539-550`. That is fine
today (both ship), but the docstring says "the matrix rebuilt from where the
wheels actually sit", and a future relaxation of the axis clause would silently
un-ground this one. Deriving `d` from the already-computed `axis_in_base`
(`d = ẑ × axis`) would make it self-contained and strictly stronger, at the
cost of ~2 lines.

## NF2 — The clearance criterion is sufficient but not necessary

`test_description.py:695`. `underside >= wheel_radius` ignores radial
separation. It is right for the shipped puck (radius 0.15 vs a wheel band of
[0.110, 0.140]), but it forbids a legitimate future design — a narrower chassis
(radius < 0.11) hanging between the wheels — under a message that says the
chassis "intersects the wheels" when it would not. Conservative is the right
direction for a gate; worth a sentence in the docstring saying the check is
deliberately the strong form.

## NF3 — `PLACEMENT_TOL_M` used as a dimensionless tolerance

`test_description.py:587`. Columns 0 and 1 of the compared rows are sines and
cosines, not metres; only column 2 is a length. Harmless (1e-9 is right for
both, and the observed error is 1.8e-16), but the constant's name asserts units
it does not have here.

## NF4 — The N+1th site of the doc sweep: the roadmap's §PR2 "Test:" bullet

`docs/design/urdf-mjcf-pr-breakdown.md:67-72` still enumerates only the round-1
gate: "wheel joint names/types/count (=3); the 120° mount spacing and the
outward-radial spin axis after rpy composition; `base_footprint` at minus the
wheel radius; inertia on every non-frame link; still expands + parses; loadable
by `robot_state_publisher`." The driver-matrix assertion, the visual/collision
assertion and the clearance assertion are missing, and the bullet is marked
**DONE**, so it reads as the record of what PR2 shipped. `README.md`,
`meshes/README.md`, `spec.md`, D29 and the test docstrings were all updated;
this one was not. Not a false claim (it is incomplete, not wrong), which is why
it is a NOTE — but it is exactly the N+1th-place pattern this PR keeps hitting.

## NF5 — One unwrapped line in the README reflow

`src/robot_description/README.md:43` is 120 characters where its neighbours are
71–78. Artifact of the paragraph rewrite.

---

# Checked and fine

- **B1's new test is only sensitive to real mis-mounts.** Beyond the three
  round-1 repros I looked for a residual mis-model the *pair* of layout tests
  both pass and did not find one: a mirror about x, a global rotation, any
  permutation, a wheel off the axle plane, an inward or tangential axis, and a
  wheel joint hung off the chassis are each caught by one of the two. The
  mounts are now pinned absolutely; radius, angle, axis and child link all have
  an owner.
- **`test_wheel_joints_are_exactly_three_continuous`'s new child-wiring clause**
  is real work and correctly *not* redundant with the matrix test — a
  cross-wiring leaves the mounts right, so only the wiring clause sees it.
- **The geometry test does not over-reach.** `MASSLESS_FRAME_LINKS` is skipped,
  so `base_link` and `base_footprint` are not required to carry geometry;
  a zero-radius wheel is still caught (by `izz = 0` in the inertia test); a
  malformed `<visual>` fails loudly at parse time.
- **B3's numbers.** `chassis_z_offset = 0.085`, `chassis_height = 0.06` →
  underside `0.055` vs wheel top `0.05`: 5 mm clear, matching the comment
  exactly. The comment's corrected N4 arithmetic checks out too
  (`hypot(0.14, 0.05) = 0.14866`, `0.15 − 0.1487 = 1.3 mm`). Nothing else was
  tuned to the old 0.03: inertias are about the link's own centre and are
  z-offset-independent, `base_footprint` is off `wheel_radius`, and no doc
  quotes a chassis height (`grep` for `0.085 | chassis_z_offset | 0.115` finds
  only the property, its comment, the failure message and `implementation.md`).
- **N6's guard did not break the teardown path.** With `RSP_READY_MARKER`
  changed to an unreachable string on the scratch copy,
  `test_model_loads_in_robot_state_publisher` fails cleanly — 1 failure,
  `real 0m31.672s` for the whole `colcon test`, i.e. the 30 s deadline, not a
  hang — and `ps -ef | grep [r]obot_state_publisher` is empty afterwards. The
  guard `if not reader.is_alive()` is correct on the normal path (the pipe
  closes when the group dies, the reader returns, the fd is closed).
- **Ratchet and scope.** `robot_description` 12 → 14 and no other entry;
  audit `vs-base +0` for all ten packages; no new lint failures (the three
  lint tests still pass); no test removed or skipped.
- **Worktree left as found.** `/tmp/rt2` and the fetched upstream files
  deleted; `git status --porcelain` clean apart from this report.

---

## Summary

**BLOCK: 3** — BF1 (`wheel_radius`, the driver's third constant, is ungated; a
retune is green and silently scales every velocity by 0.8 — the same hole B1
closed for `base_radius`), BF2 (the new clearance assert ignores the collision
`<origin>` and the joint `rpy`; two verified burials stay green), BF3 (D29 now
claims "every assertion was confirmed by perturbation", which ~10 shipped
clauses contradict, plus the "half of that growth" arithmetic).

**NOTE: 5** — NF1 (matrix test assumes the axis rather than reading it), NF2
(clearance criterion is the strong form; say so), NF3 (metre tolerance used
dimensionlessly), NF4 (roadmap §PR2's test list not updated), NF5 (long line).

The three round-1 BLOCKs are genuinely fixed — I re-ran all seven repros and
every one now fails exactly the intended test with a legible message, and the
driver transcription is correct against upstream. BF1 and BF2 are the same
shape as B1 and B3 respectively, one constant and one term further out; both
are a handful of lines in tests that already exist. BF3 is one sentence in the
decision log.
