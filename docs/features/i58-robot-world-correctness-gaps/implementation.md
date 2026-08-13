# Implementation: robot_world — two correctness gaps (#58)

Owned paths: `src/robot_world/` only (R10). Nothing outside it was edited.

## Commits

| sha | what |
| --- | --- |
| `e01d68c` | criterion 1 — `held_by` uniqueness, both layers, with tests |
| `a777eef` | criterion 2 — `_seed` genuinely holds the seed, with tests |
| `6a11e15` | follow-up hardening of the store-layer check (see "Surprises") |

Each commit is green on its own (`src/robot_world/test` was run from the
package directory after each).

## Criterion 1 — `held_by` uniqueness

### The rule, once (R1)

`robot_world/document.py` gained a module-level

```python
def duplicate_hold_sides(objects: Iterable[WorldObject]) -> list[Side]
```

returning every `Side` claimed by more than one object, in `Side` declaration
order. It iterates `Side` itself rather than `SIDE_ORDER`, so a third member
added tomorrow is covered by construction rather than by remembering to extend
a tuple. Both enforcement layers call it; neither re-derives the scan.

It is exported from `robot_world/__init__.py`, following that file's existing
habit of re-exporting every name in `document.__all__`. That also gives the
future ROS query service — the consumer this issue exists for — a way to assert
the invariant it now depends on without importing a private module.

### Layer A — `WorldDocument.__post_init__` (R1, R5)

Placed immediately after the duplicate-`object_id` check it mirrors:

```
WorldDocument.objects has more than one object held by the same gripper: left
```

`ValueError` (R2), matching that sibling check. This closes direct
construction, `from_dict`, and therefore every on-disk load: `read_document` →
`WorldDocument.from_dict` → `SerializationError` → `WorldStoreError`, which is
the existing "a corrupt world file is refused, never silently repaired" path
(R5). A pre-existing violating live file must be repaired or deleted by hand;
that is the same deal already accepted for corrupt JSON.

### Layer B — `WorldStore.set_held_by` / `add_object` (R1, R2, R3)

Layer A alone would not close the issue: the store's live registry is a plain
`dict` and `document()` is only built on demand, so a direct reader
(`objects()`/`find_object()`) would see an inconsistent scene until the next
commit. `WorldStore._refuse_hold_conflict(item)` raises `WorldStoreError` (R2)
naming both objects and the fix:

```
cannot record 'anvil_1' as held by the left gripper: it already holds 'cube_1';
release that first (set_held_by('cube_1', None))
```

**Check-then-mutate.** `set_held_by` now builds the updated `WorldObject`,
checks it, and only then calls `_replace`. Nothing touches `self._objects`,
`_pending` or `_batch_depth` before the check, so a refused call leaves the
store byte-identical — including inside an open `batch()`, where a refusal
neither commits nor dirties anything, and the batch still exits normally and
commits whatever legitimately preceded it. `add_object` checks after its
existing type/duplicate-id guards and before the insert.

The refusal is immediate and has no transient-collision exemption inside a
batch (R3): the in-memory scene *is* what a direct reader sees. `set_held_by`'s
docstring now states the ordering requirement (clear, then set) at the call
site, and notes it is the order the physical robot must use anyway.

`update_object_pose` preserves `held_by`, so it cannot introduce a conflict and
gained no check. `_load` gained none either (R4): it accepts only a
`WorldDocument`, which after Layer A cannot exist in a violating state — pinned
by a test rather than by a comment.

## Criterion 2 — the seed (R7, R8)

`WorldStore.__init__` conflated "the scene to load" with "the scene `reset()`
restores". For a `FileWorldStore` reopening a live file those are different
documents, so the inherited `_seed` held the *live* scene as read at startup.

`__init__` now takes a keyword-only `seed: WorldDocument | None = None`,
defaulting to `document`, type-checked exactly like `document`
(`TypeError: seed must be a WorldDocument, got ...`). `FileWorldStore` passes
`super().__init__(document, seed=seed)` with the seed it has already read from
disk. Every existing caller — `WorldStore()`, `WorldStore(doc)`,
`MockBackend`'s `WorldStore(world_to_document(seed))` (`mock_backend.py:140`),
`robot_mcp`'s `FileWorldStore(...)` (`server.py:404`), and every test — is
unchanged in behaviour: with no `seed`, `_seed` is `document`, as before.

**No deep copy** (R7): `WorldDocument` is frozen all the way down
(`MappingProxyType` of locations, tuple of frozen `WorldObject`s holding frozen
`Pose`s), so a copy would buy nothing and imply a mutability that does not
exist.

**The disk re-read stays** (R8). `FileWorldStore.seed_document()` still calls
`read_seed_document(self._seed_path)`; its docstring now says why (it is the
D23 mechanism — replacing the seed *file* must change what `reset()` restores,
which `test_reset_restores_from_the_seed_file_not_from_memory` pins) and what
the honest `_seed` buys: collapsing the override would no longer restore a
drifted live scene as ground truth, it would merely stop following the file.

## Tests (R9)

All in `src/robot_world/test/`, matching the local conventions (sentence-shaped
names, one-line prose docstring, `conftest.py` fixtures, `pytest.raises(...,
match=...)`). 11 new test functions/cases; package total 53 → 64.

| R9 | test | file |
| --- | --- | --- |
| 1 | `test_a_reopened_store_resets_to_the_seed_not_to_the_scene_it_opened` | `test_file_store.py` |
| 2 | `test_mutating_the_working_scene_never_changes_what_reset_restores` | `test_file_store.py` |
| 3 | `test_the_seed_is_not_disturbed_by_anything_the_store_does`, `test_a_store_can_be_told_its_seed_separately_from_its_scene` | `test_store.py` |
| 4 | new cases in `test_scene_invariants_are_enforced_at_the_parse_boundary` (parsed *and* constructed), plus `test_one_object_per_gripper_is_all_the_rule_forbids` | `test_document.py` |
| 5 | `test_a_gripper_cannot_be_given_a_second_object_to_hold`, `test_a_refused_hold_inside_a_batch_leaves_the_batch_intact` | `test_store.py` |
| 6 | `test_a_new_object_cannot_arrive_in_a_full_gripper` | `test_store.py` |
| 7 | `test_a_live_file_holding_one_object_in_two_grippers_is_refused` | `test_file_store.py` |
| 8 | `test_a_hold_changes_hands_by_clearing_it_first` | `test_store.py` |
| R4 | `test_a_conflicting_scene_cannot_reach_a_store_at_all` | `test_store.py` |

Two choices worth flagging:

* **The headline seed test reads `_seed` through the base method**, not through
  the attribute: `assert WorldStore.seed_document(second) == document`. That is
  precisely the future-maintainer scenario (the override collapsed into
  `return self._seed`) expressed through public API, and it is the assertion
  that fails without the fix. Asserting on `second._seed` directly would test
  the same thing while reaching into a private.
* **The batch test asserts recovery, not internals**: after the refusal inside
  the batch, the next mutation commits immediately (`commits == [1, 1]`), which
  is the observable proof `_batch_depth` came back to zero; and a batch whose
  only call is refused commits nothing at all, which proves `_pending` was never
  set.

### Anti-tautology checks (each run, then reverted)

* Swapping `set_held_by` to mutate-then-raise (`_replace` before
  `_refuse_hold_conflict`): **2 failures** —
  `test_a_gripper_cannot_be_given_a_second_object_to_hold` and
  `test_a_refused_hold_inside_a_batch_leaves_the_batch_intact`. So the tests
  discriminate check-then-mutate from mutate-then-raise, not merely "it
  raises".
* Reverting `super().__init__(document, seed=seed)` to `super().__init__(document)`
  (i.e. reintroducing the criterion-2 bug): **1 failure** —
  `test_a_reopened_store_resets_to_the_seed_not_to_the_scene_it_opened`, at the
  `WorldStore.seed_document(second)` assertion. Noted: the R9.2 test
  (`..._never_changes_what_reset_restores`) passes either way, because on a
  *fresh* live file `document is seed` already; it documents the issue's stated
  guarantee but the headline test is the one that discriminates.
* Before the change, a probe confirmed `WorldDocument(...)` accepted two
  objects both `held_by=Side.LEFT` and `set_held_by` created the collision
  silently; after, both are refused (message quoted above).

## Blast radius (R10)

`robot_backends` and `robot_mcp` were **run, not edited**:

* `robot_backends`: 77 passed.
* `robot_mcp`: 85 passed.
* Full suite: `pixi run build` (9 packages, exit 0) then `pixi run test` —
  **705 tests, 0 errors, 0 failures, 0 skipped**, integrity audit `AUDIT
  PASSED`, `robot_world +11` vs `scripts/test_baseline.json` (a ratchet gain;
  the baseline file needs no re-cut and was not touched). Logs in
  `.dev/runs/i58-robot-world-correctness-gaps/20260812T214715/`.

**`mock_world.py` traced** (context.md §6 flagged it as inferred-but-not-traced):
`world_to_document` (`mock_world.py:220-242`) builds each `WorldObject` from an
`ObjectSpec` with `object_id`/`label`/`pose`/`graspable` only — `held_by` is
never passed, so it is always `None` and the function cannot synthesize a
conflict. `world_from_document` goes the other way and drops `held_by`
entirely. `ObjectSpec` has no `held_by` field at all. **No escalation needed.**

The other direction — could the new invariant fire on a legal `MockBackend`
path? `_grasp` refuses an already-held object and resolves a *free* gripper via
`_resolve_grasping_side`/`_require_free_gripper` before calling the store;
`_place`/`_open_gripper`/`_release_persisted_holds` only ever clear. Gripper
book-keeping and store `held_by` are set and cleared together, and `_power_on`
clears both, so they cannot diverge. Confirmed by both suites staying green.

## Escalations

None. No ruling looked wrong in implementation.

## Surprises / notes for the manager

* **`Side` is not orderable.** It is a plain `Enum` (`skills.py:73-78`), so the
  sibling check's `sorted({...})` idiom does not transfer. Iterating `Side`
  gives a deterministic, complete order without a key function.
* **`duplicate_hold_sides` reports *every* duplicated side**, so a naive
  truthiness test in the store would fire on a conflict the caller did not
  create and then look for a holder on the wrong side (a `StopIteration`
  escaping a normal method). Not reachable through the public API — the
  registry is conflict-free inductively — but the guard now asks whether *the
  claimed side* is among the duplicates, which keeps the refusal about the call
  being made and makes the holder lookup total. That is commit `6a11e15`.
* **R1's "single source of ... the message wording"** is satisfied for the
  *rule*, not literally for the string: Layer A describes a scene
  ("WorldDocument.objects has more than one object held by the same gripper:
  left") while Layer B refuses a call and names the blocking object and the fix.
  R1's own example signature (`-> list[Side]`) implies the message is formatted
  at the call site, and one shared phrase — "held by the same gripper" — is what
  both tests match on.
* **Export decision:** `duplicate_hold_sides` was added to both
  `document.__all__` and the package `__init__` export list, per the manager's
  "follow the existing export list" note. `store.py` imports it from
  `robot_world.document` directly, so the package-level export exists for
  consumers (the query service), not for internal wiring — a reviewer who
  considers that speculative can drop the `__init__` line without touching
  anything else.
* **No surviving red-team NOTEs yet** (no red-team pass at time of writing).
