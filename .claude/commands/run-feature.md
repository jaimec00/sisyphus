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
2. **Provision new dependencies first, then probe the real API.** If the feature
   adds a **new third-party dependency**, install it in the worktree now
   (`pixi add …`) and probe the **installed** package by *executing* against it —
   `python -c` imports, `inspect.signature`, `dir()`, reading the installed
   source. Do this **before** step 3 and before any ruling. Every ruling that
   touches that dependency must quote **execute-verified** signatures and
   behavior; never training-recalled API. (In #37 `mcp` turned out to be a 2.x
   rewrite — `mcp.server.fastmcp` and `mcp.types` are gone — and every agent's
   recalled API was wrong; only empirical probing caught it.)
3. Dispatch **context-explorer** → `context.md`. **Do not mutate the worktree
   while it is reading** — no `pixi add`, no installs, no edits. A concurrent
   `pixi add` in #37 made the explorer report a bogus `pixi.toml`/`pixi.lock`
   mismatch and a missing `.pixi/` env. Sequence provisioning (step 2) and
   exploration; never overlap them.
4. **Rule on open questions:** read the open questions `context.md` leaves and
   decide each one yourself as an explicit **manager ruling**, recorded in
   `status.md` (and/or `context.md`) **before** dispatching the implementer — lock
   the design up front rather than leaving it to be discovered mid-implementation.
   Rulings are **binding but not assumed correct**: a downstream agent that
   believes a ruling is wrong **escalates to you in-process** — it must neither
   silently deviate nor comply into a bug. (#37's ruling R5 prescribed a dict
   merge whose order let a caller-supplied `skill` argument override the tool
   name — a confused-deputy bug, authored by the manager, that the implementer
   caught and escalated.) Rulings cover in-scope questions only; a genuine
   **design fork** still escalates to Sisyphus per the escalation rule below.
5. Dispatch **implementer** → code + tests + `implementation.md`.
6. Dispatch **red-team** → `red_team.md`. Prompt it with **where to look
   hardest**: restate each of your own rulings and judgment calls as a
   **falsifiable claim** and tell it to **disprove that claim empirically — run
   the code, don't reason about it** ("R5 claims the boot-smoke fails when a
   package is dropped from discovery: sabotage discovery and show me the run";
   "attack R5 — does that merge order let a caller override the tool name?").
   A ruling nobody tried to break is still an assumption, and a generic "review
   this" buys a generic review.
7. If BLOCK items exist: resume **implementer** to fix — then **red-team the fix
   itself**, a second pass scoped to just the fix diff. A fix is new logic the
   round-1 review never saw, and it is written under time pressure by the agent
   that got it wrong the first time. Hand that pass the heuristic that keeps
   paying: *a fix that corrects a claim in N places is a strong prior the same
   claim is wrong in an N+1th* — its job is to find the N+1th. **The scoped
   pass is part of that round, not another one.** Max **2** red-team↔fix
   rounds; surviving NOTES → a **follow-up comment on the issue** (Sisyphus
   files the issues; do not create them yourself).
8. Dispatch **test-runner**. If FAIL: resume implementer (may read the logs) →
   back to steps 6/8 as needed until green.
9. When green against **current** main: **re-read the durable decision against
   the final diff** — if the feature adds or amends a `docs/design/decisions.md`
   entry, read that entry once more against the diff as it actually landed, not
   as it was planned. The decision log is the least-reviewed, longest-half-life
   artifact in the repo: in #55 a correction was made in the ephemeral
   `docs/features/<slug>/` copy and D23 kept the sentence it corrected. Then
   open a **squash-merge** PR, ensure the full local suite passes, and report
   "ready" to Sisyphus with the PR link.
   **Do NOT merge, and do NOT delete the `docs/features/<slug>/` docs** — they
   stay for review; Sisyphus deletes them at merge (the CI "docs clean" check
   reads as failing until then — expected).
10. **Comments** (manager-only, outward):
    - **Follow-ups** (new work uncovered, incl. surviving NOTES) → comment on the
      **issue** (title + rationale + affected paths). Sisyphus files them.
    - **Retro** (workflow / agent-feature / "would've made dev easier" suggestions)
      → comment on the **PR**. Sisyphus reads it via cron.

**A stopped worker is resumed, not respawned.** If a worker subagent is
terminated mid-run, recover it from its transcript (`claude --resume
<session-id>`) instead of dispatching a fresh one — a fresh worker re-derives the
design and may re-litigate settled rulings. Before judging what is missing,
inspect the on-disk state it left (commits **and** untracked files) and
build/source the env before running its tests.

Escalate to Sisyphus only for a real blocker or a genuine design fork. As the
worktree manager, **you are the only one who converses outward** — post a comment
on the PR/issue, pause, and resume when Sisyphus replies. Your worker subagents
escalate to you in-process; they do not post outward. Otherwise use best
judgment. Stay within the brief's owned paths.
