# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the serialization plumbing itself."""

import json

import pytest
from robot_skills import (
    FailureCode,
    Grasp,
    JsonSerializable,
    NavigateTo,
    SkillResult,
)
from robot_skills.serialization import (
    check_keys,
    ensure_mapping,
    get_bool,
    get_enum,
    get_float,
    get_sequence,
    get_str,
    SerializationError,
)
from skill_api_fixtures import assert_json_safe, make_observation


def test_no_enum_or_dataclass_leaks_into_the_dict_form():
    """to_dict() output is JSON-native: no Enum, no dataclass, no tuple."""
    result = SkillResult.failure(
        Grasp('mug_1', 'left'),
        make_observation(),
        FailureCode.OUT_OF_REACH,
        'too far',
    )
    as_dict = result.to_dict()
    assert_json_safe(as_dict)
    assert json.loads(json.dumps(as_dict)) == as_dict


def test_json_text_round_trip_via_helpers():
    """to_json/from_json are equivalent to the dict path."""
    observation = make_observation()
    text = observation.to_json()
    assert json.loads(text) == observation.to_dict()
    assert type(observation).from_json(text) == observation

    skill = NavigateTo('kitchen')
    assert NavigateTo.from_json(skill.to_json(sort_keys=True)) == skill


def test_from_json_rejects_non_object_payloads():
    """A JSON array or scalar is not a serialized skill-API object."""
    with pytest.raises(SerializationError, match='expected a mapping'):
        NavigateTo.from_json('["kitchen"]')
    with pytest.raises(SerializationError, match='invalid JSON'):
        NavigateTo.from_json('{not json')


def test_json_serializable_subclasses_must_implement_the_contract():
    """The base class enforces both halves of the round trip."""
    class Incomplete(JsonSerializable):
        def to_dict(self):
            return {}

    with pytest.raises(TypeError):
        Incomplete()


def test_check_keys_reports_missing_and_unknown():
    """Key validation names the offending keys, for actionable brain errors."""
    with pytest.raises(SerializationError, match='missing required key\\(s\\): b'):
        check_keys({'a': 1}, required=('a', 'b'), context='T')
    with pytest.raises(SerializationError, match='unknown key\\(s\\): c'):
        check_keys({'a': 1, 'c': 3}, required=('a',), optional=('b',), context='T')
    check_keys({'a': 1, 'b': 2}, required=('a',), optional=('b',), context='T')


def test_scalar_getters_reject_wrong_types():
    """Numbers are not bools, lists are not strings, and vice versa."""
    with pytest.raises(SerializationError, match='expected a number'):
        get_float({'v': True}, 'v', context='T')
    with pytest.raises(SerializationError, match='expected a boolean'):
        get_bool({'v': 1}, 'v', context='T')
    with pytest.raises(SerializationError, match='expected a string'):
        get_str({'v': 1}, 'v', context='T')
    with pytest.raises(SerializationError, match='expected a list'):
        get_sequence({'v': 'abc'}, 'v', context='T')
    assert get_float({'v': 2}, 'v', context='T') == 2.0
    assert get_sequence({'v': [1, 2]}, 'v', context='T') == [1, 2]


def test_enum_getter_lists_the_allowed_values():
    """An invalid enum value tells the brain what it may say instead."""
    with pytest.raises(SerializationError, match='out_of_reach'):
        get_enum({'code': 'exploded'}, 'code', FailureCode, context='T')


def test_ensure_mapping_rejects_non_string_keys():
    """JSON objects only ever have string keys; anything else is a bug upstream."""
    with pytest.raises(SerializationError, match='expected string keys'):
        ensure_mapping({1: 'a'}, context='T')
