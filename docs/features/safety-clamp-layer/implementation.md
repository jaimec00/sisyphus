# Implementation — `robot_safety`: dynamic clamp/abort safety layer (issue #43)

Final state after the red-team round: **169 tests in `robot_safety`, full
workspace suite green** (521 tests, 0 failures, test-integrity audit passed —
see "Verification" below). All rulings R1–R14 implemented as written; no ruling
was deviated from and no escalation was needed. `src/robot_skills/**` was
**not** edited.

Red-team round 1 (1 BLOCK, 6 NOTES): **B1, N3, N4 and N5 are fixed** — see
"Red-team round 1" below. N1, N2 and N6 were ruled out of scope by the manager
and are follow-ups.

## What shipped

`src/robot_safety/robot_safety/`, six modules:

| module | contents |
|---|---|
| `state.py` | `MotionAxis` (`BASE`/`COLUMN`/`ARM`), `SafetyState` — one telemetry sample |
| `events.py` | `SafetyEventKind` (6 members), `SafetyEvent` — the structured verdict |
| `limits.py` | `ColumnLimits`, `MotionLimits`, `KeepOutBox`, `SafetyLimits`, `SafetyConfigError` |
| `limits.yaml` | the shipped defaults, **the single source of every number** |
| `policy.py` | `SKILL_POLICIES`, `SkillPolicy`, `policy_for`, `unclassified_skills` — which checks apply to which skill |
| `collision.py` | `CollisionGuard` protocol, `NullCollisionGuard`, `KeepOutBoxGuard`, `target_pose` |
| `layer.py` | `ClampedCall`, `SafetyLayer.filter(skill, state) -> ClampedCall \| SafetyEvent` |

Tests, `src/robot_safety/test/` (8 modules + the 3 pre-existing linters):
`safety_fixtures.py` (local builders — the cross-package import of
`skill_api_fixtures` is impossible, per context §6), `conftest.py`,
`test_limits_config.py`, `test_safety_state.py`, `test_safety_events.py`,
`test_skill_policy.py`, `test_safety_layer.py`, `test_collision_guard.py`,
`test_mock_backend_integration.py`, `test_no_ros_runtime.py`.

`package.xml` gained `<depend>robot_skills</depend>`,
`<exec_depend>python3-yaml</exec_depend>` (R14) and
`<test_depend>robot_backends</test_depend>` (see "Choices" below).
`setup.py` gained `package_data={'robot_safety': ['*.yaml']}` +
`include_package_data=True` (R12). `README.md` rewritten from the one-line
skeleton stub.

## Acceptance criteria → the tests that hold them

| criterion | test |
|---|---|
| over-limit target → clamped to limit | `test_safety_layer.py::test_an_over_limit_column_target_is_clamped_to_the_limit` (above-max and below-min, asserting `offending_value`/`limit`/`clamped_value` and that the caller's own skill was not mutated) |
| over-force close → `SafetyEvent` | `::test_over_force_while_closing_is_a_safety_event` (`CloseGripper`, `Grasp(side=…)`, `Grasp(side=None)` → both sides checked) |
| e-stop → abort-all | `::test_the_estop_aborts_every_skill` (parametrized over **every registered skill**, derived from `SKILL_TYPES`) + `::test_the_estop_is_reported_ahead_of_any_clamp_or_other_abort` |
| in-limit call → pass-through unchanged | `::test_an_in_limit_call_passes_through_unchanged` (every registered skill, asserted with **`is`** per R6) |
| velocity cap enforced | `::test_a_measured_speed_over_its_cap_aborts`, `::test_every_axis_is_capped`, `::test_an_axis_at_its_cap_is_allowed`, `::test_an_axis_unrelated_to_the_skill_still_aborts_it` (R5) |
| limits from YAML with documented defaults | `test_limits_config.py` — the shipped file is packaged, parses, populates every field, and its numbers *are* the defaults (a retuned copy produces retuned limits) |
| R11: `OpenGripper` never blocked | `::test_over_force_never_blocks_the_skills_that_do_not_close_jaws` |
| full suite + integrity guard | `pixi run test`, transcript below |

Tests that exist to catch specific bugs rather than restate the code:
pass-through identity checked with `is` (an equal rebuild would pass `==`);
the caller's `ExtendColumn` asserted unmutated after a clamp; a guard returning
a truthy non-event raises instead of being believed; the layer re-asked after
an e-stop sample returns the *original* verdict (it never latches);
`test_an_aborted_call_never_touches_the_world` diffs the Mock's whole
serialized world across an abort, the way `robot_backends` proves its own
refusals.

## Choices made inside the rulings

**`MotionLimits` vs `SafetyLimits` (R6).** R6 types `ClampedCall.limits` as
`MotionLimits` without saying what that is. I split the config in two: the
*envelope* a backend must honour during motion (per-axis speed caps + jaw-force
ceiling) is `MotionLimits` and rides out with every accepted call; the column
travel range and the keep-out regions are *decision inputs* the layer consumes
itself and do not travel. So `SafetyLimits = ColumnLimits + MotionLimits +
keep-out boxes`, and `ClampedCall.limits is layer.limits.motion`.

**Keep-out boxes are half-spaces, not corner pairs.** Every bound
(`x_min`…`z_max`) is optional and an omitted bound means unbounded on that
side. A `min_corner`/`max_corner` pair would have forced the shipped
"below the floor" region to invent a finite horizontal extent — a fake number
that would silently stop guarding at ±50 m. A box with *no* bound is a load
error (it would exclude the world). Labels are required and unique, so a
`COLLISION_RISK` event names the region it tripped.

**The shipped `keep_out_boxes` is not empty.** One documented region,
`below_floor` (`z_max: -0.02`): the world frame's floor is z=0, so a gripper
target below it is a bad transform or a hallucinated pose. It makes
`KeepOutBoxGuard.from_limits(SafetyLimits.defaults())` meaningful out of the
box, and it is tested
(`test_collision_guard.py::test_the_shipped_keep_out_regions_stop_a_target_below_the_floor`).
It changes nothing for a default `SafetyLayer()`, whose guard is still
`NullCollisionGuard` (R7).

**Partial telemetry is honest telemetry.** `SafetyState.velocities` and
`.gripper_forces` are *partial* mappings: an absent key means "no reading", not
"zero". A backend that cannot measure jaw force (the Mock) leaves it out rather
than reporting a fictitious 0.0 N. Consequence, stated in the docstrings and
tested: an unread axis/side is not judged. The conservative alternative —
"unmeasured ⇒ abort" — would make every `Grasp` unexecutable against today's
only backend, i.e. the layer would be turned off in practice.

**Caps are ceilings, not fences.** `speed > cap` and `force > cap` abort;
exactly *at* the cap passes. Tested both ways, so the boundary is a decision
rather than an accident.

**`SafetyConfigError`** (a `ValueError`) is raised for a malformed limits file,
naming the offending key; `TypeError` is raised for a caller passing the wrong
*type* into `filter`/`SafetyLayer(...)`. A caller bug must surface as a caller
bug — turning it into a safety event would hide it behind a plausible-looking
refusal.

**Velocity caps are checked in `MotionAxis` enum order**, not dict order, so
the axis reported when several are over is deterministic.

**Mock integration test (`test_mock_backend_integration.py`).** CLAUDE.md
invariant 2 says new code must work against the Mock first, so I added a
`<test_depend>robot_backends</test_depend>` (test-only; no runtime dependency,
no cycle — `robot_backends` does not depend on `robot_safety`) and four tests
proving the seam composes: the backend refuses `ExtendColumn(9.0)` with
`OUT_OF_RANGE`, the layer clamps it, the backend then accepts it; an in-limit
skill reaches the backend untouched; aborted calls leave the world byte
identical; and a safety event's `failure_code` lands on the safety half of the
shared enum while the Mock's refusal lands on the backend half (D17's split,
demonstrated rather than asserted in prose). This also pins that the shipped
defaults are compatible with the Mock's column model — a layer whose limits
were laxer than the machine's would clamp to a value the machine still rejects.

**One `# noqa`, the repo's first.** `flake8-builtins` flags `A003` on
`SafetyLayer.filter` (shadows the builtin). The method name is the published
contract in issue #43 and in R1/R2, so the line is silenced with a comment
explaining why, rather than renaming the seam around a linter. If the manager
prefers a different name, it is a one-line change plus the docs.

## Red-team round 1 — what changed

### B1 (BLOCK) — permissive default dispatch for unknown skills

Three sites enumerated skill types by `isinstance` and defaulted to *let it
through*: the force check, the column clamp, and `target_pose`. A skill added
upstream — a routine feature — would have arrived unclamped, ungeometried and
outside the force check while every test stayed green, because a hand-written
7-tuple of skills cannot notice an eighth.

**Fixed by an exhaustive table plus a runtime refusal, and both were needed.**

`robot_safety/policy.py` enumerates the vocabulary exactly once, keyed by wire
name (the same key `SKILL_TYPES` uses), with three flags per skill —
`closes_jaws`, `clamps_column_height`, `has_cartesian_target` — and a comment
per row saying *why*. "Nothing applies" is an explicit `SkillPolicy()` row for
`NavigateTo` and `OpenGripper`; it can no longer be reached by omission. The
three check sites now read the table.

I took the manager's option on the failure mode: **an unclassified skill is
refused at runtime** (`SafetyEventKind.UNCLASSIFIED_SKILL`, checked second,
right after e-stop and before any guard is asked). A gate that cannot say which
checks apply to a command has no basis for letting it through, and a
test-time-only tripwire protects this repo's own maintainers but not a
downstream workspace that adds a skill without running our suite. It is a
`SafetyEvent` rather than a raise because that is the layer's contract —
structured refusal on the return path — and because the situation is a
workspace-integration gap, not a caller type error (those still raise).

The test tripwire is the second half, not an alternative:
`unclassified_skills()` compares the table against `SKILL_TYPES` and must
return `()`; `test_skill_policy.py` also pins that the suite's example skills
*are* the registry, that each flag implies the field the layer will reach for
(`fields()` on the skill dataclass), and that exactly one skill is clamped.
`EVERY_SKILL` in `test_safety_layer.py` is now derived from `SKILL_TYPES`
instead of hand-written.

Verified empirically that the tripwire fires on a *genuinely* new registered
skill, not just a synthetic mapping — defining a `Skill` subclass registers it
in the live `SKILL_TYPES` registry:

```
in registry: True
tripwire: ('wipe_surface',)
verdict: SafetyEvent SafetyEventKind.UNCLASSIFIED_SKILL
```

The stand-in used by the tests (`safety_fixtures.UnclassifiedSkill`) is
declared `register=False` precisely so importing the fixtures cannot pollute
the shared registry for the rest of the session — with a test asserting that.

### N3 — R9's abort/clamp discipline was one-sided and untested

`_check_collision` now rejects an injected guard's event that carries a
`clamped_value` (`ValueError`, "a guard aborts, it never rewrites"), mirroring
the check `ClampedCall` already made in the other direction. `ClampedCall`'s
four defensive branches had *no* tests — nothing constructed one directly —
and now have four (`was_clamped` both ways, the abort-event rejection, and the
three malformed-argument cases).

### N4 — cap positivity was enforced only on the YAML path

`MotionLimits(velocities={BASE: -1.0, …}, max_gripper_force=0.0)` used to
construct fine. The rule moved into `MotionLimits.__post_init__` via a new
`_as_positive` helper; `_get_positive` keeps only its key-naming duty on the
parse path. A limit set is not always born from a file (a test, a caller tuning
one section, a future ROS-parameter bridge), and a negative "cap" reaching a
`speed > cap` comparison would silently disable that axis.

### N5 — `from_limits` on a boxless config yielded a silently inert guard

`KeepOutBoxGuard.from_limits` now raises `SafetyConfigError` when the limit set
configures no regions. Wiring in a geometry check and getting silence is worse
than not wiring one in; an intentionally empty guard stays expressible as
`KeepOutBoxGuard(())`.

### Not implemented (manager ruled out of scope, filed as follow-ups)

N1 (`gripper.abort_force`, a second higher threshold), N2 (`require_readings`,
making absent telemetry itself an event), N6 (a guard that raises propagates
out of `filter` — fail-closed, left deliberate).

## Verified empirically

### R12 — YAML packaging (the flagged risk). Verified three ways, all green.

1. **Source tree**, no install sourced:
   ```
   $ pixi run python -c "import sys; sys.path[:0]=['src/robot_safety','src/robot_skills']; ..."
   resolved: .../src/robot_safety/robot_safety/limits.yaml
   is_file: True
   max_force: 40.0
   ```
2. **After `pixi run build`** (`colcon build --symlink-install`), from `/tmp`
   with `install/setup.bash` sourced:
   ```
   package file: .../build/robot_safety/robot_safety/__init__.py
   resource:     .../build/robot_safety/robot_safety/limits.yaml   True
   40.0
   ```
   Mechanism confirmed by inspection: colcon's symlink install writes
   `install/robot_safety/.../robot-safety.egg-link -> build/robot_safety`, and
   `build/robot_safety/robot_safety` is itself a **symlink to the source
   package directory** (`stat` shows the same inode, `20857605`, for
   `src/.../limits.yaml` and `build/.../limits.yaml`). So the YAML is visible
   with no rebuild step after an edit — the trap R12 was avoiding.
3. **A copying (non-symlink) install** honours `package_data` too:
   ```
   $ python setup.py -q build --build-base /tmp/sfpkgtest
   /tmp/sfpkgtest/lib/robot_safety/limits.yaml      <- present
   ```
   This is the case `package_data` actually governs; without it, a future
   `pip install`/wheel of this package would ship the code and silently lose
   the limits.

Locked in against regression by
`test_limits_config.py::test_the_shipped_limits_file_is_packaged_beside_the_code`
and by `test_no_ros_runtime.py`, which loads the defaults in a clean subprocess
and asserts `rclpy`/`ament_index_python`/`rosidl` are absent from `sys.modules`
(R14).

### Full suite

Run after the red-team fixes (the numbers actually seen):

```
$ pixi run build && pixi run test
Summary: 521 tests, 0 errors, 0 failures, 0 skipped

package             tests  skipped  errors  failures  non-lint  vs-base  status
_workspace_tooling    114        0       0         0       111       +0  ok
robot_backends         62        0       0         0        59       +0  ok
robot_brain             3        0       0         0         0       +0  ok
robot_bringup           3        0       0         0         0       +0  ok
robot_description       3        0       0         0         0       +0  ok
robot_mcp              55        0       0         0        52       +0  ok
robot_perception        3        0       0         0         0       +0  ok
robot_safety          169        0       0         0       166     +166  ok
robot_skills          109        0       0         0       106       +0  ok
AUDIT PASSED: every expected package collected tests
All stages passed.
```

## Out-of-owned-path edit — flagged for the manager

**`scripts/tests/test_ratchet.py`, one assertion** (commit `865ba0e`).
`test_the_real_skeleton_packages_are_still_read_as_skeletons` hard-codes the
list of workspace packages that hold no implementation code, and
`robot_safety` was on it. This feature makes that statement false, so the suite
failed for a reason unrelated to the code under test. The edit moves
`robot_safety` from the "is a skeleton" list to the "holds implementation"
assertion beside `robot_skills`/`robot_backends` — the test keeps checking both
directions. Nothing else outside `src/robot_safety/**` was touched;
`scripts/test_baseline.json`, `pixi.toml` and every other package are
unmodified.

`scripts/test_baseline.json` still reads `robot_safety: 0`, which passes (the
ratchet only fails a package that drops *below* its floor; this one is at
`+166`). Ratcheting it to the real count remains the follow-up the manager
already recorded for Sisyphus.

## Known weaknesses / follow-up candidates (NOTES, not blockers)

1. **`Grasp` and `NavigateTo` are invisible to the collision guard.** Only
   `MoveGripper`/`Place` name a Cartesian target, so `target_pose()` returns
   `None` for the rest and the stub guard passes them. A `Grasp` moves through
   space too; resolving an object id (or a named location) to a swept volume is
   the real-geometry feature. Stated in code and covered by an explicit test
   (`test_skills_with_no_cartesian_target_are_not_geometry_this_stub_can_judge`)
   so it is a documented limit rather than a silent hole.
2. **`SafetyEvent` has no wire form.** No `to_dict`/`from_dict`, deliberately:
   putting a safety event on the wire means choosing whether `SkillResult.code`
   grows finer codes, which is a `robot_skills`/D18 decision this issue
   explicitly excludes. The bridge exists as one property
   (`SafetyEvent.failure_code`), so that later feature has exactly one seam to
   widen.
3. **Nothing enforces the transmitted envelope.** `ClampedCall.limits` is a
   contract the backend is *trusted* to honour (R5); no backend reads it yet.
   Worth an issue when `SimBackend` lands — a transmitted cap nobody consumes
   is a promise, not a guarantee.
4. **The default numbers are reasoned, not measured** (documented as such in
   `limits.yaml`). The Real-backend feature should revisit them against actual
   hardware.
5. **`velocities` is a scalar speed per axis**, not a vector or per-joint. That
   matches the skill API's granularity today (nothing below the seam is
   exposed), but a real arm has per-joint limits; expect this to become
   per-joint when the Sim backend has joints to report.
