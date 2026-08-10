# Retro: mock-skill-api

> Written by the worktree manager when the PR is ready. Sisyphus reads this to
> improve the workflow and to file follow-ups. Issues are **not** created here.

First feature run through the loop, so a lot of this is one-time scaffolding
friction rather than recurring cost. The loop itself worked: red-team round 1
found zero BLOCKs but eleven substantive NOTEs, and round 2 — a delta-only pass
over the fixes — found a genuine BLOCK that round 1 could not have seen. That
second pass paid for itself.

## Operational difficulties

**1. Git identity was unset in the worktree.** The first `git commit` failed with
"Author identity unknown". I set it repo-local from the existing history's
author. Trivial, but it blocks the very first commit of every new worktree.

**2. `pixi run test` — the command CLAUDE.md names as canonical — is red on
main and cannot demonstrate greenness.** The five empty skeleton packages
(`robot_brain`, `robot_bringup`, `robot_description`, `robot_perception`,
`robot_safety`) have no tests, so colcon's `python -m unittest` fallback exits 5.
Worse, the task is `colcon test && colcon test-result --verbose`, so the `&&`
short-circuits and the results summary never prints at all. Every "green" claim
in this run had to be made with a package-scoped invocation instead, which means
the documented command and the actual gate have diverged.

**3. `colcon test` reports success on *zero* tests.** colcon treats pytest exit
code 5 (`NO_TESTS_COLLECTED`) as success, so "0 errors, 0 failures" is not
evidence that anything ran. I only caught this because the red-team read colcon's
source; I then had to explicitly instruct the test-runner to report **absolute
test counts** per package. A future run whose collection silently breaks would
report green with an empty suite.

**4. `tests_require` is silently ignored by modern setuptools.** The scaffolded
`setup.py` used it, so colcon saw no pytest dependency, chose the `unittest`
runner, and collected nothing — a green-looking no-op. The implementer had to
diagnose this from colcon's source before any test could run. Fixed here with
`extras_require={'test': ['pytest']}`; the other five packages still have the
broken form.

**5. The environment's `launch_testing` / `launch_ros` pytest plugins are
incompatible with the installed pytest 9** and abort the session on load. Worked
around with a self-documenting package-local `pytest.ini` in each owned package.
This is an environment defect being papered over in two places.

**6. The `red-team` agent cannot write its own report.** Its role definition says
it writes `red_team.md`, but its tool list is `Read, Grep, Glob` — no `Write`.
Both rounds returned the full report as message text and I pasted it to disk.
This works, but it burns a large amount of manager context re-emitting a ~19 KB
document verbatim, and it means the "read-only" guarantee is enforced by
accident rather than by design.

**7. `main` moved mid-run, and it changed the rules I was operating under.**
Two ops PRs landed while this feature was in flight, replacing "surviving NOTES →
GitHub issues" with "propose in `retro.md`; Sisyphus files them". I had already
drafted three issues and was about to file them. I caught it only because I
diffed `CLAUDE.md` as part of the rebase — nothing in the loop tells a manager
that its own instructions may have changed underneath it.

**8. There is no CI.** Step 7 of the loop says "ensure light CI + the full local
suite pass", but `.github/workflows/` does not exist. Nothing failed; there was
simply nothing to run. The instruction currently describes a gate that isn't
built.

**9. One brief ambiguity.** "place/**close-drop** with an empty gripper" in the
failure-path list read two ways. The implementer took `CloseGripper` on an empty
gripper as a legal no-op and the red-team endorsed that reading (AC4 says
open/close *toggles*, and names **open** as the dropper). Called it and moved on
— flagged below for confirmation rather than escalated, since it's cheap to
change now and the reading is defensible.

## Suggested improvements

- **Workflow:** after `git rebase origin/main`, the manager should re-read
  `CLAUDE.md` and `.claude/commands/run-feature.md` and diff them against the
  versions it started with. Difficulty 7 was a near-miss that the loop gives no
  systematic protection against — it's exactly the kind of thing that gets worse
  as more worktrees run in parallel.
- **Workflow:** the "green" gate should name a command that can actually be green.
  Either fix the workspace suite (see follow-ups) or have the loop specify a
  package-scoped invocation over the brief's owned paths.
- **Agent tooling:** give `red-team` a `Write` tool scoped to `red_team.md`, or
  formally make the manager its scribe. Right now the role description and the
  tool list contradict each other.
- **Agent tooling:** put "report absolute test counts, not just failure counts"
  into the `test-runner` role definition rather than relying on each manager to
  remember it. This is a correctness property of the gate itself.
- **Agent tooling:** a delta-only red-team pass after a fix round proved
  worthwhile — it caught a BLOCK that a whole-feature re-review would likely have
  buried. Worth making the second round explicitly delta-scoped in the loop.
- **Missing context/docs:** a short "ament_python package conventions in this
  repo" doc — `extras_require` not `tests_require`, `test/` layout, lint stubs,
  what colcon does and doesn't treat as failure. The context-explorer had to
  reconstruct this from scratch, and the two real build-config traps above cost
  the implementer significant time.
- **Missing context/docs:** record where physical limits live (backend refuses
  the impossible; safety clamps the illegal). This came up as a design question
  during red-team and has no home in `decisions.md`.

## Proposed follow-ups (Sisyphus files the issues)

- **Make `pixi run test` green and honest workspace-wide** — the canonical
  command is red on main because five empty packages collect no tests, and the
  `&&` hides the summary; managers currently cannot prove greenness with the
  documented command. Give each skeleton package a trivial passing test plus
  `extras_require={'test': ['pytest']}`, and/or change the task so the summary
  always prints. — affected paths: `pixi.toml`, `src/robot_brain/`,
  `src/robot_bringup/`, `src/robot_description/`, `src/robot_perception/`,
  `src/robot_safety/`

- **Guard against colcon's silent zero-test success** — exit code 5 is treated as
  success, so a broken collection reports green with an empty suite. Assert a
  non-zero test count in the `test-runner` role definition, and lift the two
  package-local `pytest.ini` plugin workarounds to one workspace-level config
  with a single removal point once the pytest-9 incompatibility is fixed. —
  affected paths: `.claude/agents/test-runner.md`, `src/robot_skills/pytest.ini`,
  `src/robot_backends/pytest.ini`, workspace pytest config

- **Record where physical limits live (backend vs. safety layer)** — `MockBackend`
  refuses out-of-reach/out-of-range commands; `robot_safety` will need the same
  numbers (0–1.2 m column, 0.85 m reach). Two copies can silently diverge, and a
  future reader may conclude the backend already checks limits and skip the
  safety layer. Record the split — backend refuses the physically impossible,
  safety clamps the policy-illegal, both may fire — and have `robot_safety` read
  one source of truth. — affected paths: `docs/design/decisions.md`,
  `src/robot_backends/robot_backends/mock_world.py`, future `src/robot_safety/`

- **Ratify the wire-format compatibility policy before a second component binds
  to it** — serialization rejects unknown keys everywhere. Right for `Skill` (a
  garbled LLM tool call should fail loudly), but it means the ROS 2 action layer
  adding `stamp`/`frame_id` breaks every older reader. The current stance is now
  documented in the module docstring; it needs ratifying, or replacing with a
  reserved ignored `extensions` field on `Observation`/`SkillResult` while
  keeping `Skill` strict. — affected paths:
  `src/robot_skills/robot_skills/serialization.py`, `docs/design/decisions.md`

- **Give `red-team` a write tool, or make the manager its scribe explicitly** —
  the role says it writes `red_team.md`; its tool list has no `Write`. Both
  rounds returned ~19 KB of report as message text for the manager to persist,
  which is a large avoidable context cost per round. — affected paths:
  `.claude/agents/red-team.md`, `.claude/commands/run-feature.md`

- **Add the light CI the loop already assumes** — step 7 requires "light CI +
  the full local suite" to pass, but `.github/workflows/` does not exist. With
  green checks as the merge gate and no manual approval, the gate is currently
  only as strong as the local run. — affected paths: `.github/workflows/`

- **Set git identity in worktree scaffolding** — the first commit in a fresh
  worktree fails with "Author identity unknown". — affected paths: worktree
  creation tooling, `.claude/commands/run-feature.md`

- **Confirm the intended semantics of `close_gripper` on an empty gripper** —
  the brief lists "place/close-drop with an empty gripper" among failure paths;
  shipped as a legal no-op (`ok` + informational reason), with only `Place`
  failing. Red-team endorsed that reading. If the intent was failure, it's a
  one-line change plus one test now, and awkward once a brain depends on
  close-then-grasp. — affected paths:
  `src/robot_backends/robot_backends/mock_backend.py`,
  `docs/features/mock-skill-api/brief.md`

- **Decide whether `pixi.lock` should be tracked** — it is untracked and not
  gitignored, so it shows up as noise in every worktree. Lockfiles are normally
  committed. Left alone here as out of scope. — affected paths: `pixi.lock`,
  `.gitignore`
