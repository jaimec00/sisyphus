# red_team — i52: `openclaw.robot.json` validates against the real schema

Reviewed: branch diff vs `origin/main` (three commits), issue #52, `context.md`,
`status.md` (R1–R11), `implementation.md`. Read-only pass; no source or test was
edited. Evidence below is either read out of the worktree, read out of the
installed OpenClaw tree (`node/node_modules/openclaw/{docs,dist}` — gitignored,
OpenClaw 2026.7.1-2), or derived by reasoning about executions recorded in
`status.md` / `implementation.md`. Nothing here was executed by me.

**Verdict: 1 BLOCK, 9 NOTEs.** The config fix itself is right, the reshape lost
no guarantee, and the new shell-out guard is *structurally* load-bearing (it
cannot pass against a no-op binary). The one blocker is not in the code — it is
that this PR makes the project's only real gate (`pixi run test`) depend on an
artifact that the worktree bootstrap does not install.

---

## Summary of the attacks the manager asked for

| Attack | Verdict |
|---|---|
| 1. R5/R7 — can the guard silently evaporate? | **No, structurally sound** (details below), but see BLOCK-1 for the operational consequence and NOTE-4 for the stale-CLI blind spot. |
| 2. R7 hermeticity | Holds for the observed side effects; two unneutralised env vars (`OPENCLAW_HOME`, `OPENCLAW_PROFILE`) — NOTE-5. Passing a complete env is correct; clobbering only `HOME` does **not** break the `#!/usr/bin/env node` shebang because `PATH` is inherited. |
| 3. R6 repo-root discovery | **Correct and robust.** No wrong-`pixi.toml` and no sibling-worktree hazard — see analysis below. |
| 4. R2+R4 | **Both hold, and R4 is *not* a no-op** — agent-level `tools.sandbox.tools` is consulted at runtime; I found the code. R2's "costs nothing at runtime" is *nearly* true but overstated — NOTE-1. |
| 5. R3 `robot__*` | **Correct for this server key.** Unguarded assumption if the key is ever renamed — NOTE-2. |
| 6. Reshaped existing tests | **No guarantee lost; two strengthened.** `walk()` already recursed lists, so the secret scanner still reaches the agent entry. |
| 7. Test quality / ratchet honesty | Real tests, not tautologies; the R2+R4 coupling test genuinely fails on the described mutation; 38→46 is exactly the 8 tests added. |
| 8. Prose honesty | No overclaim found in the new docstrings or README step 6. Two small prose defects — NOTE-8, NOTE-9. |

---

## BLOCK

### BLOCK-1 — the authoritative test gate now depends on an artifact the worktree bootstrap never installs

`src/robot_brain/test/test_openclaw_validates.py:83` (`assert binary.is_file()`)
+ `scripts/start-feature.sh:74-75` + `DEVELOPMENT.md:38-41`.

R5 was ruled deliberately and `implementation.md` §"Tradeoffs" 1 acknowledges the
cost in the abstract ("a fresh clone that runs `pixi install` but not `pixi run
install-openclaw` gets six red tests"). What it did not check is that **this is
the default state of every worktree this project creates.**

`scripts/start-feature.sh:74-75` is the only bootstrap any feature worktree gets:

```
echo '[bootstrap] pixi install (provision env before first agent action)...' | tee -a '$log'; \
pixi install >>'$log' 2>&1 || echo '[bootstrap] pixi install returned nonzero; ...'
```

`node/` is per-worktree (`scripts/install_openclaw.sh:10-11` derives `prefix`
from the script's own repo root, and `node/` is gitignored), so nothing carries
over from a sibling worktree.

Concrete failure scenario, and it is not hypothetical:

1. Sisyphus merges this PR.
2. Every other open worktree does the mandatory `git fetch && git rebase
   origin/main` (CLAUDE.md, "Staying current with `main`") and re-runs
   `pixi run test`. Six `robot_brain` tests go red, in a package that feature
   never touched. That worktree's `test-runner` — a sonnet agent whose contract
   is "reports pass/fail + log path only" — reports RED and the loop stalls on a
   failure unrelated to the feature.
3. `scripts/start-feature.sh <n>` creates the next worktree. Its first
   `pixi run test` is red before a line of feature code exists.

CLAUDE.md makes this the whole ballgame: "the authoritative test gate is the
laptop `test-runner` running the full `pixi run test` suite … before the manager
signals 'ready'". A change that makes that gate fail spuriously in every fresh
worktree degrades the one check the merge model actually trusts.

**Fix direction** (R5 itself does not need reversing, and neither does R7's
no-skip stance):

- Add `pixi run install-openclaw` to the bootstrap line in
  `scripts/start-feature.sh` (next to `pixi install`, same tolerant
  `|| echo …` treatment), and state in `DEVELOPMENT.md:38-41` that
  `install-openclaw` is a **prerequisite for `pixi run test`**, not just for
  running the CLI by hand.
- `scripts/` and root `*.md` are **operational scope** ("never `src/`" is the
  feature loop's boundary, CLAUDE.md "Change management"), so the worktree
  manager should **escalate this outward** rather than edit those files here:
  either land the ops PR first, or record it as an explicit merge precondition
  in the PR description so Sisyphus sequences the two merges correctly.
- Cheap in-scope mitigation regardless: nothing today tells a human reading a
  red `robot_brain` suite that the remedy is one command *unless* they read the
  assertion text. That is adequate, so this is a sequencing fix, not a code fix.

---

## Analysis behind the "no finding" verdicts

Recording these because the manager asked for them specifically and a
"we looked and it holds" is worth as much as a finding.

### R5/R7 — the guard cannot go green while checking nothing

Walked each of the four hypotheses:

- **(a) `node/` never installed.** `openclaw_binary()`
  (`test_openclaw_validates.py:80-84`) is called from inside test bodies via
  `validate()`, never at import/collection time, so this is six *failures*, not
  a collection error and not a skip. `scripts/check_test_integrity.py` counts
  tests *collected*, so even a skip would have been invisible — the implementer
  read that correctly. Structural, matches the recorded "6 failed, 0 skipped".
- **(b) stale/older `node/`.** The positive test would pass against an older
  schema, but `test_the_validator_rejects_a_broken_fragment[agents.entries
  instead of agents.list]` (`:151-158`) would go **red** the moment the local
  CLI is old enough to still accept `agents.entries`. So an old CLI is loud, not
  silent. What is *not* caught is a CLI that is merely months behind current
  upstream — NOTE-4.
- **(c) `node` not on `PATH` (suite run outside the pixi env).** The shim is
  `#!/usr/bin/env node`, so the exec succeeds and the process exits 127 with
  `env: 'node': No such file or directory` on stderr. The positive test goes red
  on `returncode == 0`; the three negative controls survive the `returncode !=
  0` assertion but die on `assert 'agents' in stdout + stderr` (`:181`) — that
  second assertion is what makes the negative control robust rather than
  decorative, and it is doing real work here. Plus
  `test_the_cli_is_installed_where_the_suite_expects_it:137` names the cause
  directly. Loud.
- **(d) CLI warns-but-exits-0 on a future violation.** This is precisely the
  `exit 0` substitution case. Structurally: 3 negative controls fail on
  `returncode != 0`, and `test_validating_writes_only_where_the_test_told_it_to`
  fails on `(home / 'state').is_dir()` (`:200`) because a no-op binary opens no
  state DB. That is 4 red, 2 green — the implementer's claim is **structural**,
  not an artifact of how the fake was installed. Verified by reading, not by
  trusting the transcript.

### R6 — repo-root discovery

`repository_root()` (`:60-77`) walks `Path(robot_brain.__file__).resolve()`
upward for `pixi.toml`. Attacked all four hypotheses; all fail to bite:

- **Nested `pixi.toml`.** There is exactly one `pixi.toml` in the tree, at the
  worktree root; no package directory carries one, so "nearest match wins"
  cannot land on a wrong one.
- **Walks to `/`.** Raises `AssertionError` with a message that says what it was
  looking for and why it matters (`:75-77`) — not an unhelpful `StopIteration`
  or `IndexError`.
- **`--symlink-install`.** `.resolve()` is the correct choice and the docstring
  explains it accurately. Under a *non*-symlink `colcon build` the copy lives at
  `build/robot_brain/robot_brain/`, and the walk still terminates at the
  worktree root — so the marker is doing the robustness work, exactly as
  claimed.
- **Sibling worktrees.** This was the sharpest hypothesis and it does not land:
  `.resolve()` yields a real path *inside this worktree*, and the walk only ever
  goes up from there, so it stops at this worktree's `pixi.toml` before it could
  reach `/home/sisyphus/worktrees`. There is no mechanism by which it selects
  another worktree's `node/`.

### R2+R4 — is agent-level `tools.sandbox` consulted, or is R4 a no-op?

**It is consulted. R4 is not a no-op.** Two independent confirmations:

- Docs: `docs/gateway/sandbox-vs-tool-policy-vs-elevated.md:62` lists
  "`tools.sandbox.tools.allow`/`deny` **and `agents.list[].tools.sandbox.tools.*`**";
  `docs/tools/multi-agent-sandbox-tools.md:221,231` puts
  `agents.list[].tools.sandbox.tools` in the filtering order and says it
  *replaces* the global for that agent.
- Code: `dist/tool-policy-Bx6D7Inl.js:148-158` —
  `resolveSandboxToolPolicyForAgent()` reads
  `resolveAgentConfig(cfg, agentId)?.tools?.sandbox?.tools`, picks
  agent-over-global for `allow`/`alsoAllow`/`deny`, and
  `dist/agent-tools.policy-YD9HuYgO.js:101` pushes that policy into the chain
  when `sandboxMode === "all"`.

So the trap R4 was written to avoid is genuinely avoided, and choosing the
agent-level key (rather than imposing top-level policy on the operator's other
agents) is the right call.

### R3 — is `robot__*` right for *this* key?

Yes. `docs/gateway/config-tools.md:59` states the mangling rules exactly:
non-`[A-Za-z0-9_-]` → `-`, non-letter-initial gets an `mcp-` prefix, "long or
duplicate prefixes may be truncated or suffixed". `robot` is short, unique,
letter-initial and already in the safe alphabet, so the provider-safe prefix is
the identity. No interaction with `toolFilter.include`: that filters which tools
the *server* registers (raw names), while `tools.allow` filters by *prefixed*
name at the agent layer — different namespaces, both needed, neither redundant.
The rename-safety question is covered (`server()` KeyErrors if `mcp.servers`'s
key stops matching `MCP_SERVER_NAME`, and `:127` derives the glob from
`MCP_SERVER_NAME`), with one gap — NOTE-2.

### The reshaped existing tests — nothing lost

Diffed `src/robot_brain/test/test_openclaw_config.py` against `origin/main`'s
copy assertion by assertion:

- `walk()` (`:78-86`) is **unchanged** and already recursed lists
  (`elif isinstance(value, list)`), so
  `test_the_fragment_contains_no_secret` still reaches every key of the agent
  entry now that it lives inside an array. Specifically checked, since a
  dict-only `walk` would have silently stopped scanning.
- `set(entries) == {AGENT_ID}` → `[e['id'] for e in …list] == [AGENT_ID]`:
  keeps *exactly one* and *it is AGENT_ID*, and adds ordering-sensitive
  agreement with `bindings[].agentId`. Strictly stronger.
- `all(MCP_SERVER_NAME in entry …)` → `allowed == ['robot__*']`: strictly
  stronger (the old substring form is exactly what let `mcp__robot__*` through).
- `agent()` (`:59-70`) additionally asserts id uniqueness — a failure mode that
  did not exist under a map and now does.
- Every other test (`name`, `workspace`, launch command, tool filter, timeout,
  secrets, asset names) is byte-identical.

Ratchet: 12 → 14 in `test_openclaw_config.py`, 6 new in
`test_openclaw_validates.py` = 8; `scripts/test_baseline.json:6` moved 38 → 46.
Honest, and all eight are substantive (the closest thing to filler,
`test_the_cli_is_installed_where_the_suite_expects_it`, earns its place by
turning "nobody ran install-openclaw" into its own diagnosis and by checking
`node` on `PATH`, which no other test does).

---

## NOTE

### NOTE-1 — R2's "`mode:"all"` costs nothing at runtime" is overstated; `workspaceAccess:"ro"` moves the *effective workspace* on some code paths

`src/robot_brain/robot_brain/openclaw/openclaw.robot.json:40-43`,
`src/robot_brain/README.md:165-172`.

The manager explicitly asked whether anything says a sandbox is established
eagerly or has a startup cost without Docker. What I found in the installed
tree, reading `dist/`:

- **No eager container.** `ensureSandboxWorkspaceForSession()`
  (`dist/sandbox-DtTssSMH.js:1345-1372`) only lays out *host* directories; the
  Docker backend is reached from tool execution. `docs/gateway/sandboxing.md:9`
  agrees ("only tool execution moves into the sandbox when enabled").
  So the "no container is ever built on a Pi without Docker" half of R2 is
  sound, and the README's hedged "should" is honest.
- **But sandboxing is not inert.** `sandbox.enabled` is a function of `mode`,
  not of whether a sandboxed tool is ever called. Every sandboxed run computes
  `effectiveWorkspace = sandbox.workspaceAccess === "rw" ? resolvedWorkspace :
  sandbox.workspaceDir` (`dist/selection-JInn13lc.js:11378`,
  `dist/run-attempt-CXZNKJ6y.js:5770`, `dist/compact-DLB4d8IL.js:551`) and
  `fs.mkdir`s it. With our `"ro"`, that is the **sandbox** workspace under
  `~/.openclaw/sandboxes/…`, not `~/.openclaw/agents/robot`.

The main run path is safe — `dist/selection-JInn13lc.js:11816-11847` loads
bootstrap files (i.e. `AGENTS.md`, the operating prompt this whole package
exists to ship) from `resolvedWorkspace`, the agent workspace. **But the
embedded *compaction* run does not**: `dist/compact-DLB4d8IL.js:607-617` calls
`resolveBootstrapContextForRun({ workspaceDir: effectiveWorkspace, … })`, which
under `mode:"all"` + `"ro"` points at an empty sandbox workspace. Failure
scenario: a long Telegram conversation triggers compaction, and the compacted
continuation is built without `AGENTS.md` — the agent silently loses its
operating prompt mid-chore.

This is docs+dist reading, not an executed observation, and it is upstream
behaviour rather than a defect in this diff — hence NOTE. Fix direction: keep
R2, but (a) soften the README's framing from "nothing should ever be sandboxed
in practice" to name what *does* change (effective workspace, per-run sandbox
workspace dirs), and (b) raise a follow-up to observe on the Pi whether a
compaction turn keeps the prompt, since that is the one thing here that could
quietly disarm D22.

### NOTE-2 — the `robot__*` glob silently assumes the server key needs no provider-safe mangling

`src/robot_brain/test/test_openclaw_config.py:127`.

`assert allowed == [f'{MCP_SERVER_NAME}__*']` derives the glob from the server
name, which is right — but if `MCP_SERVER_NAME` ever becomes something like
`robot mcp` or `2robot`, OpenClaw's prefix becomes `robot-mcp` / `mcp-2robot`
(`docs/gateway/config-tools.md:59`) while this test happily asserts
`'robot mcp__*'`. Config still validates (`tools.allow` is `array<string>`),
`doctor` is not in the suite, and the agent gets zero tools — the exact Bug-A
shape #52 exists to kill, re-entering by a different door.

Fix direction: one line next to the existing assertion pinning the assumption
that makes the identity mangling valid, e.g.
`assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,20}', MCP_SERVER_NAME)`, with the
`config-tools.md:59` rule quoted as the reason.

### NOTE-3 — `test_the_sandbox_does_not_filter_away_the_robot_tools` hand-copies the `mode` enum, which is what #52 exists to stop

`src/robot_brain/test/test_openclaw_config.py:143`:
`assert sandbox['mode'] in ('off', 'non-main', 'all')`.

The validator next door now owns this question authoritatively. The hand-copied
tuple duplicates it and can only go stale in the *permissive* direction: if
OpenClaw adds a fourth mode and someone adopts it, `config validate` passes and
this assertion fails on a list copied out of a 2026 doc. Fix direction: drop the
enum assertion (the guard is the implication on the next two lines), and let
`test_openclaw_validates.py` own "is this value legal".

### NOTE-4 — the shell-out never records or floors the CLI version, so a months-old `node/` validates silently

`src/robot_brain/test/test_openclaw_validates.py:122-126` (`report()`).

`scripts/install_openclaw.sh:36` installs unpinned `openclaw` and the test never
re-runs it, so a `node/` populated once and never refreshed keeps the suite
green against a schema that may no longer be the one the Pi runs. The guard is
still checking *something* real (hence NOTE, not BLOCK), and no hermetic test
can consult upstream — but the failure report should at least say **which build
said yes**. Fix direction: have `report()` (or a module-level constant) capture
`openclaw --version` and include it, so a future "but it passed on my machine"
is resolvable in one read. A version floor is a separate follow-up (`node/`
pinning is a #51-scope gap, not this PR's).

### NOTE-5 — hermeticity neutralises three env vars but not `OPENCLAW_HOME` / `OPENCLAW_PROFILE`

`src/robot_brain/test/test_openclaw_validates.py:106-111`.

`environment = dict(os.environ)` then overrides `HOME`, `OPENCLAW_STATE_DIR`,
`OPENCLAW_CONFIG_PATH`. But `dist/paths-BMBAvkNf.js:22-28` resolves OpenClaw's
home through `resolveRequiredHomeDir(env, os.homedir)`, i.e. **`OPENCLAW_HOME`
takes precedence over `HOME`**, and `OPENCLAW_PROFILE` further namespaces state
paths (`dist/runtime-paths-C6MOwQ_j.js:223-235`). If either is exported in a
developer's shell or a future pixi activation, the `HOME` redirect is partially
bypassed and the test starts touching the real install — failing (or passing)
for a reason that has nothing to do with the config.

Note the *inverse* hypothesis does **not** hold: inheriting `PATH` is correct
and necessary (`#!/usr/bin/env node`), and clobbering `HOME` does not break
shebang resolution, which goes through `PATH` only. The design is right; the
allowlist is just one var short.

Fix direction: build the child env by stripping every `OPENCLAW_*` key from the
inherited copy and then setting exactly the three the test wants — that makes
the hermeticity claim in the `validate()` docstring (`:98-105`) total rather
than enumerated.

### NOTE-6 — the positive test validates the git-tracked source file in place

`src/robot_brain/test/test_openclaw_validates.py:140-143`, `:196,208`.

`config validate` is read-only today, and `test_validating_writes_only_where_the_
test_told_it_to` byte-compares the file afterwards with a good comment
(`:205-207`) explaining the boundary. So the risk is caught — but only by a
*different* test, after the fact. Validating a byte-identical copy written into
`tmp_path` would give the identical guarantee (the CLI never sees the path,
only the bytes) with zero chance of a future subcommand or auto-migration
touching the source tree. Judgement call; current form is defensible.

### NOTE-7 — the sandbox pair is asymmetric, and the "on" half forbids the README's own documented fallback

`src/robot_brain/test/test_openclaw_config.py:160`
(`assert sandbox['mode'] != 'off'`) vs `src/robot_brain/README.md:169-172`
("If your Pi has no Docker and OpenClaw complains anyway, either install it or
set `sandbox.mode: "off"` — but then also drop `workspaceAccess: "ro"`").

The two are consistent today (the README is instructing the operator about
*their merged config*, not this fragment), but the asymmetry means that if this
repo ever has to ship `mode:"off"` — the escape hatch it documents — a test must
be *deleted* rather than adapted. The companion test at `:144-148` is written as
an implication and does not have this problem. Fix direction, if touched at all:
express the second one the same way — `if mode == 'off': assert
'workspaceAccess' not in sandbox` — which forbids the same "read-only claim
without sandbox" state while surviving a deliberate policy change.

### NOTE-8 — README's `agents.list` parenthetical reads as if `agents` is the array

`src/robot_brain/README.md:104`: "one entry appended to the `agents.list` array
(`agents` is `{defaults, list}` — an array keyed by each entry's `id`, **not** a
map)". The em-dash clause attaches to `agents`, which is an *object*; it is
`list` that is the array. An operator merging by hand is exactly the reader who
will misparse this. One-word fix.

### NOTE-9 — two small factual slips in the new README prose

- `src/robot_brain/README.md:166-167` lists the sandboxed tool classes as
  `exec`/`read`/`write`/`edit`/`process` and drops `apply_patch`, which
  `docs/gateway/sandboxing.md:17` includes. Harmless, but this repo's README
  quotes upstream precisely elsewhere.
- `src/robot_brain/test/test_openclaw_validates.py:89` does
  `from importlib import resources` inside the function body while
  `robot_brain/agent.py:27` imports it at module level. No reason for the
  difference; matching the surrounding style is free.

---

## Acceptance criteria

| AC (from `context.md:9-23`) | Met? | Evidence |
|---|---|---|
| 1. `agents.list` array with `id` | yes | `openclaw.robot.json:32-57`; pinned by `test_openclaw_config.py:106` and negative control `test_openclaw_validates.py:152-153` |
| 2. `sandbox.mode` in enum, `workspaceAccess: "ro"` | yes | `openclaw.robot.json:40-43`; negative control `test_openclaw_validates.py:154-155` |
| 3. existing test updated in step | yes | no guarantee lost, two strengthened (see above); docstring rewritten per R9; `agent.py:41-43` comment fixed |
| 4. shell-out drift guard | yes | `test_openclaw_validates.py`, structurally no-op-proof (see R5/R7 analysis) |
| 5. don't touch `mcp.servers`/`tools.allow`/`bindings` beyond the reshape | **deviates, correctly** | `tools.allow` respelled (R3) and `tools.sandbox` added (R4). Both are manager-ruled with `openclaw doctor` evidence and both are re-verified in `implementation.md`. This is a *documented* departure from the issue's AC5 and the PR description should say so plainly so Sisyphus judges the widened diff on purpose, not by surprise. |

Architectural invariants (CLAUDE.md): none touched. `robot_brain` stays
ROS-free — the new subprocess is a Node binary, and `test_no_ros_runtime.py` is
unaffected. The skill API seam, the backend abstraction and the safety layer are
all downstream of this change and untouched.
