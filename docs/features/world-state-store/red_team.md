# Red-team — world-state store (issue #54, D23)

Round 1. Read-only review of the branch diff against the issue's 6 acceptance
criteria, `status.md`'s rulings R1–R10/R7a, and CLAUDE.md invariants 1–5.

**Verdict: 3 BLOCKs, all small fixes. The architecture is right** — the
store/backend split is the correct seam, the parsing is genuinely strict, the
atomic-write mechanics are correct, and the acceptance-criteria tests are (with
one exception) substantive rather than padded. Two of the manager's rulings were
checked empirically and hold; one is stated in the docs in a form that is
literally false.

## What I verified and could *not* break

- **R2's rationale is empirically correct.** `Observation._check_held_objects_agree`
  (`src/robot_skills/robot_skills/observation.py:356-389`) enforces the
  agreement in **both** directions — a gripper naming an object that is absent
  or reports a different `held_by`, *and* an object reporting a holder whose
  gripper names something else. Persisting `held_by` without gripper state would
  therefore raise on the first `get_observation()`. The ruling stands as written.
- **`_release_persisted_holds` is reachable on every load path.** The only two
  paths that call `WorldStore._load` are `WorldStore.__init__`
  (`store.py:76`, reached by `FileWorldStore.__init__:282`) and `reset()`
  (`store.py:186`); both are followed by `MockBackend._power_on()`
  (`mock_backend.py:149` and `:177`), which ends in `_release_persisted_holds`.
  No third path exists **through the backend**. (See NOTE-1 for the path *around*
  the backend.)
- **Released objects cannot become permanently unreachable.** A held object's
  persisted pose is always `location_pose + shoulder + offset` for some named
  location, so after re-homing it is within reach from that location at the same
  column height; if the column was extended when the crash happened, an
  `extend_column` recovers it. `Grasp`/`Place` refuse with an accurate
  `out_of_reach` reason rather than crashing. No dead end.
- **Every handler validates before it mutates the store.** Walked all seven:
  `_navigate_to` (:271 refuse, :278 mutate), `_move_gripper` (:284 refuse first),
  `_grasp` (:294-:311 all refusals, :313-:318 mutations), `_place` (:323/:331
  refuse, :333-:338 mutate), `_extend_column` (:344 refuse, :350 mutate),
  `_open_gripper`/`_close_gripper` (cannot refuse). `_carry_held_objects` cannot
  raise. So "batch commits on exceptional exit" never persists a half-applied
  skill *today*. See NOTE-2 for what keeps that true.
- **Criterion 5, byte-for-byte.** `default_world.json` matches the pre-change
  literal exactly: 4 locations (charger 0,0,0 / kitchen 2,0,0 / table 0,2,0 /
  living_room -2,1,0), 7 objects in the same order with the same poses and
  `graspable` flags, `start_column_height` 0.3, `start_location` charger
  (cross-checked against `context.md:90-100`, which transcribed
  `mock_world.py:173-213` from main, and against unmodified pins in
  `test_mock_skills.py:218,349`, `test_mock_failures.py:99,111,204,226` and
  `robot_brain/.../AGENTS.md:87,179`). No drift.
- **Ratchet re-cut honestly.** `robot_backends` 60→73 (= +10 `test_mock_persistence`
  +3 new `test_mock_world` cases), `robot_mcp` 71→80 (= +6 options +3 persists),
  `robot_world` 48 (= 6+4+13+10+3+12 across the six test modules). No existing
  package count went down; nothing was removed or moved.
- **Criterion 2 is proved by genuinely separate processes**
  (`test_world_state_persists.py:51-88`), not by reused in-memory state, and it
  additionally pins the power-cycle semantics (nothing held, robot on the
  charger). **Criterion 3 is proved from the seed *file***
  (`test_file_store.py:63-81` rewrites the seed mid-run and shows `reset()`
  follows the file, not memory) — this is the strongest test in the branch.
- **The seam is unchanged.** `RobotBackend`, `Observation`, `SkillResult`,
  `RobotState`, `SceneObject`, `SCHEMA_VERSION` are consumed, not modified;
  `robot_skills`' ratchet count is unchanged at 106; the world stamp is a
  separate key with a separate counter, pinned by `test_document.py:68-75`
  (asserts `SCHEMA_VERSION_KEY` appears nowhere in a world document).
  Invariant 3 is intact: `main` → `run_stdio(backend)` → `build_server(backend, None)`
  → `SkillToolRouter` with `default_safety_layer()`; there is still no way to get
  an ungated server, and `--world-state` does not touch that path.
- **D23 and the `PROJECT.md` edit are accurate, correctly numbered (D22 → D23),
  and append-only** under a new dated header.

---

## BLOCK

### BLOCK-1 — The spawned-server tests inherit `ROBOT_WORLD_STATE`/`ROBOT_WORLD_SEED` from the developer's environment

`src/robot_mcp/test/test_stdio_transport.py:40-43` (`server_parameters()`), used
by `test_world_state_persists.py:27-34` and `:116`.

```python
env = dict(os.environ)
env['PYTHONPATH'] = ...
env.pop('ROS_DOMAIN_ID', None)
```

The whole environment is copied into the spawned `python -m robot_mcp`, and this
branch just taught that command to read `ROBOT_WORLD_STATE` / `ROBOT_WORLD_SEED`
(`server.py:364-381`). `ROS_DOMAIN_ID` is popped for exactly this reason; the two
new variables are not.

**Failure scenario.** A developer (or a deployment shell, or a future CI job)
exports `ROBOT_WORLD_STATE=~/.local/state/robot/world.json` — which is precisely
the documented usage now in `robot_mcp/__init__.py:23`. Then:

- `test_stdio_transport.py:46` compares the spawned server against a fresh
  in-process `MockBackend()`. The spawned server now resumes a persisted world,
  so the test passes on the first run of a clean file and **fails on every run
  after**, with a confusing diff — the exact breakage R8's rationale was written
  to prevent, reintroduced through the env instead of through a default.
- `test_world_state_persists.py:113` (`test_the_default_command_still_writes_nothing`)
  fails for the same reason, and both tests **write into the developer's real
  live-state file** — a test suite mutating production state.
- With only `ROBOT_WORLD_SEED` exported, both spawned servers `parser.error` and
  exit 2 (`server.py:383-386`), so the tests fail with a transport-level error
  that names nothing useful.
- `persisted_server()` (`:27`) inherits `ROBOT_WORLD_SEED` too, so a developer's
  exported seed silently replaces the shipped demo apartment underneath a test
  that asserts on `mug_1` and `kitchen`.

Note the in-process option tests get this right (`test_world_state_options.py:25-26,58-59`
`monkeypatch.delenv` both vars) — the subprocess ones were missed.

**Fix.** In `server_parameters()`, pop both variables beside the `ROS_DOMAIN_ID`
pop, and have `persisted_server()` build on that cleaned env (it already does,
once the pop is there). One line each, plus a comment saying why. Optionally add
one test asserting the spawned default server ignores a set `ROBOT_WORLD_STATE`
— no: assert the opposite, that `server_parameters()` yields an env without
them, so the guard cannot silently rot.

### BLOCK-2 — `_flush` clears the dirty flag before the commit that may fail

`src/robot_world/robot_world/store.py:237-240`:

```python
def _flush(self) -> None:
    self._pending = False
    self._commit()
```

If `_commit()` raises — full disk, read-only mount, `EACCES`, the directory
removed under the process — `_pending` has already been set to `False`, so the
store now claims to be in sync with a file that is one skill stale.

**Failure scenario.** MCP server running with `--world-state /var/lib/robot/world.json`
on a filesystem that fills up. The agent calls `place`:

1. `MockBackend.execute` batch → mutations → `batch.__exit__` → `_flush` →
   `_pending = False` → `_commit()` raises `WorldStoreError`.
2. The exception escapes `execute` (it is not `_SkillRefused`) and is caught by
   `SkillToolRouter.call_tool`'s generic handler (`server.py:242`), so the agent
   is told *place failed* — while in memory it **succeeded**.
3. The agent recovers and calls `navigate_to('table')` holding nothing. That
   handler mutates only backend proprioception; `_carry_held_objects` is a no-op,
   so **no store mutation occurs**, `_pending` stays `False`, and no retry is
   ever attempted.
4. Disk space is freed. Everything looks healthy. The next restart silently
   resurrects the mug in the kitchen — the divergence never announced itself
   again after step 2.

With the correct ordering the flag stays `True`, and because `_commit()` writes
the *whole* document, the very next mutation of any kind repairs the file
automatically. It also makes a refused skill's flush an opportunity to catch up
rather than a silent skip.

**Fix.** Commit first, clear second:

```python
def _flush(self) -> None:
    self._commit()
    self._pending = False
```

Add a test: monkeypatch `_commit` to raise once, assert `store._pending` is still
true / that the following mutation writes the file and that the file then
contains **both** changes.

### BLOCK-3 — "opens no file at all" is stated four times and is false, and the test that guards it does not test its own name

- `src/robot_world/README.md:51-55` — "`WorldStore()` and `MockBackend()` touch
  no file at all".
- `src/robot_backends/README.md:44` — "opens **no file at all** (D23), so that
  promise is unconditional".
- `src/robot_world/robot_world/store.py:24-27` — "a bare `WorldStore` (and
  therefore a bare `MockBackend()`) is exactly as deterministic and **file-free**".
- `src/robot_backends/robot_backends/mock_backend.py:27-30` — "it builds an
  *in-memory* store and **touches no file**".

All four are false. `WorldStore()` with no document calls `read_seed_document()`
(`store.py:73`) → `default_seed_document()` (`storage.py:114-129`), which opens
and parses `default_world.json` through `importlib.resources`; `MockBackend()`
does the same via `default_world()` (`mock_world.py:270`). R7's literal words
("no file is opened, created, or written") are violated; only its *spirit* — no
live-state file, no writes, no cross-test contamination — is honoured, and that
spirit is the part that actually matters.

This would be a NOTE if it were only prose. It is a BLOCK because the test that
is supposed to hold the line asserts something weaker than its own name:

`src/robot_backends/test/test_mock_persistence.py:182-196`,
`test_a_bare_backend_never_opens_a_file`, only patches `store_module.write_document`
and asserts `tmp_path` is empty. It would pass unchanged on a `MockBackend` that
opened and read arbitrary files — including one that read a live-state file from
`$HOME`. Same for `test_file_store.py:136` (`test_the_in_memory_store_never_touches_the_disk`)
and `test_world_state_persists.py:113`. The three tests prove *"never writes"*,
which is the true and valuable invariant; they are named and documented as
proving *"never opens"*, which is neither true nor tested.

**Failure scenario (why the wording matters, not just the pedantry).** Someone
reasoning from the README concludes a bare `MockBackend()` cannot fail on I/O and
uses it in a sandbox with the package installed as a zip / with `package_data`
dropped from a wheel. Every `MockBackend()` in the process now raises
`WorldStoreError` at construction — a failure mode that did not exist before this
branch, because `default_world()` was a pure literal.

**Fix (cheap, pick either).**
1. Reword all four to the true claim — *"never writes a file; the only file it
   ever opens is the read-only seed shipped inside the package"* — and rename the
   tests to `test_a_bare_backend_never_writes_a_file`. Keep the write guard.
2. Or, if you want the literal claim back, memoize the seed read
   (`functools.lru_cache` on `default_seed_document`; `WorldDocument` is frozen,
   `locations` is a `MappingProxyType` and `objects` a tuple of frozen
   dataclasses, so a shared instance cannot leak mutable state between callers —
   the store copies into its own dicts in `_load`). That also removes the
   parse-and-validate cost paid on *every* `MockBackend()` construction (see
   NOTE-4). It still would not make "opens no file" true on the *first* call, so
   (1) is needed regardless.

---

## NOTE

### NOTE-1 — `MockBackend.store` is a public handle that can brick `get_observation()`

`mock_backend.py:161-164`. The property is documented as "the store holding the
live scene this backend drives", with no constraint attached. Mutating `held_by`
through it desynchronises the store from `_MockGripper.held_object_id`, which
`Observation` refuses to represent:

```python
backend.execute(NavigateTo('kitchen')); backend.execute(Grasp('mug_1'))
backend.store.reset()          # or set_held_by('mug_1', None), or remove_object('mug_1')
backend.get_observation()      # ValueError: ... 'mug_1' ... reports held_by=None
```

Through `robot_mcp` that means every subsequent tool call returns `isError` until
someone calls the `reset` tool. This is *loud*, and nothing in the repo does it —
hence NOTE, not BLOCK. But `PROJECT.md:127` names "letting perception write into
it" as the immediate next step, and a perception writer doing
`store.remove_object(...)` on a held object is exactly this bug. Answering the
manager's question directly: **yes**, an object can end up disagreeing with the
gripper, but only by going around the backend.

**Fix.** Two docstring sentences on the `store` property naming the constraint
("mutations made through this handle bypass the backend's gripper book-keeping;
changing `held_by`, or removing a held object, will make `get_observation()`
raise — drive the scene through skills, or call `backend.reset()` afterwards"),
plus one test pinning the current behaviour so the next feature discovers it from
a test rather than from a bricked session. Worth doing in this PR — it is cheap
and it is where the next feature lands.

### NOTE-2 — What keeps "commit on exceptional exit" honest is undocumented and lives in another package

`store.py:189-204` deliberately commits a batch left by an exception, and
`mock_backend.py:32-36` promises "a refused skill therefore cannot leave the
world half-changed". That promise is currently true (verified handler by handler
above), and it is genuinely regression-protected — but by
`mock_backend_fixtures.py:60-87` (`assert_refused` compares the *entire*
serialized observation before and after every refusal) and
`test_mock_failures.py:194-210`, which cover all 7 backend refusal codes. Neither
`store.py` nor `mock_backend.py` says so, so a future author weakening
`assert_refused` would not know they were removing the only mechanism enforcing
the store's atomicity promise.

Also note the rationale given in `implementation.md` ("the store has no in-memory
rollback to make 'skip the write' honest") is a weaker argument than it looks:
`document()` is a cheap immutable snapshot and `_load()` already exists, so
rollback *is* available. The reason not to roll back is better stated as "a
rollback of the store alone would desync it from backend proprioception, so the
real invariant is validate-before-mutate across both".

**Fix.** One sentence in `batch()`'s docstring pointing at `assert_refused` as
the thing that keeps this safe, and a matching comment in `assert_refused`.

### NOTE-3 — `--world-state` and `--world-seed` at the same path silently turn `reset()` into a no-op

`server.py:390-404` → `FileWorldStore.__init__` (`store.py:268-282`). With
`seed_path == live_path`, `seed_document()` (`store.py:294-296`) re-reads the
*mutated live file*, so `reset()` restores the current state. No error, no
warning — `reset` just quietly stops working, defeating acceptance criterion 3
for that deployment. Everything else in this feature fails loudly (R6); this one
case does not.

**Fix.** In `FileWorldStore.__init__`, refuse when the two resolve to the same
file (`os.path.samefile` guarded for the not-yet-existing live file, or compare
`Path.resolve()`), with a `WorldStoreError` saying why. One test.

### NOTE-4 — The seed is re-read, re-parsed and re-validated on every call

`storage.py:114-129` is uncached, so:
- every `MockBackend()` construction pays a resource open + `json.loads` + full
  document validation (`mock_world.py:270`);
- every read of `MockBackend.world` on a file-backed store does a **disk read**
  (`mock_backend.py:159` → `FileWorldStore.seed_document`), turning a property
  into I/O — and one that can now raise `WorldStoreError` if the seed file was
  removed at runtime. `test_mock_persistence.py:109` reads it in an assertion.

Correct, just wasteful and surprising. See BLOCK-3 fix (2) for the memoization
note; for `FileWorldStore` the re-read is deliberate (R3) and should stay, but
`MockBackend.world`'s docstring should say it hits the disk.

### NOTE-5 — `FileWorldStore` inherits a `_seed` attribute holding the *live* document

`store.py:73` sets `self._seed = document`; `FileWorldStore.__init__:282` passes
the **live** document to `super().__init__`. So a `FileWorldStore`'s `_seed` is
its live scene, and it is only harmless because `seed_document()` is overridden
(`:294`). Any future base-class code that reads `self._seed` directly — an
obvious "avoid the re-read" optimization, cf. NOTE-4 — would silently make
`reset()` restore the live scene. Set `_seed = None` in the file store, or make
the base class read through `seed_document()` everywhere.

### NOTE-6 — Atomic-write details

`storage.py:141-176`. All minor, all in the same function:
- `tempfile.mkstemp` creates the file mode `0600`, and `os.replace` carries that
  onto the live file, silently replacing whatever mode it had (e.g. an operator's
  `0644`). Consider `os.chmod(temporary, 0o644 & ~umask)` or preserving the
  target's existing mode when it exists.
- If `os.fdopen` itself raises (`:162`), the fd from `mkstemp` leaks — the
  `except` unlinks the path but never closes the descriptor. Wrap the
  `fdopen` in the try or `os.close` on that path.
- A `SIGKILL` between `mkstemp` and `os.replace` leaves a `.world.json.*.tmp`
  behind; the docstring's "a crashed write leaves no litter" is true only for
  exceptions, not for a killed process. Worth one honest clause.

### NOTE-7 — The atomic-write tests monkeypatch the real `os` module

`test_atomic_write.py:65,82,109` do `monkeypatch.setattr(storage.os, 'replace', ...)`.
`storage.os` **is** the `os` module, so this patches `os.replace` / `os.fdopen`
process-wide for the duration of the test, not just for `storage`. `monkeypatch`
undoes it, so this is safe today, but it will bite the moment anything else runs
concurrently in the same interpreter. Prefer a module-level indirection
(`_replace = os.replace`) or patch `robot_world.storage.os` via a seam the module
owns. The tests themselves are otherwise excellent — they inject the failure in
both of the windows that matter and check for litter, which is the right shape.

### NOTE-8 — `parse_args` refuses `ROBOT_WORLD_SEED` alone with an `argparse` error

`server.py:383-386`. The check is on the *resolved* values, so an operator who
exports `ROBOT_WORLD_SEED` globally makes a bare `python -m robot_mcp` exit 2.
That is arguably correct (loud beats silent), but the message names the *flags*,
not the env vars that actually caused it. Mention both.

### NOTE-9 — `parameters.env['PWD'] = str(tmp_path)` does nothing

`test_world_state_persists.py:117`. The subprocess's working directory comes from
`monkeypatch.chdir(tmp_path)` in the parent (`StdioServerParameters.cwd` is
`None`, so it inherits); setting `PWD` in the env has no effect on where the
child would write. Harmless, but it reads as if it were load-bearing. Delete it
or replace it with a comment explaining the inherited cwd.

### NOTE-10 — `robot_world/package.xml:11` declares `<depend>rclpy</depend>`

For a package whose own `test_no_ros_runtime.py` exists to prove it never imports
rclpy, and whose README leads with "pure Python". `robot_backends` does the same,
so this follows precedent, but `robot_mcp` (also pure Python) does not declare
it. Dropping it here would make the manifest match the package's stated contract.

### NOTE-11 — Parse errors inside `objects` do not name which object

`document.py:268-272` builds each `WorldObject` with the context
`'WorldDocument.objects[]'` — no index — and `WorldObject.from_dict:155-158`
parses `pose`/`graspable` *outside* the `parse_errors('WorldObject')` scope, so a
bad pose in the 5th object of a hand-edited live file produces a message that
names neither the object nor its position. For a format whose whole error policy
is "fail loudly so an operator can see what went wrong" (R6), the index is worth
carrying. `test_file_store.py:108-120` passes only because it corrupts
`objects[0]`.

### NOTE-12 — `WorldDocument` does not reject two objects claiming the same `held_by` side

`document.py:185-220` validates duplicate ids, empty locations and
`start_location` membership, but a file with `mug_1: held_by "left"` and
`plate_1: held_by "left"` parses cleanly. Harmless today because `MockBackend`
clears every hold at power-on, but a store consumer that does *not* clear (the
planned ROS query service) would hand out a scene `Observation` cannot represent.
One line in `__post_init__`, consistent with the duplicate-id check already there.

### NOTE-13 — `start_column_height` is the one field in the on-disk schema that describes the robot, not the room

`document.py:183`, and `mock_backend.py:239-243` has to validate it against
`RobotModel` because `WorldDocument` deliberately cannot. R1's own rationale
("perception never emits the robot's column height", "the store must be the layer
that survives the backend swap") argues against it as much as against
`_MockGripper.offset`; it is in the file only because `MockWorld` had it. After
the MuJoCo swap (#4) a real robot comes up wherever it physically is, and this
field becomes either dead weight or a homing command in disguise. Not worth
churning now — the deviation is small and R7a's `_power_on` validation contains
it — but flag it in the issue as the field to re-examine when #4 lands, rather
than letting it calcify as "world data".

---

## Test adequacy — explicit assessment

| criterion | proving test | verdict |
|---|---|---|
| 1. observation ↔ live file | `test_mock_persistence.py:57` | **Adequate.** Checks both directions and reads the file back with `read_document` rather than through the store. |
| 2. fresh process sees the mutation | `test_world_state_persists.py:51` | **Strong.** Two genuinely separate `python -m robot_mcp` processes over one path; also pins power-cycle semantics and compares against a fresh in-memory reference so a wrong-but-consistent world cannot pass. Weakened only by BLOCK-1 (env leakage). |
| 3. `reset()` from the seed file | `test_file_store.py:63`, `test_mock_persistence.py:114`, `test_world_state_persists.py:91` | **Strong.** `test_file_store.py:63` rewrites the seed file mid-run — this genuinely distinguishes "restored from the file" from "restored from a startup snapshot", which is the failure mode that matters. |
| 4. atomic writes | `test_atomic_write.py` (6 tests) | **Adequate.** Failure injected in both windows; co-location of the temp file asserted directly (not merely documented); litter checked on both paths. Not a tautology. See NOTE-7 for the patching style. |
| 5. seed == old `default_world()`; suites green | `test_default_seed.py:47`, `test_mock_world.py:142` | **Adequate.** The longhand pin is the right call and the docstring explains why comparing against `default_world()` would assert nothing. `test_the_seed_file_is_exactly_what_a_write_would_emit` is a nice anti-drift guard. |
| 6. D23 + roadmap | docs | **Done.** |

The 48 `robot_world` tests are **substantive, not padded**: every one of them can
fail for a distinct reason, the batch/no-op/nesting/exception cases are pinned
against a commit counter rather than against file contents, and the strict-parsing
tests cover unknown key, missing key, wrong type, foreign version, absent version
and the scene-level invariants separately. The one test that does not earn its
name is `test_a_bare_backend_never_opens_a_file` (BLOCK-3). The untested gaps I
would add alongside the BLOCK fixes: a failed `_commit` followed by a successful
one (BLOCK-2), and a corrupt `--world-state` file making `python -m robot_mcp`
exit loudly at startup (covered at the store level, not at the CLI level).

## Invariants

1. **Skill API is the seam** — held. The store is below the API and the brain
   never sees it; `robot_mcp` gained flags, not vocabulary.
2. **Backend abstraction** — held, and improved. No Mock kinematics reached the
   on-disk schema: `RobotModel`, `_MockGripper.offset`/`orientation` and all
   reach arithmetic stayed in the backend, pinned by
   `test_mock_world.py:151-167` and `test_mock_persistence.py:199-222`. The one
   wart is NOTE-13.
3. **Safety layer** — untouched and unbypassable; the new backend still enters
   through `build_server` → `SkillToolRouter` → `default_safety_layer()`.
4. **Structured scene JSON** — strengthened; the scene is now literally a
   document, and `label` accepts free-form strings (`as_identifier` only rejects
   blanks), so a perception writer is not boxed in.
5. **Reuse** — held; `robot_skills.serialization` is reused verbatim, stdlib
   `json`/`tempfile`/`os.replace` only, no new dependency.

---
---

# Round 2 — review of the fix commit `12c49a1`

Scoped to the fix commit. Round-1 findings already cleared are not re-reviewed;
this section attacks the fixes themselves and sweeps for regressions.

**Verdict: 1 BLOCK, and it is a one-line documentation fix in
`docs/design/decisions.md` — the same false claim BLOCK-3 removed everywhere
else, left standing in the one document that survives the merge.** Every other
fix is correct, and three of them are better than what I asked for. Round 1's
BLOCK-1/-2/-3 and promoted NOTE-1/-3 are all genuinely resolved, each with a test
that fails on the un-fixed code.

## BLOCK

### R2-BLOCK-4 — `decisions.md` still carries the "opens no file at all" claim BLOCK-3 removed

`docs/design/decisions.md:55` (inside D23):

> **Persistence is opt-in.** `MockBackend()` and `python -m robot_mcp` with no
> options open **no file at all**; …

The fix commit corrected all four sites I listed (`robot_world/README.md:51-57`,
`robot_backends/README.md:43-45`, `store.py:25-29`, `mock_backend.py:27-31`) —
and they are now accurate — but D23 itself was missed. `MockBackend()` still
opens `robot_world/default_world.json` (once per process, thanks to the new
cache), and so does `python -m robot_mcp` with no options via
`build_server(None)` → `MockBackend()`.

**Why this is the BLOCK and the READMEs were not.** `docs/features/world-state-store/`
is deleted at merge and the package READMEs can be corrected in any later PR, but
`docs/design/decisions.md` is the **permanent, append-only** record — after this
merges, D23 becomes the durable statement of the invariant, and it will be the
thing a future contributor (or the MuJoCo/perception work) reasons from. Shipping
a design-log entry that misstates its own invariant is exactly what BLOCK-3 was
about, in the worst place for it. Editing it now is not an append-only violation:
the entry is unmerged and belongs to this PR.

**Fix.** One line — match the wording already used in the READMEs, e.g. "…with no
options **never write a file**; the only file either opens is `robot_world`'s
read-only shipped seed". Consider also making D23 record the seed==live refusal
and the commit-then-clear ordering, since both are now load-bearing behaviour,
but that is optional.

## Fix-by-fix verification

**1. BLOCK-2 — `_flush` reorder (`store.py:250-260`).** Correct.

- `batch().__exit__` (`store.py:214-217`) decrements `_batch_depth` **before**
  calling `_flush()`, so a raising commit cannot leak or corrupt the depth
  counter: depth is already back to 0 and the store stays usable. Nested batches
  behave the same (only the outermost flushes; the decrement precedes it at every
  level). No depth defect.
- **Exception masking is real but narrow and correct in priority.** Only after a
  *previously failed* commit can `_pending` be true on entry to `execute`; a then-
  refused skill flushes on the way out. If that flush succeeds, the file
  self-heals and the `_SkillRefused` propagates normally into a failed
  `SkillResult`. If it fails again, the `WorldStoreError` raised in the `finally`
  replaces the in-flight `_SkillRefused` (chained via `__context__`) and
  `execute` raises instead of returning a result. That is the right ordering — a
  disk failure outranks a refusal — and it is loud (`SkillToolRouter.call_tool`
  turns it into `isError`). See R2-NOTE-3 for the doc gap it leaves.
- **`pending_write` (`store.py:129-138`) cannot corrupt anything**: read-only
  property, returns an immutable `bool`, no setter, no aliasing. Its docstring
  correctly states the three cases.
- **The self-heal claim is proved, not asserted.**
  `test_file_store.py:181-220` fails the first write, then asserts (a)
  `pending_write is True`, (b) the file is *stale* — the mutation is only in
  memory, (c) a *different* mutation afterwards lands **both** changes on disk,
  (d) `pending_write is False` again. Assertion (a) fails on the pre-fix
  ordering, so the test genuinely pins the fix rather than the behaviour.

**2. NOTE-3 — path aliasing (`store.py:319-339`).** I could not defeat it in any
way that matters.

- Ordering is right: the check runs at `:296`, **before** `seed_document()` and
  before the live file is created, so a misconfiguration creates nothing.
- `Path.resolve()` is non-strict by default, so a live path whose **parent does
  not exist** resolves fine (no exception) — verified against
  `test_a_missing_live_file_is_created_from_the_seed` and
  `test_a_missing_or_corrupt_seed_is_a_hard_error`, both of which still pass
  through the guard without a false positive.
- **Hardlinks** (distinct paths, same inode) are caught by the `samefile()`
  fallback. **Symlinks** and `..` traversal are caught by `resolve()` and are
  both tested (`test_file_store.py:231-240`). A **broken symlink** at the live
  path still resolves to the seed's real path and is caught. On a
  **case-insensitive** filesystem `exists()` is also case-insensitive, so the
  `samefile()` fallback fires; irrelevant on the target platform anyway.
- A symlink created *after* construction is not caught — no construction-time
  check can catch that, and it is not a defect.
- **It does not fire on the legitimate case**: two genuinely separate files with
  identical contents differ under both `resolve()` and `samefile()`, pinned at
  `test_file_store.py:242-245`.
- The error is loud and attributable per R6: a `WorldStoreError` naming **both**
  paths and the consequence ("or `reset()` would restore whatever the robot last
  wrote"). Enforced in the store rather than only in the CLI, so every caller
  benefits — better than what I suggested. Covered end-to-end through the parsed
  CLI options at `test_world_state_options.py:130-136`.

**3. `@lru_cache(maxsize=1)` on `default_seed_document` (`storage.py:115`).** Safe.
I traced the whole reachable object graph rather than accepting the claim:

- `WorldDocument` is `frozen=True`; `__post_init__` copies `locations` into a
  **local** dict and wraps it in a `MappingProxyType`, so no caller ever holds a
  reference to the backing dict; `objects` is a `tuple`; `start_location` /
  `start_column_height` are `str`/`float`.
- `WorldObject` is `frozen=True`, and `Point`, `Quaternion`, `Pose` are all
  `@dataclass(frozen=True)` (`geometry.py:36,86,129`). Frozen all the way down —
  the claim checks out.
- Every consumer copies: `WorldStore._load` builds a **new** locations dict and a
  new id→object dict (and `_replace`/`del` operate on the store's own dict, never
  on the document's tuple); `document()` constructs a fresh `WorldDocument`;
  `world_from_document` builds new `ObjectSpec`s and a new `MockWorld` which
  copies + proxies again; `write_document` only reads via `to_dict()`.
  `MockBackend` never mutates a document — it converts (`world_to_document`) or
  rebuilds (`world_from_document`).
- **No interaction with `reset()`.** `WorldStore.reset()` reloads (and copies)
  from `_seed`; `FileWorldStore.reset()` goes through `seed_document()`, and
  criterion 3's strong test uses an explicit `seed_path`, which bypasses the
  cache entirely (`read_document`) — so the "rewrite the seed file mid-run" test
  still proves what it proved. Nothing patches the shipped resource, so no test
  is defeated by the cache. `lru_cache` does not cache exceptions, so a broken
  install still raises on every call.
- Cache poisoning **is** incidentally pinned: `test_default_seed.py:88-99` builds
  a `FileWorldStore` from the shipped (now cached) seed, mutates and
  `remove_object('sofa_1')`, then asserts `default_seed_document()` still has
  `sofa_1`. See R2-NOTE-2 for why that coverage is fragile.

**4. BLOCK-1 — `clean_environment()` (`mcp_fixtures.py:25-46`).** Complete, and
the implementer's audit is sound — I re-derived it rather than taking it.

- `INHERITED_ENV_TO_DROP` is the single source of truth and carries the *why*.
  `server_parameters()` (`test_stdio_transport.py:43-44`) uses it;
  `persisted_server()` (`test_world_state_persists.py:29`) builds on
  `server_parameters()`; `test_world_state_persists.py:118` uses it directly;
  `robot_mcp/test/test_no_ros_runtime.py:55` uses it. That is every
  subprocess-spawning site in `robot_mcp`.
- **The "other probes never reach `parse_args`" reasoning is correct.** Grepped
  every `os.environ`/`getenv` in `src/`: the *only* readers of these two
  variables anywhere in the tree are `server.py:367` and `:376`, inside
  `parse_args`. The four other `dict(os.environ)` sites are the no-ROS probes in
  `robot_backends` (`:48`, probe builds a `MockBackend()` directly — read it,
  no `robot_mcp` import), `robot_world` (`:50`, probe uses an explicit
  `FileWorldStore(tmpdir)`), `robot_safety` and `robot_brain` (unrelated
  packages). None can reach `parse_args`, so none can be perturbed. Leaving them
  alone was right.
- **The guard test bites.** `test_world_state_options.py:106-127` sets all three
  variables and asserts each is absent from **both** `server_parameters().env`
  and `persisted_server(...).env`, iterating `INHERITED_ENV_TO_DROP` itself — so
  reverting either pop, *or* removing an entry from the constant, fails it. It
  also asserts `PYTHONPATH` survives and that `--world-state` is still how a
  persisted server is asked for, so the fix cannot be "empty the environment".
- Bonus: R1-NOTE-9 (the inert `PWD` line) was fixed properly — replaced with an
  accurate comment about the inherited cwd (`test_world_state_persists.py:115-117`).

**5. BLOCK-3 — rewording and rename.** Now literally true everywhere it appears.

- `test_a_bare_backend_never_writes_a_file` (`test_mock_persistence.py:182-207`)
  now patches **both** `write_document` and `read_document` in the `robot_world.store`
  namespace to `pytest.fail`. It therefore fails on code that writes *and* on
  code that reads a world file, while still allowing the seed-resource read
  (which goes through `storage.default_seed_document`, not the patched name) —
  correct targeting, and it tests its own name. The docstring states the
  distinction explicitly. Same treatment in `test_atomic_write.py:136-141`.
- The reworded claims hold **after** the cache too: "the only file it opens is
  the read-only shipped seed" is true whether that happens once per process or
  once per call, and `store.py:19-20`'s "reads the shipped seed **once**, and
  after that touches nothing" became true *because* of the cache.
- The one site still false is `decisions.md:55` → R2-BLOCK-4.

**6. Promoted NOTE-1 — `backend.store`.** Better than I asked for: the docstring
(`mock_backend.py:164-174`) names all three hazards (`held_by`, removing a held
object, `store.reset()` mid-carry), says what the recovery is, and addresses the
perception writer directly; and
`test_mock_persistence.py:263-281` pins the whole cycle — desync → `ValueError`
on the next observation → `reset()` recovers → `held_objects() == ()`.

## Regression sweep

Nothing round 1 cleared was weakened.

- **Atomicity.** `storage.write_document` is byte-identical apart from the
  `lru_cache` import; the six `test_atomic_write.py` tests are unchanged except
  for the rename at `:136`. The new guard and the flush reorder sit strictly
  above `write_document` and cannot affect the temp-file/`os.replace` mechanics.
- **R2 / `_release_persisted_holds`.** Untouched (`mock_backend.py:257-264`), and
  its two entry paths are unchanged: `__init__` → `_power_on` and `reset()` →
  batch → `store.reset()` + `_power_on`. The power-cycle test
  (`test_mock_persistence.py:89-111`) is unchanged and still asserts the release
  reaches disk.
- **Criterion 2** (`test_world_state_persists.py:51-88`) and **criterion 3**
  (`test_file_store.py:63-81`) are unchanged in substance; criterion 2 is now
  *stronger*, because BLOCK-1's fix removes the environment dependency that was
  the one thing weakening it. Criterion 3's seed-rewrite test uses an explicit
  seed path, so the cache does not touch it.
- The aliasing guard introduces no false positives in the existing suite: the
  missing-live-file, missing-seed and corrupt-seed tests all pass through it with
  `seed_path` present but distinct, or absent entirely.
- **Ratchet honest again**: `robot_backends` 73→74, `robot_mcp` 80→82,
  `robot_world` 48→50; every other package unchanged. That is exactly the 5 new
  tests the fix commit describes — nothing was dropped to make room.

## Round-2 NOTEs

### R2-NOTE-1 — The cache makes two "re-read from disk" statements false

`FileWorldStore.seed_document()`'s docstring (`store.py:315-317`) says "**Re-read**
the seed from disk, so `reset()` restores ground truth", and
`robot_world/README.md:46` says the seed is "read when | construction and every
`reset()`". Both are now false for the **shipped** seed: `read_seed_document(None)`
returns the memoized document. Concrete consequence: a developer iterating on
`default_world.json` in a symlink-installed checkout while a server runs will call
`reset` and not see their edit until they restart the process — which is precisely
what the docstring promises they will. (For an operator-supplied `--world-seed`
the statement remains true, since that path goes through `read_document`.)
NOTE rather than BLOCK because the cache's own docstring states its premise
honestly and no acceptance criterion depends on the shipped seed being re-read.
**Fix:** one clause — "re-reads an operator-supplied seed from disk; the shipped
seed is memoized and picked up at process start".

### R2-NOTE-2 — The cache's safety has no test of its own

New global state, and the only thing standing between it and a poisoned process
is `test_default_seed.py:88-99`, whose name and docstring are about the *file*
being read-only, not about the cache — so someone "tidying" it to use an explicit
`seed_path` would silently delete the cache's only guard. Cheap hardening:
`assert default_seed_document() is default_seed_document()` (the cache contract
itself), plus one line in that test's docstring saying it also proves the shared
instance survives a store's mutations. Worth noting in passing that
`default_seed_document.cache_clear()` exists implicitly and is the escape hatch
for any future test that wants to swap the shipped seed.

### R2-NOTE-3 — The reorder gives a *refused* skill a new (correct) way to write

Two behaviours the reorder introduces, both desirable, neither documented: after a
failed commit, (a) the next skill's batch exit flushes even if that skill was
**refused**, so the file self-heals on a refusal, and (b) if that flush fails too,
the `WorldStoreError` replaces the in-flight `_SkillRefused` and `execute` raises
instead of returning a failed `SkillResult`. `_flush`'s new docstring covers the
"stay dirty" reasoning but not either consequence, and
`test_a_refused_skill_writes_nothing` (`test_mock_persistence.py:165`) is now
strictly true only for a store that was clean on entry — its name over-generalises
by exactly one case. One sentence in `batch()`'s docstring and one clause in that
test's docstring.

### R2-NOTE-4 — The aliasing guard cannot see the *shipped* seed

`_refuse_seeding_from_the_live_file` returns early when `seed_path is None`
(`store.py:329-330`), so `FileWorldStore('<site-packages>/robot_world/default_world.json')`
— i.e. `--world-state` pointed at the shipped resource — is not refused, and the
robot would write its live state over the seed. Far-fetched, contained by R9's
"never an `importlib.resources` path" rule, and now partly masked by the cache
(the running process keeps the pristine seed in memory), but it is the one
aliasing case the new guard's docstring implies it covers and does not. A clause
in the docstring is enough; comparing against `resources.files(...)` would be
over-engineering.

### R2-NOTE-5 — The guard's verdict is computed once, from `resolve()`

Fine for absolute paths. For a **relative** live path, `resolve()` is evaluated
against the CWD at construction while `_commit` → `write_document` re-resolves at
every write, so a `chdir` after construction moves where writes land without
re-checking the aliasing. This is a pre-existing property of relative paths rather
than something the guard introduced — noting it only so nobody reads the guard as
a durable guarantee. If it ever matters, store `Path(live_path).resolve()`
(absolutised) rather than the path as written.

## Round-2 test adequacy

The 5 new tests are all substantive and each fails on the un-fixed code:
`test_a_failed_commit_leaves_the_store_dirty_and_the_next_write_repairs_it`
(fails on the old flag ordering at its very first assertion),
`test_a_seed_that_is_the_live_file_is_refused` (three aliasing forms + the
negative case), `test_a_spawned_server_never_inherits_the_worlds_environment`
(iterates the constant, so it fails on a reverted pop *or* a shortened list),
`test_seeding_from_the_live_file_is_refused` (the same guard through the parsed
CLI), and `test_going_around_the_backend_through_the_store_is_loud` (the full
desync → raise → `reset()` cycle). The two renamed tests now assert what their
names claim. The only untested new mechanism is the `lru_cache` contract
(R2-NOTE-2).

**Bottom line: fix `decisions.md:55` and this is ready.** The round-2 NOTEs are
all follow-ups; none of them should hold the PR.
