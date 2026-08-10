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
- Headless: no GUI required; visualize via Foxglove (`foxglove_bridge`).

## Repo layout
- `src/robot_*` — ROS 2 ament_python packages (brain, skills, safety, backends,
  perception, description, bringup).
- `docs/design/` — architecture + decision log (read-only design source).
- `docs/features/<slug>/` — per-feature `brief.md`, `context.md`,
  `implementation.md`, `red_team.md`, `status.md`.
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
context (sonnet) → implement + tests (opus) → red-team (opus, read-only) →
fix (≤2 rounds) → test-runner (sonnet) → repeat until green → PR.
Full narrative in `DEVELOPMENT.md`; orchestration in `.claude/commands/run-feature.md`.

## Red-team severity rubric
- **BLOCK** (must fix before merge): correctness bugs, safety-invariant
  violations, design-principle violations, extensibility traps,
  weak/inadequate tests.
- **NOTE** (follow-up issue, not a blocker): style, naming, micro-optimizations,
  speculative generality.
Be rigorous, **not** nitpicky. Quality + extensibility is the goal.

## Escalation
Solve at your scope. Escalate to the parent **only** for a real blocker or a
genuine design fork (not ordinary difficulty). Record it in `status.md`.
Chain: worker → worktree manager → Sisyphus (Pi) → Jaime.

## Merge governance
The worktree manager signals "ready" (green against **current** main).
**Sisyphus owns the merge** and chooses order; other worktrees then rebase on
main and re-green before their turn. PRs are **squash-merged**. Never
force-push main; never delete branches others depend on.

## Coding standards
- Match surrounding style; keep each package cohesive per its README.
- Every feature ships tests that actually exercise its acceptance criteria.
- Commit in small green increments (recoverable state).
- Secrets are never committed (`.claude/settings.local.json`, `.env` are ignored).
