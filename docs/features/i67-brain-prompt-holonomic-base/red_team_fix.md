# Red team — round 2 (scoped to the fix diff `6c0312c..HEAD`)

Reviewer: second red-team agent. Scope: commits `a64aee7`, `f2044f9`, `a7c8c15`
only. The `AGENTS.md` prose fix and round-1 findings are not re-litigated.

**Verdict: no BLOCKs. The three round-1 BLOCKs are closed, and I confirmed each
closure by mutation rather than by reading.** Everything below is a NOTE
(follow-up routing), ordered most-useful-first. Test adequacy is assessed
explicitly in §0.

Method: all mutations were run on a `cp -r` copy of `src/` under `/tmp/rt2`,
never in the worktree. The worktree ends exactly as it started (`git status`:
`M docs/features/.../status.md`, `?? docs/features/.../red_team.md` — both
pre-existing manager docs I did not touch).

---

## 0. Test adequacy and regression — VERIFIED

`pixi run test` from the worktree: **761 tests, 0 errors, 0 failures, 0
skipped; AUDIT PASSED**; `robot_brain` 58 collected / **55 non-lint, `+0`
vs-base** — the ratchet in `scripts/test_baseline.json:6` is correct. Diff
touches only `src/robot_brain/test/test_prompt_drift.py`,
`scripts/test_baseline.json`, and this feature's docs — nothing outside owned
paths.

Each round-1 BLOCK is closed, proven by mutating the copy and re-running
`test_prompt_drift.py` (28 tests, ~0.8 s):

| mutation (on the copy) | result |
|---|---|
| prompt body → `a base on 4 wheels` (the spelling the old literal list missed) | `test_a_superseded_body_claim_is_not_still_taught` **and** `test_the_descriptor_that_replaced_it_is_taught` fail; 26 pass — B1 closed |
| ledger pattern typo'd to `wheeel` | only `test_the_matcher_catches_the_spellings_a_literal_list_did_not` fails — the control is load-bearing, exactly as claimed |
| `RobotModel.reach_radius` `0.85 → 0.40` | `test_the_reach_the_examples_quote_is_the_live_one` fails: *"the prompt quotes [0.85] as the reach; the model says 0.4"* — B3 closed |
| prompt `beyond the 0.85 m reach` → `beyond its reach of 0.85 m` | fails with *"the prompt quotes [] as the reach"* — the docstring's **empty-set** claim (`:217`) is true, not vacuous |
| prompt `0.85m reach` (space removed) | same loud empty-set failure |
| a second reach added (`Each arm has a 0.9 m reach.`) | fails: `{0.85, 0.9} == {0.85}` — both directions bind |
| N1 scenario: body → "a mystery box on legs", descriptor left in a ```-fenced TODO in the intro | raw `PROMPT`: **passes** (round-1 hole); `PROMPT.split('\n## ',1)[0]` (the manager's one-liner): **passes**; landed `without_fences(PROMPT).split('\n## ',1)[0]`: **fails** — *both halves* of the implementer's correction are confirmed, the one-liner alone genuinely was not enough |

**No coverage was lost** collapsing the absence check from 3 parametrized cases
to 1: the pattern matches all three old literals (`four-wheel`, `4-wheel`,
`four wheel`), which the new control test asserts directly, plus five more.

Spot-checks of `implementation.md`'s round-2 mutation table: rows 1, 3 and 5
reproduce exactly as written. Its claims about `decisions.md` are accurate too —
`docs/design/decisions.md:78` really does read `The cost is the "4 wheels"
aesthetic of D1`, and D26 also prints `4-wheel base` twice.

The new-prose audit the brief asked for: `test_no_ros_runtime.py:29` does list
`ament_index_python` in its refusal tuple (claim at `:134-136` is literally
true — see N7 for the one place it over-reaches); `RobotModel` is indeed the
Mock's body model (`mock_backend.py:139`, `mock_world.py:70-85`), so `:130` is
true.

---

## NOTE 1 — the replaced module docstring is *still* not true (`test_prompt_drift.py:17`)

VERIFIED by inspection of the module as it landed.

B2 replaced "No expected value in this module is typed by hand" with:

> Every expected value here is read from one of those, with a **single
> deliberate exception**: `SUPERSEDED_BODY_CLAIMS` …

The exception is not single. In the same file, hand-typed:

- `:383`, `:398` — `assert 'out_of_reach' in examples` / `in guidance`. This is
  the strongest counterexample: `out_of_reach` is a `FailureCode` **value**,
  `FailureCode` is already imported, and two methods earlier (`:346`) the file
  *does* derive its code from the enum (`next(code for code in FailureCode if
  code.is_safety_event)`). Renaming the code is caught elsewhere, so nothing is
  silently broken — but the docstring's universal is false.
- `:331`, `:384` `'clamped'`; `:348` `'do not repeat'`; `:386`
  `examples.count('call place(') >= 3`; `:404-412` the seven section headings;
  every `section(PROMPT, '…')` anchor; and, new in this diff, the eight control
  spellings, the four innocent strings and `_STATED_REACH`'s phrasing (`:73`).

Failure scenario: a maintainer adds a hand-typed limit "because the docstring
says there is exactly one exception and mine is the second", or a reviewer
trusts the sentence and skips checking. Fix direction: say what is true —
values a live source owns are read from it, structural anchors and prose
markers are literals — or, cheaper and better, derive `out_of_reach` from
`FailureCode` at `:383`/`:398` and keep the sentence scoped to *values*.

## NOTE 2 — "a later edit to the pattern cannot quietly narrow it" is false (`test_prompt_drift.py:176-178`)

VERIFIED by construction:

```
pattern                                    controls    misses
r'\b(?:four|4)[\s-]+wheel'                 all pass    []
r'\b(?:four|4)[\s-]+wheel(?:s|ed|\s|-)'    all pass    ['The base is four-wheel.', 'a four-wheel',
                                                        'four-wheel, holonomic', 'four-wheel!']
```

Every one of the eight controls is followed by a space or by `s`/`ed`; none
exercises the phrase before punctuation or at end of string. So a plausible
"tightening" edit passes all eight controls while going blind to
`The base is four-wheel.` Fix direction: either soften the claim ("cannot
quietly drop the spellings it already catches" — which *is* true and is the
control's real value), or add one control ending the phrase at a full stop.

## NOTE 3 — the N+2th unguarded prompt claim: `known_locations` (`AGENTS.md:89`, `:97-98`, `:168`)

VERIFIED. The prompt enumerates the world's locations twice and asserts
completeness — *"`known_locations` is the complete set of names `navigate_to`
accepts. There are no others."* Nothing in `robot_brain` compares that list to
`default_world().locations`, which the module already calls.

Repro: added `"hallway"` to `src/robot_world/robot_world/default_world.json` on
the copy → `test_prompt_drift.py` **28 passed**. (`robot_world`'s own
`test_default_seed.py` goes red, so the *seed* change is not silent — but its
failure names `EXPECTED_LOCATIONS`, and once that is updated the prompt is left
teaching a closed set that is no longer closed. Same shape as the column-travel
gap the implementer surfaced, one package over.)

This is behaviourally the most consequential of the unchecked numbers: the
agent is told, in the imperative, that no other destination exists. Fix
direction (follow-up): one assertion, using the live source already imported —
pull the `known_locations` array out of the fenced observation and compare it
with `set(default_world().locations)`.

## NOTE 4 — the rest of the number map (`AGENTS.md`)

VERIFIED by mutation unless marked. Table the brief asked for:

| number | where | live source | what checks it | verdict |
|---|---|---|---|---|
| `3-omniwheel` | `:3` | D26/D29 (ledger) | presence + absence checks | **covered** (prose mutation fails 2 tests) |
| `0.0`/`1.2` column travel | `:133` | `limits.column` | `TestSafetyEnvelope` set equality | **covered** |
| `0.6`/`0.15`/`0.5` speed caps | `:137` | `limits.motion.velocities` | same | **covered** (`base: 0.6→0.7` → that test fails) |
| `40` N gripper ceiling | `:138` | `limits.motion.max_gripper_force` | same | **covered** (`40.0→50.0` → that test fails) |
| `0.85 m reach` | `:201` | `RobotModel.reach_radius` | new test | **covered** (this diff) |
| `[0, 1.2] m`, `clamped to 1.2 m`, `from 1.2 m up` | `:219`, `:220`, `:223` | `limits.column.max_height` **and** `RobotModel.max_column_height` | nothing | **gap** — retuned `max_height` to 1.5 *and* fixed only the safety section (what the failing test points at): 28 passed with the examples still teaching a 1.2 m clamp. Already surfaced by the implementer; note that `mock_world.py:86` gives it the *same* live source the new reach test already calls, so `implementation.md`'s "different owning package" reason for deferring is weaker than stated — `default_world().robot.max_column_height` is 1.20 |
| `schema_version: 1` | `:60`, `:80` | `robot_skills.serialization.SCHEMA_VERSION` | nothing | **gap** — bumped to 2, 28 passed |
| `z = 0.45` counter top, `0.55`, `0.10` clearance | `:177`, `:179-181` | `counter_1` seed pose | nothing | **gap** — moved `counter_1.z` to 0.60, 28 passed |
| observation snapshot: `0,2,0`, `column_height 0.3`, `cup_1 0.3,1.9,0.75` | `:81-88` | seed world | nothing | **gap** (same class; the block is a byte-faithful snapshot of the live world today) |
| `3.25 m` from the shoulder | `:200` | derivable from world + `RobotModel` | nothing | **gap** (UNVERIFIED as a *drift* repro; verified that no test reads it. Arithmetic checks out today: ≈3.243 from the left shoulder) |
| `~0.10`/`~0.4`/`~0.15` placement offsets | `:151-155` | none — advice | nothing needed | n/a |
| `2.0` in `extend_column({"height": 2.0})` | `:217` | deliberately out of range | keys schema-checked only | fine |
| "three failed attempts" | `:26` | policy | nothing needed | n/a |

Fix direction (one follow-up, not this PR): the recurring shape is *a live fact
quoted inside a fence*. The cheapest general fix is a fixture that parses the
two fenced payload blocks and the example transcripts, so `schema_version`,
`known_locations` and the world coordinates land under the same set-equality
treatment the safety section already gets.

## NOTE 5 — retypes the new pattern still misses (`test_prompt_drift.py:69`)

VERIFIED (executed against `\b(?:four|4)[\s-]+wheel`, `re.IGNORECASE`):

- **Good news first, and it matters:** the line-wrap cases are *covered* —
  `four\nwheel` and `four-\nwheel` both match, because `[\s-]+` eats the
  newline. A non-breaking space matches too. The ~80-column hand wrap is not a
  hole.
- Misses: `four‑wheel` (U+2011), `four–wheel` (en dash), `four—wheel` (em
  dash) — worth a mention only because this file *does* use `—` in prose
  (`:23`, `:39`, `:94`, `:117`, `:130`), so a smart-dash editor is not fantasy;
  `4wheel`; `**four**-wheel`; and the adjective form `four large/driven/castor
  wheels`, which is the most plausible human retype of the lot.
- Correctly *not* matched: `24 wheels`, `the fourth wheel` (the `\b` earns its
  keep).
- False positives it would fire on that R1 does not forbid: `four wheelchairs`,
  `4 wheelbarrows`, `four wheels of cheese`. Low-probability in a household
  prompt, but the pattern has no right-hand boundary.

Fix direction: none required — the ledger is documented as a pin on a *known*
stale claim, not a classifier. If cheap, `[\s‐-―-]+` and a
`wheel(?:s|ed)?\b` tail would close the dash and the false-positive classes.

## NOTE 6 — the presence check still passes on three variants of the round-1 scenario (`test_prompt_drift.py:167`)

VERIFIED. With the body sentence reworded away and the descriptor surviving
only in an intro-level note, `without_fences(PROMPT).split('\n## ',1)[0]` still
finds it when the note is:

| note form | landed check |
|---|---|
| ```` ``` ```` fence | **fails** (fixed) |
| `~~~` fence | **passes** (hole) |
| 4-space indented block | **passes** (hole) |
| `<!-- HTML comment -->` | **passes** (hole) |

`brain_fixtures._FENCE` only knows backtick fences. `AGENTS.md` uses only
backtick fences today, and the paired absence check is the primary guard, so
this is residual — but the docstring at `:161-166` frames fence-stripping as
"the half that does the work", which is only true for one fence syntax. Fix
direction: teach `without_fences` the `~~~` form (one alternation), or drop the
claim to "backtick fences".

## NOTE 7 — the `ament_index_python` rationale over-reaches (`test_prompt_drift.py:133-137`)

VERIFIED. The literal claim is true: `test_no_ros_runtime.py:29` names
`ament_index_python` in the module list its subprocess probe refuses to see
loaded. The *implicature* — that reading the URDF from here would be caught —
is not: the probe only inspects what `import robot_brain` loads in a bare
subprocess, and the static scan's `FORBIDDEN_ROOTS` is `('rclpy',)` alone.

Repro: added `import ament_index_python` to the copy's `test_prompt_drift.py` →
`test_no_ros_runtime.py` + `test_prompt_drift.py` = **31 passed**. A future
implementer who reads this docstring as "the suite will stop me" is wrong; the
argument that actually holds is the design one (a `<test_depend>` on
`robot_description` puts a ROS dependency into the one package defined by not
having any, D21). Fix direction: keep the design argument, drop or qualify the
parenthetical.

## NOTE 8 — a second ledger row ships without controls (`test_prompt_drift.py:180`)

VERIFIED by inspection. `test_the_matcher_catches_the_spellings_a_literal_list_did_not`
hardcodes `SUPERSEDED_BODY_CLAIMS['3-omniwheel holonomic']`, so adding a second
row gets no positive control at all (the negative test loops all patterns, but
over a list of innocents specific to *this* claim), and deleting the row turns
the control test into a `KeyError` rather than a clean signal. The docstring
concedes this ("A second row brings its own controls") and the file's own thesis
is that "an untested pattern is a hole", so the two are in tension. Fix
direction (follow-up, only if a second row ever lands): make the controls a
sibling mapping keyed the same way and parametrize over the ledger's keys, so a
row without controls fails at collection.

---

## Routing suggestion

- To the **issue** as follow-ups: NOTE 3 + NOTE 4 (one issue — "the prompt's
  fenced facts are unchecked: `known_locations`, `schema_version`, the world
  coordinates and the column travel quoted in worked examples"), which
  subsumes the implementer's surviving NOTE.
- Cheap in-PR polish if the manager wants it, all prose-only and none blocking:
  NOTE 1, NOTE 2, NOTE 7.
