# Implementation — world-state store (issue #54, D23)

What landed, why it is shaped this way, and where the seams are. Rulings R1–R10
in `status.md` were followed as written; the two places where I made a judgment
call inside a ruling are flagged under **Deviations / judgment calls**.

## 1. What was built

### New package: `src/robot_world/` (pure Python, no ROS)

| module | holds |
|---|---|
| `document.py` | `WorldObject`, `WorldDocument` — the one JSON schema, plus `WORLD_SCHEMA_VERSION` / `WORLD_SCHEMA_VERSION_KEY` / `check_world_schema_version()` |
| `storage.py` | `read_document`, `read_seed_document`, `default_seed_document`, `document_text`, `write_document` (atomic), `WorldStoreError` |
| `store.py` | `WorldStore` (in memory) and `FileWorldStore` (live file + seed file) |
| `default_world.json` | the shipped seed: the demo apartment, 4 locations + 7 objects |

Plus the standard package furniture (`package.xml`, `setup.py` with
`package_data={package_name: ['*.json']}`, `setup.cfg`, `pytest.ini`,
`resource/robot_world`, `README.md`) and a test suite of 48 non-linter tests
including a `test_no_ros_runtime.py` scoped to this package.

### Changed: `robot_backends`

- `mock_world.py` — `default_world()` now loads the shipped seed document
  instead of building a literal; new `world_from_document()` /
  `world_to_document()` converters. `MockWorld`, `ObjectSpec`, `RobotModel`
  unchanged and still exported (R10).
- `mock_backend.py` — the scene moved into a `WorldStore`. `_MockObject` is
  gone; the backend keeps `_MockGripper`, proprioception (`_base_pose`,
  `_location`, `_column_height`) and every bit of reach arithmetic, and calls
  `store.update_object_pose()` / `store.set_held_by()` where it used to mutate
  a dict. New constructor: `MockBackend(world=None, *, store=None)`; new
  `store` property; `world` property now rebuilds a `MockWorld` from the
  store's **seed** plus the backend's `RobotModel`.

### Changed: `robot_mcp`

`main(argv=None)` parses `--world-state PATH` / `--world-seed PATH` with
`ROBOT_WORLD_STATE` / `ROBOT_WORLD_SEED` as env fallbacks (flag beats env);
`backend_from_options()` turns them into a `MockBackend` over a
`FileWorldStore`, or `None` (today's in-memory Mock) when no live path is
given. `--world-seed` without `--world-state` is an `argparse` error.

### Docs

`docs/design/decisions.md` gains **D23**; `docs/design/PROJECT.md` roadmap
step 3 is struck through with what landed and what is still to come, and the
"two memories" line at :43 gains a pointer to `robot_world`.

## 2. Design, and the tradeoffs taken

### One document, two files, one writer

`WorldDocument` is a frozen dataclass implementing `robot_skills`'
`JsonSerializable`, so the seed and the live file are literally the same
format; `reset()` is "parse the seed file, adopt it, write it out". One parser,
one writer, one golden file. `Pose.to_dict`/`from_dict` and the
`check_keys`/`get_float`/`get_bool`/`get_optional_enum`/`parse_errors` helpers
are reused verbatim — no hand-rolled JSON anywhere (R5, invariant 5).

**Object order is preserved, not sorted.** Sorting inside `__post_init__` would
have made the round-trip contract trivially true, but it would also churn a
curated seed file's grouping and would have changed `default_world().objects`
order against today's. `Observation` already sorts by `object_id` at the point
where a canonical order actually matters.

**Its own version counter** (`world_schema_version`) with its own key: the
disk format talks to the robot's own past self, D18's `SCHEMA_VERSION` talks to
the brain, and they must be free to move separately. A test asserts the key is
*not* `schema_version` and that no skill-API stamp leaks into a world document.

### Store vs. backend: the split that has to survive MuJoCo

The store answers *what the world contains*; the backend answers *how a skill
changes it*. Concretely, the store has no idea what a gripper, a reach radius
or a failure code is; the backend has no idea what a file is. That is what
makes the store the thing the Sim backend inherits rather than something it has
to work around (R1/R2, invariant 2).

The one place this is subtle is `held_by`, which the wire format demands be
consistent with the gripper's `held_object_id` (`Observation` raises if they
disagree). The store *records* it, the backend *decides* it — including at
power-on, where the backend clears every persisted hold because its grippers
came up empty (R2). Objects keep their last persisted pose, which is a normal
Mock state (the Mock has no gravity, and `Place` checks reach, not support).
This also makes a crash mid-skill safe: if only the pose reached disk, the
stale `held_by` is dropped on the next load anyway.

### Atomicity: two levels

1. **The write.** `write_document()` serializes first (so a bad document fails
   before touching the filesystem), then `tempfile.mkstemp(dir=<target's own
   directory>)`, writes, and `os.replace`s. Same directory is load-bearing:
   `os.replace` is only atomic within a filesystem. Any failure unlinks the
   temp file, so a crashed write leaves neither a corrupt target nor litter.
   `OSError` becomes `WorldStoreError`; anything else propagates unchanged.
2. **The transaction.** `store.batch()` accumulates mutations and commits once
   on exit. `MockBackend.execute()` wraps the handler *and*
   `_carry_held_objects()` in one batch, so a skill that moves up to three
   objects is **one** `os.replace`. Outside a batch, every mutation
   autocommits. A no-op mutation (same pose, same holder) commits nothing.

A batch commits on **exceptional** exit too. The alternative — skipping the
write — would leave the file silently disagreeing with what the caller can
already read back from memory, and the store has no in-memory rollback to make
"skip the write" honest. In practice this never fires for a refused skill,
because every handler validates before it mutates; a test pins that a refusal
writes nothing.

### Persistence is opt-in, in both places (R7/R8)

`MockBackend()` builds an in-memory store; `python -m robot_mcp` with no
options passes `backend=None`. Two tests hold this down by *failing* if
`write_document` is ever called. This is not conservatism: ~12 call sites
construct a bare `MockBackend()`, and `test_stdio_transport.py` compares a
spawned server against a fresh in-process `MockBackend()` — a default-on
persisted world would cross-contaminate the first group and break the second on
its second run.

### Error policy (R6)

| situation | behaviour |
|---|---|
| live file missing | created from the seed, run continues |
| live file corrupt / schema-violating | `WorldStoreError`, file left **untouched** |
| seed file (or shipped resource) missing/corrupt | `WorldStoreError`, hard fail; no live file is created |
| unknown key, missing key, wrong type, foreign version | `SerializationError` → wrapped as `WorldStoreError` at the file boundary |

Never a silent repair: overwriting a corrupt live file with the seed destroys
the only evidence an operator has.

### Where the files live (R9)

Seed: inside the importable package, read through `importlib.resources`,
following `robot_brain/agent.py` and `robot_safety/limits.py`. Live state:
**always** a caller-supplied path, never an `importlib.resources` path (an
install location is not guaranteed writable) — and no `$HOME`/XDG default was
invented, because opt-in persistence means no default is needed.

## 3. Acceptance criteria → the tests that prove them

| criterion | test |
|---|---|
| 1. `get_observation()` reflects the live file | `robot_backends/test/test_mock_persistence.py::test_the_observation_reflects_what_is_in_the_live_file` (reads the file back with `read_document`, both directions) |
| 2. fresh process sees the mutation | `robot_mcp/test/test_world_state_persists.py::test_a_fresh_process_sees_the_previous_run_mutation` — two spawned `python -m robot_mcp --world-state PATH` processes over one file; plus the in-process minimum in `test_mock_persistence.py::test_a_second_backend_over_the_same_file_sees_the_mutation` |
| 3. `reset()` restores from the seed | `test_mock_persistence.py::test_reset_restores_the_scene_from_the_seed_file`, `robot_world/test/test_file_store.py::test_reset_restores_from_the_seed_file_not_from_memory` (rewrites the seed file mid-run and shows `reset()` follows the *file*), and `test_world_state_persists.py::test_the_reset_tool_restores_the_seed_across_processes` |
| 4. atomic writes | `robot_world/test/test_atomic_write.py` — failure injected between temp-write and `os.replace` (pre-existing file byte-identical and parseable, no temp left), failure injected *during* the temp write, temp proven co-located with its target, happy path leaves no litter |
| 5. seed reproduces `default_world()`; suites stay green | `robot_world/test/test_default_seed.py` (longhand pin of all 4 locations + 7 objects, and the file being exactly what a write would emit), `robot_backends/test/test_mock_world.py::test_default_world_is_exactly_the_shipped_seed_document`; every pre-existing test passes unmodified |
| 6. D23 + roadmap | `docs/design/decisions.md`, `docs/design/PROJECT.md` |

Also covered: the `JsonSerializable` round-trip contract, strict-parsing error
paths (R5), the missing/corrupt live and seed cases (R6), batch-vs-autocommit
write counts, the power-cycle release, the store/world precedence split, and
CLI/env option precedence.

Full suite: **672 tests, 0 failures** (`pixi run test`). Baseline re-cut:
`robot_world` 48 (new), `robot_backends` 60 → 73, `robot_mcp` 71 → 80. No test
was removed or moved.

## 4. Deviations / judgment calls

1. **`store.object(id)` is named `find_object(id)`.** The issue sketched
   `object(id)`, but `ament_flake8` rejects it (`A003: class attribute
   "object" is shadowing a Python builtin`) and the repo already has exactly
   this lookup as `Observation.find_object(object_id)`. Naming it the same
   thing at both layers seemed better than a `# noqa`.
2. **`MockBackend.world` returns the *seed* world, not the live scene.**
   R7 says "reconstructed from the store's scene"; I read the ruling's
   *purpose* — "so its documented contract still holds" — as decisive, and the
   documented contract is "the world this backend was seeded from … `reset()`
   always returns to that same seed world". With a file store those two differ
   after the first mutation, and returning the live scene would have made
   `backend.world` mean something new. It is built from
   `store.seed_document()` + the backend's `RobotModel`. The live scene is
   available as `backend.store` / `get_observation()`. Flagging it because it
   is the one place I chose the ruling's rationale over its literal wording.
3. **`MockBackend.__init__` no longer calls `reset()`.** It calls `_power_on()`
   (home the robot, release persisted holds). Calling `reset()` would have
   wiped the live-state file on every construction and made acceptance
   criterion 2 unachievable. `reset()` is `store.reset()` + `_power_on()`,
   batched into one write.
4. **`MockBackend` validates the store's `start_column_height` against its own
   `RobotModel`** at power-on, raising `ValueError`. `MockWorld` used to do
   this; with an injected store there is no `MockWorld` in the path, and a
   world file that puts the robot outside its own column travel should fail at
   startup, not mid-chore. `WorldDocument` itself does **not** check it — the
   column range is the robot's, not the room's (R4).

## 5. Known gaps (deferred with rationale; carried in code + README)

- **DEF-1 — no cross-process locking.** Two processes on one live file race on
  `os.replace`: last writer wins, no corruption, possible lost update. Stated
  in `storage.py`'s module docstring and both READMEs rather than assumed away.
- **DEF-3 — no `fsync`.** Atomic against a crashed process, not durable against
  a power cut with a dirty page cache.
- **DEF-2 — the map is read-only.** `locations()` only; no skill mutates the
  map yet.
- **No ROS 2 query service** and **no perception writer** — both explicit
  non-goals here; `PROJECT.md`'s roadmap line now names them as what remains.

## 6. Surviving red-team NOTEs

None yet (no red-team round has run at the time of writing). Any that survive
the round will be recorded here and in `status.md` for the manager to relay.
