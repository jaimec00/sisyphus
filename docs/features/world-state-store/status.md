# status — world-state-store (issue #54)

- **Branch:** `feat/i54-world-state-store-json-disk-persisted-ma` (from `origin/main` @ `b8e07a0`)
- **Phase:** red-team round 1 fixed → test-runner next
- **Round:** 0 (no red-team yet)
- **Blockers:** none

## Loop log

| step | agent | state |
|---|---|---|
| 0 sync | manager | done — clean worktree at `origin/main` `b8e07a0` |
| 1 brief | manager | done — issue #54 read, body non-empty, 5 locked design calls |
| 2 provision+probe deps | manager | **n/a** — no new third-party dependency. The brief mandates stdlib `json` and explicitly excludes PyYAML from the write path. Nothing to `pixi add`, nothing to probe. |
| 3 context | context-explorer | done — `context.md` (654 lines, 8 open questions) |
| 4 rulings | manager | done — R1–R10 below |
| 5 implement | implementer | done — `robot_world` package + backend/MCP wiring + D23; `pixi run test` green (672 tests, 0 failures); baseline re-cut. See `implementation.md` §4 for one judgment call inside R7 (`MockBackend.world` returns the *seed* world, not the live scene). |
| 6 red-team | red-team | round 1 done — 3 BLOCKs; all fixed by the implementer (BLOCK-1 env leakage, BLOCK-2 `_flush` ordering, BLOCK-3 false "opens no file" claim + weak test), plus promoted NOTE-3 (seed == live path refused) and NOTE-1 (`backend.store` docstring + pinning test). `pixi run test` green: 677 tests, 0 failures; baseline re-cut. Surviving NOTEs listed in `implementation.md` §7 for the manager to file — nothing mis-sorted. |
| 7 test-runner | test-runner | pending |
| 8 rebase + PR | manager | pending |

---

## Manager rulings (binding; escalate in-process if you believe one is wrong)

These are **binding but not assumed correct**. If implementing one of these
produces a bug, a contradiction, or a violation of a CLAUDE.md invariant,
**escalate to the manager in-process** — do not silently deviate, and do not
comply into a bug.

### R1 — Scope of persisted state: **map + object registry only. Robot proprio stays with the backend.**

The store persists the **scene**: named locations, the object registry, and the
robot's *start* parameters (`start_location`, `start_column_height`). It does
**not** persist live robot proprioception (current base pose, current column
height, gripper jaw state / offset / orientation).

*Rationale.* The roadmap line and D16 both name this store "map/objects", and
invariant 4 says perception emits *objects with coordinates* — perception never
emits the robot's column height. More decisively: `_MockGripper.offset` and
`.orientation` are **Mock-specific kinematic internals** (an offset from a
shoulder that only `RobotModel` defines). Writing them into the world file
would bake Mock internals into the store and make them dead weight the moment
the MuJoCo swap (#4) lands — an extensibility trap. The store must be the
layer that *survives* the backend swap.

This overrides `context.md` open question 1's recommendation (full snapshot),
and it is a deliberate, narrower reading of the issue's "persist enough that
`get_observation()` after a restart reflects reality": see R2 for what a
restart actually means.

### R2 — Restart semantics: a restart is a **power cycle**. Objects persist; the robot re-homes; `held_by` is cleared on load.

On loading an existing live-state file, the backend comes up at its **seed
posture** (base at `start_location`, column at `start_column_height`, both
grippers `OPEN` at `home_gripper_offset`, holding nothing). Therefore any
object carrying a persisted `held_by` is reconciled by **clearing `held_by`**,
while **keeping the object's last persisted pose**.

The clearing is the **backend's** job, performed through the store (and thus
persisted), not the store's: "my grippers are empty, so nothing is held" is a
statement about the robot, not about the world.

*Rationale.* `Observation.__post_init__` asserts that the object-side
`held_by` and the gripper-side `held_object_id` agree. Persisting `held_by`
without persisting gripper state would make `get_observation()` **raise** on
the first call after a restart — a hard crash, and exactly the failure
`context.md` open question 5 predicts. Clearing on load is the only
reconciliation consistent with R1. An object left mid-air is already a normal
Mock state (`Place` checks reach, not support; the Mock has no gravity), so
nothing incoherent results.

*Bonus property:* this makes a crash **mid-skill** safe. `Place` mutates pose
and `held_by` together; if only the pose reached disk, the stale `held_by` is
dropped on load anyway, so the recovered state is correct either way.

### R3 — One document schema, two files. `reset()` = seed → live.

There is **one** JSON world-document schema, used by both the read-only seed
file and the written live-state file. `reset()` re-reads the seed and
atomically overwrites the live-state file with it.

Document shape (indicative; the implementer owns exact key names):

```json
{
  "world_schema_version": 1,
  "start_location": "charger",
  "start_column_height": 0.3,
  "locations": {"charger": {"position": {...}, "orientation": {...}}},
  "objects": [
    {"object_id": "mug_1", "label": "mug", "pose": {...},
     "graspable": true, "held_by": null}
  ]
}
```

*Rationale.* One schema means one parser, one writer, one golden fixture, and
it makes "restore from seed" a file-level operation that is obviously correct.

### R4 — `RobotModel` is **not** world data and does not go in the file.

`RobotModel` (shoulder offsets, `reach_radius`, column travel limits) is
**robot hardware description**, not world state. It stays a Python value on
`MockWorld`, and later comes from the URDF/MJCF (roadmap step 5). The world
file describes the *scene*; it never describes the *robot's body*.

### R5 — The world file gets its **own** version stamp, independent of D18's `SCHEMA_VERSION`.

Stamp the document with `world_schema_version: 1`. It is a **separate
counter** from `robot_skills.serialization.SCHEMA_VERSION` and must not be
conflated with it: the brief's non-goals forbid a skill-API wire-format
change, and this is a new on-disk format with no relationship to the wire
format beyond sharing the JSON-safe-dict philosophy.

Parsing follows the repo's existing strictness: unknown keys rejected, a
*different* version is a loud error, an *absent* stamp reads as the current
version (mirroring `check_schema_version`). **Reuse
`robot_skills.serialization`** (`JsonSerializable`, `check_keys`, `get_float`,
`get_bool`, `get_enum`, `parse_errors`, `Pose.to_dict`/`from_dict`) rather
than hand-rolling parsing — invariant 5 (reuse) applies inside the repo too.

*Rationale.* D18's own precedent argues a stamp added after the fact is the
migration you did not want to do twice. It is one key now.

### R6 — Missing live-state file → seed it. **Corrupt** live-state file → fail loudly.

- **Missing** live-state file: create it from the seed and continue. This is
  the expected steady state for a fresh checkout / first run.
- **Corrupt or unparseable** live-state file: raise a dedicated error
  (e.g. `WorldStoreError`, a `ValueError` subclass). Never silently
  "repair" it by overwriting with the seed — that destroys the very evidence
  an operator needs, and it is the one case where the file is telling you
  something went wrong.
- **Missing or corrupt seed**: always a hard error. The seed is a shipped
  package resource; its absence is a broken install, not a runtime condition.

*Rationale.* Matches this repo's established stance (`SerializationError`,
`SafetyConfigError`, and `check_test_integrity.py`'s refusal to let a baseline
"quietly evaporate").

### R7 — `MockBackend()` with no arguments **stays pure in-memory**. Persistence is opt-in. *(highest-risk ruling — read the rationale)*

`MockBackend()` must behave **exactly as it does today**: no file is opened,
created, or written. A file-backed store is used only when one is explicitly
constructed and injected.

Ruled constructor: `MockBackend(world: MockWorld | None = None, *, store: WorldStore | None = None)`

- `store is None` → build an **in-memory** store from `world or default_world()`. Identical observable behaviour to today.
- `store` given → the **store is the scene** (locations, objects, start params);
  `(world or default_world()).robot` supplies the `RobotModel` (per R4).
  The `.world` property returns a `MockWorld` reconstructed from the store's
  scene plus that `RobotModel`, so its documented contract still holds.
  Document this precedence in the docstring and **test it**.

*Rationale.* This is the ruling most likely to be got wrong, so it is spelled
out. `src/robot_backends/test/conftest.py:16`, `src/robot_mcp/test/conftest.py:22,34`
and ~10 other call sites construct a bare `MockBackend()`. If that started
writing to a shared on-disk path, every one of those tests would
cross-contaminate through a single file, and the module's documented
determinism guarantee ("the same world plus the same sequence of skills
always produces the same observations, byte for byte") would be **false**.
Opt-in persistence keeps determinism intact and keeps the disk path explicit.

### R8 — `python -m robot_mcp` with no flag/env **stays in-memory**. Persistence is opt-in there too.

Add real plumbing to `robot_mcp` (none exists today — `main()` is
`anyio.run(run_stdio)`):

- `--world-state PATH` / env `ROBOT_WORLD_STATE` — the live-state file.
- `--world-seed PATH` / env `ROBOT_WORLD_SEED` — override the shipped seed.
- CLI flag beats env var; **neither set → in-memory, today's behaviour.**

*Rationale — this is not a preference, it is forced by an existing test.*
`src/robot_mcp/test/test_stdio_transport.py:42` spawns a real
`python -m robot_mcp` subprocess and asserts its tool results equal those of a
**fresh in-memory `MockBackend()`** (`:48`, `:61-65`). If the spawned server
defaulted to a persisted file, that test would (a) write into the developer's
`$HOME` and (b) **fail on its second run**, because the server would resume the
previous run's state — mug already moved — while the reference backend starts
clean. Defaulting persistence on would silently break an existing green test.

Acceptance criterion 2 ("a fresh MCP process against the same live-state
file") is fully served by the explicit `--world-state` form, and that is what
the new test must drive.

### R9 — Seed ships **inside** the importable package; the live-state file never does.

- **Seed:** ship at `src/robot_world/robot_world/<name>.json`, read via
  `importlib.resources`, following the two existing precedents
  (`robot_brain/agent.py:49-67` with `package_data` in `setup.py:12-13`, and
  `robot_safety/limits.py:363-365`). Do not put it under `resource/` — that
  directory is the ament index marker, not an asset directory.
- **Live state:** **never** an `importlib.resources` path — a package install
  location is not guaranteed writable. It is always a caller-supplied path.
  Tests must use `tmp_path`. No default under `$HOME` is introduced in this
  iteration (R8 makes persistence opt-in, so no default is needed); do not
  invent an XDG default now.
- The shipped seed must reproduce **today's `default_world()` scene exactly** —
  4 locations, 7 objects, the poses and `graspable` flags in
  `mock_world.py:194-213`. Add a test asserting the loaded seed equals the
  scene those existing tests already pin.

### R10 — `default_world()` survives as a public function, now backed by the shipped seed JSON.

Keep `default_world() -> MockWorld` and keep `MockWorld`, `ObjectSpec`,
`RobotModel` exported from `robot_backends` (`__init__.py:28-37`).
`default_world()` becomes "load the shipped seed document + attach the default
`RobotModel`". `MockBackend(world)` keeps working unchanged.

*Rationale.* `default_world` is imported by `test_mock_world.py:12`,
`test_prompt_drift.py:31` and `test_clear_the_table.py:29`, and `MockWorld` is
constructed directly in `test_mock_skills.py:124`, `test_mock_failures.py:151,244`
and `test_safety_gate.py:211`. Acceptance criterion 5 says existing suites stay
green; removing these types would churn all of them for no gain.

### Additional binding constraints

- **One skill = one atomic disk transition.** `_carry_held_objects()` plus a
  handler's own mutations can touch up to 3 objects per skill. Give the store a
  batch/transaction scope (e.g. `with store.batch():`) so `MockBackend.execute()`
  performs **one** `os.replace` per skill; per-mutation autocommit is the
  default outside a batch. Test both.
- **Atomic write mechanics:** write a temp file **in the same directory** as
  the target (`os.replace` is only atomic within a filesystem), then
  `os.replace`. Do not use `tempfile.NamedTemporaryFile`'s default directory.
- **No ROS.** `robot_world` is pure Python. Carry a `test_no_ros_runtime.py`
  scoped to the new package, mirroring `src/robot_backends/test/test_no_ros_runtime.py`.
- **Ratchet:** a new package is auto-discovered once its `package.xml` is
  `git add`ed. After the suite is final, run
  `python scripts/check_test_integrity.py --update-baseline` and **commit**
  `scripts/test_baseline.json`.
- **Docs:** record **D23** in `docs/design/decisions.md` (append-only, newest at
  the bottom, `- **D23 — <title> (closes #54).** … *Rationale:* …` under a
  `## 2026-08-12 — <session title>` header) and update `PROJECT.md:127`
  (roadmap step 3). `PROJECT.md:43` needs no factual correction.
- **Unchanged, non-negotiable:** `RobotBackend` (`interface.py`),
  `Observation`/`SkillResult`/`RobotState`/`SceneObject` and
  `SCHEMA_VERSION`. This is a data-source refactor, not a wire-format change.

---

## R7a — amendment (manager, after implementer escalation): `.world` returns the **seed**, not the live scene

The implementer escalated a contradiction inside R7's wording, correctly.
R7 said `.world` should be "reconstructed from the store's scene", but the
property's *documented contract* is "the immutable world this backend was
seeded from" and "`reset()` always returns to that same seed world". With a
file store those two diverge the moment anything mutates.

**Ruling: the implementer's reading stands.** `MockBackend.world` returns the
**seed** (`world_from_document(self._store.seed_document(), robot=self._robot)`).
Returning the live scene would silently change the property's meaning and make
`reset()`'s docstring false. The live scene is reachable via `backend.store`
and `get_observation()`. R7's "store wins for scene" clause still governs what
the backend *drives*; it was never meant to redefine `.world`.

Two forced consequences, both accepted:
- `__init__` calls `_power_on()` rather than `reset()`. Necessary and correct:
  `reset()` under a file store rewrites the live file from the seed, so
  constructing a backend would have wiped the very state acceptance criterion 2
  requires it to load.
- `_power_on()` validates `start_column_height` against the `RobotModel`,
  because with an injected store no `MockWorld` is in the path to do it, and
  per R4 `WorldDocument` deliberately does not know the column range.

Naming: `store.find_object(id)` rather than the issue sketch's `store.object(id)`
— `ament_flake8` rejects `object` as a method name (A003), and `find_object` is
already the repo's name for this lookup on `Observation`. Accepted.

---

## Deferred, with rationale (follow-ups for the issue, not this PR)

- **DEF-1 — No cross-process file locking.** Two processes pointed at the same
  live-state file concurrently can race on `os.replace` (last writer wins;
  no corruption, but a lost update). The deployment model is a single
  robot-side service with "one task at a time" already a system-level guard
  (D16/D21), and `SkillToolRouter` serializes within a process on an
  `anyio.Lock`. Flag it explicitly in the store's docstring as a known gap —
  do **not** silently assume it away, and do not build locking now.
- **DEF-2 — No location add/remove API.** The map is quasi-static and no skill
  mutates it. `locations()` is read-only this iteration.
- **DEF-3 — No `fsync`.** Atomic-vs-durable: `os.replace` gives atomicity
  against a crashed process, not against a power cut with dirty page cache.
  Out of scope at this scale; note it rather than implement it.
