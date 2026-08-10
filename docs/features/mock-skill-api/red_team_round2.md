# Red team round 2: mock-skill-api (delta-only)

> Focused review of the round-1 fix commits `3bdb6c7` / `722f8fc`. Round 1
> (`red_team.md`) reviewed the whole feature and stands; this pass reviews only
> the delta and its blast radius. Authored by the read-only `red-team` agent;
> persisted by the worktree manager.

## Verdict

**Delta is sound; one BLOCK, on a test — not on the implementation.** All six
changes do what the implementer claims, and I could not break any of them.
NOTE 3's invariant is correctly two-directional, fires on `from_dict` as well as
construction, and cannot be tripped by any legal `MockBackend` sequence. NOTE 4
preserves the explicit-side path exactly and preserves left-preference *in the
code*. Nothing round 1 certified was touched: `assert_refused`, `snapshot`,
`run`, `reset()`, the round-trip helper, the aliasing guarantees and the
determinism tests are byte-for-byte unchanged apart from the `reason_contains`
annotation. No file outside `src/robot_skills/`, `src/robot_backends/` and
`docs/features/mock-skill-api/` was modified.

The one BLOCK is that the new test guarding left-preference cannot fail if
left-preference is replaced by nearest-arm selection.

## BLOCK

### BLOCK 1 — `test_implicit_side_still_prefers_the_left_arm_when_both_can_reach` cannot detect the regression it exists to prevent

`src/robot_backends/test/test_mock_skills.py:142-146`

The test grasps `bowl_1`, which sits at `(2.25, 0.00, 0.92)`. From `kitchen`
`(2.0, 0, 0)` with `column_height=0.3`, the shoulders are `(2.0, ±0.18, 0.8)`, so
the offsets are `(0.25, ∓0.18, 0.12)` — **bit-identical norms** (`0.1093` both
sides). The object is exactly on the sagittal plane.

**Failure scenario:** someone reads NOTE 4's fix and takes the "obvious next
step", replacing the `SIDE_ORDER` loop in `_resolve_grasping_side`
(`mock_backend.py:389-392`) with
`min(reachable, key=lambda s: self._reach_offset(s, target).norm())`. That
directly contradicts the method's own docstring (`mock_backend.py:379`: *"order
is fixed, no distance tie-breaks"*) and implementation.md §9, and it silently
changes which arm the robot uses. Yet:

- this test still passes (distances tie; `min` is stable and returns left);
- `test_grasp_without_a_side_fills_the_left_gripper_then_the_right`
  (`:103-111`) still passes — `mug_1` is nearer the left shoulder (0.326 m vs
  0.422 m) anyway;
- `test_grasp_without_a_side_prefers_a_gripper_that_can_actually_reach` (`:114`)
  still passes — only one arm can reach;
- the determinism tests still pass — nearest-arm is deterministic too.

So **no test in the suite distinguishes "first in `SIDE_ORDER` that is
reachable" from "nearest reachable arm"**, which is precisely the property the
fix round claims to have pinned. The test's docstring ("Preference order is
unchanged where reach does not decide it") asserts something the body cannot
verify.

**Fix direction:** use an object both arms can reach where the **right** arm is
strictly nearer, so left-preference is the only thing that explains the outcome.
`plate_1` at `(2.30, -0.10, 0.90)` works out of the existing default world (left
0.422 m, right 0.326 m, both under the 0.85 m reach) — an implicit
`Grasp('plate_1')` must still land in the **left** gripper. Add an explicit
assertion that the right arm could also have reached (e.g. that
`Grasp('plate_1', Side.RIGHT)` succeeds from a fresh backend), so the test also
fails loudly if a future reach-model change turns it into a single-reachable-arm
case by accident.

## Rulings requested by the manager

**Is NOTE 3's invariant too strong? No — keep it.** `Observation`'s module
docstring (`observation.py:10-13`) defines the type as *"an immutable snapshot of
the whole world as the robot currently believes it … every known object"*, not
"every currently visible object". A held object is by definition believed-in, and
whatever set `held_object_id` necessarily knows the id and can report the object
at the gripper pose. The visibility-filtered-perception scenario resolves
cleanly: the perception→observation assembler must synthesize/retain the in-hand
object, which is the correct behaviour anyway (an occluded in-hand mug still
exists). The obligation is now stated on the type itself
(`observation.py:274-276`), so the perception feature will bind to a written
contract rather than an accident. The alternative — a silently self-contradicting
scene — is the hazard round 1 flagged and is strictly worse.

I verified it cannot fire on any legal `MockBackend` sequence: `reset()`
(`mock_backend.py:134-151`) starts consistent; `_grasp` (`:243-248`), `_place`
(`:264-269`) and `_open_gripper` (`:287-292`) each update both projections before
returning; `_carry_held_objects` (`:340-345`) touches only poses;
navigate/extend/move/close never touch holding state; refusals return the
pre-state unchanged. `ObjectSpec` has no `held_by` field, so no custom
`MockWorld` can seed an inconsistent scene. Ordering inside `__post_init__` is
also right — the duplicate-id check (`:292-296`) runs *before*
`_check_held_objects_agree`, so the `by_id` dict can't silently mask a collision.

**Did NOTE 4 change previously-tested behavior? No.** Explicit-side is unchanged,
including check precedence: `_require_free_gripper` before `_require_reachable`
(`mock_backend.py:381-383`) matches the old `_resolve_free_side` →
`_require_reachable` order, so `GRIPPER_OCCUPIED` still wins over
`OUT_OF_REACH` (pinned by `test_mock_failures.py:61-73`). The
no-free-arm-can-reach fallback (`:394`) reports `free[0]`'s distance — the same
side and the same message the old code produced, which is why
`test_grasp_out_of_reach` (`test_mock_failures.py:117-125`) still asserts on
`'charger'` unchanged. Determinism survives: no distance is ever compared to
another distance. (Left-preference is genuinely preserved in the code — see
BLOCK 1 for why it is not genuinely *tested*.)

**Is the detector honest? Substantially yes.** `import rclpy as r` is caught
(`alias.name` is `'rclpy'`); the lookalike-module and prose cases genuinely don't
fire; the relative-import comment at `:125` is correct. The self-test asserts on
**contents**, not just cardinality (`:146-149`), and `:156`
(`SAMPLE_WITH_LAZY_IMPORTS.count('import rclpy') == 1`) is a real demonstration
that the old grep found 1 of 3 — I confirmed the count is exactly 1. Residual
blind spots are NOTEs A–C below.

**Is `assert_pose_close`'s tolerance sane? Yes.** `abs=1e-9` with no `rel` means
pytest uses the absolute tolerance only (it does not fall back to the 1e-6
relative default), so this is a genuinely tight bound at metre scale. Orientation
stays exact, which is correct — `gripper.orientation` is stored verbatim
(`mock_backend.py:217, 265`). The `badly_scaled` parametrization is a real guard:
`0.1 - 0.8 + 0.8 == 0.09999999999999987`, and the target is reachable
(0.700 m < 0.85 m), so the case actually runs.

## NOTE

### NOTE A — the source scan is non-recursive, so a future subpackage is invisible to it

`src/robot_backends/test/test_no_ros_runtime.py:167`

`for entry in sorted(os.listdir(root))` walks only the top-level package
directory. Round 1's failure scenario was "a lazy `from rclpy.qos import ...`
inside a future `SimBackend` helper" — if that helper lands as
`robot_backends/robot_backends/sim/kinematics.py` rather than a flat module, it
is never scanned and the hole reopens in a form the new AST detector would
otherwise have caught. **Fix:** `os.walk(root)` instead of `os.listdir`, skipping
`__pycache__`.

### NOTE B — `scanned >= 10` has a margin of one and doesn't express its intent

`src/robot_backends/test/test_no_ros_runtime.py:175`

The real count is exactly 11 (7 in `robot_skills`, 4 in `robot_backends`). It
does guard against scanning nothing — the stated goal — but any consolidation of
two modules makes it fail spuriously, and its message ("expected to scan both
packages") claims something a single global count can't check. **Fix:** count per
package and assert each is ≥ 1, or assert a known filename set was visited.

### NOTE C — two documented-but-unmentioned detector gaps

`src/robot_backends/test/test_no_ros_runtime.py:128-138`

`import_module(name='rclpy')` (keyword form) is skipped by `not node.args` at
`:130`, and a non-literal argument (`import_module(MODULE_NAME)`) is undetectable
in principle. The docstring is honest about "a literal string argument" but not
about the kwarg form. **Cheap fix** for the first: also inspect `node.keywords`
for `name`/`module`. The second is inherent — worth one sentence in the docstring
so a reader doesn't over-trust the scan.

### NOTE D — `assert_pose_close` was applied to three comparisons that are exact by construction

`src/robot_backends/test/test_mock_skills.py:173`, `:189`, and
`src/robot_backends/test/test_mock_scenario.py:61`

`_place` assigns `item.pose = skill.pose` verbatim (`mock_backend.py:268`), so a
*placed object's* pose is bit-identical to the commanded pose — no shoulder round
trip is involved. Using the 1e-9 tolerance there loses a discriminating
assertion: a refactor to `item.pose = self._gripper_pose(side)` would land within
1e-9 and go unnoticed. The *gripper* comparisons at `:79` and `:176` are the ones
that genuinely need the tolerance, and those are correct. Implementation.md's own
rule ("comparisons where both sides come from the same reconstruction stay
exact") points the same way. **Fix:** revert `:173`, `:189` and
`test_mock_scenario.py:61` to `==`, keep `assert_pose_close` for gripper poses.

### NOTE E — implicit-side `Place` is now inconsistent with implicit-side `Grasp`

`src/robot_backends/robot_backends/mock_backend.py:414-429` vs `:366-394`

`_resolve_holding_side(None)` still returns the first holding side in
`SIDE_ORDER` and only then checks reach (`_place`, `:262`). With both hands full
and only the right arm able to reach the target, `Place(target)` fails
`OUT_OF_REACH` naming the left arm — verbatim the asymmetry NOTE 4 fixed for
`Grasp`.

I rule this **acceptable as-is**, and better than the symmetric fix: with two
loads, letting geometry decide *which object gets put down* would be a surprising
silent choice, and unlike `Grasp` the brain can pick the side without parsing
prose (`held_objects()` / `gripper(side).held_object_id` tell it directly). But
the asymmetry is now a real, undocumented property of the API. **Fix direction:**
one sentence in `_resolve_holding_side`'s docstring and in implementation.md §9
explaining why `Place` deliberately does not mirror `Grasp`.

### NOTE F — cross-field violations escape `from_dict` as bare `ValueError`, not `SerializationError`

`src/robot_skills/robot_skills/observation.py:322`, `:326`, `:335`; contract at
`serialization.py:74` (`class SerializationError(ValueError)`) and `:17-20`

Every other parse failure in the module raises `SerializationError`, and the
strictness policy (now written down for NOTE 6) says a malformed dict must be "a
loud, attributable failure". A caller wrapping `Observation.from_dict` in
`except SerializationError` to turn a bad LLM/transport payload into a clean
refusal will **not** catch a held-object disagreement — it propagates as an
unhandled `ValueError`. `test_the_held_object_invariant_survives_a_round_trip`
(`test_observation.py:136`) codifies this by matching on `ValueError`.

This is pre-existing (duplicate object ids and one-gripper-per-side behave the
same way), so it is not a delta regression — but the delta adds a third instance
and is the natural moment to settle it. **Fix direction:** either have
`from_dict` translate constructor `ValueError`s into `SerializationError` at the
parse boundary, or document explicitly that `from_dict` raises `ValueError` (of
which `SerializationError` is one kind) and have callers catch `ValueError`.

## Test adequacy of the delta (explicit)

- **NOTE 3 tests: strong.** Both directions, five sub-cases, distinct `match=`
  strings that pin the *specific* violation rather than "some ValueError", plus a
  doctored-dict parse case proving enforcement survives `from_dict`. The
  `plate_1`/`mug_1` sub-case (`test_observation.py:106-118`) exercises the second
  loop while the first loop passes.
- **NOTE 2 tests: strong on the detector, adequate on the scan.** The self-test
  asserts contents, both polarities, and demonstrates the old grep's inadequacy
  numerically. Scan weaknesses are NOTEs A and B.
- **NOTE 4 tests: three of four are strong.** I verified the geometry of
  `_asymmetric_backend`: `cube_1` is 0.62 m from the right shoulder and 0.98 m
  from the left, so those are real discriminating cases. The fourth is BLOCK 1.
- **NOTE 5 tests: strong.** The `badly_scaled` case genuinely fails under `==`
  and genuinely passes the reach check, so the parametrization is not decorative.
  See NOTE D for the over-application.
- **NOTE 6 / NOTE 11: no behaviour change, nothing to test.** `serialization.py`'s
  diff is docstring-only (`:22-44`); `mock_backend_fixtures.py:64` is
  annotation-only.

## Manager disposition (worktree manager, round 2)

BLOCK 1 dispatched to the implementer as a mandatory fix (round 2 of the cap of
2), together with NOTEs A, B, C, D, E and F — all cheap, all in owned paths, and
A/D in particular restore discriminating power to tests rather than merely
tidying them. No further red-team round after this: the cap is reached, and the
test-runner is the final gate.
