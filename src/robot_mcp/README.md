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
- `server.py` — `build_server(backend=None, safety=None)`,
  `default_safety_layer()`, the safety gate on the skill path, and the stdio
  entry point.

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

## The safety gate

Every **skill** tool call passes through `robot_safety.SafetyLayer` before the
backend sees it (D4/D17, invariant 3). The verdict is taken inside the router's
lock, against an observation sampled inside that same lock, so nothing can move
the world between "judged" and "executed". `get_observation` and `reset` do not
go through it — they command no motion.

| verdict | what the agent gets |
|---|---|
| pass-through | the backend's result, byte-identical to no gate at all |
| clamp | the **rewritten** skill runs; `skill` shows what actually ran and the clamp's own words are appended to `reason` (backend note first, `; `-joined) |
| abort | nothing runs; `status: "failed"`, `code: "rejected"`, `reason` is the safety event's own `detail`, and `observation` is the untouched pre-call world |

No field was added to `SkillResult` for this: the payload is still exactly
`SkillResult.to_dict()` at `schema_version: 1`, and `rejected` is the
safety-owned member of `FailureCode` (`SAFETY_EVENT_CODES`), so an agent tells
"the motion was stopped" from "pick a different goal" without string matching.

`build_server(backend=None, safety=None)`: `safety=None` means
`default_safety_layer()` — the *whole* of the shipped `limits.yaml`, envelope
and keep-out geometry, built from one `SafetyLimits` so the two cannot come
from different reads of the file. **Never "no gate"**: a non-`SafetyLayer`
argument is a `TypeError`, and there is no argument, env var or code path that
yields an ungated server. An injected `safety=` is used exactly as given —
nothing is layered on top of a caller's own guard.

**What actually bites today, honestly.** `SafetyState` is built from the
backend's observation and nothing else, because no backend in this repo
publishes telemetry — there is no e-stop line, no measured axis speed and no
jaw-force reading anywhere. So of the layer's six checks, against the Mock:

- **live:** the `extend_column` height clamp (the shipped `[0.0, 1.2]` m
  travel range), the unclassified-skill refusal, and the `keep_out_boxes`
  configured in `limits.yaml` — a `move_gripper` or `place` target inside
  `below_floor` is aborted by the default server, with the region's label in
  the reason. That geometry is still a **stub**: one floor half-space checked
  against the *goal* pose. There is no robot model, no mesh and no swept
  volume here, so it stops a hallucinated target, not a collision;
- **wired and reachable, but silent until a backend measures something:**
  e-stop, per-axis velocity caps, gripper over-force. They are not fiction —
  the layer checks them on every call — but with no reading available they
  cannot fire, and pretending otherwise would be the worst kind of safety
  theatre.

(`SafetyLayer()` constructed on its own still defaults to
`NullCollisionGuard` — the honest default for a package with no robot model.
Making the configured regions live is *this* server's decision, taken in
`default_safety_layer()`, because this is the seam the LLM is on the other
side of.)

## Run it

From a clean checkout, with no colcon build:

```bash
cd <repo> && PYTHONPATH=<repo>/src/robot_skills:<repo>/src/robot_backends:<repo>/src/robot_safety:<repo>/src/robot_mcp \
  pixi run --frozen python -m robot_mcp
```

Concretely, for a checkout at `/home/sisyphus/worktrees/main`:

```bash
cd /home/sisyphus/worktrees/main && PYTHONPATH=/home/sisyphus/worktrees/main/src/robot_skills:/home/sisyphus/worktrees/main/src/robot_backends:/home/sisyphus/worktrees/main/src/robot_safety:/home/sisyphus/worktrees/main/src/robot_mcp \
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
        "PYTHONPATH": "/home/sisyphus/worktrees/main/src/robot_skills:/home/sisyphus/worktrees/main/src/robot_backends:/home/sisyphus/worktrees/main/src/robot_safety:/home/sisyphus/worktrees/main/src/robot_mcp"
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
**No cancellation, no `/stop`, no e-stop tool.** The skill interface is
synchronous and instantaneous: when a tool call returns, the motion is over, so
there is no in-flight command to cancel. Giving the agent a cancel tool would
require an async execution model, which is a design decision this package does
not get to make (see the issue's out-of-scope list). The safety layer's e-stop
check runs on every call, but nothing in this repo can engage the line yet — an
e-stop the *agent* could trip would be the wrong shape anyway (D21: enforcement
lives below the tool boundary, never in the LLM's hands).
