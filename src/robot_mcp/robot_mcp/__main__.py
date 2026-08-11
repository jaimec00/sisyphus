# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Run the stdio MCP server with ``python -m robot_mcp`` (no colcon build).

Kept alongside the ``robot_mcp_server`` console script because an MCP client
config points at a command line, and this one works from a plain checkout.
"""

from robot_mcp.server import main

if __name__ == '__main__':
    main()
