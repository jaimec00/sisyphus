# Implementation — #65 PR2: mobile base URDF (3-omniwheel holonomic, LeKiwi crib)

All rulings R1–R14 implemented as written. Nothing was deviated from; two
things were *added* to a ruling's rationale on execute-verified evidence, and
both are flagged below for the manager (§"For the manager").

## What was built

### `src/robot_description/urdf/base.xacro` (R1, R2, R6, R7, R8)

A parametric base, not a copied one:

- **`base_chassis_link`** — a cylinder puck (visual + collision + inertial),
  fixed to `base_link` via `base_chassis_joint` at `z = chassis_z_offset`.
  `base_link` itself is untouched (R1): it is declared in `robot.urdf.xacro`,
  xacro does not merge two `<link>`s of one name, and D27 fixes it as the
  assembly's root. `urdf/robot.urdf.xacro` was not edited at all.
- **`base_footprint`** — a massless frame, fixed child of `base_link` at
  `xyz = (0, 0, -wheel_radius)` (R5).
- **`xacro:macro name="omni_wheel" params="name angle"`**, instantiated three
  times. `name` is the joint name; the link is `${name}_link`, derived inside
  the macro so the pairing cannot drift (R2). Origin
  `xyz = (base_radius·cos φ, base_radius·sin φ, 0)`,
  `rpy = (0, π/2, radians(φ))`, `<axis xyz="0 0 1"/>` — i.e. the child link's
  +z is the spin axis, pointing radially outward (R6).
- **Properties for everything** (R7). Two are marked `SOURCED` with their
  origin (`wheel_radius = 0.05`, `base_radius = 0.125`, from LeRobot's
  `lekiwi.py`); the rest are marked `ESTIMATED` in the file. Mount angles are
  three named degree values (`wheel_angle_left/back/right` = 60/180/300)
  converted with `${radians(...)}` at use, so "120° apart" is a property of the
  three values rather than an arithmetic identity.
- **Inertials computed from the same properties** (R8) by the solid-cylinder
  formulas, so retuning a dimension cannot leave a stale tensor behind.
  `base_link` and `base_footprint` stay massless frames.
- **Attribution is a header comment** (R4) naming LeKiwi's URDF +
  `JOINT_NAMES.md` (Apache-2.0), LeRobot's `lekiwi.py` (Apache-2.0) and
  XLeRobot (MIT), and which specific constant came from where. No `NOTICE`
  file, because no third-party file is vendored (R3).

No meshes, no `setup.py` `data_files` change (R3). `meshes/` still holds only
its README.

### `src/robot_description/test/test_description.py` (R9, R10)

`EXPECTED_LINKS` grows to the six links of R2. Five new test functions, in the
existing one-concern-per-function style; **no existing test function's body,
`SUBASSEMBLIES`, or `FILE_BEARING_TAGS` was touched.** A module-scoped
`parsed_model` fixture was added (the two PR1 tests that parse inline were left
as they are).

1. `test_wheel_joints_are_exactly_three_continuous`
2. `test_wheel_mounts_are_120_degrees_apart` — the load-bearing one. Parent is
   `base_link`; axle z == 0; all three radii equal and > 0; the three mount
   angles pairwise 120° apart; and each joint's `axis` **rotated by its own
   rpy** equals the outward radial unit vector at that wheel's own measured
   angle. Nothing is compared against a dimension literal — the radius and the
   angles are measured off the expansion, so retuning `base_radius` is not a
   test edit while breaking the *relationship* is. `math` only, no numpy: a
   15-line `_rotation_from_rpy` helper.
3. `test_base_footprint_is_the_ground_projection` — fixed, parent `base_link`,
   `xyz == (0, 0, -wheel_radius)` with `wheel_radius` read from the model's own
   wheel collision cylinders (and asserted identical across the three).
4. `test_moving_links_have_inertia`
5. `test_model_loads_in_robot_state_publisher` — R10's shape: `Popen`, stderr
   merged into stdout, read incrementally by a daemon thread, pass on the
   `Robot initialized` marker, fail on early exit (reporting rc + captured
   output) or on a 30 s deadline, teardown in `finally`, dedicated
   `ROS_DOMAIN_ID`, no `launch_testing`.

`package.xml` gains `<test_depend>robot_state_publisher</test_depend>` (rosdep
name) with the block comment extended (R11). Stale "4-wheel base" strings fixed
in `setup.py:description`, `package.xml:<description>`, `README.md:3` (R12).

### Docs (R12)

`D29` appended to `docs/design/decisions.md` (D1–D28 untouched); §PR2 marked
DONE in `docs/design/urdf-mjcf-pr-breakdown.md` with the `os.walk` bullet moved
to PR4 citing D29; `docs/design/spec.md`'s "Description & packaging" section
refreshed; `README.md` and `meshes/README.md` updated (the "geometry arrives
with the base (PR2)" sentence in particular — under R3 it does not).

## Two rulings I checked hard rather than took on trust

### R6's mount angles — confirmed, from a *second* independent source

Beyond re-deriving the driver-matrix identity, I resolved upstream LeKiwi's own
wheel joints through its CAD link chain into its root frame
(`base_plate_layer1-v5`) by forward kinematics:

```
base_left_wheel   mount pos [-0.0862, 0.0491, 0.0179]  radius_xy=0.0992  phi=150.35 deg
base_back_wheel   mount pos [ 0.0006,-0.1192, 0.0179]  radius_xy=0.1192  phi=270.29 deg
base_right_wheel  mount pos [ 0.0863, 0.0505, 0.0179]  radius_xy=0.1000  phi= 30.35 deg
axis . outward-radial = 1.0000   (all three)
```

Subtracting the ~90.35° rotation between that CAD frame and REP-103 gives
**60° / 180° / 300°** — exactly R6, from a source R6 did not use. The axis
resolves to the outward radial to four decimals for all three wheels, so R6's
`rpy = (0, π/2, φ)` + `axis = 0 0 1` reproduces upstream's convention exactly.
R6 is right; I implemented it unchanged.

### R6's *sign* clause — one sentence is an over-claim (NOTE, not a blocker)

`status.md` says a positive joint velocity "drives the contact point along
`+d`, i.e. the same sign convention the driver's `wheel_linear_speeds` uses …
PR6 therefore needs no sign fix-up." The executed check
(`cross(r̂, [0,0,-r_w]) == d̂·r_w`) is about the **contact-patch material**, and
under the rolling constraint the *body* then moves the other way:

- back wheel, `r̂ = (-1,0,0)`, `d̂ = ẑ × r̂ = (0,-1,0)`;
- `Ω × (0,0,-0.05) = θ̇·(0,-0.05,0) = 0.05·θ̇·d̂` (the verified identity);
- no-slip ⇒ `v_axle = -0.05·θ̇·d̂`, so `θ̇ = +1` moves the body along **−d̂**;
- the driver (`wheel_angular_speeds = m·[vx,vy,ω] / wheel_radius`, fetched and
  read verbatim; `_degps_to_raw` applies no negation) means positive ⇒ body
  along **+d̂**.

So the two differ by a sign. This is **not a bug in the model** — upstream
LeKiwi's URDF carries exactly the same relation (see above), and whether a
physical Feetech motor's positive direction matches the URDF axis is a
calibration fact, not a geometric one. I therefore implemented R6 unchanged and
wrote the *precise* statement into D29 rather than the over-claim, since D29 is
append-only and PR6 will read it as fact. Flagged for the manager below.

## Surprises, and things future PRs should know

1. **A `--` inside an XML comment is invalid XML, and in `package.xml` it fails
   *silently*.** My first draft of both `base.xacro` and `package.xml` had one.
   In `base.xacro` xacro says so loudly. In `package.xml` the effect is much
   worse: colcon logs
   `Failed to parse potential ROS package manifest … not well-formed (invalid
   token): line 22, column 29` **at DEBUG level only**, silently reclassifies
   the package from `ament_python` to plain `python`, drops the
   `ament_prefix_path` environment hook, and **still reports the build as
   successful**. The whole gate then fails with `PackageNotFoundError:
   package 'robot_description' not found`, which points at the wrong thing.
   Reproduced deterministically on a scratch copy of `origin/main` (alternating
   the original and my `package.xml`, clean build each time):
   `orig: ament_prefix_path.* pythonpath.*` / `mine: pythonpath.*`.
   Worth a follow-up: nothing in the workspace validates `package.xml` as XML.
2. **`--ros-args -p robot_description:=<xml>` does not work for a real model.**
   Phase 2 verified it with a tiny one-line URDF; with the multi-line expansion
   rcl aborts before the node sees anything:
   `Couldn't parse parameter override rule: '-p robot_description:=<?xml …`
   (rc 250). An override rule is parsed as a single-line YAML scalar. The test
   writes a **params file** instead (a block scalar, hand-rolled so no `yaml`
   dependency is needed) — which is also what a real bringup launch passes.
3. **`ros2 run` re-spawns the node, so `terminate()` on the wrapper leaks it —
   and the leak *deadlocks the test*.** First green-looking version hung
   `colcon test` indefinitely. Diagnosis by `ps`: the wrapper died, the real
   `robot_state_publisher` (`.pixi/envs/default/lib/robot_state_publisher/…`)
   was reparented to init and kept the inherited stdout pipe open, so the
   reader thread stayed blocked inside `read()` holding the buffered-reader
   lock, and `process.stdout.close()` blocked forever waiting for that lock.
   Killing the orphan by hand released pytest immediately, confirming it. Fixed
   with `start_new_session=True` + `os.killpg` (SIGTERM then SIGKILL) on the
   captured process group. Anyone writing the PR8 bringup smoke test will hit
   the same thing.

## Verification

### Sabotage checks (all on a scratch copy at `/tmp/i65-sab`, never in place)

Setup: `git archive HEAD | tar -x -C /tmp/i65-sab`, symlink the worktree's
`.pixi`, then per variant
`colcon build --symlink-install --packages-select robot_description` +
`colcon test --packages-select robot_description` +
`colcon test-result --verbose`, run inside `pixi run`. Scratch copies deleted
afterwards; `git status` clean.

**(a) mount angles no longer 120° apart** — `wheel_angle_right` 300 → 290:

```
Summary: 15 tests, 0 errors, 1 failure, 0 skipped
- test_wheel_mounts_are_120_degrees_apart
    AssertionError: wheel mount angles are not evenly spaced: angles
    [60.0, 180.0, 290.0] give gaps [120.0, 110.0, 130.0], expected three of
    120 degrees
```

Exactly one failure, the intended one. (Note this is invisible to every PR1
assertion: `check_urdf`, the link set and the asset resolution all stay green.)

**(b) a model `robot_state_publisher` rejects** — the footprint joint's
`<child link="base_footprint"/>` → `base_ghost`:

```
Summary: 15 tests, 0 errors, 3 failures, 0 skipped
- test_model_loads_in_robot_state_publisher
    Failed: robot_state_publisher exited (rc 250) instead of initializing,
    i.e. it rejected the description:
    Error:   Failed to build tree: child link [base_ghost] of joint
             [base_footprint_joint] not found
    Failed to parse robot description using: urdf_xml_parser/URDFXMLParser
    terminate called after throwing an instance of 'std::runtime_error'
      what():  Unable to initialize urdf::model from robot description
    [ros2run]: Aborted
```

Legible, and **fast**: the whole `build + test + test-result` cycle took
`real 0m4.863s` — it neither hung nor passed. (`check_urdf` and the footprint
test fail too, as expected for this particular break.)

Two more, since the two most subtle clauses deserved their own probe:

**(c) spin axis flipped** — `rpy="0 ${pi/2} …"` → `0 ${-pi/2} …`:

```
Summary: 15 tests, 0 errors, 1 failure, 0 skipped
- test_wheel_mounts_are_120_degrees_apart
    AssertionError: base_back_wheel's spin axis, rotated into base_link
    coordinates, is [1.0, -0.0, 0.0]; it must be the outward radial direction
    [-1.0, 0.0, 0.0] at its own mount angle (180.0000 deg) …
```

Note that `check_urdf`, `robot_state_publisher` and the 120°-spacing clause all
stay green here — the axis clause is doing work nothing else does.

**(d) zero wheel mass** — `<mass value="${wheel_mass}"/>` → `0.0`:

```
Summary: 15 tests, 0 errors, 1 failure, 0 skipped
- test_moving_links_have_inertia
    AssertionError: link(s) that move need a real inertial block:
    ['base_left_wheel_link: mass 0.0', 'base_back_wheel_link: mass 0.0',
     'base_right_wheel_link: mass 0.0']
```

### Full suite

`pixi run build` then `pixi run test`: **green** across the whole workspace.
`scripts/test_baseline.json`'s `robot_description` entry ratcheted **7 → 12**
by the run itself (D28), and **no other package's number changed** — verified
on the committed diff. No `ALLOW_TEST_DECREASE` was used and no test was
removed or skipped.

Also checked after every run: no leaked `robot_state_publisher` process
(`ps -ef | grep robot_state_publisher` → 0).

## Red-team round 1 — the three BLOCKs, fixed

All three accepted; none escalated. Commits: `9b6fe96` (B3 model), `a7cf283`
(B1/B2/B3 gates + N2/N3/N5/N6 + doc corrections).

### B1 — the layout gate certified self-consistency, not correctness

The red-team was right and the finding generalises past the instance, which is
why it is now in D29 rather than only here. Every clause of
`test_wheel_mounts_are_120_degrees_apart` compared measured quantities *to each
other* — equal radii, 120° gaps, each axis radial at its own measured angle —
and all three are invariant under rotating or permuting the mount set. So a
left/right swap, a cyclic 120° shift and a global 40° rotation were all fully
green, while the model disagreed with the driver about which motor sits where.

Fix (the red-team's primary, not the minimum): a new
`test_wheel_mounts_match_the_lerobot_driver_matrix` rebuilds the driver's
body→wheel matrix from the parsed model — row *i* as `(-sin φ, cos φ, radius)`
from the measured mount — and compares it row-for-row against the driver's own
`(cos a, sin a, base_radius)` for `a = radians([240, 0, 120] - 90)`. The driver
rows are a **dict keyed by joint name**, not an ordered list: the motor-key →
row pairing *is* the fact under test, and a list would silently re-map if
anyone reordered it. `math` only, no numpy. Also added, per the flag:
`joint.child == joint.name + '_link'` for each wheel, in the joint-identity
test, rather than trusting the macro to make it true.

The relational test is kept — it gives the more legible message when the layout
breaks symmetrically — but its docstring now states its own scope: it says
*how* the layout broke, and the matrix test is what pins *where* each wheel is.
The module docstring now names the relational/absolute distinction as the thing
to ask about any future geometric assertion.

### B2 — the issue's first acceptance criterion had no assertion

New `test_solid_links_have_visual_and_collision_geometry`: every link not in
`MASSLESS_FRAME_LINKS` has at least one `<visual>` and one `<collision>`; the
chassis's collision is a non-degenerate cylinder; and its *visual* cylinder has
the same radius and length, so what a reviewer sees is what the planner hits.

### B3 — the chassis was buried in the wheels

`chassis_z_offset` 0.03 → **0.085** (= `wheel_radius + chassis_height/2` = 0.08,
plus 5 mm for the wheel mounts rather than sitting exactly on the float edge).
Per the ruling the *relationship* is now gated, in the same test as B2:
`chassis_z - chassis_height/2 >= wheel_radius`, all three read off the parsed
model, so retuning any of them keeps the constraint enforced.

I did **not** add an assertion for N4's "wheels do not stick out past the body"
(the ruling left it to my judgement). It is an aesthetic preference, not a
correctness constraint — a base with wheels wider than its puck is ugly, not
broken — and gating it would forbid a legitimate future design. The comment now
names the real criterion (`hypot(base_radius + wheel_width/2, wheel_radius)` =
0.1487, not 0.140) and says explicitly that it is deliberately not asserted,
which is the part that was actually wrong.

### Doc corrections that had to land with the fix

D29's fifth clause claimed asserting relationships "makes mis-mounting it a
test failure" — false as shipped, and the #55 trap in `decisions.md` itself.
Since D29 is this PR's own entry and unmerged, it is corrected in place: the
gate clause now lists what actually ships, and a new clause records the
permutation hole, the three repros, and the general lesson (*a gate built only
from internal consistency checks certifies self-consistency, and a wrong model
can be perfectly self-consistent*), which is the part PR3–PR7 need. The two
false docstrings in `test_description.py` are corrected likewise, and
`README.md` / `spec.md`'s gate summaries updated.

NOTEs folded in: **N2** (two "PR2 lands the first `.stl`" sentences),
**N3** (D29's heading now reads "… (amends D27's PR2 mesh bullet)"),
**N5** (D29's first clause now records that `base_radius = 0.125` is the
*driver's* number and that upstream's own CAD puts its wheels at 0.0992 /
0.1192 / 0.1000 m — not even a common circle — with why following the driver is
still right), **N6** (`process.stdout.close()` guarded behind
`if not reader.is_alive()`). **N1** is the manager's file to correct; **N7** is
informational.

### Round-2 sabotage verification

Same protocol as round 1: `git archive HEAD | tar -x -C /tmp/i65-fix`, `.pixi`
symlinked, one perturbation at a time restored from a pristine copy of
`base.xacro`, build + test + `test-result` per variant, inside `pixi run`.
Scratch copies deleted; `git status --porcelain` empty afterwards. Every one of
these was **green before this round**.

**B1 repro 1 — swap `base_left_wheel` ↔ `base_right_wheel` (60 ↔ 300):**

```
build/robot_description/pytest.xml: 17 tests, 0 errors, 1 failure, 0 skipped
- test_wheel_mounts_match_the_lerobot_driver_matrix
  AssertionError: the model's wheel layout does not reproduce the LeRobot
  driver's kinematic matrix ... Mismatches: ['base_left_wheel (mounted at
  300.0000 deg, radius 0.1250): model row [0.866025, 0.5, 0.125] != driver row
  [-0.866025, 0.5, 0.125]', 'base_right_wheel (mounted at 60.0000 deg, radius
  0.1250): model row [-0.866025, 0.5, 0.125] != driver row [0.866025, 0.5,
  0.125]']
```

**B1 repro 2 — cyclic +120° (left=180, back=300, right=60):** 1 failure, same
test, all three rows named (`base_back_wheel … [0.866025, 0.5, 0.125] !=
[0.0, -1.0, 0.125]`, etc.).

**B1 repro 3 — global rotation of all three mounts by +40°:** 1 failure, same
test, all three rows named (`base_left_wheel (mounted at 100.0000 deg) …
[-0.984808, -0.173648, 0.125] != [-0.866025, 0.5, 0.125]`).

**B2 repro 1 — delete the chassis's `<visual>` *and* `<collision>`:**

```
- test_solid_links_have_visual_and_collision_geometry
  AssertionError: link(s) that are bodies rather than frames must carry both
  visual and collision geometry: ['base_chassis_link: no <visual>',
  'base_chassis_link: no <collision>']
```

**B2 repro 2 — delete the wheel macro's `<visual>`:** 1 failure, same test,
`['base_left_wheel_link: no <visual>', 'base_back_wheel_link: no <visual>',
'base_right_wheel_link: no <visual>']`.

**B3 repro — `chassis_z_offset` back to the shipped-before 0.03:**

```
- test_solid_links_have_visual_and_collision_geometry
  AssertionError: the chassis intersects the wheels: its underside sits at
  z = 0.0000 (centre 0.0300 minus half of 0.0600) while the wheels reach
  z = 0.0500. They are siblings under base_link, so this contact is not
  filtered anywhere; raise chassis_z_offset to at least
  wheel_radius + chassis_height/2 = 0.0800.
```

**B3 boundary — `chassis_z_offset = 0.079`, 1 mm under the minimum:** fails
with `underside 0.0490 … wheels reach 0.0500`, i.e. the assert bites at the
millimetre, not just at the gross violation.

**Cross-wiring — the macro parameterised so `base_left_wheel` drives
`base_right_wheel_link` and vice versa** (link set, joint set and every mount
angle left intact):

```
- test_wheel_joints_are_exactly_three_continuous
  AssertionError: each wheel joint must drive the link named after it;
  cross-wiring two of them leaves the joint set, the link set and the mount
  geometry all intact while moving the wrong wheel:
  {'base_left_wheel': 'base_right_wheel_link',
   'base_right_wheel': 'base_left_wheel_link'}
```

Note that the driver-matrix test correctly stays green here — the *mounts* are
right, only the wiring is wrong — which is why this needed its own assertion.

### Full suite after the fix round

`pixi run build` + `pixi run test`: **756 tests, 0 errors, 0 failures, 0
skipped**, all ten packages `ok`. Baseline ratcheted `robot_description`
**12 → 14** and nothing else (`git diff scripts/test_baseline.json` is a single
line). Tree clean.

## For the manager

Two items to surface; neither is a blocker and neither was acted on beyond what
is written above.

1. **R6's "PR6 therefore needs no sign fix-up" is an over-claim** (§ above).
   **RESOLVED** — the manager re-derived it independently, agreed, and ruled
   D29's wording stands as written. It then caught the inverse of the #55
   failure mode in my work: D29 was right while the **source comment still
   carried the over-claim**, and `base.xacro` is the file a PR6 author actually
   reads. Fixed in a follow-up commit, applying the "a claim wrong in N places
   is probably wrong in an N+1th" heuristic to my own text:
   - `urdf/base.xacro`, the `omni_wheel` macro comment — the tail clause "the
     same sign the driver's `wheel_linear_speeds` uses" is replaced by the
     precise three-sentence version (contact patch along `+d̂` ⇒ body along
     `−d̂`; driver's positive ⇒ body along `+d̂`; motor direction is a
     calibration fact to confirm in sim), pointing at D29 rather than
     re-deriving.
   - `urdf/base.xacro`, the attribution header — "Mount convention … **and axis
     sign**: … checked numerically against its kinematic matrix" was the same
     conflation in miniature (the matrix check verifies the *mount mapping*,
     not the axis sign). Split into the convention (verified two ways) and the
     sign (spelled out on the macro).
   - `test_wheel_mounts_are_120_degrees_apart`'s axis assertion message — the
     clause "which is what makes a positive joint velocity roll the wheel the
     way the driver expects" asserted the same false fact *in a failure
     message*, which is worse than saying nothing. Replaced with what the
     assertion actually checks: the wheel-link frame convention shared with
     upstream, and the composition PR6/PR7 read the kinematics off (D29).

   No geometry, property value or D29 text changed; the suite re-ran green
   (754 tests, 0 failures) with the baseline unchanged at 12 (`+0`), confirming
   the assert-message edit still lints and still fires.
2. **Follow-up candidates** (issue comments are yours to post, per CLAUDE.md):
   - Nothing in the workspace validates `package.xml` as XML, and colcon's
     failure mode for an invalid one is a silent build-type downgrade with a
     green build (§Surprises 1). A one-line guard in
     `scripts/check_test_integrity.py` or a workspace-tooling test would have
     turned a 40-minute diagnosis into a one-line error.
   - `src/robot_brain/robot_brain/openclaw/AGENTS.md:3` still tells the brain
     the robot has "a four-wheel base" — stale against D26/D29, but outside
     this PR's owned paths, so untouched (R14).
