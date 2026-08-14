# Red team — full pass A (N+1), #67

Scope: **the whole `origin/main..HEAD` diff**, not the fix diff. Assigned lens:
**the manager's rulings in `status.md` are review targets, not scaffolding.**
Findings were formed before reading `red_team.md` / `red_team_fix.md`; those were
read only at the end, to avoid re-reporting closed items and to attribute prior
art honestly (NOTE 7 of the fix pass is prior art for BLOCK 2 — see there).

**Verdict: 3 BLOCK, 7 NOTE.** All three BLOCKs are ruling-level or
documentation-correctness, none is a behavioural bug in the shipped guard: the
guard itself is sound and I proved it by mutation. But the run's signature defect
— *a claim about the code that is wrong in prose rather than in code* — has now
occurred a **fourth** time (B2, B3, F1, and now BLOCK 1), in an owned path, in the
one file three passes explicitly cleared.

---

## Baseline: is `pixi run test` green? Yes — with an environment caveat

**VERIFIED.**

```
$ pixi run test
Summary: 761 tests, 0 errors, 0 failures, 0 skipped
10 packages, 761 tests collected, 731 of them non-linter
AUDIT PASSED: every expected package collected tests
All stages passed.                                  # EXIT=0
```

`robot_brain` 58 tests / 55 non-lint, `vs-base +0` against the bumped
`scripts/test_baseline.json:6` (50 → 55; 5 new tests = 2 parametrised ledger
tests + 2 pattern controls + 1 reach test). Ratchet is correct.

**Caveat, and it is not this branch's bug.** Two of my first three full runs
failed the integrity audit with a spurious `no-result` on a *different* package
each time (`robot_mcp`, then `robot_brain`), each time at a package that spawns
subprocesses, while that package's own `pytest.xml` showed all tests passed.
I reproduced the cause deliberately: **two concurrent `pixi run test` invocations
in one worktree race in the shared `build/` tree.**

```
$ ( pixi run test > /tmp/rt_conc_1.log ) & ( pixi run test > /tmp/rt_conc_2.log ) & wait
EXIT1=1   EXIT2=0
/tmp/rt_conc_1.log:
  File "scripts/check_test_integrity.py", line 765, in delete_result_files
    path.unlink()
FileNotFoundError: [Errno 2] No such file or directory:
  '.../build/_workspace_tooling/pytest.xml'
```

VERIFIED. Two consequences, both retro-shaped and both recorded as NOTE 7 below:
parallel agents in one worktree must not run the suite concurrently, and
`check_test_integrity.py:765` should use `unlink(missing_ok=True)`.

---

## BLOCK

### B-A1 — `src/robot_brain/README.md:71-73` still says "No expected value there is typed by hand", and this PR is what made that false

**VERIFIED.**

`src/robot_brain/README.md:68-73`:

> `test/test_prompt_drift.py` closes that: every checkable claim is compared
> against the **live** source that owns it — `robot_mcp.tools.TOOL_NAMES`, each
> tool's own `inputSchema`, `robot_safety.SafetyLimits.defaults()` and
> `robot_backends.default_world()`. **No expected value there is typed by hand**,
> and a word in backticks that names nothing real fails the suite.

This is the **last surviving copy of the exact sentence this PR spent two rounds
correcting** in `test_prompt_drift.py:17`. Confirmed it is the only one left:

```
$ grep -rn "typed by hand" --include=*.md --include=*.py . | grep -v docs/features
src/robot_brain/README.md:72:expected value there is typed by hand, and a word in backticks that names
```

Two ways it is now wrong, both caused by this diff:

1. `SUPERSEDED_BODY_CLAIMS` (`test_prompt_drift.py:70-72`) is a hand-typed
   expected value in that module. The PR's own replacement docstring
   (`test_prompt_drift.py:17-22`) now says the opposite — *"It is an aim, not an
   invariant"* — so **two files in one package assert contradictory things about
   the same property**, and the one that is wrong is the one a human reads.
2. The enumeration of live sources is now incomplete in a second way: this PR
   added `robot_backends`' `RobotModel` (via `default_world().robot`) as a
   checked source for the reach, and a hand-typed ledger as the mechanism for the
   drivetrain. Neither is in the list.

**Why this is a BLOCK and not a NOTE.** The manager ruled B2 a BLOCK on precisely
this ground — *"a newly-false claim in a statement of purpose, in the PR whose
subject is a stale claim"* (`status.md:176`) — and then ruled F1 a must-fix for
the same reason one round later. The README sentence is the same claim, in the
same package, in an owned path, and it is the *user-facing* one: it is what the
package README promises a reader about how much the drift suite guarantees.
Applying the run's own accepted standard, this is a BLOCK.

**Why three passes missed it.** Everyone scoped the README check to *body*
claims and cleared it on that basis — `context.md:110`
("`robot_brain/README.md` | no body-description prose found") and
`red_team.md:320` ("`openclaw.robot.json` and `README.md` carry no body claims at
all"). Both are true. Nobody asked whether the README's claims about *the test
suite* survived the PR.

**Fix direction.** One edit in `src/robot_brain/README.md:63-73`: restate the
guarantee the way the module docstring now does (an aim, with named exceptions),
and either extend the live-source list or stop enumerating it. Do not re-derive a
new absolute claim — F1's lesson was that this sentence *has always* overclaimed;
the fix is a framing that stays true as tests are added.

---

### B-A2 — R2's rationale is wrong for the third time, and the wrong version is shipped in source — `status.md:154-156`, `src/robot_brain/test/test_prompt_drift.py:134-139`

**VERIFIED by mutation.** (Prior art: `red_team_fix.md` NOTE 7 flagged the
over-reach and proposed a replacement argument; the manager neither promoted it
nor routed it outward — see NOTE 1 below. I am re-raising it at BLOCK because
**the replacement argument NOTE 7 proposed is also wrong**, so the ruling has now
been through three rationales and none of them holds.)

R2 rejects deriving the wheel count from the URDF. Round 1 found the first
rationale (colcon/xacro cost) inflated; the manager corrected it to:

> it needs a new dependency edge that drags `ament_index_python` into the one
> package whose identity is "no ROS" (D21, asserted at
> `test_no_ros_runtime.py:31-35`). — `status.md:154-156`

and that reasoning is baked into shipped source at
`test_prompt_drift.py:134-139`:

> reading that would put `ament_index_python` on this package's path -- and this
> is the one package whose defining property is that it needs no ROS at all
> (D21; `test_no_ros_runtime` names that module as one it refuses to see loaded).

**The cited assertion does not cover the rejected design.** Repro, on a `cp -r`
copy under `/tmp` (worktree untouched):

```
$ # added `from ament_index_python.packages import get_package_share_directory`
$ # to /tmp/rt_a/robot_brain/test/test_prompt_drift.py
$ pytest test/test_no_ros_runtime.py test/test_prompt_drift.py -q
31 passed in 0.81s
```

`test_no_ros_runtime.py`'s `PROBE` (lines 22-36) inspects `sys.modules` of a bare
subprocess that imports **`robot_brain` only**; `FORBIDDEN_ROOTS` is
`('rclpy',)` and the static scan walks `robot_brain.__file__`'s directory, i.e.
the runtime package, **not `test/`**. A URDF read would live in `test/`, where
nothing forbids it.

**And the replacement argument is mis-scoped too.** `red_team_fix.md` NOTE 7
proposed falling back on *"a `<test_depend>` on `robot_description` puts a ROS
dependency into the one package defined by not having any, D21"*. But D21's
no-ROS property, as this repo itself states it, is about the **deployed assets**,
not the test suite — `test_no_ros_runtime.py:9-15`: *"Anyone deploying the agent
copies those files onto a Raspberry Pi that has no ROS install at all, so an
`rclpy` import here … would make the assets unloadable where they are used"*, and
`README.md:26-28`: *"they load from a source checkout and from a symlink-installed
build alike, with no ament index and no ROS graph."* A `<test_depend>` cannot
affect either. The tests already run only on the laptop, under colcon, against a
build tree the manager itself measured as already required.

So: **the conclusion is right and every recorded reason for it is wrong.** That
is exactly the failure mode the N+1 rule names — the code matches the ruling
perfectly and the ruling is unsound — and it is load-bearing, because this
docstring is what PR3–PR7 (column, arms, gripper, camera) will read when they hit
the same fork. A reader who trusts it will add another hand-typed row without
trying; a reader who checks it will find the objection hollow and may add the
coupling for want of a real reason.

**Fix direction.** State the reason that actually holds: **D12/D13's seam** — *"the
skill API is the seam between hardware-agnostic brain and swappable
drivers/URDF"* (D13), *"brain is hardware-agnostic via the skill API"* (D12), and
CLAUDE.md invariant 1. `robot_brain` reaching into `robot_description` puts the
hardware layer on the brain's side of the seam, which is the thing the seam
exists to prevent. Then say plainly that **no guard enforces this** — the choice
is a design call, not a constraint — so the next reader is not misled twice.
(This is also the substance of B-A3: the seam is *why* the prompt's body prose is
structurally ungatable, which is a decision-log fact, not a docstring aside.)

---

### B-A3 — the ruling "this feature needs no `decisions.md` entry" is wrong, and every ruling in this run evaporates at merge

**VERIFIED** (mechanism), **argued** (the judgement).

The manager ruled that #67 implements D26/D29 and decides nothing new, so no
D-entry. I disagree, on three checkable grounds.

**1. The repo's own precedent puts this squarely in the log.** D-entries in
feature PRs are normal here, not exceptional:

```
$ git log --oneline -3 -- docs/design/decisions.md
3a0958c PR2 — Mobile base URDF (3-omniwheel holonomic, LeKiwi crib) (closes #65) (#66)
aeb7463 ops: make the test-count ratchet self-maintaining ... (#63)
9201c97 PR1 — robot_description package + CI expand/parse gate (closes #61) (#62)
```

And both D27 and D29 are exactly the register at issue: D29 opens *"this decision
is what filling `urdf/base.xacro` actually turned out to require"*, and its sixth
clause is *a lesson rather than a decision* ("relational assertions are not
enough, and finding out cost a red-team round"). D19 — "`close_gripper` on an
empty gripper returns success plus a flag" — shows the bar is not high.

**2. This PR settles a real fork with a rejected alternative and an accepted
gap.** Namely: *`robot_brain` does not read `robot_description`; the prompt's body
prose is guarded by a hand-maintained superseded-claim ledger; the accepted
consequence is that the ledger pins claims already known stale and cannot detect
the next one.* That binds PR3–PR7, each of which will add body prose to this same
file. And per B-A2 the *reason* is architectural (D12/D13's seam), which makes it
a better decision-log entry than a docstring aside: the prompt carries hardware
facts across a seam designed to keep hardware out of the brain, so there is no
live source on the brain's side to check them against. That is a structural
property of the architecture, not an oversight awaiting a follow-up.

**3. The chosen home for the reasoning is deleted at merge.** CLAUDE.md:
`docs/features/<slug>/` is ephemeral, *"deleted at merge (CI keeps
`docs/features/` empty on `main`)"*, and `.github/workflows/guards.yml` enforces
it as CI's only check (`git ls-files 'docs/features/*'` must be empty). So R1–R4,
R3', **both ruling corrections**, and both disposition tables cease to exist on
`main`. The manager wrote the corrections *"here rather than quietly keeping the
conclusion, because the reasoning is what the next person will reuse"*
(`status.md:143-145`) — into the one directory CI guarantees will not survive.
The follow-ups and the retro are routed outward and do persist as GitHub
comments; **the rationale is not routed anywhere.**

Together: a durable convention, a rejected alternative, an accepted gap, and an
architectural reason — with the only surviving record being a docstring that
B-A2 shows is misdirecting. That is the "least-reviewed, longest-half-life
defect" shape.

**Fix direction.** A short D30, in D29's register: the decision (prompt body prose
is guarded by a hand-typed superseded-claim ledger in `robot_brain`'s own suite),
the rejected alternative and the *correct* reason (D12/D13's seam — the brain is
hardware-agnostic, so it does not reach into `robot_description`; the cost
argument was measured and does not hold; no guard enforces the seam here), and
the accepted gap (the ledger pins known-stale claims only; PR3–PR7 will add four
more ungated body facts). If the manager still judges a D-entry premature because
"the ledger is a patch, not a fix" — a fair objection — then the minimum is to
move the surviving rationale into `src/robot_brain/README.md`, which is in an
owned path, is where this package documents what its tests do and do not
guarantee, and needs an edit anyway for B-A1.

---

## NOTE

### N-A1 — two of the six "routed to follow-up" NOTES were not actually routed

**VERIFIED by reading.** `status.md:194` says *"all six other NOTES — **follow-up**
— routed below"*. The outward list (`status.md:203-209`) carries fix-pass NOTE 3
(→ follow-up 4), NOTE 4 (→ 5), NOTE 5 + NOTE 6 (→ 6), plus round-1 N5 (→ 3) and
two of the manager's own (1, 2). **NOTE 7** (the `ament_index_python` over-reach)
and **NOTE 8** (a second ledger row ships without controls) appear nowhere in it.
NOTE 7 is now B-A2. NOTE 8 should be routed, or its docstring concession
(`test_prompt_drift.py:181-182`, *"A second row brings its own controls"*) is the
whole of the plan.

### N-A2 — `known_locations`: the manager's deferral is **correct**. Ruling it plainly

**VERIFIED (the gap), and I uphold the ruling.**

The gap is real and I reproduced it. Adding `hallway` to the seed world on a
`/tmp` copy:

```
$ # added "hallway" to /tmp/rt_a/robot_world/robot_world/default_world.json
$ pytest test/test_prompt_drift.py -q
28 passed in 0.77s
```

while `AGENTS.md:89` enumerates four locations and `AGENTS.md:97-98` says
*"`known_locations` is the complete set of names `navigate_to` accepts. There are
no others."*

**But it is not a body-description claim, so it is not inside acceptance
criterion 2.** #67 asks for a sweep of *"the rest of `src/robot_brain/` for other
**body-description** claims"*. The body is the base, column, arms, grippers and
camera; `known_locations` describes the **world the robot moves through**, not the
robot. Reading "body-description" as "any factual claim in the prompt" makes the
sweep unbounded — the entire prompt is factual claims, and gating all of them is
the whole purpose of the module, not the scope of one issue.

The sweep that *was* required is complete: the base is corrected and gated; the
reach (`AGENTS.md:201`) is now gated against `RobotModel` (B3); the column /
shoulder coupling the prompt states (`AGENTS.md:48`, `:142`) is **true** — I
checked `mock_world.py:114-122`, `shoulder()` returns
`column_height + shoulder_offset_z`; "two arms with grippers" matches D26.
**AC2 is met.** `known_locations` is a legitimate follow-up, and it is already
routed as `status.md:207`.

One thing worth saying anyway: the manager's own follow-up text calls it *"the
behaviourally worst of the gaps"*, and the fix is ~4 lines using machinery this
module already imports (`default_world().locations`, `section()`). Deferring the
worst known defect in the file you are editing when the fix is that cheap is
defensible but thin. Not a blocker.

### N-A3 — R1 is right in outcome; its stated principle does not justify its own wording

**Argued.** R1's line is *"state what the body is, not what the planner may do
with it"* — and then mandates the word **"holonomic"**, whose entire meaning *is*
what the body may do. A pure body descriptor would be "a three-omniwheel base".
The manager already conceded half of this in the correction (`status.md:161-166`)
and kept the wording; I agree with keeping it, but for a reason R1 never states:
**vocabulary consistency with `spec.md:29` and D26/D29 is the actual point of the
issue**, and the strafe risk is bounded not by the word choice but by the tool
table plus `AGENTS.md:97-98` ("There are no others"). If R1's principle survives
into a future prompt edit, it will mislead — the operative rule is *match the
decision log's term*, not *avoid affordance words*.

### N-A4 — R3' puts the deploy note somewhere nobody reads at deploy time

**Argued.** R3' routes the "an already-deployed Pi copy stays stale until someone
re-runs the `scp`" fact to the **PR description**. PR descriptions persist, but
nobody re-reads a merged PR description before deploying. The durable home is
`src/robot_brain/README.md:91-99`, the "Installing the agent on the Pi" section —
an owned path, already the place that documents the `scp`, and already needing an
edit for B-A1. One sentence there costs nothing and lands where the reader is.
(UNVERIFIED that the note reaches the PR at all: no PR is open yet —
`gh api repos/:owner/:repo/pulls` returns nothing for this branch.)

### N-A5 — the ledger's remaining escape hatch, with the executed repro

**VERIFIED.** Already routed as `status.md:209` follow-up 6; recording the run so
the follow-up has evidence. On the `/tmp` copy, rewriting `AGENTS.md:3` to
`a four‑wheel base (3-omniwheel holonomic)` — U+2011 non-breaking hyphen —
leaves **28 passed**: the absence pattern `\b(?:four|4)[\s-]+wheel` does not
match, and the presence test is satisfied by the parenthetical. Low likelihood in
hand-typed prose; correctly a follow-up, not scope.

The guard's *working* half is genuinely strong, and I verified each half rather
than reading it:

| mutation on the `/tmp` copy | result |
|---|---|
| revert `AGENTS.md:3` to "a four-wheel base" | **2 failed** (absence + presence) |
| descriptor moved into a fenced note in the intro | **1 failed** (presence) — the N1 fix holds |
| backtick the new words (`` `3-omniwheel` ``) | **2 failed** — R3 holds |
| `RobotModel.reach_radius` 0.85 → 0.40 | **1 failed** — B3's fix holds |

### N-A6 — "two arms with grippers" has a live source, already imported, and is ungated

**Argued.** `Side` is imported at `test_prompt_drift.py:45` and has exactly two
members; the prompt's arm count is checked against nothing. By B3's accepted
principle ("`RobotModel` *is* a live body source, and an unchecked body claim
that has one is a BLOCK"), this is the same shape. I am **not** raising it to
BLOCK: D1 fixes "exactly 2 arms (not >2)", so the mutation is a non-scenario, and
`TestBodyDescription`'s docstring discloses the gap honestly. Worth adding to
follow-up 1 ("the prompt's body prose has no live source"), which currently
implies the drivetrain is the only such case.

### N-A7 — two ops items, both out of this PR's owned paths

**VERIFIED.**

1. `scripts/check_test_integrity.py:765` — `path.unlink()` without
   `missing_ok=True` crashes the whole driver when a concurrent run removes the
   file first (traceback above). One-word fix; retro/ops, not this PR.
2. `CLAUDE.md` says the decision log is *"D1–D28"* while `decisions.md` now ends
   at **D29** (landed in #66). Stale doc-claim of exactly the class this feature
   exists to fight, in a file every agent auto-loads. Out of owned paths — route
   as a follow-up.

---

## Claims I checked that produced no finding

- **The prose edit itself** (`AGENTS.md:3-6`). Word-diff is a single clause plus
  a reflow; every line ≤ 80 cols; no restructuring. R4 satisfied.
- **R1's premise.** Re-verified `NavigateTo` is a one-field dataclass
  (`robot_skills/skills.py`), and the served schemas carry no direction, heading,
  velocity or base-pose argument. The prompt adds no affordance sentence.
- **The ledger's data shape.** `MappingProxyType` prevents mutation; a future row
  whose *replacement* descriptor matched its own superseded pattern would make
  the absence and presence tests contradictory — red, not silent.
- **`_STATED_REACH`** (`:75`). `(\d+(?:\.\d+)?) m reach` matched against a
  `float`-normalised set comparison handles `0.850` and `1` correctly; the
  empty-set case (phrase reworded away) fails, as documented.
- **The positive/negative pattern controls.** Not tautologies — they would catch a
  typo in the pattern, which the absence test structurally cannot.
- **`openclaw.robot.json`, `agent.py`, `setup.py`, `package.xml`** — no body
  claims, no stale claims, unchanged and correctly unchanged.
- **The test-count ratchet.** 50 → 55 is exactly the 5 tests added; audit reports
  `+0`.

## Worktree state

`git status --porcelain` is **empty** at the end of this pass, as it was at the
start. Every mutation ran on `cp -r` copies under `/tmp/rt_a/`; nothing in the
worktree was edited, checked out, stashed or reset. Build artifacts under
`build/`, `install/` and `log/` were regenerated by five `pixi run test`
invocations — all gitignored.
