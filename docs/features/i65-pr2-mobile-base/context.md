# Context — #65 PR2: mobile base URDF (3-omniwheel holonomic, LeKiwi crib)

Read `docs/features/i65-pr2-mobile-base/status.md` first — Phase 2 already
execute-verified `robot_state_publisher`'s boot/failure behaviour and exit
codes, the LeKiwi vs XLeRobot crib split, the LeKiwi mesh sizes, and the base
kinematics/mount-angle numbers derived from and checked against the LeRobot
driver. This document does not repeat that; it covers the *repo* side: the
harness PR2 extends, stale content to sweep, the test-ratchet/lint machinery,
and precedent (or its absence) for the kind of test PR2 needs to add.

## Acceptance criteria, restated (from issue #65)

1. `xacro` still expands `robot.urdf.xacro`; `check_urdf`/`urdf_parser_py`
   still parse it.
2. Exactly 3 wheel joints, all `continuous`, with the expected names.
3. `EXPECTED_LINKS` grows to include `base_link`, `base_footprint`, and the 3
   wheel links.
4. Model loadable by `robot_state_publisher`.
5. `pixi run test` stays green; the test-count floor ratchets up, not down.
6. Owned paths: `src/robot_description/` only. No `robot_backends` change
   (that's PR6). Column/arms/grippers/camera/MJCF are later PRs.

## 1. The PR1 harness, in detail

Package root: `/home/sisyphus/worktrees/i65-pr2-mobile-base-urdf-3-omniwheel-holonom/src/robot_description/`.

### `test/test_description.py` — the gate PR2 extends

File-level docstring (`test/test_description.py:7-50`) is itself the spec for
how to extend this file — read it in place; summarized touch points below.

Module-level constants (`test/test_description.py:63-80`):
- `EXPECTED_LINKS = {'base_link'}` (`:63`) — **must grow** to
  `{'base_link', 'base_footprint', 'base_left_wheel_link', ...}` (exact wheel
  link names TBD, see Open Questions). This is the one the docstring calls out
  as "the likeliest PR2–PR7 regression" (`:18-22`): a renamed/dropped link
  passes `check_urdf` but fails this set-equality assert.
- `SUBASSEMBLIES = ('base.xacro', 'column.xacro', 'arm.xacro')` (`:68`) — **do
  not touch**; base.xacro stays included, PR2 only fills its contents.
- `FILE_BEARING_TAGS = ('mesh', 'texture')` (`:80`) — already covers
  `<mesh filename="...">`, so if base.xacro imports mesh geometry, no code
  change is needed here; `test_every_asset_reference_resolves` (`:222-251`)
  will pick up every `<mesh>` element in the expansion automatically and
  resolve it through `get_package_share_directory('robot_description')`
  (`:106-127`) — i.e. against the **installed** tree, not the source tree.

Existing test functions (all currently pass on PR1's empty subassemblies —
none of these are to be *replaced*, only extended or joined by new ones):
- `test_share_layout_is_installed` (`:158-176`) — checks `urdf/` and
  `meshes/` dirs exist and the 4 known `.xacro` files are installed. Its
  docstring explicitly says a hypothetical `urdf/wheel.xacro` is **not**
  covered here — that's covered by the expansion itself failing loudly if an
  include is missing (`:162-165`). No change needed unless PR2 adds a
  sibling `.xacro` file under `urdf/` (see Open Questions on macro layout).
- `test_top_level_includes_every_subassembly` (`:178-193`) — unaffected;
  `robot.urdf.xacro` keeps its 3 includes untouched.
- `test_xacro_expands_without_error` (`:196-200`) — unaffected mechanically;
  will simply exercise whatever xacro PR2 writes.
- `test_check_urdf_parses_the_expansion` (`:203-210`) — unaffected
  mechanically; new geometry must still produce a URDF `check_urdf` accepts
  (valid tree, no dangling joint references, etc.).
- `test_link_set_is_exactly_the_expected_links` (`:213-219`) — asserts
  `links == EXPECTED_LINKS`; this is where the new wheel/base_footprint/
  base_link names must line up exactly with whatever `EXPECTED_LINKS` is
  extended to.
- `test_every_asset_reference_resolves` (`:222-251`) — will start doing real
  work the moment base.xacro references a `.stl` (currently the empty-set
  case the docstring calls "load-bearing from that moment").
- `test_robot_is_named` (`:254-257`) — unaffected.

**No wheel-joint-count/type/name assertion exists yet** — issue #65's
"assert exactly 3 wheel joints, all continuous" and "loadable by
robot_state_publisher" are **new test functions PR2 must add**, following the
existing one-function-per-concern style (each test function here asserts one
thing and explains, in its docstring, *why* that thing needs its own
assertion — match that style).

### `setup.py` (`src/robot_description/setup.py`)

- `data_files` (`:29-35`) currently does 4 flat `glob()` tuples, including
  `(os.path.join('share', package_name, 'meshes'), glob('meshes/*'))`
  (`:34`). The comment block immediately above (`:22-28`) already documents
  the **known limit** this trips: `data_files` cannot copy a directory, so
  `meshes/<subdir>/x.stl` fails the *build* with an opaque "can't copy" error.
  Per the issue and the breakdown doc (`docs/design/urdf-mjcf-pr-breakdown.md:66-68`),
  **PR2 is where this becomes an `os.walk`-based install**, written against
  whatever mesh layout PR2 actually lands (flat `meshes/*.stl` vs.
  `meshes/base/*.stl` subdirectories — an Open Question below).
- `description=` (`:40-42`) still reads `'... (4-wheel base + extendable
  column + 2 arms).'` — **stale, in-package, fixable here** (see §2).

### `package.xml` (`src/robot_description/package.xml`)

- `<description>` (`:6`) — same stale "4-wheel base" text as `setup.py`,
  **in-package, fixable here**.
- `test_depend` block (`:19-22`): `ament_index_python`, `urdfdom`,
  `urdfdom_py`, `xacro`. **`robot_state_publisher` is not yet declared here**
  — the manager's Phase 2 provisioning pinned it in `pixi.toml`
  (`ros-jazzy-robot-state-publisher >=3.3.3,<4`) but `package.xml` is a
  separate declaration surface (this is the same two-place pattern PR1 used
  for `xacro`/`urdfdom`/`urdfdom_py`). Adding
  `<test_depend>robot_state_publisher</test_depend>` here, matching the
  existing block's comment (`:16-18`) explaining *why* the test depends on
  each tool, is a clear house-convention touch point, not a design fork.

### `README.md` (`src/robot_description/README.md`)

- Line 3: `Robot description: URDF/Xacro + MJCF (4-wheel base + extendable
  column + 2 arms).` — same stale text, **in-package, fixable here**.
- Lines 10-13 describe `urdf/{base,column,arm}.xacro` as "empty for now;
  geometry lands in PR2 (base)..." and `meshes/` as "empty for now" — both
  need updating once base.xacro is filled in and meshes land.
- Lines 20-29 describe exactly what the gate asserts today; needs a line
  about the new wheel-joint/robot_state_publisher assertions once added.

### The four `.xacro` files

- `urdf/robot.urdf.xacro` (`:1-28`) — top level. Declares `base_link` itself
  (`:26`, comment `:10-13` explains why: it's the assembly's root frame, not
  base.xacro's). **Do not move `base_link`'s declaration into base.xacro** —
  base.xacro instead attaches its own base geometry *to* `base_link` via a
  joint, per this file's own comment and per D27
  (`docs/design/decisions.md:90`, "base geometry attaches *to* it via a
  joint exactly as the column and arms do").
- `urdf/base.xacro` (`:1-9`) — currently just an empty `<robot
  xmlns:xacro=...>` wrapper with a comment naming it "LeKiwi 3-omniwheel
  holonomic base (D26)". This is the file PR2 fills in.
- `urdf/column.xacro`, `urdf/arm.xacro` — untouched by PR2, stay empty.

### `pytest.ini` (`src/robot_description/pytest.ini`)

`addopts = -p no:launch_testing -p no:launch_ros` — these ROS2 pytest
plugins are disabled workspace-wide (see `pytest.ini:1-15`) because they
declare hooks pytest ≥8 rejects. **This package's tests cannot use
`launch_testing` utilities** — any `robot_state_publisher` test must drive
the process directly via `subprocess`/`Popen`, not `launch_testing.actions`.

### `meshes/README.md`

Explains why it exists as a real file rather than `.gitkeep`
(`glob()` skips dotfiles) and that "geometry arrives with the base (PR2)...".
Needs its own update once meshes land, and its glob-skip warning is exactly
why the `os.walk` install rewrite (above) must still install a real, present
`meshes/README.md` if the directory-walk logic changes what governs
installation of that directory.

## 2. Stale "4-wheel base" sweep (full repo)

**Empirically found** via `grep -rn "4-wheel\|four-wheel"` across
`.py/.md/.xml/.xacro/.toml/.cfg/.txt`, excluding `.pixi/`:

In-package (PR2 owns, fixable here):
- `src/robot_description/setup.py:42` — `description=` kwarg.
- `src/robot_description/package.xml:6` — `<description>` element.
- `src/robot_description/README.md:3` — first line.

Out of scope (report only, do not touch — outside `src/robot_description/`):
- `src/robot_brain/robot_brain/openclaw/AGENTS.md:3` — the OpenClaw system
  prompt literally says "You are the brain of a household mobile
  manipulator: **a four-wheel base**, an extendable vertical column and two
  arms with grippers." This is a live LLM-brain prompt, not a doc; it is
  stale against D26 but editing `robot_brain` is out of this PR's owned
  paths (`src/robot_description/` only, per the issue). Worth a follow-up.
- `docs/design/decisions.md:7,76,78,84` — D1's original "4-wheel base" text
  and D26's references to it. **Correctly historical** — `decisions.md` is
  append-only (per `CLAUDE.md`: "Why: docs/design/decisions.md ... the
  append-only source of truth"); D26 explicitly supersedes D1's wheel count
  in the same lines. Not stale, not to be edited.
- `docs/design/spec.md:29` — `**Base** | ... | D26 (supersedes D1's
  4-wheel)` — correctly phrased as history, not current state. Not stale.

No other hits for "4 wheel" (space-separated) or "4-wheel" anywhere else in
tracked source/docs.

## 3. Test-count ratchet / D28 machinery

`scripts/check_test_integrity.py` and `scripts/test_baseline.json` — read in
full; summary for the implementer:

- Current baseline for `robot_description`:
  `scripts/test_baseline.json` → `"robot_description": 7` — matching the 7
  non-linter test functions in `test_description.py` today (`test_share_layout_is_installed`,
  `test_top_level_includes_every_subassembly`, `test_xacro_expands_without_error`,
  `test_check_urdf_parses_the_expansion`, `test_link_set_is_exactly_the_expected_links`,
  `test_every_asset_reference_resolves`, `test_robot_is_named`).
- The floor is **self-maintaining, one direction** (D28,
  `docs/design/decisions.md:96-100`): a full `pixi run test` run rewrites a
  package's entry **up** to whatever non-linter test count it just produced,
  automatically, no `--update-baseline` needed. Going down needs
  `ALLOW_TEST_DECREASE=1` or `--allow-decrease` — irrelevant here since PR2
  only adds tests.
- **Nothing manual is required of the implementer** beyond writing and
  committing new tests, then running `pixi run test` once and committing the
  resulting `scripts/test_baseline.json` diff (the ratchet writes it, the
  implementer commits it — same pattern the D28 decision text describes for
  the PR that introduced the rule).
- `find_implementation_modules` (`scripts/check_test_integrity.py:395-423`)
  — **empirically checked**: `src/robot_description/robot_description/__init__.py`
  is empty (confirmed by reading it), so `robot_description` currently has
  **no implementation modules** and is exempt from the "implementation code
  needs a real (non-linter) test" rule regardless. PR2 adds only `.xacro`
  and (likely) `.stl`/mesh files plus new `test_*.py` functions — no new
  `.py` implementation module — so this rule stays inert unless the
  implementer adds Python code to the package (not expected by the brief).

## 4. Linting constraints

- **flake8**: config at
  `.pixi/envs/default/lib/python3.12/site-packages/ament_flake8/configuration/ament_flake8.ini`
  (empirically read) —
  `max-line-length = 99`, `extend-ignore = B902,C816,D100,D101,D102,D103,D104,D105,D106,D107,D203,D212,D404,I202`,
  `import-order-style = google`. flake8 only lints `.py` files (confirmed:
  `ament_flake8` invokes stock `flake8`, which by construction only walks
  Python source) — **`.xacro`/`.urdf`/`.xml` files are never flake8-linted**.
- **pep257**: `test/test_pep257.py:23` runs with `--add-ignore D213`
  (docstring starts on line 1, matching PEP 257, per the module docstring
  `:9-13`). Combined with flake8's own `D203`/`D212` ignores. Applies only to
  any new `.py` files (e.g. a new test module).
- **copyright**: `test/test_copyright.py` runs `ament_copyright.main.main()`
  with default args. **Empirically read**
  (`.pixi/envs/default/lib/python3.12/site-packages/ament_copyright/main.py:40-44`):
  the extensions it scans are
  `['c', 'cc', 'cpp', 'cxx', 'h', 'hh', 'hpp', 'hxx', 'cmake', 'py']` —
  **`.xacro`/`.urdf`/`.xml`/`.stl` files are never copyright-linted.** Only a
  new `.py` file (e.g. a `test_robot_state_publisher.py`-style module) needs
  a header. Exact header to copy verbatim, from
  `src/robot_description/test/test_flake8.py:1-5`:

  ```python
  # Copyright (c) 2026 Jaime C.
  #
  # Use of this source code is governed by an MIT-style
  # license that can be found in the LICENSE file or at
  # https://opensource.org/licenses/MIT.
  ```

## 5. Pattern for a subprocess/node test — none in this exact shape exists

Searched the whole repo (`grep -rl "Popen\|\.terminate()\|TimeoutExpired\|\.poll()"`)
— **no file uses `Popen`, `.terminate()`, `TimeoutExpired`, or `.poll()`
anywhere in `src/` or `scripts/`.** The closest precedents are all
short-lived `subprocess.run(..., timeout=N, capture_output=True)` calls that
wait for the process to *exit* on its own:
- `src/robot_brain/test/test_openclaw_validates.py:130-137` and `:154-159` —
  `subprocess.run([...], capture_output=True, text=True, timeout=180,
  check=False)` against `openclaw config validate` (a command that exits).
- `scripts/tests/test_boot_smoke.py:139-159` (`run_launcher`) — same
  short-run pattern with `timeout=60`.
- `scripts/tests/test_boot_smoke.py:162-179` (`handshake`) — the one
  **long-running-process** case in the repo, but it goes through the MCP
  `stdio_client`/`ClientSession` async context manager (`anyio.fail_after`
  budget, `:172-177`), which cleanly tears the child down on `__aexit__`.
  That machinery is MCP-protocol-specific (JSON-RPC over stdio) and does not
  apply to `robot_state_publisher`, which speaks no protocol on stdout at
  all — it just logs.

**Net: PR2's `robot_state_publisher` test is genuinely new territory for
this repo's test style**, not an existing pattern to copy. Per
`status.md`'s Phase 2 findings, the shape needed is: start the node (its
binary is not on `PATH`; launch via `ros2 run robot_state_publisher
robot_state_publisher` per `status.md:41-43`), pass the expanded URDF via
`--ros-args -p robot_description:="<xml>"`, then either see the `Robot
initialized` log line (success — terminate the process) or see it exit on
its own (failure — report rc + captured output). This has to be built from
`subprocess.Popen` + reading stdout/stderr incrementally with a timeout +
`.terminate()`/`.kill()`, since nothing in-repo already does this shape.

## 6. `os.walk`-style nested `data_files` install — no precedent anywhere

Checked every `setup.py` in the workspace
(`find src -name setup.py | xargs grep -l data_files`) — every other
`ament_python` package (`robot_backends`, `robot_brain`, `robot_bringup`,
`robot_mcp`, `robot_safety`, `robot_skills`, `robot_world`) either ships no
`data_files` at all or uses the same flat-`glob()` pattern
`src/robot_description/setup.py:29-35` already uses. **No package in this
repo installs a nested data directory via `data_files` today.** PR2 is
the first, exactly as the breakdown doc and D27 anticipate
(`docs/design/urdf-mjcf-pr-breakdown.md:66-68`,
`docs/design/decisions.md:90` last two sentences). The implementer has no
in-repo pattern to crib from for the `os.walk` version and will be writing
it from scratch — the shape is standard (`os.walk('meshes')`, one
`data_files` tuple per directory, mirroring the source tree under
`share/robot_description/meshes/`), but nothing here has done it before.

## 7. What PR2 must not disturb in `robot_backends`

**Empirically checked**:
`grep -rl "wheel\|base_link\|base_radius" src/robot_backends --include="*.py"`
returns **nothing**. `RobotModel`
(`src/robot_backends/robot_backends/mock_world.py:69` onward) carries only
`shoulder_offset_y`, `shoulder_offset_z`, `reach_radius`,
`home_gripper_offset`, `min_column_height`, `max_column_height` — no base/
wheel-related field exists at all today. This confirms the issue's "no
`robot_backends` runtime change (that's PR6)" is not just a scope
instruction but already the actual state of the coupling: **there is
nothing in `robot_backends` that currently reads or depends on base
geometry**, so PR2 cannot accidentally break it by construction. PR6 (per
`docs/design/urdf-mjcf-pr-breakdown.md:113-121`) is the one that will
eventually make `RobotModel` read the 7 constants (column/arm-only today;
base kinematics were never on that list) from the URDF via a golden-value
test. Stay out of `robot_backends/` entirely.

## Open questions

1. **Wheel link/joint naming.** `status.md` (execute-verified) records the
   LeKiwi driver's actuated joint names as `base_left_wheel`,
   `base_back_wheel`, `base_right_wheel` (all `continuous`), taken from
   `SIGRobotics-UIUC/LeKiwi`'s `URDF/JOINT_NAMES.md` because they match the
   LeRobot driver's motor names. But the *link* names those joints connect
   to are not settled anywhere read so far — LeKiwi's raw URDF names them
   through a `drive_motor_mount → ST3215_Servo_Motor → omni_wheel_mount`
   chain of intermediate links per wheel (per `status.md`, "a raw CAD
   export ... no parameters at all"), which is not a link-set shape PR2
   should copy verbatim. Does `EXPECTED_LINKS` want exactly one link per
   wheel (e.g. `base_left_wheel_link`) directly on the `continuous` joint,
   or does it need to preserve some intermediate mount-link structure
   nearer to the crib? The issue text ("3 omniwheel links at 120° spacing")
   reads like one link per wheel is intended, but this needs a ruling since
   it fixes `EXPECTED_LINKS`'s exact contents, which the harness then holds
   as a strict set.
2. **Mesh sourcing and size.** `status.md` records the LeKiwi omniwheel STL
   as 15 MB, referenced three times, plus a 461 KB base plate — i.e.
   PR2 committing the literal LeKiwi meshes could add tens of MB to this
   git repo. No git-lfs or asset-size policy exists anywhere in this repo
   (checked: no `.gitattributes`, no LFS config). Does PR2 (a) vendor the
   actual LeKiwi STLs (cribbed as visual geometry, matching the issue's "3
   omniwheel links ... cribbed from the LeKiwi/XLeRobot base URDF" and the
   breakdown's "PR2 imports the first real mesh set"), (b) author simpler
   primitive collision/visual geometry (cylinders for wheels, a box/cylinder
   for the base plate) and defer real meshes to a later cleanup PR, or (c)
   something else (decimated/simplified meshes)? This decides both
   `base.xacro`'s content and whether the `os.walk` install-rewrite (§6) is
   exercised by this PR at all — a primitives-only base wouldn't need it
   yet, contradicting the breakdown doc's expectation that PR2 is where
   that rewrite happens.
3. **Mesh licensing/attribution.** LeKiwi is Apache-2.0
   (`status.md`); this repo is MIT (`LICENSE`, `package.xml:8`), with no
   `NOTICE`/`THIRD_PARTY`/attribution file convention anywhere in the repo
   today (checked: none exists). If literal LeKiwi mesh files are vendored
   (Open Question 2, option a), does this PR need to add an attribution
   file, a per-file header, or a note in `meshes/README.md`? Nothing in
   `decisions.md`/`spec.md` addresses this despite D26 committing to
   cribbing LeKiwi/XLeRobot files directly.
4. **`base_footprint` semantics.** The issue asks for "a `base_footprint`
   frame" alongside `base_link`. REP-105/120 convention (ROS-wide, not
   specific to this repo — no reference to `base_footprint` exists anywhere
   in this repo today) is a link projected onto the ground plane, joined to
   `base_link` by a fixed joint with a z-offset equal to wheel radius (or
   ride height). Is that the intended relationship (`base_footprint` as
   ground projection, connected via a `fixed` joint from `base_link`), or
   is `base_footprint` meant as the URDF's actual root (with `base_link`
   floating above it) — the more common REP-120 layout, which would be a
   bigger structural change than "add a frame" and would also change what
   `robot.urdf.xacro`'s existing root-frame comment (`:10-13`, "`base_link`
   ... is the robot's root frame") means. Nothing read so far settles which
   frame is root.
5. **Macro layout — one `base.xacro` file or a `urdf/wheel.xacro` macro
   included from it.** The issue's "cribbed from the LeKiwi/XLeRobot base
   URDF" plus "3 omniwheel links ... with continuous joints" could be
   authored as one flat `base.xacro`, or as a `xacro:macro` for a single
   wheel instantiated three times (mirroring the arm's planned
   macro-per-side pattern in PR4, `urdf/arm.xacro:1-9`,
   `docs/design/urdf-mjcf-pr-breakdown.md:92-98`). A macro-per-wheel
   approach may want its own file under `urdf/`, which `SUBASSEMBLIES`
   (`test/test_description.py:68`) and `test_share_layout_is_installed`
   (`:158-176`, whose docstring already anticipates and excuses exactly
   this case) do not require registering, but which affects how readable/
   reusable the geometry is. No strong signal either way in the repo; a
   style call for the manager.
