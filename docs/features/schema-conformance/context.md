# Context: conform skill/observation schema to D17–D19 (issue #33)

## 1. Owned paths

Per the issue, this is a conformance pass on the existing seam, not a new
package.

**In bounds (edit):**
- `src/robot_skills/robot_skills/skills.py`
- `src/robot_skills/robot_skills/observation.py`
- `src/robot_skills/robot_skills/result.py`
- `src/robot_skills/robot_skills/serialization.py`
- `src/robot_skills/robot_skills/geometry.py` (only if the stamp/nested
  handling touches it — see §3, likely untouched)
- `src/robot_skills/robot_skills/__init__.py` (export `SCHEMA_VERSION`)
- `src/robot_skills/test/*` (extend/add tests; new golden-fixture test +
  fixture file(s))
- `src/robot_backends/robot_backends/mock_backend.py` (grasped flag,
  gripper-empty idempotent success — already mostly true, see §5)
- `src/robot_backends/test/*` (extend/add tests for grasped + FailureCode
  classification if backend-side code references it)

**Out of bounds / do not touch:**
- `src/robot_safety/**` — currently a skeleton package
  (`src/robot_safety/robot_safety/__init__.py` is empty, no logic; see §6).
  The brief asks that `robot_safety`/`robot_backends` *can* classify a
  `FailureCode`'s owner programmatically — that means the classifier lives in
  `robot_skills.result` (importable by both), not that `robot_safety` gains
  new code in this issue.
- `docs/design/decisions.md` — read-only design source (D17–D19 already
  ratified there, `docs/design/decisions.md:28-33`).
- `.github/workflows/guards.yml`, `pixi.toml` tasks — no CI/build changes
  needed; `pixi run test` already drives `colcon test` + the zero-test guard.
- Any other `src/robot_*` package (`robot_brain`, `robot_bringup`,
  `robot_description`, `robot_perception`) — not part of this seam.

## 2. Current schema inventory (every public type)

All in `src/robot_skills/robot_skills/`. Every one of these is a
`@dataclass(frozen=True)` subclassing `JsonSerializable`
(`serialization.py:86`) and has both `to_dict`/`from_dict` **except** where
noted.

`geometry.py`:
- `Point(x, y, z)` — `geometry.py:37-83`. `to_dict`/`from_dict` at
  `geometry.py:69,73`.
- `Quaternion(x, y, z, w)` — `geometry.py:87-126`. `to_dict`/`from_dict` at
  `geometry.py:111,115`.
- `Pose(position: Point, orientation: Quaternion)` — `geometry.py:130-184`.
  `to_dict`/`from_dict` at `geometry.py:166,173`.

`skills.py` (all register in `SKILL_TYPES`/`_REGISTRY`, all serialize via the
shared `Skill.to_dict`/`Skill.from_dict` polymorphic dispatch,
`skills.py:124-145`):
- `Side` (Enum: `LEFT`/`RIGHT`) — `skills.py:74-78`. Not itself
  `JsonSerializable`; serialized as its `.value` string wherever embedded.
- `Skill` (ABC base, frozen dataclass) — `skills.py:86-145`. Concrete
  subclasses implement `_payload`/`_from_payload`; `to_dict`/`from_dict` live
  once on the base (`skills.py:124`, `128`).
- `NavigateTo(location: str)` — `skills.py:154-172`.
- `MoveGripper(side: Side, pose: Pose)` — `skills.py:176-200`.
- `Grasp(object_id: str, side: Side | None = None)` — `skills.py:204-236`.
- `Place(pose: Pose, side: Side | None = None)` — `skills.py:240-271`.
- `ExtendColumn(height: float)` — `skills.py:275-293`.
- `_GripperSkill(side: Side)` — shared base, `register=False`, not itself
  dispatchable — `skills.py:296-313`.
- `OpenGripper(side)` — `skills.py:317-320`.
- `CloseGripper(side)` — `skills.py:324-327`.
- `skill_from_dict` — module function alias, not a type.
- `SKILL_TYPES` — a `MappingProxyType` registry, not a serializable type.

`observation.py`:
- `GripperState` (Enum: `OPEN`/`CLOSED`) — `observation.py:53-58`. Not
  `JsonSerializable`; serialized as `.value`.
- `SceneObject(object_id, label, pose, graspable=True, held_by: Side|None)` —
  `observation.py:61-127`. `to_dict`/`from_dict` at `observation.py:96,106`.
- `GripperObservation(side, state, pose, held_object_id: str|None)` —
  `observation.py:130-188`. `to_dict`/`from_dict` at `observation.py:162,171`.
  **This is the type item 2 (`grasped`) targets.**
- `RobotState(pose, column_height, grippers: tuple[GripperObservation,...],
  location: str|None)` — `observation.py:191-266`. `to_dict`/`from_dict` at
  `observation.py:236,245`.
- `Observation(robot: RobotState, objects: tuple[SceneObject,...]=(),
  known_locations: tuple[str,...]=())` — `observation.py:269-395`.
  `to_dict`/`from_dict` at `observation.py:359,367`. **Top-level
  machine-to-machine type — gets `SCHEMA_VERSION` per the brief.**

`result.py`:
- `SkillStatus` (Enum: `OK`/`FAILED`) — `result.py:42-46`. Not
  `JsonSerializable`; serialized as `.value`.
- `FailureCode` (Enum, 10 members) — `result.py:49-66`. Not
  `JsonSerializable`; serialized as `.value`. **This is the type item 3
  (backend-refusal vs. safety-clamp split) targets.** Full member list in
  §6.
- `SkillResult(skill: Skill, status: SkillStatus, observation: Observation,
  reason: str|None=None, code: FailureCode|None=None)` —
  `result.py:69-172`. `to_dict`/`from_dict` at `result.py:143,153`.
  **Top-level machine-to-machine type — gets `SCHEMA_VERSION` per the
  brief.**

`serialization.py` — infra, not schema types: `JsonDict` (a `dict[str, Any]`
type alias), `JsonSerializable` (ABC), `SerializationError`, `parse_errors`
(context manager), `check_keys`, `ensure_mapping`, `get_str`,
`get_optional_str`, `get_float`, `get_bool`, `get_mapping`, `get_sequence`,
`get_enum`, `get_optional_enum`.

`validation.py` — constructor-level validators, not schema types:
`as_finite_float`, `as_identifier`, `as_enum`, `as_optional_enum`.

**"Every public type" for the golden-fixture test** = everything in each
module's `__all__` that is a concrete, instantiable `JsonSerializable`:
`Point`, `Quaternion`, `Pose`, every concrete `Skill` subclass (`NavigateTo`,
`MoveGripper`, `Grasp`, `Place`, `ExtendColumn`, `OpenGripper`,
`CloseGripper` — 7, matching `test_every_documented_skill_is_registered`,
`src/robot_skills/test/test_skills.py:41-53`), `SceneObject`,
`GripperObservation`, `RobotState`, `Observation`, `SkillResult`. That's 3
(geometry) + 7 (skills) + 4 (observation) + 1 (result) = 15 concrete
serializable types. Enums (`Side`, `GripperState`, `SkillStatus`,
`FailureCode`) are not `JsonSerializable` themselves but their `.value`
appears inside the golden fixtures of whichever type embeds them.

## 3. Serialization mechanics

- **No shared dataclass-to-dict helper / no `dataclasses.asdict`.** Every
  type hand-rolls `to_dict`/`from_dict` field by field (see citations in
  §2). `serialization.py` supplies only the *parsing primitives*
  (`get_str`, `get_enum`, `check_keys`, …) and the `JsonSerializable` ABC
  contract (`serialization.py:86-122`) plus `to_json`/`from_json`
  convenience wrappers built on `to_dict`/`from_dict`
  (`serialization.py:111-122`). Adding `SCHEMA_VERSION` therefore means
  editing each top-level type's `to_dict`/`from_dict`/`check_keys` call by
  hand — there is no single choke point that stamps it automatically.
- **Wire-format compatibility policy** is documented as a module docstring
  section in `serialization.py:28-51` ("Wire-format compatibility policy").
  Key line: *"If independently versioned peers ever become real ... the
  migration is not to relax `check_keys` globally ... It is to add one
  reserved, explicitly ignored `extensions` sub-object to the
  machine-to-machine types (`Observation`, `SkillResult`), keeping `Skill`
  ... strict."* (`serialization.py:44-50`). This is the passage D18 refers
  to as "already written" and the brief calls the "wire-format policy
  already documented." Note: it describes an `extensions` escape hatch for
  *unknown keys*, not `SCHEMA_VERSION` explicitly — `SCHEMA_VERSION` is new
  work, but this policy section is the natural place to also document the
  version-stamp/compat rule (additive vs. bump), per D18's text.
- **Insertion point for `SCHEMA_VERSION`:** the brief says it belongs "in the
  wire form of the machine-to-machine types (`Observation`, `SkillResult`)
  via `to_dict()`/`from_dict()`" — i.e. only the two top-level types, not
  nested ones (`Pose`, `SceneObject`, `GripperObservation`, `RobotState`,
  `Skill` subclasses do **not** get their own stamp). Concretely:
  `Observation.to_dict` (`observation.py:359-365`) and `SkillResult.to_dict`
  (`result.py:143-151`) are the two functions to touch, plus
  `Observation.from_dict` (`observation.py:367-395`) and
  `SkillResult.from_dict` (`result.py:153-172`), plus each one's
  `check_keys(...)` call (`observation.py:372-377`, `result.py:158-163`) to
  admit the new key.
- **Nested types are unaffected structurally** — `Observation.to_dict` calls
  `self.robot.to_dict()` (`observation.py:362`) which recurses through
  `RobotState.to_dict` → each `GripperObservation.to_dict`; none of those
  need to know about `SCHEMA_VERSION`. Same for `SkillResult.to_dict` calling
  `self.skill.to_dict()` and `self.observation.to_dict()`
  (`result.py:146,150`) — `SkillResult`'s stamp would sit only at its own
  top level, and its embedded `observation` dict would *also* carry its own
  `schema_version` key (since `Observation.to_dict()` is called directly),
  i.e. the two stamps can appear at two nesting depths in one `SkillResult`
  dict. The implementer needs to decide/document whether that's the
  intended shape or whether `SkillResult` should suppress/require agreement
  with the nested `Observation`'s stamp — the brief doesn't resolve this,
  it's a real design question to flag, not assume away.

## 4. Round-trip / equality semantics

- **Equality:** plain dataclass `__eq__` (frozen dataclasses default to
  `eq=True`), field-by-field, including nested dataclasses/tuples/enums.
  No custom `__eq__` anywhere in these modules. Tests rely on this directly,
  e.g. `assert rebuilt == observation`
  (`src/robot_skills/test/skill_api_fixtures.py:51`).
- **`from_dict` is strict about unknown keys.** `check_keys` (
  `serialization.py:156-173`) computes `allowed = set(required) |
  set(optional)` and raises `SerializationError` on anything else
  (`serialization.py:168-173`). This is exercised directly:
  `test_result_parsing_is_strict` expects `SerializationError` with message
  `'unknown key'` when an extra `duration_s` key is added to a `SkillResult`
  dict (`src/robot_skills/test/test_skill_result.py:115-116`), and
  `test_observation_parsing_is_strict` does the same for a `weather` key on
  `Observation` (`src/robot_skills/test/test_observation.py:206-207`).
  **Consequence:** a version-tolerant `from_dict` for `Observation`/
  `SkillResult` must explicitly add `'schema_version'` to the
  `optional=(...)` (or `required=(...)`) tuple passed to `check_keys` in
  both types' `from_dict`, or every existing dict containing the new key
  will be rejected as "unknown key(s)". This is exactly the mechanism the
  golden-fixture test needs to exploit to prove drift is caught (a field
  silently dropped from `to_dict` won't trip `check_keys`, but the golden
  fixture comparing exact `to_dict()` output will catch it; a field
  renamed/retyped needs an explicit assertion on the fixture, not just
  round-trip, since `check_keys` only catches *added* unknown keys, not
  renamed/dropped ones).
- `SerializationError` is the **only** exception `from_dict` raises,
  including translated constructor invariant violations
  (`serialization.py:125-141`, `parse_errors`); this is asserted directly in
  `test_from_dict_raises_only_serialization_error`
  (`src/robot_skills/test/test_skill_serialization.py:76-108`).
- The `JsonSerializable` contract is documented as: `type(x).from_dict(
  x.to_dict()) == x` and `json.loads(json.dumps(x.to_dict())) == x.to_dict()`
  (`serialization.py:91-93`) — both must keep holding for every type,
  including the two now carrying `schema_version`.

## 5. Gripper path

- `GripperObservation` (`observation.py:130-188`) fields today: `side`,
  `state` (`GripperState`), `pose`, `held_object_id: str | None`. **No
  `grasped` field yet** — this is the D19 addition target. Note
  `is_holding` is already a derived `@property` (`observation.py:157-160`)
  reading `held_object_id is not None`; `grasped` is semantically close but
  distinct per D19 — it should reflect a load-bearing grip outcome (e.g. the
  result of the most recent `close_gripper`/`grasp` attempt), not simply
  "currently holding something" — the brief allows "a bare bool," so the
  implementer decides exactly what `grasped` tracks vs. `held_object_id`.
- Constructors of `GripperObservation`: only one production call site,
  `MockBackend._gripper_observation`
  (`src/robot_backends/robot_backends/mock_backend.py:330-338`), which
  builds it from the mutable `_MockGripper` dataclass
  (`mock_backend.py:66-73`: `state`, `offset`, `orientation`,
  `held_object_id`). `_MockGripper` also has **no `grasped`/aperture/force
  field today** — the mock's internal mutable state needs a place to carry
  whatever `grasped` reflects.
- **`close_gripper` handler** — `MockBackend._close_gripper`
  (`mock_backend.py:299-306`): already **never fails**. It just flips
  `gripper.state = GripperState.CLOSED` and returns an informational note
  ("already closed") or `None`; there is no `GRIPPER_EMPTY` refusal here
  today (contrary to what the D19 prose implies was the previous
  behavior — verify this against the actual code, not the design doc, since
  the code already does the "success" half of D19). Confirmed by
  `test_close_gripper_on_thin_air_picks_nothing_up`
  (`src/robot_backends/test/test_mock_skills.py:256-266`): closing on
  nothing returns `SkillStatus.OK`. **What's missing** is only the
  `grasped=false` reporting — the success behavior is already conformant.
- **`open_gripper` handler** — `MockBackend._open_gripper`
  (`mock_backend.py:284-297`): also already idempotent-success; returns
  `'... already open'` as an informational reason when nothing changes
  (`mock_backend.py:297`), confirmed by
  `test_open_gripper_opens_and_drops_what_it_holds`
  (`src/robot_backends/test/test_mock_skills.py:269-273`, the `idle` case).
- **Where `GRIPPER_EMPTY` *is* actually raised today:** only in `_place`
  (`mock_backend.py:256-260`) and `_resolve_holding_side`
  (`mock_backend.py:414-442`, called from `_place`) — i.e. attempting to
  `Place` with nothing held. It is **not** raised by `_close_gripper` or
  `_open_gripper`. Tests exercising this: `test_place_with_an_empty_gripper`
  and `test_place_with_the_wrong_gripper_named`
  (`src/robot_backends/test/test_mock_failures.py:95-114`).
- **State the mock tracks per gripper:** `state` (open/closed),
  `offset`/`orientation` (arm posture relative to shoulder — not exposed on
  the wire; `GripperObservation.pose` is derived from it via
  `_gripper_pose`, `mock_backend.py:322-328`), `held_object_id`. No
  aperture/contact-force state exists anywhere in the mock world model
  (`mock_world.py`) — the brief explicitly allows skipping that ("a bare
  bool is acceptable").
- Grasp itself (`_grasp`, `mock_backend.py:220-249`) is unaffected by D19 in
  behavior (it already fails cleanly for `UNKNOWN_OBJECT`/`NOT_GRASPABLE`/
  `OBJECT_ALREADY_HELD`/`GRIPPER_OCCUPIED`/`OUT_OF_REACH` and succeeds
  otherwise) but presumably should also set `grasped=true` on the
  now-holding gripper's observation, since `grasped` is meant to answer "did
  I get it?" generally, not just for `close_gripper`.

## 6. `FailureCode` inventory

Defined `result.py:49-66`, 10 members:

| Member | Wire value | Raised by (mock_backend.py) | Brief's bucket |
|---|---|---|---|
| `UNKNOWN_LOCATION` | `unknown_location` | `_navigate_to`, `mock_backend.py:202-206` | backend refusal |
| `UNKNOWN_OBJECT` | `unknown_object` | `_grasp`, `mock_backend.py:224-228` | backend refusal |
| `NOT_GRASPABLE` | `not_graspable` | `_grasp`, `mock_backend.py:230-233` | backend refusal |
| `OBJECT_ALREADY_HELD` | `object_already_held` | `_grasp`, `mock_backend.py:235-239` | backend refusal |
| `GRIPPER_OCCUPIED` | `gripper_occupied` | `_require_free_gripper` (`mock_backend.py:396-403`), `_refuse_both_grippers_occupied` (`mock_backend.py:405-412`) | backend refusal |
| `GRIPPER_EMPTY` | `gripper_empty` | `_place` (`mock_backend.py:256-260`), `_resolve_holding_side` (`mock_backend.py:414-442`) | **not listed in either of the brief's two buckets** — see below |
| `OUT_OF_REACH` | `out_of_reach` | `_require_reachable`, `mock_backend.py:352-364` | backend refusal |
| `OUT_OF_RANGE` | `out_of_range` | `_extend_column`, `mock_backend.py:272-282` | backend refusal |
| `UNSUPPORTED_SKILL` | `unsupported_skill` | `MockBackend.execute`, `mock_backend.py:181-187` | backend refusal |
| `REJECTED` | `rejected` | not raised anywhere in this codebase yet (reserved for the future safety layer per its docstring, `result.py:53-55`) | safety-layer clamp |

**Gotcha the implementer must resolve:** the brief's scope section lists the
backend-refusal set as `OUT_OF_REACH, OUT_OF_RANGE, UNKNOWN_LOCATION,
UNKNOWN_OBJECT, NOT_GRASPABLE, GRIPPER_OCCUPIED, OBJECT_ALREADY_HELD,
UNSUPPORTED_SKILL` (8 codes) and the safety-clamp set as `REJECTED` (+
future). That's 9 of 10 members — **`GRIPPER_EMPTY` is not in either
list.** By D17's rule of thumb ("can't be done" = backend refusal), placing
on an empty gripper reads as a backend refusal (it's a precondition
failure, not an in-flight safety abort), so it almost certainly belongs in
the backend-refusal bucket too — but the brief's explicit enumeration
omits it, so this is a gap to flag/resolve deliberately rather than
silently reclassify. Consumers of `FailureCode` today: `mock_backend.py`
(raises), and tests in both packages (`test_mock_failures.py`,
`test_backend_interface.py`, `test_skill_result.py`,
`test_skill_serialization.py`) that assert on specific codes. `robot_safety`
has no code today (`src/robot_safety/robot_safety/__init__.py` is empty) —
it consumes nothing yet; the brief's "robot_safety/robot_backends can
classify a code's owner programmatically" is a forward-looking capability
requirement on the classifier's *existence and importability*, not evidence
of an existing call site to update.

## 7. Existing test layout + conventions

- **Per-package `test/` directory**, discovered via each package's
  `pytest.ini` (`testpaths = test`,
  `src/robot_skills/pytest.ini:15`, same pattern in
  `src/robot_backends/pytest.ini`). Both `pytest.ini` files disable the
  `launch_testing`/`launch_ros` plugins that are incompatible with pytest 8
  in this RoboStack env (`src/robot_skills/pytest.ini:1-14`).
- **`colcon test` finds tests** via each package's `setup.py`/`package.xml`
  declaring the ament_python test dependency + pytest entry point (standard
  ament_python layout); `pixi run test` = `python
  scripts/check_test_integrity.py` (`pixi.toml:30`), which wraps `colcon
  test` (`colcon build --symlink-install` already run via `pixi run build`,
  `pixi.toml:25`) plus a **zero-collected-tests guard**
  (`scripts/check_test_integrity.py`, tested by
  `scripts/tests/test_audit.py` — the actual #24 test-integrity artifact,
  since `docs/features/test-integrity/` was deleted at merge per repo
  convention). That guard fails a package whose JUnit result reports zero
  tests, no result file, or every collected test skipped
  (`scripts/tests/test_audit.py:96-249`) — i.e. a golden-fixture test file
  that exists but is never collected (e.g. wrong filename, not starting
  with `test_`) would silently not count, so name it `test_*.py` under
  `test/`.
- **Fixtures/helpers module pattern:** each package keeps a
  `*_fixtures.py` module (not `conftest.py`) holding builder functions and
  assertion helpers, imported directly by test modules —
  `src/robot_skills/test/skill_api_fixtures.py` (`make_gripper`,
  `make_robot_state`, `make_observation`, `assert_json_safe`,
  `assert_round_trip`) and `src/robot_backends/test/mock_backend_fixtures.py`
  (`snapshot`, `run`, `assert_pose_close`, `assert_refused`). `conftest.py`
  in each package is thin — just pytest fixtures wrapping those helpers
  (`src/robot_skills/test/conftest.py:14-23`,
  `src/robot_backends/test/conftest.py:13-22`). A new golden-fixture test
  should follow this pattern: put fixture-building helpers in the existing
  `*_fixtures.py` module (or a new one) rather than `conftest.py`.
- **No existing golden/fixture-*file* precedent** (no checked-in JSON/YAML
  fixture files anywhere in `src/robot_skills` or `src/robot_backends` —
  confirmed by directory listing, only `.py` test files exist). The golden
  fixtures for this issue are new: the implementer decides whether to
  inline expected dicts as Python literals in the test module (matching the
  existing style, e.g. `test_skill_result.py:103`'s inline dict literal) or
  add real fixture files (e.g. `test/golden/*.json`) — nothing in the repo
  currently does the latter for this seam.
- **Style notes to match:** descriptive `test_<behavior>` names with a
  one-line docstring explaining the *reason* the test exists (nearly every
  test in every file read has one); tests assert on `to_dict()` equality
  against other `to_dict()` calls, not hardcoded literals, except where
  pinning an exact wire shape is the point (e.g.
  `test_skill_result.py:103`) — a golden-fixture test is exactly the case
  where hardcoded literals are the point, so it's an intentional exception
  to the general "compare structures, not literals" habit seen elsewhere.
  `scripts/tests/test_audit.py`'s docstring opens with "The fixtures below
  are the XML shapes ... was **observed** to write" — i.e. #24's tests
  ground fixtures in real observed output and say so; the golden-fixture
  test here should likewise generate its fixtures from real `to_dict()`
  calls (not hand-typed guesses) and document that.

## 8. Design decisions (verbatim, `docs/design/decisions.md`)

**D17** (`docs/design/decisions.md:28`):
> Split by *kind* of limit, not by component. **Kinematic/workspace
> reachability** (target pose out of range, joint stop, self-collision,
> unreachable) is the **backend's** job: it *refuses* the skill up front and
> returns a "couldn't run" error (`unreachable`/fault) before motion.
> **Dynamic safety** (joint-limit clamp, collision avoidance during motion,
> gripper force limits, e-stop) is the **safety/clamp layer** (D4): it sits
> between brain-issued skills and the backend and *clamps or aborts*
> in-flight, returning a safety event. Rule of thumb: *"can't be done" =
> backend refusal; "unsafe to continue" = safety-layer clamp/abort.*

**D18** (`docs/design/decisions.md:30`):
> One schema definition for `Observation` / `SkillResult` / skill signatures
> as typed models in a single package, carrying an explicit
> `SCHEMA_VERSION`. Because every consumer lives in the one base repo (D13),
> a breaking change updates *all* binders in the *same* PR — no
> multi-version support, no deprecation windows (zero external consumers
> yet). **Compat rule:** additive optional field = non-breaking; remove /
> rename / retype = version bump + update all binders atomically.
> **Enforcement:** a golden-fixture schema test that fails if a field is
> dropped or retyped without a `SCHEMA_VERSION` bump (fits the
> test-integrity guard from #24).

**D19** (`docs/design/decisions.md:32`):
> Add an additive `grasped: bool` (with aperture / contact-force detail) to
> `SkillResult`/`Observation`. `close_gripper` on nothing is a **successful**
> skill that reports `grasped=false`; the brain learns "did I get it?" from
> that flag in the closed loop (D4), not from a raised exception. Same
> idempotent-report semantics for `open_gripper` on an already-open gripper.
> Errors stay reserved for "couldn't run" (fault, unreachable, e-stop);
> **over-force while closing** is a *safety event* on D17's clamp/abort
> path, a separate concern.

Relevant earlier decisions:
- **D3** (`docs/design/decisions.md:9`): perception must be "structured scene
  JSON with grounded 3D coordinates, not prose captions" — why `Observation`
  cannot regress to prose even under a version bump.
- **D4** (`docs/design/decisions.md:10`): "every skill returns status + a
  fresh observation... A safety/clamp layer... is mandatory from day one" —
  the closed-loop contract `SkillResult` implements and that the `grasped`
  flag feeds.
- **D13** (`docs/design/decisions.md:19`): "one **base repo** holds all
  glue/IP... The **skill API is the seam**" — why D18's "update all binders
  atomically in one PR" is affordable (no external/versioned consumers to
  placate).

CLAUDE.md architectural invariants directly implicated: invariant 1 ("the
skill API is the seam"), invariant 3 ("the safety layer clamps/rejects
illegal commands... never bypass it" — motivates keeping the D17
classification advisory/documentary on `FailureCode`, not a behavior
change), invariant 4 ("structured scene JSON with coordinates").

## 9. Risks / gotchas

- **`check_keys` will reject `schema_version` unless explicitly allowed** in
  both `Observation.from_dict` and `SkillResult.from_dict` — see §4. Forget
  this and every existing round-trip test breaks immediately with "unknown
  key(s): schema_version".
- **Exact-dict-literal tests will break if not updated:**
  - `src/robot_skills/test/test_skill_result.py:103` asserts
    `as_dict['skill'] == {'skill': 'grasp', 'object_id': 'mug_1', 'side':
    'right'}` — this is the *nested* `skill` sub-dict, not the top-level
    `SkillResult` dict, so it is unaffected by `SkillResult` getting
    `schema_version` at its own top level (confirmed: `Skill` types are
    explicitly excluded from the stamp per the brief and per
    `serialization.py:48-50`'s "keeping `Skill` ... strict").
  - No other test in either package asserts full-dict equality against a
    literal for `Observation`/`SkillResult` — the grep in this exploration
    found only self-referential `to_dict() == to_dict()` /
    `to_dict() == snapshot(...)` comparisons (`mock_backend_fixtures.py:85`,
    `test_mock_scenario.py:105,115`, `test_mock_skills.py:266,307`), which
    survive automatically since both sides gain the same key. **The new
    golden-fixture test is therefore the *first* place a literal dict
    listing every field will exist for these two types — get it right.**
- **`GripperObservation` gains a field (`grasped`) that is *not* one of the
  two brief-mandated stamped types.** Since only `Observation`/`SkillResult`
  get `SCHEMA_VERSION` in their own dict, but `GripperObservation` (nested
  inside `RobotState` inside `Observation`) gets a *new field*, that's an
  additive field on a *non-stamped* type nested inside a stamped one — per
  D18's compat rule this is still non-breaking (additive), but the golden
  fixture for `Observation`/`RobotState`/`GripperObservation` all need
  updating together since they nest.
- **`_MockGripper` needs new mutable state** for whatever `grasped` tracks
  (`mock_backend.py:66-73`) — decide if it's derived (e.g. `state is
  GripperState.CLOSED and held_object_id is not None`) or independently
  tracked (e.g. sticky through an `open_gripper` that doesn't reset it).
  The brief doesn't specify; get it right against the acceptance criterion
  "`close_gripper`(empty) ... return `OK` results ... reporting
  `grasped=false`."
- **`GRIPPER_EMPTY` classification gap** — see §6; resolve explicitly rather
  than silently picking a bucket.
- **`FailureCode.REJECTED`** has no current call site (`robot_safety` is a
  skeleton) — the classifier must still correctly bucket it as the sole
  safety-clamp member today, anticipating the safety layer that doesn't
  exist yet.
- **`test_no_source_file_imports_rclpy`**
  (`src/robot_backends/test/test_no_ros_runtime.py:189-214`) recursively
  scans every `.py` file under `robot_skills`/`robot_backends` package roots
  for `rclpy` imports — any new module added for this work (e.g. a
  `schema_version.py` or a classifier helper) must not import `rclpy`,
  directly or lazily, or this test fails. (It's a very unlikely mistake
  here, but the test is strict enough to be worth knowing about.)
- **Golden-fixture test must actually fail on drift** — the acceptance
  criterion says it must be "demonstrably" so. Since `check_keys` only
  catches *added* unknown keys (not renamed/dropped ones, and not retyped
  values), the golden test's own assertions (not `check_keys`) are what
  must catch rename/drop/retype. A round-trip test (`from_dict(to_dict()) ==
  x`) alone does **not** catch this class of drift either, because renaming
  a field consistently in both `to_dict` and `from_dict` still round-trips
  — only a fixed, checked-in expected `to_dict()` shape (or explicit
  field-name/type assertions) catches it. This is presumably exactly why
  D18 calls for a *golden fixture*, not just the existing round-trip tests.
- **Two-nesting-depth `schema_version`** in `SkillResult.to_dict()` (its own
  top-level key plus one inside the nested `observation` sub-dict) — flagged
  in §3 as an open design question the brief does not resolve.

## 10. Build/test commands

From `CLAUDE.md` and `pixi.toml`:
- `pixi install` then work inside `pixi shell`.
- Build: `pixi run build` → `colcon build --symlink-install`
  (`pixi.toml:25`).
- Full test suite (with the zero-test-collected guard from #24):
  `pixi run test` → `python scripts/check_test_integrity.py`
  (`pixi.toml:30`).
- Re-audit the last run's results without re-running: `pixi run test-audit`
  → `python scripts/check_test_integrity.py --audit-only` (`pixi.toml:32`).
- **Single-package test run** (documented in
  `scripts/check_test_integrity.py:34`):
  `python scripts/check_test_integrity.py --packages-select robot_skills`
  (swap in `robot_backends` as needed). This still runs through the same
  guard, so a `robot_skills`-only golden-fixture regression is caught the
  same way a full run would catch it.
- Pure-pytest ad hoc run (bypassing the colcon/guard wrapper, useful for
  fast local iteration but **not** a substitute for the driver above before
  calling something green): `cd src/robot_skills && python -m pytest
  test/` (relies on the package being importable, e.g. via a prior `colcon
  build --symlink-install` + sourcing the install, or running from inside
  the built/sourced workspace — `pytest.ini` already restricts `testpaths =
  test` and disables the incompatible `launch_testing` plugins,
  `src/robot_skills/pytest.ini:1-15`).
