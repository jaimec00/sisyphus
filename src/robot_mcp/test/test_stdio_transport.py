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
import os
import sys

import anyio
from mcp import ClientSession, stdio_client, StdioServerParameters
import pytest
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo

pytestmark = pytest.mark.anyio

#: Whole-test budget, wide enough for a cold interpreter start on slow hardware.
#: A server that comes up but never answers must fail the run, not stall it --
#: nothing else here would ever time out on its own.
TRANSPORT_TIMEOUT_SECONDS = 30.0


def server_parameters() -> StdioServerParameters:
    """Return the launch parameters for the server as the README documents it.

    ``PYTHONPATH`` carries the workspace packages, which is exactly what the
    README's one-liner and its MCP client config snippet set.
    """
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(path for path in sys.path if path)
    env.pop('ROS_DOMAIN_ID', None)
    return StdioServerParameters(command=sys.executable, args=['-m', 'robot_mcp'], env=env)


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
