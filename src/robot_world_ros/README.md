# robot_world_ros

The ROS 2 **world-state service**: a thin adapter that exposes the
`robot_world` store's current scene to the rest of the graph, and applies
per-object writes back to it (D35).

## What it is

`robot_world` is a pure-Python store with no ROS at runtime (D30). This package
is the small ROS-facing shell around it: it owns one `FileWorldStore` and
answers the read question — *what does the world look like right now?* — with
the store's own canonical JSON, plus three write services that mutate one
object at a time on that same store. It holds no world logic of its own.

## Interface

### Read

| | |
|---|---|
| Service | `/world_query/get_world` |
| Type | `robot_world_ros_interfaces/srv/GetWorld` |
| Request | *(empty)* |
| Response | `string world_json` |

`world_json` is exactly `robot_world.document_text(store.document())` — the
full world document (named locations + objects + start pose +
`start_column_height` + `world_schema_version`) as the byte-identical JSON the
store writes to disk.

### Write

| Service | Type | Request | Response |
|---|---|---|---|
| `/world_query/update_object_pose` | `srv/UpdateObjectPose` | `string object_id`, `geometry_msgs/Pose pose` | `bool success`, `string error` |
| `/world_query/add_object` | `srv/AddObject` | `string object_id`, `string label`, `geometry_msgs/Pose pose`, `bool graspable` | `bool success`, `string error` |
| `/world_query/remove_object` | `srv/RemoveObject` | `string object_id` | `bool success`, `string error` |

`success=true` + `error=''` means the write committed atomically to disk
(and is therefore visible to `get_world`, which snapshots the same store).
`success=false` + a non-empty `error` means the write did **not** durably
commit, in one of two cases, told apart by `error`:

- *Refused* — unknown/duplicate `object_id`, blank `label`/`object_id`, or a
  pose the store rejects (a non-finite float or an all-zero quaternion): the
  scene is left unchanged.
- *Commit failed* — the atomic disk write failed (read-only or full disk):
  the change is held in the store's memory and re-written on the next
  mutation (D23's dirty-flag design), so it is not lost, but `success=false`
  means it is not yet durable.

The handlers catch `(ValueError,
TypeError)` — the two types every store/serialization refusal raises
(`WorldStoreError` and `SerializationError` are both `ValueError` subclasses) —
and never swallow anything else.

### Why a JSON string for the read, typed fields for the writes

The read carries canonical JSON to stay a *thin adapter* rather than a second
representation (D23). A typed read message (`WorldLocation[]` plus
`geometry_msgs/Pose`, an enum for `held_by`, a separate schema field) would
drift from the document schema as it evolves: float64 rounds the store's
floats, `held_by` needs a mapping that can rot, and the message schema forks the
document schema. Serializing the document once — by the store's own writer —
preserves float precision, `held_by` fidelity and the schema stamp by
construction, and returns the seed world *exactly as the store reads it*. The
read path is infallible (a snapshot of in-memory state), so it carries no
status fields.

The writes are the opposite case and correctly typed. A *whole-document* write
would force perception to echo back the human-curated locations map,
`start_location` and `start_column_height`, which it does not observe — a
second representation again, or a merge step in the node. Per-object ops are
naturally atomic (one mutation is one temp-file + `os.replace` commit, D23's
"one batch = one write") and field-shaped, so they carry typed fields and
`geometry_msgs/Pose`. `geometry_msgs/Pose` is byte-structurally identical to
the store's `Pose` (three float64 position plus four float64 quaternion
components), so building the store pose from the message is a lossless copy,
not a re-encoding; the store's own strictness runs anyway in the `Pose` /
`Quaternion` constructors.

`held_by` is deliberately **absent** from every write: "which gripper holds
this object" is robot proprioception owned by the backend (the store's
`set_held_by`), not something perception observes. `AddObject` always registers
`held_by=None`.

### Freshness and the write/read path

Every write handler mutates `self._store` — the **same** `FileWorldStore` the
read handler snapshots — so the node is the **sole writer**, and
read-after-write is fresh *by construction*: there is no separate in-memory
snapshot that can go stale, because read and write share one store instance,
and each store mutation commits to disk atomically in the same call. No file
re-read happens after a write (it would be redundant). The residual
cross-process race (a second process writing the same live file) is the
documented "no cross-process lock" accepted gap in D23, and is a launch-time
concern, not this service's.

## Parameters

| Parameter | Type | Default | Meaning |
|---|---|---|---|
| `world_state_path` | string | `''` | Live-state file. Empty falls back to `$ROBOT_WORLD_STATE`; still empty → **startup failure**. |
| `world_seed_path` | string | `''` | Seed file. Empty falls back to `$ROBOT_WORLD_SEED`; still empty → the seed shipped inside `robot_world`. |

The node never invents a live-state path — a node must be told where the world
lives, or it would silently create a second world. The store is opened **once**
at startup (seeding a missing live file, or refusing a corrupt one loudly —
D23); each request snapshots in-memory state.

## Run

```bash
ros2 run robot_world_ros world_query_node \
  --ros-args -p world_state_path:=/tmp/world.json

ros2 service call /world_query/get_world \
  robot_world_ros_interfaces/srv/GetWorld

ros2 service call /world_query/update_object_pose \
  robot_world_ros_interfaces/srv/UpdateObjectPose \
  "{object_id: mug_1, pose: {position: {x: 0.3, y: 2.0, z: 0.75}, orientation: {w: 1.0}}}"
```

## Testing

`test/` carries the three ament linters plus:

- `test_world_query.py` — checks the read response is byte-identical to
  `document_text(store.document())` and drives the live
  `/world_query/get_world` service headlessly.
- `test_world_write.py` — checks each write persists (survives a fresh store
  re-read from disk), that a refusal leaves the live file byte-identical, that
  a write is atomic (whole document, no `.tmp` litter), and drives the live
  write+read services end to end to prove write-then-query is fresh.
