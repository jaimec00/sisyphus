# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Helpers for driving a server through a real MCP client session.

A plain module rather than ``conftest.py`` so test modules can import it
directly, matching ``robot_backends/test/mock_backend_fixtures.py``.
"""

from contextlib import asynccontextmanager
import json
import os
import sys
from typing import Any, AsyncIterator

from mcp.client import Client
import mcp_types as types
from robot_backends import RobotBackend
from robot_mcp import build_server, WORLD_SEED_ENV, WORLD_STATE_ENV
from robot_safety import SafetyLayer

#: Environment variables a spawned server must never inherit from the
#: developer's shell.  ``ROS_DOMAIN_ID`` would join a live ROS graph;
#: ``ROBOT_WORLD_STATE``/``ROBOT_WORLD_SEED`` are read by ``python -m
#: robot_mcp`` (D23) and would point the server under test at the developer's
#: *real* world file -- it would resume that world instead of starting from the
#: shipped scene, fail on the second run, and write production state from a
#: test.  Anything new the server reads from the environment belongs here too.
INHERITED_ENV_TO_DROP = ('ROS_DOMAIN_ID', WORLD_STATE_ENV, WORLD_SEED_ENV)


def clean_environment() -> dict[str, str]:
    """Return the environment a spawned server is launched with.

    Carries the workspace packages on ``PYTHONPATH`` -- exactly what the
    README's one-liner and its MCP client config set -- and drops everything in
    :data:`INHERITED_ENV_TO_DROP`.
    """
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(path for path in sys.path if path)
    for name in INHERITED_ENV_TO_DROP:
        env.pop(name, None)
    return env


@asynccontextmanager
async def connected(
    backend: RobotBackend, safety: SafetyLayer | None = None,
) -> AsyncIterator[Client]:
    """Yield an MCP client connected in-process to a server driving ``backend``.

    ``Client(server)`` is the SDK's own in-process transport: a real client
    session (initialize, list_tools, call_tool) with no subprocess to wait on.
    ``test_stdio_transport.py`` covers the wire itself.

    ``safety`` is passed straight through to :func:`~robot_mcp.build_server`,
    so a test that leaves it out drives the *default* gate -- the one a
    deployment gets -- rather than a permissive test-only one.
    """
    async with Client(build_server(backend, safety)) as client:
        yield client


def payload(result: types.CallToolResult) -> Any:
    """Return a tool result's structured payload, checking the text block agrees.

    Every assertion in the suite goes through here, so "the text block is a
    verbatim copy of the structured content" is checked on every single call
    rather than once in a dedicated test.
    """
    assert len(result.content) == 1, result.content
    block = result.content[0]
    assert isinstance(block, types.TextContent), block
    if result.is_error:
        # An error's text block is the human-readable message, and the
        # structured envelope repeats it verbatim.
        assert block.text == result.structured_content['message']
    else:
        assert json.loads(block.text) == result.structured_content
    return result.structured_content
