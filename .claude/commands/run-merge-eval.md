# run-merge-eval — Sisyphus merge-evaluation policy (canonical)

Single source of truth for how Sisyphus evaluates a finished worktree run (feature or
op) for merge. Both triggers reference THIS file instead of embedding the policy:
- the push watcher (`scripts/pi/watch-run.sh`) fires on run-end and points here;
- the backstop cron (`sisyphus-pr-check-backstop`) points here on its 15-min sweep.

## Authority
Jaime has delegated the merge decision to Sisyphus. **Sisyphus performs every merge.**
The cron does not merge — it only wakes Sisyphus. Subagents / worktree managers do not
merge — they stop at an open, CI-green PR. Operational agents (`ops/op-*`) no longer
self-merge; Sisyphus merges those too.

## How to judge a PR for merge
1. **Description** — what it claims to do.
2. **PR/issue comments** — the manager's "ready" signal, red-team, retro, follow-ups
   (feature runs). For an `ops/op-*` PR the ready signal is simply GREEN CI + in scope.
3. **CI is GREEN** — note this is the **docs-clean guard only**. GitHub has no
   pixi/RoboStack env, so `pixi run test` never runs there.
4. **The laptop suite ran green inside the loop** — that is the real test gate
   (feature runs); the manager's "ready" signal attests to it.
5. **Merge-order** sense against any other open PRs.

Do NOT re-run tests — the test-runner already ran the suite inside the loop.

## Operational scope (`ops/op-*` PRs)
In scope: `docs/`, root `*.md`, `.claude/`, `scripts/`. **Never `src/`.** Merge when
green + in-scope; otherwise escalate.

## When ready
Merge it yourself: delete the ephemeral `docs/features/<slug>/` dir if present so the
docs-clean guard passes, then squash-merge and delete the branch. Then message Jaime a
brief note of what merged.

## When to escalate instead of merging
If genuinely tricky — a risky or ambiguous change, a design fork, or low confidence —
post your assessment + recommendation on the PR and message Jaime; do not merge. If the
run produced no PR, or the PR is not actually ready (CI pending/failing, draft, manager
not done), do not force it.

## Silence contract (cron/backstop sweeps only)
If after evaluating there is nothing to deliver (nothing merged, no escalation, nothing
noteworthy), the entire response must be exactly the token `NO_REPLY` and nothing else —
the delivery layer suppresses it. Never send "no changes" prose.
