# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for SkillResult: the closed-loop feedback object."""

import pytest
from robot_skills import (
    FailureCode,
    Grasp,
    NavigateTo,
    SkillResult,
    SkillStatus,
)
from robot_skills.serialization import SerializationError
from skill_api_fixtures import make_observation


def test_ok_result_carries_the_skill_and_a_fresh_observation(observation):
    """A successful result closes the loop: what ran, and the world after it."""
    skill = NavigateTo('kitchen')
    result = SkillResult.ok(skill, observation)
    assert result.status is SkillStatus.OK
    assert result.succeeded is True
    assert result.skill == skill
    assert result.observation is observation
    assert result.reason is None
    assert result.code is None


def test_ok_result_may_carry_an_informational_reason(observation):
    """Success can still explain itself (e.g. a no-op)."""
    result = SkillResult.ok(NavigateTo('kitchen'), observation, 'already at kitchen')
    assert result.succeeded is True
    assert result.reason == 'already at kitchen'
    assert result.code is None


def test_failed_result_requires_a_code_and_a_reason(observation):
    """Failures are always attributable, both by machine and by human."""
    result = SkillResult.failure(
        Grasp('ghost_1'), observation, FailureCode.UNKNOWN_OBJECT, "no object 'ghost_1'")
    assert result.status is SkillStatus.FAILED
    assert result.succeeded is False
    assert result.code is FailureCode.UNKNOWN_OBJECT
    assert 'ghost_1' in result.reason

    with pytest.raises(ValueError, match='must carry a FailureCode'):
        SkillResult(
            skill=Grasp('mug_1'),
            status=SkillStatus.FAILED,
            observation=observation,
            reason='nope',
        )
    with pytest.raises(ValueError, match='non-empty reason'):
        SkillResult(
            skill=Grasp('mug_1'),
            status=SkillStatus.FAILED,
            observation=observation,
            code=FailureCode.UNKNOWN_OBJECT,
        )


def test_successful_result_must_not_carry_a_failure_code(observation):
    """Status and code can never disagree."""
    with pytest.raises(ValueError, match='must not carry a FailureCode'):
        SkillResult(
            skill=Grasp('mug_1'),
            status=SkillStatus.OK,
            observation=observation,
            code=FailureCode.GRIPPER_EMPTY,
        )


def test_member_types_are_enforced(observation):
    """A result cannot be assembled from loose strings or dicts."""
    with pytest.raises(TypeError):
        SkillResult.ok('navigate_to', observation)
    with pytest.raises(TypeError):
        SkillResult.ok(NavigateTo('kitchen'), observation.to_dict())
    with pytest.raises(TypeError):
        SkillResult(
            skill=NavigateTo('kitchen'), status='ok', observation=observation)


def test_result_round_trip_preserves_nested_skill_enum_and_observation(round_trip):
    """The whole nested structure survives JSON, enums included."""
    observation = make_observation()
    failure = SkillResult.failure(
        Grasp('mug_1', 'right'),
        observation,
        FailureCode.GRIPPER_OCCUPIED,
        "the right gripper already holds 'plate_1'",
    )
    round_trip(failure)
    round_trip(SkillResult.ok(NavigateTo('table'), observation))

    as_dict = failure.to_dict()
    assert as_dict['status'] == 'failed'
    assert as_dict['code'] == 'gripper_occupied'
    assert as_dict['skill'] == {'skill': 'grasp', 'object_id': 'mug_1', 'side': 'right'}
    rebuilt = SkillResult.from_dict(as_dict)
    assert rebuilt.skill == failure.skill
    assert rebuilt.observation == observation
    assert rebuilt.code is FailureCode.GRIPPER_OCCUPIED


def test_result_parsing_is_strict(observation):
    """A malformed result dict raises rather than losing the failure reason."""
    good = SkillResult.ok(NavigateTo('kitchen'), observation).to_dict()
    with pytest.raises(SerializationError, match='missing required key'):
        SkillResult.from_dict({'status': 'ok'})
    with pytest.raises(SerializationError, match='unknown key'):
        SkillResult.from_dict({**good, 'duration_s': 1.5})
    with pytest.raises(SerializationError, match='not a valid SkillStatus'):
        SkillResult.from_dict({**good, 'status': 'maybe'})
    with pytest.raises(SerializationError, match='not a valid FailureCode'):
        SkillResult.from_dict({**good, 'status': 'failed', 'code': 'oops', 'reason': 'x'})
