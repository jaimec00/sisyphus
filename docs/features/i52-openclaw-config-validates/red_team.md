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

---
---

# Round 2 — delta review (`b16a0f0..HEAD`, commits `6afe1a0` + `41a9880`)

Round 0 above is unchanged. This section reviews **only** the round-1 delta
against the manager's revised rulings (R2-revised, R4-revised, R12) in
`status.md:213-302` and the `[R1]` additions to `implementation.md`. Nothing
cleared in round 0 is re-litigated.

**Verdict: 1 BLOCK, 4 NOTEs.** The reversal is the right call and is documented
honestly. `sandbox_complaints()` is a genuine detector with real teeth in the
direction it was aimed. It has one hole, and it is the same class of hole the
detector exists to close.

## Round 0 disposition

| Round 0 finding | Status |
|---|---|
| BLOCK-1 (bootstrap) | **Fixed**, in-loop per R12. Verified below; the fix is correct. |
| NOTE-1 (`mode:"all"` + `"ro"` moves the effective workspace) | **Fixed by reversal** — `mode:"off"`, no `workspaceAccess`. See BLOCK-2's second half for the residue. |
| NOTE-2 (prefix mangling unguarded) | **Fixed**, and better than suggested — the glob is now *derived* from the key, plus a separate mangling guard. |
| NOTE-3 (hand-copied `mode` enum) | **Fixed**, with a comment at `test_openclaw_config.py:154-157` saying why it is deliberately absent. Good. |
| NOTE-4 (no CLI version in the report) | **Fixed** (`cli_version()`); version-floor half deferred, correctly, as #51 scope. |
| NOTE-5 (`OPENCLAW_HOME`/`PROFILE`) | **Fixed**, and the implementer self-reported and rewrote a vacuous first attempt. Judged below: honest. |
| NOTE-6, 7, 8, 9 | Not fixed; the manager ruled them follow-ups. Not re-raised. |

---

## BLOCK

### BLOCK-2 — `sandbox_complaints()` returns clean on the one config that reproduces Bug B: no `sandbox` key at all

`src/robot_brain/test/test_openclaw_config.py:169-183` (specifically `:170`,
`mode = sandbox.get('mode', 'off')`).

The manager asked for a config that is *genuinely broken in the way R4 was
written to prevent* and that the detector passes. Here it is, and it is not
exotic — it is the single most likely future edit to this fragment.

Delete `"sandbox": {"mode": "off"}` from
`src/robot_brain/robot_brain/openclaw/openclaw.robot.json:40-42`, on the
entirely reasonable-sounding grounds that `off` is OpenClaw's documented default
(`docs/gateway/sandboxing.md:31`) and shipping a default is noise — which is the
*same* argument R4-revised used to drop the inert `tools.sandbox` gate. Then:

- `sandbox_complaints()` reads `sandbox = {}`, defaults `mode` to `'off'`, finds
  no `workspaceAccess` and no gate → returns `[]`.
  `test_the_shipped_sandbox_settings_do_not_contradict_each_other:226` passes.
- `openclaw config validate` passes — `sandbox` is optional.
- The whole suite is green.

But this is a **merge fragment**, not a whole config, and an agent entry with no
`sandbox` key does not get `off` — it *inherits*
`agents.defaults.sandbox.mode` from whatever the operator already has
(`docs/tools/multi-agent-sandbox-tools.md:184`,
`agents.list[].sandbox.mode > agents.defaults.sandbox.mode`; and the
troubleshooting entry at `:382-384`, "Check if there's a global
`agents.defaults.sandbox.mode` that overrides it"). An operator who sandboxes by
default is not hypothetical — OpenClaw's own documented Example 3
(`multi-agent-sandbox-tools.md:139-145`) ships
`agents.defaults.sandbox: {mode: "non-main"}`, and a Telegram channel session is
*always* non-main (`sandboxing.md:38`, `multi-agent-sandbox-tools.md:345`).

Concrete failure scenario: someone tidies the key away, the fragment merges into
that operator's config, the robot agent is silently sandboxed with no
`bundle-mcp` gate, and every `robot__*` tool is filtered before the provider
request (`sandbox-vs-tool-policy-vs-elevated.md:110`). The brain answers on
Telegram with nothing to drive. That is Bug B verbatim — the failure R4 exists to
prevent — reached through a config this suite calls healthy.

The explicit `"mode": "off"` currently shipped is therefore **load-bearing in a
way nothing states or checks**: it is not a redundant default, it is an override
that makes the fragment self-contained against the operator's global posture.
`README.md:163-164` gets close ("the only sandbox key it carries. That is a
decision, not an omission") but says the decision was *off vs on*, not
*explicit vs inherited*.

**Fix direction** (two lines in the detector, one row in its test, one sentence
in the README):

```python
if 'mode' not in sandbox:
    complaints.append(
        'sandbox.mode is unset, so the agent inherits '
        'agents.defaults.sandbox.mode from the operator config')
```

with `assert sandbox_complaints({'tools': {}})` added to
`test_the_sandbox_consistency_check_detects_what_it_forbids` (`:197-206`), and a
clause in README's "Why sandboxing is off" saying the key is explicit *because*
omitting it would inherit.

**Fix this alongside it, same function, three more lines** (NOTE-grade on its
own — nothing is broken today — but the function is already open and this is the
finding that caused the reversal): the detector does not encode the
`workspaceAccess` half. `sandbox_complaints({'sandbox': {'mode': 'all',
'workspaceAccess': 'ro'}, 'tools': {'sandbox': {'tools': {'alsoAllow':
['bundle-mcp']}}}})` returns `[]` — yet that is *exactly* the posture R2 was
reversed away from, because `dist/compact-DLB4d8IL.js:551` swaps the effective
workspace for anything but `"rw"` and the compaction path can come back without
`AGENTS.md`. A future editor who follows the test's own invitation ("Should that
trade ever be revisited, the detector above is the thing that stops the sandbox
arriving without its tool gate", `:223-224`) gets the gate checked and the
compaction hazard waved through. The docstring is *honest* — it claims only the
gate — but the detector should carry both reasons, not one:

```python
elif sandbox.get('workspaceAccess', 'none') != 'rw':
    complaints.append(
        f'sandbox.mode is {mode!r} with workspaceAccess '
        f'{sandbox.get("workspaceAccess", "none")!r}: the effective workspace '
        'becomes the sandbox workspace (dist/compact-DLB4d8IL.js:551) and a '
        'compaction turn can lose AGENTS.md')
```

That also makes the PASS row's choice of `'rw'` at `:205` *meaningful* rather
than incidental — right now `'ro'` would pass that row identically.

---

## What I checked and cleared

### The R12 bootstrap fix is correct

`scripts/start-feature.sh:76-77`. Attacked all three of the manager's questions:

- **Does it run in the right shell/env?** Yes. The two lines are appended to the
  same `inner` string with `; \`, so they execute in the same `bash -c` after
  `export PATH="$HOME/.pixi/bin:…"` (`:70`) and after `cd '$wt'` (`:72`) — `pixi`
  resolves and the manifest is the new worktree's. Quoting is sound: `inner` is
  double-quoted, so `$wt`/`$log`/`$MODEL` interpolate at build time as intended,
  and the new echo strings are single-quoted, which protects their `(`, `)` and
  `;` from the outer expansion. They contain no `$` or backtick. `bash -n` clean
  per `implementation.md:312-313`.
- **Is non-fatal right?** Yes. `bash -c "$inner"` runs without `set -e`, so a
  bare failure would fall through anyway; the `|| echo` makes it *say so*, and it
  matches how `pixi install` is already handled two lines up. Dying instead would
  strand a created worktree and branch with no manager to report it. And the
  manager's worry — "six red tests whose cause scrolled past" — is answered
  in-suite, not in the log: `test_the_cli_is_installed_where_the_suite_expects_it`
  fails separately from the config tests, and its message names
  `pixi run install-openclaw`. The diagnosis travels with the failure.
- **Anything else that creates worktrees?** Checked. `scripts/start-op.sh:66-74`
  builds its own `inner` that runs neither `pixi install` nor any test, and
  `.claude/commands/run-op.md:9,40` states operational agents skip test-runner
  rounds entirely — so ops worktrees need no `node/` and are not broken by R5.
  `.github/workflows/guards.yml` has no pixi env and runs only docs-clean.
  A hand-made `git worktree add` / fresh clone is covered by
  `DEVELOPMENT.md:50-58`, which is accurate and states the prerequisite plainly.

One residual, NOTE-1 below.

### `mode: "off"` is not under-specified — no surface is gained

I looked for a concrete tool or path the old posture denied that `off` now
permits. There is none, and the reason is structural rather than lucky:

- Sandboxing controls **where** tools run, never **which**
  (`docs/gateway/sandboxing.md:9,356`: "Tool allow/deny policies still apply
  before sandbox rules. If a tool is denied globally or per-agent, sandboxing
  doesn't bring it back").
- `tools.allow: ["robot__*"]` is a non-empty allowlist, and
  `sandbox-vs-tool-policy-vs-elevated.md:67` is explicit: "If `allow` is
  non-empty, everything else is treated as blocked." The allowlist is total, and
  it is *confirmed empirically* by the `openclaw doctor` warning the implementer
  recorded — the `message` tool being unavailable is direct evidence that
  built-ins are blocked by it.
- So no `exec`/`read`/`write`/`edit`/`apply_patch`/`process` tool exists for this
  agent in either posture; `workspaceAccess: "ro"` was governing a container
  mount for filesystem tools that cannot be called; `tools.elevated` is
  exec-only (`sandbox-vs-tool-policy-vs-elevated.md:116`) and exec is denied;
  `skills: []`.
- The MCP tools themselves execute on the laptop over `ssh -T`, not on the Pi, so
  "runs on the host rather than in a container" describes a process that was
  never local.

The trade is genuinely one-sided: `off` removes an unverified Docker
prerequisite and a documented prompt-loss hazard, and gives up protection that
was already vacuous.

### The R2+R4 detector: teeth confirmed, and the PASS row is not tautological

Traced `sandbox_complaints()` by hand on each row of
`test_the_sandbox_consistency_check_detects_what_it_forbids:197-206`:

- The three FAIL rows all reach the branch they are meant to and all return
  non-empty. The `mode:'all'` + no-gate row is the R4 case and is real.
- The PASS row (`:204-206`, `mode:'all'` + `workspaceAccess:'rw'` + the gate) is
  **load-bearing, not tautological**: it pins the detector's *silence*, so a
  future "hardening" that made the function complain whenever `mode != 'off'`
  would fail here. That is the crying-wolf direction and it is genuinely
  guarded. `implementation.md:276`'s claim checks out.
- The detector is also strictly stronger than the round-0 pair it replaced: it
  accepts `group:plugins` and `robot__*` as admissions (per
  `config-tools.md:52-57`) rather than only `bundle-mcp`, and it reads
  `gate['allow']` as well as `gate['alsoAllow']` — both correct per
  `dist/tool-policy-Bx6D7Inl.js:151-158`, which merges the two.
- Its false-rejection surface is narrow: I could not construct a legitimate
  config it rejects. `mode:'off'` with no companions is clean; sandbox-on with
  any of the three admissions is clean.

Its one hole is BLOCK-2.

### The hermeticity rewrite is honest, and the strip cannot misfire here

`test_the_child_inherits_no_openclaw_variable_we_did_not_set:247-279`. The
manager's question was whether the tautology merely moved up a level. It did
not:

- The test plants three adversarial variables via `monkeypatch` (including one
  invented name) and then asserts on what the *production* helper
  `scratch_environment()` — the one `validate():132` actually uses — hands the
  child. Reverting the helper to `dict(os.environ)` fails it
  (`implementation.md:290`). That is a test of a policy against a hostile input,
  not a restatement of the code.
- The `PATH`-must-survive assertion (`:278-279`) pins the *opposite* error, and
  the over-strip mutation row (`implementation.md:291`) shows it bites. Pinning a
  policy from both sides is exactly right.
- The docstring's reason for not going end-to-end is **verifiable and correct**,
  not an excuse: `dist/paths-BMBAvkNf.js:46-49` returns
  `env.OPENCLAW_STATE_DIR` immediately when set, before `OPENCLAW_HOME` can
  influence `effectiveHomedir`. So with `OPENCLAW_STATE_DIR` also set, neither
  decoy can move what `config validate` writes, and an end-to-end assertion
  really would be vacuous. Declaring that in the docstring instead of shipping a
  green-but-empty test is the standard this repo asks for; the self-report is to
  the implementer's credit.
- **Can stripping all `OPENCLAW_*` break the run and be misattributed?** No, not
  from anything this repo controls: `pixi.toml` has no `[activation]` block and
  sets no `OPENCLAW_*`, `install_openclaw.sh` sets none, and the CLI needs none
  to run `config validate` (the only ones in play — `OPENCLAW_HOME`,
  `OPENCLAW_PROFILE`, `OPENCLAW_STATE_DIR`, `OPENCLAW_CONFIG_PATH` — are
  precisely the ones being controlled). A developer's own exported variable is
  the case the strip exists to defeat.

Known limit, acceptable: the test exercises the helper, not the call path, so a
future refactor that gave `validate()` its own env-building would slip past it.
The mutation row proves the coupling holds today.

### The glob derivation, the version report, the enum

- **Derivation** (`:130,133`): `allowed == [f'{server_key}__*']` where
  `server_key` comes from the shipped `mcp.servers`, plus `:148`
  `server_key == MCP_SERVER_NAME`. Together this is *stronger* than round 0 —
  both the JSON↔JSON and the JSON↔Python couplings are pinned, where round 0
  pinned only the latter.
- **Mangling guard** (`:136-150`): reproduces the parts of
  `config-tools.md:59` that can bite here — charset, case, and letter-initial —
  conservatively (requiring lowercase rejects `Robot`, which OpenClaw would
  mangle to `robot`, so the test errs safe regardless of whether lower-casing is
  exactly the rule). Duplicate-prefix suffixing is implicitly excluded by the
  single-key unpack. Length truncation is not covered — NOTE-3.
- **Version report** (`:140-161`): `report()` now names the build, and
  `implementation.md:294-301` shows it on a real failure rather than by
  inspection. `cli_version()` is `lru_cache`d and, being inside an assertion
  message, is evaluated lazily — it costs nothing on a green run. One failure
  mode, NOTE-2.
- **Enum**: gone, with `:154-157` recording *why* ("hand-copying it from
  documentation is the habit that produced #52"). Exactly right.

### Nothing round 0 relied on was dropped; the ratchet is honest

Counted the delta function by function.

Removed: `test_the_sandbox_does_not_filter_away_the_robot_tools` and
`test_the_sandbox_grants_no_more_than_read_access_to_the_workspace`. Their
guarantees:

- "mode ≠ off ⟹ MCP admission present" → preserved and widened in
  `sandbox_complaints()`'s `elif` (`:179-182`).
- "`workspaceAccess` under `mode: off` is a comment, not a restriction" →
  preserved as an explicit complaint (`:175-176`), which is *stronger* than the
  old form: it now forbids the inert state instead of merely forbidding
  `mode: 'off'`.
- "mode must be on" → deliberately dropped with the posture. Correct, and it
  resolves round-0 NOTE-7's asymmetry as a side effect.

Everything else in the file is unchanged, including `walk()` (still list-aware,
so the secret scanner still reaches the agent entry), `agent()`'s uniqueness
assertion, the empty-allowlist assertion (`:132`), and all nine
launch/tool/timeout/asset tests.

Counts: `test_openclaw_config.py` 14 → 15 functions (−2, +3);
`test_openclaw_validates.py` 6 → 7 collected (+1). Net **+2**, so 46 → 48, and
48 − 38 = 10 = the 3 + 7 `implementation.md:318-320` claims against `main`.
`scripts/test_baseline.json:6` reads 48. **Honest.**

### Prose after the reversal

Checked the new prose against the round-0 honesty bar and found no overclaim:

- `README.md:163-192` ("Why sandboxing is off") states the compaction hazard as
  "Read from the installed dist, not observed on a Pi" (`:184`) — precisely the
  right epistemic label, and `implementation.md:373-379` repeats it. The
  `dist/` line citations are real and I verified two of them.
- `README.md:177-178` claims "`test_openclaw_config.py` fails if the mode is ever
  flipped on without the gate" — true, and it does **not** claim the
  `workspaceAccess` hazard is tested, which it is not. Correctly scoped.
- `test_openclaw_config.py:209-225` and `:223-224` likewise claim only the gate.
- `README.md:220-222` ("carries no sandbox setting that contradicts another") is
  the detector's actual contract, not a safety claim.

One stale word, NOTE-4.

---

## NOTE

### NOTE-1 — the bootstrap's own warning is destroyed before the manager can read it

`scripts/start-feature.sh:76-77` write to `$log` with `tee -a` / `>>`, and then
`:80` runs `claude … | tee '$log'` — **without `-a`**, which truncates. So the
`install-openclaw returned nonzero` breadcrumb exists for the few seconds before
Claude starts and is then deleted. It survives only in the tmux pane, which the
manager agent cannot read.

Pre-existing (`:80` was already `tee '$log'` before this PR), but R12 is what
makes it matter: the one artifact explaining a red `robot_brain` is erased by
the process that will be asked to explain it. Not a BLOCK because the in-suite
diagnosis is self-describing — `test_the_cli_is_installed_where_the_suite_
expects_it` fails separately and its message names the remedy. Fix direction:
`tee -a '$log'` at `:80`. One character, and it makes every future bootstrap
warning survivable.

### NOTE-2 — the failure reporter can eat the failure it is reporting

`src/robot_brain/test/test_openclaw_validates.py:140-161`.

`report()` calls `cli_version()`, which spawns a second subprocess. If that
spawn raises — `TimeoutExpired` at 60s, `OSError`, a binary that hangs on
`--version` — the exception propagates *out of the assertion message
expression*, so pytest reports the secondary failure and the real
`AssertionError` carrying the CLI's stderr is never constructed. The scenario is
narrow (it needs a binary broken in a way that still passed `openclaw_binary()`)
but the cost of the guard is three lines, and this is the one code path whose
entire job is to be readable when everything else has gone wrong. Fix direction:
wrap the body of `cli_version()` in `try/except Exception: return 'unknown
version'`.

### NOTE-3 — the mangling guard covers charset and case but not truncation

`src/robot_brain/test/test_openclaw_config.py:149`,
`re.fullmatch(r'[a-z][a-z0-9_-]*', server_key)`.

`config-tools.md:59` also says "long or duplicate prefixes may be truncated or
suffixed". Duplicates are excluded structurally by the single-key unpack, but
length is unbounded by this regex, so a future `robot_manipulation_stack` would
pass the guard while OpenClaw might truncate its prefix. Round 0 suggested a
bound; adopting it is one character (`{0,20}` in place of `*`). Low likelihood,
zero cost.

Related, same two lines: `server_key, = FRAGMENT['mcp']['servers']` at `:130`
and `:147` means adding a *second* MCP server to the fragment (a perception
server, say) fails both tests with a bare
`ValueError: too many values to unpack (expected 1)`. That is a loud trip-wire,
which is fine, but the message does not say what to do. A one-line
`assert set(FRAGMENT['mcp']['servers']) == {MCP_SERVER_NAME}, …` ahead of it
would turn a puzzle into an instruction.

### NOTE-4 — "the one pinned in this env" is still the wrong word

`src/robot_brain/README.md:236-237`. `scripts/install_openclaw.sh:36` runs
`npm install … openclaw` unpinned, so nothing in this env is pinned; the phrase
should be "the one installed in this env". It matters slightly more now that
`report()` names the build — a reader who trusts "pinned" will not think to
re-run `install-openclaw`. Carried over from round 0's NOTE-4 territory, one
word.

---

## Round 2 acceptance-criteria delta

| AC | Round 2 status |
|---|---|
| 2. `sandbox.mode` is a legal enum value | still met, now `"off"` (`openclaw.robot.json:40-42`); still negative-controlled by `test_openclaw_validates.py:189-190` putting `"read-only"` back |
| 5. minimal touch | **widened twice, both on the record**: `tools.allow` respelled (R3) and `scripts/start-feature.sh` + `DEVELOPMENT.md` edited (R12). The PR description must state both, plus the third deviation — the issue's own snippet proposed `sandbox.mode: "all"` and this ships `"off"` for a reason the issue could not have known. `status.md:250-253` already says to; make sure it reaches the PR body, because a reviewer comparing the issue to the diff will otherwise read the sandbox line as a mistake. |

All other ACs unchanged from round 0. Architectural invariants: still none
touched; `robot_brain` remains ROS-free.
