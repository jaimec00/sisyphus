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

``from_dict`` raises **only** :class:`SerializationError`: constructor-level
``TypeError``/``ValueError`` (a violated invariant such as two grippers
disagreeing about which object they hold) are translated at the parse boundary
by :func:`parse_errors`, so a caller turning a bad payload into a clean refusal
catches one exception type and cannot miss a case.

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

The version stamp (D18)
-----------------------
:data:`SCHEMA_VERSION` is one integer for the whole skill API, stamped under
:data:`SCHEMA_VERSION_KEY` into the wire form of the machine-to-machine types
(``Observation`` and ``SkillResult``).  Skills are not stamped: they are the
type an LLM writes by hand, and a required bookkeeping key there is one more
thing for a model to get wrong.

* **Producing.**  Every ``Observation``/``SkillResult`` dict carries the stamp,
  *including* an ``Observation`` nested inside a ``SkillResult``.  The stamp
  belongs to the type's wire form, not to a message envelope, so an observation
  published on its own is self-describing and ``Observation.to_dict()`` stays a
  single canonical function with no nested/top-level special case.  Two stamps
  at two depths in one result dict is intended, not accidental.
* **Parsing.**  The key is optional: a dict without it is read as the current
  version (which is what makes an added field non-breaking).  A dict carrying a
  *different* version is a :class:`SerializationError` -- D18 grants no
  multi-version support and no deprecation windows, so a foreign version is an
  error rather than something to guess at.

**Compat rule.**  Adding an *optional* field is non-breaking and does not bump
:data:`SCHEMA_VERSION`.  Removing, renaming or retyping a field is breaking: it
bumps the version and updates every binder in the same PR (affordable because
every consumer lives in this one repo, D13).

**Enforcement.**  ``src/robot_skills/test/golden/v<N>/`` holds a frozen,
checked-in ``to_dict()`` fixture per public type.  ``test_golden_schema.py``
compares today's output against ``v{SCHEMA_VERSION}``, tolerating added keys and
failing on a dropped, renamed or retyped one -- so a breaking change stays red
until the author bumps the version and writes a new ``v<N>`` fixture set.
"""

from abc import ABC, abstractmethod
from contextlib import contextmanager
from enum import Enum
import json
from typing import Any, Iterator, Mapping, Self, Sequence, TypeVar

__all__ = [
    'SCHEMA_VERSION',
    'SCHEMA_VERSION_KEY',
    'JsonDict',
    'JsonSerializable',
    'SerializationError',
    'check_schema_version',
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
    'parse_errors',
]

#: A plain, JSON-safe mapping as produced by ``to_dict()``.
JsonDict = dict[str, Any]

#: Version of the whole skill-API wire format (D18); see the module docstring.
SCHEMA_VERSION = 1

#: Key the version stamp travels under in a machine-to-machine dict.
SCHEMA_VERSION_KEY = 'schema_version'

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
        """Rebuild an object from the output of :meth:`to_dict`.

        Raises :class:`SerializationError` -- and nothing else -- for any
        malformed input, including one that only violates an invariant the
        constructor checks (see :func:`parse_errors`).
        """
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


@contextmanager
def parse_errors(context: str) -> Iterator[None]:
    """Translate constructor-level validation errors into a parse error.

    Wrap the object construction at the end of a ``from_dict`` in this: a
    dataclass rejecting its arguments (``TypeError``) or violating one of its
    invariants (``ValueError``) is, at a parse boundary, a malformed payload,
    and callers must be able to catch exactly one exception type there.  A
    :class:`SerializationError` raised deeper in the parse passes through
    unchanged, so nested messages are not double-wrapped.
    """
    try:
        yield
    except SerializationError:
        raise
    except (TypeError, ValueError) as exc:
        raise SerializationError(f'{context}: {exc}') from exc


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


def check_schema_version(data: Mapping[str, Any], *, context: str) -> None:
    """Verify a machine-to-machine dict's version stamp, if it carries one.

    An absent stamp means "the current version": that is what lets an added
    optional field be non-breaking without every producer being updated first.
    A *present* stamp naming another version is refused rather than guessed at
    -- D18 grants no multi-version support, so the only honest answer to a
    foreign payload is a loud one.
    """
    if SCHEMA_VERSION_KEY not in data:
        return
    value = data[SCHEMA_VERSION_KEY]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SerializationError(
            f'{context}.{SCHEMA_VERSION_KEY}: expected an integer, '
            f'got {type(value).__name__}')
    if value != SCHEMA_VERSION:
        raise SerializationError(
            f'{context}.{SCHEMA_VERSION_KEY}: unsupported schema version {value} '
            f'(this build speaks version {SCHEMA_VERSION} only)')


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
