# Context: #67 — robot_brain system prompt still claims a four-wheel base

## Acceptance criteria (restated from the issue)

1. `src/robot_brain/robot_brain/openclaw/AGENTS.md:3` no longer claims a
   "four-wheel base" — it must describe the actual body: the LeKiwi
   3-omniwheel holonomic base (D26/D29), unchanged column + 2 arms + grippers.
2. Sweep the rest of `src/robot_brain/` for any other stale body-description
   claim and fix those too, or confirm there are none.
3. Owned paths per the issue: `src/robot_brain/robot_brain/openclaw/AGENTS.md`
   only (the analogous fix in `src/robot_description/` already shipped in PR
   #66 / commit `3a0958c`, out of scope here). Note the open question below
   about whether that scope should widen.

## 1. How `AGENTS.md` is shipped to the planner (traced, empirically-observed + inferred-from-source)

- **Source of truth**: `src/robot_brain/robot_brain/openclaw/AGENTS.md` — this
  *is* the live system prompt text (D21: "the brain is not a program in this
  repo"). Under D21 there is no prompt-assembly step; the file's prose is
  handed to OpenClaw verbatim.
- **Packaged as package data, not `share/`**: `src/robot_brain/setup.py:12`
  `package_data={package_name: ['openclaw/*.md', 'openclaw/*.json']}`,
  `include_package_data=True` (`setup.py:13`). Comment at `setup.py:9-11`
  explains why: readable from a source checkout *and* a symlink-installed
  `colcon build` alike, no ament index needed.
- **Loaded via `importlib.resources`**: `src/robot_brain/robot_brain/agent.py`
  — `operating_prompt()` (`agent.py:70-76`) calls `_read(PROMPT_RESOURCE)`
  (`agent.py:64-67`), `PROMPT_RESOURCE = 'AGENTS.md'` (`agent.py:57`), via
  `resources.files(RESOURCE_PACKAGE) / _RESOURCE_DIRECTORY / name`
  (`agent.py:66`, `RESOURCE_PACKAGE = 'robot_brain'`, `_RESOURCE_DIRECTORY =
  'openclaw'`). Memoized with `lru_cache(maxsize=1)`.
- **Deploy is a literal copy, documented but *not executed from this repo***:
  `src/robot_brain/README.md:91-99` — `scp
  src/robot_brain/robot_brain/openclaw/AGENTS.md
  pi:~/.openclaw/agents/robot/AGENTS.md`. `agent.py:53-56`: "OpenClaw reads an
  agent's system prompt from the `AGENTS.md` in its workspace... so the file
  ships under the name it must eventually be installed as." This is
  **inferred-from-source / documented-not-observed** — README.md:85-87 says
  plainly that everything needing the Pi (including this copy step) "cannot
  reach it... Steps 3, 4, 5, 7 and 8... are documented, not observed."
- **Net effect**: there is exactly one authored copy of the prompt text — the
  file at the owned path — and every consumer (tests, the loader, the deploy
  doc) reads/copies it verbatim. Fixing the source file is fixing "the live
  system prompt" in the sense the issue means (no separate generated or
  cached copy exists in-repo to also update). What is **not** verified by
  anything in this repo is that a *previously deployed* Pi copy gets
  refreshed — that's an operational/deploy action outside this feature's
  owned paths.

## 2. Existing tests that touch it, and why they didn't catch the drift

`src/robot_brain/test/test_prompt_drift.py` is the "prompt type-checker"
(docstring `test_prompt_drift.py:7-18`). It loads `PROMPT = operating_prompt()`
(`test_prompt_drift.py:37`) and checks it against **live sources**:
`robot_mcp.tools.TOOL_NAMES`/`TOOLS`, `robot_safety.SafetyLimits.defaults()`,
`robot_backends.default_world()`, and the `robot_skills` enums. Concretely it
asserts:
- the tool table lists exactly the tools the agent has (`test_prompt_drift.py:100-107`)
- every worked-example call is a real tool/args, deserializes at the schema
  (`:109-114`, `:160-169`)
- a withheld tool (`reset`) is never named (`:116-126`)
- **`test_the_prompt_names_nothing_the_system_does_not_have`**
  (`test_prompt_drift.py:128-136`): `inline_words(PROMPT) - live_vocabulary()`
  must be empty — but `inline_words` (`test/brain_fixtures.py:74-79`) only
  extracts identifiers from **backtick-fenced inline code spans**
  (`_INLINE = re.compile(r'`([^`\n]+)`')`, `brain_fixtures.py:56`), outside
  fenced code blocks. "a four-wheel base" in `AGENTS.md:3` is plain prose,
  not in backticks, so it is invisible to this check.
- safety-envelope numbers match `SafetyLimits.defaults()` (`:172-188`)
- failure codes are all documented (`:203-215`)
- worked examples use real locations/objects from `default_world()`
  (`:218-241`)
- required sections exist (`:269-280`)

**Why it didn't catch this drift**: every check in this suite is a
*checkable-claim* check — tool names, argument schemas, numeric limits, enum
values, world ids — each backed by a "live source" object the test imports
(`robot_mcp`, `robot_safety`, `robot_backends`, `robot_skills`). **There is no
live source object in this repo that represents "what the robot's body looks
like" in a form `robot_brain`'s tests could import and diff prose against** —
`robot_description`'s URDF is a separate package with no Python API this test
suite consumes (nor should it, per D21's "no ROS graph" design — see
`agent.py:20-24`). Free-form physical-embodiment prose (wheel count, base
kinematics, "extendable vertical column", "two arms with grippers") is simply
outside what `test_prompt_drift.py` was built to check — it can only catch
drift in claims that have a machine-checkable counterpart. This is the
"interesting question" the issue implies: **there is no guard here, and one
plausible follow-up (not this issue's scope to decide) is whether one should
exist** — see Open Questions below.

`test_openclaw_config.py` and `test_openclaw_validates.py` (also read) check
the *config fragment* (`openclaw.robot.json`) — schema validity against the
real `openclaw config validate` CLI, sandbox settings, tool exposure, secrets.
Neither reads `AGENTS.md` prose at all. Irrelevant to this drift.

## 3. Sweep of `src/robot_brain/` for body-description claims

Exhaustive grep for wheel/holonomic/base-kinematics/arm/column/gripper/camera/
head/DoF/reach-type claims across all `.py`/`.md`/`.json` under
`src/robot_brain/` (**empirically-observed** via `grep -rniE`):

| file:line | claim | consistent with D26/D29 + spec.md? |
|---|---|---|
| `robot_brain/openclaw/AGENTS.md:3` | "a four-wheel base" | **No** — D26/D29 supersede this with a 3-omniwheel holonomic base. **The one claim to fix.** |
| `robot_brain/openclaw/AGENTS.md:3-4` | "an extendable vertical column and two arms with grippers" | Yes — unchanged by D26/D29 (column mechanism changed from belt to linear-rail servo per D26, but "extendable vertical column" as prose is still accurate; "two arms with grippers" unchanged). |
| `robot_brain/openclaw/AGENTS.md:48` | "Raises both shoulders with it" (re: `extend_column`) | Yes — two arms, unaffected by base swap. |
| `robot_brain/openclaw/AGENTS.md:117,133,137,142-144,201` | column/reach/shoulder mentions (out_of_reach, column travel 0.0–1.2 m, arm speed 0.5 m/s, shoulder reach) | Yes — column travel numbers are asserted live against `SafetyLimits.defaults()` by `test_prompt_drift.py:175-188`, not hand-typed; unaffected by the base change. |
| `robot_brain/openclaw/AGENTS.md:137` | "Speed caps: base 0.6 m/s, column 0.15 m/s, arm 0.5 m/s" | Yes as a *number* (checked live, see above) — but note it is a single scalar "base" speed with no mention of holonomy/strafe (see §5, the open question). |
| `robot_brain/robot_brain/agent.py`, `__init__.py`, `setup.py`, `test/*.py` | no body-description prose found | N/A — these are all about OpenClaw plumbing (D21), not the robot's physical form. |
| `robot_brain/README.md` | no body-description prose found (all OpenClaw/deploy/testing narrative) | N/A |

No other stale claim exists in `src/robot_brain/`. **This was verified
exhaustively** (empirically-observed: `grep -rniE
"wheel|holonomic|omniwheel|base kinemat|column|arm\b|arms\b|gripper|camera|
head|dof|degrees of freedom|reach\b|elbow|claw|manipulator" src/robot_brain/`)
— every hit besides `AGENTS.md:3-4` is either OpenClaw/tooling prose or a
skill-API mention (gripper/arm as *skill vocabulary*, not a body-shape claim).

**Repo-wide cross-check** (outside owned paths, reported not fixed):
`grep -rn "four-wheel\|4-wheel\|four wheel"` across `src/` and `docs/` finds,
besides `AGENTS.md:3`:
- `docs/design/decisions.md:7,76,78,84` — historical decision-log entries
  (D1, D26). These are **append-only by design** ("D1-D28... the append-only
  source of truth") and correctly describe the *old* form as history, with
  D26 explicitly recording the supersession. Not stale — do not touch.
- `docs/design/spec.md:29` — `"LeKiwi 3-omniwheel holonomic" base | D26
  (supersedes D1's 4-wheel)"` — this is spec.md correctly stating the
  *current* fact and citing the superseded one; not stale.
- `src/robot_description/test/test_description.py:495` — a test comment
  referencing "D26 supersedes D1's 4-wheel base" as rationale for a
  perturbation test; not a live claim, out of `robot_brain` scope anyway.

So the sweep confirms the issue's framing: `AGENTS.md:3` is the **only** place
in the whole repo where "four-wheel" (or an equivalent claim) survives as a
*live, uncontextualized* fact rather than a historical/decision-log record.

## 4. The correct body description, sourced

From `docs/design/decisions.md`:
- **D1** (original, now partially superseded): "4-wheel base + extendable
  vertical column + 2 arms (elbow + claw) + head (RGB-D cam + mic)."
  (`decisions.md:7`)
- **D26** (2026-08-12, current): "Base — LeKiwi 3-omniwheel holonomic (tweaks
  D1's 4-wheel base). A holonomic 3-omniwheel base has a ready-made URDF, is
  the proven XLeRobot substrate, and is a far easier control problem than a
  custom 4-wheel base with no reference... Chosen: take the holonomic win."
  (`decisions.md:78`). Also: "Supersedes D1's base (4-wheel → 3-omniwheel)...
  D1's '2 arms + extendable column + head cam' intent is preserved."
  (`decisions.md:84`)
- **D29** (2026-08-13, built): "The mobile base is a *parametric* 3-omniwheel
  holonomic base built from LeKiwi's names, joint types, two dimensions and
  its mount convention..." (`decisions.md:105`) — this is the PR #66 build
  that actually landed the base geometry.

From `docs/design/spec.md` (the flattened current-state doc, which **is
already up to date** — checked, no stale base claim found there):
- `spec.md:29`: `| **Base** | **LeKiwi 3-omniwheel holonomic** base | D26
  (supersedes D1's 4-wheel) |`
- `spec.md:110-119` describes the built base in detail (D29): parametric
  3-omniwheel holonomic, `base_chassis_link`/`base_footprint` fixed children
  of `base_link`, wheels at 60°/180°/300° on `continuous` joints named
  `base_{left,back,right}_wheel`.
- `spec.md:30,32,33` (column, gripper, head camera) are unaffected by the base
  swap and match D26.

**No stale spot found in `spec.md` for this topic** — its base row and its
D29 prose are current. (Scope note: I did not audit all of `spec.md` for
unrelated staleness, only the base/body-description sections relevant to this
issue.)

## 5. What the skill API actually exposes re: base motion (evidence, not a recommendation)

The issue argues the affordance difference matters because holonomic bases
can strafe. What the planner can actually *command*, traced through
`robot_skills`:

- `src/robot_skills/robot_skills/skills.py:153-172` — `NavigateTo` is the
  **only** base-motion skill:
  ```python
  class NavigateTo(Skill):
      """Drive the base to a named location in the semantic map."""
      name: ClassVar[str] = 'navigate_to'
      location: str
  ```
  Its sole field is `location: str`, validated as an identifier
  (`as_identifier`, `skills.py:161-163`). No pose, no velocity, no heading, no
  direction argument anywhere on this or any other skill class in the file
  (`MoveGripper`, `Grasp`, `Place`, `ExtendColumn`, `OpenGripper`,
  `CloseGripper` — none address the base).
- `AGENTS.md:44` teaches exactly this: `| navigate_to | location | Drive the
  base to a named place from known_locations. |` — matching the schema
  (enforced live by `test_prompt_drift.py:143-146` against
  `SCHEMAS['navigate_to']`).
- The safety layer's base speed cap is a **single scalar**, not a per-axis
  one: `src/robot_safety/robot_safety/state.py:36-46` — `MotionAxis` enum has
  members `BASE = 'base'`, `COLUMN = 'column'`, `ARM = 'arm'` — one m/s number
  per axis, no `BASE_LATERAL`/`BASE_STRAFE` distinction. `MotionLimits`
  (`limits.py:159-193`) caps each `MotionAxis` with one `velocity_cap()`
  float. `AGENTS.md:137` states this as "Speed caps: base 0.6 m/s..." — one
  number, matching the schema.

**Evidence summary**: above the skill seam (CLAUDE.md invariant 1 — "the
brain commands skills (goals/poses), never raw joints"), the planner has no
way to request a strafe, a heading, or any base motion parameter other than
"go to this named place." The holonomic-vs-4-wheel distinction currently has
zero expressible effect at the `navigate_to(location)` call site — whatever
path/motion the base executes to satisfy a `navigate_to` call is entirely a
backend/planning-layer concern below the skill API, not something the LLM
selects. I did not find any code (skills, safety, or the prompt) where a
"can strafe" capability is or could currently be exercised by the brain.

## Owned paths / likely touch points

- **Owned (in scope)**: `src/robot_brain/robot_brain/openclaw/AGENTS.md`
  (lines 3-4) — the only stale claim found.
- **Not owned, already fixed**: `src/robot_description/` (PR #66, commit
  `3a0958c`).
- **Not owned, correctly historical**: `docs/design/decisions.md` (append-only
  log — do not edit).
- **Not owned, already current**: `docs/design/spec.md` — no change needed
  for this issue's scope.
- **Test file that must keep passing, and is the natural place to add a
  regression check if desired**: `src/robot_brain/test/test_prompt_drift.py`
  / `src/robot_brain/test/brain_fixtures.py`. Note: as shown in §2, this
  suite's existing machinery (`inline_words`/`live_vocabulary`) only checks
  backtick-fenced tokens against live catalogues, so it cannot itself
  distinguish "four-wheel" from "3-omniwheel holonomic" prose. A test that
  actually exercises this acceptance criterion would need a new, narrower
  assertion (e.g. asserting specific known-stale substrings are absent from
  the prompt, and/or that specific current substrings — "holonomic",
  "3-omniwheel" — are present) since there is no importable "body shape" live
  source to diff against the way tool names/limits/world ids are diffed.

## Known gotchas

- `operating_prompt()` is `lru_cache(maxsize=1)`-memoized
  (`agent.py:70-76`) — irrelevant across a single test/process run, but worth
  knowing if anything ever reloads the module in-process expecting a fresh
  read.
- The prompt is deliberately **hand-written prose, not generated**
  (`README.md:63-73`, `agent.py:15-19`): "prompt quality is a human
  deliverable (D22)... a generated one would read like a schema dump." Any
  fix should preserve that register/tone (see the rest of `AGENTS.md` for
  style — short declarative sentences, second person "you", concrete units).
- `test/test_prompt_drift.py::TestToolCatalogue::test_the_prompt_names_nothing_the_system_does_not_have`
  checks *inline-code* tokens against `live_vocabulary()`
  (`test_prompt_drift.py:68-94`, `brain_fixtures.py:74-79`) — if a fix adds
  new backticked words (e.g. `` `holonomic` `` or a tool/field name in
  backticks), make sure any such word is either plain prose (no backticks) or
  actually present in `live_vocabulary()`, or this test will fail. Plain
  descriptive words like "holonomic" or "3-omniwheel" written **without**
  backticks are unconstrained by this check (as the current "four-wheel" text
  demonstrates by having survived undetected).
- `test/test_pep257.py`, `test/test_flake8.py`, `test/test_copyright.py` exist
  in this package (read but not detailed above) — standard style/lint gates;
  `AGENTS.md` is Markdown so they likely don't apply to it, but any `.py`
  touched (e.g. if a new test is added to `test_prompt_drift.py` or
  `brain_fixtures.py`) must satisfy them.

## Open questions (for the manager to rule on)

1. **Scope of the fix in `AGENTS.md`**: should the prompt say just "a
   3-omniwheel holonomic base" (mirroring `robot_description`'s fix
   verbatim), or does it need any additional framing given §5's finding that
   the skill API currently gives the planner no way to exploit holonomy
   (`navigate_to` takes only a named location)? The issue's own argument
   ("materially different control affordance... it can strafe") is not
   actually true *of what the planner can command today* — is a purely
   descriptive correction sufficient, or should the prompt avoid implying a
   capability (strafing) the agent cannot invoke?
2. **Should `test_prompt_drift.py` gain a regression test for this class of
   drift** (a body-description claim with no live source to diff against),
   given §2's finding that the existing "prompt names nothing the system
   doesn't have" check structurally cannot catch free prose outside
   backticks? This would be new test-writing scope beyond a one-line prose
   fix — worth deciding whether it belongs in this issue or a follow-up.
3. **Deploy staleness**: since AGENTS.md is `scp`'d to the Pi as a manual,
   undocumented-as-executed step (README.md:91-99, §1 above), fixing the
   source file does not itself update any already-deployed Pi copy. Is that
   acceptable as "fixed" for this issue (the source-of-truth file is
   correct), or does it need a mention/reminder in the PR description for
   whoever next runs the Pi deploy steps?
