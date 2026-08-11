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
longer exists. `test/test_prompt_drift.py` closes that: every checkable claim
is compared against the **live** source that owns it —
`robot_mcp.tools.TOOL_NAMES`, each tool's own `inputSchema`,
`robot_safety.SafetyLimits.defaults()` and `robot_backends.default_world()`. No
expected value there is typed by hand, and a word in backticks that names
nothing real fails the suite.

## Installing the agent on the Pi

**Not run from this worktree.** OpenClaw is not installed on this laptop and
this repo cannot reach the Pi, so everything below is a documented procedure,
not an observed result. The field names come from `docs.openclaw.ai`; step 6
is where you find out if this build spells them the same.

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
   - `agents.entries.robot`
   - one entry appended to the top-level `bindings` array.
3. **Point the launch command at your checkout — you almost certainly have to
   edit this.** The fragment hard-codes `/home/sisyphus/worktrees/main` in
   **three** places inside `mcp.servers.robot.args`: the four `PYTHONPATH`
   entries, the `--manifest-path`, and the `ssh` destination alias `laptop`
   (which must resolve on the *Pi's* `~/.ssh/config`, not on the laptop's).
   None of those three was verified against a real Pi from this repo. Change
   every one that does not match your machines; a stale path here fails as
   "the agent has no tools", not as a readable error. All four packages
   (`robot_skills`, `robot_backends`, `robot_safety`, `robot_mcp`) must be on
   `PYTHONPATH` — `robot_safety` is a runtime dependency since the safety gate
   landed, and without it the server does not import at all.
4. **Check the SSH leg by hand first**, before involving OpenClaw:
   ```bash
   ssh -T laptop 'PYTHONPATH=… pixi run --frozen --manifest-path …/pixi.toml python -m robot_mcp' \
     <<< '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
   ```
   One JSON-RPC frame must come back on stdout and **nothing else** — no
   banner, no motd, no warning. If the shell prints anything of its own, fix
   that first: it will corrupt the MCP stream.
5. **Add the Telegram account on the Pi, with the `openclaw` CLI** — the bot
   token is a secret and is never committed to this repo. Then set the
   binding's `match.accountId` to that account's id (the fragment ships
   `REPLACE_WITH_TELEGRAM_ACCOUNT_ID`).
6. **Verify the config OpenClaw actually parsed:**
   ```bash
   openclaw config schema          # does this build accept these fields?
   openclaw agents list --bindings # is `robot` bound to your Telegram account?
   ```
   Fields most likely to differ between builds, and worth checking here:
   `agents.entries.<id>.sandbox.mode`'s value vocabulary, the tool-id spelling
   in `tools.allow` (`mcp__robot__*`), and `bindings[].match`'s keys. The
   fragment deliberately omits `model`, so the agent inherits your global
   default; set it in the entry if you want a specific one.

   **Then check the agent can still *answer*.** `tools.allow: ["mcp__robot__*"]`
   is written as if it scopes only MCP tools. If your build treats `allow` as a
   strict allowlist over *all* tools, and replying to Telegram goes through a
   tool in another namespace, the agent will drive the robot and be unable to
   say a word back — which is half of what this milestone is for. Say hello to
   it before asking for a chore; if it does not reply, **`tools.allow` is the
   first thing to relax** (widen it or drop it entirely and rely on the MCP
   server's own `toolFilter`).
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
  server needs, exposes exactly the tools that exist, and holds no credential.
- **Not tested, anywhere:** that OpenClaw accepts the fragment, that the SSH
  leg works from the Pi, that any of the three hard-coded paths in step 3 are
  right for your machines, and that an LLM given this prompt actually clears
  the table. Nothing in this repo has ever run against a real Pi; the first
  three are steps 3/4/6 above. For the third, the closest
  automated stand-in is
  `src/robot_mcp/test/test_clear_the_table.py`, which drives the whole chore
  over real MCP calls with a deterministic driver in place of the model — it
  proves the loop *can* be closed from the payloads alone, not that the model
  closes it.
