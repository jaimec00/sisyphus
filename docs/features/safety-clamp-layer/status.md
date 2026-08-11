# Status — `safety-clamp-layer` (issue #43)

- **Phase:** red-team round 2 done (0 BLOCK) → final polish → test-runner
- **Branch:** `feat/i43-robot-safety-dynamic-clamp-abort-safety`, based on `origin/main` @ `9236fef`
- **Blockers:** none
- **Escalations:** none

## Log
- Synced worktree, read issue #43 (body present, acceptance criteria clear).
- Provisioning probe: **PyYAML 6.0.3 already installed** in the pixi env
  (`.pixi/envs/default/.../yaml/__init__.py`), execute-verified via
  `pixi run python -c "import yaml; yaml.safe_load('a: 1')"`. **No new
  third-party dependency** → no `pixi add`, no `pixi.toml` edit.
- context-explorer → `context.md` (8 open questions, Q7/Q8 newly surfaced).
- Manager independently verified the three load-bearing claims before ruling:
  `test_no_ros_runtime.py`'s subprocess probe covers only
  `robot_backends`/`robot_skills` (**not** `robot_safety`);
  `check_test_integrity.py:339-367`'s docstring names "a clamp function in
  `robot_safety`, say" as exactly what flips the no-real-tests rule;
  `robot_mcp/README.md:109-114`'s "no in-flight command to cancel" is verbatim.
- Rulings R1–R14 recorded below → dispatch implementer.
- **Implementer round 0 complete.** 5 modules + `limits.yaml`, 7 test modules.
  `pixi run build && pixi run test`: 490 tests, 0 failures, AUDIT PASSED.
  R12 packaging verified empirically three ways (source tree, symlink-install
  via egg-link with matching inode, copying install for a future wheel).
- Manager approved two implementer-flagged decisions:
  - `scripts/tests/test_ratchet.py` edit (commit `865ba0e`, **outside owned
    paths**): the workspace test hard-coded `robot_safety` as a skeleton, which
    this feature makes false. Verified the edit *strengthens* the assertion —
    `robot_safety` moves from the "must be empty" list to an explicit "holds
    implementation" assertion beside `robot_skills`/`robot_backends`; both
    directions still checked. Necessary, minimal, isolated in its own commit.
  - `# noqa: A003` on `SafetyLayer.filter` (`layer.py:165`): `filter` shadows a
    builtin, but the name is the brief's published contract (deliverable 1) and
    R1/R2. One commented line silenced rather than renaming the seam around a
    linter.
- **Red-team round 1** → `red_team.md`: **1 BLOCK, 6 NOTES**. Seven of the eight
  attack hypotheses I set held under concrete attack — notably no NaN fail-open
  (every route closed at construction via `as_finite_float` + non-negativity),
  consistent inclusive boundaries, deterministic R10 ordering, strict R13 YAML
  parsing, and `Grasp(side=None)` genuinely checking both sides.
  - **B1 (BLOCK)** — three `isinstance` dispatch sites default *permissive*
    (`layer.py:104`, `layer.py:261`, `collision.py:42`), and `EVERY_SKILL` is a
    hard-coded 7-tuple, so a skill added upstream would flow through unclamped
    with all 138 tests still green. Manager verified independently and concurs:
    the package already applies the opposite discipline to its own `MotionAxis`
    vocabulary (`limits.py:169-172` — an uncapped axis is a load error), so the
    skill vocabulary deserves the same. Sent back to the implementer.
  - Manager dispositions: fix **B1 + N3 + N4 + N5** (each a cheap hardening of a
    seam I ruled on — R7, R9, R13). **N1, N2, N6 → follow-up comment on the
    issue**, not implemented: N1 (`gripper.abort_force`) and N2
    (`require_readings`) add safety semantics beyond the brief; N6 (a raising
    guard propagates) is fail-closed today and the alternative forces a
    fail-open/fail-closed choice better made deliberately later.
  - Red-team independently concurred the `test_ratchet.py` edit does not weaken
    that test, and judged test adequacy "above the repo's bar".
- **Implementer fix round 1.** B1 fixed with a new `policy.py`: `SKILL_POLICIES`
  enumerates the vocabulary once, keyed by wire name (the key `SKILL_TYPES`
  uses), and an unclassified skill is **refused at runtime**
  (`SafetyEventKind.UNCLASSIFIED_SKILL`, checked second — after e-stop, before
  the guard) *and* trips a dev-time test. The implementer took the runtime-error
  option I offered and justified it: a test-only tripwire protects this repo's
  maintainers but not a downstream workspace that adds a skill without running
  our suite. Structured refusal on the return path is this layer's contract.
  N3/N4/N5 also fixed. 521 tests, 0 failures.
- **Red-team round 2** → `red_team_round2.md` (new file; round 1's evidence kept
  intact). **0 BLOCK, 3 NOTES.** Confirmed the three rewritten dispatch sites
  preserve behaviour exactly (no inversion, no widening), `Grasp(side=None)`
  still checks both sides, `OpenGripper` still un-gated (R11), N4/N5 regress no
  construction path, and the ~31 new tests are load-bearing.
  - Manager disposition: take all three notes as a **final polish pass**, no
    third red-team round (2-round cap respected; these are hardenings, not
    design). N1 — assert the *converse* flag→field implication, closing B1's
    hole as reached by mis-classification rather than omission, with `side`
    explicitly exempt per R11. N2 — `policy_for` verifies the skill is the shape
    its name promises, turning an exotic `AttributeError` into a refusal.
    N3 — document the raise/return boundary where it is actually read (on
    `filter`, in the README, and on the `CollisionGuard` protocol).

---

# Manager rulings (binding, not assumed correct)

A downstream agent that believes a ruling is **wrong** must escalate to me
in-process — neither silently deviate nor comply into a bug.

## R1 — `state` is a new `robot_safety`-local `SafetyState`; no shared-schema edit (Q1)

`filter(skill, state)` takes a `SafetyState` **local to `robot_safety`** that
*composes* the shared `Observation` rather than extending it:

```python
@dataclass(frozen=True)
class SafetyState:
    observation: Observation                     # shared schema, read-only
    estop_engaged: bool = False
    velocities: Mapping[MotionAxis, float] = ...  # measured, m/s, non-negative
    gripper_forces: Mapping[Side, float] = ...    # measured, newtons, non-negative
```

**No D18 escalation.** The brief says escalate only if a shared field is
*genuinely* needed; composition works. The deeper reason it belongs here and not
in `Observation`: `Observation` is the **brain-facing** perception type (D3 /
invariant 4) and the brain never plans on joint velocity or jaw force —
telemetry is a safety-layer input, so putting it in `Observation` would widen
the brain's contract for no consumer.

`MotionAxis` is a local enum (`BASE`, `COLUMN`, `ARM`) — its `.value` strings are
also the YAML config keys, so config and telemetry share one vocabulary and a
typo cannot silently create an uncapped axis. Reuse `robot_skills.Side` (public
export) for the gripper mapping; do **not** invent a second side enum.

## R2 — `filter` is a **re-entrant per-state-sample gate**; this resolves the "in-flight" tension (Q3)

The repo has no in-flight execution model — `RobotBackend.execute()` is
synchronous and total, and `robot_mcp/README.md:109-114` states as policy "there
is no in-flight command to cancel". D17 nevertheless says this layer "clamps or
aborts in-flight". **Ruling:** build the pre-execution gate the brief's
deliverable 1 literally specifies, and make it *correct to call repeatedly*
against successive `SafetyState` samples. A future async backend gets in-flight
abort by sampling telemetry and re-calling `filter` — no redesign.

Concretely this means `filter` must be **pure and stateless**: no memory between
calls, no mutation of the layer, the same `(skill, state)` always yields the same
verdict. `SafetyLayer` holds only config + the collision guard.

This is **not** a design fork and does not escalate: it is the brief's own API
shape, and the extensibility story is recorded here on purpose.

## R3 — the joint-limit clamp is `ExtendColumn.height`, and **only** that (Q2)

Clamp `ExtendColumn.height` into `[column.min_height, column.max_height]`. The
codebase deliberately reserves exactly this for us — `validation.py:12-14` ("they
deliberately do not encode robot limits... clamping is the safety layer's job")
and `test_skills.py:112-115` (`test_extend_column_does_not_clamp`).

## R4 — poses are **never** clamped (Q2)

`MoveGripper.pose` and `Place.pose` pass through **untouched**. Two reasons:

1. **D17 boundary.** "target pose out of range... unreachable" is assigned by
   name to the *backend's* up-front refusal. A Cartesian envelope clamp is
   indistinguishable from reachability at this layer.
2. **Clamping a pose is semantically unsafe.** Clamping is only sound for a
   *scalar, monotone* quantity where "less of it" is strictly safer (height).
   A 6-DoF goal has no such ordering: silently relocating a `Place` puts the mug
   down 20 cm from where the brain intended, which is **worse** than refusing.

Pose-based safety belongs to the collision guard (R7), which **aborts** rather
than rewrites. That split — clamp scalars, abort geometry — is the rule.

## R5 — velocity is enforced **two different ways**, deliberately (Q3)

- **Measured** velocity in `SafetyState.velocities` over its cap → **abort**
  (`SafetyEvent`, kind `VELOCITY_EXCEEDED`). This is the "unsafe to continue"
  check and the thing that makes R2's re-entrant model meaningful.
- **Commanded** caps ride out on `ClampedCall.limits` as an envelope the backend
  is contractually required to honor. Not checked here — transmitted.

**Check every axis present in `state.velocities`, not just the ones "relevant" to
the skill.** If the base is moving too fast, commanding an arm motion is still
unsafe; the whole machine is in an unsafe dynamic state. A skill→axis map would
be both more code and less safe.

## R6 — `ClampedCall` carries the skill, the envelope, and the clamp record (Q4)

```python
@dataclass(frozen=True)
class ClampedCall:
    skill: Skill                        # possibly rewritten
    limits: MotionLimits                # envelope the backend must honor
    clamps: tuple[SafetyEvent, ...] = ()  # what was rewritten, empty if nothing
    @property
    def was_clamped(self) -> bool: ...
```

**"Pass through unchanged" is defined as identity**, not equality: for an
in-limit call, `result.skill is skill` must hold and `result.clamps == ()`. Test
it with `is`. Identity makes "unchanged" unambiguous and catches a needless
rebuild that `==` would wave through.

## R7 — collision guard is an injected `Protocol` with a **working** stub (Q5)

A `typing.Protocol` (not an ABC — no inheritance requirement on implementors)
injected into `SafetyLayer.__init__`, defaulting to a permissive null guard:

```python
class CollisionGuard(Protocol):
    def check(self, skill: Skill, state: SafetyState) -> SafetyEvent | None: ...
```

Returning `None` means "clear". Ship **two** implementations: `NullCollisionGuard`
(always clear, the default) and a `KeepOutBoxGuard` configured from YAML with a
trivial axis-aligned box that actually aborts a `MoveGripper`/`Place` whose target
pose falls inside it. **The stub must genuinely work and be tested** — a hook
whose only implementation returns `None` is a dead parameter, not an extension
point. Real geometry stays out of scope (non-goal); the *seam* is what ships.

Note this does not contradict R4: the guard **aborts**, it does not rewrite.

## R8 — `SafetyEvent.kind` is a **local** enum; no `robot_skills` edit (Q6)

`SafetyEventKind` lives in `robot_safety`: `ESTOP_ENGAGED`, `COLUMN_LIMIT`,
`VELOCITY_EXCEEDED`, `GRIPPER_OVERFORCE`, `COLLISION_RISK`. Adding members to
`FailureCode` would edit `robot_skills` (outside owned paths) and drag in
`test_failure_codes.py`'s exhaustive partition.

Consume the shared schema read-only via **one documented mapping**:
`SafetyEvent.failure_code -> FailureCode` returning `FailureCode.REJECTED` (the
sole current member of `SAFETY_EVENT_CODES`). That gives the later integration
issue exactly one seam to widen, and keeps this package's vocabulary its own.
**No escalation needed.**

## R9 — one `SafetyEvent` type serves both abort and clamp-record (Q6, brief deliverable 3)

The brief lists one type with four fields: *kind, offending value, limit, clamped
value*. A pure abort has no clamped value; a clamp does. So:

```python
@dataclass(frozen=True)
class SafetyEvent:
    kind: SafetyEventKind
    detail: str                      # human-readable specifics
    offending_value: float | None = None
    limit: float | None = None
    clamped_value: float | None = None
    side: Side | None = None         # which gripper, when applicable
    axis: MotionAxis | None = None   # which axis, when applicable
    @property
    def is_clamp(self) -> bool:      # clamped_value is not None
```

Returned **directly** from `filter` ⇒ abort. Appearing in `ClampedCall.clamps`
⇒ a record of a rewrite that still executes. This matches the brief's field list
literally instead of inventing a second type.

## R10 — check order is fixed, aborts before clamps

1. **e-stop** (`SafetyEventKind.ESTOP_ENGAGED`) — short-circuits *everything*
2. collision guard
3. measured velocity
4. gripper over-force
5. column-height clamp (rewrite)
6. attach `MotionLimits` → return `ClampedCall`

E-stop is checked first so an e-stopped over-limit call reports `ESTOP_ENGAGED`,
not a clamp. All aborts precede all clamps: never spend work rewriting a call
that is about to be refused.

## R11 — over-force applies **only to jaw-closing skills**, and never blocks `OpenGripper`

Check `state.gripper_forces` against `gripper.max_force` for **`CloseGripper` and
`Grasp` only** — the two skills that close jaws (D19: "over-force *while
closing*"). For `Grasp(side=None)` the backend picks the side, so check **every**
side and abort if any exceeds (conservative — we cannot know which it will pick).

**`OpenGripper` must never be blocked by over-force, and neither must
`NavigateTo`.** Opening is the *remedy* for an over-force condition; gating it on
force would make an over-force state unrecoverable — the robot could never let go.
This is a safety property, not a convenience, and it needs an explicit test.

## R12 — YAML ships **inside the importable package**, loaded via `importlib.resources` (Q7)

`src/robot_safety/robot_safety/limits.yaml`, loaded with
`importlib.resources.files('robot_safety') / 'limits.yaml'`.

Rejected: `ament_index_python.get_package_share_directory`. It would be the
repo's first use of ROS runtime machinery in a pure-data package, breaks the
sibling packages' explicit "no ROS graph needed to import" policy, and adds a
"must `colcon build` before tests see the file" trap. A file inside the
symlink-installed package directory is visible immediately, from source tree and
installed build alike.

Add `package_data={'robot_safety': ['*.yaml']}` + `include_package_data=True` to
`setup.py`. **The implementer must empirically verify this** — run `pixi run
build` and load the config both from the source tree and from
`build/robot_safety`/`install/`. Do not assume; this is the one packaging risk in
the feature.

## R13 — the shipped YAML is the **single source of the defaults**

`SafetyLimits.defaults()` loads the shipped `limits.yaml`. Do **not** duplicate
the numbers as Python constants — two sources drift, and the drift would be
silent and safety-relevant. A test must assert the shipped file parses and
populates every field.

Parsing is **strict**: reject unknown keys, missing keys, non-finite values,
negative caps, and `min_height >= max_height`, with a clear error naming the key.
Use `yaml.safe_load` (never `yaml.load`). Write `robot_safety`'s own small
validation — do **not** reach into `robot_skills`' non-exported serialization
helpers (`check_keys`, `get_float` are not in its `__all__`); read-only
consumption means the *public* surface.

Documented starting defaults (household mobile manipulator; refine if you have a
better basis, but document the rationale in the YAML comments):

| key | value | rationale |
|---|---|---|
| `column.min_height` / `max_height` | `0.0` / `1.2` m | column stowed → counter/shelf reach |
| `velocity.base` | `0.6` m/s | well under human walking (~1.4 m/s) indoors |
| `velocity.column` | `0.15` m/s | slow lift; pinch risk |
| `velocity.arm` | `0.5` m/s | typical collaborative-arm cap |
| `gripper.max_force` | `40.0` N | holds a full mug, below hand-injury threshold |

## R14 — declare the dependencies now (Q8)

In `src/robot_safety/package.xml` (owned path): add `<depend>robot_skills</depend>`
(matching `robot_backends/package.xml:11`) and `<exec_depend>python3-yaml</exec_depend>`.
Leave the pre-existing unused `<depend>rclpy</depend>` alone. PyYAML resolving
today only transitively via `ros-jazzy-desktop` is not a reason to leave it
undeclared.

Add a small `robot_safety`-local no-ROS-runtime test (subprocess import probe,
asserting no `rclpy`/`ament_index_python` in `sys.modules`) — cheap, in owned
paths, and it locks in R12 against a future regression.

## Out of scope / not ruled
- `scripts/test_baseline.json` (`robot_safety: 0`) is **outside owned paths** —
  do not edit it. Baseline `0` never fails the ratchet, so the suite goes green
  regardless. Ratcheting it to the real count is a **follow-up for the issue
  comment**, for Sisyphus to file.
- Integration into the brain loop, real collision geometry, backend reachability
  refusal — non-goals per the brief.
