# robot_mcp

The skill API as **MCP tools**, so an MCP-capable LLM agent can drive the robot
by tool calls alone: perceive with `get_observation`, act with one tool per
skill, read the returned scene, decide the next call.

Pure Python over the official MCP SDK (`mcp >= 2.0`), stdio transport, backed by
`MockBackend` — **no ROS graph is required to run or test it** (enforced by
`test/test_no_ros_runtime.py`), and no simulator or hardware either.

## Contents
- `schemas.py` — JSON Schemas derived from the skill dataclasses. Nothing is
  hand-written per skill; an unmapped field type raises at import time rather
  than shipping a tool whose schema lies.
- `tools.py` — the catalogue: one tool per entry in `SKILL_TYPES`, plus
  `get_observation` and `reset`, which are backend calls, not skills.
- `server.py` — `build_server(backend=None)` and the stdio entry point.

## The tools
| tool | arguments | returns |
|---|---|---|
| `navigate_to` | `location` | `SkillResult.to_dict()` |
| `move_gripper` | `side`, `pose` | `SkillResult.to_dict()` |
| `grasp` | `object_id`, `side?` | `SkillResult.to_dict()` |
| `place` | `pose`, `side?` | `SkillResult.to_dict()` |
| `extend_column` | `height` | `SkillResult.to_dict()` |
| `open_gripper` / `close_gripper` | `side` | `SkillResult.to_dict()` |
| `get_observation` | — | `Observation.to_dict()` |
| `reset` | — | `Observation.to_dict()` |

Arguments are the skill's own wire names (`Skill.to_dict`), and results are the
skill layer's own dicts at `schema_version: 1` — `SkillResult.to_dict()` and
`Observation.to_dict()`, verbatim, in both `structuredContent` and a JSON text
block. This package defines no vocabulary of its own and reformats nothing into
prose: the agent reads `status`, `code`, `grasped`, `held_by` and coordinates
straight out of the result.

A skill the backend **refuses** (`grasp` on an unknown object) is a normal
result carrying `status: "failed"` plus a `code` — not an error. Only a
malformed *call* (arguments the skill seam rejects, or a tool that does not
exist) comes back with `isError`, carrying the seam's own message.

## Run it

From a clean checkout, with no colcon build:

```bash
cd <repo> && PYTHONPATH=<repo>/src/robot_skills:<repo>/src/robot_backends:<repo>/src/robot_mcp \
  pixi run --frozen python -m robot_mcp
```

Concretely, for a checkout at `/home/sisyphus/worktrees/main`:

```bash
cd /home/sisyphus/worktrees/main && PYTHONPATH=/home/sisyphus/worktrees/main/src/robot_skills:/home/sisyphus/worktrees/main/src/robot_backends:/home/sisyphus/worktrees/main/src/robot_mcp \
  pixi run --frozen python -m robot_mcp
```

It speaks MCP on stdin/stdout and logs nothing there, so point a client at it:

```json
{
  "mcpServers": {
    "robot": {
      "command": "pixi",
      "args": [
        "run", "--frozen",
        "--manifest-path", "/home/sisyphus/worktrees/main/pixi.toml",
        "python", "-m", "robot_mcp"
      ],
      "env": {
        "PYTHONPATH": "/home/sisyphus/worktrees/main/src/robot_skills:/home/sisyphus/worktrees/main/src/robot_backends:/home/sisyphus/worktrees/main/src/robot_mcp"
      }
    }
  }
}
```

`--manifest-path` is what makes the config independent of the client's working
directory; drop it if your client launches the command from the repo root.

*Secondary, if you have built the workspace:* `pixi run build` installs the
`robot_mcp_server` console script into `install/robot_mcp/lib/robot_mcp/`, which
ament_python does not put on `PATH` — so a client config would name it by
absolute path (`<repo>/install/robot_mcp/lib/robot_mcp/robot_mcp_server`) after
sourcing `install/setup.bash`. The `python -m` command above needs neither, and
is the one to prefer.

## Drive one in-process

```python
from mcp.client import Client
from robot_backends import MockBackend
from robot_mcp import build_server

backend = MockBackend()
async with Client(build_server(backend)) as client:          # no transport
    await client.call_tool('navigate_to', {'location': 'kitchen'})
    result = await client.call_tool('grasp', {'object_id': 'mug_1'})
    assert result.structured_content['status'] == 'ok'
    assert result.structured_content['observation'] == backend.get_observation().to_dict()
```

`build_server` takes any `RobotBackend`, so the same server will front a Sim or
Real backend later (D9) with no change here; a fresh `MockBackend` is the
default. One server owns one backend for its lifetime, and `reset` is the only
way back to the seed world.

## Deliberately absent
**No cancellation, no `/stop`, no e-stop.** The skill interface is synchronous
and instantaneous: when a tool call returns, the motion is over, so there is no
in-flight command to cancel. Giving the agent a cancel tool would require an
async execution model, which is a design decision this package does not get to
make (see the issue's out-of-scope list). The safety layer, when it lands, sits
below this server on the skill seam — never bypassed by it.
