# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 — the omni inverse kinematics from ``/cmd_vel`` to wheel speeds (RULING 5).

``robot_nav.omni_base_controller.body_to_wheel`` is a pure function (no graph),
tested here against the LeRobot ``lekiwi.py`` ``_body_to_wheel_raw`` matrix
(mount angles 60/180/300 deg, ``base_radius=0.125``, ``wheel_radius=0.05``) and
against the calibrated sign constant.  The sign is a *sim-calibrated fact*
(``base.xacro``: the URDF/MJCF joint-axis convention is opposite the driver's),
so these tests pin the matrix and the convention, not a re-derivation.

The pure-function half needs no ROS; the matrix it uses is also cross-checked
here against an independent NumPy computation of ``K`` so a typo in the
hand-written constants cannot pass.
"""

import math

import numpy as np

from robot_nav.omni_base_controller import (
    BASE_RADIUS,
    body_to_wheel,
    WHEEL_RADIUS,
    WHEEL_SIGN,
)


def _lekiwi_matrix():
    """Independently reconstruct the LeRobot K matrix (60/180/300 deg mounts)."""
    return np.array([
        [-math.sin(math.radians(60.0)), math.cos(math.radians(60.0)), BASE_RADIUS],
        [-math.sin(math.radians(180.0)), math.cos(math.radians(180.0)), BASE_RADIUS],
        [-math.sin(math.radians(300.0)), math.cos(math.radians(300.0)), BASE_RADIUS],
    ])


def test_geometry_constants_match_the_urdf_sources():
    """The wheel/base radii are the LeRobot-sourced values from base.xacro."""
    assert WHEEL_RADIUS == 0.05
    assert BASE_RADIUS == 0.125


def test_pure_vx_splits_left_right_and_zeroes_the_back_wheel():
    """Pure +vx: the back wheel's rolling direction is perpendicular, so it is 0.

    The back wheel's mount angle is 180 deg, so its rolling direction d =
    (-sin 180, cos 180) = (0, -1) -- purely along y, hence no vx projection.
    The left/right wheels (60/300 deg) carry the +vx (opposite signs, the omni
    pair).
    """
    left, back, right = body_to_wheel(0.3, 0.0, 0.0, wheel_sign=1.0)
    assert abs(back) < 1e-9, back
    assert abs(left + right) < 1e-9, (left, right)
    assert left < 0.0 < right
    # Magnitude: 0.866*0.3 / 0.05.
    expected = 0.8660254037844386 * 0.3 / 0.05
    assert abs(right - expected) < 1e-9, (right, expected)
    assert abs(left + expected) < 1e-9, (left, expected)


def test_pure_vy_drives_all_three_wheels():
    """Pure +vy projects on every wheel (all mount directions have a +y part)."""
    left, back, right = body_to_wheel(0.0, 0.2, 0.0, wheel_sign=1.0)
    # back: -1.0 * 0.2 / 0.05 = -4.0; left/right: 0.5 * 0.2 / 0.05 = +2.0.
    assert abs(back - (-0.2 / 0.05)) < 1e-9, back
    assert abs(left - (0.5 * 0.2 / 0.05)) < 1e-9, left
    assert abs(right - (0.5 * 0.2 / 0.05)) < 1e-9, right


def test_pure_wz_turns_every_wheel_equally():
    """Pure +wz adds the same base_radius*wz/ wheel_radius to every wheel."""
    left, back, right = body_to_wheel(0.0, 0.0, 0.6, wheel_sign=1.0)
    expected = BASE_RADIUS * 0.6 / WHEEL_RADIUS
    for value in (left, back, right):
        assert abs(value - expected) < 1e-9, (value, expected)


def test_matrix_matches_an_independent_reconstruction():
    """The module's matrix equals an independently built LeRobot K (all axes)."""
    k = _lekiwi_matrix()
    for vx, vy, wz in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
                       (0.3, -0.2, 0.4), (-0.11, 0.07, -0.9)):
        expected = (k @ np.array([vx, vy, wz])) / WHEEL_RADIUS
        got = np.array(body_to_wheel(vx, vy, wz, wheel_sign=1.0))
        np.testing.assert_allclose(got, expected, atol=1e-9)


def test_calibrated_sign_is_the_mjcf_joint_convention():
    """The default result is the negated (MJCF joint-axis) convention.

    ``base.xacro`` states the URDF/MJCF joint-axis convention is opposite the
    LeRobot driver's: a positive joint velocity moves the body along -d.  So the
    shipped ``WHEEL_SIGN`` is -1.0 and the default output is the negated matrix
    result.  (The value was confirmed empirically in sim; a change here means
    re-running that calibration.)
    """
    assert WHEEL_SIGN == -1.0
    raw = np.array(body_to_wheel(0.3, -0.1, 0.2, wheel_sign=1.0))
    default = np.array(body_to_wheel(0.3, -0.1, 0.2))
    np.testing.assert_allclose(default, -raw, atol=1e-12)


def test_zero_twist_is_zero_wheels():
    """A zero Twist commands zero wheels (the timeout guard's target state)."""
    assert body_to_wheel(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)


def test_scaling_is_linear_in_the_twist():
    """The map is linear: doubling the Twist doubles every wheel speed."""
    single = np.array(body_to_wheel(0.1, 0.05, 0.2))
    double = np.array(body_to_wheel(0.2, 0.1, 0.4))
    np.testing.assert_allclose(double, 2.0 * single, atol=1e-12)
