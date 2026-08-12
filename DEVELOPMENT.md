# DEVELOPMENT.md — how we build Sisyphus

Human-readable process narrative. For agent-behavior rules, **`CLAUDE.md` is
canonical**; this document defers to it.

## Roles (hierarchy)
- **Jaime** — product owner. High-level features; final authority on design forks.
- **Sisyphus (Pi, OpenClaw)** — top manager / product manager. Talks to Jaime,
  decomposes features into worktree-sized briefs with acceptance criteria,
  dispatches per-worktree runs, monitors GitHub, and **owns merges**. Does not
  edit code.
- **Worktree manager (laptop, Claude Code)** — engineering manager for one
  worktree. Runs the loop below, dispatching worker subagents. Reports "ready".
- **Worker subagents (laptop)** — `context-explorer` (sonnet),
  `implementer` (opus), `red-team` (opus, read-only), `test-runner` (sonnet).
- **Operational agent (laptop, Claude Code)** — one-shot agent for
  bypass-the-loop operational/meta changes (docs, `.claude`, scripts). Sisyphus
  hands it a change prompt; it authors the change and opens the PR, stopping at
  an open, CI-green PR — **Sisyphus merges it**. Dispatched via
  `scripts/start-op.sh`; loop in `.claude/commands/run-op.md`.

## Where it runs
Code, git, pixi env, and tests live on the **laptop**; **Claude Code** is the
execution host. Sisyphus (Pi) coordinates via **GitHub** (issues/PRs/comments).
The **brief is the issue**; per-feature `docs/features/<slug>/` working docs are
ephemeral (git-tracked during the run, deleted at merge). GitHub is both the
state substrate and the trigger.

### OpenClaw in the pixi env
The pixi env ships **Node.js 24** (`nodejs = ">=24.15,<25"` in `pixi.toml`), so the
OpenClaw brain (D21) can be installed and run from the same env as the robot
stack rather than depending on a host Node. The 24.x pin is deliberate: it is the
line the Pi already runs OpenClaw on, and openclaw's own `engines` field is
`">=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0"`, so a looser `">=22"` could legally
resolve to a Node (23.x, 25.0–25.8) that openclaw refuses to start on. Node
24.19.0 resolves on both `linux-64` (laptop) and `linux-aarch64` (Pi).

```
pixi run install-openclaw     # npm install into ./node (gitignored), never -g
pixi run openclaw --version   # runs the project-local binary; args are forwarded
```

`install-openclaw` runs `scripts/install_openclaw.sh`, which installs into a
project-local npm prefix (`node/`) using the pixi-provided Node/npm. Nothing is
written outside the repo except npm's own download cache, and `node/` is
gitignored so the ~300-package dependency tree never enters git. The `openclaw`
task depends on `install-openclaw`, so the first run installs and later runs are
a no-op refresh.

**`install-openclaw` is a prerequisite for `pixi run test`, not just for running
the CLI by hand.** `src/robot_brain/test/test_openclaw_validates.py` puts the
shipped `openclaw.robot.json` in front of the real `openclaw config validate`,
and it **hard-fails** — no skip — when the binary is missing: a schema-drift
guard that silently turns itself off is the failure mode it exists to end.
`scripts/start-feature.sh` therefore runs `install-openclaw` in every new
worktree's bootstrap, and `node/` is per-worktree (gitignored), so nothing
carries over from a sibling. If `robot_brain` goes red with *"no OpenClaw CLI
at …"*, the remedy is that one command.

Useful for checking config work against the real thing rather than against docs:

```
OPENCLAW_CONFIG_PATH=<file> pixi run openclaw config validate
pixi run openclaw config schema        # full JSON schema for openclaw.json
pixi run openclaw doctor               # what the schema cannot catch: globs,
                                       # tool policy, sandbox gates
```

## The loop (per feature)
1. **Brief** — Sisyphus opens a **GitHub issue** whose body *is* the brief (goal,
   owned paths, acceptance criteria, required tests). No brief file.
2. **Trigger** — **pull-based**: Sisyphus (Pi) runs
   `scripts/start-feature.sh <issue>` over SSH, which creates a fresh worktree off
   the latest `origin/main` (`feat/i<n>-<slug>`) and launches a detached Claude
   Code manager in `tmux`. There is **no backfill cron / Actions-runner
   trigger** — work is only ever started by an explicit call to the script.
3. **Context** — `context-explorer` explores the repo → `context.md`.
4. **Rule** — the manager decides context-explorer's open questions as explicit
   **manager rulings**, recorded in `status.md`, *before* dispatching the
   implementer; a genuine design fork escalates to Sisyphus instead.
5. **Implement** — `implementer` builds the feature + tests, commits in small
   increments, writes `implementation.md`.
6. **Red-team** — `red-team` (read-only) reviews source + tests vs. acceptance
   criteria → `red_team.md` (severity rubric).
7. **Fix** — `implementer` addresses BLOCK items (≤2 rounds; surviving NOTES →
   follow-up **comment on the issue**; Sisyphus files them).
8. **Test** — `test-runner` runs the suite, reporting pass/fail + log path only.
   Loop 5–8 until green.
9. **PR** — the manager opens a **squash-merge** PR (full local suite passes;
   light GitHub CI runs guards). The `docs/features/<slug>/` docs stay for review;
   the manager posts a **retro comment on the PR**.
10. **Merge** — when green, Sisyphus **deletes the ephemeral `docs/features/<slug>/`
    docs** (the CI "docs clean" gate then passes), picks order, and squash-merges
    (no manual approval gate — green is the gate, meaning docs-clean CI **plus** the laptop suite having run green in the loop; Sisyphus merges on its own judgment — from the PR description, comments, and those checks, not by re-running tests — and asks Jaime only when a merge is genuinely tricky). Other open worktrees then rebase
    on main, re-green, and take their turn.

`status.md` tracks phase/round/blockers so any agent (or a restart) resumes
deterministically.

## Change management & staying current
Every change to `main` — features **and** Sisyphus's briefs/docs/ops — goes
through a PR; no direct pushes. No manual approval gate; green checks are the
gate. Operational/meta PRs bypass the full loop but are **not** authored by
Sisyphus directly: Sisyphus writes a change prompt and dispatches an **operational
agent** (`scripts/start-op.sh`, the `/run-op` loop) that authors the change and opens
the PR (`docs`/`.claude`/`scripts` scope only, never `src/`); **Sisyphus merges it**,
as it does every PR.
Because main moves, agents `fetch`+`rebase origin/main` before work and after any
merge — never build on stale main.

## Comments & escalation (manager-only, outward)
Workers escalate in-process to their worktree manager. Only the **manager**
comments outward, three purposes:
- **Escalation** (mid-run blocker / design fork) → comment + pause; Sisyphus
  (polling via cron) replies; the manager resumes on the reply.
- **Follow-ups** (new work) → comment on the **issue**; Sisyphus files them.
- **Retro** (dev-experience / workflow suggestions) → comment on the **PR**.

## Parallelism
Sisyphus decomposes along package seams (`robot_brain` / `robot_skills` / ...).
Each worktree declares **owned paths** in its brief. Merges are serialized; the
integration/nightly suite is the final gate.

## Logs
Run/test logs go to gitignored `.dev/runs/<slug>/<ts>/`, kept until the PR
merges, then pruned. The nightly job cleans stragglers.

## Automated checks
- **GitHub CI (PRs):** one light guard — fails if `docs/features/` is non-empty
  (ephemeral docs must be deleted at merge). CI has **no pixi/RoboStack env**, so
  `pixi run test` never runs on GitHub; "CI green" means *docs-clean passed*,
  nothing more. Provisioning a CI pixi env was **explicitly declined** (Jaime,
  2026-08-11) in favor of the laptop-as-gate model below — cheaper, and it matches
  where the code, env, and tests live. Revisit only if the trust model (#16)
  demands a GitHub-side gate.
- **The test gate is the laptop.** The authoritative gate is `test-runner` running
  the full `pixi run test` suite — including the test-integrity guard and
  per-package ratchet below — inside the run loop, before the manager signals
  "ready".
- **Test-integrity guard (`pixi run test`):** refuses to call a hollow run
  green — a package with no result file, zero collected tests, or an
  all-skipped suite fails. It also **ratchets**: `scripts/test_baseline.json`
  records each package's non-linter test count, and dropping below it fails,
  as does a package that grows implementation code while its only tests are
  ament linters. When tests are legitimately added or removed, re-cut the
  floor and commit it in the same PR:
  `pixi run python scripts/check_test_integrity.py --update-baseline`.
- **Laptop nightly cron:** runs the full suite on `main`, reports regressions.
- **Sisyphus (Pi) cron:** polls open PR statuses + manager comments (escalations,
  follow-ups, retros), squash-merges green PRs (escalating to Jaime only when a merge is genuinely tricky), answers escalations.

## Dispatch, push-notify, and merge flow
How a run actually starts and how Sisyphus learns it finished.

- **Dispatch (pull-started).** Runs begin from the Pi via
  `scripts/pi/dispatch.sh feature <issue> [slug]` or
  `scripts/pi/dispatch.sh op <slug> (-f <brief-file> | "<prompt>")`. It SSHes to the
  laptop launcher (`scripts/start-feature.sh` / `scripts/start-op.sh`), which starts the
  manager or operational agent detached in tmux, and additionally spawns the Pi-side
  push watcher for that run. `DRY_RUN=1` prints the plan and starts nothing (no watcher).
- **Push notifier.** `scripts/pi/watch-run.sh` waits for the run's `EXIT=<code>` marker
  in the run log — **not** tmux-session death, because the launcher leaves an idle
  `exec bash` shell behind, so the session outlives the run. On the marker it fires
  **one** wake to Sisyphus on the local Gateway, delivered via the `sisyphus` Telegram
  account. Latency ~15s. Hard kills (OOM, `kill -9`, reboot) never write the marker, so
  the watcher falls back to session-gone and wakes anyway.
- **Backstop cron.** `sisyphus-pr-check-backstop` sweeps every 15 min. It is a pure
  safety net for anything the push missed (Pi reboot, dead watcher, dropped SSH) — not
  the primary path.
- **Merge authority.** Sisyphus performs **every** merge. The cron never merges (it only
  wakes Sisyphus); subagents and worktree managers never merge (they stop at an open,
  green PR). Both the watcher and the cron reference
  `.claude/commands/run-merge-eval.md` — the single canonical merge policy — instead of
  embedding their own copy.
- **Routing contract.** Any wake to Sisyphus **must** pin `--reply-account sisyphus`.
  Without it, delivery falls back to the default account, which is a different bot.

## Budgets & safety
Per-feature agent/token caps. Merges require green checks — the laptop test suite
and red-team inside the loop, plus docs-clean CI on GitHub (see *Automated
checks*); Sisyphus merges — no manual approval gate. No force-push to main; no destructive
actions without Jaime.
