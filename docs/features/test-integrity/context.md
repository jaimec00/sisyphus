# Context — test-integrity (issue #16)

Brief: GitHub issue #16 (verbatim in the run's task). Acceptance restated:

1. `pixi run test` is **honestly green** across every package in `src/` — not
   just "colcon didn't error," but every package that's supposed to have
   tests actually ran some.
2. A **guard** exists that **fails** if any package collects **zero tests**.
3. Re-verify `robot_skills` / `robot_backends` (mock-skill-api, issue #4) did
   not merge on a hollow green — confirm their `pixi run test` runs actually
   collect >0 tests once built.

## The mechanism (read from the installed colcon plugins, not guessed)

`pixi.toml:24-26`:
```
build = "colcon build --symlink-install"
test = "colcon test && colcon test-result --verbose"
```

`.pixi` is **not materialized in this worktree** (no `.pixi/` dir, no
`build/`/`log/` yet) — `pixi install` + `pixi run build` have not been run
here. The RoboStack conda packages are however already fetched into the
rattler package cache, so the exact colcon source they'll execute is
inspectable now:
- `colcon-core` 0.21.1:
  `/home/sisyphus/.cache/rattler/cache/pkgs/colcon-core-0.21.1-pyhcf101f3_0/site-packages/colcon_core/`
- `colcon-test-result` 0.3.8:
  `/home/sisyphus/.cache/rattler/cache/pkgs/colcon-test-result-0.3.8-pyhd8ed1ab_1/site-packages/colcon_test_result/`

**Why a package with zero tests can still be "green":**

1. `colcon_core/package_augmentation/python.py:77-92` (`extract_dependencies`)
   maps **both** old-style `tests_require=[...]` (used by
   `src/robot_brain/setup.py:20`) **and** `extras_require={'test': [...]}`
   (used by all 6 other packages, e.g. `src/robot_backends/setup.py:20`,
   `src/robot_skills/setup.py:20`) into the same `'test'` dependency bucket.
2. `colcon_core/task/python/test/pytest.py:49-50`
   (`PytestPythonTestingStep.match`) claims a package purely because
   `has_test_dependency(setup_py_data, 'pytest')` is true — **it never checks
   whether a `test/` directory or any `test_*.py` file exists.** Every one of
   the 7 `src/robot_*` packages declares this dependency (via `package.xml`
   `<test_depend>python3-pytest</test_depend>` and the matching
   `setup.py`/`extras_require`), so the pytest step runs for **all seven**,
   including the five that have no test files at all.
3. `pytest.py:52-63`: the step runs `python -m pytest` with **cwd =
   `context.args.path`** (the package's `src/<pkg>` directory) and **no
   explicit test-path argument**, writing JUnit XML to
   `Path(build_base) / 'pytest.xml'` — i.e. **`build/<pkg>/pytest.xml`**.
   Discovery therefore depends entirely on pytest's default rootdir
   discovery plus that package's own `pytest.ini`/`setup.cfg` (see below).
4. `pytest.py:168-178` — **the actual bug the issue describes**:
   ```python
   try:
       from _pytest.main import ExitCode
       EXIT_CODE_NO_TESTS = ExitCode.NO_TESTS_COLLECTED
   except ImportError:
       from _pytest.main import EXIT_NOTESTSCOLLECTED
       EXIT_CODE_NO_TESTS = EXIT_NOTESTSCOLLECTED
   if completed.returncode not in (
       EXIT_CODE_NO_TESTS, EXIT_CODE_TESTS_FAILED
   ):
       return completed.returncode
   ```
   When pytest exits `5` (`NO_TESTS_COLLECTED`), the function falls off the
   end and implicitly returns `None` — colcon does **not** treat that as a
   task failure. `colcon test` for that package is reported as fine.
5. `colcon_test_result/test_result/xunit.py` (`XunitTestResult`) walks
   `build/` for `*.xml`, requires a `tests` attribute (raises/skip if
   absent, `xunit.py:117-122`), and defaults `errors`/`failures` to `0` if
   missing. `colcon_test_result/verb/test_result.py:83-108`
   (`TestResultVerb.main`) — the exact thing `pixi run test` invokes via
   `colcon test-result --verbose` — only **prints** a result if
   `error_count or failure_count or --all`, and the final exit code is `1 if
   summary.error_count or summary.failure_count else 0`. A `pytest.xml` with
   `tests="0" errors="0" failures="0"` therefore is invisible in the default
   (non-`--all`) output **and never fails the summary**.

Net effect: a package that ships zero test files still (a) matches the
pytest testing step, (b) produces `build/<pkg>/pytest.xml` with `tests="0"`,
and (c) is completely silent and non-failing in both `colcon test` and
`colcon test-result --verbose`. This is exactly the "hollow green" issue
#16 describes, reproduced from the real installed plugin code rather than
assumed.

Caveat: this is reasoned from the plugin source, not yet observed by
actually running `pixi install && pixi run build && pixi run test` in this
worktree (no `build/`/`log/` exist yet). The implementer should run that
once, at least for one clean and one intentionally-empty package, to confirm
the exact `build/<pkg>/pytest.xml` shape (e.g. whether pytest's own
`--junit-xml` writes a `tests="0"` testsuite for a zero-collection run, or
whether the file is just the "dummy result" `pytest.py:142-151` writes
before invocation and gets overwritten) before hard-coding a parser against
it.

## Per-package inventory (`src/`)

All 7 are `ament_python` (`<build_type>ament_python</build_type>` in every
`package.xml`) and all declare the same four `<test_depend>`s
(`ament_copyright`, `ament_flake8`, `ament_pep257`, `python3-pytest`).

| package | `test/` dir | `pytest.ini` | test files | status |
|---|---|---|---|---|
| `robot_backends` | yes (`src/robot_backends/test/`) | yes | `conftest.py`, `mock_backend_fixtures.py`, `test_mock_scenario.py`, `test_mock_failures.py`, `test_flake8.py`, `test_copyright.py`, `test_backend_interface.py`, `test_pep257.py`, `test_no_ros_runtime.py`, `test_mock_world.py`, `test_mock_skills.py` | real, substantial suite |
| `robot_skills` | yes (`src/robot_skills/test/`) | yes | `test_flake8.py`, `conftest.py`, `test_copyright.py`, `test_pep257.py`, `test_geometry.py`, `skill_api_fixtures.py`, `test_observation.py`, `test_skill_result.py`, `test_skill_serialization.py`, `test_skills.py` | real, substantial suite |
| `robot_brain` | **none** | **none** | **none** | skeleton: `robot_brain/__init__.py` is 0 bytes, `README.md` says "Status: skeleton" |
| `robot_bringup` | **none** | **none** | **none** | skeleton, empty `__init__.py` |
| `robot_perception` | **none** | **none** | **none** | skeleton, empty `__init__.py` |
| `robot_description` | **none** | **none** | **none** | skeleton, empty `__init__.py` |
| `robot_safety` | **none** | **none** | **none** | skeleton, empty `__init__.py` |

So 5 of 7 packages currently have **zero** test files despite declaring
`test_depend`s that imply a linted, tested package (the pattern in
`robot_backends`/`robot_skills` is: one `test_flake8.py` +
`test_copyright.py` + `test_pep257.py` per package that literally invokes
`ament_flake8`/`ament_copyright`/`ament_pep257` as pytest tests — see
`src/robot_backends/test/test_flake8.py:9-18`,
`src/robot_backends/test/test_copyright.py:9-18`,
`src/robot_backends/test/test_pep257.py:15-24`). None of the 5 skeleton
packages have this pattern at all yet.

`robot_backends/pytest.ini:1-15` and `robot_skills/pytest.ini:1-15` are
identical and explain a real environment gotcha:
```
[pytest]
addopts = -p no:launch_testing -p no:launch_ros
testpaths = test
```
RoboStack's `launch_testing`/`launch_ros` pytest plugins declare hooks pytest
>= 8 rejects (`PluginValidationError` on `pytest_pycollect_makemodule`),
which **aborts the whole pytest session**, not just those plugins' tests.
Both existing packages disable the plugins and pin `testpaths = test`. The 5
skeleton packages have **no such file**, so if/when tests are added to them,
either the same `pytest.ini` (or an equivalent `setup.cfg [tool:pytest]`) is
needed, or they'll hit the same `PluginValidationError` the moment pytest
tries to collect anything — this could itself look like another flavor of
"green with 0 real tests" if mishandled (a session-abort exit code other
than 0/1/5 currently just `return completed.returncode` at
`pytest.py:178`, which **does** propagate as a task failure, so this
particular failure mode is not silent — but it's a real pitfall worth
knowing before adding tests to the skeleton packages).

`setup.cfg` in every package (e.g. `src/robot_backends/setup.cfg:1-5`) only
sets `[develop]`/`[install]` script dirs — no pytest config there; pytest
config lives solely in the per-package `pytest.ini` where present.

## CI / workflow context

`.github/workflows/guards.yml` is the **only** GitHub Actions workflow. It
runs on `pull_request` and does exactly one thing: fail if
`docs/features/*` is tracked (ephemeral docs must be gone before merge). It
does **not** check out a ROS/pixi environment and does **not** run
`colcon build`/`colcon test` — per `DEVELOPMENT.md:74-77` ("GitHub CI (PRs):
light guards... Heavy tests run on the laptop"), the full suite is a
laptop-only concern (test-runner subagent, nightly cron), not CI. Adding a
zero-tests guard to `guards.yml` would be a scope change (it would need a
full RoboStack/pixi environment in CI, which nothing currently provisions)
— worth flagging to the manager rather than assuming, since the issue only
asks for `pixi run test` to be honest, not for a new CI job. A guard that
runs as an extra step in `pixi run test` (or a script `pixi run test` calls
after `colcon test-result`) satisfies the literal acceptance criteria
without touching CI.

## `.dev/runs/` and `scripts/` conventions

- `.dev/runs/<slug>/<timestamp>/` is gitignored, holds run/test logs, kept
  until merge (`DEVELOPMENT.md:71-73`). This run's dir already exists:
  `.dev/runs/i16-test-integrity-make-pixi-run-test-honest/20260810-041116/manager.log`
  (currently empty).
- `scripts/` currently holds one file, `scripts/start-feature.sh` — an ops
  dispatcher, not a test tool. It's `bash`, `set -euo pipefail`, with a
  `die()` helper — the only existing style precedent in `scripts/`, though a
  guard that parses JUnit XML is likely more natural in Python (the repo has
  no other bash-vs-python precedent for this kind of tool).
- `.claude/agents/test-runner.md:8-9` — the test-runner subagent's default
  command is `pixi run test`, narrowed to the feature's packages "when
  appropriate." Whatever the guard becomes, it should still be invocable via
  `pixi run test` so that default keeps working unmodified.

## Architectural invariants that apply (CLAUDE.md)

This feature is process/tooling, not robot behavior, so most of the five
invariants (skill-API seam, backend abstraction, safety layer, structured
perception JSON) don't bear directly — the relevant ones are:
- "**Reuse** frameworks... don't reinvent" — colcon/pytest already produce
  everything needed (JUnit XML with a `tests` count per package); the guard
  should be a thin, honest reading of that output, not a parallel test
  harness.
- "Every feature ships tests that actually exercise its acceptance
  criteria" — the guard itself needs a test proving it **catches** a
  zero-test package (e.g. a fixture package/XML fixture with `tests="0"`)
  and does **not** false-positive on the two real suites.

## Owned paths (proposed — for the manager/implementer to confirm)

Likely in scope:
- `pixi.toml` — the `test` task, to wire in the guard.
- A new guard script, e.g. `scripts/check_test_coverage.py` (name TBD) —
  parses `build/*/pytest.xml` (or wherever `colcon test` actually lands
  them once verified — see caveat above) and fails non-zero if any expected
  package's `tests` count is `0` or the file is missing.
- `src/robot_brain/`, `src/robot_bringup/`, `src/robot_perception/`,
  `src/robot_description/`, `src/robot_safety/` — each needs at minimum the
  same `test/test_flake8.py` + `test/test_copyright.py` + `test/test_pep257.py`
  pattern already used in `robot_backends`/`robot_skills` (plus a matching
  `pytest.ini` disabling `launch_testing`/`launch_ros`) so the guard has
  something legitimate to require rather than just failing forever on
  packages that are intentionally still skeletons. Whether the guard should
  *require* non-linter unit tests too, or accept "linter-only is fine for a
  skeleton package," is a real design choice for the implementer — the issue
  only says "collects zero tests," and the linter tests do genuinely collect
  as pytest tests.
- Possibly `docs/design/decisions.md` if this warrants a recorded decision
  (repo convention per `CLAUDE.md`: "do not violate [invariants] without a
  recorded design decision" — this isn't an invariant violation but is a
  process rule worth a decision entry if the guard's policy is non-obvious).

Must **not** touch:
- `.github/workflows/guards.yml` unless the implementer deliberately decides
  CI should also run the guard (see CI section above — flag, don't assume).
- Any `docs/features/<slug>/` directory other than this one.
- `docs/design/PROJECT.md` (read-only design source per `CLAUDE.md`).
- Unrelated package internals (`robot_backends/robot_backends/*.py`,
  `robot_skills/robot_skills/*.py`) — those two packages' *tests* are the
  ones to re-verify (issue's item 3), not their implementation.

## Gotchas recap

- The bug is in **swallowed exit codes** (`NO_TESTS_COLLECTED` treated as
  non-fatal), not in colcon "not running" tests — the pytest step *does*
  run for every package regardless of whether it has a `test/` dir, because
  `match()` only checks the `test_depend`/`extras_require` metadata.
- `colcon test-result` also independently hides zero-error/zero-failure
  results by default (`--all` is required to see them) — so even a human
  running `colcon test-result --verbose` by hand after a hollow `colcon
  test` won't notice anything wrong without inspecting `tests="0"` values
  directly.
- `robot_brain/setup.py` uses the older `tests_require=['pytest']` kwarg
  while the other 6 use `extras_require={'test': ['pytest']}` — both are
  recognized identically by colcon's dependency extraction
  (`package.py:77-92`), so this inconsistency doesn't affect the guard, but
  a fix that touches `robot_brain/setup.py` for consistency is optional
  polish, not required.
- No `build/`/`log/` directories exist yet in this worktree — first step for
  the implementer is `pixi install && pixi run build` to get a real
  baseline before writing/validating the guard.
