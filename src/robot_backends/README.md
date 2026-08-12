# robot_backends

Backend abstraction: **Mock | Sim (MuJoCo) | Real** behind one interface
(decision D9). The brain never learns which backend it is driving.

## Contents
- `interface.py` — `RobotBackend`: `reset()`, `get_observation()`,
  `execute(skill) -> SkillResult`. Small and *total*: a legal skill never
  raises, it returns a success or an attributable failure.
- `mock_world.py` — `MockWorld`, `ObjectSpec`, `RobotModel`, `default_world()`:
  immutable seed data (named locations, objects with poses, a crude reach and
  column-travel model). No clock, no RNG. Since D23 the *scene* half comes
  from `robot_world`'s shipped seed **file**; `world_from_document()` /
  `world_to_document()` convert between the two.
- `mock_backend.py` — `MockBackend`: the world model that runs the whole
  perceive → act → observe loop with no simulator and no hardware. The scene
  lives in a `robot_world.WorldStore`; the backend owns proprioception and the
  reach arithmetic.

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
identical — the tests assert exactly that. Every one of them is a *backend
refusal* in D17's sense ("can't be done"); the Mock never emits a safety event,
which is the clamp layer's job. Closing on thin air or opening an already-open
gripper are **successes** that report themselves via `grasped` in the returned
observation (D19), not failures.

Pass your own `MockWorld` to test a different scene; `reset()` always returns
to that seed world. Deterministic: the same world plus the same skills always
produces the same observations — `MockBackend()` with no arguments opens **no
file at all** (D23), so that promise is unconditional.

Persistence is opt-in. Hand it a file-backed store and the world outlives the
process:

```python
from robot_backends import MockBackend
from robot_world import FileWorldStore

backend = MockBackend(store=FileWorldStore('/var/lib/robot/world.json'))
```

Then the **store is the scene** (locations, objects, start parameters) and the
`MockWorld` argument, if given, supplies only the `RobotModel` — a world file
describes the room, never the robot's body. Coming up against an existing live
file is a power cycle: the robot homes with empty grippers, so anything the
file still records as held is released where it lies. `reset()` re-reads the
**seed file** and rewrites the live state from it. One skill is one atomic disk
write.

Status: Mock only. `SimBackend` (MuJoCo) and `RealBackend` are later features
and must satisfy this same interface. See
`docs/features/mock-skill-api/implementation.md`.
