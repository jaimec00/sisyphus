# Implementation — #46 First milestone: OpenClaw `robot` agent → robot_mcp → Mock

Written by the implementer. Rulings R1–R11 (`status.md`) were followed; every
place where judgment was exercised inside a ruling is called out below, as is
the one file touched outside the owned paths.

## What shipped, commit by commit

| commit | what |
|---|---|
| `0341d2b` | `robot_safety` wired into `robot_mcp`'s `SkillToolRouter` + `test_safety_gate.py` + README |
| `3deb78e` | `default_world()` gains `cup_1` on the table + the test that pins why |
| `5eb609b` | `test_clear_the_table.py` — the milestone end-to-end test |
| `843a925` | `src/robot_brain`: prompt, config fragment, loaders, drift guards, README |
| `f431b56` | `scripts/test_baseline.json` re-cut (F7) |
| (R12) | the shipped `keep_out_boxes` made live at the default server |

### 1. The safety gate (R1–R4)

`SkillToolRouter._payload`'s skill branch now calls `_gated_execute(skill)`
inside the existing `anyio.Lock` instead of `self._backend.execute(skill)`:

```python
state = SafetyState(observation=self._backend.get_observation())
verdict = self._safety.filter(skill, state)
if isinstance(verdict, SafetyEvent):
    return SkillResult.failure(skill, state.observation, verdict.failure_code, verdict.detail)
result = self._backend.execute(verdict.skill)
if not verdict.was_clamped:
    return result
notes = [event.detail for event in verdict.clamps]
if result.reason:
    notes.insert(0, result.reason)
return replace(result, reason='; '.join(notes))
```

Decisions taken inside the rulings:

- **The abort reports the observation the verdict was taken against**, not a
  second `get_observation()` call. Nothing executed, so the two are equal by
  construction; taking one sample removes any way for them to disagree.
- **`dataclasses.replace` rather than rebuilding `SkillResult`.** R1 asked me
  to re-examine whether composing a clamp note into a *successful* result's
  `reason` loses information. It does not, and `replace` is why: `status`,
  `code`, `skill` and `observation` travel across unread, so a field added to
  `SkillResult` later cannot be silently dropped here, and the frozen
  dataclass's `__post_init__` re-validates the rebuilt value (it validates
  only; it mutates nothing). `reason` is documented as "an informational note"
  on a successful result and the repo already uses it that way (`_place`
  returns `"released 'mug_1' from the left gripper"`), so a clamp note is the
  same kind of thing rather than a new use. The one case worth naming: when the
  clamped skill is *still* refused by the backend, the composed reason carries
  the backend's refusal first and the clamp second, `status` stays `failed` and
  `code` stays the backend's — covered by
  `test_a_clamped_command_the_backend_still_refuses_keeps_both_stories`. **No
  escalation was needed on R1.**
- **`safety` is type-checked** (`isinstance(safety, SafetyLayer)`), not
  duck-typed: accepting "anything with a `filter`" would make a permissive
  stand-in a supported way to build an ungated server, which is the thing
  invariant 3 forbids. `SkillToolRouter(backend)` and `build_server(backend)`
  both end up holding a real layer.
- **`INSTRUCTIONS` gained two sentences** telling the agent what a clamp and a
  `rejected` look like. The tool payload shape is unchanged (R1); this is the
  server's own instructions string, which clients surface to the model.

**Not done, deliberately:** no `SafeBackend` decorator (R2), no telemetry hook
(R4), no new `SkillResult` field and no edit to `src/robot_skills` (R1). The
existing `test_tool_calls.py` passes **unchanged**, which is the pass-through
proof.

#### R12 — the configured keep-out geometry is live at the default server

I raised the gap (a default `SafetyLayer()` uses `NullCollisionGuard`, so
`limits.yaml`'s `below_floor` region was declared-but-inert at the one seam the
LLM is behind) rather than closing it unilaterally; the manager ruled to close
it, in `robot_mcp` only. `default_safety_layer(limits=None)` builds **one**
`SafetyLimits.defaults()` and hands the same object to both
`SafetyLayer(limits=…)` and `KeepOutBoxGuard.from_limits(…)`, so the envelope
and the geometry cannot come from two different reads of the file. A limits set
with no regions makes `from_limits` raise `SafetyConfigError` — the *only*
error swallowed, and it degrades to `NullCollisionGuard()` rather than failing
server construction; any other error still raises, because a malformed limits
file must not quietly become a permissive server. An injected `safety=` is used
exactly as given; nothing is layered on top of a caller's guard.

Four tests: the **default** server (`build_server()`, no arguments, driven
through the real MCP client) aborts a sub-floor `move_gripper` with
`status: "failed"`, `code: "rejected"` and `below_floor` in the reason, with
the backend's execute log empty; the default layer's guard is a
`KeepOutBoxGuard` holding exactly the shipped boxes and the same column limits;
a box-less limits file yields a `NullCollisionGuard` and a working server; and
an injected null-guard layer lets the same sub-floor target through to the
backend (which refuses it `out_of_reach`), proving injection wins entirely.

**`SafetyLayer()`'s own default guard was not changed.** Whether `robot_safety`
should default to enforcing its configured regions is that package's decision,
not this feature's — it stays as follow-up **F8** for Sisyphus to file.

### 2. `default_world()` (R10)

`cup_1` (label `cup`) at `(0.30, 1.90, 0.75)`, beside `book_1`. From the
`table` stand point at the starting column height the shoulders sit at
`z = 0.80`; the further of the two objects is `0.41 m` away, well inside the
`0.85 m` reach — so the loop needs no `extend_column`, and `cup_1` is
unreachable from `charger` like everything else on that table.

**It broke nothing.** No existing assertion pinned the object set and no golden
fixture encodes the world (the fixtures build their own scenes), so R10's
escalation trigger was never reached. The new
`test_the_table_holds_more_than_one_graspable_object_to_clear` pins the
property that made the change necessary — two graspable, *reachable* objects —
with the reach arithmetic asserted rather than eyeballed.

### 3. The milestone test (R11)

`src/robot_mcp/test/test_clear_the_table.py` drives `mcp_fixtures.connected`
(a real in-process MCP client) through the whole chore. The driver is
deliberately blind: it takes object ids, the drop pose and its stopping
condition out of the returned observation dicts, so it proves the loop can be
closed *from the payloads alone* — a test that named `book_1` would only prove
the world was as expected. It also mirrors what the prompt teaches (derive a
place pose from a surface already in the observation, `+0.10 m` clearance).

Six tests: the full loop for every table object; the transcript rebuilt through
`SkillResult.from_dict`; `out_of_reach` on a place-before-walking-over, with
the object still held and the *same* call succeeding after `navigate_to`; the
**default** server clamping `extend_column(2.5)` mid-run (and the agent
recovering from the resulting `out_of_reach` by lowering the column); and an
injected `KeepOutBoxGuard` over the drop zone aborting with `rejected`, twice,
with the object never entering the region.

### 4. `robot_brain` (R5–R9)

Layout exactly as R5 specified, `package_data` + `importlib.resources` like
`robot_safety/limits.yaml`, `<depend>rclpy</depend>` dropped, README rewritten.
`SOUL.md` was **not** shipped — R5 said only if it earns its place, and it
would have been an empty file for symmetry.

The prompt (`openclaw/AGENTS.md`) is hand-written prose carrying what
PROJECT.md:29 requires: the tool table with units, the observation format, a
recovery for *every* `FailureCode`, the safety envelope, and three worked
examples — the clear-the-table loop, an `out_of_reach` the agent recovers from
by moving rather than by re-aiming, and a safety clamp changing what ran (with
the `out_of_reach` that follows it, so the agent learns to tell a clamp from a
refusal). Every quoted `reason` string in the examples was produced by running
the real sequence against Mock, not invented (verified: `3.25 m`, `commanded
column height 2 m is outside the [0, 1.2] m travel range; clamped to 1.2 m`,
`released 'book_1' from the left gripper`).

R9's place-pose problem gets its own section: derive the pose from an object's
pose or your own, `+0.10 m` clearance, and on `out_of_reach` move rather than
re-aim. The skill was **not** redesigned (follow-up F1).

**The drift guards** (`test/test_prompt_drift.py`, five classes) compare the
prompt with the live sources — `TOOL_NAMES`, each tool's own `input_schema`
(the `inputSchema` wire field; `mcp==2.0` exposes it under the snake-case
name), `SafetyLimits.defaults()`, `default_world()`, and `FailureCode`. The
strongest of them derives an allowed **vocabulary** from the running system
(tool names, argument names, every key of a real `SkillResult` dict, every enum
value on the wire, the seed world's ids and labels) and fails on any backticked
word outside it — so an invented field name is caught as loudly as an invented
tool.

These were validated by *mutation*, not by passing: renaming a tool is caught
by 4 tests, renaming an argument by 2, retuning a limit by 1, dropping a
failure-code row by 2, a fictional location by 1, and an invented observation
field by 1. (Run ad hoc, not committed — the tests assert against the real
prompt.)

**The config fragment** follows R7/R8: `ssh -T laptop bash -lc …` stdio
transport, all four packages on `PYTHONPATH` (including `robot_safety`, now a
runtime dependency), a merge fragment of exactly three keys, a placeholder
Telegram `accountId`, no token. Its tests assert internal consistency **only**,
and the module docstring says outright that nothing here checks OpenClaw
accepts it.

Judgment calls inside R8, all flagged in the README's step 6 as "verify with
`openclaw config schema`":

- **`model` is omitted** rather than guessed, so the agent inherits the global
  default; inventing a model id would have been a fabrication that fails at
  runtime.
- `sandbox.mode: "read-only"` and `tools.allow: ["mcp__robot__*"]` are the
  best-supported spellings I have, but the *value vocabularies* are not
  something this repo can verify — the README names them as the two most
  likely to differ between builds.
- `toolFilter.include` lists all nine tools and is asserted equal to
  `TOOL_NAMES`, which makes the fragment a drift guard too (a new skill fails
  the suite until the fragment lists it).

**R7 verification, done and not done.** `pixi`'s manifest-deprecation warning
goes to **stderr** — confirmed by running the documented command with stdout
and stderr captured separately and reading back a single clean JSON-RPC frame
on stdout. The **SSH leg itself is unverified**: this worktree cannot reach the
Pi, and `ssh -T laptop` is the Pi's route, not the laptop's. README step 4 is
that check, for whoever runs it there.

### 5. Baseline (F7)

`--update-baseline` re-cut from a green run: `robot_safety 0 → 176` (its suite
landed after the baseline was last written), `robot_brain 0 → 37`,
`robot_mcp 52 → 67`, `robot_backends 59 → 60`.

## Red-team round 1 — what changed

All three BLOCKs were claims that outran their evidence, and all three are
fixed as text plus assertions. Nothing in the running safety path changed.

**B1 — the keep-out guard *is* routable around, and the test said otherwise.**
The red team's arithmetic is right: `NavigateTo` has
`has_cartesian_target=False`, so the guard never looks, while
`_carry_held_objects` re-poses the held object onto the gripper — landing it at
`(2.30, ±0.10, 0.75)`, inside the test's own box, before the `place` is ever
refused. I fixed the *claim*, not the guard: the test now says what the guard
actually does (judges the commanded target pose of a cartesian skill) and
**asserts the limitation** — the carried object's position is proven to be
inside the forbidden box while the place into it is refused, with a note that
if the assertion ever fails, the guard grew a swept-volume check and the test
should be rewritten to celebrate it. `robot_mcp/README.md` gains the gap next
to the other honest limitations: a chore can move an object *through* a region
without a verdict; closing that is MoveIt's swept-volume work (invariant 5).

**B2 — `reset` withheld from the agent, and the test turned into a
classification.** `toolFilter.include` drops `reset`; the prompt drops its row
and every mention of it and instead states plainly that there is no undo. The
config assertion is now `exposed == TOOL_NAMES - WITHHELD_TOOLS`, where
`WITHHELD_TOOLS` maps a tool name to the reason it is withheld (shared in
`brain_fixtures.py`, since both the config suite and the prompt suite need it).
Drift protection is intact in both directions — a tool added to the seam fails
both suites until somebody decides which side it belongs on — but the half that
made "hand every future tool to the LLM" the path of least resistance is gone.
`WITHHELD_TOOLS` names are asserted to be real tools carrying a real reason, so
the list cannot go stale or become a shrug, and the prompt's vocabulary guard
now rejects a withheld tool's name as firmly as a fictional one. Rationale is
in `robot_brain/README.md`. **I agree with the ruling**: against Mock it is
harmless, but this is the boundary a Sim/Real backend will front, where
`reset()` is real motion and real lost state.

**B3 — the mandatory guards are absent, and now say so.** Not built (they are
deferred D16 items and out of #46's scope). `robot_mcp/README.md`'s
"Deliberately absent" section now names max-steps, chore timeout,
stuck-detection and one-task-at-a-time, and — the part that matters — why the
three things that *look* like bounds are not: a prompt sentence is not
enforcement, `requestTimeoutMs` bounds a call rather than a chore, and the
router's lock serializes calls rather than tasks. `robot_brain/README.md`
mirrors one paragraph. Manager files it as **F10**.

**NOTEs fixed in the same round.**

- **N1** — `default_safety_layer` answers the no-regions case with
  `if not limits.keep_out_boxes:` and catches nothing at all, so "any other
  error still raises" is structural rather than a fact about how many `raise`
  statements another package currently has.
- **N2** — the milestone's headline assertion no longer rests on a 0.19 m
  margin. The driver returns `{object_id: the pose it was commanded to}`, and
  the test asserts each cleared object is unheld and at *exactly* that
  position, with no two objects sharing a spot. `clutter(...) == []` stays as
  the secondary, agent's-eye reading.
- **N3** — each drop slides `0.15 m` to the side of the last, and the prompt
  teaches the same. Stacking two rigid bodies at one point is a Mock artefact
  nobody should learn from.
- **N4** — README step 6 now says to check the agent can still *reply* on
  Telegram, and that `tools.allow` is the first thing to relax if not.
- **N5 — verified, and here is exactly what was observed.** I extracted the
  shipped `args` array from the fragment, substituted this worktree's path, and
  ran the **inner command through `bash -lc`** with stdout and stderr captured
  separately: stdout was **1024 bytes, one line, first byte `{`** — a single
  JSON-RPC frame and nothing else; `pixi`'s manifest warning went to stderr
  (676 bytes). A bare `bash -lc true` on this account produced **0 bytes on
  stdout and 0 on stderr**, so the login shell itself is silent here. The
  `ssh` hop remains unverified and cannot be tested from this machine: `ssh -T
  laptop` fails with `Could not resolve hostname laptop` — that alias is the
  *Pi's*. So the form ships unchanged, and `robot_brain/README.md` step 4 now
  states precisely which half was checked, which was not, and to drop `-l` if
  a login shell on the real machines turns out to print anything.

**N6 and N7 were declined by the manager** and survive as NOTES for Sisyphus:
the vocabulary guard is a bag of words rather than a grammar (documented
tradeoff), and `_gated_execute` costs one extra `get_observation()` per call
(correctness over micro-optimisation on Mock).

## Commands run, and their outcomes

| command | result |
|---|---|
| `pixi run build` | 8 packages finished, clean |
| `pixi run test` (before R12) | 584 tests, 0 errors, 0 failures, 0 skipped |
| `pixi run test` (after R12) | 588 tests, 0 errors, 0 failures, 0 skipped |
| `pixi run test` (after red-team round 1) | **589 tests, 0 errors, 0 failures, 0 skipped**; audit passed, all stages passed |
| `pixi run python scripts/check_test_integrity.py --update-baseline` | three times, each from a green run: the initial re-cut (4 packages), `robot_mcp 67 → 71` after R12, `robot_brain 37 → 38` after round 1 |
| per-package `pytest` (fast loop) | robot_skills 109, robot_backends 63, robot_safety 179, robot_mcp 74, robot_brain 41 |
| shipped `args[4]` under `bash -lc`, streams split | stdout: one 1024-byte JSON-RPC frame; stderr: pixi's warning. `bash -lc true`: 0 bytes on both |
| `ssh -T -o BatchMode=yes laptop true` | `Could not resolve hostname laptop` — the ssh hop is the Pi's to verify |
| documented stdio command, stdout/stderr split | stdout: one JSON-RPC frame; stderr: pixi's manifest warning |

The one intermediate red: `scripts/tests/test_ratchet.py` failed once
`robot_brain` gained code — see below. Fixed in the same commit that caused it.

## Outside the owned paths — forced, flagged, not scope creep

Both edits below were *made red by this feature* and could not be left alone;
neither adds capability, and the manager has confirmed keeping them.

1. **`scripts/tests/test_ratchet.py`** asserts an inventory of which packages
   are skeletons, and it listed `robot_brain`. R5 deliberately ends that
   status, so the moment `robot_brain` gained `agent.py` the workspace-tooling
   suite went **red** (`AssertionError: robot_brain is no longer a skeleton`).
   The guard's own docstring names this exactly: "either a skeleton package
   grew code (and now owes real tests) or the detection became too eager". It
   is the first case, and the package pays what the guard demands — 37 real
   tests. The fix is one line (move the name from the skeleton list to the
   has-implementation list) plus a comment saying why. **No behaviour of the
   guard changed**, and the alternative — leaving it red — would have broken
   the whole test gate for an unrelated package.
2. **`src/robot_safety/README.md`** — its status paragraph asserted that
   "wiring into the brain loop [is a] separate feature". This feature *is* that
   wiring, so the sentence became **false on merge**. Replaced with who calls
   the layer now, plus the honest limits (only the MCP seam is gated; three
   checks cannot fire without telemetry). Documentation only — **no code in
   `robot_safety` was touched**, and the package stays read-only per the brief.

## Things a reviewer should look at hardest

- **The keep-out geometry is live but it is still a stub.** Since R12 the
  default server enforces `limits.yaml`'s regions, which is a real check on a
  hallucinated target — but it tests the *goal pose* against axis-aligned
  boxes. No robot model, no mesh, no swept volume, one configured half-space.
  Read `robot_mcp/README.md`'s "what actually bites today" as the accurate
  statement of coverage; anything stronger would be overselling it.
- **Composed `reason` strings are prose, not structure.** A consumer that wants
  to know *what* was clamped must read English or diff the skill it sent
  against `result['skill']`. That is R1's deliberate deferral (F3), but it is
  the weakest part of the wire contract.
- **`_gated_execute` costs one extra `get_observation()` per skill call.**
  Free against Mock; a Sim/Real backend where perception is expensive will want
  to look at this again.
- **The prompt's vocabulary guard can produce a false failure** if someone
  backticks an ordinary English word in the prompt. That is intended (it forces
  the convention), but it is the test most likely to annoy a future editor —
  the convention is documented at the top of `brain_fixtures.py`.
- **Nothing here proves an LLM can do it.** The milestone test is a
  deterministic driver standing in for the model, and the whole Telegram leg is
  unverified from this worktree (R11, F2).

## Surviving follow-ups (manager posts; I file nothing)

F1–F7 from `status.md` all stand. Two more surfaced during implementation, and
R12 narrowed the first of them:

- **F8 — should `SafetyLayer()` itself default to enforcing its configured
  keep-out boxes?** R12 closed the gap at the MCP seam
  (`robot_mcp.default_safety_layer`), so this is no longer a hole in the
  product; what remains is `robot_safety`'s own choice of default, which every
  *other* future caller inherits. `robot_safety`'s docstring argues both sides
  (a null guard is honest for a package with no robot model; a guard built
  from config that vetoes nothing is a dead parameter). That package's
  decision, not this feature's. Paths: `src/robot_safety/robot_safety/layer.py`,
  `src/robot_safety/robot_safety/collision.py`.
- **F10 — the PROJECT.md-mandatory server-side guards are unbuilt** (max-steps,
  chore timeout, stuck-detection, one-task-at-a-time). Deferred D16 items, now
  documented as absent rather than implied present (B3). Paths:
  `src/robot_mcp/robot_mcp/server.py`, `docs/design/PROJECT.md`.
- **F11 — the keep-out guard judges commanded target poses only**, so a
  carried object or a driving base passes through a region unjudged (B1).
  Needs swept-volume collision against the base's route — MoveIt's job.
  Paths: `src/robot_safety/robot_safety/collision.py`.
- **F9 — the OpenClaw config fragment hard-codes
  `/home/sisyphus/worktrees/main`** in `PYTHONPATH` and `--manifest-path`, and
  the SSH alias `laptop` must resolve on the Pi. Left as-is per the manager;
  `robot_brain/README.md` step 3 now calls out all three explicitly, says none
  was verified against a real Pi, and warns that a stale path fails as "the
  agent has no tools" rather than as a readable error. A generator or an
  env-var indirection would fix it properly. Paths:
  `src/robot_brain/robot_brain/openclaw/openclaw.robot.json`.
