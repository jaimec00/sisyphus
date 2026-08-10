# Red-team — conform skill/observation schema to D17–D19 (issue #33), round 1

Reviewed: branch diff against `origin/main` @ `0a66386`, the issue's five
acceptance criteria, the manager rulings in `status.md`, and CLAUDE.md's
architectural invariants. Read-only pass; no source or test was modified.

**Verdict:** the implementation is substantially correct and the claims in
`implementation.md` hold up against the code (spot-checked, not taken on
faith). Two BLOCKs, both narrow and cheap — one is a real blind spot in the
golden guard itself, the other a missing regression test on a newly introduced
invariant. Everything else is NOTE.

## Verification of the acceptance criteria

| # | Criterion | Status |
|---|---|---|
| 1 | `SCHEMA_VERSION` exported + in the wire form; golden test that demonstrably fails on drop/retype | met (with the gap in BLOCK 1) |
| 2 | `grasped` on the gripper path; idempotent gripper successes | met |
| 3 | `FailureCode` owner split, programmatically classifiable | met |
| 4 | Round-trip invariant holds for every type | met |
| 5 | Full suite green, no unrelated churn | scope clean (see "Scope") |

Things I attacked and could **not** break:

- `schema_drift` (`src/robot_skills/test/golden_fixtures.py:194-225`) catches a
  dropped key, a renamed key (drop is reported even though the added name is
  tolerated — proven at `test_golden_schema.py:88-90`), a retyped scalar, an
  object collapsed to a scalar, an array collapsed to an object, a changed
  value, and a shortened list, at every nesting depth including inside list
  elements and inside `SkillResult.observation`
  (`test_golden_schema.py:92-96`, `151-153`).
- `bool`/`int` subtyping is handled deliberately (`json_type_name`,
  `golden_fixtures.py:170-191`) and asserted directly
  (`test_golden_schema.py:110-112`). `int` vs `float` is likewise distinguished
  before the value comparison, so `1 == 1.0` cannot mask a retype.
- The guard cannot pass vacuously: `load_golden` raises `FileNotFoundError` on a
  missing fixture, the parametrization has 15 real cases, and completeness is
  asserted in both directions against `__subclasses__()` discovery *and* the
  files on disk (`test_golden_schema.py:51-63`).
- The fixtures are content-correct, not placeholders: every optional field is
  non-null in the fixture that owns the type (`SceneObject.held_by = "left"`,
  `GripperObservation.held_object_id = "mug_1"`, `RobotState.location`,
  `SkillResult.reason`/`code`), floats are non-integral where it matters, and
  there are no empty containers whose element type would be uninferable.
- Version-mismatch parsing raises `SerializationError` (not `ValueError`/
  `KeyError`), rejects a string `"1"` and a `bool`, and is enforced inside a
  nested observation too (`test_skill_serialization.py:149-165`). `check_keys`
  still rejects genuinely unknown keys (`test_skill_result.py:115-116`,
  `test_observation.py:259-260`).
- Mock `grasped` is consistent across grasp / place / open / close / close-on-air
  / already-open / drop-on-open / both grippers, and the D19 "idempotent
  success" semantics are pinned by tests that assert `status is OK` **and**
  `code is None` — reintroducing a `GRIPPER_EMPTY` refusal on those paths would
  fail (`test_mock_skills.py:272-338`).
- The `FailureCode` partition is enumerated by hand and tested for totality and
  disjointness (`test_failure_codes.py:17-25`, `44-53`), and
  `test_mock_failures.py:236-238` asserts the mock exercises every refusal code,
  so a new code cannot go unexercised.

---

## BLOCK

### BLOCK 1 — the golden guard is producer-only: it never checks that the frozen v1 wire form still *parses*

`src/robot_skills/test/test_golden_schema.py:38-48` (and the asymmetry it
relies on, `golden_fixtures.py:194-225`).

The guard compares `sample.to_dict()` against the fixture. It proves "we still
*write* v1". Nothing anywhere proves "we can still *read* v1". Combined with
the deliberate added-key tolerance, that leaves a breaking change fully
invisible at the same `SCHEMA_VERSION`.

Concrete failure scenario. A later PR adds force sensing:

```python
# observation.py
aperture_m: float          # new field, emitted by to_dict()
check_keys(data, required=('side', 'state', 'pose', 'aperture_m'), ...)
```

- `assert_round_trip` passes — `to_dict()` emits the key, so `from_dict()` finds it.
- `schema_drift` passes — every golden key is still present with the same type
  and value; `aperture_m` is an *added* key, which the guard tolerates by design.
- Yet every v1 payload in existence — a logged observation, a stored brain
  memory, the checked-in `golden/v1/GripperObservation.json` itself — now fails
  to parse with `missing required key(s): aperture_m`, at version 1, with no
  bump and no red test. That is exactly the class of silent wire break D18's
  enforcement clause exists to prevent.

The same blind spot covers any consumer-side drift that `to_dict()` does not
mirror (e.g. narrowing `check_keys(optional=...)`).

Note the implementer clearly saw this risk for one field —
`test_a_payload_without_grasped_infers_it_from_the_load`
(`test_observation.py:105-120`) is precisely a "can we still read the older
wire form" test — but it was hand-written for `grasped` only rather than
generalised into the guard, where it costs one line.

**Fix direction.** In the existing parametrized test, add the reader half:

```python
sample = GOLDEN_SAMPLES[name]
assert type(sample).from_dict(load_golden(name)) == sample, (
    f'golden/v{SCHEMA_VERSION}/{name}.json no longer parses ...')
```

It holds today for all 15 types (each golden is a JSON round trip of
`sample.to_dict()`, and `from_dict(to_dict(x)) == x` is the tested contract), it
closes the added-required-key hole, and as a bonus it gives acceptance criterion
4 ("round-trip holds for **every** type") a single enumerated home instead of
relying on per-module coverage.

### BLOCK 2 — the new `grasped` invariant is untested at the parse boundary

`src/robot_skills/robot_skills/observation.py:177-181` (invariant),
`src/robot_skills/test/test_observation.py:93-102` (only test),
`src/robot_skills/test/test_skill_serialization.py:77-109` (the invariant test
that should have gained a case).

This PR adds a new constructor `ValueError` (`held_object_id` set while
`grasped=False`). The repo treats "`from_dict` raises **only**
`SerializationError`" as a load-bearing, explicitly documented invariant
(`serialization.py:22-26`), with a dedicated test that enumerates every
constructor invariant reachable from a parse — duplicate `object_id`, one
gripper per side, status/code agreement, non-finite geometry, blank identifier.
The new invariant is the one that is *not* enumerated there, and it is
reachable: `GripperObservation.from_dict({..., 'held_object_id': 'mug_1',
'grasped': False})` takes the explicit-key branch (`observation.py:215-216`) and
trips the invariant.

The code is correct today (the constructor call is inside `parse_errors`,
`observation.py:217`). The failure this enables is a *future* one: a refactor
that moves the `cls(...)` call out of the `with parse_errors(context)` block —
the same shape as the existing `grippers` construction in
`RobotState.from_dict` (`observation.py:292-295`), which sits outside its
`parse_errors` block — would leak a raw `ValueError` through the transport
boundary, past every `except SerializationError` handler, and no test would go
red. The whole point of that invariant test is to make that impossible.

**Fix direction.** One case in `test_from_dict_raises_only_serialization_error`:

```python
with pytest.raises(SerializationError, match='while grasped=False'):
    GripperObservation.from_dict({**held, 'grasped': False})
```

---

## NOTE (follow-ups, not blockers)

1. **`SkillResult.grasped(side)` is a method whose name collides with a bool
   field** — `result.py:186-194` vs `GripperObservation.grasped`
   (`observation.py:154`). `if result.grasped:` is always `True` (a bound method
   is truthy), and it reads exactly like the field it mirrors; `succeeded` next
   door is a property, which strengthens the wrong expectation. Consider
   `did_grasp(side)` / `grasped_with(side)`. Related: passing a non-`Side`
   surfaces as `KeyError('no gripper observation for side ...')` from
   `RobotState.gripper` (`observation.py:265-270`) rather than a typed error —
   untested, low impact given the `RobotState` one-per-side invariant.

2. **Version check runs after key check** — `observation.py:415-421`,
   `result.py:244-250`. A genuine v2 payload that carries a v2-only key reports
   `unknown key(s): <new_field>` instead of `unsupported schema version 2`,
   sending the reader hunting an LLM typo instead of a version mismatch. Calling
   `check_schema_version` *before* `check_keys` makes the diagnosis match the
   cause. (Both are `SerializationError`, so behaviour is unaffected.)

3. **Type discovery only sees imported modules** —
   `golden_fixtures.py:149-167`. `JsonSerializable.__subclasses__()` finds a new
   type only if its module was imported; the completeness test therefore relies
   entirely on `robot_skills/__init__.py` re-exporting everything. A future
   serializable type in a module not re-exported escapes both the completeness
   test and the guard, silently. The transitive walk and the abstract/private
   filters are correct; consider importing via `pkgutil.iter_modules` over the
   package (or at least stating the reliance in the docstring). The adjacent
   `assert len(discovered) == 15` (`test_golden_schema.py:58`) is redundant with
   the set equality above it and will need editing on every legitimate type
   addition.

4. **Bumping `SCHEMA_VERSION` without regenerating fails unhelpfully** —
   `golden_fixtures.py:144-146`. `load_golden` raises a bare
   `FileNotFoundError: .../golden/v2/CloseGripper.json`; the carefully written
   guidance in the assertion message (`test_golden_schema.py:45-48`) is never
   reached in that path. Raising from `load_golden` with "run `python
   src/robot_skills/test/golden_fixtures.py` after a deliberate bump" would make
   the failure self-explanatory. (The other two tests do print something
   actionable, so this is polish.)

5. **`held_object_id ⇒ grasped` makes a detected drop unrepresentable** —
   `observation.py:171-181`. Ruling 3 says `grasped` is a *sensed* fact that
   diverges from the world-model fact on a real robot; the invariant then
   forbids one direction of divergence — the detected-drop case, which is
   arguably the most safety-relevant thing the flag exists to report. The call
   is defensible ("carrying X while not gripping it" is an incoherent *report*,
   and the type already refuses contradictory views in
   `_check_held_objects_agree`), and D19 does not require the state, so this is
   not a blocker. But note the failure mode is a hard `ValueError` raised from
   inside a backend's `get_observation()` — no parse boundary to translate it —
   so the contract a real/sim backend must follow ("on a detected drop, clear
   `held_object_id` in the same update and report `grasped=False`") should be
   stated in the `GripperObservation` docstring, not left implicit.

6. **The guard pins values as well as shapes** (`golden_fixtures.py:223-224`),
   which is deliberate and documented, but means an unrelated numeric change
   (e.g. a different quaternion normalisation) surfaces as a schema failure. The
   failure message names it "value changed", which is clear enough; recording in
   the module docstring that a value change is *also* a deliberate freeze would
   save a future dev one minute of confusion.

## Scope

Clean. Changes are confined to `src/robot_skills/**`, `src/robot_backends/**`
and `docs/features/schema-conformance/**`. No touch to `src/robot_safety/**`,
`docs/design/**`, `.github/**`, `pixi.toml` or `scripts/**`. The two package
`README.md` updates are inside their own packages and describe the seam this
issue changed; both read accurately against the code. No drive-by refactors or
reformatting spotted in the reviewed files.

## Architectural invariants

- **1 (skill API is the seam):** respected — the stamp, the `grasped` field and
  the ownership split all live on the seam types; no joint-level leakage.
- **2 (Mock first):** respected — the Mock is the only backend touched and it
  reports `grasped` from its own book-keeping, with the sensing override
  documented for future backends (`mock_backend.py:340-358`).
- **3 (safety layer never bypassed):** respected — the D17 split is data plus
  two predicates, no behaviour change, `REJECTED` correctly the sole safety
  member with no call site yet.
- **4 (structured scene JSON):** respected — no prose regression; `grasped` is a
  typed bool on the wire.
- **5 (reuse):** n/a.
