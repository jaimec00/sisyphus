# Red-team pass I — full adversarial pass over `origin/main..HEAD` (`b83eeba`)

**Scope:** the entire branch diff, not the fix diff. Findings formed before
reading any prior report; prior reports consulted afterwards only to avoid
re-reporting closed items (that check retired two of my draft NOTES — see
*What I dropped after reading the prior passes*).

## Verdict

**BLOCK: none.** The prose is true as far as I can execute against it, and I
executed against every claim in D30 that is checkable. The seven
`TestBodyDescription` tests are load-bearing under mutation, both acceptance
criteria are met, `pixi run test` is green (763 tests, 0 failures, 0 skipped,
ratchet `+0`, `robot_brain` 57 non-lint = baseline), and the worktree is clean.

Five NOTES, four of which are **carried over unfixed from pass H/G** and are
already routed or one-line prose edits. None is a merge blocker.

---

## 1. The four claims the calibration singled out — all VERIFIED

I checked these against `gh` and `git log`, not against the task prompt.

### 1a. "the comment on #65 predates #66's own merge" — **VERIFIED, true**

```
$ gh issue view 65 --json comments,closedAt    → comment createdAt 2026-08-14T00:43:28Z
$ gh pr view 66 --json mergedAt                → 2026-08-14T00:49:37Z
$ gh issue view 67 --json createdAt            → 2026-08-14T00:49:53Z
```

Comment → merge = 6 min 09 s; merge → issue = 16 s. The claim asserts only the
ordering, which holds. (The UTC dates are 08-14; local commit dates are
`-0400`, so D30's `## 2026-08-13` heading is consistent with the repo's
local-date convention — `git log --date=iso-local` puts the fix commit
`150dd12` at 2026-08-13 21:07 -0400.)

### 1b. "the worktree team that noticed the discrepancy while building the base" — **VERIFIED, fair**

The #65 comment opens: *"**Follow-ups uncovered during #65** (PR #66).
Manager-routed per CLAUDE.md; Sisyphus files the issues, deduping against the
roadmap. Neither is in this PR's owned paths (`src/robot_description/` only),
so both were deliberately left untouched."* That is the #65 worktree manager
routing a finding made while building the base, exactly as D30 describes, and
exactly the CLAUDE.md path D30 credits. #67's body ends *"filed as new work,
deduped against it"* — the Sisyphus half. Every account in this repo is
`jaimec00`, so authorship cannot be distinguished by account; the comment text,
the 16-second gap after the merge and CLAUDE.md's routing rule all agree with
the entry, and nothing in the record contradicts it.

### 1c. "re-running the suite against `main` with the false prompt in place confirms it" — **VERIFIED, true**

Executed on a copy of `origin/main`'s `src/` outside the worktree
(`git archive origin/main src | tar -x -C /tmp/rt_main`), with the four-wheel
prompt in place:

```
$ grep -n four-wheel /tmp/rt_main/src/robot_brain/robot_brain/openclaw/AGENTS.md
3:You are the brain of a household mobile manipulator: a four-wheel base, an
$ pytest /tmp/rt_main/src/robot_brain/test/test_prompt_drift.py
23 passed in 0.75s
```

Nothing in main's drift suite goes red on the false prompt. I also re-ran the
`reach_radius` half of the same sentence on that copy: `0.85 → 0.40` in
`mock_world.py` leaves main's suite at **23 passed**, confirming D30's *"where
retuning `reach_radius` used to leave the entire suite green"*.

### 1d. Nothing in D30 still asserts a duration, a count or a PR attribution — **VERIFIED**

I read D30 clause by clause. Every numeral remaining is a stable identifier or
a structural count I re-executed:

| numeral in D30 | status |
|---|---|
| D1 / D21 / D26 / D29 / D12 / D13, #65 / #66 / #67, PR3–PR7, PR6, PR3–PR5 | identifiers, all correct (D13 does state the skill API is the seam to "swappable drivers/URDF"; D1 does say "4-wheel base"; D26 does print "the '4 wheels' aesthetic of D1" at `decisions.md:78`) |
| "three `continuous` wheel joints" | VERIFIED — `base.xacro:169` types the macro `continuous`, instantiated at `:203-205` as `base_left_wheel`/`base_back_wheel`/`base_right_wheel` |
| "four claims in the prompt's opening sentence, three have owners" | true (base / column / arms / grippers; the base is the unowned one) — see NOTE 3 for a readability wrinkle |
| "one digit" | the wheel count; consistent with the sentence that follows |
| "`4 wheels`", "four-wheel", "3-omniwheel" | spellings, all present where claimed |

No duration survives anywhere in D30, the test module, the class docstrings,
`spec.md` or the README (`grep -inE "for a day|for [a-z]+ (day|hour|minute)|
[0-9]+ (days|hours|minutes)"` over all five files returns nothing). No PR is
credited with catching the drift. `status.md` follow-up 4 no longer carries a
test count.

---

## 2. Everything else in D30 that is checkable, re-executed myself

| D30 claim | how I checked | result |
|---|---|---|
| `get_package_share_directory('robot_description')` raises `PackageNotFoundError` under `colcon test` | took the **real** env from this run's own `log/latest_test/robot_brain/command.log` (`AMENT_PREFIX_PATH` = install/{robot_brain,robot_mcp,robot_safety,robot_backends,robot_world,robot_skills}) and ran the call under it | **VERIFIED** — raises, listing exactly those six prefixes + the conda env; `robot_backends` resolves fine, and `install/robot_description/share/ament_index/.../robot_description` exists, so it is the missing edge, not a missing build |
| `test_no_ros_runtime` does not stop a test-time ROS import | read `test_no_ros_runtime.py`: `PROBE` inspects `sys.modules` after `import robot_brain` in a bare subprocess; `test_no_source_file_imports_rclpy` walks `os.path.dirname(robot_brain.__file__)`; `FORBIDDEN_ROOTS = ('rclpy',)` | **VERIFIED** — neither reaches `test/`, and neither forbids `ament_index_python` |
| colcon install tree + xacro already required by the same `pixi run test` | `robot_description`'s 17 tests ran green in my full-suite run | **VERIFIED** |
| the URDF has the wheel count structurally, "omniwheel"/"holonomic" only in a comment | `grep -rn -i` over `src/robot_description/urdf/` | **VERIFIED** — all six hits in `base.xacro` are inside `<!-- -->` blocks; the joint names carry `wheel`, never `omniwheel` |
| `RobotModel` carries no drivetrain field | printed `__dataclass_fields__` | **VERIFIED** — `shoulder_offset_y/z, reach_radius, home_gripper_offset, min_column_height, max_column_height` |
| PR6 parses only kinematic constants | `urdf-mjcf-pr-breakdown.md:129-137` — "parses the expanded URDF → the 7 constants … 0.18 / 0.50 / 0.85 / 0.00 / 1.20 / home offset" | **VERIFIED** — D30's list matches, and no longer restates the miscounted "seven" |
| the head camera is the one addition PR3–PR7 makes that the prompt does not mention | read every PR section: PR3 column, **PR3.5 head camera link**, PR4 arms, PR5 grippers, PR6 loader, PR7 MJCF + head RGB-D sensor. No other new body part; wrist cameras/mic are in D26 but in no roadmap PR | **VERIFIED** |
| "the Mock serves exactly one gripper observation per member" | executed `MockBackend().get_observation().robot.grippers` | **VERIFIED** — exactly `[Side.LEFT, Side.RIGHT]`, one each |
| `known_locations` has an owner and no assertion | added `hallway` to `robot_world/default_world.json` on a `/tmp` copy | **VERIFIED green** — 33 passed while the prompt says "There are no others" |
| `schema_version` has an owner and no assertion | `SCHEMA_VERSION = 1 → 2` in `robot_skills/serialization.py` | **VERIFIED green** — 30 passed |
| the column travel restated inside a worked example has an owner and no assertion | `limits.yaml max_height 1.2 → 1.5` **and** the safety *section* updated to 1.5, leaving the fenced example at 1.2 | **VERIFIED green** — 30 passed |
| the drift was a real drift, not a prompt born wrong | `git log -S'four-wheel base'` → entered 2026-08-11 19:19 (#50); D26 landed 2026-08-13 10:55; #66 merged 2026-08-13 20:49 | **VERIFIED** — the prompt predates D26, so "went on telling … after D26 retired that base" is exact |

---

## 3. Test adequacy — **ADEQUATE** (explicit verdict, earned by mutation)

All mutations were applied to a `cp -r` copy of `src/` under `/tmp`, run with a
`PYTHONPATH` harness against the same pixi env. Baseline on the unmutated copy:
**30 passed**.

| # | mutation | expected | observed |
|---|---|---|---|
| M1 | prompt reverted to main's `a four-wheel base` (the exact #67 defect) | red | **2 failed** — absence *and* presence checks |
| M2 | descriptor rewritten as `a base with 4 wheels` (D26's own spelling) | red | **2 failed** |
| M3 | `two arms` → `three arms` | red | **1 failed** (`'two arms'` from `len(Side)`) |
| M4 | `extendable` → `fixed` vertical column | red | **1 failed** |
| M5 | `two arms with grippers` → `two arms` | red | **1 failed** |
| M6 | `RobotModel.reach_radius` 0.85 → 0.40 | red | **1 failed**, message names both numbers |
| M7 | `max_column_height` 1.20 → 0.0 (no travel) | red | **6 failed**, incl. the column check |
| M8 | ledger pattern typo'd (`fuor`/`whee1`) | red | **1 failed** — the positive controls catch a pattern that can never match |
| M9 | ledger pattern widened to `(?:four|4)` | red | **2 failed** — negative controls *and* the absence check (the prompt legitimately contains "a fourth attempt") |
| M10 | descriptor removed from the sentence, left only inside a fenced note | red | **1 failed** — fence scoping holds |
| M11 | intro sentence re-wrapped across the descriptor (a *correct* prompt) | green | **30 passed** — no false positive |
| M12 | `two arms with grippers` → `two arms, each with its own gripper` (correct reword) | green | **30 passed** |
| M13 | a legitimate second ledger row added (`'linear-rail lift'`) | — | **1 failed on a correct prompt** — see NOTE 1 |

Every one of the seven `TestBodyDescription` tests is killed by at least one
mutation, and the two "must stay green" controls stay green. The 50 → 57
baseline bump in `scripts/test_baseline.json` is exactly the seven new tests
(`main` = 50 non-lint, branch audit = 57, `+0` vs base).

The tests I did **not** find adequate coverage for are disclosed by the code
itself, in the class docstring and in D30: the ledger cannot detect the *next*
stale claim, the column check reads a word rather than a number, and
`known_locations`/`schema_version`/the fenced column travel remain unasserted
(all three re-verified green under mutation above, all three routed as
follow-ups). That is a documented residue, not an undisclosed gap.

---

## 4. Acceptance criteria — both met (my own search terms)

**AC1** — `AGENTS.md:3-4` now reads *"a 3-omniwheel holonomic base"*, matching
`spec.md:29` ("**LeKiwi 3-omniwheel holonomic** base") and D26's vocabulary. No
new backticks (R3 holds; the vocabulary check is green). M1/M2 prove a
regression is caught.

**AC2** — my own sweep, not inherited:
`grep -rn -iE "wheel|holonomic|omni|drivetrain|chassis|legs|track|caster|servo|
actuator|dof|joint|torso|head|camera|lidar|sensor|microphone|battery|payload|
strafe|mecanum|differential|manipulator|mobile base"` over all of
`src/robot_brain/`. Outside `test_prompt_drift.py` the only body-description
claim about *this* robot is `AGENTS.md:3`. `openclaw.robot.json` contains a
tool filter and no body prose. `README.md:82`'s "D1's four-wheel base, retired
by D26" is history about a superseded decision, correctly stated, and is not
read by the ledger (which scans `PROMPT` only).

---

## 5. Manager rulings re-checked as review targets

- **R1 (state the body, not the manoeuvre)** — upheld. The shipped sentence
  adds no affordance framing. The ruling's *rationale* was already corrected in
  round 1 and the corrected version ("what bounds the damage is that the
  belief's only outlet is the prose report") is the one that survives.
- **R2 (no `robot_description` test-depend)** — the conclusion is upheld and,
  for the first time in this run, the stated reason is one I could execute
  against and confirm (§2, row 1). D30 correctly labels it "a design call with
  nothing enforcing it".
- **R3, R4** — upheld; the diff is a single-clause word change plus its wrap.
- **G3 (write D30)** — upheld: `docs/features/` is deleted at merge
  (`.github/workflows/guards.yml`), so `decisions.md` + `spec.md` are the only
  places the binding rule survives, which is why I spent this pass on them.

---

## 6. NOTES (none blocking)

### NOTE 1 — `status.md` follow-up 1 is now stale in the artifact that survives merge (`status.md:345`)

**VERIFIED by reading the landed code.** Follow-up 1 still reads *"The prompt's
body prose has no live source, and PR3–PR7 are about to add four more body
facts (column, arms, gripper, camera)"*. After this PR, three of the four
opening-sentence claims **do** have live sources (arms from `Side`, grippers
from the Mock observation, column travel and reach from `RobotModel`), and D30
itself concludes *"the head camera is the one addition it does not mention
yet"* — the prompt already names the column, the arms and the grippers. The
next sentence of the follow-up partly self-corrects ("B3 shows a live source
exists for *reach*"), but the headline and the "four more body facts"
parenthetical are what a filed issue title would be built from. This is the
**only** part of `status.md` that outlives the merge, so it is worth one edit
before it is posted: "the prompt's *drivetrain* claim has no live source; the
head camera is the one body fact PR3–PR7 add that the prompt does not yet
mention." (Pass A's N-A6 flagged the opposite-direction version of this before
the fix landed; the fix inverted it.)

### NOTE 2 — carried over unfixed from pass H: `spec.md`'s D30 row wrap + grammar (`docs/design/spec.md:180`)

Still present. Line 180 is **100 columns** in a bullet whose other lines wrap at
78–84, and the sentence reads *"The same applies to a body fact the prompt
gains for the first time … as it lands ungated unless its author reads it from
an owner or pins it."* Cosmetic in a permanent, hand-wrapped file; re-wrap and
drop the stray "as".

### NOTE 3 — carried over unfixed from pass H: the module docstring's reason for the ledger (`test_prompt_drift.py:22-24`)

Still says the ledger is hand-typed "because the drivetrain it describes has no
live source **in this package** at all". Nothing this suite reads live is in
this package — `Side`, `RobotModel`, `SafetyLimits`, `TOOL_NAMES` are all
siblings — so the stated reason does not distinguish the drivetrain. The class
docstring and D30 both use the load-bearing scope ("no live source on this side
of the skill API"). One-word fix; I confirm pass H's reading.

*(Same paragraph, minor: D30's "three have owners … the arm count from `Side`
…, and the column's travel and the arm's reach from `RobotModel`" enumerates
four items for a count of three. The fourth — the reach — is explicitly flagged
as living in a worked example rather than the opening sentence, so the sentence
is true; it just makes the reader do the subtraction.)*

### NOTE 4 — carried over from pass G (routed as follow-up 7): a second ledger row goes red on a correct prompt

**VERIFIED by mutation M13.** Adding a legitimate second row
(`'linear-rail lift': r'\bbelt[\s-]+drive'`) fails
`test_the_descriptor_that_replaced_it_is_taught[linear-rail lift]` on a
byte-correct prompt, because the presence check parametrizes over every ledger
key and demands each key appear verbatim in the introduction. It fails *loudly*
with a legible message, and follow-up 7 already carries both halves (this, plus
the positive-control test hard-coding the one key). No change needed here; I am
re-confirming the follow-up is backed by execution in this pass too.

### NOTE 5 — a live source for the *words* exists outside the URDF, if the dependency call is ever revisited

D30 declines the URDF edge partly because "the edge buys one digit — the URDF
holds the wheel *count* structurally but the words 'omniwheel' and 'holonomic'
only in a comment". True of the URDF. Worth knowing for whoever reverses the
call on evidence: `robot_description`'s `package.xml:6` and `setup.py:44`
*descriptions* carry the exact phrase "3-omniwheel holonomic base + extendable
column + 2 arms", and those are reachable through the same `<test_depend>` +
ament index. It is prose-pinned-to-prose rather than a structural fact, so it
does not change the decision — but it is a datum the entry does not have, and
D30 explicitly invites the next PR to reverse on evidence.

---

## 7. What I dropped after reading the prior passes

Two draft NOTES died on contact with the record, and I record them so nobody
re-opens them:

- **D30's "across the skill-API seam" framing.** I drafted a NOTE that D13 puts
  the URDF on the *same* (swappable) side of the seam as `robot_backends`, so
  "across the seam" cannot by itself separate `robot_description` from the
  existing deps. Pass C already adjudicated this: the load-bearing word is
  **"meets"** — `robot_backends`' contents reach the brain *through* the seam on
  the wire, and nothing from the URDF ever does. The entry's wording is precise
  as written.
- **"a robot that *existed* from the moment #66 merged."** Nothing physical
  exists (the README says nothing here has ever run against a real Pi), but the
  clause anchors "existed" to a PR merging a URDF in a sim-first repo, and the
  italics mark the distinction being drawn (D26 decided it; #66 built it). Not
  false; not worth an edit to an append-only entry.

---

## 8. Suite, worktree and N+1 status

```
$ pixi run test
Summary: 763 tests, 0 errors, 0 failures, 0 skipped
robot_brain  60  0  0  0  57 non-lint  +0  ok
AUDIT PASSED: every expected package collected tests
All stages passed.

$ git status --porcelain      # (empty)
$ git log --oneline -1        b83eeba
```

No file in the worktree was created, edited or reverted by me except this
report. Every mutation ran on `/tmp/rt_i67` (a copy of `src/`) or `/tmp/rt_main`
(a `git archive` of `origin/main`), both outside the worktree. `build/`, `log/`
and `install/` were touched only by `pixi run test`, which is gitignored and
expected.

**N+1 status, stated plainly.** This is a clean pass that **immediately follows
a fix commit** (`b83eeba`), so by the letter of the rule it proves the fixes
landed rather than proving the tree is sound. The mitigating fact, which I
verified rather than assumed: `b83eeba` and its two predecessors touched only
prose — `src/robot_brain/test/test_prompt_drift.py` has had no assertion change
since `e89b187`, and `AGENTS.md` none since `d92150f`. So the code and tests
have now been reviewed clean by five consecutive passes, four of them with
independent mutation batteries, and the entire residual risk was concentrated
in the prose `b83eeba` rewrote — which is exactly what I attacked hardest, from
`gh` and `git log` rather than from the summary I was handed.

My recommendation: if the manager lands **no** further edits, the next full pass
would be reviewing a byte-identical tree, and I would call the pair
(H's fixes → this pass) sufficient. If it edits anything — including the
one-line NOTE 1 fix to the follow-up list, which I do recommend before the
follow-ups are posted outward — then that edit is new prose in the class of
artifact that has produced every defect on this branch, and it wants one more
look before "ready".
