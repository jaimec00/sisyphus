# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world document: the one JSON schema both world files are written in.

A :class:`WorldDocument` is the whole scene as data -- named locations, the
object registry, and where the robot starts -- and nothing else.  The *same*
schema describes the read-only seed and the written live-state file (D23), so
there is one parser, one writer, and "restore the scene from the seed" is a
file-level operation instead of a second code path.

What is deliberately **not** in here:

* **The robot's body.**  Shoulder offsets, reach radius and column travel are
  hardware description, not world state; they stay with the backend and later
  come from the URDF/MJCF.  A world file describes the scene, never the robot.
* **Live proprioception.**  Base pose, column height and gripper posture are
  the robot's, not the world's, and the Mock's arm offsets are backend
  internals that must not be baked into a file the MuJoCo backend will inherit.
  ``start_location``/``start_column_height`` are *scene* parameters (where a
  robot comes up in this apartment), not a live reading.

``held_by`` **is** here, because "the mug is in the left gripper" is a fact
about the mug.  A backend coming up against an existing live file starts with
empty grippers and therefore clears it (see :mod:`robot_world.store`).  A
gripper has one hand, so at most one object may name each side:
:func:`duplicate_hold_sides` is that rule, enforced here for a whole scene and
again on every store mutation, so nobody reading the world -- a file, a
document or a live store -- can find one gripper holding two things.

Parsing is strict in the same way the skill API is (see
:mod:`robot_skills.serialization`, whose helpers are reused verbatim): unknown
keys, missing keys and wrong types all raise
:class:`~robot_skills.SerializationError`.

The version stamp
-----------------
:data:`WORLD_SCHEMA_VERSION` is this file format's **own** counter, stamped
under :data:`WORLD_SCHEMA_VERSION_KEY`.  It is deliberately *not*
``robot_skills.SCHEMA_VERSION`` (D18): the skill API is a wire format between
brain and robot, this is an on-disk format between the robot and its own past
self, and the two version independently.  As with D18, an absent stamp reads as
the current version and a *different* stamp is a loud error.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Self

from robot_skills import Pose, SerializationError, Side
from robot_skills.serialization import (
    check_keys,
    ensure_mapping,
    get_bool,
    get_float,
    get_mapping,
    get_optional_enum,
    get_sequence,
    get_str,
    JsonDict,
    JsonSerializable,
    parse_errors,
)
from robot_skills.validation import as_finite_float, as_identifier, as_optional_enum

__all__ = [
    'check_world_schema_version',
    'duplicate_hold_sides',
    'WORLD_SCHEMA_VERSION',
    'WORLD_SCHEMA_VERSION_KEY',
    'WorldDocument',
    'WorldObject',
]

#: Version of the on-disk world document format; see the module docstring.
WORLD_SCHEMA_VERSION = 1

#: Key the world-file version stamp travels under.  Not ``schema_version``:
#: sharing the key with D18's skill-API stamp would invite conflating two
#: counters that are free to move independently.
WORLD_SCHEMA_VERSION_KEY = 'world_schema_version'


def check_world_schema_version(data: Mapping[str, Any], *, context: str) -> None:
    """Verify a world document's version stamp, if it carries one.

    Mirrors :func:`robot_skills.serialization.check_schema_version` against
    :data:`WORLD_SCHEMA_VERSION`: an absent stamp means "the current version"
    (so an added optional field stays non-breaking), while a stamp naming
    another version is refused rather than guessed at.
    """
    if WORLD_SCHEMA_VERSION_KEY not in data:
        return
    value = data[WORLD_SCHEMA_VERSION_KEY]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(
            f'{context}.{WORLD_SCHEMA_VERSION_KEY}: expected an integer, '
            f'got {type(value).__name__}')
    if value != WORLD_SCHEMA_VERSION:
        raise SerializationError(
            f'{context}.{WORLD_SCHEMA_VERSION_KEY}: unsupported world schema version '
            f'{value} (this build speaks version {WORLD_SCHEMA_VERSION} only)')


@dataclass(frozen=True)
class WorldObject(JsonSerializable):
    """One registered object: what it is, where it is, and who holds it.

    Immutable, like every other value that crosses a seam in this repo: the
    store replaces entries rather than mutating them, so an object handed out
    by a query cannot change under its reader.
    """

    object_id: str
    label: str
    pose: Pose
    graspable: bool = True
    held_by: Side | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'object_id', as_identifier(self.object_id, name='WorldObject.object_id'))
        object.__setattr__(
            self, 'label', as_identifier(self.label, name='WorldObject.label'))
        if not isinstance(self.pose, Pose):
            raise TypeError(
                f'WorldObject.pose must be a Pose, got {type(self.pose).__name__}')
        if not isinstance(self.graspable, bool):
            raise TypeError(
                f'WorldObject.graspable must be a bool, got {type(self.graspable).__name__}')
        object.__setattr__(
            self,
            'held_by',
            as_optional_enum(self.held_by, Side, name='WorldObject.held_by'),
        )

    def to_dict(self) -> JsonDict:
        """Return the object's JSON-safe dict form."""
        return {
            'object_id': self.object_id,
            'label': self.label,
            'pose': self.pose.to_dict(),
            'graspable': self.graspable,
            'held_by': None if self.held_by is None else self.held_by.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`WorldObject` from its dict form."""
        data = ensure_mapping(data, context='WorldObject')
        check_keys(
            data,
            required=('object_id', 'label', 'pose'),
            optional=('graspable', 'held_by'),
            context='WorldObject',
        )
        pose = Pose.from_dict(get_mapping(data, 'pose', context='WorldObject'))
        graspable = True
        if 'graspable' in data:
            graspable = get_bool(data, 'graspable', context='WorldObject')
        with parse_errors('WorldObject'):
            return cls(
                object_id=get_str(data, 'object_id', context='WorldObject'),
                label=get_str(data, 'label', context='WorldObject'),
                pose=pose,
                graspable=graspable,
                held_by=get_optional_enum(data, 'held_by', Side, context='WorldObject'),
            )


def duplicate_hold_sides(objects: Iterable[WorldObject]) -> list[Side]:
    """Return every gripper side claimed by more than one of ``objects``.

    The one definition of "``held_by`` is unique": a gripper has one hand, so at
    most one object may name each :class:`~robot_skills.Side` at a time (any
    number may be held by nobody).  Both places that enforce it --
    :meth:`WorldDocument.__post_init__` for whole scenes, and
    :class:`~robot_world.WorldStore`'s mutators for a live registry -- call this
    rather than re-deriving it, because two copies of an invariant drift.

    The result is in :class:`~robot_skills.Side` declaration order, so a message
    built from it is stable; it is empty for a scene that holds the invariant.
    """
    sides = [item.held_by for item in objects if item.held_by is not None]
    return [side for side in Side if sides.count(side) > 1]


@dataclass(frozen=True)
class WorldDocument(JsonSerializable):
    """A whole scene: the map, the object registry, and the start parameters.

    Object order is preserved as written rather than sorted, so a hand-curated
    seed file keeps reading in the order its author grouped it (kitchen things
    together, table things together) and a round trip is byte-stable.
    Consumers that need a canonical order sort at the point of use --
    ``Observation`` already does.
    """

    locations: Mapping[str, Pose]
    start_location: str
    objects: tuple[WorldObject, ...] = ()
    start_column_height: float = 0.3

    def __post_init__(self) -> None:
        locations = dict(self.locations)
        for name, pose in locations.items():
            as_identifier(name, name='WorldDocument.locations key')
            if not isinstance(pose, Pose):
                raise TypeError(
                    f'WorldDocument.locations[{name!r}] must be a Pose, '
                    f'got {type(pose).__name__}')
        if not locations:
            raise ValueError('WorldDocument.locations must not be empty')
        object.__setattr__(self, 'locations', MappingProxyType(locations))

        objects = tuple(self.objects)
        for item in objects:
            if not isinstance(item, WorldObject):
                raise TypeError(
                    'WorldDocument.objects must contain WorldObject values, '
                    f'got {type(item).__name__}')
        ids = [item.object_id for item in objects]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(
                f'WorldDocument.objects has duplicate object_id(s): {", ".join(duplicates)}')
        held_twice = duplicate_hold_sides(objects)
        if held_twice:
            raise ValueError(
                'WorldDocument.objects has more than one object held by the same '
                f'gripper: {", ".join(side.value for side in held_twice)}')
        object.__setattr__(self, 'objects', objects)

        start = as_identifier(self.start_location, name='WorldDocument.start_location')
        if start not in locations:
            raise ValueError(
                f'WorldDocument.start_location {start!r} is not one of the locations: '
                f'{", ".join(sorted(locations))}')
        object.__setattr__(
            self,
            'start_column_height',
            as_finite_float(
                self.start_column_height, name='WorldDocument.start_column_height'),
        )

    @property
    def start_pose(self) -> Pose:
        """Return the base pose a robot comes up (and resets) at."""
        return self.locations[self.start_location]

    def find_object(self, object_id: str) -> WorldObject | None:
        """Return the registered object with this id, or ``None`` if absent.

        Named to match :meth:`robot_skills.Observation.find_object`, the same
        lookup one layer up.
        """
        for item in self.objects:
            if item.object_id == object_id:
                return item
        return None

    def to_dict(self) -> JsonDict:
        """Return the document's JSON-safe dict form, version stamp included."""
        return {
            WORLD_SCHEMA_VERSION_KEY: WORLD_SCHEMA_VERSION,
            'start_location': self.start_location,
            'start_column_height': self.start_column_height,
            'locations': {
                name: pose.to_dict() for name, pose in self.locations.items()
            },
            'objects': [item.to_dict() for item in self.objects],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`WorldDocument` from its dict form."""
        data = ensure_mapping(data, context='WorldDocument')
        check_world_schema_version(data, context='WorldDocument')
        check_keys(
            data,
            required=('start_location', 'locations'),
            optional=(
                'objects', 'start_column_height', WORLD_SCHEMA_VERSION_KEY),
            context='WorldDocument',
        )
        raw_locations = get_mapping(data, 'locations', context='WorldDocument')
        locations = {
            name: Pose.from_dict(
                get_mapping(raw_locations, name, context='WorldDocument.locations'))
            for name in raw_locations
        }
        objects = tuple(
            WorldObject.from_dict(
                ensure_mapping(item, context='WorldDocument.objects[]'))
            for item in get_sequence(data, 'objects', context='WorldDocument')
        ) if 'objects' in data else ()
        start_column_height = 0.3
        if 'start_column_height' in data:
            start_column_height = get_float(
                data, 'start_column_height', context='WorldDocument')
        with parse_errors('WorldDocument'):
            return cls(
                locations=locations,
                start_location=get_str(data, 'start_location', context='WorldDocument'),
                objects=objects,
                start_column_height=start_column_height,
            )
