# Red team — test-integrity (issue #16)

Reviewed: `origin/main..HEAD` (guard + driver, guard tests, `pixi.toml`, five
skeleton packages' scaffold, `setup.py` `tests_require`→`extras_require`,
`README.md`), against issue #16, `context.md`, `implementation.md`, `CLAUDE.md`.

This change is the trust boundary for every future PR's "green", so it is held
to a higher bar than a normal feature. The core mechanism is sound and the
evidence in `implementation.md` is real (I confirmed `build/*/pytest.xml`
against the shipped colcon 0.21.1 `pytest.py` and pytest 8's `junitxml.py`).
Four things block.

## What is genuinely solid (verified, not taken on trust)

- The diagnosis is correct at the source level. `colcon_core/task/python/test/
  pytest.py:175-178` returns `None` for pytest exit 5, and the pre-run
  placeholder at `pytest.py:145-151` is exactly the XML the guard special-cases.
- **Not honouring `COLCON_IGNORE`** (`check_test_integrity.py:87-111`) is the
  right call and is the single most valuable decision here — verified
  end-to-end by the implementer (`negative_ignore.log`).
- Expected set keyed on `<name>`, not the directory (`_package_name`), matches
  colcon's own keying.
- Delete-before-run then `min_mtime` is correctly *ordered*: `started` is taken
  after `delete_result_files` and before `colcon test`
  (`check_test_integrity.py:400-408`), so there is no window where a
  pre-existing file can be mistaken for fresh. `delete_result_files` only
  unlinks files that parse as xUnit, so `package.xml` survives; a failed unlink
  raises and aborts (fails safe).
- Running all four stages instead of `colcon test && colcon test-result` is a
  real improvement: the old form hid the results summary exactly when it
  mattered, and `colcon test` demonstrably exits 0 on genuine test failures.
- `--all` on `colcon test-result` and the per-package table make the numbers
  legible instead of silent.
- Rejecting unknown `--packages-select` names (`main:382-386`) closes the
  obvious "typo shrinks the expected set" hole.
- `extras_require={'test': ['pytest']}` is now consistent across **all seven**
  packages (grep-verified: `robot_{backends,brain,bringup,description,
  perception,safety,skills}/setup.py`). No `tests_require` remains anywhere,
  and nothing else in the repo references it. This change is correct and
  complete — and it is load-bearing: without it colcon falls through to the
  `unittest` step, which writes no JUnit XML at all.
- The skeleton packages' linter tests follow the existing
  `robot_backends`/`robot_skills` pattern exactly and are not vacuous
  (`ament_copyright` skips `setup.py` beside a `package.xml`, but does check the
  test modules and the LICENSE; `ament_flake8` already caught a real 100-char
  line in `robot_description/setup.py`). Linter-only is honest for packages with
  literally zero implementation code — see NOTE-6 for the ratchet.

---

# BLOCK

## BLOCK-1 — Skipped tests count as "collected"; an all-skipped suite is green

`scripts/check_test_integrity.py:143-160` (`parse_xunit` reads `tests`,
`errors`, `failures` — never `skipped`) and `:224` (`if tests == 0`).

pytest's JUnit writer computes
`numtests = passed + failure + skipped + error`
(`_pytest/junitxml.py:655-661`) and emits `skipped` as a separate attribute
that the guard discards. A module-level skip is also recorded as one skipped
`<testcase>` (`junitxml.py:631-637`).

**Failure scenario.** Someone adds `pytest.importorskip('mujoco')` at the top of
`robot_perception`'s only real test module while the sim backend is
unavailable — or marks a flaky suite `@pytest.mark.skip` "temporarily", or a
future hardware suite guards itself on a device being present. Result XML:
`tests="12" skipped="12" failures="0" errors="0"`. `colcon test` → 0,
`colcon test-result` → 0, and the audit prints

```
robot_perception       12       0         0  ok
```

`pixi run test` exits 0 while **zero test bodies executed**, and the report
does not even hint at it — the table has no `skipped` column, so a human
reading the log cannot notice either. This is the same hollow green issue #16
describes, reached by the most common route in a ROS workspace (import-guarded
tests), and it survives the new guard untouched.

**Fix direction.** Parse `skipped` in `parse_xunit`, add it to `PackageAudit`
and to the report table, and fail the package when `tests - skipped == 0`
(status e.g. `all-skipped`). Keep `tests > 0` as a separate condition so the
two failure modes stay distinguishable in the message. Both existing suites
report `skipped="0"`, so this introduces no false positives today.

## BLOCK-2 — The driver's exit-code composition, the whole point of the change, has zero tests

`scripts/check_test_integrity.py:396-434` (the non-`--audit-only` path:
`rc_test`, `rc_tooling`, `rc_result`, `rc_audit` → `stages` → `return 1`).
`scripts/tests/test_audit.py` — all five `guard.main(...)` calls
(`:330`, `:349`, `:365`, `:385`) pass `--audit-only`; `guard._run` and
`guard.run_tooling_tests` are never monkeypatched or otherwise exercised.

The 31 tests cover `audit_package` / `parse_xunit` / `find_source_packages` /
`delete_result_files` well. They cover the driver **not at all**. That is the
wrong half to leave untested, because `implementation.md` §4 records the
critical fact that **`colcon test` exits 0 on a genuine test failure** — so the
*only* thing that turns a real failing test into a non-zero `pixi run test` is
`rc_result` being folded into `stages`. Nothing tests that.

**Failure scenario.** A future refactor (or a merge resolution) changes the tail
of `main` to `return rc_audit`, or reintroduces short-circuiting
(`if rc_test: return rc_test`), or drops `'colcon test-result': rc_result` from
the `stages` dict. All 31 tests still pass, `pixi run test` still prints
`AUDIT PASSED`, and from then on every genuinely failing test in the workspace
merges green — while the guard's own suite reports 31/31 and the report says
the workspace is honest. The tool that exists to prevent hollow greens becomes
the hollow green.

Per CLAUDE.md ("weak/inadequate tests" = BLOCK; "every feature ships tests that
actually exercise its acceptance criteria"), acceptance criterion (a) —
`pixi run test` is green *and fails when it should* — is currently asserted only
by a one-off manual log, not by a test.

**Fix direction.** Monkeypatch `guard._run` (and `guard.run_tooling_tests`) with
a recorder in a driver test module, point `--source-dir`/`--build-base` at
`tmp_path` with pre-written fresh results, and assert:
1. `main([])` returns 1 when *each* of the four stages fails alone (four
   parametrised cases), and 0 only when all pass;
2. all four stages still execute after an earlier one fails (the "every stage
   runs" invariant), and the report is printed in the failing case;
3. results are deleted before `colcon test` is invoked (record call order);
4. narrowed mode passes `--packages-select` through to `colcon test`, skips the
   tooling suite, does not expect `_workspace_tooling`, prints the PARTIAL
   banner, and deletes only the selected packages' results.

These are cheap (`monkeypatch.setattr(guard, '_run', ...)`) and they are the
tests the acceptance criteria actually ask for.

## BLOCK-3 — Third-party sources under `src/` become permanently red, by design, with no escape hatch

`scripts/check_test_integrity.py:87-111` (`find_source_packages` expects
*every* `package.xml` under `src/`) plus `implementation.md`: "There is
deliberately **no allow-list** of packages exempt from the guard".

This is not hypothetical. `pixi.toml:16-22` already plans exactly this:

```
#   mujoco_ros2_control             (likely source-build via robot.repos)
```

A `.repos`/`vcs import` checkout lands third-party ROS packages under `src/`.
The moment that happens, `pixi run test` fails permanently: the guard demands
those upstream packages produce test results, the team cannot add tests to
vendored upstream code, `COLCON_IGNORE` is (correctly) not honoured, and there
is no exemption mechanism. The predictable reaction under time pressure is to
stop trusting `pixi run test` or to bypass the driver entirely — which
re-opens issue #16 permanently and is worse than the status quo.

The same trap fires for any non-ament CMake package (colcon discovers packages
via `setup.py`/`CMakeLists.txt` too, without a `package.xml` — those are
*under*-audited; vendored `package.xml` packages are *over*-audited).

**Fix direction.** Base the expected set on packages **owned by this repo**
rather than on "anything under `src/`". The cleanest rule that keeps the guard
un-opt-out-able: expect every package whose `package.xml` is **tracked in this
git repo** (`git ls-files src/**/package.xml`) — vcs-imported dependencies are
gitignored, so they drop out automatically, while a first-party package cannot
escape by adding a marker file. If a checked-in policy file is preferred
instead, it must be an explicit, reviewed list of *exclusions with reasons*, not
a marker file inside the package. Either way, do it now: the design note in
`implementation.md` should be updated so the "no exemptions" stance is scoped to
first-party packages.

## BLOCK-4 — Fail-open when the expected package set is empty

`scripts/check_test_integrity.py:99-111` (`os.walk` on a nonexistent directory
yields nothing; no existence check, no "found zero packages" error) and
`:259-290` (`format_report` on a short list prints `AUDIT PASSED`);
`main:359` derives `--source-dir` from `Path(__file__).resolve().parent.parent`.

**Failure scenario.** The script is moved or the layout changes — e.g. it is
relocated to `scripts/ci/check_test_integrity.py`, or `scripts/` is
reorganised, or someone runs it with a mistyped `--source-dir`. `repo_root` now
points one level off, `find_source_packages` walks a directory with no
`package.xml` and returns `[]`, `packages` becomes `['_workspace_tooling']`,
`colcon test` still runs the whole workspace fine, the tooling suite still
writes its 31-test result — and the guard prints:

```
1 packages, 31 tests collected
AUDIT PASSED: every expected package collected tests
```

exit 0, with **all seven ROS packages unaudited**. A trust-boundary tool must
not pass when it fails to find the thing it is auditing. Note this is the one
input the guard never validates, while it is meticulous about validating
everything else (unknown `--packages-select`, `<name>`-less manifests,
malformed XML).

**Fix direction.** Raise/`parser.error` if `--source-dir` does not exist, and if
`find_source_packages` returns zero packages, in the driver and in
`--audit-only`. Add a test (`--source-dir` pointing at an empty tmp dir → exit
non-zero, not `AUDIT PASSED`).

---

# NOTE

## NOTE-1 — `parse_xunit` is stricter than colcon's parser, contrary to its docstring

`scripts/check_test_integrity.py:122-131` claims it "mirrors
`colcon_test_result`'s own xunit parser ... so the guard counts exactly the
files colcon counts", but `:146-148` requires `failures` via
`suite.attrib['failures']` (KeyError → file discarded), whereas
`colcon_test_result` defaults `errors`/`failures` to 0 and only requires
`tests`. A suite XML with `tests` but no `failures` (plausible from a
non-pytest producer once a C++ package lands) would be counted by
`colcon test-result` yet reported by the guard as "no JUnit result file" — and
also skipped by `delete_result_files`, so a stale copy survives the clean.
Fails loudly, so not a blocker. Fix: `int(suite.attrib.get('failures', 0))`,
keep `tests` required, and drop the parity claim or make it true.

## NOTE-2 — Placeholder detection is pytest-specific and combines oddly across files

`:51`, `:157-159`, `:210-223`. `sentinel_only` is ANDed across files and is
`False` for any file with no `<testcase>` elements, so a package holding both
colcon's placeholder (`tests="1"`) and a genuine `tests="0"` result reports
`tests=1, status=ok`. Also, `ament_cmake`'s analogous "did not generate result
file" placeholder uses the test's own name, not `pytest.missing_result`, so a
C++ test binary that crashes before writing results would be counted as one
collected test. In both cases `colcon test-result` still fails the overall run
(the placeholders carry `errors="1"`), so nothing merges hollow — but the
audit's own verdict is wrong. Fix: treat any `<testcase>` carrying a
`<failure>`/`<error>` placeholder marker as non-evidence, and compute
"sentinel-only" over the union of cases rather than ANDing per file.

## NOTE-3 — `--source-dir` / `--build-base` are not passed through to colcon

`:405-417` invoke bare `colcon test` / `colcon test-result` with `cwd=repo_root`
while the audit reads `args.build_base`. `--build-base /tmp/x` therefore
deletes and audits `/tmp/x` while colcon writes to `./build`. It fails loudly
(everything reports `no-result`), so it is a usability/consistency wart, not a
hole. Fix: pass `--build-base`/`--test-result-base` through, or restrict those
flags to `--audit-only`.

## NOTE-4 — No ratchet: silent erosion below the "> 0" bar is invisible

`robot_skills` (59) dropping to 3 linter tests because a `pytest.ini`
`testpaths` edit or an `--ignore` in `addopts` stopped collecting
`test/test_skills.py` passes the audit unremarked. The audit already knows every
count and prints them; a checked-in per-package baseline (or a printed delta vs
the previous run) would catch this cheaply. Follow-up issue material, not a
blocker for #16 as written.

## NOTE-5 — `pixi run test-audit` can report `AUDIT PASSED` from an arbitrarily old build

`:391-394` — documented in `--help` and in `implementation.md`, and it is a
convenience command, so this is acceptable. Cheap improvement: print each
package's newest result age in the table under `--audit-only`, so a stale
"pass" is self-evidently stale.

## NOTE-6 — Linter-only skeleton packages are honest today; they need a ratchet when code lands

`src/robot_{brain,bringup,description,perception,safety}/test/test_*.py`. With
zero implementation code, three real linter tests that genuinely execute and
genuinely fail (they caught a real flake8 violation during this run) are the
honest maximum, and requiring more would block unrelated work — so this is not
a BLOCK. But note what those three tests currently lint in a skeleton package:
an empty `__init__.py` and the three test files themselves. The moment
`robot_safety` grows a clamp function, "3 tests, ok" stops being honest. Pair
with NOTE-4: a follow-up issue for "a package with implementation code must
have non-linter tests".

## NOTE-7 — Operational hygiene

- `_run` (`:320-322`) lets `FileNotFoundError` escape if `colcon` is not on
  PATH; a one-line `except FileNotFoundError` with a clear message would be
  friendlier than a traceback. Fails safe either way.
- Every result file is parsed 2–3× (`find_result_files` → `audit_package` →
  `unexpected_result_dirs`). Irrelevant at this scale; mentioning only so it is
  a deliberate choice.
- Deleting `scripts/tests/test_audit.py` while keeping `test_lint.py` leaves
  `_workspace_tooling` at `tests=3` and green — the "the guard guards itself"
  claim only guarantees *some* tests exist, exactly as for the skeleton
  packages. Accurate to the issue; worth stating plainly in
  `implementation.md` rather than implying the guard's own suite is
  tamper-proof.
- CI still does not run the guard (`.github/workflows/guards.yml`), so a "green
  PR" per CLAUDE.md continues to depend on the laptop test-runner having
  actually run `pixi run test`. Correctly flagged as a follow-up in
  `implementation.md`; restating because it bounds what issue #16 actually
  buys.

---

## Verdict

**Changes requested — 4 BLOCK, 7 NOTE.**

The architecture of the guard is right (evidence-only audit, source-tree-derived
expected set, `COLCON_IGNORE` not honoured, delete-then-freshness, all stages
run, self-audited tooling suite) and the `extras_require` fix is correct and
complete. But two holes let a hollow green through today (BLOCK-1 skips,
BLOCK-4 empty expected set), one guarantees the guard will be abandoned as soon
as `robot.repos` lands (BLOCK-3), and the single most load-bearing piece of
logic in the change — the exit-code composition that converts a real test
failure into a non-zero `pixi run test` — is untested (BLOCK-2). For any other
feature I would push two of these to follow-ups; for the tool that defines what
"green" means, they should be fixed before merge. All four fixes are small and
local.
