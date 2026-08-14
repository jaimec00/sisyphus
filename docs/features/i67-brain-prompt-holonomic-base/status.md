# Status — #67 robot_brain system prompt still claims a four-wheel base

- **Slug:** `i67-brain-prompt-holonomic-base`
- **Branch:** `feat/i67-robot-brain-system-prompt-still-claims-a`
- **Issue:** #67
- **Phase:** round 5 fixed → one confirming pass owed, then PR

### Round 5 — I committed the exact error I spent the run policing

Pass D: **0 BLOCKs**, 6 NOTES — the first clean pass, and it correctly flagged
the N+1 caveat about itself. I fixed four of its NOTES as prose corrections in
`d7f0f9a`, **authored by me rather than the implementer**, and one of them was a
new false claim in the permanent decision entry. Pass E found it.

**BLOCK (pass E) — D30's "PR6 dissolves that" clause.** I wrote that pointing
`RobotModel` at the URDF would give the drivetrain an owner and retire the
ledger row. I took that from pass D's NOTE 2, which was marked VERIFIED — but
only its premise ("PR6 points `RobotModel` at the URDF") had been verified; the
drivetrain half was inference. I transcribed inference into an append-only
artifact without executing against it, which is precisely the instruction I gave
the implementer in round 3 and the defect D30's own rationale is about. **Ninth
instance on this branch, and the second time a fix for the defect introduced a
fresh one.**

Verified myself before rewriting: `urdf-mjcf-pr-breakdown.md:129-137` scopes PR6
to `RobotModel`'s *kinematic* constants (shoulder offsets, reach, column travel,
home offset) and `mock_world.py:82-87` shows `RobotModel` carries **no drivetrain
field**. The failure this would have caused is concrete: PR6's author reads D30
in `main`, deletes `SUPERSEDED_BODY_CLAIMS` as "retired", and #67 recurs.
Corrected to claim only what the roadmap says, with the useful half kept (PR6
lowers the *price* of reversal; it does not perform it).

The lesson I would keep from this whole run, stated against myself: **a finding
labelled VERIFIED is verified for its own claim, not for the inference you draw
from it.** I enforced that on two agents and then failed it myself, in the one
artifact that is permanent.

- **Phase (superseded):** round 4 fixed (4 BLOCKs, 3 of them in D30)

### Round 4 — the decision log was the least-reviewed artifact, exactly as warned

Pass C (`red_team_full_c.md`) found **4 BLOCKs**, three against **D30 itself** —
written *after* both earlier full passes, so no reviewer had ever seen it, and
partly edited by me. Step 9 of the loop exists for precisely this, and the #55
precedent it cites (a correction made in the ephemeral copy while the decision
log kept the sentence it corrected) is what nearly happened here.

| item | disposition |
|---|---|
| **H1** — D30 claimed the suite "compares every prompt claim that has an owner". VERIFIED false by counter-example *from D30's own list*: adding `hallway` to the seed world leaves 31 tests green while the prompt says "There are no others". The accurate list lived only in this file — **deleted at merge** — so the false categorical was what would survive on `main`. | **fixed** — narrowed to what was measured, with the unguarded owned claims named in the entry itself. |
| **H2** — D30's rule "if the Mock reasons about it, check it" was false of the very sentence this PR rewrote: `two arms`→`seven arms`, `extendable`→`fixed`, `grippers`→`suction cups` all passed. `Side` has exactly two members and was **already imported** in that file. | **fixed in the code, not by weakening the entry** — arm count now read from `Side`, grippers from the Mock's one-per-side observation, column travel from `RobotModel`. D30's rule is now true *because the code changed*. **This also means AC2 was not fully met before**: "two arms with grippers" is a body claim in the same sentence as the four-wheel one. |
| **H3** — the presence check was a raw substring over hard-wrapped Markdown, so re-wrapping line 3 by one word turned the suite **red on a byte-correct prompt**. That re-wrap already happened once in this PR, and PR3–PR7 each edit that sentence. | **fixed** — `introduction()` now collapses whitespace. Three passes missed it because the *absence* regex is wrap-tolerant and everyone measured that half. |
| **H4** — every decision D1–D29 has a `spec.md` row; D30 had none, leaving its binding invariant invisible to the PR3–PR7 authors it binds. | **fixed** — row added under `## Ops & gates`. |

Two implementer judgement calls I endorse: it **edited** D30 rather than
appending a correction (the append-only rule protects merged history, and D30
has never been on `main` — it arrives as one squashed commit, so `main` sees
only the corrected entry), and it **declined** to bump `spec.md:15`'s "Flattened
through **D28**" to D30, because that line asserts something about the whole
file it had only spot-checked. Refusing to assert what you verified only in part
is the single behaviour this run most needed and least had.

- **Phase (superseded):** round 3 — two full N+1 passes found 3 BLOCKs → fixed
- **Round:** 3. **Deliberately past `run-feature.md`'s "max 2 rounds"** — see the
  rule conflict recorded under *Ops findings* below. CLAUDE.md makes a BLOCK a
  must-fix-before-merge, and `.claude/agents/red-team.md` (as of #69) requires
  full passes until a clean one follows a clean one. Both outrank a round count.
- **Blockers:** none needing Sisyphus (no escalation; all three BLOCKs are in-scope fixes)

## Phase log

| when | phase | outcome |
|---|---|---|
| start | sync | worktree at `3a0958c` = `origin/main`, clean |
| start | brief | issue #67 read via `--json`; body non-empty, acceptance criteria clear |
| start | new deps | **none** — this feature adds no third-party dependency, so step 2's provision-and-probe is a no-op |
| start | context-explorer | `context.md` written; 3 open questions raised |
| start | manager rulings | R1–R4 below, recorded before implementer dispatch |
| r1 | implementer | 3 commits: prose fix, `SUPERSEDED_BODY_CLAIMS` ledger + `TestBodyDescription`, `implementation.md`. `pixi run test` green (760). No escalation. |
| r1 | red-team | `red_team.md`: **3 BLOCK** (B1 ledger spelling gap, B2 module docstring now false, B3 `RobotModel` *is* a live body source and `AGENTS.md:201`'s 0.85 m reach is unchecked) + 5 NOTES. Verified my rulings A–H empirically. |
| r1 | manager | Accepted all 3 BLOCKs; **promoted N1** (fence-blind presence test) to must-fix — cheap, same weakness class as B1. Corrected my own R1/R2 rationales (below). Resumed implementer. |
| r1 | implementer (fix) | 3 commits. B1 ledger → regex + positive/negative controls; B2/B3 docstrings; N1 fence-scoped. **Corrected my N1 one-liner**: `PROMPT.split('\n## ',1)[0]` alone still passed the scenario, because the first `##` is "How to work" — a fenced note in the intro is *inside* the intro. Found by running it. `pixi run test` green (761). |
| r1 | red-team (scoped fix pass) | `red_team_fix.md`: **no BLOCKs.** All 3 BLOCKs + N1 confirmed closed **by mutation**, both halves of the N1 correction independently verified, no coverage lost in the 3-params→1 collapse. 6 NOTES. |
| r2 | manager | Promoted **two** NOTES to must-fix (F1, F2 below) — both are the *same class* as B2, and F1 means B2's replacement is still false. Everything else routed to follow-ups. Resumed implementer. |
| r2 | implementer (fix) | 2 commits. F1 docstring reframed as an aim, not an exception count; F2 both halves (honest sentence *and* the punctuation control), verified against my exact narrowing pattern. Count unchanged at 58. |
| r3 | manager | Rebased onto `1a8472b` — **ops PR #69 landed the N+1 rule mid-run**, requiring full passes until clean-follows-clean. My last pass was fix-diff-scoped and was itself followed by fixes, so I owed full passes. Dispatched two, split by lens (rulings / does-it-work), each told to form findings *before* reading prior reports. |
| r3 | test-runner | **PASS** — 761 tests, 0 failures, 0 skipped, audit passed, ratchet `+0` (`robot_brain` 55 non-lint). One transient `no-result` on the first run; cause later identified (see Ops findings). |
| r3 | red-team ×2 (full) | **3 BLOCKs between them**, two of which are *my* rulings. Both passes independently re-verified the shipped fix and all five tests as correct by mutation. AC2 independently audited and **upheld**. |

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

## Ruling corrections (round 1) — recorded because a ruling nobody corrected is still an assumption

The red-team was asked to attack my rulings empirically and did. Two of them
were **right in conclusion and wrong in reasoning**. Recording the correction
here rather than quietly keeping the conclusion, because the reasoning is what
the next person will reuse:

- **R2's cost argument was inflated.** I claimed deriving the wheel count from
  the URDF would cost this suite "a colcon install tree and the xacro
  toolchain". Measured: the install tree is *already* required (`colcon test`
  runs after `colcon build`), xacro is already installed and already exercised
  in the same run (~0.07 s), and the wheel count is **structural** (three
  `*_wheel_link` names), not a number to be parsed out of English as I asserted.
  **The conclusion survives on a different cost I failed to name:** it needs a
  new dependency edge that drags `ament_index_python` into the one package
  whose identity is "no ROS" (D21, asserted at `test_no_ros_runtime.py:31-35`).
  Don't add the edge — but for that reason, not mine.
- **R1's rationale was imprecise.** The premise held under attack (no
  direction, heading, velocity, twist or base-pose parameter exists in any
  *served* MCP schema; the only orientation quaternion is a gripper pose; the
  base speed cap is one scalar with no angular term). But my line — "holonomic"
  is inert while "it can strafe" is an invitation — does not survive as stated:
  an LLM unpacks the one into the other, and *both* are equally un-encodable at
  the seam. What actually bounds the damage is that the belief's only outlet is
  the prose report to Jaime, which a body noun triggers far less than a
  capability sentence does. **The wording ruling is unchanged**; the rule is
  better stated as *describe the body, not the manoeuvre*.

R3 and R4 survived attack unamended (no new backticks; the reflow collapses to
a single-token word-diff with every prose line ≤80 cols).

## Round 1 dispositions

| item | severity | disposition |
|---|---|---|
| B1 — ledger misses `4 wheels`/`4 wheel`, the spelling `decisions.md:78` itself prints, while the docstring claims "every spelling" | BLOCK | **fix** — prefer a regex over one more literal; a literal list is the thing that just failed |
| B2 — `test_prompt_drift.py:17` "No expected value in this module is typed by hand" is now false | BLOCK | **fix** — a newly-false claim in a statement of purpose, in the PR whose subject is a stale claim |
| B3 — `RobotModel` *is* a live body source (already imported, already a test_depend) and `AGENTS.md:201`'s "0.85 m reach" is unchecked (mutation to 0.40 left 27 passing) | BLOCK | **fix** — add the assertion, narrow the docstring to "the *drivetrain* has no live source". **0.85 itself is not to be changed** (see follow-ups) |
| N1 — presence test matches raw `PROMPT`, so it passes on the descriptor hiding in a fenced comment | NOTE | **promoted to fix** — one line, and the same weakness class as B1 |
| N2, N3 | NOTE | absorbed into the ruling corrections above |
| N4 — `'4-wheel'` fragment is safe | NOTE | no action |
| N5 — `AGENTS.md:81`'s example observation omits the `orientation` the real one always sends | NOTE | **survives → follow-up comment on the issue** (pre-existing, out of this brief) |

## Round 2 dispositions (final round)

The scoped fix pass returned **no BLOCKs**. I promoted two NOTES anyway, because
the heuristic the pass was handed — *a fix that corrects a claim in N places is
a strong prior the same claim is wrong in an N+1th* — found the N+1th **inside
the fix itself**:

| item | disposition |
|---|---|
| **F1** — B2's *replacement* docstring (`test_prompt_drift.py:17-20`) claims `SUPERSEDED_BODY_CLAIMS` is the "single deliberate exception" to values being read live. **False, and I verified it myself:** `:383-384` hand-type `'out_of_reach'` and `'clamped'`, and `FailureCode` is derived live at `:346` — so the first is a hand-typed copy of a value the module already has live. Also the seven headings and `count('call place(') >= 3`. | **fix** — and note the root cause this exposes: B2 was never "the ledger made the docstring false", it is that this docstring *has always overclaimed*. The pre-existing sentence was false for the same reason. Fixed with a framing that stays true as tests are added, rather than an exception count nobody will maintain. |
| **F2** — `:176` claims "a later edit to the pattern cannot quietly narrow it". Disproved: `r'\b(?:four\|4)[\s-]+wheel(?:s\|ed\|\s\|-)'` passes all eight controls while going blind to `The base is four-wheel.` — no control ends the phrase at punctuation. | **fix** — honest sentence, or the one control that makes it true. |
| all six other NOTES | **follow-up** — routed below. Deliberately not widened into this PR; the brief is the four-wheel claim. |

Worth recording plainly: **F1 is the third time in this run that a claim about
the code was wrong in a docstring rather than in the code.** That is the actual
recurring defect this feature surfaced, and it is what the retro should carry —
the prompt is not the only prose in this repo with no gate.

## Round 3 dispositions — the N+1 rule paid for itself immediately

Both full passes found BLOCKs, right after a scoped pass had returned none.
That is exactly the failure mode #69 was written to catch, in the same run the
rule landed.

| item | disposition |
|---|---|
| **G1** — `src/robot_brain/README.md:71-73` still says *"No expected value there is typed by hand"*: the **last surviving copy** of the sentence this PR spent two rounds fixing in `test_prompt_drift.py:17`, made false by this PR. Two files in one package now assert opposite things about the same property. | **fix** — B2 and F1 were both must-fix on this exact ground. Three passes missed it because everyone scoped their README check to *body* claims and cleared it correctly on that basis. |
| **G2** — the `ament_index_python` rationale is **false**, verified by mutation in both passes independently (adding the import leaves the suite green: the probe inspects only `import robot_brain` in a bare subprocess, and `FORBIDDEN_ROOTS` is `('rclpy',)` over the runtime package, not `test/`). | **fix** — and see below: this is the **third** wrong reason for the same ruling, and I authored the second and third. |
| **G3** — my ruling that this needs no `decisions.md` entry was **wrong**. | **fix — write D30.** |
| NOTE 8 (a second ledger row would ship without controls), pass B's replay-test idea, the `U+2011` escape | **follow-up** — routed below. |

### G2 — I have now been wrong about the same sentence three times

Worth recording as the run's sharpest lesson, because the pattern is mine:

1. **Original R2**: rejected the URDF check because it would cost "a colcon
   install tree and the xacro toolchain". Measured by round 1: false — both
   were already present, and the wheel count is structural, not English.
2. **My "correction"**: adopted round 1's proposed replacement — that it would
   drag `ament_index_python` into the one package defined by having no ROS —
   and told the implementer it was the version that "survives measurement".
   **Nobody measured it.** Both round-3 passes did, and it is false.
3. **Pass A's proposed replacement** (that D21's no-ROS property is about
   *deployed assets*, which a `<test_depend>` cannot touch) may itself be
   mis-scoped.

Every time, a plausible reason was accepted because it sounded like the kind of
thing that would be true. So the instruction to the implementer for round 3 was
**verify the new reason by executing against it before writing it**, and write
the honest minimum ("a design choice, not one the suite enforces") rather than a
fourth confident wrong reason. The *conclusion* — don't add the test-depend —
has been upheld by every pass; only the reasoning kept failing.

### G3 — why the "no decision entry" ruling was wrong

I ruled this feature decides nothing new. The argument that changed my mind is
mechanical rather than aesthetic: **`docs/features/` is deleted at merge**
(CLAUDE.md; `.github/workflows/guards.yml` enforces `git ls-files
'docs/features/*'` empty). So every ruling in this file, both ruling
corrections and all three disposition tables **cease to exist on `main`**.
Follow-ups survive because they are routed to the issue; the retro survives
because it is routed to the PR; the *reasoning* is routed nowhere. And feature
PRs land D-entries here as a matter of course — D27 in #62, D29 in #66, whose
register is precisely "what filling this in turned out to require".

### AC2 — my `known_locations` deferral was upheld, with a better reason than I gave

Pass A reproduced the gap (adding `hallway` to the seed world leaves 28 prompt
tests green) and still ruled the deferral legitimate: `known_locations` is a
**world**-description claim, not a body-description one, so it falls outside
acceptance criterion 2 — and reading "body-description" as "any factual claim"
makes the required sweep unbounded. The sweep AC2 actually asks for is complete:
base fixed and gated, reach gated against `RobotModel`, the column/shoulder
claim verified true against `mock_world.py:114-122`, "two arms" matching D26.

## Ops findings for the retro (not code, not this PR to fix)

1. **`run-feature.md`'s "max 2 red-team↔fix rounds" now conflicts with
   `.claude/agents/red-team.md`'s N+1 rule** (#69), which requires passes until
   a clean one follows a clean one. This run hit the conflict directly: round 3
   was past the cap and found three BLOCKs, two of them wrong manager rulings.
   The documents should be reconciled — and on this evidence the cap is the one
   that should give.
2. **Two concurrent `pixi run test` runs race in the shared `build/` tree** and
   crash at `scripts/check_test_integrity.py:765`, where `path.unlink()` wants
   `missing_ok=True`. This produced the transient `no-result` the test-runner
   reported. **My fault operationally** — I had three agents running at once —
   but the one-line fix is real, and a gate that can flake on a clean tree is
   worth hardening given the whole merge decision rests on it.
3. **I routed follow-ups from an agent's summary message instead of its report
   file, and dropped two NOTES.** The scoped pass wrote 8 NOTES and surfaced 6
   in its summary; its report explicitly listed NOTE 7 among its top picks, and
   NOTE 7 is what became BLOCK G2 a round later. Route from the artifact, not
   the abstract.

## Follow-ups to route outward (manager-only, per CLAUDE.md feedback routing)

To the **issue** (Sisyphus files them; I do not open issues):
1. **The prompt's body prose has no live source, and PR3–PR7 are about to add four more body facts** (column, arms, gripper, camera). B3 shows a live source exists for *reach*; there is none for the drivetrain. Paths: `src/robot_brain/test/test_prompt_drift.py`, `src/robot_description/`.
2. **`RobotModel.reach_radius` is 0.85 m while D26 pins SO-101 at ~0.4 m.** Surfaced by B3's mutation test. The prompt agrees with the mock world today, so it is not this PR's bug — but one of the two numbers is wrong about the arm we chose. Paths: `src/robot_backends/robot_backends/mock_world.py`, `docs/design/decisions.md` (D26).
3. **N5** — the prompt's example observation omits `orientation`, which the real `get_observation()` always sends. Path: `src/robot_brain/robot_brain/openclaw/AGENTS.md`.
4. **`known_locations` is unguarded, and it is the behaviourally worst of the gaps.** The prompt enumerates the world's locations twice and states *"There are no others"* (`AGENTS.md:89,97,168`), but adding `hallway` to the seed world leaves **28 prompt tests green** (VERIFIED by mutation in the scoped pass). An agent told a location does not exist will not navigate to it. Paths: `src/robot_brain/test/test_prompt_drift.py`, `src/robot_backends/robot_backends/mock_world.py`.
5. **Three more prompt numbers quoted outside their section-scoped check**, each VERIFIED green under mutation: `schema_version: 1` (bumped to 2 → green), `counter_1.z` (moved → green), and the column travel in the worked examples (`AGENTS.md:219,223` — retuned to 1.5 with only the safety section fixed → green). **Correction to `implementation.md`:** the column travel was deferred there as belonging to "a different owning package", but `mock_world.py:86` gives that number the *same* live source the new reach test already calls, so it is cheaper to close than stated.
6. **The best-shaped fix anyone found this run, and it belongs to follow-up 4/5, not here.** Pass B verified that the failure reasons quoted in the prompt's worked examples are **byte-identical** to what `MockBackend` + `default_safety_layer()` actually emit. So one replay test asserting the emitted reason string is `in PROMPT` would pin the reach, the `3.25 m` distance, the message format *and* the column travel quoted inside a fence — five of the eight unguarded rows — with no new dependency and no hand-typed ledger. Whoever takes the unchecked-prose issue should start here rather than extending the ledger. Path: `src/robot_brain/test/test_prompt_drift.py`.
7. **A second ledger row would ship without controls.** `test_the_matcher_catches_the_spellings_a_literal_list_did_not` hardcodes the one key, so adding a row gets no positive control and deleting it raises `KeyError` instead of a clean signal. Only bites when a second body fact is superseded — but that is exactly when nobody will be looking. Fix shape: a sibling mapping keyed the same way, parametrized over the ledger's keys, so a row without controls fails at collection. **Second half, VERIFIED by mutation in pass G:** a second row also forces *its own key* to appear in `introduction()`, because the presence check parametrizes over every key — adding a `'linear-rail lift'` row turns the suite red on a correct prompt. So the ledger's two halves need separating: absence should be per-row, presence should not. Path: `src/robot_brain/test/test_prompt_drift.py`.
8. **`scripts/check_test_integrity.py:765` crashes when two `pixi run test` runs share the `build/` tree** — `path.unlink()` needs `missing_ok=True`. One line. The laptop suite is the repo's only real merge gate, so a gate that can fail spuriously on a clean tree is worth hardening. Path: `scripts/check_test_integrity.py`.

9. **The stale-claim regex and the fence helper have known blind spots**, all follow-up-sized: `\b(?:four|4)[\s-]+wheel` misses Unicode dashes (and the prompt does use `—` in prose), `4wheel`, and `four large wheels`; `_FENCE` knows only backticks, so the presence check still passes with a `~~~` fence, an indented block, or an HTML comment. Path: `src/robot_brain/test/{test_prompt_drift.py,brain_fixtures.py}`.
10. **A fence inserted mid-sentence in the introduction still hides the descriptor.** `introduction()` drops fences *then* collapses whitespace, so the two halves of a split sentence rejoin and the presence check stays green even though the prompt no longer introduces the robot as anything. Narrow, but it is the third variant of the same scoping bug. Path: `src/robot_brain/test/test_prompt_drift.py`.
11. **`docs/design/spec.md:15` says "Flattened through **D28**"** while the file already carries D29 facts and now a D30 row. Pre-existing from #66, made one worse by this PR. Deliberately **not** bumped here: that line asserts something about the whole file, and this run verified only the base/body sections — asserting it on a spot check is the exact move that produced nine defects. Belongs with **`CLAUDE.md`'s "D1–D28"**, stale since #66 and now two behind, in the file every agent auto-loads. Both are ops-scope (root/`docs/`), so they want an ops PR, not a feature one. Paths: `docs/design/spec.md`, `CLAUDE.md`.
12. **The ledger pattern's false-*positive* surface is unexplored.** `\b(?:four|4)[\s-]+wheel` would fire on a legitimate future world object — "a four-wheel cart" as a thing the robot navigates around — failing the suite on a correct prompt. Follow-up 9 covers the false-negative side only. Path: `src/robot_brain/test/test_prompt_drift.py`.


To the **PR** (retro):
- the deploy note (R3');
- this issue existed *because* prose claims have no gate — and the run then hit
  the same defect **three more times in docstrings** (B2, B3, F1), including
  once inside the fix for it. The ledger is a patch, not a fix;
- a process note from the implementer worth generalising: it lost a set of
  uncommitted edits to `git checkout -- src/` while reverting a mutation.
  Mutation testing should commit first or mutate a copy — both red-team agents
  used `cp -r` to `/tmp` and neither lost anything.
