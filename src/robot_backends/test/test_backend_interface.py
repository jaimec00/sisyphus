# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Tests for the RobotBackend seam itself (decision D9)."""

from dataclasses import dataclass

import pytest
from robot_backends import MockBackend, RobotBackend
from robot_skills import (
    FailureCode,
    NavigateTo,
    Observation,
    Skill,
    SkillResult,
    SkillStatus,
)


@dataclass(frozen=True)
class _SingSkill(Skill, register=False):
    """A skill no backend implements (kept out of the global registry)."""

    name = 'sing'

    def _payload(self):
        return {}

    @classmethod
    def _from_payload(cls, data):
        return cls()


class _StubBackend(RobotBackend):
    """A second backend, standing in for the future Sim/Real implementations."""

    def __init__(self, observation: Observation) -> None:
        self._observation = observation
        self.executed: list[Skill] = []

    def reset(self) -> Observation:
        self.executed.clear()
        return self._observation

    def get_observation(self) -> Observation:
        return self._observation

    def execute(self, skill: Skill) -> SkillResult:
        self.executed.append(skill)
        return SkillResult.ok(skill, self._observation)


def test_mock_backend_implements_the_interface(backend):
    """The Mock is a RobotBackend, so brain code can be typed against it."""
    assert isinstance(backend, RobotBackend)
    assert isinstance(backend.reset(), Observation)
    assert isinstance(backend.get_observation(), Observation)
    assert isinstance(backend.execute(NavigateTo('kitchen')), SkillResult)


def test_the_interface_cannot_be_used_directly():
    """The interface is a contract; every method must be implemented."""
    with pytest.raises(TypeError):
        RobotBackend()

    class Partial(RobotBackend):
        def reset(self):
            return None

    with pytest.raises(TypeError):
        Partial()


def test_a_second_backend_needs_nothing_beyond_the_three_methods(backend):
    """Sim/Real can satisfy the same seam without extending the interface."""
    stub = _StubBackend(backend.get_observation())
    assert isinstance(stub, RobotBackend)

    def drive(robot: RobotBackend) -> str | None:
        """Brain-side code written against the interface alone."""
        robot.reset()
        result = robot.execute(NavigateTo('kitchen'))
        assert result.succeeded
        return robot.get_observation().robot.location

    assert drive(backend) == 'kitchen'
    assert drive(stub) == 'charger'  # the stub reports a canned observation
    assert stub.executed == [NavigateTo('kitchen')]


def test_execute_is_total_over_unknown_skills(backend):
    """An unimplemented skill fails cleanly instead of raising."""
    before = backend.get_observation()
    result = backend.execute(_SingSkill())
    assert result.status is SkillStatus.FAILED
    assert result.code is FailureCode.UNSUPPORTED_SKILL
    assert result.code.is_backend_refusal is True, 'the backend refused; nothing moved (D17)'
    assert 'sing' in result.reason
    assert result.observation == before


def test_execute_rejects_things_that_are_not_skills(backend):
    """Passing a raw dict or a joint array is a programming error, not a failure."""
    for bad in ({'skill': 'navigate_to', 'location': 'kitchen'}, 'navigate_to', [0.1, 0.2]):
        with pytest.raises(TypeError):
            backend.execute(bad)


def test_backend_rejects_a_world_that_is_not_a_mock_world():
    """The seed world is typed data, not a loose dict."""
    with pytest.raises(TypeError):
        MockBackend(world={'kitchen': (2.0, 0.0, 0.0)})
