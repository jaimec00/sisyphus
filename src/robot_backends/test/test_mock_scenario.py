# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""End-to-end composition: the fetch-the-mug loop the brain will drive."""

import json

from mock_backend_fixtures import (
    assert_pose_close,
    snapshot,
    TABLE_DROP_X,
    TABLE_DROP_Y,
    TABLE_DROP_Z,
)
from robot_backends import MockBackend
from robot_skills import (
    ExtendColumn,
    Grasp,
    GripperState,
    NavigateTo,
    Observation,
    Place,
    Pose,
    Side,
    SkillResult,
    SkillStatus,
)

SCENARIO = (
    NavigateTo('kitchen'),
    Grasp('mug_1'),
    NavigateTo('table'),
    Place(Pose.from_xyz(TABLE_DROP_X, TABLE_DROP_Y, TABLE_DROP_Z)),
)


def test_navigate_grasp_navigate_place_scenario(backend, world):
    """The brief's scenario: every step returns ok and the world ends as expected."""
    results = []
    for skill in SCENARIO:
        result = backend.execute(skill)
        assert result.status is SkillStatus.OK, f'{skill!r} failed: {result.reason}'
        results.append(result)

    final = results[-1].observation
    assert final == backend.get_observation()

    # The robot is at the table, empty-handed, both grippers open.
    assert final.robot.location == 'table'
    assert final.robot.pose == world.locations['table']
    assert final.held_objects() == ()
    for gripper in final.robot.grippers:
        assert gripper.held_object_id is None
    assert final.robot.gripper(Side.LEFT).state is GripperState.OPEN

    # The mug moved from the kitchen counter to the requested spot on the table.
    mug = final.find_object('mug_1')
    assert_pose_close(mug.pose, Pose.from_xyz(TABLE_DROP_X, TABLE_DROP_Y, TABLE_DROP_Z))
    assert mug.held_by is None
    assert mug.graspable is True

    # Nothing else in the scene moved.
    initial = MockBackend(world).get_observation()
    for item in final.objects:
        if item.object_id != 'mug_1':
            assert item == initial.find_object(item.object_id)


def test_scenario_intermediate_states_are_visible_step_by_step(backend):
    """Each step's observation shows the loop actually progressing."""
    after_navigate = backend.execute(SCENARIO[0]).observation
    assert after_navigate.robot.location == 'kitchen'
    assert after_navigate.find_object('mug_1').held_by is None

    after_grasp = backend.execute(SCENARIO[1]).observation
    assert after_grasp.find_object('mug_1').held_by is Side.LEFT
    assert after_grasp.robot.gripper(Side.LEFT).state is GripperState.CLOSED

    carried = backend.execute(SCENARIO[2]).observation
    assert carried.robot.location == 'table'
    assert carried.find_object('mug_1').held_by is Side.LEFT
    assert carried.find_object('mug_1').pose == carried.robot.gripper(Side.LEFT).pose

    placed = backend.execute(SCENARIO[3]).observation
    assert placed.find_object('mug_1').held_by is None


def test_scenario_survives_a_json_round_trip_at_every_step(backend):
    """The whole loop is transport-ready: results and observations are JSON."""
    for skill in SCENARIO:
        result = backend.execute(skill)
        text = json.dumps(result.to_dict())
        rebuilt = SkillResult.from_dict(json.loads(text))
        assert rebuilt == result
        assert Observation.from_dict(
            json.loads(json.dumps(result.observation.to_dict()))) == result.observation


def test_reset_restores_the_seed_world(backend):
    """reset() undoes an arbitrary amount of history, exactly."""
    initial = snapshot(backend)
    for skill in (*SCENARIO, ExtendColumn(1.1)):
        backend.execute(skill)
    assert snapshot(backend) != initial

    observation = backend.reset()

    assert observation.to_dict() == initial
    assert snapshot(backend) == initial


def test_two_backends_are_bit_identical_given_the_same_skills():
    """Determinism: no clock, no RNG, so runs are reproducible."""
    first, second = MockBackend(), MockBackend()
    for skill in (*SCENARIO, ExtendColumn(0.8), Grasp('book_1')):
        result_a = first.execute(skill)
        result_b = second.execute(skill)
        assert result_a.to_dict() == result_b.to_dict()
    assert snapshot(first) == snapshot(second)


def test_a_second_run_after_reset_reproduces_the_first(backend):
    """The same backend replayed from reset produces the same observations."""
    first_pass = [backend.execute(skill).to_dict() for skill in SCENARIO]
    backend.reset()
    second_pass = [backend.execute(skill).to_dict() for skill in SCENARIO]
    assert first_pass == second_pass
