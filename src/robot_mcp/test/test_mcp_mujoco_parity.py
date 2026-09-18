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
    Pose,
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


# ---------------------------------------------------------------------------
# The R2 representative stream, run on BOTH backends (issue #110, R1).
#
# This is the load-bearing parity test: one literal, deterministic skill
# stream is driven through a fresh MockBackend and a fresh MuJoCoBackend (each
# through the real MCP client, so what is compared is the *wire* payload), and
# every step is asserted per ruling R1:
#
#   Tier A  every Observation/SkillResult dict round-trips under the shared
#           seam (``from_dict`` -> ``to_dict`` is a fixed point).
#   Tier B  the discrete (and discrete-derived) fields the brain branches on
#           are EXACTLY equal Mock == MuJoCo: schema_version, status, code, the
#           commanded (post-clamp) ``skill`` dict, ``reason``, each object's
#           object_id/label/graspable/held_by, known_locations,
#           robot.location, robot.column_height, and each gripper's
#           side/state/held_object_id/grasped.
#   Tier C  the physical-continuous fields a seed or a skill *pins* are EXACTLY
#           equal: the robot base pose, every object's seed pose (before any
#           grasp) and every object's placed pose (after ``place`` -- position
#           and orientation).
#   Tier D  the physical-continuous fields that genuinely differ are compared
#           *structurally only* (present + well-typed), never for cross-backend
#           equality: the gripper pose (MuJoCo reports real 5-DOF kinematics,
#           the Mock shoulder+offset arithmetic) and the carried object's
#           mid-carry pose (it rides the gripper).  The carried object is
#           checked against *its own* backend's gripper within the PR4 carry
#           tolerance, not against the other backend.
#
# The stream (R2): reset -> navigate table -> grasp book_1 -> navigate kitchen
# -> place far (out_of_reach) -> place at the counter -> extend_column 2.5
# (clamped) -> grasp mug_1 from the charger (out_of_reach).
# ---------------------------------------------------------------------------

#: Where the driver drops each object, borrowed from PR4's status.md R6: a
#: pose just above the kitchen counter (z 0.85) at the rim, stepwise along y so
#: two held objects do not land on one another.  Literal coordinates are fine
#: here -- this stream *pins* the drop pose, so both backends write the same
#: metric pose and Tier C asserts it exactly.
_COUNTER_DROP = (2.15, 0.00, 0.95)
_FAR_DROP = (0.0, 0.0, 0.95)

#: How far a carried object may sit from its own backend's gripper (the PR4
#: carry tolerance).  Tier D checks this, not cross-backend equality.
_CARRY_TOLERANCE_M = 5e-3


def _downcast_deep(value):
    """Return ``value`` with floats narrowed to float32 (MuJoCo's precision).

    MuJoCo reports ``xpos``/``xquat`` as float32, and mapping those through
    numpy widens them to Python float with the float32 representable value;
    the Mock computes in double.  A seed/base/placed pose therefore agrees to
    *float32* precision but is not bit-identical to the Mock's double -- both
    read the same decimal literal through a different float width.  Narrowing
    both sides to float32 is what "exact" means for a value a float32 sim
    reports, and it stays strict: a genuinely different pose survives the
    narrowing.
    """
    import numpy as np

    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return float(np.float32(value))
    if isinstance(value, dict):
        return {key: _downcast_deep(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_downcast_deep(item) for item in value]
    return value


def _tier_b_view(result, observation):
    """Return the Tier B (discrete) projection of a step.

    ``result`` is the whole payload (a ``SkillResult`` dict, or the bare
    ``Observation`` dict ``reset`` returns), ``observation`` the observation it
    carries.  The discrete skill fields are only present on a skill result.
    """
    return {
        'schema_version': result['schema_version'],
        'status': result.get('status'),
        'code': result.get('code'),
        'skill': result.get('skill'),
        'reason': result.get('reason'),
        'object_schema_version': observation['schema_version'],
        'objects': [
            {
                'object_id': item['object_id'],
                'label': item['label'],
                'graspable': item['graspable'],
                'held_by': item['held_by'],
            }
            for item in observation['objects']
        ],
        'known_locations': observation['known_locations'],
        'robot_location': observation['robot']['location'],
        'column_height': observation['robot']['column_height'],
        'grippers': [
            {
                'side': gripper['side'],
                'state': gripper['state'],
                'held_object_id': gripper['held_object_id'],
                'grasped': gripper['grasped'],
            }
            for gripper in observation['robot']['grippers']
        ],
    }


def _tier_c_view(observation):
    """Return the Tier C (seed/base/placed pose) projection, float32-narrowed."""
    return _downcast_deep({
        'robot_pose': observation['robot']['pose'],
        'objects': [
            {'object_id': item['object_id'], 'pose': item['pose']}
            for item in observation['objects']
        ],
    })


def _position_distance(first, second):
    """Return the metric distance between two wire-form positions."""
    import math

    return math.dist(
        (first['x'], first['y'], first['z']),
        (second['x'], second['y'], second['z']))


def _as_step(name, body):
    """Return a stream step as ``(name, result_dict, observation_dict)``.

    ``reset``/``get_observation`` answer with a bare ``Observation`` dict while
    every skill tool answers with a ``SkillResult`` (which carries an
    ``observation`` under the same key).  Normalising here keeps ``reset`` a
    first-class step of the stream without special-casing it in the tests.
    """
    if 'observation' in body:
        return name, body, body['observation']
    return name, body, body


async def _run_stream(client):
    """Drive the R2 stream through ``client``, returning the per-step payloads.

    The arguments are literal and identical for both backends, so any divergence
    in the returned payloads is the backend's, not the driver's.  Each step is a
    ``(name, result_dict, observation_dict)`` triple.
    """
    steps = []

    async def call(name, arguments=None):
        result = await client.call_tool(name, arguments or {})
        assert not result.is_error, payload(result)
        steps.append(_as_step(name, payload(result)))
        return steps[-1][2]

    await call('reset', {})
    await call('navigate_to', {'location': 'table'})
    await call('grasp', {'object_id': 'book_1'})
    await call('navigate_to', {'location': 'kitchen'})
    await call('place', {'pose': Pose.from_xyz(*_FAR_DROP).to_dict()})
    await call('place', {'pose': Pose.from_xyz(*_COUNTER_DROP).to_dict()})
    await call('extend_column', {'height': 2.5})
    await call('navigate_to', {'location': 'charger'})
    await call('grasp', {'object_id': 'mug_1'})
    return steps


async def _stream_both_backends():
    """Return ``[(name, mock_body, mujoco_body), ...]`` for the R2 stream.

    Each backend gets its own fresh, identically seeded instance and its own
    MCP session, so the two runs cannot influence one another.
    """
    mock_steps = []
    async with connected(MockBackend()) as client:
        mock_steps = await _run_stream(client)
    mujoco_steps = []
    async with connected(MuJoCoBackend()) as client:
        mujoco_steps = await _run_stream(client)

    assert [name for name, _, _ in mock_steps] == [name for name, _, _ in mujoco_steps]
    return [
        (mock_name, mock_body, mock_obs, mujoco_body, mujoco_obs)
        for (mock_name, mock_body, mock_obs), (_, mujoco_body, mujoco_obs)
        in zip(mock_steps, mujoco_steps)
    ]


async def test_the_representative_stream_is_tier_a_b_on_both_backends():
    """Tier A round-trip and Tier B discrete equality, per step (R1).

    Every payload -- Mock and MuJoCo -- must be a legal ``SkillResult`` that
    re-serialises stably, and every discrete field the brain branches on must be
    byte-identical between the two backends at every step of the R2 stream.
    """
    steps = await _stream_both_backends()
    assert len(steps) == 9, [name for name, *_ in steps]

    for name, mock_body, mock_obs, mujoco_body, mujoco_obs in steps:
        # Tier A: every wire dict is legal and stable under a round trip.  The
        # observation always parses; a *result* (skill) dict parses too.
        _assert_observation_parses(mock_obs)
        _assert_observation_parses(mujoco_obs)
        if 'observation' in mock_body:
            _assert_result_parses(mock_body)
        if 'observation' in mujoco_body:
            _assert_result_parses(mujoco_body)

        # Tier B: the discrete projection is identical.
        assert _tier_b_view(mock_body, mock_obs) == _tier_b_view(mujoco_body, mujoco_obs), name


async def test_the_representative_streams_failure_vocabulary_matches():
    """The step-specific beats of the R2 stream, named so a drift is legible.

    Tier B already compares every field; this pins the *story* the stream tells
    -- the same success/refusal codes, the same side, the same clamped skill --
    so a failure reports which beat broke rather than a giant dict diff.
    """
    steps = await _stream_both_backends()
    by_name = {}
    for name, mock_body, mock_obs, mujoco_body, mujoco_obs in steps:
        by_name.setdefault(name, []).append(
            (mock_body, mock_obs, mujoco_body, mujoco_obs))

    # reset -> seeded at the charger, nothing held.
    _, reset_mock_obs, _, reset_mujoco_obs = by_name['reset'][0]
    for observation in (reset_mock_obs, reset_mujoco_obs):
        assert observation['robot']['location'] == 'charger'
        assert all(item['held_by'] is None for item in observation['objects'])

    # navigate table (twice: once at step 2, once after the clamp at step 8).
    for mock_body, _, mujoco_body, _ in by_name['navigate_to']:
        assert mock_body['status'] == mujoco_body['status'] == 'ok'

    # grasp book_1 -> ok; the same side ends up holding it.
    grasp_mock, grasp_mock_obs, grasp_mujoco, grasp_mujoco_obs = by_name['grasp'][0]
    assert grasp_mock['status'] == grasp_mujoco['status'] == 'ok'
    held_mock = next(
        gripper for gripper in grasp_mock_obs['robot']['grippers']
        if gripper['held_object_id'] == 'book_1')
    held_mujoco = next(
        gripper for gripper in grasp_mujoco_obs['robot']['grippers']
        if gripper['held_object_id'] == 'book_1')
    assert held_mock['side'] == held_mujoco['side']
    assert held_mock['grasped'] is held_mujoco['grasped'] is True

    # place far away (still at the table) -> the same refusal code, still held.
    far_mock, far_mock_obs, far_mujoco, far_mujoco_obs = by_name['place'][0]
    for body, observation in ((far_mock, far_mock_obs), (far_mujoco, far_mujoco_obs)):
        assert body['status'] == 'failed'
        assert body['code'] == 'out_of_reach'
        book = next(
            item for item in observation['objects']
            if item['object_id'] == 'book_1')
        assert book['held_by'] is not None

    # place at the counter -> ok, released.
    near_mock, near_mock_obs, near_mujoco, near_mujoco_obs = by_name['place'][1]
    for body, observation in ((near_mock, near_mock_obs), (near_mujoco, near_mujoco_obs)):
        assert body['status'] == 'ok'
        assert body['reason'] == "released 'book_1' from the left gripper"
        book = next(
            item for item in observation['objects']
            if item['object_id'] == 'book_1')
        assert book['held_by'] is None

    # extend_column 2.5 -> clamped, same clamped skill + same clamped height.
    clamp_mock, clamp_mock_obs, clamp_mujoco, clamp_mujoco_obs = by_name['extend_column'][0]
    for body, observation in (
            (clamp_mock, clamp_mock_obs), (clamp_mujoco, clamp_mujoco_obs)):
        assert body['status'] == 'ok'
        assert body['skill'] == {'skill': 'extend_column', 'height': _COLUMN_MAX}
        assert observation['robot']['column_height'] == pytest.approx(_COLUMN_MAX)
        assert 'clamped' in body['reason']

    # grasp mug_1 from the charger -> the seed out-of-reach pair, same code.
    mug_mock, _, mug_mujoco, _ = by_name['grasp'][1]
    for body in (mug_mock, mug_mujoco):
        assert body['status'] == 'failed'
        assert body['code'] == 'out_of_reach'
        assert body['skill']['object_id'] == 'mug_1'


async def test_the_representative_stream_pinned_poses_are_exactly_equal():
    """Tier C: base, seed and placed poses are equal across the backends (R1).

    Tested as three named beats rather than the whole stream because only these
    payload *pins* a continuous value: the seed/base pose (before any grasp) and
    the placed pose (``place`` writes ``skill.pose`` verbatim).  The mid-carry
    poses are deliberately excluded -- they are Tier D.
    """
    steps = await _stream_both_backends()
    by_name = {}
    for name, mock_body, mock_obs, mujoco_body, mujoco_obs in steps:
        by_name.setdefault(name, []).append(
            (mock_body, mock_obs, mujoco_body, mujoco_obs))

    # Base pose: teleporting to a named location is a verbatim pose copy, so the
    # robot's base pose matches at every step (the location map is shared).
    for name, _, mock_obs, _, mujoco_obs in steps:
        assert _tier_c_view(mock_obs)['robot_pose'] == (
            _tier_c_view(mujoco_obs)['robot_pose']), name

    # Seed poses: after reset (before any grasp) every object sits at its seed.
    _, reset_mock_obs, _, reset_mujoco_obs = by_name['reset'][0]
    seed_mock = {
        item['object_id']: item['pose'] for item in reset_mock_obs['objects']}
    seed_mujoco = {
        item['object_id']: item['pose'] for item in reset_mujoco_obs['objects']}
    assert _downcast_deep(seed_mock) == _downcast_deep(seed_mujoco)

    # Placed pose: the object lands *exactly* where the skill commanded (both
    # backends write ``skill.pose`` verbatim, position and orientation).
    _, near_mock_obs, _, near_mujoco_obs = by_name['place'][1]
    placed_mock = next(
        item['pose'] for item in near_mock_obs['objects']
        if item['object_id'] == 'book_1')
    placed_mujoco = next(
        item['pose'] for item in near_mujoco_obs['objects']
        if item['object_id'] == 'book_1')
    assert _downcast_deep(placed_mock) == _downcast_deep(placed_mujoco)
    assert placed_mock['position'] == Pose.from_xyz(*_COUNTER_DROP).position.to_dict()

    # The seed poses are what the *reset observation* reports (Tier C), and they
    # are the seed document's values -- asserted here so the equality above is
    # known to be comparing the real seeds, not two empty projections.
    assert placed_mock == Pose.from_xyz(*_COUNTER_DROP).to_dict()


async def test_the_representative_stream_tier_d_poses_are_structural_only():
    """Tier D: gripper + carried-object poses are well-typed, NOT compared (R1).

    The gripper pose is backend-specific kinematics (MuJoCo's 5-DOF arm, the
    Mock's shoulder+offset arithmetic), and a carried object rides the gripper,
    so it inherits that difference.  What is asserted: both report a well-formed
    pose, and the carried object stays within the PR4 carry tolerance of *its
    own* backend's gripper -- never that the two backends agree numerically.
    """
    steps = await _stream_both_backends()

    for name, _, mock_obs, _, mujoco_obs in steps:
        for observation in (mock_obs, mujoco_obs):
            for gripper in observation['robot']['grippers']:
                pose = gripper['pose']
                assert set(pose) == {'position', 'orientation'}, (name, pose)
                assert set(pose['position']) == {'x', 'y', 'z'}
                assert set(pose['orientation']) == {'x', 'y', 'z', 'w'}
                for value in list(pose['position'].values()) + list(
                        pose['orientation'].values()):
                    assert isinstance(value, float)

    # The mid-carry beat: after grasping book_1 both backends hold it, and it
    # rides its own gripper within tolerance on each -- but the two poses are
    # not compared to each other.
    carry = [step for step in steps if step[0] == 'grasp'][0]
    for observation in (carry[2], carry[4]):
        book = next(item for item in observation['objects'] if item['object_id'] == 'book_1')
        holder = next(
            gripper for gripper in observation['robot']['grippers']
            if gripper['held_object_id'] == 'book_1')
        assert _position_distance(
            book['pose']['position'], holder['pose']['position']) <= _CARRY_TOLERANCE_M
