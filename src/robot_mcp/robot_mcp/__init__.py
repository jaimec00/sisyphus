# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""An MCP tool server over the skill API, so an LLM agent can drive the robot.

The seam (``robot_skills``) already defines the commands, the structured scene
and the result format; this package only carries them over the Model Context
Protocol.  Every tool returns the seam's own dicts -- no prose, no second
serialization -- and the seven skill tools are generated from
:data:`~robot_skills.SKILL_TYPES`, so the catalogue cannot drift from the seam.

Pure Python: importing this package neither needs nor starts a ROS graph.

Run it (stdio transport, Mock backend)::

    python -m robot_mcp

Or drive one in-process, as the tests do::

    from mcp.client import Client
    from robot_backends import MockBackend
    from robot_mcp import build_server

    backend = MockBackend()
    async with Client(build_server(backend)) as client:
        result = await client.call_tool('grasp', {'object_id': 'mug_1'})
        assert result.structured_content['status'] == 'failed'   # not there yet
"""

from robot_mcp.schemas import no_arguments_schema, schema_for_type, skill_schema
from robot_mcp.server import build_server, main, run_stdio, SkillToolRouter
from robot_mcp.tools import OBSERVATION_TOOL, RESET_TOOL, TOOL_NAMES, TOOLS

__all__ = [
    'build_server',
    'main',
    'no_arguments_schema',
    'OBSERVATION_TOOL',
    'RESET_TOOL',
    'run_stdio',
    'schema_for_type',
    'skill_schema',
    'SkillToolRouter',
    'TOOL_NAMES',
    'TOOLS',
]
