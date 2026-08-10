# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Small constructor-level validators shared by the skill-API dataclasses.

These guard *structural* validity only -- a finite number where a number is
required, a non-empty identifier where an id is required, a real
:class:`~enum.Enum` member where an enum is required.  They deliberately do not
encode robot limits (joint ranges, reach, force): clamping and rejecting
out-of-envelope *values* is the safety layer's job, and the world model's, not
the data type's.
"""

from enum import Enum
from math import isfinite
from typing import Any, TypeVar

__all__ = ['as_enum', 'as_finite_float', 'as_identifier', 'as_optional_enum']

_EnumT = TypeVar('_EnumT', bound=Enum)


def as_finite_float(value: Any, *, name: str) -> float:
    """Return ``value`` as a finite float, rejecting bools, NaN and infinities."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f'{name} must be a number, got {type(value).__name__}')
    result = float(value)
    if not isfinite(result):
        raise ValueError(f'{name} must be finite, got {result!r}')
    return result


def as_identifier(value: Any, *, name: str) -> str:
    """Return ``value`` as a non-blank identifier string."""
    if not isinstance(value, str):
        raise TypeError(f'{name} must be a string, got {type(value).__name__}')
    if not value.strip():
        raise ValueError(f'{name} must be a non-empty string')
    return value


def as_enum(value: Any, enum_type: type[_EnumT], *, name: str) -> _EnumT:
    """Return ``value`` as an ``enum_type`` member, accepting its string value."""
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ', '.join(repr(member.value) for member in enum_type)
        raise ValueError(
            f'{name} must be one of {allowed}, got {value!r}') from exc


def as_optional_enum(value: Any, enum_type: type[_EnumT], *, name: str) -> _EnumT | None:
    """Return ``value`` as an ``enum_type`` member, passing ``None`` through."""
    if value is None:
        return None
    return as_enum(value, enum_type, name=name)
