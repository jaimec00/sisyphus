# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance: ``--backend mujoco`` serves the same wire schema as Mock.

MuJoCo and Mock are different backends with different *physics* -- gripper
poses, and anything a real simulation reports, legitimately differ.  But they
must speak the *same schema* over the MCP client (invariant 2 / D9): both
produce ``Observation`` and ``SkillResult`` dicts that the shared seam
(``robot_skills.Observation.from_dict`` / ``SkillResult.from_dict``) parses,
re-serialises stably, and that agree on every field the seed world guarantees
(scene object poses, ids, graspability, map vocabulary, robot location,
column height, gripper open/empty posture).  That is what lets a brain or a
server swap the backend without a schema change.
"""

from mcp_fixtures import connected, payload
import pytest
from robot_backends import MockBackend, MuJoCoBackend
from robot_skills import (
    GripperState,
    Observation,
    SkillResult,
)

pytestmark = pytest.mark.anyio


def _assert_observation_parses(wire):
    """Prove ``wire`` is a legal ``Observation`` that survives a round trip."""
    obs = Observation.from_dict(wire)
    assert obs.to_dict() == wire, 're-serialisation must be stable'


def _assert_result_parses(wire):
    """Prove ``wire`` is a legal ``SkillResult`` that survives a round trip."""
    result = SkillResult.from_dict(wire)
    assert result.to_dict() == wire, 're-serialisation must be stable'


async def test_mujoco_observation_is_the_mock_wire_schema():
    """The MuJoCo reset/observation payload parses under the shared schema."""
    async with connected(MuJoCoBackend()) as client:
        reset = payload(await client.call_tool('reset', {}))
        observed = payload(await client.call_tool('get_observation', {}))

    _assert_observation_parses(reset)
    _assert_observation_parses(observed)
    assert observed == reset


async def test_mujoco_result_is_the_mock_wire_schema():
    """A MuJoCo ``execute`` refusal parses under the shared ``SkillResult`` schema."""
    async with connected(MuJoCoBackend()) as client:
        result = payload(await client.call_tool('navigate_to', {'location': 'kitchen'}))

    _assert_result_parses(result)
    assert result['status'] == 'failed'
    assert result['code'] == 'unsupported_skill'
    assert result['skill'] == {'skill': 'navigate_to', 'location': 'kitchen'}


async def test_mujoco_agrees_with_mock_on_the_seed_guaranteed_fields():
    """Everything the seed world fixes is identical across the two backends.

    The seed world does not dictate gripper *pose* (each backend derives it
    from its own body model), so those may differ; every field the seed pins
    down must not.
    """
    async with connected(MuJoCoBackend()) as client:
        observations = []
        for tool in ('reset', 'get_observation'):
            observations.append(
                payload(await client.call_tool(tool, {})).copy())

    mu = observations[0]

    # Reference: a fresh Mock run of the same two calls.
    mock = MockBackend()
    mock.reset().to_dict()
    wire_reference = mock.get_observation().to_dict()

    # The stable structures that both must report identically.
    assert mu['schema_version'] == wire_reference['schema_version']
    assert mu['robot']['location'] == wire_reference['robot']['location'] == 'charger'
    assert mu['robot']['column_height'] == pytest.approx(
        wire_reference['robot']['column_height'], abs=1e-6)

    # Same object ids, graspability and seed poses in the same sorted order.
    assert [o['object_id'] for o in mu['objects']] == [
        o['object_id'] for o in wire_reference['objects']]
    for left, right in zip(mu['objects'], wire_reference['objects']):
        assert left['label'] == right['label']
        assert left['graspable'] == right['graspable']
        # Both backends report the seed pose verbatim (identical positions and
        # identity orientation), so the wire forms match field for field.
        assert left['pose'] == right['pose']

    # Map vocabulary is the same set of names.
    assert mu['known_locations'] == wire_reference['known_locations']

    # Gripper posture (open/empty) matches; poses may differ (skipped).
    assert len(mu['robot']['grippers']) == len(wire_reference['robot']['grippers'])
    for our, ref in zip(mu['robot']['grippers'], wire_reference['robot']['grippers']):
        assert our['side'] == ref['side']
        assert our['state'] == ref['state'] == GripperState.OPEN.value
        assert our['held_object_id'] is None
        assert ref['held_object_id'] is None
        assert our['grasped'] is False
        assert ref['grasped'] is False
