# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The golden test: ``RobotModel``'s defaults come from the shipped URDF (PR6).

This is the D23 "source of truth" payoff.  Before PR6 the six kinematic
constants lived here as literals, and the description gate carried a
transcribed copy (the ``ROBOT_MODEL_*`` constants in
``robot_description/test/test_description.py``) that could drift.  Now the
description owns one loader (``robot_description.robot_model.load_robot_model``)
and ``RobotModel`` seeds its defaults from it, so Mock and the future MuJoCo
backend cannot disagree.

The assertions are **absolute, not self-consistent** (D29's lesson): they pin
the parsed/derived values to the literals the brief specifies, rather than
comparing the loader against another value read off the same URDF.  A loader
that silently returned 0.18 everywhere, or that derived ``reach_radius`` from
the arm's actual link reach instead of reading the property, is a wrong model
that would otherwise pass a merely internally-consistent check.
"""

import pytest

from robot_backends import RobotModel
from robot_description.robot_model import load_robot_model
from robot_skills import Point

#: The literals from the brief (issue #87) -- the numbers the Mock backend, the
#: safety layer and the brain's prompt already enforce, typed here by hand so a
#: drift in either the URDF or the loader is a test failure, never a silent
#: disagreement.
EXPECTED = {
    'shoulder_offset_y': 0.18,
    'shoulder_offset_z': 0.50,
    'reach_radius': 0.85,
    'min_column_height': 0.00,
    'max_column_height': 1.20,
    'home_gripper_offset': (0.35, 0.0, -0.05),
}


def test_loader_reads_the_urdf_constants_exactly():
    """The loader returns the five properties and the derived home offset, exactly."""
    constants = load_robot_model()

    assert constants.shoulder_offset_y == pytest.approx(EXPECTED['shoulder_offset_y'])
    assert constants.shoulder_offset_z == pytest.approx(EXPECTED['shoulder_offset_z'])
    assert constants.reach_radius == pytest.approx(EXPECTED['reach_radius'])
    assert constants.min_column_height == pytest.approx(EXPECTED['min_column_height'])
    assert constants.max_column_height == pytest.approx(EXPECTED['max_column_height'])
    # The derived home offset is the grasp midpoint vs shoulder at the zero
    # pose; it must match the literal to float precision (same expansion).
    for got, want in zip(constants.home_gripper_offset, EXPECTED['home_gripper_offset']):
        assert got == pytest.approx(want)


def test_default_robot_model_loads_from_the_urdf():
    """``RobotModel()`` with no arguments is the URDF's own numbers."""
    model = RobotModel()

    assert model.shoulder_offset_y == pytest.approx(EXPECTED['shoulder_offset_y'])
    assert model.shoulder_offset_z == pytest.approx(EXPECTED['shoulder_offset_z'])
    assert model.reach_radius == pytest.approx(EXPECTED['reach_radius'])
    assert model.min_column_height == pytest.approx(EXPECTED['min_column_height'])
    assert model.max_column_height == pytest.approx(EXPECTED['max_column_height'])
    assert isinstance(model.home_gripper_offset, Point)
    assert model.home_gripper_offset == Point(*EXPECTED['home_gripper_offset'])


def test_explicit_overrides_still_win_over_urdf_defaults():
    """A caller-supplied field overrides the URDF default; others read it."""
    model = RobotModel(reach_radius=0.5)

    # the override takes
    assert model.reach_radius == pytest.approx(0.5)
    # everything else still comes from the URDF
    assert model.shoulder_offset_y == pytest.approx(EXPECTED['shoulder_offset_y'])
    assert model.shoulder_offset_z == pytest.approx(EXPECTED['shoulder_offset_z'])
    assert model.min_column_height == pytest.approx(EXPECTED['min_column_height'])
    assert model.max_column_height == pytest.approx(EXPECTED['max_column_height'])
    assert model.home_gripper_offset == Point(*EXPECTED['home_gripper_offset'])
