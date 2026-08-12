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

Three things it does own, because MCP has no opinion on them:

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
* **the safety gate.**  This is the brain-facing seam (D21), so every skill
  tool call goes through :class:`~robot_safety.SafetyLayer` *before* the
  backend sees it (D4/invariant 3).  The gate is injectable so a test or a
  deployment can tighten it, and there is deliberately no way -- argument, env
  var or code path -- to obtain a server without one.

Where the world lives is a *deployment* choice, so it is a command-line one:
``--world-state PATH`` (or ``$ROBOT_WORLD_STATE``) puts the scene in a JSON
file that survives restarts, ``--world-seed PATH`` overrides the scene
``reset`` restores.  With neither, the world is in memory and dies with the
process -- the pre-D23 behaviour, kept as the default on purpose so that
running the documented command never writes to disk behind anyone's back.

Pure Python: no ROS graph is needed to run or test it (``test_no_ros_runtime``).
"""

import argparse
from dataclasses import replace
import json
import os
from typing import Any, Mapping, Sequence

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp_types as types
from robot_backends import MockBackend, RobotBackend
from robot_mcp.tools import OBSERVATION_TOOL, RESET_TOOL, TOOL_NAMES, TOOLS
from robot_safety import (
    KeepOutBoxGuard,
    NullCollisionGuard,
    SafetyEvent,
    SafetyLayer,
    SafetyLimits,
    SafetyState,
)
from robot_skills import (
    JsonDict,
    SerializationError,
    Skill,
    skill_from_dict,
    SKILL_KEY,
    SKILL_TYPES,
    SkillResult,
)
from robot_world import FileWorldStore

__all__ = [
    'backend_from_options',
    'build_server',
    'default_safety_layer',
    'main',
    'parse_args',
    'run_stdio',
    'SkillToolRouter',
    'WORLD_SEED_ENV',
    'WORLD_STATE_ENV',
]

#: Environment variables the world-state flags fall back to.
WORLD_STATE_ENV = 'ROBOT_WORLD_STATE'
WORLD_SEED_ENV = 'ROBOT_WORLD_SEED'

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
    'way to cancel one. A safety layer below these tools may clamp a command '
    '(it still runs -- the returned "skill" is what actually ran and "reason" '
    'says what was changed) or refuse it outright with the code "rejected", in '
    'which case nothing moved.'
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


def default_safety_layer(limits: SafetyLimits | None = None) -> SafetyLayer:
    """Return the gate a server with no injected ``safety`` runs behind.

    The whole of ``limits.yaml``, enforced: the envelope *and* the keep-out
    geometry configured beside it.  ``SafetyLayer()`` on its own defaults to
    :class:`~robot_safety.NullCollisionGuard`, which is the honest default for
    a package with no robot model -- but it would leave the shipped
    ``keep_out_boxes`` declared and not working at the one seam an LLM is on
    the other side of, and the file says in its own words that the entry exists
    "so the seam ships working, not merely declared".  So this server builds
    the guard from the same limit set it gives the layer: **one**
    :class:`~robot_safety.SafetyLimits`, so the envelope and the geometry can
    never come from two different reads of the file.

    A limits file with no regions is a configuration choice, not a broken
    server, so that one case is answered *before* asking -- with the same
    ``NullCollisionGuard`` ``KeepOutBoxGuard.from_limits`` would have refused
    to build.  **Nothing is caught here**: a malformed limits file raises out
    of this function rather than quietly becoming a permissive server, and it
    stays that way whatever ``robot_safety`` learns to validate next.

    Still stub geometry: it judges commanded target poses against axis-aligned
    boxes.  No robot model, no mesh, no swept volume, so a *carried* object or
    a driving base is not checked -- real collision geometry is a later
    feature (invariant 5).
    """
    if limits is None:
        limits = SafetyLimits.defaults()
    if not limits.keep_out_boxes:
        return SafetyLayer(limits=limits, collision_guard=NullCollisionGuard())
    return SafetyLayer(
        limits=limits, collision_guard=KeepOutBoxGuard.from_limits(limits))


class SkillToolRouter:
    """Dispatch MCP tool calls onto one backend, through the safety gate.

    One router owns one backend for the life of the server, so the world an
    agent builds up across calls persists (and ``reset`` is the only way back).
    The SDK runs handlers concurrently and a backend is a stateful, non
    reentrant world model, so every backend call is serialized on one lock:
    two interleaved grasps must not read the same pre-grasp world.  The safety
    verdict is taken inside that same lock, against an observation sampled
    inside it, so no other call can move the world between "judged" and
    "executed".

    The gate is a :class:`~robot_safety.SafetyLayer` and cannot be ``None``:
    the LLM is on the other side of this seam and D21 puts enforcement below
    the tool boundary, never in the prompt.
    """

    def __init__(self, backend: RobotBackend, safety: SafetyLayer | None = None) -> None:
        """Wrap ``backend`` behind ``safety`` (:func:`default_safety_layer` if none).

        An injected layer is used **as given** -- nothing is layered on top of
        a caller's own guard, because a gate that quietly adds checks the
        caller did not ask for is as surprising as one that drops them.

        ``safety`` is type-checked rather than duck-typed: "anything with a
        ``filter``" would make a no-op stand-in an accepted way to build an
        ungated server, which is precisely the thing invariant 3 forbids.
        """
        if safety is None:
            safety = default_safety_layer()
        if not isinstance(safety, SafetyLayer):
            raise TypeError(
                f'safety must be a SafetyLayer, got {type(safety).__name__}')
        self._backend = backend
        self._safety = safety
        self._lock = anyio.Lock()

    @property
    def safety(self) -> SafetyLayer:
        """Return the gate every skill call passes through."""
        return self._safety

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
            result = self._gated_execute(skill)
        return result.to_dict()

    def _gated_execute(self, skill: Skill) -> SkillResult:
        """Run ``skill`` through the safety layer, then (if allowed) the backend.

        Called with the lock held.  Three outcomes, all reported in the seam's
        own wire format so the agent needs no second vocabulary to read them
        (D18): the payload of a skill tool is a ``SkillResult`` dict, whatever
        safety decided.

        * **abort** -- ``filter`` returned an event, so nothing executes and the
          result is a failure carrying the event's own ``detail``, under the
          shared code the event maps onto (``rejected``, the safety half of
          :class:`~robot_skills.FailureCode`).  The observation reported is the
          one the verdict was taken against: nothing ran, so nothing moved, and
          re-reading it would only invite the two to disagree.
        * **clamp** -- the *rewritten* skill executes, so ``result['skill']``
          shows the agent what actually ran rather than what it asked for, and
          each clamp's ``detail`` is appended to the backend's own note.  A
          successful result's ``reason`` is exactly this: an informational
          note, which is where "you asked for 9 m, you got 1.2 m" belongs.
        * **pass-through** -- the backend's result, untouched.  The layer hands
          back the caller's own skill object when it changed nothing, so this
          path is byte-identical to calling the backend directly.
        """
        state = SafetyState(observation=self._backend.get_observation())
        verdict = self._safety.filter(skill, state)
        if isinstance(verdict, SafetyEvent):
            return SkillResult.failure(
                skill, state.observation, verdict.failure_code, verdict.detail)
        result = self._backend.execute(verdict.skill)
        if not verdict.was_clamped:
            return result
        notes = [event.detail for event in verdict.clamps]
        if result.reason:
            notes.insert(0, result.reason)
        # ``replace`` rather than a rebuild: every other field -- status, code,
        # the skill that ran, the fresh observation -- travels across unread,
        # so a field added to SkillResult cannot be silently dropped here.
        return replace(result, reason='; '.join(notes))


def _reject_arguments(name: str, arguments: Mapping[str, Any]) -> None:
    """Refuse arguments to a tool whose schema declares it takes none."""
    if arguments:
        raise ToolCallError(
            _INVALID_ARGUMENTS,
            f'{name} takes no arguments, got: {", ".join(sorted(arguments))}')


def build_server(
    backend: RobotBackend | None = None,
    safety: SafetyLayer | None = None,
) -> Server:
    """Return a server exposing ``backend`` (a fresh Mock by default) as tools.

    The backend is injectable so a test can hold the very object the server
    drives, and so a later Sim/Real backend needs no change here (D9).

    ``safety`` is injectable for the same reason -- a deployment with a real
    robot model wants its own collision guard, and a test wants to drive the
    abort path -- but ``None`` means :func:`default_safety_layer` (the whole of
    the shipped ``limits.yaml``, geometry included), never *no gate*.  There is
    no way to build an ungated server (invariant 3).
    """
    router = SkillToolRouter(MockBackend() if backend is None else backend, safety)
    return Server(
        SERVER_NAME,
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        on_list_tools=router.list_tools,
        on_call_tool=router.call_tool,
    )


async def run_stdio(
    backend: RobotBackend | None = None,
    safety: SafetyLayer | None = None,
) -> None:
    """Serve one stdio client until it disconnects."""
    server = build_server(backend, safety)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the server's command line, falling back to the environment.

    A flag beats its environment variable, which beats "no world file at all".
    ``--world-seed`` on its own is refused: it would silently do nothing.
    """
    parser = argparse.ArgumentParser(
        prog='robot_mcp',
        description='Serve the robot skill API as MCP tools over stdio.',
    )
    parser.add_argument(
        '--world-state',
        metavar='PATH',
        default=os.environ.get(WORLD_STATE_ENV) or None,
        help=(
            'JSON file holding the live world state, created from the seed if '
            f'absent. Without it (or ${WORLD_STATE_ENV}) the world is in '
            'memory and dies with the process.'),
    )
    parser.add_argument(
        '--world-seed',
        metavar='PATH',
        default=os.environ.get(WORLD_SEED_ENV) or None,
        help=(
            'JSON file holding the read-only seed scene that reset() restores '
            f'(or ${WORLD_SEED_ENV}); defaults to the scene shipped with '
            'robot_world. Requires --world-state.'),
    )
    args = parser.parse_args(argv)
    if args.world_seed is not None and args.world_state is None:
        parser.error(
            '--world-seed needs --world-state: with no live-state file there is '
            'nothing for a seed to seed')
    return args


def backend_from_options(
    world_state: str | None = None,
    world_seed: str | None = None,
) -> RobotBackend | None:
    """Return the backend those options ask for, or ``None`` for the default.

    ``None`` means "let :func:`build_server` make its own in-memory Mock" --
    which is exactly today's behaviour, and stays the default deliberately:
    a server that persisted by default would write into whatever directory it
    happened to start in and would resume a previous run's world without
    anyone asking it to (D23).
    """
    if world_state is None:
        return None
    return MockBackend(store=FileWorldStore(world_state, seed_path=world_seed))


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point: run the stdio server."""
    args = parse_args(argv)
    anyio.run(run_stdio, backend_from_options(args.world_state, args.world_seed))
