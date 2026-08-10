# robot_skills

The **skill API**: the typed contract between the LLM brain and any robot
backend. This is the architectural seam — above it the brain reasons in goals
(named locations, object ids, gripper poses); below it a backend does IK,
planning and motion. Nothing here mentions a joint.

Pure Python: importing this package needs no ROS graph and no ROS packages.
(A later feature adds ROS 2 action transport *on top of* these types; the
package's `rclpy` dependency is reserved for that.)

## Contents
- `skills.py` — `Skill` and the seven commands: `NavigateTo`, `MoveGripper`,
  `Grasp`, `Place`, `ExtendColumn`, `OpenGripper`, `CloseGripper`, plus the
  `SKILL_TYPES` wire-name registry and `Side`.
- `observation.py` — structured perception: `Observation`, `RobotState`,
  `GripperObservation` (including the sensed `grasped` flag, D19),
  `SceneObject`, `GripperState`.
- `result.py` — `SkillResult` (status + `FailureCode` + reason + the
  observation taken after the attempt), `SkillStatus`, `FailureCode` and its
  ownership split, `BACKEND_REFUSAL_CODES` / `SAFETY_EVENT_CODES` (D17:
  *"can't be done"* is the backend's refusal, *"unsafe to continue"* is the
  safety layer's clamp/abort).
- `geometry.py` — `Point`, `Quaternion`, `Pose` (shaped like `geometry_msgs`).
- `serialization.py`, `validation.py` — strict JSON-safe round trips and
  constructor-level validation.

Every type is a frozen dataclass and round-trips losslessly through
`to_dict()`/`from_dict()` and `to_json()`/`from_json()`.

## Schema versioning (D18)
`Observation` and `SkillResult` stamp `SCHEMA_VERSION` into their dict form
(skills do not — they are the type an LLM writes by hand). Adding an optional
field is non-breaking; removing, renaming or retyping one bumps the version and
updates every binder in the same PR. `test/golden/v<N>/` holds a frozen
`to_dict()` fixture per type and `test/test_golden_schema.py` fails on any
breaking drift — see the wire-format policy in `serialization.py` and the
regeneration instructions in `test/golden_fixtures.py`.

```python
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo, Side, SkillStatus

backend = MockBackend()
backend.execute(NavigateTo('kitchen'))
result = backend.execute(Grasp('mug_1'))
assert result.status is SkillStatus.OK
assert result.observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
```

Naming: the *skill* `Place` always means "put the held object down"; a named
spot in the semantic map is always a **location**. See
`docs/features/mock-skill-api/implementation.md` and `docs/design/PROJECT.md`.
