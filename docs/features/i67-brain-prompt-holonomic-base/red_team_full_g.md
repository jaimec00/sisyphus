# Red-team pass G — full adversarial pass on `origin/main..HEAD` (`524f2f3`)

Confirming pass after the manager's unreviewed corrections in `524f2f3`.
Findings formed before reading any prior report; prior reports consulted
afterwards only to dedupe and to audit `524f2f3`'s self-referential count.

**Verdict: 2 BLOCKs, 4 NOTES.** Both BLOCKs are false factual claims in the
permanent docs, both introduced by `524f2f3` itself, and neither is in code.
**The code and the tests are clean**: full suite green (763 tests, 0 failures,
0 skipped, ratchet `+0`, `robot_brain` 57), fifteen mutations each caught by the
test that owns it, and D30's load-bearing *measured* claim
(`get_package_share_directory('robot_description')` raises under `colcon test`)
re-verified from scratch in a rebuilt copy of the workspace.

**N+1 status: clean-follows-clean is NOT met.** Pass F found 1 BLOCK; the fix
for it introduced a new one (BLOCK 1 below) and a second (BLOCK 2). One more
full pass is owed after these land.

---

## BLOCK 1 — D30's re-anchored timeline is wrong again: "for two days after D26" is **ten hours** by the record, and it contradicts this branch's own test docstrings (`docs/design/decisions.md:115`)

**VERIFIED** — `git log`, `gh pr view 66`, `gh issue view 67`.

The sentence `524f2f3` installed:

> the prompt went on telling the planner it drove "a four-wheel base" **for two
> days after D26 retired that base**, and it became false about a robot that
> *existed* the moment #66 merged. Nothing went red. **The gap between the merge
> and the correction was seventeen minutes** …

The second number is right; the first is not. Every anchor available in the
record:

```
$ git log --format='%h %ad %s' --date=iso-strict -S "D26 — Every actuated joint" -- docs/design/decisions.md
5fb6f21 2026-08-13T10:55:44-04:00 docs: record D26 hardware architecture …   # D26 lands on main
$ git log -1 --format='%h %ad %s' --date=iso-strict 150dd12
150dd12 2026-08-13T21:07:00-04:00 brain: the prompt's body is the body we built  # "the correction"
$ git log -1 --format='%ad' --date=iso-strict 3a0958c        # #66 merged
2026-08-13T20:49:37-04:00
$ grep -n "^## 2026-08-12 — Hardware architecture" docs/design/decisions.md
74:## 2026-08-12 — Hardware architecture: … (supersedes the base + lift specifics of D1)
```

- Anchored on **D26 as the record has it** (the commit that put D26 on `main`,
  `5fb6f21`): 10 h 11 m. Same calendar day as the correction.
- Anchored on the **section-header date** `2026-08-12` (the decision session,
  the most generous reading): between 21 h and 1 d 21 h — still under two days,
  under *any* hour of that day.
- The entry's own second sentence fixes the end anchor: "the correction" is
  `150dd12`, 17 m 23 s after the merge. So both durations in the paragraph
  measure to the *same* event, and they cannot be 17 minutes and two days from
  points that are themselves 10 hours apart.

**It also contradicts the branch's own source, on the identical anchor.**
`src/robot_brain/test/test_prompt_drift.py:61` ("the prompt kept saying
'four-wheel' **for a day**") and `:182` ("described D1's four-wheel base **for a
day after D26**") were examined by pass F, ruled correct *as anchored on D26*
(`red_team_full_f.md:56-66`, "Do **not** touch `test_prompt_drift.py:61,182`"),
and left alone. `524f2f3` adopted pass F's proposed re-anchor to D26 and then
doubled the duration that had just been certified against it. The branch now
ships two different durations for one fact, and the one in the append-only,
doc-outranking artifact is the wrong one.

**Failure scenario.** `decisions.md` is append-only and wins over every other
doc. A reader in three months — most plausibly whoever takes routed follow-up 1
(the unguarded-prose issue) and has to argue how much gating prose deserves —
reads D30 and takes away "a live system prompt was false for two days". They
then hit `test_prompt_drift.py:182` saying one day, on the same anchor, and now
neither number is trustworthy. The true story is sharper than both and is the
entry's best evidence for itself: the prompt became false the instant the base
merged, it was corrected 17 minutes later, and the *only* reason it was
corrected is that a human reviewing #66 filed #67 **sixteen seconds** after the
merge (`gh issue view 67 --json createdAt` = `2026-08-14T00:49:53Z`; `gh pr view
66 --json mergedAt` = `2026-08-14T00:49:37Z`). D29's neighbouring entry closes
with "a decision entry that rounds its own evidence up is the failure this PR
already had to fix once"; this rounds its own evidence up by a factor of ~4.7.

**Fix direction.** Two clauses, one edit:

1. Make the duration true and consistent with `:61`/`:182` — either drop it
   ("…went on telling the planner it drove 'a four-wheel base' after D26 retired
   that base…") or state what the commit record shows ("…for the ten hours
   between D26 landing on `main` and this PR, and it became false about a robot
   that *existed* the moment #66 merged"). If the duration is kept anywhere,
   `:61`/`:182` must be made to agree with it in the same commit.
2. **The same edit must fix the tally in the closing rationale**, or it creates
   the next instance: "the same defect **ten** times" and "**Twice** a fix for
   the defect introduced a fresh instance of it" both become stale the moment
   this one is counted (eleven, and three). Every numeral in this entry has now
   been wrong at least once — `seven kinematic constants`, `a day`, `two days`.
   The safe form is to stop counting: keep the enumeration ("a docstring, its
   replacement, the class docstring twice, a test's claim about its own
   controls, the package README, and this entry itself, repeatedly — twice in a
   correction to it") and delete the arithmetic that has to be maintained
   against a history nobody will re-derive.

---

## BLOCK 2 — `spec.md`'s D30 row names the wrong PR for the head camera: the roadmap gives it to **PR3.5**, and this row points the rule at the author of PR7 (`docs/design/spec.md:179`)

**VERIFIED** — by reading the roadmap and `spec.md`'s own Body table.

The clause `524f2f3` added:

> The same applies to a body fact the prompt gains for the first time — **PR7's
> head camera is the next one** — which lands **ungated** unless its author reads
> it from an owner or pins it.

The roadmap owns that fact and assigns it elsewhere:

```
$ grep -n -i "camera" docs/design/urdf-mjcf-pr-breakdown.md
90:### PR3.5 — Head camera link + optical frame  *(decided D26)*
91:- Mount a `head_camera_link` on `column_top` …
99:- **Test:** `head_camera_link` + optical frame present; …
142:- **Includes the head RGB-D sensor** (D26): model the camera at …   # PR7 = MJCF sensor
157:               └─► PR3.5 head camera link (after PR3; arm-independent)
```

The head camera **arrives at PR3.5**, immediately after PR3 and before PR4–PR6;
PR7 only overlays the MJCF *sensor* on a link that has existed for four PRs by
then. `spec.md` already says so itself, 146 lines earlier — line 33: "**One
head-mounted RGB-D.** URDF reserves a `head_camera_link` + REP-103 optical frame
on `column_top`". So the file now contradicts itself about which PR owns the
camera.

**Failure scenario, and it is the exact one the clause was added to prevent.**
D30's binding rule has no enforcement — it binds *the author of the PR that adds
the body fact*, by being read. `spec.md` is the file that author is most likely
to read (it is the flattened current state; `decisions.md` is the long-form
log). It tells them the next ungated body fact is PR7's. PR3.5 then lands the
head camera with nothing said, four PRs early, and the rule misfires exactly as
designed to fire — the D30 row is the only place in `main` that will name the
next case, and it names the wrong one.

Worth recording for calibration, because it is this run's signature defect one
more time: the wrong attribution was **transcribed from pass F's fix direction**
(`red_team_full_f.md:174`, "(the head camera, PR7, is the next one)"), which was
a NOTE about the *rule's missing half* and was VERIFIED for that claim only. A
finding labelled VERIFIED is verified for its own claim, not for the incidental
detail you lift out of it — the entry says so itself, one file away.

**Fix direction.** One token: "PR3.5's head camera is the next one". Or, safer
against roadmap re-planning and matching what D30 itself does (it names no PR
number for the camera): "…a body fact the prompt gains for the first time — the
head camera is the next one — lands **ungated** unless…".

---

## NOTE 1 — `status.md:26` still carries the "seven kinematic constants" miscount that `decisions.md` just dropped

`docs/features/…/status.md:25-27`: "`urdf-mjcf-pr-breakdown.md:129-137` scopes
PR6 to *seven kinematic constants* (shoulder offsets, reach, column travel, home
offset)". Its own parenthetical enumerates six, and `mock_world.py:82-87` has
six fields. `524f2f3` corrected `decisions.md` to "only `RobotModel`'s
*kinematic* constants" (**verified correct**: six fields, all kinematic, no
drivetrain field — `grep -n "class RobotModel" -A 20`) but left the manager
record asserting the number it just retired. `docs/features/` is deleted at
merge, so this cannot reach `main` — but the PR description and the retro are
written from this file. NOTE, not BLOCK, on that ground alone.

## NOTE 2 — "the last two were authored by the reviewer enforcing the rule" is true only under one of two readings (`docs/design/decisions.md:118`)

The four D30 instances, by authorship (`git show` on each commit):

| instance | introduced in | author role |
|---|---|---|
| "compares every prompt claim that has an owner" (H1) | `5837211` (original entry) | implementer |
| "for a day after the base was built and gated" | `5837211` (original entry) | implementer |
| "PR6 dissolves that" | `d7f0f9a` (a correction) | manager |
| "the seven kinematic constants" | `edb2221` (a correction) | manager |

"Twice a fix for the defect introduced a fresh instance" is **exactly right**
(rows 3 and 4). "The last two were authored by the reviewer" is right if "last
two" means *last two authored*; it is wrong if it means *last two found* — the
timeline claim was found last (pass F) and was written by the implementer in the
original entry. Since the sentence sits immediately after a discovery-ordered
narrative, the wrong reading is the available one. Also: in this repo "the
reviewer" is the red-team, which is read-only and authors no decision prose; the
author here was the manager. Both are one-word fixes ("both fix-introduced
instances were the manager's"), and both should be re-checked anyway when
BLOCK 1's tally edit lands.

## NOTE 3 — a second ledger row forces its key into the prompt's *introduction*, which will go red on a correct prompt (`test_prompt_drift.py:72-74,191-207`)

**VERIFIED by mutation** (on `/tmp/rt67`, a `cp -r` copy):

```
+    'linear-rail lift': r'\bbelt[\s-]+driv',     # a plausible D26 supersession row
=> FAILED test_the_descriptor_that_replaced_it_is_taught[linear-rail lift]
   1 failed, 31 passed
```

`test_the_descriptor_that_replaced_it_is_taught` requires every ledger **key** to
appear verbatim in `introduction()`. That is right for the drivetrain, whose
descriptor is in the opening sentence — but the ledger's documented rule is "add
a row when a decision supersedes another body fact", and most body facts (the
gripper type, the column's drive, the camera) are not named in that sentence and
have no business being forced into it. The next author gets a red suite on a
correct prompt and will either contort the key or delete the assertion.

Failure is **loud and at extension time**, not silent, so NOTE. It belongs with
routed follow-up 7 ("a second ledger row would ship without controls") — same
area, same trigger, and the fix shapes are compatible (a per-row record carrying
its pattern, its controls, and whether its descriptor is an *introduction*
claim).

## NOTE 4 — the surviving follow-up list has a hole in its numbering (`status.md:342-358`)

Items run 1, 2, 3, 4, 5, **6bis**, 7, 8, **10, 11, 12, 9** — there is no item 6
(only a "6bis" implying one), and 9 is stranded after 12. This list is the
*only* part of `status.md` that survives merge (it is routed to the issue;
`docs/features/` is deleted). The manager has already recorded losing follow-ups
once in this run for a routing reason (`status.md:337-340`, "I routed follow-ups
from an agent's summary … and dropped two NOTES"). Renumber before routing, or
route by title rather than by number.

---

## Test adequacy — **ADEQUATE** (explicit verdict)

Re-derived from scratch by mutation, not inherited. Baseline on the copy: 30
passed. Every mutation reverted afterwards; the control re-ran green each time.
Nothing was mutated in the worktree.

| # | mutation | result |
|---|---|---|
| M1 | `AGENTS.md:3` reverted to "a four-wheel base" (the issue's own regression) | absence **+** descriptor tests fail |
| M2 | descriptor → "a wheeled base" (stale claim *not* reinstated) | descriptor test fails |
| M3 | "two arms with grippers" → "two arms" | arms/grippers test fails |
| M4 | "an extendable vertical column" → "a vertical column" | column test fails |
| M5 | `RobotModel.reach_radius` 0.85 → 0.95 | reach test fails, **and only it** |
| M6 | `max_column_height` → 0.0 (travel collapsed) | column test fails (+5 collateral) |
| M7 | `Side` gains a third member | arms/grippers test fails (`'three arms'` absent) |
| M8 | `Side` gains a **fourth** member | `KeyError: 4` — the docstring's "right kind of loud" claim is true |
| M9 | "four-wheel" hidden in a fenced note under "Worked examples" | absence test fails (whole-prompt scan) |
| M10 | intro sentence re-wrapped across four lines (a *correct* prompt) | **30 passed** — no false positive |
| M11 | ledger pattern narrowed to `\b(?:four\|4)-wheel` (the literal list's old hole) | positive-control test fails |
| M12 | ledger pattern widened to `\b(?:four\|4)` | negative-control test fails |
| M13 | second ledger row added | NOTE 3 above |

All seven `TestBodyDescription` tests are load-bearing: each has a mutation that
only it catches, and the two control tests catch both directions of a pattern
edit. M5 also confirms D30's claim that "retuning `reach_radius` used to leave
the entire suite green" — with the new test removed, nothing else fails.

**Enumeration audit** (`test_prompt_drift.py:14-18`, `README.md:70-73`): every
source named is genuinely read by an assertion — `TOOL_NAMES` (`:316`), each
tool's `input_schema` (`:355`), `SafetyLimits.defaults()` (`:397`),
`default_world()` (`:438,449`), `RobotModel` (`:282,301`), `Side` (`:268`),
`FailureCode` (`:417`), and `SkillStatus`/`GripperState`/`NavigateTo` feeding
`live_vocabulary()` (`:136-147`). The one live source read but not named is
`MockBackend`'s observation (the one-gripper-per-side read at `:266`); it is
named in the class docstring instead, and both prose lists now hedge as an aim
rather than a guarantee, so this is not an over-claim.

## D30's technical and measured claims, re-executed

| claim | result |
|---|---|
| `get_package_share_directory('robot_description')` raises `PackageNotFoundError` under `colcon test` | **VERIFIED.** Full `colcon build` + `colcon test --packages-select robot_brain` in a rebuilt copy at `/tmp/rt67ws` — where `robot_description` **is** built and installed — probe output: `RAISED PackageNotFoundError: "package 'robot_description' not found, searching: [robot_brain, robot_mcp, robot_safety, robot_backends, robot_world, robot_skills, .pixi/envs/default]"`. The dependency-scoped `AMENT_PREFIX_PATH` is the mechanism, so the claim holds structurally, not by accident of this tree. |
| `test_no_ros_runtime` does not stop a **test-time** ROS import | **VERIFIED.** `test_no_ros_runtime.py:23-38` probes only what `import robot_brain` loads in a bare subprocess; `:101-118` walks `os.path.dirname(robot_brain.__file__)`, i.e. the runtime package, not `test/`. My probe imported `ament_index_python` inside `test/` and nothing objected. |
| the URDF carries the wheels structurally but "omniwheel"/"holonomic" only in a comment | **VERIFIED.** `base.xacro` matches at `:3,7,43,51,149`, all inside `<!-- -->`; the three `continuous` wheel joints come from the macro at `:169`. |
| `RobotModel` carries no drivetrain field, so PR6 cannot retire the ledger | **VERIFIED.** `mock_world.py:82-87` — six kinematic fields, no wheel/base/drive term anywhere in the class. The corrected wording ("only `RobotModel`'s kinematic constants — shoulder offsets, reach, column travel, home offset") is true and carries no numeral. |
| "the seventeen minutes … only because a human read the diff and filed #67" | **VERIFIED.** #66 merged `2026-08-14T00:49:37Z`; #67 filed `2026-08-14T00:49:53Z` (16 s later); `150dd12` at `2026-08-14T01:07:00Z` (17 m 23 s after the merge). |
| "no test, no gate and no CI check was capable of noticing" | **VERIFIED.** The vocabulary check reads `` `backticked` `` spans only (`brain_fixtures.py:56,74-79`); `main` is green today *with* the false sentence; GitHub CI runs only the docs-clean guard. |

## Acceptance criteria — both met (my own search terms)

- **AC1.** `grep -ci "four-wheel\|4-wheel\|4 wheel" AGENTS.md` → `0`; line 3 reads
  "a 3-omniwheel holonomic base", matching `spec.md:29`'s exact term, in plain
  prose (no backticks, so `live_vocabulary()` is not disturbed — R3 holds). The
  regression is gated in both directions (M1).
- **AC2.** Independent sweep of `src/robot_brain/**` for body nouns
  (`wheel|chassis|holonomic|omni|drivetrain|base|column|arm|gripper|jaw|camera|
  sensor|lidar|torso|head|servo|motor`): the only body-description claims are the
  opening sentence (all four clauses now gated — drivetrain by ledger, arms and
  grippers by `Side` + the Mock, column by `RobotModel`, reach by `RobotModel`),
  `AGENTS.md:48` "Raises both shoulders with it" (true against
  `mock_world.py`'s kinematics, behavioural rather than body-descriptive), and
  the safety numbers (gated against `SafetyLimits` since before this PR). The
  README's body prose was corrected in `7518968`/`d7f0f9a` and is now accurate.
  No false body claim survives anywhere in the package.

## Suite and worktree state

```
$ pixi run test
Summary: 763 tests, 0 errors, 0 failures, 0 skipped
robot_brain  60 tests, 57 non-lint, vs-base +0, ok
AUDIT PASSED: every expected package collected tests / All stages passed.
$ git status --porcelain      # after every experiment
(empty)
```

`scripts/test_baseline.json` still reads `"robot_brain": 57`; the ratchet did not
rewrite it (`+0`). All perturbation ran on `cp -r` copies at `/tmp/rt67` and
`/tmp/rt67ws`; nothing in the worktree was edited.

## What I did not re-litigate

Closed by earlier passes and re-confirmed only where cheap: R1's
describe-the-body-not-the-manoeuvre ruling, R2's conclusion (declining the
`robot_description` test-depend — now backed by an executed measurement rather
than a fourth rationale), R3/R4, the `known_locations` deferral as a
*world*-description claim outside AC2, and the eight routed follow-ups. The
`_FENCE`/Unicode-dash blind spots and the ledger's false-positive surface are
already routed (follow-ups 9 and 12) and are not re-reported.
