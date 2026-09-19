# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Smoke-test the generated world-query interface (keeps the test gate green).

This package holds no behaviour of its own -- it is the srv definitions, and
all the logic lives in ``robot_world_ros``. The gate, however, audits a
first-party package that yields no test result as ``no-result`` (a failure),
so the interfaces are exercised at all: each must *generate*, be importable
from Python, and carry the fields the node reads and fills in.

The read srv is exercised by ``test_get_world_*``; the three write srvs (this
PR's surface) get one smoke test each, asserting the request/response fields
and that a response round-trips.
"""


def test_get_world_response_has_world_json():
    """The generated ``GetWorld.Response`` carries the canonical-JSON field."""
    from robot_world_ros_interfaces.srv import GetWorld
    assert hasattr(GetWorld.Response, 'world_json')


def test_get_world_request_is_empty_and_response_round_trips():
    """The request is empty; ``world_json`` round-trips verbatim."""
    from robot_world_ros_interfaces.srv import GetWorld
    request = GetWorld.Request()
    assert request is not None
    response = GetWorld.Response()
    response.world_json = '{"world_schema_version": 1}'
    assert response.world_json == '{"world_schema_version": 1}'


def test_update_object_pose_request_and_response_fields():
    """``UpdateObjectPose`` carries object_id + a pose request, success/error back."""
    from robot_world_ros_interfaces.srv import UpdateObjectPose
    request = UpdateObjectPose.Request()
    assert hasattr(request, 'object_id')
    assert hasattr(request, 'pose')
    response = UpdateObjectPose.Response()
    assert hasattr(response, 'success')
    assert hasattr(response, 'error')


def test_update_object_pose_round_trips():
    """The request's pose fields and the response's status round-trip verbatim."""
    from robot_world_ros_interfaces.srv import UpdateObjectPose
    request = UpdateObjectPose.Request()
    request.object_id = 'mug_1'
    request.pose.position.x = 0.3
    request.pose.position.y = 2.0
    request.pose.position.z = 0.75
    request.pose.orientation.w = 1.0
    assert request.object_id == 'mug_1'
    assert request.pose.position.x == 0.3
    assert request.pose.position.y == 2.0
    assert request.pose.position.z == 0.75
    assert request.pose.orientation.w == 1.0

    response = UpdateObjectPose.Response()
    response.success = False
    response.error = 'no object'
    assert response.success is False
    assert response.error == 'no object'


def test_add_object_request_and_response_fields():
    """``AddObject`` carries object_id + label + pose + graspable, success/error back."""
    from robot_world_ros_interfaces.srv import AddObject
    request = AddObject.Request()
    assert hasattr(request, 'object_id')
    assert hasattr(request, 'label')
    assert hasattr(request, 'pose')
    assert hasattr(request, 'graspable')
    response = AddObject.Response()
    assert hasattr(response, 'success')
    assert hasattr(response, 'error')


def test_add_object_round_trips():
    """The request's scalar/pose fields and the response's status round-trip verbatim."""
    from robot_world_ros_interfaces.srv import AddObject
    request = AddObject.Request()
    request.object_id = 'spoon_1'
    request.label = 'spoon'
    request.graspable = True
    request.pose.position.x = 1.0
    request.pose.orientation.z = 0.0
    request.pose.orientation.w = 1.0
    assert request.object_id == 'spoon_1'
    assert request.label == 'spoon'
    assert request.graspable is True
    assert request.pose.position.x == 1.0
    assert request.pose.orientation.w == 1.0

    response = AddObject.Response()
    response.success = True
    response.error = ''
    assert response.success is True
    assert response.error == ''


def test_remove_object_request_and_response_fields():
    """``RemoveObject`` carries an object_id request, success/error back."""
    from robot_world_ros_interfaces.srv import RemoveObject
    request = RemoveObject.Request()
    assert hasattr(request, 'object_id')
    response = RemoveObject.Response()
    assert hasattr(response, 'success')
    assert hasattr(response, 'error')


def test_remove_object_round_trips():
    """The request's object_id and the response's status round-trip verbatim."""
    from robot_world_ros_interfaces.srv import RemoveObject
    request = RemoveObject.Request()
    request.object_id = 'mug_1'
    assert request.object_id == 'mug_1'

    response = RemoveObject.Response()
    response.success = True
    response.error = ''
    assert response.success is True
    assert response.error == ''
