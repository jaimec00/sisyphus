# Status — #67 robot_brain system prompt still claims a four-wheel base

- **Slug:** `i67-brain-prompt-holonomic-base`
- **Branch:** `feat/i67-robot-brain-system-prompt-still-claims-a`
- **Issue:** #67
- **Phase:** manager rulings recorded → dispatching implementer
- **Round:** 0 (no red-team pass yet)
- **Blockers:** none

## Phase log

| when | phase | outcome |
|---|---|---|
| start | sync | worktree at `3a0958c` = `origin/main`, clean |
| start | brief | issue #67 read via `--json`; body non-empty, acceptance criteria clear |
| start | new deps | **none** — this feature adds no third-party dependency, so step 2's provision-and-probe is a no-op |
| start | context-explorer | `context.md` written; 3 open questions raised |
| start | manager rulings | R1–R4 below, recorded before implementer dispatch |

## Manager verification (done before ruling — not taken on the explorer's word)

Two load-bearing claims in `context.md` were re-verified directly:

1. **`NavigateTo` takes only a named location.** `src/robot_skills/robot_skills/skills.py:153-172` —
   the dataclass has exactly one field, `location: str`, coerced by `as_identifier`.
   Reading the rest of the file confirms `MoveGripper`, `Grasp`, `Place`,
   `ExtendColumn`, `OpenGripper`, `CloseGripper` are the only other skills and
   none of them addresses the base. **There is no direction, heading, velocity
   or pose argument anywhere on the base's only command.**
2. **The prompt-drift suite structurally cannot see this claim.**
   `src/robot_brain/test/brain_fixtures.py:56,74-79` — `inline_words()` extracts
   identifiers only from `` `backtick` `` spans, after `without_fences()` strips
   fenced blocks. `test_prompt_drift.py:128-136` diffs exactly that set against
   `live_vocabulary()`. Plain prose such as "a four-wheel base" is never in the
   input set, so no existing assertion could have failed on it.

Also checked, and it changes ruling R2: **`src/robot_description/robot_description/`
contains only `__init__.py`** — there is no Python API for the body. Its own gate
(`src/robot_description/test/test_description.py:60-70`) resolves everything through
`get_package_share_directory`, i.e. it requires a `colcon build` install tree plus
the `xacro` / `check_urdf` / `urdf_parser_py` toolchain, "with no source-tree
fallback, by design". `robot_brain`'s `package.xml:18-21` test-depends only on the
four pure-Python siblings (`robot_mcp`, `robot_safety`, `robot_backends`,
`robot_skills`). That asymmetry is what makes the "derive the wheel count from the
URDF" idea expensive rather than elegant — see R2.

## Rulings

These are **binding but not assumed correct**. If you believe one is wrong,
**escalate to me in-process** — do not silently deviate, and do not comply into
a bug.

### R1 — Correct the fact; do not advertise the affordance.

`AGENTS.md:3` must describe the base as it actually is: **a 3-omniwheel
holonomic base**, matching `docs/design/spec.md:29` and D26/D29's vocabulary.
The phrase must contain "holonomic" and convey three omniwheels; prefer
spec.md's exact term `3-omniwheel holonomic` over a paraphrase.

**It must NOT add framing about what that lets the robot do** — no "it can
strafe", no "it can move in any direction", no new sentence about base motion.
The issue's own argument for why this matters ("a materially different control
affordance ... it can strafe") is **not true of anything the planner can
command today**: `navigate_to(location)` is the only base skill and takes a
named location (verified above). This repo already has a principle for exactly
this, and it is enforced by a test — `test_prompt_drift.py:116-126`, on
withheld tools: a capability the agent cannot invoke gets "no row, no example
and no mention", because it would be *"an invitation dressed as documentation"*.

The line I am drawing, stated so it can be attacked: **state what the body
is, not what the planner may do with it.** Describing the body accurately is
the whole point of the issue and is inert at the tool seam — there is no
argument anywhere in the skill API into which a belief about holonomy can be
encoded. Advertising a manoeuvre is not inert: it invites plans that no tool
can express.

### R2 — Ship a regression test, but do not couple `robot_brain` to the URDF.

CLAUDE.md requires every feature to ship tests that exercise its acceptance
criteria, and the red-team rubric makes "weak/inadequate tests" a BLOCK. A
one-line prose fix with no guard is precisely the drift that produced this
issue, so a test belongs **in this PR**.

**Do it in `src/robot_brain/test/test_prompt_drift.py`** as a superseded-claim
ledger: a module-level constant of body claims the decision log has
*superseded* (D1's four-wheel base — spelling variants `four-wheel`,
`4-wheel`, `four wheel`, case-insensitive), asserted absent from `PROMPT`;
plus an assertion that the current base descriptor **is** present. Cite
D1→D26/D29 in the constant's comment so a future reader knows where the ledger
comes from and how to extend it when the next body fact is superseded.

**Do NOT** add `robot_description` as a test dependency and derive the wheel
count from the expanded URDF. I considered it — it is the design that matches
this suite's "check against a live source" philosophy, and I am rejecting it
on cost, not on principle: it would make `robot_brain`'s fast pure-Python
suite newly require a `colcon build` install tree and the xacro/`check_urdf`
toolchain (per `test_description.py:60-70`, that gate has no source-tree
fallback by design), and it would still have to recover a wheel *count* by
parsing English prose. That is a large, brittle coupling bought for one
sentence.

The honest consequence: this test **pins a known-stale claim; it does not
detect the next one.** Say so in the docstring, in this suite's register — a
test whose limits are undocumented reads as a guarantee it is not. The general
problem (the prompt's body-description prose has no live source to diff
against, while PR3–PR7 are about to add the column, arms, gripper and camera)
is **real and out of scope here** — it goes out as a follow-up comment on the
issue for Sisyphus to file, per CLAUDE.md's feedback routing.

### R3 — No new backticks in the changed prose.

Any word added to `AGENTS.md` must be plain prose, not `` `code` ``. A
backticked token is diffed against `live_vocabulary()`
(`test_prompt_drift.py:128-136`) and "holonomic" / "omniwheel" exist in no
schema, enum or world id — backticking either would turn a correct prose fix
into a red test.

### R4 — Preserve the hand-written register.

The prompt is a human deliverable (D22; `README.md:63-73`, `agent.py:15-19`),
deliberately not generated. Match the surrounding voice: short declarative
sentences, second person, concrete units. This is a surgical edit to one
clause of line 3 — **do not** reflow, restructure or "improve" the rest of the
file. Keep the diff minimal so the review is about the claim, not the prose.

### Disposition of the explorer's open questions

| # | question | ruling |
|---|---|---|
| 1 | just the fact, or extra affordance framing? | **R1** — just the fact, no affordance framing |
| 2 | regression test here or follow-up? | **R2** — test here (ledger form); the general no-live-source problem is a follow-up |
| 3 | deploy staleness to the Pi | **R3'** — out of code scope; goes in the **PR description** as a deploy note. `AGENTS.md` reaches the Pi by a manual `scp` (`src/robot_brain/README.md:91-99`) that this repo never executes, so any already-deployed copy stays stale until someone re-runs it. Not a blocker, not a code change — but it must not go unsaid. |
