# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""What we can honestly check about the OpenClaw config fragment, in Python.

**Whether OpenClaw accepts it is checked next door**, in
``test_openclaw_validates.py``, which shells out to the real
``openclaw config validate``.  Until #52 that was impossible and this docstring
said so; the CLI now ships in the pixi env (``pixi run install-openclaw``), so
the schema-shape question belongs to the validator, not to a hand-written list
of field names copied out of the documentation.

What the validator cannot answer, and this file therefore must, is everything
that is *well-formed but wrong*.  ``tools.allow`` is ``array<string>`` in the
schema, so every spelling of a tool glob validates -- including
``mcp__robot__*``, which is Claude Code's convention and matched no OpenClaw
tool at all (``openclaw doctor``: "allowlist contains unknown entries").  A
config can validate and still hand the brain nothing to drive.

So: it parses, it declares the server the prompt assumes, the agent it binds is
the agent it configures, the launch command actually starts *our* server with
the packages it now needs (``robot_safety`` is a runtime dependency since the
safety gate landed), the tools it exposes are the tools that exist under the
name OpenClaw will give them, its sandbox settings do not contradict each
other -- and it carries no secret.
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


def agent() -> dict:
    """Return the fragment's agent entry for ``AGENT_ID``.

    ``agents`` is a *list* keyed by an ``id`` field, not a mapping (the schema
    says ``agents.additionalProperties: false`` over ``{defaults, list}``), so
    "the robot's entry" is a search rather than a lookup.  Local to this file
    for the same reason ``server()`` is: nothing in ``src/`` reads the entry.
    """
    entries = [entry for entry in FRAGMENT['agents']['list']
               if entry.get('id') == AGENT_ID]
    assert len(entries) == 1, f'{AGENT_ID} appears {len(entries)} times in agents.list'
    return entries[0]


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
    """One agent id, spelled the same in the entry, the binding and the code.

    ``id`` is the only field the schema *requires* of an entry, and it is what
    ``bindings[].agentId`` resolves against -- the validator rejects a binding
    that names an absent agent, but it cannot know our code calls it
    ``AGENT_ID``.
    """
    assert [entry['id'] for entry in FRAGMENT['agents']['list']] == [AGENT_ID]
    assert [binding['agentId'] for binding in FRAGMENT['bindings']] == [AGENT_ID]
    assert agent()['name'] == AGENT_ID
    # The prompt is loaded from the workspace, so the workspace must be the
    # agent's own directory -- OpenClaw has no "prompt" field to point at.
    assert agent()['workspace'].endswith(f'agents/{AGENT_ID}')


def test_the_agent_is_scoped_to_the_robot_tools():
    """The brain drives the robot, not the Pi it runs on.

    The spelling matters as much as the scoping.  OpenClaw exposes an MCP tool
    as ``<server>__<tool>``; ``mcp__<server>__<tool>`` is Claude Code's
    convention, and an allowlist written that way validates cleanly while
    matching nothing -- ``openclaw doctor`` on the old fragment said
    "agents.robot.tools.allow allowlist contains unknown entries
    (mcp__robot__*)".  That is a mute robot, not a scoped one, so the glob is
    pinned exactly rather than by substring.

    The glob is derived from the ``mcp.servers`` key actually shipped, not from
    ``MCP_SERVER_NAME``, so renaming the server without rewriting the allowlist
    fails here instead of at the agent's first tool call.  That derivation is
    only sound while the key needs no mangling -- see below.
    """
    server_key, = FRAGMENT['mcp']['servers']
    allowed = agent()['tools']['allow']
    assert allowed, 'an empty allow list would give the agent nothing to drive'
    assert allowed == [f'{server_key}__*'], allowed


def test_the_server_name_needs_no_provider_safe_mangling():
    """``<server>__*`` is only the right glob when the prefix is the identity.

    OpenClaw derives the prefix from the ``mcp.servers`` key by lower-casing,
    replacing every character outside ``[A-Za-z0-9_-]`` with ``-`` and putting
    an ``mcp-`` in front of a name that does not start with a letter --
    ``mcp.servers["Outlook Graph"]`` globs as ``outlook-graph__*``
    (``docs/gateway/config-tools.md:59``).  Rename the server to ``robot mcp``
    and the test above would assert ``'robot mcp__*'``, which validates, matches
    nothing, and hands the brain no tools: Bug A returning through a side door.
    """
    server_key, = FRAGMENT['mcp']['servers']
    assert server_key == MCP_SERVER_NAME, 'the code and the config disagree'
    assert re.fullmatch(r'[a-z][a-z0-9_-]*', server_key), (
        f'{server_key!r} would be mangled into a different tool prefix')


#: Any one of these in ``tools.sandbox.tools`` lets the ``robot`` MCP tools
#: through the sandbox gate (``docs/gateway/config-tools.md:52-56``).  Note the
#: enum of ``sandbox.mode`` is deliberately *not* copied here: what is a legal
#: value is the validator's question, and hand-copying it from documentation is
#: the habit that produced #52.
SANDBOX_MCP_ADMISSIONS = ('bundle-mcp', 'group:plugins', f'{MCP_SERVER_NAME}__*')


def sandbox_complaints(entry: dict) -> list[str]:
    """Return every way ``entry``'s sandbox settings contradict each other.

    A detector rather than a run of asserts, so that it can be shown to
    *detect*.  The shipped fragment has sandboxing off, which makes every rule
    below vacuous for that fragment; a test that only ran them against the
    fragment would be a test of nothing.

    Read the rules as being about a **merged** config, not this file alone.
    That is why an *absent* ``mode`` is a complaint rather than a synonym for
    ``"off"``: OpenClaw resolves it as
    ``agentSandbox?.mode ?? agents.defaults.sandbox?.mode ?? "off"``
    (``dist/config-Dy4vED5-.js:153``), so an entry that says nothing adopts
    whatever posture the operator's config already has.
    """
    sandbox = entry.get('sandbox')
    if not isinstance(sandbox, dict) or 'mode' not in sandbox:
        return ['sandbox.mode is unset, so the merged entry inherits '
                'agents.defaults.sandbox.mode from the operator config instead '
                'of stating its own posture']

    mode = sandbox['mode']
    gate = entry.get('tools', {}).get('sandbox', {}).get('tools', {})
    admitted = list(gate.get('alsoAllow', [])) + list(gate.get('allow', []))
    complaints = []
    if mode == 'off':
        if 'workspaceAccess' in sandbox:
            complaints.append('workspaceAccess is inert while sandbox.mode is off')
        if gate:
            complaints.append('tools.sandbox is inert while sandbox.mode is off')
        return complaints

    if not any(admission in admitted for admission in SANDBOX_MCP_ADMISSIONS):
        complaints.append(
            f'sandbox.mode is {mode!r}, so bundled MCP tools are filtered out '
            f'unless the sandbox tool policy admits them: {gate}')
    # ``workspaceAccess`` also defaults to "none" when unset
    # (``dist/config-Dy4vED5-.js:156``), so an absent value is not neutral here.
    workspace_access = sandbox.get('workspaceAccess', 'none')
    if workspace_access != 'rw':
        complaints.append(
            f'sandbox.mode is {mode!r} with workspaceAccess {workspace_access!r}: '
            'the effective workspace becomes the sandbox workspace '
            '(dist/compact-DLB4d8IL.js:551) and a compaction turn can come back '
            'without AGENTS.md')
    return complaints


def test_the_sandbox_consistency_check_detects_what_it_forbids():
    """The check above is only worth running if it actually catches something.

    Every forbidden state below is real, and two of them are the states this
    fix was talked into and then out of:

    * **sandbox on, no gate** -- ``openclaw doctor`` on exactly that fragment
      warns ``tools.sandbox.tools.alsoAllow (unset) does not include
      "bundle-mcp" ... Sandboxed agents will filter bundled MCP tools before
      provider requests``: a config that validates and quietly disarms the
      robot.
    * **sandbox on, ``workspaceAccess`` not ``"rw"``** -- the posture this
      fix originally shipped, reversed because it can cost the brain its
      operating prompt (see the shipped-settings test below).
    * **no ``mode`` at all** -- the state that *looks* like the tidy version of
      ``off`` and is in fact "whatever the operator does".
    * the two inert-key states, which are the mild half: settings that read
      like a restriction and enforce nothing.
    """
    assert sandbox_complaints({'tools': {}})
    assert sandbox_complaints({'sandbox': {'workspaceAccess': 'rw'}})
    assert sandbox_complaints({'sandbox': {'mode': 'all'}, 'tools': {}})
    assert sandbox_complaints(
        {'sandbox': {'mode': 'all', 'workspaceAccess': 'ro'},
         'tools': {'sandbox': {'tools': {'alsoAllow': ['bundle-mcp']}}}})
    assert sandbox_complaints({'sandbox': {'mode': 'off', 'workspaceAccess': 'ro'}})
    assert sandbox_complaints(
        {'sandbox': {'mode': 'off'},
         'tools': {'sandbox': {'tools': {'alsoAllow': ['bundle-mcp']}}}})
    # ...and does not cry wolf over either coherent configuration.  The second
    # is the reason the detector cannot simply forbid `mode != "off"`.
    assert sandbox_complaints({'sandbox': {'mode': 'off'}}) == []
    assert sandbox_complaints(
        {'sandbox': {'mode': 'all', 'workspaceAccess': 'rw'},
         'tools': {'sandbox': {'tools': {'alsoAllow': ['bundle-mcp']}}}}) == []


def test_the_shipped_sandbox_settings_do_not_contradict_each_other():
    """Sandboxing is **off**, said out loud, and the fragment says only that.

    The first attempt at this fix turned it on (``mode: "all"`` +
    ``workspaceAccess: "ro"``) on the reasoning that it costs nothing while the
    agent is allowed no ``exec``/``read``/``write``/``edit``/``apply_patch``/
    ``process``-class tool.  It does not cost nothing.  Sandboxing on with
    ``workspaceAccess`` other than ``"rw"`` swaps the *effective workspace* for
    the sandbox workspace (``dist/compact-DLB4d8IL.js:551``), and the compaction
    path resolves the bootstrap context from that -- so a long conversation
    could compact and come back without ``AGENTS.md``, which is the entire
    brain.  A sandbox protecting nothing is not worth risking the operating
    prompt for, so ``mode: "off"``, and no inert companions.

    **``"off"`` is written down rather than left to the default**, and that is
    the one sandbox key here that must not be tidied away.  This is a merge
    fragment: an entry with no ``mode`` inherits ``agents.defaults.sandbox.mode``
    from the config it is merged into, and OpenClaw's own documented example
    ships ``agents.defaults.sandbox: {mode: "non-main"}`` -- under which a
    Telegram session is *always* non-main.  Dropping the key would hand such an
    operator a sandboxed robot with no gate and no tools, which is precisely the
    failure the detector above exists to prevent.  Deleting an inert key is
    tidying; deleting an override is a behaviour change.
    """
    assert sandbox_complaints(agent()) == [], sandbox_complaints(agent())


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
