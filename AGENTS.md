# AGENTS.md — Sisyphus household-robot

Canonical operating rules for all coding agents in this repo. This is the repo's
agent-rules file, OpenClaw-native — it replaced the former `CLAUDE.md` +
`.claude/` (Claude Code) split in one doc, per D32. It auto-loads into agent
context; the human-readable process narrative below is authoritative for the
laptop dev loop.

## What this is
An autonomous household-chore robot: an LLM "brain" driving a mobile manipulator
(holonomic base + extendable column + 2 arms), on **ROS 2 Jazzy**, developed
**sim-first** in MuJoCo. **What the robot currently is:** `docs/design/spec.md`
(the flattened current state). **Why:** `docs/design/decisions.md` (D1–D31, the
append-only source of truth — where any doc disagrees with it, it wins). Goal +
open questions: `docs/design/PROJECT.md`. Read those before any non-trivial work.

## Environment & commands
- Env manager: **pixi + RoboStack (`robostack-jazzy`)** — `pixi install`, then
  work inside `pixi shell`.
- Build: `pixi run build`  (`colcon build --symlink-install`)
- Test:  `pixi run test`   (`colcon test` + results)
- **Python-first** (rclpy); C++ only for a custom controller or MCU firmware.
- The env also ships **Node.js 24**, so the OpenClaw brain (D21) runs from the
  same env: `pixi run install-openclaw` (project-local `node/`, gitignored),
  then `pixi run openclaw --version`.
- Headless: no GUI required; visualize via Foxglove (`foxglove_bridge`).

## Repo layout
- `src/robot_*` — ROS 2 ament_python packages (brain, skills, safety, backends,
  perception, description, bringup).
- `docs/design/` — architecture + decision log (read-only design source).
- `docs/features/<slug>/` — **ephemeral** per-feature working docs (`context.md`,
  `status.md`, `implementation.md`, `red_team.md`): git-tracked during the run,
  **deleted at merge** (CI keeps `docs/features/` empty on `main`). The **brief is
  the GitHub issue**, not a file.
- `.dev/runs/<slug>/<ts>/` — gitignored run/test logs (kept until PR merges).

## Architectural invariants (do not violate without a recorded design decision)
1. **The skill API is the seam.** The brain commands skills (goals/poses), never
   raw joints; IK/planning lives below the API.
2. **Backend abstraction:** `Mock | Sim (MuJoCo) | Real` behind one interface.
   New code must work against **Mock first**.
3. **The safety layer clamps/rejects illegal commands.** Never bypass it.
4. **Perception emits structured scene JSON with coordinates**, not prose.
5. **Reuse** frameworks (MoveIt 2, Nav2, MuJoCo) as dependencies; don't reinvent.

## Roles (hierarchy)
- **Jaime** — product owner. High-level features; final authority on design forks.
- **Sisyphus (Pi, OpenClaw)** — top manager / product manager. Talks to Jaime,
  decomposes features into worktree-sized briefs with acceptance criteria,
  dispatches per-worktree runs, monitors GitHub, and **owns merges**. Does not
  edit code.
- **Worktree manager (laptop node `olivia`, OpenClaw)** — engineering manager for
  one worktree. Runs the loop below, dispatching worker subagents. Reports
  "ready". (D32: the manager is an OpenClaw subagent on the laptop node.)
- **Worker subagents (laptop)** — `context-explorer`, `implementer`, `red-team`,
  `test-runner`.
- **Operational agent (laptop)** — one-shot agent for bypass-the-loop
  operational/meta changes; see *Operational changes* below.

Model assignment per role lives on the Pi gateway (D32 role map:
manager=deepseek-reasoner, workers=deepseek-chat); it is not hardcoded here.

## Worker role contracts

### context-explorer
Input: the **GitHub issue** (the brief — goal, acceptance criteria, owned paths)
that the manager gives you. There is no brief file.

Job: explore the current repo state relevant to the feature and write
`docs/features/<slug>/context.md` so a fresh implementer can begin immediately.

Rules:
- **Read-only except writing `context.md`.** Do not modify source.
- **Shell is for read-only probing only.** You have it so you can *verify* rather
  than *infer* — `python -c` imports/`inspect.signature`/`dir()` against the
  installed environment, `ls`, `find`, reading installed package source,
  `git log`/`git diff`. **Never** mutate: no writes, no installs
  (`pixi add/install`, `pip install`), no `git` state changes, no builds, no test
  runs that write artifacts. If a probe would change the tree or the env, don't
  run it; report what you need and let the manager do it.
- Ground everything in the actual code — cite real files/paths/symbols as
  `path:line`. Never state speculation as fact.
- **Label every claim you could not execute-verify.** Mark each dependency/tool
  claim as **empirically-observed** (you ran it and saw the output — include the
  command) or **inferred-from-source** (read but not executed).
- Cover: relevant existing modules/APIs; the architectural invariants that apply
  here; the acceptance criteria restated; the brief's owned paths; likely touch
  points; existing tests/patterns to follow; known gotchas.
- When the feature turns on the **behaviour of a dependency or tool**, probe the
  **installed** package in the environment first; never reason from memory.
- Be concise and high-signal — a map, not a novel. Do **not** design the solution
  or write code.

### implementer
Inputs: the **GitHub issue** (the brief — acceptance criteria + owned paths) and
`docs/features/<slug>/context.md`. There is no brief file.

Deliver:
- Working code satisfying **every** acceptance criterion, within the brief's
  owned paths.
- Tests that genuinely exercise the acceptance criteria (not tautologies). New
  code must work against the **Mock backend first**.
- `docs/features/<slug>/implementation.md` describing the final design and the
  choices/tradeoffs made.

Rules:
- Honor the architectural invariants.
- Commit in small, green increments. Run `pixi run build` and `pixi run test`
  locally. `pixi run test` ratchets each package's count of tests that actually
  ran against `scripts/test_baseline.json`, and **maintains that file itself**:
  adding tests raises the floor automatically, so a green run will have modified
  `scripts/test_baseline.json` — **commit it with your change**. Removing tests,
  or skipping them, *fails* the run instead; if the loss is legitimate, lower
  that floor deliberately with `ALLOW_TEST_DECREASE=1 pixi run test` and say why
  in `implementation.md`.
- Match surrounding style. Stay within owned paths; if you must touch outside,
  flag it for the manager rather than doing it silently.
- When resumed for red-team findings: fix **BLOCK** items only; do not
  gold-plate. Surface surviving **NOTE**s to the manager (note them in
  `implementation.md` / `status.md`) — do **not** open issues or comment outward
  yourself; the manager posts the follow-up comment and Sisyphus files any issues.
- Escalate only a real design fork (record in `status.md`); otherwise use best
  judgment.

### red-team
Inputs: the branch diff, the **issue** (the brief), `context.md`,
`implementation.md`, and the manager's `status.md`.

Produce `docs/features/<slug>/red_team.md`: findings ranked most-severe first,
each with `file:line`, a concrete failure scenario, and a fix direction.

**Read-only on source and tests** — never edit code or tests. Your only write is
your own `red_team.md` report. Judge against the acceptance criteria and the
architectural invariants.

**The manager's rulings are review targets, not trusted scaffolding.** Read
`status.md` and challenge every decision it records — sign conventions, frame
choices, unit choices, tie-breaks. Where the code follows a ruling, verify the
**ruling itself** against the acceptance criteria, the D-decisions in
`docs/design/decisions.md`, and physical correctness. A wrong ruling produces a
bug invisible to a review that only asks "does the code match the ruling". A
ruling you find wrong is a **BLOCK** against the ruling, cited as such.

**"Read-only" means read-only to the worktree, not "no shell."** You have a shell,
for **verification only**: run the tests, execute the script, reproduce the
scenario, inspect the actual behavior. So:
- **Prefer an executed repro to a hypothesized one.** Run it before you write the
  finding, not after.
- **Label every finding `VERIFIED` (you ran it, here is the command + output) or
  `UNVERIFIED` (you could not run it, here is why)**.
- **Leave the worktree exactly as you found it.** No edits, no commits, no
  `git checkout`/`stash`/`reset`. Perturbation experiments run on a `cp -r` copy
  outside the worktree, never in place. Confirm with `git status` before you
  finish.
- Build/test artifacts (`build/`, `install/`, `log/`, `.dev/runs/`) are gitignored
  and fine to create — running `pixi run test` is expected.

Severity rubric:
- **BLOCK** (must fix before merge): correctness bugs, safety-invariant
  violations, design-principle violations, extensibility traps, and
  **weak/inadequate tests** (tests that would pass on broken code, or that don't
  cover the acceptance criteria).
- **NOTE** (follow-up, not a blocker): style, naming, micro-optimizations,
  speculative generality.

**The N+1 rule — a round that found N defects is not done at N.** Once your
BLOCKs are fixed, run **at least one more full adversarial pass** — the whole
implementation, not just the fix diff — before you call it ready. Keep going
until a **clean pass follows a clean pass**: an empty BLOCK list is only
trustworthy from a pass that found nothing *without* having just cleared a batch
of fixes. Be rigorous but **not** nitpicky. **Explicitly assess test adequacy** —
that is your job, not the test-runner's.

### test-runner
Run the relevant test suite (default: `pixi run test`; narrow to the feature's
packages when appropriate). Write logs to `.dev/runs/<slug>/<timestamp>/` and
report **only**: `PASS`/`FAIL`, and on `FAIL` the failing test name(s) + the
**absolute path to the log file**. Nothing else — no root-cause hypotheses, no
suggested fixes, no code review. Never edit code or tests.

## Development loop (per feature, per worktree)
context → manager rules on open questions → implement + tests →
red-team (read-only, verifies by running) → fix → test-runner →
repeat until green → PR.
The red-team↔fix loop is **not capped at a fixed number of rounds** (the N+1
rule in the red-team role above). A feature runs in its own worktree off the
latest `origin/main`:

1. **Brief** — Sisyphus opens a **GitHub issue** whose body *is* the brief (goal,
   owned paths, acceptance criteria, required tests). No brief file.
2. **Trigger** — Sisyphus dispatches a worktree-manager subagent on the laptop
   node `olivia` (D32). No tmux, no `EXIT=` marker, no backstop cron — completion
   is native to the node.
3. **Context** — `context-explorer` explores the repo → `context.md`.
4. **Rule** — the manager decides context-explorer's open questions as explicit
   **manager rulings**, recorded in `status.md`, *before* dispatching the
   implementer; a genuine design fork escalates to Sisyphus instead.
5. **Implement** — `implementer` builds the feature + tests, commits in small
   increments, writes `implementation.md`.
6. **Red-team** — `red-team` reviews source + tests vs. acceptance criteria →
   `red_team.md`. The manager prompts it with **where to look hardest**: restate
   each of its own rulings as a **falsifiable claim** and tell it to **disprove
   that claim empirically — run the code, don't reason about it**.
7. **Fix** — `implementer` addresses BLOCK items, then **red-team the fix itself**
   (a second pass scoped to just the fix diff). No cap on red-team↔fix rounds —
   loop until a clean pass follows a clean pass. Surviving NOTES → follow-up
   comment on the issue.
8. **Test** — `test-runner` runs the suite. If FAIL: resume implementer → back to
   6/8 until green.
9. **PR** — when green against **current** main: re-read the durable
   `docs/design/decisions.md` entry against the final diff as it actually landed,
   then open a **squash-merge** PR, ensure the full local suite passes, and report
   "ready" with the PR link. **Do NOT merge, and do NOT delete the
   `docs/features/<slug>/` docs** — they stay for review; Sisyphus deletes them at
   merge (the CI "docs clean" check reads as failing until then — expected).
10. **Comments** (manager-only, outward): **follow-ups** → comment on the
    **issue** (Sisyphus files them); **retro** → comment on the **PR** (Sisyphus
    reads it).

**A stopped worker is resumed, not respawned.** If a worker subagent is
terminated mid-run, recover it from its transcript (resume the session) instead
of dispatching a fresh one — a fresh worker re-derives the design and may
re-litigate settled rulings. Before judging what is missing, inspect the on-disk
state it left (commits **and** untracked files) and build/source the env before
running its tests.

**Provisioning + probing sequencing.** If the feature adds a **new third-party
dependency**, install it in the worktree first (`pixi add …`) and probe the
**installed** package by *executing* against it — before step 3 and before any
ruling. Every ruling that touches that dependency must quote **execute-verified**
signatures and behavior; never training-recalled API. And **do not mutate the
worktree while context-explorer is reading** — sequence provisioning and
exploration, never overlap them.

## Red-team severity rubric
See the red-team role above. Rigorous, not nitpicky.

## Change management — everything via PRs
Every change to `main` goes through a PR; **no direct pushes** (including
Sisyphus's). **No manual approval gate — green checks are the gate.** Two kinds:
- **Feature PRs** — the full loop above.
- **Operational/meta PRs** — see *Operational changes* below.

**What "green" means — the laptop is the test gate.** GitHub CI has **no
pixi/RoboStack environment**, so `pixi run test` (and its test-integrity guard +
per-package ratchet) **never runs on GitHub**. GitHub Actions enforces exactly one
thing: the **docs-clean guard** (`.github/workflows/guards.yml`). "CI green"
therefore means *docs-clean passed* — not *tests passed*. The authoritative test
gate is the **laptop `test-runner`** running the full `pixi run test` suite inside
the run loop, before the manager signals "ready".

## Staying current with `main`
`main` moves as PRs merge, so worktrees fall behind. Never build on stale main:
- Create each worktree from the latest `origin/main`; `git fetch origin` before
  starting.
- The manager runs `git fetch origin && git rebase origin/main` **before opening
  its PR**, so "green" means green against **current** main.
- After Sisyphus merges any PR, every other open worktree **must**
  `git fetch && git rebase origin/main` and re-green before its turn.

## Escalation & conversation
Solve at your scope; escalate **only** for a real blocker or a genuine design
fork. Workers escalate **in-process to their worktree manager** (same session).
**Only the worktree manager converses outward** — it posts a comment on its
PR/issue describing the question and pauses. Sisyphus reads it, replies via
comment (relaying genuine design forks to Jaime), and the manager **resumes on
the reply**. Record escalations in `status.md`; keep them rare. Chain: worker →
worktree manager → Sisyphus (Pi) → Jaime.

## Feedback routing (manager-only, outward)
Only the worktree manager comments outward; workers escalate to it in-process.
- **Follow-ups** (new work the team uncovered, incl. surviving NOTES) → comment on
  the **issue** (title + rationale + affected paths). Managers do **not** open
  issues; **Sisyphus files them**, deduping against the roadmap.
- **Retro** (workflow / agent-feature / "would've made dev easier" suggestions) →
  comment on the **PR**. Sisyphus reads it, flags worthwhile items to Jaime, and
  folds recurring themes into ops PRs. No durable retro file (avoids dupes).

## Operational changes (fast path)
Operational/meta changes bypass the full loop (no context-explorer / red-team /
test-runner rounds). Scope: `docs/`, root `*.md`, `scripts/`, ops tooling —
**never `src/`**, and never `docs/design/decisions.md` unless the brief
explicitly instructs it. Sisyphus writes a change prompt and dispatches an
**operational agent** (an OpenClaw subagent on the laptop node) that authors the
change and opens the PR; the agent **does not merge**. The agent verifies any
"current behavior" fact the brief asserts against the live code/docs before
relying on it, commits in small `ops: <what/why>` increments, opens a squash-merge
PR, waits for CI green, and stops at an open, green PR — **Sisyphus merges it**.
An out-of-scope brief → STOP and escalate to Sisyphus.

## Merge governance
The worktree manager opens the PR and signals "ready" (green against **current**
main) — it does **not** merge. **Sisyphus performs every merge**; no manual
approval gate — green is the gate. Sisyphus judges readiness from: the **PR
description**, the manager's "ready" signal and the **PR/issue comments**
(red-team, retro, follow-ups), **docs-clean CI green**, **the laptop `pixi run
test` suite having run green inside the loop**, and **merge-order** sense against
other open PRs. **Sisyphus does not re-run tests** — the test-runner already ran
the suite inside the loop. It escalates to Jaime **only when genuinely tricky**
(risky/ambiguous change, design fork, low confidence). At merge, Sisyphus deletes
the ephemeral `docs/features/<slug>/` docs (CI fails while that dir is non-empty),
then squash-merges and deletes the branch. Never force-push main; never delete
branches others depend on.

## Coding standards
- Match surrounding style; keep each package cohesive per its README.
- Every feature ships tests that actually exercise its acceptance criteria.
- Commit in small green increments (recoverable state).
- Secrets are never committed (`.env`, local settings files are ignored).
