# Red-team — #67 brain prompt still claims a four-wheel base

Diff under review: `3a0958c..HEAD` (3 commits). Read-only pass; every
perturbation was run on a copy under `/tmp/rt67`, never in the worktree.
`git status` was clean before and after (only this file is new).

## Baseline: the suite is green

`pixi run test` — **VERIFIED**, exit 0.

```
Summary: 760 tests, 0 errors, 0 failures, 0 skipped
robot_brain  57 tests ... non-lint 54  vs-base +0  ok
AUDIT PASSED: every expected package collected tests
All stages passed.
```

The prose fix is correct against the design source: `docs/design/spec.md:29`
("**LeKiwi 3-omniwheel holonomic** base") and `decisions.md:78` (D26). R3 holds
— `git diff --word-diff` shows the edit adds no backticks, and
`test_the_prompt_names_nothing_the_system_does_not_have` is green. Owned paths
respected: the diff touches only `src/robot_brain/**`,
`docs/features/i67-brain-prompt-holonomic-base/**` and
`scripts/test_baseline.json`.

---

## BLOCK

### B1 — The ledger misses the digit-spaced spelling, which is the one D26 itself prints — `src/robot_brain/test/test_prompt_drift.py:58`

**VERIFIED.** Ran the ledger's own matcher against plausible retypes:

```
$ python -c "<ledger tuple>; for c in cands: ..."
CAUGHT  'four-wheel base'      ['four-wheel']
CAUGHT  'a 4-wheel base'       ['4-wheel']
CAUGHT  'four wheels'          ['four wheel']
CAUGHT  'four-wheeled base'    ['four-wheel']
CAUGHT  'FOUR-WHEEL BASE'      ['four-wheel']
MISSED  '4 wheels'             []
MISSED  'a 4 wheel base'       []
MISSED  'four  wheel base'     []
MISSED  'quad-wheel base'      []
```

`'4 wheels'` is not a hypothetical spelling. It is the spelling the governing
decision uses verbatim: `docs/design/decisions.md:78` — *"The cost is the
**'4 wheels'** aesthetic of D1"*. The realistic failure is exactly the one this
ledger exists to catch: somebody re-drafting the prompt's opening sentence with
D26 open in the other window types "4 wheels", and
`test_a_superseded_body_claim_is_not_still_taught` stays green through it.

The test's own docstring (`:134`) says *"every spelling of the claim"*. That is
false as written for the digit-spaced form.

**Fix direction.** Add `'4 wheel'` to the tuple — it subsumes `'4 wheels'` and
`'a 4 wheel base'` in one token. If you want the collapsed-whitespace case too,
match with `re.search(r'\b(four|4)[\s-]+wheel', PROMPT, re.I)` and keep the
ledger as the list of *stems*; either is a one-line change.

### B2 — The module docstring now makes a false claim about its own module — `src/robot_brain/test/test_prompt_drift.py:17`

**VERIFIED by reading the diff.** Line 17 still reads:

> No expected value in this module is typed by hand.

`SUPERSEDED_BODY_CLAIMS` (`:57-59`) is an expected value, and it is typed by
hand. The statement was true at `3a0958c` and this PR made it false.

This is not a style nit. #67 exists because a shipped document kept asserting a
fact the system had stopped having, and nothing went red. Shipping a newly-false
assertion in the opening statement of purpose of the very file that fixes it is
the same failure mode, one directory over, in a place even fewer people read.

The implementer declined this under "R4's minimal-diff spirit"
(`implementation.md:148-153`). R4 does not cover it: R4 scopes explicitly to
`AGENTS.md` — *"This is a surgical edit to one clause of line 3 — do not
reflow, restructure or 'improve' the rest of **the file**"* (`status.md:118-124`).
It says nothing about the test module, and it certainly does not license
leaving a false sentence standing.

**Fix direction.** One clause, e.g. *"Every expected value here is read from a
live source, with one deliberate exception —`SUPERSEDED_BODY_CLAIMS`, whose
limits `TestBodyDescription` states."* No restructuring needed.

### B3 — "the body has no live source here" is false, and the body number it excuses leaving unguarded is scheduled to change — `src/robot_brain/test/test_prompt_drift.py:116-120`, `AGENTS.md:201`

**VERIFIED.** The class docstring's load-bearing justification is:

> because the body has no live source here: `robot_description` ships a URDF
> and no Python

There *is* a live source for the body in this module, imported 80 lines above
at `:34`. `robot_backends.mock_world.RobotModel` is the robot's geometry:

```
$ python -c "from robot_backends.mock_world import RobotModel; ..."
shoulder_offset_y 0.18
shoulder_offset_z 0.5
reach_radius 0.85
min_column_height 0.0
max_column_height 1.2
```

And the prompt states one of those numbers as a body fact —
`AGENTS.md:201`: *"beyond the **0.85 m** reach"* — with nothing checking it.
Proved by mutation on the copy:

```
$ # /tmp/rt67 copy: reach_radius 0.85 -> 0.40  (D26: SO-101 is "~0.4 m reach")
$ python -m pytest src/robot_brain/test/test_prompt_drift.py -q
27 passed in 0.75s
$ # same for shoulder_offset_y 0.18 -> 0.99
27 passed in 0.76s
```

`grep -rn "0\.85\|reach_radius" src/robot_brain/` returns exactly one hit: the
prompt line itself. `TestSafetyEnvelope` cannot catch it — it scopes to
`section(PROMPT, 'The safety envelope')`, and 0.85 is in a worked example.

This is not a stale claim today (0.85 is correct), so it is not a defect the
sweep should have flagged as stale — but D26 pins the real arms at **~0.4 m
reach**, so this number is on the roadmap to go wrong, and the guard costs one
line against a dependency this package already declares
(`package.xml:20`, `<test_depend>robot_backends</test_depend>`).

**Fix direction — pick one, not both:**
(a) correct the docstring: the thing with no live source here is the
*drivetrain description*, not "the body"; or
(b) keep the wording honest by making it true — add
`assert f'{RobotModel().reach_radius:g} m reach' in PROMPT` beside the ledger,
and the sentence becomes "the drivetrain has no live source" on its own.

(b) is the better test and is cheaper than the ledger that was written. Either
way the current sentence must not ship as-is; it is the same species of
inaccuracy as B2.

---

## NOTE

### N1 — `test_the_descriptor_that_replaced_it_is_taught` passes on a prompt whose body sentence is nonsense — `test_prompt_drift.py:140-148`

**VERIFIED.** On the copy, I rewrote the opening sentence to *"a mystery box on
legs"* and left `3-omniwheel holonomic` only inside a fenced comment:

```
You are the brain of a household mobile manipulator: a mystery box on legs, an
extendable vertical column and two arms with grippers.

```
# TODO(old-notes): chassis was 3-omniwheel holonomic
```
```

```
$ python -m pytest .../test_prompt_drift.py -q
27 passed in 0.76s
```

The docstring at `:144-145` claims the pair is what stops either from passing
vacuously. It stops *one* vacuity (a deleted sentence); it does not stop the
phrase surviving somewhere useless. Every other test in this module scopes its
input — `section(...)`, `tool_table(...)`, `inline_words(...)` (which strips
fences via `without_fences`). This one alone matches raw `PROMPT`.

Not a BLOCK: the scenario needs someone to both rewrite the sentence *and* leave
the phrase in a fence, and the file currently has no such fence. But the fix is
one expression, and it would make the test say what its docstring says.

**Fix direction.** Scope to the intro, e.g. `PROMPT.split('\n## ', 1)[0]` (the
paragraph the class says it guards), or at minimum `without_fences(PROMPT)`,
which the sibling fixtures already export. Leave the *absence* check on raw
`PROMPT` — stricter is right in that direction.

### N2 — R2's cost argument for rejecting the URDF-derived test is measurably inflated (ruling-level)

**VERIFIED, and the manager asked to be told plainly: two of the three stated
costs do not survive measurement.**

R2 (`status.md:92-100`) and the class docstring (`:117-120`) reject deriving the
wheel count from `robot_description` because it "would cost this pure-Python
suite a colcon install tree and the xacro toolchain … and it would still have to
recover a wheel *count* by parsing English prose."

- *"a colcon install tree"* — already required. `robot_brain`'s suite only ever
  runs under `colcon test`, which runs after `colcon build`; the install tree is
  present by construction (`log/latest_test/robot_brain/command.log` shows
  colcon invoking pytest with `install/robot_brain/...` on `PYTHONPATH`).
- *"the xacro toolchain"* — already installed and already exercised in the same
  `pixi run test` run by `robot_description`'s own gate:
  ```
  xacro:      .pixi/envs/default/bin/xacro
  check_urdf: .pixi/envs/default/bin/check_urdf
  urdf_parser_py ok
  $ time xacro .../robot.urdf.xacro > /dev/null
  real 0m0.074s
  ```
- *"recover a wheel count by parsing English prose"* — false in both
  directions. The count is structural in the model, not prose:
  ```
  wheel links: ['base_left_wheel_link', 'base_back_wheel_link', 'base_right_wheel_link'] 3
  ```
  and the assertion would be `f'{len(wheels)}-omniwheel' in PROMPT` — English
  is parsed on the *prompt* side only, exactly as the shipped test already does.

What the measurement *does* confirm is a real cost the ruling never named.
`robot_brain`'s colcon test env carries only its declared deps on
`AMENT_PREFIX_PATH` (verified in `command.log`: `robot_brain, robot_mcp,
robot_safety, robot_backends, robot_world, robot_skills` — no
`robot_description`), so the change needs a new `<test_depend>` edge, and that
edge drags `ament_index_python` into the one package whose defining property is
that it needs no ROS at all (D21; `test_no_ros_runtime.py:31-35` names
`ament_index_python` explicitly as a module it refuses to see loaded).

**So: R2's conclusion survives; its stated reasoning does not.** No code change
is requested here — but the class docstring repeats the inflated version to
future readers (see B3), and that is what should be corrected.

Also worth recording for the follow-up R2 already schedules: there is a
*cheaper* candidate live source than the URDF —
`src/robot_description/package.xml:6` contains the literal string
`3-omniwheel holonomic base`. It is still hand-typed prose, so cross-checking it
buys consistency rather than truth, which is probably why it is not obviously
better than the ledger. Worth one line in the follow-up, not in this PR.

### N3 — R1's line is right in outcome, thin in argument (Claim B, answered)

**VERIFIED against the actually-served schemas**, not the dataclasses. I
enumerated `robot_mcp.tools.TOOLS[*].input_schema` from the running catalogue.
The complete argument surface is:

| tool | arguments |
|---|---|
| `navigate_to` | `location: string` |
| `grasp` | `object_id: string`, `side: enum[left,right]` |
| `place` | `pose{position{x,y,z}, orientation{x,y,z,w}}`, `side` |
| `move_gripper` | `side`, `pose{position, orientation}` |
| `extend_column` | `height: number` |
| `open_gripper`/`close_gripper` | `side` |
| `get_observation` | — |

There is **no** direction, heading, velocity, twist, yaw or base-pose parameter
anywhere. The only orientation quaternion the planner can write is a *gripper*
pose on `place`/`move_gripper`. Base orientation exists but is read-only —
`get_observation` returns `robot.pose.orientation`, and
`mock_world.py:117-118` says outright *"Base orientation is ignored on purpose:
the Mock reasons about distances, not headings."* `SafetyLimits.defaults()`
caps the base at a single scalar 0.6 m/s with no angular term. **R1's factual
premise holds: there is nothing at the seam into which a belief about holonomy
can be encoded.** The prompt is not under-describing a reachable affordance.

Where I disagree with the *reasoning*: "holonomic" is not inert to an LLM — it
unpacks to "can translate in any direction without turning" as reliably as the
sentence you refused to write. The distinction R1 draws is real but it is not
the one stated. What actually bounds the damage is not that the belief is
un-encodable at the tool seam (both phrasings are equally un-encodable); it is
that the only channel the belief can reach is the *prose report to Jaime* —
"I slid sideways up to the counter" — and a body noun in a body list produces
that far less often than an explicit capability sentence would. Same conclusion,
better justification. No change requested; recorded so the next prompt edit
applies the right rule ("describe the body, not the manoeuvre") rather than a
rule that will stop being true the moment a base skill grows a pose argument.

### N4 — `'4-wheel'` as a bare fragment is safe, with one benign edge

**VERIFIED.** Nothing in the current prompt matches, and I could not construct a
plausible legitimate prompt string containing `4-wheel`. The one real
false-positive class is a prompt that *disclaims* the old base ("this is not a
four-wheel base") — which would fail the ledger while being factually correct.
That is acceptable, because R1 already forbids that genre of sentence. No
change.

### N5 — The example observation omits a field the real observation always sends — `AGENTS.md:81`

**VERIFIED** by dumping `MockBackend().get_observation().to_dict()`: `robot.pose`
always carries an `orientation` quaternion, and every `objects[].pose` does too.
The prompt's example shows `"pose": {"position": {...}}` only. Line 100-102
correctly describes orientation as optional *on the way in*; the example is
abbreviated on the way *out*. Nothing parses the fenced observation JSON against
the live shape, so this is untested. **Pre-existing, not introduced here, and
defensible as abbreviation** — flagging only because #67 is about the prompt's
fidelity to the system and this is the other unguarded corner of it.

---

## Claims checked that produced no finding

- **Mutation check reproduces exactly (Claim C, first half).** On the copy, with
  `AGENTS.md:3` restored to "a four-wheel base":
  ```
  FAILED ...TestBodyDescription::test_a_superseded_body_claim_is_not_still_taught[four-wheel]
  FAILED ...TestBodyDescription::test_the_descriptor_that_replaced_it_is_taught[3-omniwheel holonomic]
  2 failed, 25 passed in 0.78s
  ```
  Exactly two failures, exactly the two named, nothing else in the module moves.
  The implementer's claim is accurate. **VERIFIED.**

- **The reflow is genuinely minimal (Claim H1).** `git diff --word-diff=plain`
  reduces the four-line churn to a single token change:
  `a [-four-wheel-]{+3-omniwheel holonomic+}\nbase`. The rewrapped paragraph is
  76/78/80/41 columns; every prose line in the file is ≤80 (the >80 lines are
  all table rows). R4 satisfied. **VERIFIED — no finding.**

- **The two test counts do not disagree (Claim G).** The ratchet counts
  *non-skipped, non-linter* tests; "57 passed" is the pytest total including
  `test_flake8`, `test_pep257`, `test_copyright`. The audit prints both:
  `robot_brain  57 tests ... non-lint 54  vs-base +0  ok`. 57−3 = 54, and
  53−3 = 50 = the old baseline. Baseline 54 is correct; nothing is being
  silently excluded. **VERIFIED — no finding.**

- **The sweep is complete (Claim F).** Re-run independently with my own terms
  over all 18 tracked files in the package —
  `wheel|omni|holonomic|chassis|caster|drivetrain|skid|differential|mecanum|axle`,
  then `dof|degrees of freedom|joint|elbow|shoulder|wrist|servo|feetech|sts3215|
  so-?101|lekiwi|xlerobot|motor|actuat`, then
  `camera|rgb-?d|depth|lidar|mic|sensor|head|webcam`, then
  `(two|three|four|2|3|4|6) *(arm|wheel|gripper|jaw|finger|camera|dof)`.
  `openclaw.robot.json` and `README.md` carry no body claims at all. The only
  shape claims in the package are `AGENTS.md:3-4` — base (fixed), "extendable
  vertical column" and "two arms with grippers", both still true under
  `spec.md:30-32`. The one thing the implementer's term list missed is *reach*
  (`AGENTS.md:201`) — not stale, but unguarded; that is B3, not a sweep failure.
  **VERIFIED.**

---

## Verdict

The prose fix is correct, minimal, in register, and green. The regression test
is real — it fails on the exact bug and on nothing else. What blocks is
narrower than the feature: **the ledger has a hole at the spelling the design
log itself uses (B1), and this PR ships two docstrings that are false about its
own code (B2, B3)** — which, on an issue that exists because a document kept
asserting something the system stopped being, is the wrong thing to wave
through. All three are one-line fixes.
