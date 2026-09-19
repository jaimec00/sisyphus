# robot_world_ros

The ROS 2 **world-state query service**: a thin adapter that exposes the
`robot_world` store's current scene to the rest of the graph (D35).

## What it is

`robot_world` is a pure-Python store with no ROS at runtime (D30). This package
is the small ROS-facing shell around it: it owns one `FileWorldStore` and
answers one question — *what does the world look like right now?* — with the
store's own canonical JSON. It holds no world logic of its own.

## Interface

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

### Why a JSON string rather than typed message fields

Carrying the canonical JSON is what makes the service a *thin adapter* rather
than a second representation (D23). Typed fields (`WorldLocation[]` plus
`geometry_msgs/Pose`, an enum for `held_by`, a separate schema field) would
drift from the document schema as it evolves: float64 rounds the store's
floats, `held_by` needs a mapping that can rot, and the message schema forks the
document schema. Serializing the document once — by the store's own writer —
preserves float precision, `held_by` fidelity and the schema stamp by
construction, and returns the seed world *exactly as the store reads it*.

The read path is infallible (a snapshot of in-memory state), so there is no
success/error field. The write service lands in a later PR and will carry its
own status fields.

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
```

## Testing

`test/` carries the three ament linters plus `test_world_query.py`, which
checks the response is byte-identical to `document_text(store.document())` and
drives the live `/world_query/get_world` service headlessly.
