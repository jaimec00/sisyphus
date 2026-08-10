# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Shared helpers for the robot_backends tests.

Kept out of ``conftest.py`` so test modules can import them directly without
relying on a module name every package in the workspace shares.
"""

import pytest
from robot_backends import MockBackend
from robot_skills import (
    FailureCode,
    JsonDict,
    Pose,
    Skill,
    SkillResult,
    SkillStatus,
)

#: A pose on the table that is within reach when the robot stands at 'table'.
TABLE_DROP_X = 0.35
TABLE_DROP_Y = 2.05
TABLE_DROP_Z = 0.75


def snapshot(backend: MockBackend) -> JsonDict:
    """Return the backend's whole world state as a comparable plain dict."""
    return backend.get_observation().to_dict()


def run(backend: MockBackend, *skills: Skill) -> SkillResult:
    """Execute skills in order, asserting each succeeds; return the last result."""
    result = None
    for skill in skills:
        result = backend.execute(skill)
        assert result.status is SkillStatus.OK, (
            f'{skill!r} unexpectedly failed: {result.code} {result.reason}')
    assert result is not None, 'run() needs at least one skill'
    return result


def assert_pose_close(actual: Pose, expected: Pose, *, tolerance: float = 1e-9) -> None:
    """Assert two poses match to floating-point tolerance.

    The mock stores an arm *offset* from the shoulder and reconstructs world
    poses as ``(target - shoulder) + shoulder``, which is not bit-exact for
    badly scaled coordinates.  That approximation is the real contract, so the
    tests state it rather than relying on numbers that happen to round well.
    """
    actual_xyz = (actual.position.x, actual.position.y, actual.position.z)
    expected_xyz = (expected.position.x, expected.position.y, expected.position.z)
    assert actual_xyz == pytest.approx(expected_xyz, abs=tolerance)
    assert actual.orientation == expected.orientation


def assert_refused(
    backend: MockBackend,
    skill: Skill,
    code: FailureCode,
    *,
    reason_contains: str | None = None,
) -> SkillResult:
    """Assert a skill fails with ``code`` and leaves the world byte-identical.

    This is the strong form of "does not corrupt state": the entire serialized
    world before and after the attempt must match, and the observation handed
    back with the failure must match it too.
    """
    before = snapshot(backend)
    result = backend.execute(skill)
    after = snapshot(backend)

    assert result.status is SkillStatus.FAILED, f'{skill!r} unexpectedly succeeded'
    assert result.code is code, f'expected {code}, got {result.code}: {result.reason}'
    assert result.reason, 'a failed result must explain itself'
    if reason_contains is not None:
        assert reason_contains in result.reason, (
            f'reason {result.reason!r} does not mention {reason_contains!r}')
    assert result.skill == skill
    assert after == before, 'a refused skill must not mutate the world'
    assert result.observation.to_dict() == before, (
        'a failed result must report the unchanged world')
    return result
