# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criteria for the clamp/abort gate (D4/D17).

One module per promise the issue makes: an over-limit target is clamped to the
limit, over-force while closing is a safety event, the e-stop aborts
everything, an in-limit call passes through *unchanged*, and the velocity cap
is enforced.  The limits used here are synthetic (see ``safety_fixtures``) so a
failure never reads as "somebody retuned ``limits.yaml``".
"""

import pytest
from robot_safety import (
    ClampedCall,
    KeepOutBox,
    KeepOutBoxGuard,
    MotionAxis,
    SafetyEvent,
    SafetyEventKind,
    SafetyLayer,
    SafetyLimits,
    SafetyState,
)
from robot_skills import (
    CloseGripper,
    ExtendColumn,
    Grasp,
    MoveGripper,
    NavigateTo,
    OpenGripper,
    Place,
    Pose,
    Side,
)
from safety_fixtures import make_limits, make_observation, make_state

EVERY_SKILL = (
    pytest.param(NavigateTo('kitchen'), id='navigate_to'),
    pytest.param(MoveGripper(Side.LEFT, Pose.from_xyz(0.4, 0.2, 0.9)), id='move_gripper'),
    pytest.param(Grasp('mug_1'), id='grasp'),
    pytest.param(Place(Pose.from_xyz(0.4, 0.2, 0.9)), id='place'),
    pytest.param(ExtendColumn(0.5), id='extend_column'),
    pytest.param(OpenGripper(Side.RIGHT), id='open_gripper'),
    pytest.param(CloseGripper(Side.RIGHT), id='close_gripper'),
)


# --------------------------------------------------------------------------
# In-limit calls pass through unchanged
# --------------------------------------------------------------------------

@pytest.mark.parametrize('skill', EVERY_SKILL)
def test_an_in_limit_call_passes_through_unchanged(layer, skill):
    """Unchanged means *identical*: the caller gets its own object back.

    Checked with ``is`` rather than ``==`` on purpose -- an equal-but-rebuilt
    skill would satisfy equality while quietly proving the layer copies
    everything through, which is the bug this test exists to catch.
    """
    verdict = layer.filter(skill, make_state())

    assert isinstance(verdict, ClampedCall)
    assert verdict.skill is skill
    assert verdict.clamps == ()
    assert verdict.was_clamped is False


def test_an_accepted_call_carries_the_motion_envelope(layer):
    """The caps the layer cannot enforce ride out with the call for the backend."""
    verdict = layer.filter(NavigateTo('kitchen'), make_state())

    assert verdict.limits is layer.limits.motion
    assert verdict.limits.velocity_cap(MotionAxis.BASE) == 1.0
    assert verdict.limits.max_gripper_force == 10.0


def test_the_default_layer_takes_its_limits_from_the_shipped_yaml():
    """No arguments means the documented defaults, not improvised constants."""
    assert SafetyLayer().limits == SafetyLimits.defaults()


# --------------------------------------------------------------------------
# Joint (column) limit clamp
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    'commanded, expected, bound',
    [
        pytest.param(2.5, 1.0, 1.0, id='above-max'),
        pytest.param(-0.4, 0.0, 0.0, id='below-min'),
    ],
)
def test_an_over_limit_column_target_is_clamped_to_the_limit(
        layer, commanded, expected, bound):
    """The one clamped scalar: height is pulled to the stop, not refused."""
    skill = ExtendColumn(commanded)

    verdict = layer.filter(skill, make_state())

    assert isinstance(verdict, ClampedCall)
    assert verdict.was_clamped
    assert isinstance(verdict.skill, ExtendColumn)
    assert verdict.skill.height == expected
    assert skill.height == commanded, 'the skill the caller holds must not be mutated'

    event, = verdict.clamps
    assert event.kind is SafetyEventKind.COLUMN_LIMIT
    assert event.is_clamp
    assert event.offending_value == commanded
    assert event.limit == bound
    assert event.clamped_value == expected


@pytest.mark.parametrize('height', [0.0, 0.5, 1.0])
def test_a_column_target_inside_the_range_is_left_alone(layer, height):
    """Including exactly on either stop: at the limit is within the limit."""
    skill = ExtendColumn(height)

    verdict = layer.filter(skill, make_state())

    assert verdict.skill is skill
    assert not verdict.was_clamped


def test_the_clamp_follows_the_configured_range_not_a_hard_coded_one():
    """Retuning the YAML retunes the clamp; nothing is baked into the code."""
    layer = SafetyLayer(limits=make_limits(column={'min_height': 0.2, 'max_height': 0.4}))

    verdict = layer.filter(ExtendColumn(2.5), make_state())

    assert verdict.skill.height == 0.4
    assert layer.filter(ExtendColumn(0.0), make_state()).skill.height == 0.2


# --------------------------------------------------------------------------
# Gripper force
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    'skill, side',
    [
        pytest.param(CloseGripper(Side.LEFT), Side.LEFT, id='close-left'),
        pytest.param(Grasp('mug_1', Side.LEFT), Side.LEFT, id='grasp-left'),
        pytest.param(Grasp('mug_1'), Side.LEFT, id='grasp-either-side'),
    ],
)
def test_over_force_while_closing_is_a_safety_event(layer, skill, side):
    """D19: over-force while closing is the safety concern, not the close itself.

    ``Grasp`` with no side is checked on both grippers: the backend picks one
    and the layer cannot know which, so an over-force reading anywhere is
    disqualifying.
    """
    state = make_state(gripper_forces={Side.LEFT: 25.0, Side.RIGHT: 0.0})

    verdict = layer.filter(skill, state)

    assert isinstance(verdict, SafetyEvent)
    assert verdict.kind is SafetyEventKind.GRIPPER_OVERFORCE
    assert verdict.side is side
    assert verdict.offending_value == 25.0
    assert verdict.limit == 10.0
    assert not verdict.is_clamp


def test_closing_the_unloaded_gripper_is_unaffected_by_the_other_one(layer):
    """Force is per-side: a hot left jaw does not veto a right-handed close."""
    state = make_state(gripper_forces={Side.LEFT: 25.0, Side.RIGHT: 1.0})

    verdict = layer.filter(CloseGripper(Side.RIGHT), state)

    assert isinstance(verdict, ClampedCall)


def test_force_at_the_limit_is_allowed(layer):
    """The cap is a ceiling, not a fence: exceeding it is what is unsafe."""
    state = make_state(gripper_forces={Side.LEFT: 10.0, Side.RIGHT: 0.0})

    assert isinstance(layer.filter(CloseGripper(Side.LEFT), state), ClampedCall)


@pytest.mark.parametrize(
    'skill',
    [
        pytest.param(OpenGripper(Side.LEFT), id='open_gripper'),
        pytest.param(NavigateTo('kitchen'), id='navigate_to'),
        pytest.param(ExtendColumn(0.5), id='extend_column'),
    ],
)
def test_over_force_never_blocks_the_skills_that_do_not_close_jaws(layer, skill):
    """Opening must stay available: it is the *remedy* for over-force.

    Gating ``OpenGripper`` on measured force would make an over-force state
    unrecoverable -- the robot could never let go of what is crushing.  This is
    a safety property, not a convenience.
    """
    state = make_state(gripper_forces={Side.LEFT: 900.0, Side.RIGHT: 900.0})

    verdict = layer.filter(skill, state)

    assert isinstance(verdict, ClampedCall)


def test_an_unread_gripper_is_not_judged(layer):
    """No force reading is not the same as a reading of zero, or of too much."""
    state = make_state(gripper_forces={})

    assert isinstance(layer.filter(CloseGripper(Side.LEFT), state), ClampedCall)


# --------------------------------------------------------------------------
# E-stop
# --------------------------------------------------------------------------

@pytest.mark.parametrize('skill', EVERY_SKILL)
def test_the_estop_aborts_every_skill(layer, skill):
    """Abort-all: nothing gets through while the line is engaged."""
    verdict = layer.filter(skill, make_state(estop_engaged=True))

    assert isinstance(verdict, SafetyEvent)
    assert verdict.kind is SafetyEventKind.ESTOP_ENGAGED
    assert skill.name in verdict.detail


def test_the_estop_is_reported_ahead_of_any_clamp_or_other_abort(layer):
    """Checked first, so the most urgent true statement is the one returned."""
    state = make_state(
        estop_engaged=True,
        velocities={'base': 9.0},
        gripper_forces={Side.LEFT: 900.0},
    )

    verdict = layer.filter(ExtendColumn(9.0), state)

    assert verdict.kind is SafetyEventKind.ESTOP_ENGAGED


def test_releasing_the_estop_restores_the_previous_verdict(layer):
    """The gate is stateless: it never latches, so recovery needs no reset call."""
    skill = NavigateTo('kitchen')

    assert isinstance(layer.filter(skill, make_state(estop_engaged=True)), SafetyEvent)
    assert isinstance(layer.filter(skill, make_state()), ClampedCall)


# --------------------------------------------------------------------------
# Velocity caps
# --------------------------------------------------------------------------

def test_a_measured_speed_over_its_cap_aborts(layer):
    """The "unsafe to continue" check: too fast means stop, not slow down."""
    verdict = layer.filter(NavigateTo('kitchen'), make_state(velocities={'base': 1.4}))

    assert isinstance(verdict, SafetyEvent)
    assert verdict.kind is SafetyEventKind.VELOCITY_EXCEEDED
    assert verdict.axis is MotionAxis.BASE
    assert verdict.offending_value == 1.4
    assert verdict.limit == 1.0


@pytest.mark.parametrize('axis', list(MotionAxis))
def test_every_axis_is_capped(layer, axis):
    """No axis is uncapped, and each is judged against its own cap."""
    cap = layer.limits.motion.velocity_cap(axis)

    verdict = layer.filter(
        NavigateTo('kitchen'), make_state(velocities={axis.value: cap + 0.1}))

    assert isinstance(verdict, SafetyEvent)
    assert verdict.axis is axis


def test_an_axis_at_its_cap_is_allowed(layer):
    """Right at the cap is within the cap; only exceeding it is unsafe."""
    state = make_state(velocities={'base': 1.0, 'column': 0.5, 'arm': 2.0})

    assert isinstance(layer.filter(NavigateTo('kitchen'), state), ClampedCall)


def test_an_axis_unrelated_to_the_skill_still_aborts_it(layer):
    """A base running away makes an arm command unsafe too.

    The whole machine is in an unsafe dynamic state; a skill-to-axis map would
    be more code buying less safety.
    """
    verdict = layer.filter(ExtendColumn(0.5), make_state(velocities={'base': 9.0}))

    assert isinstance(verdict, SafetyEvent)
    assert verdict.axis is MotionAxis.BASE


def test_an_unread_axis_is_not_judged(layer):
    """A backend that cannot measure an axis reports nothing, not zero."""
    state = SafetyState(observation=make_observation())

    assert isinstance(layer.filter(NavigateTo('kitchen'), state), ClampedCall)


# --------------------------------------------------------------------------
# Check order, purity, argument checking
# --------------------------------------------------------------------------

def test_a_collision_risk_outranks_a_velocity_or_force_reading():
    """Order is fixed: e-stop, collision, velocity, force, then clamps."""
    layer = SafetyLayer(
        limits=make_limits(),
        collision_guard=KeepOutBoxGuard((KeepOutBox('below_floor', z_max=0.0),)),
    )
    state = make_state(velocities={'base': 9.0}, gripper_forces={Side.LEFT: 900.0})

    verdict = layer.filter(MoveGripper(Side.LEFT, Pose.from_xyz(0.4, 0.2, -0.5)), state)

    assert verdict.kind is SafetyEventKind.COLLISION_RISK


def test_a_velocity_reading_outranks_a_force_reading(layer):
    """Both true, one answer: the earlier check in the fixed order wins."""
    state = make_state(velocities={'base': 9.0}, gripper_forces={Side.LEFT: 900.0})

    verdict = layer.filter(CloseGripper(Side.LEFT), state)

    assert verdict.kind is SafetyEventKind.VELOCITY_EXCEEDED


def test_an_abort_outranks_a_clamp(layer):
    """Never spend work rewriting a call that is about to be refused."""
    verdict = layer.filter(ExtendColumn(9.0), make_state(velocities={'arm': 9.0}))

    assert isinstance(verdict, SafetyEvent)
    assert verdict.kind is SafetyEventKind.VELOCITY_EXCEEDED


@pytest.mark.parametrize('skill', EVERY_SKILL)
def test_the_gate_is_pure_and_re_entrant(layer, skill):
    """Same sample, same verdict -- the property a future in-flight loop needs.

    An asynchronous backend gets "abort in flight" by re-sampling telemetry and
    re-asking, so the gate must never latch, accumulate or otherwise remember.
    """
    state = make_state(velocities={'base': 0.2}, gripper_forces={Side.LEFT: 1.0})

    first = layer.filter(skill, state)
    second = layer.filter(skill, state)

    assert first == second
    assert layer.filter(skill, make_state(estop_engaged=True)).kind is (
        SafetyEventKind.ESTOP_ENGAGED)
    assert layer.filter(skill, state) == first


@pytest.mark.parametrize(
    'skill, state',
    [
        pytest.param('extend_column', None, id='skill-is-prose'),
        pytest.param(ExtendColumn(0.5), 'estopped', id='state-is-prose'),
        pytest.param(ExtendColumn(0.5), make_observation(), id='state-is-an-observation'),
    ],
)
def test_a_caller_error_raises_instead_of_becoming_a_safety_event(layer, skill, state):
    """A type error in the caller is a bug, not an unsafe robot: it must surface."""
    with pytest.raises(TypeError):
        layer.filter(skill, state)


def test_a_layer_cannot_be_built_on_something_that_is_not_a_limit_set():
    """Configuration errors surface at construction, not at the first motion."""
    with pytest.raises(TypeError):
        SafetyLayer(limits={'column': {'min_height': 0.0, 'max_height': 1.0}})
    with pytest.raises(TypeError):
        SafetyLayer(collision_guard=object())
