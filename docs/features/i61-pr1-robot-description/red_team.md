# Red team — #61 PR1: `robot_description` package + expand/parse gate

Reviewed: `git diff origin/main..HEAD` (7 commits), against issue #61's acceptance
criteria, `context.md`, `implementation.md`, and CLAUDE.md's invariants.

Everything below was executed. Method: the worktree was never edited. Two
throwaway rigs outside it:

- `/tmp/rt61/` — a hand-built fake ament prefix that reproduces the real
  `--symlink-install` chain (`prefix/share/robot_description/urdf/x.xacro` →
  `buildmirror/urdf/x.xacro` → `srccopy/urdf/x.xacro`), with an unmodified copy
  of `test_description.py` run against it via
  `AMENT_PREFIX_PATH=/tmp/rt61/prefix python -m pytest`. Verified it reproduces
  the real result (5 passed) before perturbing anything.
- `/tmp/ws61/` — a full second colcon workspace (`cp -r` of
  `src/robot_description`), built/tested with `--base-paths/--build-base/
  --install-base` pointed at `/tmp/ws61`, used for build-time and ratchet
  experiments. The ratchet was run with
  `check_test_integrity.py --source-dir <worktree>/src --build-base /tmp/ws61/build`
  so the real `scripts/test_baseline.json` floor stayed live.

Baseline before attacking: `pixi run build` → 9 packages finished;
`pixi run test` → **710 tests, 0 errors, 0 failures, 0 skipped, AUDIT PASSED**,
`robot_description 8 tests / 5 non-lint / +0`. The suite is genuinely green.

---

## BLOCK

### B1. The gate is blind to mesh/asset references — the exact install break it was built to catch (`src/robot_description/test/test_description.py:26-28`, whole gate) — **VERIFIED**

The module docstring's central claim is *"a .xacro that exists in the source tree
but never reaches the install tree fails here instead of at robot bringup"*, and
`meshes/` is installed (and a `README.md` invented for it) precisely so mesh
files are findable. But nothing in the five tests ever looks at a geometry
reference. A description that names meshes which do not exist anywhere passes
all five tests.

Repro (perturbation E in `/tmp/rt61`, top-level xacro only):

```xml
<link name="base_link">
  <visual><geometry><mesh filename="package://robot_description/meshes/i_do_not_exist.stl"/></geometry></visual>
  <collision><geometry><mesh filename="package://robot_description/meshes/also_missing.dae"/></geometry></collision>
</link>
```

```
### E: link referencing meshes that are not installed
5 passed in 0.09s
```

Failure scenario, and it is PR2's first day: D26 commits the whole robot to
cribbing LeRobot/XLeRobot geometry, so PR2–PR4 add `<mesh filename="package://
robot_description/meshes/…"/>` entries by the dozen. A typo'd filename, a mesh
committed to `src/` but not reaching the install tree, or a mesh referenced but
never committed at all — every one of these is green here and red at
`robot_state_publisher`/RViz/MuJoCo. This is the same class of break as the
`data_files` glob break the gate *does* catch (I verified that one: removing the
installed `urdf/` fails all 5), but for the file type `meshes/` exists to hold.
It is also the class the gate can least afford to miss, because meshes are the
one part of the description a human reviewer cannot eyeball.

Fix direction: after parsing the expansion, walk every `<mesh filename=…>` in
the model, resolve `package://<pkg>/<rel>` through
`get_package_share_directory(pkg)` (and plain relative paths against
`share/robot_description/`), and assert the file exists. The set is empty today —
which is the point: it costs nothing now and is load-bearing from PR2, exactly
like `EXPECTED_LINKS = {'base_link'}`.

### B2. Nothing asserts the top level actually *includes* the three subassemblies — an explicit acceptance criterion with zero coverage (`src/robot_description/test/test_description.py:92-104`) — **VERIFIED**

Issue #61: *"Ship `robot.urdf.xacro` including empty `base.xacro`/`column.xacro`/
`arm.xacro`"*. The only test that mentions them, `test_share_layout_is_installed`,
checks they exist **on disk in the install tree**. Installation and inclusion are
independent properties, and today the gate only checks the first.

Repro (perturbation I): delete all three `<xacro:include …/>` lines from
`robot.urdf.xacro`, change nothing else.

```
### I: all three <xacro:include> lines deleted
5 passed in 0.09s
```

So the criterion is unasserted, and worse, `test_share_layout_is_installed`
supplies *false comfort*: it goes on asserting `arm.xacro` is installed while
the arm is no longer part of the robot. (What *is* tested is that includes which
are present **resolve** — perturbation J, malformed `column.xacro`, correctly
fails 4 tests. That is a different property.)

Failure scenario in PR4's hands: an author restructuring the top level drops
`<xacro:include filename="arm.xacro"/>`. The link-set assert fires with "missing
[…]", the author "fixes" it by trimming `EXPECTED_LINKS` to match the armless
expansion, and the gate is green on a one-armed robot while `arm.xacro` is still
installed and still linted. The gate's own extension instruction ("add the new
links to `EXPECTED_LINKS`") makes this the path of least resistance.

Fix direction: parse `urdf/robot.urdf.xacro` (it is XML) and assert an
`xacro:include` element exists with `filename` equal to each `SUBASSEMBLIES`
entry — one loop, and it turns `SUBASSEMBLIES` from a disk-listing constant into
a wiring contract.

### B3. `check_urdf` — the binary the gate shells out to on every run — is not provisioned by `pixi.toml` (`pixi.toml:29-30`) — **VERIFIED**

Issue #61 acceptance: *"Build deps `xacro` + `urdfdom`/`urdfdom_py` (or
`check_urdf`) added via pixi + `package.xml`"*. `package.xml:20` honestly
declares `<test_depend>urdfdom</test_depend>`, but `package.xml` is
declarative-only in this workspace (the implementer says so himself); **pixi is
the actual provisioning mechanism**, and it pins only `ros-jazzy-xacro` and
`ros-jazzy-urdfdom-py`.

Evidence — `check_urdf` comes from `ros-jazzy-urdfdom`, and the pinned
`urdfdom-py` does *not* depend on it:

```
$ grep -rl "bin/check_urdf" .pixi/envs/default/conda-meta/*.json
urdfdom-6.0.0-h8631160_0.json
$ # pixi.lock, the urdfdom-py record's full depends: list:
  depends:
  - lxml
  - python >=3.12,<3.13.0a0
  - pyyaml
  - setuptools
$ # only ros-jazzy-urdfdom depends on urdfdom, and it is pulled in solely by
$ #   ros-jazzy-desktop = "*"
```

So the gate's dependency on `check_urdf` rests on an unpinned `"*"` metapackage
edge that this PR neither declared nor tested for. Failure scenario: any
re-lock/`pixi update` where `ros-jazzy-desktop`'s closure shifts, or a future
slimmer env, and `test_check_urdf_parses_the_expansion` dies — with a *misleading*
message, since `_require_tool` asserts the tool "is declared in package.xml and
provided by the pixi environment -- run inside `pixi run`", which would be
precisely the false part.

Fix direction: one line in `pixi.toml` next to the other two —
`ros-jazzy-urdfdom = "…"` (or conda-forge `urdfdom`).

---

## NOTE

### N1. `decisions.md` D27 and the module docstring assert, as verified fact, a check_urdf behaviour that is false — **VERIFIED**

`test_description.py:14-17` and D27 both claim the link-set assert exists to
catch *"a degenerate expansion that parses fine but describes nothing, which
`check_urdf` is happy with"*. `check_urdf` is not happy with it. Perturbation C
(remove `base_link`, leave a valid empty `<robot name="sisyphus">`):

```
### C: zero links (degenerate expansion)
FAILED test_description.py::test_check_urdf_parses_the_expansion
FAILED test_description.py::test_link_set_is_exactly_the_expected_links
2 failed, 3 passed
```

The link-set assert *does* have unique value — I found the case the docs should
be citing. A **renamed** link (`base_link` → `base`) is a perfectly valid URDF:

```
### link RENAME
AssertionError: link set drifted: missing ['base_link'], unexpected ['base']
1 failed, 4 passed
```

That is the honest justification (a renamed / silently-dropped expected link),
and it is a far more likely PR2–PR7 regression than a degenerate expansion.
`decisions.md` is permanent and is what PR2–PR7 authors will read; a
"verified by perturbation" claim that does not survive the perturbation should
be corrected. Everything else in D27's verified list I re-ran and confirmed true
(extra link → link-set + check_urdf; duplicate link → check_urdf only; malformed
XML → expansion; `urdf/*` dropped from `data_files` → all five).

### N2. The ratchet bites on a deleted test but not on a skipped one — **VERIFIED**, pre-existing tooling, follow-up not a blocker

Both halves run against the real `scripts/test_baseline.json` floor of 5:

```
# one test function removed
robot_description  7 tests  0 skipped  4 non-lint  -1  below-baseline
FAIL robot_description: 4 non-linter tests, 1 below the baseline of 5
AUDIT FAILED   → real exit code 1
```

```
# same test @pytest.mark.skip'd instead
robot_description  8 tests  1 skipped  5 non-lint  +0  ok
AUDIT PASSED   → real exit code 0
```

So R1's ratchet claim holds for deletion (the case that matters most) and does
not hold for `@pytest.mark.skip`. That hole is in
`scripts/check_test_integrity.py`, which this PR is correctly forbidden from
touching — route as a follow-up on the issue, not as a fix here. The `skipped`
column does surface it in the table; the `status` column does not.

### N3. Non-recursive install globs break `pixi run build` the first time meshes get a subdirectory (`src/robot_description/setup.py:26-27`) — **VERIFIED**

`glob('meshes/*')` returns directories too, and `data_files` cannot copy one:

```
$ # /tmp/ws61 copy, with src/robot_description/meshes/base/wheel.stl added
--- stderr: robot_description
error: can't copy '/tmp/ws61/build/robot_description/urdf/inc': doesn't exist or not a regular file
Failed   <<< robot_description [1.31s, exited with code 1]
```

Not a BLOCK: it fails loudly at build time, immediately, and the fix is small.
But it is worth flagging because D27 sells the glob as *"adding a `.xacro` in PR3
must not also require remembering to register it"*, and that invariant holds only
for a flat directory — while the LeRobot/XLeRobot STL sets PR2–PR4 will import are
the single most likely thing in this package to want subdirectories, and the
setuptools error message ("doesn't exist or not a regular file", about a directory
that exists) does not point at the cause. Fix direction when it bites: build the
`data_files` entries from an `os.walk`, one tuple per directory.

### N4. Cascade noise when the expansion fails (`src/robot_description/test/test_description.py:84-89`) — **VERIFIED**

The module-scoped `expansion` fixture is consumed by three tests that never check
`rc`, and `expanded_urdf_path` writes `expansion.stdout` unconditionally. When
xacro fails, the root cause is reported legibly by
`test_xacro_expands_without_error` (I confirmed the full message, including
xacro's `included from:` chain, reaches the assertion output), but the other three
fail with raw noise:

```
FAILED test_link_set_is_exactly_the_expected_links -   File "<string>", line 1
    lxml.etree.XMLSyntaxError: Document is empty, line 1, column 1
FAILED test_robot_is_named -   File "<string>", line 1
```

One clear signal plus three misleading ones. No test passes for the wrong reason
— I could not construct an ordering or fixture-reuse case where one does. Cheap
improvement: have `expanded_urdf_path` (or a small helper the three consumers
share) fail with "xacro expansion failed; see test_xacro_expands_without_error"
when `rc != 0`.

### N5. `xacro`'s stderr is ignored when `rc == 0` (`src/robot_description/test/test_description.py:106-111`) — **UNVERIFIED** (no rc-0-with-warning input constructed)

The docstring advertises "rc + captured stderr is a legible failure", but only
`returncode` and non-empty stdout are asserted; stderr is used solely in the
failure message. xacro emits deprecation warnings on stdout/stderr at rc 0, so a
description that expands only via deprecated syntax passes silently. Low cost to
add if PR2 wants it; not worth blocking on with an empty description.

### N6. No joint/limit assertions yet — deliberate, restated so PR2 owns it

The implementer deferred these explicitly and I agree with the call (there is no
joint to assert about). Flagging only because D26 pins concrete values (column
prismatic travel 0.00–1.20 matching D23's `RobotModel`) that nothing currently
cross-checks, and B1/B2 aside, joint limits are the third thing this gate will
need to be a real gate in PR4's hands.

---

## Claims I attacked and found TRUE (recorded so they are not re-litigated)

- **R3, the install-wiring gate, holds — including the `--symlink-install`
  question.** VERIFIED. The real chain is
  `install/…/urdf/x.xacro → build/…/urdf/x.xacro → src/…/urdf/x.xacro`. xacro does
  **not** resolve symlinks when resolving relative includes, so a new
  `wheel.xacro` present only in the source tree, included from an (installed,
  symlinked) `base.xacro`, fails loudly with no rebuild:
  ```
  AssertionError: xacro exited 2:
    error: No such file or directory: …/share/robot_description/urdf/wheel.xacro
    when processing file: …/share/robot_description/urdf/base.xacro
    included from: …/share/robot_description/urdf/robot.urdf.xacro
  ```
  Edits to an existing `.xacro` are live (no rebuild needed); new files require a
  rebuild and say so. Both errors point the safe way. No silent pass.
- **Install-wiring breaks fail as assertions, not as collection errors.** VERIFIED.
  Whole `urdf/` missing → 5 failed, `test_share_layout_is_installed` first with
  `missing install dir: …`. One installed file deleted → 5 failed with the
  "…the workspace needs a rebuild" message. `meshes/` missing → 1 failed, clean
  message. The only confusing mode is bare `pytest` outside colcon
  (5 `PackageNotFoundError` fixture errors) — by design, no source-tree fallback,
  and `colcon test` on an unbuilt package fails earlier and more clearly
  ("Check that the following packages have been built"). Acceptable.
- **R4's three-tools claim, both halves.** VERIFIED. Duplicate link name →
  `check_urdf` **only** (link set dedups, so the set assert cannot see it). Joint
  naming a nonexistent link → `check_urdf` only. Duplicate joint name, and a
  revolute joint with no `<limit>` → `check_urdf`. Renamed link →
  **link-set assert only** (`check_urdf` passes). Typo'd xacro tag
  (`<xacro:propery>`) → expansion. Malformed XML → expansion. The three tools do
  cover three distinct modes; the gap is B1/B2, not overlap.
- **R1's auto-extension.** VERIFIED. Dropped a new `test/test_newly_added.py`
  with two functions into the `/tmp/ws61` copy, registered it nowhere, rebuilt and
  ran: `pytest.xml: 10 tests` with `robot_description.test.test_newly_added ::
  test_auto_discovered_one/_two` present. `testpaths = test` picks it up; the
  audit counts it.
- **R2's `meshes/README.md`.** VERIFIED. `install/robot_description/share/
  robot_description/meshes/README.md` exists (as a symlink), and
  `glob('/tmp/rt61/globtest/*')` on a directory holding `.gitkeep` + `README.md`
  returns `['…/README.md']` only. A `.gitkeep` would indeed have left the
  installed directory missing and `test_share_layout_is_installed` red.
- **The implementer's baseline claims.** VERIFIED. `git diff origin/main..HEAD --
  scripts/test_baseline.json` is exactly one line (`robot_description: 0 → 5`);
  no other floor moved.
- **`robot_world`'s floor is 11 below its live count, and it is a pre-existing
  `main` issue.** VERIFIED. `origin/main:scripts/test_baseline.json` has
  `"robot_world": 50`; this branch does not touch that entry; the live full run
  reports `robot_world 64 tests / 61 non-lint / +11 / ok`. Nothing in this PR
  caused it and nothing in this PR can fix it without a blanket re-cut that would
  ratchet another package's floor inside this diff. Route as a follow-up on the
  issue; the implementer's handling (narrow `--packages-select … --update-baseline`)
  was the right call.
- **`package.xml` keys.** VERIFIED by inspection + provenance: `xacro`,
  `urdfdom` (provides `check_urdf`), `urdfdom_py` (provides `urdf_parser_py`),
  `ament_index_python` are all real ROS package names and all four are actually
  used by the gate. `<depend>rclpy</depend>` correctly dropped — the package ships
  no node. Nothing needed at test time is undeclared. (The provisioning half of
  this is B3.)
- **Scope discipline.** VERIFIED. `git diff --name-only origin/main..HEAD |
  grep -E 'robot_backends|check_test_integrity'` → empty. No untracked files under
  `src/` or `scripts/`.
- **D27 describes the diff as it landed** and does **not** repeat the issue's
  "D23 (URDF-as-source)" mislabel — it quotes D23's actual `RobotModel` sentence,
  which I checked against `decisions.md` D23 directly. Accurate apart from N1.
- **The `--`-in-XML-comment class does not lurk elsewhere.** VERIFIED. Every
  `*.xml` and `*.xacro` under `src/` parses with `ElementTree` — including the two
  new multi-line comment blocks in `package.xml`. The README's documented command
  `xacro $(ros2 pkg prefix --share robot_description)/urdf/robot.urdf.xacro` also
  runs and produces the expected one-link expansion.

---

## Verdict

**BLOCK: 3** — B1 (mesh references unvalidated), B2 (subassembly includes
unasserted), B3 (`check_urdf` unprovisioned in `pixi.toml`).
**NOTE: 6** — N1 (false claim in D27/docstring), N2 (skip hole in the ratchet,
pre-existing, follow-up), N3 (non-recursive install globs), N4 (cascade noise),
N5 (stderr ignored), N6 (joint asserts deferred).

The packaging, the layout, the build-type call and the install wiring are sound,
and the harness genuinely does what R1/R2/R3 claim — I could not break those. The
three BLOCKs are all about the gate's reach in PR2–PR7's hands, which is what this
PR is for: two of them (B1, B2) cost a dozen lines each and are empty-set
assertions today, exactly like `EXPECTED_LINKS`, and B3 is one line in
`pixi.toml`. Fix those three and this is a harness worth inheriting.

## Worktree state

`git status --porcelain` → ` M docs/features/i61-pr1-robot-description/status.md`
only, which is the **manager's** own in-flight edit (phase 5 → done, phase 6 →
running), present before this review started and untouched by it. No source or
test file was modified; every perturbation ran in `/tmp/rt61` and `/tmp/ws61`,
outside the worktree. `build/`, `install/` and `log/` were refreshed by
`pixi run build` / `pixi run test` and are gitignored; the final full-suite run
left them green (710 tests, AUDIT PASSED).

---

# Round 2 — scoped to the fix diff

Reviewed: `git diff 6b72adf..HEAD` (`2c975c9` pixi/B3, `688b8e4` test file/B1+B2+N4,
`5bb00f6` D27+setup.py/N1+N3, `570a106` baseline, `f28741d` docs), against issue
#61's acceptance criteria, round 1 above, and `implementation.md`'s round-2
section. A different reviewer wrote round 1; this pass attacks the fix.

Method, same discipline as round 1 — **the worktree was never edited**:

- `/tmp/rt61b/` — a fresh fake ament prefix
  (`prefix/share/ament_index/resource_index/packages/robot_description` +
  `prefix/share/robot_description/{urdf,meshes}`), with an unmodified copy of
  `test_description.py` run against it via
  `pixi run bash -c 'export AMENT_PREFIX_PATH=/tmp/rt61b/prefix:$AMENT_PREFIX_PATH;
  python -m pytest …'`. Verified it reproduces the real result (**7 passed**)
  before perturbing anything, and `diff -r` confirmed the rig's `urdf/` matched
  the source tree again at the end.
- `/tmp/ws61b/build/` — a copy of the real `build/*/pytest.xml` results, doctored
  by removing `<testcase>` entries, audited with
  `check_test_integrity.py --audit-only --build-base /tmp/ws61b/build
  --source-dir <worktree>/src --baseline <worktree>/scripts/test_baseline.json`,
  so the real floor stayed live and the real `build/` was untouched.

Baseline for this pass: `pixi run test` → **712 tests, 0 errors, 0 failures,
0 skipped, AUDIT PASSED**, `robot_description 10 tests / 7 non-lint / +0`.

**All three round-1 BLOCKs now fail correctly** (repros re-run verbatim, results
under "Regression check" below). The fix is sound. One new finding, of exactly
the class the fix's own shape predicts.

---

## BLOCK

### B4. The gate now sees `<mesh filename=…>` and nothing else on disk — a broken `<texture filename=…>` still passes all seven tests (`src/robot_description/test/test_description.py:215`) — **VERIFIED**

B1's fix hardcodes one tag: `root.iter('mesh')`. `<texture filename=…>` inside
`<material>` is the other filename-bearing element in the URDF spec, it names a
file on disk exactly the way `<mesh>` does, and neither `check_urdf` nor
`urdf_parser_py` opens it. So the fix moved the blind spot rather than closing
it.

Repro (perturbation **T1** in `/tmp/rt61b`, top-level xacro, nothing else
changed) — a named material with a texture that does not exist, plus a per-visual
one:

```xml
<material name="chassis_paint">
  <texture filename="package://robot_description/meshes/i_do_not_exist.png"/>
</material>
<link name="base_link">
  <visual>
    <geometry><box size="0.1 0.1 0.1"/></geometry>
    <material name="chassis_paint">
      <texture filename="package://robot_description/meshes/also_missing.png"/>
    </material>
  </visual>
</link>
```

```
=== T1: <texture filename=> pointing at files that do not exist ===
7 passed in 0.10s
```

Compare the same shape with `<mesh>`, which the fix does catch:

```
=== E-neg: round-1 B1 repro (missing meshes) ===
E  AssertionError: 2 of 2 mesh reference(s) do not resolve to a file …
   1 failed, 6 passed in 0.13s
```

Failure scenario: PR4 imports the gripper/camera visuals D26 commits to and
gives one a textured material — or PR2 imports an XLeRobot visual set whose
`<material>` carries a diffuse map. The `.png` is typo'd, or committed to `src/`
and not reaching the install tree, or referenced and never committed. Seven
green tests, and the failure surfaces downstream: RViz logs a texture-load
error and renders untextured (cosmetic), while **MuJoCo's model compiler treats
a missing asset file as a hard compile error** — so the same class of break that
B1 was blocked for lands on PR7's MJCF conversion instead of PR2's bringup.
(The MuJoCo half of that sentence is **UNVERIFIED** — `mujoco` is still a TODO
in `pixi.toml` and is not in the env, so I could not run it. The gate-blindness
itself is VERIFIED.)

This is a BLOCK on the same grounds round 1 gave for B1, which the implementer
accepted without disagreement: the assertion is over an **empty set today**, it
costs nothing until the first textured material, and PR1's entire stated purpose
is to be the harness PR2–PR7 inherit rather than one fitted to what already
passes. It is also the cheapest fix in this report — the helper and the message
are already written and already correct for the `package://` / `file://` /
absolute / share-relative forms.

Fix direction: make the tag list a constant instead of a literal, e.g.

```python
MESH_BEARING_TAGS = ('mesh', 'texture')   # every URDF element naming a file on disk
references = [element.get('filename')
              for tag in MESH_BEARING_TAGS
              for element in root.iter(tag)]
```

and rename the test/helper off `mesh` (`test_every_file_reference_resolves` /
`_resolve_file_reference`), so the next author who adds a file-bearing tag has
one obvious place to add it. `EXPECTED_LINKS` earned its constant for the same
reason. The docstring (lines 23–26) and `decisions.md:92` both say "every
`<mesh filename=…>`", which is *honest* about today's scope — update both to
match the widened list rather than leaving the permanent record describing a
narrower gate than PR2 needs.

While in there, two adjacent references I confirmed are also invisible and which
the same constant would absorb when they become relevant — flagged, not blocked:
`<mujoco><compiler meshdir="…"/></mujoco>` (**M13**: nonexistent `meshdir` →
`7 passed`), which is PR7's shape, and any future `<gazebo><uri>` (not tested;
this project is MuJoCo, not Gazebo).

---

## NOTE

### N7. The N+1th copy of the corrected claim survives in the package README — the one permanent doc a PR2 author reads first (`src/robot_description/README.md:20-22`) — **VERIFIED**

N1 corrected the "three tools" framing in the two places the implementer looked:
the module docstring and `decisions.md` D27. It did not look at the package's own
README, which the fix diff never touches (`git diff --name-only 6b72adf..HEAD`
lists six files; `README.md` is not among them). It still reads:

> `test/test_description.py` is the CI gate the later PRs extend: it expands the
> *installed* top-level xacro, parses the expansion with `check_urdf`, and
> asserts the link set.

That enumeration is now stale by two asserts (mesh resolution, the include
contract) and reads as exhaustive. Not false, so not a BLOCK — but D27 was
corrected precisely because the permanent record is what PR2–PR7 authors read,
and a `README.md` sitting *in the package* is strictly likelier to be read than
`decisions.md` D27. The corrected-away claim itself ("a degenerate expansion …
which `check_urdf` is happy with") survives **nowhere**: I grepped `degenerate`,
`happy with`, `describes nothing`, `parses fine`, `three tools`, `three failure
modes` across all `*.md`/`*.py`/`*.xacro`/`*.xml` outside `.pixi`, and the only
hits are D27's *corrected* wording plus `implementation.md` / `status.md`, both
ephemeral and both already correct. Fix direction: one sentence in the README.

### N8. A nested `<xacro:include>` deleted inside a subassembly is unasserted — the PR2 shape the implementer flagged (`src/robot_description/test/test_description.py:168-175`) — **VERIFIED, and I judge it acceptably deferred**

The implementer's own caveat, tested. `base.xacro` includes `wheel.xacro`;
`wheel.xacro` is installed; the include is deleted:

```
=== I7: nested include DELETED from base.xacro (wheel.xacro installed, unused) ===
7 passed in 0.09s
```

Whereas the *broken* nested include is loud (`I6`: `base.xacro` includes a file
that does not exist → **5 failed, 2 passed**, the expansion failure plus three
`_require_expansion` pointers — N4's fix working).

This is B2 one level down, but it is materially weaker than B2 was, and that is
why I do not block on it. B2 was a BLOCK because in PR1 the subassemblies are
*empty*, so deleting a top-level include changed nothing observable — zero
coverage. From PR2 onward a subassembly that gets un-included takes its links
with it, and `test_link_set_is_exactly_the_expected_links` fires with
`missing [...]`. The residual exposure is the same "author trims
`EXPECTED_LINKS` to match" path round 1 named for B2 — real, but now backstopped.
Fix direction when PR2 lands: make the include walk recursive (parse each
`SUBASSEMBLIES` file too, collect its includes, and assert every collected
filename is installed), written against PR2's actual nesting rather than a guess
at it — the same reasoning the manager accepted for deferring N3's `os.walk`.

### N9. A duplicated top-level include passes; a differently-spelled one fails (`src/robot_description/test/test_description.py:169-170`) — **VERIFIED**

Set semantics, both directions:

```
=== I4: DUPLICATE include of base.xacro ===              7 passed
=== I5: equivalent spelling ./base.xacro ===             1 failed (includes assert)
```

I5 is arguably a **feature**, not a bug: D27 mandates relative includes
(`filename="base.xacro"`, not `$(find robot_description)`), so the strict-equality
assert quietly enforces that decision, and it fails loudly with the offending
filename in the message. I4 is the real (minor) hole: a duplicate include is
invisible here, and once subassemblies carry macros it is how you get a
duplicate-macro redefinition. Cheap fix if wanted: compare the *list* length to
the set size and say "duplicate include".

### N10. `_resolve_mesh`'s message misdescribes two of its four branches (`src/robot_description/test/test_description.py:216-217`, `:220-226`) — **VERIFIED**

Cosmetic, but both fire on inputs a PR2 author will actually produce:

- `<mesh filename=""/>` (**M3**) fails with *"a `<mesh>` element in the expansion
  has no filename attribute"* — it has one; it is empty. Same message as the
  genuinely-absent case (**M4**).
- A `file://` or absolute reference that does not exist (**M8**, **M9**) fails
  with *"do not resolve to a file in the installed share tree"* — it was never
  looked for in the share tree.

Everything else about the helper is right; see the regression list.

### N11. `test_share_layout_is_installed`'s docstring overclaims (`src/robot_description/test/test_description.py:147`) — **VERIFIED**, pre-existing (round-1 code, not the fix diff)

*"The urdf/ and meshes/ install dirs exist, with every source file in them"* — it
checks four hardcoded names (`robot.urdf.xacro` + `SUBASSEMBLIES`), not every
source file. A `urdf/wheel.xacro` added in PR2 is not asserted installed by this
test. Functionally covered (an included-but-uninstalled file fails the expansion
loudly — round 1's R3, re-confirmed by **I6**), so this is a docstring fix, not
a code one. Flagged only because the round-1 B2 lesson was precisely that this
test's *name* invites being read as more than it checks.

---

## Regression check — round 1's BLOCKs, and its "could not break" claims

All executed in `/tmp/rt61b` / `/tmp/ws61b`.

- **B1 fixed, and it bites for the right reason.** Round-1 repro E verbatim →
  `1 failed, 6 passed`, `test_every_mesh_reference_resolves` only, message names
  both references and their resolved paths. Positive control (a `<mesh>` naming
  `meshes/README.md`, which *is* installed, in the same `<visual><geometry>`
  position) → `7 passed`. The pair is what makes it a real assertion rather than
  a no-op over an empty set: the negative proves `iter('mesh')` reaches that
  position, the positive proves resolution succeeds on a file that exists.
- **`_resolve_mesh` — all four branches exercised directly, ten inputs.** Every
  one fails loudly and correctly, none passes for the wrong reason:
  | input | result |
  | --- | --- |
  | `package://no_such_pkg_xyz/…` (**M1**) | `Failed` at the `pytest.fail`, message names the package. `pytest.fail` inside the helper works as intended — a **failure**, not an error |
  | `package://robot_description` (no slash) (**M2**) | fails, resolved path shown as the bare share dir |
  | `filename=""` (**M3**) / no `filename` attr (**M4**) | fails at `all(references)` (message: N10) |
  | `package://robot_description/../../../../../../etc/hostname` (**M5**) | **passes** — and correctly so: real ROS `package://` resolution is the same naive join, so the gate matches the runtime |
  | cross-package `package://robot_bringup/package.xml` + `…/definitely_missing.stl` (**M6b**, real install prefix on `AMENT_PREFIX_PATH`) | `1 of 2` — the real file resolves, the missing one fails |
  | `file:///etc/hostname` (**M7**) / `file:///etc/definitely_not_here` (**M8**) / `/opt/nope/a.stl` (**M9**) / `file://localhost/…` (**M10**) | pass / fail / fail / fail, all correct |
  The implementer's caveat that the `file://` and absolute branches are
  "unexercised by any real input" is **too pessimistic**: `$(find
  robot_description)/meshes/x.stl` — the standard xacro idiom PR2 may well use —
  is substituted by xacro into an absolute path and lands on the absolute branch.
  **M11**: two such references, one real and one not → `1 of 2` missing, correct.
  **M12**: an installed mesh that is a **dangling symlink** (the exact
  `--symlink-install` shape: install → build → deleted source) → correctly
  reported missing; intact → passes.
- **B2 fixed, both directions.** Round-1 repro I verbatim (all three includes
  deleted) → `1 failed, 6 passed`, includes assert only. **I2** (a fourth,
  genuinely installed `extra.xacro` included) → fails — set equality really does
  catch the unexpected direction. **I3** (one include commented out) → fails;
  `ElementTree` drops comments, so it is correctly counted as missing.
- **B3 fixed, on both platforms, and the chain is real.** `pixi.lock`'s
  `environments.default.packages` contains `ros-jazzy-urdfdom` for **linux-64**
  (`6.0-py312h24bf083_18`) *and* **linux-aarch64** (`6.0-py312h9804fc4_18`) — the
  Pi is covered. The edge is version-pinned, not a `"*"`:
  `ros-jazzy-urdfdom` → `depends: [… 'urdfdom >=6.0,<6.1a0' …]`, and
  `grep -rl "bin/check_urdf" .pixi/envs/default/conda-meta/*.json` →
  `urdfdom-6.0.0-h8631160_0.json`, i.e. conda-forge `urdfdom` owns the binary and
  is in the lock for both platforms. `pixi install --locked` exits 0, so the lock
  genuinely satisfies the amended manifest (not just `--frozen`). `check_urdf`
  resolves inside `pixi run`. The implementer's "one declared, version-pinned
  hop" caveat is accurate and, in my judgement, the **right** call for a
  RoboStack env — pinning conda-forge `urdfdom` directly would mix channels for a
  package RoboStack wraps, and `<test_depend>urdfdom</test_depend>` keeps the
  rosdep spelling honest. Nothing further needed here.
- **N4's `_require_expansion` cascade — routing is complete.** `grep -n
  'expansion\.stdout\|expansion\.returncode\|_require_expansion'` shows the only
  surviving raw consumers are `_require_expansion` itself, the root-cause test
  `test_xacro_expands_without_error` (correct — it *is* the rc assert), and the
  `expanded_urdf_path` fixture at `:142`. That fixture is the candidate the brief
  flagged, and it is **not** a bypass in practice: its sole consumer,
  `test_check_urdf_parses_the_expansion`, calls `_require_expansion(expansion)`
  before touching the path, and `write_text('')` on a failed expansion cannot
  itself raise. Verified on the malformed-XML perturbation: one legible root
  cause plus three *"xacro expansion failed (rc 2), so this assertion never ran
  — see test_xacro_expands_without_error"* pointers, each carrying xacro's own
  diagnostic. (`test_top_level_includes_every_subassembly` raises a raw
  `ElementTree.ParseError` on that input — but a malformed top-level file *is*
  that test's own root cause, so it is not cascade noise.)
- **The ratchet bites at the new floor of 7.** Doctored `pytest.xml` in
  `/tmp/ws61b`, real baseline file:
  ```
  # drop the new mesh test          → 6 non-lint, -1, below-baseline
  FAIL robot_description: 6 non-linter tests, 1 below the baseline of 7
  AUDIT FAILED → exit 1
  # drop both new tests             → 5 non-lint, -2, below-baseline
  FAIL robot_description: 5 non-linter tests, 2 below the baseline of 7
  AUDIT FAILED → exit 1
  ```
  The second case is the important one: the *old* floor of 5 is no longer green.
- **The baseline diff is still exactly one line vs `origin/main`.**
  `git diff --numstat origin/main..HEAD -- scripts/test_baseline.json` → `1 1`,
  content `"robot_description": 0 → 7`. No other package's floor moved. `+11` on
  `robot_world` is the pre-existing `main` drift round 1 documented, untouched.
- **Scope discipline in the fix diff.** `git diff --name-only 6b72adf..HEAD` →
  six files, all owned: `docs/design/decisions.md`,
  `docs/features/i61-pr1-robot-description/implementation.md`, `pixi.toml`,
  `scripts/test_baseline.json`, `src/robot_description/setup.py`,
  `src/robot_description/test/test_description.py`. No `scripts/check_test_integrity.py`,
  no other `src/` package, no untracked source files.
- **D27 as it actually landed**, re-read against the final diff rather than the
  plan. Every sentence describes code that exists: the "four tools plus two
  wiring asserts" enumeration matches the seven test functions; the renamed-link
  justification replaces the false degenerate-expansion one; the mesh clause
  ("`check_urdf` … validates the model but never opens a mesh") is exactly what
  T1/E-neg show; the provisioning sentence matches the `pixi.lock` evidence; the
  N3 flat-glob limit matches `setup.py:20-27` and round 1's verified build error.
  The single scoping I would widen is "every `<mesh filename=…>`" — accurate
  today, and B4 is the argument for making it accurate about more.
- **Full suite green after the fix.** `pixi run test` → **712 tests, 0 errors,
  0 failures, 0 skipped**, `robot_description 10 / 7 non-lint / +0`, AUDIT PASSED.

---

## Round-2 verdict

**BLOCK: 1** — B4 (`<texture filename=…>`, and the hardcoded single tag behind it).
**NOTE: 5** — N7 (stale gate description in the package README), N8 (nested
include unasserted — deferrable), N9 (duplicate include passes), N10 (two
misdescribing failure messages), N11 (docstring overclaim, pre-existing).

The fix is good work. All three round-1 BLOCKs are genuinely closed, each one
verified in both directions rather than only in the failing one; `_resolve_mesh`
survived ten adversarial inputs without a single pass-for-the-wrong-reason; the
N4 cascade fix routes completely; the pin is real on both platforms including the
Pi; the ratchet moved to a floor that actually bites; and D27 now describes the
gate that shipped. The implementer's self-reported caveats were honest and, in
the `file://`/absolute case, more pessimistic than the code deserves.

B4 is the one thing I would not merge without: it is the same defect as B1, one
element name away, in a test the implementer wrote yesterday — and the fix is a
tuple literal plus a rename. Closing it makes the *shape* of the check
extensible, which is the difference between a harness PR2–PR7 inherit and one
they have to re-litigate the first time they point at a file that is not an STL.

## Worktree state

`git status --porcelain` at the end of this pass:

```
 M docs/features/i61-pr1-robot-description/status.md
?? docs/features/i61-pr1-robot-description/red_team.md
```

Exactly the two expected entries: the **manager's** own in-flight `status.md`
edit, present before this pass started and untouched by it, and this report. No
source, test, doc, `pixi.toml`, `pixi.lock` or baseline file was modified; every
perturbation ran in `/tmp/rt61b` and `/tmp/ws61b`, outside the worktree, and the
rig's `urdf/` was `diff -r`'d back to matching the source tree at the end.
`build/`, `install/`, `log/` and `.pixi/` were refreshed by `pixi run test` /
`pixi install --locked` and are gitignored; the final full-suite run left them
green (712 tests, AUDIT PASSED).
