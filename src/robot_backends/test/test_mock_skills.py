# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Per-skill round trips: each skill's effect shows up in the next observation."""

from mock_backend_fixtures import assert_pose_close, run, snapshot
import pytest
from robot_backends import MockBackend, MockWorld, ObjectSpec
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    Grasp,
    GripperState,
    MoveGripper,
    NavigateTo,
    OpenGripper,
    Place,
    Point,
    Pose,
    Quaternion,
    Side,
    SkillStatus,
)


def test_initial_observation_matches_the_seed_world(backend, world):
    """A fresh backend reports exactly what the world spec describes."""
    observation = backend.get_observation()
    assert observation.robot.location == 'charger'
    assert observation.robot.pose == world.start_pose
    assert observation.robot.column_height == world.start_column_height
    assert observation.known_locations == tuple(sorted(world.locations))
    assert [item.object_id for item in observation.objects] == sorted(
        spec.object_id for spec in world.objects)
    for gripper in observation.robot.grippers:
        assert gripper.state is GripperState.OPEN
        assert gripper.held_object_id is None
    assert observation.held_objects() == ()


def test_navigate_to_moves_the_base_and_updates_the_named_location(backend, world):
    """navigate_to: the reported location and metric pose both follow."""
    result = backend.execute(NavigateTo('kitchen'))
    assert result.status is SkillStatus.OK
    assert result.observation.robot.location == 'kitchen'
    assert result.observation.robot.pose == world.locations['kitchen']

    # The result's observation is the same world the next query reports.
    assert backend.get_observation() == result.observation

    again = backend.execute(NavigateTo('kitchen'))
    assert again.status is SkillStatus.OK
    assert 'already at' in again.reason


@pytest.mark.parametrize(
    'target',
    [
        # Well-scaled against the left shoulder at (2.0, 0.18, 0.8).
        Pose(Point(2.2, 0.25, 1.0), Quaternion(0.0, 0.0, 0.7071, 0.7071)),
        # Badly scaled: 0.1 - 0.8 + 0.8 != 0.1 in binary floating point, so this
        # case pins the *approximate* contract the offset-from-shoulder model
        # actually offers.
        Pose(Point(2.0, 0.20, 0.1), Quaternion(0.0, 0.0, 0.7071, 0.7071)),
    ],
    ids=['well_scaled', 'badly_scaled'],
)
def test_move_gripper_puts_that_gripper_at_the_requested_pose(backend, target):
    """move_gripper: the commanded pose is what the observation reports back."""
    run(backend, NavigateTo('kitchen'))
    other_before = backend.get_observation().robot.gripper(Side.RIGHT)

    result = backend.execute(MoveGripper(Side.LEFT, target))

    assert result.status is SkillStatus.OK, result.reason
    assert_pose_close(result.observation.robot.gripper(Side.LEFT).pose, target)
    assert result.observation.robot.gripper(Side.RIGHT) == other_before


@pytest.mark.parametrize(
    ('side', 'object_id'),
    [(Side.LEFT, 'mug_1'), (Side.RIGHT, 'plate_1')],
)
def test_grasp_attaches_an_object_to_the_named_gripper(backend, side, object_id):
    """grasp: both arms work, and the object is reported held from both views."""
    run(backend, NavigateTo('kitchen'))

    result = backend.execute(Grasp(object_id, side))

    assert result.status is SkillStatus.OK
    gripper = result.observation.robot.gripper(side)
    assert gripper.state is GripperState.CLOSED
    assert gripper.held_object_id == object_id
    item = result.observation.find_object(object_id)
    assert item.held_by is side
    assert item.pose == gripper.pose, 'a held object sits in the gripper'
    assert result.observation.held_objects() == (item,)


def test_grasp_without_a_side_fills_the_left_gripper_then_the_right(backend):
    """grasp: an unspecified side resolves deterministically, left first."""
    run(backend, NavigateTo('kitchen'))
    first = backend.execute(Grasp('mug_1'))
    second = backend.execute(Grasp('plate_1'))

    assert first.observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
    assert second.observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'
    assert second.observation.robot.gripper(Side.RIGHT).held_object_id == 'plate_1'


def test_grasp_without_a_side_prefers_a_gripper_that_can_actually_reach():
    """An object only the right arm can reach is grasped with the right arm.

    The shoulders are 0.36 m apart against a 0.85 m reach, so "first free arm"
    alone would refuse a grasp the robot can plainly do.
    """
    backend = MockBackend(
        MockWorld(
            locations={'dock': Pose.from_xyz(0.0, 0.0, 0.0)},
            start_location='dock',
            objects=(
                # 0.62 m from the right shoulder, 0.98 m from the left one.
                ObjectSpec('cube_1', 'cube', Pose.from_xyz(0.0, -0.80, 0.80)),
            ),
        )
    )
    observation = backend.get_observation()
    left = observation.robot.gripper(Side.LEFT).pose.position  # sanity: arms differ
    assert left.y > observation.robot.gripper(Side.RIGHT).pose.position.y

    result = backend.execute(Grasp('cube_1'))

    assert result.status is SkillStatus.OK, result.reason
    assert result.observation.robot.gripper(Side.RIGHT).held_object_id == 'cube_1'
    assert result.observation.robot.gripper(Side.LEFT).held_object_id is None
    assert result.observation.find_object('cube_1').held_by is Side.RIGHT


def test_implicit_side_prefers_the_left_arm_even_when_the_right_is_nearer(backend, world):
    """SIDE_ORDER preference, not proximity, decides which arm grasps.

    `plate_1` is strictly nearer the right shoulder and comfortably inside both
    arms' reach, so a 'nearest reachable arm' rule would pick the right one.
    Only left-preference explains the left gripper ending up with it — which is
    what makes this test able to fail if that rule is ever swapped in.
    """
    run(backend, NavigateTo('kitchen'))
    observation = backend.get_observation()
    model = world.robot
    plate = observation.find_object('plate_1').pose.position
    distance = {
        side: plate.distance_to(
            model.shoulder(observation.robot.pose, observation.robot.column_height, side))
        for side in (Side.LEFT, Side.RIGHT)
    }
    # Guard the premise: the case only discriminates while both arms can reach
    # and the right one is strictly nearer.
    assert max(distance.values()) < model.reach_radius, distance
    assert distance[Side.RIGHT] < distance[Side.LEFT], distance

    result = backend.execute(Grasp('plate_1'))

    assert result.status is SkillStatus.OK, result.reason
    assert result.observation.robot.gripper(Side.LEFT).held_object_id == 'plate_1'
    assert result.observation.robot.gripper(Side.RIGHT).held_object_id is None

    # ...and the nearer arm genuinely could have taken it.
    fresh = MockBackend(world)
    run(fresh, NavigateTo('kitchen'))
    assert fresh.execute(Grasp('plate_1', Side.RIGHT)).status is SkillStatus.OK


def test_a_held_object_travels_with_the_robot(backend, world):
    """Carrying is modelled: navigating moves the load, the shelf stays put."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1'))
    plate_before = backend.get_observation().find_object('plate_1')

    result = run(backend, NavigateTo('table'))

    mug = result.observation.find_object('mug_1')
    gripper = result.observation.robot.gripper(Side.LEFT)
    assert mug.pose == gripper.pose
    assert mug.pose.position.distance_to(world.locations['table'].position) < 1.5
    assert result.observation.find_object('plate_1') == plate_before


def test_place_drops_the_held_object_at_the_requested_pose(backend):
    """place: the object lands where asked and the gripper ends up empty and open."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1'), NavigateTo('table'))
    target = Pose.from_xyz(0.35, 2.05, 0.75)

    result = backend.execute(Place(target))

    assert result.status is SkillStatus.OK
    assert 'mug_1' in result.reason
    mug = result.observation.find_object('mug_1')
    # Exact: place assigns the commanded pose to the object verbatim, with no
    # shoulder round trip, so a tolerance here would hide a refactor that
    # started deriving the object's pose from the gripper.
    assert mug.pose == target
    assert mug.held_by is None
    gripper = result.observation.robot.gripper(Side.LEFT)
    # Approximate: the gripper pose *is* reconstructed from the shoulder.
    assert_pose_close(gripper.pose, target)
    assert gripper.held_object_id is None
    assert gripper.state is GripperState.OPEN


def test_place_can_name_the_gripper_that_releases(backend):
    """place: with both hands full, the requested side is the one that lets go."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1', Side.LEFT), Grasp('plate_1', Side.RIGHT))
    target = Pose.from_xyz(2.35, -0.15, 0.95)

    result = backend.execute(Place(target, Side.RIGHT))

    assert result.status is SkillStatus.OK
    assert result.observation.find_object('plate_1').pose == target  # exact: assigned
    assert result.observation.robot.gripper(Side.RIGHT).held_object_id is None
    assert result.observation.robot.gripper(Side.LEFT).held_object_id == 'mug_1'


def test_extend_column_sets_the_height_and_lifts_the_arms(backend):
    """extend_column: the column height, the grippers and the load all rise."""
    run(backend, NavigateTo('kitchen'), Grasp('mug_1'))
    before = backend.get_observation()
    gripper_before = before.robot.gripper(Side.LEFT)

    result = backend.execute(ExtendColumn(0.9))

    assert result.status is SkillStatus.OK
    robot = result.observation.robot
    assert robot.column_height == 0.9
    rise = 0.9 - before.robot.column_height
    assert robot.gripper(Side.LEFT).pose.position.z == pytest.approx(
        gripper_before.pose.position.z + rise)
    assert result.observation.find_object('mug_1').pose == robot.gripper(Side.LEFT).pose
    assert result.observation.find_object('plate_1') == before.find_object('plate_1')


def test_close_gripper_closes_the_named_gripper_only(backend):
    """close_gripper: one side toggles, the other is untouched."""
    result = backend.execute(CloseGripper(Side.RIGHT))

    assert result.status is SkillStatus.OK
    assert result.observation.robot.gripper(Side.RIGHT).state is GripperState.CLOSED
    assert result.observation.robot.gripper(Side.LEFT).state is GripperState.OPEN

    again = backend.execute(CloseGripper(Side.RIGHT))
    assert again.status is SkillStatus.OK
    assert 'already closed' in again.reason


def test_close_gripper_on_thin_air_picks_nothing_up(backend):
    """close_gripper is not a grasp: no object attaches by accident."""
    run(backend, NavigateTo('kitchen'))
    before = snapshot(backend)

    result = backend.execute(CloseGripper(Side.LEFT))

    assert result.status is SkillStatus.OK
    assert result.observation.robot.gripper(Side.LEFT).held_object_id is None
    assert result.observation.held_objects() == ()
    assert result.observation.to_dict()['objects'] == before['objects']


def test_open_gripper_opens_and_drops_what_it_holds(backend):
    """open_gripper: an empty gripper just opens; a full one drops its load."""
    idle = backend.execute(OpenGripper(Side.LEFT))
    assert idle.status is SkillStatus.OK
    assert 'already open' in idle.reason

    run(backend, NavigateTo('kitchen'), Grasp('mug_1'))
    gripper_pose = backend.get_observation().robot.gripper(Side.LEFT).pose

    result = backend.execute(OpenGripper(Side.LEFT))

    assert result.status is SkillStatus.OK
    assert 'mug_1' in result.reason
    gripper = result.observation.robot.gripper(Side.LEFT)
    assert gripper.state is GripperState.OPEN
    assert gripper.held_object_id is None
    dropped = result.observation.find_object('mug_1')
    assert dropped.held_by is None
    assert dropped.pose == gripper_pose, 'the object is dropped where the gripper was'


def test_every_result_carries_the_skill_and_a_fresh_observation(backend):
    """Closed loop: no skill result ever needs a follow-up query."""
    skills = (
        NavigateTo('kitchen'),
        ExtendColumn(0.5),
        Grasp('mug_1'),
        CloseGripper(Side.RIGHT),
        OpenGripper(Side.RIGHT),
        MoveGripper(Side.RIGHT, Pose.from_xyz(2.3, -0.2, 0.95)),
        NavigateTo('table'),
        Place(Pose.from_xyz(0.35, 2.05, 0.75)),
    )
    for skill in skills:
        result = backend.execute(skill)
        assert result.status is SkillStatus.OK, f'{skill!r}: {result.reason}'
        assert result.skill == skill
        assert result.observation == backend.get_observation()
        assert result.to_dict() == type(result).from_dict(result.to_dict()).to_dict()
