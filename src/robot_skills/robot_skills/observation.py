# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Structured perception: what the robot reports back after every skill.

CLAUDE.md invariant 4 and decision D3: an observation is *data with
coordinates*, never a prose caption.  An :class:`Observation` is an immutable
snapshot of the whole world as the robot currently believes it -- robot pose,
current named location, column height, both grippers, and every known object
with its id, label, 3D pose and graspability.

Immutability is load-bearing: the brain (or a test) holding an observation can
never reach through it and mutate a backend's world model, so comparing two
snapshots is a sound way to prove that a failed skill changed nothing.
"""

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Mapping, Self

from robot_skills.geometry import Pose
from robot_skills.serialization import (
    check_keys,
    check_schema_version,
    ensure_mapping,
    get_bool,
    get_enum,
    get_float,
    get_mapping,
    get_optional_enum,
    get_optional_str,
    get_sequence,
    get_str,
    JsonDict,
    JsonSerializable,
    parse_errors,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    SerializationError,
)
from robot_skills.skills import Side, SIDE_ORDER
from robot_skills.validation import as_finite_float, as_identifier

__all__ = [
    'GripperObservation',
    'GripperState',
    'Observation',
    'RobotState',
    'SceneObject',
]


@unique
class GripperState(Enum):
    """Whether a gripper's jaws are open or closed."""

    OPEN = 'open'
    CLOSED = 'closed'


@dataclass(frozen=True)
class SceneObject(JsonSerializable):
    """One perceived object: identity, semantic label, 3D pose, graspability.

    ``held_by`` is set when a gripper is currently holding the object; it
    always agrees with the matching :attr:`GripperObservation.held_object_id`.
    """

    object_id: str
    label: str
    pose: Pose
    graspable: bool = True
    held_by: Side | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'object_id', as_identifier(self.object_id, name='SceneObject.object_id'))
        object.__setattr__(
            self, 'label', as_identifier(self.label, name='SceneObject.label'))
        if not isinstance(self.pose, Pose):
            raise TypeError(
                f'SceneObject.pose must be a Pose, got {type(self.pose).__name__}')
        if not isinstance(self.graspable, bool):
            raise TypeError(
                f'SceneObject.graspable must be a bool, got {type(self.graspable).__name__}')
        if self.held_by is not None and not isinstance(self.held_by, Side):
            raise TypeError(
                f'SceneObject.held_by must be a Side or None, '
                f'got {type(self.held_by).__name__}')

    @property
    def is_held(self) -> bool:
        """Return whether a gripper currently holds this object."""
        return self.held_by is not None

    def to_dict(self) -> JsonDict:
        """Return this object's JSON-safe dict form."""
        return {
            'object_id': self.object_id,
            'label': self.label,
            'pose': self.pose.to_dict(),
            'graspable': self.graspable,
            'held_by': None if self.held_by is None else self.held_by.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`SceneObject` from its dict form."""
        context = cls.__name__
        data = ensure_mapping(data, context=context)
        check_keys(
            data,
            required=('object_id', 'label', 'pose'),
            optional=('graspable', 'held_by'),
            context=context,
        )
        graspable = True
        if 'graspable' in data:
            graspable = get_bool(data, 'graspable', context=context)
        with parse_errors(context):
            return cls(
                object_id=get_str(data, 'object_id', context=context),
                label=get_str(data, 'label', context=context),
                pose=Pose.from_dict(get_mapping(data, 'pose', context=context)),
                graspable=graspable,
                held_by=get_optional_enum(data, 'held_by', Side, context=context),
            )


@dataclass(frozen=True)
class GripperObservation(JsonSerializable):
    """The state of one gripper: which side, open/closed, where, holding what.

    ``grasped`` and ``held_object_id`` answer two different questions on
    purpose (D19).  ``held_object_id`` is a *world-model* fact -- **which**
    known object this gripper carries -- while ``grasped`` is a *sensed* fact:
    the jaws report a load (aperture short of closed, contact force present).
    They diverge on a real robot, which can feel an unidentified object it
    cannot name, so the brain gets both: ``grasped`` answers "did I get it?"
    after a ``close_gripper``, ``held_object_id`` answers "get what?".

    The one combination that cannot happen is holding a named object without
    gripping it, and the constructor rejects it; the Mock, which has no force
    sensing, derives ``grasped`` from what it holds.

    **Contract for a backend with real sensing.**  That rejection makes "the
    world model still thinks it carries ``mug_1``, but the jaws report empty" --
    a dropped object -- unrepresentable, on purpose: a stale ``held_object_id``
    is a lie the brain would plan against.  On a detected drop the backend must
    therefore clear ``held_object_id`` (and the matching
    :attr:`SceneObject.held_by`) in the *same* update that reports
    ``grasped=False``, and place the object where it believes it fell.  It must
    not build the observation from a half-updated world model: the constructor
    raises a ``ValueError`` inside ``get_observation()``, where there is no
    parse boundary to translate it.
    """

    side: Side
    state: GripperState
    pose: Pose
    held_object_id: str | None = None
    grasped: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.side, Side):
            raise TypeError(
                f'GripperObservation.side must be a Side, got {type(self.side).__name__}')
        if not isinstance(self.state, GripperState):
            raise TypeError(
                'GripperObservation.state must be a GripperState, '
                f'got {type(self.state).__name__}')
        if not isinstance(self.pose, Pose):
            raise TypeError(
                f'GripperObservation.pose must be a Pose, got {type(self.pose).__name__}')
        if not isinstance(self.grasped, bool):
            raise TypeError(
                'GripperObservation.grasped must be a bool, '
                f'got {type(self.grasped).__name__}')
        if self.held_object_id is not None:
            object.__setattr__(
                self,
                'held_object_id',
                as_identifier(self.held_object_id, name='GripperObservation.held_object_id'),
            )
            if not self.grasped:
                raise ValueError(
                    f'GripperObservation: the {self.side.value} gripper reports holding '
                    f'{self.held_object_id!r} while grasped=False; a carried object is '
                    'gripped by definition')

    @property
    def is_holding(self) -> bool:
        """Return whether this gripper currently holds an *identified* object."""
        return self.held_object_id is not None

    def to_dict(self) -> JsonDict:
        """Return this gripper's JSON-safe dict form."""
        return {
            'side': self.side.value,
            'state': self.state.value,
            'pose': self.pose.to_dict(),
            'held_object_id': self.held_object_id,
            'grasped': self.grasped,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`GripperObservation` from its dict form."""
        context = cls.__name__
        data = ensure_mapping(data, context=context)
        check_keys(
            data,
            required=('side', 'state', 'pose'),
            optional=('held_object_id', 'grasped'),
            context=context,
        )
        held_object_id = get_optional_str(data, 'held_object_id', context=context)
        # ``grasped`` is an additive field (D18): a payload written before it
        # existed still says whether the gripper has a load -- via the object it
        # reports carrying -- so infer it rather than defaulting to "empty jaws"
        # and contradicting the rest of the same dict.
        grasped = held_object_id is not None
        if 'grasped' in data:
            grasped = get_bool(data, 'grasped', context=context)
        with parse_errors(context):
            return cls(
                side=get_enum(data, 'side', Side, context=context),
                state=get_enum(data, 'state', GripperState, context=context),
                pose=Pose.from_dict(get_mapping(data, 'pose', context=context)),
                held_object_id=held_object_id,
                grasped=grasped,
            )


@dataclass(frozen=True)
class RobotState(JsonSerializable):
    """Where the robot is and what its body is doing.

    ``location`` is the named spot in the semantic map the robot is currently
    at (``None`` when it is between/away from known locations); ``pose`` is the
    metric base pose that goes with it.
    """

    pose: Pose
    column_height: float
    grippers: tuple[GripperObservation, ...]
    location: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.pose, Pose):
            raise TypeError(f'RobotState.pose must be a Pose, got {type(self.pose).__name__}')
        object.__setattr__(
            self,
            'column_height',
            as_finite_float(self.column_height, name='RobotState.column_height'),
        )
        grippers = tuple(self.grippers)
        for gripper in grippers:
            if not isinstance(gripper, GripperObservation):
                raise TypeError(
                    'RobotState.grippers must contain GripperObservation values, '
                    f'got {type(gripper).__name__}')
        sides = [gripper.side for gripper in grippers]
        if sorted(side.value for side in sides) != sorted(side.value for side in SIDE_ORDER):
            raise ValueError(
                'RobotState.grippers must contain exactly one entry per side, '
                f'got {[side.value for side in sides]}')
        object.__setattr__(self, 'grippers', grippers)
        if self.location is not None:
            object.__setattr__(
                self, 'location', as_identifier(self.location, name='RobotState.location'))

    def gripper(self, side: Side) -> GripperObservation:
        """Return the observation for one gripper."""
        for gripper in self.grippers:
            if gripper.side is side:
                return gripper
        raise KeyError(f'no gripper observation for side {side!r}')

    def to_dict(self) -> JsonDict:
        """Return the robot state's JSON-safe dict form."""
        return {
            'pose': self.pose.to_dict(),
            'location': self.location,
            'column_height': self.column_height,
            'grippers': [gripper.to_dict() for gripper in self.grippers],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a :class:`RobotState` from its dict form."""
        context = cls.__name__
        data = ensure_mapping(data, context=context)
        check_keys(
            data,
            required=('pose', 'column_height', 'grippers'),
            optional=('location',),
            context=context,
        )
        grippers = tuple(
            GripperObservation.from_dict(ensure_mapping(item, context=f'{context}.grippers'))
            for item in get_sequence(data, 'grippers', context=context)
        )
        with parse_errors(context):
            return cls(
                pose=Pose.from_dict(get_mapping(data, 'pose', context=context)),
                column_height=get_float(data, 'column_height', context=context),
                grippers=grippers,
                location=get_optional_str(data, 'location', context=context),
            )


@dataclass(frozen=True)
class Observation(JsonSerializable):
    """An immutable snapshot of robot state plus the perceived scene.

    ``known_locations`` exposes the semantic map's vocabulary so the brain can
    only ever navigate to names that exist (see the ``unknown_location``
    failure path).

    Construction enforces that the two views of a held object agree: a
    gripper's ``held_object_id`` must name a perceived object whose ``held_by``
    is that same side, and vice versa.
    """

    robot: RobotState
    objects: tuple[SceneObject, ...] = ()
    known_locations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.robot, RobotState):
            raise TypeError(
                f'Observation.robot must be a RobotState, got {type(self.robot).__name__}')
        objects = tuple(self.objects)
        for item in objects:
            if not isinstance(item, SceneObject):
                raise TypeError(
                    'Observation.objects must contain SceneObject values, '
                    f'got {type(item).__name__}')
        ids = [item.object_id for item in objects]
        duplicates = sorted({name for name in ids if ids.count(name) > 1})
        if duplicates:
            raise ValueError(
                f'Observation.objects has duplicate object_id(s): {", ".join(duplicates)}')
        object.__setattr__(self, 'objects', objects)
        self._check_held_objects_agree(objects)
        locations = tuple(
            as_identifier(name, name='Observation.known_locations')
            for name in self.known_locations
        )
        object.__setattr__(self, 'known_locations', locations)

    def _check_held_objects_agree(self, objects: tuple[SceneObject, ...]) -> None:
        """Enforce that the gripper view and the object view report the same load.

        The redundancy is deliberate (the brain wants both views), so the type
        -- not just the backend that happens to fill it in -- has to guarantee
        the two projections cannot disagree.  A backend that reads gripper state
        and object attachment from two different sources (a simulator's joints
        and its weld constraints, say) fails loudly here instead of handing the
        brain a scene that contradicts itself.
        """
        by_id = {item.object_id: item for item in objects}
        for gripper in self.robot.grippers:
            held_id = gripper.held_object_id
            if held_id is None:
                continue
            item = by_id.get(held_id)
            if item is None:
                raise ValueError(
                    f'Observation: the {gripper.side.value} gripper reports holding '
                    f'{held_id!r}, which is not among the perceived objects')
            if item.held_by is not gripper.side:
                raise ValueError(
                    f'Observation: the {gripper.side.value} gripper reports holding '
                    f'{held_id!r}, but that object reports held_by='
                    f'{None if item.held_by is None else item.held_by.value}')
        for item in objects:
            if item.held_by is None:
                continue
            gripper = self.robot.gripper(item.held_by)
            if gripper.held_object_id != item.object_id:
                raise ValueError(
                    f'Observation: object {item.object_id!r} reports being held by the '
                    f'{item.held_by.value} gripper, but that gripper reports holding '
                    f'{gripper.held_object_id!r}')

    def find_object(self, object_id: str) -> SceneObject | None:
        """Return the object with ``object_id``, or ``None`` if not perceived."""
        for item in self.objects:
            if item.object_id == object_id:
                return item
        return None

    def objects_with_label(self, label: str) -> tuple[SceneObject, ...]:
        """Return every perceived object carrying ``label``."""
        return tuple(item for item in self.objects if item.label == label)

    def held_objects(self) -> tuple[SceneObject, ...]:
        """Return every object currently held by a gripper."""
        return tuple(item for item in self.objects if item.is_held)

    def to_dict(self) -> JsonDict:
        """Return the observation's JSON-safe dict form, version stamp included.

        The stamp travels with the type, not with an envelope, so an
        observation nested in a :class:`~robot_skills.result.SkillResult` still
        carries it (see the wire-format policy in
        :mod:`robot_skills.serialization`).
        """
        return {
            SCHEMA_VERSION_KEY: SCHEMA_VERSION,
            'robot': self.robot.to_dict(),
            'objects': [item.to_dict() for item in self.objects],
            'known_locations': list(self.known_locations),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild an :class:`Observation` from its dict form."""
        context = cls.__name__
        data = ensure_mapping(data, context=context)
        # Version first: a payload from another schema most likely trips
        # check_keys on a key that version added, and "unknown key(s): ..."
        # would send the reader hunting an LLM typo instead of a mismatch.
        check_schema_version(data, context=context)
        check_keys(
            data,
            required=('robot',),
            optional=(SCHEMA_VERSION_KEY, 'objects', 'known_locations'),
            context=context,
        )
        objects: tuple[SceneObject, ...] = ()
        if 'objects' in data:
            objects = tuple(
                SceneObject.from_dict(ensure_mapping(item, context=f'{context}.objects'))
                for item in get_sequence(data, 'objects', context=context)
            )
        locations: tuple[str, ...] = ()
        if 'known_locations' in data:
            locations = tuple(
                _as_location_name(item, context=context)
                for item in get_sequence(data, 'known_locations', context=context)
            )
        with parse_errors(context):
            return cls(
                robot=RobotState.from_dict(get_mapping(data, 'robot', context=context)),
                objects=objects,
                known_locations=locations,
            )


def _as_location_name(value: Any, *, context: str) -> str:
    """Return a location name parsed from an untrusted list entry."""
    if not isinstance(value, str):
        raise SerializationError(
            f'{context}.known_locations: expected strings, got {type(value).__name__}')
    return value
