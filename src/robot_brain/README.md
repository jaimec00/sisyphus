# robot_brain

The robot's brain, which under **D21** is not a program in this repo: it is a
dedicated **OpenClaw Telegram agent** whose native tool-call loop *is* the
perceive → act → re-perceive loop (D4), driving the skills that `robot_mcp`
exposes as MCP tools. There is no planner loop here, no prompt assembly and no
tag parser — D21 deleted all three.

What is left, and what this package owns, is the pair of files that make a
generic agent *this robot's* brain:

- `robot_brain/openclaw/AGENTS.md` — the **operating prompt**: the skill API
  (names, arguments, units), the observation format, the failure vocabulary and
  what to do about each code, the safety envelope, and worked examples
  including a recovery and a safety clamp. OpenClaw reads an agent's system
  prompt from the `AGENTS.md` in its workspace, so the file ships under the
  name it must be installed as.
- `robot_brain/openclaw/openclaw.robot.json` — a **merge fragment** for
  `~/.openclaw/openclaw.json`: the `robot` MCP server, the `robot` agent entry,
  and the Telegram binding. **Not** a drop-in config file (see below).
- `robot_brain/agent.py` — `operating_prompt()` and `config_fragment()`, the
  loaders the tests and any deploy script use, plus `AGENT_ID` /
  `MCP_SERVER_NAME` so the agent's name is spelled once.

The assets live inside the importable package (like `robot_safety`'s
`limits.yaml`), so they load from a source checkout and from a
symlink-installed build alike, with no ament index and no ROS graph. This
package is **not** a ROS node and no longer depends on `rclpy`.

## How the pieces sit

```
Jaime (Telegram)
  → OpenClaw `robot` agent            [Raspberry Pi]   ← AGENTS.md + the config fragment
      → MCP tool call over `ssh -T laptop …`  (WireGuard)
        → robot_mcp stdio server      [laptop]
          → safety layer (clamp/abort, D4/D17)
            → Mock backend
```

`robot_mcp` speaks **stdio only**, and it runs on the laptop where the code,
the env and (later) ROS live — so the Pi launches it over SSH and talks MCP on
that pipe. `-T` because a pty would inject terminal bytes into a stream that
must carry nothing but MCP frames. (Verified on the laptop: `pixi`'s manifest
warning goes to **stderr**; stdout carries only JSON-RPC.)

## What the agent is *not* given

`mcp.servers.robot.toolFilter.include` exposes every tool the server serves
**except `reset`**, and the prompt does not mention it. `reset` restores the
seed world: harmless against Mock, but this is the same tool boundary a Sim or
Real backend will front (D9), where `RobotBackend.reset()` is real motion and
real lost state. A planner that decides mid-chore to "start over" must not be
able to; an operator who wants it drives the server directly. The prompt tells
the agent plainly that there is no undo, so it plans forward instead of
looking for one.

The tests treat this as a **classification**, not an exclusion list: a tool
added to `robot_mcp` fails both the config suite and the prompt suite until
someone consciously exposes it or adds it to `WITHHELD_TOOLS` with a reason.
Neither test forces a future tool into the model's hands.

## The prompt is guarded, not generated

Prompt quality is the deliverable (D22: an LLM has to *operate* this robot
well), so it is hand-written prose. The risk that buys is silent drift — rename
a skill and the prompt still reads beautifully while teaching a call that no
longer exists. `test/test_prompt_drift.py` is what narrows that: wherever a
claim has a live source, the test reads the expected value from there instead
of restating it — `robot_mcp.tools.TOOL_NAMES`, each tool's own `inputSchema`,
`robot_safety.SafetyLimits.defaults()`, `robot_backends.default_world()` and
its `RobotModel` (the arm's reach) — and a word in backticks that names nothing
real fails the suite.

That is the aim, not a guarantee. The prompt's description of the **body** is
where the aim runs out: the arm count, its grippers, the column's travel and the
arm's reach do have owners here and are read from them, but the drivetrain has
no live source on this side of the skill-API seam (D30), so the base is pinned
instead by a hand-typed ledger of superseded claims — it catches the claim
already known to be stale (D1's four-wheel base, retired by D26) and not the
next one. Elsewhere a handful of assertions still name a heading or a phrase by
hand, and some owned claims have no assertion at all (`known_locations` is the
one to know about). `TestBodyDescription` states its own limits in place.

## Installing the agent on the Pi

**Not run from this worktree** — but no longer unverifiable either. Since #51
the pixi env ships OpenClaw itself (`pixi run install-openclaw`), so the
*shape* of the fragment is now checked automatically on every test run:
`test/test_openclaw_validates.py` hands the shipped file to the real
`openclaw config validate` and fails if it is rejected. That found and fixed
two schema errors (#52) that had been sitting in a fragment written from
`docs.openclaw.ai` alone.

What is still an unobserved procedure is everything that needs the *Pi*: this
repo cannot reach it, cannot resolve the `laptop` SSH alias, and has no
Telegram account. Steps 3, 4, 5, 7 and 8 below are documented, not observed.
Step 6 is where you find out whether *your* build agrees with the one in this
env.

1. **Copy the two assets to the Pi** (from a checkout of this repo, or by
   `scp`):
   ```bash
   ssh pi 'mkdir -p ~/.openclaw/agents/robot'
   scp src/robot_brain/robot_brain/openclaw/AGENTS.md pi:~/.openclaw/agents/robot/AGENTS.md
   scp src/robot_brain/robot_brain/openclaw/openclaw.robot.json pi:/tmp/
   ```
   `~/.openclaw/agents/robot/` is the agent's workspace; `AGENTS.md` in it is
   the system prompt.
2. **Merge the fragment into `~/.openclaw/openclaw.json`** — do not copy it
   over the file. Merge exactly three keys, leaving everything else alone:
   - `mcp.servers.robot`
   - one entry appended to the `agents.list` array — `agents` is an object with
     exactly two keys, `defaults` and `list`, and `list` is an **array** whose
     items carry their own `id`, **not** a map keyed by agent name
   - one entry appended to the top-level `bindings` array.
3. **Point the launch command at your checkout — you almost certainly have to
   edit this.** The fragment hard-codes `/home/sisyphus/worktrees/main` in
   **two** places inside `mcp.servers.robot.args` — the `--manifest-path` and
   the path to `scripts/robot-mcp-launch.sh` — plus the `ssh` destination alias
   `laptop` (which must resolve on the *Pi's* `~/.ssh/config`, not on the
   laptop's). None of the three was verified against a real Pi from this repo.
   Change every one that does not match your machines; a stale path here fails
   as "the agent has no tools", not as a readable error.

   There is **no package list to edit**: the launcher discovers every
   `src/<pkg>` with a `package.xml` and puts it on `PYTHONPATH` itself. That
   list used to be a third thing to hand-edit here, and it is what broke the
   deployment in #55 — `robot_world` was added to the workspace, the list was
   not, and the server stopped importing.
4. **Check the SSH leg by hand first**, before involving OpenClaw — with the
   command spelled exactly as the fragment spells it:
   ```bash
   ssh -T laptop "bash -lc 'exec pixi run --frozen --manifest-path …/pixi.toml …/scripts/robot-mcp-launch.sh'" \
     <<< '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
   ```
   **The outer quotes are load-bearing.** `ssh` appends its arguments
   "separated by spaces, before it is sent to the server to be executed"
   (`ssh(1)`) and does not re-quote them, so the entire remote command has to
   be *one* argument carrying its own quoting. Written as separate words
   (`ssh -T laptop bash -lc 'exec …'`), the remote shell sees
   `bash -lc exec pixi …`, `bash -c` takes only the next word as its command
   string, and the remote runs a bare `exec` — a no-op that **exits 0 having
   started nothing**, which surfaces as "the agent has no tools" with no error
   anywhere. That is what the fragment used to ship;
   `test_openclaw_config.py::test_the_flattened_remote_command_is_one_the_remote_shell_can_run`
   now asserts the flattened, re-split form for exactly this reason.

   One JSON-RPC frame must come back on stdout and **nothing else** — no
   banner, no motd, no warning. If the shell prints anything of its own, fix
   that first: it will corrupt the MCP stream.

   What *has* been checked, on the laptop, is the half that does not need the
   Pi: the remote command the fragment produces — `ssh`'s flattening simulated
   by joining the arguments after the destination and running the result the
   way `sshd` does, `bash -c "<flattened string>"` — puts a single JSON-RPC
   frame on stdout and nothing else, with `pixi`'s manifest warning on stderr
   and no output at all from this account's profile files. The `-l` is checked
   too, on this laptop: with a stripped environment (`env -i HOME=… USER=…`),
   `bash -c 'command -v pixi'` finds nothing and `bash -lc 'command -v pixi'`
   resolves it — so the login shell is what makes `pixi` reachable over a
   non-interactive `ssh` command. What is **not** checked is the `ssh` hop
   itself, or the *Pi's* login shell: the `laptop` alias does not resolve from
   the laptop, so only the Pi can run this step. If a login shell on your
   machines does turn out to print something, drop the `-l` (use `bash -c`
   with an absolute `pixi` path) rather than trying to document around it.
5. **Add the Telegram account on the Pi, with the `openclaw` CLI** — the bot
   token is a secret and is never committed to this repo. Then set the
   binding's `match.accountId` to that account's id (the fragment ships
   `REPLACE_WITH_TELEGRAM_ACCOUNT_ID`).
6. **Verify the config OpenClaw actually parsed:**
   ```bash
   openclaw config validate        # does this build accept these fields?
   openclaw doctor                 # do the values match anything real?
   openclaw agents list --bindings # is `robot` bound to your Telegram account?
   ```
   `config validate` is the same check the test suite runs here, so it should
   pass unless your build's schema differs; if it does, that difference is the
   bug and this repo wants to hear about it. `doctor` goes further and is the
   one worth reading closely — it catches what the schema cannot, because
   `tools.allow` is just `array<string>` to the validator. One warning this
   fragment is deliberately shaped to avoid, quoted from a real run:

   - `allowlist contains unknown entries (mcp__robot__*)` — OpenClaw exposes
     an MCP tool as `<server>__<tool>`, so the glob is `robot__*`. The
     `mcp__…` form is Claude Code's convention and matches nothing. The prefix
     is the server key put through a mangling pass (lower-cased, characters
     outside `[A-Za-z0-9_-]` become `-`, a non-letter start gets an `mcp-`);
     `robot` survives it unchanged, a renamed server might not.

   The fragment deliberately omits `model`, so the agent inherits your global
   default; set it in the entry if you want a specific one.

   **Why sandboxing is off.** The fragment ships `sandbox: {"mode": "off"}` — the only sandbox key it
   carries. That is a decision, not an omission, and the two things that make
   turning it on non-trivial are worth stating before someone does:

   - **Turning it on filters the robot tools away by default.** The sandbox
     tool policy is a *second* allow gate in front of MCP tools. `openclaw
     doctor` on a `mode: "all"` copy of this fragment: `tools.sandbox.tools.
     alsoAllow (unset) does not include "bundle-mcp" … Sandboxed agents will
     filter bundled MCP tools before provider requests`. The agent-level key
     `agents.list[].tools.sandbox.tools` is genuinely consulted and overrides
     the global one for that agent (`dist/tool-policy-Bx6D7Inl.js:148-158`,
     `dist/agent-tools.policy-YD9HuYgO.js:101`), so
     `tools.sandbox.tools.alsoAllow: ["bundle-mcp"]` is the fix — but the point
     is that the config validates *without* it while the robot has nothing to
     drive. `test_openclaw_config.py` fails if the mode is ever flipped on
     without the gate.
   - **`workspaceAccess` other than `"rw"` moves the effective workspace.**
     With sandboxing enabled, `effectiveWorkspace` becomes the *sandbox*
     workspace rather than the agent's (`dist/compact-DLB4d8IL.js:551`), and
     the compaction path resolves its bootstrap context from that — so a long
     conversation could compact and come back without `AGENTS.md`, which is the
     whole brain. Read from the installed dist, not observed on a Pi; it is the
     reason this fragment stopped at `off`.

   Against that, sandboxing would protect nothing here: OpenClaw sandboxes
   `exec`/`read`/`write`/`edit`/`apply_patch`/`process`-class tools
   (`docs/gateway/sandboxing.md:17`), and this agent is allowed none of them —
   only `robot__*`. `off` also retires the Docker prerequisite that `mode:
   "all"` implies (`sandbox.backend` defaults to `docker`). If you widen
   `tools.allow` to anything that executes, revisit all of this.

   **Do not delete `"mode": "off"` as a redundant default — it is an
   override.** `off` is the default only for a *whole* config; this is a merge
   fragment, and OpenClaw resolves an entry's mode as
   `agents.list[].sandbox.mode ?? agents.defaults.sandbox.mode ?? "off"`
   (`dist/config-Dy4vED5-.js:153`). An entry that says nothing therefore adopts
   *your* global posture — and if that is `agents.defaults.sandbox: {mode:
   "non-main"}`, which is what OpenClaw's own multi-agent example ships, the
   robot is sandboxed with no gate and no tools, because a Telegram channel
   session is always non-main. Stating the mode is what makes the fragment
   self-contained. (The inert `tools.sandbox` gate *was* dropped as redundant;
   that key is not an override, and the distinction is the whole point.)
   `test_openclaw_config.py` fails if the key goes missing.

   **Then check the agent can still *answer*.** `doctor` warns that with a
   `robot__*`-only allowlist "the message tool is unavailable for that agent;
   explicit channel actions such as sendAttachment, upload-file, thread-reply,
   or reply can fail". Plain replies may still work; the explicit channel
   actions may not. Say hello to it before asking for a chore, and if it does
   not answer, **`tools.allow` is the first thing to relax** — add `"message"`
   or `"group:messaging"` alongside `robot__*` rather than dropping the scope
   entirely.
7. **Text the agent "clear the table"** and read the tool-call log. Expected:
   `get_observation`, then a `navigate_to` / `grasp` / `navigate_to` / `place`
   loop repeated until the table is empty, then a plain-language report. The
   Mock's seed world puts two graspable objects on the table, so a single
   grasp is a wrong answer, not a finished chore.
8. **Prove the gate is server-side**: ask it to "raise the column to two
   metres". The tool result must come back `ok` with `skill.height` of `1.2`
   and a `reason` saying it was clamped — the clamp happens below the tool
   boundary, so no prompt wording can talk the agent past it. Then ask it to
   "put your hand a metre underground": that one must come back
   `status: "failed"`, `code: "rejected"`, naming the `below_floor` keep-out
   region.

## What is tested here, and what is not

- **Tested:** the prompt does not drift from the live tools, schemas, limits
  and seed world; the fragment parses, names the agent consistently, launches
  *this* repo's server over stdio without a pty, carries every package the
  server needs, exposes exactly the tools that exist under the name OpenClaw
  will actually give them, carries no sandbox setting that contradicts another,
  and holds no credential.
- **Tested against the real OpenClaw**, since #51/#52: the shipped fragment is
  accepted by `openclaw config validate` from the CLI in the pixi env
  (`test/test_openclaw_validates.py`). The same test reintroduces the two
  schema bugs #52 fixed and requires the CLI to reject them, so it cannot pass
  against a binary that has quietly become a no-op. It **hard-fails** if
  `pixi run install-openclaw` has not been run: a drift guard that skips itself
  is not a guard.
- **Not enforced anywhere, and not by this prompt:** max-steps, chore timeout,
  stuck-detection and one-task-at-a-time. `PROJECT.md` requires them
  server-side, "never trusted to the LLM"; they are unbuilt (deferred D16), and
  `AGENTS.md`'s "three failed attempts is a report, not a fourth attempt" is
  the agent being careful, **not** a guard. See `robot_mcp/README.md`'s
  "Deliberately absent".
- **Not tested, anywhere:** that *your* build of OpenClaw agrees with the one
  installed in this env (`install-openclaw` is unpinned, so "this env" is
  whatever npm last resolved — the failure message names the build that
  disagreed), that the tool globs and the sandbox gate behave at
  runtime the way `openclaw doctor` says they will, that the SSH leg works from
  the Pi, that any of the three hard-coded paths in step 3 are right for your
  machines, and that an LLM given this prompt actually clears the table.
  Nothing in this repo has ever run against a real Pi; the first four are steps
  3/4/6 above. For the last, the closest
  automated stand-in is
  `src/robot_mcp/test/test_clear_the_table.py`, which drives the whole chore
  over real MCP calls with a deterministic driver in place of the model — it
  proves the loop *can* be closed from the payloads alone, not that the model
  closes it.
