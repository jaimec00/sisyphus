# Context: mock-skill-api

Explored 2026-08-10 for branch `feat/mock-skill-api`. Read-only exploration;
nothing outside this file was modified.

## What already exists (map)

Both owned packages are **empty ament_python skeletons** — no skill/backend
code, no tests, no `test/` dir anywhere in `src/`. This is the first feature
to add real Python code and the first real tests in the repo, so there is no
in-repo precedent to match beyond the skeleton conventions below.

```
src/robot_skills/
  README.md            # 1-line status stub, says "over MoveIt 2/Nav2, exposed as ROS 2 actions"
  package.xml
  setup.py
  setup.cfg
  resource/robot_skills        # empty marker file (ament index), do not touch
  robot_skills/__init__.py     # EMPTY (0 bytes)

src/robot_backends/
  README.md            # 1-line status stub
  package.xml
  setup.py
  setup.cfg
  resource/robot_backends      # empty marker file
  robot_backends/__init__.py   # EMPTY (0 bytes)
```

Sibling skeletons (`robot_brain`, `robot_safety`, `robot_perception`,
`robot_description`, `robot_bringup`) are identically shaped — same
`setup.py`/`package.xml` template, empty `__init__.py`. Nothing there is
relevant except as a style reference; do not touch them (brief: "Do not
modify other packages").

### Exact current file contents (ament_python conventions in use)

`src/robot_skills/package.xml` (verbatim; `robot_backends/package.xml` is
identical except `<name>`/`<description>`):
```xml
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>robot_skills</name>
  <version>0.0.0</version>
  <description>Skill API implementation over MoveIt 2 / Nav2, exposed as ROS 2 actions.</description>
  <maintainer email="hejaca00@gmail.com">Jaime</maintainer>
  <license>MIT</license>

  <buildtool_depend>ament_python</buildtool_depend>
  <depend>rclpy</depend>

  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```

`src/robot_skills/setup.py` (verbatim; `robot_backends/setup.py` identical
except `package_name`/`description`):
```python
from setuptools import find_packages, setup

package_name = 'robot_skills'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Jaime',
    maintainer_email='hejaca00@gmail.com',
    description='Skill API implementation over MoveIt 2 / Nav2, exposed as ROS 2 actions.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [],
    },
)
```
Note `packages=find_packages(exclude=['test'])` — a `test/` directory at the
package root (sibling of `setup.py`, e.g. `src/robot_skills/test/`) is the
expected location for pytest files; it will be excluded from the installed
package automatically.

`src/robot_skills/setup.cfg` (verbatim; `robot_backends/setup.cfg` identical
except path):
```
[develop]
script_dir=$base/lib/robot_skills
[install]
install_scripts=$base/lib/robot_skills
```

`resource/robot_skills` and `resource/robot_backends` are empty ament-index
marker files — normal, leave as-is.

`README.md` for both is a 2-line stub: title + one-line description +
"Status: skeleton. See `docs/design/PROJECT.md`..." — the `robot_skills`
README's description text ("over MoveIt 2/Nav2, exposed as ROS 2 actions")
is now stale relative to this feature (no ROS 2 action wiring here); worth
updating if you touch the README, but not an acceptance criterion.

## Package dependency gap (important)

`robot_backends/package.xml` currently declares only `<depend>rclpy</depend>`
— **no dependency on `robot_skills`**. But the acceptance criteria require
`robot_backends.MockBackend.execute(skill) -> SkillResult` where `Skill`,
`SkillResult`, and `Observation` are types owned by `robot_skills`
(brief: "`robot_skills` — the skill API contract (skill + observation + result
types)"; "`robot_backends` — the `RobotBackend` interface + `MockBackend`
implementation"). So `robot_backends` must import from `robot_skills` at
runtime, and its `package.xml` needs `<depend>robot_skills</depend>` added
for colcon to build/register the two packages in the correct order and for
the dependency to be honest. This is a real gap in the current scaffold, not
a design decision already made — flagging it, not prescribing the fix.

Also note: both `package.xml`s declare `<depend>rclpy</depend>` even though
this feature is explicitly **pure Python, no ROS graph** (brief: "Importable
and runnable with no ROS 2 graph running"; "Pure Python, deterministic: no
ROS graph required to import/run"). Importing `rclpy` doesn't itself require
a running graph, but the skill/observation/backend modules should not need
to import `rclpy` at all to satisfy the brief's spirit — keep the new code's
import chain ROS-free even though the dependency declaration exists in the
skeleton.

## Binding design constraints (CLAUDE.md + decisions.md)

- **D2 — control altitude: hybrid, skill-level.** "Skill/pose commands are
  the default... the LLM must not do IK." (`decisions.md:8`). Skills are
  goals (`navigate_to`, `grasp`, etc.), never raw joints — matches
  `CLAUDE.md` invariant 1 ("brain commands skills..., never raw joints; IK/
  planning lives below the API").
- **D3 — perception output: structured, not prose.** "structured scene JSON
  with grounded 3D coordinates, not prose captions" (`decisions.md:9`).
  `Observation`'s object list must carry id/label/3D pose/graspable, matching
  `CLAUDE.md` invariant 4.
- **D9 — backend abstraction.** "Mock → Sim (MuJoCo) → Real behind one
  `RobotBackend` interface... develop/test the harness with zero physics,
  then swap backends without touching brain code" (`decisions.md:15`).
  `MockBackend` must satisfy an interface that `SimBackend`/`RealBackend`
  (not built here) can also satisfy without changing the interface shape.
- **D16 — control plane.** Relevant to this feature only insofar as it
  establishes the surrounding loop (`plan → atomic step (ROS 2 action) →
  re-perceive → replan`, `decisions.md:22`) and confirms skills are the
  atomic unit an eventual ROS 2 action wraps — this feature stops short of
  that wiring (brief: "No ROS 2 action/message wiring in this feature").
- `CLAUDE.md` invariant 2: "New code must work against Mock first" — this
  *is* the Mock; later Sim/Real backends must satisfy the same interface
  this feature defines.
- `CLAUDE.md` invariant 3 (safety layer) and PROJECT.md's harness layering
  put the safety/clamp layer **between** brain and skills, not inside
  `MockBackend` — the brief's non-goals confirm no clamp logic belongs here;
  the API shape should merely *allow* a safety wrapper later (brief:
  "should allow a safety wrapper and a ROS action layer without redesign").
- PROJECT.md's skill list to implement, verbatim from "Next steps" §3:
  `navigate_to`, `move_gripper`, `grasp`, `place`, `extend_column`,
  `open/close_gripper` (`docs/design/PROJECT.md:125`) — matches the brief's
  acceptance criteria list exactly.
- PROJECT.md's stack line: "Backend abstraction: Mock → Sim (MuJoCo) → Real
  behind one `RobotBackend` interface (`execute_skill`, `get_observation`)"
  (`docs/design/PROJECT.md:53`) — note this PROJECT.md phrasing says
  `execute_skill`; the brief's acceptance criteria say `execute(skill)` and
  `get_observation()` (`docs/features/mock-skill-api/brief.md:45-46`). The
  brief is the fixed target per its own preamble and per this feature's
  instructions — treat `execute()`/`reset()`/`get_observation()` as
  authoritative over the older PROJECT.md phrasing.

## Acceptance criteria (restated from brief.md)

1. `robot_skills` defines typed skills: `navigate_to(place)`,
   `move_gripper(side, pose)`, `grasp(object_id)`, `place(pose)`,
   `extend_column(height)`, `open_gripper(side)`, `close_gripper(side)` —
   dataclasses/enums/Protocol, shape at implementer's discretion, documented.
2. `robot_skills` defines `Observation` (robot pose incl. current place,
   column height, per-gripper state/held object, list of objects with
   id/label/3D pose/graspable) and `SkillResult` (status ∈ {ok, failed},
   reason, resulting observation). Both serializable to plain dict/JSON.
3. `robot_backends` defines `RobotBackend` interface: `reset()`,
   `get_observation()`, `execute(skill) -> SkillResult`.
4. `MockBackend` implements it with a small deterministic world model:
   navigate changes place; grasp attaches a present+graspable object to a
   free gripper; place/open drops the held object at/near the robot;
   extend_column sets height; open/close toggles the gripper.
5. Failure paths return `status=failed` with a reason and leave state intact:
   grasp missing/ungraspable object; grasp with occupied gripper; place/
   close-drop with empty gripper; navigate to unknown place.
6. Importable/runnable with no ROS 2 graph running.

Required tests (from brief, restated): per-skill round trip; the four
failure paths; dict/JSON round-trip for `Observation` and `SkillResult`;
a composition scenario (`navigate_to(kitchen) → grasp(mug_1) →
navigate_to(table) → place(...)`) all returning `ok` with expected final
state.

## Owned paths

`src/robot_skills/`, `src/robot_backends/` only. No ROS 2 action/message
wiring; no other package may be touched.

## Likely touch points

- `src/robot_skills/robot_skills/__init__.py` and new modules under
  `src/robot_skills/robot_skills/` for skill types, `Observation`,
  `SkillResult`.
- `src/robot_backends/robot_backends/__init__.py` and new modules under
  `src/robot_backends/robot_backends/` for the `RobotBackend` interface and
  `MockBackend`.
- `src/robot_backends/package.xml` — likely needs `<depend>robot_skills</depend>`
  added (see gap above).
- New `test/` directories: `src/robot_skills/test/`, `src/robot_backends/test/`
  (see below — no existing test dir to copy from).
- Possibly `README.md` in both packages, currently stale/generic stubs.

## Test layout and how `pixi run test` resolves

- `pixi.toml` tasks (`pixi.toml:24-27`): `build = "colcon build --symlink-install"`,
  `test = "colcon test && colcon test-result --verbose"`. No pytest.ini or
  pytest config exists anywhere in the repo (checked, none in `src/` or
  root).
- No package in `src/` currently has a `test/` directory or any test files —
  this feature establishes the pattern. Standard ROS 2 `ament_python`
  convention (matches `find_packages(exclude=['test'])` in each `setup.py`)
  is a `test/` directory at the package root, sibling to `setup.py`, e.g.
  `src/robot_skills/test/test_skills.py`, containing plain pytest tests;
  `colcon test` auto-discovers and runs these via the `ament_python` build
  type's pytest hook (the pixi env has `colcon-python-setup-py` and
  `colcon-ros` extensions installed, confirmed under
  `.pixi/envs/default/lib/python3.12/site-packages/`).
- The skeleton `package.xml`s declare `test_depend`s on `ament_copyright`,
  `ament_flake8`, `ament_pep257`, `python3-pytest` — the standard
  `ros2 pkg create --build-type ament_python` template also emits
  `test/test_copyright.py`, `test/test_flake8.py`, `test/test_pep257.py`
  stub files that invoke those linters via pytest. **Those stub files are
  absent here** — nothing currently exercises those test_depends. Whether to
  add them (so `colcon test` lints, matching the declared dependencies) or
  leave them out (declared but unused) is a judgment call for the
  implementer; the brief's acceptance criteria only require functional
  tests, not lint-test scaffolding.
- Since both packages must be **pure Python, importable without a ROS graph**,
  the required tests can equally be run directly with `pytest` from within
  the package dirs during development — but the given/expected CI path is
  `pixi run build` then `pixi run test` (colcon), per `.claude/agents/implementer.md:21-22`
  and `.claude/agents/test-runner.md:9`. Running raw `pytest` without a prior
  `colcon build` may not resolve the `robot_skills → robot_backends`
  cross-package import unless the workspace is sourced or the two package
  source dirs are both on `PYTHONPATH`/`sys.path` — verify the actual
  resolution mechanism (colcon symlink-install + `local_setup` sourcing)
  before relying on bare `pytest`.

## Existing repo conventions relevant here

- Maintainer/license metadata is uniform across all packages: `maintainer='Jaime'`,
  `maintainer_email='hejaca00@gmail.com'`, `license='MIT'` — reuse verbatim.
- No source-file copyright/license header convention exists yet (every
  `__init__.py` in the repo is currently empty, 0 bytes) — this feature sets
  the first precedent; no established header to match.
- No top-level `LICENSE` file was found in the repo (only `license='MIT'` in
  package metadata) — not this feature's concern.
- Python version: 3.12 (Jazzy), per `docs/design/PROJECT.md:82` ("Python
  3.12 (Jazzy)") and the pixi env (`python3.12` site-packages paths
  observed).
- `docs/features/TEMPLATE/brief.md` and `status.md` show the standard
  feature-doc shape; `docs/features/mock-skill-api/status.md` currently
  shows `phase: context`, `round: 0`, `owner_agent: context-explorer` — the
  implementer will pick this up next per `.claude/commands/run-feature.md`.

## Known gotchas / open questions for the implementer

1. **Shared-type ownership vs. import direction.** The brief splits skill
   API types into `robot_skills` and the backend interface into
   `robot_backends`, but `RobotBackend.execute(skill) -> SkillResult` forces
   `robot_backends` to import `robot_skills` types. Confirm/add the
   `package.xml` dependency (see above) and decide whether `SkillResult` and
   `Observation` truly belong in `robot_skills` (brief says so explicitly:
   "the skill + observation + result types") even though semantically they
   describe backend/world state — that's the brief's call, not open, but the
   resulting one-directional dependency (`robot_backends` → `robot_skills`,
   never the reverse) is a constraint the implementer must preserve for
   later `SimBackend`/`RealBackend` swap-in per D9.
2. **Where within each package the modules live** (single `types.py` vs.
   `skill.py`/`observation.py`/`result.py` split; `mock_backend.py` vs. a
   `backends/mock.py` subpackage) — brief explicitly leaves "final shape at
   the implementer's discretion."
3. **Test scaffolding for declared lint test_depends** (`ament_copyright`,
   `ament_flake8`, `ament_pep257`) — add stub test files to exercise them, or
   leave the test_depends unused for now. Not an acceptance criterion either
   way.
4. **Determinism requirement** ("no wall-clock/random nondeterminism in the
   Mock", brief) — if the mock world model needs any IDs/timestamps, they
   must be deterministic/seedable, not `datetime.now()`/unseeded `random`.
5. **"Place" as both a location name and a skill name.** The brief overloads
   `place` as a named location concept (`navigate_to(place)`, robot pose
   "incl. current place") and as the skill `place(pose)` (put down held
   object). Keep these conceptually distinct in the implementation (e.g. a
   `Place`/location identifier type vs. the `Place` skill/action) to avoid
   confusing naming collisions — brief doesn't disambiguate the names, so
   this is left to implementer judgment.
6. **Failure path for `navigate_to` unknown place** implies the Mock world
   model needs *some* fixed/seeded notion of known places (e.g. a small
   preset set like kitchen/table used in the scenario test) — the brief's
   required composition test references `kitchen` and `table` by name
   (`brief.md:63-64`), so the Mock's initial state must include those as
   valid places out of the box for that test to be constructable, alongside
   at least one seeded object (e.g. `mug_1`) that is present and graspable.
