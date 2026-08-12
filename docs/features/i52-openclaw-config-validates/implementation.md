# implementation — i52: `openclaw.robot.json` validates against the real schema

Issue #52. Rulings R1–R12 in `status.md` are the spec (R2 and R4 **revised**
after round 1; R12 added). Every one is implemented, none deviated from.
Everything below was **run**, not recalled — OpenClaw 2026.7.1-2 from
`node/node_modules/.bin/openclaw` in this worktree.

> **Round 1 (red-team → fix) is folded in.** The revised decisions are marked
> **[R1]** where they replace something this document previously said. The one
> substantive reversal — sandboxing on → off — is written up in
> "[R1] R2 reversed" below, including why the original rationale was wrong.

## What changed

| Path | Change |
|---|---|
| `src/robot_brain/robot_brain/openclaw/openclaw.robot.json` | `agents.entries{}` → `agents.list[]` with `"id"`; `sandbox: {"mode": "off"}`; `tools.allow` respelled |
| `src/robot_brain/robot_brain/agent.py` | `AGENT_ID` comment: no longer "the key under `agents.entries`" (R9) |
| `src/robot_brain/test/test_openclaw_config.py` | docstring rewritten (R9); `agent()` helper (R11); three `entries` assertions reshaped; three new tests |
| `src/robot_brain/test/test_openclaw_validates.py` | **new** — shells out to the real CLI (R5/R6/R7) |
| `src/robot_brain/README.md` | R8 |
| `scripts/start-feature.sh`, `DEVELOPMENT.md` | **[R1]** R12 — bootstrap installs OpenClaw |
| `scripts/test_baseline.json` | `robot_brain: 38 → 48` (R10) |

## The config

```json
"agents": {
  "list": [
    {
      "id": "robot",
      "default": false,
      "name": "robot",
      "workspace": "~/.openclaw/agents/robot",
      "skills": [],
      "sandbox": { "mode": "off" },
      "tools": { "allow": ["robot__*"] }
    }
  ]
}
```

`mcp.servers` and `bindings` are untouched.

### Every ruling re-checked against the live CLI before implementing

Not taken on faith. Pulled `openclaw config schema` (2.4 MB) into a scratch
`HOME` and read the relevant subtrees:

- `properties.agents` → `properties == ['defaults','list']`,
  `additionalProperties == False`. **R1 confirmed**: `entries` cannot exist.
- `agents.list.items` → `required == ['id']`, `additionalProperties == False`,
  and `default/name/workspace/skills/sandbox/tools` are all legal item keys —
  so the reshape carries every field over without dropping anything.
- `…items.properties.sandbox.properties.mode` →
  `anyOf[const off|non-main|all]`; `.workspaceAccess` →
  `anyOf[const none|ro|rw]`. Both the original R2 values and the revised
  `mode: "off"` are in the enum; `"read-only"` never was.
- `…items.properties.tools.properties.sandbox` → an object with one property
  `tools: {allow, alsoAllow, deny}`, `additionalProperties: false` at both
  levels. R4's shape is schema-legal at the agent level — which is why the
  revised R4 can keep it as a *conditional* invariant rather than dropping the
  knowledge along with the key.

Then, on the shipped file:

```
$ OPENCLAW_CONFIG_PATH=…/openclaw.robot.json openclaw config validate   # before
OpenClaw config is invalid: …/openclaw.robot.json
  × agents: Invalid input
EXIT=1

$ …                                                                     # after
Config valid: …/src/robot_brain/robot_brain/openclaw/openclaw.robot.json
EXIT=0
```

### R3 and R4 independently re-verified (the manager asked for this)

Both rulings rest on `openclaw doctor` output, so I reproduced the differential
rather than trusting the transcript. Method: three copies of the *fixed*
fragment, each with the `robot` MCP server rewired to a locally-runnable
command (`bash -lc 'PYTHONPATH=… exec python -m robot_mcp'`) so the server
really starts and "failed to load" is not the confound. `openclaw doctor`,
scratch `HOME`/`OPENCLAW_STATE_DIR`:

| variant | doctor says |
|---|---|
| `robot__*`, gate present, `mode: "all"` | neither warning |
| `tools.allow: ["mcp__robot__*"]` | `[tools] agents.robot.tools.allow allowlist contains unknown entries (mcp__robot__*). These entries won't match any tool unless the plugin is enabled.` |
| `tools.sandbox` removed, `mode: "all"` kept | `tools.sandbox.tools.alsoAllow (unset) does not include "bundle-mcp", … Sandboxed agents will filter bundled MCP tools before provider requests. Add "bundle-mcp" to tools.sandbox.tools.alsoAllow …` |
| **[R1]** as shipped now (`mode: "off"`, no gate) | neither warning — the gate really is inert with the sandbox off |
| **[R1]** `mode: "all"`, gate removed again | the `bundle-mcp` warning returns |

**R3 holds.** R4's *mechanism* holds too — the gate is real and agent-level —
but round 1 removed the need for it; see below. Also confirmed R2's premise
from OpenClaw's own docs (`node_modules/openclaw/docs/gateway/sandboxing.md:17`):
sandboxing covers `exec`, `read`, `write`, `edit`, `apply_patch`, `process` —
none of which this agent is allowed — and `:33` gives `backend` default
`docker`.

### [R1] R2 reversed — sandboxing is now `off`, and why the first answer was wrong

The original R2 (`mode: "all"` + `workspaceAccess: "ro"`) rested on "`mode:
"all"` costs nothing at runtime — no sandboxed tool class is allowed, so no
container is ever built". The red-team attacked the "costs nothing" half
(NOTE-1) and the manager verified the core claim in the installed dist:

```js
// node_modules/openclaw/dist/compact-DLB4d8IL.js:551
const effectiveWorkspace = sandbox?.enabled
  ? (sandbox.workspaceAccess === "rw" ? resolvedWorkspace : sandbox.workspaceDir)
  : resolvedWorkspace;
```

`sandbox.enabled` follows `mode`, not "was a sandboxed tool ever called". So
with the sandbox on and `workspaceAccess` anything but `"rw"`, the *effective*
workspace becomes the sandbox workspace — and the compaction path feeds that
into bootstrap-context resolution. The agent's operating prompt is `AGENTS.md`
**in the agent workspace** (`test_openclaw_config.py` already asserts the
workspace must be the agent's own directory, because OpenClaw has no `prompt`
field). A long Telegram conversation could therefore compact and come back
without the brain.

Weighed against a sandbox that, by the original ruling's own reasoning,
protects nothing today: not a trade worth making. `sandbox: {"mode": "off"}`,
and `workspaceAccess` dropped rather than left inert. This validates equally
(`Config valid`, exit 0 — re-run on the shipped file) and retires the
Docker-on-the-Pi prerequisite.

**This is a deliberate deviation from the issue's own snippet**, which named
`mode: "all"`. The issue proposed that value to make the config *validate*;
`"off"` validates just as well and does not risk the prompt. Flagged for the PR
description.

I verified the *reversal* too, not just the reasoning: the differential doctor
table above gained two rows. `mode: "off"` genuinely silences the `bundle-mcp`
warning (so the gate really is inert and shipping it would be noise, per the
revised R4), and flipping the mode back on brings the warning straight back
(so the conditional invariant test has something real to guard).

## Test design

### `test_openclaw_config.py` — what the validator *cannot* catch

The docstring's old opening ("**We cannot check that OpenClaw accepts it**") is
gone; the file now states the complementary boundary, which is the honest one:
`tools.allow` is `array<string>` in the schema, so **every** spelling
validates, including the one that matched no tool. A config can validate and
still hand the brain nothing to drive. That is what this file guards.

Reshaped, none deleted — every guarantee preserved:

- `test_the_agent_the_binding_routes_to_is_the_agent_configured` —
  `set(entries) == {AGENT_ID}` became
  `[e['id'] for e in agents['list']] == [AGENT_ID]`, which keeps both halves
  (exactly one entry; it is `AGENT_ID`) and additionally pins ordering-free
  agreement with `bindings[].agentId`. `name` and `workspace` unchanged.
- `test_the_agent_is_scoped_to_the_robot_tools` — **strengthened**. Was
  `all(MCP_SERVER_NAME in entry …)`, a substring test that `mcp__robot__*`
  passes. Now an exact match, and **[R1]** the expected glob is derived from
  the `mcp.servers` key actually shipped rather than from the `MCP_SERVER_NAME`
  constant, so renaming the server without rewriting the allowlist fails here
  instead of at the agent's first tool call.
- Everything else (secrets scan, launch command, tool filter, timeout, asset
  names) untouched.

New:

- **[R1]** `test_the_server_name_needs_no_provider_safe_mangling` — closes
  NOTE-2. Deriving `<server>__*` from the key is only sound while the key
  survives OpenClaw's prefix mangling unchanged (lower-cased, non-`[A-Za-z0-9_-]`
  → `-`, non-letter start gets `mcp-`; `mcp.servers["Outlook Graph"]` globs as
  `outlook-graph__*`, `docs/gateway/config-tools.md:59`). `robot` survives it;
  a rename to `Robot Arm` would not, and would re-enter Bug A by a side door.
- **[R1]** `sandbox_complaints()` + two tests — the revised R4. A *detector*
  rather than a run of asserts, because the shipped fragment has sandboxing off
  and every rule is vacuous against it; a test that only ran them against the
  fragment would be a test of nothing. This is the same shape the file already
  uses for the secrets scanner ("a scan that cannot recognise the thing it is
  looking for is worse than no scan"). Three forbidden states:
  - `mode != 'off'` without a `bundle-mcp` / `group:plugins` / `robot__*`
    admission in `tools.sandbox.tools` — the trap, doctor-confirmed;
  - `mode == 'off'` with `workspaceAccess` — an inert setting that reads like a
    restriction;
  - `mode == 'off'` with a `tools.sandbox` gate — an inert key.

  `test_the_sandbox_consistency_check_detects_what_it_forbids` feeds it three
  synthetic broken entries **and** two coherent ones (so it cannot pass by
  crying wolf); `test_the_shipped_sandbox_settings_do_not_contradict_each_other`
  points it at the fragment.
- **[R1]** the hand-copied `('off', 'non-main', 'all')` enum is **gone**. What
  is a legal `mode` is the validator's question now (NOTE-3); copying the enum
  out of documentation is precisely the habit that produced #52.

`agent()` is test-local (R11), mirroring `server()`, and asserts the id is
unique — `agents.list` is an array, so duplicate ids are now expressible where
they were not under a map.

### `test_openclaw_validates.py` — the shell-out

Seven tests. Repo root by marker walk from
`Path(robot_brain.__file__).resolve()` for `pixi.toml` (R6); binary at
`<root>/node/node_modules/.bin/openclaw`, invoked directly, never through
`pixi run openclaw` (which `depends-on` `install-openclaw`). Hermetic: `HOME`,
`OPENCLAW_STATE_DIR`, `OPENCLAW_CONFIG_PATH` all under `tmp_path`; `PATH`
inherited deliberately, since the shim is `#!/usr/bin/env node`.

**[R1]** Two hardening changes from round 1's NOTEs:

- `scratch_environment()` builds the child env by **stripping every
  `OPENCLAW_*` key** and then setting exactly three, rather than clobbering the
  ones we thought of. `OPENCLAW_HOME` outranks `HOME` — measured, not read:
  with both set and `OPENCLAW_STATE_DIR` unset, the state DB appears at
  `$OPENCLAW_HOME/.openclaw/state/openclaw.sqlite` and `$HOME` stays empty. The
  installed dist references **485 distinct `OPENCLAW_*` names**
  (`grep -rhoE 'OPENCLAW_[A-Z0-9_]+' dist/ | sort -u | wc -l`), so an allowlist
  of dangerous ones cannot be complete; a prefix denylist can.
- `report()` now opens with `openclaw --version`
  (`OpenClaw 2026.7.1-2 (0790d9f) said: exit 1 …`), closing NOTE-4's
  "which build disagreed?" gap. `--version` costs 0.09 s and is `lru_cache`d.

**An honesty note on where that first one is asserted.** I first wrote the
`OPENCLAW_HOME` test end-to-end — export a decoy `OPENCLAW_HOME`, run
`validate`, assert the decoy stays empty — and the falsification pass caught it
as **vacuous**: reverting `scratch_environment()` to `dict(os.environ)` left it
green. Measured why: with `OPENCLAW_STATE_DIR` *also* set, neither
`OPENCLAW_HOME` nor `OPENCLAW_PROFILE` moves anything `config validate` writes,
so the subprocess genuinely cannot distinguish the two policies. The test now
asserts on the environment `scratch_environment()` hands out — which is a total
claim, is falsifiable in both directions (see the mutation table), and does not
pretend to an end-to-end guarantee that today's CLI cannot demonstrate. The
docstring says exactly that.

**R5 (no skip) is implemented as an assertion, not a marker.** Missing binary →
`AssertionError: no OpenClaw CLI at <path>: run \`pixi run install-openclaw\`
(project-local, gitignored)`.

Why the marker walk and not a parent count — measured, with
`build/robot_brain` on `PYTHONPATH` as under `colcon test`:

```
__file__            : …/build/robot_brain/robot_brain/__init__.py
lexical  parents[2] : …/build          <- wrong
resolved parents[2] : …/src            <- right
```

So a fixed depth gives two different answers depending on how you compute it.
The marker makes both land on the root anyway; `.resolve()` is what keeps the
answer right if the package is ever installed outside the tree. Both are in
the code because the combination is what is robust, and the docstring says so
rather than implying `.resolve()` alone is doing the work.

## Proof that every new test can fail

R7's negative case exists because a shell-out that only asserts `returncode ==
0` is worthless. Every new assertion was broken and watched go red, then
restored (`git status` clean afterwards, verified).

**[R1]** Re-run in full after round 1, and extended. Every row below is a real
run; the harness asserts the *expected* outcome per row, so a mutation that
failed to bite would be reported (`unexpected: 0`), and the config is restored
byte-for-byte afterwards (`restored: True`, plus a clean `git status`).

Config mutations — note the deliberate **PASS** row, which is what stops the
sandbox detector from passing by crying wolf:

| mutation | want | result |
|---|---|---|
| `tools.allow` back to `["mcp__robot__*"]` | FAIL | `test_the_agent_is_scoped_to_the_robot_tools` 1 failed |
| **[R1]** rename the `mcp.servers` key, leave the glob | FAIL | same test, 1 failed — the glob really is derived |
| **[R1]** server key that needs mangling (`"Robot Arm"`) | FAIL | `test_the_server_name_needs_no_provider_safe_mangling` 1 failed |
| **[R1]** `sandbox.mode: "all"`, no gate ← *the revised-R4 conditional* | FAIL | `test_the_shipped_sandbox_settings_do_not_contradict_each_other` 1 failed |
| **[R1]** `mode: "off"` + inert `workspaceAccess` | FAIL | same test, 1 failed |
| **[R1]** `mode: "off"` + inert `tools.sandbox` gate | FAIL | same test, 1 failed |
| **[R1]** `mode: "all"` **with** the gate | **PASS** | 2 passed — no false alarm, and it still validates |
| rename the agent id to `brain` | FAIL | `test_the_agent_the_binding_routes_to_is_the_agent_configured` 1 failed |
| duplicate the entry (same id twice) | FAIL | `agent()` helper, 1 failed |
| revert to `agents.entries` (the #52 bug) | FAIL | `…_is_accepted_by_the_installed_openclaw` 1 failed |
| revert `sandbox.mode: "read-only"` (the #52 bug) | FAIL | `…_is_accepted_by_the_installed_openclaw` 1 failed |

Harness mutations:

| mutation | result |
|---|---|
| delete `node/node_modules/.bin/openclaw` | **6 failed, 0 skipped** — R5 holds; message names `pixi run install-openclaw` |
| replace the binary with a script whose only statement is `exit 0` | **4 failed, 2 passed** — the positive test passes (as predicted), and the three negative controls *plus* the hermeticity test catch it. The specific failure mode R7 was written against, demonstrated. |
| drop `OPENCLAW_STATE_DIR` from the child env | `test_validating_writes_only_where_the_test_told_it_to` **1 failed**: `state did not follow OPENCLAW_STATE_DIR: [PosixPath('…/home/.openclaw')]` |
| change `ROOT_MARKER` to a name that does not exist | **6 failed** — the walk really walks; a lucky cwd is not carrying it |
| **[R1]** revert `scratch_environment()` to `dict(os.environ)` | `test_the_child_inherits_no_openclaw_variable_we_did_not_set` **1 failed** |
| **[R1]** over-strip (`PATH` dropped as well) | **6 failed** — the `#!/usr/bin/env node` shim dies; the same test catches the *opposite* error, so the env policy is pinned from both sides |
| **[R1]** neuter `sandbox_complaints()` to always return `[]` | `test_the_sandbox_consistency_check_detects_what_it_forbids` **1 failed** |

`report()`'s version line was checked on a real failure, not by inspection:

```
E  AssertionError: OpenClaw 2026.7.1-2 (0790d9f) said: exit 1
E    --- stderr ---
E    OpenClaw config is invalid: …/openclaw.robot.json
E      × agents.list.0.sandbox.mode: Invalid input (allowed: "off", "non-main", "all")
```

Hermeticity itself is measured, not assumed. With the redirect:
`$H/state/state/openclaw.sqlite` and nothing else. Without it:
`$H/.openclaw/state/openclaw.sqlite`. With `OPENCLAW_HOME` also set and
`OPENCLAW_STATE_DIR` unset: `$OPENCLAW_HOME/.openclaw/state/openclaw.sqlite`,
`$HOME` empty. All observed by `find` on scratch directories.

**[R1]** R12's bootstrap change was exercised too, not just written: the exact
added line run standalone → `RC=0`, log tail `up to date in 1s` /
`OpenClaw 2026.7.1-2 (0790d9f)`; and the `|| echo …` fallback with a forced
failure → message printed, `RC=0`, execution continues. `bash -n
scripts/start-feature.sh` clean.

## Results

`pixi run test`: **599 tests, 0 errors, 0 failures, 0 skipped**; audit passed.
`robot_brain` 51 collected / 48 non-linter, `+10` over the 38 floor — exactly
the ten tests added (3 in `test_openclaw_config.py`, 7 in
`test_openclaw_validates.py`). Baseline re-cut to 48 and committed (R10).

Cost: the CLI invocations add ~8 s to a package that ran in ~1.4 s (each
`config validate` is ~1.4 s; five of the seven tests make one). Accepted — it
is the only way to hold the config to the real schema, and it is a rounding
error on the full suite.

## Tradeoffs and things the manager should know

1. **`pixi run test` for `robot_brain` hard-depends on a gitignored npm
   artifact.** That is R5, chosen deliberately. Round 1's BLOCK-1 showed the
   cost is not abstract: `scripts/start-feature.sh` bootstrapped worktrees with
   `pixi install` alone, so *every* worktree rebasing onto this would have gone
   red in a package it never touched. **[R1]** Fixed at the bootstrap, per R12,
   not by weakening the guard — `pixi run install-openclaw` now runs beside
   `pixi install`, non-fatal on failure (verified both branches), and
   `DEVELOPMENT.md` states the prerequisite. Still mitigated in the suite by
   `test_the_cli_is_installed_where_the_suite_expects_it` existing separately,
   so the diagnosis reads "run this command" rather than "the config is broken".
   Not mitigated by `depends-on` on the `test` task — R5 forbids it and it would
   put npm in every test run.

   Residual: a worktree created *before* this merges, or a clone bootstrapped by
   hand, still needs the command once. That is what the assertion message is
   for.

2. **NOTE for follow-up — the agent may not be able to reply on Telegram.**
   Found while re-verifying R3; not covered by any ruling and *not* fixed here,
   because it is a policy widening, not a validity bug. `openclaw doctor` on the
   shipped config warns:

   > Agent "robot" is routed from channel "telegram", but the message tool is
   > unavailable for that agent; explicit channel actions such as
   > sendAttachment, upload-file, thread-reply, or reply can fail. Add
   > "message" to the agent tool allowlist, add "group:messaging", or switch
   > the agent to a profile that includes messaging tools.

   This is the concern `README.md` step 6 already raised speculatively ("check
   the agent can still *answer*"); it is now confirmed by the tool rather than
   guessed. Plain text replies probably still work — only the *explicit channel
   actions* are named — but nothing here can prove that without a Telegram
   account and a gateway. I updated step 6 to quote the real warning and to
   recommend *adding* `"message"`/`"group:messaging"` rather than dropping the
   scope, and left the config as R3 specifies. **Manager: this looks like a
   follow-up issue** ("robot agent's `tools.allow` may leave it mute on
   Telegram — decide whether to add `message`/`group:messaging`").

3. **[R1] The Docker prerequisite is gone**, along with the sandbox. Nothing in
   the fragment now requires a container runtime on the Pi. The README keeps the
   reasoning under "Why sandboxing is off" so that turning it back on is a
   decision made with the compaction hazard and the `bundle-mcp` gate in view,
   not rediscovered.

4. **[R1] The compaction/`AGENTS.md` hazard is dist-reading, not an
   observation.** `dist/compact-DLB4d8IL.js:551` is unambiguous about the
   effective-workspace swap, but nobody here has run a Telegram conversation
   long enough to compact. It is recorded in the README and in the test
   docstring as the reason for `mode: "off"`; it is *not* asserted as fact
   anywhere in the code. If a follow-up ever wants sandboxing on, that is the
   thing to observe on the Pi first.

5. **`openclaw doctor` is deliberately not in the suite** (R7). It takes ~30 s,
   wants a gateway, and fails its health check without credentials. Its findings
   are instead frozen into `test_openclaw_config.py`'s assertions and quoted in
   the docstrings, so the reasoning survives even though the command does not
   run in CI.

6. **[R1] Surviving NOTEs, not fixed, per the manager's round-1 instruction.**
   NOTE-6 (the positive test validates the git-tracked file in place rather than
   a `tmp_path` copy) — judgement call, the byte-comparison in
   `test_validating_writes_only_where_the_test_told_it_to` covers the risk and
   the boundary is commented. NOTE-4's *version floor* half — the report now
   names the build, but nothing pins or refreshes `node/`; that is #51-scope.
   Plus the Telegram `message`-tool gap in item 2 above.

7. **[R1] Scope widened once, on the record.** Everything is inside
   `src/robot_brain/**`, `scripts/test_baseline.json` and these feature docs —
   **except** `scripts/start-feature.sh` and `DEVELOPMENT.md`, which R12
   explicitly authorised for BLOCK-1 and only for that. `pixi.toml` untouched
   (R5 forbids the `depends-on`). Both files would normally be operational
   scope; the manager ruled in-loop because shipping the guard without the
   bootstrap fix would red out every sibling worktree while the remedy waited on
   a second PR.
