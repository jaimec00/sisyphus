# Context: `robot_safety` — dynamic clamp/abort safety layer (issue #43, D17/D4)

Repo: worktree `i43-robot-safety-dynamic-clamp-abort-safety`, branch
`feat/i43-robot-safety-dynamic-clamp-abort-safety`, current with `origin/main`
at exploration time. All paths below are absolute under
`/home/sisyphus/worktrees/i43-robot-safety-dynamic-clamp-abort-safety`.

## 1. Acceptance criteria (restated from the issue, verbatim scope)

1. `SafetyLayer.filter(skill_call, state) -> ClampedCall | SafetyEvent`;
   in-limit calls pass through **unchanged**.
2. Clamps/aborts: joint-limit clamp, velocity caps, gripper force limits,
   e-stop. E-stop short-circuits all motion. Over-force while closing the
   gripper → `SafetyEvent` (D19: grasp success is normal loop info via
   `grasped`; over-*force* is the safety concern, not `close_gripper` itself).
3. `SafetyEvent` type **local to `robot_safety`** (kind, offending value,
   limit, clamped value); consumes the shared `robot_skills` schema
   **read-only**. A genuinely-needed shared-schema field is a D18 escalation,
   not an edit to `robot_skills`.
4. Config-driven limits (YAML) with sane defaults; a collision-guard **hook**
   (stub geometry only — real geometry is a later issue).
5. Tests: over-limit target → clamped to limit; over-force close →
   `SafetyEvent`; e-stop → abort-all; in-limit call → pass-through unchanged;
   velocity cap enforced. Limits loaded from YAML with documented defaults.
   Full local suite green (`pixi run test`); test-integrity guard passes.

Owned paths: `src/robot_safety/**`. Read-only on `src/robot_skills/**`.
Non-goals: backend reachability refusal, real collision geometry, wiring into
the brain loop. The GitHub issue body (`gh issue view 43`, empirically
fetched) has no comments and matches this restatement exactly — no additional
constraints beyond the manager's brief.

## 2. Package skeleton as it stands today

`src/robot_safety/` (all read, exact contents):
- `robot_safety/__init__.py` — empty (not inspected further, confirmed empty
  by directory listing; no exports yet).
- `README.md` — one line: "Status: skeleton. See `docs/design/PROJECT.md`."
- `package.xml` (`src/robot_safety/package.xml`) — `buildtool_depend
  ament_python`, `<depend>rclpy</depend>`, the three ament lint
  `test_depend`s (`ament_copyright`, `ament_flake8`, `ament_pep257`) +
  `python3-pytest`. **No `robot_skills` `<depend>`** — add it if the
  implementation imports `robot_skills` types (which it must, per the brief:
  "consume the shared schema... read-only").
- `setup.py` (`src/robot_safety/setup.py:1`) — byte-identical boilerplate to
  every other ament_python package in this repo (`find_packages(exclude=
  ['test'])`, the two `data_files` entries for the ament resource index and
  `package.xml`, `install_requires=['setuptools']`,
  `extras_require={'test': ['pytest']}`). **No YAML config `data_files` entry
  exists yet** — this is new ground (see §5).
- `setup.cfg`, `pytest.ini`, `resource/robot_safety` — identical boilerplate
  to `robot_skills`/`robot_backends`.
- `test/test_copyright.py`, `test/test_flake8.py`, `test/test_pep257.py` —
  byte-identical (`diff` empirically run, no output) to the same three files
  in `robot_skills`/`robot_backends`. These are the *only* tests today.

`robot_safety` is **not** imported by anything else in the repo yet (no
consumer package exists to integrate against — integration is explicitly a
non-goal).

## 3. Package conventions to match exactly

**Copyright header** (`src/robot_skills/test/test_copyright.py:1-5`, and every
source file in the repo), required by `ament_copyright`/`test_copyright.py`:
```python
# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.
```
Must be the literal first lines of every `.py` file (module docstring comes
after).

**flake8 config** — no per-package override anywhere in the repo (checked:
no `.flake8`, no `tox.ini`, `setup.cfg` files hold only `[develop]`/`[install]`
script_dir stanzas). The config in force is the installed
`ament_flake8` package's own
(`.pixi/envs/default/lib/python3.12/site-packages/ament_flake8/configuration/ament_flake8.ini`,
read directly, empirically located):
```
[flake8]
extend-ignore = B902,C816,D100,D101,D102,D103,D104,D105,D106,D107,D203,D212,D404,I202
import-order-style = google
max-line-length = 99
show-source = true
statistics = true
```
So: 99-char lines, Google import-order style, docstrings not required on
every def/module (D10x ignored) but **are** enforced where present (see
pep257 below). Quote style throughout the codebase is single-quotes
(observed uniformly in `skills.py`, `mock_backend.py`, etc.) — flake8 has no
quote-style plugin configured here, but match the existing convention anyway.

**`ament_pep257`** (`src/robot_skills/test/test_pep257.py:23`): run as
`main(argv=['.', 'test', '--add-ignore', 'D213'])` — D213 excluded because
this repo writes docstring summaries on the *first* line (D212 style, which
`ament_flake8`'s own ignore list already selects). Every module, class, and
public function needs a one-line-first docstring in the D212/PEP257 style
used throughout (see any file above for the idiom — summary line, blank
line, details).

**`pytest.ini`** (`src/robot_safety/pytest.ini`, identical to
`robot_skills`/`robot_backends`):
```ini
[pytest]
addopts = -p no:launch_testing -p no:launch_ros
testpaths = test
```
Disables the RoboStack `launch_testing`/`launch_ros` pytest plugins, which
are incompatible with pytest ≥ 8 (see the file's own comment,
`src/robot_safety/pytest.ini:1-13`, already in place — nothing to change).

**Non-Python data files / `setup.py` `data_files`** — every package's
`setup.py` (`src/robot_safety/setup.py:9-13` included) currently declares
exactly two `data_files` entries: the ament resource-index marker and
`package.xml`. **There is zero prior art in this repo for shipping a
non-Python data file (e.g. a YAML config) from an `ament_python` package.**
No package anywhere under `src/` currently has a `data_files` entry beyond
those two, and `find /home/.../src -iname '*.yaml' -o -iname '*.yml'`
(excluding `.pixi`) returned **nothing** — there is no YAML file in the
source tree at all today. This is genuinely new ground the implementer has to
establish; see §5 for the two candidate mechanisms and the open question.

## 4. `robot_backends` — the seam a `SafetyLayer` sits against

`RobotBackend` (`src/robot_backends/robot_backends/interface.py:26-53`) is a
tiny, total ABC:
```python
class RobotBackend(ABC):
    @abstractmethod
    def reset(self) -> Observation: ...
    @abstractmethod
    def get_observation(self) -> Observation: ...
    @abstractmethod
    def execute(self, skill: Skill) -> SkillResult: ...
```
Its docstring (`interface.py:14-19`) explicitly anticipates a safety layer:
> "a **safety wrapper** can implement `RobotBackend` itself, clamp or reject a
> skill, and delegate to the wrapped backend" — one legitimate shape, though
> the brief specifies a different, narrower shape: `filter(skill_call, state)
> -> ClampedCall | SafetyEvent`, not a full `RobotBackend` wrapper. Both are
> consistent with the repo; which one to build is for the manager, not for me
> to decide (see Open Questions).

`MockBackend.execute` (`src/robot_backends/robot_backends/mock_backend.py:186-199`)
is **synchronous, atomic, and total**: it validates *before* mutating (raises
an internal `_SkillRefused`, caught and turned into a failed `SkillResult`),
and by the time `execute()` returns, the skill is either fully applied or not
applied at all. There is no streaming/partial-progress state exposed by this
backend — no mid-motion callback, no cancellation token. `robot_mcp`'s README
(`src/robot_mcp/README.md:108-114`) states this as policy, not just as the
Mock's limitation:
> "**Deliberately absent. No cancellation, no `/stop`, no e-stop.** The skill
> interface is synchronous and instantaneous: when a tool call returns, the
> motion is over, so there is no in-flight command to cancel... The safety
> layer, when it lands, sits below this server on the skill seam — never
> bypassed by it."

This is a real tension with the issue's language ("clamp-or-abort **in-flight**",
"E-stop short-circuits all motion") — **empirically/inferred-from-source: no
execution model in this repo today has an "in-flight" a skill can be aborted
mid-way through.** `RobotBackend.execute` is call-in/call-out. Flagged as
Open Question Q3.

`MockBackend`'s own docstring (`mock_backend.py:299-309`, on `_close_gripper`)
already anticipates the exact D19/D17 split the brief describes:
> "Errors here stay reserved for 'couldn't run'; over-force while closing is a
> safety event on D17's clamp path, not this handler's."
So the Mock intentionally does **not** implement over-force detection — that
is `robot_safety`'s job entirely, and the Mock/backend layer is not expected
to change for this feature (owned paths exclude `robot_backends`).

`src/robot_backends/test/test_mock_failures.py:213-234` has a test asserting
*every* Mock refusal code is a `BACKEND_REFUSAL_CODES` member, never a
`SAFETY_EVENT_CODES` member — i.e. the Mock today emits zero safety events;
`robot_safety` is the only place they can originate from.

## 5. Test-integrity guard + per-package ratchet — exact requirements

Read in full: `scripts/check_test_integrity.py` (outside owned paths, but
governs what "green" means) and `scripts/test_baseline.json` (also outside
owned paths).

- `scripts/test_baseline.json` (current contents, empirically read):
  ```json
  { "robot_safety": 0, ... }
  ```
  The ratchet only *fails* a package when its collected non-linter test count
  drops **below** its baseline. Since `robot_safety`'s baseline is `0`, any
  number of non-linter tests ≥ 0 clears the ratchet check by itself.
- **However**, a separate, unconditional rule applies once real code lands:
  `audit_package` (`scripts/check_test_integrity.py:481-486`) fails a package
  with status `no-real-tests` when it "holds implementation code... but all
  collected tests are linter tests" — i.e. **the moment `robot_safety` gains
  a non-trivial `.py` module under `robot_safety/robot_safety/`, it must also
  have at least one non-linter test**, or the guard fails regardless of the
  baseline being 0. "Implementation code" is defined narrowly
  (`find_implementation_modules`, `check_test_integrity.py:339-367`): any
  `.py` file under an importable subpackage of the package root (has
  `__init__.py`), excluding `test`/`tests` dirs and
  `__init__.py`/`conftest.py`/`setup.py`, that holds more than blank
  lines/comments.
- The baseline file `scripts/test_baseline.json` is **not** in owned paths
  (`src/robot_safety/**`). Whether/when to bump `robot_safety`'s baseline
  entry from `0` to the real test count (via `python
  scripts/check_test_integrity.py --update-baseline`, which the docstring at
  `check_test_integrity.py:26-29` describes) is a decision for whoever runs
  `pixi run test` at the end of the loop (the test-runner agent per
  CLAUDE.md), not blocking for green today since baseline `0` never fails the
  ratchet check on its own — flagging so the manager can rule on whether the
  context-explorer's silence here should be read as "leave it to
  test-runner."
- `pixi run test` = `python scripts/check_test_integrity.py`
  (`pixi.toml:29`, read), which runs `colcon test` (does **not** run `colcon
  build` first) then audits JUnit XML under `build/`. **This means a fresh
  `pixi run build` (`colcon build --symlink-install`, `pixi.toml:27`) must
  have already happened for `colcon test` to see current source** — relevant
  if a new `data_files` entry is added to `setup.py` (see below): a
  symlink-install layout typically needs a fresh `colcon build` to create the
  new symlink for a newly-declared data file. **Not empirically verified**
  (no build was run, per the read-only constraint) — flagged as a thing the
  implementer/test-runner must confirm by actually running `pixi run build`
  after adding any new `data_files` entry.

## 6. Existing test idiom (from `robot_skills`/`robot_backends`)

- Plain pytest **functions**, not classes, throughout every test module
  inspected (`test_skills.py`, `test_backend_interface.py`,
  `test_mock_failures.py`, etc.).
- Each package keeps a `test/conftest.py` with a handful of fixtures, and a
  separate `test/<package>_fixtures.py` (e.g. `skill_api_fixtures.py`,
  `mock_backend_fixtures.py`) holding builder/assertion **helpers**, imported
  directly by test modules — e.g. `src/robot_skills/test/conftest.py:11`:
  `from skill_api_fixtures import assert_round_trip, make_observation`, and
  `src/robot_backends/test/test_mock_failures.py:15`: `from
  mock_backend_fixtures import assert_refused, run, snapshot`. Rationale
  stated in both files' docstrings: "Kept out of `conftest.py` so test
  modules can import them directly without relying on a module name every
  package in the workspace shares." **This import works because there is no
  `__init__.py` under `test/`** (confirmed: `find ... -name __init__.py -path
  '*/test/*'` found none) — pytest's rootdir-relative sys.path insertion
  makes the bare module name resolvable. **This pattern is per-package, not
  cross-package**: nothing suggests (and nothing was found) that
  `robot_safety`'s tests could import `skill_api_fixtures` or
  `mock_backend_fixtures` directly across package boundaries — those modules
  live under each package's own `test/` dir, which is not on `robot_safety`'s
  test `sys.path`. If `robot_safety` tests need an `Observation`/`RobotState`
  builder, they should write their **own** local `test/safety_fixtures.py`
  analogous to the two above (own `make_observation`-style helper, or import
  `robot_skills`'s public dataclasses directly and construct inline) — there
  is no installed/importable shared test-fixture package.
- `make_observation`/`make_robot_state`/`make_gripper`
  (`src/robot_skills/test/skill_api_fixtures.py:66-113`) build an
  `Observation` field-by-field with `**overrides` merged over sane defaults —
  a reusable pattern to copy locally, not to import.
- `assert_refused` (`src/robot_backends/test/mock_backend_fixtures.py:64-88`)
  is the idiom for "the world is provably unchanged": snapshot
  `to_dict()` before, run, snapshot after, assert equality — worth mirroring
  for "an aborted/e-stopped call must not have mutated anything either" if
  the safety layer ever gets a mutable state to protect (see Q1).

## 7. Any existing config/YAML prior art

**None.** Empirically searched (`find src -iname '*.yaml' -o -iname
'*.yml'`, excluding `.pixi`) — zero YAML files anywhere in `src/`.
`robot_bringup` (`src/robot_bringup/`) is itself an unimplemented skeleton
(only `__init__.py` + the three lint tests, same shape as `robot_safety`
before this feature) — no launch files, no params files, nothing to crib.
`robot_mcp` has no config loading either. **PyYAML 6.0.3 is installed**
(empirically verified: `.pixi/envs/default/bin/python3 -c "import yaml;
print(yaml.__version__)"` → `6.0.3`; `yaml.safe_load('a: 1\nb: [1,2,3]\n')` →
`{'a': 1, 'b': [1, 2, 3]}`), pulled in transitively via `ros-jazzy-desktop`
— no new dependency needed, and nothing in `package.xml`/`setup.py` currently
declares it as a direct dependency of any package (also true here: adding it
explicitly to `robot_safety/package.xml`/`setup.py` would be the honest thing
to do even though it resolves today without doing so).

**`ament_index_python` is installed** (empirically verified importable from
`.pixi/envs/default/bin/python3`), so `get_package_share_directory` is
available as a mechanism — but see the strong counter-signal below.

**Strong counter-signal against reaching for `ament_index_python`/ROS
machinery at all in this class of package.** Both existing sibling packages
enforce, as an acceptance-tested invariant, that they **never import
`rclpy`/`ament_index_python`/`rosidl`**, even lazily:
`src/robot_backends/test/test_no_ros_runtime.py` (read in full) —
- `test_packages_run_without_ros` (`test_no_ros_runtime.py:38-56`) runs a
  clean subprocess, imports `robot_backends`/`robot_skills`, and asserts
  `sys.modules` contains none of `rclpy`, `rclpy.*`, `rosidl*`,
  **`ament_index_python*`** (`test_no_ros_runtime.py:26-29`, the check
  explicitly lists `ament_index_python` as a forbidden root alongside
  `rclpy`).
- `test_no_source_file_imports_rclpy` (`test_no_ros_runtime.py:242-263`)
  AST-scans every `.py` file in both packages, including lazy/dynamic
  imports, for any `rclpy` import — `FORBIDDEN_ROOTS = ('rclpy',)`
  (`test_no_ros_runtime.py:107`) (this second check is only about `rclpy`,
  not `ament_index_python`, at the AST-scan level; the *subprocess* check is
  the one that also forbids `ament_index_python`).
- Both packages' `README.md`s state this as policy: "Pure Python: importing
  this package needs no ROS graph and no ROS packages"
  (`src/robot_skills/README.md:8`); "the package's `rclpy` dependency is
  reserved for [a later ROS 2 action transport feature]"
  (`src/robot_skills/README.md:9`). `robot_safety/package.xml` already
  declares `<depend>rclpy</depend>` in its skeleton (pre-existing, not yet
  used) — consistent with "reserved for later," not "use it now."

There is **no `test_no_ros_runtime.py`-equivalent test in `robot_safety`
today**, and the acceptance criteria for #43 do not require one — but the
pattern strongly suggests config loading should **not** depend on
`get_package_share_directory`/`ament_index_python` (which would also
re-introduce the "must `colcon build` before tests see the file" fragility
from §5) in favor of a plain-filesystem approach (e.g. a YAML file shipped
inside the importable `robot_safety` package directory itself, loaded via
`importlib.resources` or a `Path(__file__).parent`-relative lookup, which
works identically from source tree and from a symlink-installed build with
no ROS runtime and no separate build step to pick up a new file — a file
already inside the symlinked package directory is visible immediately).
**This is not a decision I am making — flagged in Open Questions (new,
below) since it directly determines the `data_files`/packaging approach.**

## 8. `robot_skills` surface available read-only

Confirmed by direct read of `src/robot_skills/robot_skills/{skills,result,observation,validation,serialization}.py`:

- **7 skills**, all frozen dataclasses, registered by wire name in
  `SKILL_TYPES` (`skills.py:330-331`): `NavigateTo(location: str)`,
  `MoveGripper(side: Side, pose: Pose)`, `Grasp(object_id: str, side:
  Side|None)`, `Place(pose: Pose, side: Side|None)`, `ExtendColumn(height:
  float)`, `OpenGripper(side: Side)`, `CloseGripper(side: Side)`. **Nothing
  here mentions a joint, a velocity, or a force** — confirmed by full read,
  matching the manager's brief.
- `Skill` base class docstring (`skills.py:16-18`) explicitly anticipates
  this feature: "a **safety layer** can inspect, clamp (by building a new
  skill) or reject a skill before it ever reaches a backend, because a skill
  is not a call" — i.e. clamping a skill means constructing a **new,
  differently-valued instance** of the same frozen dataclass (skills are
  immutable; there is no in-place mutation path).
- `robot_skills/robot_skills/validation.py:9-14` (module docstring) is
  direct, load-bearing evidence for Q2:
  > "These guard *structural* validity only... They deliberately do **not**
  > encode robot limits (joint ranges, reach, force): clamping and rejecting
  > out-of-envelope *values* is the safety layer's job, and the world model's,
  > not the data type's."
  And `test_skills.py:112-115` (`test_extend_column_does_not_clamp`) asserts
  `ExtendColumn(-3.0).height == -3.0` and `ExtendColumn(99.0).height == 99.0`
  unclamped, with the docstring "Range policy belongs to the safety layer and
  the world model, not the type." **`ExtendColumn.height` is confirmed as
  the one skill field the codebase explicitly, deliberately, leaves
  unclamped for `robot_safety` to own.**
- `Observation`/`RobotState`/`GripperObservation` (`observation.py`, full
  read) carry: robot `pose` (`Pose`), `column_height: float`, `location:
  str|None`, per-side `GripperObservation` (`state: GripperState`
  open/closed, `pose: Pose`, `held_object_id: str|None`, `grasped: bool`),
  and `objects: tuple[SceneObject, ...]` (id, label, pose, graspable,
  held_by). **Confirmed: no velocity field, no force/contact field, no
  e-stop flag anywhere in this type tree.** `grasped` is the *closest* thing
  to a force signal today, but it is boolean ("jaws report a load"), not a
  magnitude — insufficient by itself to compare against a numeric force
  limit.
- `FailureCode` / `BACKEND_REFUSAL_CODES` / `SAFETY_EVENT_CODES`
  (`result.py:64-136`, full read): 10 members total.
  `SAFETY_EVENT_CODES = frozenset({FailureCode.REJECTED})` — exactly one
  member today. The enum's docstring (`result.py:85-89`) explicitly
  anticipates this feature adding members: "adding a dynamic-safety code
  (e-stop, collision abort, gripper over-force) is a deliberate
  classification and not a silent default" — i.e. the design **already
  expects new `FailureCode` members for e-stop/over-force/etc., classified
  into `SAFETY_EVENT_CODES`**. `test_failure_codes.py` (read via grep,
  `src/robot_skills/test/test_failure_codes.py:19-52`) asserts every
  `FailureCode` member is in exactly one of the two sets — so **adding a
  member to `FailureCode`/`SAFETY_EVENT_CODES` is an edit to
  `robot_skills/robot_skills/result.py`**, which is **outside the owned
  path** (`src/robot_safety/**` only, `robot_skills` is read-only). This is
  the crux of Open Question Q6: reusing `FailureCode` for
  `SafetyEvent.kind` values would require editing `robot_skills`, which the
  brief prohibits without a D18 escalation; a **local** `robot_safety` enum
  avoids that edit entirely.
- `SkillResult.code` (`result.py:154`) is typed `FailureCode | None`
  specifically — so if a `SafetyEvent` needs to eventually flow back to the
  brain as (or alongside) a `SkillResult`, `SkillResult.code` today can
  **only** hold a `FailureCode`, not an arbitrary local `robot_safety` enum,
  without a `robot_skills` edit. Since "wiring into the brain loop" and
  building `SkillResult`s is a non-goal here, this is likely moot for this
  issue's scope, but worth the implementer knowing the shape of the
  constraint if `SafetyEvent` construction ever touches `SkillResult`.

## 9. Design-doc constraints (`docs/design/decisions.md`, `PROJECT.md`)

Quoted verbatim, full read of the relevant decisions:

- **D4** (`decisions.md:10`): "every skill returns status + a fresh
  observation (event-driven). A safety/clamp layer (joint limits, collision
  checks, gripper force limits, e-stop) is mandatory from day one."
- **D17** (`decisions.md:28`), the operative decision for this issue, in
  full: "Split by *kind* of limit, not by component. **Kinematic/workspace
  reachability** (target pose out of range, joint stop, self-collision,
  unreachable) is the **backend's** job: it *refuses* the skill up front and
  returns a 'couldn't run' error (`unreachable`/fault) before motion.
  **Dynamic safety** (joint-limit clamp, collision avoidance during motion,
  gripper force limits, e-stop) is the **safety/clamp layer** (D4): it sits
  between brain-issued skills and the backend and *clamps or aborts*
  in-flight, returning a safety event. Rule of thumb: *'can't be done' =
  backend refusal; 'unsafe to continue' = safety-layer clamp/abort.*...
  *Rationale:* keeps `robot_safety` backend-agnostic (same clamps regardless
  of Mock/Sim/Real) while each backend owns only its own reachability;
  matches D2 (LLM never does IK) and D4."
- **D18** (`decisions.md:30`): version-stamped single schema source, one
  `SCHEMA_VERSION`, additive-optional-field = non-breaking,
  remove/rename/retype = breaking + atomic cross-repo update. This is the
  mechanism the brief invokes when it says "escalating (D18)" for a
  genuinely-needed shared-schema field — i.e. escalate to the manager/Sisyphus
  rather than editing `robot_skills` unilaterally.
- **D19** (`decisions.md:32`): "`close_gripper` on an empty gripper: success +
  `grasped` flag, not an error... Errors stay reserved for 'couldn't run'
  (fault, unreachable, e-stop); **over-force while closing is a safety event
  on D17's clamp/abort path**, a separate concern."
- **D21** (`decisions.md:38-45`): brain = OpenClaw agent via `robot_mcp` tool
  calls; "**Safety, e-stop, and hard guards are enforced server-side, below
  the tool boundary — never by the LLM** (D4/D17)." Also: "There is **no
  custom planner loop**" — reinforces that wiring into a brain loop
  (non-goal here) may never even exist as a literal loop to wire into; the
  seam is the MCP tool boundary / `RobotBackend`, not a custom loop.
- **PROJECT.md:23**: "Safety/clamp layer: reject or clamp illegal/unsafe
  commands — joint limits, collision checks, gripper **force limits**,
  velocity caps, e-stop. Present from day one."
- **PROJECT.md:45**: "Guards (mandatory for a home robot) live server-side,
  below the tool boundary — never trusted to the LLM:
  max-steps+timeout+stuck-detection; user stop/cancel interrupts;
  heartbeat/dead-man...; **e-stop**; one task at a time." (This is describing
  the *later*, task-level guard layer in `robot_mcp`/task-service territory,
  not necessarily this issue's e-stop — but shows "e-stop" is a term used at
  more than one layer in this design; this issue's e-stop is specifically the
  D17/D4 skill-level one.)
- **PROJECT.md:75**: repo tree comment: `robot_safety/  # clamp/limits/force/e-stop`.
- No line in either doc specifies numeric default limits (joint ranges,
  velocity caps, force thresholds) — "sane defaults" in the brief is left to
  the implementer to choose and document in the YAML, there is no design-doc
  number to match.

## Open questions (evidence only, no recommendation — manager rules)

**Q1. What is `state` in `filter(skill_call, state)`?**
`Observation` (full read, §8) carries pose/column_height/location/gripper
open-closed-state/held-object/grasped-bool/scene objects — **no velocity, no
force magnitude, no e-stop flag**. Two ways the codebase could go:
(a) a new `robot_safety`-local state type carrying telemetry
(velocity, gripper force, e-stop-engaged) that a caller assembles alongside
an `Observation`; or (b) escalating under D18 to add fields to the shared
`Observation`/`RobotState`/`GripperObservation` types. Evidence for (a): the
brief explicitly frames a shared-schema field as something to *escalate*, not
default to, and owned paths exclude `robot_skills`. Evidence against a
trivial (a): if `SafetyEvent`/`ClampedCall` ever need to be handed to a
`RobotBackend.execute()`-shaped caller or a `SkillResult`, the shared types
are the only vocabulary those callers understand. No test or doc anywhere
picks between these.

**Q2. What does "joint-limit clamp" mean with no joints in the skill API?**
Confirmed clampable, by direct code evidence: `ExtendColumn.height` — the
type explicitly leaves it unclamped by design and says so twice
(`validation.py:12-14`, `test_skills.py:114`). Not obviously clampable the
same way: `MoveGripper.pose`/`Place.pose` are Cartesian poses, not joint
angles — clamping a pose into a workspace envelope in the safety layer
risks doing what D17 assigns to backend reachability refusal ("target pose
out of range... unreachable" is explicitly the backend's job). `NavigateTo`,
`Grasp`, `OpenGripper`, `CloseGripper` carry no continuous numeric value to
clamp at all (only identifiers/enums). No design doc or code names a second
clampable field beyond `ExtendColumn.height`.

**Q3. "Velocity caps" — no skill carries a velocity, and no backend has an
in-flight execution model.** `RobotBackend.execute()` (§4) is synchronous,
call-in/call-out, with no streaming state and no cancellation — confirmed by
reading `interface.py`, `mock_backend.py`, and `robot_mcp/README.md:108-114`
("no cancellation, no e-stop... when a tool call returns, the motion is
over"). This directly conflicts, at the level of *today's execution model*,
with the issue's "clamp-or-abort **in-flight**" and "E-stop short-circuits
all motion" language. Options the evidence supports: (i) the cap is enforced
against a *measured* velocity value inside whatever `state` type Q1 settles
on, producing an abort/`SafetyEvent` pre-hoc (checked once per `filter()`
call, since there is no mid-motion callback to hook into today) rather than
truly "in-flight"; (ii) the cap is attached to the outgoing `ClampedCall` as
a commanded motion-limit envelope the backend is trusted to honor, never
itself checked against a live value; (iii) both. No backend or test in the
repo exercises anything resembling a partial/interrupted skill execution.

**Q4. What must `ClampedCall` carry beyond the rewritten `Skill`?**
No existing type in the repo is a candidate reuse target — `Skill` itself is
immutable data with no room for metadata (`skills.py`, full read), and
`SkillResult` is shaped for backend outcomes, not clamp records
(`result.py:150-155`, `code: FailureCode | None` typed specifically, see
§8's `SkillResult.code` note). Nothing in the issue or design docs specifies
whether a caller needs to know *what* was clamped and by how much on the
happy path, versus only being told about it on a `SafetyEvent`.

**Q5. Collision-guard hook shape.** No existing protocol/callable pattern in
this repo to crib from for pluggable stub geometry — `RobotBackend` (§4) is
the only ABC-based seam in the codebase, and it is a different concern
(backend swapping, not an injected check). PROJECT.md/decisions.md name
"collision checks"/"collision avoidance during motion" as within D4/D17's
scope but give no interface shape, deferring "real collision geometry" to a
later issue by name in the brief's non-goals.

**Q6. Should `SafetyEvent.kind` be a new `robot_safety`-local enum, or reuse
`FailureCode`?** `SAFETY_EVENT_CODES` today contains exactly one member,
`FailureCode.REJECTED` (`result.py:134-136`, confirmed by grep-read of
`test_failure_codes.py:41`: `{code.value for code in SAFETY_EVENT_CODES} ==
{'rejected'}`). The `FailureCode` docstring anticipates future safety codes
being added (`result.py:85-89`) but every current member and the
`is_backend_refusal`/`is_safety_event` partition live in `robot_skills`
(read-only for this issue). Adding new `FailureCode` members (e-stop,
over-force, velocity-cap-abort, ...) is a `robot_skills` edit and therefore
out of the owned paths without a D18 escalation per the brief's own
instruction ("consume the shared schema... read-only; a genuinely needed
shared-schema field means escalating (D18), not editing `robot_skills`").
The brief's deliverable #3 says the `SafetyEvent` type itself is "local to
`robot_safety`" — but does not say whether its `kind` field's *values* must
also avoid `FailureCode` membership, or whether a local enum should instead
carry an optional/companion mapping onto the existing `REJECTED` code for
anything that needs to interoperate with `SkillResult.code`.

**Q7 (new, found during exploration). Where does the YAML config file live
and how is it loaded — `data_files`/`ament_index_python`
(`get_package_share_directory`), or a plain file inside the importable
`robot_safety` package loaded via `importlib.resources`/`Path(__file__)`?**
No prior art exists in this repo for either mechanism (§7). Evidence for the
plain-file/`importlib.resources` route: the sibling packages
(`robot_skills`, `robot_backends`) enforce, as a tested invariant, that they
never import `rclpy` or `ament_index_python` even lazily
(`test_no_ros_runtime.py`, §7) — reaching for
`ament_index_python.packages.get_package_share_directory` would be the first
use of ROS runtime machinery in a package of this shape in the whole repo,
and would also introduce a "must `colcon build` before tests see a newly
added data file" fragility not otherwise present (§5's `pixi run test` /
`colcon test` note). Evidence for the `data_files`/share route: it is the
standard idiomatic ROS 2 `ament_python` pattern for shipping config, would
make the YAML discoverable the same way a real deployed node finds its
params, and `robot_safety/package.xml` already carries `<depend>rclpy</depend>`
(unused so far, "reserved," same as `robot_skills`'s) so the package is not
categorically committed to a ROS-runtime-free existence the way
`robot_skills`/`robot_backends` explicitly are (no `test_no_ros_runtime.py`
exists or is required for `robot_safety`). No test, doc, or comment in the
repo picks between these.

**Q8 (new). Should `robot_safety/package.xml` gain a `<depend>robot_skills</depend>`
and `<depend>python3-yaml</depend>` (or similar) now that the implementation
will import both?** Every other package that imports `robot_skills` declares
it (`src/robot_backends/package.xml:11`: `<depend>robot_skills</depend>`);
`robot_safety/package.xml` currently declares neither. PyYAML resolves today
only because `ros-jazzy-desktop` pulls it in transitively (§7) — not because
any package.xml/setup.py names it directly anywhere in this repo, so there
is no existing precedent for how a direct PyYAML dependency should be
declared (`rosdep` key `python3-yaml` is the typical `ament_python` idiom,
not verified against this repo's `rosdep`/pixi setup).
