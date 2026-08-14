# Implementation: i67-brain-prompt-holonomic-base

> Written by the implementer. Describes the final design, the choices and
> tradeoffs behind it, and how each acceptance criterion is tested.

## What was built

Two commits, both inside `src/robot_brain/`:

| commit | file | what |
|---|---|---|
| `3e4a6f1` | `robot_brain/openclaw/AGENTS.md` | the prompt's opening sentence now describes a 3-omniwheel holonomic base |
| `a0eafd3` | `test/test_prompt_drift.py` | `SUPERSEDED_BODY_CLAIMS` ledger + `TestBodyDescription` (4 tests) |

Nothing outside `src/robot_brain/**` and this feature's docs was touched
(`scripts/test_baseline.json` excepted — the ratchet rewrites it and says to
commit it; `robot_brain` 50 → 54 non-lint tests).

## 1. The prose fix (R1, R3, R4)

```diff
-You are the brain of a household mobile manipulator: a four-wheel base, an
-extendable vertical column and two arms with grippers. Jaime talks to you over
-Telegram and asks for chores. You do them by calling the robot's skills as MCP
-tools and reading what comes back.
+You are the brain of a household mobile manipulator: a 3-omniwheel holonomic
+base, an extendable vertical column and two arms with grippers. Jaime talks to
+you over Telegram and asks for chores. You do them by calling the robot's skills
+as MCP tools and reading what comes back.
```

- **Wording** is `docs/design/spec.md:29`'s own term, `3-omniwheel holonomic`,
  per R1 — not a paraphrase, so the prompt and the spec row say the same thing
  when read side by side.
- **No affordance framing** was added: no strafing, no "moves in any
  direction", no new sentence. I agree with R1 and did not need to be talked
  into it — `NavigateTo` has exactly one field (`location: str`), so there is
  no argument anywhere above the skill seam into which a belief about holonomy
  could be encoded, and the file already refuses this genre of sentence for
  `reset` ("an invitation dressed as documentation",
  `test_prompt_drift.py:116-126`).
- **No new backticks** (R3). "3-omniwheel" and "holonomic" are in no schema,
  enum or world id, so backticking either would have failed
  `test_the_prompt_names_nothing_the_system_does_not_have`.
- **The reflow is four lines, all one paragraph** (R4). The replacement clause
  is 11 characters longer than the one it replaces, which pushes line 3 past
  the file's ~80-column prose wrap; re-wrapping that paragraph (and only that
  paragraph) is the smallest edit that keeps the file's existing shape. The
  paragraph is still four lines and every other line in the file is byte-identical.

## 2. The sweep (acceptance criterion 2) — verified, not inherited

I re-ran the sweep rather than trusting `context.md`:

```
grep -rniE "wheel|holonomic|omni|kinemat|chassis|caster|differential|\bbase\b|
            dof|degrees of freedom|elbow|manipulator|\bhead\b|camera" \
     src/robot_brain/ --include=*.py --include=*.md --include=*.json --include=*.xml --include=*.cfg
```

Five hits outside `setup.cfg`'s `$base` install paths, and only one is a claim
about the body's *shape*:

| hit | verdict |
|---|---|
| `AGENTS.md:3` "a four-wheel base" | the stale claim — **fixed** |
| `AGENTS.md:44`, `:92`, `:136` | "the base" as the thing that moves / has a location / has a speed cap — no shape claim, and the 0.6 m/s is already asserted live against `SafetyLimits.defaults()` |
| `setup.cfg:2,4` | `$base` install-path variable, unrelated |

The wider claims on line 3-4 ("extendable vertical column", "two arms with
grippers") are still true under D26 — `spec.md:30-32` keeps a one-prismatic-joint
lift column and 2× SO-101 with parallel-jaw grippers. So `AGENTS.md:3` was the
only stale body claim in the package, which matches what the issue asserted.

## 3. The regression test (R2)

`SUPERSEDED_BODY_CLAIMS` maps **the descriptor that replaced a body fact** to
**the spellings it replaced**:

```python
SUPERSEDED_BODY_CLAIMS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    '3-omniwheel holonomic': ('four-wheel', '4-wheel', 'four wheel'),
})
```

Keyed that way (rather than as two separate lists) because the pairing is the
fact worth recording: *this* replaced *that*, per D1 → D26/D29, cited in the
constant's comment. Two parametrized tests read it — the superseded spellings
must be absent from `PROMPT`, the current descriptor must be present — both
case-insensitive on both sides, so a ledger row typed in any case still works.

**Why both directions.** Absence alone passes vacuously on a prompt that simply
deleted the sentence; presence alone passes on a prompt that describes both
bases. The pair is what pins "the prompt describes this body".

**Where it lives.** A new `TestBodyDescription` class rather than a home inside
an existing one. This file's organising scheme is one class per family of claim
(`TestToolCatalogue`, `TestToolArguments`, `TestSafetyEnvelope`,
`TestFailureCodes`, `TestWorkedExamples`), each with a one-line docstring
stating its invariant; "what the robot's body is" is a new family, and hiding it
inside `TestToolCatalogue` (whose stated invariant is about tools) would have
been the actual departure from the scheme. It is placed first because it guards
the prompt's first sentence.

**What it does not do**, stated in the class docstring rather than left for a
reader to discover: it pins the stale claims we already know about and cannot
notice the next one. When PR3–PR7 land the column, arms, gripper and camera,
this file stays green while the prompt lies, until somebody adds a row by hand.

**Why not derive the truth from the URDF.** Per R2, and I agree on the same
cost grounds: `src/robot_description/robot_description/` is `__init__.py` and
nothing else, and its own gate resolves everything through
`get_package_share_directory` with no source-tree fallback by design
(`test_description.py:60-70`), so importing the body would make `robot_brain`'s
10-second pure-Python suite newly depend on a colcon install tree plus
xacro/`check_urdf` — and it would then still have to recover a wheel *count*
from an English sentence. Large brittle coupling, one sentence of value.

## What was verified

- `pixi run build` — 9 packages, clean.
- `colcon test --packages-select robot_brain` — **57 passed** (was 53; +3
  superseded spellings, +1 current descriptor). `test_flake8`, `test_pep257`
  and `test_copyright` all green on the edited `.py`.
- **Mutation check.** With `AGENTS.md:3` reverted to "a four-wheel base", the
  suite reports exactly two failures —
  `test_a_superseded_body_claim_is_not_still_taught[four-wheel]` and
  `test_the_descriptor_that_replaced_it_is_taught[3-omniwheel holonomic]` — and
  the other 55 stay green. The new tests fail on the bug they were written for,
  and nothing else in the file could ever have caught it (confirming
  `context.md` §2: the prompt-drift suite only reads `` `backticked` `` tokens).
- `pixi run test` — full workspace green, 760 tests, 0 skipped, audit passed;
  the ratchet raised `robot_brain` 50 → 54 and rewrote
  `scripts/test_baseline.json`, which is committed with the change.

## For the red-team to look hardest at

1. **Is the reflow of lines 3-6 within R4's "surgical"?** It is a four-line
   diff for a one-clause change. The alternative (leaving line 3 at 87 columns)
   breaks the file's prose wrap. I judged wrap-preserving to be the smaller
   violation; disagree loudly if not.
2. **Is `'4-wheel'` as a bare substring safe?** It is checked against the whole
   prompt, not just line 3. Nothing in the current prompt contains it and I
   cannot construct a legitimate future sentence that would, but it is the one
   ledger entry that is a fragment rather than a phrase.
3. **The ledger is a hand-typed constant in a module whose docstring says "no
   expected value in this module is typed by hand."** That sentence is now
   *false as written* for `SUPERSEDED_BODY_CLAIMS`. I chose to leave the module
   docstring alone (R4's minimal-diff spirit, and it is a claim about the
   checkable-claim tests it describes) and to state the exception loudly in the
   new class's own docstring instead. A reviewer could reasonably want the
   module docstring amended; I did not want to edit the file's opening
   statement of purpose on my own judgment.
4. **Whether pinning is enough**, versus the general problem: the prompt's body
   prose has no live source, and PR3–PR7 are about to add four more body facts
   to it. R2 rules that out of scope here and routes it to a follow-up — see
   below.

## Notes for the manager (not code changes)

- **Follow-up to surface on the issue** (R2's own instruction): the prompt's
  body-description prose has no live source to diff against, and the ledger
  added here is a pin, not a guard. As PR3–PR7 land the column, arms, gripper
  and camera geometry, that gap widens. Worth an issue for Sisyphus to file.
- **Deploy staleness** (R3'): `AGENTS.md` reaches the Pi by a manual `scp`
  (`src/robot_brain/README.md:91-99`) that this repo never executes. Fixing the
  source file does not refresh an already-deployed copy — belongs in the PR
  description, per the manager's ruling.
- **No `decisions.md` entry.** Agreed with the manager's ruling: this
  implements D26/D29, it decides nothing new.
- **No escalation raised.** R1–R4 were all followed; I re-derived R1's and R2's
  reasoning independently before complying and reached the same conclusions.

---

# Round 2 — red-team fixes

Three BLOCKs and one promoted NOTE, in two commits (`a64aee7`, `f2044f9`).
Nothing in `AGENTS.md` changed: the prose fix survived review, and every fix
below is in `src/robot_brain/test/test_prompt_drift.py`.

Test count 55 → 58 (`robot_brain` non-lint 54 → 55; the ledger's absence check
went from 3 parametrized cases to 1, and four tests were added). Ratchet raised
itself; no `ALLOW_TEST_DECREASE` was needed.

## B1 — the ledger is a pattern now, with controls

`('four-wheel', '4-wheel', 'four wheel')` → `r'\b(?:four|4)[\s-]+wheel'`, matched
case-insensitively. The mapping keeps its shape (current descriptor → the claim
it retired) and the parametrize ids stay readable (`[3-omniwheel holonomic]`),
because both ledger tests now parametrize over the *keys*.

**How I convinced myself it catches what the list missed.** Not by reading it —
by making the belief executable, as
`test_the_matcher_catches_the_spellings_a_literal_list_did_not`. It asserts the
pattern fires on all eight strings from the red-team's table: the three the old
list caught (`a four-wheel base`, `a 4-wheel base`, `four wheels`), its
incidental catches (`FOUR-WHEEL BASE`, `four-wheeled base`), and the three it
missed (`4 wheels` — `decisions.md`'s own spelling — `a 4 wheel base`, and
`four  wheel base` with a doubled space). That test is not decoration: the
absence check can only ever observe a pattern *failing* to match, so a typo'd
pattern would leave it green forever. Verified by mutation — changing the
pattern to `wheeel` fails the control test and **nothing else**, i.e. without it
the hole would have been invisible again.

**How I convinced myself it does not false-positive.**
`test_no_matcher_fires_on_the_body_we_actually_have` runs every ledger pattern
against prose the prompt is entitled to contain: the sentence this PR installed,
a plausible omniwheel-geometry sentence, `four objects are on the table` (the
word "four" near furniture), and the full speed-caps line (digits near "base").
Plus the live case, which is the absence check itself, green on the real prompt.
The one real false-positive class stays open by design and matches N4: a prompt
that *disclaims* the old base ("this is not a four-wheel base") would fail — R1
forbids that genre of sentence anyway, and I would rather this guard be loud.
`quad-wheel` is still not matched; a synonym is a different claim and would get
its own row.

## B2 — the module docstring

"No expected value in this module is typed by hand" is replaced by a sentence
that names the exception and points at the class that bounds it. The red-team
was right and I was wrong to shelter behind R4: R4's text scopes to `AGENTS.md`.
Shipping a newly-false sentence in the statement of purpose of the file that
fixes a stale claim is the same bug one directory over.

## B3 — the reach is checked, and the docstring says what is true

`test_the_reach_the_examples_quote_is_the_live_one` compares
`{n for n in PROMPT matching '<n> m reach'}` with
`{default_world().robot.reach_radius}` — a set equality in both directions, in
the style of `TestSafetyEnvelope`, so a changed model, a second stated reach, or
a rewording that removes the phrase all fail with the numbers in the message.
`default_world()` rather than a bare `RobotModel()`: it is the world the agent
actually meets, and this module already imports it.

I checked the rest of `RobotModel` before asserting only the reach:
`shoulder_offset_y` (0.18) does not appear in the prompt at all, and
`shoulder_offset_z` (0.50) appears only as "arm 0.5 m/s" — the safety velocity
cap, a different fact. Asserting against that would be a coincidence, not a
check; the docstring says so.

`RobotModel.reach_radius` is left at 0.85 as instructed — the prompt and the
model agree today, which is what this PR owns.

The class docstring now says the **drivetrain** has no live source (true) rather
than "the body" (false), and gives the reason that survives measurement: reading
the URDF needs a `<test_depend>` on `robot_description`, which drags
`ament_index_python` onto the path of the one package whose defining property is
that it needs no ROS (D21; `test_no_ros_runtime` names that module explicitly).
The inflated colcon/xacro/English-parsing reasoning is gone from the file.

## N1 — the presence check

**Intro-scoping alone was not enough, and I found that by running it.** The
manager's and red-team's suggested one-liner (`PROMPT.split('\n## ', 1)[0]`)
still passed the red-team's own scenario, because the prompt's first `##`
heading is "How to work" — a fenced note left under the opening paragraph is
*inside* the introduction. The check is now
`without_fences(PROMPT).split('\n## ', 1)[0]`: dropping the fences is the half
that does the work, and the intro scope keeps the phrase in the paragraph that
introduces the body. The absence check stays on the raw prompt, as advised.

## Verification (round 2)

`pixi run test` green: 761 tests, 0 skipped, audit passed, baseline
`robot_brain` 54 → 55, committed. Five mutations, each run through
`colcon test` and reverted, with the working tree clean afterwards:

| mutation | expected | observed |
|---|---|---|
| prompt says "a base on 4 wheels" (the spelling the old list missed) | absence + presence fail | exactly those two |
| body sentence → "a mystery box on legs", descriptor left in a fenced TODO in the intro (N1's scenario) | presence fails | it does — and did **not** before the `without_fences` fix |
| `RobotModel.reach_radius` 0.85 → 0.40 (the red-team's mutation) | reach test fails | `the prompt quotes [0.85] as the reach; the model says 0.4` |
| prompt's "0.85 m reach" → "1.85 m reach" | reach test fails | it does (the equality binds from both sides) |
| ledger pattern typo'd to `wheeel` | matcher control fails, absence stays green | exactly that — the reason the control exists |

## Surviving NOTEs for the manager

Not fixed here (per "fix BLOCKs only"), surfaced for routing:

- **N5** (red-team) — `AGENTS.md:81`'s example observation omits the
  `orientation` quaternion the live observation always sends; nothing parses the
  fenced observation JSON against the live shape. Pre-existing, defensible as
  abbreviation.
- **N2's cheaper live source** — `src/robot_description/package.xml:6` contains
  the literal string `3-omniwheel holonomic base`; cross-checking it buys
  consistency, not truth. For the follow-up, not this PR.
- **Observed while doing B3, not in the red-team's list**: the same unguarded
  corner exists one more time. `AGENTS.md:219,223` quote the column travel
  (`[0, 1.2] m`, "from 1.2 m up") inside worked examples, where
  `TestSafetyEnvelope`'s section-scoped check cannot see them — so retuning
  `SafetyLimits` would leave those two lines stale and green. Same shape as the
  reach gap, different owning package; belongs with the follow-up about the
  prompt's unchecked prose rather than in this PR.
- The manager's own `status.md`/`red_team.md` are uncommitted/untracked in the
  worktree; I left them alone rather than committing another agent's docs.
