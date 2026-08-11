# robot_safety

The **dynamic-safety layer** (D4, mandatory from day one): it sits between
brain-issued skills and the backend and **clamps or aborts** them, returning a
structured safety event rather than raising (D17).

Split by *kind* of limit, not by component:

- *"can't be done"* — unreachable pose, unknown object — is a **backend
  refusal**, decided below this layer;
- *"unsafe to continue"* — column out of travel, an axis running too fast, jaws
  squeezing too hard, the e-stop line — is a **safety event**, decided here.

Backend-agnostic: Mock, Sim and Real get the same clamps. Pure Python —
importing this package needs no ROS graph and no ROS packages.

## Contents
- `state.py` — `SafetyState`, `MotionAxis`: one telemetry sample (observation +
  e-stop line + measured axis speeds + measured jaw forces). It *composes* the
  shared `Observation` rather than extending it: the brain never plans on jaw
  force, so telemetry stays a safety-layer input.
- `events.py` — `SafetyEvent`, `SafetyEventKind`: what the layer says, as data.
  Local vocabulary; `SafetyEvent.failure_code` is the single documented bridge
  onto the shared `FailureCode`.
- `limits.py` + `limits.yaml` — the configured envelope, strictly parsed. **The
  YAML is the single source of the defaults**; no metre, m/s or newton is
  hard-coded in Python.
- `policy.py` — `SKILL_POLICIES`: which checks apply to which skill, enumerated
  once. Not an `isinstance` chain, because those default to *permissive*.
- `collision.py` — `CollisionGuard` protocol plus `NullCollisionGuard` (the
  default) and `KeepOutBoxGuard` (working stub geometry).
- `layer.py` — `SafetyLayer.filter(skill, state) -> ClampedCall | SafetyEvent`.

```python
from robot_backends import MockBackend
from robot_safety import SafetyEvent, SafetyLayer, SafetyState
from robot_skills import ExtendColumn

backend = MockBackend()
layer = SafetyLayer()                                    # limits from limits.yaml
state = SafetyState(observation=backend.get_observation())

verdict = layer.filter(ExtendColumn(9.0), state)
if isinstance(verdict, SafetyEvent):
    ...                                                  # aborted: do not execute
else:
    backend.execute(verdict.skill)                       # clamped to the column stop
```

Checks run in a fixed order, **aborts before clamps**: e-stop → unclassified
skill → collision → measured velocity → gripper over-force → column clamp. An
in-limit call passes through *identically* (`verdict.skill is skill`), so
"unchanged" is checkable, not merely believed.

**A skill this layer has no policy for is refused**, not waved through, and
`unclassified_skills()` turns the same gap into a test failure — so adding a
skill to the shared seam costs one deliberate line in `policy.py` instead of
silently arriving unclamped and unchecked.

`filter` is **pure and stateless**: the same `(skill, state)` always yields the
same verdict. Today's backends are synchronous — `execute()` returns when the
motion is over, so there is nothing in flight to abort — and the layer is called
once, before execution. When an asynchronous backend arrives, "abort in flight"
is the same function re-asked against successive telemetry samples.

Two things are deliberately **not** here: poses are never clamped (an
out-of-range pose is reachability, which is the backend's refusal, and a 6-DoF
goal has no safe direction to be nudged in — geometry is *aborted* by the
collision guard instead), and `OpenGripper` is never blocked by over-force,
because opening is the remedy for it.

Status: clamp/abort gate complete against Mock. Real collision geometry, backend
reachability refusal and wiring into the brain loop are separate features. See
`docs/design/decisions.md` (D4, D17, D19).
