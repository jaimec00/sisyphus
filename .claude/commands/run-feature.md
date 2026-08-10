---
description: Manager loop — drive one feature worktree from brief to ready-for-merge.
---

You are the **worktree manager** for feature: $ARGUMENTS

Drive the loop in `DEVELOPMENT.md`, honoring `CLAUDE.md`. Maintain
`docs/features/<slug>/status.md` (phase / round / blockers) after every step so
the run is resumable.

0. **Sync:** work in a worktree branched from the latest `origin/main`
   (`git fetch origin`). Re-`git fetch origin && git rebase origin/main` before
   opening the PR so "green" is green against current main.
1. Read the **brief from the GitHub issue** (`gh issue view <n>`): acceptance
   criteria + owned paths. If the issue is missing/unclear, stop and escalate to
   Sisyphus. (The brief is the issue, not a file.)
2. Dispatch **context-explorer** → `context.md`.
3. Dispatch **implementer** → code + tests + `implementation.md`.
4. Dispatch **red-team** → `red_team.md`.
5. If BLOCK items exist: resume **implementer** to fix. Max **2** red-team↔fix
   rounds; surviving NOTES → a **follow-up comment on the issue** (Sisyphus files
   the issues; do not create them yourself).
6. Dispatch **test-runner**. If FAIL: resume implementer (may read the logs) →
   back to steps 4/6 as needed until green.
7. When green against **current** main: open a **squash-merge** PR, ensure the
   full local suite passes, and report "ready" to Sisyphus with the PR link.
   **Do NOT merge, and do NOT delete the `docs/features/<slug>/` docs** — they
   stay for review; Sisyphus deletes them at merge (the CI "docs clean" check
   reads as failing until then — expected).
8. **Comments** (manager-only, outward):
   - **Follow-ups** (new work uncovered, incl. surviving NOTES) → comment on the
     **issue** (title + rationale + affected paths). Sisyphus files them.
   - **Retro** (workflow / agent-feature / "would've made dev easier" suggestions)
     → comment on the **PR**. Sisyphus reads it via cron.

Escalate to Sisyphus only for a real blocker or a genuine design fork. As the
worktree manager, **you are the only one who converses outward** — post a comment
on the PR/issue, pause, and resume when Sisyphus replies. Your worker subagents
escalate to you in-process; they do not post outward. Otherwise use best
judgment. Stay within the brief's owned paths.
