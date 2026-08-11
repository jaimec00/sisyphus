# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""What we can honestly check about the OpenClaw config fragment.

**We cannot check that OpenClaw accepts it.**  OpenClaw runs on the Pi, is not
installed on this laptop, and nothing here validates against its schema -- the
field names come from its documentation, and the README tells the operator to
verify them on the Pi with ``openclaw config schema``.  Any test in this file
that implied otherwise would be lying.

What *is* checkable, and worth checking, is internal consistency: it parses, it
declares the server the prompt assumes, the agent it binds is the agent it
configures, the launch command actually starts *our* server with the packages
it now needs (``robot_safety`` is a runtime dependency since the safety gate
landed), the tools it exposes are the tools that exist -- and it carries no
secret.
"""

import json
import re

from brain_fixtures import WITHHELD_TOOLS
from robot_brain import AGENT_ID, config_fragment, MCP_SERVER_NAME
from robot_brain.agent import CONFIG_RESOURCE, PROMPT_RESOURCE
from robot_mcp.tools import TOOL_NAMES

FRAGMENT = config_fragment()

#: The workspace packages the launch command must put on ``PYTHONPATH``.
REQUIRED_PACKAGES = ('robot_skills', 'robot_backends', 'robot_safety', 'robot_mcp')

#: A Telegram bot token: digits, a colon, then a long opaque string.  The
#: shape, not a specific token -- a committed credential must fail the suite
#: whoever's it is.
TOKEN_SHAPE = re.compile(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b')

#: Key names that would hold a credential if anyone put one here.
SECRET_KEYS = frozenset(
    {'token', 'apikey', 'api_key', 'secret', 'password', 'bottoken'})


def server() -> dict:
    """Return the fragment's MCP server entry for the robot."""
    return FRAGMENT['mcp']['servers'][MCP_SERVER_NAME]


def launch_command() -> str:
    """Return the whole launch command line, arguments joined."""
    return ' '.join([server()['command'], *server()['args']])


def walk(value):
    """Yield every ``(key, value)`` pair anywhere in a JSON structure."""
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key, nested
            yield from walk(nested)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def test_the_fragment_is_json_and_declares_only_merge_keys():
    """It is a *fragment*: three top-level keys to merge, not a whole config.

    Shipping something that looks like a complete ``openclaw.json`` would
    invite an operator to copy it over theirs and lose every other agent.
    """
    assert set(FRAGMENT) == {'mcp', 'agents', 'bindings'}


def test_the_agent_the_binding_routes_to_is_the_agent_configured():
    """One agent id, spelled the same in the entry, the binding and the code."""
    assert set(FRAGMENT['agents']['entries']) == {AGENT_ID}
    assert [binding['agentId'] for binding in FRAGMENT['bindings']] == [AGENT_ID]
    entry = FRAGMENT['agents']['entries'][AGENT_ID]
    assert entry['name'] == AGENT_ID
    # The prompt is loaded from the workspace, so the workspace must be the
    # agent's own directory -- OpenClaw has no "prompt" field to point at.
    assert entry['workspace'].endswith(f'agents/{AGENT_ID}')


def test_the_agent_is_scoped_to_the_robot_tools():
    """The brain drives the robot, not the Pi it runs on."""
    allowed = FRAGMENT['agents']['entries'][AGENT_ID]['tools']['allow']
    assert allowed, 'an empty allow list would give the agent nothing to drive'
    assert all(MCP_SERVER_NAME in entry for entry in allowed), allowed


def test_the_binding_carries_a_placeholder_account_not_a_credential():
    """Telegram routing needs an account; the token is added on the Pi."""
    match = FRAGMENT['bindings'][0]['match']
    assert match['channel'] == 'telegram'
    assert 'REPLACE' in match['accountId'], 'ship a placeholder, not somebody real'


def test_the_fragment_contains_no_secret():
    """A credential must never reach this repo, in any key or any value.

    The detector is checked against a token-shaped sample first: a scan that
    cannot recognise the thing it is looking for is worse than no scan.
    """
    assert TOKEN_SHAPE.search('"botToken": "8123456789:AAH_notARealTokenButShaped"')
    text = json.dumps(FRAGMENT)
    assert not TOKEN_SHAPE.search(text), 'something token-shaped is in the fragment'
    for key, value in walk(FRAGMENT):
        assert key.lower() not in SECRET_KEYS, f'{key!r} has no business here'
        if isinstance(value, str):
            assert not value.startswith('sk-'), key


def test_the_launch_command_starts_this_repos_server_over_stdio():
    """The transport OpenClaw is told to use is the one the server speaks."""
    assert server()['transport'] == 'stdio'
    assert server()['enabled'] is True
    command = launch_command()
    assert 'python -m robot_mcp' in command


def test_the_launch_command_reaches_the_laptop_without_a_pty():
    """``robot_mcp`` runs on the laptop; OpenClaw runs on the Pi (D21 topology).

    ``ssh -T`` because a pty would inject terminal control bytes into a stream
    that carries nothing but MCP frames.
    """
    assert server()['command'] == 'ssh'
    assert '-T' in server()['args']


def test_the_launch_command_carries_every_package_the_server_needs():
    """Including ``robot_safety`` -- the gate is a runtime dependency now.

    A missing entry here is not a subtle degradation: the server fails to
    import and the agent has no tools at all.
    """
    command = launch_command()
    for package in REQUIRED_PACKAGES:
        assert f'/src/{package}' in command, package


def test_the_exposed_tools_are_the_tools_this_agent_should_have():
    """Every served tool is either exposed or withheld *on purpose*.

    Two failure modes, one assertion: the filter cannot name a tool the server
    does not serve, and a tool added to the seam cannot quietly appear in (or
    vanish from) the model's allowlist.  What it deliberately does **not** do
    is demand that every future tool be exposed -- a teleop escape hatch or a
    torque setter must be able to arrive without the path of least resistance
    being "hand it to the LLM".
    """
    exposed = set(server()['toolFilter']['include'])
    expected = set(TOOL_NAMES) - set(WITHHELD_TOOLS)

    assert exposed == expected, exposed.symmetric_difference(expected)


def test_every_withheld_tool_is_a_real_tool_with_a_stated_reason():
    """The withhold list cannot go stale or become a shrug."""
    assert set(WITHHELD_TOOLS) <= set(TOOL_NAMES), (
        set(WITHHELD_TOOLS) - set(TOOL_NAMES))
    for name, reason in WITHHELD_TOOLS.items():
        assert len(reason) > 40, f'{name} is withheld without saying why'


def test_the_request_timeout_allows_for_a_slow_chore():
    """A skill call crosses a VPN and runs a motion; a short default would cut it."""
    assert server()['requestTimeoutMs'] >= 30_000


def test_the_asset_names_are_the_names_openclaw_expects():
    """``AGENTS.md`` is not our choice: it is where OpenClaw reads a prompt from."""
    assert PROMPT_RESOURCE == 'AGENTS.md'
    assert CONFIG_RESOURCE.endswith('.json')
