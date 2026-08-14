# Status — #65 PR2: mobile base URDF (3-omniwheel holonomic, LeKiwi crib)

Slug: `i65-pr2-mobile-base`
Branch: `feat/i65-pr2-mobile-base-urdf-3-omniwheel-holonom`
Worktree base: `origin/main` @ 3d07063

| Phase | State |
| --- | --- |
| 0. Sync | done — worktree at origin/main head 3d07063 |
| 1. Brief read (issue #65) | done |
| 2. Provision + probe deps | done |
| 3. Context explorer | done — `context.md`, 5 open questions |
| 4. Manager rulings | done — R1–R14 below |
| 5. Implementer | done — see `implementation.md` |
| 6. Red-team | r1 `red_team.md` (3 BLOCK, 7 NOTE); r1-scoped `red_team_fix.md` (3 BLOCK, 5 NOTE) |
| 7. Fix rounds (max 2) | done — r1 (B1–B3 + N2–N6), r2 (BF1–BF3 + NF1–NF5); N1 fixed by the manager |
| 8. Test-runner | done — **756 tests, 0 errors, 0 failures, 0 skipped**, `vs-base +0` on all 10 packages |
| 9. PR + ready | done — **[PR #66](https://github.com/jaimec00/sisyphus/pull/66)**, green against current main (3d07063) |

Blockers: none. **Ready for Sisyphus.**

## Phase 9 — ready

- **PR:** https://github.com/jaimec00/sisyphus/pull/66 — squash-merge, closes #65.
- **Test gate:** the laptop `test-runner` ran the full `pixi run test` inside the
  loop: 756 tests, 0 failures. Ratchet moved `robot_description` 7 → 14, a
  single-line diff; no other package's floor changed.
- **Rebase:** `origin/main` had not moved from 3d07063, so "green" is green
  against current main with no rebase needed.
- **Durable-decision re-read (step 9):** D29 re-read against the diff as it
  landed, not as planned. Caught one drift — the lead sentence said "Five
  clauses" after the fix round added a sixth (`d9b2046`). Every other claim in
  the lead re-checked and left alone.
- **Comments posted:** follow-ups on issue #65 (the four-wheel string in the
  live OpenClaw prompt; nothing validates `package.xml` as XML); retro on
  PR #66.
- **`docs/features/i65-pr2-mobile-base/` is deliberately NOT deleted** — it
  stays for review, and Sisyphus removes it at merge. The docs-clean CI check
  therefore reads as failing until then, which is expected.

## Phase 2 — provisioning + execute-verified crib probe

`pixi add ros-jazzy-robot-state-publisher` → added
`ros-jazzy-robot-state-publisher >=3.3.3,<4` to `pixi.toml`. `pixi.lock` is
**unchanged**: the package was already in the solve via the `ros-jazzy-desktop`
closure at a matching version, so pinning it changes the manifest only. That
pin is deliberate and follows D27's precedent (`check_urdf` reached the env only
through the unpinned desktop closure until `ros-jazzy-urdfdom` was pinned) — the
acceptance criterion "loadable by `robot_state_publisher`" must not depend on a
transitive closure. `pixi install` re-verified clean afterwards.

Sequencing note (per the loop's #37 lesson): provisioning completed **before**
the context-explorer was dispatched, and the worktree is not mutated while it
reads. The dirty `pixi.toml` it may observe is this step's output, expected.

### Execute-verified: `robot_state_publisher` boot behaviour

Not recalled — run in this worktree's pixi env.

- The executable is **not on `PATH`**; it lives at
  `.pixi/envs/default/lib/robot_state_publisher/robot_state_publisher` and is
  launched as `ros2 run robot_state_publisher robot_state_publisher`.
- **Valid URDF** (`base_link` + one `continuous` joint + child link), passed as
  `--ros-args -p robot_description:="<xml>"`: logs
  `[INFO] ... [robot_state_publisher]: Robot initialized` within ~1 s and then
  **stays alive** (it is a long-running node — a test must terminate it, and
  `timeout` returns rc 124, which is the *success* path, not a failure).
- **Invalid URDF** (joint naming a non-existent child link): prints
  `Failed to parse robot description using: urdf_xml_parser/URDFXMLParser`,
  then `terminate called after throwing an instance of 'std::runtime_error'` /
  `Unable to initialize urdf::model from robot description`, and **exits
  rc 250** (`[ros2run]: Aborted`) in well under a second.

So the gate shape is: start the node, read its output until either the
`Robot initialized` marker appears (pass — then terminate it) or the process
exits (fail — its rc and output are the message). This is a *real* extra gate
beyond `check_urdf`: `robot_state_publisher` builds a KDL tree, which rejects
models `urdf_parser_py` accepts.

Headless boot needed no display and no ROS graph beyond the default DDS.

### Execute-verified: the LeKiwi / XLeRobot crib (what is actually there)

Sources read directly, not recalled:

- **`Vector-Wangel/XLeRobot`** — `simulation/Maniskill/assets/xlerobot/xlerobot.urdf`
  has **no wheel joints at all**. Its base is driven by a *virtual planar joint*
  (`root_x_axis_joint`/`root_y_axis_joint` prismatic + `root_z_rotation_joint`
  continuous) and `base_link` carries IKEA-Råskog-cart meshes
  (`raskogbody.stl`, `raskogwheel1.stl`, `raskogwheel2.stl`) as *fixed visuals*
  plus box collisions. **XLeRobot is not the 3-omniwheel crib** — for wheels it
  has nothing to crib.
- **`SIGRobotics-UIUC/LeKiwi`** (Apache-2.0) — `URDF/LeKiwi.urdf` is the real
  crib, and `URDF/JOINT_NAMES.md` records that its three actuated base joints
  are deliberately named after the LeRobot driver's motors:
  **`base_left_wheel`, `base_back_wheel`, `base_right_wheel`**, all
  `type="continuous"`. But the file itself is a raw CAD export: each wheel hangs
  off a `drive_motor_mount → ST3215_Servo_Motor → omni_wheel_mount` chain with
  16-digit float origins and no parameters at all. It is a source of *names,
  types and numbers*, not a file to copy.
- **Mesh sizes** (why R3 below rules the way it does): the LeKiwi omniwheel STL
  `4-Omni-Directional-Wheel_Single_Body-v1.stl` is **15 MB**, and the URDF
  references three copies of it. The base plate is 461 KB.

### Execute-verified: the base kinematics, from the LeRobot driver

`huggingface/lerobot`, `src/lerobot/robots/lekiwi/lekiwi.py`
(`_body_to_wheel_raw` / `_wheel_raw_to_body`), read verbatim:

```python
wheel_radius: float = 0.05
base_radius:  float = 0.125
angles = np.radians(np.array([240, 0, 120]) - 90)      # order: left, back, right
m = np.array([[np.cos(a), np.sin(a), base_radius] for a in angles])
```

That `angles` array is the wheels' **rolling directions**, not their mount
positions — a distinction that is easy to get backwards and that decides where
the three wheels physically sit. Derived and then **checked by execution**: with
mount positions `phi` in a REP-103 body frame (x forward, y left, z up) and
rolling direction `d = z × r = (-sin phi, cos phi)`, the matrix
`[[-sin phi, cos phi, base_radius]]` reproduces LeRobot's `m` exactly
(`np.allclose(...) is True`) for

| joint | mount angle `phi` | where it sits |
| --- | --- | --- |
| `base_left_wheel` | **60°** | front-left |
| `base_back_wheel` | **180°** | rear centre |
| `base_right_wheel` | **300°** (= −60°) | front-right |

So the driver's frame is *already* REP-103-consistent and the LeRobot names are
geometrically true — no 180° flip, despite `[240, 0, 120]` reading like one.

Axis sign, also checked by execution: with the joint `axis` set to the outward
radial `r = (cos phi, sin phi, 0)`, a **positive** joint velocity drives the
wheel's contact-patch *material* along `+d`
(`np.cross(r, [0,0,-wheel_radius]) == d*wheel_radius` for all three wheels).

> **Corrected during Phase 5** (implementer escalation, ratified by the manager
> after re-deriving it). This paragraph originally continued "…i.e. the same
> sign convention the driver's `wheel_linear_speeds` uses. PR6 therefore needs
> no sign fix-up." That conflates the contact patch with the body: under the
> no-slip constraint `v_axle = -theta_dot * wheel_radius * d`, so a positive
> joint velocity moves the **body** along `-d`, while the driver's positive
> wheel speed means the body along `+d`. Whether a physical Feetech motor's
> positive direction matches the URDF axis is a **calibration** fact, not a
> geometric one, and upstream LeKiwi carries the same relation — so the model
> is unchanged, but PR6/PR7 must confirm the sign in sim rather than assume it.
> The precise statement is what landed in **D29**; this note exists so the
> ephemeral doc does not contradict the durable one (the #55 lesson).

## Phase 4 — manager rulings (binding, but not assumed correct)

Rulings are **binding**. A downstream agent that believes one is wrong
**escalates to the manager in-process** — it must neither silently deviate nor
comply into a bug.

### R1 — `base_link` keeps no geometry; the base attaches `base_chassis_link` to it

The issue says "*`base_link` collision + visual geometry*". Taken literally that
is **impossible** without breaking D27: `base_link` is declared in
`urdf/robot.urdf.xacro` (`:26`), xacro does not merge two `<link>` elements of
the same name, and a duplicate link name is exactly the error D27 records
`check_urdf` rejecting (`link 'x' is not unique`). D27 also states the rule
outright — "`base_link` lives in the **top level**, not in `base.xacro` … the
base geometry attaches *to* it via a joint exactly as the column and arms do"
(`decisions.md:90`). `decisions.md` wins where docs disagree (CLAUDE.md).

So: `base.xacro` declares **`base_chassis_link`**, carrying the base's visual +
collision geometry, joined to `base_link` by a **fixed** joint
`base_chassis_joint`. `robot.urdf.xacro` is not edited at all.

### R2 — link and joint names

Joints — **exactly** these three, `type="continuous"`:
`base_left_wheel`, `base_back_wheel`, `base_right_wheel`.

They are not invented: `SIGRobotics-UIUC/LeKiwi`'s `URDF/JOINT_NAMES.md` records
that its URDF was deliberately renamed to match the LeRobot driver's motor keys
in `lerobot/robots/lekiwi/lekiwi.py` so "joint-state and command dictionaries
[are] directly comparable between the robot driver and the URDF". That is D26's
single-ecosystem rule applied to naming, and PR6 is the PR that will cash it in.
**Do not "improve" these names** (no `_joint` suffix, no `wheel_1/2/3`).

Links — one link per wheel, named by suffixing its joint:
`base_left_wheel_link`, `base_back_wheel_link`, `base_right_wheel_link`. The
macro derives both from a single `name` argument, so the pairing cannot drift.

**No intermediate mount links.** LeKiwi's raw URDF hangs each wheel off a
`drive_motor_mount → ST3215_Servo_Motor → omni_wheel_mount` chain; that is a CAD
export artifact, not kinematics. Copying it would add six fixed links to TF, to
`EXPECTED_LINKS`, and to PR7's MJCF for zero information.

Therefore:

```python
EXPECTED_LINKS = {
    'base_link', 'base_footprint', 'base_chassis_link',
    'base_left_wheel_link', 'base_back_wheel_link', 'base_right_wheel_link',
}
```

### R3 — **primitives only; no vendored meshes; the `os.walk` install rewrite is deferred**

This is the ruling most worth attacking, and it rules against the breakdown
doc's expectation that PR2 is where meshes and the `os.walk` install land.

The issue itself makes it conditional — "***if*** the base imports the first
real mesh set, this is where the `os.walk`/nested-mesh install path gets
written". I am ruling the condition **false** for PR2, on three grounds, all
from the Phase 2 probe:

1. **Size.** The LeKiwi omniwheel is a **15 MB** STL and the URDF references
   three of them. This repo has no git-lfs and no asset-size policy
   (`context.md` OQ2). Committing tens of MB of binary for a wheel that a
   cylinder models correctly is a bad permanent trade.
2. **It is the wrong body.** The only cheap LeKiwi mesh (461 KB
   `base_plate_layer1.stl`) is LeKiwi's *flat drive plate*. Per D26 our chassis
   is **XLeRobot's IKEA-Råskog-cart frame** over the LeKiwi drive — and
   XLeRobot's own URDF models that cart with **box collisions plus cart meshes,
   and has no wheel joints at all**. Vendoring LeKiwi's plate would assert
   geometry this robot does not have.
3. **Primitives are the *better* collision geometry** for a mobile base — convex,
   cheap, and what MoveIt/Nav2/MuJoCo want anyway. Visual fidelity is the only
   thing lost, and it is the one thing sim-first (D9/D16) does not need yet.

Consequence, and it must be **recorded, not silently dropped**: D27's flat-glob
limit stays open, and the `os.walk` rewrite moves to **the first PR that
actually lands meshes** (PR4's SO-101 arm STLs are the likely one). That is the
faithful reading of D27's own justification for deferring it out of PR1 —
written "against actual files rather than against a guess about their layout".
Writing it now against an empty `meshes/` would be exactly that guess. R12
records this in the decision log so PR4 inherits it.

`setup.py`'s `data_files` is therefore **unchanged** by this PR, and
`meshes/README.md` stays the only thing in `meshes/`.

### R4 — attribution: a comment, not a NOTICE file (OQ3, now mostly moot)

Under R3 no third-party *file* is copied, so MIT-vs-Apache-2.0 file vendoring
does not arise. What is cribbed is **facts** — two dimensions, three names, a
joint type, a mount convention. `base.xacro` must carry a header comment naming
each source (LeKiwi URDF + `JOINT_NAMES.md`, Apache-2.0; LeRobot
`lekiwi.py`; XLeRobot), its license, and **which specific constant came from
where**. No `NOTICE`/`THIRD_PARTY` file — inventing a repo-wide attribution
convention on this PR is out of scope, and R12's decision entry is the durable
record. Revisit if a later PR vendors actual files.

### R5 — `base_footprint` is a fixed **child** of `base_link` (OQ4)

`base_link` stays the URDF root (D27, non-negotiable here). `base_footprint` is
its child via a **fixed** joint at `xyz="0 0 -${wheel_radius}"` — i.e.
`base_link` sits at wheel-axle height and `base_footprint` is on the ground.

The REP-120-shaped alternative (footprint as root, `base_link` above it) is
rejected: it would move the root frame declaration out of the top level, which
D27 fixes, for a purely cosmetic gain.

**The consequence must be recorded, because it constrains a later PR.** TF links
have exactly one parent, and `robot_state_publisher` will publish
`base_link → base_footprint` from this URDF. So whatever publishes odometry
(PR6 / Nav2 bringup) **must publish `odom → base_link`, not
`odom → base_footprint`** — the latter would give `base_footprint` two parents
and break the TF tree at runtime. Goes in R12's decision entry.

### R6 — mount angles, axis convention, and the numbers

All execute-verified in Phase 2 — see `status.md` above; **do not re-derive**,
and do not "correct" the angles to `[240, 0, 120]` (that array in the LeRobot
driver is *rolling directions*, not mount positions; the two differ by 90°+180°
and the verification script proves the mapping below reproduces the driver's
kinematic matrix exactly).

In the REP-103 body frame (x forward, y left, z up), measured about +z from +x:

| joint | mount angle | where |
| --- | --- | --- |
| `base_left_wheel` | 60° | front-left |
| `base_back_wheel` | 180° | rear centre |
| `base_right_wheel` | 300° | front-right |

Wheel *i* sits at `xyz = (base_radius·cos φ, base_radius·sin φ, 0)` relative to
`base_link`, with joint `rpy = (0, ${pi/2}, ${radians(φ)})` and
`<axis xyz="0 0 1"/>`.

That rpy makes the child link's **+z the wheel's spin axis**, pointing radially
outward — the conventional wheel-link frame, and the one PR7's MJCF and any
wheel controller will expect. It also means a **positive** joint velocity drives
the wheel's contact-patch *material* along `+d = ẑ × r̂` (verified:
`cross(r̂, [0,0,-r_w]) == d̂·r_w` for all three).

> **Corrected during Phase 7** (red-team N1, and the N+1th site of the same
> over-claim: this ruling originally continued "…the same sign the LeRobot
> driver's `wheel_linear_speeds` uses"). It does not. The rolling constraint
> gives `v_axle = -θ̇·r_w·d̂`, so a positive joint velocity moves the **body**
> along `−d̂` while the driver's positive wheel speed means the body along
> `+d̂`. The model is unchanged and matches upstream LeKiwi; the physical motor
> sign is a **calibration** fact for PR6/PR7 to confirm in sim. **D29** carries
> the precise statement — three source sites were corrected in Phase 5 while
> this one, the ruling that authored the error, survived. Fixed here so no copy
> of the claim outlives its correction.

Express the axis as `0 0 1` in the rotated child frame, **not** as a
radial vector with zero rpy — the composition is the thing PR6 depends on, so it
should be the thing the test checks.

Execute-verified in this env (Phase 4): xacro supports `radians()`, `cos()`,
`sin()`, `pi` and computed macro arguments; `${r*cos(radians(60))}` expands to
`0.06250000000000001`.

### R7 — properties: what is real, what is a placeholder, and say which

Every dimension is an `<xacro:property>`. Two of them are **real, sourced
numbers** and must be commented as such (from LeRobot `lekiwi.py`'s
`_body_to_wheel_raw`/`_wheel_raw_to_body` defaults):

- `wheel_radius = 0.05` — m.
- `base_radius = 0.125` — m, "distance from the centre of rotation to each
  wheel", i.e. to each wheel's **axle centre**.

The rest are **placeholders chosen to be plausible**, and each needs a comment
saying so plainly rather than implying a source it does not have:
`wheel_width`, `chassis_radius`, `chassis_height`, the chassis z-offset, and the
masses in R8. A fake citation is worse than an honest "estimated".

Mount angles are properties too — three named degree values (`wheel_angle_left`
= 60, `..._back` = 180, `..._right` = 300), converted with `${radians(...)}` at
use. Not a hard-coded `120` sprinkled around: the 120° spacing is then a
*property of the three values*, which R9's test asserts.

### R8 — inertials: yes, and asserted (explicitly permitted scope)

Give `base_chassis_link` and the three wheel links real `<inertial>` blocks —
mass as a property, inertia **computed from the same radius/height/width
properties** by the standard solid-cylinder formulas, so retuning a dimension
cannot leave a stale inertia behind. `base_link` and `base_footprint` stay
massless frames (standard for pure frames).

The brief does not ask for this. It is permitted anyway because a base with no
mass is not simulable, and PR7 would otherwise revisit every link. Keep it
simple; R9's test asserts positive mass and positive diagonal inertia rather
than specific values.

### R9 — the five new tests (extend `test_description.py`, do not replace it)

House style is one concern per function, each with a docstring saying *why it
needs its own assertion*. Match it.

1. `test_wheel_joints_are_exactly_three_continuous` — the joint-name set is
   exactly the R2 three, all `type == 'continuous'`, count 3.
2. `test_wheel_mounts_are_120_degrees_apart` — the load-bearing one. From the
   *parsed* model: each wheel joint's parent is `base_link`; its origin xy has
   `hypot == base_radius` and `z == 0`; the three angles are pairwise 120°
   apart; and the joint axis **rotated into `base_link` coordinates** equals the
   outward radial unit vector at that wheel's own angle. Without this, "120°
   spacing" and the R6 sign convention are comments, not a gate. Read
   `base_radius` from the expansion's own geometry, not by hardcoding 0.125 in
   two places.
   Use `math` only — **no numpy** (it is not a declared test dep); a ~15-line
   rpy→rotation-matrix helper is fine.
3. `test_base_footprint_is_the_ground_projection` — a `fixed` joint
   `base_link → base_footprint` at `xyz == (0, 0, -wheel_radius)`.
4. `test_model_loads_in_robot_state_publisher` — R10.
5. `test_moving_links_have_inertia` — every link except `base_link` and
   `base_footprint` has an `<inertial>` with positive mass and positive
   `ixx/iyy/izz`.

Plus: extend `EXPECTED_LINKS` per R2. Do **not** touch `SUBASSEMBLIES`,
`FILE_BEARING_TAGS`, or any existing test function's body.

### R10 — the `robot_state_publisher` test, concretely

Phase 2 established the mechanics (see above); build to them, do not re-probe:

- Launch `ros2 run robot_state_publisher robot_state_publisher --ros-args -p
  robot_description:=<the expanded URDF>`. The binary is **not on PATH**;
  `_require_tool('ros2')` is the guard to use.
- `subprocess.Popen`, stderr merged into stdout, read **incrementally**.
  **Pass** when the line `Robot initialized` appears — then terminate the node.
  **Fail** if the process exits first (report its rc *and* captured output — the
  useful message is in there) or if a hard deadline (~30 s) elapses.
- Always `terminate()`/`kill()` in a `finally`. A leaked node poisons later runs.
- Isolate the ROS graph: set a dedicated `ROS_DOMAIN_ID` in the child's env.
- No `launch_testing` — `pytest.ini` disables that plugin workspace-wide.

There is **no in-repo precedent for this shape** (`context.md` §5 checked:
nothing uses `Popen`/`terminate`/`poll` anywhere). You are writing it from
scratch; keep it small and readable, and if the node proves flaky under
`colcon test` rather than under a bare `pytest`, **escalate to the manager** —
do not weaken the assert into a tautology.

### R11 — `package.xml`: declare the new test dependency

Add `<test_depend>robot_state_publisher</test_depend>` alongside the existing
four, and extend that block's comment to say why. Use the **rosdep/ROS** name,
never the conda `ros-jazzy-*` spelling. `pixi.toml` is already done (Phase 2) —
**do not re-run `pixi add`**.

### R12 — durable docs: fix the stale "4-wheel", add **D29**, update the roadmap

In-package staleness, all three D26 got wrong and PR1 inherited — fix them
(`context.md` §2): `setup.py:42` `description=`, `package.xml:6`
`<description>`, `README.md:3`. New text describes a **3-omniwheel holonomic
base**. Also refresh `README.md`'s "empty for now" lines and its summary of what
the gate asserts, and `meshes/README.md`'s "geometry arrives with the base
(PR2)" line — under R3 it does not, and leaving that sentence there would be a
lie the next PR trips over.

Add **D29** to `docs/design/decisions.md` (append-only; D28 is last; follow the
house entry style — a bolded claim sentence, then clauses, then *Rationale:*).
It must record, because none of these are derivable from the diff:
- The base is **authored parametrically, not copied**: LeKiwi's URDF is a CAD
  export, so what is cribbed is names + joint types + two numbers + a mount
  convention (R2, R6, R7), with the joint names matching the LeRobot driver's
  motor keys so driver and URDF share one namespace (D26 applied to naming).
- The mount-angle mapping and axis sign convention of R6, **and that it was
  verified numerically against the driver's kinematic matrix** — including the
  trap that the driver's `[240, 0, 120]` array is rolling directions, not mount
  positions. This is the single fact PR6 most needs and most likely to get
  wrong.
- R1's `base_chassis_link` shape and **R5's odometry constraint**
  (`odom → base_link`, never `odom → base_footprint`).
- **R3**: primitives, no vendored meshes, with the reasons, and that D27's
  flat-glob `os.walk` rewrite is therefore **deferred to the first PR that lands
  real meshes** — this amends the breakdown's §PR2 expectation and must not be
  lost.
- What the gate grew by (R9), so "the link set is a gate" stays true.

Update `docs/design/urdf-mjcf-pr-breakdown.md`: mark **§PR2 DONE** in the same
style PR1 is marked (`**PR1 is DONE** — merged as PR #62 …, ratified as D27`),
and move its "PR2 is where the flat `glob()` install becomes an `os.walk`
version" bullet to the first mesh-bearing PR, citing D29. Also refresh
`docs/design/spec.md`'s "Description & packaging" section (`:102-117`) — it is
the flattened current state and currently implies the description has no
geometry.

Yes, this reaches outside `src/robot_description/`. That is deliberate and is
this repo's own convention (D27/PR1 did the same), not scope creep: a structural
call with no decision-log entry is a call PR3–PR7 will re-litigate.

### R13 — the test-count floor

Per D28 the ratchet is self-maintaining: run `pixi run test` once at the end and
**commit the resulting `scripts/test_baseline.json` diff**. Current value is
`"robot_description": 7`; R9 takes it to 12. **Verify the diff touches only the
`robot_description` entry** — if any other package's number moves, stop and
escalate to the manager rather than committing it.

### R14 — scope discipline

Do not touch: `src/robot_backends/**` or `src/robot_brain/**` (out of the
brief's owned paths); `urdf/column.xacro`, `urdf/arm.xacro` (PR3/PR4);
`urdf/robot.urdf.xacro` (R1 — no edit is needed at all);
`scripts/check_test_integrity.py`; `pixi.toml`/`pixi.lock` (done in Phase 2);
`docs/design/decisions.md` entries D1–D28 (append-only — add D29, edit nothing
above it). Do **not** "fix" the `pixi.toml` `[project]`→`[workspace]`
deprecation warning.

`src/robot_brain/robot_brain/openclaw/AGENTS.md:3` tells the OpenClaw brain the
robot has "a four-wheel base" — genuinely stale against D26 and worth fixing,
but it is out of the owned paths. **Ignore it**; the manager routes it as a
follow-up comment on issue #65.
