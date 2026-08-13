# Status: robot_world — two correctness gaps (#58)

- **Phase:** manager rulings recorded → dispatching implementer
- **Round:** 0 (no red-team pass yet)
- **Blockers:** none
- **Branch:** `feat/i58-robot-world-two-correctness-gaps-to-clos`
- **Owned paths:** `src/robot_world/` only
- **New dependencies:** none (step-2 provisioning is a no-op for this feature)

## Manager rulings (binding; escalate in-process if you believe one is wrong)

Context: `docs/features/i58-robot-world-correctness-gaps/context.md`, open
questions 1–8. Each ruling below answers one or more of them.

### Criterion 1 — `held_by` uniqueness

**R1 (Q1) — Define the invariant once, enforce it at two layers.**
Add a module-level helper in `document.py` (single source of truth for both the
rule and the message wording) — e.g.
`duplicate_hold_sides(objects: Iterable[WorldObject]) -> list[Side]` — and call
it from both layers below. Do **not** hand-write the scan twice; two copies of
an invariant drift.

- **Layer A — `WorldDocument.__post_init__`.** Mirrors the existing
  duplicate-`object_id` check (document.py:203-207) in placement, shape
  (`sorted({...})`, `", ".join(...)`) and wording. Put it immediately after
  that check. This closes construction, `from_dict`, and every on-disk load
  for free.
- **Layer B — `WorldStore.set_held_by` and `WorldStore.add_object`.** Check
  **before** mutating `self._objects`; never mutate-then-raise. Layer A alone
  is *not* sufficient: the store's live state is a plain `dict`, and
  `.document()` is only built on demand — so a check that lives only in
  `__post_init__` would let `set_held_by` leave the in-memory registry
  inconsistent until the next `document()`/commit. The issue's stated failure
  mode is *a query service reading the store directly*, i.e. via
  `objects()`/`find_object()`, which never goes through `document()`. Layer B
  is the one that actually closes the issue; Layer A is the ground-truth
  definition and the parse-boundary net.

**R2 (Q2) — Exception types: reuse both existing idioms, invent nothing.**
`ValueError` from Layer A (matches `WorldDocument.__post_init__`);
`WorldStoreError` from Layer B (matches `add_object`'s "already holds an
object"). No new exception class. Both are `ValueError` subclasses, so a caller
catching `ValueError` is correct either way.

**R3 (Q3, Q6) — Fires per-mutation, immediately, including inside `batch()`.**
No deferral to `document()`/commit, and no transient-collision exemption
mid-batch. `batch()` defers only the *disk write*; the in-memory scene changes
on each call, and that in-memory scene is exactly what a direct reader sees —
exempting it would reopen the window this issue exists to close. A caller that
genuinely needs to move a hold clears first
(`set_held_by(a, None)` then `set_held_by(b, side)`), which is also what the
physical robot must do: you cannot grasp with a full gripper. Say this in
`set_held_by`'s docstring so the ordering requirement is discoverable at the
call site. No current call path in `src/` produces even a transient collision
(context.md §6).

**R4 — `_load` gets no separate check.** It accepts only a `WorldDocument`,
which after R1 Layer A cannot exist in a violating state. Do not add a
redundant scan there; do add a test that pins this reasoning (a violating
document cannot be constructed, therefore cannot be `_load`ed).

**R5 (Q4) — A pre-existing on-disk violation is refused loudly, never
repaired.** This falls out of Layer A: `from_dict` → `SerializationError` →
`read_document` → `WorldStoreError`. It matches `storage.py`'s stated stance
("A corrupt world file is always this, never a silent repair") and the existing
`test_a_schema_violating_live_file_is_refused`. Accepted consequence: such a
live file must be repaired or deleted by hand rather than recovered by
`reset()`, because construction raises first — identical to the already-accepted
corrupt-live-file behaviour. Not reachable in practice today (`MockBackend`
clears all holds at power-on).

**R6 (Q5) — Semantics confirmed, not a design choice.** At most one object with
`held_by == Side.LEFT` and at most one with `held_by == Side.RIGHT`, across the
whole registry. `Side` has exactly two members and there is no other gripper-id
concept. Any number of objects may have `held_by is None`.

### Criterion 2 — the seed

**R7 (Q7) — No deep copy. `WorldDocument` is already deeply immutable;
the defect is that `_seed` points at the wrong document.**
Verified in `document.py`: `__post_init__` normalizes `locations` to
`MappingProxyType(dict(...))` and `objects` to a `tuple`, and `Pose`/
`WorldObject` are frozen dataclasses. So the issue's "deep copy" option is
moot — copying would add cost and imply a mutability that does not exist.
**Do not add a deep copy.** The real bug is `WorldStore.__init__` assigning
`self._seed = document` when `FileWorldStore` passes it the *live* document.

Fix: give `WorldStore.__init__` a keyword-only `seed: WorldDocument | None =
None`, defaulting to `document`, type-checked the same way, documented as "the
scene `reset()` restores; defaults to `document`." `FileWorldStore.__init__`
then passes `super().__init__(document, seed=seed)` with the seed it already
read from disk. After this, `self._seed` is honest for both store flavours.

**R8 — Keep `FileWorldStore.seed_document()`'s disk re-read. Do not
"simplify" it into `return self._seed`.** It is the literal D23 mechanism
("`reset()` re-reads the *seed file*"), not an optimization: the seed file can
be replaced on disk after construction, and
`test_reset_restores_from_the_seed_file_not_from_memory` pins exactly that
behaviour. R7 makes `_seed` correct as defense-in-depth so that a *future*
maintainer who does collapse the override gets correct behaviour anyway — it
does not replace the re-read. Keep both; say why in the override's docstring.

> **Corrected after the round-1 review (N1).** The last claim was wrong, and the
> shipped docstring inherited it. `FileWorldStore.__init__` calls the *virtual*
> `self.seed_document()` **before** `super().__init__` assigns `_seed`, so the
> naive collapse into `return self._seed` does not "get correct behaviour
> anyway" and does not silently drift either — it dies at construction with
> `AttributeError: 'FileWorldStore' object has no attribute '_seed'` (probe P1,
> run: confirmed; applying the collapse for real fails 21 of the package's 64
> tests — every `FileWorldStore` construction, plus `test_no_ros_runtime.py`).
> What is actually pinned,
> from two directions:
> * dropping `seed=` from `super().__init__(document, seed=seed)` → the **new**
>   tests fail (two of them, after round-1 fix N2);
> * the *plausible* refactor (re-read in `__init__`, `seed_document()` returns
>   `self._seed`) → the **pre-existing**
>   `test_reset_restores_from_the_seed_file_not_from_memory` fails, because it
>   replaces the seed *file* after construction and demands `reset()` follow it.
>
> So the re-read must stay for the operator-re-seeds-a-running-robot reason,
> not for a defense-in-depth-against-collapse reason. The docstring now says
> that. R8's instruction (keep the re-read, do not collapse it) stands.

### Tests

**R9 — Minimum new coverage** (match existing conventions: sentence-shaped
names, one-line prose docstring, `conftest.py` fixtures, `pytest.raises(...,
match=...)`):

1. **The issue's headline seed test.** Store A over live+seed; mutate through A
   so the live file diverges from the seed; open store B over the same pair;
   `B.reset()`; assert B's scene equals the **seed file's** content and does
   *not* equal the live document B was constructed from. Assert
   `B.seed_document()` is the seed. This is the scenario context.md §3 shows is
   the only one that exposes the aliasing — the existing single-store reset test
   does not cover it.
2. Mutate through a store, then assert `seed_document()`/`reset()` still yield
   ground truth — "mutating the working doc does not change what `reset()`
   restores", stated as the issue words it.
3. In-memory `WorldStore(document)`: `seed_document()` is the seed after
   mutation.
4. Document layer: two objects claiming the same side → `ValueError`; add as a
   case in `test_scene_invariants_are_enforced_at_the_parse_boundary`.
5. Store layer: `set_held_by` collision → `WorldStoreError`, **and the store is
   unchanged afterwards** (first object still held, second still free) — this
   is what proves check-then-mutate rather than mutate-then-raise. Assert the
   same inside an open `batch()` (per R3), and that the batch's pending/commit
   bookkeeping is not corrupted.
6. `add_object` with a `WorldObject` already carrying a colliding `held_by` →
   `WorldStoreError`.
7. A live file on disk with two same-side holds → refused at load (R5).
8. Clearing (`set_held_by(..., None)`) is never refused, and a hold can be
   moved by clearing then setting.

### Scope

**R10 — `src/robot_world/` only.** Do not edit `robot_backends`, `robot_mcp`,
or `robot_skills`. But **do run** their suites as a blast-radius check (running
is not editing). If the new invariant fires anywhere outside `robot_world`,
**escalate to the manager in-process** — do not weaken the invariant and do not
edit out-of-scope files to accommodate it. context.md §6 flags
`robot_backends/mock_world.py`'s `world_to_document`/`world_from_document`
conversion as inferred-but-not-traced; trace it.

## Log

- Fetched `origin`; worktree at `c3718e1`, clean, branched from current
  `origin/main`.
- Read issue #58 (body present, two criteria, explicit de-scope list).
- Step 2 provisioning: no new third-party dependency → no-op, nothing installed,
  worktree left unmutated for the explorer.
- context-explorer ran (read-only) → `context.md`, 8 open questions.
- Manager rulings R1–R10 recorded above (this entry) — **before** implementer
  dispatch.
- Implementer: criterion 1 landed (`e01d68c`) — `duplicate_hold_sides` in
  `document.py` as the one rule (R1), enforced in `WorldDocument.__post_init__`
  (`ValueError`, R2/R5) and check-then-mutate in `set_held_by`/`add_object`
  (`WorldStoreError`, R2/R3). `_load` left unchecked per R4, pinned by a test.
- Implementer: criterion 2 landed (`a777eef`) — `WorldStore.__init__` takes a
  keyword-only `seed` defaulting to `document` (R7); `FileWorldStore` passes the
  seed it read from disk; the `seed_document()` disk re-read kept and documented
  (R8). No deep copy: `WorldDocument` is frozen all the way down.
- Implementer: `6a11e15` scopes the store-layer check to the side being claimed
  (a truthiness test on `duplicate_hold_sides` could fire on a conflict the
  caller did not create). Details in `implementation.md`.
- Tests: R9.1–R9.8 plus the R4 pin; robot_world 53 → 64 tests. Both fixes were
  verified by reverting them and watching the right tests fail (details in
  `implementation.md`).
- Blast radius (R10, run-not-edit): `pixi run build` clean;
  `pixi run test` = **705 tests, 0 failures**, audit passed, `robot_world +11`
  vs baseline (no baseline re-cut needed). `mock_world.py`'s
  `world_to_document`/`world_from_document` traced: neither ever sets `held_by`,
  so no conflict can be synthesized — nothing to escalate.
- No escalations. Surviving NOTEs: none yet (no red-team pass).
- Round 1 review: no BLOCKs, six NOTEs (`red_team.md`). Implementer fixed the
  three the manager scoped in — N1 (docstring told a failure mode that cannot
  happen), N2 (criterion 2 hung on one assertion), N3 (ordering assertion could
  not discriminate) — in `f34e998`. R8 above corrected in place.
- Probes run for the record: **P1** confirms N1 (`AttributeError` at
  construction, not a quiet drift); **P2** now fails **two** tests instead of one
  after the N2 fix; **P3** passes with the reversed input (free strengthening);
  **P4** confirms N4 (`Side.LEFT` then `'left'` → writes 1 → 2, a redundant disk
  write; out of scope, routed to the issue); **P5** exhausts 6561 `set_held_by`
  sequences over 3 objects with no escaping `StopIteration`/`ValueError`.
- Also established: `deepcopy(WorldDocument)` raises `TypeError: cannot pickle
  'mappingproxy' object` — the issue's proposed deep copy is not merely
  unnecessary (R7), it is impossible without unwrapping the proxy.
- Surviving NOTEs for the manager to route outward: **N4** (string `side`
  bypasses the no-op short circuit → redundant write; pre-existing), **N5** (D23
  amendment, `decisions.md` not an owned path), **N6** (`MockBackend.store`
  docstring) and the `_grasp` mutate-then-raise ordering (both `robot_backends`,
  out of scope per R10). Not touched by the implementer.
