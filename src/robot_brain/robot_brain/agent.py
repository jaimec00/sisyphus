# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Loaders for the two files that *are* the brain (D21).

Under D21 the brain is not a program in this repo -- it is an OpenClaw agent
whose behaviour is fixed by its operating prompt and by the config that points
it at ``robot_mcp``.  So this package ships **assets**, and this module is the
smallest amount of Python that lets the test suite hold them to the live skill
API instead of to a copy somebody remembered to update.

Nothing here talks to OpenClaw, and nothing generates the prompt: prompt
quality is a human deliverable (D22 -- an LLM has to *operate* this robot
well), and a generated one would read like a schema dump.  What the loaders
buy is that ``robot_brain``'s tests, and anyone deploying the agent, name one
file rather than a path spelled out from memory.

The assets live *inside* the importable package, following ``robot_safety``'s
``limits.yaml``: a file beside the code is readable from a source checkout and
from a symlink-installed build alike, with no ament index and no ROS graph.
"""

from functools import lru_cache
from importlib import resources
import json
from typing import Any, Mapping

__all__ = [
    'AGENT_ID',
    'config_fragment',
    'CONFIG_RESOURCE',
    'MCP_SERVER_NAME',
    'operating_prompt',
    'PROMPT_RESOURCE',
    'RESOURCE_PACKAGE',
]

#: The OpenClaw agent id, the key under ``agents.entries`` and the directory
#: name under ``~/.openclaw/agents/``.  One name, used everywhere.
AGENT_ID = 'robot'

#: The name of the MCP server entry the agent drives the robot through.
MCP_SERVER_NAME = 'robot'

#: Where the assets sit inside the package.
RESOURCE_PACKAGE = 'robot_brain'
_RESOURCE_DIRECTORY = 'openclaw'

#: The operating prompt.  ``AGENTS.md`` is not a name we chose: OpenClaw reads
#: an agent's system prompt from the workspace file of that name (there is no
#: ``prompt`` field in its config), so the file ships under the name it must
#: eventually be installed as.
PROMPT_RESOURCE = 'AGENTS.md'

#: The config **merge fragment** -- the keys to merge into an existing
#: ``openclaw.json``, never a drop-in replacement for it.  See the README.
CONFIG_RESOURCE = 'openclaw.robot.json'


def _read(name: str) -> str:
    """Return the text of one shipped asset."""
    resource = resources.files(RESOURCE_PACKAGE) / _RESOURCE_DIRECTORY / name
    return resource.read_text(encoding='utf-8')


@lru_cache(maxsize=1)
def operating_prompt() -> str:
    """Return the agent's operating prompt, verbatim.

    Memoized: it is immutable text, and every drift test asks for it.
    """
    return _read(PROMPT_RESOURCE)


@lru_cache(maxsize=1)
def config_fragment() -> Mapping[str, Any]:
    """Return the OpenClaw config fragment, parsed.

    Parsed here rather than in each caller so that "the shipped fragment is
    not valid JSON" is a failure of *this* package, at import of its tests,
    rather than a puzzle discovered on the Pi at 11pm.
    """
    return json.loads(_read(CONFIG_RESOURCE))
