# Red-team review — `robot_safety` dynamic clamp/abort layer (issue #43)

Read-only review of the 4-commit branch against issue #43's acceptance
criteria, the manager's rulings R1–R14, and CLAUDE.md's architectural
invariants. Governing question throughout: **can this fail open?**

**Verdict: 1 BLOCK, 6 NOTES.** The implementation is unusually solid — every
one of the eight hypotheses the manager flagged as a likely defect source was
attacked concretely, and seven of them hold. The single BLOCK is not a bug in
what shipped but a fail-open-on-growth trap with established prior art in this
repo for exactly the guard that is missing.

---

## BLOCK

### B1 — the layer silently passes any *future* skill, and no test will notice

`src/robot_safety/robot_safety/layer.py:104` (`_closing_sides`),
`src/robot_safety/robot_safety/layer.py:261` (`_clamp`),
`src/robot_safety/robot_safety/collision.py:42` (`target_pose`),
`src/robot_safety/test/test_safety_layer.py:41-49` (`EVERY_SKILL`).

Three separate dispatch points enumerate skill types by `isinstance`:

```python
layer.py:104      if not isinstance(skill, (CloseGripper, Grasp)):  return ()
layer.py:261      if not isinstance(skill, ExtendColumn):           return skill, ()
collision.py:42   if isinstance(skill, (MoveGripper, Place)):       return skill.pose
```

Every one of them defaults to **permissive** for an unrecognised skill, and
none of them fails loudly when the skill API grows. The test suite's
`EVERY_SKILL` is a hard-coded 7-tuple, so it will not grow either.

**Concrete failure scenario.** A later feature adds a skill to `robot_skills`
— `robot_mcp`'s own tests use `wipe_surface`
(`src/robot_mcp/test/test_schemas.py:176-181`) and `spin`
(`:190`) as the worked examples of exactly this event. Say
`TiltHead(angle: float)` lands in `SKILL_TYPES`. Then:

* `_clamp` passes it through unclamped — `angle` is a scalar with a physical
  range that nobody decided on;
* `target_pose` returns `None`, so `KeepOutBoxGuard` waves it through even if
  the skill later grows a pose;
* `_closing_sides` returns `()`, so it is outside the force check;
* **all 138 `robot_safety` tests still pass.** Nothing anywhere in the
  workspace signals that a new skill entered the world with no safety
  disposition.

**Why this is BLOCK and not NOTE.** (a) It is a fail-open in a safety layer,
triggered by ordinary forward development rather than by a mistake. (b) The
package already applies precisely this discipline to its *other* vocabulary
and says so in prose: `MotionLimits.__post_init__`
(`limits.py:169-172`) makes a `MotionAxis` with no configured cap a **load
error**, and `limits.yaml:27-29` states the rationale — "adding an axis to the
enum is a load error until it is given a cap here rather than silently
becoming uncapped." The identical argument applies to skills, and is not
applied. (c) There is direct in-repo prior art: `robot_mcp` derives its whole
tool catalogue from `SKILL_TYPES` and pins it with
`assert tools.TOOL_NAMES == set(SKILL_TYPES) | ...`
(`src/robot_mcp/test/test_schemas.py:64`), plus monkeypatch tests proving the
catalogue *grows* when the registry does (`:176-198`). `robot_safety` consumes
the same registry and has no equivalent.

**Fix direction (cheap — a test, not a redesign).** Add one tripwire to
`test_safety_layer.py` that derives from the registry rather than restating it:

```python
from robot_skills import SKILL_TYPES
def test_every_registered_skill_has_a_considered_safety_disposition():
    assert {s.name for s, in ...EVERY_SKILL params...} == set(SKILL_TYPES)
```

so that adding a skill upstream fails `robot_safety`'s suite until someone
decides whether it is clamped, force-checked, geometry-checked, or explicitly
none of the three. Optionally also derive `EVERY_SKILL` itself from
`SKILL_TYPES` where the parametrisation allows. A stronger variant (a
`_DISPOSITIONS: Mapping[type[Skill], ...]` table that must be total over
`SKILL_TYPES`) is more code than this issue needs; the tripwire test is
sufficient and matches `robot_mcp`'s pattern.

---

## NOTES

### N1 — over-force inhibits only jaw-closing skills, with no hard ceiling above it

`src/robot_safety/robot_safety/layer.py:237-253`, `:91-108`; R11.

Attacked as instructed, in both directions.

**(b) `Grasp(side=None)` holds.** `_closing_sides` returns `SIDE_ORDER`
(`robot_skills/skills.py:82` = `(LEFT, RIGHT)`), so *both* grippers are
checked, and `test_over_force_while_closing_is_a_safety_event[grasp-either-side]`
(`test_safety_layer.py:148`) proves it with a state where only the *left* jaw
is over-force. No default-side hole.

**(a) The carve-out is defensible, but it is unbounded above.** I initially
scored this a BLOCK on the grounds that it contradicts R5's own "the whole
machine is in an unsafe dynamic state" reasoning (which aborts *every* skill
when *any* axis is over its cap). On reflection the asymmetry is justified:
a base at 2 m/s is a runaway, whereas a jaw at 45 N against a 40 N cap is a
*firm hold*. If over-force inhibited all motion, a robot carrying anything
near the ceiling would be paralysed with only `OpenGripper` available, i.e.
forced to drop what it is holding. R11 survives.

What does *not* survive is the top of the range. With
`gripper_forces={LEFT: 900.0}` the layer today accepts `NavigateTo('kitchen')`,
`MoveGripper`, `Place` and `ExtendColumn` — and
`test_over_force_never_blocks_the_skills_that_do_not_close_jaws`
(`test_safety_layer.py:186-205`) uses literally 900 N to assert that. 900 N is
not a firm grip on a mug; it is a crush, and the layer's answer is "carry on,
drive to the kitchen." Follow-up direction: a second, higher threshold in
`limits.yaml` (`gripper.abort_force` alongside `gripper.max_force`) above which
*all* motion except `OpenGripper` aborts — one number, one check, and it keeps
R11's carry-a-heavy-mug case working. Worth an issue before the Real backend.

### N2 — absent telemetry is permissive, with no way for a deployment to demand a reading

`src/robot_safety/robot_safety/state.py:93-94` (both mappings default to `{}`),
`layer.py:221-222` and `layer.py:241-242` (`if ... is None: continue`).

Answering the manager's question directly: an absent axis/side is treated as
**unknown-and-unjudged**, not as `0.0`. That is the better of the two readings
the question posed (it is not fail-open-as-zero), it is documented
(`state.py:85-88`), and it is tested both at the state level
(`test_safety_state.py:49-54`) and at the layer level
(`test_safety_layer.py:208-212`, `:296-300`). The reasoning in
`implementation.md` — "unmeasured ⇒ abort would make every `Grasp`
unexecutable against today's only backend" — is sound.

But the *net effect* is still permissive, and there is no opt-in to change it.
`SafetyState(observation=obs)` — the exact shape used by the integration
test's `sample()` helper (`test_mock_backend_integration.py:20-27`) — passes
every velocity and every force check. A Real backend with a dead base encoder
reports nothing, and the base becomes uncapped with no signal. Fix direction:
a `require_readings` list in `limits.yaml` (default empty, so Mock keeps
working) naming axes/sides whose absence is itself a `SafetyEvent`. Cheap, and
it turns "we chose permissive" from a hard-coded property into a deployment
decision. File before the Real backend lands.

### N3 — the abort/clamp discipline (R9) is enforced on one side only, and that side is untested

`src/robot_safety/robot_safety/layer.py:203-210` (`_check_collision`),
`layer.py:65-83` (`ClampedCall.__post_init__`).

R9 attacked as instructed. `is_clamp` is reliable for every event the layer
itself constructs: the four abort sites (`layer.py:198`, `:226`, `:244`,
`collision.py:103`) never set `clamped_value`, and the one clamp site
(`layer.py:268-277`) always does. `ClampedCall` additionally *rejects* an abort
event placed in `clamps` (`layer.py:79-82`). So a caller cannot be confused by
anything this package produces, and the real discriminator is
`isinstance(verdict, ClampedCall)`, not `is_clamp` — the ruling holds.

Two residual gaps, both small:

1. The mirror check is missing. `_check_collision` validates that an injected
   guard returned a `SafetyEvent` (and there is a good test for the truthy-junk
   case, `test_collision_guard.py:161-173`) but **not** that it returned an
   *abort*. A third-party guard returning
   `SafetyEvent(kind=COLLISION_RISK, detail='x', clamped_value=0.5)` is handed
   straight back from `filter` as an abort whose `is_clamp` is `True` —
   precisely the state `ClampedCall` refuses on the other side. One line:
   `if event.is_clamp: raise TypeError(...)` beside the existing type check.
2. `ClampedCall.__post_init__` has **no test at all** — nothing in
   `src/robot_safety/test/` constructs a `ClampedCall` directly (grepped:
   zero matches for `ClampedCall(`). All four of its defensive branches,
   including the R9 discipline check, are dead to the suite.

### N4 — `MotionLimits` enforces cap positivity only on the YAML path

`src/robot_safety/robot_safety/limits.py:161-178` vs `:93-104`, `:192-197`.

`_get_positive` rejects zero/negative caps with a good rationale
(`limits.py:95-99`), and `test_limits_config.py:135-145` covers negative, zero
and infinite caps — but only through `from_mapping`. `MotionLimits.__post_init__`
itself checks finiteness and axis-completeness and nothing else, so
`MotionLimits(velocities={BASE: -1.0, COLUMN: 1.0, ARM: 1.0}, max_gripper_force=0.0)`
constructs fine. `ColumnLimits` does *not* have this gap — it enforces
`min_height < max_height` in `__post_init__` (`limits.py:119-122`), so direct
construction is safe there. The asymmetry is the smell.

Not fail-open (a non-positive cap aborts everything, i.e. fails closed), so
NOTE rather than BLOCK — but the next feature that builds `MotionLimits`
programmatically (ROS params, a Real-backend calibration step) gets no check.
Fix: move the positivity rule into `MotionLimits.__post_init__` and let
`_get_positive` keep only its key-naming duty.

### N5 — `KeepOutBoxGuard.from_limits` on a boxless config yields a silently inert guard

`src/robot_safety/robot_safety/collision.py:90-93`, `limits.py:319-323`.

`keep_out_boxes` is the one optional YAML section, and omitting it is legal and
tested (`test_limits_config.py:199-204`). Combined with
`KeepOutBoxGuard.from_limits(limits)`, a deployment that wires the guard in
against a YAML missing the section gets a guard that checks nothing and says
nothing — the "dead parameter" R7 exists to prevent, arrived at by
configuration rather than by design. Fix direction: have `from_limits` raise
`SafetyConfigError` when `limits.keep_out_boxes` is empty (explicitly-empty
stays expressible as `KeepOutBoxGuard(())`), or log/require an explicit
`allow_empty=True`.

### N6 — an injected guard that *raises* takes the whole call down

`src/robot_safety/robot_safety/layer.py:205`.

`self._collision_guard.check(skill, state)` is called unguarded, so an
exception from a third-party guard (a MoveIt/MuJoCo checker mid-shutdown, a
transform lookup timeout) propagates out of `filter`. The brief and
`events.py:13` insist a safety verdict is "data, never an exception"; this is
the one path where the layer emits an exception it did not choose.

Assessed as NOTE, not BLOCK, because it fails **closed**: `filter` raises, so
the caller never gets a `ClampedCall` and nothing executes. But the behaviour
is undocumented and untested. Fix direction: either catch `Exception` and
convert to a `COLLISION_RISK` abort event naming the guard (fail-closed *and*
structured), or state explicitly in `CollisionGuard`'s docstring that `check`
must be total and that exceptions are the caller's to handle — plus a test
either way.

---

## Hypotheses attacked that hold (stated explicitly, per instruction)

**H1 — NaN / non-finite fail-open: holds, comprehensively.** I traced every
route a non-finite number could reach a comparison and found none.
`SafetyState.velocities` / `.gripper_forces` are coerced through
`as_finite_float` *and* a non-negativity check at construction
(`state.py:49-71`, called from `__post_init__` at `:105-114`), so
`SafetyState(observation=obs, velocities={'base': float('nan')})` raises
`ValueError` — tested at `test_safety_state.py:76` (`nan-speed`) and `:80`
(`negative-force`). `+inf`/`-inf` are rejected by the same `isfinite` gate
(`robot_skills/validation.py:31-32`); the infinite case is covered on the
config side (`test_limits_config.py:143`, `id='infinite-velocity-cap'`) and on
the event side (`test_safety_events.py:91`, `id='non-finite-limit'`).
`ExtendColumn.height` is finite-validated upstream
(`robot_skills/skills.py:283-284`), as are `Point.x/y/z`
(`robot_skills/geometry.py:44-47`) and every configured limit
(`limits.py:88`, `:116-118`, `:168`, `:177`, `:235`). There is no field in the
`(skill, state, limits)` triple that can hold a NaN by the time `filter`
compares it. Bonus: even if a NaN *did* reach `KeepOutBox.contains`
(`collision.py:248-260`), all comparisons go `False` and the point is judged
**inside** the box — fail-closed, the right direction.

**H3 — boundary conditions: hold, consistently, and are documented.** All
three magnitude checks treat the limit as **inclusive-allowed**: velocity
`speed > cap` (`layer.py:225`), force `force <= cap: continue`
(`layer.py:242`), column `height > max_height` / `< min_height`
(`limits.py:130-133`). Each has an at-the-boundary test:
`test_an_axis_at_its_cap_is_allowed` (`test_safety_layer.py:277-281`),
`test_force_at_the_limit_is_allowed` (`:179-183`),
`test_a_column_target_inside_the_range_is_left_alone[0.0|1.0]` (`:118-126`).
`ExtendColumn(height == max_height)` returns via `violated_bound() is None` →
`return skill, ()` (`limits.py:134`, `layer.py:265-266`), so **identity is
preserved** per R6 — the parametrised test asserts `verdict.skill is skill`
(`:125`). Keep-out bounds are inclusive in the *opposite* direction (a point
exactly on a boundary is *inside*, `collision.py:249` and
`test_collision_guard.py:64` `id='on-the-lower-corner'`) — which is the correct
inverse, since inclusivity there means more conservative, not less. The policy
is stated in `implementation.md` ("Caps are ceilings, not fences") and in each
test's docstring. Nothing to fix.

**H7 — check order: holds, deterministically.** Order in `filter`
(`layer.py:181-192`) is e-stop → collision → velocity → force → clamp, exactly
R10. The simultaneous case the manager asked for is a real test:
`test_the_estop_is_reported_ahead_of_any_clamp_or_other_abort`
(`test_safety_layer.py:229-239`) sets `estop_engaged=True`,
`velocities={'base': 9.0}`, `gripper_forces={LEFT: 900.0}` and filters
`ExtendColumn(9.0)` — all four conditions true at once — and asserts
`ESTOP_ENGAGED`. Adjacent ranks are pinned too (`:307-334`). Velocity axes are
walked in `MotionAxis` enum order rather than dict order (`layer.py:220`), so
the reported axis is deterministic when several are over. Statelessness (R2) is
tested by re-asking after an e-stop sample and getting the original verdict
back (`:242-247`, `:337-352`).

**H8 — YAML strictness: holds.** `yaml.safe_load` only (`limits.py:337`), with
a test that a `!!python/object/apply` tag is rejected
(`test_limits_config.py:221-226`). A **partial** file is rejected loudly, not
defaulted: missing top-level section (`:190-196`), missing key within a
section (`:119`, `:129`, `:147`), unknown key at any level (`:131`, `:159`,
`:181-187`), non-finite (`:125`, `:143`), negative and zero caps (`:135-146`),
`min >= max` for both the column (`:121`) and each box axis (`:155`), wrong
types (`:148`, `:149`), duplicate box labels (`:165-172`), non-YAML and empty
text (`:207-218`). No key anywhere falls back to a hard-coded default —
verified by reading `limits.py` end to end for a literal metre/newton and
finding none, which is itself pinned by
`test_defaults_are_read_from_the_yaml_and_not_baked_into_python`
(`:65-82`), which retunes the parsed dict and asserts the limits move with it.
A malformed file cannot produce a `SafetyLayer` with no effective limits: it
raises before construction. R13 holds.

**H6 (second half) — `KeepOutBoxGuard` is genuinely exercised, not a fake.**
It aborts a real `MoveGripper` and a real `Place`
(`test_collision_guard.py:76-92`), its containment is checked at six points
including corners and an unbounded axis (`:60-74`), and the *shipped*
`below_floor` region stops a real command while letting a nearby legal one
through (`:129-139`). Not a `return None` stub.

**H10 — read-only discipline.** No Bash tool in this session, so I could not
run `git diff origin/main --stat` directly; instead I read
`src/robot_skills/robot_skills/validation.py`, `result.py`'s
`FailureCode`/`SAFETY_EVENT_CODES` block, `skills.py`'s `SIDE_ORDER`/
`SKILL_TYPES`/`ExtendColumn` and `geometry.py`'s `Point`/`Pose` and compared
them line-for-line against `context.md`'s pre-implementation transcription
(written from `origin/main`). They match, including line numbers
(`SIDE_ORDER` at `skills.py:82`, `SKILL_TYPES` at `:331`,
`SAFETY_EVENT_CODES = frozenset({REJECTED})` at `result.py:134-136`).
`robot_safety` consumes only public exports plus
`robot_skills.validation`'s `__all__` members (`as_enum`, `as_finite_float`,
`as_identifier`) — it does **not** reach into the non-exported
`serialization.check_keys`/`get_float`, exactly as R13 required; it wrote its
own `_check_keys`/`_get_float` (`limits.py:66-104`). No `robot_skills` edit,
no D18 escalation needed.

**The `scripts/tests/test_ratchet.py` edit does not weaken that test.**
`test_the_real_skeleton_packages_are_still_read_as_skeletons`
(`scripts/tests/test_ratchet.py:316-331`) still asserts both directions: the
four remaining skeletons are `()` (`:326-328`) and the three implementation
packages are truthy (`:329-331`, `robot_safety` now beside `robot_skills` and
`robot_backends`). Moving a package across as it gains code is the intended
maintenance of that test, not an erosion of it. Concurring with the manager's
conclusion.

---

## Test adequacy — explicit assessment

**Adequate, and above the repo's bar.** 135 non-linter tests across 7 modules.
Every acceptance criterion in issue #43 maps to at least one test that would
**fail** if the corresponding check were deleted:

| criterion | test | would fail if the check were deleted? |
|---|---|---|
| over-limit → clamped to limit | `test_safety_layer.py:97-115` | yes — asserts `was_clamped`, the clamped height, and `offending_value`/`limit`/`clamped_value` |
| over-force close → `SafetyEvent` | `:151-167` | yes — `isinstance(verdict, SafetyEvent)` |
| e-stop → abort-all | `:220-226` (× 7 skills) | yes |
| in-limit → pass-through unchanged | `:57-69` (× 7 skills) | yes — `is`, not `==` |
| velocity cap enforced | `:254-262`, `:266-274` (× 3 axes) | yes |
| limits from YAML w/ defaults | `test_limits_config.py:65-82` | yes — retunes the file and asserts the limits move |
| collision hook w/ stub geometry | `test_collision_guard.py:83-92` | yes |

Tests that earn their place rather than restating the code: the `is`-identity
pass-through (`test_safety_layer.py:66-69`) catches a needless rebuild that
`==` would wave through; `assert skill.height == commanded` (`:108`) catches a
layer that mutates the caller's object; the whole-world dict diff across an
abort (`test_mock_backend_integration.py:69-87`) mirrors how `robot_backends`
proves its own refusals; `test_a_guard_that_breaks_the_protocol_is_not_quietly_believed`
(`test_collision_guard.py:161-173`) is a genuine adversarial case;
`test_defaults_are_read_from_the_yaml_and_not_baked_into_python`
(`test_limits_config.py:65-82`) is the single best test in the feature — it
would catch the exact drift R13 exists to prevent.

**I could not find a test that would still pass if its corresponding check
were deleted**, with these caveats:

* `test_shipped_defaults_are_documented_in_the_file_itself`
  (`test_limits_config.py:85-96`) asserts `len(comment_lines) > 20`. That is a
  proxy metric, not a behavioural assertion — it would pass on 21 lines of
  gibberish. Harmless as a doc-rot tripwire; not evidence of anything.
* `test_an_event_is_data_and_not_an_exception` (`test_safety_events.py:55-61`)
  and `test_states_with_equal_readings_compare_equal`
  (`test_safety_state.py:89-92`) restate dataclass-generated behaviour. Cheap,
  and each names a real regression (making `SafetyEvent` an `Exception`), so
  they stay.

**Coverage gaps** (already itemised above): `ClampedCall.__post_init__` has
zero tests (N3); no test for a raising collision guard (N6); no test for direct
`MotionLimits` construction with a non-positive cap (N4); and, the BLOCK, no
test that fails when the skill registry grows (B1). Nothing over-parametrised:
the `× 7 skills` parametrisations genuinely exercise the abort-all and
pass-through criteria across the whole API surface rather than repeating one
trivial case.

---

## Architectural invariants (CLAUDE.md)

1. **Skill API is the seam** — respected: the layer commands only `Skill`
   objects, rewrites exactly one scalar field, and never touches joints.
2. **Mock first** — respected and demonstrated:
   `test_mock_backend_integration.py` proves the clamped call is executable by
   the very backend that refused the unclamped one, via a `<test_depend>` with
   no runtime dependency and no cycle.
3. **Safety layer clamps/rejects; never bypassed** — the layer exists and is
   correct; nothing wires it in yet, which is an explicit non-goal of #43. The
   "never bypassed" half becomes enforceable in the integration issue.
4. **Structured scene JSON** — n/a; `Observation` is consumed unmodified
   (`state.py:91`, identity-asserted at `test_safety_state.py:26`).
5. **Reuse, don't reinvent** — respected: `collision.py:9-13` explicitly defers
   real geometry to MoveIt rather than growing a bespoke checker, and the stub
   is scoped to a keep-out box.

R12's packaging risk (the one the manager flagged) is closed: the YAML loads
through `importlib.resources` (`limits.py:352`), `setup.py:12-13` declares
`package_data` + `include_package_data`, and
`test_limits_config.py:38-47` plus the clean-subprocess probe in
`test_no_ros_runtime.py:26-79` (which asserts no `rclpy`/`rosidl`/
`ament_index_python` in `sys.modules` *and* clamps a skill using the packaged
defaults) lock it against regression.

---

## Summary

| id | severity | one-line |
|---|---|---|
| B1 | **BLOCK** | three `isinstance` dispatch points default permissive for unknown skills, and no test fails when `SKILL_TYPES` grows |
| N1 | NOTE | over-force has no hard ceiling: at 900 N the layer still allows `NavigateTo`/`MoveGripper`/`Place` |
| N2 | NOTE | absent telemetry is permissive with no opt-in to require a reading (dead encoder ⇒ uncapped axis) |
| N3 | NOTE | `_check_collision` does not reject a clamp-flavoured event; `ClampedCall.__post_init__` is untested |
| N4 | NOTE | `MotionLimits` enforces cap positivity only on the YAML path, unlike `ColumnLimits` |
| N5 | NOTE | `KeepOutBoxGuard.from_limits` on a boxless config yields a silently inert guard |
| N6 | NOTE | an injected guard that raises propagates out of `filter` (fails closed, but undocumented and untested) |

B1 is a one-test fix. N1, N2 and N5 are the ones worth carrying to the issue as
follow-ups before the Real backend lands.
