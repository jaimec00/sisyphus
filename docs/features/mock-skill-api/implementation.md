# Implementation: mock-skill-api

> Written by the implementer. Describes the final design, the choices and
> tradeoffs behind it, and how each acceptance criterion is tested.

## What was built

Two pure-Python packages, no ROS graph, no physics, no wall clock, no RNG.

```
src/robot_skills/robot_skills/
  serialization.py   JsonSerializable base + strict JSON-safe parsing helpers
  validation.py      constructor-level validators (finite floats, identifiers, enums)
  geometry.py        Point, Quaternion, Pose  (shaped like geometry_msgs)
  skills.py          Side, Skill + the 7 commands, SKILL_TYPES registry
  observation.py     GripperState, SceneObject, GripperObservation, RobotState, Observation
  result.py          SkillStatus, FailureCode, SkillResult

src/robot_backends/robot_backends/
  interface.py       RobotBackend (ABC): reset / get_observation / execute
  mock_world.py      MockWorld, ObjectSpec, RobotModel, default_world()
  mock_backend.py    MockBackend
```

Tests live in each package's `test/` directory (ament_python convention).

## API shape

```python
from robot_backends import MockBackend
from robot_skills import (
    CloseGripper, ExtendColumn, Grasp, MoveGripper, NavigateTo, OpenGripper,
    Place, Pose, Side, SkillStatus,
)

backend = MockBackend()                     # default demo apartment; or MockBackend(world)
observation = backend.reset()               # -> Observation (also returned, not just stored)

result = backend.execute(NavigateTo('kitchen'))
result = backend.execute(Grasp('mug_1'))            # side optional -> left first
result = backend.execute(ExtendColumn(0.9))
result = backend.execute(MoveGripper(Side.RIGHT, Pose.from_xyz(2.3, -0.2, 0.95)))
result = backend.execute(NavigateTo('table'))
result = backend.execute(Place(Pose.from_xyz(0.35, 2.05, 0.75)))   # side optional

assert result.status is SkillStatus.OK        # or result.succeeded
result.observation.robot.location             # 'table'
result.observation.find_object('mug_1').pose  # where the mug ended up
result.to_dict()                              # JSON-safe, round-trips exactly
```

A failure:

```python
r = backend.execute(NavigateTo('mars'))
r.status  # SkillStatus.FAILED
r.code    # FailureCode.UNKNOWN_LOCATION
r.reason  # "unknown location 'mars'; known locations: charger, kitchen, living_room, table"
r.observation  # the *unchanged* world
```

Skills (all frozen dataclasses, all validated at construction):

| skill | arguments | notes |
|---|---|---|
| `NavigateTo` | `location: str` | named spot in the semantic map |
| `MoveGripper` | `side: Side`, `pose: Pose` | Cartesian goal; IK lives below the API |
| `Grasp` | `object_id: str`, `side: Side \| None` | `None` = backend picks a free gripper |
| `Place` | `pose: Pose`, `side: Side \| None` | `None` = backend picks the holding gripper |
| `ExtendColumn` | `height: float` | absolute metres |
| `OpenGripper` | `side: Side` | releases the load where the gripper is |
| `CloseGripper` | `side: Side` | grips nothing; `Grasp` is how you pick up |

## Decisions and tradeoffs

**1. Types live in `robot_skills`; `robot_backends` depends on it.**
Per the manager's ruling. `<depend>robot_skills</depend>` was added to
`src/robot_backends/package.xml` (it was missing). The dependency is strictly
one-directional and must stay that way, so `SimBackend`/`RealBackend` can drop
in without touching the contract.

**2. "Place" the skill vs. "place" the location.** The design docs overload the
word. Resolved by never using "place" for a location: a named spot is a
**location** (`NavigateTo(location=...)`, `RobotState.location`,
`Observation.known_locations`, `MockWorld.locations`), and the skill `Place`
only ever means "put the held object down". No `PlaceId` type was introduced —
locations are plain strings, because the semantic map (Nav2 + a later store) is
the authority on what a name means; the skill API just passes the name through
and reports `known_locations` so the brain can stay inside the vocabulary.

**3. Method names follow the brief** (`reset()`, `get_observation()`,
`execute(skill)`), not PROJECT.md's older `execute_skill`. `reset()` returns
the fresh `Observation` rather than `None`, so callers never need a follow-up
query — same closed-loop rule as `execute`.

**4. `execute` is total; failures are values, not exceptions.** Any legal
`Skill` returns a `SkillResult`, including one the mock does not implement
(`FailureCode.UNSUPPORTED_SKILL`). `TypeError` is reserved for programming
errors (passing a dict or a joint array instead of a `Skill`). This is what
lets a ROS 2 action layer map goal→result mechanically and a planner branch on
`result.code` without parsing prose.

**5. Failures carry both a code and a reason.** `FailureCode` is a closed enum
(machine-readable, JSON-safe, testable without string matching); `reason` is
the specific human/LLM-readable sentence ("cannot grasp 'mug_1': it is 2.30 m
from the left shoulder, beyond the 0.85 m reach (robot is at 'charger')").
`SkillResult.__post_init__` enforces that a failure has both and that a success
has no code, so status and code can never disagree. `FailureCode.REJECTED` is
reserved for the future safety layer.

**6. Validate-then-mutate, enforced structurally.** Every mock handler raises an
internal `_SkillRefused` before touching state; `execute` converts it to a
failed result. There is no code path that mutates and then discovers a problem.
The tests verify the *whole serialized world* is byte-identical after a refusal,
not merely that the status is `failed`.

**7. Observations are immutable snapshots.** All observation types are frozen
dataclasses holding tuples, rebuilt fresh on every `get_observation()`. A caller
(or a test) holding an observation cannot reach through it into the backend's
world model, which is also what makes "compare snapshots" a sound proof of
non-mutation.

**8. The mock has a crude reach/limit model (`RobotModel`).** Shoulders sit
either side of the base at `column_height + 0.5 m`; a gripper may be anywhere
within `reach_radius` (0.85 m) of its shoulder; the column travels 0–1.2 m.
Grasp/place/move_gripper beyond that return `OUT_OF_REACH`, and
`ExtendColumn` beyond travel returns `OUT_OF_RANGE`.
*Tradeoff:* the brief only required four failure paths, and range policy is
normally the safety layer's business. But a backend that accepts
`extend_column(-5)` or grasps a mug from across the apartment would make the
harness lie about what the real robot can do, and the composition test would no
longer prove that navigating was necessary. The distinction kept is: the
backend **refuses** physically impossible commands (as real hardware does); it
never **clamps** — clamping stays the safety layer's job, and the skill types
themselves do no range checking at all (`ExtendColumn(99.0)` is a legal object).
Base *orientation* is ignored in the reach maths on purpose; the mock reasons
about distances, and a real backend replaces this class wholesale.

**9. Two-arm semantics.** `Side` is `left`/`right`; `Grasp`/`Place` take an
optional side. Resolution is deterministic and documented: the first side in
`SIDE_ORDER = (LEFT, RIGHT)` that is free (grasp) or holding (place). Naming an
occupied gripper gives `GRIPPER_OCCUPIED`; naming an empty one for a place gives
`GRIPPER_EMPTY`; both hands full with no side named gives `GRIPPER_OCCUPIED`
naming both loads. Grasping an object someone is already holding has its own
code, `OBJECT_ALREADY_HELD`.

**10. `close_gripper` on an empty gripper succeeds.** The brief's failure list
mentions "place/close-drop with an empty gripper". Interpreted as *drop-like*
actions: `Place` with an empty gripper fails (`GRIPPER_EMPTY`); closing an empty
gripper is a legal no-op (it is the natural "prepare" action and failing it
would be surprising), and it deliberately does **not** pick anything up — the
mock has no contact model, `Grasp` is how you acquire an object. Likewise
`OpenGripper` on an empty gripper succeeds with an informational reason, and
drops the load when there is one. Both no-ops report `status=ok` plus a `reason`
("the left gripper was already open"), which is why `SkillResult` allows an
informational reason on success.

**11. Held objects are carried.** After every successful skill the mock glues
each held object's pose to its gripper's pose, so navigating or raising the
column moves the load, and the object list stays physically consistent with the
gripper state. `SceneObject.held_by` and `GripperObservation.held_object_id` are
redundant on purpose (the brain wants both views); the mock keeps them in sync
and the tests check both.

**12. Serialization is hand-written, not `dataclasses.asdict`.** Enums become
their string values, nested objects recurse, and parsing is strict: missing
keys, unknown keys and wrong types all raise `SerializationError` naming the
offending key. Strictness beats forgiveness here — a garbled LLM tool call
should be a loud, attributable failure, not a half-populated command. Tests
assert the dict form contains only JSON-native types and survives
`json.dumps`/`json.loads` unchanged.

**13. Lint stubs.** `test_flake8.py`, `test_pep257.py` and `test_copyright.py`
were added to both packages (they were declared as `test_depend`s but never
exercised) and all pass. Two notes:
- every new source file carries a short MIT header, which is what makes
  `ament_copyright` pass — this feature sets that precedent for the repo;
- `test_pep257.py` passes `--add-ignore D213`. D213 ("summary on the second
  line") and D212 ("summary on the first line") are mutually exclusive;
  `ament_flake8`'s own config selects the D212 style, so enforcing D213 too
  would make the two linters contradict each other. The code uses first-line
  summaries, as PEP 257 does. Nothing else is ignored.

**14. Build-config changes needed to make `colcon test` run at all** (both
inside the owned packages, both flagged here):
- `setup.py`: `tests_require=['pytest']` → `extras_require={'test': ['pytest']}`.
  Modern setuptools drops `tests_require`, so colcon saw no pytest dependency
  and ran `python -m unittest`, which collected nothing and exited 5.
- `pytest.ini`: disables the environment's `launch_testing` / `launch_ros`
  pytest plugins, which declare hooks pytest 9 rejects and abort the session on
  load. Neither package uses launch testing. The file documents itself and
  should be deleted once the environment ships a compatible `launch_testing`.

## Test inventory (107 tests at round 0; 116 after the round 1 fixes below)

### `robot_skills` — 55 tests (58 after round 1)
| file | covers |
|---|---|
| `test_geometry.py` (7) | Point/Quaternion/Pose arithmetic, finite/type validation, immutability, round trips, strict parsing |
| `test_skills.py` (10) | AC1: all seven skills exist, are registered under their wire names, are frozen data, validate their arguments, accept `Side` as enum or string; polymorphic `Skill.from_dict` dispatch; malformed/unknown-skill parsing; that skills do **not** clamp |
| `test_observation.py` (9) | AC2: coordinates-not-prose object fields, both grippers, held-object consistency, one gripper per side, duplicate-id rejection, immutability, field-by-field round trip, strict parsing |
| `test_skill_result.py` (7) | AC2: status/reason/observation, the status↔code invariants, member typing, nested JSON round trip incl. enums and the embedded skill |
| `test_skill_serialization.py` (8) | AC2: no enum/dataclass/tuple leaks into `to_dict()`, `json.dumps`/`loads` fidelity, `to_json`/`from_json`, the strictness helpers |
| lint stubs (3) | flake8, pep257, copyright |
| `skill_api_fixtures.py`, `conftest.py` | shared builders + the `round_trip` assertion (dict round trip **and** JSON text round trip **and** dict-form stability) |

### `robot_backends` — 52 tests (58 after round 1)
| file | covers |
|---|---|
| `test_backend_interface.py` (6) | AC3: the three methods; `RobotBackend` and partial subclasses cannot be instantiated; a second stub backend satisfies the same seam with nothing extra (proves Sim/Real can drop in); `execute` is total over unknown skills; non-`Skill` input raises |
| `test_mock_skills.py` (12) | AC4, "per-skill round trip": initial state matches the seed world; navigate; move_gripper; grasp on **both** arms; implicit-side resolution; carrying a load while navigating; place; place with a named side; extend_column lifting arms + load; close_gripper (incl. that it picks nothing up); open_gripper (idle + drop); and that every result carries the skill and an observation equal to the next `get_observation()` |
| `test_mock_failures.py` (14) | AC5: the four required paths (navigate-unknown, grasp-missing, grasp-occupied, place-empty) plus ungraspable, both-hands-full, already-held, out-of-reach ×3, out-of-range, a seven-refusal run, and a custom world proving the rules come from the world, not hard-coded names. Every case asserts the full serialized world is unchanged and that the failed result reports that unchanged world |
| `test_mock_scenario.py` (6) | "Scenario/composition": `navigate_to(kitchen) → grasp(mug_1) → navigate_to(table) → place(...)` all `ok`, final state checked (robot at table, hands empty, mug at the requested pose, nothing else moved), step-by-step intermediate states, a JSON round trip at every step, `reset()` exactness, and determinism (two backends bit-identical; replay after reset identical) |
| `test_mock_world.py` (7) | seed-world validation, defensive copying/immutability, `RobotModel` geometry and validation, and that the default world contains `kitchen`/`table`/`mug_1` |
| `test_no_ros_runtime.py` (2) | AC6: a clean subprocess imports both packages and runs the whole loop with no ROS module imported; plus a source scan for any `import rclpy` |
| lint stubs (3) | flake8, pep257, copyright |

### Acceptance criteria → tests
| criterion | where |
|---|---|
| AC1 typed skills | `test_skills.py::test_every_documented_skill_is_registered`, `::test_argument_validation`, `::test_skill_round_trips_polymorphically` |
| AC2 Observation + SkillResult, dict/JSON | `test_observation.py`, `test_skill_result.py`, `test_skill_serialization.py` |
| AC3 `RobotBackend` interface | `test_backend_interface.py` |
| AC4 MockBackend mutates plausibly | `test_mock_skills.py` (all 12) |
| AC5 failure paths, state intact | `test_mock_failures.py` (all 14, via `assert_refused`) |
| AC6 no ROS graph | `test_no_ros_runtime.py` |
| required scenario test | `test_mock_scenario.py::test_navigate_grasp_navigate_place_scenario` |

## Commands run

```
pixi run build
  -> Summary: 7 packages finished [4.22s]

pixi run -- colcon test --packages-select robot_skills robot_backends
pixi run -- colcon test-result --verbose
  -> Summary: 107 tests, 0 errors, 0 failures, 0 skipped

# direct pytest against the source tree (no colcon install), from each
# package directory (the lint stubs lint the current directory, as colcon runs
# them):
export PYTHONPATH=<repo>/src/robot_skills:<repo>/src/robot_backends
cd src/robot_skills  && python -m pytest test -q   -> 55 passed
cd src/robot_backends && python -m pytest test -q  -> 52 passed
```

(Running those from the repo root instead makes the lint stubs walk the whole
workspace and report a pre-existing `E501` in `src/robot_description/setup.py`,
which is outside this feature's owned paths.)

**Known workspace issue, outside the owned paths (flagged, not fixed):**
plain `pixi run test` (whole workspace) still reports failures for the five
*empty* skeleton packages — `robot_brain`, `robot_bringup`, `robot_description`,
`robot_perception`, `robot_safety`. They contain no tests, so colcon's fallback
`python -m unittest` exits 5 ("NO TESTS RAN"). This is pre-existing and
unrelated to this feature; it needs either a test per package, the same
`extras_require` change, or a `--packages-select` in the pixi task. Because
`pixi run test` is `colcon test && colcon test-result`, those failures also stop
the results summary from printing.

## Round 1 fixes (post red-team)

Red team returned **READY TO MERGE, zero BLOCKs**; the manager called one
voluntary round for six in-scope items. All six done, no reasoned refusals.
Test count went 107 → **116** (58 per package).

**NOTE 2 — the rclpy scan had the hole it claimed to close.**
`test_no_ros_runtime.py` grepped the literal `'import rclpy'`, so a lazy
`from rclpy.node import Node`, an `import rclpy.qos`, or an
`importlib.import_module('rclpy')` all slipped through. Replaced with
`find_forbidden_imports()`, an AST walk over `Import`/`ImportFrom` nodes plus
`import_module`/`__import__` calls with a literal string argument, matching on
the *root module token* so a lookalike (`rclpy_stub_that_is_not_rclpy`) and the
word in prose do not fire. The detector is now itself tested
(`test_the_import_detector_catches_every_form_it_claims_to`) against a sample
containing all three forms and against a clean sample — otherwise the hole just
moves. The package scan also asserts it actually visited ≥10 files, so it cannot
pass by scanning nothing.

**NOTE 3 — `Observation` now enforces the invariant it documents.**
`Observation._check_held_objects_agree()` validates both directions: every
gripper's `held_object_id` must name a perceived object whose `held_by` is that
gripper's side, and every object with `held_by == s` must be the object gripper
`s` reports. The failure scenario was real: a `SimBackend` reading gripper state
from joints and attachment from a weld constraint could hand the brain a scene
that contradicts itself, and it would construct and round-trip happily. Tests:
`test_a_gripper_holding_an_object_the_scene_disagrees_about_is_rejected` (three
sub-cases: object absent, object says nobody, object says the other arm),
`test_an_object_claiming_a_holder_that_holds_nothing_is_rejected` (two
sub-cases), and `test_the_held_object_invariant_survives_a_round_trip` (a
doctored dict is caught on parse, not just on construction).

**NOTE 4 — implicit-side `Grasp` is now reach-aware.**
`_resolve_grasping_side()` replaces "first free arm, then check reach". With no
side named it returns the first side in `SIDE_ORDER` that is *both* free and
within reach; with a side named, behaviour is unchanged (that arm must be free
and able to reach). When no free arm can reach, it falls back to the preferred
free arm's existing `out_of_reach` message, and with no free arm at all to the
existing `gripper_occupied` message. Still fully deterministic — fixed order, no
distance tie-breaking. Refactor: `_require_reachable` now sits on a predicate
`_reach_offset`, and the two refusal helpers (`_require_free_gripper`,
`_refuse_both_grippers_occupied`) are split out. Tests:
`test_grasp_without_a_side_prefers_a_gripper_that_can_actually_reach` (object
0.62 m from the right shoulder, 0.98 m from the left),
`test_implicit_side_still_prefers_the_left_arm_when_both_can_reach` (preference
order unchanged where reach does not decide),
`test_naming_an_arm_that_cannot_reach_is_still_refused`, and
`test_implicit_side_reports_out_of_reach_when_no_free_arm_can_reach`.

**NOTE 5 — float comparisons state the real contract.**
`assert_pose_close()` (tolerance 1e-9 on position, exact on orientation) replaces
`==` wherever a *commanded* pose is compared to a *reported* one, and
`test_move_gripper_puts_that_gripper_at_the_requested_pose` is parametrized with
a `badly_scaled` case (`z=0.1` against a shoulder at `z=0.8`, where
`0.1 - 0.8 + 0.8 == 0.09999999999999987`) that the old bit-exact assertion would
have failed. **The implementation is unchanged** — offset-from-shoulder is what
makes the arm ride the base and the column. Pose-vs-pose comparisons where both
sides come from the *same* reconstruction (a held object sitting in its gripper)
stay exact, because there the equality is meaningful.

**NOTE 6 — wire-format policy written down.** `serialization.py`'s module
docstring now states the stance explicitly: these dicts are an internal,
versioned-together format; adding/renaming/removing a field is a coordinated
breaking change across the repo. Rationale recorded too — the likeliest producer
of a malformed dict is an LLM, and silently ignoring an invented key
(`"objct_id"`, `"height_cm"`) turns a typo into a wrong action instead of a clean
refusal. The documented migration if independently versioned peers ever appear is
a reserved, ignored `extensions` sub-object on `Observation`/`SkillResult` only,
keeping `Skill` (the type an LLM writes) strict — *not* relaxing `check_keys`
globally. No behaviour change.

**NOTE 11 typing nit.** `assert_refused(..., reason_contains: str | None)`.

Not done, by the manager's instruction (now GitHub issues / Sisyphus's call):
NOTE 1 (`decisions.md` entry — outside owned paths), NOTE 7, NOTE 8a
(workspace-level pytest config), NOTE 9 (brief-author confirmation on
close-gripper-empty), NOTE 10 (workspace suite red for the five empty skeleton
packages), and NOTE 11's other bullets.

Re-verified after the fixes: `pixi run build` green (7 packages);
`colcon test --packages-select robot_skills robot_backends` + `test-result
--verbose` → **116 tests, 0 errors, 0 failures, 0 skipped**; direct pytest per
package → 58 passed each; the no-ROS subprocess check still passes.

## Deliberately left out (per the brief's non-goals)

- No safety/clamp layer. The API is shaped so one can be added without redesign:
  skills are inert validated data (inspect/replace/reject before dispatch), and
  a wrapper can implement `RobotBackend` itself and delegate. `FailureCode.REJECTED`
  is reserved for it.
- No ROS 2 action/message wiring, no `rclpy` import. `Skill`/`Observation`/
  `SkillResult` already have the exact dict shape a goal/result message needs,
  and `SKILL_TYPES` is the registry a tag parser or action server will key off.
- No physics, no MuJoCo, no IK/MoveIt, no perception model, no LLM brain.
- No frame ids on poses: every pose is world/map frame for now. Adding
  `frame_id` later is an additive field on `Pose`; parsing is strict about
  unknown keys, so that change will be visible rather than silent.
- No velocity/force/duration fields on skills, no cancellation or feedback
  streaming — those belong with the ROS 2 action layer.
- No seed parameter on `MockBackend`: it contains no RNG at all. The
  `MockWorld` *is* the seed. If a future noise model needs randomness, it takes
  an explicit seed then.
