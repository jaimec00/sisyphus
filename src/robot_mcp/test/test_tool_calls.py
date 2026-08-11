# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""What an agent gets back: the seam's dicts, refusals, and bad calls.

Every expectation is the *other* backend's own answer to the same skill, not a
dict written out by hand here -- so this suite fails if the server drops a
field, reorders a nested structure, reformats a value, or lets its backend
drift out of step with the skills it was told to run.
"""

import anyio
from mcp_fixtures import connected, payload
import pytest
from robot_backends import MockBackend
from robot_skills import Grasp, NavigateTo, Pose, skill_from_dict, SKILL_TYPES

pytestmark = pytest.mark.anyio


async def test_reset_navigate_grasp_observe_matches_a_parallel_backend(backend, reference):
    """The required sequence, dict-equal to the same skills run directly.

    ``reference`` is stepped through the identical skills in the identical
    order, so this asserts both that each tool returns the seam's dict verbatim
    *and* that the server's own backend ends up in the same state -- one call
    per step, each observed before the next, exactly how an agent drives it.
    """
    async with connected(backend) as client:
        assert payload(await client.call_tool('reset', {})) == reference.reset().to_dict()

        result = payload(await client.call_tool('navigate_to', {'location': 'kitchen'}))
        assert result == reference.execute(NavigateTo('kitchen')).to_dict()
        assert result['status'] == 'ok'

        result = payload(await client.call_tool('grasp', {'object_id': 'mug_1'}))
        assert result == reference.execute(Grasp('mug_1')).to_dict()
        assert result['status'] == 'ok'
        assert result['skill'] == {'skill': 'grasp', 'object_id': 'mug_1', 'side': None}

        observed = payload(await client.call_tool('get_observation', {}))
        assert observed == reference.get_observation().to_dict()

    # The structured scene the agent reads, not prose: the grasp is visible as
    # data at every level the brief calls out.
    assert observed['schema_version'] == 1
    held = [gripper for gripper in observed['robot']['grippers'] if gripper['grasped']]
    assert [gripper['held_object_id'] for gripper in held] == ['mug_1']
    mug = next(item for item in observed['objects'] if item['object_id'] == 'mug_1')
    assert mug['held_by'] == held[0]['side']
    assert set(mug['pose']['position']) == {'x', 'y', 'z'}


async def test_get_observation_does_not_disturb_the_scene(backend, reference):
    """Perceiving is free: two observations either side of nothing are equal."""
    async with connected(backend) as client:
        before = payload(await client.call_tool('get_observation', {}))
        after = payload(await client.call_tool('get_observation', {}))

    assert before == after == reference.get_observation().to_dict()


async def test_reset_undoes_what_the_tools_did(backend, reference):
    """``reset`` returns the seed scene, so an agent can start a run over."""
    async with connected(backend) as client:
        await client.call_tool('navigate_to', {'location': 'kitchen'})
        moved = payload(await client.call_tool('grasp', {'object_id': 'mug_1'}))
        assert moved['status'] == 'ok'

        restored = payload(await client.call_tool('reset', {}))

    assert restored == reference.get_observation().to_dict()
    assert restored['robot']['location'] == 'charger'


#: A run touching every skill tool once, with its arguments in wire form.
FULL_RUN = (
    ('extend_column', {'height': 0.6}),
    ('open_gripper', {'side': 'right'}),
    ('close_gripper', {'side': 'right'}),
    ('move_gripper', {'side': 'left', 'pose': Pose.from_xyz(0.4, 1.9, 0.8).to_dict()}),
    ('navigate_to', {'location': 'kitchen'}),
    ('grasp', {'object_id': 'mug_1', 'side': 'left'}),
    ('navigate_to', {'location': 'table'}),
    ('place', {'pose': Pose.from_xyz(0.35, 2.05, 0.75).to_dict(), 'side': 'left'}),
)


async def test_every_skill_is_reachable_as_a_tool(backend, reference):
    """One call per skill tool, each dict-equal to the skill run directly.

    Covers the four skills the sequence test does not touch, so no tool can be
    wired to the wrong skill class or quietly mis-parse its arguments -- a
    ``move_gripper`` tool that dropped the pose would still "work" without this.
    """
    assert {name for name, _ in FULL_RUN} == set(SKILL_TYPES)

    async with connected(backend) as client:
        for name, arguments in FULL_RUN:
            got = payload(await client.call_tool(name, arguments))
            expected = reference.execute(skill_from_dict({'skill': name, **arguments}))
            assert got == expected.to_dict(), (name, got)
            assert got['skill'] == {'skill': name, **_wire_defaults(name, arguments)}


def _wire_defaults(name: str, arguments: dict) -> dict:
    """Return ``arguments`` as the skill echoes them back (optionals filled in)."""
    if name in ('grasp', 'place') and 'side' not in arguments:
        return {**arguments, 'side': None}
    return arguments


@pytest.mark.parametrize(
    ('object_id', 'code'),
    [('ghost_1', 'unknown_object'), ('counter_1', 'not_graspable')],
)
async def test_a_refused_skill_is_a_normal_result_not_an_error(
    backend, reference, object_id, code,
):
    """A backend refusal comes back as data the agent can branch on."""
    async with connected(backend) as client:
        result = await client.call_tool('grasp', {'object_id': object_id})

    assert not result.is_error
    refused = payload(result)
    assert refused['status'] == 'failed'
    assert refused['code'] == code
    assert refused['reason']
    assert refused == reference.execute(Grasp(object_id)).to_dict()
    # Refusals leave the world alone, and the tool reports that world.
    assert refused['observation'] == reference.get_observation().to_dict()


@pytest.mark.parametrize(
    ('name', 'arguments', 'fragment'),
    [
        ('grasp', {}, 'missing required key(s): object_id'),
        ('grasp', {'object_id': 'mug_1', 'side': 'up'}, "'up' is not a valid Side"),
        ('grasp', {'object_id': 'mug_1', 'bogus': 1}, 'unknown key(s): bogus'),
        ('navigate_to', {'location': 5}, 'expected a string'),
        ('extend_column', {'height': 'high'}, 'expected a number'),
        ('move_gripper', {'side': 'left', 'pose': {}}, 'missing required key(s): position'),
    ],
)
async def test_a_malformed_argument_is_a_tool_error_carrying_the_seam_message(
    backend, name, arguments, fragment,
):
    """Bad arguments fail the call, not the process, and say what was wrong."""
    async with connected(backend) as client:
        result = await client.call_tool(name, arguments)

        assert result.is_error
        envelope = payload(result)
        assert envelope['error'] == 'SerializationError'
        assert fragment in envelope['message'], envelope['message']

        # The session survives: the very next call still works.
        assert payload(await client.call_tool('get_observation', {}))['schema_version'] == 1


async def test_a_malformed_argument_changes_nothing(backend, reference):
    """A rejected call never reaches the backend, so the world is untouched."""
    async with connected(backend) as client:
        await client.call_tool('navigate_to', {'location': 'kitchen'})
        before = payload(await client.call_tool('get_observation', {}))

        assert (await client.call_tool('grasp', {'object_id': ''})).is_error
        assert (await client.call_tool('grasp', {'object_id': 'mug_1', 'side': 8})).is_error

        assert payload(await client.call_tool('get_observation', {})) == before

    assert before == reference.execute(NavigateTo('kitchen')).observation.to_dict()


async def test_an_unknown_tool_is_an_error_listing_the_real_ones(backend):
    """An invented tool name is refused by name, not by crashing the handler."""
    async with connected(backend) as client:
        envelope = payload(await client.call_tool('teleport', {}))

        assert envelope['error'] == 'UnknownTool'
        assert 'teleport' in envelope['message']
        for expected in ('grasp', 'get_observation', 'reset'):
            assert expected in envelope['message']

        assert payload(await client.call_tool('get_observation', {}))['schema_version'] == 1


async def test_the_skill_key_cannot_be_smuggled_in_as_an_argument(backend, reference):
    """The tool name picks the skill; a ``skill`` argument cannot override it."""
    async with connected(backend) as client:
        result = await client.call_tool(
            'grasp', {'skill': 'navigate_to', 'location': 'kitchen'})

        assert result.is_error
        assert payload(result)['error'] == 'InvalidArguments'
        assert payload(await client.call_tool(
            'get_observation', {})) == reference.get_observation().to_dict()


@pytest.mark.parametrize('name', ['get_observation', 'reset'])
async def test_the_argument_free_tools_refuse_arguments(backend, name):
    """Their schema says ``additionalProperties: false``; the handler agrees."""
    async with connected(backend) as client:
        envelope = payload(await client.call_tool(name, {'side': 'left'}))

        assert envelope['error'] == 'InvalidArguments'
        assert 'side' in envelope['message']


async def test_an_unexpected_backend_failure_is_reported_not_raised(reference):
    """A backend that breaks its contract must not take the session down."""
    class BrokenBackend(MockBackend):
        """A backend whose execute raises, as a buggy Sim/Real one might."""

        def execute(self, skill):
            """Fail the way no legal backend should."""
            raise RuntimeError('joint controller went away')

    async with connected(BrokenBackend()) as client:
        envelope = payload(await client.call_tool('navigate_to', {'location': 'kitchen'}))
        assert envelope['error'] == 'RuntimeError'
        assert 'joint controller went away' in envelope['message']

        # Still serving: perception, which does not go through execute, works.
        assert payload(await client.call_tool(
            'get_observation', {})) == reference.get_observation().to_dict()


async def test_concurrent_calls_share_one_consistent_backend(backend):
    """Overlapping calls see one world, and one of them wins the mug.

    The SDK dispatches tool calls in a task group, so this is the interleaving
    the router's lock exists for: whatever the ordering, the mug is grasped
    exactly once and every loser gets an attributable refusal.
    """
    results = {}

    async def grasp(index: int, client) -> None:
        results[index] = payload(await client.call_tool('grasp', {'object_id': 'mug_1'}))

    async with connected(backend) as client:
        await client.call_tool('navigate_to', {'location': 'kitchen'})
        async with anyio.create_task_group() as tasks:
            for index in range(4):
                tasks.start_soon(grasp, index, client)

        observed = payload(await client.call_tool('get_observation', {}))

    statuses = sorted(result['status'] for result in results.values())
    assert statuses == ['failed', 'failed', 'failed', 'ok']
    assert all(
        result['code'] is not None
        for result in results.values() if result['status'] == 'failed')
    holders = [
        gripper for gripper in observed['robot']['grippers']
        if gripper['held_object_id'] == 'mug_1'
    ]
    assert len(holders) == 1
    assert backend.get_observation().to_dict() == observed
