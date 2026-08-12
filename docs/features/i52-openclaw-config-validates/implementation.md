# implementation — i52: `openclaw.robot.json` validates against the real schema

Issue #52. Rulings R1–R11 in `status.md` are the spec; every one is
implemented, none deviated from. Everything below was **run**, not recalled —
OpenClaw 2026.7.1-2 from `node/node_modules/.bin/openclaw` in this worktree.

## What changed

| Path | Change |
|---|---|
| `src/robot_brain/robot_brain/openclaw/openclaw.robot.json` | `agents.entries{}` → `agents.list[]` with `"id"`; `sandbox` fixed; `tools.allow` respelled; sandbox tool gate added |
| `src/robot_brain/robot_brain/agent.py` | `AGENT_ID` comment: no longer "the key under `agents.entries`" (R9) |
| `src/robot_brain/test/test_openclaw_config.py` | docstring rewritten (R9); `agent()` helper (R11); three `entries` assertions reshaped; two new tests |
| `src/robot_brain/test/test_openclaw_validates.py` | **new** — shells out to the real CLI (R5/R6/R7) |
| `src/robot_brain/README.md` | R8 |
| `scripts/test_baseline.json` | `robot_brain: 38 → 46` (R10) |

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
      "sandbox": { "mode": "all", "workspaceAccess": "ro" },
      "tools": {
        "allow": ["robot__*"],
        "sandbox": { "tools": { "alsoAllow": ["bundle-mcp"] } }
      }
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
  `anyOf[const none|ro|rw]`. **R2's values are in the enums.**
- `…items.properties.tools.properties.sandbox` → an object with one property
  `tools: {allow, alsoAllow, deny}`, `additionalProperties: false` at both
  levels. **R4's shape is schema-legal at the agent level.**

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
| as shipped (`robot__*`, gate present) | neither warning |
| `tools.allow: ["mcp__robot__*"]` | `[tools] agents.robot.tools.allow allowlist contains unknown entries (mcp__robot__*). These entries won't match any tool unless the plugin is enabled.` |
| `tools.sandbox` removed, `mode: "all"` kept | `tools.sandbox.tools.alsoAllow (unset) does not include "bundle-mcp", … Sandboxed agents will filter bundled MCP tools before provider requests. Add "bundle-mcp" to tools.sandbox.tools.alsoAllow …` |

**R3 and R4 both hold.** Also confirmed R2's premise from OpenClaw's own docs
(`node_modules/openclaw/docs/gateway/sandboxing.md:17`): sandboxing covers
`exec`, `read`, `write`, `edit`, `apply_patch`, `process` — none of which this
agent is allowed — and `:33` gives `backend` default `docker`, which is where
R8's operator note comes from.

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
  passes. Now `allowed == ['robot__*']` exactly, with the doctor quote as the
  reason.
- Everything else (secrets scan, launch command, tool filter, timeout, asset
  names) untouched.

New, and the R2+R4 pin:

- `test_the_sandbox_does_not_filter_away_the_robot_tools` — states the
  implication: `mode != 'off'` ⟹ `bundle-mcp` in
  `tools.sandbox.tools.alsoAllow`. A later edit cannot keep the sandbox and
  drop the gate.
- `test_the_sandbox_grants_no_more_than_read_access_to_the_workspace` —
  asserts `mode != 'off'` *and* `workspaceAccess in ('none','ro')`. Both
  halves, because `workspaceAccess: "ro"` under `mode: "off"` is a comment,
  not a restriction. Together the two tests are a total spec of R2+R4: the
  first forbids "sandbox without gate", the second forbids "read-only claim
  without sandbox".

`agent()` is test-local (R11), mirroring `server()`, and asserts the id is
unique — `agents.list` is an array, so duplicate ids are now expressible where
they were not under a map.

### `test_openclaw_validates.py` — the shell-out

Six tests. Repo root by marker walk from `Path(robot_brain.__file__).resolve()`
for `pixi.toml` (R6); binary at `<root>/node/node_modules/.bin/openclaw`,
invoked directly, never through `pixi run openclaw` (which `depends-on`
`install-openclaw`). Hermetic: `HOME`, `OPENCLAW_STATE_DIR`,
`OPENCLAW_CONFIG_PATH` all under `tmp_path`; `PATH` inherited deliberately,
since the shim is `#!/usr/bin/env node`.

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

Config mutations (each run against the single test that should catch it):

| mutation | result |
|---|---|
| `tools.allow` back to `["mcp__robot__*"]` | `test_the_agent_is_scoped_to_the_robot_tools` **1 failed** |
| drop `tools.sandbox`, keep `mode: "all"` | `test_the_sandbox_does_not_filter_away_the_robot_tools` **1 failed** |
| `sandbox.mode: "off"` | `test_the_sandbox_grants_no_more_than_read_access_to_the_workspace` **1 failed** |
| rename the agent id to `brain` | `test_the_agent_the_binding_routes_to_is_the_agent_configured` **1 failed** |
| duplicate the entry (same id twice) | `agent()` helper **1 failed** |
| revert to `agents.entries` (the #52 bug) | `…_is_accepted_by_the_installed_openclaw` **1 failed** |
| revert `sandbox.mode: "read-only"` (the #52 bug) | `…_is_accepted_by_the_installed_openclaw` **1 failed** |

Harness mutations:

| mutation | result |
|---|---|
| delete `node/node_modules/.bin/openclaw` | **6 failed, 0 skipped** — R5 holds; message names `pixi run install-openclaw` |
| replace the binary with `#!/bin/sh` / `exit 0` | **4 failed, 2 passed** — the positive test passes (as predicted), and the three negative-control cases *plus* the hermeticity test catch it. This is the specific failure mode R7 was written against, demonstrated. |
| drop `OPENCLAW_STATE_DIR` from `validate()` | `test_validating_writes_only_where_the_test_told_it_to` **1 failed**: `state did not follow OPENCLAW_STATE_DIR: [PosixPath('…/home/.openclaw')]` |
| change `ROOT_MARKER` to a name that does not exist | **6 failed** — the walk really walks; a lucky cwd is not carrying it |

Hermeticity itself is measured, not assumed. With the redirect:
`$H/state/state/openclaw.sqlite` and nothing else. Without it:
`$H/.openclaw/state/openclaw.sqlite`. Both observed by `find` on a scratch
`HOME`.

## Results

`pixi run test`: **597 tests, 0 errors, 0 failures, 0 skipped**; audit passed.
`robot_brain` 49 collected / 46 non-linter, `+8` over the 38 floor — exactly
the eight tests added (2 in `test_openclaw_config.py`, 6 in
`test_openclaw_validates.py`). Baseline re-cut to 46 and committed (R10).

Cost: the three CLI invocations add ~4.3 s to a package that ran in ~1.4 s
(each `config validate` is ~1.4 s). Accepted — it is the only way to hold the
config to the real schema, and it is a rounding error on the full suite.

## Tradeoffs and things the manager should know

1. **`pixi run test` for `robot_brain` now hard-depends on a gitignored npm
   artifact.** That is R5, chosen deliberately, and it is a real cost: a fresh
   clone that runs `pixi install` but not `pixi run install-openclaw` gets six
   red tests. Mitigated by `test_the_cli_is_installed_where_the_suite_expects_it`
   existing separately, so the diagnosis is "run this command", not "the config
   is broken", and by the assertion message naming the remedy. Not mitigated by
   `depends-on` on the `test` task — R5 forbids it and it would put npm in
   every test run.

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

3. **`sandbox.mode: "all"` implies a Docker backend** (`backend` defaults to
   `docker`). The agent is allowed no sandboxed tool class, so no container
   should ever be built — but "should" is inference from the docs, not
   something this repo can observe. Documented as an operator note in step 6
   (R8), with the honest escape hatch: if a Pi without Docker complains, set
   `mode: "off"` *and* drop `workspaceAccess`, rather than leaving an inert
   restriction. Per R2 this is also a follow-up for Sisyphus.

4. **`openclaw doctor` is deliberately not in the suite** (R7). It takes ~30 s,
   wants a gateway, and fails its health check without credentials. Its findings
   are instead frozen into `test_openclaw_config.py`'s assertions and quoted in
   the docstrings, so the reasoning survives even though the command does not
   run in CI.

5. **Scope kept.** No changes outside `src/robot_brain/**`,
   `scripts/test_baseline.json` and these feature docs. `pixi.toml` untouched.
