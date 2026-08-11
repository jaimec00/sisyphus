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
from typing import Any, AsyncIterator

from mcp.client import Client
import mcp_types as types
from robot_backends import RobotBackend
from robot_mcp import build_server


@asynccontextmanager
async def connected(backend: RobotBackend) -> AsyncIterator[Client]:
    """Yield an MCP client connected in-process to a server driving ``backend``.

    ``Client(server)`` is the SDK's own in-process transport: a real client
    session (initialize, list_tools, call_tool) with no subprocess to wait on.
    ``test_stdio_transport.py`` covers the wire itself.
    """
    async with Client(build_server(backend)) as client:
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
