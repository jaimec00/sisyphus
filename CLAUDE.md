# CLAUDE.md — Sisyphus household-robot

Canonical operating rules for all coding agents in this repo. Auto-loaded into
every agent's context. The process narrative lives in `DEVELOPMENT.md`; **this
file is the source of truth for agent behavior** — `DEVELOPMENT.md` defers to it.

## What this is
An autonomous household-chore robot: an LLM "brain" driving a mobile manipulator
(4-wheel base + extendable column + 2 arms), on **ROS 2 Jazzy**, developed
**sim-first** in MuJoCo. Full architecture + rationale:
`docs/design/PROJECT.md` and `docs/design/decisions.md` (D1–D16). Read those
before any non-trivial work.

## Environment & commands
- Env manager: **pixi + RoboStack (`robostack-jazzy`)** — `pixi install`, then
  work inside `pixi shell`.
- Build: `pixi run build`  (`colcon build --symlink-install`)
- Test:  `pixi run test`   (`colcon test` + results)
- **Python-first** (rclpy); C++ only for a custom controller or MCU firmware.
- The env also ships **Node.js 24**, so the OpenClaw brain (D21) runs from the
  same env: `pixi run install-openclaw` (project-local `node/`, gitignored),
  then `pixi run openclaw --version`. Details in `DEVELOPMENT.md`.
- Headless: no GUI required; visualize via Foxglove (`foxglove_bridge`).

## Repo layout
- `src/robot_*` — ROS 2 ament_python packages (brain, skills, safety, backends,
  perception, description, bringup).
- `docs/design/` — architecture + decision log (read-only design source).
- `docs/features/<slug>/` — **ephemeral** per-feature working docs (`context.md`,
  `status.md`, `implementation.md`, `red_team.md`): git-tracked during the run,
  **deleted at merge** (CI keeps `docs/features/` empty on `main`). The **brief is
  the GitHub issue**, not a file.
- `.claude/agents/`, `.claude/commands/` — agent roles + orchestration.
- `.dev/runs/<slug>/<ts>/` — gitignored run/test logs (kept until PR merges).

## Architectural invariants (do not violate without a recorded design decision)
1. **The skill API is the seam.** The brain commands skills (goals/poses), never
   raw joints; IK/planning lives below the API.
2. **Backend abstraction:** `Mock | Sim (MuJoCo) | Real` behind one interface.
   New code must work against **Mock first**.
3. **The safety layer clamps/rejects illegal commands.** Never bypass it.
4. **Perception emits structured scene JSON with coordinates**, not prose.
5. **Reuse** frameworks (MoveIt 2, Nav2, MuJoCo) as dependencies; don't reinvent.

## Development loop (per feature, per worktree)
context (sonnet) → manager rules on open questions → implement + tests (opus) →
red-team (opus, read-only) → fix (≤2 rounds) → test-runner (sonnet) →
repeat until green → PR.
The red-team is **read-only to the worktree, not shell-less**: it has `Bash` to
*verify* (run tests, reproduce a scenario) and labels each finding VERIFIED or
UNVERIFIED, but never edits source or tests and never leaves the tree dirty.
Full narrative in `DEVELOPMENT.md`; orchestration in `.claude/commands/run-feature.md`.

## Red-team severity rubric
- **BLOCK** (must fix before merge): correctness bugs, safety-invariant
  violations, design-principle violations, extensibility traps,
  weak/inadequate tests.
- **NOTE** (follow-up issue, not a blocker): style, naming, micro-optimizations,
  speculative generality.
Be rigorous, **not** nitpicky. Quality + extensibility is the goal.

## Change management — everything via PRs
Every change to `main` goes through a PR; **no direct pushes to main** (including
Sisyphus's). There is **no manual approval gate** — green checks (tests +
red-team + CI) are the gate; a green PR is mergeable. Two kinds:
- **Feature PRs** run the full loop (context → implement → red-team → test).
- **Operational/meta PRs** — briefs, docs, agent-rule / `.claude` tweaks, ops
  tooling — bypass the full loop, but Sisyphus no longer authors them
  directly. Sisyphus writes a change prompt and dispatches an **operational
  agent** — `scripts/start-op.sh <slug> "<prompt>"`, driving the
  `.claude/commands/run-op.md` loop — which authors the change and opens the PR.
  The operational agent does **not** merge: **Sisyphus squash-merges operational
  PRs** once CI is green, exactly as it does feature PRs. Operational scope only:
  `docs/`, root `*.md`, `.claude/`, `scripts/`, ops tooling — **never `src/`**
  (that is the feature loop).

**What "green" means — the laptop is the test gate.** GitHub CI has **no
pixi/RoboStack environment**, so `pixi run test` (and its test-integrity guard +
per-package ratchet) **never runs on GitHub**. GitHub Actions enforces exactly one
thing: the **docs-clean guard** (`.github/workflows/guards.yml`). "CI green"
therefore means *docs-clean passed* — not *tests passed*. The authoritative test
gate is the **laptop `test-runner`** running the full `pixi run test` suite inside
the run loop, before the manager signals "ready". Giving CI a pixi env was
**explicitly declined** (Jaime, 2026-08-11) in favor of this laptop-as-gate model:
cheaper, and it matches where the code, env, and tests actually live. Revisit only
if the trust model (#16) demands a GitHub-side gate.

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
PR/issue describing the question and pauses. Sisyphus (polling via cron) reads
it, replies via comment (relaying genuine design forks to Jaime), and the
manager **resumes on the reply**. Record escalations in `status.md`; keep them
rare. Chain: worker → worktree manager → Sisyphus (Pi) → Jaime.

## Feedback routing (manager-only, outward)
Only the worktree manager comments outward; workers escalate to it in-process.
- **Follow-ups** (new work the team uncovered, incl. surviving NOTES) → comment on
  the **issue** (title + rationale + affected paths). Managers do **not** open
  issues; **Sisyphus files them**, deduping against the roadmap.
- **Retro** (workflow / agent-feature / "would've made dev easier" suggestions) →
  comment on the **PR**. Sisyphus reads via cron, flags worthwhile items to Jaime,
  and folds recurring themes into ops PRs. No durable retro file (avoids dupes).

## Merge governance
The worktree manager opens the PR and signals "ready" (green against **current**
main) — it does **not** merge and does **not** delete its `docs/features/<slug>/`
docs (they stay for review). **At merge, Sisyphus deletes those ephemeral docs**
(CI fails while `docs/features/` is non-empty — that guard is CI's *only* check),
then **squash-merges** and chooses order. Jaime has **delegated the merge decision to Sisyphus**: Sisyphus merges green PRs on its own judgment — judging readiness from the **PR description**, the manager's "ready" signal and the **PR/issue comments** (red-team, retro, follow-ups), **docs-clean CI being green**, **the laptop `pixi run test` suite having run green inside the loop**, and what makes sense for **merge order**. **Sisyphus does not re-run tests** — the test-runner already ran the suite inside the loop; testing is not Sisyphus's responsibility. It escalates a merge to Jaime **only when it is genuinely tricky** (a risky or ambiguous change, a design fork, or low confidence). Other open worktrees then rebase on main
and re-green before their turn. Never force-push main; never delete branches
others depend on.

## Coding standards
- Match surrounding style; keep each package cohesive per its README.
- Every feature ships tests that actually exercise its acceptance criteria.
- Commit in small green increments (recoverable state).
- Secrets are never committed (`.claude/settings.local.json`, `.env` are ignored).
