# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The documented command, over a real pipe.

Every other test drives the server in-process, which proves the handlers but
not that ``python -m robot_mcp`` is a working MCP server: an import error, a
stray ``print`` on stdout, or an entry point that never starts the transport
would all pass those and fail a real client.  This spawns the command an MCP
client config would spawn (README) and speaks JSON-RPC to it over stdio.
"""

import json
import sys

import anyio
from mcp import ClientSession, stdio_client, StdioServerParameters
from mcp_fixtures import clean_environment
import pytest
from robot_backends import MockBackend
from robot_skills import (
    Grasp,
    GripperState,
    NavigateTo,
    Observation,
    SkillResult,
)

pytestmark = pytest.mark.anyio

#: Whole-test budget, wide enough for a cold interpreter start on slow hardware.
#: A server that comes up but never answers must fail the run, not stall it --
#: nothing else here would ever time out on its own.
TRANSPORT_TIMEOUT_SECONDS = 30.0


def server_parameters(backend: str | None = None) -> StdioServerParameters:
    """Return the launch parameters for the server as the README documents it.

    The environment comes from :func:`~mcp_fixtures.clean_environment`: the
    workspace packages on ``PYTHONPATH``, minus the variables a spawned server
    must not inherit from whoever is running the suite (see
    ``INHERITED_ENV_TO_DROP`` -- a stray ``ROBOT_WORLD_STATE`` would point this
    server at the developer's real world file).  ``backend`` appends
    ``--backend <name>``, so the same helper spawns the MuJoCo server too.
    """
    args = ['-m', 'robot_mcp']
    if backend is not None:
        args += ['--backend', backend]
    return StdioServerParameters(
        command=sys.executable, args=args, env=clean_environment())


async def test_a_client_drives_the_spawned_server_over_stdio():
    """Initialize, list tools and run a two-step chore across the wire."""
    reference = MockBackend()

    with anyio.fail_after(TRANSPORT_TIMEOUT_SECONDS):
        async with stdio_client(server_parameters()) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == 'robot_mcp'
                assert initialized.instructions

                listed = await session.list_tools()
                assert 'grasp' in {tool.name for tool in listed.tools}

                moved = await session.call_tool('navigate_to', {'location': 'kitchen'})
                assert moved.structured_content == reference.execute(
                    NavigateTo('kitchen')).to_dict()

                grasped = await session.call_tool('grasp', {'object_id': 'mug_1'})
                assert not grasped.is_error
                assert grasped.structured_content == reference.execute(Grasp('mug_1')).to_dict()
                assert json.loads(grasped.content[0].text) == grasped.structured_content

                # A bad call is an error result, and the process keeps serving.
                refused = await session.call_tool('grasp', {})
                assert refused.is_error
                assert refused.structured_content['error'] == 'SerializationError'

                observed = await session.call_tool('get_observation', {})
                assert observed.structured_content == reference.get_observation().to_dict()


async def test_a_client_drives_the_spawned_mujoco_server_over_stdio():
    """``python -m robot_mcp --backend mujoco`` serves the same schema live (R4.2).

    The heavy parity work is the in-process R2 stream; this proves the *flag*
    end-to-end: a real subprocess, launched the way an MCP client config would
    launch it, drives one minimal stream (navigate_to -> grasp -> observation)
    and answers in the shared wire schema.  Nothing here compares against a
    Mock -- the point is that the sim server speaks the same protocol and schema
    as any other backend.
    """
    with anyio.fail_after(TRANSPORT_TIMEOUT_SECONDS):
        async with stdio_client(server_parameters('mujoco')) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == 'robot_mcp'

                moved = await session.call_tool('navigate_to', {'location': 'table'})
                assert not moved.is_error
                moved_body = moved.structured_content
                assert moved_body['status'] == 'ok'
                assert moved_body['skill'] == {'skill': 'navigate_to', 'location': 'table'}
                # The result parses under the shared seam and re-serialises stably.
                assert SkillResult.from_dict(moved_body).to_dict() == moved_body
                # The text block mirrors the structured content, exactly as the
                # Mock server's does.
                assert json.loads(moved.content[0].text) == moved_body

                grasped = await session.call_tool('grasp', {'object_id': 'book_1'})
                assert not grasped.is_error
                grasp_body = grasped.structured_content
                assert grasp_body['status'] == 'ok'
                held = next(
                    gripper for gripper in grasp_body['observation']['robot']['grippers']
                    if gripper['held_object_id'] == 'book_1')
                assert held['grasped'] is True
                assert held['state'] == GripperState.CLOSED.value

                observed = await session.call_tool('get_observation', {})
                assert not observed.is_error
                observed_body = observed.structured_content
                assert Observation.from_dict(observed_body).to_dict() == observed_body
                book = next(
                    item for item in observed_body['objects']
                    if item['object_id'] == 'book_1')
                assert book['held_by'] == held['side']
