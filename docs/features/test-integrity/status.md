# status — test-integrity (issue #16)

- **Branch:** `feat/i16-test-integrity-make-pixi-run-test-honest`
- **Base:** `origin/main` @ 3e3355b (up to date at start)

| field | value |
|---|---|
| phase | implement |
| round | 0 |
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
