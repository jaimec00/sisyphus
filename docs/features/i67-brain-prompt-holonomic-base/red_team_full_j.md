# Red-team pass J — confirming full adversarial pass over `origin/main..HEAD` (`74053de`)

**Scope:** the entire branch diff. Findings formed before reading any prior
report; prior reports consulted afterwards only to avoid re-reporting closed
items. The three edits in the unreviewed commit `74053de` were audited first and
hardest, then D30 and the `spec.md` row end to end, then the whole
implementation fresh with a new mutation battery.

## Verdict

**BLOCK: none.**

The three edits in `74053de` are each **true and each an improvement**, including
the one the calibration flagged as suspect: `spec.md`'s "as" is **not** a stray
leftover — `git log` shows it was a deliberate substitution for "which" when the
appositive was rewritten, and the sentence parses correctly with "as" meaning
"since". No edit is needed there, and I recommend against one.

Everything else I could execute against, I executed against: 763 tests green
(`robot_brain` 57 non-lint = baseline 57, ratchet `+0`), 30 prompt-drift tests,
**26 mutations** on a `/tmp` copy, all seven `TestBodyDescription` tests killed
by at least one mutation, two "correct rewrite" controls staying green, both
acceptance criteria met under my own search terms, every executable claim in D30
re-measured from scratch (including the three "owned but unasserted" claims, the
`PackageNotFoundError`, and the `origin/main` false-prompt run), and the two
historical claims re-checked against `gh`. Worktree clean; the `/tmp` copy
`diff -r`s identical to `src/` afterwards.

Three NOTES, none blocking, **none of which I recommend acting on before merge**
(see §7 — on this branch the marginal prose edit has been the defect source, not
the defect fix).

---

## 1. The three edits in `74053de`, audited individually

### 1a. `status.md:345` — follow-up 1 rewritten — **VERIFIED accurate**

The headline is *"The drivetrain is the one body claim with no live source, and
the head camera is the one body fact still to arrive."* I checked both halves
against the code and against `spec.md`'s Body table rather than against the
entry that asserts them:

| clause | check | result |
|---|---|---|
| the drivetrain is the **one** body claim with no live source | enumerated the prompt's body claims: base, column ("extendable"), arms ("two"), grippers, reach (0.85), column travel (0.0–1.2), speed caps, gripper force, "raises both shoulders". Owners: `RobotModel` (column travel, reach, shoulder geometry), `Side` + Mock observation (arms, grippers), `limits.yaml` (caps, force). Base: none — `grep -rn -i 'omniwheel\|holonomic\|drivetrain\|wheel'` over `src/` outside `robot_description` hits only `AGENTS.md`, `robot_brain/README.md`, `test_prompt_drift.py` (and two unrelated "python wheel" comments in `robot_world`) | **true** |
| the head camera is the **one** body fact still to arrive | `spec.md:29-33` Body table = base, column, arms, gripper, head camera. The prompt names the first four. `spec.md:41-43` puts wrist cameras, microphone and the suction end-effector under **"Explicitly deferred"**, so they are not part of the committed body | **true** |
| "the arm count, the grippers, the column and the reach are all read from owners" | mutations M3/M5 (arms, grippers), M4/M14 (column), M6/M7 (reach) — every one goes red | **true** |
| "closing that needs the `robot_description` dependency call reversed (D30 records the price and says PR6 lowers it without performing it)" | matches D30's *"What PR6 changes is the price of reversal, not the need for it"* and *"PR6 does not retire the ledger"* | **faithful compression** |

The self-marking parenthetical ("the original framing … is now false") is also
correct as a description of the framing it replaces: before this PR the prompt's
body prose genuinely had no live source, and the "four more body facts" list is
now wrong in the sense that matters (three of the four are already stated by the
prompt and now gated; only the camera is new). This is the only part of
`status.md` that survives merge, and it is now accurate and actionable — it
names what remains, why, and the paths.

### 1b. `docs/design/spec.md:178-181` — the re-wrap, and the "as" — **VERIFIED, no defect**

The re-wrap is correct: the 100-column line pass I flagged is gone, and the
bullet's lines now run 64–84 columns. 84 (line 179) is above the file's 80-ish
habit but **inside its existing distribution** — `spec.md` already has 8 lines
over 80, including 82 at `:167` in the neighbouring D28 bullet and 82 at `:6`.
Not a finding.

On the "as": the calibration's hypothesis is that it is a stray word left from a
prior edit. **It is not.** `git show 3af01f5 -- docs/design/spec.md`:

```
-  the first time — PR7's head camera is the next one — which lands **ungated**
+  the first time — the head camera, which the prompt does not mention today — as
+  it lands **ungated** unless its author reads it from an owner or pins it.
```

The appositive was rewritten from "PR7's head camera is the next one" to "the
head camera, **which** the prompt does not mention today", which consumed the
"which" the main clause had been using; "as" was substituted deliberately. Read
as *since*, the sentence parses and is true: *the same rule applies to a
first-time body fact, since it lands ungated unless its author reads it from an
owner or pins it.* (The same commit also dropped "PR7's", which was itself
imprecise — the roadmap puts the `head_camera_link` in **PR3.5** and only the
RGB-D sensor in PR7.) Awkward-but-correct is a NOTE ceiling, and here I would not
even spend the NOTE: another edit to this sentence is more likely to introduce a
defect than to remove one.

### 1c. `test_prompt_drift.py:23` — "no owner on this side of the skill API" — **VERIFIED true**

The replaced framing ("no live source *in this package*") was wrong because
nothing this suite reads live is in this package. The new framing is the one D30
and the class docstring use, and it is the true one: no package on the brain's
side of the seam owns the drivetrain (`RobotModel.__dataclass_fields__` =
`shoulder_offset_y, shoulder_offset_z, reach_radius, home_gripper_offset,
min_column_height, max_column_height` — no drivetrain field; `limits.yaml`'s
`base 0.6 m/s` is a speed cap, not a wheel configuration). Confirmed by the
grep in §1a. Pass I's NOTE 3 is closed by this edit.

---

## 2. D30 and the `spec.md` row, end to end — every checkable claim re-executed

I did not take pass I's table on trust; I re-ran each of these myself.

| D30 claim | how I checked | result |
|---|---|---|
| "re-running the suite against `main` with the false prompt in place confirms it" | `git archive origin/main src \| tar -x -C /tmp/rtmain`; ran main's `test_prompt_drift.py` under the pixi env | **VERIFIED** — `four-wheel` at `AGENTS.md:3`, **23 passed**. Main's gate is blind to the defect |
| `known_locations` has an owner and no assertion | added `hallway` to `robot_world/default_world.json` on the `/tmp` copy | **VERIFIED green** — 30 passed while the prompt says "There are no others" |
| `schema_version` has an owner and no assertion | `SCHEMA_VERSION = 1 → 2` in `robot_skills/serialization.py` | **VERIFIED green** — 30 passed |
| the column travel restated in a worked example has an owner and no assertion | `limits.yaml max_height 1.2 → 1.5` **and** the prompt's safety section updated to match, leaving the fenced examples at 1.2 | **VERIFIED green** — 30 passed |
| `get_package_share_directory('robot_description')` raises `PackageNotFoundError` under `colcon test` | pulled the real `AMENT_PREFIX_PATH` out of this run's `log/latest_test/robot_brain/command.log` (6 prefixes: robot_brain, robot_mcp, robot_safety, robot_backends, robot_world, robot_skills) and ran the call under it | **VERIFIED** — raises for `robot_description`, resolves for `robot_backends`; `install/robot_description/share/ament_index/.../robot_description` exists, so it is the missing dependency edge, not a missing build |
| `test_no_ros_runtime` does not stop a test-time ROS import | read it: `PROBE` inspects `sys.modules` after `import robot_brain` in a bare subprocess; `test_no_source_file_imports_rclpy` walks `os.path.dirname(robot_brain.__file__)`; `FORBIDDEN_ROOTS = ('rclpy',)` | **VERIFIED** — neither reaches `test/`; the class docstring's "covers what the *shipped* assets import, not what a test does" is exact |
| the install tree + xacro are already required by the same `pixi run test` | `robot_description`'s 17 tests ran green in my full-suite run | **VERIFIED** |
| the URDF has the wheel count structurally; "omniwheel"/"holonomic" only in a comment | `grep -rn -i` over `src/robot_description/urdf/` → 5 hits, all inside `<!-- -->` (`base.xacro:3,7,43,51,149`) | **VERIFIED** |
| PR6 parses only kinematic constants and `RobotModel` has no drivetrain field | `urdf-mjcf-pr-breakdown.md:129-137` (0.18 / 0.50 / 0.85 / 0.00 / 1.20 / home offset); `mock_world.py:82-87` | **VERIFIED** — and D30 no longer restates the miscounted "seven" |
| the head camera is the one PR3–PR7 addition the prompt does not mention | read every roadmap section: PR3 column, PR3.5 head camera, PR4 arms, PR5 grippers, PR6 loader, PR7 MJCF + head RGB-D sensor | **VERIFIED** |
| "`4 wheels` … is the spelling D26 itself prints" | `decisions.md:78` — *"The cost is the '4 wheels' aesthetic of D1"* | **VERIFIED** |
| "the comment on #65 predates #66's own merge" | `gh issue view 65` → comment `2026-08-14T00:43:28Z`; `gh pr view 66` → merged `2026-08-14T00:49:37Z`; issue #67 created `00:49:53Z` | **VERIFIED** — ordering holds |
| "the worktree team that noticed the discrepancy while building the base surfaced it as a follow-up … and Sisyphus filed it" | the #65 comment opens *"Follow-ups uncovered during #65 (PR #66). Manager-routed per CLAUDE.md; Sisyphus files the issues…"*; #67's body ends *"filed as new work, deduped against it"* | **VERIFIED, fair** |

`spec.md`'s row was checked clause by clause against the same sources and against
the code it names: `SUPERSEDED_BODY_CLAIMS` is in `test/test_prompt_drift.py`,
`robot_brain/package.xml:18-21` test-depends only on `robot_mcp`,
`robot_safety`, `robot_backends`, `robot_skills` (no `robot_description`), the
arm count does come from `Side` and the travel/reach from `RobotModel`. Nothing
in the row is false, and the rule it states is the one the code implements.

---

## 3. Test adequacy — **ADEQUATE** (explicit verdict, earned by 26 mutations)

Baseline on the unmutated `/tmp/rtj` copy: **30 passed**. Every mutation was
applied to that copy and reverted; `diff -r` confirms the copy is byte-identical
to `src/` afterwards.

**Kills — every mutation below turned the suite red:**

| # | mutation | observed |
|---|---|---|
| M1 | prompt reverted to main's `a four-wheel base` (the exact #67 defect) | 2 failed (absence + presence) |
| M2 | rewritten as `a base with 4 wheels` (D26's own spelling) | 2 failed |
| M3 | `two arms` → `three arms` | 1 failed |
| M4 | `extendable` → `fixed` vertical column | 1 failed |
| M5 | `with grippers` dropped | 1 failed |
| M6 | `RobotModel.reach_radius` 0.85 → 0.40 | 1 failed, message names both numbers |
| M7 | prompt's `0.85 m reach` → `0.90 m reach` | 1 failed |
| M8 | a second, model-less `1.10 m reach` phrase added | 1 failed (set comparison in both directions) |
| M9 | ledger pattern typo'd so it can never match | 1 failed (positive controls) |
| M10 | ledger pattern widened to `(?:four\|4)` | 2 failed (negative controls **and** the absence check — the prompt legitimately says "a fourth attempt") |
| M14 | `max_column_height` 1.20 → 0.00 (no travel) | 6 failed, incl. the column check |
| M15 | the whole intro sentence deleted | 3 failed |
| N1 | descriptor removed from the sentence | 1 failed |
| N2 | sentence made generic, descriptor left only inside a fenced note in the intro | 1 failed — fence scoping holds |
| N3 | descriptor moved below the first `## ` heading | 1 failed — intro scoping holds |
| N4 | retired claim hidden inside a fenced example | 1 failed — the absence check scans the whole prompt |
| N5 | retired claim in an HTML comment | 1 failed |
| N9 | `COUNT_WORDS` loses the entry for 2 | 1 failed (loud, as documented) |
| P6 | column clause dropped from the sentence | 1 failed |
| R1 | `## How to work` demoted to `# How to work` (would silently widen `introduction()`) | failed — `test_the_prompt_covers_what_the_architecture_requires` bounds it |
| S1 | `Side` gains a third member | failed — gripper/`Side` set comparison |

**Controls — every one stayed green on a *correct* prompt:**

| # | control | observed |
|---|---|---|
| M12 | the intro sentence re-wrapped across the descriptor | 30 passed — no false positive |
| M13 | `two arms with grippers` → `two arms, each with its own gripper` | 30 passed |

All **seven** `TestBodyDescription` tests are killed by at least one mutation:
absence (M1/M2/P5), presence (M1/M2/N1/N2/N3/P1), positive controls (M9),
negative controls (M10), arms+grippers (M3/M5/N9/S1), column (M4/M14/P6), reach
(M6/M7/M8). The 50 → 57 baseline bump is exactly these seven tests, and the
audit reports `robot_brain 57 non-lint, +0`.

**Known, routed residue re-confirmed by execution** (not new findings — already
follow-ups 9/12): with the correct descriptor left in place, *adding* a
contradictory claim spelled `four large wheels` (P2), `four‑wheel` with U+2011
(P3) or `4wheel` (P4) leaves the suite **green**. Each of these still fails if
the descriptor is replaced rather than supplemented (P1), so the realistic drift
shape — somebody retypes the sentence — is caught. The unrealistic one — somebody
adds a second, contradictory body sentence — is not.

---

## 4. Acceptance criteria — both met (my own search terms)

**AC1.** `AGENTS.md:3-4` reads *"a 3-omniwheel holonomic base"*, matching
`spec.md:29` and D26/D29's vocabulary, with no new backticks (R3 holds; the
vocabulary check is green). M1/M2 prove the regression is now caught.

**AC2.** My own sweep, not inherited:
`grep -rn -iE "wheel|holonomic|omni|drivetrain|chassis|caster|mecanum|
differential|track|leg|torso|head|camera|lidar|microphone|battery|payload|dof|
servo|actuator|motor|joint|shoulder|elbow|column|arm|gripper|base"` over every
file in `src/robot_brain/` (including `package.xml`, `setup.py`, `setup.cfg`,
`openclaw.robot.json`, `agent.py`, `__init__.py`, all seven test modules).
Outside `test_prompt_drift.py`'s own ledger comments, the **only** body-
description claim about this robot is `AGENTS.md:3`. `openclaw.robot.json` is a
tool filter with no body prose; `README.md:82`'s "D1's four-wheel base, retired
by D26" is correctly-stated history about a superseded decision and is not read
by the ledger (which scans `PROMPT` only).

---

## 5. Manager rulings re-checked as review targets

- **R1 (state the body, not the manoeuvre)** — upheld. The shipped sentence adds
  no affordance framing, and the corrected rationale (the belief's only outlet is
  the prose report) is the one that survives in `status.md`.
- **R2 (no `robot_description` test-depend)** — conclusion upheld, and the stated
  reason is now one I executed against and confirmed myself (§2). D30 correctly
  labels it *"a design call with nothing enforcing it"*.
- **R3, R4** — upheld; the source diff is one clause plus its wrap.
- **G3 (write D30)** — upheld and load-bearing: `docs/features/` is deleted at
  merge, so `decisions.md` + `spec.md` are the only surviving statements of the
  binding rule. Both are true as written (§2).

---

## 6. NOTES (none blocking, none recommended for action before merge)

### NOTE 1 — `introduction()`'s two defensive behaviours have no regression guard (`test_prompt_drift.py:107-119`)

**VERIFIED by mutation.** Reverting `introduction()` to
`' '.join(PROMPT.split('\n## ', 1)[0].split())` (drops fence-stripping, N10) or
to `without_fences(PROMPT).split('\n## ', 1)[0]` (drops whitespace collapsing,
N11) leaves the suite at **30 passed**. Both behaviours were promoted to must-fix
during this run (N1 in round 1, H3 in round 4), and neither is now pinned by
anything: they only matter for prompt shapes that do not exist today, so a future
refactor could delete them silently and the loss would surface as a false red on
a re-wrap (H3's exact failure) or a false green on a fenced descriptor (N1's).
The ledger *pattern* got positive and negative controls for precisely this
reason; the helper did not. Fix shape, if anyone takes it: assert
`introduction()`'s behaviour against two synthetic strings, in the same register
as `test_the_matcher_catches_the_spellings_a_literal_list_did_not`. Follow-up
sized; same family as routed follow-up 7.

### NOTE 2 — the module docstring's one-line reason is a compression D30 does not fully endorse (`test_prompt_drift.py:22-24`)

**UNVERIFIED (an argument, not an execution).** The new line says
`SUPERSEDED_BODY_CLAIMS` "is hand-typed because the drivetrain it describes has
no owner on this side of the skill API". True of the *descriptor*; the ledger's
other half — the pattern matching the **retired** spelling — has no live owner
anywhere and never will, which is exactly D30's *"Even then the URDF owns the
wheel count and not the words '3-omniwheel holonomic', and owns nothing at all
for the whole-prompt scan that catches the retired spelling. Retiring the row is
therefore work somebody chooses and replaces, never a side effect."* A PR6 author
reading only the docstring could infer "the drivetrain now has an owner →
the ledger's reason is gone", which is the concrete failure `status.md`'s round-5
entry names. The docstring does point at `TestBodyDescription`, and `decisions.md`
+ `spec.md` — the artifacts that survive merge — both state the rule correctly, so
the guard against that inference is in place where it counts. **I do not
recommend editing this before merge:** the marginal gain is small and every one
of the last six rounds put a fresh false claim into the passage it had just
corrected.

### NOTE 3 — `spec.md:179` is 84 columns

Above the file's 80-ish habit, inside its existing distribution (8 lines already
exceed 80, including 82 in the adjacent D28 bullet). Cosmetic; recorded only so
the next reviewer does not re-derive it.

---

## 7. Suite, worktree, and N+1 status

```
$ pixi run test
Summary: 763 tests, 0 errors, 0 failures, 0 skipped
robot_brain  60  0  0  0  57 non-lint  +0  ok
AUDIT PASSED: every expected package collected tests
All stages passed.

$ diff -r --exclude=__pycache__ --exclude=.pytest_cache src /tmp/rtj/src
COPY IDENTICAL (all mutations reverted)

$ git status --porcelain      # (empty)
$ git log --oneline -1        74053de
```

Every mutation ran on `/tmp/rtj` (a `cp -r` of `src/`) or `/tmp/rtmain` (a
`git archive` of `origin/main`), both outside the worktree. Nothing in the
worktree was created, edited or reverted by me except this report. `build/`,
`install/` and `log/` were touched only by `pixi run test`.

**N+1, stated plainly.** `74053de` touched three files: two prose docs and one
docstring line. `git log` confirms **no assertion in `test_prompt_drift.py` has
changed since `e89b187`** and `AGENTS.md` has not changed since `d92150f` — so
the code and tests have now been reviewed clean by six consecutive passes, five
of them with independent mutation batteries, and this pass added a 26-mutation
battery of its own that found nothing new. The residual risk on this branch has
been concentrated entirely in prose, and the last prose edit is the thing I
attacked first, from `git log` and `gh` rather than from the summary I was
handed — including the one claim the calibration expected to be wrong, which
turned out to be right.

This is a clean pass following a clean pass, with the only intervening change
being three prose edits that I verified individually. **I judge the branch
ready.** If the manager lands no further edits, no further pass is owed. If it
edits anything — including either NOTE above — that edit is new prose in the
exact class that produced every defect on this branch, and it wants one more
look; my recommendation is not to.
