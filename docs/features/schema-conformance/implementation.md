# Implementation — conform skill/observation schema to D17–D19 (issue #33)

A conformance pass on the existing seam: no new package, no new runtime
dependency, no behaviour change to the Mock beyond what D19 requires.

## What changed

### D17 — failure-code ownership (`robot_skills/result.py`)

`FailureCode` now carries an explicit owner split, as *data* rather than as
behaviour:

- `BACKEND_REFUSAL_CODES` — the nine "can't be done" codes, refused up front
  before anything moves.
- `SAFETY_EVENT_CODES` — `REJECTED` today; the home of any future dynamic
  safety event (e-stop, collision abort, gripper over-force).
- `FailureCode.is_backend_refusal` / `.is_safety_event` — so a consumer branches
  on the code it already holds, without importing a set.

Both sets are exported from `robot_skills` so `robot_safety` (still a skeleton)
and `robot_backends` can classify a code programmatically. The class docstring
states D17's rule of thumb and why the distinction matters to the brain (a
refusal means "pick a different goal"; a safety event means "the motion was
stopped mid-way, re-observe").

Per manager ruling 2, `GRIPPER_EMPTY` is a **backend refusal**: placing with
nothing held is a precondition checked before motion, not an in-flight abort.

The two sets are enumerated by hand rather than one being derived as
"everything else". Deriving would make the partition trivially total but would
silently default a future safety code into the refusal bucket — the opposite of
what D17 asks for. Totality is enforced by a test instead
(`test_every_failure_code_belongs_to_exactly_one_owner`): union covers the enum,
intersection is empty, and every member answers exactly one of the two
predicates.

### D19 — the `grasped` flag (`robot_skills/observation.py`, `mock_backend.py`)

`GripperObservation` gains `grasped: bool = False` (additive, defaulted, so it
is non-breaking under D18).

Design choices, in order of how much they were left open:

- **`grasped` is independent of `held_object_id`** (ruling 3), not a derived
  property. `held_object_id` is a world-model fact (*which* object);
  `grasped` is a sensed one (*something* is in the jaws). A real gripper feels
  a load it cannot identify, so `grasped=True, held_object_id=None` is a
  legitimate state and the type must be able to express it.
- **The reverse combination is rejected in the constructor** (this was left to
  my judgement). Carrying a named object while reporting `grasped=False` is not
  a state any robot can be in, and the codebase already takes the position that
  redundant views must not be able to disagree — see
  `Observation._check_held_objects_agree`, whose docstring argues exactly this
  for the gripper/object pair. Making it a loud `ValueError` (translated to
  `SerializationError` at a parse boundary) means a backend that reads grip
  state and object attachment from two different sources fails at the seam
  instead of handing the brain a contradiction.
- **A payload without `grasped` infers it from the load** rather than
  defaulting to `False`. This is what keeps "additive optional field =
  non-breaking" honest for this particular field: a dict written before the
  field existed still *says* the gripper has a load (it names the object), so
  reading it as "empty jaws holding a mug" would both contradict the same dict
  and trip the constructor invariant. The dataclass default stays `False`
  because a bare `GripperObservation(...)` with no load has no load.
- **No `grasped` field on `SkillResult`.** D19's text names
  `SkillResult`/`Observation`, but the flag is per-gripper and a copy on the
  result would be a second source of truth able to disagree with the
  observation the result already carries. Instead `SkillResult.did_grasp(side)`
  reads through to `observation.robot.gripper(side).grasped` — the one-line
  closed-loop answer to "did I get it?" with no duplicated state.
- **The Mock derives `grasped` from what it holds.** It models no aperture and
  no contact force, so its only evidence of a grip is its own book-keeping; no
  new mutable state was added to `_MockGripper`. `_gripper_observation`'s
  docstring says so, and says that a backend with real sensing overrides it
  with the sensor.

Behaviour: as `context.md` §5 predicted, the Mock already returned success for
`close_gripper` on nothing and `open_gripper` on an open gripper — verified in
the code, not just the docs. The remaining work was the `grasped` reporting and
tests that *pin* the semantics so a future backend cannot regress them into
exceptions. Over-force while closing remains out of scope (D17 safety path).

### D18 — version stamp (`robot_skills/serialization.py` and the two types)

- `SCHEMA_VERSION = 1` and `SCHEMA_VERSION_KEY = 'schema_version'` live in
  `serialization.py` (the documented home of the wire-format policy, and a
  module both `observation.py` and `result.py` already import), re-exported from
  `robot_skills`.
- `Observation.to_dict()` and `SkillResult.to_dict()` stamp it. Per ruling 1 the
  stamp appears at **both** depths of a `SkillResult` dict — the stamp belongs
  to the type's wire form, not to a message envelope, so an observation lifted
  out of a result and published alone stays self-describing and
  `Observation.to_dict()` needs no nested special case. Both docstrings say this
  is intentional.
- Skills are **not** stamped: they are the type an LLM writes by hand, and the
  existing policy prose already argues for keeping `Skill` maximally strict and
  minimal.
- `check_schema_version()` implements ruling 4: absent means "current version"
  (which is what makes an added field non-breaking); present-but-different is a
  `SerializationError`, since D18 grants no multi-version support and no
  deprecation windows. A non-integer (or `bool`) stamp is likewise refused. Both
  `from_dict`s widen their `check_keys(optional=...)` to admit the key.
- The "Wire-format compatibility policy" section of `serialization.py` gained a
  "The version stamp (D18)" subsection covering producing, parsing, the compat
  rule and where it is enforced.

### D18 — golden-fixture guard (`src/robot_skills/test/`)

`golden/v1/<Type>.json` — 15 files, one canonical `to_dict()` per concrete
public serializable type, **generated from real `to_dict()` calls** by
`golden_fixtures.py` (never hand-typed; the module docstring says so, matching
the culture from #24). They are a frozen *historical* record of what version 1
looked like, not a snapshot to refresh.

How the guard works (`schema_drift()` in `golden_fixtures.py`, exercised by
`test_golden_schema.py`): the comparison is deliberately **asymmetric**.

- every golden key must still be present, with the same JSON type and the same
  value, recursively;
- keys present only in today's output are **not** reported.

That asymmetry *is* D18's compat rule in code: an added optional field passes at
the same version with no regeneration, while a drop, rename or retype fails —
and keeps failing — until the author bumps `SCHEMA_VERSION` and writes a
`golden/v2/` set, which is the same PR in which every binder gets updated. The
failure message says exactly that.

Why a golden fixture rather than more round-trip assertions: a field renamed in
`to_dict` *and* `from_dict` together still round-trips, and `check_keys` only
ever objects to keys that were **added**. This was verified end-to-end, not
argued: renaming `grasped` → `gripping` in both directions in `observation.py`
left `test_observation.py -k round_trip` green (3 passed) while four golden
tests failed with `<root>.robot.grippers[0].grasped: dropped or renamed`. The
mutation was then reverted.

The guard itself is unit-tested against synthetic mutations rather than trusted:
dropped key, renamed key, retyped scalar (`float` → `str`), retyped `bool` → `int`
(Python's `bool`/`int` subtyping is handled by `json_type_name`, which is also
asserted directly), object → scalar, array → object, changed value, shortened
list, multiple drifts reported at once, and — the other half of the rule — added
keys at three nesting depths *not* flagged.

Completeness: `public_serializable_types()` walks `JsonSerializable.__subclasses__()`
transitively, keeps classes defined in `robot_skills` that are neither abstract
(`Skill`) nor private (`_GripperSkill`), and a test asserts the discovered set
equals `GOLDEN_SAMPLES` **and** the set of `.json` files on disk, in both
directions. A new type on the seam therefore fails the suite until it has a
fixture; a fixture cannot outlive its type either.

Fixture discovery at runtime: paths resolve from `Path(__file__).parent`, so
they work under `colcon test` / `--symlink-install`. Confirmed by running the
real driver (`pixi run test`), not just bare pytest, and
`test_the_fixtures_are_reachable_and_json_under_the_test_runner` fails loudly if
the directory ever becomes invisible to the runner.

Samples are built inside `golden_fixtures.py` rather than reused from
`skill_api_fixtures.make_observation()` on purpose: a frozen fixture must not
move because a shared builder was tweaked for an unrelated test. Every optional
field is filled with a **non-null** value where the type allows one (e.g.
`SkillResult` uses a *failure*, so `reason` and `code` are pinned as strings) —
a `null` would round-trip through a retype unnoticed.

Regenerating, for a future dev:

```
python src/robot_skills/test/golden_fixtures.py          # write missing files
python src/robot_skills/test/golden_fixtures.py --force  # overwrite (rare)
```

Existing files are skipped unless `--force`: a fixture that can be silently
refreshed guards nothing. (The command needs the workspace on `PYTHONPATH`,
e.g. `pixi run bash -c 'source install/setup.bash && python …'`.)

## Tests

New/extended, all exercising acceptance criteria rather than restating code:

- `test_failure_codes.py` (5 tests) — partition is total and disjoint, the
  classification itself is pinned, `GRIPPER_EMPTY`'s bucket is justified by a
  named test, sets are immutable.
- `test_mock_failures.py::test_every_mock_refusal_is_owned_by_the_backend_not_the_safety_layer`
  — every refusal the Mock can produce classifies as a backend refusal, and the
  test asserts it covers `BACKEND_REFUSAL_CODES` (minus `UNSUPPORTED_SKILL`,
  which `test_backend_interface.py` covers) so a new code cannot go unexercised.
- `test_observation.py` — `grasped` vs `is_holding` independence, the rejected
  impossible direction, inference when the key is absent, type validation,
  round trip of the unidentified-load case.
- `test_mock_skills.py` — closing on nothing succeeds with `grasped=False`
  (twice, idempotently), closing on a held object keeps reporting the grasp,
  opening an already-open gripper is an idempotent success, opening clears the
  flag, grasping sets it on both the observation and `SkillResult.did_grasp()`.
- `test_skill_serialization.py` — the stamp is present at both depths and absent
  from skills, survives round trips, is optional on parse, and a foreign or
  non-integer version is refused (including one hidden in a nested observation).
- `test_golden_schema.py` (23 tests) — as described above.

Command output (this worktree, `pixi` env available):

- `pixi run build` → `Summary: 7 packages finished`.
- `pixi run test` → `Summary: 236 tests, 0 errors, 0 failures, 0 skipped`,
  `AUDIT PASSED`, `All stages passed`. (Baseline before this work: 198 tests.)
  Per package: `robot_skills` 59 → 93, `robot_backends` 58 → 62; everything else
  unchanged. Linters (`ament_flake8`, `ament_pep257`, `ament_copyright`) are part
  of those counts and are green, including the new files.

## Deliberately left out

- **No aperture/contact-force detail** on `GripperObservation`, though D19
  mentions it parenthetically: the Mock has no such state to report, and an
  unpopulated field on the seam would be speculative generality. When a backend
  with force sensing arrives, adding `aperture_m`/`contact_force_n` as an
  **optional** field is non-breaking — no version bump, no golden regeneration
  (`test_the_guard_allows_an_added_optional_field` literally uses those two
  names). Adding one as *required* is breaking and now fails the reader half of
  the guard; see the round-1 fixes below.
- **No `robot_safety` code.** It is out of bounds for this issue and still a
  skeleton; the classifier lives in `robot_skills.result` precisely so both it
  and `robot_backends` can import it when the time comes. Nothing in the Mock
  emits a safety event today.
- **No multi-version parsing or migration shims** — D18 forbids them.
- **No `extensions` escape hatch** — the existing policy prose reserves that for
  the day independently versioned peers become real; that day has not come.

## Red-team round 1 — fixes

Both BLOCKs fixed; four NOTEs promoted by the manager applied; NOTE 3 waived by
the manager (type discovery relies on `__init__` re-exporting every module —
true, and the adjacent count assertion is a deliberate tripwire).

### BLOCK 1 — the guard was producer-only

Correct finding, and the more interesting of the two: comparing `to_dict()` to
the fixture proved only that we still *write* v1. `test_the_frozen_fixture_still_parses_into_an_equal_object`
adds the reader half — `type(sample).from_dict(load_golden(name)) == sample` for
all 15 types.

Verified against the red-team's exact scenario rather than assumed: adding an
`aperture_m` that `to_dict()` emits *and* `check_keys(required=...)` demands
left the writer half green for all 15 types and `test_observation.py -k round_trip`
green (3 passed), and failed **only** the new reader half, on the four types
that embed a gripper (`GripperObservation`, `RobotState`, `Observation`,
`SkillResult`). Mutation reverted.

**Did it make an existing test redundant?** No, and nothing was deleted.
`test_a_payload_without_grasped_infers_it_from_the_load` (`test_observation.py`)
looks adjacent but tests the opposite direction: it *removes* a key from the
dict to prove a pre-`grasped` payload still parses meaningfully. The v1 fixtures
all contain `grasped`, so the golden reader test never exercises that path. The
`assert_round_trip` helper also stays: it additionally covers `to_json`/
`from_json`, the JSON-text round trip and dict stability, none of which the
golden test touches.

### BLOCK 2 — the new invariant was not enumerated at the parse boundary

Fair: the repo treats "`from_dict` raises **only** `SerializationError`" as
load-bearing, with one test enumerating every constructor invariant reachable
from a parse, and the `held_object_id`-without-`grasped` case was missing from
it. Added there. The assertion has teeth because `SerializationError` subclasses
`ValueError`, not the reverse — a leaked raw `ValueError` fails
`pytest.raises(SerializationError)`.

### Promoted NOTEs

- **NOTE 1** — `SkillResult.grasped(side)` → `did_grasp(side)`. The collision
  with the bool field made `if result.grasped:` always true at a call site that
  reads as obviously correct. Renamed at the definition, in the six test call
  sites and in this document; the reason is now in the method docstring so it is
  not renamed back.
- **NOTE 2** — `check_schema_version` now runs *before* `check_keys` in both
  `from_dict`s, so a future payload is diagnosed as a version mismatch rather
  than as an unknown key it happens to also carry. Pinned by
  `test_a_foreign_version_is_diagnosed_before_the_keys_it_explains`, which sends
  a v2 payload carrying a v2-only key.
- **NOTE 4** — `load_golden` now turns a missing fixture into the instruction
  the author needs (how to freeze a new version; and, at an unchanged version,
  "restore it from git — a frozen record is not something to regenerate")
  instead of a bare `FileNotFoundError`.
- **NOTE 5** — documentation only, as agreed. The `GripperObservation` docstring
  now states the contract the invariant imposes on any backend with real
  sensing: on a detected drop, clear `held_object_id` and the matching
  `SceneObject.held_by` in the *same* update that reports `grasped=False`,
  because the constructor raises inside `get_observation()`, where there is no
  parse boundary to translate it.
- **NOTE 6** — `schema_drift`'s docstring and the writer test now record that
  values are frozen as well as shapes, deliberately, with examples of the drift
  that catches (a changed unit, a renamed enum value).

### Post-fix suite

`pixi run test` → `Summary: 252 tests, 0 errors, 0 failures, 0 skipped`,
`AUDIT PASSED`, `All stages passed`. Per package: `robot_skills` 93 → 109,
`robot_backends` 62 (unchanged), everything else unchanged.

## Notes for the manager

- Nothing was touched outside the owned paths. The two package `README.md`s were
  updated (`robot_skills`, `robot_backends`) since both describe the seam this
  issue changes; both are inside their own packages.
- No escalations, no design forks. The three questions `context.md` raised were
  answered by the manager rulings in `status.md` and implemented as written; the
  two sub-decisions those rulings left to me (the constructor invariant on
  `grasped`, and not duplicating the flag onto `SkillResult`) are documented
  above with their rationale.
- **Surviving NOTE after round 1:** NOTE 3 only — `public_serializable_types()`
  discovers a type only if its module was imported, so the completeness test
  relies on `robot_skills/__init__.py` re-exporting every module. The manager
  waived it as not worth the churn; it is worth a follow-up if the package ever
  grows a module that is not re-exported. No other red-team finding is
  outstanding.
