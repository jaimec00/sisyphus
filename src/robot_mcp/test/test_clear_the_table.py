# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The first milestone, minus the LLM: "clear the table" over real MCP calls.

D21's milestone is a Telegram message driving an OpenClaw agent's tool loop
against the Mock backend.  OpenClaw does not run on this laptop, so what is
proven here is everything *below* the agent: that a caller who can only read
the structured payloads -- no Python objects, no knowledge of
``default_world()`` -- can perceive, plan, act and re-perceive over the MCP
tool surface until the table is clear, and that the safety gate is genuinely
in that path.

The driver below is therefore deliberately blind: it takes its object ids,
its target poses and its stopping condition out of the observation dicts the
tools return, exactly as the prompt in ``robot_brain`` teaches the agent to.
A test that named ``book_1`` would prove the world was as expected; this one
proves the *loop closes* on whatever is there.  It is a stand-in for the LLM,
not a claim that the LLM was tested.
"""

import math

from mcp_fixtures import connected, payload
import pytest
from robot_backends import default_world, MockBackend
from robot_safety import KeepOutBox, KeepOutBoxGuard, SafetyLayer, SafetyLimits
from robot_skills import Pose, SkillResult

pytestmark = pytest.mark.anyio

#: Where the chore happens, and where the clutter goes.  Both are names the
#: agent reads out of ``known_locations``; only the choice between them is a
#: decision, which is exactly the decision the LLM would be making.
TABLE = 'table'
KITCHEN = 'kitchen'

#: How near the robot's own base an object must be to count as "in front of
#: me".  The observation carries no reach radius (the backend owns kinematics),
#: so an agent judges "on this table" by proximity and finds out about reach
#: from an ``out_of_reach`` refusal -- which is what the prompt teaches.
NEARBY = 1.0

#: Refuse to loop forever: a stuck plan must fail the test, not hang it.
MAX_STEPS = 8

#: How far above a support surface to release a carried object.
DROP_CLEARANCE = 0.10

#: The travel range the shipped safety limits allow.
COLUMN = SafetyLimits.defaults().column


def _distance(first: dict, second: dict) -> float:
    """Return the metric distance between two wire-form positions."""
    return math.dist(
        (first['x'], first['y'], first['z']),
        (second['x'], second['y'], second['z']))


def clutter(observation: dict) -> list[dict]:
    """Return the graspable, unheld objects near the robot, nearest first.

    The whole plan is derived from this: what to pick up next, and when the
    chore is over (an empty list).
    """
    base = observation['robot']['pose']['position']
    nearby = [
        item for item in observation['objects']
        if item['graspable'] and item['held_by'] is None
        and _distance(item['pose']['position'], base) <= NEARBY
    ]
    return sorted(nearby, key=lambda item: _distance(item['pose']['position'], base))


def support_pose(observation: dict, label: str = 'counter') -> dict:
    """Return a pose just above the nearest surface labelled ``label``.

    The answer to "``place`` wants metric coordinates and I am an LLM": derive
    them from a pose already in the observation instead of inventing numbers.
    """
    base = observation['robot']['pose']['position']
    surfaces = [item for item in observation['objects'] if item['label'] == label]
    assert surfaces, f'no {label!r} in the scene to put anything on'
    nearest = min(surfaces, key=lambda item: _distance(item['pose']['position'], base))
    spot = nearest['pose']['position']
    return Pose.from_xyz(spot['x'], spot['y'], spot['z'] + DROP_CLEARANCE).to_dict()


async def call(client, name: str, arguments: dict | None = None) -> dict:
    """Call one tool and return its payload, failing on a *protocol* error.

    A refused skill is **not** an error here -- it is the payload the caller is
    meant to read -- so only ``isError`` (a malformed call) fails the test.
    """
    result = await client.call_tool(name, arguments or {})
    assert not result.is_error, payload(result)
    return payload(result)


async def clear_the_table(client, place_pose=None) -> list[str]:
    """Drive the chore to completion, returning the ids actually put away.

    One tool call per step, each one's returned observation deciding the next
    -- the loop D4 asks for and D21 hands to OpenClaw's native turn loop.
    """
    put_away = []
    for _ in range(MAX_STEPS):
        at_table = await call(client, 'navigate_to', {'location': TABLE})
        assert at_table['status'] == 'ok', at_table['reason']
        remaining = clutter(at_table['observation'])
        if not remaining:
            return put_away

        target = remaining[0]['object_id']
        grasped = await call(client, 'grasp', {'object_id': target})
        assert grasped['status'] == 'ok', grasped['reason']

        carried = await call(client, 'navigate_to', {'location': KITCHEN})
        assert carried['status'] == 'ok', carried['reason']
        pose = support_pose(carried['observation']) if place_pose is None else place_pose
        placed = await call(client, 'place', {'pose': pose})
        assert placed['status'] == 'ok', placed['reason']
        put_away.append(target)
    raise AssertionError(f'the loop did not terminate in {MAX_STEPS} rounds: {put_away}')


async def test_clear_the_table_end_to_end_over_mcp(backend):
    """The milestone: perceive, then navigate/grasp/place until nothing is left.

    Every id and every coordinate here came out of a tool result; the only
    inputs are the two location names, which the first observation lists.
    """
    async with connected(backend) as client:
        start = await call(client, 'get_observation')
        assert {TABLE, KITCHEN} <= set(start['known_locations'])

        at_table = await call(client, 'navigate_to', {'location': TABLE})
        expected = [item['object_id'] for item in clutter(at_table['observation'])]
        assert len(expected) >= 2, expected

        put_away = await clear_the_table(client)

        final = await call(client, 'navigate_to', {'location': TABLE})

    assert sorted(put_away) == sorted(expected)
    # The acceptance criterion, read the way the agent reads it: standing at
    # the table, there is nothing left to pick up.
    assert clutter(final['observation']) == []
    # ...and the objects are in the kitchen, not deleted or still in a gripper.
    kitchen_x = 2.0
    for object_id in put_away:
        item = next(
            entry for entry in final['observation']['objects']
            if entry['object_id'] == object_id)
        assert item['held_by'] is None
        assert item['pose']['position']['x'] == pytest.approx(kitchen_x, abs=0.6)
    for gripper in final['observation']['robot']['grippers']:
        assert gripper['held_object_id'] is None


async def test_every_step_of_the_run_is_readable_back_as_a_skill_result(backend):
    """The transcript is data at ``schema_version: 1``, not prose (D18).

    A client that rebuilds each payload gets the same typed values the backend
    produced -- which is what lets a *different* consumer (a log, a store, the
    next backend) read the same run.
    """
    async with connected(backend) as client:
        await call(client, 'navigate_to', {'location': TABLE})
        observation = await call(client, 'get_observation')
        target = clutter(observation)[0]['object_id']
        grasped = await call(client, 'grasp', {'object_id': target})

    rebuilt = SkillResult.from_dict(grasped)
    assert rebuilt.succeeded
    assert rebuilt.skill.object_id == target
    assert rebuilt.observation.find_object(target).is_held
    assert rebuilt.to_dict() == grasped


async def test_a_place_from_too_far_away_is_a_refusal_the_loop_recovers_from(backend):
    """The mistake an agent actually makes: place before walking over.

    ``out_of_reach`` has to be legible enough to fix without parsing prose --
    a normal result with a code -- and it must leave the object still held, or
    "retry from nearer" would be dropping the mug.
    """
    async with connected(backend) as client:
        at_table = await call(client, 'navigate_to', {'location': TABLE})
        target = clutter(at_table['observation'])[0]['object_id']
        grasped = await call(client, 'grasp', {'object_id': target})
        # The kitchen counter's pose is in the observation from here; the
        # distance to it is not something the agent can see.
        far_away = support_pose(grasped['observation'])

        refused = await call(client, 'place', {'pose': far_away})
        assert refused['status'] == 'failed'
        assert refused['code'] == 'out_of_reach'
        assert refused['code'] != 'rejected', 'a reach refusal is the backend, not safety'
        assert refused['observation']['objects'] == grasped['observation']['objects']
        still_held = next(
            item for item in refused['observation']['objects']
            if item['object_id'] == target)
        assert still_held['held_by'] is not None

        # The documented recovery: get closer, then repeat the same call.
        carried = await call(client, 'navigate_to', {'location': KITCHEN})
        placed = await call(client, 'place', {'pose': support_pose(carried['observation'])})

    assert placed['status'] == 'ok'
    assert placed['observation']['objects'] != refused['observation']['objects']


async def test_the_default_server_clamps_a_column_command_mid_run(backend):
    """Safety is in the milestone path, with nothing injected to put it there.

    The agent over-reaches for a high shelf; the gate rewrites the command
    rather than letting the backend refuse it, and the *observation* -- not a
    special field -- tells the agent where the column really ended up, which
    is how it knows to lower it again and carry on.
    """
    async with connected(backend) as client:
        await call(client, 'navigate_to', {'location': TABLE})
        raised = await call(client, 'extend_column', {'height': 2.5})

        assert raised['status'] == 'ok'
        assert raised['skill']['height'] == COLUMN.max_height
        assert raised['observation']['robot']['column_height'] == COLUMN.max_height
        assert 'clamped' in raised['reason']

        # From up there the table is out of reach -- so the agent reads the
        # refusal, drops back to a working height and finishes the chore.
        target = clutter(raised['observation'])[0]['object_id']
        overreached = await call(client, 'grasp', {'object_id': target})
        assert overreached['status'] == 'failed'
        assert overreached['code'] == 'out_of_reach'

        lowered = await call(client, 'extend_column', {'height': 0.3})
        assert lowered['reason'] is None
        put_away = await clear_the_table(client)

    assert len(put_away) >= 2


async def test_an_injected_guard_stops_the_loop_and_the_agent_cannot_route_around_it():
    """A keep-out region over the drop zone: the chore fails, safely and legibly.

    The enforcement is server-side (D21), so no sequence of tool calls the
    agent can make gets the object into the region -- it keeps holding it and
    is told, in the safety layer's own words, why.
    """
    counter = next(
        spec for spec in default_world().objects if spec.label == 'counter')
    forbidden = KeepOutBox(
        label='counter_top',
        x_min=counter.pose.position.x - 0.5,
        x_max=counter.pose.position.x + 0.5,
        y_min=counter.pose.position.y - 0.5,
        y_max=counter.pose.position.y + 0.5,
        z_min=counter.pose.position.z,
    )
    backend = MockBackend()
    guarded = SafetyLayer(collision_guard=KeepOutBoxGuard(boxes=(forbidden,)))

    async with connected(backend, guarded) as client:
        at_table = await call(client, 'navigate_to', {'location': TABLE})
        target = clutter(at_table['observation'])[0]['object_id']
        await call(client, 'grasp', {'object_id': target})
        carried = await call(client, 'navigate_to', {'location': KITCHEN})

        pose = support_pose(carried['observation'])
        refused = await call(client, 'place', {'pose': pose})
        retried = await call(client, 'place', {'pose': pose})

    assert refused['status'] == 'failed'
    assert refused['code'] == 'rejected'
    assert 'counter_top' in refused['reason']
    assert retried['code'] == 'rejected', 'trying again must not wear the gate down'
    # The object never entered the region: it is still in a gripper.
    assert [item['object_id'] for item in refused['observation']['objects']
            if item['held_by'] is not None] == [target]
