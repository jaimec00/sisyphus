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
1. Read the **brief from the GitHub issue**: acceptance criteria + owned paths.
   Use the JSON form: `gh issue view <n> --json number,title,body,labels,state`.
   The bare `gh issue view <n>` fails in this repo on the Projects-classic
   deprecation GraphQL error, so always pin `--json` fields. Treat an empty or
   missing body as a **hard stop**, not a silent pass. If the issue is
   missing/unclear, stop and escalate to Sisyphus. (The brief is the issue, not a
   file.)
2. Dispatch **context-explorer** → `context.md`.
3. **Rule on open questions:** read the open questions `context.md` leaves and
   decide each one yourself as an explicit **manager ruling**, recorded in
   `status.md` (and/or `context.md`) **before** dispatching the implementer — lock
   the design up front rather than leaving it to be discovered mid-implementation.
   Rulings cover in-scope questions only; a genuine **design fork** still
   escalates to Sisyphus per the escalation rule below.
4. Dispatch **implementer** → code + tests + `implementation.md`.
5. Dispatch **red-team** → `red_team.md`.
6. If BLOCK items exist: resume **implementer** to fix. Max **2** red-team↔fix
   rounds; surviving NOTES → a **follow-up comment on the issue** (Sisyphus files
   the issues; do not create them yourself).
7. Dispatch **test-runner**. If FAIL: resume implementer (may read the logs) →
   back to steps 5/7 as needed until green.
8. When green against **current** main: open a **squash-merge** PR, ensure the
   full local suite passes, and report "ready" to Sisyphus with the PR link.
   **Do NOT merge, and do NOT delete the `docs/features/<slug>/` docs** — they
   stay for review; Sisyphus deletes them at merge (the CI "docs clean" check
   reads as failing until then — expected).
9. **Comments** (manager-only, outward):
   - **Follow-ups** (new work uncovered, incl. surviving NOTES) → comment on the
     **issue** (title + rationale + affected paths). Sisyphus files them.
   - **Retro** (workflow / agent-feature / "would've made dev easier" suggestions)
     → comment on the **PR**. Sisyphus reads it via cron.

Escalate to Sisyphus only for a real blocker or a genuine design fork. As the
worktree manager, **you are the only one who converses outward** — post a comment
on the PR/issue, pause, and resume when Sisyphus replies. Your worker subagents
escalate to you in-process; they do not post outward. Otherwise use best
judgment. Stay within the brief's owned paths.
