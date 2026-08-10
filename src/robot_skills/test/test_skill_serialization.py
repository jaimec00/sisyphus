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
    GripperObservation,
    JsonSerializable,
    NavigateTo,
    Observation,
    Pose,
    SCHEMA_VERSION,
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


def test_from_dict_raises_only_serialization_error():
    """A parse boundary has one exception type, invariant violations included.

    Callers turning a bad LLM or transport payload into a clean refusal write
    ``except SerializationError``; a constructor-level ``ValueError``/
    ``TypeError`` leaking through would silently bypass that handler.
    """
    observation = make_observation()
    good = observation.to_dict()

    # An invariant checked by __post_init__, not by the key/type getters.
    duplicated = {**good, 'objects': [good['objects'][0], good['objects'][0]]}
    with pytest.raises(SerializationError, match='duplicate object_id'):
        Observation.from_dict(duplicated)

    # One gripper per side is likewise a constructor invariant.
    one_armed = {**good}
    one_armed['robot'] = {**good['robot'], 'grippers': [good['robot']['grippers'][0]]}
    with pytest.raises(SerializationError, match='one entry per side'):
        Observation.from_dict(one_armed)

    # A gripper carrying an object it reports not gripping is likewise refused
    # by the constructor, and must surface as a parse error like the rest.
    held = good['robot']['grippers'][0]
    held = {**held, 'held_object_id': 'mug_1', 'grasped': True}
    with pytest.raises(SerializationError, match='while grasped=False'):
        GripperObservation.from_dict({**held, 'grasped': False})

    # And so is the status/code agreement on a result.
    result = SkillResult.ok(NavigateTo('kitchen'), observation).to_dict()
    with pytest.raises(SerializationError, match='must carry a FailureCode'):
        SkillResult.from_dict({**result, 'status': 'failed', 'reason': 'nope'})

    # Non-finite geometry is rejected by the dataclass, not by get_float.
    with pytest.raises(SerializationError, match='must be finite'):
        Pose.from_dict({'position': {'x': float('nan'), 'y': 0.0, 'z': 0.0}})

    # A skill argument the type refuses (blank identifier) surfaces the same way.
    with pytest.raises(SerializationError, match='non-empty'):
        NavigateTo.from_dict({'skill': 'navigate_to', 'location': '  '})


def test_the_machine_to_machine_types_stamp_the_schema_version():
    """D18: an Observation/SkillResult dict says which schema it was written to.

    Both nesting depths are stamped on purpose: an observation lifted out of a
    result and published alone must still be self-describing.
    """
    observation = make_observation()
    result = SkillResult.ok(NavigateTo('kitchen'), observation)

    assert observation.to_dict()['schema_version'] == SCHEMA_VERSION
    as_dict = result.to_dict()
    assert as_dict['schema_version'] == SCHEMA_VERSION
    assert as_dict['observation']['schema_version'] == SCHEMA_VERSION
    assert isinstance(SCHEMA_VERSION, int) and not isinstance(SCHEMA_VERSION, bool)

    # A skill is written by an LLM by hand; it carries no bookkeeping key.
    assert 'schema_version' not in as_dict['skill']
    assert 'schema_version' not in observation.robot.to_dict()


def test_the_stamp_survives_the_round_trip_and_is_optional_on_parse(round_trip):
    """An added optional field is non-breaking, so an unstamped dict still parses."""
    observation = make_observation()
    result = SkillResult.failure(
        Grasp('mug_1'), observation, FailureCode.OUT_OF_REACH, 'too far')
    round_trip(observation)
    round_trip(result)

    unstamped = {key: value for key, value in observation.to_dict().items()
                 if key != 'schema_version'}
    assert Observation.from_dict(unstamped) == observation

    partly_stamped = {key: value for key, value in result.to_dict().items()
                      if key != 'schema_version'}
    assert SkillResult.from_dict(partly_stamped) == result


def test_a_foreign_schema_version_is_refused_rather_than_guessed_at():
    """D18 grants no multi-version support: a stamp we do not speak is an error."""
    observation = make_observation().to_dict()
    result = SkillResult.ok(NavigateTo('kitchen'), make_observation()).to_dict()

    with pytest.raises(SerializationError, match='unsupported schema version 2'):
        Observation.from_dict({**observation, 'schema_version': SCHEMA_VERSION + 1})
    with pytest.raises(SerializationError, match='unsupported schema version'):
        SkillResult.from_dict({**result, 'schema_version': 99})
    # ...including one hidden in the nested observation.
    with pytest.raises(SerializationError, match='unsupported schema version'):
        SkillResult.from_dict(
            {**result, 'observation': {**observation, 'schema_version': 0}})
    with pytest.raises(SerializationError, match='expected an integer'):
        Observation.from_dict({**observation, 'schema_version': '1'})
    with pytest.raises(SerializationError, match='expected an integer'):
        Observation.from_dict({**observation, 'schema_version': True})


def test_a_foreign_version_is_diagnosed_before_the_keys_it_explains():
    """The reason a v2 payload looks wrong is the version, not a typo'd key.

    A future version most likely carries keys this build has never heard of, so
    checking keys first would report ``unknown key(s): ...`` and send the reader
    hunting an LLM typo instead of a schema mismatch.
    """
    from_the_future = {
        **make_observation().to_dict(),
        'schema_version': SCHEMA_VERSION + 1,
        'ambient_temperature_c': 21.5,
    }
    with pytest.raises(SerializationError, match='unsupported schema version'):
        Observation.from_dict(from_the_future)

    result = {
        **SkillResult.ok(NavigateTo('kitchen'), make_observation()).to_dict(),
        'schema_version': SCHEMA_VERSION + 1,
        'duration_s': 1.5,
    }
    with pytest.raises(SerializationError, match='unsupported schema version'):
        SkillResult.from_dict(result)


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
