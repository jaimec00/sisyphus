# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The telemetry sample: composed from an observation, never extending it.

``SafetyState`` is the layer's only window onto the dynamic world, so a bad
reading has to be rejected where it enters -- a negative "speed" or a NaN force
reaching a comparison would decide a safety question by accident.
"""

import pytest
from robot_safety import MotionAxis, SafetyState
from robot_skills import Side
from safety_fixtures import make_observation, make_state


def test_the_shared_observation_is_composed_not_copied():
    """The state carries the brain-facing observation through untouched."""
    observation = make_observation()

    state = SafetyState(observation=observation)

    assert state.observation is observation
    assert state.estop_engaged is False
    assert state.velocities == {}
    assert state.gripper_forces == {}


def test_measurement_keys_accept_their_wire_strings():
    """Telemetry keyed by the enum's string values coerces to the enum.

    The axis strings are also the ``velocity`` keys in ``limits.yaml``: one
    vocabulary for config and telemetry means a typo cannot invent an axis.
    """
    state = SafetyState(
        observation=make_observation(),
        velocities={'base': 0.25, MotionAxis.ARM: 0.1},
        gripper_forces={'left': 3.0},
    )

    assert state.velocity(MotionAxis.BASE) == 0.25
    assert state.velocity(MotionAxis.ARM) == 0.1
    assert state.gripper_force(Side.LEFT) == 3.0


def test_an_unread_axis_or_side_reads_as_none_not_zero():
    """Absent means "no reading", which is not the same as "standing still"."""
    state = SafetyState(observation=make_observation(), velocities={'base': 0.1})

    assert state.velocity(MotionAxis.COLUMN) is None
    assert state.gripper_force(Side.RIGHT) is None


def test_measurements_are_decoupled_from_the_callers_dict():
    """A caller mutating its dict afterwards cannot rewrite a judged sample."""
    readings = {'base': 0.1}
    state = SafetyState(observation=make_observation(), velocities=readings)

    readings['base'] = 99.0

    assert state.velocity(MotionAxis.BASE) == 0.1
    with pytest.raises(TypeError):
        state.velocities[MotionAxis.BASE] = 99.0


@pytest.mark.parametrize(
    'kwargs, expected',
    [
        pytest.param({'observation': 'kitchen'}, TypeError, id='observation-not-a-snapshot'),
        pytest.param({'estop_engaged': 1}, TypeError, id='estop-not-a-bool'),
        pytest.param({'velocities': {'wrist': 0.1}}, ValueError, id='unknown-axis'),
        pytest.param({'velocities': {'base': -0.1}}, ValueError, id='negative-speed'),
        pytest.param({'velocities': {'base': float('nan')}}, ValueError, id='nan-speed'),
        pytest.param({'velocities': {'base': 'fast'}}, TypeError, id='non-numeric-speed'),
        pytest.param({'velocities': 0.5}, TypeError, id='velocities-not-a-mapping'),
        pytest.param({'gripper_forces': {'middle': 1.0}}, ValueError, id='unknown-side'),
        pytest.param({'gripper_forces': {Side.LEFT: -1.0}}, ValueError, id='negative-force'),
    ],
)
def test_a_bad_reading_is_rejected_where_it_enters(kwargs, expected):
    """Sensor nonsense fails at construction, not at the comparison it skews."""
    with pytest.raises(expected):
        make_state(**kwargs)


def test_states_with_equal_readings_compare_equal():
    """Value semantics: two samples of the same world are interchangeable."""
    assert make_state() == make_state()
    assert make_state(estop_engaged=True) != make_state()
