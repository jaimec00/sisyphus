# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The loader's own test, in the package that owns it (PR6).

``robot_backends/test/test_urdf_model.py`` pins the six constants through the
consuming ``RobotModel`` -- the D23 source-of-truth payoff at the seam.  This
file is the matching claim *at the source*: the description package grew real
code (``robot_model.py``) with the PR6 loader, so per the test-count ratchet it
owes a direct test of that loader here, independent of any consumer wrapping
it into a ``RobotModel``/``Point``.

The assertions are absolute, not self-consistent (D29): the five declared
properties and the derived home-gripper offset are pinned to the literals from
the brief (issue #87) -- the same numbers the Mock backend, the safety layer
and the brain's prompt already enforce.  A loader that returned 0.18 for every
scalar, or derived ``reach_radius`` from the arm's actual reach instead of
reading the ``<xacro:property>``, is a wrong model that a merely
internally-consistent check would let through.
"""

import pytest

from robot_description.robot_model import load_robot_model, RobotModelConstants

#: The literals from the brief -- typed here by hand so a drift in either the
#: URDF or the loader is a failure, never a silent disagreement.
EXPECTED = {
    'shoulder_offset_y': 0.18,
    'shoulder_offset_z': 0.50,
    'reach_radius': 0.85,
    'min_column_height': 0.00,
    'max_column_height': 1.20,
    'home_gripper_offset': (0.35, 0.0, -0.05),
}


def test_load_robot_model_returns_the_brief_constants():
    """The loader returns the five declared properties plus the derived offset."""
    constants = load_robot_model()

    assert isinstance(constants, RobotModelConstants)
    assert constants.shoulder_offset_y == pytest.approx(EXPECTED['shoulder_offset_y'])
    assert constants.shoulder_offset_z == pytest.approx(EXPECTED['shoulder_offset_z'])
    assert constants.reach_radius == pytest.approx(EXPECTED['reach_radius'])
    assert constants.min_column_height == pytest.approx(EXPECTED['min_column_height'])
    assert constants.max_column_height == pytest.approx(EXPECTED['max_column_height'])


def test_home_gripper_offset_matches_the_gate_reference():
    """The derived home offset is the grasp midpoint minus shoulder, at q=0."""
    constants = load_robot_model()

    for got, want in zip(constants.home_gripper_offset,
                         EXPECTED['home_gripper_offset']):
        assert got == pytest.approx(want)
