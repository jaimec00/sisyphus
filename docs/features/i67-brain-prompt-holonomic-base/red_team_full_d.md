# Red-team — full pass D (#67), after the round-4 fixes

**Scope:** the entire `origin/main..HEAD` diff at `d3abd98` (rebased on
`1a8472b`), not the fix diff. Findings were formed by reading + executing
*before* the five prior reports were opened; those were read afterwards only to
suppress re-reporting.

**Result: 0 BLOCKs, 6 NOTES.**

**N+1 status — this is not yet the bar.** This pass immediately follows a batch
of fixes (`e89b187`, `7518968`, `7e97a1f`, `d3abd98`), so by
`.claude/agents/red-team.md` it proves *the fixes landed*, not that the code is
sound. One more full pass, on an unfixed tree, is what makes an empty BLOCK list
trustworthy. Nothing in this report needs a code or doc change to get there.

---

## 0. Baseline — VERIFIED

```
$ pixi run test
Summary: 763 tests, 0 errors, 0 failures, 0 skipped
robot_brain  60 tests  0 skipped  0 errors  0 failures  non-lint 57  vs-base +0  ok
10 packages, 763 tests collected, 733 of them non-linter
AUDIT PASSED   All stages passed.
```

Ratchet is correct: `scripts/test_baseline.json`'s `robot_brain: 57` is exactly
the non-lint count the auditor computes (`+0`), and 50 → 57 is the seven tests
`TestBodyDescription` adds. Every other package is `+0`.
`git status` clean before and after.

## 1. Acceptance criteria — both met, audited independently

**AC1 (the false claim is gone, the real body described).** `AGENTS.md:3-4` now
reads "a 3-omniwheel holonomic base", matching `spec.md:29`'s
`LeKiwi 3-omniwheel holonomic` and D26/D29's vocabulary, in plain prose (R3
satisfied — no new backticks, so `live_vocabulary()` is untouched).

**AC2 (sweep the rest of `src/robot_brain/`).** My own sweep, with my own terms
(`wheel|holonomic|chassis|omni|lidar|camera|rgb|depth|sensor|torso|head|elbow|claw|servo|feetech|so-101|lekiwi|payload|battery|dof|joint|column|gripper|arm|four|three|dual`)
over every tracked file in the package: the only body-description prose in
`src/robot_brain/` lives in `openclaw/AGENTS.md` and in `README.md`'s
description of the gate. `agent.py`, `__init__.py`, `openclaw.robot.json`,
`setup.py` and `package.xml` make no claim about the machine. A repo-wide sweep
for surviving `four|4[ -]?wheel` claims finds only decision-log history
(`decisions.md:7,76,78,84`), `spec.md:29`'s correct "supersedes D1's 4-wheel",
`test_description.py:495`'s comment, and this feature's own ephemeral docs — no
live asset still asserts it.

Round 4's ruling that AC2 was *not* met until the arm/gripper/column claims were
pinned was right, and it is met now: all three are asserted, and each fails on
its drift (§2). The residual body claims inside the prompt — "Raises both
shoulders with it" (`AGENTS.md:48`), "too far from that shoulder" (`:117`),
"Raising the column raises the shoulders" (`:142`) — are true of
`mock_world.py`/`mock_backend.py` and are consequences of facts already pinned
(two arms, a column with travel). No further unread body-description claim.

## 2. Test adequacy — **adequate**, verified by mutation

All mutations run on a `cp -r` copy under `/tmp/rt67`, never in the worktree.

| mutation | result | expected |
|---|---|---|
| `AGENTS.md:3` → "three arms with grippers" | RED `test_the_arms_and_their_grippers_are_the_ones_the_robot_serves` | RED |
| `AGENTS.md:3` → drop "with grippers" | RED same test | RED |
| `AGENTS.md:3` → "a **fixed** vertical column" | RED `test_the_column_is_called_extendable_while_the_model_gives_it_travel` | RED |
| `AGENTS.md:201` → "beyond the 0.9 m reach" | RED `test_the_reach_the_examples_quote_is_the_live_one` | RED |
| `mock_world.py:84` `reach_radius` 0.85 → 0.9 | RED same test | RED |
| `mock_world.py:87` `max_column_height` 1.20 → 0.0 | RED column test (+ collateral) | RED |
| re-wrap the body sentence over four lines, text unchanged | **GREEN** | GREEN (H3 fix works) |
| descriptor surviving only inside a fenced note in the intro | RED `test_the_descriptor_that_replaced_it_is_taught` | RED (fence scoping intact) |
| `four-wheel` planted in a fenced note in the intro | RED `test_a_superseded_body_claim_is_not_still_taught` | RED |
| `four\nwheels` straddling a wrap | RED same test | RED |
| a fenced block in the intro whose body contains a `## ` line | intro scoping **not** truncated (M15 GREEN, M14 RED on the planted claim) | correct |

So the H3 fix does what it claims *and* did not weaken either of the other two
things `introduction()` does: fences are dropped **before** the `\n## ` split
(so a fenced `## ` line cannot truncate the intro) and the collapse happens
**after** both.

**`COUNT_WORDS`'s `KeyError` is loud enough.** Removing `2: 'two'` to simulate a
count it cannot spell:

```
>       assert f'{COUNT_WORDS[len(Side)]} arms' in introduction()
                  ^^^^^^^^^^^^^^^^^^^^^^
E       KeyError: 2
test/test_prompt_drift.py:266: KeyError
```

pytest prints the failing expression, the file:line and the docstring that
explains the design. That is a legible failure, not a confusing one — the
docstring's claim holds.

**Every docstring in `TestBodyDescription` checked against behaviour, and each
is true.** In particular `test_the_matcher_catches_the_spellings_a_literal_list_
did_not`'s three-way grouping is exactly right — I re-ran the *old* literal list
`('four-wheel','4-wheel','four wheel')` against all nine controls: it MISSED
`4 wheels`, `a 4 wheel base` and `four  wheel base`, CAUGHT the other five, and
`The base is four-wheel.` is the punctuation control F2 added, not a miss.

## 3. D30 and spec.md — technical claims audited by execution

Every falsifiable claim in the rewritten D30 entry was checked. **All the
load-bearing ones are true**, including the three that decide the design call:

- **`get_package_share_directory('robot_description')` raises under
  `colcon test` — VERIFIED.** I built a throwaway copy of the whole workspace at
  `/tmp/rt67ws`, added a probe test to `robot_brain/test/`, and ran
  `colcon test --packages-select robot_brain`:
  ```
  PROBE_RESULT: PackageNotFoundError "package 'robot_description' not found, searching:
    ['…/install/robot_brain', '…/robot_mcp', '…/robot_safety', '…/robot_backends',
     '…/robot_world', '…/robot_skills', '…/.pixi/envs/default']"
  ```
  The check genuinely needs a `<test_depend>`. D30's central measured fact holds.
- **"nothing enforces it (a test-time ROS import here is unguarded)" — VERIFIED.**
  The same run, full suite: `test/test_no_ros_runtime.py ...` — three passes with
  an `ament_index_python` import sitting in a sibling test file.
  `FORBIDDEN_ROOTS` is `('rclpy',)` and the walk is over `robot_brain.__file__`'s
  directory, not `test/`.
- **"the URDF holds the wheel count structurally but 'omniwheel'/'holonomic'
  only in a comment" — VERIFIED.** In `urdf/base.xacro` both words appear only
  inside `<!-- -->` (lines 3, 7, 43, 51, 149).
- **`known_locations`, `schema_version` and the worked example's column travel
  are owned and unasserted — VERIFIED by mutation**, all three green:
  adding `"attic"` to the prompt's fenced `known_locations`, bumping
  `schema_version` to 7, and rewriting `AGENTS.md:218-219` to
  `[0, 9.9] m … clamped to 9.9 m` each leave all 30 prompt tests passing.
- **`4 wheels` really is D26's own spelling** (`decisions.md:78`, *"the '4
  wheels' aesthetic of D1"*), and D1 spells it `4-wheel base`.
- **`robot_description`'s own gate exercises the install tree and xacro** —
  `test_description.py:60-67` resolves everything through
  `get_package_share_directory` with "no source-tree fallback, by design", and
  drives the `xacro` CLI.
- **`robot_brain`'s test deps are exactly the four cross-seam packages** —
  `package.xml:18-21`.
- **The spec.md row** is in the established `## Ops & gates` shape (bold lead,
  concrete artifact path, trailing `(D30)`), is consistent with the entry it
  summarises, and correctly carries the one thing PR3–PR7 authors must know.

The NOTES below are the residue — all prose, none of it load-bearing, none of it
a BLOCK.

---

## NOTE 1 — the one rule D30 exports to `spec.md` is stated wider than the rule it just set (`docs/design/decisions.md:120`, `docs/design/spec.md:175-177`)

D30:119 sets the rule in order of preference: *"find the claim's owner and read
it; a ledger row is the fallback for a claim that genuinely has none."* D30:120
then states as a mechanical consequence *"a decision that supersedes a body fact
**must** add a ledger row, since nothing detects the need"* — and `spec.md:175-177`
bolds exactly that half, which is the only part of D30 that survives into the
file agents are told to read first.

For a body fact **with** a live owner the second sentence is false, and I can run
the counter-example: if `Side` grew to three, `test_the_arms_and_their_grippers_
are_the_ones_the_robot_serves` goes red (VERIFIED — the "three arms" mutation
above is the same edge from the other side), so something *does* detect the
drift and no ledger row is needed. Followed literally for the column, it is
actively self-contradictory: a row `{'fixed column': r'\bextendable\b'}` asserts
"extendable" absent from the prompt while
`test_the_column_is_called_extendable_while_the_model_gives_it_travel` asserts it
present in the introduction.

**Why NOTE and not BLOCK.** The error is in the safe direction (it asks for more
gating than needed), and the contradiction it can produce is a loud test failure
in the same edit, not a silent hole. There is even a real argument for the wide
form: the absence check scans the *whole* prompt while the live checks are
intro-scoped, so a row does catch a stale spelling lingering in a worked example
that the live check would miss — which is the honest justification, and is not
the one given.

**Fix direction** (if D30 is touched again before merge; it is append-only after
that): scope both sentences — "a decision that supersedes a body fact **with no
live owner** must add a ledger row; nothing detects that need. Where the fact has
an owner the suite goes red on its own, and a row is optional belt-and-braces
against a stale spelling outside the introduction." Same edit in `spec.md:175-177`.

---

## NOTE 2 — "Those PRs each add body prose to this same sentence" is not what PR3–PR7 are, and consequence #1 is defeated by PR6 (`docs/design/decisions.md:120`)

**VERIFIED against `docs/design/urdf-mjcf-pr-breakdown.md:83-147`.** PR3 is the
column, PR3.5 the head-camera link, PR4 the arm macro, PR5 the grippers, **PR6
`RobotModel` reads from the URDF**, **PR7 MJCF derivation**. Two problems:

1. PR6 and PR7 add no body prose to `AGENTS.md` at all, and PR3/PR4/PR5 add
   *URDF geometry* — the prompt's opening sentence already names the column, the
   arms and the grippers. The only genuinely new prose fact in the range is
   PR3.5's head camera, which the entry does name. "Each add body prose to this
   same sentence" reads as a fact and is a loose prediction.
2. The first mechanical consequence — *"a fact whose owner is `robot_description`
   cannot be read without reversing this decision's dependency call"* — is
   exactly what **PR6 dissolves**: once `RobotModel` is populated from the URDF,
   `robot_brain` reads URDF-owned facts through `robot_backends` with no new
   edge. That is also the un-named path by which the drivetrain gets a live
   source on the brain's side and the ledger row retires — the thing D30 says it
   wants ("recorded so the next PR can reverse it on evidence") without naming
   the PR that will do it.

**Fix direction:** name PR3.5 as the one new prose fact, and add a clause
pointing at PR6 as the reversal path — it strengthens the entry rather than
weakening it.

---

## NOTE 3 — the module docstring still *opens* with the overclaim it was rewritten to retire (`src/robot_brain/test/test_prompt_drift.py:14-16`)

> "So **every** checkable claim the prompt makes is compared here against the
> **live** source that owns it…"

That is false — `known_locations` is a checkable claim with an owner and no
assertion (VERIFIED green under mutation, §3). Lines 19-22 immediately walk it
back ("It is an aim, not an invariant"), which is why this is a NOTE and not the
BLOCK B2/F1/G1 were. But it is the same sentence-shape that has now been fixed
three times in three files, left standing in the file whose subject is exactly
that defect. Pass C gave the README's analogous sentence the same NOTE treatment
(`red_team_full_c.md:217`); this one is one hedge further away from its qualifier.

**Fix direction:** one word — "So the checkable claims the prompt makes are
compared here against the live source that owns each…". The following sentences
already do the rest.

---

## NOTE 4 — the body checks are introduction-scoped, not sentence-scoped (`test_prompt_drift.py:266-267`, `:281`)

`'gripper' in introduction()`, `'extendable' in introduction()` and
`f'{…} arms' in introduction()` are satisfied by *any* occurrence in the intro,
not by the body sentence. Today the intro is two paragraphs and each phrase
occurs once, so the checks bite (all three mutations RED). The failure mode needs
two edits: a later PR adds an intro paragraph mentioning a gripper or the column,
and *then* the body sentence loses the clause — undetected.

Related, and **VERIFIED** as the one price of the H3 fix: because fences are
removed and *then* whitespace collapsed, a fenced block inserted into the middle
of the body sentence lets the two halves rejoin. Splitting the sentence as
`a 3-omniwheel\n\n```\nnoise\n```\n\nholonomic\nbase` leaves the descriptor test
**green**. Strictly less likely than the re-wrap H3 fixed, and not worth undoing
that fix for.

**Fix direction (follow-up sized, belongs with follow-up 6bis):** scope the
phrase checks to the sentence that contains the ledger descriptor rather than to
the whole introduction — e.g. take the descriptor's sentence out of
`introduction()` and assert against that. Cheap, and it makes all three checks
absolute rather than "somewhere nearby".

---

## NOTE 5 — `spec.md:15` still says "Flattened through **D28**" while the file carries D29 and D30 rows

**Pre-existing, not this PR's defect** — `spec.md:110,120,137` already cited D29
under a D28 header when this branch was cut (#66 landed it that way). D30 makes
it one decision staler. The implementer's refusal to bump a whole-file assertion
it had only spot-checked is the right instinct and the manager was right to
endorse it (`status.md:23-29`); the resulting inconsistency should just be routed
outward rather than left silent, since "the flattened HEAD of every decision" is
the file's own claim about itself.

**Fix direction:** a follow-up on the issue — re-flatten `spec.md` through D30 and
bump line 15, as its own small ops change.

---

## NOTE 6 — the "live sources" enumerations omit `robot_skills` (`test_prompt_drift.py:15-16`, `README.md:70-72`)

Both lists name `robot_mcp`'s catalogue, the tool schemas, `robot_safety`'s
limits and `robot_backends`' seed world / `RobotModel`. The arm count is now read
from `Side`, which is `robot_skills` — as are `FailureCode`, `GripperState` and
`SkillStatus`, all already feeding `live_vocabulary()`. Four of the five
cross-seam test deps are listed; the fifth, which this PR made load-bearing for a
body claim, is not. Cosmetic, but these two lists are what a reader uses to
decide where a new expected value should come from.

---

## Claims I checked that produced no finding

- `introduction()`'s three jobs (fence-drop → intro-split → collapse) are in the
  only order that works, and each is independently exercised (M11, M14, M15, the
  re-wrap case).
- The absence pattern `\b(?:four|4)[\s-]+wheel` matches all nine positive
  controls and none of the four negatives; `[\s-]+` eats a newline, so the wrap
  cannot hide a retired claim (VERIFIED).
- `test_the_reach_the_examples_quote_is_the_live_one` is a genuine two-way set
  comparison: a changed model number, a changed prompt number and a reworded-away
  phrase (empty set) all fail. Only one `N m reach` occurrence exists in the
  prompt, so the set does not accidentally collapse.
- `MockBackend` really does emit exactly one gripper observation per `Side`
  member (`mock_backend.py:197` over `SIDE_ORDER`), so the class docstring's
  justification is true.
- R1 (state the body, not the manoeuvre) survives: the shipped sentence adds a
  body noun and no capability sentence, and `navigate_to(location)` remains the
  only base command.
- R4 (surgical edit) survives: the change is a one-clause word-diff plus the
  re-wrap it forces; no prose line exceeds 80 columns.
- `RobotModel.reach_radius = 0.85` vs D26's SO-101 is a real disagreement, but it
  is between `mock_world.py` and `decisions.md` — the prompt agrees with its own
  owner. The manager's deferral (follow-up 2) is correct; fixing it here would be
  a `robot_backends` change with safety/mock fallout.
- The `known_locations` deferral is correct: it is a world-description claim, and
  reading AC2's "body-description" as "any factual claim" makes the sweep
  unbounded. Already routed as follow-up 4.
- Round 4's H1–H4 are all closed, each re-verified independently here rather than
  taken from `status.md`.

## Worktree state

Clean. `git status --porcelain` empty before and after; `pixi run test` touched
only `build/`, `install/`, `log/` (gitignored) and left `scripts/test_baseline.json`
unchanged at `+0`. All mutation work happened in `/tmp/rt67` and `/tmp/rt67ws`,
which are outside the repo and can be deleted at will.
