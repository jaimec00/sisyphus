# Implementation: robot_world — two correctness gaps (#58)

Owned paths: `src/robot_world/` only (R10). Nothing outside it was edited.

## Commits

| sha | what |
| --- | --- |
| `e01d68c` | criterion 1 — `held_by` uniqueness, both layers, with tests |
| `a777eef` | criterion 2 — `_seed` genuinely holds the seed, with tests |
| `6a11e15` | follow-up hardening of the store-layer check (see "Surprises") |
| `f34e998` | round-1 review fixes N1–N3 (see "Round 1 review fixes") |
| `a7012b5` | round-2 review fixes N7–N9 (see "Round 2 review fixes") |

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
`read_seed_document(self._seed_path)`; its docstring says why — it is the D23
mechanism, replacing the seed *file* must change what `reset()` restores, which
`test_reset_restores_from_the_seed_file_not_from_memory` pins — and notes that
`__init__` obtains the seed through it, before `_seed` exists. (The docstring
originally also claimed a collapsed override would "quietly stop following the
file"; that was wrong and was corrected in round 1 — see N1 below.)

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
  the attribute: `assert WorldStore.seed_document(second) == document`. It is
  the assertion that fails when `seed=` is dropped from
  `super().__init__`, expressed through public API; asserting on `second._seed`
  directly would test the same thing while reaching into a private. (An earlier
  draft of this bullet and of the test's docstring justified it as catching a
  *collapsed override* — that was the N1/N7 falsehood: the collapse cannot be
  written, and the refactor that can is caught by the pre-existing
  `test_reset_restores_from_the_seed_file_not_from_memory`.)
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

## Round 1 review fixes (`f34e998`)

No BLOCKs were found. Three of the six NOTEs were scoped in by the manager; the
other three (N4, N5, N6, plus the `_grasp` ordering) are out of scope here and
are being routed to the issue.

**N1 — the `seed_document()` docstring predicted a failure that cannot happen.**
It claimed collapsing the override into `return self._seed` would "quietly stop
following the file". False: `FileWorldStore.__init__` calls the *virtual*
`self.seed_document()` (store.py:348) **before** `super().__init__` assigns
`_seed` (store.py:354), so the naive collapse dies at construction. Probe P1,
run:

```
AttributeError: 'FileWorldStore' object has no attribute '_seed'
```

The docstring now gives the real reason the re-read stays — the seed is a
*file*, and replacing it must change what `reset()` restores (an operator
re-seeding a running robot), which is what the pre-existing
`test_reset_restores_from_the_seed_file_not_from_memory` pins — and notes that
`__init__` consumes `seed_document()` before `_seed` exists, so this is not an
attribute read waiting to happen. Docstring only; no behaviour changed.

For the record, the two failure modes are pinned from opposite directions:
dropping `seed=` is caught by the **new** tests; the *plausible* refactor
(`__init__` re-reads directly, `seed_document()` returns `self._seed`) is caught
by the **pre-existing** test, and passes the new one — precisely because R7 made
`_seed` honest.

**N2 — criterion 2 hung on a single assertion.** `WorldStore.seed_document(second)
== document` was the only assertion in the suite that failed when `seed=` was
dropped, so deleting one line un-pinned the criterion. Now
`test_mutating_the_working_scene_never_changes_what_reset_restores` also reopens
a store over the drifted live file and asserts
`WorldStore.seed_document(reopened) == document`, and the headline test asserts
`WorldStore.seed_document(second) != drifted` alongside the equality so it
states both halves of the claim. Re-ran probe P2: dropping `seed=` now fails
**two** independent tests (was one).

**N3 — the ordering assertion could not discriminate.** The input's
first-appearance order was also `LEFT, RIGHT`, so a first-appearance
implementation would have passed. Input flipped to `RIGHT, RIGHT, LEFT, LEFT`,
expectation unchanged (`[Side.LEFT, Side.RIGHT]`); passes, so the assertion now
genuinely pins the declaration-order guarantee the two-side message depends on.

### Red-team probes, run

| probe | result |
| --- | --- |
| P1 (N1's `AttributeError`) | **confirms N1** — `AttributeError ... '_seed'` at construction; applying the collapse for real fails **21 of 64** package tests, loudly and immediately |
| P2 (N2's single-assertion thinness) | after the fix, dropping `seed=` fails 2 tests, not 1 |
| P3 (N3's ordering) | passes with the reversed input — free strengthening, no bug |
| P4 (N4's redundant write) | **confirms N4** — `Side.LEFT` then `'left'`: writes 1 → 2. Out of scope; for the issue |
| P5 (`StopIteration` unreachability) | `ok` — 6561 `set_held_by` sequences over 3 objects, nothing escaped, every resulting scene re-validated through Layer A |

Also established while there: **`deepcopy(WorldDocument)` raises `TypeError:
cannot pickle 'mappingproxy' object`.** So the deep copy the issue proposed is
not merely unnecessary (R7) — it is impossible without first unwrapping the
proxy, which would mean building a *less* immutable document to copy it.

## Round 2 review fixes (`a7012b5`)

No BLOCKs. The round-2 pass ran the "where else is this claim repeated" sweep on
the N1 fix and found the deleted sentence still living in two other places, plus
one assertion of mine that carries no weight. Docstrings, one comment and one
deleted assertion; **no behaviour change**.

**N7 — the sentence N1 removed from `store.py` survived in a test docstring.**
`test_a_reopened_store_resets_to_the_seed_not_to_the_scene_it_opened`'s
docstring still presented "someone collapses `seed_document()` into `return
self._seed`" as the coming refactor *and* implied this test is what catches it.
Both halves are wrong after `f34e998`: that collapse dies at construction (P1),
and what this test actually catches is a dropped `seed=`. Reworded to say what
it pins — `FileWorldStore` hands the true seed to `super().__init__` rather than
letting `_seed` default to the live document — and to point at
`test_reset_restores_from_the_seed_file_not_from_memory` for the seed-file
re-read. The repo no longer contradicts itself about this mechanism.

**N8 — the module docstring described the pre-`seed=` API.** `store.py:18-19`
said a `WorldStore`'s `reset()` "returns to the document it was built from",
which `test_a_store_can_be_told_its_seed_separately_from_its_scene` asserts is
not so. Now: "returns to its seed, which is the document it was built from
unless one is passed separately" — the wording of `__init__`'s own docstring.
Round-1 collateral that both earlier passes missed; it is the first thing a
reader of the module sees.

**N9 — dropped my tautological assertion.** `assert
WorldStore.seed_document(second) != drifted` cannot fail given the two
assertions above it (`drifted != document`, `seed_document(second) == document`)
and dataclass-generated equality — confirmed: `document.py` defines no custom
`__eq__` (probe Q5). It read as a second guarantee while catching nothing, and
its real cost is a maintainer deleting the assertion that *does* discriminate
and keeping this one. The `reopened` block in the other test is the genuine
strengthening and stays. The red team owns this one — its N2 "alternatively"
branch proposed it, and I took both halves.

### Round-2 probes, run

| probe | result |
| --- | --- |
| Q1 (`f34e998` on `store.py` was docstring-only) | **confirmed** — the only hunk is inside `seed_document`'s docstring; `return read_seed_document(self._seed_path)` untouched |
| Q2 (lint/pep257/copyright) | green, in every package run below |
| Q3 (N2's "two tests") | **exactly two** fail on reverting `seed=`, still true after N9's deletion: the reopened-store test and the mutate-then-reset test |
| Q4 (N3 discriminates) | **confirmed** — a first-appearance `duplicate_hold_sides` now fails `test_one_object_per_gripper_is_all_the_rule_forbids` with `[Side.RIGHT, Side.LEFT] != [Side.LEFT, Side.RIGHT]` |
| Q5 (N9's tautology) | **confirmed** — no custom `__eq__` in `document.py`, so equality is transitive and the assertion was implied |

I did **not** add a test pinning "the collapse cannot be written", per the red
team's explicit recommendation: it would pin a non-behaviour and make a
legitimate future restructuring of `__init__` fail for the wrong reason.

## Escalations

None. No ruling looked wrong at implementation time; R8's stated rationale was
corrected after the review (see N1 above and the correction in `status.md`).

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
* **Surviving red-team NOTEs** (not fixed here, for the manager to route to the
  issue): **N10** — `document.py`'s module docstring points at
  `robot_world.store` for the "a backend clears persisted holds at power-on"
  behaviour, which actually lives in `MockBackend._release_persisted_holds`
  (pre-existing cross-reference rot). **N11** — `store.py` names a test
  *function* in shipped source; the red team explicitly recommends **not**
  acting (the reference earns its keep, and there is module-granularity
  precedent in `schemas.py`/`serialization.py`). **N4** — `set_held_by(id, 'left')` after `set_held_by(id, Side.LEFT)`
  bypasses the `item.held_by == side` short circuit (`Side` has no `str` mixin)
  and costs a redundant whole-document write; pre-existing, confirmed by probe
  P4. **N5** — D23 gains a semantic startup-failure class the decision log does
  not record (`decisions.md` is not an owned path). **N6** — `MockBackend.store`'s
  docstring still says a desync shows up on the next `get_observation()`; it now
  raises at the call (`robot_backends`, out of scope per R10), as does the
  `_grasp` mutate-then-raise ordering it flagged.
