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

from mock_backend_fixtures import assert_pose_close
import pytest
from robot_backends import MockBackend, MuJoCoBackend, RobotBackend
from robot_skills import (
    FailureCode,
    GripperState,
    NavigateTo,
    Observation,
    Pose,
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

#: Column-hold tolerance (F11 regression): across _STEPS real
#: dynamics steps the position-actuator servo must keep the
#: column at its seed height (a body would otherwise slump).
_COLUMN_HOLD_TOLERANCE = 0.01


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
    """F11 regression: reset leaves column ctrl at seed AND it holds over steps.

    The actuator home sweep must exclude the column position actuator -- if
    it did not, reset would first command ``data.ctrl[column] = height`` and
    then zero it, leaving the servo with no target so the column slumps under
    gravity once stepped. Guard the servo target right after reset() and that
    the reported column height holds over a long real-dynamics run.
    """
    backend.reset()

    # The position actuator must still be commanded to the seed height right
    # after reset -- the home sweep must not have zeroed it (F11).
    assert backend._data.ctrl[backend._column_ctrl] == pytest.approx(
        document.start_column_height)

    # Drive real dynamics; the column must not collapse once stepped.
    backend.step(_STEPS)
    hold = backend.get_observation().robot.column_height
    assert hold == pytest.approx(
        document.start_column_height, abs=_COLUMN_HOLD_TOLERANCE)


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


@pytest.mark.parametrize(
    'skill', [NavigateTo('kitchen')])
def test_execute_refuses_every_skill_for_pr1(backend, skill):
    """PR1 has no skills; execute returns a clean, attributable refusal."""
    before = backend.get_observation()
    result = backend.execute(skill)

    assert result.status is SkillStatus.FAILED
    assert result.code is FailureCode.UNSUPPORTED_SKILL
    assert result.code.is_backend_refusal is True, 'backend refused; nothing moved'
    assert result.reason
    # The refusal leaves the world unchanged.
    assert result.observation == before
    assert backend.get_observation() == before


def test_execute_rejects_a_non_skill(backend):
    """Passing a raw dict is a programming error, not a skill refusal."""
    with pytest.raises(TypeError):
        backend.execute({'skill': 'navigate_to', 'location': 'kitchen'})
