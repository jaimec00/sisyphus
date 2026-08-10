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

---

# Round 2

Final review round. Re-read the current `scripts/check_test_integrity.py`,
`scripts/tests/{test_audit,test_driver,test_lint,conftest}.py`, `pixi.toml`,
`README.md` and `implementation.md`, plus the shipped
`colcon_test_result/test_result/xunit.py` and `colcon_core/verb/test.py` to
re-check the parity and flag claims. Verdicts below are from the code, not from
the implementer's summary.

## Round-1 BLOCK verification

### BLOCK-1 (all-skipped suite is green) — **FIXED**

- `check_test_integrity.py:62` `SKIPPED_ATTRIBUTES = ('skip', 'skipped',
  'disabled')`; `:238-239` sums all three per `<testsuite>`; `:66-67`
  `XUnitCounts` carries `skipped`.
- `:92-95` `PackageAudit.executed = tests - skipped`; `:331-335` sets the new
  `_STATUS_ALL_SKIPPED` when `executed <= 0`, kept distinct from
  `_STATUS_ZERO_TESTS` at `:328-330`, so the two failure modes stay legible.
- Visibility (the half of BLOCK-1 that was about a human not being able to
  notice) is also fixed: `:379` adds a `skipped` column, `:400-403` appends
  `(N skipped)` to the totals line.

I verified the parity claim directly against the shipped
`colcon_test_result/test_result/xunit.py:108-115` — colcon sums exactly
`skip` + `skipped` + `disabled` into one `skipped_count`, requires `tests` and
`failures`, and defaults `errors` and the three skip attributes to 0. The
guard's `parse_xunit` (`:232-247`) now matches that rule attribute for
attribute, which incidentally also resolves round-1 NOTE-1 correctly (my
round-1 premise about `failures` being optional was wrong; `failures` has
default `None` in colcon's table, i.e. required — the implementer caught this
and fixed the docstring rather than the code, which is right).

Behaviour I probed that the fix handles correctly:
- multiple `<testsuite>` children under one `<testsuites>`: summed (`:233-247`);
- multiple result files per package: summed (`:309-315`), and
  `test_skips_are_summed_across_result_files` (`test_audit.py:150-160`) pins
  that one non-skipped test anywhere in the package clears the status;
- pytest reports `xfail` in the `skipped` bucket, so an all-`xfail` package
  reports `all-skipped`. Arguably a false positive (xfail bodies do run), but
  it is fail-loud and a whole package of xfails deserves a look. Not a finding.
- `skipped > tests` (malformed but non-negative) → `executed < 0` → still
  `all-skipped`. Correct.

One residual, deliberately out of scope of BLOCK-1, in NOTE-12 below: a suite
where every test *errors* is still `ok` to the audit.

### BLOCK-2 (untested exit-code composition) — **FIXED**

`scripts/tests/test_driver.py` (194 lines, 19 tests) is new and does the right
thing: it patches `guard._run`, `guard.run_tooling_tests` and
`guard.delete_result_files` (`test_driver.py:57-61`) but lets the *fake*
`colcon test` write real JUnit files (`:73-77`) and lets the real
`delete_result_files` run underneath the recorder (`:63-66`), so the audit
still judges real files on disk. That is the difference between a contract test
and a mock-shaped tautology.

The four asserts I asked for are all present and non-trivial:
1. `test_no_single_failing_stage_can_be_swallowed` (`:125-143`) parametrises
   over all four stages, asserts `rc == 1` **and** that the `FAILED stages:`
   line names exactly that stage — so a mutation that drops any one key from
   the `stages` dict (`check_test_integrity.py:586-591`) fails.
2. `test_every_stage_runs_and_the_report_prints_after_an_early_failure`
   (`:176-188`) asserts the full event order after `colcon test` fails, killing
   any re-introduced short-circuit.
3. `test_results_are_deleted_before_colcon_test_runs` (`:206-212`) asserts the
   ordering by index, and `test_a_stale_result_cannot_stand_in_for_a_package_
   colcon_skipped` (`:215-226`) asserts the *consequence* (`'99' not in out`),
   which is the stronger of the two.
4. Narrowed mode: `:255-287` cover passthrough, tooling skipped, PARTIAL
   banner, selective deletion, and that narrowing does not weaken the verdict.

**Spot-check of unlisted mutations** (the thing you asked me to try — I reasoned
these through the test bodies rather than trusting the implementer's table):

| mutation | caught by |
|---|---|
| drop `'workspace-tooling tests': rc_tooling` from `stages` | `:125` param case `TOOLING` (rc 1 + named) |
| `failed = [... if rc]` → `if rc > 1` | every `rc == 1` param case |
| `rc_audit = 0 if all(...) else 1` → `rc_audit = 0` | `:125` AUDIT case, `:163`, `:229` |
| print the report only when failing | `:113` (`'AUDIT PASSED' in out`) |
| drop `--packages-select` passthrough to colcon | `:261` |
| move `packages.append(TOOLING_PACKAGE)` into the narrowed branch | `:229`, `:263-264` |
| `executed <= 0` → `executed < 0` | `test_audit.py:120` |
| shrink `SKIPPED_ATTRIBUTES` to `('skipped',)` | `test_audit.py:163-176` |
| invert the "no git signal" fallback to `return [], []` | `test_audit.py:484-493` |
| remove the empty-expected-set `parser.error` | `test_audit.py:513`, `:539` |

The "swap `max()` for `min()` over stage codes" mutation does not apply: the
driver does not compose a numeric max, it returns a flat `1` if any stage
non-zero (`:592-597`), which the parametrised test covers exhaustively.

One mutation **survives**: deleting `min_mtime=started` from the audit call
(`:582`) leaves all 19 driver tests green, because pre-run deletion covers the
same scenarios. See NOTE-13 — low severity, the freshness rule itself is unit
tested in `test_audit.py:291-327`.

### BLOCK-3 (vendored sources permanently red) — **FIXED**

`discover_packages` (`:167-192`) now splits `find_manifests` output into
`expected` (git-tracked) and `unowned`, and `find_source_packages` (`:195-197`)
is a thin wrapper. I attacked this model along every axis you named:

- **Worktree.** This repo *is* a linked worktree (`.git` is a file). `git -C
  <src> rev-parse --is-inside-work-tree` and `git -C <src> ls-files -z` both
  read the worktree's own index and work normally. Decisive evidence that the
  git path (not the fallback) is live here: `vendored_untracked.log` shows
  `mujoco_ros2_control` classified as *unowned* while the seven `robot_*`
  packages stayed expected — under the fallback every package is expected, so
  that output is only reachable via a working `git ls-files`.
- **Path matching.** `ls-files` with `-C <dir>` emits paths relative to that
  dir and, crucially, `-z` suppresses `core.quotePath` escaping, so non-ASCII
  paths do not silently fall out of the tracked set. Both sides of the
  comparison are `.resolve()`d (`:162`, `:190`), so a symlinked `src/` matches.
- **A package added but not `git add`ed** → unowned + a `note:` line, not
  silently dropped (`:483-487`, tested at `test_audit.py:428-443`). This is the
  right trade: on a branch that is about to become a PR the manifest is tracked
  by construction, and the local pre-`git add` window is loudly annotated.
- **git absent from PATH** → `subprocess.run` raises `FileNotFoundError`
  (an `OSError`), caught at `:140-141` → `None` → expect everything. **Tarball
  export / non-git checkout** → `rev-parse` non-zero → `None` → expect
  everything (`:154-155`). Both fail *loud*, never silent.
- **Can a git failure be misread?** No, and this is the part I was most
  suspicious of. The only way to reach a wrongly-small expected set is
  `rev-parse` succeeding with `true` *and* `ls-files` succeeding with output
  that omits real manifests. In that case every first-party package lands in
  `unowned`, `expected` is empty, and BLOCK-4's `parser.error` fires
  (`:526-531`) — i.e. a malfunctioning ownership probe is always a hard exit,
  never a pass. `test_a_source_tree_of_only_vendored_packages_is_an_error`
  (`test_audit.py:539-553`) pins exactly that composition. Good design; the two
  fixes reinforce each other rather than interacting badly.
- **A nested git repo under `src/`** (which is what `vcs import` creates) is not
  listed by the superproject's `ls-files`, so vendored trees drop out
  automatically — no `.gitignore` entry required. **Submodules** are listed only
  as a gitlink, so their `package.xml` is likewise unowned. Both are the
  intended semantics ("owned by *this* repo"); worth knowing that vendoring a
  first-party package as a submodule would exempt it, but that is a visible,
  reviewable act, same as removing a manifest from the index.
- **Opt-out resistance preserved.** `test_a_first_party_package_cannot_opt_out_
  with_a_marker_file` (`test_audit.py:461-471`) and
  `test_a_gitignored_first_party_package_is_still_expected` (`:474-481`) pin the
  two ways someone would try. `COLCON_IGNORE` still grants nothing — verified
  in code (`find_manifests` never looks at markers) and in tests.

The tests for this fix are real: they `git init` a tmp repo and `git add` actual
files (`test_audit.py:75-83`) rather than monkeypatching `_git`, so they would
catch a regression in the actual command line, not just in the plumbing.

### BLOCK-4 (fail-open on an empty expected set) — **FIXED**

`main:522-531`: `--source-dir` must be a directory, and the discovered owned set
must be non-empty; both are `parser.error` (exit **2**, distinct from the
stage-failure exit **1** at `:595` and from `_run`'s 127 for a missing command).
Placement is correct and unconditional — the checks sit *before* the `narrowed`
branch, before `--audit-only`'s return at `:546-551`, and before any deletion or
subprocess, so they cover every code path including `--audit-only` and
`--packages-select`. `test_driver.py:290-296` asserts `workspace.events == []`
after a bad `--source-dir`, which proves nothing ran (delete included), not just
that the exit code was non-zero. Three more cases at `test_audit.py:513-553`.

The deliberate decision *not* to add an "implausibly small" floor
(`implementation.md:355-361`) is the right call — a hardcoded `>= 7` would be a
lie the first time a package is split.

## New findings

No BLOCKs. Everything below is NOTE (follow-up material), numbered continuing
from round 1.

### NOTE-8 — A malformed or `<name>`-less *vendored* manifest still aborts the whole run

`check_test_integrity.py:126-130` (`find_manifests` raises `ValueError` for a
missing `<name>`) and `:200-205` (`_package_name` lets
`ElementTree.ParseError` escape). Both run over **every** manifest found,
*before* `discover_packages` (`:184-185`) applies the ownership filter.

**Scenario.** `vcs import` lands a third-party tree under `src/` that contains a
`package.xml` the guard cannot parse — a non-ROS file that happens to be named
`package.xml`, a manifest with an XML entity the vendored repo tolerates, or a
template with no `<name>`. `pixi run test` then dies with a `ValueError` or a
raw `ParseError` traceback before a single test runs. That is a smaller,
lower-probability instance of exactly the BLOCK-3 failure mode: third-party
content under `src/` making the driver unfixably red.

**Fix.** Filter by ownership first, then resolve names: keep the hard error for
a *tracked* manifest (that is a real repo bug and must be loud) and demote an
unparseable *untracked* manifest to an `unowned` entry with a `note:` line
explaining why it was skipped.

### NOTE-9 — Vendored packages get two contradictory notes in the report

`check_test_integrity.py:483-496` passes only `packages` (expected + tooling) to
`unexpected_result_dirs` (`:349-361`), so once colcon builds and tests a
vendored package, `build/mujoco_ros2_control/` holds results for a name that is
not in the expected set. The report then prints both

```
note: mujoco_ros2_control is in the source tree but not tracked ...
note: build/mujoco_ros2_control holds results for a package that is not in the
      source tree (leftover?)
```

The second is false and directly contradicts the first, which erodes trust in
exactly the report this feature exists to make trustworthy. Fix: pass
`set(packages) | set(unowned)` as the "known" set to `unexpected_result_dirs`.
Not caught by tests because `_notes` is only exercised through the tracked/
untracked case where no build dir exists (`test_audit.py:428-443`).

### NOTE-10 — `git` is now a hard dependency of the tooling suite but is undeclared

`scripts/tests/test_audit.py:75-83` shells out to `git init` / `git add` with
`check=True`, and `:496-510` runs `discover_packages` against the real repo.
`pixi.toml:9-14` does not list `git`, so the suite depends on the ambient system
git leaking into the pixi environment. On a machine without git (a container
image built from `pixi install` alone) seven tooling tests error out and
`pixi run test` is red for an environmental reason. Fix: add `git` to
`[dependencies]`. Do **not** make the tests skip on missing git — with the
all-skipped rule now in place a partial skip is visible in the table but not
fatal, which is the wrong outcome for this particular capability.

### NOTE-11 — The one test that touches the real repo passes identically under the fallback

`scripts/tests/test_audit.py:496-510`
(`test_the_real_workspace_manifests_are_tracked`) asserts `ours <= expected` and
`not ours & unowned`. Under the no-git fallback (`:187`) `expected` is *every*
package and `unowned` is `[]`, so both assertions hold. The test therefore
cannot distinguish "git ownership works in this checkout" from "the ownership
probe silently degraded". The docstring's caution about not asserting
`unowned == []` is right and should stay; the missing half is one line:
`assert guard._git_tracked_manifests(REPO_ROOT / 'src') is not None`. Low
severity only because a degraded probe is fail-loud in both directions (see the
BLOCK-3 verdict above) — but it means the git path's behaviour in a *worktree*
is currently pinned by a manual log, not by CI-able evidence.

### NOTE-12 — A suite in which every test errors is `ok` to the audit

`check_test_integrity.py:317-336` judges only `tests`, `skipped` and the
sentinel; `errors`/`failures` are recorded and printed but never fail a
package (a deliberate choice, `implementation.md:444-446`). A package whose only
test module fails to import reports `tests="1" errors="1"`; twelve setup errors
report `tests="12" errors="12"`. Both come out `status=ok`.

In the full driver this is harmless — `colcon test` and `colcon test-result`
both go non-zero, so the run is red. The gap is `pixi run test-audit`
(`pixi.toml:32`), which returns **0** and prints `AUDIT PASSED: every expected
package collected tests` over a table showing `errors 12`. README:35-36 sells
that command as "re-read the last run's results", and a human will read the
banner, not the column. Fix: in `--audit-only` either include errors/failures in
the verdict, or make the final line read e.g. `AUDIT PASSED (12 errors, 0
failures — see colcon test-result)` so the banner cannot be read as "all well".

### NOTE-13 — The driver's freshness argument is not pinned by any driver test

`check_test_integrity.py:582` (`audit(..., min_mtime=started)`). Removing
`min_mtime=started` leaves all 19 driver tests green, because pre-run deletion
independently covers every scenario they exercise. The second layer is
explicitly described as defence-in-depth for a failed/partial clean
(`implementation.md:169-178`), and `audit_package`'s staleness logic is well
unit-tested (`test_audit.py:291-327`) — but the *wiring* is not. One test that
no-ops the patched `delete_result_files`, leaves an old result for a package the
fake colcon does not regenerate, and asserts `stale` would close it.

## Paths touched

Confined to the feature's owned paths. `docs/features/` contains only
`test-integrity/` (`context.md`, `status.md`, `implementation.md`,
`red_team.md`) — no other feature's docs exist on this branch.
`.github/workflows/guards.yml` and `docs/design/{PROJECT.md,decisions.md}` are
present and unmodified (oldest mtimes in the tree; `implementation.md:15-16`
and `:434-438` also record the deliberate decision to leave CI alone).
Round 2's additions are `scripts/check_test_integrity.py`,
`scripts/tests/test_{audit,driver}.py`, `README.md` (six lines, accurate to the
shipped behaviour including the ownership rule and the all-skipped case) and
`docs/features/test-integrity/*`. Round 1's cross-cutting touches
(`src/robot_*/…`, `pixi.toml`) are unchanged since round 1 and were already
flagged by the implementer.

## Test adequacy (explicit assessment)

**Adequate.** 66 tests across three modules, and — the part that matters — they
test the contract rather than the implementation:

- `test_audit.py` fixtures are the XML shapes actually observed from this
  colcon/pytest pair, including the verbatim colcon placeholder
  (`test_audit.py:43-50`), so they will keep meaning what they mean.
- The git-ownership tests use a real `git init`/`git add`, not a patched `_git`
  (`test_audit.py:75-83`) — they would catch a wrong command line or wrong path
  relativity, which a mock would not.
- `test_driver.py`'s fakes stop at the process boundary: the audit under test
  still reads real files written by the fake stage (`:73-77`), so the tests
  cannot pass on a guard that has stopped reading evidence.
- The parametrisation at `:125-143` asserts *which* stage is named, not merely
  that the run failed — that is what makes it a mutation-resistant test of the
  composition rather than of the exit code.

Residual gaps are NOTE-11 (real-repo git path not pinned) and NOTE-13
(`min_mtime` wiring not pinned); neither can hide a hollow green, both are
one-test fixes.

## Round 2 verdict

**APPROVE — 0 BLOCK, 6 NOTE (NOTE-8 … NOTE-13, plus round 1's surviving
NOTE-2, NOTE-4, NOTE-6 and the CI item in NOTE-7).**

All four round-1 BLOCKs are genuinely fixed in code, not papered over, and the
fixes compose well: the ownership filter's failure modes all land on BLOCK-4's
refusal to audit nothing, so a malfunctioning guard exits non-zero instead of
passing. The new driver tests are the ones the acceptance criteria asked for
and they survive mutations beyond the four the implementer listed. Nothing I
found in round 2 can produce a hollow green through `pixi run test`; the
remaining notes are report accuracy (NOTE-9, NOTE-12), robustness against
malformed vendored input (NOTE-8), environment declaration (NOTE-10) and two
missing pins (NOTE-11, NOTE-13). All are follow-up material, not blockers.
