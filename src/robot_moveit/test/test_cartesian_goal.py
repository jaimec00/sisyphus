# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The cartesian_goal node's pure logic: failure mapping, envelope, transforms.

Ruling R3 makes the *envelope pre-check* -- not an error code -- the thing that
tells ``out_of_reach`` apart from a collision, and R5 makes the plan-failure
mapping the thing that tells a collision apart from a planner timeout. Both are
pure functions by construction, so they are tested here with no ROS graph and no
MoveIt: a bug in either is a wrong answer to the acceptance criteria, and this
is the cheapest place to catch it.

The TF transform helper is also pure (it takes a built ``TransformStamped``);
its test uses a 90-degree yaw so a sign error in either the rotation or the
orientation composition shows up as a wrong vector, not just a wrong magnitude.
"""
import math

import pytest


def _target(x, y, z):
    return (x, y, z)


def _shoulder(x, y, z):
    return (x, y, z)


# -- is_out_of_reach (R3) ----------------------------------------------------

def test_reach_is_inclusive_at_the_boundary():
    """A target exactly on the reach sphere is reachable, not out of reach.

    The Mock backend compares with ``<=`` (``mock_backend._reach_offset``), so a
    strict ``>`` here is the only way the two agree; a ``>=`` would make the
    MoveIt path refuse a goal the Mock would accept.
    """
    from robot_moveit.cartesian_goal import is_out_of_reach
    assert not is_out_of_reach(_target(0.0, 0.0, 0.85), _shoulder(0.0, 0.0, 0.0),
                               0.85)


def test_target_beyond_reach_is_out_of_reach():
    from robot_moveit.cartesian_goal import is_out_of_reach
    assert is_out_of_reach(_target(0.0, 0.0, 0.86), _shoulder(0.0, 0.0, 0.0),
                           0.85)
    # A diagonal target: the check is Euclidean, not per-axis.
    assert is_out_of_reach(_target(0.5, 0.5, 0.5), _shoulder(0.0, 0.0, 0.0),
                           0.85)


def test_reach_is_measured_from_the_shoulder_not_the_base():
    """The same target is reachable from one shoulder and not the other.

    This is the property that makes the R3 pre-check mean anything: with the
    shoulders 0.36 m apart (arm.xacro's ``shoulder_offset_y``), a target on the
    left shoulder's sphere is well outside the right's.
    """
    from robot_moveit.cartesian_goal import is_out_of_reach
    right_shoulder = _shoulder(0.0, -0.18, 0.0)
    # 0.80 m from the LEFT shoulder, i.e. inside its sphere; the same point is
    # 0.80 + 0.36 = 1.16 m from the right shoulder, well outside.
    target = _target(0.0, 0.18 + 0.80, 0.0)
    assert not is_out_of_reach(target, _shoulder(0.0, 0.18, 0.0), 0.85)
    assert is_out_of_reach(target, right_shoulder, 0.85)


# -- classify_plan_failure (R5) ----------------------------------------------

def test_start_and_goal_in_collision_map_to_collision():
    from moveit_msgs.msg import MoveItErrorCodes
    from robot_moveit.cartesian_goal import STATUS_COLLISION, classify_plan_failure
    for code in (MoveItErrorCodes.START_STATE_IN_COLLISION,
                 MoveItErrorCodes.GOAL_IN_COLLISION):
        status, reason = classify_plan_failure(code, [])
        assert status == STATUS_COLLISION, (code, status)
        assert 'collision' in reason


def test_planning_failed_with_contacts_is_a_collision():
    """A bare ``-1`` with a contact list is a collision during planning."""
    from moveit_msgs.msg import MoveItErrorCodes
    from robot_moveit.cartesian_goal import STATUS_COLLISION, classify_plan_failure
    status, reason = classify_plan_failure(
        MoveItErrorCodes.PLANNING_FAILED, [object(), object()])
    assert status == STATUS_COLLISION, status
    assert 'contacts=2' in reason


def test_planning_failed_without_contacts_is_a_failure_not_a_collision():
    """The ambiguity R3 exists for: a clean ``-1`` is *not* reported as collision.

    Claiming a collision here would be a lie the caller cannot detect; the
    honest answer is FAILURE, and the envelope pre-check has already ruled out
    "unreachable" by the time this runs.
    """
    from moveit_msgs.msg import MoveItErrorCodes
    from robot_moveit.cartesian_goal import STATUS_FAILURE, classify_plan_failure
    status, reason = classify_plan_failure(MoveItErrorCodes.PLANNING_FAILED, [])
    assert status == STATUS_FAILURE, status
    assert 'collision' not in reason


# -- apply_transform ---------------------------------------------------------

def _transform(translation, yaw):
    """Build a TransformStamped with a pure-yaw rotation about z."""
    from geometry_msgs.msg import TransformStamped
    transform = TransformStamped()
    transform.header.frame_id = 'base_link'
    transform.transform.translation.x = translation[0]
    transform.transform.translation.y = translation[1]
    transform.transform.translation.z = translation[2]
    transform.transform.rotation.z = math.sin(yaw / 2.0)
    transform.transform.rotation.w = math.cos(yaw / 2.0)
    return transform


def _pose_stamped(x, y, z):
    from geometry_msgs.msg import PoseStamped
    stamped = PoseStamped()
    stamped.header.frame_id = 'world'
    stamped.pose.position.x = x
    stamped.pose.position.y = y
    stamped.pose.position.z = z
    stamped.pose.orientation.w = 1.0
    return stamped


def test_apply_transform_rotates_position_by_the_yaw():
    """A 90-degree yaw maps +x onto +y -- the sign that is easy to invert."""
    from robot_moveit.cartesian_goal import apply_transform
    result = apply_transform(
        _pose_stamped(1.0, 0.0, 0.0), _transform((0.0, 0.0, 0.0), math.pi / 2))
    assert result.pose.position.x == pytest.approx(0.0, abs=1e-9)
    assert result.pose.position.y == pytest.approx(1.0, abs=1e-9)
    assert result.pose.position.z == pytest.approx(0.0, abs=1e-9)


def test_apply_transform_adds_translation_after_rotating():
    """The translation is applied to the *rotated* point, in the target frame."""
    from robot_moveit.cartesian_goal import apply_transform
    result = apply_transform(
        _pose_stamped(1.0, 0.0, 0.0),
        _transform((10.0, 20.0, 30.0), math.pi))
    assert result.pose.position.x == pytest.approx(9.0, abs=1e-9)
    assert result.pose.position.y == pytest.approx(20.0, abs=1e-9)
    assert result.pose.position.z == pytest.approx(30.0, abs=1e-9)


def test_apply_transform_composes_orientation():
    """A 90-degree transform turns an identity pose's frame by 90 degrees."""
    from robot_moveit.cartesian_goal import apply_transform
    result = apply_transform(
        _pose_stamped(0.0, 0.0, 0.0), _transform((0.0, 0.0, 0.0), math.pi / 2))
    assert result.pose.orientation.z == pytest.approx(
        math.sin(math.pi / 4), abs=1e-9)
    assert result.pose.orientation.w == pytest.approx(
        math.cos(math.pi / 4), abs=1e-9)
    assert result.header.frame_id == 'base_link'


# -- _target_is_identifiable -------------------------------------------------

def test_target_identification_uses_the_explicit_switch():
    """Identification is an explicit flag, not an empty-pose sentinel.

    A default-constructed Pose is a *valid* pose (unit quaternion), so it could
    never serve as "not given"; this asserts the flag is what decides, and that
    an object id alone is enough.
    """
    from robot_moveit_ros_interfaces.srv import CartesianGoal
    from robot_moveit.cartesian_goal import _target_is_identifiable

    request = CartesianGoal.Request()
    assert not _target_is_identifiable(request)

    request.object_id = 'mug_1'
    assert _target_is_identifiable(request)

    by_pose = CartesianGoal.Request()
    by_pose.use_target_pose = True
    assert _target_is_identifiable(by_pose)
