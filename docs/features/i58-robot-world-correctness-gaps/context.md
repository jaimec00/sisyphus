# Context: robot_world — two correctness gaps to close (#58)

Brief: GitHub issue #58, "robot_world: two correctness gaps to close before
the ROS query service." Owned paths: `src/robot_world/` only. Triaged from
the #54/#55 post-merge follow-up list.

## Acceptance criteria (restated)

1. **`held_by` uniqueness is unenforced.** `WorldDocument`/`WorldStore`
   currently let two `WorldObject`s claim the same `held_by` side
   simultaneously. Add an invariant check on mutation so a future reader of
   the store directly (a query service) cannot observe one gripper "holding"
   two objects. Harmless today only because `MockBackend` always clears
   `held_by` at power-on (`_release_persisted_holds`) and enforces
   one-object-per-gripper itself via its own `_grippers` bookkeeping before
   ever calling `set_held_by`.
2. **`FileWorldStore._seed` (inherited attribute) does not reliably hold the
   seed.** On a `FileWorldStore` reopened over an existing live file, the
   base class's `self._seed` attribute ends up holding *the live document as
   read at construction time*, not the true seed. This is currently harmless
   only because `FileWorldStore.seed_document()` is overridden to always
   re-read from disk and ignore `self._seed`. Store a deep copy / re-read
   from disk (i.e. make the seed the store actually relies on unambiguously
   correct), and add a test proving mutating the working doc does not change
   what `reset()` restores.

Explicitly de-scoped (do not touch): `batch()` commit-on-exception assertion,
atomic-write edge cases, error-message polish, the unused `rclpy` depend.

## 1. `WorldDocument` structure today

File: `src/robot_world/robot_world/document.py`.

### `WorldObject` (document.py:103-166)

Frozen dataclass:
```python
@dataclass(frozen=True)
class WorldObject(JsonSerializable):
    object_id: str
    label: str
    pose: Pose
    graspable: bool = True
    held_by: Side | None = None
```
`held_by` lives directly on each `WorldObject` (document.py:116), validated
in `__post_init__` (document.py:118-133) via
`as_optional_enum(self.held_by, Side, name='WorldObject.held_by')`
(document.py:129-133) — this only checks the *type* (must be `Side` or
`None`), never cross-object uniqueness (it can't; it has no view of siblings).
`Side` (from `robot_skills`, `src/robot_skills/robot_skills/skills.py:74-78`)
has exactly two members, `LEFT`/`RIGHT`, so "held_by uniqueness" in practice
means: at most one object with `held_by == Side.LEFT` and at most one with
`held_by == Side.RIGHT`, across the whole registry — there is no
"gripper id" concept beyond `Side`.

### `WorldDocument` (document.py:169-283)

```python
@dataclass(frozen=True)
class WorldDocument(JsonSerializable):
    locations: Mapping[str, Pose]
    start_location: str
    objects: tuple[WorldObject, ...] = ()
    start_column_height: float = 0.3
```
`__post_init__` (document.py:185-220) is where whole-document invariants
already live. It currently checks, in order: every location is an
`as_identifier`/`Pose`; `locations` non-empty; every object is a
`WorldObject`; **object_id duplicates** — the closest existing precedent for
what's being asked here:
```python
ids = [item.object_id for item in objects]
duplicates = sorted({name for name in ids if ids.count(name) > 1})
if duplicates:
    raise ValueError(
        f'WorldDocument.objects has duplicate object_id(s): {", ".join(duplicates)}')
```
(document.py:203-207) — then `start_location` is a real, known location.
There is **no** equivalent check for `held_by` duplicates anywhere in this
`__post_init__`.

### Mutation / construction entry points that can set or clear `held_by`

- `WorldObject(...)` direct construction (any caller) — sets `held_by` on one
  object at a time, no cross-object visibility.
- `WorldObject.from_dict` (document.py:145-166) — same, from JSON.
- `WorldDocument(...)` direct construction / `__post_init__` — sees the whole
  `objects` tuple but does not check `held_by` today (empirically verified,
  see below).
- `WorldDocument.from_dict` (document.py:250-283) — builds `objects` then
  calls the frozen constructor; same gap.
- `WorldStore.set_held_by(object_id, side)` (store.py:157-173) — the
  intended single-object setter; replaces one dict entry via `self._replace`.
- `WorldStore.add_object(item)` (store.py:175-184) — a *new* `WorldObject`
  can already carry a non-`None` `held_by` at insertion time; this is a
  second entry point that can introduce a `held_by` collision besides
  `set_held_by`.
- `WorldStore._load(document)` (store.py:221-228) — adopts a whole
  `WorldDocument` into `self._objects` (a dict keyed by `object_id`)
  **without any validation**; called from `WorldStore.__init__` (store.py:78)
  and from `reset()` (store.py:192-200, `self._load(self.seed_document())`).
  This is the path by which a *pre-existing on-disk violation* (a hand-edited
  or historically-written live/seed file with two objects both `held_by:
  "left"`) would silently become the live in-memory state.
- `FileWorldStore.__init__` (store.py:288-303) — calls `read_document` on an
  existing live file (storage.py:88-99, which parses via
  `WorldDocument.from_dict`) and passes that straight to `super().__init__`,
  i.e. straight to `_load`. Same gap: an on-disk violation loads silently.
- `update_object_pose` and `remove_object` do not set `held_by` but are
  otherwise-normal mutation entry points worth knowing about for the "on
  every mutation" design question.

**Batch semantics** (store.py:202-217, `batch()`): mutations inside a
`with store.batch():` block call the normal setters (`set_held_by`,
`add_object`, etc.) immediately in memory (`self._objects` is updated on
each call via `_replace`/`add_object`), only the **disk commit** via
`_flush()`/`_commit()` is deferred to batch exit. So whatever "check on
mutation" is added, it will see the in-memory state update at each call
regardless of whether a batch is open — there is no existing "check once at
commit" plumbing to hook into if per-mutation-immediate is preferred; the
existing invariant style (object_id duplicates, in `WorldDocument.__post_init__`)
checks in one shot over the whole registry, at construction, not incrementally.

### Empirically observed: no `held_by` uniqueness anywhere today

Ran (read-only, in a temp dir, no repo writes):
```
PYTHONPATH=src/robot_world:src/robot_skills python3 -c "..."
```
Result: `WorldDocument(...)` constructs successfully with two `WorldObject`s
both `held_by=Side.LEFT`; `WorldStore(doc)` loads it via `_load` without
error; and calling `store2.set_held_by('a', Side.LEFT)` then
`store2.set_held_by('b', Side.LEFT)` on a store with two free objects
succeeds both times with no exception. **empirically-observed.**

## 2. Where validation currently happens / the existing error idiom

Two layers, two exception families, used consistently:

- **Parse/construction-invariant layer** — `ValueError`/`TypeError` raised
  directly from `__post_init__` (document.py:185-220, and
  `robot_skills.observation.Observation.__post_init__`,
  `src/robot_skills/robot_skills/observation.py:333-354`, same pattern:
  duplicate-id check, `sorted({...})`, `", ".join(...)` in the message). When
  reached via `from_dict`, these get wrapped into `SerializationError` by the
  `parse_errors(context)` context manager (`src/robot_skills/robot_skills/serialization.py:166-181`,
  `SerializationError` is itself a `ValueError` subclass,
  serialization.py:122-123) so a caller parsing JSON only ever has to catch
  one exception type.
- **Store-runtime-refusal layer** — `WorldStoreError` (storage.py:63-70, also
  a `ValueError` subclass), used by `WorldStore._require` for "no such
  object" (store.py:230-237) and `add_object` for "id already taken"
  (store.py:180-183), and by `storage.py` for read/write/parse failures.
  Docstring: "A corrupt world file is always this, never a silent repair."

The closest structural precedent for a cross-object invariant that spans the
whole registry and needs to fire both at parse time *and* at runtime
mutation time is `robot_skills.observation.Observation._check_held_objects_agree`
(`src/robot_skills/robot_skills/observation.py:356-389`): it cross-checks
every gripper's `held_object_id` against every `SceneObject.held_by`, raising
plain `ValueError` from `__post_init__`, with one message per direction of
disagreement. It does not need a *separate* "two objects same gripper" check
because a `dict`/1:1 `gripper.held_object_id` naturally forces at most one
object per side — but it is evidence for the message style and that
`ValueError` (not a new exception type) is the going idiom for a whole-scene
invariant violation raised from `__post_init__`.

## 3. `FileWorldStore` `_seed` lifecycle end to end

Files: `src/robot_world/robot_world/store.py`, `.../storage.py`.

- **Assignment.** `WorldStore.__init__` (store.py:70-78):
  ```python
  def __init__(self, document: WorldDocument | None = None) -> None:
      if document is not None and not isinstance(document, WorldDocument):
          raise TypeError(...)
      self._seed = document if document is not None else read_seed_document()
      self._batch_depth = 0
      self._pending = False
      self._load(self._seed)
  ```
  `self._seed` is set exactly once, here, and never reassigned by `_load`,
  `reset()`, or any mutation method.
- **What it points to for a bare `WorldStore`.** Whatever `document` the
  caller passed (or the shipped seed if none). Correct and stable for the
  lifetime of the store, because `WorldDocument` is frozen/immutable so
  nothing can mutate the object `self._seed` points to in place.
- **What it points to for a `FileWorldStore`.** `FileWorldStore.__init__`
  (store.py:288-303):
  ```python
  seed = self.seed_document()               # re-reads seed from disk (override)
  if self._live_path.exists():
      document = read_document(self._live_path)   # the LIVE file's content
  else:
      document = seed
      write_document(self._live_path, document)
  super().__init__(document)                # sets self._seed = document
  ```
  On a **fresh** live path (first run), `document is seed` in content, so
  `self._seed` happens to equal the true seed. On a **reopened** live path
  (any subsequent run against a file that already diverged from the seed),
  `document` is the **live** file's content, not the seed's — so
  `self._seed` (base-class attribute) ends up holding the live document, not
  the seed, even though its name says "seed."
- **Every read of `self._seed`.** Exactly one place in the whole package:
  `WorldStore.seed_document()` (store.py:125-127), `return self._seed`. But
  `FileWorldStore` **overrides** `seed_document()` (store.py:315-317):
  ```python
  def seed_document(self) -> WorldDocument:
      """Re-read the seed from disk, so ``reset()`` restores ground truth."""
      return read_seed_document(self._seed_path)
  ```
  This override never touches `self._seed`. Because Python method dispatch
  is virtual, `WorldStore.reset()` (store.py:192-200,
  `self._load(self.seed_document())`) calls the **overridden** version on a
  `FileWorldStore` instance, so **today `reset()` is correct** — it always
  re-reads the seed file from disk, never the possibly-stale/aliased
  `self._seed` attribute.
- **The gap, precisely.** `self._seed` is assigned on `FileWorldStore` but
  is **dead** in the sense that nothing currently reads it back for that
  subclass — it is shadowed by the override. It is a landmine: it is named
  and typed exactly like "the seed", any future maintainer optimizing away
  the "re-read on every `reset()`/`seed_document()` call" (a real cost: a
  full file read + parse + validate) by changing `FileWorldStore.seed_document()`
  back to `return self._seed` would silently reintroduce the aliasing bug —
  and, as the issue notes, **no current test would catch it**, because the
  only test that opens a *second* `FileWorldStore` over an already-mutated
  live file (`test_a_mutation_outlives_the_store_that_made_it`,
  `src/robot_world/test/test_file_store.py:43-60`) never calls `reset()` on
  it, and the tests that do call `reset()` after mutating either construct
  only one store per test or (in
  `test_reset_restores_from_the_seed_file_not_from_memory`,
  test_file_store.py:63-81) rewrite the **seed file itself** externally
  before calling `reset()` on the *original* store object, which happens to
  still exercise the override correctly regardless of what `self._seed`
  contains.

  **empirically-observed** (ran, read-only, own `tempfile.mkdtemp()`, no
  repo writes):
  ```python
  store = FileWorldStore(live_path, seed_path=seed_path)   # fresh live file
  # store._seed == seed_doc  -> True  (coincidence of "first run")
  store.update_object_pose('a', Pose.from_xyz(9, 9, 9))    # mutate + commit

  store2 = FileWorldStore(live_path, seed_path=seed_path)  # reopen, already mutated
  # store2._seed == seed_doc (the TRUE seed)                -> False
  # store2._seed == store2.document() (the LIVE doc at construction) -> True
  # store2.seed_document() (the override, re-reads disk) == seed_doc -> True
  ```
  Confirms: `self._seed` on a `FileWorldStore` aliases "whatever the live
  file said at construction time," not the seed; only the override's
  disk-re-read gives the correct answer today.
- **`MockBackend` also depends on `seed_document()` being correct.**
  `MockBackend.world` (`src/robot_backends/robot_backends/mock_backend.py:152-160`):
  `return world_from_document(self._store.seed_document(), robot=self._robot)`
  — another consumer of the polymorphic `seed_document()` contract, so
  whatever fix is chosen must keep `seed_document()` (not `self._seed`)
  answering "the true seed," for both store flavours.

## 4. D23, quoted

`docs/design/decisions.md:51` (bullet, "World state is a JSON-file store"):

> Two files, **one document schema**: a **read-only seed** shipped inside
> `robot_world` and a **live-state file** the robot writes; `reset()`
> re-reads the *seed file* and rewrites the live file from it, so the first
> mutation can never destroy ground truth.

And decisions.md:54 (one of the four boundaries):

> **A restart is a power cycle.** The robot comes up at the scene's start
> posture with empty grippers, so any object the file still records as
> `held_by` is released *where it lies* (its persisted pose is kept).
> Persisting `held_by` without gripper state would otherwise make the first
> `get_observation()` raise, since `Observation` enforces that the two views
> agree...

And decisions.md:57 (accepted gaps line, for calibration — these two issues
are *not* in this list, i.e. they were not accepted-as-known-gaps in D23,
they are bugs found afterward):

> Accepted gaps, recorded rather than assumed away: no `fsync` ..., no
> cross-process lock ..., and no location add/remove API yet.

So "reset() re-reads the seed file" is the literal guarantee criterion 2
protects; nothing in D23 explicitly promises `held_by` uniqueness, but D23's
"restart releases held objects" boundary is exactly why criterion 1 has been
harmless so far — `MockBackend._power_on`/`_release_persisted_holds`
(mock_backend.py:240-275) always clears `held_by` before anything else can
observe the store, today. A read-only query service (the reason this issue
exists) would not go through `MockBackend` at all, so that safety net would
not apply to it.

## 5. Existing test conventions (`src/robot_world/test/`)

- `conftest.py` (`src/robot_world/test/conftest.py`) defines one shared
  fixture scene, `SMALL_WORLD` (module-level constant, two locations `dock`/
  `bench`, two objects `cube_1`/`anvil_1`, neither held), exposed as the
  `document` fixture (conftest.py:29-32), and a `seed_file` fixture
  (conftest.py:35-40) that writes `document` to `tmp_path/seed.json` via
  `write_document` and returns the path as a `str`. All of `test_store.py`
  and `test_file_store.py` build on these two fixtures; no test constructs
  its own from-scratch scene unless deliberately testing something the
  shared scene can't (e.g. `test_document.py`'s own tiny inline documents
  for round-trip/parse-error assertions).
- Naming: test functions are full sentences,
  `test_<subject>_<claim>`, and each has a one-line docstring restating the
  behavioural claim in prose (e.g. `test_reset_restores_from_the_seed_file_not_from_memory`,
  test_file_store.py:63). Follow this for new tests.
- `test_document.py` groups: round-trip contract tests (`round_trip` helper,
  test_document.py:22-27), strict-parse tests (unknown/missing/mistyped
  keys), and a dedicated
  `test_scene_invariants_are_enforced_at_the_parse_boundary` (test_document.py:133-148)
  that already exercises `start_location` validity, duplicate `object_id`,
  and empty `locations` together — this is the natural home for a new
  "duplicate `held_by`" sub-case if the invariant lands in
  `WorldDocument.__post_init__`, following the exact `pytest.raises(...,
  match=...)` idiom already used there.
- `test_store.py` groups: query tests, mutation tests
  (`test_moving_and_holding_an_object`, test_store.py:36-54, is the existing
  `held_by`-focused test — it never exercises two objects held by the same
  side), reset tests (`test_reset_restores_the_seed_scene`, test_store.py:84-96),
  batch tests (monkeypatching `WorldStore._commit` to count commits, e.g.
  test_store.py:124-141), and refusal tests
  (`test_mutating_an_unknown_object_is_refused`, test_store.py:72-81 — the
  `WorldStoreError` idiom to match if the new check is store-level).
- `test_file_store.py` groups: seed/reseed lifecycle tests, atomic-write
  interaction tests, and the corrupt/missing-file refusal tests
  (`test_a_corrupt_live_file_is_never_silently_repaired`, test_file_store.py:97-105
  — the precedent for "loud refusal, evidence untouched" if load-time
  rejection of an already-`held_by`-violating file is chosen).
  `test_reset_restores_from_the_seed_file_not_from_memory` (test_file_store.py:63-81)
  is the closest existing precedent for the new "mutating the working doc
  doesn't change what `reset()` restores" test criterion 2 asks for, but
  note it rewrites the **seed file** externally and reuses the **same**
  store object — it does **not** cover the "second store reopening an
  already-mutated live file, then reset()" scenario that the empirical probe
  above shows is where `self._seed` actually diverges from the truth.

## 6. Other consumers in `src/` that could break if `held_by` validation starts raising

(from `grep -rn "held_by\|set_held_by\|WorldStore\|FileWorldStore" src`)

- **`robot_backends/robot_backends/mock_backend.py`** — the only production
  code that calls `self._store.set_held_by(...)`:
  - `_grasp` (mock_backend.py:301-330): already checks `item.held_by is not
    None` (refuses "already held") before calling
    `self._store.set_held_by(item.object_id, side)` (line 329), and
    separately resolves a *free* gripper side via `_resolve_grasping_side`/
    `_free_side` (mock_backend.py:478-489) before ever reaching the store
    call — so today it is structurally impossible for the backend to ask the
    store to create a same-side collision through normal skill dispatch.
  - `_place` / `_open_gripper` (mock_backend.py:332-350, 364-379) only ever
    call `set_held_by(..., None)` (clearing), never a value that could
    collide.
  - `_release_persisted_holds` (mock_backend.py:268-275) clears multiple
    objects' `held_by` to `None` inside one `store.batch()` at every
    power-on/reset — all clears, never collision-introducing.
  - `MockBackend.world` (mock_backend.py:152-160) reads `self._store.seed_document()`
    — relevant to criterion 2 (must keep returning the true seed), not
    criterion 1.
  - **Net**: a `held_by`-uniqueness check added to `set_held_by`/`add_object`
    should not fire for any current `MockBackend` code path — inferred from
    reading all four call sites above, not executed against the real
    invariant (which does not exist yet). **inferred-from-source.**
- **`robot_mcp/robot_mcp/server.py` / `robot_mcp/robot_mcp/tools.py`** — no
  direct `set_held_by`/`WorldStore` mutation calls; `server.py:404` only
  constructs `FileWorldStore(...)` and hands it to `MockBackend`. `tools.py:56`
  only mentions `held_by` in a docstring describing the JSON scene shape
  returned to the brain. **inferred-from-source.**
- **`robot_backends/robot_backends/mock_world.py`** — builds a `WorldDocument`
  from the legacy `MockWorld` seed representation; its own docstring
  (mock_world.py:197) says "`held_by` is dropped" when going the other
  direction (`world_from_document`... `world_to_document`?) — worth
  double-checking this conversion path doesn't ever synthesize two objects
  with the same `held_by` (looked plausible not to, since it's a straight
  seed description with nothing held), but not exhaustively traced here.
  **inferred-from-source, not fully traced — flagged for the implementer.**
- **Tests exercising `held_by`** across `robot_backends/test/` and
  `robot_mcp/test/` (test_mock_persistence.py, test_mock_scenario.py,
  test_mock_skills.py, test_mock_failures.py, test_world_state_options.py,
  test_world_state_persists.py, test_clear_the_table.py, test_tool_calls.py)
  all only ever assert a *single* object's `held_by` state — none construct
  a two-objects-same-side scene — so none are expected to be affected by
  adding the invariant. **inferred-from-source** (not executed; owned paths
  for this issue are `src/robot_world/` only, so these packages' tests are
  out of scope to run/modify here, but worth being aware they exist as a
  blast-radius check).

## 7. Open questions for the manager

1. **Where does the `held_by`-uniqueness check live?** In
   `WorldDocument.__post_init__` (mirrors the existing `object_id`-duplicate
   check exactly, document.py:203-207; covers `WorldDocument(...)` direct
   construction and `from_dict`/JSON-load "for free," and would also need
   `WorldStore.document()` (store.py:116-123) or `_load` to go through
   `WorldDocument` construction to inherit it) versus in `WorldStore` itself
   (`set_held_by`/`add_object`/`_load`, matching the `WorldStoreError`
   runtime-refusal idiom, store.py:230-237/180-183) versus both. Note the
   store's live state (`self._objects: dict[str, WorldObject]`) is **not**
   currently re-validated as a `WorldDocument` on every mutation — only
   `document()` builds one, on demand — so "checked in `__post_init__`
   alone" would **not** by itself make `set_held_by`/`add_object` raise
   immediately; it would only be caught the next time `.document()` (or a
   commit, which calls `.document()`, store.py:341-343) is called, unless
   the store also calls something that constructs/validates a `WorldDocument`
   on every mutation, or duplicates the check as a lightweight
   dict-scan in the store.
2. **Exception type.** `ValueError` (matching `WorldDocument.__post_init__`'s
   existing idiom, e.g. the duplicate-`object_id` `ValueError` at
   document.py:206) or `WorldStoreError` (matching `WorldStore`'s runtime
   refusals, e.g. `add_object`'s "already holds an object" at
   store.py:181-182)? These differ in whether `pytest.raises` callers need
   `SerializationError`/`ValueError` vs `WorldStoreError` — `WorldStoreError`
   *is* a `ValueError` subclass, so a `WorldStoreError` choice is
   catchable either way, but the reverse isn't true.
3. **Fires on every mutation, or only at `.document()`/commit time?**
   `set_held_by`/`add_object` calling `self._require`/dict lookups directly
   (cheap, O(objects) scan per call) vs. relying on `.document()`'s
   `__post_init__` (already O(objects) but only invoked at `document()`/
   `_commit()`/`reset()` time, store.py:116-123, 341-343) — a caller reading
   `store.find_object(...)`/`store.objects()` directly between a `set_held_by`
   call and the next `.document()` call would see the in-memory dict, not a
   validated `WorldDocument`, if the check lives only in `__post_init__`.
4. **Load-from-disk path for an existing violating file.** `read_document`
   (storage.py:88-99) already delegates to `WorldDocument.from_dict`
   (`_parse`, storage.py:73-85) — if the invariant lives in
   `WorldDocument.__post_init__`, a violating on-disk live file would already
   be rejected there with `WorldStoreError(...)` wrapping the
   `SerializationError` (storage.py:82-85), matching the existing "loud
   refusal, never silent repair" idiom for a corrupt live file
   (`test_a_corrupt_live_file_is_never_silently_repaired`,
   test_file_store.py:97-105, and `test_a_schema_violating_live_file_is_refused`,
   test_file_store.py:108-120). Confirm this is the desired behavior for an
   *existing* file written before this fix landed (reject at load, requiring
   manual repair/`reset()`-from-seed) rather than a silent auto-repair
   (clearing the later-conflicting `held_by` to `None`), which the codebase's
   existing "never silently repair a corrupt file" stance (storage.py:63-70
   docstring) would argue against.
5. **Per-`Side` across the whole registry, confirmed?** `Side` has exactly
   two members (`LEFT`/`RIGHT`, `src/robot_skills/robot_skills/skills.py:74-78`)
   and there is no other "gripper id" concept in this schema — so "held_by
   uniqueness" is unambiguous (at most one `WorldObject` with
   `held_by == Side.LEFT`, at most one with `held_by == Side.RIGHT`, across
   `objects`), not a design choice, but worth the manager explicitly
   confirming since the issue phrasing ("one gripper holding two objects")
   is symmetric with this reading.
6. **Does `batch()` check per-op or once at the end?** Given `batch()`
   already applies each mutation to `self._objects` immediately and only
   defers the *disk write* (store.py:202-217), a per-mutation check (answer
   to Q3) would also naturally fire per-op inside a batch — meaning a
   *transient* same-side collision that is corrected by a later call in the
   same batch (e.g. hypothetically: grasp on the left with object A, then in
   the same batch move A off and grasp with object B, though no current
   skill actually does this) would raise mid-batch even though the batch's
   *final* state is valid. Decide whether that transient-mid-batch case
   should be allowed (check only at flush/`document()`-construction time) or
   is out of scope / not realistically reachable given current call sites
   (per finding in section 6 above, no current `MockBackend` code path
   produces even a transient collision).
7. **Deep copy vs. re-read-from-disk for `_seed`.** The issue text offers
   both options. Given `FileWorldStore.seed_document()` **already**
   re-reads from disk unconditionally today (store.py:315-317) and that is
   what makes `reset()` currently correct, the actual fix target is likely
   the **misleading `self._seed` attribute inherited from the base class**
   (assigned in `WorldStore.__init__`, store.py:75, to "whatever document
   the subclass's constructor happened to pass," which for `FileWorldStore`
   is the live document, not the seed) rather than the override itself.
   Options include: (a) leave the re-read-from-disk override as the
   long-term mechanism and stop relying on/storing a same-named `self._seed`
   at all in `FileWorldStore` (e.g. don't call `super().__init__` with a
   `document` that gets mislabeled, or shadow/neutralize the attribute so a
   future "optimize away the override" cannot silently reintroduce the bug);
   (b) keep assigning `self._seed` but make it a genuine deep-copied/re-read
   seed at `__init__` time too (matching the issue's literal wording), even
   though `seed_document()`'s override would still take precedence at call
   time — this fixes the attribute's honesty but doesn't remove the
   landmine that a future edit to the override could still silently regress
   past a still-correct `self._seed`, unless `self._seed` is *also* kept
   correct forever (i.e. by construction proof, not by convention). The
   manager should decide which of these (or a variant) satisfies "store a
   deep copy / re-read from disk" as the issue intends, given the override
   already provides the re-read.
8. **New test's exact shape.** Per the empirical finding in section 3, the
   *only* scenario that currently exposes the aliasing (were the override
   ever weakened) is: construct a `FileWorldStore` A over a live file that
   **already differs from the seed** (either from a prior mutation by A
   itself, or by a second store B), then call `reset()` and assert the
   result matches the **seed file's** content, not "whatever the live file
   said when this store object was constructed." Confirm whether the desired
   new test should specifically construct two `FileWorldStore` instances
   over the same live/seed pair (mirroring
   `test_a_mutation_outlives_the_store_that_made_it`, test_file_store.py:43-60)
   to pin this, or whether a single-store-mutate-then-reset test already
   covers it well enough (empirically it does *not*, because the existing
   single-store reset test rewrites the seed file externally rather than
   mutating the live file through the store first).

## Files touched by this exploration (read only)

- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/robot_world/document.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/robot_world/store.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/robot_world/storage.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/robot_world/__init__.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/test/conftest.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/test/test_document.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/test/test_store.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_world/test/test_file_store.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_skills/robot_skills/observation.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_skills/robot_skills/serialization.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_skills/robot_skills/skills.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/src/robot_backends/robot_backends/mock_backend.py`
- `/home/sisyphus/worktrees/i58-robot-world-two-correctness-gaps-to-clos/docs/design/decisions.md` (D23, line 51-57)
