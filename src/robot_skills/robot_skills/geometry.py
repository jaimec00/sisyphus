# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Minimal, dependency-free geometry types used across the skill API.

Deliberately shaped like ``geometry_msgs/Point``, ``geometry_msgs/Quaternion``
and ``geometry_msgs/Pose`` so a later ROS 2 transport layer is a field-by-field
copy, but importable with no ROS graph and no ROS packages installed.

Units are SI: metres for positions, a unit quaternion for orientation.  Poses
carry no frame id yet; in this first iteration every pose is expressed in the
world/map frame (see ``docs/features/mock-skill-api/implementation.md``).
"""

from dataclasses import dataclass, field
from math import sqrt
from typing import Any, Mapping, Self

from robot_skills.serialization import (
    check_keys,
    ensure_mapping,
    get_float,
    get_mapping,
    JsonDict,
    JsonSerializable,
    parse_errors,
)
from robot_skills.validation import as_finite_float

__all__ = ['Point', 'Pose', 'Quaternion']


@dataclass(frozen=True)
class Point(JsonSerializable):
    """A 3D position in metres."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __post_init__(self) -> None:
        for name in ('x', 'y', 'z'):
            object.__setattr__(
                self, name, as_finite_float(getattr(self, name), name=f'Point.{name}'))

    def __add__(self, other: 'Point') -> 'Point':
        """Return the component-wise sum of two points."""
        if not isinstance(other, Point):
            return NotImplemented
        return Point(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: 'Point') -> 'Point':
        """Return the component-wise difference of two points."""
        if not isinstance(other, Point):
            return NotImplemented
        return Point(self.x - other.x, self.y - other.y, self.z - other.z)

    def norm(self) -> float:
        """Return the Euclidean length of this point seen as a vector."""
        return sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def distance_to(self, other: 'Point') -> float:
        """Return the Euclidean distance to ``other`` in metres."""
        return (self - other).norm()

    def to_dict(self) -> JsonDict:
        """Return ``{'x': ..., 'y': ..., 'z': ...}``."""
        return {'x': self.x, 'y': self.y, 'z': self.z}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`Point` from its dict form."""
        data = ensure_mapping(data, context='Point')
        check_keys(data, required=('x', 'y', 'z'), context='Point')
        with parse_errors('Point'):
            return cls(
                x=get_float(data, 'x', context='Point'),
                y=get_float(data, 'y', context='Point'),
                z=get_float(data, 'z', context='Point'),
            )


@dataclass(frozen=True)
class Quaternion(JsonSerializable):
    """An orientation as a quaternion; defaults to the identity rotation."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0

    def __post_init__(self) -> None:
        for name in ('x', 'y', 'z', 'w'):
            object.__setattr__(
                self, name, as_finite_float(getattr(self, name), name=f'Quaternion.{name}'))
        if self.norm() == 0.0:
            raise ValueError('Quaternion must not be all zeros')

    def norm(self) -> float:
        """Return the length of the quaternion (1.0 when normalized)."""
        return sqrt(self.x * self.x + self.y * self.y + self.z * self.z + self.w * self.w)

    @classmethod
    def identity(cls) -> 'Quaternion':
        """Return the identity rotation."""
        return cls()

    def to_dict(self) -> JsonDict:
        """Return ``{'x': ..., 'y': ..., 'z': ..., 'w': ...}``."""
        return {'x': self.x, 'y': self.y, 'z': self.z, 'w': self.w}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`Quaternion` from its dict form."""
        data = ensure_mapping(data, context='Quaternion')
        check_keys(data, required=('x', 'y', 'z', 'w'), context='Quaternion')
        with parse_errors('Quaternion'):
            return cls(
                x=get_float(data, 'x', context='Quaternion'),
                y=get_float(data, 'y', context='Quaternion'),
                z=get_float(data, 'z', context='Quaternion'),
                w=get_float(data, 'w', context='Quaternion'),
            )


@dataclass(frozen=True)
class Pose(JsonSerializable):
    """A 3D position plus orientation, in the world frame."""

    position: Point = field(default_factory=Point)
    orientation: Quaternion = field(default_factory=Quaternion)

    def __post_init__(self) -> None:
        if not isinstance(self.position, Point):
            raise TypeError(
                f'Pose.position must be a Point, got {type(self.position).__name__}')
        if not isinstance(self.orientation, Quaternion):
            raise TypeError(
                'Pose.orientation must be a Quaternion, '
                f'got {type(self.orientation).__name__}')

    @classmethod
    def from_xyz(cls, x: float, y: float, z: float = 0.0) -> 'Pose':
        """Return a pose at ``(x, y, z)`` with the identity orientation."""
        return cls(position=Point(x, y, z))

    def translated(self, offset: Point) -> 'Pose':
        """Return a copy of this pose shifted by ``offset``, keeping orientation."""
        if not isinstance(offset, Point):
            raise TypeError(f'offset must be a Point, got {type(offset).__name__}')
        return Pose(position=self.position + offset, orientation=self.orientation)

    def with_position(self, position: Point) -> 'Pose':
        """Return a copy of this pose with a different position."""
        return Pose(position=position, orientation=self.orientation)

    def distance_to(self, other: 'Pose') -> float:
        """Return the translational distance to ``other`` in metres."""
        if not isinstance(other, Pose):
            raise TypeError(f'other must be a Pose, got {type(other).__name__}')
        return self.position.distance_to(other.position)

    def to_dict(self) -> JsonDict:
        """Return ``{'position': {...}, 'orientation': {...}}``."""
        return {
            'position': self.position.to_dict(),
            'orientation': self.orientation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`Pose` from its dict form."""
        data = ensure_mapping(data, context='Pose')
        check_keys(data, required=('position',), optional=('orientation',), context='Pose')
        orientation = Quaternion()
        if data.get('orientation') is not None:
            orientation = Quaternion.from_dict(
                get_mapping(data, 'orientation', context='Pose'))
        position = Point.from_dict(get_mapping(data, 'position', context='Pose'))
        with parse_errors('Pose'):
            return cls(position=position, orientation=orientation)
