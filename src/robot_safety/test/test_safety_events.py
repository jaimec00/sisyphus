# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The safety event: structured data on the return path, never an exception.

Also pins the single documented bridge to the shared schema (D18): this
package's kinds are its own, and they reach ``robot_skills`` through exactly
one mapping, which a later "safety events on the wire" feature widens in one
place.
"""

import pytest
from robot_safety import MotionAxis, SafetyEvent, SafetyEventKind
from robot_skills import FailureCode, SAFETY_EVENT_CODES, Side


def test_a_clamp_records_the_value_it_produced():
    """``is_clamp`` distinguishes a rewrite from an abort by the same type."""
    clamp = SafetyEvent(
        kind=SafetyEventKind.COLUMN_LIMIT,
        detail='clamped to the column stop',
        offending_value=2.0,
        limit=1.2,
        clamped_value=1.2,
    )

    assert clamp.is_clamp
    assert clamp.offending_value == 2.0
    assert clamp.limit == 1.2


def test_an_abort_has_no_clamped_value():
    """A refused motion has no "what it became", and says so."""
    abort = SafetyEvent(kind=SafetyEventKind.ESTOP_ENGAGED, detail='e-stop engaged')

    assert not abort.is_clamp
    assert abort.clamped_value is None
    assert abort.offending_value is None


def test_every_kind_maps_onto_a_shared_safety_event_code():
    """The one bridge to ``robot_skills``, and it stays on the safety side."""
    for kind in SafetyEventKind:
        event = SafetyEvent(kind=kind, detail='x')

        assert event.failure_code is FailureCode.REJECTED
        assert event.failure_code in SAFETY_EVENT_CODES
        assert event.failure_code.is_safety_event
        assert not event.failure_code.is_backend_refusal


def test_an_event_is_data_and_not_an_exception():
    """Callers inspect a value; nothing here is raisable or catchable."""
    event = SafetyEvent(kind=SafetyEventKind.COLLISION_RISK, detail='inside a keep-out box')

    assert not isinstance(event, BaseException)
    assert event == SafetyEvent(
        kind=SafetyEventKind.COLLISION_RISK, detail='inside a keep-out box')


def test_events_carry_which_gripper_or_axis_they_are_about():
    """Enough structure for a caller to react without parsing prose."""
    force = SafetyEvent(
        kind=SafetyEventKind.GRIPPER_OVERFORCE,
        detail='too hard',
        offending_value=55.0,
        limit=40.0,
        side=Side.RIGHT,
    )
    speed = SafetyEvent(
        kind=SafetyEventKind.VELOCITY_EXCEEDED,
        detail='too fast',
        offending_value=1.4,
        limit=0.6,
        axis=MotionAxis.BASE,
    )

    assert force.side is Side.RIGHT and force.axis is None
    assert speed.axis is MotionAxis.BASE and speed.side is None


@pytest.mark.parametrize(
    'kwargs, expected',
    [
        pytest.param({'kind': 'estop_engaged'}, TypeError, id='kind-not-a-member'),
        pytest.param({'detail': ''}, ValueError, id='blank-detail'),
        pytest.param({'detail': None}, TypeError, id='missing-detail'),
        pytest.param({'limit': float('inf')}, ValueError, id='non-finite-limit'),
        pytest.param({'offending_value': 'lots'}, TypeError, id='non-numeric-value'),
        pytest.param({'side': 'left'}, TypeError, id='side-not-a-member'),
        pytest.param({'axis': 'base'}, TypeError, id='axis-not-a-member'),
    ],
)
def test_a_malformed_event_is_rejected(kwargs, expected):
    """The event is the layer's evidence; it may not be built out of prose."""
    fields = {'kind': SafetyEventKind.COLUMN_LIMIT, 'detail': 'x'}
    fields.update(kwargs)

    with pytest.raises(expected):
        SafetyEvent(**fields)
