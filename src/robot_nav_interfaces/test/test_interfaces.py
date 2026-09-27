# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Smoke-test the generated navigate action (keeps the test gate green).

This package holds no behaviour of its own -- it is the action definition, and
all the logic lives in ``robot_nav``. The gate, however, audits a first-party
package that yields no test result as ``no-result`` (a failure), so the action
is exercised at all: it must *generate*, be importable from Python, and carry
the fields the bridge reads and fills in (a ``location`` goal, a
``success``/``error`` result).

Mirrors ``robot_world_ros_interfaces/test/test_interfaces.py``, the same gate
requirement for the world services.
"""


def test_navigate_to_location_action_generates():
    """The generated ``NavigateToLocation`` action is importable with its parts."""
    from robot_nav_interfaces.action import NavigateToLocation
    assert hasattr(NavigateToLocation, 'Goal')
    assert hasattr(NavigateToLocation, 'Result')
    assert hasattr(NavigateToLocation, 'Feedback')


def test_navigate_to_location_goal_carries_location():
    """The goal carries the location *name* (never a pose -- D30/D36)."""
    from robot_nav_interfaces.action import NavigateToLocation
    goal = NavigateToLocation.Goal()
    goal.location = 'kitchen'
    assert goal.location == 'kitchen'


def test_navigate_to_location_result_round_trips():
    """The result carries the success flag and its reason, verbatim."""
    from robot_nav_interfaces.action import NavigateToLocation
    result = NavigateToLocation.Result()
    result.success = True
    result.error = ''
    assert result.success is True
    assert result.error == ''

    result.success = False
    result.error = "unknown location 'ghost'; known locations: charger, kitchen"
    assert result.success is False
    assert result.error == (
        "unknown location 'ghost'; known locations: charger, kitchen")


def test_navigate_to_location_has_no_pose_field():
    """The seam is semantic: the action must not expose a raw pose to the brain."""
    from robot_nav_interfaces.action import NavigateToLocation
    goal_fields = set(NavigateToLocation.Goal.get_fields_and_field_types())
    assert 'location' in goal_fields
    assert 'pose' not in goal_fields
    assert 'tf_pose' not in goal_fields
