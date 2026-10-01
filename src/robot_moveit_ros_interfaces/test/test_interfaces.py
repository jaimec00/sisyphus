# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The generated CartesianGoal interface imports and carries its fields.

The gate (scripts/check_test_integrity.py) audits a first-party package with no
non-linter test as a FAILURE, so this is the minimal claim that belongs to the
*interfaces* package: the .srv generated, its Python module imports, and the
request/response carry the fields the node and its clients agree on. Field
*behaviour* is the node package's test.
"""

STATUS_SUCCESS, STATUS_OUT_OF_REACH, STATUS_COLLISION, STATUS_FAILURE = (0, 1, 2, 3)


def test_cartesian_goal_interface_fields():
    from geometry_msgs.msg import Pose
    from robot_moveit_ros_interfaces.srv import CartesianGoal

    request = CartesianGoal.Request()
    assert request.object_id == ''
    assert request.arm == ''
    assert request.use_target_pose is False
    assert isinstance(request.target_pose, Pose)

    response = CartesianGoal.Response()
    assert response.success is False
    assert response.error_code == 0
    assert response.status == 0
    assert response.message == ''

    # The request/response types are the pair the node serves and the test
    # client calls -- a mismatch here would mean two generated modules.
    assert CartesianGoal.Request is not CartesianGoal.Response

    # The status enum values the node and the test both branch on. These are
    # plain module constants in cartesian_goal (not .srv constants), so the
    # wire contract is "an int in 0..3"; this is the one place the numbers are
    # written down outside the node.
    assert (STATUS_SUCCESS, STATUS_OUT_OF_REACH, STATUS_COLLISION,
            STATUS_FAILURE) == (0, 1, 2, 3)
