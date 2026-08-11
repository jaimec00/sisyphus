# Status — #46 First milestone: OpenClaw `robot` agent → robot_mcp → Mock (D21)

Slug: `i46-openclaw-robot-agent` · Branch: `feat/i46-first-milestone-openclaw-robot-agent-rob`
Worktree: `/home/sisyphus/worktrees/i46-first-milestone-openclaw-robot-agent-rob`

## Phase log
| # | Phase | State |
|---|---|---|
| 0 | Sync with `origin/main` (`f9ee2b7`) | done — branch was level with origin/main |
| 1 | Read brief (issue #46, body present) | done |
| 2 | Provision deps + probe installed API | done — **no new dependency**; `mcp==2.0.0` already pinned in `pixi.toml` and probed (`importlib.metadata.version('mcp') == '2.0.0'`, `mcp.Client` present, `mcp_types` top-level). OpenClaw itself is **not installable here** (see R8). |
| 3 | context-explorer → `context.md` | done |
| 4 | Manager rulings | done — R1–R11 below |
| 5 | implementer → code + tests + `implementation.md` | done — 8 commits, `0341d2b`…`0dd208c`. Implementer reported the full gate green (584 tests) before R12; R12 re-ran green and re-cut the baseline (`robot_mcp` 67 → 71). |
| 5a | Manager ruling R12 (raised by the implementer as F8) | done — issued mid-run, landed as `0dd208c` |
| 6 | red-team → `red_team.md` | done — **3 BLOCK, 7 NOTE**. Every hypothesis about the safety wiring itself was checked and cleared (R1's abort path, R3's chokepoint, R12's exception scope, the milestone test's non-vacuity, R10's arithmetic, the drift guards' vacuity traps, the secret scanner). All three BLOCKs are claims that outrun their evidence, not defects in running code. |
| 7 | fix rounds (≤2) | **round 1 done, all 3 BLOCKs closed** — `77381fd` (B1: the milestone test now states the guard's real scope and *asserts* the carried-object gap instead of commenting it away), `2ae80df` (B2: `reset` withheld from the agent in the fragment and the prompt; the drift test became `exposed == TOOL_NAMES - WITHHELD_TOOLS`, a name→reason mapping), `efd6197` (B3: `robot_mcp/README.md` names all four absent guards and why the three things that *look* like bounds are not), `89553e7` (N1–N3), `05c2a5e` (docs + baseline). N4/N5 folded in; **N5 was verified empirically** — `bash -lc` on the shipped args puts one JSON-RPC frame and nothing else on stdout, pixi's warning on stderr; the ssh hop itself is untestable here (`laptop` is the Pi's alias). N6/N7 declined → surviving NOTES for Sisyphus. **Round 2 not needed.** |
| 8 | test-runner (`pixi run test`) | **PASS** — `pixi run build` exit 0, `pixi run test` exit 0; 588 tests, 0 errors / 0 failures / 0 skipped, 561 non-linter across 9 packages, every package at `vs-base: +0` or better, none flagged. Log: `.dev/runs/i46-openclaw-robot-agent/20260811-184324/test.log` |
| 8b | test-runner re-run after fix round 1 | **PASS** — build exit 0, test exit 0; 589 tests, 0 errors / 0 failures / 0 skipped, 562 non-linter, every package `vs-base: +0`. Log: `.dev/runs/i46-openclaw-robot-agent/20260811-190227/test.log` |
| 9 | rebase on current main, open PR, report "ready" | **done** — `origin/main` had not moved (`f9ee2b7`), 0 commits behind, so no rebase was needed. Branch pushed; **PR #50** open and `MERGEABLE`. |
| 10 | outward comments (manager-only) | **done** — follow-ups F1–F12 on issue #46 (`#issuecomment-5259871402`); retro on PR #50 (`#issuecomment-5259874300`) |

## Final state
**Ready for Sisyphus.** PR: https://github.com/jaimec00/sisyphus/pull/50

The `docs-clean` CI check reads **fail** — expected and correct: this directory is still
present for review, and Sisyphus deletes it at merge. That guard is CI's only check;
the authoritative test gate is the laptop `pixi run test` run above.

**Do not read the live Telegram leg as verified.** OpenClaw is not installed on this
laptop and the Pi is unreachable from it, so the config fragment's field names are
documentation-derived, the SSH hop is untested, and nothing here proves an LLM closes
the loop. `src/robot_brain/README.md` carries the numbered Pi-side procedure for that run.

## Blockers
None.

## Run notes
- The implementer session was terminated once by an Anthropic session limit (17:0x, resets 18:30 ET) **after** it had committed R12 and re-cut the baseline. On-disk state was verified clean and complete (`git status` empty, `implementation.md` updated in `0dd208c`); no work was lost and the worker was not respawned.

---

## Manager rulings (binding, not assumed correct)

A downstream agent that believes a ruling is wrong **escalates to me in-process**
— it must neither silently deviate nor comply into a bug.

### R1 — The safety verdict rides inside `SkillResult`; the tool payload shape does not change
The payload of a skill tool call stays **exactly `SkillResult.to_dict()`**, so it
remains parseable by `SkillResult.from_dict` and `schema_version: 1` keeps telling
the truth. **No field is added to `SkillResult`, and `src/robot_skills` is not
touched.**

- **Abort** (`SafetyLayer.filter` returned a `SafetyEvent`): nothing executes.
  Return `SkillResult.failure(skill, observation, FailureCode.REJECTED,
  event.detail)`, where `observation` is the backend's **current** observation
  (the scene is unchanged) and `event.detail` is passed **verbatim** —
  `robot_mcp` does not re-word safety vocabulary.
  `SafetyEvent.failure_code` already returns `REJECTED` for every kind
  (`robot_safety/events.py:100-110`); use it rather than hard-coding the constant.
- **Clamp** (`ClampedCall.was_clamped`): execute `call.skill` (the *rewritten*
  skill, so `result['skill']` shows the agent what actually ran, e.g.
  `height: 1.2` when it asked for `9.0`), then return that result with each
  clamp's `detail` composed into `reason`. Backend note and safety note are
  joined with `'; '` when both exist; neither is reworded or dropped.
- **Pass-through**: byte-identical to today. `ClampedCall.skill is skill` when
  nothing was clamped, so the existing `test_tool_calls.py` assertions
  (`payload(...) == reference.execute(...).to_dict()`) must **still pass
  unchanged**. If any of them break, that is a bug in the wiring, not a test to
  edit — escalate to me before editing one.

*Rationale:* `FailureCode.REJECTED` was built in #43 for exactly this moment
(`events.py:22-23`: "the later 'wire a safety layer into the loop' feature has
exactly one seam to widen"). Option (b) from `context.md` §7.1 — a typed `safety`
field on `SkillResult` — would put safety vocabulary into `robot_skills`, which
sits *below* `robot_safety` and deliberately does not know it. That is a layering
change, not an additive field, and it is not #46's to make. Recorded as a
follow-up instead (F3).

### R2 — The gate lives in `robot_mcp`'s router, not in a new backend decorator
Wire `SafetyLayer` into `SkillToolRouter._payload`, inside the existing
`anyio.Lock`, between `skill_from_dict(...)` and `self._backend.execute(...)`.
Build the `SafetyState` from the backend's own observation taken under the same
lock. Do **not** add a `SafeBackend` `RobotBackend` decorator in `robot_safety`.

*Rationale:* a decorator would make `robot_safety` depend on `robot_backends` at
runtime and add a second implementation of the backend contract to keep in sync,
and it would make safety aborts indistinguishable from backend refusals to a
non-MCP caller. The MCP seam is the only brain-facing seam that exists today
(D21), and R3 makes it unbypassable there. The wider "every backend caller is
gated" question is a follow-up (F4).

### R3 — Safety is injectable but never disableable
`build_server(backend=None, safety=None)`; `safety=None` means **`SafetyLayer()`
with the shipped `limits.yaml` defaults**, never "no safety". One `SafetyLayer`
per server, held by the router (it is pure and stateless — `layer.py:9-16`).
There must be **no** argument, env var or code path that yields a server with no
gate (invariant 3). Injectability exists so tests can drive the abort/clamp paths
(e.g. `SafetyLayer(collision_guard=KeepOutBoxGuard(...))`, or a `SafetyLimits`
with a tighter column range) — add a test that asserts the *default* server
clamps, so the default is guarded too.

### R4 — `SafetyState` gets the observation and nothing else, and the README says so plainly
Construct `SafetyState(observation=<current observation>)`. Leave
`estop_engaged`, `velocities` and `gripper_forces` at their defaults: **no
telemetry source exists anywhere in the repo** (`context.md` §1, verified by
grep), and inventing one would be fiction. Do **not** add a telemetry-callback
hook to the router — that is speculative generality until a backend has telemetry.

Consequence to state **honestly** in `src/robot_mcp/README.md` (do not oversell):
against Mock today the live checks are the unclassified-skill refusal, the
(null) collision guard and the `ExtendColumn` height clamp; e-stop, velocity and
gripper-force are wired and reachable but can never fire until a backend
publishes telemetry. Follow-up F5.

### R5 — The OpenClaw agent assets live in `src/robot_brain`
`src/robot_brain` is today a dead skeleton whose README still describes the
tag-parser planner loop that D21 deleted. It becomes the home of the brain's
**operating prompt + OpenClaw config**, because the brain *is* that prompt now.
This also gives the assets a ratcheted test home, which a top-level directory
would not have (`context.md` §5).

Ship, following `robot_safety`'s `limits.yaml` precedent (`package_data` +
`importlib.resources`, so it loads from a source checkout and a symlink-install
alike):

```
src/robot_brain/robot_brain/openclaw/AGENTS.md            # the operating prompt
src/robot_brain/robot_brain/openclaw/openclaw.robot.json  # config MERGE FRAGMENT
src/robot_brain/robot_brain/agent.py                      # loaders + the agent id
src/robot_brain/README.md                                 # rewritten for D21 + install + manual verification
src/robot_brain/test/…                                    # the drift guards (R6) + test_no_ros_runtime.py
```

Also: drop `<depend>rclpy</depend>` from `src/robot_brain/package.xml` (the brain
is not a ROS node under D21) and add the sibling `<test_depend>`s it needs.
`SOUL.md` only if it earns its place — do not ship an empty file for symmetry.

### R6 — The prompt is hand-written prose, guarded by drift tests
Quality of the prompt *is* the deliverable (LLM operability, D22), so do not
generate it from the catalogue. Instead make it impossible for it to drift, with
tests in `src/robot_brain/test/` that assert against the **live** sources:

1. every name in `robot_mcp.tools.TOOL_NAMES` appears in the prompt, and the
   prompt names no tool that does not exist;
2. for each skill tool, the argument names the prompt teaches match that tool's
   `inputSchema` properties (a renamed/added argument must fail this test);
3. every number the prompt states as the safety envelope equals the corresponding
   value from `robot_safety.SafetyLimits.defaults()` (column travel range,
   velocity caps, max gripper force) — no hand-typed constants;
4. every location name and object id used in the prompt's worked examples exists
   in `robot_backends.default_world()`.

The prompt must carry what PROJECT.md:29 requires: skill API (names/args/units),
observation format, safety envelope, and 2–3 worked examples — including one
where a skill comes back `status: "failed"` and the agent recovers, and one where
a safety clamp changes what ran. It must also teach the `place` pose problem (R9).

### R7 — Transport: stdio over SSH, Pi → laptop
OpenClaw runs on the Pi; `robot_mcp` speaks **stdio only** and runs on the
laptop, so `command: "python"` in the fragment would be wrong. Use the existing,
proven Pi→laptop SSH path (`scripts/pi/dispatch.sh` already uses the host alias
`laptop`):

```
"command": "ssh", "args": ["-T", "laptop", "bash", "-lc", "<the robot_mcp command>"]
```

`-T` (no pty) because a pty would corrupt the MCP byte stream; verify that
nothing but MCP frames reach stdout (pixi's manifest warning goes to stderr —
confirm, do not assume). The remote command is `robot_mcp/README.md`'s own
`pixi run --frozen --manifest-path … python -m robot_mcp` form, with
`src/robot_safety` **added to `PYTHONPATH`** (new dependency — also update
`robot_mcp/README.md`'s two command blocks and its client-config JSON). A
streamable-http transport is a follow-up if SSH proves flaky (F6).

### R8 — OpenClaw config is docs-verified, not execute-verified — say so
OpenClaw is not installed on this laptop and the Pi refuses our key, so the
config fragment **cannot** be validated against a real OpenClaw here. Field names
below are from `docs.openclaw.ai` (fetched this run) and are the contract to
follow:

- custom agents: `agents.entries.<agentId>` with `default`, `name`, `workspace`,
  `model`, `skills`, `sandbox.mode`, `tools.{profile,allow,deny}`;
- an agent's system prompt comes from **workspace files** `AGENTS.md` / `SOUL.md`
  / `USER.md` — there is **no** `instructions`/`prompt`/`systemPrompt` field;
- MCP servers: `mcp.servers.<name>` with `command`, `args`, `env`, `enabled`,
  `transport`, `requestTimeoutMs`, `toolFilter.{include,exclude}`;
- Telegram routing: top-level `bindings: [{agentId, match: {channel, accountId}}]`;
- per-agent workspace/state: `~/.openclaw/agents/<agentId>/`.

Rules that follow: the file is a **merge fragment**, not a drop-in
`openclaw.json` — its README section says exactly which keys to merge and tells
the operator to verify on the Pi with `openclaw config schema` and
`openclaw agents list --bindings`. Tests may assert our fragment's **internal**
consistency (parses; declares `mcp.servers.robot`; `agents.entries.robot` exists
and is scoped to that server; the binding names `agentId: "robot"`; the command
references `robot_mcp` and carries `robot_safety` on `PYTHONPATH`). Tests must
**not** claim OpenClaw accepts it. **No secrets**: the fragment carries no bot
token — the README says to add the Telegram account token on the Pi via the
`openclaw` CLI. Add a test asserting the fragment contains no token-shaped value.

### R9 — `place` takes a metric pose; teach it, do not redesign it
`Place(pose, side=None)` needs coordinates, which sits in tension with
PROJECT.md:28 ("no hand-typed coordinates to the LLM"). Redesigning the skill
(e.g. `place(location=…)` or `place_on(object_id)`) is a **design fork and out of
scope** — record it as follow-up F1. In scope: the prompt teaches the agent to
derive a place pose from the observation it already has (offset from its own
pose, or from a known object's pose) and to retry nearer on `out_of_reach`, and
one worked example demonstrates exactly that.

### R10 — `default_world()` gains a second graspable object on the table
"Clear the table" against today's seed world is a single grasp — not a loop.
Add **one** graspable object at `table` (suggest `cup_1` / label `cup`, posed
within reach from the `table` stand point — check the arithmetic in `context.md`
§6, do not eyeball it), and update every assertion it breaks **in the same
commit**. Keep the change to one object; do not restructure the world.
**Escalate to me** if it breaks more than a handful of assertions or if any
golden fixture encodes the world.

### R11 — What "done" means here, given OpenClaw cannot run on this laptop
The issue's "Done when" needs a live Telegram round-trip on the Pi, which this
worktree **cannot** execute. Deliver both halves and be explicit about the seam:

- **Automated (this PR must prove it):** an end-to-end milestone test in
  `src/robot_mcp/test/` that drives a real in-process MCP client
  (`mcp_fixtures.connected`) through the full "clear the table" loop —
  `get_observation` → `navigate_to(table)` → `grasp` → `navigate_to(kitchen)` →
  `place(...)` for **each** table object — and asserts the table ends clear, plus
  that safety is genuinely in the path (a clamped `extend_column`, and an abort
  from an injected guard) and that a refusal (`out_of_reach`) comes back as a
  readable `status`/`code` the loop recovers from. This is the stand-in for the
  LLM, not a claim that the LLM was tested.
- **Manual (documented, not run here):** `src/robot_brain/README.md` carries a
  numbered Pi-side verification procedure — merge the fragment, create the
  workspace, copy `AGENTS.md`, add the Telegram account, `openclaw agents list
  --bindings`, then text "clear the table" and check the tool-call log.

The PR description and the "ready" signal must state plainly that the live
Telegram leg is unverified from this worktree and is Sisyphus's/Jaime's to run.
Do not word anything as if it had been observed working.

---

## Follow-ups to raise (manager posts these on the issue at step 10)
- **F1** — `place` requires hand-typed metric coordinates from the LLM, against
  PROJECT.md:28. Consider a `place_on(object_id)` / `place_at(location)` skill.
  Paths: `src/robot_skills/skills.py`, `src/robot_backends`, `src/robot_mcp`.
- **F2** — Live Pi-side verification of the OpenClaw `robot` agent (R11) is
  untested from the laptop; needs one manual run.
- **F3** — Typed safety detail on the wire (`SafetyEventKind`, `limit`,
  `clamped_value`), which R1 deliberately deferred; needs a layering decision
  about where the shared vocabulary lives.
- **F4** — Safety gating for non-MCP backend callers (R2 gates the MCP seam only).
- **F5** — No telemetry source for e-stop / axis velocity / jaw force, so three
  of the six safety checks can never fire (R4).
- **F6** — streamable-http transport for `robot_mcp`, if stdio-over-SSH (R7)
  proves flaky over WireGuard.
- **F7** — `scripts/test_baseline.json` records `robot_safety: 0` while ~87 real
  tests exist; this PR should run `--update-baseline` (R-adjacent, cheap here).
