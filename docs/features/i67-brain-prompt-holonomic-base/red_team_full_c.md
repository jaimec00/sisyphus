# Red team — full pass C (#67), against `origin/main..HEAD` @ `7e97a1f`

Full adversarial pass over the **whole** diff (12 commits), run under the N+1
rule in `.claude/agents/red-team.md`. Findings were formed by execution before
the prior reports (`red_team.md`, `red_team_fix.md`, `red_team_full_a.md`,
`red_team_full_b.md`) were read; those were then consulted only to avoid
re-reporting closed items.

**Result: 4 BLOCKs, 3 NOTES.** Three of the four BLOCKs are against artifacts
that **no red-team pass has ever seen** — `docs/design/decisions.md`'s D30
(commit `5837211`, written after both full passes) and the spec.md convention
it breaks. The fourth is a fragility in the presence check that every pass so
far measured on the *absence* regex and never on the presence one.

Baseline established first: **`pixi run test` is green** — 761 tests, 0 errors,
0 failures, 0 skipped; `robot_brain` 58 total / 55 non-lint, ratchet `+0`,
audit passed (`/tmp/rt_full_c_test.log`). `git status` clean before and after,
except this report.

All mutation work was done on `cp -r` copies under `/tmp/rtc` (a full
`colcon build` of `src/` outside the worktree). The worktree was never modified.

---

## BLOCK 1 — D30 claims the drift suite "compares every prompt claim that has an owner", and this run's own measurements disprove it — including for the very category it lists (`docs/design/decisions.md:116`)

**VERIFIED.**

> `test_prompt_drift.py` already compares **every prompt claim that has an
> owner** against it — tool names, argument schemas, safety limits, failure
> codes, **seed-world ids** — and structurally could not have caught this one

This is false, and the counter-example sits inside D30's own enumerated list.
I reproduced it myself rather than inherit it:

```
# on /tmp/rtc: add a fifth location to the seed world
$ python3 -c "...d['locations']['hallway']={...}..."   # robot_world/default_world.json
$ colcon test --packages-select robot_brain --pytest-args -k "not flake8 and not pep257 and not copyright and not openclaw"
tests=31 failures=0 errors=0
```

The prompt enumerates `known_locations` twice (`AGENTS.md:89`, `:168`) and
states flatly *"There are no others"* (`AGENTS.md:97-98`). That claim has an
owner — `default_world().locations` — and the suite does not compare it: it
only checks the one-way containment that example calls use real locations
(`test_prompt_drift.py:366-374`). A seed-world id added, renamed or removed
leaves 31/31 green while the prompt tells the planner a real place does not
exist.

That is not the only one. The manager's own `status.md:291-292` lists four more
unguarded claims **with owners**, each recorded as VERIFIED-by-mutation earlier
in this same run: `schema_version: 1`, `counter_1.z`, the column travel quoted
in the worked examples, and (per pass A's N-A6) the arm count. Plus the two I
add below in BLOCK 2.

**Why this is a BLOCK and not a wording quibble.** `docs/features/` is deleted
at merge (CLAUDE.md; `guards.yml`), so `status.md`'s accurate list of unguarded
claims **ceases to exist on `main`**, while D30's sentence saying the opposite
is **append-only and permanent**. That is precisely the #55 failure the task
brief names: the correction lives in the ephemeral doc and the log keeps the
sentence it corrects. It is also the exact defect class this feature exists to
fight — prose asserting something false about the code — now written into the
one file CLAUDE.md declares wins over every other doc.

**Fix direction.** Amend the clause to what was measured, e.g. *"already
compares many prompt claims against their owners — tool names, argument
schemas, safety limits, failure codes, the ids used in worked examples — and
structurally could not have caught this one; several other owned claims
(`known_locations`, `schema_version`, the arm count) are unguarded too, routed
as follow-ups."* Since the entry is append-only, a corrective sub-bullet is
acceptable if editing is not — but the false categorical must not ship as the
surviving statement.

---

## BLOCK 2 — D30's "Where a live source did exist, it is used" and its rule "if the Mock reasons about it, check it" are both false of the one sentence this PR rewrote (`docs/design/decisions.md:119`)

**VERIFIED.**

> **Where a live source did exist, it is used.** … The rule this sets: **if the
> Mock reasons about it, check it — only what lives beyond the seam gets a
> ledger row.**

The prompt's body sentence is `AGENTS.md:3-4`:

> a 3-omniwheel holonomic base, an extendable vertical column and **two arms
> with grippers**

Three of those four claims are things the Mock reasons about, all with live
sources on this side of the seam, and none of them is checked. Mutations on
`/tmp/rtc`, prompt-only, all 31 non-lint `robot_brain` tests green each time:

| mutation to `AGENTS.md:3-4` | live source that contradicts it | result |
|---|---|---|
| `two arms` → `seven arms` | `robot_skills.SIDE_ORDER` / `Side` (exactly 2; `mock_backend.py:197` emits one gripper observation per member) | **31 pass, 0 fail** |
| `an extendable vertical column` → `a fixed vertical column` | `RobotModel.min/max_column_height` (0.0–1.2 m of travel), `extend_column` | **31 pass, 0 fail** |
| `two arms with grippers` → `two arms with suction cups` | `GripperState`, `open_gripper`/`close_gripper`, `grippers[]` on the wire | **31 pass, 0 fail** |

`Side` is **already imported** by this module (`test_prompt_drift.py:45`) and
already used in `live_vocabulary()`. So the arm count is not a "no live source"
case at all — it is a live source that was in scope, in hand, and skipped.

Pass A raised the arm-count gap as NOTE N-A6 and declined to escalate it, on
the explicit ground that *"`TestBodyDescription`'s docstring discloses the gap
honestly"* (`red_team_full_a.md:343-352`). That ground no longer holds: D30 —
written after that pass — does not disclose the gap, it **denies** it, in the
permanent log, with a bolded rule the PR does not follow. The class docstring
(`test_prompt_drift.py:148-151`) is still honest; D30 contradicts it.

`docs/design/decisions.md:120` compounds it: *"The column, the arms, the
gripper and the camera each add body prose to this same file, and each lands
ungated unless somebody adds a ledger row or **finds it a live source**."* The
column, the arms and the gripper are **already** in that sentence, already
ungated, and each already **has** a live source that nobody looked for.

**Fix direction — prefer the code fix, it is cheaper than the prose fix.** Add
one assertion in `TestBodyDescription`, in the style of the reach check, that
pins the arm count to `len(SIDE_ORDER)`; that makes D30's rule true, closes a
real body gap in the exact sentence #67 is about, and costs ~4 lines with an
already-imported symbol. (The column/gripper claims can stay follow-up 1 if
scope is a concern — but then D30's bullet must say so.) If the manager instead
keeps the gap, D30:119-120 must be corrected to say the arm count is a live
source that was *declined*, not one that does not exist.

---

## BLOCK 3 — the presence check breaks on a pure re-wrap of the sentence D30 says PR3–PR7 will extend (`src/robot_brain/test/test_prompt_drift.py:176-178`)

**VERIFIED.**

```python
introduction = without_fences(PROMPT).split('\n## ', 1)[0]
assert current.lower() in introduction.lower()
```

`current` is the raw literal `'3-omniwheel holonomic'` and the match is a raw
substring over hard-wrapped Markdown. `AGENTS.md:3` is **76 columns** and ends
on the word `holonomic`. Four more characters anywhere earlier in that sentence
pushes `holonomic` onto the next line, and the guard goes red on a prompt that
is byte-for-byte correct and uses the repo's exact canonical term:

| mutation on `/tmp/rtc` (semantics unchanged) | result |
|---|---|
| wrap moved one word left: `…a 3-omniwheel\nholonomic base, an extendable…` | **FAIL** `test_the_descriptor_that_replaced_it_is_taught[3-omniwheel holonomic]` |
| double space: `a 3-omniwheel  holonomic` | **FAIL**, same test |

The failure message is *"the prompt never introduces the robot as '3-omniwheel
holonomic'"* — which is the opposite of the truth, on a prompt that says
exactly that.

**Why BLOCK, not NOTE.** This is the extensibility trap the rubric names:

1. This exact edit **already happened once in this PR** — `implementation.md`
   records that the new clause is 11 characters longer and that the paragraph
   had to be re-wrapped for it.
2. D30:120 states that PR3–PR7 will each add body prose *to this same
   sentence*. Every one of those additions re-wraps the paragraph.
3. The tempting repairs for a PR3 author facing a false red are to un-wrap the
   line (no linter checks `.md` width) or to weaken/delete the guard — which is
   how a guard dies. The whole point of this PR is a durable guard.
4. The fix is one line.
5. `red_team_fix.md:154-156` concluded *"the ~80-column hand wrap is not a
   hole"*. That is true of the **absence** regex (`[\s-]+` eats a newline —
   confirmed) and false of the **presence** check, so the existing record
   actively points a reader away from this.

This is distinct from pass B's NOTE 2 (`red_team_full_b.md:177`), which is
about a *semantic* reword (`three-omniwheel`) that pass B argued is
intentionally a decision prompt. There is no decision to make here: the term is
unchanged and canonical; only whitespace moved.

**Fix direction.** Normalise before matching — `introduction = ' '.join(
without_fences(PROMPT).split('\n## ', 1)[0].split())` — or make the descriptor
whitespace-flexible the way the absence pattern already is. Note the asymmetry
worth stating in the docstring: the absence side is wrap-tolerant, the presence
side is not.

---

## BLOCK 4 — D30 is the first decision in the log with no row in `docs/design/spec.md`, breaking a 29-for-29 convention on the file agents are told to read first

**VERIFIED.**

```
$ grep -o "D[0-9]\+" docs/design/spec.md | sort -u -V
D1 … D29            (plus D435, a RealSense part number)
$ grep -o "^- \*\*D[0-9]\+" docs/design/decisions.md | ...
D1 … D30
```

Every decision D1–D29 has landed a bullet in `spec.md`, including the ones that
decide nothing about the robot's body: **D25** (the red-team gets a shell) and
**D28** (the ratchet) both sit under `## Ops & gates`. D30 lands none.

The consequence is concrete, and it is the manager's own G3 argument applied
one file further: `docs/features/` is deleted at merge, so after this PR the
durable invariant *"`robot_brain` takes no dependency on `robot_description`;
the prompt's body prose is gated by a hand-typed ledger that must gain a row
per superseded body fact"* exists **only** as a paragraph in the append-only
log and a docstring inside a test file. CLAUDE.md points every agent at
`spec.md` as "what the robot currently is" and at `decisions.md` as "why". A
PR3–PR7 author following that instruction reads about the launcher, the
ratchet and the red-team's shell under `## Ops & gates`, and does not learn
that the sentence they are about to extend has a gate they must feed — which
is the single thing D30:120 says binds them.

**Fix direction.** One bullet under `spec.md`'s `## Ops & gates` (or
`## Brain & architecture`), e.g. *"**The prompt's body prose is gated by a
ledger, not by the URDF:** `robot_brain` takes no dependency on
`robot_description`; `SUPERSEDED_BODY_CLAIMS` in `test_prompt_drift.py` pins
each superseded body fact and must gain a row when a decision supersedes
another one (D30)."*

---

## NOTE 1 — the README's "the gap is exactly where the prompt describes the body" is the same overclaim, one hedge weaker (`src/robot_brain/README.md:75-76`)

**VERIFIED** by the same `hallway` mutation as BLOCK 1. `known_locations` is
not a body claim, has an owner, and is unguarded — so the gap is not "exactly"
the body. The sentence is saved from BLOCK by the immediately preceding "That
is the aim, not a guarantee" and by a coherent narrow reading (the aim is about
where *expected values* come from, and `known_locations` has no assertion at
all, so it is neither live-sourced nor hand-typed). Worth one word of
precision — "the gap this decision is about" rather than "the gap is exactly" —
given this is the third attempt at this sentence across two files.

## NOTE 2 — D30:117's grammar attaches both assertions to the pattern

"maps the descriptor … to a pattern matching the claim it retired …, asserted
absent from the prompt and present in its introduction." The pattern is
asserted absent; the **descriptor** is asserted present. Cosmetic, but in an
entry whose subject is precision about what the code does.

## NOTE 3 — the pattern's false-positive surface is undocumented

`\b(?:four|4)[\s-]+wheel` fires on any legitimate future prose containing "a
four-wheel cart" as a *world object*. `test_no_matcher_fires_on_the_body_we_
actually_have` covers the numbers next door but not this class. Follow-up 9 in
`status.md` records the false-**negative** blind spots; the false-positive side
is not recorded anywhere. Cheap to add a sentence to the ledger's comment.

Already-routed items I re-confirmed and am **not** re-reporting: the controls
test hardcodes the single ledger key (`status.md` follow-up 7); the Unicode-dash
and `4wheel` blind spots (follow-up 9); `known_locations`, `schema_version`,
`counter_1.z` and the examples' column travel as unguarded owned claims
(follow-ups 4, 5); the `check_test_integrity.py:765` concurrency crash
(follow-up 8, out of scope, explicitly excluded from this pass).

---

## What I verified and found sound

- **Every technical claim in D30:118 is true, and I re-measured each one
  independently rather than trusting the entry.** Probe test added to a
  `/tmp/rtc` copy and run under real `colcon test`:
  - `get_package_share_directory('robot_description')` → **`PackageNotFoundError`**;
    `AMENT_PREFIX_PATH` for a `robot_brain` test is exactly
    `robot_brain:robot_mcp:robot_safety:robot_backends:robot_world:robot_skills`
    plus the pixi env. `robot_backends`, `robot_mcp`, `robot_world` resolve;
    `robot_description`, `robot_bringup`, `robot_perception` do not. The claim
    that the check "genuinely needs a `<test_depend>`" is **true**.
  - `import rclpy` inside a `robot_brain` test **succeeds and nothing goes
    red**. `test_no_ros_runtime`'s probe imports only `robot_brain` in a bare
    subprocess (`:23-38`) and its scan walks `os.path.dirname(robot_brain.
    __file__)` (`:105`), i.e. the runtime package, never `test/`. The claim
    "`test_no_ros_runtime` does **not** stop a test-time ROS import" is
    **true** — and, notably, is the first version of this reason in the run
    that survives measurement.
  - The colcon install tree and xacro **are** already required by the same
    `pixi run test` (`test_description.py:59-67` resolves everything through
    `get_package_share_directory`, "no source-tree fallback, by design").
  - "the URDF holds the wheel *count* structurally but the words 'omniwheel'
    and 'holonomic' only in a comment": expanded `robot.urdf.xacro` with the
    real CLI — **three `continuous` `*_wheel` joints** present; `omniwheel`
    and `holonomic` appear **zero** times in the expansion and only in comments
    in `base.xacro`. True, and if anything understated.
  - The D12/D13 seam characterisation holds **as narrowed**: D13 literally puts
    "drivers/URDF" on the swappable side of the skill-API seam. `robot_backends`
    is on that side too but its contents (world ids, poses, reach behaviour)
    reach the brain *through* the seam on the wire; nothing from the URDF ever
    does. The distinguishing word is "meets", and it is doing real work.
- **`4 wheels` really is `decisions.md`'s own spelling of D1's base**
  (`decisions.md:78`, *"the '4 wheels' aesthetic of D1"*), and the literal list
  it replaced (`git show d92150f`: `('four-wheel','4-wheel','four wheel')`)
  really does let through `4 wheels`, `a 4 wheel base` and `four  wheel base`
  while catching the other five controls. `test_the_matcher_catches_…`'s
  docstring describes its own control set accurately.
- **The reach check is real and correctly two-directional.** `reach_radius`
  0.85 → 0.40 fails it; re-spelling `0.85 m reach` → `0.85m reach` fails it
  (empty set ≠ `{0.85}`), so a reword-away is caught too.
- **The manager's ruling corrections.** R1's re-statement ("describe the body,
  not the manoeuvre") survives: `NavigateTo` has one field, `location: str`,
  and no served schema carries a heading, twist or base pose. R2's *conclusion*
  survives on the measured reason now in D30:118. R3/R4 hold (no new backticks;
  the reflow is one paragraph, all lines ≤ 80). The AC2 disposition on
  `known_locations` — a **world** claim, outside criterion 2's "body-description
  claims" — is right on my own independent reading; reading criterion 2 as "any
  factual claim" makes the required sweep unbounded, and the gap is correctly
  routed as follow-up 4.
- **Ratchet.** `scripts/test_baseline.json` `robot_brain` 50 → 55 is exactly
  the 5 new tests (3 plain + 2 parametrised × 1 key); the green run reports
  55 non-lint, `+0`.
- **Acceptance criterion 1 is met** and criterion 2's sweep was performed and
  recorded; my own independent grep of `src/robot_brain/**` over ~20 body terms
  turned up no *false* body claim beyond the one fixed.

## Test-adequacy verdict — **adequate for the criteria as scoped, with BLOCK 2's caveat**

Thirteen targeted mutations, each run through real `colcon test` on the `/tmp`
copy. Nine were caught, each by exactly the intended test with a legible
message and no collateral failures:

| mutation | caught by |
|---|---|
| revert `AGENTS.md:3` to `a four-wheel base` | absence **and** presence tests (2 fail) |
| `…base with 4 wheels` (the spelling the old literal list missed) | absence test |
| `a three-omniwheel holonomic base` | presence test |
| body sentence moved below the first `## ` heading | presence test |
| descriptor left only inside a fence in the intro | presence test |
| pattern typo `whee1` | controls test |
| pattern narrowed to `…wheel(?:s\|ed\|\s\|-)` (F2's exact case) | controls test |
| `reach_radius` 0.85 → 0.40 | reach test |
| `0.85 m reach` → `0.85m reach` | reach test |

Both halves of the ledger (positive controls, negative controls) are present
and both bite. F2's narrowing repair is genuinely closed. The four uncaught
mutations are the three in BLOCK 2 plus the two false-red cases in BLOCK 3.

The suite's remaining structural weakness is the one D30 both admits and then
denies: it pins the claim already known stale, and the rest of the body
sentence rides on prose. Follow-up 6bis (the replay test) remains the right
general answer and is correctly routed out of this PR.

---

*Worktree left clean: no source or test file was modified; `git status`
reported clean before this report and reports only this new file after it. All
mutation work was done on `/tmp/rtc`, a `cp -r` copy built and tested outside
the worktree.*
