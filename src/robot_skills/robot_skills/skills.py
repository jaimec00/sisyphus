# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The skill API: the typed commands the brain may send to a robot backend.

This module is *the* architectural seam (CLAUDE.md invariant 1).  Above it the
brain reasons about goals -- named locations, object ids, gripper poses; below
it a backend does IK, planning and motion.  Nothing here mentions a joint.

Every skill is a frozen dataclass, i.e. inert validated data.  That is what
makes the layers above and below composable without redesign:

* a **safety layer** can inspect, clamp (by building a new skill) or reject a
  skill before it ever reaches a backend, because a skill is not a call;
* a **ROS 2 action layer** can serialize a skill onto the wire with
  :meth:`Skill.to_dict` and rebuild it with :func:`skill_from_dict`;
* an **LLM tag parser** can map ``<grasp mug_1>`` onto :class:`Grasp` via the
  :data:`SKILL_TYPES` registry, which is keyed by the same wire names.

Naming note: "place" is overloaded in the design docs.  Here the *skill*
:class:`Place` always means "put the held object down"; a named spot in the
semantic map is always a **location** (:class:`NavigateTo.location`,
``RobotState.location``).  The word "place" is never used for a location.
"""

from abc import abstractmethod
from dataclasses import dataclass
from enum import Enum, unique
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Self

from robot_skills.geometry import Pose
from robot_skills.serialization import (
    check_keys,
    ensure_mapping,
    get_enum,
    get_float,
    get_mapping,
    get_optional_enum,
    get_str,
    JsonDict,
    JsonSerializable,
    SerializationError,
)
from robot_skills.validation import as_enum, as_finite_float, as_identifier, as_optional_enum

__all__ = [
    'SIDE_ORDER',
    'SKILL_KEY',
    'SKILL_TYPES',
    'CloseGripper',
    'ExtendColumn',
    'Grasp',
    'MoveGripper',
    'NavigateTo',
    'OpenGripper',
    'Place',
    'Side',
    'Skill',
    'skill_from_dict',
]

#: Key holding the skill's wire name in its dict form.
SKILL_KEY = 'skill'

_REGISTRY: dict[str, type['Skill']] = {}


@unique
class Side(Enum):
    """Which of the robot's two arms/grippers a skill addresses."""

    LEFT = 'left'
    RIGHT = 'right'


#: Deterministic preference order used whenever a skill leaves the arm implicit.
SIDE_ORDER: tuple[Side, ...] = (Side.LEFT, Side.RIGHT)


@dataclass(frozen=True)
class Skill(JsonSerializable):
    """Base class for every skill: an immutable, validated command object.

    Subclasses declare a unique wire ``name`` and their own payload fields.
    ``Skill.from_dict`` is polymorphic -- it dispatches on the ``skill`` key --
    while ``SubClass.from_dict`` additionally asserts the expected name.
    """

    name: ClassVar[str] = ''

    def __init_subclass__(cls, register: bool = True, **kwargs: Any) -> None:
        """Register a concrete subclass under its wire name.

        Pass ``register=False`` for a shared intermediate base class that is
        not itself a dispatchable skill.
        """
        super().__init_subclass__(**kwargs)
        if not register:
            return
        if not cls.name:
            raise TypeError(f'{cls.__name__} must declare a non-empty class-level name')
        existing = _REGISTRY.get(cls.name)
        if existing is not None and existing is not cls:
            raise TypeError(
                f'skill name {cls.name!r} is already registered to {existing.__name__}')
        _REGISTRY[cls.name] = cls

    @abstractmethod
    def _payload(self) -> JsonDict:
        """Return this skill's arguments as a JSON-safe dict (no ``skill`` key)."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a concrete skill from its dict form (``skill`` key included)."""
        raise NotImplementedError

    def to_dict(self) -> JsonDict:
        """Return ``{'skill': <wire name>, **arguments}``."""
        return {SKILL_KEY: self.name, **self._payload()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild a skill, dispatching on the ``skill`` key."""
        data = ensure_mapping(data, context=cls.__name__)
        if SKILL_KEY not in data:
            raise SerializationError(
                f'{cls.__name__}: missing required key: {SKILL_KEY}')
        wire_name = get_str(data, SKILL_KEY, context=cls.__name__)
        target = _REGISTRY.get(wire_name)
        if target is None:
            known = ', '.join(sorted(_REGISTRY))
            raise SerializationError(
                f'unknown skill {wire_name!r} (known skills: {known})')
        if cls is not Skill and target is not cls:
            raise SerializationError(
                f'{cls.__name__}.from_dict got skill {wire_name!r}')
        return target._from_payload(data)


def skill_from_dict(data: Mapping[str, Any]) -> Skill:
    """Rebuild any skill from its dict form (alias of ``Skill.from_dict``)."""
    return Skill.from_dict(data)


@dataclass(frozen=True)
class NavigateTo(Skill):
    """Drive the base to a named location in the semantic map."""

    name: ClassVar[str] = 'navigate_to'

    location: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'location', as_identifier(self.location, name='NavigateTo.location'))

    def _payload(self) -> JsonDict:
        return {'location': self.location}

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        context = cls.__name__
        check_keys(data, required=(SKILL_KEY, 'location'), context=context)
        return cls(location=get_str(data, 'location', context=context))


@dataclass(frozen=True)
class MoveGripper(Skill):
    """Move one gripper to a Cartesian pose (IK lives below this API)."""

    name: ClassVar[str] = 'move_gripper'

    side: Side
    pose: Pose

    def __post_init__(self) -> None:
        object.__setattr__(self, 'side', as_enum(self.side, Side, name='MoveGripper.side'))
        if not isinstance(self.pose, Pose):
            raise TypeError(
                f'MoveGripper.pose must be a Pose, got {type(self.pose).__name__}')

    def _payload(self) -> JsonDict:
        return {'side': self.side.value, 'pose': self.pose.to_dict()}

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        context = cls.__name__
        check_keys(data, required=(SKILL_KEY, 'side', 'pose'), context=context)
        return cls(
            side=get_enum(data, 'side', Side, context=context),
            pose=Pose.from_dict(get_mapping(data, 'pose', context=context)),
        )


@dataclass(frozen=True)
class Grasp(Skill):
    """Pick up a perceived object by id.

    ``side`` is optional: leaving it ``None`` lets the backend choose a free
    gripper deterministically (left first).
    """

    name: ClassVar[str] = 'grasp'

    object_id: str
    side: Side | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'object_id', as_identifier(self.object_id, name='Grasp.object_id'))
        object.__setattr__(
            self, 'side', as_optional_enum(self.side, Side, name='Grasp.side'))

    def _payload(self) -> JsonDict:
        return {
            'object_id': self.object_id,
            'side': None if self.side is None else self.side.value,
        }

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        context = cls.__name__
        check_keys(
            data, required=(SKILL_KEY, 'object_id'), optional=('side',), context=context)
        return cls(
            object_id=get_str(data, 'object_id', context=context),
            side=get_optional_enum(data, 'side', Side, context=context),
        )


@dataclass(frozen=True)
class Place(Skill):
    """Put the currently held object down at a Cartesian pose.

    ``side`` is optional: leaving it ``None`` lets the backend choose the
    holding gripper deterministically (left first).
    """

    name: ClassVar[str] = 'place'

    pose: Pose
    side: Side | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.pose, Pose):
            raise TypeError(f'Place.pose must be a Pose, got {type(self.pose).__name__}')
        object.__setattr__(
            self, 'side', as_optional_enum(self.side, Side, name='Place.side'))

    def _payload(self) -> JsonDict:
        return {
            'pose': self.pose.to_dict(),
            'side': None if self.side is None else self.side.value,
        }

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        context = cls.__name__
        check_keys(data, required=(SKILL_KEY, 'pose'), optional=('side',), context=context)
        return cls(
            pose=Pose.from_dict(get_mapping(data, 'pose', context=context)),
            side=get_optional_enum(data, 'side', Side, context=context),
        )


@dataclass(frozen=True)
class ExtendColumn(Skill):
    """Set the vertical lift column to an absolute height in metres."""

    name: ClassVar[str] = 'extend_column'

    height: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'height', as_finite_float(self.height, name='ExtendColumn.height'))

    def _payload(self) -> JsonDict:
        return {'height': self.height}

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        context = cls.__name__
        check_keys(data, required=(SKILL_KEY, 'height'), context=context)
        return cls(height=get_float(data, 'height', context=context))


@dataclass(frozen=True)
class _GripperSkill(Skill, register=False):
    """Shared implementation for the two single-argument gripper skills."""

    side: Side

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'side', as_enum(self.side, Side, name=f'{type(self).__name__}.side'))

    def _payload(self) -> JsonDict:
        return {'side': self.side.value}

    @classmethod
    def _from_payload(cls, data: Mapping[str, Any]) -> Self:
        context = cls.__name__
        check_keys(data, required=(SKILL_KEY, 'side'), context=context)
        return cls(side=get_enum(data, 'side', Side, context=context))


@dataclass(frozen=True)
class OpenGripper(_GripperSkill):
    """Open one gripper, releasing whatever it holds where the gripper is."""

    name: ClassVar[str] = 'open_gripper'


@dataclass(frozen=True)
class CloseGripper(_GripperSkill):
    """Close one gripper (a grip on nothing; use :class:`Grasp` to pick up)."""

    name: ClassVar[str] = 'close_gripper'


#: Read-only registry mapping wire names to skill classes.
SKILL_TYPES: Mapping[str, type[Skill]] = MappingProxyType(_REGISTRY)
