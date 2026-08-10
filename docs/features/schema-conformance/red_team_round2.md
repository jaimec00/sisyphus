# Red-team round 2 — schema-conformance (issue #33)

Scope: the four round-1 fix commits (`22c065d`, `d729674`, `e4c9ae4`,
`7872c2a`) on top of `832ea60`. Read-only pass. Round-1 findings and the
round-1 record live in `red_team.md`; this file only judges the delta.

**Verdict: BLOCK 1 closed. BLOCK 2 closed. No new BLOCK introduced.**
One new follow-up NOTE, below, written to stand alone.

---

## BLOCK 1 — closed

`src/robot_skills/test/test_golden_schema.py:62-83`.

The reader half is real, not decorative. Verified rather than assumed:

- **Parametrization is complete.** `@pytest.mark.parametrize('name',
  sorted(GOLDEN_SAMPLES))` — the same 15-way parametrization as the writer half,
  no type quietly excluded, and the completeness test
  (`test_golden_schema.py:86-98`) still forces `GOLDEN_SAMPLES` to equal both the
  discovered type set and the files on disk. `robot_skills` 93 → 109 tests
  (+15 reader cases +1 ordering test) matches exactly what the diff adds, so the
  cases are being collected, not skipped.
- **It catches the class I identified, not just the one variant.** The
  required-key promotion fails it (the fixture lacks the key →
  `missing required key(s)` → error). So does the mirror case I did *not* name
  in round 1: narrowing `check_keys(optional=...)` — the fixture still *carries*
  the key, so parsing it raises `unknown key(s)`. Both are invisible from the
  writer side, both now go red. The implementer's reproduction claim is
  consistent with the code: an `aperture_m` on `GripperObservation` would fail
  the reader half on exactly the four gripper-embedding types
  (`GripperObservation`, `RobotState`, `Observation`, `SkillResult`), because
  those are the only fixtures whose JSON reaches that parser.
- **No vacuous pass.** `load_golden` (`golden_fixtures.py:144-165`) still
  *raises* on a missing file; the new `try/except` only replaces the message and
  re-raises `FileNotFoundError ... from exc`. It catches nothing else —
  `PermissionError`, `IsADirectoryError` and `json.JSONDecodeError` still
  propagate — so a fixture that is absent, unreadable or corrupt errors both
  parametrized tests rather than skipping them. The round-1 vacuity hole stays
  closed.
- **The redundancy claim is true, not convenient.** I checked both halves.
  `test_a_payload_without_grasped_infers_it_from_the_load`
  (`test_observation.py:105-120`) deletes `grasped` from a payload; every v1
  fixture *contains* `grasped` (`golden/v1/GripperObservation.json:2` and the
  nested copies), so the reader test never walks the inference branch — the two
  tests exercise disjoint paths. `assert_round_trip`
  (`skill_api_fixtures.py:45-63`) additionally covers `to_json`/`from_json` and
  dict stability across JSON text, which the golden test does not touch.
  Nothing was deleted.

Two limits of the `== sample` oracle I probed, both **covered elsewhere** and
neither blocking:

- A parser that stops *reading* `grasped` and always infers it from
  `held_object_id` would still satisfy the reader half, because every v1 fixture
  has `grasped ⟺ held_object_id`. It is caught by
  `test_observation.py:240`, which round-trips a `grasped=True,
  held_object_id=None` gripper — the divergent case the inference cannot fake.
- Likewise a parser that stops reading `SceneObject.graspable`: the fixture's
  value (`true`) is the dataclass default, so the reader half would not notice.
  Caught by `test_observation.py:239`, which round-trips `counter_1`
  (`graspable=False`). This is the one place where a fixture value coincides with
  a default, and it is the follow-up NOTE below.

## BLOCK 2 — closed

`src/robot_skills/test/test_skill_serialization.py:99-104`.

- **It reaches the intended branch.** The payload carries an explicit
  `'grasped': False` alongside `'held_object_id': 'mug_1'`, so
  `GripperObservation.from_dict` takes the explicit-key branch
  (`observation.py:226-227`) rather than the inference default, `check_keys`
  passes (both keys are optional), `get_optional_str`/`get_bool` both succeed,
  and the only thing left to fail is `__post_init__`
  (`observation.py:188-192`) inside `with parse_errors(context)`. Nothing raises
  earlier.
- **The implementer's reasoning about teeth holds against the actual class
  definitions.** `class SerializationError(ValueError)`
  (`serialization.py:122`) — the subclassing runs one way only, so a raw
  `ValueError` is *not* an instance of `SerializationError` and
  `pytest.raises(SerializationError)` does not swallow it. Under the refactor I
  described in round 1 — `cls(...)` moved outside `with parse_errors(...)`, the
  shape already present at `observation.py:301-311` for `RobotState`'s grippers
  — the `ValueError` would escape the `raises` block and fail the test. The
  `match='while grasped=False'` string is regex-safe and matches
  `observation.py:191`.
- The intermediate `held = {**held, 'held_object_id': 'mug_1', 'grasped': True}`
  at line 102 is immediately overridden by the `grasped: False` in the call; it
  is harmless (it keeps the base dict self-consistent) and does not weaken the
  assertion.

## NOTE fixes — verified

- **NOTE 2 (ordering).** `check_schema_version` now precedes `check_keys` in
  both parsers (`observation.py:426-435`, `result.py:249-256`), still after
  `ensure_mapping`, so the `in` test cannot hit a non-mapping. Unknown-key
  rejection at the *current* version is unweakened: an absent stamp returns
  immediately and a current stamp passes, leaving `check_keys` to raise — still
  pinned by `test_observation.py:259-260` and `test_skill_result.py:115-116`,
  whose payloads carry `schema_version: 1`. Absent = current
  (`test_skill_serialization.py:140-154`), foreign and wrong-type both
  `SerializationError` (`157-173`). The new
  `test_a_foreign_version_is_diagnosed_before_the_keys_it_explains`
  (`176-197`) has teeth: its payloads carry a foreign version *and* an unknown
  key, so it would have failed under the old order with
  `unknown key(s): ambient_temperature_c`.
- **NOTE 1 (rename).** `did_grasp` at the definition
  (`result.py:186-199`) with the reason recorded in the docstring so it is not
  renamed back. A repo-wide grep for `.grasped(`/`result.grasped` finds no stale
  reference outside the round-1 report and the changelog entry in
  `implementation.md` that names the old symbol on purpose. All six call sites
  moved with their assertions intact (`test_mock_skills.py:99, 285, 291, 301,
  303, 315` — same lines, same expected values as before the rename); nothing was
  dropped or weakened. Both package READMEs refer to `grasped` as the
  `GripperObservation` field, which is still accurate.
- **NOTE 5 (documentation).** `observation.py:149-158` states a concrete backend
  contract — on a detected drop, clear `held_object_id` *and* the matching
  `SceneObject.held_by` in the same update that reports `grasped=False`, place
  the object where it is believed to have fallen, never build an observation from
  a half-updated world model — and correctly names the consequence (a
  `ValueError` from inside `get_observation()`, with no parse boundary to
  translate it). It matches the code, including
  `Observation._check_held_objects_agree`, which is what would otherwise fire
  second.
- **NOTES 4 and 6.** `load_golden`'s message distinguishes "you just bumped,
  freeze it" from "a fixture vanished at an unchanged version — restore it from
  git", which is the right split. `schema_drift`'s docstring
  (`golden_fixtures.py:222-226`) and the writer test's docstring record that
  values are frozen as well as shapes. Documentation only; behaviour unchanged
  (the `schema_drift` body is byte-identical to round 1).

## New risk introduced by these commits

None found. The three behavioural changes are the parser reorder (verified
above to preserve every existing strictness assertion), the method rename (no
stale references, no lost assertions), and `load_golden`'s error message (still
raises, catches only `FileNotFoundError`). Everything else is test additions and
docstrings. Scope remains confined to `src/robot_skills/**`,
`src/robot_backends/**` and `docs/features/schema-conformance/**`.

Round-1 NOTE 3 (type discovery relies on `robot_skills/__init__.py` re-exporting
every module) remains the only round-1 survivor; the manager waived it and
`implementation.md:304-309` already records it for follow-up, so I am not
re-litigating it here.

---

## Surviving NOTES — follow-up

### Golden fixtures should not give an optional field its dataclass default value

When the next `SCHEMA_VERSION` is frozen, every optional field in
`GOLDEN_SAMPLES` should carry a value that differs from the dataclass default.
The golden guard's reader half asserts `from_dict(fixture) == sample`; where a
fixture's value happens to equal the field's default, a parser that stops
reading that key entirely still produces an equal object, so neither half of the
guard notices. Today exactly one field is in that position —
`SceneObject.graspable` is `true` in `golden/v1/SceneObject.json` and defaults to
`True` — and the gap is covered incidentally by
`src/robot_skills/test/test_observation.py:239`, which round-trips a
`graspable=False` object; the point of the follow-up is that the coverage is
incidental rather than structural, and that the v1 files must not be edited to
fix it (they are a frozen historical record by design, per
`golden_fixtures.py:7-33`). The cheapest durable form is a rule applied when
`golden/v2/` is generated, optionally enforced by a test asserting no sample's
optional field equals its `dataclasses.fields()` default.

Affected paths: `src/robot_skills/test/golden_fixtures.py` (`GOLDEN_SAMPLES`),
`src/robot_skills/test/golden/v1/SceneObject.json`,
`src/robot_skills/test/test_golden_schema.py`.
