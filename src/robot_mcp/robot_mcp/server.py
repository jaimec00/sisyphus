# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The MCP server: one backend, exposed as tools over stdio.

An MCP-capable agent drives the robot by calling these tools and reading the
dicts they return -- ``reset`` / ``get_observation`` to perceive, one tool per
skill to act.  Everything the agent sees is the seam's own wire format
(``SkillResult.to_dict`` / ``Observation.to_dict``, version-stamped per D18):
this module adds no vocabulary of its own and reformats nothing into prose.

Two things it does own, because MCP has no opinion on them:

* **what counts as an error.**  A backend *refusal* (grasping thin air) is a
  perfectly normal tool result carrying ``status: "failed"`` and a ``code`` --
  the agent is meant to read it and try something else.  Only a malformed
  *call* -- arguments the skill seam rejects, or a tool that does not exist --
  comes back as ``isError``.  Nothing raises out of the handler, because the
  low-level SDK turns an escaped exception into a protocol error and drops the
  session; one bad tool call must not take the server down.
* **serialization.**  The handler builds skills with
  :func:`~robot_skills.skill_from_dict` and returns
  ``to_dict()`` verbatim, so validation and wire format stay defined in
  exactly one place (invariant 1).

Pure Python: no ROS graph is needed to run or test it (``test_no_ros_runtime``).
"""

import json
from typing import Any, Mapping

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp_types as types
from robot_backends import MockBackend, RobotBackend
from robot_mcp.tools import OBSERVATION_TOOL, RESET_TOOL, TOOL_NAMES, TOOLS
from robot_skills import JsonDict, SerializationError, skill_from_dict, SKILL_KEY, SKILL_TYPES

__all__ = ['build_server', 'main', 'run_stdio', 'SkillToolRouter']

#: Server identity reported to the client during initialization.
SERVER_NAME = 'robot_mcp'
SERVER_VERSION = '0.0.0'

#: Shown to the model by clients that surface server instructions.
INSTRUCTIONS = (
    'Drive a household mobile manipulator. Call get_observation first: it '
    'returns the scene as structured data -- object ids with poses and a '
    '"graspable" flag, the robot pose, each gripper, and the location names '
    'navigate_to accepts. Then call one skill per step and read the returned '
    'observation before deciding the next one. A refused skill comes back as '
    'status "failed" with a code explaining why (for example unknown_object '
    'or out_of_reach); it is not an error, and the scene is unchanged. Skills '
    'are synchronous: when the call returns, the motion is done. There is no '
    'way to cancel one.'
)

#: Error labels for the machine-readable envelope of a failed call.
_UNKNOWN_TOOL = 'UnknownTool'
_INVALID_ARGUMENTS = 'InvalidArguments'


class ToolCallError(Exception):
    """A call this server refuses before the skill seam ever sees it.

    Carries the label reported as ``structuredContent['error']`` so the agent
    can branch on the condition without parsing the message.
    """

    def __init__(self, error: str, message: str) -> None:
        """Store the machine-readable ``error`` label alongside ``message``."""
        super().__init__(message)
        self.error = error


def _result(payload: JsonDict) -> types.CallToolResult:
    """Return a successful tool result carrying ``payload`` unmodified.

    ``structuredContent`` is the dict the skill layer produced, byte for byte.
    The text block is a verbatim ``json.dumps`` of that same dict, for clients
    that ignore structured content -- not a second, divergent rendering.
    """
    return types.CallToolResult(
        content=[types.TextContent(type='text', text=json.dumps(payload))],
        structuredContent=payload,
    )


def _error_result(error: str, message: str) -> types.CallToolResult:
    """Return a tool error: a bad call, never a refused-but-legal skill."""
    return types.CallToolResult(
        content=[types.TextContent(type='text', text=message)],
        structuredContent={'error': error, 'message': message},
        isError=True,
    )


class SkillToolRouter:
    """Dispatch MCP tool calls onto one backend.

    One router owns one backend for the life of the server, so the world an
    agent builds up across calls persists (and ``reset`` is the only way back).
    The SDK runs handlers concurrently and a backend is a stateful, non
    reentrant world model, so every backend call is serialized on one lock:
    two interleaved grasps must not read the same pre-grasp world.
    """

    def __init__(self, backend: RobotBackend) -> None:
        """Wrap ``backend``; it is the only mutable state the server has."""
        self._backend = backend
        self._lock = anyio.Lock()

    async def list_tools(self, context: Any, params: Any) -> types.ListToolsResult:
        """Return the whole catalogue (it is small, so pagination is moot)."""
        return types.ListToolsResult(tools=list(TOOLS))

    async def call_tool(
        self, context: Any, params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        """Run one tool call, reporting every failure as a result, never a raise."""
        try:
            return _result(await self._payload(params.name, params.arguments or {}))
        except SerializationError as exc:
            # The seam's own message: which field, what was wrong, what is allowed.
            return _error_result(type(exc).__name__, str(exc))
        except ToolCallError as exc:
            return _error_result(exc.error, str(exc))
        except Exception as exc:
            # Last line of defence: an unexpected failure is reported to the
            # caller and the session survives to serve the next call.
            return _error_result(
                type(exc).__name__, f'{params.name} failed unexpectedly: {exc}')

    async def _payload(self, name: str, arguments: Mapping[str, Any]) -> JsonDict:
        """Return the dict one tool call produces, or raise for a bad call."""
        if name in (OBSERVATION_TOOL, RESET_TOOL):
            _reject_arguments(name, arguments)
            async with self._lock:
                observation = (
                    self._backend.get_observation() if name == OBSERVATION_TOOL
                    else self._backend.reset())
            return observation.to_dict()
        if name not in SKILL_TYPES:
            known = ', '.join(sorted(TOOL_NAMES))
            raise ToolCallError(_UNKNOWN_TOOL, f'unknown tool {name!r} (tools: {known})')
        if SKILL_KEY in arguments:
            # The tool name selects the skill; accepting the key as well would
            # let a call to one tool run a different skill.
            raise ToolCallError(
                _INVALID_ARGUMENTS,
                f'{name}: {SKILL_KEY!r} is not an argument -- the tool name is the skill')
        skill = skill_from_dict({SKILL_KEY: name, **arguments})
        async with self._lock:
            result = self._backend.execute(skill)
        return result.to_dict()


def _reject_arguments(name: str, arguments: Mapping[str, Any]) -> None:
    """Refuse arguments to a tool whose schema declares it takes none."""
    if arguments:
        raise ToolCallError(
            _INVALID_ARGUMENTS,
            f'{name} takes no arguments, got: {", ".join(sorted(arguments))}')


def build_server(backend: RobotBackend | None = None) -> Server:
    """Return a server exposing ``backend`` (a fresh Mock by default) as tools.

    The backend is injectable so a test can hold the very object the server
    drives, and so a later Sim/Real backend needs no change here (D9).
    """
    router = SkillToolRouter(MockBackend() if backend is None else backend)
    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        on_list_tools=router.list_tools,
        on_call_tool=router.call_tool,
    )


async def run_stdio(backend: RobotBackend | None = None) -> None:
    """Serve one stdio client until it disconnects."""
    server = build_server(backend)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Console-script entry point: run the stdio server."""
    anyio.run(run_stdio)
