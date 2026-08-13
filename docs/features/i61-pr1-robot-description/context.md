# Context — #61 PR1: robot_description package + CI expand/parse gate

Slug: `i61-pr1-robot-description`. Brief: GitHub issue #61 (verbatim in the
dispatch prompt; fetched directly via `gh issue view 61 --json body` and
confirmed identical, no comments yet).

Provisioning is **done and out of scope for this doc** (see
`docs/features/i61-pr1-robot-description/status.md` Phase 2): `pixi.toml` /
`pixi.lock` already carry `ros-jazzy-xacro = ">=2.1.1,<3"` (pixi.toml:29) and
`ros-jazzy-urdfdom-py = ">=1.2.1,<2"` (pixi.toml:30); `xacro`, `check_urdf`,
`xacro.process_file`/`process_doc`, `urdf_parser_py.urdf.URDF.from_xml_string`
were all execute-verified there. Do not re-derive or re-run any of that.

## 1. The ament_python → ament_cmake decision — evidence map

This is the one real design fork in the issue. Below is everything in this
repo's tooling that touches build-type, with concrete evidence for each side.

### 1a. `scripts/check_test_integrity.py` — the test ratchet

**Package discovery is build-type-agnostic.** `find_manifests` (:171-195) walks
`src/` for any `package.xml` and reads `<name>` via `ElementTree` — it never
looks at `<build_type>`/`<buildtool_depend>`. `discover_packages` (:231-256)
decides *ownership* (must the guard demand results) purely from **git
tracking** the `package.xml` path, again build-type-blind. So converting
`robot_description` to `ament_cmake` does **not** need any change to package
discovery, and there is **no hardcoded package list** anywhere in this file —
`scripts/tests/test_audit.py:516-520,631-633` assert the *live* discovery
sees `robot_description` (and every other `robot_*` package) as owned, by
calling `guard.discover_packages(REPO_ROOT / 'src')` against the real tree,
not a fixture. Confirmed the guard's own tests don't special-case build type
either — `grep robot_description` across `scripts/tests/*.py` only turns up
those two membership-set lines.

**Result parsing is also build-type-agnostic, in principle.** `parse_xunit`
(:272-322) reads any file with a `<testsuite>`/`<testsuites>` root and the
`tests`/`failures` attributes colcon's own xunit parser requires — it doesn't
care what produced the XML. `audit_package` (:407-493) looks for result files
under `build_base/<package_name>` (:430, via `find_result_files`, which
`os.walk`s recursively, :388-404) — so as long as colcon puts an
`ament_cmake` package's ctest+JUnit output under `build/<package_name>/...`
(see §1c below — it does, by CMake convention), the mtime-freshness and
zero/all-skipped/below-baseline logic all apply unchanged.

**Where it is NOT build-type-agnostic — `is_linter_case` (:325-336) and
`LINTER_TEST_NAMES` (:86-90).**
```python
LINTER_TEST_NAMES = frozenset({
    'test_copyright', 'test_flake8', 'test_pep257', 'test_mypy',
    'test_xmllint', 'test_lint_cmake', 'test_cppcheck', 'test_cpplint',
    'test_uncrustify',
})
...
def is_linter_case(case):
    name = (case.get('name') or '').split('[')[0]
    module = (case.get('classname') or '').split('.')[-1]
    return name in LINTER_TEST_NAMES or module in LINTER_TEST_NAMES
```
The comment above it (:81-90) says explicitly: *"Every ament linter test is a
single function whose name matches its module's"* — this is true for the
**ament_python** convention this repo uses today: hand-written
`test/test_flake8.py::test_flake8()` etc. (see `src/robot_description/test/
test_flake8.py` — pytest's own JUnit writer sets `classname` to the dotted
module path, so `classname.split('.')[-1]` == `'test_flake8'`, matching the
set).

**It is NOT true for `ament_cmake`'s own linter hooks**
(`ament_lint_auto`/`ament_cmake_{flake8,pep257,copyright,xmllint,lint_cmake}`,
all present in the pixi env — **empirically-observed**, `find
.pixi/envs/default/share -iname '*ament_cmake_pytest*'` etc.). Read their
xunit writers directly:
- `ament_flake8`'s CLI writes `testname = '%s.%s' % (folder_name, file_name)`
  where `folder_name` = the package name and `file_name` is the ctest test
  name with `.xunit.xml`/`.xml` stripped (`ament_flake8/main.py:118-127`), so
  for a package `robot_description` the JUnit `classname` is
  `"robot_description.flake8"` and the passing testcase's `name` is literally
  `"flake8"` (`main.py:342-344`). `classname.split('.')[-1]` == `'flake8'`.
  **`'flake8' not in LINTER_TEST_NAMES`** (which has `'test_flake8'`).
- Same pattern, verified by reading source, for `ament_copyright`
  (`ament_copyright/main.py:205,405-445`, testcase name `"copyright"`) and
  `ament_pep257` (`ament_pep257/main.py:147,234-280`, testcase name
  `"pep257"`).
- `ament_lint_cmake`'s hand-rolled xunit writer uses `classname="%(testname)s"`
  where `testname` defaults to the **bare** ctest name `"lint_cmake"`
  (`ament_lint_cmake/main.py:108-122` reachable via
  `.pixi/envs/default/lib/python3.12/site-packages/ament_lint_cmake/main.py`;
  cmake side default in
  `.pixi/envs/default/share/ament_cmake_lint_cmake/cmake/ament_lint_cmake.cmake:29-34`).
  `'lint_cmake' not in LINTER_TEST_NAMES` (which has `'test_lint_cmake'`).
- `ament_xmllint` follows the same shape (own xunit writer, testname default
  `"xmllint"`; `.pixi/envs/default/share/ament_cmake_xmllint/cmake/
  ament_xmllint.cmake:104-148`).

**So: if `robot_description` becomes `ament_cmake` and wires its linters the
idiomatic way (`find_package(ament_lint_auto REQUIRED)` +
`ament_lint_auto_find_test_dependencies()`, letting the `*_lint_hook.cmake`
files in each `ament_cmake_<linter>` package auto-register), none of the
resulting linter testcases match `is_linter_case`, and they all get counted
as `non_linter` — i.e. they inflate the baseline and, worse, would silently
satisfy `_STATUS_NO_REAL_TESTS` (":481-486, package holds implementation code
but only linter tests") even though nothing about the package's actual URDF
behavior is exercised.** This is source-verified (read, not executed against
a live ament_cmake build in this repo — there is no ament_cmake package
anywhere in this workspace to run the guard against yet) — label
**inferred-from-source**, flagged as Open Question 1 below.

Two secondary observations on the same hooks, also inferred-from-source:
- `ament_cmake_flake8`/`ament_cmake_pep257`'s hooks only fire if
  `file(GLOB_RECURSE *.py)` finds Python files
  (`ament_cmake_flake8/cmake/ament_cmake_flake8_lint_hook.cmake:15-19`,
  `ament_cmake_pep257/.../ament_cmake_pep257_lint_hook.cmake:15-19`) — an
  `ament_cmake` `robot_description` with **no** `.py` files (just
  `.xacro`/`.urdf`) gets **no flake8/pep257 test at all**, registered or not.
- `ament_cmake_copyright`'s hook is unconditional
  (`ament_cmake_copyright_lint_hook.cmake:15-22`, always calls
  `ament_copyright()`).
- `ament_cmake_xmllint`'s hook only fires on `file(GLOB_RECURSE *.xml)`
  (`ament_cmake_xmllint_lint_hook.cmake:15-18`) — `.xacro`/`.urdf` files do
  **not** match `*.xml`, so only `package.xml` itself would be linted unless
  the implementer explicitly calls `ament_xmllint()` on the urdf/xacro tree.

**No hardcoded expected-count table beyond `scripts/test_baseline.json`**,
which already has `"robot_description": 0` (matching today's 3-linter-only
stub). Whoever lands real tests (xacro-expand + check_urdf-parse + link-set
assert) must re-cut it with `python scripts/check_test_integrity.py
--update-baseline` (`check_test_integrity.py:29`, its own docstring) — this is
independent of the ament_python/ament_cmake choice, but the ament_cmake path's
linter-miscounting above means the number that gets baked into the baseline
would be **wrong** (inflated by uncounted linter testcases) unless
`LINTER_TEST_NAMES` is extended first.

### 1b. `scripts/check_provisioning.py`

Nothing package-specific: it only checks the OpenClaw CLI binary
(`OPENCLAW_RELATIVE_PATH`, :54) exists and is executable. No interaction with
`robot_description` or build type at all (confirmed by full read, 150 lines).

### 1c. Where ament_cmake's JUnit lands, and whether colcon/`find_result_files` sees it

`ament_add_pytest_test` (`ament_cmake_pytest/cmake/
ament_add_pytest_test.cmake:89`) writes to
`${AMENT_TEST_RESULTS_DIR}/${PROJECT_NAME}/${testname}.xunit.xml`; the
linter macros (`ament_flake8.cmake:49`, `ament_lint_cmake.cmake:36`, etc.) use
the identical pattern. `AMENT_TEST_RESULTS_DIR` defaults to
`${CMAKE_BINARY_DIR}/test_results` (`ament_cmake_test/cmake/
ament_cmake_test-extras.cmake:23`), and `CMAKE_BINARY_DIR` for a colcon-built
package is `build/<package_name>` by colcon's own per-package build-directory
convention — so the file lands at
`build/robot_description/test_results/robot_description/<testname>.xunit.xml`.
`find_result_files` (`check_test_integrity.py:388-404`) walks
`build_base/<name>` (i.e. `build/robot_description`) **recursively**, so it
reaches that nested path. This chain (colcon → ctest → `AMENT_TEST_RESULTS_DIR`
→ audit) is **inferred-from-source**, not executed — there is no ament_cmake
package anywhere in this workspace yet to build and observe.

### 1d. Per-package `pytest.ini` / `-p no:launch_testing -p no:launch_ros` workaround

Every ament_python package in this repo ships an identical `pytest.ini`
(compare `src/robot_description/pytest.ini`, `src/robot_bringup/pytest.ini` —
same file, same comment block) with `addopts = -p no:launch_testing -p
no:launch_ros` and `testpaths = test`. This is picked up because colcon's
ament_python test step runs pytest with the package root as pytest's rootdir.

For `ament_cmake_pytest`, `ament_add_pytest_test` (:90-100) invokes
`python -m pytest "${path}" -o cache_dir=... --junit-xml=... --junit-prefix=...`
via ctest — **it does not pass `-p no:launch_testing` / `-p no:launch_ros`**,
and the ctest `WORKING_DIRECTORY` defaults to `CMAKE_CURRENT_BINARY_DIR`
(`ament_cmake_test/cmake/ament_add_test.cmake:82-83`), i.e. the **build**
directory, not the package source directory where `pytest.ini` would live.
Whether pytest's ini-discovery (which walks up from the common ancestor of
its *args*, not from cwd) still finds a `pytest.ini` sitting next to the test
file passed as `${path}` is a real question the implementer must verify by
actually building and running — I did not execute this (no ament_cmake
package exists in the tree to try it on). Flagged as Open Question 2 /
gotcha: if the RoboStack `launch_testing`/`launch_ros` pytest-plugin
incompatibility (documented in every existing `pytest.ini`'s docstring) hits
an `ament_cmake_pytest` test the same way it hit ament_python's, the fix is
either an `ament_add_pytest_test(... )` invocation that also disables those
plugins directly, or confirming the ini is still discovered.

### 1e. The existing `robot_description` stub, today

- `src/robot_description/setup.py` — plain ament_python `setup()`, `data_files`
  registers the ament index + `package.xml` only, no `urdf`/`meshes` data
  files yet.
- `src/robot_description/setup.cfg` — script-dir boilerplate, irrelevant to
  ament_cmake (no console scripts declared, entry_points is empty).
- `src/robot_description/package.xml` — `<buildtool_depend>ament_python
  </buildtool_depend>`, `<depend>rclpy</depend>` (a dependency **the actual
  URDF-only package almost certainly does not need** — worth reconsidering
  regardless of the ament_python/ament_cmake call), three `test_depend`s
  (`ament_copyright`, `ament_flake8`, `ament_pep257`) + `python3-pytest`,
  `<export><build_type>ament_python</build_type></export>`.
- `src/robot_description/pytest.ini` — see §1d.
- `src/robot_description/test/{test_copyright,test_flake8,test_pep257}.py` —
  hand-written, each imports the linter's `main`/`main_with_errors` directly
  and asserts `rc == 0`; these are exactly the tests
  `check_test_integrity.py`'s `LINTER_TEST_NAMES` was built to recognize (§1a).
- `src/robot_description/resource/robot_description` — empty marker file for
  the ament index resource, standard ament_python plumbing.
- `src/robot_description/robot_description/__init__.py` — empty; this is the
  importable Python subpackage `find_implementation_modules`
  (`check_test_integrity.py:339-367`) would scan for "implementation code". It
  currently holds nothing, so the package is correctly classified as having
  no implementation and its 3 linter tests are an "honest" suite per the
  guard's own doctrine (:349 comment). **If converting to ament_cmake, this
  whole importable-Python-subpackage layout disappears** (a URDF-only package
  has no Python code at all) — `find_implementation_modules` would then find
  nothing to scan for either build type, so this part is a non-issue; noted
  only so the implementer knows the directory is dropped, not ported.
- `src/robot_description/README.md` — one-liner, "Status: skeleton."

### 1f. House style — every other `src/robot_*` package

All seven other packages (`robot_backends`, `robot_brain`, `robot_bringup`,
`robot_mcp`, `robot_perception`, `robot_safety`, `robot_skills`, `robot_world`)
are **ament_python**, same shape: `setup.py` with `find_packages(exclude=
['test'])` + the two-entry `data_files` (ament index resource +
`share/<pkg>/package.xml`), `setup.cfg` script-dir boilerplate, `package.xml`
with `<buildtool_depend>ament_python</buildtool_depend>` +
`<export><build_type>ament_python</build_type></export>`, the same
`test_depend` triad + `python3-pytest`, and the same `test/test_{copyright,
flake8,pep257}.py` trio (verified by reading `robot_world/setup.py`,
`robot_world/package.xml`, `robot_bringup/setup.py`,
`robot_bringup/package.xml` in full — byte-identical boilerplate modulo
`description`/`depend` lines). **There is no ament_cmake package anywhere in
this repo today** — converting `robot_description` would make it the first
and only one, with no in-repo CMakeLists.txt to crib from. `robot_world`'s
`setup.py` additionally shows the local idiom for shipping data *inside* the
importable package (`package_data`/`include_package_data`, with a comment
explaining why — `src/robot_world/setup.py:9-15`) as a contrast to
`share/`-based `data_files`; not directly applicable to ament_cmake but shows
the kind of design-rationale comment this repo expects in packaging code.

### 1g. `.github/workflows/`, other scripts, bringup/launch references

`.github/workflows/guards.yml` is the **only** workflow file in the repo; it
runs exactly one job, `docs-clean` (`git ls-files 'docs/features/*'` must be
empty) — it does not touch `robot_description`, does not build, does not run
pixi (matches CLAUDE.md's "CI enforces exactly one thing" claim, confirmed by
reading the file in full, 20 lines).

Repo-wide grep for `robot_description` outside `src/robot_description/`
itself turns up only: `README.md:13-14` (package list, prose), this feature's
own `docs/features/i61-pr1-robot-description/status.md`, and the two
`scripts/tests/{test_audit,test_ratchet}.py` membership-set lines already
covered in §1a. **No launch file, no bringup config, no `robot_backends`
module imports or references `robot_description`** — `src/robot_bringup/`
is itself still a skeleton (README + pytest.ini + package.xml + setup.{py,cfg}
+ the 3 linter tests + an empty `__init__.py`, no launch files at all;
verified by `find src/robot_bringup -type f`). `RobotModel` (the in-code
hardware-description dataclass) lives in
`src/robot_backends/robot_backends/mock_world.py:69` and has no URDF/xacro
coupling (`grep -rn "class RobotModel"` finds exactly this one definition;
`grep -l "urdf" src/robot_backends/**/*.py` finds nothing). This confirms the
issue's "does NOT change any `robot_backends` runtime behavior" constraint is
naturally satisfiable — there is no existing coupling to break.

## 2. Test discovery/run chain, end to end

`pixi run test` (`pixi.toml:44`) → `{ cmd = "python scripts/
check_test_integrity.py", depends-on = ["check-provisioning"] }` →
`check_provisioning.py` runs first and hard-fails on a missing OpenClaw CLI
(unrelated to this PR) → `check_test_integrity.py main()` (:803-969):
1. `discover_packages(src/)` — finds `robot_description` (git-tracked
   `package.xml`), appends the `_workspace_tooling` pseudo-package (:859).
2. Deletes stale result files under `build/` (:916-918).
3. Runs `colcon test --base-paths src --build-base build --test-result-base
   build` (:923-929) — colcon dispatches each package's test step per its
   `<build_type>` (`ament_python` today: runs `pytest` per `pytest.ini`'s
   `testpaths`; would become `ctest`-driven for `ament_cmake`, §1c).
4. Runs the workspace-tooling suite (`scripts/tests/`) as its own
   pseudo-package (:694-715).
5. `colcon test-result --all --verbose` (:937-939) — surfaces everything.
6. `audit(packages, build_base, ...)` (:941) — reads the JUnit XML colcon
   already produced (never re-runs tests) and applies the zero-test /
   all-skipped / below-baseline / no-real-tests rules from §1a.

A new test file added under `src/robot_description/test/` (ament_python path)
is picked up automatically via `pytest.ini`'s `testpaths = test` — no
registration needed anywhere else. Under `ament_cmake`, a new pytest test
needs an explicit `ament_add_pytest_test(<name> test/<file>.py)` call in
`CMakeLists.txt` (`ament_cmake_pytest/cmake/
ament_add_pytest_test.cmake:52`) — it is **not** auto-discovered the way
ament_python's `testpaths` glob is.

`test-audit` (`pixi.toml:47`) re-reads existing `build/` results without
re-running anything — useful for iterating on the audit logic alone, not
relevant to first getting `robot_description`'s tests to run.

## 3. D23 / D26 — what they actually say (read `docs/design/decisions.md` directly)

**Correction to the issue text:** the issue says "See `docs/design/
decisions.md` D23 (URDF-as-source)". The actual **D23** in the file
(`decisions.md:51-59`) is titled *"World state is a JSON-file store
(`robot_world`)..."* — it is about `WorldStore`/`FileWorldStore`/JSON
persistence, **not** about URDF. There is no decision literally titled
"URDF-as-source" anywhere in `decisions.md` (`grep -rn "URDF-as-source"
docs/` finds nothing). D23's only URDF-relevant sentence is one bullet:
> "The world file never describes the robot's body. `RobotModel` (shoulder
> offsets, reach, column travel) is hardware description and stays in code,
> later coming from the URDF/MJCF." (`decisions.md:53`)
This establishes that `RobotModel` (currently a plain dataclass in
`robot_backends/mock_world.py:69`) is **expected to eventually be derived
from URDF/MJCF**, but that migration is explicitly **not** this PR's job
(confirmed by the issue's "Does NOT change any `robot_backends` runtime
behavior" constraint and §1g above). Nothing in D23 prescribes a file-layout
or link-naming convention for `robot_description` itself.

**D26** (`decisions.md:76-79`, quoted in full for the parts that bear on
naming/layout):
> "Every actuated joint lives on one Feetech STS3215 / LeRobot bus; base,
> column, and arms are all cribbed from the LeRobot substrate rather than
> authored... the whole robot is one URDF actuator model, one MJCF, one bus."
> "Column — linear-rail STS3215 lift on the arm bus (Nori-style)... modeled
> as **one prismatic joint** in the URDF (this is exactly PR3 of the
> `[[urdf-mjcf-pr-breakdown]]`, limits 0.00–1.20 per D23's `RobotModel`)."

The `[[urdf-mjcf-pr-breakdown]]` is an Obsidian-style wikilink with **no
corresponding file in this repo** (`find docs -iname "*breakdown*"` /
`-iname "*urdf*"` / `-iname "*mjcf*"` all empty except this one line) — it
is presumably Jaime's private planning note, not something in-repo. The
"roadmap #5" the issue cites as this PR's parent does not exist as a GitHub
issue in this repo either — `gh issue list --state all` shows no issue #5
(closest is #54, "World-state store... (roadmap #3)"). Neither gap blocks
PR1 (the issue body is self-contained: PR1 = package + expand/parse/assert
gate, single `base_link`, nothing else), but flag it in case the manager
wants Sisyphus to file/locate the actual roadmap issue.

**No decisions.md entry currently documents the ament_python-vs-ament_cmake
build-type choice this PR settles**, nor the description-package file layout
(`share/{urdf,meshes}`, `robot.urdf.xacro` + `base.xacro`/`column.xacro`/
`arm.xacro`). Whether this PR should add a `D27` (or similar) entry recording
that choice, given every prior structural call in this repo (D9, D13, D15,
D23, D24) got a numbered decision, is Open Question 3.

## 4. Existing URDF/xacro/mesh assets and consumers

None exist anywhere in the repo (`find . -iname "*.urdf*" -o -iname "*.xacro"
-o -iname "*.mjcf" -o -iname "*.stl" -o -iname "*.dae"`, excluding `.pixi`/
`.git`, returns nothing). This PR ships the first ones. No consumer
(`robot_backends`, `robot_bringup`, or anything else) currently loads a robot
description — `robot_bringup` has no launch files yet (§1g), and
`robot_backends`' `RobotModel` is hand-authored Python, not URDF-derived
(D23, §3). PR7 is where `mujoco`/MJCF land per the issue's own "out of
scope" note and per D26's "one MJCF" line — irrelevant to PR1.

## 5. Acceptance criteria restated (from the issue, verbatim scope preserved)

- Build deps `xacro` + `urdfdom`/`urdfdom_py` (or `check_urdf`) added via
  pixi + `package.xml` — **pixi side already done** (pixi.toml:29-30); the
  `package.xml` `<depend>`/`<test_depend>`/`<build_depend>` entries (and
  their exact rosdep-key spelling, e.g. `xacro`, `urdfdom_py` — not the
  conda `ros-jazzy-` names) are still the implementer's job.
- Install layout `share/robot_description/{urdf,meshes}` wired (empty
  `meshes/` presumably, since PR1 is out-of-scope for actual geometry).
- The ament_python-vs-ament_cmake call settled (issue leans ament_cmake;
  see Open Question 1 for the concrete cost of that lean).
- `robot.urdf.xacro` includes empty `base.xacro`, `column.xacro`, `arm.xacro`
  and expands to a single `base_link`.
- New pytest test(s) (wherever they end up registered, ament_python
  `test/`-glob or ament_cmake `ament_add_pytest_test`) that: (a) run `xacro`
  to expand the top-level file with rc 0 (or via `xacro.process_file`,
  execute-verified available), (b) run `check_urdf`/`urdf_parser_py.urdf.URDF.
  from_xml_string` to parse the expanded output, (c) assert the resulting
  link set is exactly `{base_link}`.
- Constraints: `pixi run build` green, xacro-expands, new pytest asserts
  pass, mergeable alone, does not touch `robot_backends` runtime behavior,
  existing suite stays green (which includes re-cutting
  `scripts/test_baseline.json` for `robot_description`'s new non-linter
  count — currently `0`, `scripts/test_baseline.json:6`).

## 6. Owned paths (from the brief)

`src/robot_description/**`, `pixi.toml`/`pixi.lock` (dependency lines only,
already added), `scripts/test_baseline.json` (the `robot_description` entry).
Touching `scripts/check_test_integrity.py` itself (e.g. extending
`LINTER_TEST_NAMES`) would be outside the literal package path but may be
necessary depending on how Open Question 1 resolves — flagged there.

## 7. Likely touch points

- `src/robot_description/{package.xml,setup.py,setup.cfg}` → either edited
  in place (ament_python) or replaced by `package.xml` + `CMakeLists.txt`
  (ament_cmake, `setup.py`/`setup.cfg`/`resource/`/`robot_description/
  __init__.py` deleted).
- `src/robot_description/test/` — keep or replace the 3 linter test files
  depending on the build-type call (§1a: ament_cmake's auto-hooks are not a
  drop-in behavioral replacement for the hand-written versions, given the
  `is_linter_case` mismatch).
- New `src/robot_description/urdf/{robot.urdf.xacro,base.xacro,column.xacro,
  arm.xacro}` (or wherever the implementer places them under `urdf/`) +
  `src/robot_description/meshes/` (likely just a `.gitkeep` or similarly
  empty, since geometry is out of scope).
- `scripts/test_baseline.json` — re-cut via `--update-baseline` once real
  tests exist.
- Possibly `scripts/check_test_integrity.py`'s `LINTER_TEST_NAMES` (Open
  Question 1) — outside the literal owned-paths list, worth the manager's
  explicit sign-off if touched.

## 8. Existing tests/patterns to follow

- `src/robot_description/test/test_{copyright,flake8,pep257}.py` — current
  linter-test pattern (import the linter's `main`, `pytest.mark.linter` +
  specific mark, assert `rc == 0`). If staying ament_python, extend this
  `test/` directory with e.g. `test_expand.py` in the same style: module
  docstring, `pytest.mark`, clear assert messages.
- `src/robot_world/setup.py:9-15` — example of a packaging-code comment
  explaining *why* a data-shipping choice was made; this repo expects that
  register of explanation in `setup.py`/`CMakeLists.txt` too.
- `scripts/check_test_integrity.py`'s own docstring style (dense,
  rationale-first module/function docstrings) is the house style for
  anything touching test infrastructure.

## 9. Known gotchas (recap, all cross-referenced above)

1. `is_linter_case`/`LINTER_TEST_NAMES` does not recognize ament_cmake's own
   linter-hook testcase names (`flake8`, `copyright`, `pep257`, `lint_cmake`,
   `xmllint` vs. the expected `test_flake8` etc.) — §1a.
2. `ament_add_pytest_test` does not pass `-p no:launch_testing -p
   no:launch_ros`, and its ctest working directory is the **build** dir, not
   the package source dir where `pytest.ini` lives — whether pytest still
   discovers the ini is unverified (no ament_cmake package exists yet to
   test against) — §1d.
3. ament_cmake's flake8/pep257 hooks only fire if `*.py` files exist in the
   package; xmllint only fires on `*.xml` (not `*.xacro`/`*.urdf`) — §1a.
4. No ament_cmake package exists anywhere in this repo to crib a
   `CMakeLists.txt` from — §1f.
5. `<depend>rclpy</depend>` in the current `package.xml` (:9) is almost
   certainly dead weight for a URDF-only package regardless of the
   ament_python/ament_cmake call.
6. The issue's "D23 (URDF-as-source)" label doesn't match the real D23
   title/content, and the `[[urdf-mjcf-pr-breakdown]]` / "roadmap #5" it
   cites don't exist as files/issues in this repo — §3.

## 10. Open questions for the manager

1. **Given the `is_linter_case`/`LINTER_TEST_NAMES` mismatch (§1a), how
   should the ament_cmake conversion (if chosen) keep the test-integrity
   guard honest?** Concrete options: (a) extend `LINTER_TEST_NAMES` in
   `scripts/check_test_integrity.py` to also match the bare names
   ament_lint_auto's hooks produce (`flake8`, `copyright`, `pep257`,
   `lint_cmake`, `xmllint`) — touches shared tooling outside
   `robot_description`'s owned paths, needs its own tests in
   `scripts/tests/`; (b) keep the hand-written `test/test_{copyright,flake8,
   pep257}.py` pattern *even under ament_cmake*, wired via explicit
   `ament_add_pytest_test()` calls instead of `ament_lint_auto`'s
   auto-hooks, so the JUnit shape stays pytest's own (which the guard
   already recognizes) — no shared-tooling change, but non-idiomatic
   ament_cmake; (c) accept the miscount for now (it only inflates the
   baseline, it doesn't hide a *missing* test) and file a follow-up. My
   read: (b) is the lowest-risk option for a PR whose explicit purpose is to
   be "the harness PRs 2–7 extend" — it keeps the guard's existing,
   already-tested classification logic correct without touching
   `scripts/check_test_integrity.py`, at the cost of not using
   `ament_lint_auto`'s auto-discovery. But this is a real trade the manager
   should rule on, not something I should decide.
2. **Does the ament_cmake `ament_add_pytest_test` pytest.ini-discovery
   concern (§1d, gotcha 2) need to be resolved before or during
   implementation?** I could not execute-verify it (no ament_cmake package
   in the tree). Recommend the implementer's first move, if ament_cmake is
   chosen, is a throwaway build to observe whether the RoboStack
   `launch_testing`/`launch_ros` plugin-validation crash reappears, before
   writing the real xacro/urdf test.
3. **Should this PR add a numbered decision entry** (e.g. `D27`) to
   `docs/design/decisions.md` recording the ament_python-vs-ament_cmake call
   and the `share/{urdf,meshes}` layout, matching every other structural
   call in this repo's history (D9, D13, D15, D23, D24)? The issue doesn't
   ask for one explicitly, but the pattern is otherwise universal for
   "sub-decisions settled up front."
4. **Is the `robot_description` → `<depend>rclpy</depend>` line
   (`package.xml:9`) meant to be dropped?** It looks like a copy-paste
   leftover from the shared skeleton template (every other package has the
   same line for a reason — they ship rclpy nodes; `robot_description` ships
   none) — not strictly this PR's problem to fix, but worth a ruling since
   it's touched either way by the ament_python/ament_cmake conversion.
5. **The issue's "D23 (URDF-as-source)" / `[[urdf-mjcf-pr-breakdown]]` /
   "roadmap #5" references don't resolve to anything in this repo (§3).**
   Should the manager treat this as a documentation gap worth a follow-up
   comment (per CLAUDE.md's "Follow-ups... comment on the issue" routing),
   or is it simply Jaime's private planning material that doesn't need to
   exist in-repo? Not a blocker for PR1's own scope either way.
