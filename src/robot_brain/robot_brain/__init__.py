# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The brain: an OpenClaw agent's operating prompt and its config (D21).

The brain of this robot is *not* a planner loop in this package.  D21 settled
that it is a dedicated OpenClaw Telegram agent whose native tool-call loop is
the perceive -> act -> re-perceive loop (D4), driving the skills exposed by
``robot_mcp``.  What is left for this package to own is what makes that agent
*this robot's* brain: the prompt it operates from, and the configuration that
points it at the tool server.

Both ship as files, both are loaded through :mod:`robot_brain.agent`, and both
are held to the live skill API by this package's tests -- a renamed tool, a new
skill argument or a retuned safety limit fails a test here rather than
confusing an agent in the kitchen.

Pure Python: importing this package neither needs nor starts a ROS graph.

    from robot_brain import operating_prompt
    print(operating_prompt())
"""

from robot_brain.agent import (
    AGENT_ID,
    config_fragment,
    CONFIG_RESOURCE,
    MCP_SERVER_NAME,
    operating_prompt,
    PROMPT_RESOURCE,
)

__all__ = [
    'AGENT_ID',
    'config_fragment',
    'CONFIG_RESOURCE',
    'MCP_SERVER_NAME',
    'operating_prompt',
    'PROMPT_RESOURCE',
]
