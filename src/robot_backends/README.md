# robot_backends

Backend abstraction: **Mock | Sim (MuJoCo) | Real** behind one interface
(decision D9). The brain never learns which backend it is driving.

## Contents
- `interface.py` — `RobotBackend`: `reset()`, `get_observation()`,
  `execute(skill) -> SkillResult`. Small and *total*: a legal skill never
  raises, it returns a success or an attributable failure.
- `mock_world.py` — `MockWorld`, `ObjectSpec`, `RobotModel`, `default_world()`:
  immutable seed data (named locations, objects with poses, a crude reach and
  column-travel model). No clock, no RNG.
- `mock_backend.py` — `MockBackend`: an in-memory world model that runs the
  whole perceive → act → observe loop with no simulator and no hardware.

```python
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo, Place, Pose

backend = MockBackend()                       # default demo apartment
backend.execute(NavigateTo('kitchen'))
backend.execute(Grasp('mug_1'))
backend.execute(NavigateTo('table'))
result = backend.execute(Place(Pose.from_xyz(0.35, 2.05, 0.75)))
assert result.succeeded
```

Failures (`unknown_location`, `unknown_object`, `not_graspable`,
`gripper_occupied`, `gripper_empty`, `out_of_reach`, `out_of_range`, ...) are
validated **before** any mutation, so a refused skill leaves the world byte
identical — the tests assert exactly that.

Pass your own `MockWorld` to test a different scene; `reset()` always returns
to that seed world. Deterministic: the same world plus the same skills always
produces the same observations.

Status: Mock only. `SimBackend` (MuJoCo) and `RealBackend` are later features
and must satisfy this same interface. See
`docs/features/mock-skill-api/implementation.md`.
