# Context — i73 / PR3 — Extendable column (prismatic STS3215 lift)

Owned paths: `src/robot_description/` only (+ `docs/design/decisions.md` for
the durable decision, + this ephemeral dir). No other package may change.

## 1. The existing harness — `src/robot_description/test/test_description.py` (949 lines)

Module docstring (`test_description.py:7-76`) is itself the harness's design
doc — read it in full before touching anything. It states the extension
contract explicitly (`:69-75`): add new links to `EXPECTED_LINKS`, add any new
file-naming element to `FILE_BEARING_TAGS`, add whatever joint/limit
assertions the subassembly earns, keep the link set *exact*.

### Module constants (`:94-185`)
- `EXPECTED_LINKS` (`:94-101`) — exact link set, currently the 6 base links.
  **PR3 must add every new column link name here.** The assert
  (`test_link_set_is_exactly_the_expected_links`, `:482-488`) is `==`, so a
  link that isn't added here fails the run, and one added-but-unused would
  also make this test fail (there's no way to "silently" add a link — good).
- `WHEEL_JOINTS`, `DRIVER_ROLLING_ANGLES_DEG`, `DRIVER_BASE_RADIUS_M`,
  `DRIVER_WHEEL_RADIUS_M`, `DRIVER_MATRIX_TOL` (`:103-143`) — base-only, PR3
  does not touch these. They *are* the precedent for how to pin an absolute
  outside contract (see §5/§Q5 below): transcribed constants with a comment
  naming the exact upstream source line, not a live import.
- `MASSLESS_FRAME_LINKS = frozenset({'base_link', 'base_footprint'})`
  (`:145-148`) — **every link not in this set must carry a real
  `<inertial>`** (enforced generically over `parsed_model.links`, not just
  base links — see below). Whether `column_top` (or any new column link) joins
  this set is Q6.
- `RSP_READY_MARKER`, `RSP_STARTUP_TIMEOUT_S`, `RSP_DOMAIN_ID` — untouched by
  PR3.
- `PLACEMENT_TOL_M = 1e-9`, `ANGLE_TOL_DEG = 1e-6` (`:167-168`) — reusable for
  any new placement assertion PR3 writes (e.g. joint origin checks).
- `SUBASSEMBLIES = ('base.xacro', 'column.xacro', 'arm.xacro')` (`:173`) —
  already lists `column.xacro`; **no change needed**, `column.xacro` is
  already included by `robot.urdf.xacro` (empty today) and already gated by
  `test_top_level_includes_every_subassembly` / `test_share_layout_is_installed`.
- `FILE_BEARING_TAGS = ('mesh', 'texture')` (`:185`) — PR3 vendors no meshes
  (see §6/§Nori), so this stays untouched and `test_every_asset_reference_resolves`
  stays a no-op the way it was for PR1/PR2.

### Helpers PR3 will reuse (do not duplicate)
- `_require_identity_origin(origin, what)` (`:286-309`) — asserts a URDF
  `<origin>` is absent or exactly identity. Used today to guard `_collision_cylinder`'s
  reads. **Any new dimension PR3 reads off a shape's geometry (e.g. a rail
  cylinder radius) must be paired with this**, per the same rationale that
  governs the base: a shape offset/rotation would make a read-off number mean
  something else while still parsing fine.
- `_collision_cylinder(model, link_name)` (`:312-331`) — returns
  `(radius, length)` off a link's first collision cylinder, calling
  `_require_identity_origin` internally. Reusable verbatim for any cylindrical
  column link.
- `_rotation_from_rpy` / `_rotate` (`:235-261`) — hand-rolled rotation math,
  reusable if PR3 needs to check an axis after rpy composition (unlikely for
  a straight prismatic lift, but available).
- `_wheel_radius`, `_wheel_placements` (`:264-283`, `:334-346`) — base-specific,
  not reusable for the column.

### Tests that are generic over **all** links/joints (PR3 is caught by these automatically — no edit needed to the test *body*, only to the constants they read)
- `test_solid_links_have_visual_and_collision_geometry` (`:710-788`) — its
  **first loop** (`:740-750`) iterates `parsed_model.links` and requires
  `<visual>` + `<collision>` on every link **not in `MASSLESS_FRAME_LINKS`**.
  Any new column link that is a real body (not added to `MASSLESS_FRAME_LINKS`)
  is automatically required to carry both, or this test fails — this is the
  gate for issue's "own D27/D29 geometry" acceptance criterion applied to the
  column. Its **second half** (`:752-788`) is base-chassis-specific (hardcoded
  `'base_chassis_link'`) and does **not** generalize — PR3 gets no automatic
  chassis-vs-column clearance check; if a manager ruling wants one (e.g. column
  clears the chassis), it must be **written**, following this test's own
  pattern (read the two z-extents off the model, assert a relationship, not a
  literal — the docstring at `:725-738` explains why the strong/conservative
  form was chosen for the analogous chassis/wheel check).
- `test_moving_links_have_inertia` (`:791-821`) — same generic-loop pattern:
  every link not in `MASSLESS_FRAME_LINKS` must have `inertial.mass > 0` and
  `ixx/iyy/izz > 0`; every link *in* `MASSLESS_FRAME_LINKS` must have **no**
  mass (`:806-809`). **This is the test that makes Q1/Q6 load-bearing**: get
  the topology wrong (e.g. leave a solid carriage link out of `EXPECTED_LINKS`'s
  inertia coverage, or wrongly mark a solid link massless) and this either
  fails loudly (good) or — if a link is wrongly added to
  `MASSLESS_FRAME_LINKS` — silently stops checking it (bad; this is exactly
  the "a set allowed to grow silently stops being a gate" trap the module
  docstring warns about for `EXPECTED_LINKS`, applied to this second set).
- `test_model_loads_in_robot_state_publisher` (`:849-911`) — fully generic,
  re-runs against whatever `expansion` produces; no edit needed. **Empirically
  confirmed this test's assumption generalizes to a prismatic joint** — see §3.
- `test_every_asset_reference_resolves` (`:913-943`) — generic, stays a no-op
  since PR3 (like PR2) is primitives-only (no meshes) — see §6.
- `test_check_urdf_parses_the_expansion`, `test_xacro_expands_without_error`,
  `test_top_level_includes_every_subassembly`, `test_share_layout_is_installed`,
  `test_robot_is_named` — fully generic, need no edits, but must all stay
  green (they are exactly the acceptance criterion "xacro still expands...
  check_urdf/urdfdom_py still parse it").

### Tests PR3 must **add** (none exist yet for prismatic/joint-limit semantics)
Nothing in the current file asserts a joint's `type == 'prismatic'` or reads
`joint.limit.lower/upper`. The base has no limited joints (all `continuous`),
so this is new ground for PR3 — exactly what the issue's second acceptance
criterion asks for ("First place the URDF owns a RobotModel number — assert
against the model, not the raw file"). Follow the existing style: a fixture-free
assertion function operating on `parsed_model` (see `test_wheel_joints_are_exactly_three_continuous`
at `:491-522` as the closest structural precedent — asserts joint existence,
type, and wiring in one function with one combined-fault message pattern).

### Where a new column link would make an *existing* test fail vs silently pass
- **Fail loudly (good):** omitting the new link name(s) from `EXPECTED_LINKS`
  → `test_link_set_is_exactly_the_expected_links` fails with a legible diff.
  Omitting `<visual>`/`<collision>` on a non-frame link → `test_solid_links_have_visual_and_collision_geometry`
  fails. Omitting `<inertial>` on a non-frame link → `test_moving_links_have_inertia`
  fails. Wrong `<limit>` attrs → `check_urdf`/`urdf_parser_py` both refuse to
  parse at all (verified in §3), so this fails at the earliest possible test,
  `test_check_urdf_parses_the_expansion`.
- **Silently pass (the trap to avoid):** adding a link to `MASSLESS_FRAME_LINKS`
  that is *not* actually a pure frame — this exempts it from both the
  visual/collision and inertia checks with zero visible signal anywhere else
  in the suite. This is precisely Q6.

## 2. PR2's `base.xacro` — the authoring precedent to match

`src/robot_description/urdf/base.xacro:1-207`. Read the whole attribution
header (`:1-34`) — it is the template for column.xacro's own header (name the
crib, the license, which constant came from where, and say so if a number is
ESTIMATED rather than sourced).

Conventions to replicate exactly:
- **Property block first, geometry after**, each property commented SOURCED
  or ESTIMATED (`:37-97`). SOURCED cites the exact upstream file/function
  (`:43-49`, e.g. `lekiwi.py`'s driver defaults); ESTIMATED says so plainly
  and states the criterion used to pick the number (`:51-74`, e.g. the
  chassis-radius comment deriving the 1.3 mm clearance by hand).
- **Inertias are `${...}` xacro expressions computed from the same properties**
  that define the shape (`:122-132`, `:189-198`), never separately-typed
  numbers — "so retuning a dimension cannot leave a stale inertia tensor
  behind" (`:76-79`). A prismatic-lift carriage/rail should follow the same
  rule: pick a mass property, compute a solid-cylinder or solid-box inertia
  tensor from the same radius/height/length properties used for the collision
  geometry.
- **One `<xacro:macro>` when a shape repeats** (`omni_wheel`, `:168-201`); the
  column has no repetition (one lift), so a macro is optional, not required.
- **Every `<joint>` that attaches new geometry to `base_link` is explicit about
  its origin offset** (`:105-109` for the chassis, `:140-144` for the
  footprint) — a bare "at the parent's origin" join is never used when the
  real mount point differs.
- **`base_link` itself stays geometry-free**, declared only in
  `robot.urdf.xacro` (`base.xacro:31-33`, `robot.urdf.xacro:10-13,26`); PR3
  attaches to it via joints exactly as PR2 does, never adds a second
  `<link name="base_link">` (unbuildable under xacro — this is exactly D29's
  recorded deviation, see decisions.md D29 second bullet, `decisions.md:108`).
- **`base.xacro`'s own header text explicitly anticipates PR3's shape:**
  *"Shape: `base_link` is the assembly's root frame and is declared by
  robot.urdf.xacro, not here (D27). This file attaches `base_chassis_link` to
  it with a fixed joint, exactly as the column (PR3) and arms (PR4) will."*
  (`base.xacro:31-34`). This is direct textual evidence from the PR2 author
  that column geometry is expected to attach to `base_link` with **one joint**,
  the same pattern as the chassis — relevant to Q1/Q2 below.

`robot.urdf.xacro:1-28` — the top-level file. Already includes
`column.xacro` (`:22`); PR3 fills `urdf/column.xacro` (currently a stub, see
below) and adds nothing to the top level.

`urdf/column.xacro` today (`:1-11`) is a deliberate stub:
```
<!--
  Extendable column: one linear-rail STS3215 lift, modeled as a single
  prismatic joint (D26).

  Deliberately empty: PR1 ships the package and the expand/parse gate only.
  The column links and the prismatic joint land in PR3.
-->
<robot xmlns:xacro="http://www.ros.org/wiki/xacro">
</robot>
```
`urdf/arm.xacro` is the analogous PR4 stub — do not touch it.

## 3. `urdf_parser_py` / `check_urdf` behavior on a prismatic joint — **empirically verified**, not recalled

Probed inside `pixi run bash -lc '...'` in this worktree (RoboStack Jazzy env;
`urdf_parser_py` resolves to
`.pixi/envs/default/lib/python3.12/site-packages/urdf_parser_py/__init__.py`).

- `inspect.signature(urdf_parser_py.urdf.JointLimit.__init__)` →
  `(self, effort=None, velocity=None, lower=None, upper=None)` — all four are
  optional **in the Python constructor**.
- **But `check_urdf` (urdfdom C++) requires both `effort` and `velocity` on
  any `<limit>` element, prismatic included.** Verified by constructing a
  minimal two-link URDF with `<limit lower="0.0" upper="1.2"/>` (no
  effort/velocity) and running `check_urdf` on it:
  ```
  Error:   joint [column_lift]: limit has no effort
           at line 176 .../urdf_parser/src/joint.cpp
  Error:   Could not parse limit element for joint [column_lift]
  Error:   joint xml is not initialized correctly
  ERROR: Model Parsing the xml failed
  ```
  rc 255. Adding `effort="100"` alone still fails (`limit has no velocity`,
  rc 255). Adding both `effort="100" velocity="1.0"` parses cleanly (rc 0,
  `Successfully Parsed XML`).
- **`urdf_parser_py.urdf.URDF.from_xml_string` enforces the same requirement**
  independently of `check_urdf` (its own XML reflection, not a shell to
  urdfdom): parsing the effort/velocity-less URDF raises
  `urdf_parser_py.xml_reflection.core.ParseError: ... /limit: Required
  attribute not set in XML: effort`.
  **Conclusion: `effort` and `velocity` on `column_lift`'s `<limit>` are not
  optional — both parsers that gate this package reject their absence.** This
  answers Q4's "required?" half definitively; the *values* are still open
  (see Q4 below).
- Parsed attribute shapes, confirmed by executing on a valid two-link model:
  `joint.type == 'prismatic'` (string); `joint.limit.lower`, `.upper`,
  `.effort`, `.velocity` are all plain floats; `joint.axis` is a 3-list.
  Exactly the shape `test_wheel_joints_are_exactly_three_continuous` already
  reads for `joint.type` (`test_description.py:505-506`), so the new
  prismatic-joint assertion can follow the same style.
- **`robot_state_publisher` needs no `/joint_states` publisher to log
  `Robot initialized` and stay up** — verified by writing a params file
  carrying a minimal `base_link` → (prismatic, `column_lift`) → `top_link`
  URDF (with valid effort/velocity) and running
  `ros2 run robot_state_publisher robot_state_publisher --ros-args
  --params-file ...` under a private `ROS_DOMAIN_ID`: it logged
  `[INFO] [robot_state_publisher]: Robot initialized` immediately and stayed
  up until SIGINT/SIGTERM. **No joint-state input was provided at any point.**
  This confirms `test_model_loads_in_robot_state_publisher` needs zero changes
  for a prismatic joint — it already only checks "model accepted, KDL tree
  built," the same thing it checks today for the base's `continuous` joints
  (which also have no joint-state publisher in that test).

## 4. `RobotModel` — where the two numbers live today, and what PR3 must NOT touch

`src/robot_backends/robot_backends/mock_world.py:68-127`:
```python
@dataclass(frozen=True)
class RobotModel:
    shoulder_offset_y: float = 0.18
    shoulder_offset_z: float = 0.50
    reach_radius: float = 0.85
    home_gripper_offset: Point = Point(0.35, 0.0, -0.05)
    min_column_height: float = 0.0
    max_column_height: float = 1.20
    ...
    def shoulder(self, base_pose: Pose, column_height: float, side: Side) -> Point:
        lateral = self.shoulder_offset_y if side is Side.LEFT else -self.shoulder_offset_y
        return base_pose.position + Point(0.0, lateral, column_height + self.shoulder_offset_z)
```
`min_column_height=0.0`, `max_column_height=1.20` (`:86-87`) are **exactly**
the issue's 0.00–1.20 m bounds. `column_range_text()` (`:124-126`) formats
them for a failure message; `MockWorld.__post_init__` (`:171-183`) validates
`start_column_height` (default 0.3, `:136`) against this range. The comment
at `mock_world.py:264-268` (`default_seed_document`'s docstring for the table
scenario) states the arithmetic explicitly: *"the starting column height (0.3
m puts a shoulder at z = 0.8 m...)"* = `start_column_height + shoulder_offset_z`
exactly, no other additive term — this is the empirical anchor for Q3 below.

**PR3 must not edit `robot_backends` at all** (out of scope, and the roadmap
(`urdf-mjcf-pr-breakdown.md:129-137`) reserves that refactor for **PR6**,
"guarded by a golden-value test so the existing suite stays green," explicitly
gated behind PR4 too — column bounds alone *could* land after PR3 per the
roadmap's parenthetical, but that's PR6's call, not PR3's). PR3's job is
narrower: the URDF's own `column_lift` limit must **equal** these two numbers,
and the test must assert that correspondence somehow (see Q5 — the open
question is *how*, not *whether*).

## 5. Decisions that bind this PR

- **D26** (`decisions.md:74-84`) — single Feetech STS3215/LeRobot-bus
  ecosystem is the governing constraint; the column bullet
  (`decisions.md:79`) is the literal source of PR3's scope: *"linear-rail
  STS3215 lift on the arm bus (Nori-style)... one ~600 mm linear rail driven
  by a single STS3215... modeled as one prismatic joint... limits 0.00–1.20
  per D23's RobotModel."* Note the **600 mm rail** figure here is D26's own
  paraphrase of the Nori Bot paper's mechanism, distinct from and not
  arithmetically reconciled with `RobotModel`'s 0.00–1.20 m *travel* bound —
  flagged, not resolved, in §6.
- **D27** (`decisions.md:88-92`) — the harness this PR extends: `ament_python`
  build type, relative includes, glob-based install (`glob('urdf/*')`,
  already covers `column.xacro`, no `setup.py` change needed — verified by
  reading `setup.py:31-37`), the four-tool + two-wiring-assert gate PR3 must
  keep green, and **the rule that `EXPECTED_LINKS` must grow *deliberately*.**
- **D29** (`decisions.md:105-111`) — the PR2 precedent for *how* to deviate
  from literal issue wording when it's unbuildable, **with a recorded
  rationale**, not silently: *"the issue's literal 'base_link collision +
  visual geometry' is unbuildable [because xacro doesn't merge two `<link>`
  s of one name]... The base instead attaches `base_chassis_link`... to
  `base_link` with a fixed joint, exactly as the column and arms will."*
  (`decisions.md:108`). **This is the precedent Q1/Q2 sit on**: PR3's issue
  text ("prismatic joint `column_lift` from `base_link` to a new `column_top`
  mount frame") is *not* obviously unbuildable the way D29's case was — it's
  a candidate topology, not a contradiction — so the D29 precedent licenses
  deviating *if the literal reading turns out to be wrong*, not a mandate to
  deviate by default. D29's **last bullet** (`decisions.md:111`) is the
  sharper lesson for PR3's test-writing: *"a gate built only from internal
  consistency checks certifies self-consistency, and a wrong model can be
  perfectly self-consistent... make sure at least one absolute [assertion]
  pins the artifact to whatever outside contract it exists to satisfy."* For
  PR3 that outside contract is `RobotModel.min_column_height`/`max_column_height`
  (§4) — the joint-limit assertion PR3 adds is meaningless as a gate unless it
  is pinned to those two numbers specifically, not just internally consistent
  with itself (e.g. "lower < upper" alone would be a relational check that
  passes any two limits).
- **D23** (referenced via D26/D27/roadmap, not independently quoted here) —
  established `RobotModel` as the "hardware description that stays in code,
  later coming from the URDF/MJCF" (`urdf-mjcf-pr-breakdown.md:16`) and put
  the world/robot split (`decisions.md`'s D23 entry is the world-store one at
  `decisions.md:49-58`, cross-referenced by `mock_world.py:11-13`) that keeps
  `RobotModel` out of the world file.
- **D30** (`decisions.md:113-121`) — **declined** `robot_brain` a dependency
  on `robot_description` for exactly this kind of "pin a body fact" problem,
  on cost/benefit grounds specific to that edge (`decisions.md:118`: "the
  edge buys one digit... and it lands on a package PR3–PR7 are still filling
  in"). This is **not** the same edge as PR3's candidate `robot_description`
  → `robot_backends` test dependency (opposite direction, and D30's target
  was `robot_description`, which PR3 *is*), but it is the closest precedent
  for how this codebase has previously resolved "should a test import a
  cross-package source of truth, or transcribe it" — see Q5.

## 6. The Nori Bot crib — what's recorded, what's genuinely unknown

Confirmed by grep across `docs/design/`: Nori Bot (arXiv 2605.16537) is
mentioned in exactly three places (`spec.md:49-50`, `PROJECT.md:67-68`,
`decisions.md:81,84`), and **all three explicitly flag it UNVERIFIED**:
- `spec.md:49-50`: *"Nori Bot (arXiv 2605.16537) — crib for the linear-rail
  STS3215 column and the agent↔hardware seam. **UNVERIFIED until the paper is
  read** (D26)."*
- `PROJECT.md:67-68` (still under "Open questions — still genuinely open"):
  *"Nori Bot is UNVERIFIED... the column crib and the agent↔hardware seam
  both lean on a paper we have not read (D26)."*
- `decisions.md:81`: *"Nori Bot (arXiv 2605.16537, May 2026) — a ~$947 17-DoF
  dual-arm mobile manipulator with a 600 mm linear-rail Z-lift on a Feetech
  bus, a Pi 4 thin client, exposed to an OpenClaw-style agent runtime — is
  almost this exact stack and is the crib target for the column mechanism...
  Treat Nori Bot as UNVERIFIED until we read the paper (flagged, not yet
  baked into any code)."*

**No dimensions beyond the 0.00–1.20 m `RobotModel` bounds and the ~600 mm
rail-length paraphrase in D26 are recorded anywhere in the repo.** Nothing
about rail cross-section, carriage geometry, mass, STS3215 torque/speed specs,
or mount-plate dimensions exists in any doc or code comment. **Every physical
dimension PR3 introduces beyond the two `RobotModel` bounds (rail
radius/width, carriage dimensions, masses, `effort`/`velocity` limit values)
is necessarily ESTIMATED** — there is no paper-verified number to cite, and
inventing one and citing it as sourced would be a "fake citation," which
`base.xacro`'s own header explicitly calls out as worse than an honest
estimate (`base.xacro:1-4` framing, and the base's own ESTIMATED-marked
properties are the pattern to follow, e.g. `base.xacro:51-53,61-63,76-79`).

## 7. The test ratchet — `scripts/check_test_integrity.py` + `scripts/test_baseline.json`

`pixi.toml:48`: `test = { cmd = "python scripts/check_test_integrity.py",
depends-on = ["check-provisioning"] }` — this is what `pixi run test` runs.
It does **not** run `colcon build` itself (confirmed by reading
`check_test_integrity.py:1162-1183`: it deletes stale result files, then runs
`colcon test --base-paths ... --build-base ...`, no build step) — **a
`pixi run build` (`colcon build --symlink-install`) must precede it**,
exactly as the module docstring says (`test_description.py:65-67`: "this
suite runs under `colcon test`... after a `colcon build`, not against a bare
checkout").

Current baseline (`scripts/test_baseline.json`, read directly): `"packages":
{... "robot_description": 14 ...}`. Counting `test_description.py`'s
non-linter test functions confirms this: 14 top-level `test_*` functions
today (`test_copyright.py`/`test_flake8.py`/`test_pep257.py` are excluded from
the count per `LINTER_TEST_NAMES`, `check_test_integrity.py:119`). **PR3 will
add new test functions, which raises the collected count above 14 — the
baseline auto-bumps on the next green `pixi run test` run** (D28,
`decisions.md:94-101`: "up is automatic, down is a gate"). **The implementer
should not hand-edit `scripts/test_baseline.json`** — a run that adds tests
and stays green rewrites it automatically, in the same run, as part of
`pixi run test`'s own execution; hand-editing would just be overwritten (or
worse, diverge from what the run actually produced). Only `ALLOW_TEST_DECREASE=1`
or `--allow-decrease` matters if a test is somehow *removed* — not the
expected direction here.

## 8. Build & test commands

Exact commands (this workspace, `pixi.toml`, verified reachable in this
session):
```
pixi run build      # colcon build --symlink-install
pixi run test        # python scripts/check_test_integrity.py (runs colcon test + ratchet)
```
Narrower, package-scoped iteration while developing (not a substitute for the
full `pixi run test` before signaling ready — CLAUDE.md's gate is the full
suite):
```
pixi run bash -lc 'colcon build --symlink-install --packages-select robot_description'
pixi run bash -lc 'colcon test --packages-select robot_description --event-handlers console_direct+'
```
**Note on invoking `pixi run`/`pixi run bash -lc ...` from a probe or script:**
`pixi` resolves `pixi.toml` from the **current working directory**, not from
an absolute path baked into the command — confirmed by hitting `could not
find pixi.toml ... at directory /tmp` when `cd /tmp && pixi run ...` was
tried in one shell invocation. Run `pixi` commands with the worktree root as
cwd (the default for this session) and pass absolute paths as *arguments*,
never `cd` into another directory first.

The suite resolves everything through `get_package_share_directory` (the
**installed** `share/robot_description/` tree, no source-tree fallback) —
confirmed by reading `test_description.py:60-67,388-397` and D27's own
rationale (`decisions.md:91`). A stale `install/` (edited source, no rebuild)
will make the test suite silently exercise the **old** column.xacro.

## Open questions

Framed for the manager's ruling; each includes the concrete evidence found
for each side. None of these are resolved in this document — implementation
should not start until they are ruled on.

### Q1 — link topology: is `column_top` the moving link, or a static frame atop a separate moving link?
Taken literally, "prismatic joint `column_lift` from `base_link` to a new
`column_top` mount frame" makes `column_top` the joint's **child**, i.e. the
moving link — which then, per `test_moving_links_have_inertia` and
`test_solid_links_have_visual_and_collision_geometry`'s generic loops (§1),
**must** carry mass + visual + collision unless explicitly exempted via
`MASSLESS_FRAME_LINKS`.

Two concrete candidates:
- **Option A — single link.** `column_top` *is* the carriage: one prismatic
  joint `base_link` → `column_top`, `column_top` carries real
  visual/collision/inertia (a small solid representing the carriage/mount
  plate). **+1 new link total.** Directly satisfies the issue's literal
  wording. Later PRs (3.5, 4) hang their own links off `column_top` via their
  own joints — nothing in the roadmap text requires `column_top` itself to be
  massless (checked: `urdf-mjcf-pr-breakdown.md`'s PR3.5/PR4 bullets only say
  things mount "on"/"off `column_top`", never "column_top must be a pure
  frame"). Con: elsewhere `column_top` is called a "mount frame"
  (`urdf-mjcf-pr-breakdown.md:84`, the issue text itself), language that
  elsewhere in this codebase (`base_footprint`) means *massless* — a solid
  `column_top` would be a naming/semantics mismatch a reviewer might flag,
  though nothing mechanically enforces the mismatch.
- **Option B — static rail + moving carriage + massless `column_top` frame.**
  A fixed `column_rail_link` (or no separate rail link, folded into just the
  carriage) attached to `base_link`, a prismatic joint to a solid
  `column_carriage_link`, then a **fixed** joint from the carriage to a
  massless `column_top` — mirroring the `base_link`/`base_footprint`
  fixed-child pattern exactly (`base.xacro:140-146`). **+2 or +3 new links.**
  `column_top` joins `MASSLESS_FRAME_LINKS` (Q6). Con: under this option the
  joint literally named `column_lift` no longer goes **to** `column_top` (it
  goes to the carriage) — a more explicit deviation from the issue's plumbing
  than Option A, though not from its physical intent.

**D29's precedent** (`decisions.md:108`, quoted in §5) is for deviating from
literal issue wording *when it's unbuildable*, with a recorded rationale.
Option A's literal reading is **buildable** (unlike D29's base_link-double-
declaration case) — so D29 licenses Option B only if there's an independent
reason Option A is *wrong*, not merely non-literal. `base.xacro`'s own header
(`:31-34`, quoted in §2) describes PR3 attaching to `base_link` with (singular)
"a joint," which reads as anticipating Option A's shape more than Option B's.

### Q2 — parent link: `base_link` or `base_chassis_link`?
The issue says `base_link`. `base.xacro`'s header (`:31-34`) also says the
column attaches to `base_link`, "exactly as" the chassis does — i.e. both are
**siblings** hanging off `base_link` via their own joints with their own
z-offsets, the same pattern already used for `base_chassis_link` (offset
`chassis_z_offset=0.085`) and each wheel (offset `0`).

**Computed clearance number** (from `base.xacro`'s own properties,
`base.xacro:62-74`): chassis top surface sits at
`chassis_z_offset + chassis_height/2 = 0.085 + 0.03 = 0.115` m above
`base_link`. If the column's `<joint>` origin places its own bottom surface
at `base_link`'s own `z = 0` (axle height, inside/below the chassis puck),
it will intersect the chassis solid — the exact failure mode D29's own
red-team round found and fixed for the chassis/wheel pair
(`decisions.md:111`, "the chassis puck's underside sat exactly on the axle
plane, burying the top half of every wheel"). **Rooting the column at
`base_link` is not itself wrong** (it matches both the issue and the
`base.xacro` header) **provided the column's own joint origin z-offset is
≥ 0.115 m**, exactly as the chassis's own joint carries a nonzero z-offset
despite being parented to `base_link`. No existing test enforces a
column/chassis clearance the way `test_solid_links_have_visual_and_collision_geometry`
enforces chassis/wheel clearance (§1) — whether PR3 should add one (following
that test's own "assert a relationship between numbers read off the model,
not a literal" pattern, `test_description.py:725-738`) is itself worth a
ruling.

### Q3 — what does `column_lift`'s zero position mean physically, and does the joint's `lower`/`upper` measure travel or absolute height?
`urdf-mjcf-pr-breakdown.md:25-26`'s table states the mapping as directly as
it can: `min_column_height` (0.00 m) "becomes in URDF" the prismatic
**lower limit**, `max_column_height` (1.20 m) the **upper limit** — no
mention of any additional mount-height offset folded into the limit values
themselves. `mock_world.py:114-122`'s `shoulder()` method computes
`column_height + shoulder_offset_z` with **no third additive term**, and the
docstring at `mock_world.py:266-268` confirms the arithmetic numerically
(`0.3 + 0.5 = 0.8`). **Neither source documents whether `column_height`/the
joint's translation value is measured from `base_link` (z=0, axle height) or
from wherever the column's static base actually sits** (e.g. the chassis top
at z≈0.115 per Q2). Two readings:
- The joint's **origin** (fixed placement of the column's static mount,
  wherever Q2 lands it) is separate from its **limit** (a pure 0.00–1.20
  travel range measured from that origin) — i.e. `column_top`'s height above
  `base_link` at `column_lift=0` equals the joint's own mount z-offset (not
  necessarily 0), and the *travel* (limit range) is what's pinned to
  `RobotModel`.
- Alternatively, the joint's origin sits at `base_link`'s own z=0 exactly, so
  the limit values *are* the absolute height above `base_link` — which would
  require justifying why the mount doesn't need the Q2 clearance offset (e.g.
  if the column is instead the thing lifting the chassis's function, or if
  `RobotModel`'s 0.00 baseline is meant to already include an implicit rail-base
  offset nobody has stated).

Neither the roadmap table nor `RobotModel` states which; **this is a genuinely
open question the docs do not answer**, not a fact this document is
withholding.

### Q4 — `effort`/`velocity` values: required (confirmed, §3), but sourced from where?

> **Scope error, found by red-team round 1 (B2/R14).** The claim below says
> "anywhere in this repo" on the strength of a grep of **`docs/design/`
> only**. `src/robot_safety/robot_safety/limits.yaml:36` records
> `velocity.column: 0.15` — a *policy* cap on the same axis, distinct from
> the URDF's *capability* limit but very much a number in this repo. The
> implementation then estimated 0.15 independently and shipped a cap that
> could never bind. A search that covers one directory must say so in its
> conclusion; this one did not, and the conclusion became a manager ruling.

Confirmed both attributes are mandatory for any parseable `<limit>`
(empirically, §3). No STS3215 torque/speed spec, nor any linear-rail
lead-screw ratio, is recorded anywhere in this repo (grepped `docs/design/`
for "STS3215" + torque/effort/velocity keywords — only the generic servo
naming appears, no numbers, confirmed in §6's search). Whatever values PR3
picks are necessarily ESTIMATED unless the implementer is handed a genuine
STS3215 datasheet figure with a citable source outside this repo (which
context-explorer has not verified exists or is accurate — flagged, not
fabricated). Follow `base.xacro`'s ESTIMATED-marking convention (§2) rather
than inventing a fake SOURCED citation.

### Q5 — what absolute assertion pins the column, and how does the test read `RobotModel`'s numbers?
D29's lesson (`decisions.md:111`, quoted in full in §5) requires **at least
one** assertion that isn't merely internally consistent but pinned to an
outside contract — here, `RobotModel.min_column_height`/`max_column_height`
(§4). Two candidate mechanisms:
- **Transcribe as constants**, exactly the `DRIVER_*` pattern
  (`test_description.py:110-143`) already uses for the LeRobot driver's
  numbers: module-level constants in `test_description.py` with a comment
  citing `mock_world.py`'s `RobotModel` fields by name and line, no new
  `package.xml`/`pixi.toml` dependency. Matches D30's stated preference order
  when no shared owner exists across the seam
  (`decisions.md:119`: "find the claim's owner and read it; a ledger row is
  the fallback for a claim that genuinely has none") — except here the
  "owner" (`RobotModel`) *does* exist, just in a package `robot_description`
  doesn't currently depend on.
- **Add `robot_backends` as a `<test_depend>`** of `robot_description` and
  import `RobotModel` directly. This is the *opposite* direction from the
  edge D30 declined (`robot_brain` → `robot_description`) — D30's cost
  argument (`decisions.md:118`, "the edge buys one digit... and it lands on a
  package PR3–PR7 are still filling in") doesn't transfer cleanly, but a
  structural concern does: the roadmap's own PR6 (`urdf-mjcf-pr-breakdown.md:129-137`)
  makes `robot_backends` depend on `robot_description` at **runtime**
  ("`RobotModel`'s default [will] load from the shipped URDF"). A
  `robot_description`-test → `robot_backends` edge landed now would, after
  PR6 merges, put `robot_backends` on both sides of the dependency (a runtime
  depend one way, a test depend the other) — not a hard cycle (test_depend
  and depend are different colcon graph edges) but an unusual shape worth the
  manager's eyes before it's chosen, especially since `robot_description` is
  meant to be the low-level/foundational package in this roadmap.

Transcription reads as the stronger match to the codebase's own precedent
(`DRIVER_*`, and D30's stated preference order), but it was not this
document's call to make.

### Q6 — does `column_top` (or any new link) join `MASSLESS_FRAME_LINKS`?
Directly downstream of Q1. The set's docstring is narrow by design
("Links that are pure frames... the root and the ground projection,"
`test_description.py:145-148`) — it is not a general "frames don't need
inertia" escape hatch, it names exactly the two links that qualify today.
Whatever the manager rules for Q1, each link added to
`MASSLESS_FRAME_LINKS` should carry the same kind of one-line justification
the existing two do (pure TF frame, no physical solid represented), so the
set stays a deliberate enumeration rather than growing into "the set of links
someone didn't want to give geometry to" — the exact rug-sweeping trap
`EXPECTED_LINKS`'s own docstring warns against, applied to this second set.

### Q7 (spotted, not asked for explicitly) — chassis/column clearance test
No existing test would catch a column mechanism that geometrically
intersects `base_chassis_link` (§Q2's clearance concern) the way
`test_solid_links_have_visual_and_collision_geometry`'s chassis/wheel clause
does for the base. Worth a ruling on whether PR3 should add one, following
that test's own relational-not-literal pattern
(`test_description.py:725-738`).
