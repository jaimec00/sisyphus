# Context: world-state store (JSON, disk-persisted map + object registry)

Issue #54. Roadmap step 3 (`docs/design/PROJECT.md:127`); D16's "world-state =
queried store" half (`docs/design/PROJECT.md:43`). Next free decision number:
**D23**.

## 1. Acceptance criteria (restated from the issue)

1. `get_observation()` reflects state loaded from + written to a JSON
   live-state file.
2. A skill that moves/grasps an object, then a **fresh** MCP process pointed at
   the same live-state file, shows the mutation persisted.
3. `reset()` restores the scene from a read-only seed file (not from the live
   file, and not by re-serializing a Python literal).
4. Writes are atomic (temp file + `os.replace`); a crash mid-write must not
   corrupt the live-state file.
5. The default seed reproduces today's `default_world()` scene byte-for-byte
   in effect (existing suites/behaviour do not regress).
6. `docs/design/decisions.md` gets a new `D23` entry; `PROJECT.md`'s roadmap
   line (currently line 127, item 3) gets updated to reflect the landed store.

Non-goals (explicit): no ROS 2 service/node, no YAML, no MuJoCo, no
skill-API/wire-format (`SCHEMA_VERSION`) change.

## 2. Owned paths (from the issue's "what exists today")

- `src/robot_backends/robot_backends/mock_world.py` — read, likely unchanged
  or trimmed (seed becomes JSON-derived, `default_world()` may become a
  compatibility shim or move).
- `src/robot_backends/robot_backends/mock_backend.py` — the consumer that
  currently owns all mutable world bookkeeping; this is the file the store
  slots underneath.
- `src/robot_mcp/robot_mcp/__init__.py` / `server.py` — constructs the backend
  the store must be wired into (currently `MockBackend()` with no arguments).
- New package, likely `src/robot_world/` (brief's suggested name) — the
  store itself: locations/object registry, JSON load/save, atomic write.

## 3. `mock_world.py` — full anatomy (verbatim, `src/robot_backends/robot_backends/mock_world.py`)

Three frozen dataclasses plus one factory function. **Everything here is
immutable** — this is the seed, never the live state.

```python
@dataclass(frozen=True)
class ObjectSpec:
    """One object placed in the mock world at a known world-frame pose."""
    object_id: str
    label: str
    pose: Pose
    graspable: bool = True
    # __post_init__ validates object_id/label as non-blank identifiers
    # (as_identifier), pose is a Pose, graspable is a bool.
```

```python
@dataclass(frozen=True)
class RobotModel:
    """A deliberately crude kinematic stand-in for the two-arm robot."""
    shoulder_offset_y: float = 0.18
    shoulder_offset_z: float = 0.50
    reach_radius: float = 0.85
    home_gripper_offset: Point = Point(0.35, 0.0, -0.05)
    min_column_height: float = 0.0
    max_column_height: float = 1.20
    # __post_init__ validates finiteness, reach_radius > 0,
    # min <= max column height, home_gripper_offset within reach_radius.
    def shoulder(self, base_pose: Pose, column_height: float, side: Side) -> Point: ...
    def column_range_text(self) -> str: ...
```

```python
@dataclass(frozen=True)
class MockWorld:
    """The seed state of a mock scene: locations, objects, and where we start."""
    locations: Mapping[str, Pose]
    start_location: str
    objects: tuple[ObjectSpec, ...] = ()
    start_column_height: float = 0.3
    robot: RobotModel = field(default_factory=RobotModel)
    # __post_init__: locations non-empty, becomes a MappingProxyType;
    # objects must have unique object_id; start_location must be a known
    # location; start_column_height must be within [min, max] column range.
    @property
    def start_pose(self) -> Pose: ...   # self.locations[self.start_location]
```

`default_world()` — **the exact seed scene** (`mock_world.py:173-213`), which
the new JSON seed must reproduce:

- Locations (4): `charger` (0,0,0), `kitchen` (2,0,0), `table` (0,2,0),
  `living_room` (-2,1,0). `start_location='charger'`.
- Objects (7), all `Pose.from_xyz(x, y, z)` with identity orientation:
  - `mug_1` / label `mug` / (2.30, 0.10, 0.90) / graspable=True
  - `plate_1` / label `plate` / (2.30, -0.10, 0.90) / graspable=True
  - `bowl_1` / label `bowl` / (2.25, 0.00, 0.92) / graspable=True
  - `counter_1` / label `counter` / (2.40, 0.00, 0.45) / graspable=**False**
  - `book_1` / label `book` / (0.30, 2.10, 0.75) / graspable=True
  - `cup_1` / label `cup` / (0.30, 1.90, 0.75) / graspable=True
  - `sofa_1` / label `sofa` / (-2.00, 1.60, 0.40) / graspable=**False**
- `start_column_height=0.3`, `robot=RobotModel()` (all defaults).

None of `ObjectSpec`/`RobotModel`/`MockWorld` implement `JsonSerializable`
(no `to_dict`/`from_dict`) — **empirically observed** by reading the file:
only `Point`/`Pose`/`Quaternion` (in `robot_skills.geometry`) and the
machine-to-machine types (`Observation`, `SkillResult`) have that.  Any
JSON round-trip for the seed/live-state files is new code.

## 4. `mock_backend.py` — full anatomy

`src/robot_backends/robot_backends/mock_backend.py`. Constructor:

```python
def __init__(self, world: MockWorld | None = None) -> None:
    self._world = world if world is not None else default_world()
    self._handlers: Mapping[type[Skill], Callable[..., str | None]] = {...}
    self.reset()
```

Mutable state, all instance attributes set in `reset()` (`mock_backend.py:134-151`):
- `self._base_pose: Pose`, `self._location: str | None`,
  `self._column_height: float` — **robot proprioceptive state**.
- `self._grippers: dict[Side, _MockGripper]` — `_MockGripper` holds
  `state: GripperState`, `offset: Point`, `orientation: Quaternion`,
  `held_object_id: str | None`.
- `self._objects: dict[str, _MockObject]` — `_MockObject` holds
  `object_id`, `label`, `pose: Pose`, `graspable: bool`,
  `held_by: Side | None`; built via `_MockObject.from_spec(ObjectSpec)`.

So **today the "object registry" and "robot proprio state" are both
reconstructed from `self._world` on every `reset()`**, and nothing about them
is ever written back to `self._world` (which stays the frozen seed) or to
disk. This is exactly the state the brief asks the new store to own instead.

Per-skill mutations (handler methods, each validates-then-mutates or raises
`_SkillRefused`, never partially mutates):
- `_navigate_to` (`NavigateTo`): sets `_base_pose`, `_location` from
  `self._world.locations[skill.location]`; refuses `UNKNOWN_LOCATION`.
- `_move_gripper` (`MoveGripper`): sets one gripper's `offset`/`orientation`;
  refuses `OUT_OF_REACH` via `_require_reachable`.
- `_grasp` (`Grasp`): refuses `UNKNOWN_OBJECT`, `NOT_GRASPABLE`,
  `OBJECT_ALREADY_HELD`, `GRIPPER_OCCUPIED`/reach failure; on success sets
  gripper `offset`/`orientation`/`state=CLOSED`/`held_object_id`, and sets
  `item.held_by = side` **on the object**.
- `_place` (`Place`): refuses `GRIPPER_EMPTY`/reach failure; sets gripper
  `state=OPEN`/`held_object_id=None`, sets `item.pose = skill.pose`,
  `item.held_by = None`.
- `_extend_column` (`ExtendColumn`): sets `_column_height`; refuses
  `OUT_OF_RANGE`.
- `_open_gripper` (`OpenGripper`): idempotent (D19); if holding something,
  drops it at the current gripper pose (`item.pose = self._gripper_pose(side)`,
  `item.held_by = None`), always ends `state=OPEN`.
- `_close_gripper` (`CloseGripper`): idempotent (D19); just sets
  `state=CLOSED`; never attaches an object (only `Grasp` does).
- After every successful handler, `execute()` calls `self._carry_held_objects()`
  (`mock_backend.py:360-365`), which re-poses every held object to its
  gripper's current world pose — so a held object's `pose` is *derived*, not
  independently authoritative, while it is held.

`get_observation()` (`mock_backend.py:153-173`) builds an `Observation` from
current `_base_pose`/`_column_height`/`_grippers`/`_location` plus
`self._objects.values()` sorted by `object_id`, plus
`known_locations=tuple(sorted(self._world.locations))` — **locations always
come from the immutable seed `self._world`, never mutate**.

`reset()` (`mock_backend.py:134-151`) rebuilds all mutable state from
`self._world` (the seed) and returns `self.get_observation()`.

`execute()` (`mock_backend.py:175-194`): looks up a handler by exact type or
MRO walk (`_handler_for`), returns `SkillResult.failure(...)` for an
unsupported skill or a caught `_SkillRefused`, otherwise
`SkillResult.ok(skill, self.get_observation(), note)`.

**Gotcha for the store design:** `held_by` currently lives on **both**
`_MockObject.held_by` (a `Side`) and `_MockGripper.held_object_id` (a
`str | None`) — two views of the same fact, kept in sync by hand in every
handler. `Observation.__post_init__` (`observation.py:356-390`,
`_check_held_objects_agree`) **enforces** they agree when building an
`Observation`. Whatever the store's schema is, this redundancy (or an
equivalent invariant) has to be preserved because the wire format demands it
downstream.

## 5. `RobotBackend` interface (`src/robot_backends/robot_backends/interface.py`, quoted in full above)

```python
class RobotBackend(ABC):
    @abstractmethod
    def reset(self) -> Observation: ...
    @abstractmethod
    def get_observation(self) -> Observation: ...
    @abstractmethod
    def execute(self, skill: Skill) -> SkillResult: ...
```

Total interface — must not raise for a legal-but-refused skill. This feature
is explicitly a **data-source refactor**: the interface, `Observation`,
`SkillResult`, and `SCHEMA_VERSION` (`src/robot_skills/robot_skills/serialization.py:114`,
asserted via `check_schema_version` in `Observation.from_dict` /
`SkillResult.from_dict`) must not change.

`Observation` (`src/robot_skills/robot_skills/observation.py`) and its
sub-dataclasses `RobotState`, `GripperObservation`, `SceneObject` were quoted
above (section shows every field + validation). Key invariant enforced at
construction: a gripper's `held_object_id` and the matching object's
`held_by` must agree (`Observation._check_held_objects_agree`,
`observation.py:356-389`) — raises `ValueError` if the world model built the
observation from a half-updated state.

`Point`/`Pose`/`Quaternion` (`src/robot_skills/robot_skills/geometry.py`) are
the only world-adjacent types that already implement `JsonSerializable`
(`to_dict`/`from_dict`), so `Pose.to_dict()`/`Pose.from_dict()` are directly
reusable for the store's own JSON shape.

## 6. How `robot_mcp` constructs the backend today

`src/robot_mcp/robot_mcp/server.py:301-316`:

```python
def build_server(
    backend: RobotBackend | None = None,
    safety: SafetyLayer | None = None,
) -> Server:
    router = SkillToolRouter(MockBackend() if backend is None else backend, safety)
    return Server(SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS,
                  on_list_tools=router.list_tools, on_call_tool=router.call_tool)
```

`main()` (`server.py:336-338`) takes **no arguments** and calls
`anyio.run(run_stdio)`, which calls `build_server(None, None)` — i.e. **there
is currently no CLI flag, env var, or config file that points the stdio
server at anything**; it always gets a fresh default `MockBackend()`.
**Empirically observed** (read, not executed): no `argparse`, no
`os.environ` reads anywhere in `robot_mcp/server.py` or `__main__.py`. Wiring
the store's seed/live-state file paths into `main()`/`run_stdio()`/
`build_server()` (flag, env var, or both) is new plumbing the implementer
must add — nothing to reuse here beyond the existing `backend=` injection
point, which tests already use (`test/mcp_fixtures.py:38`:
`Client(build_server(backend, safety))`).

`SkillToolRouter` (`server.py:167-291`) wraps one backend behind one
`SafetyLayer`, serializing every call on an `anyio.Lock` (`_lock`) so
concurrent MCP calls cannot interleave world reads/writes — relevant if the
store does file I/O per mutation, since that I/O happens *inside* the same
lock scope as `_backend.execute()`/`_backend.get_observation()`/
`_backend.reset()` (`server.py:234-237`, `249-251`). Nothing async-native
about the store is required by this; it just means backend calls (and hence
store file I/O) are already serialized within one process.

## 7. `robot_safety`'s touchpoints with backend/world state

**None beyond reading an `Observation`.** `SafetyLayer.filter(skill, state)`
(`src/robot_safety/robot_safety/layer.py:168`) takes a `SafetyState`
(`src/robot_safety/robot_safety/state.py`), which wraps an `Observation`
sampled by the caller (`server.py:276`: `SafetyState(observation=self._backend.get_observation())`).
`robot_safety` never touches a backend, a `MockWorld`, or any file — its own
config (`limits.yaml`) is a *separate* resource, loaded via
`importlib.resources` in `src/robot_safety/robot_safety/limits.py:363-365`
(see section 10). **Confirmed by grep** — no `MockWorld`/`MockBackend`
imports anywhere under `src/robot_safety/robot_safety/`. Nothing in this
package needs to change for the store to land.

## 8. New ament_python package conventions

Every existing package (`robot_backends` quoted below; `robot_brain`,
`robot_safety`, `robot_skills`, `robot_mcp` match the same shape) needs
**all** of:

`package.xml` (`src/robot_backends/package.xml`, full text):
```xml
<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>robot_backends</name>
  <version>0.0.0</version>
  <description>Backend abstraction: Mock | Sim (MuJoCo) | Real behind one interface.</description>
  <maintainer email="hejaca00@gmail.com">Jaime</maintainer>
  <license>MIT</license>

  <buildtool_depend>ament_python</buildtool_depend>
  <depend>rclpy</depend>
  <depend>robot_skills</depend>

  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
```
Note: `<depend>rclpy</depend>` is present even though the package is pure
Python and forbids importing it (see `test_no_ros_runtime.py`) — every
existing package declares it; a new `robot_world` package should follow suit
unless the implementer has reason to omit it (not investigated further —
flag as a minor open point, low stakes).

`setup.py` (`src/robot_backends/setup.py`, full text):
```python
from setuptools import find_packages, setup

package_name = 'robot_backends'

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
    description='Backend abstraction: Mock | Sim (MuJoCo) | Real behind one interface.',
    license='MIT',
    extras_require={'test': ['pytest']},
    entry_points={'console_scripts': []},
)
```
`robot_brain`'s `setup.py` (`src/robot_brain/setup.py`) shows the pattern for
shipping **non-Python resources inside the importable package** (which is
what the seed JSON needs — see section 10):
```python
    package_data={package_name: ['openclaw/*.md', 'openclaw/*.json']},
    include_package_data=True,
```

`setup.cfg` (`src/robot_backends/setup.cfg`, full text):
```ini
[develop]
script_dir=$base/lib/robot_backends
[install]
install_scripts=$base/lib/robot_backends
```

`resource/<package_name>` — an **empty** marker file (`resource/robot_backends`
is 0 bytes, confirmed with `wc -l` → 0), required by ament's resource index.

`pytest.ini` (`src/robot_backends/pytest.ini`, full text) — every package
needs this to disable two RoboStack `launch_testing`/`launch_ros` pytest
plugins that are incompatible with pytest >= 8 and would otherwise abort the
whole session on collection:
```ini
[pytest]
addopts = -p no:launch_testing -p no:launch_ros
testpaths = test
```

`README.md` — every package has one (see `robot_backends/README.md`, quoted
in full above) describing contents + a usage example.

`test/` layout (from `robot_backends/test/`): `conftest.py` (shared
fixtures, e.g. `backend`/`world`), a `*_fixtures.py` helper module (kept out
of `conftest.py` so it can be imported directly — see
`mock_backend_fixtures.py`), and one module per behaviour area, plus four
mandatory linter test files, verbatim boilerplate (quoted in full above):
`test_copyright.py` (`ament_copyright.main.main`), `test_flake8.py`
(`ament_flake8.main.main_with_errors`), `test_pep257.py`
(`ament_pep257.main.main`, `--add-ignore D213`). `robot_backends` also has
`test_no_ros_runtime.py` (subprocess + AST-based rclpy-import scanner — see
section 9) and `test_backend_interface.py`; a new `robot_world` package
should include an equivalent no-ROS-runtime test since it is pure Python
(CLAUDE.md invariant 2 / D21's laptop-as-service model) and per the brief
must stay pure-Python with no ROS 2 node.

**What a new `robot_world` package needs, concretely:** `package.xml`,
`setup.py` (+ `package_data`/`include_package_data` if the seed ships inside
the package — see section 10's open question on where the seed lives),
`setup.cfg`, `pytest.ini`, `resource/robot_world` (empty), `README.md`,
`robot_world/__init__.py`, its implementation module(s), and a `test/`
directory with `conftest.py`, linter boilerplate, and real behavioural
tests. `robot_world` would need `<depend>robot_skills</depend>` (for
`Pose`/`Point`/`Quaternion`/`Side`) in `package.xml`; `robot_backends` would
in turn need `<depend>robot_world</depend>` if it consumes the store.

## 9. Test conventions / the test-integrity guard

`pixi run test` → `scripts/check_test_integrity.py` (quoted structure above).
Key mechanics an implementer must know:

- **Package discovery is git-based**: any `package.xml` **tracked by git**
  under `src/` is an "expected" package (`discover_packages`,
  `check_test_integrity.py:231-256`) — so a new `robot_world` package is
  automatically picked up by `pixi run test` the moment its `package.xml` is
  `git add`ed, no registration elsewhere needed.
- **Zero-test and hollow-suite guards**: a package producing no JUnit result,
  zero collected tests, or (`_STATUS_NO_REAL_TESTS`) only linter tests while
  holding non-trivial implementation code under an importable subpackage
  (`find_implementation_modules`, `check_test_integrity.py:339-367`) **fails
  the run**. Three linter tests are an honest suite only for a genuinely
  empty skeleton.
- **The ratchet**: `scripts/test_baseline.json` (quoted in full above) records
  per-package non-linter test counts; a package dropping below its recorded
  floor fails (`_STATUS_BELOW_BASELINE`). A **new** package has no entry yet
  (a harmless note, not a failure) until someone runs
  `python scripts/check_test_integrity.py --update-baseline` and commits the
  regenerated `scripts/test_baseline.json` — the implementer must do this
  once `robot_world` (and any changed counts in `robot_backends`/`robot_mcp`)
  has its final test suite, and commit the file.
- Current baseline counts (for reference, not to be hand-edited):
  `robot_backends: 60`, `robot_mcp: 71`, `robot_safety: 176`,
  `robot_skills: 106`, `robot_brain: 48`, `_workspace_tooling: 111`,
  `robot_bringup`/`robot_description`/`robot_perception`: `0`.
- **No per-package registration in `pixi.toml`** is needed — `pixi.toml`
  (quoted in full above) only has workspace-wide `build`/`test` tasks driving
  `colcon build`/`colcon test` over the whole `src/` tree.

`test_no_ros_runtime.py` pattern (`src/robot_backends/test/test_no_ros_runtime.py`,
quoted in full above): (1) a clean-subprocess probe importing the package and
asserting no `rclpy*`/`rosidl*`/`ament_index_python` module ended up in
`sys.modules`; (2) an AST-based scanner (`find_forbidden_imports`) that walks
every `.py` file in the package looking for `import rclpy`, `from rclpy...
import`, and dynamic `importlib.import_module('rclpy...')` calls, including
inside function bodies (lazy imports) — with a self-test that the detector
itself catches all four forms. A `robot_world` package should carry the same
test, scoped to its own module, given the brief's "no ROS 2 service/node ...
keep it pure-Python" instruction.

## 10. File-IO / atomic-write / config-path precedent

**No atomic-write pattern exists anywhere in the repo yet.** Grepped for
`os.replace`/`NamedTemporaryFile`/`tempfile`/`atomic` across `src/`: the only
hits are `tempfile.TemporaryDirectory` used in
`src/robot_brain/test/test_openclaw_validates.py:43,155` for an isolated
`$HOME` in a test, unrelated to world-state persistence. **The atomic
temp-file-plus-`os.replace` write is new code for this feature** — nothing
to crib beyond stdlib `tempfile`/`os.replace` themselves.

**Config-resource-loading precedent** (read-only assets shipped *inside* the
importable package, not `share/`) — two examples, both using
`importlib.resources`:

`src/robot_brain/robot_brain/agent.py:26-27,49-67` (full pattern):
```python
from importlib import resources
...
RESOURCE_PACKAGE = 'robot_brain'
_RESOURCE_DIRECTORY = 'openclaw'
CONFIG_RESOURCE = 'openclaw.robot.json'

def _read(name: str) -> str:
    resource = resources.files(RESOURCE_PACKAGE) / _RESOURCE_DIRECTORY / name
    return resource.read_text(encoding='utf-8')

@lru_cache(maxsize=1)
def config_fragment() -> Mapping[str, Any]:
    return json.loads(_read(CONFIG_RESOURCE))
```
`src/robot_brain/setup.py:12-13`: `package_data={package_name:
['openclaw/*.md', 'openclaw/*.json']}, include_package_data=True` — this is
what makes `openclaw/openclaw.robot.json` (which lives at
`src/robot_brain/robot_brain/openclaw/openclaw.robot.json`, i.e. **inside**
the importable package, not under `resource/`) ship with the installed
package.

`src/robot_safety/robot_safety/limits.py:363-365` (second instance of the
same pattern, for `limits.yaml`):
```python
resource = resources.files('robot_safety') / DEFAULT_LIMITS_RESOURCE
```
(`DEFAULT_LIMITS_RESOURCE = 'limits.yaml'`, `limits.py:47`, shipped at
`src/robot_safety/robot_safety/limits.yaml` — beside the code, not `resource/`.)

**Load-bearing caveat for this feature**: `importlib.resources` reads assets
from wherever the package is installed (which for a symlink-installed colcon
build is the source checkout, but in general — a wheel, a zip — may not be
writable). This pattern is proven for the **read-only seed**, but the
**live-state file the brief requires the store to *write*** cannot safely
live at an `importlib.resources`-addressed path if the package could ever be
installed somewhere read-only. No existing pattern in this repo addresses a
*writable* runtime data path — this is a genuine open question (see section
11).

## 11. `docs/design/decisions.md` format + `PROJECT.md` roadmap

Decisions file structure (`docs/design/decisions.md`, full file read):
`# Robot Project — Decisions Log`, "Append-only ... Newest at the bottom.
Reversing a decision = add a new dated entry that supersedes the old one",
then `## <YYYY-MM-DD> — <session title>` headers, each followed by one or
more `- **D<N> — <Title> (closes #<issue>).** <body, present tense,
rationale marked *Rationale:* at the end>` bullets.

D21 (quoted in full, `decisions.md:40`):
> **D21 — The robot's brain IS a dedicated OpenClaw Telegram agent, not a
> custom harness (supersedes the brain-location half of D16).** Jaime talks
> to an OpenClaw agent over Telegram; that agent *is* the planner. Its
> **system prompt** carries the skill API (names/args/units/limits) +
> observation format + safety envelope + 2–3 worked examples. It plans by
> **calling the robot's skills as MCP tools** (`robot_mcp`) and reads each
> tool's structured `SkillResult` + `Observation` back — **OpenClaw's native
> tool-call loop IS the perceive → decide → act → re-perceive loop** (D4), so
> there is **no custom planner loop and no tag/tool-call parser** (the
> `<grasp mug_1>` text format of D2 is obsoleted by native tool-calling), and
> OpenClaw's **native memory** serves the user-preference store.
> - **Laptop is reduced to the robot-side service:** ... (four sub-bullets,
>   see full file for "determinism boundary", "safety enforced server-side",
>   "deferred to contingencies", *Rationale:*).

D22 (quoted in full, `decisions.md:47`):
> **D22 — Scope: a slow, low-cost chore robot, explicitly NOT competing with
> the frontier.** The target is a robot that *eventually gets some chores
> done* on a small budget — not SOTA dexterity, not a research win. **Favor
> reuse + classical/scripted skills first:** MoveIt pick-and-place of *rigid*
> objects + Nav2 driving + the LLM planner already covers "drive over, pick
> that up, put it there, tidy, load the dishwasher" with **no learned
> policies at all**. **Learned skills** (imitation learning via LeRobot / VLA,
> teleop data collection) are pulled in **only** for the specific chores
> classical methods can't do. *Rationale:* keeps cost ($0 laptop compute,
> ~$1k open hardware, no GPU until genuinely forced per D10) and difficulty
> down; the genuinely-custom hard parts (the extendable column, robust
> fragile-object grasping) are the differentiators and the real risks — not
> the whole robot.

**Next free D-number is D23**, confirmed by grep (`D22` is the highest
existing entry; nothing named `D23` anywhere in the file).

`PROJECT.md` line 43 (the "two memories" line inside "System topology /
control plane", quoted in full):
> - **Two memories:** world-state (map/objects) = a queried **store/DB**,
>   needed day one (owned by the robot-side service); user-preferences =
>   **OpenClaw's native memory**, grows over time.
This line does not itself need factual correction (it already describes what
this feature builds) but may warrant a forward pointer once the store lands.

`PROJECT.md` "Next steps" list, items 2–4 (`PROJECT.md:126-128`, quoted):
```
2. Expand `robot_mcp` to expose the full skill set (`navigate_to`, `move_gripper`,
   `grasp`, `place`, `extend_column`, `open/close_gripper`, `get_observation`)
   over the `RobotBackend` seam; land the **safety/clamp layer** server-side (D17).
3. Add the **world-state store** (map/objects) queried by the robot-side service.
4. Confirm laptop Ubuntu version → pin ROS 2 distro; then swap Mock → **MuJoCo**
   behind the same skills.
```
Item 3 (line 127) is what this feature closes and needs to be updated (e.g.
marked done / reworded to reflect the landed JSON-file store, matching how
step 2 already reads as accomplished given `robot_mcp`/`robot_safety` exist).

## 12. Open questions for the manager

1. **Scope of persisted state — objects+map only, or the full snapshot
   (incl. robot proprio: base pose/location, column height, gripper
   state/held-by)?** The brief flags this explicitly. Recommendation: persist
   the full snapshot (map is static and could stay in the seed only, but
   objects **and** robot state, since a "fresh MCP process shows the
   mutation persisted" acceptance test most naturally covers *both* "the mug
   moved" and "the robot is still standing at the table holding it" — a
   restart that resets the robot to the charger while objects stay moved
   would be a confusing half-persisted world). Tradeoff: a full snapshot
   means the store's schema must also model grippers (`_MockGripper`'s
   `state`/`offset`/`orientation`/`held_object_id`), which is more surface
   than "map/objects" as literally named in the roadmap line.

2. **How does `MockBackend` consume the store — delegate all bookkeeping to
   it, or keep `MockBackend` doing physics/reach-math against a store-backed
   world?** The reach/shoulder/carry-held-object math
   (`_shoulder`, `_gripper_pose`, `_carry_held_objects`,
   `_require_reachable`) is genuinely backend physics, not persistence, and
   should almost certainly stay in `MockBackend`. Recommendation: the store
   owns *what* the world contains (locations, objects, robot proprio) and
   *how it's read/written from disk*; `MockBackend` keeps owning *how a
   skill changes it* (validation + the offset/reach math), calling into the
   store's mutation methods (`update_object_pose`, etc.) instead of a bare
   dict. Tradeoff: this is close to today's shape swapped one layer down,
   vs. a store that also validates/executes skills itself (more coupling,
   arguably violates "pure store, no skill semantics").

3. **Seed + live-state file locations, and how `robot_mcp` points at them.**
   The only shipped-resource precedent (`agent.py`/`limits.py`, section 10)
   is **read-only** `importlib.resources` access into the installed package
   — fine for the seed, unproven/likely wrong for a file the process must
   *write* (a package install location may not be writable; symlink-install
   dev builds happen to be, but that's incidental). `robot_mcp.server.main()`
   currently takes no CLI args/env vars at all (section 6) — that plumbing
   doesn't exist yet and needs to be added regardless of the chosen default.
   Recommendation: ship the **seed** as a package resource (following D21's
   own precedent) with a default live-state path *outside* the installed
   package (e.g. a `--live-state-path`/env-var-overridable path, defaulting
   to something like a `.dev`-analogous gitignored runtime-state directory
   or an XDG-style user-data dir) so `python -m robot_mcp` and tests can both
   write it. Needs an explicit ruling since it also decides whether tests
   spin up `MockBackend`/store instances against `tmp_path` (almost
   certainly yes, for isolation) vs. a single shared file.

4. **What happens when the live-state file is missing or corrupt on
   startup?** Not addressed by the brief. Options: (a) auto-seed from the
   read-only seed file (first run just works, but silently "fixes" a
   corrupted file an operator might want to know about), (b) hard-fail
   loudly (matches this repo's general strictness — e.g.
   `SerializationError`/`SafetyConfigError` patterns raise rather than
   silently falling back). Recommendation: hard-fail on a *corrupt* file
   (parse error) — matches `check_test_integrity.py`'s own baseline-file
   philosophy ("a floor that quietly evaporates... would silently switch the
   ratchet off") and this repo's general "raise loudly on malformed input"
   stance (`SerializationError`, `SafetyConfigError`) — but auto-seed on a
   *missing* file (first run / gitignored path), since that's the expected
   steady state for a fresh checkout.

5. **Where does `held_by` live, and does the store need to preserve the
   redundant object-side/gripper-side agreement `Observation` currently
   enforces?** Today `_MockObject.held_by: Side | None` and
   `_MockGripper.held_object_id: str | None` are two independently mutated
   views of one fact (section 4's gotcha), and `Observation.__post_init__`
   asserts they agree. If the store owns object state but `MockBackend`
   keeps owning gripper state (per question 2's likely split), the store's
   `update_object_pose`/held-by mutation and `MockBackend`'s gripper mutation
   must be called together, atomically from the caller's perspective, or the
   two can drift and `get_observation()` will raise. Needs an explicit
   design decision on which side is the single source of truth for
   "held-by" (object registry entry vs. gripper dict vs. computed from one
   into the other).

6. **Concurrency / locking across *processes*.** `SkillToolRouter` already
   serializes concurrent calls *within* one process via `anyio.Lock`
   (section 6), but the brief's own acceptance test ("a skill ... then a
   fresh MCP process against the same live-state file") implies the file can
   be read/written by more than one process over time, just not
   *concurrently* in the tested scenario. Not addressed by the brief.
   Recommendation: no cross-process file lock for this iteration (single
   robot-side service is the deployment model per D16/D21 — "one task at a
   time" is already a system-level guard) but flag it explicitly as a
   known gap rather than silently assuming it away, since a second `pixi run
   openclaw`/test run pointed at the same live-state path concurrently would
   race on `os.replace`.

7. **Schema versioning of the world JSON file itself.** The brief's
   non-goals exclude a *skill-API* wire-format change, but the world
   JSON file is a **new, separate** on-disk format with no relationship to
   `SCHEMA_VERSION` (`robot_skills.serialization`, section 3/5) other than
   sharing the same JSON-safe-dict philosophy. Not addressed by the brief.
   Recommendation: stamp the world file with its own small version marker
   (independent counter, e.g. `world_schema_version: 1`) from day one — cheap
   now, and this repo's own D18 precedent ("adding a version stamp after the
   fact is exactly the kind of migration D18 was written to avoid needing
   twice") argues for not deferring it, even though the brief doesn't ask
   for it explicitly.

8. **Does `default_world()` (the Python function) survive, change meaning, or
   get removed?** The brief says "the default seed must reproduce today's
   `default_world()` scene" — implying the *scene* persists, not necessarily
   the *function*. `robot_backends/__init__.py` currently exports
   `default_world` publicly and `test/mock_backend_fixtures.py` /
   `test/test_mock_world.py` (not read in full here — worth the
   implementer's own check) may depend on calling it directly. Recommendation:
   keep `default_world()` as a thin function that returns the seed loaded
   from the shipped JSON (so it stays a valid `MockWorld` factory for any
   test that constructs a bespoke `MockBackend(world=...)`), rather than
   removing it — minimizes churn to existing tests per acceptance criterion
   5. Needs confirming against the actual existing test files
   (`src/robot_backends/test/test_mock_world.py`,
   `test_mock_scenario.py`) which the implementer should read before
   deciding — not fully inventoried here.
