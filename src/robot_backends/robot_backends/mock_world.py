# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The immutable world description the Mock backend is seeded from.

A :class:`MockWorld` is *data*: named locations, objects with poses, where the
robot starts, and a crude :class:`RobotModel` standing in for kinematics.  It
holds no state -- the live scene lives in a
:class:`~robot_world.WorldStore` (D23), which
:class:`~robot_backends.mock_backend.MockBackend` reads and mutates, and which
``reset()`` restores from its seed.  Nothing here has a clock or a random
number generator, which is what keeps runs reproducible.

Since D23 the *scene* half of a world -- locations, objects, start parameters
-- is a :class:`~robot_world.WorldDocument`, and :func:`default_world` loads
the seed **file** shipped with ``robot_world`` rather than building a Python
literal.  :class:`MockWorld` survives as the Mock's own view of that scene
plus the one thing a world file must never describe: the robot's body
(:class:`RobotModel`).  :func:`world_from_document` and
:func:`world_to_document` convert between the two.

Tests that need a different situation build their own :class:`MockWorld`
rather than reaching into a backend's internals.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from types import MappingProxyType
from typing import Mapping

from robot_description.robot_model import load_robot_model
from robot_skills import Point, Pose, Side
from robot_skills.validation import as_finite_float, as_identifier
from robot_world import default_seed_document, WorldDocument, WorldObject

__all__ = [
    'default_world',
    'MockWorld',
    'ObjectSpec',
    'RobotModel',
    'world_from_document',
    'world_to_document',
]


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


@lru_cache(maxsize=1)
def _urdf_defaults():
    """Load the shipped URDF's kinematic constants once and memoize them (D23).

    Cached because it expands ``xacro`` and parses the model: the cost is
    a one-time import-time hit, not a per-:class:`RobotModel` one.  The loader
    lives in ``robot_description`` -- this package depends on it one-way, and
    the description never imports back, so there is no cycle (see status.md R1).
    """
    return load_robot_model()


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

    Since D23's payoff (PR6) the *defaults* come from the shipped URDF, read by
    :func:`_urdf_defaults`, rather than from literals here -- so Mock and the
    future MuJoCo backend share one source of truth.  Each field is a
    ``default_factory`` reading that one value, which is what keeps the
    dataclass an explicit-override surface: ``RobotModel(reach_radius=0.5)``
    still works, and only the fields the caller leaves out fall back to the
    URDF.
    """

    shoulder_offset_y: float = field(
        default_factory=lambda: _urdf_defaults().shoulder_offset_y)
    shoulder_offset_z: float = field(
        default_factory=lambda: _urdf_defaults().shoulder_offset_z)
    reach_radius: float = field(
        default_factory=lambda: _urdf_defaults().reach_radius)
    home_gripper_offset: Point = field(
        default_factory=lambda: Point(*_urdf_defaults().home_gripper_offset))
    min_column_height: float = field(
        default_factory=lambda: _urdf_defaults().min_column_height)
    max_column_height: float = field(
        default_factory=lambda: _urdf_defaults().max_column_height)

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


def world_from_document(
    document: WorldDocument,
    robot: RobotModel | None = None,
) -> MockWorld:
    """Build a :class:`MockWorld` from a world document plus a robot model.

    ``held_by`` is dropped: a :class:`MockWorld` is a *seed* description, and
    "who is holding this" is live state belonging to the store and the robot.
    """
    if not isinstance(document, WorldDocument):
        raise TypeError(
            f'document must be a WorldDocument, got {type(document).__name__}')
    return MockWorld(
        locations=dict(document.locations),
        start_location=document.start_location,
        objects=tuple(
            ObjectSpec(
                object_id=item.object_id,
                label=item.label,
                pose=item.pose,
                graspable=item.graspable,
            )
            for item in document.objects
        ),
        start_column_height=document.start_column_height,
        robot=robot if robot is not None else RobotModel(),
    )


def world_to_document(world: MockWorld) -> WorldDocument:
    """Build a world document from a :class:`MockWorld`, dropping its robot model.

    The inverse of :func:`world_from_document` for everything a world *file*
    may describe: the robot's body is hardware description and never travels
    in a document (D23).
    """
    if not isinstance(world, MockWorld):
        raise TypeError(f'world must be a MockWorld, got {type(world).__name__}')
    return WorldDocument(
        locations=dict(world.locations),
        start_location=world.start_location,
        objects=tuple(
            WorldObject(
                object_id=spec.object_id,
                label=spec.label,
                pose=spec.pose,
                graspable=spec.graspable,
            )
            for spec in world.objects
        ),
        start_column_height=world.start_column_height,
    )


def default_world() -> MockWorld:
    """Return the standard demo apartment used by the Mock backend and tests.

    Loaded from the seed file shipped with ``robot_world``
    (``robot_world/default_world.json``), not from a literal here: since D23
    the scene is data on disk, and this function is the Mock-shaped view of it.

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
    return world_from_document(default_seed_document())
