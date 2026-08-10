# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Shared builders and round-trip helpers for the robot_skills tests.

Kept out of ``conftest.py`` so test modules can import the helpers directly
without relying on a module name that every package in the workspace shares.
"""

import json
from typing import Any

from robot_skills import (
    GripperObservation,
    GripperState,
    Observation,
    Point,
    Pose,
    Quaternion,
    RobotState,
    SceneObject,
    Side,
)

JSON_TYPES = (type(None), bool, int, float, str)


def assert_json_safe(value: Any, path: str = '<root>') -> None:
    """Assert ``value`` contains only types ``json.dumps`` handles natively."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), f'{path}: non-string key {key!r}'
            assert_json_safe(item, f'{path}.{key}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_json_safe(item, f'{path}[{index}]')
    else:
        assert isinstance(value, JSON_TYPES), (
            f'{path}: {type(value).__name__} is not JSON-safe (value={value!r})')


def assert_round_trip(obj: Any) -> None:
    """Assert an object survives dict *and* JSON-text round trips unchanged."""
    as_dict = obj.to_dict()
    assert_json_safe(as_dict)

    # Plain dict round trip.
    assert type(obj).from_dict(as_dict) == obj

    # The dict itself must survive JSON text unchanged...
    reloaded = json.loads(json.dumps(as_dict))
    assert reloaded == as_dict

    # ...and rebuild an equal object with an identical dict form.
    rebuilt = type(obj).from_dict(reloaded)
    assert rebuilt == obj
    assert rebuilt.to_dict() == as_dict

    # The to_json/from_json convenience wrappers agree with the above.
    assert type(obj).from_json(obj.to_json()) == obj


def make_gripper(
    side: Side,
    state: GripperState = GripperState.OPEN,
    held_object_id: str | None = None,
) -> GripperObservation:
    """Build a gripper observation with a deterministic pose."""
    offset = 0.2 if side is Side.LEFT else -0.2
    return GripperObservation(
        side=side,
        state=state,
        pose=Pose.from_xyz(0.3, offset, 0.8),
        held_object_id=held_object_id,
    )


def make_robot_state(**overrides: Any) -> RobotState:
    """Build a plain robot state, overridable field by field."""
    defaults: dict[str, Any] = {
        'pose': Pose(Point(1.0, 2.0, 0.0), Quaternion(0.0, 0.0, 0.7071, 0.7071)),
        'column_height': 0.4,
        'grippers': (make_gripper(Side.LEFT), make_gripper(Side.RIGHT)),
        'location': 'kitchen',
    }
    defaults.update(overrides)
    return RobotState(**defaults)


def make_observation(**overrides: Any) -> Observation:
    """Build a small observation covering held/unheld and (un)graspable objects."""
    defaults: dict[str, Any] = {
        'robot': make_robot_state(),
        'objects': (
            SceneObject('mug_1', 'mug', Pose.from_xyz(1.3, 0.2, 0.9), graspable=True),
            SceneObject(
                'counter_1', 'counter', Pose.from_xyz(1.5, 0.0, 0.5), graspable=False),
        ),
        'known_locations': ('kitchen', 'table'),
    }
    defaults.update(overrides)
    return Observation(**defaults)
