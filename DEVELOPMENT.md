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
1. **Brief** — Sisyphus writes `docs/features/<slug>/brief.md` (goal, owned
   paths, acceptance criteria, required tests) and opens a labeled GitHub issue.
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
9. **Merge** — Sisyphus reviews readiness, picks order, squash-merges. Other
   open worktrees rebase on main, re-green, then take their turn.

`status.md` tracks phase/round/blockers so any agent (or a restart) resumes
deterministically.

## Parallelism
Sisyphus decomposes along package seams (`robot_brain` / `robot_skills` / ...).
Each worktree declares **owned paths** in its brief. Merges are serialized; the
integration/nightly suite is the final gate.

## Logs
Run/test logs go to gitignored `.dev/runs/<slug>/<ts>/`, kept until the PR
merges, then pruned. The nightly job cleans stragglers.

## Nightly
A cron job on the laptop runs the full suite on `main` and reports regressions.

## Budgets & safety
Per-feature agent/token caps. Merges to main are gated (ready + green + Sisyphus
approval). No force-push to main; no destructive actions without Jaime.
