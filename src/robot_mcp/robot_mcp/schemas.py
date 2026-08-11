# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""JSON Schemas for the skill tools, derived from the skill dataclasses.

Nothing here is hand-written per skill.  A tool's argument schema is read off
:data:`~robot_skills.SKILL_TYPES` and ``dataclasses.fields()``, so a skill
added to (or reshaped in) ``robot_skills`` changes its MCP tool automatically
-- the drift the brief warns about cannot open up silently.

The trade is that the mapper must understand every field type the seam uses.
It refuses to guess: an unmapped type raises :class:`UnsupportedFieldType` at
import time (see :mod:`robot_mcp.tools`), so a future skill with a new field
type breaks the build instead of shipping a tool whose schema lies about it.

Property *names* come from the dataclass fields, which is also what each
skill's ``_payload()`` writes -- the wire names ``Skill.to_dict`` uses.
``test_schemas.py`` asserts that equality rather than assuming it.
"""

import dataclasses
from enum import Enum
from types import MappingProxyType, NoneType, UnionType
from typing import Any, get_args, get_origin, Mapping, Sequence, Union

from robot_skills import JsonDict, Point, Pose, Quaternion, Skill

__all__ = [
    'no_arguments_schema',
    'schema_for_type',
    'skill_schema',
    'UnsupportedFieldType',
]


class UnsupportedFieldType(TypeError):
    """A skill field whose type this module does not know how to describe."""


def no_arguments_schema() -> JsonDict:
    """Return a fresh schema for a tool that takes no arguments at all."""
    return {
        'type': 'object',
        'properties': {},
        'required': [],
        'additionalProperties': False,
    }

#: The scalar field types the seam uses, and their JSON Schema equivalents.
_SCALAR_SCHEMAS: Mapping[type, JsonDict] = MappingProxyType({
    str: {'type': 'string'},
    float: {'type': 'number'},
})

#: Wire-required keys of the nested geometry types.
#:
#: Their Python fields all carry defaults, but their ``from_dict`` requires the
#: keys on the wire (``Point.from_dict`` calls ``check_keys(required=('x', 'y',
#: 'z'))``), so the schema must follow the wire contract, not the Python
#: signature.  ``test_schemas.py`` checks this table against the real parsers.
_RECORD_REQUIRED: Mapping[type, tuple[str, ...]] = MappingProxyType({
    Point: ('x', 'y', 'z'),
    Quaternion: ('x', 'y', 'z', 'w'),
    Pose: ('position',),
})


def _optional_member(annotation: Any) -> Any | None:
    """Return ``T`` for an ``Optional[T]`` annotation, else ``None``.

    Handles both spellings the seam might use: ``Side | None`` (a
    ``types.UnionType``) and ``typing.Optional[Side]``.  A union of two
    non-``None`` types is not an optional and is left for the caller to reject.
    """
    if get_origin(annotation) not in (UnionType, Union):
        return None
    members = [member for member in get_args(annotation) if member is not NoneType]
    if len(members) != 1 or len(get_args(annotation)) != 2:
        return None
    return members[0]


def _enum_schema(enum_type: type[Enum]) -> JsonDict:
    """Return the schema for an enum, listing the members' own wire values."""
    return {'type': 'string', 'enum': [member.value for member in enum_type]}


def _record_schema(record: type, required: Sequence[str]) -> JsonDict:
    """Return an object schema for a dataclass, one property per field."""
    return {
        'type': 'object',
        'properties': {
            field.name: schema_for_type(field.type) for field in dataclasses.fields(record)
        },
        'required': list(required),
        'additionalProperties': False,
    }


def schema_for_type(annotation: Any) -> JsonDict:
    """Return the JSON Schema describing one skill field's wire form.

    Raises :class:`UnsupportedFieldType` for anything unmapped -- deliberately
    louder than emitting a permissive ``{}`` that would let an agent send
    arguments the seam then rejects at runtime.
    """
    member = _optional_member(annotation)
    if member is not None:
        # Optionality is expressed by leaving the key out of ``required``; the
        # value itself is still described by the inner type's schema.
        return schema_for_type(member)
    if isinstance(annotation, type):
        if annotation in _SCALAR_SCHEMAS:
            return dict(_SCALAR_SCHEMAS[annotation])
        if annotation in _RECORD_REQUIRED:
            return _record_schema(annotation, _RECORD_REQUIRED[annotation])
        if issubclass(annotation, Enum):
            return _enum_schema(annotation)
    raise UnsupportedFieldType(
        f'no JSON Schema mapping for field type {annotation!r}; '
        'teach robot_mcp.schemas about it before exposing the skill as a tool')


def _required_field_names(record: type) -> tuple[str, ...]:
    """Return the fields a dataclass cannot be built without, in field order."""
    return tuple(
        field.name for field in dataclasses.fields(record)
        if field.default is dataclasses.MISSING
        and field.default_factory is dataclasses.MISSING
    )


def skill_schema(skill_type: type[Skill]) -> JsonDict:
    """Return the argument schema of the tool that runs ``skill_type``.

    The ``skill`` key itself is not an argument: the tool *name* selects the
    skill, so the schema describes the payload only.
    """
    return _record_schema(skill_type, _required_field_names(skill_type))
