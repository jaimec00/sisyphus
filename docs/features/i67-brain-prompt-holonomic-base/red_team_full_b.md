# Red team — full pass B (N+1), whole diff `origin/main..HEAD`

Reviewer: third red-team agent, full adversarial pass over the entire
implementation (not the fix diff), per the N+1 rule in
`.claude/agents/red-team.md:56-64`. I formed my findings before reading
`red_team.md` / `red_team_fix.md`, then read them only to dedupe.
`red_team_full_a.md` was not read.

**Verdict: 1 BLOCK, 6 NOTES.** The BLOCK is not a behaviour bug — the shipped
prose fix and the five new tests are correct, and I proved that by execution.
It is a false rationale that is about to be copied into a durable GitHub issue,
in the one PR whose subject is a false claim that nothing gated. Everything
else is follow-up-sized.

Method: `pixi run test` in the worktree; every mutation on `cp -r` copies under
`/tmp/rtb` (robot_brain) and `/tmp/rtback` (robot_backends), never in place.
`git status` clean at start and at finish.

---

## 0. Test adequacy — explicit verdict

**Adequate for the acceptance criteria; documented-weak beyond them.**

`pixi run test`: **761 tests, 0 errors, 0 failures, 0 skipped; AUDIT PASSED**.
`robot_brain` 58 collected / **55 non-lint, `+0` vs-base** — the ratchet bump in
`scripts/test_baseline.json:6` (50 → 55) is exactly the five tests added, and
the ratchet reports `ok`. Diff touches only owned paths.

Criterion 1 is pinned in both directions, VERIFIED by mutation on the copy
(`test_prompt_drift.py` = 28 tests, ~0.8 s):

| mutation (on the copy) | observed |
|---|---|
| `AGENTS.md:3` → `a four-wheel base` (the literal pre-#67 text) | absence **and** presence tests fail |
| `AGENTS.md:3` → `a base with 4 wheels` (the spelling `decisions.md:78` prints) | both fail |
| `AGENTS.md:200` `beyond the 0.85 m reach` → `0.95` | reach test fails |
| same phrase reworded away (`beyond the arm reach`) | reach test fails (empty set) |
| `RobotModel.reach_radius` `0.85 → 0.60` | reach test fails |
| `four-wheel` planted in a *fenced worked example*, intro left correct | absence test fails (it scans the whole prompt) |

The one *false alarm* I found is deliberate: rewording the intro to
`a three-omniwheel holonomic base` — a correct sentence — fails
`test_the_descriptor_that_replaced_it_is_taught`, because the ledger key is a
literal (NOTE 2).

What the class does **not** catch, VERIFIED (28 passed on each): `two arms with
grippers` → `three arms with grippers`; `an extendable vertical column` → `a
fixed 1 m mast`; the worked examples' column travel `1.2 → 1.5` while the
safety section stays 1.2 (the prompt then contradicts *itself*). The class
docstring admits the first two ("It cannot notice the next one"); the third is
already routed as follow-up #5. See NOTE 3 for a cheap general fix nobody has
proposed yet.

## 0b. The prompt actually reaches a deployed agent, corrected — VERIFIED

The brief asked me to prove this from an install tree rather than the checkout.

- From `install/setup.bash` + a bare `python` in `/tmp`: `robot_brain.__file__`
  resolves to `build/robot_brain/robot_brain/__init__.py`, whose directory is a
  symlink to `src/`, and `operating_prompt()` returns the corrected sentence;
  `'four-wheel' in prompt` is `False`.
- `package_data` is not a hole: `python setup.py build` on a `/tmp` copy puts
  `build/lib/robot_brain/openclaw/AGENTS.md` and `openclaw.robot.json` in the
  package, so a **non**-`--symlink-install` build ships the corrected text too.
- The `lru_cache` is per-process and the tests read it through the same
  resolution a consumer does, so there is no path by which a consumer gets the
  old text. Note the pleasant asymmetry: a *stale* non-symlink install would
  make the drift tests go **red** (the absence check reads whatever
  `robot_brain` resolves to), not silently green.
- The genuinely stale copy is off-repo: `~/.openclaw/agents/robot/AGENTS.md` on
  the Pi, updated by a manual `scp` (`src/robot_brain/README.md:95`). Already
  captured as the deploy note (R3'); nothing here can fix it.

## 0c. The worked examples are byte-accurate against the live stack — VERIFIED

Not previously executed (round 2 marked the `3.25 m` claim UNVERIFIED). I ran
the two load-bearing examples through `MockBackend` + `default_safety_layer()`:

```
clamp  : 'commanded column height 2 m is outside the [0, 1.2] m travel range; clamped to 1.2 m'
place  : "cannot place 'cup_1': it is 3.25 m from the left shoulder, beyond the 0.85 m reach (robot is at 'table')"
```

Both are **verbatim** what `AGENTS.md:218-219` and `:200-201` quote, modulo the
hand wrap. So the prompt is accurate today — and, more usefully, the strongest
available guard is trivial and nobody has proposed it (NOTE 3).

---

## BLOCK 1 — the reason the drivetrain "cannot" be checked live is false, and it is about to be filed as an issue

`src/robot_brain/test/test_prompt_drift.py:134-138`; the same reasoning at
`docs/features/i67-brain-prompt-holonomic-base/status.md:155` and, critically,
in the outward-routed follow-up at `status.md:204`.

The class docstring justifies the hand-typed ledger with:

> It exists only in `robot_description`'s URDF, and reading that would put
> `ament_index_python` on this package's path -- and this is the one package
> whose defining property is that it needs no ROS at all (D21;
> `test_no_ros_runtime` names that module as one it refuses to see loaded).

**VERIFIED false in the operative sense.** `test_no_ros_runtime`'s probe
(`test_no_ros_runtime.py:23-38`) subprocesses `import robot_brain` only, and
its static scan's `FORBIDDEN_ROOTS` is `('rclpy',)` alone and walks only
`robot_brain/` — not `test/`. A **test-time** import is invisible to both.

Repro (on `/tmp/rtb`, worktree untouched): I added
`from ament_index_python.packages import get_package_share_directory` to
`test_prompt_drift.py` **and** a module-level read of the *installed*
`share/robot_description/urdf/base.xacro`, then ran
`test_no_ros_runtime.py test_prompt_drift.py test_pep257.py` →
**32 passed**. The file it read names exactly three wheel joints:
`['base_back_wheel', 'base_left_wheel', 'base_right_wheel']`.

So the drivetrain *does* have a reachable live source, at test time, today, and
the guard the docstring invokes does not stop anyone reaching it. The
`no ROS at all` property D21 buys is about what is **deployed to the Pi**
(`README.md:25-28`: "load from a source checkout and from a symlink-installed
build alike, with no ament index and no ROS graph"); a `<test_depend>` does not
touch that. The honest residual costs are (a) a new build-order edge onto a
package under construction in PR3–PR7, and (b) a small prose bridge from a
joint count to the English "3-omniwheel". Those may well still lose — I am
**not** demanding the URDF check.

Failure scenario, which is what makes this a BLOCK rather than a docstring
nit: `status.md:204` routes to Sisyphus, for filing as a real issue, *"The
prompt's body prose has no live source … there is none for the drivetrain."*
Sisyphus files it; PR3 lands the column; the implementer opens
`TestBodyDescription`, reads that reading the URDF is barred by this package's
defining property and by a named test, and adds a second hand-typed ledger row
instead of the four-line live check that was available all along. A false
premise laundered into a durable brief outlives every ephemeral doc in this
directory.

It is also the **fourth** false-rationale sentence in this one file this run
(B2, B3's replacement, F1, now this), which is the defect class the manager
itself named as the run's real finding (`status.md:196-199`) — and the reason
the N+1 rule exists.

Fix direction, cheapest sufficient version (no code behaviour changes):
1. `test_prompt_drift.py:134-138` — keep the design argument, drop the
   enforcement claim. E.g. "…reading it needs a `<test_depend>` on
   `robot_description`, i.e. a ROS dependency edge into the package defined by
   not having one — a design call, not something any test here enforces:
   `test_no_ros_runtime` only refuses ROS at *import* of the shipped module."
2. `status.md:204` — reword the follow-up so the issue Sisyphus files says
   *"the drivetrain's live source is `robot_description`'s installed URDF,
   reachable from tests (verified); the question is whether `robot_brain`
   should take that dependency edge"*, not "there is none".

**Prior art / dedupe:** raised in round 2 as NOTE 7 (`red_team_fix.md:193-207`)
and then **neither fixed nor routed** — `status.md`'s round-2 disposition
promoted NOTE 1/2 to F1/F2 and says "all six other NOTES → follow-up", but the
follow-up list (`status.md:203-209`) contains neither NOTE 7 nor NOTE 8. I am
re-raising it at BLOCK because (i) it is the load-bearing justification, not a
parenthetical, (ii) I have stronger evidence than round 2 did — a full URDF
read at module scope passes, and the wheel joints are right there — and (iii)
it is being copied outward into an issue. If the manager disagrees on severity,
the *minimum* acceptable outcome is that it goes on the routed follow-up list
rather than being dropped a second time.

---

## NOTE 1 — two round-2 NOTES were dropped, not routed (`status.md:190-194`, `:203-209`)

VERIFIED by reading the docs. `red_team_fix.md`'s NOTE 7 (above) and NOTE 8
(a second ledger row would ship with no positive control, and
`test_prompt_drift.py:184` hard-codes `SUPERSEDED_BODY_CLAIMS['3-omniwheel
holonomic']`, so deleting the row is a `KeyError` rather than a clean signal)
appear in neither the fix commits nor the follow-up list, though the round-2
disposition row says all remaining NOTES were routed. Fix direction: add both
to `status.md`'s follow-up list, or state why they were dropped. Process, not
code — but this feature's whole thesis is that unrouted prose rots.

## NOTE 2 — the ledger key is a literal, so a *correct* reword goes red (`test_prompt_drift.py:70-72`, `:169-171`)

VERIFIED: rewording `AGENTS.md:3` to `a three-omniwheel holonomic base` — true,
arguably better English (NOTE 4) — fails
`test_the_descriptor_that_replaced_it_is_taught` with "the prompt never
introduces the robot as 3-omniwheel holonomic". The natural repair is to edit
the ledger key, which then breaks `test_the_matcher_catches_…` with a
`KeyError` (NOTE 1's second half).

This is arguably intended — the failure is the prompt for a decision, exactly
as `WITHHELD_TOOLS` is documented — and "3-omniwheel holonomic" is the repo's
canonical term (`docs/design/spec.md:29`, `src/robot_description/package.xml:6`,
`README.md:3`), so I am not asking for a change. Recording it because the
coupling is invisible from `AGENTS.md`: the prompt's first sentence now has a
spelling that only a test file explains. Fix direction if it ever bites: make
the current descriptor a pattern too (`r'3[\s-]?omniwheel|three omniwheels?'`
plus `holonomic`) and key the controls off the same mapping.

## NOTE 3 — the cheapest live guard for the body prose is a replay, and nobody has proposed it

VERIFIED (see §0c). The two quoted failure reasons in the worked examples are
byte-identical to what `MockBackend` + `default_safety_layer()` emit today. A
single test that replays the example — navigate, grasp, place at the quoted
pose — and asserts the whitespace-normalised reason string is `in PROMPT`
would pin, in one assertion and with no new dependency: the `0.85 m` reach, the
`3.25 m` distance, the `[0, 1.2] m` travel range quoted *inside* the fence, the
`clamped to 1.2 m` value, the message format, and `'table'`/`'cup_1'`. That is
five of the eight "gap" rows in `red_team_fix.md`'s NOTE 4 table, closed by
reusing the module's stated aim rather than adding another regex.

It does not cover "three arms" / "a fixed mast" — for those the honest guard is
a golden pin of the opening body sentence (any edit to it goes red and forces a
decision), which is what you do to hand-written prose that has no live source.
Worth putting in the follow-up alongside item 1, because as written that item
asks for a live source and the answer for half of it is "replay what you
already have".

## NOTE 4 — register: `3-omniwheel` is the only digit-as-count in the document (D22)

`src/robot_brain/robot_brain/openclaw/AGENTS.md:3-4`. Read aloud: *"a
3-omniwheel holonomic base, an extendable vertical column and two arms with
grippers."* The file otherwise spells counts as words — `two arms` in the same
clause, `three failed attempts` (`:26`), `Two graspable objects` (`:171`) — and
reserves digits for measured quantities with units (`0.85 m`, `1.2 m`,
`0.6 m/s`). `a holonomic base on three omniwheels` would match the register and
the sentence's own second half.

Counterweight, which is why this is a NOTE and I would not change it without
the manager: R1 explicitly ruled for spec.md's exact term, and
`3-omniwheel holonomic` is what `spec.md:29`, `robot_description/package.xml:6`
and its README all say. Cross-document consistency is a real argument;
"3-omniwheel" is also unambiguous to a model. Editorial, not a defect.

## NOTE 5 — "holonomic" advertises a manoeuvre the skill API cannot express, next to advice that already does (`AGENTS.md:117`)

The failure table says `out_of_reach` → *"drive nearer, or aim closer to the
robot, then retry"*, but `navigate_to(location)` is the only base skill and
`known_locations` is declared closed (`:97-98`). There is no "drive nearer".
The prompt does teach the actionable form 40 lines later (*"`navigate_to` the
nearest location and repeat the same `place`"*, `:159-160`), so an agent that
reads the whole file recovers correctly — and I confirmed the fenced recovery
example does exactly that.

The word "holonomic" does not create the problem (`:117` is pre-existing and
unchanged by this diff), but it mildly sharpens it: an LLM unpacks "holonomic"
into "I can nudge sideways", and `:117` is the one line that seems to invite it.
The manager's corrected R1 (`status.md:157-166`) already reasons about exactly
this and I think lands right. Fix direction, if ever: `:117` → "navigate to a
nearer location, or aim closer to the robot, then retry". One cell, no new
claim — out of scope for this brief.

## NOTE 6 — my independent audit of acceptance criterion 2 (the sweep): adequate

I did not reuse anyone's search terms. Over all of `src/robot_brain/`
(`*.md`, `*.py`, `*.json`, `*.xml`, `*.cfg`) for
`wheel|omni|holonom|drive(train)?|chassis|base|arm|gripper|column|lift|camera|head|torso|shoulder|joint|reach|payload|body`,
the only body-shape claims in the package are `AGENTS.md:3-4` (fixed) and
`:48` ("Raises both shoulders with it" — true of two arms on one column).
`README.md`, `openclaw.robot.json`, `package.xml` and `setup.py` make no claim
about the robot's physical configuration. `README.md:267`'s "three failed
attempts" and `:277`'s "the first four are steps" are the only other `four`/
`three` hits and are not body claims.

My own line on the manager's question: **`known_locations` and the example
coordinates are world description, not body description** — they belong to the
seed world, and are correctly outside criterion 2 (they are separately worth
guarding, which is follow-up #4 and I agree with it). **Reach and column travel
*are* body claims**: reach is now pinned (this PR), the safety section's travel
range is pinned by `TestSafetyEnvelope`, and only the worked examples'
restatement of the travel range is unpinned. I do not think that last one is a
criterion-2 failure, on a distinction worth stating: retuning `limits.yaml`
turns the suite **red** (the safety-section set equality), so the drift is
*announced* and a careless second edit is needed to leave the examples stale —
materially unlike the reach, where a `RobotModel` change left the entire suite
green and silent. That is why round 1 was right to BLOCK the reach and why this
one is honestly a follow-up. Criterion 2 is met.

---

## Worktree hygiene

No source or test file was edited. All mutations ran on `/tmp/rtb` and
`/tmp/rtback`. `git status --short` is empty at the end of this pass, as it was
at the start. Build/test artifacts under `build/`, `install/`, `log/` are from
`pixi run test` and are gitignored.
