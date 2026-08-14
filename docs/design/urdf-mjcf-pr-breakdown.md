# Roadmap #5 — URDF/MJCF robot body: PR breakdown

**Status:** in progress. **PR1 is DONE** — merged as PR #62 (closes #61), ratified
as **D27**. **PR2 is DONE** — closes #65, ratified as **D29**. **PR3 is DONE** —
closes #73, ratified as **D31**. PR3.5–PR7 are not yet filed as issues.
**Decided:** URDF-as-source (Jaime, 2026-08-12). URDF/Xacro is canonical for
kinematics/geometry; MJCF carries a thin sim-only physics layer on top;
`RobotModel`'s constants are *read from* the URDF, not hardcoded.
**Hardware lineage:** the base/column/arm/gripper/camera picks and the crib
targets (XLeRobot, Nori Bot, `so101_ros2`) live in
[`spec.md`](spec.md) and are governed by [`decisions.md`](decisions.md) **D26** —
this doc does not restate them. What it owns is the **roadmap**: how that body
gets built, in what order, and what proves each step.

## Ground truth this must preserve
`RobotModel` lives in `src/robot_backends/robot_backends/mock_world.py`. The 7
numbers the URDF must eventually *own* (current literal defaults):

| field | value | becomes in URDF |
|---|---|---|
| `shoulder_offset_y` | 0.18 m | arm-mount lateral origin (±) off `column_top` |
| `shoulder_offset_z` | 0.50 m | arm-mount z origin above `column_top` |
| `reach_radius` | 0.85 m | arm kinematic reach (joint limits / link lengths) |
| `home_gripper_offset` | (0.35, 0.0, -0.05) | home pose of gripper vs shoulder |
| `min_column_height` | 0.00 m | prismatic `column_lift` lower limit |
| `max_column_height` | 1.20 m | prismatic `column_lift` upper limit |

## Guiding constraints for the split
- **Each PR: builds green, xacro-expands, passes new pytest asserts, mergeable
  alone.** No PR changes `robot_backends` runtime behavior until PR6, which is
  guarded by a golden-value test so the existing suite stays green.
- **Every PR extends one CI harness** (the expand+parse+assert test from PR1) —
  gives the red-team a concrete sabotage target each round.
- **Sequential, single slot.** All geometry PRs edit the same package's
  top-level xacro include list + install rules, so parallel worktrees would
  conflict. Keep #5 in one slot; leave the other free for unrelated ops/feature
  work.

## The PRs

### PR1 — Description package + CI expand/parse gate  *(foundation)* — **DONE (PR #62, D27)**
- Build deps wired via pixi + `package.xml`: `xacro`, `urdfdom`
  (`ros-jazzy-urdfdom` pinned explicitly, not left to the `ros-jazzy-desktop`
  closure), `urdf_parser_py`; `mujoco` later.
- **Build type decided: keep `ament_python`** (D27) — *not* ament_cmake. The
  install layout `share/robot_description/{urdf,meshes}` is served just as well
  by `data_files` in `setup.py`, while `ament_cmake` would cost a per-test
  `ament_add_pytest_test()` line (a forgotten line = a silently skipped test),
  would confuse the test ratchet's linter-name matching, and would make this the
  workspace's only non-`ament_python` package. Install globs `urdf/*` /
  `meshes/*` — never a hand-maintained list.
- Ships a top-level `robot.urdf.xacro` that includes (empty) `base.xacro`,
  `column.xacro`, `arm.xacro` and expands to a single `base_link`.
- **Test:** the gate resolves the description through the ament index (**the
  installed copy**, no source-tree fallback); xacro expands; `check_urdf` parses;
  `urdf_parser_py` re-parses and the link set is exactly `{base_link}`; every
  file the description names (`<mesh>`/`<texture>`) resolves in the share tree.
  This test file is the harness PRs 2–7 grow.
- Unblocks: everything.

### PR2 — Mobile base (3-omniwheel holonomic, LeKiwi crib) — **DONE (closes #65, D29)**
- `base_chassis_link` collision/visual + `base_footprint`, both fixed children
  of `base_link` (which keeps no geometry — D27 declares it at the top level);
  **3 omniwheel links** at 60°/180°/300° on `continuous` joints named for the
  LeRobot driver's motor keys. Every dimension is an xacro `<property>`, with
  the two sourced ones marked apart from the estimates (D29).
- **Test:** wheel joint names/types/count (=3), each driving its own link; the
  120° mount spacing and the outward-radial spin axis *after* rpy composition;
  **the LeRobot driver's body→wheel matrix rebuilt from the model and compared
  row-for-row to the driver's own constant, plus its `wheel_radius`** — the
  absolute check, without which a left/right swap or a retuned wheel is green
  (D29); `base_footprint` at minus the wheel radius; visual + collision
  geometry on every body link and a chassis that clears the wheels; inertia on
  every non-frame link; still expands + parses; loadable by
  `robot_state_publisher`.
- **Amended by D29: PR2 imports no meshes.** The base is primitives (the
  upstream omniwheel STL is 15 MB × 3, the cheap LeKiwi mesh is the wrong body
  per D26, and convex primitives are the better collision geometry anyway), so
  the flat `glob()` install stays as D27 left it and the `os.walk` rewrite
  moves to the first PR that actually lands meshes — see PR4.
- Depends on PR1.

### PR3 — Extendable column  *(linear-rail STS3215 lift, Nori-style crib (D26))* — **DONE (closes #73, D31)**
- Prismatic joint `column_lift` from `base_link` to a `column_top` mount frame;
  limits `lower=min_column_height`, `upper=max_column_height` (0.00–1.20).
- **Test:** `column_lift` is prismatic; limit lower/upper equal the column
  bounds. First place the URDF *owns* a `RobotModel` number.
- **Amended by D31 in two places, both mechanical.** The column is **two** solid
  links, not one: a static `column_rail_link` mast (a lift with 1.2 m of travel
  has a rail that is there at every joint value; modelling only the carriage
  leaves a block floating over nothing, invisible to Nav2/MoveIt and absent from
  PR7's MJCF), plus `column_top` as the carriage itself — whose **link frame
  origin is the arm/head mount datum**, with the body offset below it, so PR3.5
  and PR4 mount against a surface rather than into a solid. And `column_lift`'s
  parent is **`column_rail_link`, not `base_link`**: kinematically identical
  while the rail joint is fixed, but as siblings the carriage and the mast are
  two permanently-touching solids whose contact nothing filters — D29's
  chassis-vs-wheels bug one subassembly up. The mast's mount height is
  *computed* from `base.xacro`'s `chassis_z_offset`/`chassis_height`, which
  makes `column.xacro` include-order dependent (loudly, at expansion).
- **Recorded contradiction:** D26 paraphrases the crib as a "~600 mm linear
  rail" while `RobotModel` demands 1.20 m of *travel*; a single-stage carriage
  cannot do both, so the travel bound wins and the mast is authored longer than
  the travel. The mechanism is left to the (unread) Nori paper and to PR6. So is
  the question of whether `RobotModel.column_height` means travel or an absolute
  height — the URDF now commits to **travel** (D31).
- Depends on PR2 (mounts on base).

### PR3.5 — Head camera link + optical frame  *(decided D26)*
- Mount a `head_camera_link` on `column_top` (matching XLeRobot's dual-fit head
  mount — takes a webcam *or* a RealSense/Orbbec) plus a REP-103/REP-105 optical
  frame `head_camera_optical_frame` (z-forward, x-right), as xacro `<property>`
  mount transform so the pose is tunable.
- **Geometry + frames only** here — no sensor physics. Mock + MuJoCo run on
  ground-truth poses (D21), so nothing needs a real camera to build/test; this
  reserves the correct pose so camera→base transforms project detections to
  metric poses when the real RGB-D detector path is built.
- **Test:** `head_camera_link` + optical frame present; optical-frame origin
  matches the mount property; still expands + parses.
- Depends on PR3 (mounts on `column_top`); independent of the arms, so it can
  land before or after PR4/PR5.

### PR4 — SO-101 arm macro (single), instantiated left + right
- **Likely the first mesh-bearing PR, so it inherits the `os.walk` install
  rewrite** D27 recorded and D29 deferred out of PR2: the flat `glob()` in
  `setup.py` cannot copy a nested `meshes/<subdir>/`, and the rewrite is to be
  written against the arm's actual STL layout rather than a guess at it.
- Crib arm links/joints from `so101_ros2`; wrap in a xacro macro parameterized
  by `prefix`/`side` + mount transform. Instantiate off `column_top` at
  `y=±shoulder_offset_y (0.18)`, `z=shoulder_offset_z (0.50)`.
- **Test:** two arms present; joint names uniquely prefixed (no collision);
  each shoulder frame origin matches the offsets; reach consistent with 0.85.
- Depends on PR3.

### PR5 — Grippers (stock SO-101 parallel-jaw), one per arm
- Stock SO-101 parallel-jaw on each arm tip (the arm's 6th DOF, per
  [`spec.md`](spec.md)/D26); `home_gripper_offset` reflected in the home/zero
  pose.
- **Author the fingertip as a swappable xacro link/macro** so a compliant
  fin-ray fingertip is a later geometry swap, not a re-model. The joint/actuator
  + the `grasped` aperture model (D19) are unchanged by any fingertip choice.
  **Suction is out of scope** (D26).
- **Test:** gripper joints present per side; mimic wiring; home offset check;
  fingertip link is macro-parameterized (rigid default instantiable).
- Depends on PR4. *(Can fold into PR4 if it stays small; split keeps reviews
  tight.)*

### PR6 — `RobotModel` reads from the URDF  *(the D23 payoff / "source of truth")*
- Add a loader (in `robot_description`, or a small `robot_model_from_urdf`) that
  parses the expanded URDF → the 7 constants. Refactor `RobotModel`'s default to
  load from the shipped URDF; keep the dataclass for explicit overrides.
- **Golden test:** parsed values `==` today's literals (0.18 / 0.50 / 0.85 /
  0.00 / 1.20 / home offset), so the whole `robot_backends` suite stays green.
  This is where Mock and (future) MuJoCo become incapable of disagreeing.
- Depends on PR4 (needs shoulder frames + column bounds present). Could land a
  column-only slice after PR3 and extend after PR4 if we want it earlier.

### PR7 — MJCF derivation + physics overlay
- URDF→MJCF via MuJoCo's compiler; hand-authored MJCF overlay for the sim-only
  bits (contacts, actuator gains, sensor sites) that don't come from URDF.
- **Includes the head RGB-D sensor** (D26): model the camera at
  `head_camera_optical_frame` as an active-IR-stereo RGB-D sensor so the sim can
  render RGB-D from the correct pose when the real detector path is built.
- **Test:** MuJoCo loads the model; assert `nbody`/`nq`/`nu` match the URDF; one
  `mj_step` smoke with no NaN.
- Depends on PR5 (needs full geometry).

### (PR8 — bringup: RSP launch + RViz/Foxglove view + `mujoco_ros2_control` spawn)
- Boundary case: this is arguably the *start of roadmap #4* (Mock→MuJoCo), not
  #5. Flag for a separate issue rather than bloating #5.

## Merge order & dependency graph
```
PR1 ─► PR2 ─► PR3 ─► PR4 ─► PR5 ─► PR7
      (done) (done)   │       └─► PR6 (after PR4; optional column-only slice after PR3)
               └─► PR3.5 head camera link (after PR3; arm-independent)
```
Strictly sequential; no safe intra-package parallel pair. One dispatch slot.

## Testing without hardware (how each PR proves itself)
- `xacro` expansion succeeds (catches include/macro errors).
- `check_urdf` / `urdf_parser_py` parses the expanded URDF (catches malformed
  trees) — against the **installed** copy, so install wiring is gated too.
- pytest asserts on the parsed model: link/joint names, joint types, limits,
  frame origins — cheap, deterministic, no sim.
- PR7 only: MuJoCo `mj_step` smoke.

## Open risks
- **URDF→MJCF is never perfectly lossless.** Keep the MJCF overlay minimal at
  first; treat sim-physics tuning as iterative, not a PR7 blocker.
- **Crib fidelity (base + column).** The base is a direct crib of the
  LeKiwi/XLeRobot holonomic 3-omniwheel URDF. The column cribs Nori Bot's
  linear-rail STS3215 lift — **unverified until we read arXiv:2605.16537**
  (D26); if it doesn't hold up, the column is still just one prismatic joint we
  can author from the STS3215 spec. If any cribbed description is welded to its
  own `ros2_control` hardware iface, take the link/joint geometry only and
  re-wire transmissions to our conventions.
- ~~**ament_python vs ament_cmake** — pick once, up front.~~ **Settled in PR1:
  `ament_python` (D27).** Revisit only if PR7's MJCF work needs a genuine build
  step.
