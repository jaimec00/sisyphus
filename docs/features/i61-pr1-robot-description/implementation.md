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
