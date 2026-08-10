# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the structured observation types."""

from dataclasses import FrozenInstanceError

import pytest
from robot_skills import (
    GripperObservation,
    GripperState,
    Observation,
    Pose,
    RobotState,
    SceneObject,
    Side,
)
from robot_skills.serialization import SerializationError
from skill_api_fixtures import make_gripper, make_observation, make_robot_state


def test_observation_carries_coordinates_not_prose(observation):
    """Objects report id, label, a 3D pose and graspability (invariant 4)."""
    mug = observation.find_object('mug_1')
    assert mug is not None
    assert (mug.object_id, mug.label, mug.graspable) == ('mug_1', 'mug', True)
    assert (mug.pose.position.x, mug.pose.position.y, mug.pose.position.z) == (1.3, 0.2, 0.9)
    assert observation.find_object('nope') is None
    assert observation.objects_with_label('mug') == (mug,)
    assert observation.held_objects() == ()


def test_robot_state_reports_body_and_both_grippers(observation):
    """Robot state carries pose, named location, column height and both grippers."""
    robot = observation.robot
    assert robot.location == 'kitchen'
    assert robot.column_height == 0.4
    assert {gripper.side for gripper in robot.grippers} == set(Side)
    assert robot.gripper(Side.LEFT).state is GripperState.OPEN
    assert robot.gripper(Side.RIGHT).is_holding is False


def test_observations_are_immutable_snapshots(observation):
    """Holding an observation must not let a caller mutate a world model."""
    with pytest.raises(FrozenInstanceError):
        observation.objects = ()
    with pytest.raises(FrozenInstanceError):
        observation.robot.column_height = 1.0
    assert isinstance(observation.objects, tuple)
    assert isinstance(observation.robot.grippers, tuple)


def test_held_object_is_visible_from_both_sides():
    """A held object is reported on the gripper and on the object itself."""
    observation = make_observation(
        robot=make_robot_state(
            grippers=(
                make_gripper(Side.LEFT, GripperState.CLOSED, 'mug_1'),
                make_gripper(Side.RIGHT),
            ),
        ),
        objects=(
            SceneObject('mug_1', 'mug', Pose.from_xyz(0.3, 0.2, 0.8), held_by=Side.LEFT),
        ),
    )
    assert observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
    assert observation.held_objects()[0].held_by is Side.LEFT
    assert observation.find_object('mug_1').is_held is True


def test_grasped_is_a_sensed_load_not_an_alias_of_held_object_id():
    """D19: "something is in the jaws" and "which object" are separate facts.

    A real gripper feels a load it cannot identify, so the two must be able to
    disagree in that direction -- otherwise a backend with force sensing has
    nowhere to report an unidentified grasp.
    """
    empty = make_gripper(Side.LEFT)
    assert (empty.grasped, empty.is_holding) == (False, False)

    unidentified = make_gripper(Side.LEFT, GripperState.CLOSED, grasped=True)
    assert unidentified.grasped is True
    assert unidentified.is_holding is False
    assert unidentified.held_object_id is None

    holding = make_gripper(Side.LEFT, GripperState.CLOSED, 'mug_1')
    assert (holding.grasped, holding.is_holding) == (True, True)


def test_a_gripper_cannot_carry_an_object_it_reports_not_gripping():
    """The impossible direction is rejected: carried implies gripped."""
    with pytest.raises(ValueError, match='while grasped=False'):
        GripperObservation(
            side=Side.LEFT,
            state=GripperState.CLOSED,
            pose=Pose(),
            held_object_id='mug_1',
            grasped=False,
        )


def test_a_payload_without_grasped_infers_it_from_the_load():
    """An additive field must not silently flip the meaning of an older payload.

    Dropping ``grasped`` from a dict that reports carrying an object must not
    parse as "empty jaws holding a mug"; the world-model fact still implies it.
    """
    holding = make_gripper(Side.LEFT, GripperState.CLOSED, 'mug_1').to_dict()
    del holding['grasped']
    assert GripperObservation.from_dict(holding).grasped is True

    empty = make_gripper(Side.RIGHT).to_dict()
    del empty['grasped']
    assert GripperObservation.from_dict(empty).grasped is False

    with pytest.raises(SerializationError, match='expected a boolean'):
        GripperObservation.from_dict({**empty, 'grasped': 'yes'})


def test_a_gripper_holding_an_object_the_scene_disagrees_about_is_rejected():
    """Direction 1: the gripper claims a load the object list does not confirm."""
    holding_left = make_robot_state(
        grippers=(
            make_gripper(Side.LEFT, GripperState.CLOSED, 'mug_1'),
            make_gripper(Side.RIGHT),
        ),
    )
    # ...the object is not perceived at all.
    with pytest.raises(ValueError, match='not among the perceived objects'):
        make_observation(robot=holding_left, objects=())
    # ...the object is perceived but thinks nobody holds it.
    with pytest.raises(ValueError, match='held_by=None'):
        make_observation(
            robot=holding_left,
            objects=(SceneObject('mug_1', 'mug', Pose()),),
        )
    # ...the object thinks the *other* gripper holds it.
    with pytest.raises(ValueError, match='held_by=right'):
        make_observation(
            robot=holding_left,
            objects=(SceneObject('mug_1', 'mug', Pose(), held_by=Side.RIGHT),),
        )


def test_an_object_claiming_a_holder_that_holds_nothing_is_rejected():
    """Direction 2: the object claims a holder whose gripper disagrees."""
    with pytest.raises(ValueError, match='that gripper reports holding None'):
        make_observation(
            robot=make_robot_state(),  # both grippers empty
            objects=(SceneObject('mug_1', 'mug', Pose(), held_by=Side.LEFT),),
        )
    with pytest.raises(ValueError, match="reports holding 'plate_1'"):
        make_observation(
            robot=make_robot_state(
                grippers=(
                    make_gripper(Side.LEFT, GripperState.CLOSED, 'plate_1'),
                    make_gripper(Side.RIGHT),
                ),
            ),
            objects=(
                SceneObject('mug_1', 'mug', Pose(), held_by=Side.LEFT),
                SceneObject('plate_1', 'plate', Pose(), held_by=Side.LEFT),
            ),
        )


def test_the_held_object_invariant_survives_a_round_trip():
    """A consistent pair round-trips; a doctored dict is caught on parse."""
    consistent = make_observation(
        robot=make_robot_state(
            grippers=(
                make_gripper(Side.LEFT, GripperState.CLOSED, 'mug_1'),
                make_gripper(Side.RIGHT),
            ),
        ),
        objects=(SceneObject('mug_1', 'mug', Pose(), held_by=Side.LEFT),),
    )
    assert Observation.from_dict(consistent.to_dict()) == consistent

    # A doctored payload is caught on parse, and -- because a caller at a
    # transport boundary catches one exception type -- as a SerializationError,
    # not as the bare ValueError the constructor raises.
    doctored = consistent.to_dict()
    doctored['objects'][0]['held_by'] = None
    with pytest.raises(SerializationError, match='held_by=None'):
        Observation.from_dict(doctored)


def test_robot_state_requires_exactly_one_gripper_per_side():
    """A two-arm robot always reports two grippers, one per side."""
    with pytest.raises(ValueError, match='one entry per side'):
        make_robot_state(grippers=(make_gripper(Side.LEFT),))
    with pytest.raises(ValueError, match='one entry per side'):
        make_robot_state(grippers=(make_gripper(Side.LEFT), make_gripper(Side.LEFT)))
    with pytest.raises(KeyError):
        RobotState(
            pose=Pose(),
            column_height=0.0,
            grippers=(make_gripper(Side.LEFT), make_gripper(Side.RIGHT)),
        ).gripper('left')


def test_observation_rejects_duplicate_object_ids():
    """Object ids are the brain's handles; duplicates would be ambiguous."""
    duplicate = SceneObject('mug_1', 'mug', Pose())
    with pytest.raises(ValueError, match='duplicate object_id'):
        make_observation(objects=(duplicate, duplicate))


def test_member_type_validation():
    """Observation members are typed; loose tuples and strings are rejected."""
    with pytest.raises(TypeError):
        make_observation(robot=None)
    with pytest.raises(TypeError):
        make_observation(objects=({'object_id': 'mug_1'},))
    with pytest.raises(TypeError):
        SceneObject('mug_1', 'mug', pose=(0.0, 0.0, 0.0))
    with pytest.raises(TypeError):
        SceneObject('mug_1', 'mug', Pose(), graspable='yes')
    with pytest.raises(TypeError):
        SceneObject('mug_1', 'mug', Pose(), held_by='left')
    with pytest.raises(TypeError):
        GripperObservation(side='left', state=GripperState.OPEN, pose=Pose())
    with pytest.raises(TypeError):
        GripperObservation(side=Side.LEFT, state='open', pose=Pose())
    with pytest.raises(TypeError):
        GripperObservation(
            side=Side.LEFT, state=GripperState.OPEN, pose=Pose(), grasped='yes')


def test_observation_round_trip(round_trip, observation):
    """Observations and their nested members survive dict/JSON round trips."""
    round_trip(observation)
    round_trip(observation.robot)
    round_trip(observation.robot.gripper(Side.RIGHT))
    round_trip(observation.find_object('counter_1'))
    round_trip(make_gripper(Side.LEFT, GripperState.CLOSED, grasped=True))


def test_observation_round_trip_preserves_every_field(observation):
    """Field-by-field check that nothing is silently dropped or defaulted."""
    rebuilt = Observation.from_dict(observation.to_dict())
    assert rebuilt.known_locations == observation.known_locations
    assert rebuilt.robot.location == observation.robot.location
    assert rebuilt.robot.pose == observation.robot.pose
    assert rebuilt.robot.column_height == observation.robot.column_height
    assert rebuilt.robot.grippers == observation.robot.grippers
    assert rebuilt.objects == observation.objects


def test_observation_parsing_is_strict():
    """Malformed observation dicts raise instead of yielding partial state."""
    good = make_observation().to_dict()
    with pytest.raises(SerializationError, match='missing required key'):
        Observation.from_dict({'objects': []})
    with pytest.raises(SerializationError, match='unknown key'):
        Observation.from_dict({**good, 'weather': 'sunny'})
    with pytest.raises(SerializationError, match='expected a list'):
        Observation.from_dict({**good, 'objects': 'a mug'})
    with pytest.raises(SerializationError, match='expected strings'):
        Observation.from_dict({**good, 'known_locations': [7]})
    with pytest.raises(SerializationError, match='not a valid GripperState'):
        broken = {**good}
        broken['robot'] = {**good['robot']}
        broken['robot']['grippers'] = [
            {**good['robot']['grippers'][0], 'state': 'ajar'},
            good['robot']['grippers'][1],
        ]
        Observation.from_dict(broken)
