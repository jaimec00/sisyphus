# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Failure paths: every refusal is attributable and leaves the world untouched.

``assert_refused`` compares the *entire* serialized world before and after each
attempt, so these tests prove state is unmutated rather than merely asserting
on the returned status.
"""

from mock_backend_fixtures import assert_refused, run, snapshot
from robot_backends import MockBackend, MockWorld, ObjectSpec, RobotModel
from robot_skills import (
    BACKEND_REFUSAL_CODES,
    ExtendColumn,
    FailureCode,
    Grasp,
    GripperState,
    MoveGripper,
    NavigateTo,
    Place,
    Pose,
    Side,
    SkillStatus,
)


def test_navigate_to_unknown_location(backend):
    """Required failure path: navigate to a location the map does not have."""
    result = assert_refused(
        backend,
        NavigateTo('mars'),
        FailureCode.UNKNOWN_LOCATION,
        reason_contains='mars',
    )
    assert 'kitchen' in result.reason, 'the reason lists what the brain may say instead'
    assert backend.get_observation().robot.location == 'charger'


def test_grasp_missing_object(backend):
    """Required failure path: grasp an object that is not in the scene."""
    run(backend, NavigateTo('kitchen'))
    result = assert_refused(
        backend, Grasp('ghost_1'), FailureCode.UNKNOWN_OBJECT, reason_contains='ghost_1')
    assert 'mug_1' in result.reason, 'the reason lists the objects that do exist'
    for gripper in backend.get_observation().robot.grippers:
        assert gripper.held_object_id is None
        assert gripper.state is GripperState.OPEN


def test_grasp_ungraspable_object(backend):
    """Required failure path: grasp something perceived but not graspable."""
    run(backend, NavigateTo('kitchen'))
    assert backend.get_observation().find_object('counter_1').graspable is False
    assert_refused(
        backend, Grasp('counter_1'), FailureCode.NOT_GRASPABLE, reason_contains='counter_1')


def test_grasp_with_an_occupied_gripper(backend):
    """Required failure path: the named gripper already holds something."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1', Side.LEFT))

    result = assert_refused(
        backend, Grasp('plate_1', Side.LEFT), FailureCode.GRIPPER_OCCUPIED)
    assert 'left' in result.reason and 'mug_1' in result.reason

    # The mug is still held by the left gripper, the plate is still on the counter.
    observation = backend.get_observation()
    assert observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
    assert observation.find_object('plate_1').held_by is None


def test_grasp_when_both_grippers_are_full(backend):
    """With no side named and both hands full, the mock says so explicitly."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1'), Grasp('plate_1'))

    result = assert_refused(backend, Grasp('bowl_1'), FailureCode.GRIPPER_OCCUPIED)
    assert 'both grippers' in result.reason
    assert 'mug_1' in result.reason and 'plate_1' in result.reason


def test_grasp_an_object_already_in_hand(backend):
    """Grasping what is already held is refused with its own code."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1', Side.LEFT))
    assert_refused(
        backend,
        Grasp('mug_1', Side.RIGHT),
        FailureCode.OBJECT_ALREADY_HELD,
        reason_contains='left',
    )


def test_place_with_an_empty_gripper(backend):
    """Required failure path: place while holding nothing."""
    run(backend, NavigateTo('kitchen'))
    target = Pose.from_xyz(2.3, 0.0, 0.95)

    assert_refused(backend, Place(target), FailureCode.GRIPPER_EMPTY)
    assert_refused(
        backend, Place(target, Side.RIGHT), FailureCode.GRIPPER_EMPTY, reason_contains='right')


def test_place_with_the_wrong_gripper_named(backend):
    """Naming the empty hand fails even though the other one is full."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1', Side.LEFT))
    assert_refused(
        backend,
        Place(Pose.from_xyz(2.3, -0.1, 0.95), Side.RIGHT),
        FailureCode.GRIPPER_EMPTY,
        reason_contains='right',
    )
    assert backend.get_observation().robot.gripper(Side.LEFT).held_object_id == 'mug_1'


def test_grasp_out_of_reach(backend):
    """Physical plausibility: you must stand near a thing to pick it up."""
    result = assert_refused(
        backend, Grasp('mug_1'), FailureCode.OUT_OF_REACH, reason_contains='mug_1')
    assert 'charger' in result.reason, 'the reason says where the robot actually is'

    # ...and after navigating there, the very same skill succeeds.
    run(backend, NavigateTo('kitchen'))
    assert backend.execute(Grasp('mug_1')).status is SkillStatus.OK


def test_naming_an_arm_that_cannot_reach_is_still_refused():
    """Reach-aware side selection only applies when the brain leaves side open."""
    backend = _asymmetric_backend()

    result = assert_refused(
        backend, Grasp('cube_1', Side.LEFT), FailureCode.OUT_OF_REACH)
    assert 'left shoulder' in result.reason
    assert backend.execute(Grasp('cube_1', Side.RIGHT)).status is SkillStatus.OK


def test_implicit_side_reports_out_of_reach_when_no_free_arm_can_reach():
    """With the reaching arm already full, the refusal names the arm that is free."""
    backend = _asymmetric_backend()
    run(backend, Grasp('near_1', Side.RIGHT))  # fill the only arm that can reach

    result = assert_refused(backend, Grasp('cube_1'), FailureCode.OUT_OF_REACH)
    assert 'left shoulder' in result.reason, result.reason


def _asymmetric_backend() -> MockBackend:
    """Build a world where cube_1 is reachable by the right arm only."""
    return MockBackend(
        MockWorld(
            locations={'dock': Pose.from_xyz(0.0, 0.0, 0.0)},
            start_location='dock',
            objects=(
                # 0.62 m from the right shoulder, 0.98 m from the left one.
                ObjectSpec('cube_1', 'cube', Pose.from_xyz(0.0, -0.80, 0.80)),
                ObjectSpec('near_1', 'block', Pose.from_xyz(0.30, -0.18, 0.75)),
            ),
        )
    )


def test_move_gripper_out_of_reach(backend):
    """A Cartesian target beyond the arm's envelope is refused, not clamped."""
    run(backend, NavigateTo('kitchen'))
    assert_refused(
        backend,
        MoveGripper(Side.LEFT, Pose.from_xyz(5.0, 0.0, 1.0)),
        FailureCode.OUT_OF_REACH,
    )


def test_place_out_of_reach_keeps_the_object_in_hand(backend):
    """A refused place must not teleport the load or empty the gripper."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1'))
    assert_refused(
        backend, Place(Pose.from_xyz(-5.0, 0.0, 0.5)), FailureCode.OUT_OF_REACH)
    observation = backend.get_observation()
    assert observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
    assert observation.find_object('mug_1').held_by is Side.LEFT


def test_extend_column_out_of_range(backend, world):
    """The column has finite travel; asking beyond it is refused, not clamped."""
    model = world.robot
    assert_refused(backend, ExtendColumn(model.max_column_height + 0.5),
                   FailureCode.OUT_OF_RANGE)
    assert_refused(backend, ExtendColumn(model.min_column_height - 0.5),
                   FailureCode.OUT_OF_RANGE)
    assert backend.get_observation().robot.column_height == world.start_column_height
    assert backend.execute(ExtendColumn(model.max_column_height)).status is SkillStatus.OK


def test_a_long_run_of_failures_leaves_the_world_pristine(backend):
    """Many refusals in a row still add up to no change at all."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1', Side.LEFT))
    before = snapshot(backend)

    for skill, code in (
        (NavigateTo('mars'), FailureCode.UNKNOWN_LOCATION),
        (Grasp('ghost_1'), FailureCode.UNKNOWN_OBJECT),
        (Grasp('counter_1'), FailureCode.NOT_GRASPABLE),
        (Grasp('plate_1', Side.LEFT), FailureCode.GRIPPER_OCCUPIED),
        (Place(Pose.from_xyz(2.3, -0.1, 0.9), Side.RIGHT), FailureCode.GRIPPER_EMPTY),
        (ExtendColumn(9.0), FailureCode.OUT_OF_RANGE),
        (MoveGripper(Side.RIGHT, Pose.from_xyz(9.0, 9.0, 9.0)), FailureCode.OUT_OF_REACH),
    ):
        assert_refused(backend, skill, code)

    assert snapshot(backend) == before


def test_every_mock_refusal_is_owned_by_the_backend_not_the_safety_layer(backend):
    """D17: the mock only ever says "can't be done"; it never clamps or aborts.

    The mock has no dynamic-safety behaviour, so a safety-event code coming out
    of it would mean a code was misclassified or a refusal was mislabelled.
    """
    run(backend, NavigateTo('kitchen'), Grasp('mug_1', Side.LEFT))
    refusals = (
        (NavigateTo('mars'), FailureCode.UNKNOWN_LOCATION),
        (Grasp('ghost_1'), FailureCode.UNKNOWN_OBJECT),
        (Grasp('counter_1'), FailureCode.NOT_GRASPABLE),
        (Grasp('mug_1'), FailureCode.OBJECT_ALREADY_HELD),
        (Grasp('plate_1', Side.LEFT), FailureCode.GRIPPER_OCCUPIED),
        (Place(Pose.from_xyz(2.3, -0.1, 0.9), Side.RIGHT), FailureCode.GRIPPER_EMPTY),
        (ExtendColumn(9.0), FailureCode.OUT_OF_RANGE),
        (MoveGripper(Side.RIGHT, Pose.from_xyz(9.0, 9.0, 9.0)), FailureCode.OUT_OF_REACH),
    )
    for skill, code in refusals:
        result = assert_refused(backend, skill, code)
        assert result.code.is_backend_refusal is True, (
            f'{skill!r} reported {result.code} as a safety event')
        assert result.code.is_safety_event is False

    covered = {code for _, code in refusals}
    assert covered | {FailureCode.UNSUPPORTED_SKILL} == BACKEND_REFUSAL_CODES, (
        'a backend-refusal code exists that no mock path here exercises')


def test_failure_rules_come_from_the_world_not_from_hard_coded_names():
    """A custom world redefines what exists; the same refusals still apply."""
    backend = MockBackend(
        MockWorld(
            locations={'dock': Pose.from_xyz(0.0, 0.0, 0.0)},
            start_location='dock',
            objects=(
                ObjectSpec('cube_1', 'cube', Pose.from_xyz(0.35, 0.18, 0.75)),
                ObjectSpec('crate_1', 'crate', Pose.from_xyz(3.0, 0.0, 0.5)),
            ),
            robot=RobotModel(reach_radius=0.5),
        )
    )
    assert backend.get_observation().known_locations == ('dock',)

    # 'kitchen' exists in the default world but not in this one.
    assert_refused(backend, NavigateTo('kitchen'), FailureCode.UNKNOWN_LOCATION)
    assert_refused(backend, Grasp('mug_1'), FailureCode.UNKNOWN_OBJECT)
    assert_refused(backend, Grasp('crate_1'), FailureCode.OUT_OF_REACH)
    assert backend.execute(Grasp('cube_1')).status is SkillStatus.OK
