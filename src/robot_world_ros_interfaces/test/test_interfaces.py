# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Smoke-test the generated world-query interface (keeps the test gate green).

This package holds no behaviour of its own -- it is the srv definition, and
all the logic lives in ``robot_world_ros``. The gate, however, audits a
first-party package that yields no test result as ``no-result`` (a failure),
so the interface is exercised at all: it must *generate*, be importable from
Python, and carry the one response field the node fills in.
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
