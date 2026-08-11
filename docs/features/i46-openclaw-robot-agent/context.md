# Context: #46 — First milestone: OpenClaw `robot` agent → robot_mcp → Mock (D21)

Fresh-implementer map. Every claim is either **empirically-observed** (a
command was run and its output inspected) or **inferred-from-source** (read,
not executed) — labelled inline. Repo root:
`/home/sisyphus/worktrees/i46-first-milestone-openclaw-robot-agent-rob`.

## Acceptance criteria restated

1. `robot_mcp` exposes skills as MCP tools (already true), and its tool
   boundary calls through `robot_safety.SafetyLayer` before ever calling
   `backend.execute()` — a safety clamp/abort is visible in the tool result.
2. Mock `RobotBackend` stays the executing backend (already true — no ROS/sim).
3. An OpenClaw `robot` agent config + operating prompt exists (Pi-side,
   authored as files this repo can own and test even though OpenClaw itself
   doesn't run here).
4. End state: "clear the table" texted to the `robot` agent drives a
   `navigate_to`/`grasp`/`place` loop against Mock, safety-clamped
   server-side, ending in a coherent natural-language report.

## Owned paths (from the brief)

- `src/robot_mcp` — wire the safety layer into `SkillToolRouter`.
- `src/robot_safety` — already landed (#49/#43); read-only unless a real gap
  is found (see open questions).
- New: OpenClaw agent config + prompt, location TBD (§5, an open question).

---

## 1. The safety-insertion seam

`src/robot_mcp/robot_mcp/server.py:138-159` is `SkillToolRouter._payload`. The
skill branch is:

```python
skill = skill_from_dict({SKILL_KEY: name, **arguments})
async with self._lock:
    result = self._backend.execute(skill)
return result.to_dict()
```

`self._backend.execute(skill)` (`server.py:158`) is called **directly, with no
safety check** — this is the entire gap. The fix point is inside this method
(or a thin wrapper `SkillToolRouter` calls), still under `self._lock`.

**Building a `SafetyState`.** `robot_safety/robot_safety/state.py:74-94`:

```python
@dataclass(frozen=True)
class SafetyState:
    observation: Observation                                  # required, no default
    estop_engaged: bool = False
    velocities: Mapping[MotionAxis, float] = field(default_factory=dict)
    gripper_forces: Mapping[Side, float] = field(default_factory=dict)
```

`observation` is the only required field. `RobotBackend.get_observation()`
(`robot_backends/robot_backends/interface.py:41-42`) is synchronous and the
router already calls it directly (`server.py:143-146`, inside the same
lock) — so `SafetyState(observation=self._backend.get_observation())` is a
call the router can already make with what it has.

**empirically-observed via source-grep**: `grep -rln "estop\|velocities\|
gripper_forces\|SafetyState\|SafetyLayer" src --include="*.py" | grep -v
robot_safety` returns **nothing**. No telemetry source for e-stop, axis
velocities or jaw forces exists anywhere in the repo today, and `MockBackend`
(`robot_backends/robot_backends/mock_backend.py`) has no concept of any of
them. `SafetyState(observation=obs)` with every other field left at its
default (`estop_engaged=False`, both mappings empty) is therefore **the only
honest construction today** — not a placeholder to feel bad about. Per the
docstring at `state.py:85-88`, an absent mapping key means "no reading
available," and "an axis with no reading cannot be judged against its cap" —
so wiring this in today will, in practice:
- always pass the e-stop check (nothing can set it True),
- always pass the velocity and gripper-force checks (empty mappings, nothing
  to compare),
- still classify every skill (see below) and still refuse an unclassified one,
- still run the collision guard — `SafetyLayer()` defaults to
  `NullCollisionGuard()` (`layer.py:146-147`), a no-op, since Mock has no
  geometry the guard could check,
- **actually clamp `ExtendColumn.height`** into `[0.0, 1.2]` m
  (`limits.yaml`), the one check that bites against Mock today.

**Coverage of `SKILL_TYPES`.** `robot_safety/robot_safety/policy.py:84-105`
(`SKILL_POLICIES`) lists all 7 skill wire names: `navigate_to`,
`move_gripper`, `grasp`, `place`, `extend_column`, `open_gripper`,
`close_gripper`. `robot_safety/test/test_skill_policy.py:35-42`
(`test_every_registered_skill_has_a_policy`) asserts
`unclassified_skills() == ()` — **empirically true per the test suite** (this
is exactly what the test is for; I did not re-run it, but the assertion is
unconditional and the table is exhaustive by construction). So wiring
`SafetyLayer.filter` in **will not** start refusing any tool that works today
— every skill has a policy, `_check_classified`
(`robot_safety/robot_safety/layer.py:228-246`) only fires for a skill nobody
classified.

`SafetyLayer.filter(skill, state) -> ClampedCall | SafetyEvent`
(`layer.py:168`). Check order is fixed (`layer.py:18-22,199-213`): e-stop →
unclassified → collision → velocity → gripper-force → column clamp.
`ClampedCall.skill` (`layer.py:67`) is what to execute — the caller's own
object unless clamped (`was_clamped` property, `layer.py:91-94`).

## 2. Wire shape at the tool boundary, and the D18 cost of adding a field

`SkillResult.to_dict()` (`robot_skills/robot_skills/result.py:228-242`):

```python
{
    'schema_version': 1,
    'skill': self.skill.to_dict(),
    'status': 'ok' | 'failed',
    'reason': str | None,
    'code': str | None,          # FailureCode.value, only set when failed
    'observation': self.observation.to_dict(),
}
```

`Observation.to_dict()` (`observation.py:406-419`):
`{'schema_version': 1, 'robot': RobotState.to_dict(), 'objects': [...],
'known_locations': [...]}`.

`FailureCode` members (`result.py:91-100`): `unknown_location`,
`unknown_object`, `not_graspable`, `object_already_held`, `gripper_occupied`,
`gripper_empty`, `out_of_reach`, `out_of_range`, `unsupported_skill`,
`rejected`. `BACKEND_REFUSAL_CODES` = every one except `rejected`;
`SAFETY_EVENT_CODES` = `{FailureCode.REJECTED}` only (`result.py:117-136`).

`SafetyEvent.failure_code` (`robot_safety/robot_safety/events.py:100-110`)
**always returns `FailureCode.REJECTED`** — every safety abort maps onto the
one existing code, chosen exactly so this feature needs no new
`FailureCode` member. `SCHEMA_VERSION = 1`
(`robot_skills/robot_skills/serialization.py:114`).

**What adding a field to `SkillResult` costs (D18 guard).** Two separate
mechanisms both have to agree, and only one of them is guarded by the golden
fixtures:

- **Golden schema drift** (`test_golden_schema.py`,
  `golden_fixtures.schema_drift`, `golden_fixtures.py:213-250`) is
  **one-directional**: `schema_drift(actual, golden)` only reports a key
  **present in `golden` but missing from `actual`**. A key present only in
  `actual` (i.e. a brand-new field `to_dict()` now emits) is silently
  ignored — that is the documented additive-non-breaking rule
  (`golden_fixtures.py:17-19`, `test_the_guard_allows_an_added_optional_field`
  in `test_golden_schema.py`). **So adding a field to `SkillResult.to_dict()`
  does not by itself fail the golden guard**, and needs no fixture
  regeneration.
- **`check_keys` on the read side is not so forgiving.**
  `serialization.py:196-214`: `check_keys(data, required=..., optional=...,
  context=...)` raises `SerializationError('unknown key(s): ...')` for any
  key present in `data` that is not in `required | optional`.
  `SkillResult.from_dict` (`result.py:244-265`) calls
  `check_keys(data, required=('skill','status','observation'),
  optional=(SCHEMA_VERSION_KEY, 'reason', 'code'), context=context)`
  (`result.py:251-256`). **A new field must be added to that `optional`
  tuple too**, or any code that round-trips a `SkillResult` through
  `to_dict()` → `from_dict()` (e.g. `robot_backends/test/test_mock_scenario.py
  ::test_scenario_survives_a_json_round_trip_at_every_step`, and
  `robot_skills/test/test_skill_serialization.py`) will start raising
  `SerializationError` the moment the new key is present. The golden test
  will not catch this (it never round-trips a payload carrying the new
  field), but the pre-existing round-trip suites in `robot_skills` and
  `robot_backends` will.
- `test_every_public_serializable_type_has_a_golden_fixture`
  (`test_golden_schema.py`) hard-asserts a discovered-type count of exactly
  **15**. Adding a *field* to an existing type does not change this; adding a
  brand-new nested wire *type* (e.g. a `SafetyEvent`-shaped record) would, and
  would need a new golden fixture in the same PR.

**Net**: adding an optional field to `SkillResult` is cheap but not free — it
touches `robot_skills/robot_skills/result.py` (dataclass field + `to_dict` +
`from_dict`'s `check_keys` optional tuple), which is outside this issue's
listed owned paths (`src/robot_mcp`, `src/robot_safety`). See open question 1.

## 3. Package plumbing: how `robot_mcp` reaches its siblings

`src/robot_mcp/package.xml:11-12` declares `<depend>robot_skills</depend>` and
`<depend>robot_backends</depend>` — the standard ament_python
inter-package dependency, used by `colcon` for build ordering and by
`rosdep`. `setup.py` (`src/robot_mcp/setup.py`) does **not** list them in
`install_requires` — that field only has `setuptools`. Nothing in
`src/robot_mcp/test/conftest.py` or `pytest.ini` adds a `sys.path` shim; it
just does `from robot_backends import MockBackend` (`test/conftest.py:10`)
directly, same as `robot_backends/test/conftest.py:10` importing
`robot_backends` itself.

**How this resolves without a shim** (**inferred-from-source**, corroborated
by `src/robot_mcp/README.md`'s own "Run it" section, which is explicit and
matches): either (a) `pixi run build` has colcon-symlink-installed every
`ament_python` package into a shared `install/` prefix that's on
`PYTHONPATH` once `install/setup.bash` is sourced, or (b) for ad-hoc/dev
runs, `PYTHONPATH` is set by hand to each package's source root:

```
PYTHONPATH=<repo>/src/robot_skills:<repo>/src/robot_backends:<repo>/src/robot_mcp \
  pixi run --frozen python -m robot_mcp
```

(`robot_mcp/README.md:38-46`, verbatim). To add `robot_safety` as a new
runtime dependency of `robot_mcp`: add `<depend>robot_safety</depend>` to
`src/robot_mcp/package.xml` (alongside the existing two), and extend every
`PYTHONPATH=...` invocation (README, any launcher) with
`:<repo>/src/robot_safety`. No `setup.py`/`conftest.py` change is needed
beyond that — `robot_safety` already imports clean (`from robot_safety import
SafetyLayer, SafetyState`, no ROS).

`test_no_ros_runtime.py` convention
(`src/robot_mcp/test/test_no_ros_runtime.py`, mirrored in `robot_backends`):
two tests per package — a clean-subprocess run that builds the server/backend
in a bare interpreter and asserts no `rclpy*`/`rosidl*`/`ament_index_python*`
module got imported (`test_the_server_serves_tools_without_ros`), and a
static AST scan of every source file for `rclpy` imports, including lazy ones
inside functions and dynamic `importlib.import_module('rclpy...')` calls
(`test_no_source_file_imports_rclpy`, `find_forbidden_imports`). **Any code
this feature adds to `robot_mcp` must stay clean of `rclpy` for this test to
keep passing** — `robot_safety` is itself rclpy-free
(`robot_safety/test/test_no_ros_runtime.py` exists and presumably asserts the
same; not read in full, but `robot_safety/robot_safety/__init__.py`'s own
docstring says "Pure Python: importing this package needs no ROS graph").

## 4. Test conventions and the gates

**`check_test_integrity.py`** (`scripts/check_test_integrity.py`) is what
`pixi run test` actually runs (`pixi.toml:35`, `test = "python
scripts/check_test_integrity.py"`). It does **not** run `colcon build`
itself — it runs `colcon test` (which needs a prior build to have produced
each package's test entry point) then audits the JUnit XML `colcon test`
wrote. Key rules (all read from source, not executed — **inferred-from-source**):

- A **"non-linter test"** is any collected pytest test case whose base test
  name / module name is not in `LINTER_TEST_NAMES = {'test_copyright',
  'test_flake8', 'test_pep257', 'test_mypy', 'test_xmllint',
  'test_lint_cmake', 'test_cppcheck', 'test_cpplint', 'test_uncrustify'}`
  (`check_test_integrity.py:86-90`, `is_linter_case`, line 325).
- **Ratchet**: `scripts/test_baseline.json` records `{package: non_linter
  count}`; `audit_package` (`check_test_integrity.py:407-493`) fails a
  package whose `non_linter` count drops below its baseline entry
  (`_STATUS_BELOW_BASELINE`). A package with **any** implementation code
  (`find_implementation_modules`, `check_test_integrity.py:339-367`) and
  **zero** non-linter tests fails too (`_STATUS_NO_REAL_TESTS`), independent
  of the baseline — this is why a skeleton package's 3 linter tests are
  acceptable only while it stays a skeleton.
- Update the baseline with `python scripts/check_test_integrity.py
  --update-baseline` (commit the result); it refuses to write from a run
  that wasn't otherwise green (`_update_baseline`,
  `check_test_integrity.py:767-800`).
- `pixi run test` fails if: any package produces no JUnit result, zero
  collected tests, all-skipped tests, below-baseline non-linter count, or
  implementation-with-no-real-tests (any one `PackageAudit` not `.ok`
  fails the whole run — `main()`, lines 941-965). It also runs
  `scripts/tests` (the `_workspace_tooling` pseudo-package, testing
  `check_test_integrity.py` itself).
- **Gotcha, empirically checked via `git log`**: `scripts/test_baseline.json`
  currently records `"robot_safety": 0`
  (`scripts/test_baseline.json:11`), even though `src/robot_safety/test/`
  holds ~87 real test functions across 7 modules today (counted via
  `grep -c '^def test_'`). This is because the baseline was last committed in
  `574e508` ("#47") and `robot_safety`'s test suite landed afterward in
  `f9ee2b7` ("closes #43", `#49`) — **empirically confirmed**: `git log
  --oneline -- scripts/test_baseline.json` shows `574e508` as the latest
  touch, `git log --oneline -- src/robot_safety/test/` shows `f9ee2b7` is
  newer. This is harmless for the ratchet (0 is a floor, not an assertion of
  truth) but means the true count isn't locked in yet — a good moment for
  this feature's implementer to run `--update-baseline` once (it will also
  pick up `robot_mcp`'s new tests and the new baseline for whatever holds
  the OpenClaw-asset tests, §5).

**Docs-clean CI guard** (`.github/workflows/guards.yml`): a single GitHub
Actions job, `docs-clean`, triggered on `pull_request`. It runs `git ls-files
'docs/features/*'` and fails if that's non-empty. It does **not** run any
Python/pytest/colcon — no pixi/RoboStack environment exists in CI (matches
CLAUDE.md's "GitHub CI has no pixi/RoboStack environment" statement). This is
the entirety of what "green CI" checks.

**flake8/pep257 configuration**: **empirically-observed** —
`grep flake8|pep257 setup.cfg` across every package's `setup.cfg` shows only
`[develop]`/`[install]` script-dir stanzas (e.g.
`src/robot_mcp/setup.cfg`), **no `[flake8]` section anywhere in this repo**.
The actual flake8 config comes from the installed `ament_flake8` package's
bundled ini, found and `cat`'d directly:
`.pixi/envs/default/lib/python3.12/site-packages/ament_flake8/configuration/ament_flake8.ini`:

```ini
[flake8]
extend-ignore = B902,C816,D100,D101,D102,D103,D104,D105,D106,D107,D203,D212,D404,I202
import-order-style = google
max-line-length = 99
show-source = true
statistics = true
```

`src/robot_mcp/test/test_pep257.py` additionally passes
`--add-ignore D213` to `ament_pep257.main` at call time (not via a config
file) — see its docstring for why (D212/D213 are mutually exclusive; this
repo picked D212's convention, summary on the first docstring line).

**Copyright header** (every source `.py` file, verified against every file
read in this exploration): a 5-line block, byte-identical across the repo:

```python
# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.
```

Checked by `ament_copyright` via each package's `test/test_copyright.py`
(identical 3-line body calling `ament_copyright.main.main(argv=[])`).

## 5. Where non-package assets (OpenClaw config + prompt) can live

The ratchet in `check_test_integrity.py` audits **colcon packages**
(`find_manifests`, `discover_packages` — anything with a tracked
`package.xml` under `src/`). A top-level directory (e.g. a new
`scripts/openclaw/` or `openclaw/` at repo root) has no `package.xml` and is
therefore **invisible to the per-package ratchet and to
`find_implementation_modules`'s "must have non-linter tests" rule** — it
cannot fail that gate, but it also gets no floor protecting it from
regressing silently.

Options observed in the repo, with what each buys/costs:

- **(a) `scripts/tests/` (the `_workspace_tooling` suite).**
  `run_tooling_tests` (`check_test_integrity.py:694-715`) runs `pytest
  scripts/tests` unconditionally as part of every whole-workspace `pixi run
  test`, writing results under `build/_workspace_tooling/pytest.xml`, which
  **is** baseline-ratcheted (`scripts/test_baseline.json:4`,
  `"_workspace_tooling": 111`). Today `scripts/tests/` holds exactly four
  modules (`test_audit.py`, `test_driver.py`, `test_lint.py`,
  `test_ratchet.py`) that all test `check_test_integrity.py` itself — nothing
  there today tests anything outside `scripts/`. It is **discovered** simply
  by directory name (`pytest scripts/tests`, hardcoded at
  `check_test_integrity.py:705`) — any test file dropped into that directory
  would be collected and counted, whether or not it's about
  `check_test_integrity.py`. **This is viable as a mechanical home for tests
  over repo-root OpenClaw assets** (e.g. `scripts/tests/test_openclaw_*.py`
  validating a JSON config's shape, or that the prompt file mentions every
  skill name), but it would be scope-mixing: this directory's whole
  raison d'être today is "the tests for `check_test_integrity.py`," per its
  docstring comment at the top of the guard.
- **(b) `src/robot_brain` as the package home.** `git ls-files
  src/robot_brain` / reading its tree: it is a real ament_python package
  (`package.xml`, `setup.py`, `resource/robot_brain`) holding only an empty
  `robot_brain/__init__.py` plus the 3 linter tests
  (`test_copyright.py`/`test_flake8.py`/`test_pep257.py`). Baseline entry:
  `"robot_brain": 0` (`scripts/test_baseline.json:6`) — confirming it is
  still classified as a no-implementation skeleton by
  `find_implementation_modules`. This is the natural "brain" home per the
  repo layout table in `CLAUDE.md` ("`src/robot_*` — ROS 2 ament_python
  packages (brain, skills, safety, backends, perception, description,
  bringup)"), but D21 (`docs/design/decisions.md:40`) makes the brain
  *OpenClaw itself*, not a Python module — so whether `robot_brain` should
  hold anything for this feature (a copy of the prompt for review/testing? a
  generator script for the OpenClaw config?) is genuinely open.
  **`ament_python` packages can ship non-Python data files**: `robot_safety`
  does exactly this for `limits.yaml` —
  `src/robot_safety/setup.py`: `package_data={package_name: ['*.yaml']},
  include_package_data=True`, loaded at runtime via
  `importlib.resources.files('robot_safety') / 'limits.yaml'`
  (`robot_safety/robot_safety/limits.py:356-365`, chosen specifically
  because a package-relative resource "is readable from a source checkout
  and from a symlink-installed build alike, with no ament index and no ROS
  graph" — same property `robot_mcp`'s README claims for itself). The same
  pattern (`package_data`/`data_files` + `importlib.resources`) is directly
  reusable if the OpenClaw prompt/config end up living inside a package.
- **(c) `scripts/pi/`** (`scripts/pi/dispatch.sh`, `scripts/pi/watch-run.sh`)
  is the existing precedent for "repo-held, Pi-side control-plane asset" —
  but these are orchestration shell scripts for the *agent-dev workflow*
  (dispatching worktree runs), not robot-runtime config, and they carry no
  tests of their own (**empirically observed**: `find scripts/pi -type f`
  returns only the two `.sh` files, no `test/` subdirectory). Not a close
  analog for testable OpenClaw config, but it is precedent that
  `scripts/<subdir>/` for Pi-facing non-package assets is an accepted shape
  in this repo.

None of these is clearly "the" answer — see open question 2.

## 6. The "clear the table" scenario, worked out against `default_world()`

`default_world()` (`robot_backends/robot_backends/mock_world.py:173-204`):
locations `charger (0,0,0)`, `kitchen (2,0,0)`, `table (0,2,0)`,
`living_room (-2,1,0)`; `book_1` is the only movable object at `table`
(`Pose.from_xyz(0.30, 2.10, 0.75)`, graspable, label `book`). Robot starts at
`charger`, column height `0.3` m. `RobotModel` defaults
(`mock_world.py:64-69`): `shoulder_offset_y=0.18`, `shoulder_offset_z=0.50`,
`reach_radius=0.85`, column range `[0.0, 1.20]`.

**Concrete tool-call sequence** (`Grasp`/`Place` fields:
`robot_skills/robot_skills/skills.py:213-214,249-250` — `Grasp(object_id,
side=None)`, `Place(pose, side=None)`, both with `side` optional):

1. `navigate_to({"location": "table"})` → robot base pose becomes `(0,2,0)`.
2. `grasp({"object_id": "book_1"})` — **no `side` needed, and no
   `open_gripper`/`extend_column` prerequisite**: `MockBackend._grasp`
   (`mock_backend.py:220-249`) closes directly on the target regardless of
   the gripper's current open/closed state, and picks a free+reachable side
   itself when none is named
   (`_resolve_grasping_side`, `mock_backend.py:386-414`). Reach check at
   default column height: left shoulder = `(0,2,0) + (0, 0.18, 0.3+0.50) =
   (0, 2.18, 0.80)`; `book_1` at `(0.30, 2.10, 0.75)`; distance ≈
   `sqrt(0.30² + 0.08² + 0.05²) ≈ 0.314` m, well inside the `0.85` m
   `reach_radius` — **no `extend_column` call is required for this
   scenario**.
3. `navigate_to({"location": "kitchen"})` — carries `book_1`: on every
   successful `execute()`, `_carry_held_objects`
   (`mock_backend.py:360-365,193`) re-poses every held object to its
   holding gripper's current world pose, and the gripper's world pose tracks
   the base as it moves (`_gripper_pose` → `_shoulder(side)` →
   `RobotModel.shoulder(base_pose, ...)`, `mock_world.py:96-104`).
4. `place({"pose": {"position": {"x": 2.4, "y": 0.0, "z": 0.5}, "orientation":
   {...}}})` — drop the book near `counter_1` (`counter_1` itself sits at
   `(2.40, 0.00, 0.45)`; not required to be that exact pose, any point within
   reach of the `kitchen` shoulder works). Reach check: kitchen left
   shoulder = `(2,0,0)+(0,0.18,0.80) = (2, 0.18, 0.80)`; target
   `(2.4, 0.0, 0.5)`; distance ≈ `sqrt(0.4² + 0.18² + 0.3²) ≈ 0.53` m — inside
   reach. `Place` needs no `side` either;
   `_resolve_holding_side(None)` (`mock_backend.py:434-462`) finds whichever
   gripper is holding something (deliberately *not* reach-checked when
   picking the side, per its own docstring — the caller must retry with an
   explicit side if the holding arm turns out unreachable).

**Where this scenario would legitimately fail if targeted wrongly** (from
`robot_backends/test/test_mock_failures.py`, `mock_backend.py` refusal
paths): `grasp`/`move_gripper`/`place` all raise `out_of_reach`
(`_require_reachable`, `mock_backend.py:372-384`) when the target exceeds
`reach_radius` from the acting shoulder — e.g. `Place(Pose.from_xyz(-5.0, 0.0,
0.5))` from wherever is refused
(`test_mock_failures.py:173-177`, an existing precedent test). A `place`
target chosen without navigating to a nearby location first (e.g. placing at
the kitchen counter's pose while still at `table`) would be refused the same
way — the natural failure mode an "agent gets the loop wrong" test should
exercise.

**Existing end-to-end precedent to model a new test on**:
`robot_backends/test/test_mock_scenario.py` (`SCENARIO` tuple + assertions on
final state, intermediate per-step state, JSON round-trip survival, and
`reset()`/determinism) is the direct pattern for a `robot_mcp`-level
"clear the table" scenario test (driving it through `SkillToolRouter`/
`build_server` instead of `MockBackend.execute` directly, per
`robot_mcp/test/test_tool_calls.py`'s existing style — not read in full here,
but its presence and `mcp_fixtures.py` alongside it indicate the established
tool-call test harness to reuse).

---

## 7. Open questions for the manager

1. **Where does the safety verdict surface on the wire, and does that touch
   `robot_skills`?** Two structurally different options exist and the repo
   does not decide between them:
   - **(a) Zero new fields.** An abort (`SafetyEvent` returned directly from
     `filter`) becomes `SkillResult.failure(skill, pre_call_observation,
     FailureCode.REJECTED, event.detail)` — `REJECTED` already exists in
     `SAFETY_EVENT_CODES` for exactly this (`result.py:134-136`,
     `events.py:100-110`), so this needs **no change to `robot_skills`** at
     all. A clamp (`ClampedCall.was_clamped`) executes the rewritten skill
     and reports the achieved state implicitly through the normal
     observation (e.g. a clamped `ExtendColumn` shows the clamped
     `column_height` in the result) — optionally with an informational
     `reason` string on the successful `SkillResult` (the existing pattern:
     `_place`/`_open_gripper` already return prose notes like `"released
     'mug_1' from the left gripper"`, `mock_backend.py:270,301`).
   - **(b) A new `safety`/`clamps` field on `SkillResult`.** Richer (carries
     `SafetyEventKind`, `offending_value`, `limit`, `clamped_value` verbatim)
     but costs an edit to `src/robot_skills/robot_skills/result.py` (outside
     this issue's owned paths) — dataclass field, `to_dict`, and critically
     `from_dict`'s `check_keys(optional=...)` tuple (§2) — plus every
     existing consumer of `SkillResult.from_dict` staying correct.

   Which does #46 want? The repo's own conventions (D18's bias toward
   additive-but-minimal, and `REJECTED` apparently pre-built for exactly this
   moment — its docstring literally says *"the later 'wire a safety layer
   into the loop' feature has exactly one seam to widen"*,
   `events.py:22-23`) lean toward (a) as the in-scope-today answer, with (b)
   as a natural, separately-scoped follow-up. Not asserting an answer —
   flagging the fork.

2. **Where do the OpenClaw agent config + prompt files live, and what tests
   them?** §5 lays out three non-exclusive shapes with no clear precedent
   pick: reuse `scripts/tests/` (mixes scope with the tooling-guard's own
   suite), give `src/robot_brain` real content (fits the package-layout
   table in `CLAUDE.md`, but D21 makes "the brain" OpenClaw itself, so it's
   unclear what a Python package would even hold — a config generator? a
   frozen copy of the prompt for diffing?), or a new untested top-level
   directory in the shape of `scripts/pi/` (invisible to the ratchet
   entirely). Needs a manager ruling before the implementer picks a layout.

3. **Does `robot_mcp` construct one `SafetyLayer()` per server (module-level
   default, matching `SafetyState.defaults()`-style memoization) or one per
   `SkillToolRouter` instance?** `SafetyLayer` is stateless/pure
   (`layer.py:9-16` — "keeps configuration and a collision guard, and
   nothing else... no memory of previous calls, no mutation") so either is
   correct; this is implementation-detail-sized, not a design fork, but
   worth a one-line ruling so nobody debates it mid-implementation. My
   inference from the code's own style (`build_server(backend=None)`
   pattern, `layer.py`'s own usage example constructing `SafetyLayer()` with
   defaults) is: construct one in `build_server`, injectable the same way
   `backend` is, defaulting to `SafetyLayer()` — mirroring how `backend`
   already works at `server.py:170-176`. Flagging rather than asserting
   since it's a real (if small) decision.

4. **Does the OpenClaw agent's operating prompt get validated by any
   automated means in this repo** (e.g. a test asserting it mentions every
   tool name in `robot_mcp.tools.TOOL_NAMES`, so a skill rename can't
   silently orphan the prompt), or is it treated as prose the implementer
   hand-maintains? If the former, its test lives wherever §5 lands the
   asset — connects directly to open question 2.
