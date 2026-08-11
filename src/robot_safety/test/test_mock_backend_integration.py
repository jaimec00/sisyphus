# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The layer against a real backend: Mock first (CLAUDE.md invariant 2).

``robot_safety`` is backend-agnostic, so nothing here may be Mock-specific --
these tests use the Mock only to prove the seam composes: what the layer emits
is executable, what it aborts never reaches the world, and the two layers'
responsibilities (D17) do not overlap or leave a gap.
"""

from robot_backends import MockBackend
from robot_safety import SafetyEvent, SafetyLayer, SafetyState
from robot_skills import CloseGripper, ExtendColumn, FailureCode, Side, SkillStatus


def sample(backend: MockBackend, **overrides) -> SafetyState:
    """Return a telemetry sample around the backend's current observation.

    The Mock has neither force sensing nor an e-stop line, which is exactly why
    telemetry is the safety layer's own input type: a backend supplies what it
    can measure, and a caller assembles the rest.
    """
    return SafetyState(observation=backend.get_observation(), **overrides)


def test_a_clamped_command_is_executable_by_the_backend_that_refused_it():
    """D17's split, end to end: the backend refuses, the layer makes it legal.

    The unclamped height is a "can't be done" for the backend (out of column
    travel); the clamped one is an ordinary success.  It also pins that the
    shipped defaults are compatible with the model the Mock uses -- a layer
    whose limits were laxer than the machine's would clamp to a value the
    machine still rejects.
    """
    backend = MockBackend()
    layer = SafetyLayer()
    unsafe = ExtendColumn(9.0)

    refused = backend.execute(unsafe)
    assert refused.status is SkillStatus.FAILED
    assert refused.code is FailureCode.OUT_OF_RANGE

    verdict = layer.filter(unsafe, sample(backend))
    assert verdict.was_clamped

    result = backend.execute(verdict.skill)
    assert result.succeeded, result.reason
    assert result.observation.robot.column_height == verdict.skill.height


def test_an_in_limit_command_reaches_the_backend_untouched():
    """The layer is transparent on the happy path, which is most of the day."""
    backend = MockBackend()
    layer = SafetyLayer()
    skill = ExtendColumn(0.8)

    verdict = layer.filter(skill, sample(backend))
    result = backend.execute(verdict.skill)

    assert verdict.skill is skill
    assert result.succeeded
    assert result.observation.robot.column_height == 0.8


def test_an_aborted_call_never_touches_the_world():
    """The abort has to happen *before* the backend, or it is not a gate.

    Compares the whole serialized world either side of the refusal, the same
    way ``robot_backends`` proves its own refusals leave no trace.
    """
    backend = MockBackend()
    layer = SafetyLayer()
    before = backend.get_observation().to_dict()

    estopped = layer.filter(ExtendColumn(0.8), sample(backend, estop_engaged=True))
    over_force = layer.filter(
        CloseGripper(Side.LEFT),
        sample(backend, gripper_forces={Side.LEFT: 500.0}),
    )

    assert isinstance(estopped, SafetyEvent)
    assert isinstance(over_force, SafetyEvent)
    assert backend.get_observation().to_dict() == before


def test_a_safety_event_reports_a_code_the_backend_never_emits():
    """The two layers' vocabularies do not overlap (D17).

    Every Mock failure is a backend refusal; every safety event maps onto the
    safety half of the shared enum, so a caller can always tell "pick a
    different goal" from "the motion was stopped".
    """
    backend = MockBackend()
    layer = SafetyLayer()

    refusal = backend.execute(ExtendColumn(9.0))
    event = layer.filter(ExtendColumn(0.8), sample(backend, estop_engaged=True))

    assert refusal.code.is_backend_refusal
    assert not refusal.code.is_safety_event
    assert event.failure_code.is_safety_event
    assert event.failure_code is not refusal.code
