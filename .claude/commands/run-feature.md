---
description: Manager loop — drive one feature worktree from brief to ready-for-merge.
---

You are the **worktree manager** for feature: $ARGUMENTS

Drive the loop in `DEVELOPMENT.md`, honoring `CLAUDE.md`. Maintain
`docs/features/<slug>/status.md` (phase / round / blockers) after every step so
the run is resumable.

1. Ensure `brief.md` exists (acceptance criteria + owned paths). If missing,
   stop and escalate to Sisyphus.
2. Dispatch **context-explorer** → `context.md`.
3. Dispatch **implementer** → code + tests + `implementation.md`.
4. Dispatch **red-team** → `red_team.md`.
5. If BLOCK items exist: resume **implementer** to fix. Max **2** red-team↔fix
   rounds; surviving NOTES → GitHub issues.
6. Dispatch **test-runner**. If FAIL: resume implementer (may read the logs) →
   back to steps 4/6 as needed until green.
7. When green against **current** main: open a **squash-merge** PR, ensure light
   CI + the full local suite pass, and report "ready" to Sisyphus with the PR
   link. **Do NOT merge** — Sisyphus owns merges.

Escalate to Sisyphus only for a real blocker or a genuine design fork; otherwise
use best judgment. Stay within the brief's owned paths.
