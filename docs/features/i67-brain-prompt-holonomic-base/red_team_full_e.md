# Red-team pass E — full adversarial pass over `origin/main..HEAD` (`d7f0f9a`)

**Scope:** the whole branch diff, formed independently before reading
`red_team{,_fix}.md` and `red_team_full_{a,b,c,d}.md`. Primary target per the
brief: the four prose corrections the *manager* made in `d7f0f9a`, which no
reviewer had seen.

**Verdict: 1 BLOCK, 2 NOTES.** The BLOCK is against a sentence introduced by
`d7f0f9a` itself, in the permanent `decisions.md` entry — the branch's dominant
defect class (prose asserting something false about the code / the plan),
occurring for the ninth time, again inside a fix for it.

`pixi run test`: **green** — 763 tests, 0 failures, 0 skipped, audit passed,
ratchet `+0`, `robot_brain` 57 non-lint = `scripts/test_baseline.json`. Worktree
clean before and after (`git status --porcelain` empty); all mutation work in
`/tmp/i67rt`.

---

## BLOCK 1 — D30's new PR6 clause asserts something PR6 does not do, and tells the next author to delete this feature's only drivetrain gate (`docs/design/decisions.md:120`)

**VERIFIED** (documents + code + execution; the claim is about future work, so
the verification is that nothing in the recorded scope or in the code supports
it).

The sentence added by `d7f0f9a`:

> a fact whose owner is `robot_description` cannot be read from here without
> reversing this decision's dependency call — and **PR6 dissolves that**, since
> pointing `RobotModel` at the URDF **gives the drivetrain an owner on this side
> of the seam and retires the ledger row rather than adding to it**; and **until
> then**, a decision that *supersedes* a body fact with no live owner must add a
> ledger row…

Three claims, none of which holds:

1. **PR6 does not give the drivetrain an owner.** `docs/design/urdf-mjcf-pr-breakdown.md:129-137`
   defines PR6 exactly: "parses the expanded URDF → **the 7 constants**.
   Refactor `RobotModel`'s default to load from the shipped URDF", with a golden
   test that "parsed values `==` today's literals (0.18 / 0.50 / 0.85 / 0.00 /
   1.20 / home offset)". Those seven are `RobotModel`'s current fields
   (`src/robot_backends/robot_backends/mock_world.py:82-87`) — shoulder offsets,
   reach, column travel, home gripper offset. **Not one is a drivetrain fact**,
   and `RobotModel` has no wheel/base field at all (`grep -n "wheel|drive"
   mock_world.py` → nothing). Nothing in `robot_backends` would consume one
   either: `_navigate_to` teleports the base to a stored pose
   (`mock_backend.py:279-291`), so no wheel count, wheel radius or base radius
   has a consumer on this side of the seam. Giving the drivetrain an owner is
   *new scope nobody has written down*, and it cuts against `RobotModel`'s
   charter as D23 states it (`decisions.md:53`: "`RobotModel` (shoulder offsets,
   reach, column travel) is hardware description").
2. **"Retires the ledger row" contradicts D30's own third bullet**, two
   paragraphs above (`decisions.md:117-118`): the URDF edge "would buy **one
   digit**, since the URDF carries three wheel joints but the words 'omniwheel'
   and 'holonomic' only in a comment". VERIFIED: `grep -ni "omni|holonomic"
   src/robot_description/urdf/base.xacro` → lines 3, 7, 43, 51, 149, 168 are all
   comments or a macro name; the joints are `base_{left,back,right}_wheel`. So
   even a maximally generous PR6 would own the count `3`, never the descriptor
   `3-omniwheel holonomic` that the ledger key asserts, and never the *absence*
   of the retired spelling that `test_a_superseded_body_claim_is_not_still_taught`
   scans the whole prompt for. The row is not retired by any amount of URDF.
3. **"Until then" gives the ledger obligation an expiry that does not exist**,
   and puts D30 out of step with **its own `spec.md` row**
   (`docs/design/spec.md:171-177`), which states the same rule with no expiry.
   Two design docs now disagree about whether the rule ends at PR6.

**Failure scenario (concrete).** PR6's author reads D30 — the permanent record,
after `docs/features/` is deleted at merge — and finds it stated as fact that
their PR "gives the drivetrain an owner … and retires the ledger row". They
point `RobotModel` at the URDF for the 7 constants exactly as the roadmap says,
delete `SUPERSEDED_BODY_CLAIMS` and the two ledger tests as "retired by D30",
and add no replacement, because D30 told them the owner now exists. The prompt's
drivetrain claim returns to ungated — the precise state that produced #67 — and
the "must add a ledger row" rule reads as expired for every body fact after it.
Milder but likelier variant: PR3–PR5's author supersedes a body fact, reads
"PR6 dissolves that", and skips the row.

Why this is a BLOCK and not a NOTE: `decisions.md` is append-only and this is
its last review; the sentence is *prescriptive* about the next PR in the queue;
and it is wrong against a scope written down two files away. It is also, by
provenance, the same defect this branch has now hit nine times — pass D asserted
it as its NOTE 2 ("VERIFIED against urdf-mjcf-pr-breakdown.md:83-147"), but the
verified part was only "PR6 points `RobotModel` at the URDF"; the drivetrain
clause was inference, and the manager transcribed the inference as fact.

**Fix direction** (one sentence; keep the useful half): PR6 is genuinely the
point at which *reversal becomes cheap* — a URDF-backed `RobotModel` lets
`robot_brain` read URDF-owned facts through `robot_backends` with no new
dependency edge. Say that, and stop there:

> …cannot be read from here without reversing this decision's dependency call —
> **PR6 is where reversing it gets cheap**, since a URDF-backed `RobotModel`
> carries URDF facts across without a new edge; but PR6 as scoped parses only
> the seven kinematic constants
> ([urdf-mjcf-pr-breakdown.md](urdf-mjcf-pr-breakdown.md) §PR6), none of them
> drivetrain, and the URDF owns the wheel *count*, not the words "omniwheel" or
> "holonomic" — so retiring the ledger row is work someone must choose, not a
> consequence of PR6. Meanwhile a decision that supersedes a body fact with no
> live owner must add a ledger row, since nothing detects the need.

Drop "until then"; leave `spec.md`'s row as it is (it is correct).

---

## NOTE 1 — the live-source enumerations still narrow `robot_skills` to `Side`, while `FailureCode` drives one of the module's strongest checks (`src/robot_brain/test/test_prompt_drift.py:14-17`, `src/robot_brain/README.md:70-73`)

**VERIFIED by reading the assertions.** Both lists were extended in `d7f0f9a`
with `RobotModel` and `robot_skills.Side`. Direction one is clean — every source
named is really read by an assertion (`TOOL_NAMES`/`TOOLS` at `:315,:354`;
schemas `:353,:375`; `SafetyLimits.defaults()` `:389`; `default_world()`
`:431,:441`; `RobotModel` `:280,:298`; `Side` `:266-267`). Direction two is
still short: `robot_skills.FailureCode` backs the two-way failure-table
comparison at `:416` and the safety-code assertions at `:407,:421`, and
`SkillStatus`/`GripperState`/`MockBackend`'s wire keys feed `live_vocabulary()`
at `:132-147`. Naming `robot_skills` only "for the arm count" reads as if the
failure vocabulary were hand-typed, when it is the module's most complete live
comparison. Both paragraphs do qualify themselves ("an aim, not an invariant"),
so this understates rather than over-claims — hence NOTE, not BLOCK. Pass D
raised the same list as its NOTE 6; the fix landed half of it.

**Fix direction:** add `robot_skills`' `FailureCode` (and the Mock's observation
/ result wire keys) to both lists, or replace the enumeration with "the four
sibling packages the brain meets across the seam".

## NOTE 2 — `status.md` has no round-5 record, so pass D's surviving NOTES are routed nowhere (`docs/features/i67-brain-prompt-holonomic-base/status.md:6,310-322`)

**VERIFIED by diff.** `status.md`'s last update is `d3abd98` (round 4); the
head commit `d7f0f9a` fixed four of pass D's six NOTES and is unrecorded. The
two that were *not* fixed are absent from the follow-up list: pass D's NOTE 4
(a fenced block placed inside the introduction can split the descriptor and
leave the presence check green) and NOTE 5 (`spec.md:15` still says "Flattened
through **D28**" while the file carries D29 and D30 rows — pre-existing, made
one decision staler here). CLAUDE.md routes surviving NOTES to the issue, and
`docs/features/` is deleted at merge, so as it stands both vanish.

**Fix direction:** a round-5 row plus follow-ups 10 (fence-split descriptor,
belongs with follow-up 6bis) and 11 (re-flatten `spec.md` through D30 and bump
line 15) before the PR comment goes out.

---

## Test adequacy — **adequate** (explicit verdict)

All seven `TestBodyDescription` tests were re-derived by mutation on a copy at
`/tmp/i67rt` (`src/` copied out, run with `PYTHONPATH` at the copy; baseline 30
passed). Every mutation was caught, each by exactly the test that owns it:

| # | mutation | result |
|---|---|---|
| M1 | prompt reverted to "a four-wheel base" | absence **and** descriptor tests fail |
| M9 | "the 4 wheels are fine" hidden inside a fenced worked example | absence test fails (whole-prompt scan works) |
| M10 | correct sentence re-wrapped so the descriptor straddles a newline | **stays green** (H3's wrap tolerance holds) |
| M4 | "two arms" → "three arms" (`Side` still 2) | arms/grippers test fails |
| M5 | "extendable" → "fixed" column | column test fails |
| M6 | prompt reach 0.85 → 0.9 | reach test fails |
| M6b | "beyond the 0.85 m reach" reworded away (empty set) | reach test fails |
| M7 | `RobotModel.reach_radius` 0.85 → 0.95 | reach test fails |
| M8 | `max_column_height` → 0.0 (travel collapsed) | column test fails |
| P1 | ledger pattern typo'd to `…wheels` | positive-control test fails |
| P2 | ledger pattern widened to bare `four\|4` | negative-control **and** absence tests fail |
| P3 | ledger pattern degraded to the literal `four-wheel` | positive-control test fails |

The pattern's positive/negative controls are real controls: a silently narrowed
or widened pattern cannot pass them. `introduction()`'s three jobs
(fence-drop → intro-split → whitespace-collapse) are each independently
exercised by M9/M1/M10.

Also verified, and correctly *declared* as ungated rather than silently missed:
adding `hallway` to the seed world leaves all 30 prompt tests green, and
retuning the column travel restated inside a worked example (1.2 → 1.5) leaves
all 30 green. Both are named in D30, in the class docstring and in `status.md`'s
follow-ups 4/5 — honest residue, not a hole hidden by prose.

## Acceptance criteria — met (judged independently)

- **AC1** — `AGENTS.md:3-4` now reads "a 3-omniwheel holonomic base", matching
  `spec.md:29` and D26/D29 and the shipped URDF's three `continuous` wheel
  joints. No line in the edited paragraph exceeds 80 columns.
- **AC2 (sweep)** — my own grep of all of `src/robot_brain/` for body vocabulary
  (`wheel|holonomic|omni|differential|caster|chassis|drivetrain|servo|motor|
  battery|lidar|camera|sensor|payload|dof|torso|mast|lift|rail|four|three|dual|
  legs|mecanum`) found no surviving false body claim. The only body prose outside
  the opening sentence is `AGENTS.md:48` / `:142-144` ("raises both shoulders"),
  which is true of `mock_world.py`'s shoulder-height arithmetic, and the
  worked-example reach, which is now gated. Every remaining "four-wheel" in the
  repo is a deliberate historical citation (D1, D26, `spec.md:29`,
  `test_description.py:495`, the ledger's own comments).

## Rulings re-checked (review targets, not scaffolding)

- **R1** (state the body, not the manoeuvre) — upheld; the shipped sentence adds
  a body noun and no capability clause, and `navigate_to(location)` is still the
  only base command (`skills.py`, one `location: str` field).
- **R2 / D30's dependency call** — the *measured* reason holds: with
  `AMENT_PREFIX_PATH` restricted to `robot_brain`'s declared deps,
  `get_package_share_directory('robot_description')` raises `PackageNotFoundError`
  (executed; the workspace uses isolated installs, so this is what `colcon test`
  presents). The docstring's companion claim is also true: `test_no_ros_runtime`'s
  probe covers `import robot_brain` in a bare subprocess and its static scan
  walks `robot_brain/`, not `test/` (`test_no_ros_runtime.py:101-118`).
- **R3/R4** — upheld (no new backticks; one-clause word-diff).
- **Round-4 H1–H4** — all closed, re-verified here by mutation rather than taken
  from `status.md`.
- **`spec.md`'s D30 row** — checked as a standalone summary against D30's
  preference order: correct, and correctly narrowed to "a body fact that has no
  live owner". It is now the *more* accurate of the two; BLOCK 1 is against
  `decisions.md`, not against it.

## Worktree state

Clean. `git status --porcelain` empty at start and finish; only `build/`,
`install/`, `log/` (gitignored) touched by `pixi run test`;
`scripts/test_baseline.json` unchanged. All perturbation happened in
`/tmp/i67rt`.
