# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""JSON-safe serialization plumbing shared by every skill-API type.

The skill API is the seam between the LLM brain and the robot; everything that
crosses it has to survive a trip through JSON (LLM tool calls today, ROS 2
action messages later).  Every public type in :mod:`robot_skills` therefore
implements :class:`JsonSerializable`: ``to_dict()`` produces a plain, JSON-safe
``dict`` (only ``None``/``bool``/``int``/``float``/``str``/``list``/``dict``
values -- never an ``Enum`` or a dataclass) and ``from_dict()`` rebuilds an
equal object from it.

Parsing is deliberately strict: missing keys, unknown keys and wrong value
types all raise :class:`SerializationError` instead of silently producing a
half-populated object.  A malformed command from the brain must be a loud,
attributable failure, not a corrupted world model.

Wire-format compatibility policy
--------------------------------
Strictness includes rejecting *unknown* keys, which trades forward
compatibility for loudness.  The chosen stance, deliberately:

**These dicts are an internal, versioned-together format, not a public API.
Adding, renaming or removing a field is a coordinated breaking change made in
one commit across every producer and consumer in this repo.**

That is affordable because all of them -- brain, safety layer, backends, and
the eventual ROS 2 action transport -- ship from this repo and this workspace,
and it is worth paying because the most likely producer of a malformed dict is
an LLM: silently ignoring a key the model invented (``"objct_id"``,
``"height_cm"``) would turn a typo into a wrong action instead of a clean
refusal.

If independently versioned peers ever become real (e.g. an older brain talking
to a newer action server across a network boundary), the migration is *not* to
relax :func:`check_keys` globally -- that would give up the LLM-typo defence
everywhere.  It is to add one reserved, explicitly ignored ``extensions``
sub-object to the machine-to-machine types (:class:`Observation`,
:class:`SkillResult`), keeping :class:`Skill` -- the type an LLM writes --
strict.  Until then, no such escape hatch exists, on purpose.
"""

from abc import ABC, abstractmethod
from enum import Enum
import json
from typing import Any, Mapping, Self, Sequence, TypeVar

__all__ = [
    'JsonDict',
    'JsonSerializable',
    'SerializationError',
    'check_keys',
    'ensure_mapping',
    'get_bool',
    'get_enum',
    'get_float',
    'get_mapping',
    'get_optional_enum',
    'get_optional_str',
    'get_sequence',
    'get_str',
]

#: A plain, JSON-safe mapping as produced by ``to_dict()``.
JsonDict = dict[str, Any]

_EnumT = TypeVar('_EnumT', bound=Enum)


class SerializationError(ValueError):
    """Raised when a dict cannot be parsed into a skill-API object."""


class JsonSerializable(ABC):
    """Base class giving a type a strict, lossless plain-dict representation.

    Subclasses must implement :meth:`to_dict` and :meth:`from_dict`; they get
    :meth:`to_json`/:meth:`from_json` for free.  The contract every subclass is
    tested against is ``type(x).from_dict(x.to_dict()) == x`` and
    ``json.loads(json.dumps(x.to_dict())) == x.to_dict()``.
    """

    @abstractmethod
    def to_dict(self) -> JsonDict:
        """Return a plain, JSON-safe dict describing this object."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Rebuild an object from the output of :meth:`to_dict`."""
        raise NotImplementedError

    def to_json(self, **kwargs: Any) -> str:
        """Serialize this object to a JSON string (kwargs go to ``json.dumps``)."""
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Rebuild an object from a JSON string produced by :meth:`to_json`."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:  # pragma: no cover - thin wrapper
            raise SerializationError(f'{cls.__name__}: invalid JSON: {exc}') from exc
        return cls.from_dict(ensure_mapping(data, context=cls.__name__))


def ensure_mapping(data: Any, *, context: str) -> Mapping[str, Any]:
    """Return ``data`` as a mapping, or raise :class:`SerializationError`."""
    if not isinstance(data, Mapping):
        raise SerializationError(
            f'{context}: expected a mapping, got {type(data).__name__}')
    for key in data:
        if not isinstance(key, str):
            raise SerializationError(
                f'{context}: expected string keys, got {type(key).__name__}')
    return data


def check_keys(
    data: Mapping[str, Any],
    *,
    required: Sequence[str],
    optional: Sequence[str] = (),
    context: str,
) -> None:
    """Verify ``data`` holds every required key and no unexpected ones."""
    missing = [key for key in required if key not in data]
    if missing:
        raise SerializationError(
            f'{context}: missing required key(s): {", ".join(sorted(missing))}')
    allowed = set(required) | set(optional)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SerializationError(
            f'{context}: unknown key(s): {", ".join(unknown)} '
            f'(allowed: {", ".join(sorted(allowed))})')


def get_str(data: Mapping[str, Any], key: str, *, context: str) -> str:
    """Return a required string value."""
    value = data[key]
    if not isinstance(value, str):
        raise SerializationError(
            f'{context}.{key}: expected a string, got {type(value).__name__}')
    return value


def get_optional_str(data: Mapping[str, Any], key: str, *, context: str) -> str | None:
    """Return an optional string value (missing or ``None`` yields ``None``)."""
    if data.get(key) is None:
        return None
    return get_str(data, key, context=context)


def get_float(data: Mapping[str, Any], key: str, *, context: str) -> float:
    """Return a required numeric value as a float (``bool`` is rejected)."""
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SerializationError(
            f'{context}.{key}: expected a number, got {type(value).__name__}')
    return float(value)


def get_bool(data: Mapping[str, Any], key: str, *, context: str) -> bool:
    """Return a required boolean value."""
    value = data[key]
    if not isinstance(value, bool):
        raise SerializationError(
            f'{context}.{key}: expected a boolean, got {type(value).__name__}')
    return value


def get_mapping(data: Mapping[str, Any], key: str, *, context: str) -> Mapping[str, Any]:
    """Return a required nested mapping value."""
    return ensure_mapping(data[key], context=f'{context}.{key}')


def get_sequence(data: Mapping[str, Any], key: str, *, context: str) -> Sequence[Any]:
    """Return a required list value (a bare string is not a sequence here)."""
    value = data[key]
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise SerializationError(
            f'{context}.{key}: expected a list, got {type(value).__name__}')
    return value


def get_enum(
    data: Mapping[str, Any],
    key: str,
    enum_type: type[_EnumT],
    *,
    context: str,
) -> _EnumT:
    """Return a required enum member, parsed from its string value."""
    raw = get_str(data, key, context=context)
    try:
        return enum_type(raw)
    except ValueError as exc:
        allowed = ', '.join(str(member.value) for member in enum_type)
        raise SerializationError(
            f'{context}.{key}: {raw!r} is not a valid {enum_type.__name__} '
            f'(allowed: {allowed})') from exc


def get_optional_enum(
    data: Mapping[str, Any],
    key: str,
    enum_type: type[_EnumT],
    *,
    context: str,
) -> _EnumT | None:
    """Return an optional enum member (missing or ``None`` yields ``None``)."""
    if data.get(key) is None:
        return None
    return get_enum(data, key, enum_type, context=context)
