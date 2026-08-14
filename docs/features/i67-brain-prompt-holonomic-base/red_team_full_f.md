# Red-team pass F — full adversarial pass over `origin/main..HEAD` (`edb2221`)

**Scope:** the entire branch diff, re-derived independently. Findings were formed
and executed *before* reading `red_team{,_fix}.md` and `red_team_full_{a..e}.md`;
those were read afterwards only to avoid re-reporting closed items.

**Verdict: 1 BLOCK, 3 NOTES.**

The BLOCK is a **factual claim in the permanent `decisions.md` D30 entry that the
repository's own record refutes** — the branch's dominant defect class, tenth
instance, and the first one *no prior pass has reported*. It has been in D30
since the entry was first written (`5837211`) and survived all three rewrites,
including both of the manager's own corrections to that paragraph. It is not in
the two test-file copies of the same sentence, which are anchored differently and
are correct.

`pixi run test`: **green** — 763 tests, 0 errors, 0 failures, 0 skipped, audit
passed, ratchet `+0`, `robot_brain` 60 tests / **57 non-lint** =
`scripts/test_baseline.json`. `git status --porcelain` empty before and after.
All perturbation was done on a copy at `/tmp/rt67` (`src/` copied out, plus its
own `colcon build` for the ament-index probe); the worktree was never edited.

---

## BLOCK 1 — D30 says the false prompt survived "for a day after the base was built and gated". It survived **17 minutes**, and the record says so. (`docs/design/decisions.md:115`)

**VERIFIED** — timestamps from `git log`, `gh pr view 66`, `gh issue view 67`.

The sentence (the entry's opening, motivating evidence):

> D21 makes `AGENTS.md` a live system prompt rather than a document *about* one,
> and D26/D29 changed the base underneath it: the prompt went on telling the
> planner it drove "a four-wheel base" **for a day after the 3-omniwheel
> holonomic base was built and gated**.

"Built and gated" is unambiguous: that is D29 / #65 / PR #66, the PR that
authored `urdf/base.xacro` and its gate. The record:

```
$ gh issue view 65 --json createdAt   # "PR2 — Mobile base URDF"
2026-08-13T23:06:16Z                  # base work starts
$ gh pr view 66 --json createdAt,mergedAt
createdAt 2026-08-14T00:42:55Z        # PR opened
mergedAt  2026-08-14T00:49:37Z        # base built AND gated  <- the anchor
$ gh issue view 67 --json createdAt
2026-08-14T00:49:53Z                  # #67 filed: 16 seconds later
$ git log --format='%h %ad' --date=iso 150dd12
150dd12 2026-08-13 21:07:00 -0400     # = 2026-08-14T01:07:00Z: prompt corrected
```

**17 minutes 23 seconds**, not a day — and the claim is out by ~80× in the
direction that flatters the entry's own thesis. Even measured from the *earliest*
defensible reading of "built" (#65 filed, 23:06Z) it is two hours, still the same
evening.

**This is confined to `decisions.md`.** The two copies in the source are anchored
to **D26**, which `decisions.md` itself dates `2026-08-12` (section header,
`decisions.md:74`), so "for a day after D26" is accurate and both should stay:

- `src/robot_brain/test/test_prompt_drift.py:61` — "D26 traded it … but the
  prompt kept saying 'four-wheel' for a day" ✔
- `src/robot_brain/test/test_prompt_drift.py:182` — "described D1's four-wheel
  base **for a day after D26** (#67)" ✔

Only the D30 sentence moved the anchor to "built and gated" while keeping the
duration that belonged to the other anchor.

**Failure scenario (concrete, and it is about evidence, not code).**
`decisions.md` is append-only and outranks every other doc ("where any doc
disagrees with it, it wins"). A reader in three months — most plausibly whoever
takes follow-up 1, the unguarded-prose issue, and has to argue how much gating
prose deserves — reads D30 and takes away "a live system prompt drifted for a
day and nothing noticed". What actually happened is sharper and *more* useful:
the prompt became false **at the instant the base merged**, and the only thing
that caught it was a human reviewer of that same PR filing #67 sixteen seconds
later. Nothing in `pixi run test` went red then, and nothing would have gone red
in a week. The corrected story motivates the gate better than the wrong one, and
the wrong one invites the retort "a day is not very long — the ledger is not
worth its cost".

Why BLOCK and not NOTE: this is the last review before the entry becomes
permanent; it is a claim about *this project's own history*, refuted by this
project's own record; the neighbouring D29 entry closes with "a decision entry
that rounds its own evidence up is the failure this PR already had to fix once";
and the fix is one clause. It is the same class as the two claims already BLOCKed
inside this paragraph (H1's false categorical, pass E's "PR6 dissolves that") —
graded the same way for consistency, not for novelty.

**Fix direction.** Either re-anchor (cheapest, and matches the test file):

> …and D26/D29 changed the base underneath it: the prompt went on telling the
> planner it drove "a four-wheel base" for a day after **D26 retired it**.

or state what actually happened, which is the stronger evidence:

> …the prompt was still telling the planner it drove "a four-wheel base" at the
> moment the 3-omniwheel base merged. Nothing in the suite went red; #67 exists
> because a human reviewer of that PR noticed the sentence, not because anything
> detected it.

Do **not** touch `test_prompt_drift.py:61,182` — they are correct as anchored.

---

## NOTE 1 — "the seven kinematic constants" is six fields (or eight numbers), and D30 inherited the miscount from the roadmap (`docs/design/decisions.md:120`)

**VERIFIED by counting both sources.**

D30: "as scoped (`urdf-mjcf-pr-breakdown.md` §PR6) it parses the seven
*kinematic* constants — shoulder offsets, reach, column travel, home offset".

`urdf-mjcf-pr-breakdown.md:16` calls them "The 7 numbers the URDF must eventually
own" and then tables **six** rows; `mock_world.py:82-87` has **six** fields
(`shoulder_offset_y`, `shoulder_offset_z`, `reach_radius`, `home_gripper_offset`,
`min_column_height`, `max_column_height`) = **eight** scalars if `Point` is
counted componentwise. Seven is neither. D30's own enumeration (`2 + 1 + 2 + 1`)
also reads as six.

Kept as a NOTE, not a BLOCK, because: the numeral is a faithful citation of the
roadmap's own label; the **load-bearing** half of the sentence is true and I
re-verified it (`grep -nE "wheel|drive|base_radius|omni" mock_world.py` → nothing;
`RobotModel` carries no drivetrain field, so a URDF-backed `RobotModel` owns
nothing the ledger covers); and the roadmap is outside this PR's owned paths.
Note for calibration: pass E asserted "Those seven are `RobotModel`'s current
fields" (`red_team_full_e.md:41`) — that was the miscount's route into D30, and
it is exactly the trap the brief names (a VERIFIED finding is verified for its
own claim, not for what is read off it).

**Fix direction:** if D30 is edited for BLOCK 1, drop the numeral in the same
pass — "parses only the *kinematic* constants (shoulder offsets, reach, column
travel, home offset)". The roadmap's own "7" belongs in the ops follow-up already
routed for `spec.md:15` / `CLAUDE.md`.

## NOTE 2 — D30's rationale lists the run's false-prose instances and omits the two that were in D30 itself (`docs/design/decisions.md:121`)

**VERIFIED against `status.md` and the branch history.**

> …found the same defect repeatedly in its own prose — a docstring, its
> replacement, the class docstring twice, a test's claim about its own controls,
> and the package README…

Those six are correct (B2, F1, B3, G2, F2, G1). Missing: **the decision entry
itself, twice** — H1's "compares every prompt claim that has an owner" (round 4)
and "PR6 dissolves that / until then" (round 5, and the manager's own note at
`status.md:21-23` calls it the ninth instance). The list is a list and claims no
exhaustiveness, so this understates rather than over-claims — hence NOTE.

It is still worth one clause, because the omitted instances are the entry's own
best evidence for its own thesis: the artifact that most needed the discipline
was the permanent one, and it needed two rounds. Written down, the lesson binds
the next D-entry author; left out, D30 reads as though only docstrings drift.

**Fix direction:** "…and the package README — **and this entry twice**, each
wrong about the code or the plan it described…".

## NOTE 3 — `spec.md`'s D30 row encodes the supersession half of the rule, not the "new body fact" half (`docs/design/spec.md:171-179`)

**VERIFIED by reading both against each other.** The row is accurate as far as it
goes, and I confirmed each of its factual clauses against the code (arm count
from `Side`, travel/reach from `RobotModel`, `SUPERSEDED_BODY_CLAIMS` at
`test_prompt_drift.py:72-74`, no `robot_description` dep in `package.xml:18-24`).

But D30's binding rule has two halves — "**any new or changed** body fact lands
ungated unless its author applies the preference order" *and* "a decision that
supersedes a body fact with no live owner must add a ledger row". The spec row
carries only the second, under a headline that reads as a completed guarantee
("The operating prompt's body claims are gated inside `robot_brain`"). The
concrete case is PR7: the head camera is a **new** body fact with no ledger row
to add, and by both D30 and the class docstring it lands ungated. The reader most
likely to consult `spec.md` rather than `decisions.md` is precisely the PR3–PR7
author this rule exists to bind.

**Fix direction:** one clause — "…a **new** body fact lands ungated unless its
author does this (the head camera, PR7, is the next one); a decision that
*supersedes* a fact with no live owner must add a ledger row."

---

## Test adequacy — **adequate** (explicit verdict)

Re-derived from scratch by mutation on `/tmp/rt67`, not taken from prior passes.
Baseline there: 30 passed. Every mutation was caught, each by the test that owns
it; each was reverted and the control re-run green.

| # | mutation | result |
|---|---|---|
| M1 | `AGENTS.md:3` reverted to "a four-wheel base" | absence **+** descriptor tests fail |
| M2 | descriptor degraded to "3-omniwheel base" (no "holonomic") | descriptor test fails |
| M3 | "two arms with grippers" → "two arms" | arms/grippers test fails |
| M4 | "an extendable vertical column" → "a fixed vertical column" | column test fails |
| M5 | "two arms" → "three arms" (`Side` still 2) | arms/grippers test fails |
| M6 | prompt reach `0.85` → `0.95` | reach test fails |
| M7 | `RobotModel.reach_radius` `0.85` → `0.95` | reach test fails |
| M8 | `max_column_height` → `0.0` (travel collapsed) | column test fails (+5 collateral) |
| M9 | `Side` gains a third member | arms/grippers test fails |
| M10 | "the 4 wheels are fine" hidden inside a fenced example | absence test fails (whole-prompt scan) |
| M11 | the whole opening sentence deleted | 3 tests fail |
| M12 | descriptor left **only** inside a fence in the intro | descriptor test fails |
| D1 | `SUPERSEDED_BODY_CLAIMS` emptied ("PR6 retired it") | 1 failed + **2 skipped** (empty parameter set) — and the ratchet counts a skip as a deletion, so the row cannot be removed quietly |

Two counterfactuals, to prove the load-bearing lines are load-bearing rather than
decorative:

- Swap `introduction()` for raw `PROMPT` in the descriptor test and re-apply M12
  → **30 passed**. The intro-scoping is what makes M12 fail; the docstring's
  claimed asymmetry at `:195-204` is exactly right.
- The ledger's positive/negative controls were re-read against a narrowing edit:
  they are real controls (a typo'd or widened pattern cannot pass both), and the
  test says in its own docstring that they are "a floor, not a proof".

**Honestly-declared residue, independently re-measured green** (i.e. the entry
does not hide what it does not catch): adding a 5th location to the seed world →
the whole `robot_brain` suite unchanged (`known_locations`); `SCHEMA_VERSION`
1 → 2 → 30 passed; retuning the column travel restated inside the worked example
(1.2 → 1.5, safety section untouched) → 30 passed. All three are named in D30, in
the class docstring and in `status.md`'s follow-ups 4/5.

The seven new tests are the +7 in `scripts/test_baseline.json` (50 → 57
non-lint), and the audited run shows `robot_brain 57 +0 ok`.

## Acceptance criteria — both met (judged with my own search terms)

- **AC1** — `AGENTS.md:3-4` reads "a 3-omniwheel holonomic base", matching
  `spec.md:29` and D26/D29 and the three `continuous` wheel joints the URDF
  actually has. Prose, not backticks (R3); no affordance clause (R1); every line
  ≤ 80 columns; the diff is one clause plus a re-wrap.
- **AC2 (sweep)** — my own grep of all of `src/robot_brain/` (`wheel|holonomic|
  omni|camera|lidar|battery|motor|servo|chassis|torso|mast|drivetrain|jaw|
  shoulder|column|arm|gripper|reach|height`). Every surviving body claim is
  either gated or true:
  `AGENTS.md:48` "Raises both shoulders with it" and `:142-144` — true of
  `mock_world.py`'s shoulder arithmetic; `:50` "Close the jaws" — true of
  `spec.md:32`'s **stock SO-101 parallel-jaw** gripper; `:117` "too far from that
  shoulder"; `:201` the `0.85 m reach`, now gated both ways. `agent.py` and
  `openclaw.robot.json` contain no body claim at all. No head-camera claim exists
  to be wrong (`spec.md:33` — reserved link, buy nothing yet).

## Rulings and D30 technical claims re-executed (review targets, not scaffolding)

Every claim below was run, not read:

- **`get_package_share_directory('robot_description')` raises under `colcon test`**
  — the measured fact the whole dependency call rests on. Built the *full*
  workspace copy (all 9 packages, so `robot_description` **is** installed) and
  ran a probe test through real `colcon test --packages-select robot_brain`:
  `AMENT_PREFIX_PATH=/tmp/rt67/install/{robot_brain,robot_mcp,robot_safety,robot_backends,robot_world,robot_skills}:…/.pixi/envs/default`
  → `RAISED PackageNotFoundError: "package 'robot_description' not found"`.
  **The ruling is correct and correctly reasoned.**
- **"nothing enforces it — a test-time ROS import here is unguarded"** — added
  `import ament_index_python` to `test_prompt_drift.py`: `test_no_ros_runtime`
  **3 passed**, drift suite **30 passed**. True, and honest about being a design
  call rather than an invariant.
- **"the URDF carries three wheel joints but the words … only in a comment"** —
  `base.xacro` has `omniwheel`/`holonomic` at lines 3, 7, 43, 51, 149 (all
  comments) and the macro name `omni_wheel` at :168; the joints are
  `base_{left,back,right}_wheel`. True.
- **"`RobotModel` carries no drivetrain field"** — true (`mock_world.py:82-87`);
  so is "a URDF-backed `RobotModel` would still own nothing the ledger covers".
- **"without a new dependency edge on `robot_brain`"** — `package.xml:18-21`
  already test-depends `robot_backends`. True.
- **R1 / R3 / R4** — upheld: `navigate_to(location: str)` is still the only base
  command, no new backticks, minimal diff.
- **The live-source enumerations** (`test_prompt_drift.py:14-18`,
  `README.md:70-75`) — checked in **both** directions this time. Every source
  named is read by an assertion in that module: `TOOL_NAMES`/`TOOLS` (`:316,:355`),
  schemas (`:355,:377`), `SafetyLimits.defaults()` (`:390`), `default_world()`
  (`:433,:443`), `RobotModel` (`:281,:299`), `Side` (`:267-268`), `FailureCode`
  (`:417` two-way, `:408,:423`), and "the rest" — `SkillStatus`, `GripperState`
  — genuinely feed `live_vocabulary()` (`:136-137`). Pass E's NOTE 1 is closed.
- **`spec.md`'s D30 row** — accurate standalone and consistent with the entry
  after both rewrites; NOTE 3 is a completeness gap, not a contradiction.

## Worktree state

Clean. `git status --porcelain` empty at start and finish; `scripts/test_baseline.json`
unchanged by the audited run (`+0`); only `build/`, `install/`, `log/`
(gitignored) touched. Every perturbation ran under `/tmp/rt67`, including a
separate `colcon build`/`colcon test` workspace built there for the ament-index
probe.

## N+1 status

This pass follows a pass that found 1 BLOCK + 2 NOTES and cleared them, and it
found a **new** BLOCK in the same paragraph — so the "clean pass follows a clean
pass" bar is **not** met. After BLOCK 1 lands, one more full pass is owed before
"ready", and it should re-execute D30 end to end rather than the fix diff: this
is the fourth defect found in that one entry, and three of the four were
introduced or preserved by an edit that was itself a fix.
