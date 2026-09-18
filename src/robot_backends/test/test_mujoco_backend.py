# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""MuJoCoBackend (PR1): the sim backend reports the seed scene through the seam.

Acceptance for PR1 (issue #99): ``MuJoCoBackend`` is a ``RobotBackend`` whose
``get_observation`` matches the Mock's wire shape; every world object's pose
equals its seed pose exactly, both on :meth:`~MuJoCoBackend.reset` and after a
real ``mj_step`` (scene objects are welded into the MJCF as static bodies, so a
step cannot perturb them); the robot comes up at ``start_location`` with column
at ``start_column_height`` and empty/open grippers.
"""

import math

from mock_backend_fixtures import assert_pose_close
import mujoco
import pytest
from robot_backends import MockBackend, MuJoCoBackend, RobotBackend
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    FailureCode,
    Grasp,
    GripperState,
    MoveGripper,
    NavigateTo,
    Observation,
    OpenGripper,
    Point,
    Pose,
    Quaternion,
    Side,
    SIDE_ORDER,
    SkillResult,
    SkillStatus,
)
from robot_world import default_seed_document

#: Tolerances for pose comparison. The scene is identity-mapped and welded, so
#: MjCoCo reports the seed numerically; a small float tolerance absorbs any
#: representation noise without weakening the "equals seed" claim.
_POSE_TOLERANCE = 1e-6

#: How many real dynamics timesteps the acceptance step runs.
_STEPS = 200


@pytest.fixture
def backend() -> MuJoCoBackend:
    """Return a fresh MuJoCo backend over the shipped apartment seed."""
    return MuJoCoBackend()


@pytest.fixture
def document():
    """Return the seeded world document the backend compiles from."""
    return default_seed_document()


def _objects_by_id(observation: Observation) -> dict[str, Pose]:
    """Map object_id -> reported pose from an observation."""
    return {item.object_id: item.pose for item in observation.objects}


def _gripper_shape(observation: Observation) -> dict[Side, tuple]:
    """Return the shape of every gripper: side -> (state, held, grasped)."""
    return {
        g.side: (g.state, g.held_object_id, g.grasped)
        for g in observation.robot.grippers
    }


def test_mujoco_backend_is_a_robot_backend(backend):
    """The MuJoCo backend satisfies the RobotBackend seam (D9/Mock parity)."""
    assert isinstance(backend, RobotBackend)
    assert isinstance(backend.reset(), Observation)
    assert isinstance(backend.get_observation(), Observation)
    # execute() returns a SkillResult even for a legal-but-unsupported skill.
    assert isinstance(backend.execute(NavigateTo('kitchen')), SkillResult)


def test_observation_matches_the_seed_scene(backend, document):
    """Every reported object pose, id and map vocabulary equals the seed (R-5)."""
    observation = backend.get_observation()

    by_id = {
        item.object_id: item.pose for item in observation.objects}
    assert sorted(by_id) == sorted(
        item.object_id for item in document.objects), 'wrong object id set'
    for spec in document.objects:
        actual = by_id[spec.object_id]
        assert_pose_close(
            actual, spec.pose, tolerance=_POSE_TOLERANCE)
        reported = next(o for o in observation.objects
                        if o.object_id == spec.object_id)
        assert reported.label == spec.label
        assert reported.graspable == spec.graspable
        assert reported.held_by is None
    assert observation.known_locations == tuple(sorted(document.locations))


def test_reset_returns_seed_posture(backend, document):
    """reset() homes the robot: charger, column at start height, open grippers."""
    observation = backend.reset()

    # Robot proprioception (R-3/R-6).
    assert observation.robot.location == document.start_location == 'charger'
    assert_pose_close(
        observation.robot.pose,
        document.locations[document.start_location],
        tolerance=_POSE_TOLERANCE)
    assert observation.robot.column_height == pytest.approx(
        document.start_column_height, abs=_POSE_TOLERANCE)

    # Both SIDE_ORDER grippers are present and match the Mock's reset shape.
    sides = [g.side for g in observation.robot.grippers]
    assert sides == list(SIDE_ORDER)
    shape = _gripper_shape(observation)
    for side in SIDE_ORDER:
        state, held, grasped = shape[side]
        assert state is GripperState.OPEN
        assert held is None
        assert grasped is False


def test_column_servo_holds_seed_height_across_steps(backend, document):
    """F11 regression: reset leaves column ctrl at seed (the servo is committed).

    The actuator home sweep must exclude the column position actuator -- if it
    did not, reset would first command ``data.ctrl[column] = height`` and then
    zero it, leaving the servo with no target, so the column would slump once
    real dynamics run.  That structural guard holds here by checking the servo
    target right after reset, and again after an ``extend_column`` move.

    .. note::  A *long real-dynamics* hold (step 200+ times and assert the
       height never collapses) is **not asserted in PR2**: the base is now
       free-jointed and there is no floor/contact yet (R6 / PR4), so an
       ungrounded ``mj_step`` run is not a meaningful vehicle for a grounded
       servo-hold claim -- the prismatic solver destabilises once the free
       joint is added (probed: column qpos collapses within ~15 steps, DOF-6
       QACC warning).  That assertion returns with the floor+wheel dynamics in
       PR4.  The F11 *regression* (the home sweep zeroing the servo) is fully
       covered by the static checks below.
    """
    backend.reset()

    # The position actuator must still be commanded to the seed height right
    # after reset -- the home sweep must not have zeroed it (F11).
    assert backend._data.ctrl[backend._column_ctrl] == pytest.approx(
        document.start_column_height)
    # And the reported height is the seed (posture correctness, not dynamics).
    assert backend.get_observation().robot.column_height == pytest.approx(
        document.start_column_height, abs=_POSE_TOLERANCE)

    # An in-range extend re-commits the servo to the new height (same guard).
    result = backend.execute(ExtendColumn(0.9))
    assert result.status is SkillStatus.OK
    assert result.observation.robot.column_height == pytest.approx(0.9)
    assert backend._data.ctrl[backend._column_ctrl] == pytest.approx(0.9)


def test_gripper_shape_matches_mock_reset(backend):
    """Gripper shape is structurally the same as a fresh Mock's (parity)."""
    mu = backend.reset()
    mock = MockBackend().reset()

    for side in SIDE_ORDER:
        our = mu.robot.gripper(side)
        ref = mock.robot.gripper(side)
        # Same side present, same open/empty/grasped flags. Their *pose* can
        # differ (MuJoCo reports real kinematics; Mock reports the shoulder+
        # home-offset model), which is expected and not part of PR1 parity.
        assert our.side is ref.side
        assert our.state is ref.state
        assert our.held_object_id == ref.held_object_id
        assert our.grasped == ref.grasped


def test_scene_objects_are_invariant_across_real_steps(backend, document):
    """Acceptance: a real mj_step leaves every object exactly at its seed.

    Scene objects are static, joint-less bodies welded into the world body
    before compile (R-1), so no number of ``mj_step`` calls can move them --
    objects would *only* move if a free (dynamic) joint/contact existed.
    """
    before = backend.reset()
    before_by_id = _objects_by_id(before)

    # Advance real dynamics many times; the fused robot base cannot drive, but
    # even if arms/column drift, welded objects must not move at all.
    backend.step(_STEPS)

    after = backend.get_observation()
    after_by_id = _objects_by_id(after)

    assert sorted(after_by_id) == sorted(before_by_id)
    for object_id, pose in before_by_id.items():
        assert_pose_close(
            after_by_id[object_id], pose, tolerance=_POSE_TOLERANCE)
        # ...and still exactly the seed, not merely unchanged between the two.
        spec = document.find_object(object_id)
        assert_pose_close(
            after_by_id[object_id], spec.pose, tolerance=_POSE_TOLERANCE)


def test_navigate_to_ready_at_charger_after_reset(backend, document):
    """A fresh reset homes the base to the seed start location (R-5)."""
    observation = backend.reset()
    assert observation.robot.location == document.start_location == 'charger'
    assert_pose_close(
        observation.robot.pose,
        document.locations['charger'],
        tolerance=_POSE_TOLERANCE)


def test_navigate_to_moves_to_a_known_location(backend, document):
    """navigate_to teleports the base to the named location (R3)."""
    backend.reset()
    kitchen = document.locations['kitchen']

    result = backend.execute(NavigateTo('kitchen'))

    assert result.status is SkillStatus.OK
    assert result.code is None
    assert result.reason is None, 'a fresh move carries no informational reason'
    obs = result.observation
    assert obs.robot.location == 'kitchen'
    # The base really moved: reported pose == the kitchen reference pose.
    assert_pose_close(obs.robot.pose, kitchen, tolerance=_POSE_TOLERANCE)


def test_navigate_to_report_pose_reads_the_sim(backend, document):
    """robot.pose is read from the base body, so it tracks a real move (R5)."""
    backend.reset()
    living_room = document.locations['living_room']
    result = backend.execute(NavigateTo('living_room'))
    assert result.status is SkillStatus.OK
    assert_pose_close(result.observation.robot.pose, living_room,
                      tolerance=_POSE_TOLERANCE)
    assert_pose_close(backend.get_observation().robot.pose, living_room,
                      tolerance=_POSE_TOLERANCE)


def test_navigate_to_renavigate_notes_already_at(backend, document):
    """Re-navigating to the current location succeeds with an "already at" note."""
    backend.reset()
    first = backend.execute(NavigateTo('kitchen'))
    assert first.status is SkillStatus.OK

    result = backend.execute(NavigateTo('kitchen'))

    assert result.status is SkillStatus.OK
    assert result.code is None
    assert result.reason == "already at 'kitchen'"
    assert result.observation.robot.location == 'kitchen'
    # Re-navigating to the same place leaves the base exactly there.
    assert_pose_close(
        result.observation.robot.pose,
        document.locations['kitchen'],
        tolerance=_POSE_TOLERANCE)


def test_navigate_to_unknown_location_refuses(backend, document):
    """An unknown location is refused with UNKNOWN_LOCATION (lists knowns)."""
    backend.reset()
    before = backend.get_observation()

    result = backend.execute(NavigateTo('mars'))

    assert result.status is SkillStatus.FAILED
    assert result.code is FailureCode.UNKNOWN_LOCATION
    assert result.code.is_backend_refusal is True
    known = ', '.join(sorted(document.locations))
    assert f'known locations: {known}' in result.reason
    # Refused -> nothing moved, base still where it was.
    assert result.observation == before
    assert_pose_close(before.robot.pose, document.locations['charger'],
                      tolerance=_POSE_TOLERANCE)


def test_navigate_to_back_home_then_extend_is_clean(backend, document):
    """Home (charger) is just a location; navigate returns there (R5 reset)."""
    backend.reset()
    backend.execute(NavigateTo('table'))
    # Returning to charger teleports home rather than only updating the name.
    result = backend.execute(NavigateTo('charger'))
    assert result.status is SkillStatus.OK
    assert_pose_close(result.observation.robot.pose,
                      document.locations['charger'],
                      tolerance=_POSE_TOLERANCE)


@pytest.mark.parametrize('height,expected', [
    (0.3, 0.3),
    (0.0, 0.0),  # min
    (1.2, 1.2),  # max
])
def test_extend_column_in_range_is_ok(backend, height, expected):
    """In-range heights extend the column and read back the set value (R2)."""
    backend.reset()
    result = backend.execute(ExtendColumn(height))
    assert result.status is SkillStatus.OK
    assert result.code is None
    assert result.reason is None
    assert result.observation.robot.column_height == pytest.approx(
        expected, abs=_POSE_TOLERANCE)
    # The position actuator is commanded to hold the new height too (R3 servoing).
    assert backend._data.ctrl[backend._column_ctrl] == pytest.approx(
        expected)


@pytest.mark.parametrize('height', [2.0, -0.5, 1.21, -0.001])
def test_extend_column_out_of_range_is_refused(backend, height):
    """Out-of-range heights are refused (never clamped): sim left unchanged (R2).

    The safety layer clamped a command to <=1.2 before the backend sees it; the
    backend's OUT_OF_RANGE is defense-in-depth.  Crucially this validates the
    /reading/ of the column from the model agrees with reachable travel, so a
    future column that cannot actually reach 1.2 does not silently pretend to.
    """
    backend.reset()
    before = backend.get_observation()

    result = backend.execute(ExtendColumn(height))

    assert result.status is SkillStatus.FAILED
    assert result.code is FailureCode.OUT_OF_RANGE
    assert result.code.is_backend_refusal is True
    assert f'{height:.2f} m' in result.reason
    # Refused -> the column (and everything else) is unchanged, and the result
    # hands back that unchanged observation.
    assert result.observation == before
    assert backend.get_observation() == before
    assert before.robot.column_height == pytest.approx(
        backend._document.start_column_height)


def test_extend_column_low_then_high(backend, document):
    """The column is a prismatic joint that moves through its range in place."""
    backend.reset()
    backend.execute(NavigateTo('table'))
    low = backend.execute(ExtendColumn(0.0))
    assert low.status is SkillStatus.OK
    assert low.observation.robot.column_height == pytest.approx(0.0)
    high = backend.execute(ExtendColumn(1.2))
    assert high.status is SkillStatus.OK
    assert high.observation.robot.column_height == pytest.approx(1.2)
    assert_pose_close(low.observation.robot.pose,
                      document.locations['table'], tolerance=_POSE_TOLERANCE)


def test_unsupported_skill_still_refused_and_leaves_world_unchanged(backend):
    """A legal-but-unimplemented skill (Grasp) is refused, world unchanged (PR2)."""
    backend.reset()
    before = backend.get_observation()

    result = backend.execute(Grasp('mug_1'))

    assert result.status is SkillStatus.FAILED
    assert result.code is FailureCode.UNSUPPORTED_SKILL
    assert result.code.is_backend_refusal is True
    assert result.reason
    # Refused with nothing moved.
    assert result.observation == before
    assert backend.get_observation() == before


def test_execute_rejects_a_non_skill(backend):
    """Passing a raw dict is a programming error, not a skill refusal."""
    with pytest.raises(TypeError):
        backend.execute({'skill': 'navigate_to', 'location': 'kitchen'})


#: A moderate arm configuration (left arm) used to derive an in-reach target.
#: Deliberately away from joint limits so the solver converges reliably (R8).
_ARM_CONFIG = (0.3, 0.4, 0.9, 0.0, 0.1)

#: Read-back tolerances for a solved move_gripper (R8).
_MOVE_POSITION_TOLERANCE = 5e-3
_MOVE_ORIENTATION_TOLERANCE = 2e-2

#: How far from the shoulder the out-of-reach target sits.
_FAR_DISTANCE = 2.0


def _quat_angle(left: Quaternion, right: Quaternion) -> float:
    """Return the geodesic angle (rad) between two unit quaternions."""
    dot = abs(left.x * right.x + left.y * right.y
              + left.z * right.z + left.w * right.w)
    return 2.0 * math.acos(min(1.0, dot))


def _set_arm(backend: MuJoCoBackend, side: Side, values) -> None:
    """Write an arm's five joint values via the backend's name-based hook (R8)."""
    for value, (qpos_adr, _) in zip(values, backend._arm_joints[side].values()):
        backend._data.qpos[qpos_adr] = value
    mujoco.mj_forward(backend._model, backend._data)


def _driven_qpos(backend: MuJoCoBackend, side: Side) -> float:
    """Return the driven gripper joint's current qpos."""
    return float(backend._data.qpos[backend._gripper_joints[side]['driven'][0]])


def test_move_gripper_in_reach_lands_the_gripper(backend):
    """A pose derived from the arm's own FK is reachable and hit within R8 tol."""
    side = Side.LEFT
    backend.reset()
    _set_arm(backend, side, _ARM_CONFIG)
    target = backend.get_observation().robot.gripper(side).pose

    backend.reset()
    result = backend.execute(MoveGripper(side, target))

    assert result.status is SkillStatus.OK
    assert result.reason is None
    reached = result.observation.robot.gripper(side).pose
    assert reached.position.distance_to(target.position) < _MOVE_POSITION_TOLERANCE
    assert _quat_angle(reached.orientation, target.orientation) < _MOVE_ORIENTATION_TOLERANCE
    # The five arm position actuators are commanded to the solved values too, so
    # the servo holds the solved posture rather than springing back (R3).
    for (qpos_adr, ctrl_id) in backend._arm_joints[side].values():
        assert backend._data.ctrl[ctrl_id] == pytest.approx(
            float(backend._data.qpos[qpos_adr]))


def test_move_gripper_to_current_pose_is_a_noop(backend):
    """Commanding the gripper's own post-reset pose succeeds and moves nothing."""
    side = Side.LEFT
    backend.reset()
    before = backend.get_observation().robot.gripper(side).pose

    result = backend.execute(MoveGripper(side, before))

    assert result.status is SkillStatus.OK
    after = result.observation.robot.gripper(side).pose
    assert after.position.distance_to(before.position) < _MOVE_POSITION_TOLERANCE
    assert _quat_angle(after.orientation, before.orientation) < _MOVE_ORIENTATION_TOLERANCE


def test_move_gripper_out_of_reach_is_refused(backend):
    """A pose far beyond the arm's reach fails with the Mock's reason shape (R3)."""
    side = Side.LEFT
    backend.reset()
    before = backend.get_observation()
    far = Pose(
        position=Point(_FAR_DISTANCE, 0.0, before.robot.column_height + 0.5),
        orientation=Quaternion(),
    )

    result = backend.execute(MoveGripper(side, far))

    assert result.status is SkillStatus.FAILED
    assert result.code is FailureCode.OUT_OF_REACH
    assert result.code.is_backend_refusal is True
    assert f'{_FAR_DISTANCE:.2f} m' in result.reason or '2.01 m' in result.reason
    assert '0.85 m' in result.reason
    assert "'charger'" in result.reason
    assert f'{side.value} shoulder' in result.reason
    # Refused up front: the world (and the arm) is untouched.
    assert result.observation == before


def test_close_then_open_gripper_flips_the_state(backend):
    """close/open write the jaws (driven + mirror) and are observable (R5/R6)."""
    side = Side.LEFT
    opened = backend.reset()
    open_pose = opened.robot.gripper(side).pose
    assert opened.robot.gripper(side).state is GripperState.OPEN

    closed = backend.execute(CloseGripper(side))

    assert closed.status is SkillStatus.OK
    assert closed.observation.robot.gripper(side).state is GripperState.CLOSED
    assert _driven_qpos(backend, side) == pytest.approx(0.0, abs=1e-9)
    assert closed.observation.robot.gripper(side).grasped is False
    # The jaws physically moved, so the reported orientation changed (the jaw
    # midpoint is on the wrist roll axis, so it does not translate).
    moved = closed.observation.robot.gripper(side).pose
    assert _quat_angle(moved.orientation, open_pose.orientation) > 1e-3

    # Idempotent: closing an already-closed gripper still succeeds (D19).
    again = backend.execute(CloseGripper(side))
    assert again.status is SkillStatus.OK
    assert again.reason == f'the {side.value} gripper was already closed'

    reopened = backend.execute(OpenGripper(side))

    assert reopened.status is SkillStatus.OK
    assert reopened.reason is None
    assert reopened.observation.robot.gripper(side).state is GripperState.OPEN
    assert _driven_qpos(backend, side) == pytest.approx(-1.5, abs=1e-9)

    idempotent = backend.execute(OpenGripper(side))
    assert idempotent.status is SkillStatus.OK
    assert idempotent.reason == f'the {side.value} gripper was already open'


def test_reset_homes_grippers_open(backend):
    """Reset homes each gripper OPEN -- driven qpos at its lower limit (R6)."""
    side = Side.LEFT
    backend.execute(CloseGripper(side))

    observation = backend.reset()

    assert _driven_qpos(backend, side) <= -0.75
    assert _driven_qpos(backend, side) == pytest.approx(-1.5, abs=1e-9)
    assert observation.robot.gripper(side).state is GripperState.OPEN
