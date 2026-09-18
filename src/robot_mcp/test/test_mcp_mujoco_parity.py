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

Since PR4 every legal skill is implemented on the MuJoCo backend, so the
refusal this file round-trips is an ``out_of_reach`` grasp (mug_1 from the
charger), not an ``unsupported_skill
"""

from mcp_fixtures import connected, payload
import pytest
from robot_backends import MockBackend, MuJoCoBackend
from robot_safety import SafetyLimits
from robot_skills import (
    GripperState,
    Observation,
    SkillResult,
)

pytestmark = pytest.mark.anyio

#: The column's clamped travel ceiling, from the shipped safety limits (the
#: same source the router's safety gate clamps against).
_COLUMN_MAX = SafetyLimits.defaults().column.max_height


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
    """A MuJoCo ``execute`` refusal (an out-of-reach grasp) parses (PR4).

    Grasping ``mug_1`` from the charger is ``out_of_reach`` on both backends
    (the kitchen trio stays > 0.85 m from the charger shoulders, status.md R6),
    so this is a real backend refusal from a legal skill -- every skill is now
    implemented -- and it must round-trip under the shared schema.
    """
    async with connected(MuJoCoBackend()) as client:
        result = payload(
            await client.call_tool('grasp', {'object_id': 'mug_1'}))

    _assert_result_parses(result)
    assert result['status'] == 'failed'
    assert result['code'] == 'out_of_reach'
    # Grasp carries an optional ``side`` that defaults to None on the round
    # trip, so pin the discriminant + object rather than the whole dict.
    assert result['skill']['skill'] == 'grasp'
    assert result['skill']['object_id'] == 'mug_1'


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


async def test_mcp_extend_column_overreach_is_clamped_not_refused():
    """Over MCP the safety layer clamps an over-reach; the backend never sees 2.0.

    Mirrors Mock's ``test_the_default_server_clamps_a_column_command_mid_run``:
    the router's safety gate rewrites an out-of-range ``extend_column`` to the
    travel ceiling before the backend runs, so it comes back ``ok`` (not a
    backend ``OUT_OF_RANGE`` refusal), the executed skill height is the clamped
    maximum, the observation reports the column genuinely at that height, and
    the reason mentions the clamp.  An in-range height passes through with no
    reason and lands exactly where commanded.
    """
    async with connected(MuJoCoBackend()) as client:
        overreached = payload(
            await client.call_tool('extend_column', {'height': 2.0}))
        in_range = payload(
            await client.call_tool('extend_column', {'height': 0.3}))

    # The over-reach is clamped to the safety maximum, never refused.
    _assert_result_parses(overreached)
    assert overreached['status'] == 'ok'
    assert overreached['skill'] == {
        'skill': 'extend_column', 'height': _COLUMN_MAX}
    assert overreached['observation']['robot']['column_height'] == pytest.approx(
        _COLUMN_MAX)
    assert 'clamped' in overreached['reason']

    # The in-range height passes through unchanged (no informational reason).
    _assert_result_parses(in_range)
    assert in_range['status'] == 'ok'
    assert in_range['skill'] == {'skill': 'extend_column', 'height': 0.3}
    assert in_range['reason'] is None
    assert in_range['observation']['robot']['column_height'] == pytest.approx(0.3)
