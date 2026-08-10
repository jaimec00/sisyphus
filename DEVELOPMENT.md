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
execution host. Sisyphus (Pi) coordinates via **GitHub** (issues/PRs) plus the
git-tracked `docs/features/<slug>/` files. GitHub is both the state substrate
and the trigger.

## The loop (per feature)
1. **Brief** — Sisyphus opens a **brief PR** (self-merged, no full loop) adding
   `docs/features/<slug>/brief.md` (goal, owned paths, acceptance criteria,
   required tests), plus a labeled GitHub issue.
2. **Trigger** — a self-hosted GitHub Actions runner (or a small dev-runner) on
   the laptop starts a Claude Code manager in a fresh worktree
   (`git worktree add worktrees/<slug> -b feat/<slug>`).
3. **Context** — `context-explorer` explores the repo → `context.md`.
4. **Implement** — `implementer` builds the feature + tests, commits in small
   increments, writes `implementation.md`.
5. **Red-team** — `red-team` (read-only) reviews source + tests vs. acceptance
   criteria → `red_team.md` (severity rubric).
6. **Fix** — `implementer` addresses BLOCK items (≤2 rounds; surviving NOTES
   become follow-up issues).
7. **Test** — `test-runner` runs the suite, reporting pass/fail + log path only.
   Loop 4–7 until green.
8. **PR** — the manager opens a **squash-merge** PR; light CI (lint) + the full
   local suite must pass.
9. **Merge** — when the PR is green (tests + red-team + CI), Sisyphus picks order
   and squash-merges (no manual approval gate — green is the gate). Other open
   worktrees then rebase on main, re-green, and take their turn.

`status.md` tracks phase/round/blockers so any agent (or a restart) resumes
deterministically.

## Change management & staying current
Every change to `main` — features **and** Sisyphus's briefs/docs/ops — goes
through a PR; no direct pushes. No manual approval gate; green checks are the
gate. Operational/meta PRs are self-merged by Sisyphus without the full loop.
Because main moves, agents `fetch`+`rebase origin/main` before work and after any
merge — never build on stale main.

## Escalation channel
Workers escalate in-process to their worktree manager. Only the **manager**
converses outward: it comments on its PR/issue and pauses; Sisyphus (polling PR
status via a cron) replies by comment (relaying genuine design forks to Jaime);
the manager resumes on the reply.

## Parallelism
Sisyphus decomposes along package seams (`robot_brain` / `robot_skills` / ...).
Each worktree declares **owned paths** in its brief. Merges are serialized; the
integration/nightly suite is the final gate.

## Logs
Run/test logs go to gitignored `.dev/runs/<slug>/<ts>/`, kept until the PR
merges, then pruned. The nightly job cleans stragglers.

## Automated checks
- **Laptop nightly cron:** runs the full suite on `main`, reports regressions.
- **Sisyphus (Pi) cron:** polls open PR statuses + manager escalation comments,
  squash-merges green PRs, and answers escalations.

## Budgets & safety
Per-feature agent/token caps. Merges require green checks (tests + red-team + CI);
Sisyphus merges — no manual approval gate. No force-push to main; no destructive
actions without Jaime.
