# Red team: robot_world — two correctness gaps (#58)

Round 1. Read-only pass over `e01d68c`, `a777eef`, `6a11e15` against the issue's
two acceptance criteria, `context.md`, rulings R1–R10, and CLAUDE.md's
architectural invariants.

## Verdict

**BLOCK findings: none.** I tried to disprove R1, R3, R4, R7 and R8 as
falsifiable claims and could not construct a counterexample through any
supported path. The invariant is genuinely defined once, genuinely
check-then-mutate, and genuinely unreachable-around; the `_seed` fix is correct
and behaviour-preserving for every existing caller. The tests are falsifiable
(the implementer's revert-and-watch-it-fail checks match what I traced by hand)
and cover R9.1–R9.8 plus the R4 pin.

Six **NOTE**s follow, one of which (N1) says a *docstring* in the shipped code
asserts something that is factually not what happens — worth fixing in this PR
if the manager wants it, but not a correctness or safety defect.

---

## BLOCK findings

*(none)*

---

## NOTE findings

### N1 — `FileWorldStore.seed_document()`'s docstring predicts the wrong failure, and R8's stated rationale rests on it

`src/robot_world/robot_world/store.py:366-378`, specifically the claim at
lines 373-376:

> The inherited ``_seed`` is kept honest anyway (the constructor is handed the
> seed separately from the live document), so collapsing this override into
> ``return self._seed`` would no longer restore a *live* scene as if it were the
> seed; it would just quietly stop following the file.

That is not what would happen. `FileWorldStore.__init__` calls the **virtual**
`self.seed_document()` at `store.py:348` — *before* `super().__init__(...)` at
`store.py:354` ever assigns `self._seed`:

```python
self._refuse_seeding_from_the_live_file()
seed = self.seed_document()          # line 348 — dispatches to the override
...
super().__init__(document, seed=seed)  # line 354 — this is where _seed appears
```

Concrete scenario: a future maintainer "optimizes away the re-read" exactly as
R8 fears, replacing the override body with `return self._seed`. The very next
`FileWorldStore(live, seed_path=seed)` raises
`AttributeError: 'FileWorldStore' object has no attribute '_seed'` at line 348 —
construction dies, every `test_file_store.py` test fails at once. It does not
"quietly stop following the file"; it stops working loudly and immediately.

Why it matters: the outcome is *fine* (loud beats quiet), but the docstring is
the discoverability artefact R8 asked for, and it currently teaches a
maintainer a failure mode that cannot occur while hiding the one that can. It
also means the "defense in depth" story the manager wanted told is told
incorrectly — the honest `_seed` does not rescue a collapsed override, it makes
the collapse impossible-to-miss for a *different* reason.

Fix direction: reword to state what is actually true — the constructor consumes
`seed_document()` before `_seed` exists, so this override cannot be collapsed
into an attribute read without restructuring `__init__`; `_seed` is kept honest
so that `WorldStore.seed_document()` (and any future reader of the attribute,
e.g. a subclass or the query service) answers ground truth rather than "whatever
the live file said at startup". No code change needed.

### N2 — criterion 2's entire discriminating power is one assertion in one test

`src/robot_world/test/test_file_store.py:107`:

```python
assert WorldStore.seed_document(second) == document
```

I traced every other new/changed assertion and this is the **only** one that
fails if `store.py:354` is reverted to `super().__init__(document)`:

* `test_mutating_the_working_scene_never_changes_what_reset_restores`
  (`test_file_store.py:116-133`) uses a **single** store over a **fresh** live
  file, so `document is seed` already and lines 128-130 pass either way. The
  implementer says as much in `implementation.md`; I confirmed it by reading the
  fixture (`conftest.py:35-40` writes the seed, `live` does not exist yet, so
  `FileWorldStore.__init__` takes the `document = seed` branch at
  `store.py:351-353`).
* `test_the_seed_is_not_disturbed_by_anything_the_store_does` and
  `test_a_reopened_store_resets_to_the_seed_not_to_the_scene_it_opened`'s other
  assertions all route through the **overridden** `seed_document()`, which
  re-reads disk and was already correct before this branch.
* `test_a_store_can_be_told_its_seed_separately_from_its_scene`
  (`test_store.py:228-247`) fails on a revert too, but only with `TypeError:
  unexpected keyword argument 'seed'` — it pins the *new API*, not the
  *`FileWorldStore` wiring* that is the actual bug.

So deleting one line (107) silently un-pins acceptance criterion 2. That is
thin, not wrong.

Fix direction (cheap): in
`test_mutating_the_working_scene_never_changes_what_reset_restores`, reopen a
second store over the now-drifted live file and assert
`WorldStore.seed_document(reopened) == document` there too — two independent
tests then have to be edited to lose the regression pin. Alternatively assert
`WorldStore.seed_document(second) != drifted` alongside line 107 so the
assertion states both halves of the claim.

### N3 — `duplicate_hold_sides`'s documented ordering guarantee is not actually tested

`src/robot_world/robot_world/document.py:184-185` promises the result is in
`Side` **declaration** order ("so a message built from it is stable"), and
`store.py`/Layer A's message formatting depends on it.

`src/robot_world/test/test_document.py:187-192` is the only test of that
promise:

```python
assert duplicate_hold_sides(
    (WorldObject('a', ..., held_by=Side.LEFT),
     WorldObject('b', ..., held_by=Side.RIGHT),
     WorldObject('c', ..., held_by=Side.RIGHT),
     WorldObject('d', ..., held_by=Side.LEFT))
) == [Side.LEFT, Side.RIGHT]
```

First-appearance order in that input is also `LEFT, RIGHT`, so the assertion
cannot distinguish "iterates `Side`" from "iterates the objects and dedupes".
An implementation that returned first-appearance order would pass.

Fix direction: use an input whose first-appearance order is the reverse —
`held_by=RIGHT, RIGHT, LEFT, LEFT` — still expecting `[Side.LEFT, Side.RIGHT]`.
One-line change, and it is exactly the property that stops the two-side error
message from flapping.

### N4 — a string `side` bypasses the no-op short circuit and costs a redundant disk write

`src/robot_world/robot_world/store.py:189`:

```python
if item.held_by == side:
    return
```

`Side` is a plain `Enum` (`robot_skills/skills.py:73-78`, no `str` mixin), so
`Side.LEFT == 'left'` is `False`. `WorldObject.__post_init__` *does* accept the
string form (`as_optional_enum`, pinned by `test_document.py:220`), so:

```python
store.set_held_by('cube_1', Side.LEFT)
store.set_held_by('cube_1', 'left')      # identical fact
```

falls through the short circuit, builds an identical `WorldObject`, passes
`_refuse_hold_conflict`, and calls `_replace` → `_touch` → a full
`write_document` of the whole scene on a `FileWorldStore`. Pre-existing
behaviour, but this branch adds a docstring (`store.py:182-186`) that advertises
the clear-then-set protocol to exactly the caller most likely to be handing in
JSON-shaped values — the coming ROS query service.

Fix direction: normalize once at the top of `set_held_by`
(`side = as_optional_enum(side, Side, name='side')`) and compare after; the
downstream `WorldObject` construction then does no work. Also makes a bogus
`side` fail with a `side`-named message rather than a `WorldObject.held_by` one.

### N5 — D23 gains a new startup failure class that the decision log does not record

`docs/design/decisions.md:51-57`. Nothing in D23 becomes literally false: the
"restart is a power cycle" bullet (line 54) still holds for every scene that can
now exist, and a same-side-conflict live file is arguably covered by "a
**corrupt** one is never silently repaired" (line 57). But D23's taxonomy of
what a live file can fail on is a *schema* taxonomy (unknown key, missing key,
wrong type, foreign version) — the new rule is a *semantic* scene invariant, and
`_release_persisted_holds` (mock_backend.py:268-275), which D23 line 54 presents
as the answer to a file recording held objects, now never runs for that file
because construction raises first.

Out of scope for this PR (R10: `src/robot_world/` only; decisions.md is
operational). Fix direction: follow-up comment on the issue asking Sisyphus to
amend D23 with one clause — "at most one object may name each gripper side; a
file violating that is refused at load like any other corruption" — so the
decision log stays the source of truth for the query service that reads it.

### N6 — `MockBackend.store`'s "going around the backend is loud" contract moved earlier and its docstring did not follow

`src/robot_backends/robot_backends/mock_backend.py:162-175` tells a writer that
mutating `held_by` through the `store` handle desyncs the scene and shows up as
a loud failure on the **next `get_observation()`**; `test_mock_persistence.py:263`
pins that story. After this branch, one half of it fails *earlier and better*: a
conflicting `backend.store.set_held_by(obj, side)` now raises `WorldStoreError`
at the call, never reaching an observation.

I verified this is not a regression: in the old code that same sequence produced
a `ValueError` out of `Observation._check_held_objects_agree` on the very next
`get_observation()` — and `MockBackend.execute` calls `get_observation()` on
both its success and refusal paths, so an uncaught `ValueError` escaped
`execute()` either way. Same class of exception (`WorldStoreError` *is* a
`ValueError`), strictly better locality. Only the docstring is now incomplete.

Out of scope (R10) → follow-up on the issue.

---

## Probes I want run

Each is a hypothesis I could not execute (no Bash tool). Treat unrun probes as
unresolved.

**P1 — N1's `AttributeError` claim.** From `src/robot_world`:

```bash
python - <<'PY'
import robot_world.store as s
from robot_world import FileWorldStore, WorldDocument, WorldObject, write_document
from robot_skills import Pose
import tempfile, pathlib
s.FileWorldStore.seed_document = lambda self: self._seed   # the "collapse"
d = tempfile.mkdtemp(); seed = pathlib.Path(d)/'seed.json'
write_document(seed, WorldDocument(locations={'dock': Pose()}, start_location='dock'))
try:
    FileWorldStore(pathlib.Path(d)/'live.json', seed_path=seed)
except Exception as exc:
    print(type(exc).__name__, exc)
PY
```

*Confirms N1* if it prints `AttributeError ... '_seed'`. *Refutes N1* if it
constructs successfully or fails some other way — in which case the docstring is
right and N1 should be dropped.

**P2 — N2's "one assertion carries criterion 2".** Revert `store.py:354` to
`super().__init__(document)`, **and** delete `test_file_store.py:107`, then
`pytest src/robot_world/test -q`.
*Confirms N2* if the suite is green (the criterion-2 regression is invisible).
*Refutes N2* if anything fails — then some other test pins it too and N2 can be
downgraded to "fine as is".

**P3 — N3's ordering gap.** In a scratch copy of `test_document.py:187-192`,
swap the `held_by` values to `RIGHT, RIGHT, LEFT, LEFT`, keep the expected
`[Side.LEFT, Side.RIGHT]`, and run that test.
*Confirms N3 is worth fixing* if it passes (the assertion is stronger and free).
It should pass against the current implementation; if it fails, that is a real
BLOCK on `duplicate_hold_sides` and I want to know immediately.

**P4 — N4's redundant write.** With a `FileWorldStore`, monkeypatch
`store_module.write_document` to count calls, then:
`store.set_held_by('cube_1', Side.LEFT); n = len(writes); store.set_held_by('cube_1', 'left')`.
*Confirms N4* if `len(writes) == n + 1`. *Refutes N4* if it stays `n`.

**P5 — `StopIteration` unreachability (belt-and-braces).** I proved by
construction that `store.py:284`'s `next(...)` cannot raise (see "verified"
below), but a cheap empirical check over the whole reachable mutation surface:

```bash
python - <<'PY'
import itertools
from robot_skills import Pose, Side
from robot_world import WorldDocument, WorldObject, WorldStore, WorldStoreError
base = WorldDocument(locations={'dock': Pose()}, start_location='dock',
                     objects=tuple(WorldObject(i, 'x', Pose()) for i in ('a','b','c')))
sides = (None, Side.LEFT, Side.RIGHT)
for seq in itertools.product(itertools.product('abc', sides), repeat=4):
    st = WorldStore(base)
    for oid, side in seq:
        try: st.set_held_by(oid, side)
        except WorldStoreError: pass
    st.document()   # re-validates through Layer A
print('ok')
PY
```

*Confirms soundness* if it prints `ok`. *Refutes R1/R3* if any
`StopIteration` / `ValueError` escapes — that would be a BLOCK.

---

## What I verified as sound (traced, not assumed)

Recorded so the manager knows what was actually checked.

1. **R1/R3 — "a refused mutation leaves the registry byte-identical." Holds.**
   `set_held_by` (`store.py:188-199`) reads via `_require`, short-circuits,
   constructs `updated` (a pure value), calls `_refuse_hold_conflict`, and only
   then `_replace`. `add_object` (`store.py:201-211`) type-checks, id-checks,
   conflict-checks, and only then touches `self._objects` and `_touch()`.
   Neither `self._objects`, `_pending` nor `_batch_depth` is written before any
   raise on either path. The three-step `add_object` ordering the brief asked me
   to attack is correct: the id-taken check refuses *before* the conflict check,
   so `_refuse_hold_conflict`'s `others` (which filters by `object_id`) is never
   asked to reason about a replacement.

2. **R1 — "the invariant is defined once; the two layers cannot drift." Holds.**
   Both layers call `duplicate_hold_sides` (`document.py:174-188`); neither
   re-derives the scan. I looked for a Layer A / Layer B disagreement and found
   none reachable:
   * `others` (`store.py:278-281`) excludes the entry being replaced by
     `object_id` — which is exactly the entry Layer A would *not* see twice,
     because `self._objects` is keyed by `object_id` and `_replace`/`_load`
     always key by `item.object_id`. So `(*others, item)` is precisely the
     object set the post-mutation `document()` would validate.
   * The `item.held_by is None` early return (`store.py:276-277`) matches Layer
     A, which ignores `None` holders (`document.py:187`).
   * The `item.held_by == side` early return (`store.py:189`) can only skip a
     check for a state the registry is already in, which Layer A accepts.
   * **`next(...)` at `store.py:284` cannot raise `StopIteration`.** Reached only
     when `item.held_by ∈ duplicate_hold_sides((*others, item))`, i.e. that side
     is claimed ≥2 times in that collection; `item` contributes exactly one, so
     ≥1 of `others` claims it. The `is` comparison is safe because
     `WorldObject.__post_init__` normalizes `held_by` through `as_optional_enum`
     (`validation.py:45-61`), which returns genuine `Side` members — enum members
     are singletons and `Side` is a plain `@unique Enum` with no `str` mixin, so
     `==` and `is` coincide. Commit `6a11e15`'s scoping to the claimed side is
     what makes this total; without it the `next()` really could have looked on
     the wrong side.

3. **R3 — "never observable in a violating state via `objects()`/`find_object()`,
   including mid-batch." Holds.** `objects()`/`find_object()` read
   `self._objects` directly. The only writers are `_replace`, `add_object`'s
   insert, `remove_object`'s delete (cannot create a conflict) and `_load`.
   `batch()` (`store.py:229-244`) only defers `_flush`, never the check, so a
   direct reader inside a batch sees a conflict-free registry. Confirmed the
   batch bookkeeping is untouched by a refusal: the raise happens before
   `_touch()`, so `_pending` and `_batch_depth` are unchanged, and if the
   refusal escapes the `with` the `finally` still commits whatever legitimately
   preceded it (`test_a_batch_left_by_an_exception_still_commits` covers the
   generic form).

4. **R4 — "a `WorldDocument` with duplicate holds cannot be constructed." Holds.**
   `__post_init__` runs on `WorldDocument(...)`, on `from_dict` (via the frozen
   constructor at `document.py:305-310`), and on `dataclasses.replace`.
   `copy`/`deepcopy`/`pickle` bypass `__post_init__` but can only copy an
   already-valid instance. `object.__setattr__` is deliberate abuse, not a path.
   So `_load` needing no check (R4) is correct, and
   `test_a_conflicting_scene_cannot_reach_a_store_at_all` (`test_store.py:144`)
   pins the reasoning rather than a comment, as R4 asked.

5. **R7 — "`WorldDocument` is deeply immutable, so no deep copy is needed."
   Holds; I could not find a mutation path.**
   `__post_init__` does `locations = dict(self.locations)` (`document.py:208`) —
   a *fresh local* dict, so the caller's mapping is not aliased
   (`test_document_is_immutable_and_defensively_copied` pins this) — and the
   only surviving reference to that local is the `MappingProxyType` at
   `document.py:217`; the local dies with the frame. `objects` is normalized to
   a tuple of frozen `WorldObject`s holding frozen `Pose`s of frozen
   `Point`/`Quaternion` (`geometry.py:36, 86, 129`) whose fields are plain
   floats. `WorldStore._load` (`store.py:250`) and `document()` (`store.py:137`)
   both copy the mapping rather than aliasing it. Handing the same
   `WorldDocument` object to `_seed` and to `_load` is therefore safe; the
   `document` fixture being a module-level `SMALL_WORLD` shared across the whole
   suite is itself standing evidence.

6. **R7 — "the keyword-only `seed` changes no existing caller's behaviour."
   Holds.** I grepped every `WorldStore(`/`FileWorldStore(`/`super().__init__(`
   in the repo. Outside `robot_world`'s own tests there are exactly two
   production constructions: `mock_backend.py:140`
   (`WorldStore(world_to_document(seed))`) and `server.py:404`
   (`FileWorldStore(world_state, seed_path=world_seed)`). No caller anywhere
   passes `seed=` except `FileWorldStore.__init__` (`store.py:354`). The
   `self._load(self._seed)` → `self._load(document)` swap is equivalent for
   every one of them, because with `seed=None` the constructor sets
   `self._seed = document` *after* defaulting `document` to
   `read_seed_document()` (`store.py:91-96`) — same object, same single seed
   read, same order of the two `TypeError` guards relative to any file I/O.

7. **R8 — what the tests do and do not catch.** They catch: (a) reverting
   `super().__init__(document, seed=seed)` → `test_file_store.py:107` fails;
   (b) collapsing `FileWorldStore.seed_document()` into an attribute read →
   the *pre-existing* `test_reset_restores_from_the_seed_file_not_from_memory`
   (`test_file_store.py:63-81`) fails, because it rewrites the seed **file**
   after construction and demands `reset()` follow it. So both halves of R8's
   defense-in-depth are pinned from opposite directions, which is the right
   shape. They do **not** catch: N2's single-assertion thinness, and N1's actual
   collapse failure mode.

8. **Test adequacy against R9.1–R9.8 — adequate.** Each new test is falsifiable
   by a one-line source mutation:
   * R9.5 `test_a_gripper_cannot_be_given_a_second_object_to_hold`
     (`test_store.py:57`) — asserts `store.document() == before`, both objects'
     `held_by`, `commits == []` **and** `pending_write is False`. Moving
     `_replace` before `_refuse_hold_conflict` fails it; a mutate-then-raise
     implementation cannot pass it. This is the test that discriminates
     check-then-mutate from "it raises", exactly as R9.5 demanded.
   * R9.5 batch case (`test_store.py:78`) — asserts recovery observably
     (`commits == [1, 1]` proves `_batch_depth` returned to zero; a batch whose
     only call is refused commits nothing, proving `_pending` was never set).
     Testing the observable rather than the private attribute is the right call.
   * R9.6 `test_a_new_object_cannot_arrive_in_a_full_gripper` — refusal *and*
     the free-gripper positive case, so it cannot pass by refusing everything.
   * R9.7 `test_a_live_file_holding_one_object_in_two_grippers_is_refused`
     (`test_file_store.py:175`) — genuinely exercises the **live** read
     (`store.py:350`), not the seed read, and asserts the file bytes are
     untouched afterwards, matching the corrupt-file precedent.
   * R9.8 `test_a_hold_changes_hands_by_clearing_it_first` — covers clear,
     re-claim, both hands full, and idempotent re-assert; would fail a
     too-eager check that refused `None` or refused re-asserting an existing
     hold.
   * R9.4 — both the parsed (`SerializationError`) and the Python-constructed
     (`ValueError`) forms are covered in
     `test_scene_invariants_are_enforced_at_the_parse_boundary`, plus the
     "what the rule does *not* forbid" test.
   * `match=` regexes are specific enough: `'held by the same gripper: left'`
     and `'left gripper: it already holds'` cannot be produced by any
     pre-existing message. The one weaker regex, `'it already holds'`
     (`test_store.py:103`), still does not match `add_object`'s pre-existing
     `'the world store already holds an object'` — checked the literal
     substrings.

9. **Blast radius — the implementer's trace is correct.** I re-read
   `mock_world.py` (`world_to_document` at 220-242 builds `WorldObject` from
   `ObjectSpec` with `object_id`/`label`/`pose`/`graspable` only; `ObjectSpec`
   has no `held_by`; `world_from_document` drops it) and `mock_backend.py`:
   `_grasp` (301-330) refuses an already-held object *and* resolves a free
   gripper via `_resolve_grasping_side`/`_require_free_gripper` (469-506) before
   touching the store; `_place` (349), `_open_gripper` (374) and
   `_release_persisted_holds` (275) only ever clear. Gripper bookkeeping and
   store `held_by` are written together in every handler and both are reset by
   `_power_on`. A repo-wide grep for `set_held_by`/`add_object` outside
   `robot_world` finds only those four production call sites plus one *clearing*
   test call (`test_mock_persistence.py:276`). Every `held_by` in every shipped
   JSON (`default_world.json`) is `null`. So no new `WorldStoreError` can escape
   into a `SkillResult` or crash a chore that previously worked, and no seed or
   scenario file is now unloadable. R10's escalation trigger did not fire.

10. **CLAUDE.md invariants.** Invariant 1 (skill API is the seam) untouched — the
    store gained a scene rule, no skill semantics. Invariant 2 (Mock first) —
    the change is backend-agnostic pure Python and `robot_backends`/`robot_mcp`
    were run, not edited. Invariant 3 (safety layer) — not on this path; the new
    refusal is a *world-model* invariant, correctly `WorldStoreError` and not a
    safety code. Invariant 5 (reuse) — reuses `robot_skills.serialization`,
    `validation`, and the existing `ValueError`/`WorldStoreError` idioms rather
    than inventing an exception type (R2 honoured).
