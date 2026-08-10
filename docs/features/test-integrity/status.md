# status — test-integrity (issue #16)

- **Branch:** `feat/i16-test-integrity-make-pixi-run-test-honest`
- **Base:** `origin/main` @ 3e3355b (up to date at start)

| field | value |
|---|---|
| phase | ready (PR open, awaiting Sisyphus merge) |
| round | 2 (final) |
| blockers | none |

## Log
- **context** — done → `context.md`. Root cause confirmed from installed colcon
  source: `colcon_core/task/python/test/pytest.py` swallows pytest exit code 5
  (`NO_TESTS_COLLECTED`), and `colcon test-result` hides zero-error results
  without `--all`. 5 of 7 packages have zero test files.
- **implement** — dispatched implementer. `pixi install` running in background.
- **implement** — done → `implementation.md`. `pixi run build` + `pixi run test`
  green from a clean `build/` (163 tests, 8 audited units). No escalations, no
  design forks. Correction to the context doc: the 5 skeleton packages were not
  on the pytest path at all (`tests_require` is dropped by modern setuptools),
  so they ran colcon's `unittest` fallback, which writes **no** result file —
  they failed loudly only because Python 3.12 unittest exits 5 on an empty run.
  The predicted `tests="0"` hollow green was reproduced separately in a scratch
  scenario and is quoted verbatim in `implementation.md`.
- **red-team round 1** — 4 BLOCK, 7 NOTE → `red_team.md`.
- **fix round 1** — done → `implementation.md` § "Red-team round 1". All four
  BLOCKs fixed (all-skipped suites now fail; the driver's exit-code
  composition has 19 tests; the expected set is now the git-tracked
  first-party packages so `robot.repos` cannot make the run permanently red;
  an empty/wrong `--source-dir` is an error, not `AUDIT PASSED`). NOTE-1/3/5/7
  addressed; NOTE-2 and NOTE-4+6 deferred to follow-up issues (manager to
  file). Each fix mutation-checked: reverting it turns the new tests red.
  `pixi run build` + `pixi run test` green from a deleted `build/` (198 tests,
  8 audited units); five negative scenarios re-verified red.
  Logs: `.dev/runs/i16-test-integrity-make-pixi-run-test-honest/20260810-044847/`.
  One correction worth recording: NOTE-1's premise was wrong —
  `colcon_test_result` requires `failures` as well as `tests`, so the guard's
  strictness was already parity; the docstring was fixed rather than the code.
  No escalations, no design forks.
- **red-team round 2** — **APPROVE, 0 BLOCK, 6 new NOTE** (NOTE-8…13) →
  `red_team.md` § "Round 2". All four round-1 BLOCKs verified fixed *in code*,
  not taken on the implementer's word; the reviewer spot-checked ten unlisted
  mutations of the driver (nine caught) and attacked the new git-ownership
  model on worktrees, nested repos, submodules, missing `git` and tarball
  checkouts. Key structural result: a malfunctioning ownership probe can only
  shrink the expected set to empty, which lands on BLOCK-4's `parser.error` —
  so the guard fails hard rather than passing silently. Test adequacy judged
  adequate. Round limit reached; surviving NOTES go to the issue as follow-ups.
- **test-runner** — **PASS** from a wiped `build/ install/ log/`. `pixi run
  build` exit 0, `pixi run test` exit 0: 8 packages, **198 tests**, 0 skipped,
  0 errors, 0 failures, `AUDIT PASSED`.
  Logs: `.dev/runs/i16-test-integrity-make-pixi-run-test-honest/20260810_090937/`.
- **ready** — rebased on `origin/main` @ 3e3355b (already current), PR opened.
  Follow-ups (NOTE-2, NOTE-4+6, NOTE-8…13, CI item) posted as a comment on
  issue #16 for Sisyphus to file; retro posted on the PR.
  Reminder for Sisyphus: `docs/features/test-integrity/` is intentionally still
  present — the CI "docs clean" check reads red until you delete it at merge.
