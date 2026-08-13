# Implementation — #61 PR1: `robot_description` package + CI expand/parse gate

Branch: `feat/i61-pr1-robot-description-package-ci-expand`. Base: `origin/main` @ 5fb6f21.

## What shipped

Six commits, each green at the point it was made:

| commit | contents |
| --- | --- |
| `1da3200` | `ops:` — the Phase-2 `pixi.toml`/`pixi.lock` dependency lines (`ros-jazzy-xacro`, `ros-jazzy-urdfdom-py`), committed as-found. `pixi add` was **not** re-run (R14). |
| `48bfb90` | `docs(i61):` — the feature working docs (`context.md`, `status.md`) as authored by the context pass / manager. |
| `0b2ed14` | `robot_description:` — `urdf/` + `meshes/`, the `share/` install wiring, `package.xml` deps. |
| `3d41c23` | `robot_description:` — `test/test_description.py`, the expand/parse/link-set gate. |
| `cb73a48` | `test:` — `scripts/test_baseline.json` ratcheted `robot_description: 0 → 5`. |
| `c704abb` | `docs:` — D27 in `docs/design/decisions.md`. |

Files, against the definition of done:

- **`src/robot_description/urdf/robot.urdf.xacro`** — `<robot name="sisyphus">` (R12), declares `<link name="base_link"/>` at the top level (R5), includes `base.xacro` / `column.xacro` / `arm.xacro` with **relative** filenames (R6).
- **`src/robot_description/urdf/{base,column,arm}.xacro`** — genuinely empty (`<robot xmlns:xacro=…></robot>` + a comment naming which PR fills them and which D26 component they model).
- **`src/robot_description/meshes/README.md`** — R2's real note rather than a `.gitkeep`, and it says in-file *why* (`glob()` skips dotfiles, so a `.gitkeep` would leave the installed dir missing and the layout assert red).
- **`src/robot_description/setup.py`** — `data_files` gains `(share/robot_description/urdf, glob('urdf/*'))` and the `meshes` equivalent. Globbed, never hand-listed (R2), with a comment giving the D24 reasoning.
- **`src/robot_description/package.xml`** — `<depend>rclpy</depend>` dropped (R7); `<exec_depend>xacro</exec_depend>`; `<test_depend>` for `urdfdom`, `urdfdom_py`, `xacro`, `ament_index_python`. rosdep/ROS spellings, never `ros-jazzy-*` (R7).
- **`src/robot_description/test/test_description.py`** — five tests (R3/R4/R13), see below.
- **`src/robot_description/README.md`** — layout, the by-hand expand command, and what the gate does.
- **`scripts/test_baseline.json`** — one entry moved (R9); see "The baseline" below.
- **`docs/design/decisions.md`** — D27 (R10), citing D23's actual `RobotModel` sentence and D26's "one URDF actuator model, one MJCF, one bus" — not the issue's "URDF-as-source" mislabel (R11).

Untouched, as required (R14): `src/robot_backends/**` (`git diff --name-only origin/main...HEAD | grep -c robot_backends` → `0`), `scripts/check_test_integrity.py`, the `[project]`→`[workspace]` pixi warning.

`src/robot_description/robot_description/__init__.py`, `resource/`, `setup.cfg`, `pytest.ini` and the three linter tests were all kept as-is (R8).

## The gate

`test/test_description.py`, five tests, each a distinct failure mode:

1. `test_share_layout_is_installed` — `share/robot_description/{urdf,meshes}` exist and all four `.xacro` files are in the installed `urdf/`.
2. `test_xacro_expands_without_error` — the `xacro` **CLI** via `subprocess`, `rc == 0` (R4), plus a non-empty-stdout assert so a silent empty expansion cannot pass this one.
3. `test_check_urdf_parses_the_expansion` — the `check_urdf` **CLI** on the expansion written to a `tmp_path_factory` file, `rc == 0`.
4. `test_link_set_is_exactly_the_expected_links` — `URDF.from_xml_string(...)`, `{link.name for link in robot.links} == {'base_link'}`, via a module-level `EXPECTED_LINKS` constant the later PRs extend.
5. `test_robot_is_named` — `robot.name == 'sisyphus'`. **This one is beyond R4's four**; it costs nothing and pins R12, which is otherwise asserted nowhere.

All paths resolve through `get_package_share_directory('robot_description')`. **No source-tree fallback** (R3) — the module docstring states that plainly, along with the consequence (this suite needs a `colcon build` first; `colcon test` supplies `AMENT_PREFIX_PATH`).

R3 held up in practice: no `PackageNotFoundError`, no flakiness across ~8 `colcon test` invocations, and the perturbation below shows it is doing real work.

### Perturbation evidence (I broke it on purpose, four ways)

Each perturbation was applied to a **committed** file, run, then reverted with `git checkout --`; `git status --short src/robot_description` was clean afterwards each time.

| perturbation | tests that failed |
| --- | --- |
| extra `<link name="chassis"/>` in `base.xacro` | `test_check_urdf_parses_the_expansion`, `test_link_set_is_exactly_the_expected_links` |
| **duplicate** `<link name="base_link"/>` in `base.xacro` (well-formed XML, invalid URDF) | `test_check_urdf_parses_the_expansion` **only** |
| unclosed `<link name="oops">` in `column.xacro` | expand, check_urdf, link-set, robot-name (4) |
| `glob('urdf/*')` deleted from `setup.py` `data_files`, then rebuilt | all 5 |

The second row is the interesting one and is the empirical justification for R4's "three tools": `urdf_parser_py` happily returns a link list with `base_link` twice, whose *set* still equals `{'base_link'}`, so the link-set assert passes and only `check_urdf` catches it. The fourth row is the empirical justification for R3: with a `__file__`-relative fallback, a broken install would have gone unnoticed.

## Things I found questionable

Nothing rose to escalation, but three are worth the manager's eye.

1. **R9 vs. reality: a plain `--update-baseline` would have moved a second package.** `robot_world`'s live non-linter count is **61** against a floor of **50** (`+11` in every audit table) — its baseline was never re-cut when #60 landed. A whole-workspace `--update-baseline` writes *every* package's live count (`_update_baseline`, `check_test_integrity.py:789-790`), so it would have silently ratcheted `robot_world` 50 → 61 inside this PR. R9 says stop and escalate if that happens, so I did neither the blanket re-cut nor a hand-edit: I used the supported narrow form, `python scripts/check_test_integrity.py --packages-select robot_description --update-baseline`, which leaves other entries alone. The committed diff is exactly one line (`"robot_description": 0` → `5`). **Surfacing, not fixing: `robot_world`'s floor on `main` is 11 tests below its true count**, i.e. 11 of its tests could be deleted today without the ratchet noticing. That is a follow-up for the manager to route, not this PR's to ratchet.
2. **R4's "three gates, three tools" is right, but `check_urdf`'s value is narrower than it reads.** On the PR1 description it is nearly a no-op (a single free link), and for an extra *disconnected* link it fires only because urdfdom rejects a forest. Its real load-bearing case is the duplicate-name row above. Kept exactly as ruled — the note is only that PR2+ should not read a green `check_urdf` as "the kinematics are sane".
3. **`--symlink-install` makes the install tree a symlink chain to source** (`install/…/urdf/x.xacro → build/…/urdf/x.xacro → src/…/urdf/x.xacro`). So R3's "the gate verifies the install wiring" is true for *file registration* (a new `.xacro` not picked up by the glob until a rebuild → caught) but not for *content staleness* (editing an existing `.xacro` is live, no rebuild needed). That is the desirable direction of both errors, and it is what makes the row-4 perturbation fail loudly. Noted so nobody later assumes the gate proves a `colcon build` happened.

Deviations from the rulings: **none**. Additions beyond them: the fifth test (`test_robot_is_named`), `<test_depend>ament_index_python</test_depend>` and `<test_depend>xacro</test_depend>` in `package.xml` (R7 named exec `xacro` + the urdfdom test deps; the test also imports `ament_index_python` and shells out to `xacro`, so declaring them keeps the manifest honest), and the package `README.md` refresh (it still said "Status: skeleton").

## A real bug the gate caught on its first run

The first version of the four `.xacro` files did not expand: `XML parsing error: not well-formed (invalid token): line 3, column 13`. Cause — **XML comments may not contain `--`**, and the house prose style uses `--` as an em dash, which I had carried into the comment headers. All comment text was rewritten to avoid `--`. Worth knowing for PR2–PR7, whose authors will write the same comment style into `.xacro` files: `<!-- ... -- ... -->` is a hard parse error, not a warning.

## Commands run, with real output

```
$ pixi run build
Summary: 9 packages finished [10.2s]

$ pixi run --frozen xacro install/robot_description/share/robot_description/urdf/robot.urdf.xacro
xacro rc=0
<robot name="sisyphus">
  <!-- Root frame of the whole robot. Everything else hangs off this. -->
  <link name="base_link"/>
</robot>

$ pixi run --frozen check_urdf /tmp/expanded.urdf
robot name is: sisyphus
---------- Successfully Parsed XML ---------------
root Link: base_link has 0 child(ren)

$ pixi run --frozen python scripts/check_test_integrity.py --packages-select robot_description --update-baseline
package            tests  skipped  errors  failures  non-lint  vs-base  status
robot_description      8        0       0         0         5       +5  ok
baseline robot_description: 0 -> 5
wrote .../scripts/test_baseline.json (1 package(s) changed); commit it
All stages passed.

$ pixi run test          # final, whole workspace, after every commit
Summary: 710 tests, 0 errors, 0 failures, 0 skipped
package             tests  skipped  errors  failures  non-lint  vs-base  status
_workspace_tooling    129        0       0         0       126       +0  ok
robot_backends         77        0       0         0        74       +0  ok
robot_brain            53        0       0         0        50       +0  ok
robot_bringup           3        0       0         0         0       +0  ok
robot_description       8        0       0         0         5       +0  ok
robot_mcp              85        0       0         0        82       +0  ok
robot_perception        3        0       0         0         0       +0  ok
robot_safety          179        0       0         0       176       +0  ok
robot_skills          109        0       0         0       106       +0  ok
robot_world            64        0       0         0        61      +11  ok
10 packages, 710 tests collected, 680 of them non-linter
AUDIT PASSED: every expected package collected tests
All stages passed.
```

`robot_description` went 3 tests (all linter) → 8 (3 linter + 5 real). Workspace total 705 → 710.

## What I deliberately left for PR2+

- **All geometry.** The three subassembly files are empty; no links, joints, limits, inertials, materials or meshes. `meshes/` is empty but installed.
- **`EXPECTED_LINKS` stays exact.** PR2+ must extend the constant when it adds links; that is the intended friction, and the module docstring says so.
- **No launch file, no `robot_state_publisher`, no `robot_description` parameter.** `robot_bringup` is still a skeleton and this PR does not touch it, so nothing yet *consumes* the description at runtime — the gate is currently its only consumer.
- **No MJCF / MuJoCo anything** (PR7), and no `RobotModel`-from-URDF migration — D23 anticipates it, the issue forbids touching `robot_backends`, and there is no coupling to break today.
- **No joint/limit assertions** in the gate. The right time to add them is when the first joint exists (PR2).

## Honest caveats

- The gate has only ever been exercised against a **one-link** description. That it scales sensibly to a hundred links is a design belief, not a measurement.
- Windows/macOS path handling is irrelevant here but untested; likewise `linux-aarch64` (the Pi) — the description is expanded on the laptop only, and nothing on the Pi consumes it yet.
- `<exec_depend>xacro</exec_depend>` and friends are **declarative only** in this workspace: pixi provides the packages, and nothing in `pixi run build`/`test` resolves rosdep keys. So a typo'd key would not fail any check here. I used the standard ROS spellings (`xacro`, `urdfdom`, `urdfdom_py`, `ament_index_python`), all of which exist as real ROS package names, but that is by inspection rather than by a passing check.
- The empty subassembly files contribute nothing to the expansion today, so "the includes resolve" is exercised, while "the includes contribute content" is not — the first PR that puts a link in `base.xacro` is what proves that end of it.

---

# Round 2 — red-team fixes

Round-1 red-team found 3 BLOCKs, all VERIFIED. I reproduced every one before
fixing it, and re-broke each fix afterwards to confirm it bites. Four commits:

| commit | fixes |
| --- | --- |
| `2c975c9` | B3 — `ros-jazzy-urdfdom` pinned in pixi |
| `688b8e4` | B1 + B2 + N4 + the N1 docstring half |
| `5bb00f6` | N1 (D27) + N3 (D27 + `setup.py`) |
| (baseline) | `robot_description: 5 → 7` |

I disagree with none of the findings. All three BLOCKs were real, and B2 in
particular found a criterion straight out of the issue body that I had shipped
untested — `test_share_layout_is_installed` checks that `arm.xacro` is *on
disk*, which I had let stand in for it being *in the robot*. Those are
different properties and I conflated them.

## B3 — `check_urdf` was not provisioned

The red-team is right about the provenance, and I confirmed it independently
before changing anything:

```
$ grep -rl "bin/check_urdf" .pixi/envs/default/conda-meta/*.json
.pixi/envs/default/conda-meta/urdfdom-6.0.0-h8631160_0.json
```

So the binary is owned by conda `urdfdom`, and the pinned `ros-jazzy-urdfdom-py`
does not depend on it. `pixi add ros-jazzy-urdfdom` → `✔ Added
ros-jazzy-urdfdom >=6.0,<7`, landing in `pixi.toml` and in `pixi.lock` for both
`linux-64` and `linux-aarch64`. The edge that now carries `check_urdf` is
explicit and version-pinned:

```
$ # ros-jazzy-urdfdom-6.0-py312h24bf083_18.json depends:
['console_bridge', 'python', 'ros-jazzy-console-bridge-vendor',
 'ros-jazzy-ros-workspace', 'ros-jazzy-tinyxml2-vendor',
 'ros-jazzy-urdfdom-headers', 'ros2-distro-mutex 0.15.* jazzy_*',
 'tinyxml2', 'urdfdom >=6.0,<6.1a0', ...]
```

Note the honest limit: this pins the ROS wrapper, which pins `urdfdom
>=6.0,<6.1a0`, which owns the binary — one declared hop, not zero. Pinning
conda-forge `urdfdom` directly would be zero hops but mixes channels for a
package RoboStack already wraps, and the ROS spelling matches the two lines
above it and the `<test_depend>urdfdom</test_depend>` rosdep key. I took the
manager's instruction as written; flagging the hop so nobody later reads this
as "the binary is pinned directly".

`_require_tool`'s message was also corrected — it claimed the tool was
"provided by the pixi environment", which was precisely the false part.

## B1 — meshes

`test_every_mesh_reference_resolves` walks every `<mesh>` element in the
expansion (via `ElementTree`, not `urdf_parser_py`, so it sees references
anywhere in the document rather than only those the URDF model object exposes),
resolves `package://<pkg>/<rel>` through `get_package_share_directory(pkg)`,
plus `file://`, absolute, and share-relative forms, and asserts each is a file.

Verified both directions — a test that can only fail is not a test:

```
### E3: exact red-team repro (missing meshes on base_link, nothing else changed)
   before: 5 passed
   after:  10 tests, 1 failure -- test_every_mesh_reference_resolves only
   AssertionError: 2 of 2 mesh reference(s) do not resolve to a file in the
   installed share tree (...): ['package://robot_description/meshes/
   i_do_not_exist.stl -> .../install/robot_description/share/robot_description/
   meshes/i_do_not_exist.stl', ...]

### E2 (positive control): base_link referencing meshes/README.md, which IS installed
   10 tests, 0 failures
```

## B2 — subassembly includes

`test_top_level_includes_every_subassembly` parses the **unexpanded**
`robot.urdf.xacro` (expansion is what erases the includes) and asserts the set
of `{xacro}include` `filename`s equals `set(SUBASSEMBLIES)` — the same tuple
`test_share_layout_is_installed` uses, so the two cannot drift.

```
### I: all three <xacro:include> lines deleted
   before: 5 passed
   after:  10 tests, 1 failure -- test_top_level_includes_every_subassembly only
```

Equality, not membership: an *unexpected* include is as much a drift signal as
a missing one, and it makes the tuple a wiring contract in both directions.

## N1 — the false claim, fixed in both permanent places

The module docstring and D27 now say what the perturbation actually shows: the
exact-link-set assert's unique catch is a **renamed or silently dropped** link
(`base_link` → `base` is valid URDF that `check_urdf` passes), *not* a
degenerate expansion — `check_urdf` rejects a zero-link model too. I re-ran the
red-team's perturbation C to confirm before rewriting. D27's clause is now
scoped as "four tools plus two wiring asserts" and records the mesh/include
blind spots and the provisioning gap, so the permanent record describes the
gate that shipped rather than the one I first wrote.

## N3 — flat globs

No code change, per the ruling. `setup.py` and D27 now state that the globs are
flat, that `data_files` cannot copy a directory, that the first
`meshes/<subdir>/x.stl` therefore fails the **build** with `can't copy …:
doesn't exist or not a regular file`, and that PR2 is where the `os.walk`
version gets written against real files. I agree with not pre-solving it: the
directory layout of an imported LeRobot mesh set is exactly the thing I would
have guessed wrong.

## N4 — cascade legibility

All parse-based tests now route through `_require_expansion()`, which asserts
`rc == 0` and points at the root cause. Before/after on the malformed-XML
perturbation:

```
before: FAILED test_link_set_... -   File "<string>", line 1
        lxml.etree.XMLSyntaxError: Document is empty, line 1, column 1
after:  AssertionError: xacro expansion failed (rc 2), so this assertion never
        ran -- see test_xacro_expands_without_error for the root cause.
          XML parsing error: mismatched tag: line 11, column 2
```

Kept as assertion failures rather than a fixture error, deliberately: a fixture
that raises turns five failures into five *errors*, and the audit table reports
those in a different column.

## Round-2 commands, real output

```
$ pixi add ros-jazzy-urdfdom
✔ Added ros-jazzy-urdfdom >=6.0,<7

$ pixi run --frozen build
Summary: 9 packages finished [9.99s]

$ pixi run --frozen python scripts/check_test_integrity.py \
      --packages-select robot_description --update-baseline
package            tests  skipped  errors  failures  non-lint  vs-base  status
robot_description     10        0       0         0         7       +2  ok
baseline robot_description: 5 -> 7
wrote .../scripts/test_baseline.json (1 package(s) changed); commit it

$ git diff --numstat scripts/test_baseline.json
1	1	scripts/test_baseline.json          # still exactly one line

$ pixi run test
Summary: 712 tests, 0 errors, 0 failures, 0 skipped
package             tests  skipped  errors  failures  non-lint  vs-base  status
_workspace_tooling    129        0       0         0       126       +0  ok
robot_backends         77        0       0         0        74       +0  ok
robot_brain            53        0       0         0        50       +0  ok
robot_bringup           3        0       0         0         0       +0  ok
robot_description      10        0       0         0         7       +0  ok
robot_mcp              85        0       0         0        82       +0  ok
robot_perception        3        0       0         0         0       +0  ok
robot_safety          179        0       0         0       176       +0  ok
robot_skills          109        0       0         0       106       +0  ok
robot_world            64        0       0         0        61      +11  ok
10 packages, 712 tests collected, 682 of them non-linter
AUDIT PASSED: every expected package collected tests
All stages passed.
```

Every perturbation ran on a committed file and was reverted with `git checkout
--`; `git status --short src/robot_description` was checked clean after each
batch.

## Disagreements and surviving caveats

- **None with the findings.** The one thing I would flag is the B3 hop above
  (ROS wrapper → conda `urdfdom` → binary), which is a pin on the wrapper
  rather than on the package that owns the file.
- **Left alone as instructed**, and still true: N2 (`@pytest.mark.skip` does not
  trip the ratchet — `scripts/check_test_integrity.py`, outside owned paths),
  N5 (xacro's stderr ignored at rc 0 — a description that expands only via
  deprecated syntax still passes), N6 (joint/limit asserts, PR2+), and
  `robot_world`'s floor sitting 11 below its live count on `main`.
- **The mesh test has never seen a real mesh.** Its negative and positive
  controls were synthetic (a fake `.stl` path, and `meshes/README.md` used as a
  stand-in for an installed file). The `package://` and share-relative branches
  are exercised; `file://` and the absolute-path branch are **not** — no test
  input produces them, and I did not invent one, since URDFs in this repo will
  use `package://`.
- **The include test reads the top level only.** A subassembly that includes a
  fourth file is not covered; that is deliberate (PR1 owns the top-level wiring
  contract) but worth knowing before PR4 nests macros.
