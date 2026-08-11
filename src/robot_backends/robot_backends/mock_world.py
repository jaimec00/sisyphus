# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The immutable world description the Mock backend is seeded from.

A :class:`MockWorld` is *data*: named locations, objects with poses, where the
robot starts, and a crude :class:`RobotModel` standing in for kinematics.  It
holds no state -- :class:`~robot_backends.mock_backend.MockBackend` copies it
into mutable state on construction and on every ``reset()``, which is what
makes runs reproducible without any clock or random number generator.

Tests that need a different situation build their own :class:`MockWorld`
rather than reaching into a backend's internals.
"""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from robot_skills import Point, Pose, Side
from robot_skills.validation import as_finite_float, as_identifier

__all__ = ['MockWorld', 'ObjectSpec', 'RobotModel', 'default_world']


@dataclass(frozen=True)
class ObjectSpec:
    """One object placed in the mock world at a known world-frame pose."""

    object_id: str
    label: str
    pose: Pose
    graspable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'object_id', as_identifier(self.object_id, name='ObjectSpec.object_id'))
        object.__setattr__(self, 'label', as_identifier(self.label, name='ObjectSpec.label'))
        if not isinstance(self.pose, Pose):
            raise TypeError(
                f'ObjectSpec.pose must be a Pose, got {type(self.pose).__name__}')
        if not isinstance(self.graspable, bool):
            raise TypeError(
                f'ObjectSpec.graspable must be a bool, got {type(self.graspable).__name__}')


@dataclass(frozen=True)
class RobotModel:
    """A deliberately crude kinematic stand-in for the two-arm robot.

    There is no IK here -- that lives below the skill API in a real backend.
    All the Mock needs is a defensible notion of *where a gripper is* and *what
    it can touch*, so that skills fail for physical reasons a real robot would
    also fail for (too far, column out of travel) instead of always succeeding.

    Distances are metres.  A shoulder sits ``shoulder_offset_y`` to the side of
    the base and ``shoulder_offset_z`` above the top of the lift column; a
    gripper may be anywhere within ``reach_radius`` of its own shoulder.
    """

    shoulder_offset_y: float = 0.18
    shoulder_offset_z: float = 0.50
    reach_radius: float = 0.85
    home_gripper_offset: Point = Point(0.35, 0.0, -0.05)
    min_column_height: float = 0.0
    max_column_height: float = 1.20

    def __post_init__(self) -> None:
        for name in ('shoulder_offset_y', 'shoulder_offset_z', 'reach_radius'):
            object.__setattr__(
                self, name, as_finite_float(getattr(self, name), name=f'RobotModel.{name}'))
        object.__setattr__(
            self,
            'min_column_height',
            as_finite_float(self.min_column_height, name='RobotModel.min_column_height'),
        )
        object.__setattr__(
            self,
            'max_column_height',
            as_finite_float(self.max_column_height, name='RobotModel.max_column_height'),
        )
        if self.reach_radius <= 0.0:
            raise ValueError('RobotModel.reach_radius must be positive')
        if self.min_column_height > self.max_column_height:
            raise ValueError(
                'RobotModel.min_column_height must not exceed max_column_height')
        if not isinstance(self.home_gripper_offset, Point):
            raise TypeError('RobotModel.home_gripper_offset must be a Point')
        if self.home_gripper_offset.norm() > self.reach_radius:
            raise ValueError(
                'RobotModel.home_gripper_offset must lie within reach_radius')

    def shoulder(self, base_pose: Pose, column_height: float, side: Side) -> Point:
        """Return the world-frame shoulder point of one arm.

        Base orientation is ignored on purpose: the Mock reasons about
        distances, not headings, and a real backend replaces this wholesale.
        """
        lateral = self.shoulder_offset_y if side is Side.LEFT else -self.shoulder_offset_y
        return base_pose.position + Point(
            0.0, lateral, column_height + self.shoulder_offset_z)

    def column_range_text(self) -> str:
        """Return the column travel range, formatted for a failure reason."""
        return f'[{self.min_column_height:.2f}, {self.max_column_height:.2f}] m'


@dataclass(frozen=True)
class MockWorld:
    """The seed state of a mock scene: locations, objects, and where we start."""

    locations: Mapping[str, Pose]
    start_location: str
    objects: tuple[ObjectSpec, ...] = ()
    start_column_height: float = 0.3
    robot: RobotModel = field(default_factory=RobotModel)

    def __post_init__(self) -> None:
        locations = dict(self.locations)
        for name, pose in locations.items():
            as_identifier(name, name='MockWorld.locations key')
            if not isinstance(pose, Pose):
                raise TypeError(
                    f'MockWorld.locations[{name!r}] must be a Pose, '
                    f'got {type(pose).__name__}')
        if not locations:
            raise ValueError('MockWorld.locations must not be empty')
        object.__setattr__(self, 'locations', MappingProxyType(locations))

        objects = tuple(self.objects)
        for item in objects:
            if not isinstance(item, ObjectSpec):
                raise TypeError(
                    'MockWorld.objects must contain ObjectSpec values, '
                    f'got {type(item).__name__}')
        ids = [item.object_id for item in objects]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(
                f'MockWorld.objects has duplicate object_id(s): {", ".join(duplicates)}')
        object.__setattr__(self, 'objects', objects)

        start = as_identifier(self.start_location, name='MockWorld.start_location')
        if start not in locations:
            raise ValueError(
                f'MockWorld.start_location {start!r} is not one of the locations: '
                f'{", ".join(sorted(locations))}')
        object.__setattr__(
            self,
            'start_column_height',
            as_finite_float(self.start_column_height, name='MockWorld.start_column_height'),
        )
        if not isinstance(self.robot, RobotModel):
            raise TypeError('MockWorld.robot must be a RobotModel')
        if not (
            self.robot.min_column_height
            <= self.start_column_height
            <= self.robot.max_column_height
        ):
            raise ValueError(
                f'MockWorld.start_column_height {self.start_column_height} is outside '
                f'the column range {self.robot.column_range_text()}')

    @property
    def start_pose(self) -> Pose:
        """Return the base pose the robot starts (and resets) at."""
        return self.locations[self.start_location]


def default_world() -> MockWorld:
    """Return the standard demo apartment used by the Mock backend and tests.

    Four named locations (``charger``, ``kitchen``, ``table``,
    ``living_room``) and seven objects, including the graspable ``mug_1`` on
    the kitchen counter that the end-to-end scenario carries to the table.
    Object poses are chosen so that reaching one requires standing at the right
    location: grasping ``mug_1`` from the charger is out of reach.

    The kitchen holds three graspable objects (``mug_1``, ``plate_1``,
    ``bowl_1``) so both grippers can be filled and a third grasp still tried,
    plus the ungraspable ``counter_1`` they stand on.

    The table holds **two** graspable objects (``book_1``, ``cup_1``), which is
    what makes "clear the table" a *loop* rather than a single grasp: a brain
    driving this world has to notice there is a second thing left.  Both are
    within reach of either shoulder from the ``table`` stand point at the
    starting column height (0.3 m puts a shoulder at z = 0.8 m; the furthest of
    the two is ~0.41 m away, well inside the 0.85 m reach), so neither needs an
    ``extend_column`` first.
    """
    return MockWorld(
        locations={
            'charger': Pose.from_xyz(0.0, 0.0, 0.0),
            'kitchen': Pose.from_xyz(2.0, 0.0, 0.0),
            'table': Pose.from_xyz(0.0, 2.0, 0.0),
            'living_room': Pose.from_xyz(-2.0, 1.0, 0.0),
        },
        start_location='charger',
        objects=(
            ObjectSpec('mug_1', 'mug', Pose.from_xyz(2.30, 0.10, 0.90), graspable=True),
            ObjectSpec('plate_1', 'plate', Pose.from_xyz(2.30, -0.10, 0.90), graspable=True),
            ObjectSpec('bowl_1', 'bowl', Pose.from_xyz(2.25, 0.00, 0.92), graspable=True),
            ObjectSpec(
                'counter_1', 'counter', Pose.from_xyz(2.40, 0.00, 0.45), graspable=False),
            ObjectSpec('book_1', 'book', Pose.from_xyz(0.30, 2.10, 0.75), graspable=True),
            ObjectSpec('cup_1', 'cup', Pose.from_xyz(0.30, 1.90, 0.75), graspable=True),
            ObjectSpec('sofa_1', 'sofa', Pose.from_xyz(-2.00, 1.60, 0.40), graspable=False),
        ),
        start_column_height=0.3,
    )
