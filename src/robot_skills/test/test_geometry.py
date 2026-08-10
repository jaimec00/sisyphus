# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the geometry primitives shared by the skill API."""

from dataclasses import FrozenInstanceError
import math

import pytest
from robot_skills import Point, Pose, Quaternion
from robot_skills.serialization import SerializationError


def test_point_arithmetic_and_distance():
    """Points add, subtract and measure distance component-wise."""
    a = Point(1.0, 2.0, 3.0)
    b = Point(0.5, -2.0, 3.0)
    assert a + b == Point(1.5, 0.0, 6.0)
    assert a - b == Point(0.5, 4.0, 0.0)
    assert Point(3.0, 4.0, 0.0).norm() == pytest.approx(5.0)
    assert a.distance_to(b) == pytest.approx(math.sqrt(0.25 + 16.0))


def test_point_coerces_ints_and_rejects_non_finite():
    """Ints become floats; NaN, infinity, bools and strings are rejected."""
    assert Point(1, 2, 3) == Point(1.0, 2.0, 3.0)
    with pytest.raises(ValueError):
        Point(float('nan'), 0.0, 0.0)
    with pytest.raises(ValueError):
        Point(0.0, float('inf'), 0.0)
    with pytest.raises(TypeError):
        Point('0.0', 0.0, 0.0)
    with pytest.raises(TypeError):
        Point(True, 0.0, 0.0)


def test_point_is_frozen():
    """Geometry values are immutable."""
    with pytest.raises(FrozenInstanceError):
        Point(1.0, 2.0, 3.0).x = 9.0


def test_quaternion_defaults_to_identity_and_rejects_zero():
    """The default quaternion is the identity; an all-zero rotation is invalid."""
    assert Quaternion() == Quaternion.identity() == Quaternion(0.0, 0.0, 0.0, 1.0)
    assert Quaternion().norm() == pytest.approx(1.0)
    with pytest.raises(ValueError):
        Quaternion(0.0, 0.0, 0.0, 0.0)


def test_pose_helpers():
    """Pose helpers translate and replace position while keeping orientation."""
    rotated = Quaternion(0.0, 0.0, 0.7071, 0.7071)
    pose = Pose(Point(1.0, 1.0, 0.0), rotated)
    moved = pose.translated(Point(0.0, 1.0, 0.5))
    assert moved.position == Point(1.0, 2.0, 0.5)
    assert moved.orientation == rotated
    assert pose.with_position(Point(0.0, 0.0, 0.0)).orientation == rotated
    assert pose.distance_to(moved) == pytest.approx(math.sqrt(1.0 + 0.25))


def test_pose_rejects_wrong_member_types():
    """A pose is built from a Point and a Quaternion, nothing else."""
    with pytest.raises(TypeError):
        Pose(position=(1.0, 2.0, 3.0))
    with pytest.raises(TypeError):
        Pose(position=Point(), orientation=Point())


def test_pose_round_trip_and_default_orientation(round_trip):
    """Poses round-trip; orientation may be omitted and defaults to identity."""
    pose = Pose(Point(0.1, -0.2, 0.9), Quaternion(0.0, 0.0, 1.0, 0.0))
    round_trip(pose)
    round_trip(Point(1.0, 2.0, 3.0))
    round_trip(Quaternion(0.0, 0.0, 0.7071, 0.7071))
    assert Pose.from_dict({'position': {'x': 1.0, 'y': 2.0, 'z': 3.0}}) == Pose.from_xyz(
        1.0, 2.0, 3.0)


def test_pose_parsing_is_strict():
    """Missing, unknown and wrongly typed keys are reported, not ignored."""
    with pytest.raises(SerializationError, match='missing required key'):
        Pose.from_dict({})
    with pytest.raises(SerializationError, match='unknown key'):
        Pose.from_dict({'position': {'x': 0.0, 'y': 0.0, 'z': 0.0}, 'frame': 'map'})
    with pytest.raises(SerializationError, match='expected a number'):
        Point.from_dict({'x': 'near', 'y': 0.0, 'z': 0.0})
    with pytest.raises(SerializationError, match='expected a mapping'):
        Pose.from_dict({'position': [1.0, 2.0, 3.0]})
