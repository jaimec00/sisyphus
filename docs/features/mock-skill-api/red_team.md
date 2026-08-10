# Red team: mock-skill-api

> Read-only adversarial review of branch `feat/mock-skill-api` against
> `brief.md`, `CLAUDE.md` invariants, and `implementation.md`'s claims.
> Round 1. (Report authored by the read-only `red-team` agent; persisted by the
> worktree manager, which has the write tool.)

## Verdict

**READY TO MERGE — BLOCK list is empty.** All six acceptance criteria are met, all
four "Required tests" categories are present, and the tests are genuinely strong
rather than numerous: the failure-path helper compares a *complete* state
fingerprint, the serialization round trip is a real dict→JSON-text→dict→object
cycle with field-by-field checks, and determinism/no-ROS are verified by
mechanisms that would actually fail if the properties were broken. Scope is
clean: nothing outside `src/robot_skills/`, `src/robot_backends/` and the feature
docs was touched. The implementer's flagged judgment call (§8) and both
build-config workarounds (§14) are **ruled legitimate** — details below.

The NOTEs are follow-ups, not blockers.

## What I verified, and why it holds

These are the claims I tried hardest to break and could not:

- **`assert_refused` is a complete state fingerprint, not a status check.**
  `src/robot_backends/test/mock_backend_fixtures.py:46-73` compares
  `Observation.to_dict()` before and after every refusal *and* compares the
  failed result's own observation to the "before" snapshot. I checked that the
  dict actually covers every piece of mutable backend state: `_base_pose`,
  `_location`, `_column_height`, each gripper's `state`/`held_object_id`, and —
  the non-obvious one — each gripper's `offset`/`orientation`, which are
  recoverable from the reported world-frame gripper pose given the base pose and
  column height that are also in the dict. Plus each object's `pose`/`held_by`.
  Nothing mutable escapes the comparison. This is the strong form of "does not
  corrupt state".
- **Validate-then-mutate is structurally true.** I traced all seven handlers
  (`src/robot_backends/robot_backends/mock_backend.py:198-306`). Every
  refusal (`_SkillRefused`) is raised before the first assignment; `_place`
  resolves the side and checks reach before touching the gripper or the object;
  `_carry_held_objects` (`:193`) only runs on the success path. There is no
  mutate-then-discover path.
- **No aliasing hole in `get_observation()`.** Every value the backend hands out
  is a frozen dataclass over frozen `Pose`/`Point`/`Quaternion`, rebuilt fresh
  each call (`mock_backend.py:153-173`). `MockWorld.locations` is a
  `MappingProxyType` over a dict copied in `__post_init__`
  (`mock_world.py:122-131`) whose only reference is a dead local — a caller
  cannot reach through `backend.world` either. Correctly tested at
  `test/test_mock_world.py:26-40`.
- **Determinism.** No `time`, no `random`, no `id()`. Every collection that
  reaches an observation is ordered by `sorted()` or by the explicit
  `SIDE_ORDER` (`mock_backend.py:159,170,172`); error-message joins are sorted
  too. `test_two_backends_are_bit_identical_given_the_same_skills` would catch a
  smuggled clock (the two backends execute at different instants).
- **`reset()` truly restores the seed.** `reset()` rebinds *all five* pieces of
  state (`mock_backend.py:134-151`); `test_reset_restores_the_seed_world`
  asserts dict-exact restoration after a full scenario plus an extra skill, and
  first asserts the pre-reset snapshot *differs*, so it cannot pass vacuously.
- **Extensibility seams are open.** A safety wrapper can implement
  `RobotBackend`, inspect a frozen `Skill`, build a clamped replacement, or
  return `SkillResult.failure(..., FailureCode.REJECTED, ...)` using
  `get_observation()` — all three are available on the interface with nothing
  Mock-specific. `_StubBackend` in `test/test_backend_interface.py:37-53`
  demonstrates a second backend needs nothing beyond the three methods. A ROS 2
  action layer has a lossless `to_dict`/`from_dict` on every type and
  `SKILL_TYPES` as the goal-name registry.
- **No-ROS purity.** `test_no_ros_runtime.py:45-61` is a real clean-subprocess
  test asserting on a computed stdout value (`2.05`), not a smoke test.
- **The lint stubs are not vacuous.** `ament_copyright` excludes `setup.py`
  beside a `package.xml` (verified in
  `.pixi/envs/default/lib/python3.12/site-packages/ament_copyright/main.py:52-56`),
  and colcon runs pytest with `cwd = <package source dir>`
  (`colcon_core/task/python/test/pytest.py:153-154`), so `argv=[]` lints the
  right tree.

## Findings

### NOTE 1 — Ruling on §8: the Mock *refusing* out-of-reach / out-of-range is correct, but the decision needs a home in `decisions.md`

`src/robot_backends/robot_backends/mock_world.py:64-69`,
`mock_backend.py:272-282`, `mock_backend.py:347-359`

**Ruling: legitimate. Keep it.** This does not pre-empt or conflict with
CLAUDE.md invariant 3. The invariant is about not *bypassing* the safety layer;
a backend that additionally refuses commands its hardware physically cannot
execute bypasses nothing. Three properties make it clean: the Mock **never
clamps** (so it can never silently alter a command the safety layer vetted), the
skill *types* do zero range checking (`test_skills.py:113-116` pins this, so the
safety layer still sees the raw `ExtendColumn(99.0)`), and `FailureCode.REJECTED`
is reserved and unused. Removing these checks would be worse: a mock that grasps
a mug from across the apartment makes the harness lie, and the brief's own
composition test would stop proving that navigating was necessary.

**Residual risk:** `RobotModel`'s limits (0–1.2 m column travel, 0.85 m reach)
are the same numbers `robot_safety` will need. Two independent copies can
silently diverge, and a future reader may conclude "the backend already checks
limits" and skip the safety layer.

**Fix direction (follow-up, outside this branch):** add a line to
`docs/design/decisions.md` recording *where* limits live — backend refuses
physically impossible, safety clamps policy-illegal, both may fire — and, when
`robot_safety` lands, have it read its envelope from one source of truth rather
than re-hardcoding it.

### NOTE 2 — the "no rclpy" source scan misses the exact case its docstring claims to catch

`src/robot_backends/test/test_no_ros_runtime.py:64-76`

The docstring says "Neither package may reach for rclpy, **even lazily inside a
function**", which is the scan's whole reason to exist (the subprocess test
already covers import-time). But the scan only greps the literal string
`'import rclpy'`. A lazy `from rclpy.node import Node` inside a function body
contains `from rclpy.node import` — **not** `import rclpy` — and slips through
undetected, as would `importlib.import_module('rclpy')`.

**Failure scenario:** someone adds a lazy `from rclpy.qos import ...` inside a
future `SimBackend` helper; both tests stay green; the package silently acquires
a ROS dependency on a code path the mock loop happens not to hit.

**Fix:** match on `rclpy` as a module token (e.g. a regex for
`^\s*(import|from)\s+rclpy\b` per line, plus `import_module\(\s*['"]rclpy`), or
walk the AST for `Import`/`ImportFrom` nodes — the packages are small enough that
an `ast` walk is a few lines and is exact.

### NOTE 3 — `Observation` documents a cross-field invariant it does not enforce

`src/robot_skills/robot_skills/observation.py:62-66` (docstring: "``held_by`` …
always agrees with the matching `GripperObservation.held_object_id`") vs.
`observation.py:278-298` (`Observation.__post_init__`, which validates types,
duplicate ids and one-gripper-per-side but never the held-object agreement).

`MockBackend` keeps the two views in sync and `implementation.md` §11 says the
redundancy is deliberate — but the type is the *contract* every later backend
binds to, and the contract does not hold itself to it.

**Failure scenario:** `SimBackend` reads gripper state from MuJoCo joints and
object attachment from a separate weld constraint. A dropped weld yields
`gripper.held_object_id == 'mug_1'` while `find_object('mug_1').held_by is None`.
Brain code branching on `is_held` and brain code branching on `is_holding`
disagree; `Observation` constructs happily, round-trips happily, and nothing
fails until a plan does.

**Fix:** in `Observation.__post_init__`, assert the two projections agree in both
directions (every `gripper.held_object_id` names an existing object whose
`held_by` is that gripper's side, and every object with `held_by == s` is named
by gripper `s`), and add a test for both violation directions.

### NOTE 4 — implicit-side resolution for `Grasp` ignores reachability

`src/robot_backends/robot_backends/mock_backend.py:240` and `:361-379`

`_resolve_free_side(None)` returns the first *free* gripper in `SIDE_ORDER`, then
`_require_reachable` is applied to that already-committed side. With
`shoulder_offset_y = 0.18`, the two shoulders are 0.36 m apart against a 0.85 m
reach, so an object can be reachable by one arm and not the other.

**Failure scenario:** a mug 0.90 m from the left shoulder and 0.62 m from the
right. `Grasp('mug_1')` (no side) fails `out_of_reach` even though the robot can
plainly do it; the brain must parse the reason ("…from the left shoulder…") and
retry with an explicit side. That is exactly the prose-parsing the `FailureCode`
design exists to avoid.

**Fix:** prefer the first side in `SIDE_ORDER` that is both free *and* reachable;
fall back to the current `out_of_reach`/`gripper_occupied` messages when no side
qualifies. Still fully deterministic. Add a test with an asymmetrically placed
object.

### NOTE 5 — a test asserts bit-exact float equality that holds only by luck

`src/robot_backends/test/test_mock_skills.py:67`
(`assert result.observation.robot.gripper(Side.LEFT).pose == target`),
mechanism at `mock_backend.py:349` (`offset = target.position - shoulder`) and
`:322-328` (`position = shoulder + offset`).

The mock stores an arm *offset* and reconstructs the world pose, so the reported
pose is `(t − s) + s`, which equals `t` only when Sterbenz's lemma applies
(`t/2 ≤ s ≤ 2t`). The chosen target `(2.2, 0.25, 1.0)` against shoulder
`(2.0, 0.18, 0.8)` satisfies it on all three axes — coincidentally.

**Failure scenario:** change the target to `Pose.from_xyz(2.0, 0.9, 0.1)`
(shoulder z = 0.8): `0.1 − 0.8 = −0.7000000000000001`, `+0.8 =
0.09999999999999987 ≠ 0.1`. The test fails though nothing is broken — and, worse,
the passing test today advertises an exactness guarantee the implementation does
not offer to downstream code doing `if obs.gripper.pose == commanded_pose`.

**Fix:** compare positions with `pytest.approx`, and add a second case with
badly-scaled coordinates so the test documents the real (approximate) contract.
The offset-from-shoulder design itself is right — it is what makes the arm ride
the base — so do not change the implementation.

### NOTE 6 — strict unknown-key rejection makes the wire format non-forward-compatible

`src/robot_skills/robot_skills/serialization.py:100-117`

Strictness on *input from an LLM* is right (a garbled tool call should be loud).
But the same `check_keys` governs machine-to-machine traffic between components
that will version independently.

**Failure scenario:** the ROS 2 action feature adds `stamp` or `frame_id` (both
explicitly anticipated in `implementation.md`). Every consumer built against
today's parser raises `SerializationError: unknown key(s): stamp` — a producer
adding an additive field breaks all older readers. Same for the safety layer
adding a provenance field to `SkillResult`.

**Fix direction:** decide and document the policy now — e.g. keep strictness on
`Skill` (brain input) but allow a reserved, ignored `extensions` sub-object on
`Observation`/`SkillResult`, or state explicitly that any field addition is a
coordinated breaking change. A sentence in the module docstring is enough.

### NOTE 7 — build-config workaround #1 (`extras_require`) is legitimate; no action

`src/robot_skills/setup.py:20`, `src/robot_backends/setup.py:20`

Verified, not taken on trust: colcon maps `extras_require['test']` → test
dependencies at
`.pixi/envs/default/lib/python3.12/site-packages/colcon_core/package_augmentation/python.py:85-92`,
and `has_test_dependency(setup_py_data, 'pytest')` at
`colcon_core/task/python/test/__init__.py:215-231` is what selects the pytest
step over the `python -m unittest` fallback. `ament_python` routes through the
same `PythonTestTask` (`colcon_ros/task/ament_python/test.py:26-29`). This is the
supported mechanism, it masks nothing, and the diagnosis in `implementation.md`
§14 is accurate. Worth propagating to the other five skeleton packages when
someone owns them.

### NOTE 8 — build-config workaround #2 (`pytest.ini`) is legitimate, but colcon can go green on zero tests

`src/robot_skills/pytest.ini`, `src/robot_backends/pytest.ini`

The workaround itself is fine: `-p no:launch_testing -p no:launch_ros` only
*blocks plugin loading*; it cannot hide or skip a test, neither package has
launch tests, the file documents its own removal condition, and it is inside the
owned paths.

The real hazard is adjacent and worth recording: colcon treats pytest exit code 5
(`NO_TESTS_COLLECTED`) as **success** —
`colcon_core/task/python/test/pytest.py:175-178` returns the exit code only when
it is neither `NO_TESTS` nor `TESTS_FAILED`. So if a future `addopts`/`testpaths`
edit, plugin change or collection error ever stops these two packages from
collecting, `colcon test` reports green with zero tests and nobody notices. (This
is also why the five empty skeleton packages fail loudly today — they take the
`unittest` fallback, which does *not* swallow exit 5.)

**Fix direction:** (a) lift the two identical files to one workspace-level pytest
config once Sisyphus owns the environment fix, so the workaround is removed in
one place; (b) have the test-runner agent assert a **non-zero** test count from
`colcon test-result --verbose`, not merely "0 failures".

### NOTE 9 — brief ambiguity: "close-drop with an empty gripper"

`brief.md:51-53` vs. `mock_backend.py:299-306` and `implementation.md` §10

The brief's failure list says "place/**close-drop** with an empty gripper"; the
implementer read this as *drop-like* actions and made `CloseGripper` on an empty
gripper a legal no-op (`status=ok` with an informational reason), failing only
`Place`. I think that reading is right — AC4 says "open/close **toggles** the
gripper" and names **open** (not close) as the dropper, and the Required-tests
list names only `place-empty`. So AC5 is satisfied as written.

Flagging it anyway so the brief author can confirm: if the intent really was
"`close_gripper` on an empty gripper must fail", it is a one-line change in
`_close_gripper` plus one test — cheap now, awkward once a brain depends on
close-then-grasp.

### NOTE 10 — `pixi run test` (the canonical command in CLAUDE.md) is still red workspace-wide

`pixi.toml:26`; cause is the five empty skeleton packages, outside owned paths.

Not a regression — it was red before this branch, and this branch makes two of
seven packages green. Correctly diagnosed and correctly *not* fixed here
(`status.md:23-27` records it). Recording it as a merge-readiness item for
Sisyphus, since `colcon test && colcon test-result` short-circuits and the
results summary never prints, which makes "green against current main" hard to
demonstrate with the documented command.

### NOTE 11 — small stuff

- `mock_backend.py:256-260`: the `held_id is None` branch in `_place` is
  unreachable (`_resolve_holding_side` guarantees a load) — correctly commented,
  but it is untestable code; consider an `assert`.
- `mock_backend.py:353`: the `'nowhere'` branch is dead in the Mock
  (`self._location` is never `None`), so the `out_of_reach` reason has an
  untested formatting path.
- `mock_backend.py:134-150`: `reset()` is where every instance attribute is first
  bound; `__init__` only calls it. Harmless and DRY, but static analysers and
  readers benefit from declaring them in `__init__`.
- `test/mock_backend_fixtures.py:51`: `reason_contains: str | Any = None`
  collapses to `Any`; intent is `str | None`.
- `pixi.lock` is untracked and not in `.gitignore`. Outside owned paths — a
  decision for Sisyphus (pixi lockfiles are normally committed), not for this
  branch.

## Test adequacy assessment (explicit)

**Adequate — no BLOCK.** Checked specifically for the failure modes the rubric
calls out:

- *Would any test pass on gutted code?* I looked for this and did not find a
  meaningful case. `assert_refused` fails if any mutation leaks;
  `test_a_held_object_travels_with_the_robot` fails if `_carry_held_objects` is
  removed; every result assertion checks `result.observation` against a *post*
  mutation state, so returning a stale observation fails;
  `test_every_documented_skill_is_registered` compares the registry by exact dict
  equality, so a missing or extra skill fails; `test_reset_restores_the_seed_world`
  first asserts the state *changed*, so it cannot pass vacuously.
- *Is the round trip real?* Yes — `skill_api_fixtures.py:45-63` does
  `to_dict` → JSON-safety walk (rejects any `Enum`/dataclass/tuple leak) →
  `from_dict` equality → `json.dumps`/`loads` → equality → rebuild → equality →
  `to_dict()` stability → `to_json`/`from_json`. Backed by an explicit
  field-by-field check (`test_observation.py:121-129`) and a nested
  enum + embedded-skill check (`test_skill_result.py:88-107`). Enums and nested
  objects are covered on both legs.
- *Failure paths asserting unmutated state?* Yes, by deep comparison of a
  complete state fingerprint, on all 14 failure cases plus a seven-refusal run
  (`test_mock_failures.py:159-175`), plus a custom-world case proving the rules
  come from data, not hardcoded names (`:178-197`).
- *Trivially-true assertions?* Only `isinstance(observation.objects, tuple)` at
  `test_observation.py:52-53`, which is redundant next to the
  `FrozenInstanceError` checks above it. Not worth changing.
- *Coverage of the brief's Required tests:* per-skill round trip ✓ (12 tests, both
  arms, implicit side, carrying), four failure paths ✓, serialization ✓,
  composition scenario ✓ (plus intermediate states, per-step JSON, reset
  exactness, and cross-backend determinism).

The one real test weakness is NOTE 2 (the rclpy scan's regex hole), and the one
brittle assertion is NOTE 5. Neither undermines an acceptance criterion.

## Manager disposition (worktree manager, round 1)

No BLOCKs → no mandatory fix round. Manager elected **one voluntary fix round**
for the cheap, high-value items that are in-scope and touch test strength or the
public contract: NOTE 2, 3, 4, 5, 6 (docstring policy only), and the NOTE 11
typing nit. Deferred to GitHub issues / Sisyphus: NOTE 1 (needs a
`decisions.md` entry — outside owned paths), NOTE 8a (workspace-level pytest
config), NOTE 9 (brief-author confirmation), NOTE 10 (workspace suite red for
empty skeleton packages), NOTE 11 `pixi.lock` (Sisyphus's call).

> **Round 2 (delta-only) is in [red_team_round2.md](red_team_round2.md)** — 1 BLOCK (a test that cannot fail), 6 NOTEs; both manager-requested rulings resolved in the implementation's favour.
