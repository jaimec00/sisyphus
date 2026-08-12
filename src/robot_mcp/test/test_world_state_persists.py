# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: a mutation survives a *real* MCP process restart.

Two spawned ``python -m robot_mcp --world-state PATH`` processes over one file:
the first moves the mug, the second -- a genuinely new interpreter that never
saw the first -- reports the moved mug.  In-process tests cannot prove this;
only a second process can show the world came off disk rather than out of a
Python object that happened to still be alive.
"""

import anyio
from mcp import ClientSession, stdio_client, StdioServerParameters
import pytest
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo, Side
from robot_world import read_document
from test_stdio_transport import server_parameters, TRANSPORT_TIMEOUT_SECONDS

pytestmark = pytest.mark.anyio


def persisted_server(live_path) -> StdioServerParameters:
    """Return launch parameters for a server whose world lives in ``live_path``."""
    parameters = server_parameters()
    return StdioServerParameters(
        command=parameters.command,
        args=[*parameters.args, '--world-state', str(live_path)],
        env=parameters.env,
    )


async def call(parameters: StdioServerParameters, calls) -> list:
    """Spawn a server, make each ``(name, arguments)`` call, and let it exit."""
    results = []
    with anyio.fail_after(TRANSPORT_TIMEOUT_SECONDS):
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    assert not result.is_error, result.structured_content
                    results.append(result.structured_content)
    return results


async def test_a_fresh_process_sees_the_previous_run_mutation(tmp_path):
    """The mug the first server picked up is where the second server finds it."""
    live = tmp_path / 'world.json'
    parameters = persisted_server(live)

    results = await call(
        parameters,
        [
            ('navigate_to', {'location': 'kitchen'}),
            ('grasp', {'object_id': 'mug_1'}),
            ('navigate_to', {'location': 'table'}),
        ],
    )

    # The first process really did carry it across the flat, and really did
    # write down where it ended up.
    reference = MockBackend()
    assert results == [
        reference.execute(skill).to_dict()
        for skill in (NavigateTo('kitchen'), Grasp('mug_1'), NavigateTo('table'))
    ]
    carried = reference.get_observation().find_object('mug_1')
    assert read_document(live).find_object('mug_1').pose == carried.pose

    # A second, independent process picks the world back up off disk.
    [observed] = await call(parameters, [('get_observation', {})])
    resumed = next(
        item for item in observed['objects'] if item['object_id'] == 'mug_1')

    assert resumed['pose'] == carried.pose.to_dict()
    assert resumed['pose'] != MockBackend().get_observation().find_object(
        'mug_1').pose.to_dict()
    # ...and it is a power cycle, not a resumed grasp: nothing is held, and the
    # robot is back on its charger.
    assert resumed['held_by'] is None
    assert observed['robot']['location'] == 'charger'
    for gripper in observed['robot']['grippers']:
        assert gripper['held_object_id'] is None


async def test_the_reset_tool_restores_the_seed_across_processes(tmp_path):
    """``reset`` puts the shipped scene back on disk, for good."""
    live = tmp_path / 'world.json'
    parameters = persisted_server(live)
    pristine = MockBackend().get_observation().find_object('mug_1').pose

    await call(
        parameters,
        [
            ('navigate_to', {'location': 'kitchen'}),
            ('grasp', {'object_id': 'mug_1', 'side': Side.RIGHT.value}),
            ('reset', {}),
        ],
    )

    assert read_document(live).find_object('mug_1').pose == pristine
    [observed] = await call(parameters, [('get_observation', {})])
    restored = next(
        item for item in observed['objects'] if item['object_id'] == 'mug_1')
    assert restored['pose'] == pristine.to_dict()


async def test_the_default_command_still_writes_nothing(tmp_path, monkeypatch):
    """Without the flag the spawned server is in memory: no file, no resume."""
    monkeypatch.chdir(tmp_path)
    parameters = server_parameters()
    parameters.env['PWD'] = str(tmp_path)

    first = await call(
        parameters,
        [('navigate_to', {'location': 'kitchen'}), ('grasp', {'object_id': 'mug_1'})],
    )
    [observed] = await call(parameters, [('get_observation', {})])

    assert first[-1]['status'] == 'ok'
    assert observed == MockBackend().get_observation().to_dict()
    assert sorted(entry.name for entry in tmp_path.iterdir()) == []
