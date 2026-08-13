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

## For the manager

Two items to surface; neither is a blocker and neither was acted on beyond what
is written above.

1. **R6's "PR6 therefore needs no sign fix-up" is an over-claim** (§ above).
   The model is implemented exactly as ruled and matches upstream LeKiwi; D29
   records the precise statement (positive joint velocity ⇒ contact patch along
   `+d̂` ⇒ body along `−d̂`, versus the driver's `+d̂`) and says PR6/PR7 must
   confirm the physical motor sign in sim rather than assume it. If you would
   rather D29 restated `status.md` verbatim, say so and I will change it — but
   I did not want an append-only entry to hand PR6 a sign it cannot rely on.
2. **Follow-up candidates** (issue comments are yours to post, per CLAUDE.md):
   - Nothing in the workspace validates `package.xml` as XML, and colcon's
     failure mode for an invalid one is a silent build-type downgrade with a
     green build (§Surprises 1). A one-line guard in
     `scripts/check_test_integrity.py` or a workspace-tooling test would have
     turned a 40-minute diagnosis into a one-line error.
   - `src/robot_brain/robot_brain/openclaw/AGENTS.md:3` still tells the brain
     the robot has "a four-wheel base" — stale against D26/D29, but outside
     this PR's owned paths, so untouched (R14).
