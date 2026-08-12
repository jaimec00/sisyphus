# robot_world

The **world-state store** (D23): the map (named locations) and the object
registry (id, label, pose, graspable, held-by), queried through one small
surface and persisted as **JSON** so the world survives a process restart.

Before this package the world was a Python literal compiled into the Mock
backend — the scene only changed by editing code, and every mutation died with
the process. The store is the seam the MuJoCo swap and perception build on, so
it is pure Python, backend-agnostic, and holds **no** skill semantics: reach
arithmetic, gripper book-keeping and refusal codes stay in the backend.

## Contents
- `document.py` — `WorldDocument` / `WorldObject`: the one JSON schema both
  world files are written in, stamped with its **own** `world_schema_version`
  (independent of the skill API's D18 `SCHEMA_VERSION`). Parsing reuses
  `robot_skills.serialization`, so it is strict in the same way: unknown keys,
  missing keys and wrong types all raise.
- `storage.py` — reading, and **atomic** writing (temp file in the target's own
  directory, then `os.replace`), plus the shipped seed resource.
  `WorldStoreError` is the one exception a bad world file produces.
- `store.py` — `WorldStore` (in-memory) and `FileWorldStore` (live-state file
  seeded from a read-only seed file), sharing one query/mutate surface and a
  `batch()` scope that makes one skill one atomic disk transition.
- `default_world.json` — the shipped seed: the demo apartment
  (`charger`/`kitchen`/`table`/`living_room` + seven objects) that
  `robot_backends.default_world()` loads.

```python
from robot_world import FileWorldStore, WorldStore
from robot_skills import Pose

store = WorldStore()                       # in memory, shipped scene, no file
store.find_object('mug_1').pose            # where the mug is

live = FileWorldStore('/tmp/world.json')   # same scene, persisted
live.update_object_pose('mug_1', Pose.from_xyz(0.3, 2.0, 0.75))
FileWorldStore('/tmp/world.json').find_object('mug_1').pose   # the moved mug
```

## Seed vs. live state
| | seed | live state |
|---|---|---|
| written by | a human, in git | the robot, at runtime |
| lives in | this package (`default_world.json`), or a path you pass | a path **you** pass — never inside the package |
| read when | construction and every `reset()` | construction |
| missing | hard error (broken install / misconfiguration) | created from the seed |
| corrupt | hard error | hard error — never silently repaired |

## Persistence is opt-in
`WorldStore()` and `MockBackend()` touch no file at all, exactly as before this
package existed: the Mock's documented determinism ("the same world plus the
same skills always produces the same observations") depends on it. A persisted
world is asked for explicitly — `MockBackend(store=FileWorldStore(path))`, or
`python -m robot_mcp --world-state PATH`.

## Known gaps (accepted, documented)
- **No `fsync`.** `os.replace` is atomic against a crashed *process*, not
  durable against a power cut with a dirty page cache.
- **No cross-process locking.** Two processes on one live file race on
  `os.replace`: last writer wins (no corruption, a possibly lost update). The
  deployment model is a single robot-side service doing one task at a time
  (D16/D21).
- **The map is read-only.** No skill adds or removes a location yet.
