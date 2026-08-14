# Red-team pass H — full adversarial pass over `origin/main..HEAD` (`3af01f5`)

Scope: the entire branch diff, formed independently before any prior report was
read. Primary target, per dispatch: the passages `3af01f5` rewrote (D30's
opening, D30's closing rationale, D30's PR6 paragraph, `spec.md`'s row,
`status.md`'s follow-up list), then the live-source enumerations, then the whole
implementation by mutation, then both acceptance criteria with my own search
terms.

**Result: 1 BLOCK, 6 NOTES.** The BLOCK is in the passage `3af01f5` rewrote, and
it is the same defect class as the last four rounds: a historical claim that is
*checkable* and is refuted by this repo's own record. It is **not** a claim about
a number — the manager removed those — it is the clause that survived the purge
next to them: **"It was caught by a human reading the diff and filing #67, and
nothing in the repo would have caught it otherwise, at any later date."**

The code and tests are unchanged since pass E and I re-derived their verdict
independently by mutation: **test adequacy — ADEQUATE** (matrix below, 17
mutations, every one of them killed by the test it should be killed by, and the
two that must *not* fire did not).

---

## BLOCK 1 — D30 asserts a provenance the repo's own record contradicts: an agent found it, an agent filed it, and the thing that caught it *is* in the repo (`docs/design/decisions.md:115`)

**VERIFIED** — `gh issue view 65 --json comments`, `gh issue view 67`,
`gh pr view 66 --json mergedAt`, timestamps below.

The sentence, as `3af01f5` left it (D30's opening, its motivating evidence):

> Nothing went red — not a test, not the drift suite that exists for exactly
> this, not CI. **It was caught by a human reading the diff and filing #67, and
> nothing in the repo would have caught it otherwise, at any later date.**

The first half of that ("nothing went red") is **true and I re-verified it**
(see the re-execution table). The rest is false on the record, in two ways.

### 1. It was not caught by a human, and not by reading the merged diff

The finding is already written down **six minutes before #66 merged**, in a
comment on issue #65 authored in the #65 *worktree team's* voice:

```
$ gh issue view 65 --json comments
--- 2026-08-14T00:43:28Z
**Follow-ups uncovered during #65** (PR #66). Manager-routed per CLAUDE.md;
Sisyphus files the issues, deduping against the roadmap. ...
### 1. The OpenClaw brain prompt still tells the LLM the robot has four wheels
`src/robot_brain/robot_brain/openclaw/AGENTS.md:3` reads: ...
**Affected paths:** ... Worth a sweep of the rest of `src/robot_brain/` ...
```

```
$ gh pr view 66 --json mergedAt   -> 2026-08-14T00:49:37Z
$ gh issue view 67 --json createdAt -> 2026-08-14T00:49:53Z   (16 s later)
```

Issue #67's body is that comment's section 1 with two edits ("fixed in this PR"
→ "fixed in PR #66"; "out of its lane" → "out of that PR's owned paths") plus a
closing line — *"Not in the current roadmap ... filed as new work, deduped
against it"* — which is CLAUDE.md's own wording for **Sisyphus's** filing duty
("Managers do **not** open issues; **Sisyphus files them**, deduping against the
roadmap"). Nobody drafts that body in the sixteen seconds between the merge and
the issue; it was already drafted, by the agent that found it, in the comment
above.

So the chain the record shows is: the **#65 worktree team found it while fixing
`robot_description`'s own copies of the same string** (the comment says so:
"The equivalent strings inside `robot_description` were fixed in this PR; this
one is out of its lane"), its **manager routed it as a follow-up** per CLAUDE.md,
and **Sisyphus filed #67**. Every actor in that chain is an agent. There is no
evidence anywhere in the record of a human reading the diff, and the 16-second
gap is positive evidence against it.

**Where the false claim came from is the point.** `red_team_full_f.md:75` wrote,
inside a finding labelled **VERIFIED**, "the only thing that caught it was a
human reviewer of that same PR filing #67 sixteen seconds later". The VERIFIED
label covers the timestamps it printed — merge time, issue time, commit time —
and **not** the word "human", which pass F never checked. `red_team_full_g.md:267`
then put the whole string *"the seventeen minutes … only because a human read
the diff and filed #67"* in a table row marked **VERIFIED**, with evidence that
is again only the three timestamps. `3af01f5` deleted the number and kept the
inference. That is, exactly and for the third time in this run, the trap D30's
own rationale names two paragraphs later: **a finding labelled VERIFIED is
verified for its own claim, not for an inference drawn from it.**

### 2. "nothing in the repo would have caught it otherwise, at any later date"

The mechanism that *did* catch it is defined **in the repo**: CLAUDE.md's
"Feedback routing (manager-only, outward)" section, which is what the #65
comment cites by name, executed by the agent roles in `.claude/agents/`. The
claim as written is therefore false in its own terms — and its "at any later
date" half is a universal about the future that nothing can establish, applied
to a repo where PR3, PR3.5, PR4, PR5 and PR7 all run that same review process
over the same package.

The accurate statement is narrower, checkable, and *stronger* for D30's thesis:
**no automated gate could notice** — not a test, not the drift suite, not CI —
and the only thing that did notice was a human-designed review process running
by hand, on a neighbouring PR, by luck of the owned-paths overlap.

**Failure scenario.** `decisions.md` is append-only and outranks every other doc
("where any doc disagrees with it, it wins"). The most likely future reader is
whoever takes routed follow-up 1 (the unguarded-prose issue) and has to argue
how much gating prose deserves. They read D30 and take away "a human caught it
by reading the diff; the repo could not have". Both halves misdirect: they will
under-weight the review process that actually caught it (and is repeatable — it
is written down) and over-weight an accident of human attention that never
happened. The neighbouring D29 entry closes with *"a decision entry that rounds
its own evidence up is the failure this PR already had to fix once"*; this
rounds its own evidence up in the same direction, by attributing to luck what
the repo's own machinery produced.

**Why BLOCK, not NOTE.** Same grounds pass F and pass G used for the two
duration clauses in this same sentence, graded the same way for consistency: it
is the last review before the entry is permanent; it is a claim about this
project's history refuted by this project's record; the fix is one clause; and
it is a live instance of the exact defect the entry exists to warn about.

**Fix direction.** One clause, no new facts required:

> Nothing went red — not a test, not the drift suite that exists for exactly
> this, not CI. What caught it was the review process, not the workspace: the
> #66 team hit the same stale string inside `robot_description`, routed the
> `robot_brain` copy outward as a follow-up because it was out of that PR's
> owned paths, and #67 was filed from that routing. No gate in the repo was
> capable of noticing, and none would have become capable on its own.

If any part of the human-attention story is to be kept, it needs a citation
that exists; I could not find one. Note also that whatever replaces it must not
re-introduce a number — the timestamps are in `git`/`gh`, and D30's own closing
lesson is that the argument never needed them.

---

## NOTE 1 — the follow-up list is routed outward and carries a count that is wrong today, and a second, different count for the same experiment (`status.md:348`, `status.md:50`)

**VERIFIED.** Follow-up 4 says adding `hallway` to the seed world "leaves **28**
prompt tests green"; `status.md:50` says the same mutation "leaves **31** tests
green". I ran it on a `/tmp` copy (`default_world.json` + `hallway`, whole
`test_prompt_drift.py`): **30 passed, 0 failed.**

The *substance* is verified and important — the gap is real and the follow-up is
worth filing. But this list is the only part of `status.md` that survives merge
(it is pasted into the issue), and it will be read by whoever takes the work,
who will re-run it and get a third number. Same trap, same fix as D30's:
**drop the numeral** — "the entire prompt-drift suite stays green" is true,
stable, and says everything the number said.

## NOTE 2 — the source still states the duration D30 deliberately stopped stating (`test_prompt_drift.py:61,182`)

**VERIFIED (timestamps).** Both comments say the prompt "kept saying
'four-wheel' **for a day**". `3af01f5` removed every duration from D30 on the
ground that the anchor is ambiguous and the figure goes stale. The record:
D26's log section is dated `2026-08-12` but landed on `main` at
`2026-08-13T10:55:44-04:00`; #66 merged `2026-08-13T20:49:37-04:00`; the
correcting commit `150dd12` is `21:07:00-04:00`. So the same phrase is ~1 day
(log date), ~10 h (D26 on `main`) or 17 m (#66 merged) depending on the anchor.

`red_team_full_f.md:56-66` examined these two lines explicitly and cleared them
on the log-date anchor, and I am **not** re-litigating that: on that anchor the
sentence is defensible. This is a NOTE about the residue the last commit
created — the shipped source now asserts a figure the decision log refuses to
assert, on a different anchor, about the same fact. Cheapest fix, and it costs
no evidence: delete the two words in both places.

## NOTE 3 — the one numeral that survived the purge is true only if you count the instance that was never corrected (`decisions.md:121`)

**VERIFIED (git log).** D30's closing rationale says "the durable fix was not
getting the figure right on **the fourth attempt** but removing the arithmetic".
Attempts at the figure inside D30: `5837211` ("for a day"), `524f2f3` ("for two
days" + "seventeen minutes"), `3af01f5` (removal) — removal is the *third*. It
is the fourth only if `d92150f`'s `test_prompt_drift.py:61` counts as the first
attempt, which is precisely the instance NOTE 2 says is still in the tree. Either
correct the count, or (better, and in the entry's own spirit) write "on the next
attempt" and lose the last numeral in the paragraph.

## NOTE 4 — `spec.md`'s D30 row: the last edit broke the bullet's wrap and left a grammar slip (`docs/design/spec.md:178-179`)

**VERIFIED.** `3af01f5` rewrote the tail of the row and left
`… pins it. `robot_brain` takes **no` at ~100 columns inside a bullet whose
other lines wrap at ~78, and the sentence now reads "The same applies to a body
fact the prompt gains for the first time — the head camera … — **as it lands**
ungated unless …". Content is correct (I verified the prompt mentions no camera:
`grep -ri "camera\|wrist\|microphone" src/robot_brain/` → nothing outside the
drift test). Re-wrap and drop the stray "as".

## NOTE 5 — the presence check pins an exact literal, so a legitimate reword goes red on a correct prompt (`test_prompt_drift.py:206`)

**VERIFIED by mutation.** `test_the_descriptor_that_replaced_it_is_taught`
asserts `'3-omniwheel holonomic' in introduction()`. Re-wrapping is tolerated
(mutation M10: descriptor split across a newline → 30 passed), but a rewording
that stays true — "a holonomic base on three omniwheels" — fails. Routed
follow-up 12 covers the false-positive surface of the *pattern*; this is the
false-positive surface of the *descriptor*, and it is the half that PR3–PR5 will
touch (they each re-edit this sentence). Worth one line in follow-up 12 rather
than its own item.

## NOTE 6 — the module docstring gives the ledger the wrong reason (`test_prompt_drift.py:22-24`)

The module docstring says `SUPERSEDED_BODY_CLAIMS` is hand-typed "because the
drivetrain it describes has **no live source in this package at all**". True but
vacuous as a reason: nothing this suite reads live is in this package —
`Side`, `RobotModel`, `SafetyLimits` and `TOOL_NAMES` all live in siblings. The
class docstring and D30 both use the load-bearing scope ("no live source **on
this side of the skill API**"). Align the module docstring with them.

---

## Test adequacy — **ADEQUATE** (explicit verdict, earned by mutation)

Baseline in a `/tmp` copy (`cp -r src`, `PYTHONPATH` at the copy, pytest with
the two RoboStack plugin opt-outs): `30 passed in 0.80s`. Every mutation below
was applied to the copy only.

| # | mutation | expected | observed |
|---|---|---|---|
| M1 | prompt reverted to "a four-wheel base" (the #67 bug) | 2 fail | `test_a_superseded_body_claim…`, `test_the_descriptor…` **failed** |
| M2 | prompt says "a base with 4 wheels" (the spelling the literal list missed) | 2 fail | both **failed** |
| M3 | "two arms" → "three arms" | 1 fail | `…arms_and_their_grippers…` **failed** |
| M4 | "two arms with grippers" → "two arms" (word still appears 12× later in the prompt) | 1 fail | **failed** — intro scoping is real |
| M5 | "extendable" → "fixed" column | 1 fail | `…column_is_called_extendable…` **failed** |
| M6 | `RobotModel.reach_radius` 0.85 → 0.40 | 1 fail | `…reach_the_examples_quote…` **failed**, and **only** it — which is the executed proof of D30's "retuning `reach_radius` used to leave the entire suite green" |
| M7 | `max_column_height` → 0.0 | loud | 6 failures across the file |
| M9 | body sentence moved below the first `## ` heading | 3 fail | descriptor + arms + column **failed** |
| M10 | descriptor re-wrapped across a newline | **green** | 30 passed — the H3 fix holds |
| M11 | "four-wheel base" hidden inside a fenced example | 1 fail | absence check **failed** (whole-prompt scan) |
| M12 | pattern typo'd (`four`→`fuor`) | 1 fail | controls test **failed** — the "typo'd pattern is green forever" hole is closed |
| M13 | second ledger row added (`'linear-rail lift'`) | presence red | `test_the_descriptor…[linear-rail lift]` **failed** on a correct prompt — **independently confirms routed follow-up 7's second half** |
| M14 | reach phrase reworded away ("beyond a reach of 0.85 m") | 1 fail | **failed** (empty set ≠ `{0.85}`) |
| M15 | a second, unbacked reach quoted | 1 fail | **failed** (set comparison both ways) |
| M16 | pattern narrowed back to the literal list | 1 fail | controls test **failed** |
| M17 | ledger emptied entirely | loud | controls test **failed** + 2 skips (a skip is a deletion under D28's ratchet) |
| M18 | `hallway` added to the seed world | **green** (known gap) | 30 passed — routed follow-up 4, see NOTE 1 |

Judgement: the seven `TestBodyDescription` tests cover every claim in the
prompt's opening sentence, in both directions (absent/present, changed/removed),
with the pattern carrying its own positive **and** negative controls, and the
one deliberate gap (`known_locations`, and the ledger's inability to see an
unheard-of claim) is stated in the class docstring *and* routed. I could not
construct a body-claim regression the suite misses that is not already named in
the residue list.

## D30's technical and measured claims, re-executed myself

| claim | result |
|---|---|
| "Nothing went red — not a test, not the drift suite …, not CI" | **VERIFIED.** `git archive origin/main` → `/tmp/rt67main`; `test_prompt_drift.py` + `test_no_ros_runtime.py` + `test_openclaw_config.py` against the prompt containing "a four-wheel base": `43 passed`. GitHub CI runs only the docs-clean guard. |
| "the vocabulary check reads `` `backticked` `` tokens only" | **VERIFIED.** `brain_fixtures.py:56,74-79`; plain prose never enters the compared set. |
| "`test_no_ros_runtime` does not stop a test-time ROS import" | **VERIFIED by reading the executable definition:** `:23-38` probes only what `import robot_brain` loads in a bare subprocess; `:101-118` walks `os.path.dirname(robot_brain.__file__)`, i.e. the runtime package, not `test/`; `FORBIDDEN_ROOTS = ('rclpy',)`. |
| "`get_package_share_directory('robot_description')` raises under `colcon test`" | **UNVERIFIED by me** (needs a full `colcon build` of a copy; I declined the cost after a full `pixi run test` had already run). Structurally consistent with `package.xml:18-21` (test-depends only on `robot_mcp`/`robot_safety`/`robot_backends`/`robot_skills`) and with pass G's printed `AMENT_PREFIX_PATH`. No reason to doubt it. |
| "the URDF holds the wheel count structurally but the words only in a comment" | **VERIFIED.** `grep -ni "omniwheel\|holonomic" src/robot_description/urdf/base.xacro` → `:3,7,43,51,149`, all inside `<!-- -->`. |
| "`RobotModel` carries no drivetrain field, so PR6 does not retire the ledger" | **VERIFIED.** `mock_world.py:82-87` — `shoulder_offset_y/z`, `reach_radius`, `home_gripper_offset`, `min/max_column_height`. No wheel/base/drive term. `urdf-mjcf-pr-breakdown.md` §PR6 scopes the loader to those constants. |
| "the head camera is the one addition [the prompt] does not mention yet" (D30) / "the head camera, which the prompt does not mention today" (`spec.md`) | **VERIFIED.** Roadmap body additions in PR3–PR7: column (PR3), **head camera link (PR3.5)**, arms (PR4), grippers (PR5), head RGB-D sensor (PR7). Column/arms/grippers are already named in the opening sentence; `grep -ri "camera\|wrist\|microphone" src/robot_brain/` outside the drift test → nothing. Wrist cameras and the microphone are D26 hardware but are **not** in PR3–PR7's scope, so "the one addition" holds as scoped. |
| "the Mock serves exactly one gripper observation per member" | **VERIFIED.** `MockBackend().get_observation().robot.grippers` → `[Side.LEFT, Side.RIGHT]`, len 2, `set(Side)` equal. |
| "Of the four claims in the opening sentence, three have owners … each is now read from its own" | **VERIFIED by M3/M4/M5/M6** — arms, grippers, column and reach each have a mutation that kills a test; only the drivetrain does not. |
| `README.md:70-73` / `test_prompt_drift.py:14-18` live-source enumerations | **VERIFIED as enumerations.** Every source named is actually read: `TOOL_NAMES`/`TOOLS` (`:45`), `inputSchema` (`:85-91`), `SafetyLimits.defaults()` (`:390`), `default_world()` (`:336,354,433,443`), `RobotModel` reach + travel (`:299-303`, `:281-283`), `Side` for the arm count (`:268`), `FailureCode` for the failure table (`:417`), `SkillStatus`/`GripperState`/`Side`/`FailureCode` feeding `live_vocabulary()` (`:136`). No named source is unused, and no live source used by the module is omitted from the lists. Both hedge ("an aim, not an invariant" / "That is the aim, not a guarantee") and both name the exception. |

## Acceptance criteria — both met (my own search terms, not inherited)

- **AC1.** `AGENTS.md:3` reads "a 3-omniwheel holonomic base", matching
  `spec.md:29`'s exact term, in plain prose (no backticks — R3 holds, and the
  vocabulary check is undisturbed). `grep -Ei "four[- ]wheel|4[- ]wheel|4 wheels"`
  over the prompt → nothing. Regression gated in both directions (M1, M2, M11).
  Lines 3–6 are 76/78/80/41 columns, so R4's "no reflow" holds.
- **AC2.** My own sweep, not the implementer's: `grep -rn -i
  "wheel|holonomic|omni|base|chassis|column|arm|gripper|mobile manipulator|drive|
  caster|track"` over `src/robot_brain/**` (`*.md`, `*.py`, `*.json`, `*.xml`,
  `*.cfg`). Outside `AGENTS.md`, the only body-shaped strings are: the tool names
  in `openclaw.robot.json` (schema, not prose), `README.md:78-82`'s description
  of the *test design* (historically correct: "D1's four-wheel base, retired by
  D26"), and `README.md:253`'s "raise the column to two metres" — a deliberate
  clamp demo, consistent with the 1.2 m limit, not a body claim. No second false
  body claim exists in the package.

## Manager rulings re-checked as review targets

R1 (state the body, not the manoeuvre) — upheld: `NavigateTo` has exactly one
field, `location: str`; there is no direction/heading/twist argument anywhere in
the served schemas, so no belief about holonomy is expressible at the seam. R2's
*conclusion* (no `robot_description` test-depend) — upheld; its reasoning has
now been corrected three times and the shipped version claims only what was
measured. R3, R4 — verified above. The round-4/5 rulings (edit D30 rather than
append, since it has never been on `main`; do not bump `spec.md:15`'s "Flattened
through D28" on a spot check) are both correct and I would make the same calls.
The one ruling I dispute is not in `status.md` at all — it is the editorial
decision inside `3af01f5` to keep pass F's "human" inference while deleting the
numbers around it (BLOCK 1).

## Suite and worktree state

- `pixi run test` (run alone, nothing concurrent): **763 tests, 0 errors, 0
  failures, 0 skipped. AUDIT PASSED. All stages passed.** `robot_brain` 60
  collected / **57 non-lint**, `vs-base +0` against `scripts/test_baseline.json`'s
  `"robot_brain": 57`. Every package `+0`.
- All mutation work was done on `/tmp/rt67h` (a `cp -r` of `src/`) and
  `/tmp/rt67main` (a `git archive` of `origin/main`). Nothing in the worktree was
  edited.
- `git status --porcelain` → empty; `HEAD` = `3af01f5`.

## N+1 status

This pass found **1 BLOCK**, so the branch has not yet met the
clean-follows-clean bar. The BLOCK is a one-clause prose edit in `decisions.md`;
once it lands, that edit is itself new, unreviewed prose in the artifact that
has produced every defect of the last four rounds — so it needs a pass over the
rewritten sentence *and* the paragraph around it, not a re-read of the diff.
The code and tests have now been independently mutation-verified in three
consecutive passes without a finding; I would not spend the next pass on them
again.
