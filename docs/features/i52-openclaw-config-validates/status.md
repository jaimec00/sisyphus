# status — i52 openclaw.robot.json validates against the real schema

Issue: #52 — "Fix openclaw.robot.json: agents.list + sandbox enum (fails
'openclaw config validate')"
Branch: `feat/i52-fix-openclaw-robot-json-agents-list-sand`

## Phase
- [x] 0. Sync (`git fetch origin`; branch is level with `origin/main` @ 7d0c5a1)
- [x] 1. Brief read from the issue (`gh issue view 52 --json …`)
- [x] 2. Provision + probe the real dependency (`pixi run install-openclaw`,
      then execute-probe `openclaw config schema` / `config validate`)
- [x] 3. context-explorer → `context.md`
- [x] 4. Manager rulings on open questions (R1–R10 below)
- [ ] 5. implementer → code + tests + `implementation.md`
- [ ] 6. red-team → `red_team.md`
- [ ] 7. fix rounds (max 2)
- [ ] 8. test-runner
- [ ] 9. rebase + PR + "ready"

## Round
red-team↔fix rounds used: 0 / 2

## Blockers
None.

## Step-2 probe log (execute-verified, OpenClaw 2026.7.1-2, commit 0790d9f)

Installed into this worktree with `pixi run install-openclaw` →
`node/node_modules/.bin/openclaw` (gitignored). `node` is **only** on `PATH`
inside the pixi env; invoking the shim directly from a bare shell fails with
`/usr/bin/env: 'node': No such file or directory`.

Everything below was run, not recalled:

1. **The bug reproduces.** `OPENCLAW_CONFIG_PATH=…/openclaw.robot.json pixi run
   openclaw config validate` → `× agents: Invalid input`, **exit 1**.
2. **`agents` schema** (`openclaw config schema`, 2.4 MB of JSON):
   `properties.agents.properties` == `['defaults', 'list']`,
   `agents.additionalProperties == false` → **`entries` is not a key**.
   `agents.list.type == 'array'`; each item has `required == ['id']`,
   `additionalProperties == false`, and its property set includes
   `id, default, name, workspace, skills, sandbox, tools`.
3. **`sandbox.mode` enum** is exactly `off | non-main | all`;
   `sandbox.workspaceAccess` is exactly `none | ro | rw`.
   Confirmed by the validator's own message on a bad value:
   `× agents.list.0.sandbox.mode: Invalid input (allowed: "off", "non-main", "all")`.
4. **The corrected shape validates.** The issue's proposed `agents.list` form,
   spliced into the real fragment → `Config valid: …`, **exit 0**.
5. **The validator is strict and cross-referential** — it is a real gate, not a
   smoke test:
   - unknown top-level key → `× <root>: Invalid input`
   - unknown key inside an agent → `× agents.list.0: Invalid input`
   - `bindings[0].agentId` naming an absent agent →
     `× bindings.0.agentId: Unknown agent id "nosuchagent" (not in agents.list).`
6. **A bare fragment validates fine.** Our file is a 3-key fragment
   (`mcp`/`agents`/`bindings`), not a whole config; every other top-level key is
   optional, so pointing `OPENCLAW_CONFIG_PATH` at the fragment is legitimate.
7. **Side effects.** `config validate` does **not** write the config file
   (md5 unchanged) but it *does* create a state DB. With a scratch `HOME` it
   writes `$HOME/.openclaw/state/openclaw.sqlite` and `$HOME/.npm/…`.
   `OPENCLAW_STATE_DIR=<dir>` relocates the state DB to `<dir>/state/…`
   (verified: nothing under `$HOME/.openclaw` afterwards).
   → a hermetic test sets both `OPENCLAW_STATE_DIR` and `HOME` to tmp dirs.
8. **`pixi run openclaw` has `depends-on = ["install-openclaw"]`** — i.e. the
   pixi task shells out to npm. A test must **not** go through the pixi task
   (recursive pixi + network); it invokes `<repo>/node/node_modules/.bin/openclaw`
   directly, which works because the test process already runs inside the env.

---

# Manager rulings (binding, but not assumed correct)

A downstream agent that believes a ruling is wrong **escalates to the manager
in-process** — it must neither silently deviate nor comply into a bug.

## Two bugs the issue does not describe (found in step-2 probing)

The issue asserts "`mcp.servers`, `tools.allow`, `bindings` all check out."
That is true **schema-wise** and false **semantically** — `tools.allow` is just
`array<string>` in the schema, so any spelling validates. Probing the installed
CLI turned up two further defects, both execute-verified:

### Bug A — `tools.allow: ["mcp__robot__*"]` matches no tool at all

`mcp__<server>__<tool>` is the **Claude Code** convention, not OpenClaw's.
OpenClaw exposes MCP tools under the *provider-safe server prefix*:
`<server>__<tool>`, glob `<server>__*`
(`node_modules/openclaw/docs/gateway/config-tools.md:57-59`). The `mcp__…`
spellings in OpenClaw's own tree are all for the Claude *CLI backend*'s
`--allowedTools`, a different surface (`docs/gateway/cli-backends.md:208`).

Verified by differential `openclaw doctor` runs against a config whose `robot`
MCP server was rewired to a locally-runnable command (so the server genuinely
starts, removing "server failed to load" as a confound):

| `tools.allow` | `openclaw doctor` says |
| --- | --- |
| `["mcp__robot__*"]` | `[tools] agents.robot.tools.allow allowlist contains unknown entries (mcp__robot__*). These entries won't match any tool unless the plugin is enabled.` |
| `["robot__*"]` | no such warning |

So today's config would hand the brain **zero tools**. It validates. It is
still wrong. This is exactly the failure mode the issue exists to end.

### Bug B — the issue's own suggested `sandbox.mode: "all"` filters the MCP tools out

With sandboxing on, the sandbox tool policy is a **second allow gate** in front
of MCP tools, and it is *unset* in the issue's proposal. `openclaw doctor` on
the `["robot__*"]` config emits:

> `tools.sandbox.tools.alsoAllow (unset) does not include "bundle-mcp", …
> such as "<server>__*". Sandboxed agents will filter bundled MCP tools`

Docs: `config-tools.md:50-59`, `sandbox-vs-tool-policy-vs-elevated.md:110-112`
— "the MCP server can still load successfully while its tools are filtered
before the provider request." Adopting the issue's snippet verbatim therefore
trades a config that *fails loudly* for one that *validates and silently
disarms the robot*.

Both candidate remediations validate **and** silence the doctor warning
(verified):
- **C1** `mode:"all"` + `workspaceAccess:"ro"` + `tools.sandbox.tools.alsoAllow:["bundle-mcp"]`
- **C2** `mode:"off"`

## Rulings

**R1 — `agents.entries{}` → `agents.list[]`.** Exactly as the issue says. One
array element, `"id": "robot"` as its first key; every other field carried over
unchanged. `agents.additionalProperties` is `false`, so `entries` cannot stay.

**R2 — `sandbox: {"mode": "all", "workspaceAccess": "ro"}`.** Adopt the issue's
values (answers context.md Q3). Rationale: `workspaceAccess:"ro"` is the honest
expression of what the broken `mode:"read-only"` was reaching for, and `mode`
must be on for `workspaceAccess` to mean anything. `mode:"all"` costs nothing
at runtime here — OpenClaw sandboxes only `exec`/`read`/`write`/`edit`/
`apply_patch`/`process`-class tools (`sandboxing.md:17-19`), and this agent is
allowed *none* of them, so no container is ever built — while remaining correct
if `tools.allow` is ever widened. **Not** C2 (`mode:"off"`), which would silently
drop that guarantee. The Docker-backend prerequisite is a documentation matter,
see R8, and a follow-up for Sisyphus.

**R3 — fix Bug A: `tools.allow` becomes `["robot__*"]`.** In scope: the issue's
subject is "this config does not actually work against the real schema/CLI",
and shipping a validated config that grants no tools would satisfy the letter
of #52 while defeating its purpose. Cite the doctor evidence in a comment.

**R4 — fix Bug B: add agent-level `tools.sandbox.tools.alsoAllow: ["bundle-mcp"]`.**
Agent-level (`agents.list[0].tools.sandbox`), not top-level `tools.sandbox` —
the fragment must not impose policy on other agents in the operator's config.
R2 and R4 are a **pair**: a test must pin the coupling so a later edit cannot
keep `mode:"all"` while dropping the gate.

**R5 — the new validate test HARD-FAILS when `openclaw` is absent; no skip.**
(Answers Q1.) `pytest.mark.skipif` would make the drift-guard evaporate on
exactly the machine that never ran `install-openclaw` — and per context.md the
integrity checker's ratchet would not notice, so the skip is *silent*. This
repo's own test docstring calls a test that implies an uncheckable guarantee a
lie; a guard that quietly no-ops is the same lie. `pixi run install-openclaw`
is documented setup as of #51, and CLAUDE.md makes the laptop the authoritative
gate. The failure message must name the exact remedy: `pixi run install-openclaw`.
**Do not** add `depends-on = ["install-openclaw"]` to the `test` task — that
puts npm (and a network round-trip) in the critical path of every test run.

**R6 — repo-root discovery: walk up from `Path(robot_brain.__file__).resolve()`
for a `pixi.toml` marker.** (Answers Q2.) `.resolve()` — not lexical
`normpath` — because `--symlink-install` makes `..`-walking wrong lexically.
Marker-walk, not a fixed parent count, so relocating the package cannot break
it. No `OPENCLAW_BIN` env override (YAGNI). Binary at
`<root>/node/node_modules/.bin/openclaw`. The shim is `#!/usr/bin/env node` and
`node` is on `PATH` only inside the pixi env; verify by execution that it runs
under `colcon test`, and if it does not, invoke it via the pixi-provided
`node` explicitly rather than reaching for a skip.

**R7 — the shell-out test goes in its own file,**
`src/robot_brain/test/test_openclaw_validates.py`. (Answers Q4.) It is the only
test in the package that depends on a gitignored, non-Python artifact;
`test_no_ros_runtime.py` sets the package precedent of isolating the test that
shells out. Keep the subprocess hermetic: pass `OPENCLAW_CONFIG_PATH`, and point
`OPENCLAW_STATE_DIR` **and** `HOME` at `tmp_path` — verified that `config
validate` otherwise writes `~/.openclaw/state/openclaw.sqlite` and `~/.npm/…`.
Assert on **exit status 0**, and make the failure message include the CLI's own
stderr (that is what makes the next drift diagnosable in one read). Also assert
the *negative*: a deliberately corrupted copy of the fragment must exit
non-zero, so a test that "passes" because the binary silently no-ops cannot
survive. Do not shell out to `openclaw doctor` in the suite — it is slow and
wants a gateway.

**R8 — `src/robot_brain/README.md` is in scope; update it.** (Answers Q5.) It
documents `agents.entries.robot` (:94) and `agents.entries.<id>.sandbox.mode`
(:135) — both now false — and step 6 (:129-138) tells the operator this can only
be verified on the Pi, which #51 ended. Minimal truthful edits: the new key
path, the fact that shape is now checked automatically by the suite, and what
is *still* Pi-only (bindings, SSH reachability, real agent behavior). Add the
Docker-backend prerequisite implied by R2 as an operator note.

**R9 — rewrite the `test_openclaw_config.py` module docstring.** Its opening
claim — "**We cannot check that OpenClaw accepts it.** OpenClaw runs on the Pi,
is not installed on this laptop" — is now false and must not survive the PR.
Point it at the new file. Also fix the stale `agents.entries` comment at
`agent.py:41`.

**R10 — bump the ratchet.** `scripts/test_baseline.json` `robot_brain: 38` → the
new count (regenerate with `python scripts/check_test_integrity.py
--update-baseline`, then verify the number moved by exactly the number of tests
added).

**R11 — entry lookup helper is test-local.** Mirror the existing `server()`
helper in `test_openclaw_config.py`; do not add a public accessor to
`robot_brain/__init__.py`. Nothing in `src/` consumes the agent entry — only
tests do — and speculative API is a NOTE-grade smell in this repo.
