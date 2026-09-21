# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 — the omni inverse kinematics from ``/cmd_vel`` to wheel speeds (RULING 5).

``robot_nav.omni_base_controller.body_to_wheel`` is a pure function (no graph),
tested here against the LeRobot ``lekiwi.py`` ``_body_to_wheel_raw`` matrix
(mount angles 60/180/300 deg, ``base_radius=0.125``, ``wheel_radius=0.05``) and
against the calibrated sign constants.  The sign is a *sim-calibrated fact*
(``base.xacro``: the URDF/MJCF joint-axis convention is opposite the driver's),
so these tests pin the matrix and the convention, not a re-derivation.  The
sign is **split by column** -- translational (vx/vy) at ``WHEEL_SIGN=-1.0``,
rotational (wz) at ``WZ_SIGN=-1.0`` (the #125 rim-roller plant inverted the wz
channel, so both constants now agree at -1.0); both constants are pinned here.

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
    WZ_SIGN,
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
    """Pure +wz adds the same ``WZ_SIGN * base_radius*wz / wheel_radius``
    to every wheel (the wz column carries its own sign, ``wheel_sign`` is
    irrelevant to it)."""
    left, back, right = body_to_wheel(0.0, 0.0, 0.6, wheel_sign=1.0)
    expected = WZ_SIGN * BASE_RADIUS * 0.6 / WHEEL_RADIUS
    for value in (left, back, right):
        assert abs(value - expected) < 1e-9, (value, expected)


def test_matrix_matches_an_independent_reconstruction():
    """The module's matrix equals an independently built LeRobot K.

    With ``wheel_sign=1.0`` the translational (vx/vy) columns are the raw
    LeRobot result while the rotational (wz) column still carries
    :data:`WZ_SIGN`.
    """
    k = _lekiwi_matrix()
    for vx, vy, wz in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
                       (0.3, -0.2, 0.4), (-0.11, 0.07, -0.9)):
        expected = (k[:, :2] @ np.array([vx, vy]) + WZ_SIGN * k[:, 2] * wz) / WHEEL_RADIUS
        got = np.array(body_to_wheel(vx, vy, wz, wheel_sign=1.0))
        np.testing.assert_allclose(got, expected, atol=1e-9)


def test_calibrated_sign_is_unified_negative_one():
    """The shipped signs are each -1.0: vx/vy AND wz are negated.

    The calibrated value happens to be a **unified -1.0** but the structure is
    still **split by column**: ``WHEEL_SIGN = -1.0`` negates the translational
    columns (``base.xacro``: the URDF/MJCF joint-axis convention is opposite
    the LeRobot driver's), and ``WZ_SIGN = -1.0`` negates the wz column (the PR2
    plain-cylinder plant gave inverted yaw under a global -1.0, hence ``+1.0``
    there, but the #125 rim-roller plant inverts the wz channel).  Both are
    sim-calibrated facts; a change here means re-running that calibration.
    """
    assert WHEEL_SIGN == -1.0, 'translational columns are negated'
    assert WZ_SIGN == -1.0, 'rotational column is negated too'
    vx, vy, wz = 0.3, -0.1, 0.2
    k = _lekiwi_matrix()
    raw_matrix = (k @ np.array([vx, vy, wz])) / WHEEL_RADIUS
    np.testing.assert_allclose(np.array(body_to_wheel(vx, vy, wz)),
                               -raw_matrix, atol=1e-12)
    np.testing.assert_allclose(
        np.array(body_to_wheel(vx, vy, 0.0)),
        -(k[:, :2] @ np.array([vx, vy])) / WHEEL_RADIUS, atol=1e-12)
    np.testing.assert_allclose(
        np.array(body_to_wheel(0.0, 0.0, wz)),
        -(k[:, 2] * wz) / WHEEL_RADIUS, atol=1e-12)


def test_default_pure_wz_rotates_negative():
    """Under the DEFAULT sign, pure +wz yields NEGATIVE wheel speeds.

    The #125 rim-roller plant inverts the wz channel, so ``WZ_SIGN = -1.0``
    negates the wz column: every wheel is driven
    -base_radius*wz/wheel_radius.
    """
    expected = BASE_RADIUS * 0.6 / WHEEL_RADIUS
    for value in body_to_wheel(0.0, 0.0, 0.6):
        assert value < 0.0, value
        assert abs(value + expected) < 1e-12, (value, expected)


def test_default_pure_vx_still_negates_the_translation():
    """Under the DEFAULT sign, pure +vx is the NEGATED translational result.

    The unified global -1.0 negates the vx/vy columns (``WHEEL_SIGN = -1.0``,
    unchanged by #125), so the default is the negated raw matrix for a purely
    translational twist.
    """
    raw = np.array(body_to_wheel(0.3, 0.0, 0.0, wheel_sign=1.0))
    default = np.array(body_to_wheel(0.3, 0.0, 0.0))
    np.testing.assert_allclose(default, -raw, atol=1e-12)


def test_zero_twist_is_zero_wheels():
    """A zero Twist commands zero wheels (the timeout guard's target state)."""
    assert body_to_wheel(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)


def test_scaling_is_linear_in_the_twist():
    """The map is linear: doubling the Twist doubles every wheel speed."""
    single = np.array(body_to_wheel(0.1, 0.05, 0.2))
    double = np.array(body_to_wheel(0.2, 0.1, 0.4))
    np.testing.assert_allclose(double, 2.0 * single, atol=1e-12)
