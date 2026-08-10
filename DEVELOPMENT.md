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

## Where it runs
Code, git, pixi env, and tests live on the **laptop**; **Claude Code** is the
execution host. Sisyphus (Pi) coordinates via **GitHub** (issues/PRs/comments).
The **brief is the issue**; per-feature `docs/features/<slug>/` working docs are
ephemeral (git-tracked during the run, deleted at merge). GitHub is both the
state substrate and the trigger.

## The loop (per feature)
1. **Brief** — Sisyphus opens a **GitHub issue** whose body *is* the brief (goal,
   owned paths, acceptance criteria, required tests). No brief file.
2. **Trigger** — **pull-based**: Sisyphus (Pi) runs
   `scripts/start-feature.sh <issue>` over SSH, which creates a fresh worktree off
   the latest `origin/main` (`feat/i<n>-<slug>`) and launches a detached Claude
   Code manager in `tmux`. There is **no backfill cron / Actions-runner
   trigger** — work is only ever started by an explicit call to the script.
3. **Context** — `context-explorer` explores the repo → `context.md`.
4. **Implement** — `implementer` builds the feature + tests, commits in small
   increments, writes `implementation.md`.
5. **Red-team** — `red-team` (read-only) reviews source + tests vs. acceptance
   criteria → `red_team.md` (severity rubric).
6. **Fix** — `implementer` addresses BLOCK items (≤2 rounds; surviving NOTES →
   follow-up **comment on the issue**; Sisyphus files them).
7. **Test** — `test-runner` runs the suite, reporting pass/fail + log path only.
   Loop 4–7 until green.
8. **PR** — the manager opens a **squash-merge** PR (full local suite passes;
   light GitHub CI runs guards). The `docs/features/<slug>/` docs stay for review;
   the manager posts a **retro comment on the PR**.
9. **Merge** — when green, Sisyphus **deletes the ephemeral `docs/features/<slug>/`
   docs** (the CI "docs clean" gate then passes), picks order, and squash-merges
   (no manual approval gate — green is the gate; Sisyphus merges on its own judgment and asks Jaime only when a merge is genuinely tricky). Other open worktrees then rebase
   on main, re-green, and take their turn.

`status.md` tracks phase/round/blockers so any agent (or a restart) resumes
deterministically.

## Change management & staying current
Every change to `main` — features **and** Sisyphus's briefs/docs/ops — goes
through a PR; no direct pushes. No manual approval gate; green checks are the
gate. Operational/meta PRs are self-merged by Sisyphus without the full loop.
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
- **GitHub CI (PRs):** light guards — fails if `docs/features/` is non-empty
  (ephemeral docs must be deleted at merge). Heavy tests run on the laptop.
- **Laptop nightly cron:** runs the full suite on `main`, reports regressions.
- **Sisyphus (Pi) cron:** polls open PR statuses + manager comments (escalations,
  follow-ups, retros), squash-merges green PRs (escalating to Jaime only when a merge is genuinely tricky), answers escalations.

## Budgets & safety
Per-feature agent/token caps. Merges require green checks (tests + red-team + CI);
Sisyphus merges — no manual approval gate. No force-push to main; no destructive
actions without Jaime.
