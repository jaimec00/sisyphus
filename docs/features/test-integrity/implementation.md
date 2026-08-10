# Implementation — test-integrity (issue #16)

## What shipped

| path | what |
|---|---|
| `scripts/check_test_integrity.py` | the guard (audit of colcon's JUnit XML) **and** the driver `pixi run test` now invokes |
| `scripts/tests/test_audit.py`, `scripts/tests/test_driver.py`, `scripts/tests/test_lint.py`, `scripts/tests/conftest.py` | the guard's own suite (66 tests), recorded as the `_workspace_tooling` pseudo-package |
| `pixi.toml` | `test` now runs the driver; new `test-audit` task re-reads results without re-running |
| `src/robot_{brain,bringup,perception,description,safety}/` | `extras_require={'test': ['pytest']}`, `pytest.ini`, and `test/test_{flake8,copyright,pep257}.py` |
| `src/robot_{backends,skills}/pytest.ini` | one-line comment reword ("Neither package" → "This package"), now that all 7 carry the file |
| `src/robot_description/setup.py` | wrapped a 100-char description line so flake8 passes |
| `README.md` | documents the guard and `pixi run test-audit` |

`.github/workflows/` was **not** touched (per the manager's decision — CI has no
ROS/pixi environment). See "Deliberately left out".

## Acceptance criteria

**(a) `pixi run test` green across all 7 packages** — yes. Final run from a
deleted `build/` (`.dev/runs/i16-test-integrity-make-pixi-run-test-honest/
20260810-044847/test_final.log`) exits 0 with 198 collected tests across 8
audited units.

**(b) A guard fails on zero collected tests, wired into `pixi run test`** — yes.
Verified end-to-end in the real workspace, not just with fixtures (logs in the
same directory):

- removing `src/robot_safety/test/` → `EXIT=1`,
  `FAIL robot_safety: build/robot_safety/pytest.xml reports 0 collected tests`
  (`negative_missing_tests.log`);
- `touch src/robot_perception/COLCON_IGNORE` (colcon silently stops testing it)
  → `EXIT=1`, `FAIL robot_perception: no JUnit result file under
  build/robot_perception` (`negative_colcon_ignore.log`);
- every test in `robot_safety` skipped → `EXIT=1`, `all-skipped`
  (`negative_all_skipped.log`, added in red-team round 1);
- a wrong `--source-dir` → `EXIT=2` before any stage runs
  (`block4_and_age.log`, round 1).

**(c) Real per-package counts.** `robot_skills` = **59**, `robot_backends` =
**58**. Both genuinely collect tests, so **#4 did not merge on a hollow green**
for its own packages. The hollow green was in the *other five* packages, which
`colcon test` was failing loudly for an accidental reason (below) — a fragile
accident, now replaced by a real scaffold plus a real guard.

Full table from the final run:

```
package             tests  skipped  errors  failures  status
_workspace_tooling     66        0       0         0  ok
robot_backends         58        0       0         0  ok
robot_brain             3        0       0         0  ok
robot_bringup           3        0       0         0  ok
robot_description       3        0       0         0  ok
robot_perception        3        0       0         0  ok
robot_safety            3        0       0         0  ok
robot_skills           59        0       0         0  ok
8 packages, 198 tests collected
AUDIT PASSED: every expected package collected tests
```

## What colcon actually does (observed, not inferred)

The context doc predicted `build/<pkg>/pytest.xml` with `tests="0"`. That is
right for packages colcon runs *pytest* for, but the pre-existing state of the
5 skeleton packages was different, and the difference matters.

### 1. `tests_require` never reaches colcon — the skeletons were on the unittest path

The five skeletons used `tests_require=['pytest']`. Modern setuptools drops it:

```
setuptools/_distutils/dist.py:289: UserWarning: Unknown distribution option: 'tests_require'
```

colcon reads `setup_py_data` by actually executing `setup.py`
(`colcon_core/task/python/test/__init__.py:41`), so
`PytestPythonTestingStep.match` → `has_test_dependency(setup_py_data, 'pytest')`
was **False** and the package fell through to `SetuppyPythonTestingStep`
(`python -m unittest -v`), which **writes no JUnit XML at all**. Baseline run:

```
--- stderr: robot_brain
Ran 0 tests in 0.000s
NO TESTS RAN
---
Failed   <<< robot_brain [1.06s, exited with code 5]
...
  5 packages failed: robot_brain robot_bringup robot_description robot_perception robot_safety
```

That looks like the system working, but it is an accident: Python 3.12's
`unittest` happens to exit 5 when it runs nothing, and
`setuppy_test.py` returns that code verbatim. Had unittest discovered even one
trivial test, colcon would have reported success with **no result file** — the
silent-absence hollow green. This is why "no result file" is fatal to the guard.

### 2. The predicted hollow green, reproduced exactly

Scratch scenario (temporarily gave `robot_safety` `extras_require={'test':
['pytest']}` + the `pytest.ini`, with **no** test files, then reverted):

```
Finished <<< robot_safety [0.42s]
Summary: 1 package finished [0.55s]      # colcon test: SUCCESS
$ cat build/robot_safety/colcon_test.rc
0
$ cat build/robot_safety/pytest.xml
<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite
 name="pytest" errors="0" failures="0" skipped="0" tests="0" time="0.010"
 timestamp="2026-08-10T04:20:07.537508-04:00" hostname="olivia" /></testsuites>
$ colcon test-result --verbose
Summary: 117 tests, 0 errors, 0 failures, 0 skipped     # exit 0, robot_safety invisible
```

A package with zero tests is green *and* absent from the summary. Confirmed.

### 3. A third shape: colcon's placeholder result

Without the `pytest.ini` the RoboStack `launch_testing` plugin aborts the pytest
session, and the file left behind is the stub colcon writes *before* invoking
pytest (`pytest.py:145`):

```xml
<testsuite name="robot_safety" tests="1" failures="0" time="0" errors="1" skipped="0">
  <testcase classname="robot_safety" name="pytest.missing_result" time="0">
    <failure message="The test invocation failed without generating a result file."/>
  </testcase>
</testsuite>
```

It reports `tests="1"`, so a naive `tests > 0` check would pass it. The guard
treats a result whose test cases are *only* `pytest.missing_result` as no
result at all.

### 4. `colcon test` swallows failures too

Noted in passing: `colcon test` exited **0** on a run where
`robot_description`'s flake8 test genuinely failed; only
`colcon test-result` reported it. The driver runs both and fails on either.

## Design choices

**Python, not bash.** The guard's core job is parsing JUnit XML and mirroring
`colcon_test_result`'s parser semantics (which attributes are required, which
files to skip). `xml.etree` makes that a faithful ~40 lines; bash would need an
XML dependency or fragile regex. It also makes the guard unit-testable with
fixtures, which the acceptance criteria require. It lints clean under the repo's
own `ament_flake8`/`ament_pep257`/`ament_copyright` (enforced by
`scripts/tests/test_lint.py`, which is why `scripts/` is now linted at all).

**Evidence-only.** `audit()` reads files; it never runs pytest. Its counts are
by construction "what colcon actually did". The driver is a separate concern in
the same file.

**Expected set = the packages this repo owns; `COLCON_IGNORE` deliberately
ignored.** `discover_packages()` walks `src/` for `package.xml`, keys on
`<name>` (colcon's key, not the directory name), and then keeps the manifests
**git tracks** — see "BLOCK-3" below for why the ownership filter exists and
what it does not weaken. It does **not** honour `COLCON_IGNORE`/`AMENT_IGNORE`:
dropping a package out of the test run is precisely the failure mode being
guarded, so it must not be possible to opt out of the guard by opting out of
colcon. There is deliberately **no allow-list** and no in-package exemption
marker for first-party code — an exemption mechanism is a hollow-green
mechanism. (Tradeoff: a future first-party package that legitimately cannot be
tested will have to add at least a linter test. Given the repo convention that
every `ament_python` package carries the three linter tests, that seems right.)

**Staleness: delete first, then verify freshness.** Two layers.
1. The driver deletes every parseable JUnit file under `build/` before running
   (only files that parse as xUnit, so `package.xml` symlinks are safe), so any
   result present afterwards was written by this run. A narrowed run only
   deletes the selected packages' results, so it does not destroy evidence it
   will not regenerate.
2. `audit(..., min_mtime=started)` additionally rejects a package whose results
   all predate the run (2 s tolerance for filesystem timestamp granularity).
   Redundant after (1), but it covers a failed/partial clean, and it makes the
   freshness rule explicit and testable rather than implicit in the deletion.
`--audit-only` has no freshness signal to use; it says so in `--help`, prints a
banner saying nothing was re-run, and prints each package's result age
(NOTE-5).

**The guard guards itself.** `scripts/tests/` is run by the driver with pytest's
`--junit-xml` pointed at `build/_workspace_tooling/pytest.xml`, and
`_workspace_tooling` is in the expected set. Emptying or deleting the guard's
suite therefore fails `pixi run test` exactly like emptying a ROS package's —
an untested guard would reintroduce the very problem. It also means
`colcon test-result --all` reports the tooling suite alongside the ROS packages.
Alternatives rejected: putting the guard inside a ROS package (workspace tooling
is not robot code, and the skill-API/backend seams say packages stay cohesive),
or a new `ament_python` package just for tooling (heavier than the problem).

**`--all` on `colcon test-result`.** The driver uses
`colcon test-result --all --verbose` rather than `--verbose` alone, so zero-test
results appear in the log instead of being omitted. The audit is the thing that
*fails*; `--all` is what makes the raw numbers visible to a human.

**Every stage runs; the summary always prints.** `colcon test`, the tooling
suite, `colcon test-result` and the audit all run even after an earlier failure,
then the driver prints `FAILED stages: ...` and exits 1. The old
`colcon test && colcon test-result` short-circuited, hiding the results summary
whenever the test stage failed.

**`--packages-select` passthrough.** `.claude/agents/test-runner.md` narrows to
a feature's packages "when appropriate"; without a supported narrow mode the
obvious move is to bypass the driver and call `colcon test` directly, losing the
guard. The narrow mode keeps the guard, prints a loud
`*** PARTIAL RUN: ... not a whole-workspace verdict ***` banner, rejects unknown
package names (a typo must not quietly shrink the expected set), and skips the
tooling suite.

## Guard tests (66)

`scripts/tests/test_audit.py` (45) covers the audit, `test_driver.py` (19)
covers the driver (see BLOCK-2 below), `test_lint.py` (3) lints `scripts/`.

`scripts/tests/test_audit.py` writes XML fixtures to `tmp_path` — the exact
shapes observed above, including the real `robot_backends` header and the
verbatim colcon placeholder — so no colcon run is needed. Coverage of the
required cases: `tests="0"` fails; a missing result file fails; a build
directory with only non-result XML fails; `>0` passes; the placeholder result
counts as missing; malformed XML (truncated, empty, wrong root tag, missing
`tests` attribute, non-integer, negative) is never mistaken for evidence, and
junk XML next to a real result does not suppress it; results in subdirectories
are found and several files are summed; a stale result fails and a stale one
does not mask a fresh empty one; cleaning removes results but not `package.xml`,
and can be limited to selected packages; the expected set comes from the source
tree and from `<name>` rather than the directory; a manifest without `<name>` is
an error; the CLI exits 1 and names the offending package, exits 0 with real
numbers in the report when passing; an unknown `--packages-select` name is
rejected; the tooling pseudo-package is itself part of the expected set; and
every package this repo actually ships is expected. Round 1 added the
skip cases (BLOCK-1), the git-ownership cases (BLOCK-3) and the
nothing-to-audit cases (BLOCK-4).
`scripts/tests/test_lint.py` runs the three ament linters over `scripts/`.

## Red-team round 1

All four BLOCKs are fixed. Evidence logs are under
`.dev/runs/i16-test-integrity-make-pixi-run-test-honest/20260810-044847/`.
Every fix was also checked by **mutation**: reverting the fix in
`check_test_integrity.py` must turn the new tests red, so none of them is a
tautology.

### BLOCK-1 — an all-skipped suite was green

**Change.** `parse_xunit` now returns an `XUnitCounts` namedtuple including
`skipped`, summing the three attributes colcon itself sums (`skip`, `skipped`,
`disabled` — `colcon_test_result/test_result/xunit.py:108-115`).
`PackageAudit` carries `skipped` and an `executed = tests - skipped` property;
`audit_package` fails a package with the new status `all-skipped` when
`executed <= 0`, kept separate from `zero-tests` so the two failure modes stay
distinguishable. The report gained a `skipped` column and an
`(N skipped)` suffix on the total, so a partial skip is visible even when it
passes.

**Evidence** (`negative_all_skipped.log`): `pytest.importorskip` of a missing
module prepended to `robot_safety`'s three test modules.

```
Summary: 7 packages finished          # colcon test:        SUCCESS
Summary: 198 tests, 0 errors, 0 failures, 3 skipped   # colcon test-result: 0
robot_safety            3        3       0         0  all-skipped
FAIL robot_safety: .../pytest.xml reports 3 collected tests but all 3 were
skipped: no test body ran
EXIT=1
```

Both colcon stages are green; the driver is not. Unit coverage: all-skipped
fails, 11-of-12 skipped passes, skips are summed across result files, and each
of the three skip attributes is counted.

### BLOCK-2 — the driver's exit-code composition was untested

**Change.** New `scripts/tests/test_driver.py` (19 tests). `guard._run`,
`guard.run_tooling_tests` and `guard.delete_result_files` are monkeypatched
with recorders; the fake `colcon test` *writes* the JUnit results a real one
would, so the audit still judges real files rather than a mock. Asserted:
every stage passing exits 0; each of the four stages failing **alone** exits 1
and is the only one named (parametrised); the `colcon test` = 0 /
`colcon test-result` = 1 path (the one a genuine failing test takes) has its
own test; a hollow package fails when every command succeeded; all four stages
still run and the report still prints after an early failure; multiple
failures are all named; results are deleted **before** `colcon test`, and a
stale result cannot stand in for a package colcon skipped; the tooling suite is
audited like a package; `--base-paths`/`--build-base`/`--test-result-base`
reach colcon; a narrowed run passes the selection through, skips the tooling
suite, deletes only the selected results, prints the PARTIAL banner, and still
fails on a hollow selected package.

**Evidence.** Four mutations of `main`, each one exactly the regression the
red-team described, each caught:

| mutation | result |
|---|---|
| only `test integrity audit` decides the exit code | 6 failed |
| `'colcon test-result': rc_result` dropped from `stages` | 3 failed |
| `if rc_test: return rc_test` short-circuit | 3 failed |
| results deleted *after* `colcon test` | 2 failed |

### BLOCK-3 — vendored third-party sources would have made the run permanently red

**Change.** The expected set is now the packages this repo **owns**:
`discover_packages()` keeps a manifest only if `git ls-files` reports it as
tracked, and returns the rest as `unowned`. `find_source_packages()` remains as
the thin "just the names we must test" wrapper.

Properties, in the order they matter:

- **First-party code still cannot opt out.** Ownership is decided by git, not
  by a file inside the package: `COLCON_IGNORE`, `AMENT_IGNORE` and even
  `.gitignore` are powerless (an ignored-but-*tracked* file is still tracked).
  The only escape is removing the manifest from the index — a visible,
  reviewable diff. Tested, including a would-be `.test-integrity-exempt`
  marker.
- **A tracked package that stops being tested still fails**, vendored packages
  present or not (tested, and demonstrated end-to-end below).
- **Nothing is dropped silently.** Every unowned package gets a `note:` line in
  the report.
- **No ownership signal ⇒ no exemptions.** If `src/` is not inside a git work
  tree (tarball export, no git installed), *every* package found is expected.
  The fallback errs towards a loud failure, never towards a silent pass. An
  *empty* tracked set inside a real work tree is a real answer, not a fallback,
  and hits BLOCK-4's refusal.

**Evidence** (`vendored_untracked.log`, `vendored_plus_tracked_hole.log`): an
untracked `src/mujoco_ros2_control/` (the package `pixi.toml` already plans to
source-build) was added.

```
8 packages, 198 tests collected
note: mujoco_ros2_control is in the source tree but not tracked by this
repository (vendored/imported?), so it is not audited
AUDIT PASSED  /  EXIT=0
```

and with the same vendored package present plus `COLCON_IGNORE` on the
first-party `robot_brain`:

```
robot_brain             0        0       0         0  no-result
FAIL robot_brain: no JUnit result file under build/robot_brain
EXIT=1
```

This also caught a real bug in my own first cut: the new test
`test_the_real_workspace_manifests_are_tracked` originally asserted `unowned
== []`, which would have failed the tooling suite — and therefore
`pixi run test` — the moment anyone imported a dependency or created a package
before `git add`. That is BLOCK-3 reintroduced through the test suite; it now
only asserts that this repo's own seven packages are owned. (Caught by running
the scenario, not by reasoning about it — which is the argument for the
end-to-end negative runs.)

### BLOCK-4 — fail-open on an empty expected set

**Change.** `main` now `parser.error`s (exit 2) when `--source-dir` is not a
directory, and when the discovered owned-package set is empty, *before* any
stage runs. "Implausibly small" beyond `> 0` was left out deliberately: the
honest version of that is the per-package ratchet in NOTE-4/6, and a hardcoded
`>= 7` would be a lie the moment a package is added or split.

**Evidence** (`block4_and_age.log`):

```
$ ... --audit-only --source-dir /tmp/i16-empty-src
error: found no packages owned by this repository under /tmp/i16-empty-src --
wrong --source-dir, or the manifests are untracked; refusing to report a
passing audit of nothing                                          EXIT=2
$ ... --audit-only --source-dir /tmp/i16-nope
error: --source-dir is not a directory: /tmp/i16-nope              EXIT=2
$ ... --source-dir /tmp/i16-empty-src        # full driver: nothing ran
                                                                   EXIT=2
```

Tested for the empty dir, the missing dir, the all-vendored tree, and (in
`test_driver.py`) that no stage runs when the source dir is wrong.

### NOTEs addressed

- **NOTE-1 (docstring vs. behaviour).** The premise was slightly off:
  `colcon_test_result` requires **both** `tests` and `failures`
  (`xunit.py:108-115` — `failures` has default `None`, i.e. required), so the
  guard's strictness was already parity, and loosening `failures` to
  `.get(..., 0)` would have *broken* parity. Fixed the other way: the docstring
  now states the real rule (both required, `errors`/skips default to 0) and
  cites the colcon line range. Parity now also covers the skip attributes.
- **NOTE-3 (flags not passed through).** `colcon test` now gets
  `--base-paths <source-dir> --build-base <build-base> --test-result-base
  <build-base>` and `colcon test-result` gets `--test-result-base`, so
  overriding either flag can no longer leave colcon and the audit in different
  trees. Asserted in `test_driver.py`; the final full run's log shows the real
  command line.
- **NOTE-5 (stale `--audit-only` pass).** `--audit-only` now prints
  `*** --audit-only: nothing was re-run; the ages below are how old this
  evidence is ***` and an `age` column per package (`27s`, `4m`, `3h`, `2d`),
  so a pass from an old build is self-evidently one.
- **NOTE-7 (hygiene).** `colcon` missing from `PATH` now prints
  `error: colcon: command not found -- run this through 'pixi run test' or
  from inside 'pixi shell'` and returns 127 instead of a traceback (tested).
  Result files are still parsed 2–3× per run (discovery, audit, leftover
  scan) — a deliberate choice: at ~10 small files it is unmeasurable, and
  caching would add a lifetime question to a tool whose value is being
  obviously correct. And to state it plainly rather than imply otherwise:
  **the guard's own suite is not tamper-proof.** `_workspace_tooling` is held
  to exactly the standard every other package is held to — *some* tests must
  run — so deleting `test_audit.py`/`test_driver.py` while keeping
  `test_lint.py` leaves it green at 3 tests. Closing that needs the NOTE-4
  ratchet, not a special case for the guard.

### Deferred (manager to file follow-up issues)

- **NOTE-2** — placeholder detection is pytest-specific and `sentinel_only` is
  ANDed per file. Both misjudgements are *conservative in the merge sense*
  (colcon's placeholders carry `errors="1"`, so `colcon test-result` fails the
  run regardless); only the audit's own verdict line is wrong. Generalising it
  properly means modelling `ament_cmake`'s placeholder shape too, which is
  speculative until a C++ package lands.
- **NOTE-4 + NOTE-6** — a per-package test-count ratchet (and "a package with
  implementation code must have non-linter tests"). Real erosion protection,
  but a different feature: it needs a checked-in baseline, an update workflow,
  and a policy for legitimate drops. Out of scope for #16, which asks for
  "zero tests".

```bash
pixi run build
pixi run test                 # colcon + tooling suite + guard (the honest default)
pixi run test-audit           # re-read build/ results, run nothing
pixi run python scripts/check_test_integrity.py --packages-select robot_skills
```

## Deliberately left out / follow-ups

- **CI.** `.github/workflows/guards.yml` still only checks that
  `docs/features/` is empty. Running the guard in CI needs a full RoboStack/pixi
  environment that nothing currently provisions (`DEVELOPMENT.md`: heavy tests
  are a laptop concern). Worth a follow-up issue if PR-time enforcement is
  wanted; the guard already takes `--audit-only` for a cheap post-hoc check.
- **No minimum beyond "> 0".** A skeleton package passes on its three linter
  tests alone. The issue asks for "zero tests"; requiring real unit tests of
  packages that have no code yet would block unrelated work. If a
  "linter-only packages must be declared" policy is wanted later, the audit
  already knows each package's count and the report already prints it.
- **`errors`/`failures` are printed but not judged** by the audit —
  `colcon test-result` owns that verdict and the driver already fails on it.
  Duplicating the rule would mean two places to keep in sync.
- **No `docs/design/decisions.md` entry.** This is process tooling, not an
  architectural decision (no invariant is bent); the manager can promote it to
  a decision if they disagree.
- **Touched outside the obvious owned paths** (flagging as required):
  `README.md` (one paragraph documenting the new behaviour) and the one-line
  comment reword in `src/robot_backends/pytest.ini` /
  `src/robot_skills/pytest.ini` so all seven copies of that file are identical.
  No implementation file of `robot_backends`/`robot_skills` was modified.
