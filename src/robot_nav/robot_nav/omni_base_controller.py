# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

r"""Drive the 3-omniwheel holonomic base from ``/cmd_vel`` (issue #124, RULING 5).

Nav2's controller server publishes a ``geometry_msgs/Twist`` on ``/cmd_vel``.
The PR8b sim exposes the base as a ``velocity_controllers/JointGroupVelocityController``
(``base_velocity_controller``) over three wheel joints, commanded by a
``std_msgs/Float64MultiArray`` of 3 on ``/base_velocity_controller/commands`` in
the order ``[base_left_wheel, base_back_wheel, base_right_wheel]``.  This node
is the one conversion between the two: subscribe to ``/cmd_vel``, run the
holonomic inverse kinematics, publish the wheel speeds, and zero them if the
Twist stream stops.

The inverse kinematics (LeRobot ``lekiwi.py`` ``_body_to_wheel_raw``)
--------------------------------------------------------------------
The base is the LeKiwi 3-omniwheel holonomic platform (D26): ``base_radius =
0.125 m`` (centre-to-axle), ``wheel_radius = 0.05 m`` (``base.xacro`` sources
both from the driver).  The mount angles are left/back/right = 60/180/300 deg,
and each wheel rolls along ``d = (-sin(phi), cos(phi))``, so the body velocity
``(vx, vy)`` projected on a wheel's rolling direction is ``-sin(phi)*vx +
cos(phi)*vy`` and every wheel also sees the rotation ``wz`` scaled by
``base_radius``::

    K = [[-sin(60deg),  cos(60deg),  base_radius],     [[-0.8660254,  0.5, 0.125],
         [-sin(180deg), cos(180deg), base_radius],  =   [ 0.0,       -1.0, 0.125],
         [-sin(300deg), cos(300deg), base_radius]]      [ 0.8660254,  0.5, 0.125]]

    wheel_angular = (1.0 / wheel_radius) * K @ [vx, vy, wz]

Which is, explicitly::

    wl = (-0.8660254*vx + 0.5*vy + 0.125*wz) / 0.05
    wb = ( 0.0*vx       - 1.0*vy + 0.125*wz) / 0.05
    wr = ( 0.8660254*vx + 0.5*vy + 0.125*wz) / 0.05

Sign convention (``WHEEL_SIGN`` / ``WZ_SIGN``) -- calibrated in sim, not derived
--------------------------------------------------------------------------------
The LeRobot driver convention is *positive wheel speed spins the wheel so the
body moves along* ``d``.  The URDF/MJCF joint-axis convention is the
**opposite**: a positive joint velocity moves the body along ``-d``
(``base.xacro`` states this verbatim on the mount-angle macros, see also D29).
That is a *fact about the assembled sim*, not something to re-derive, so each
sign lives behind its own constant and was **calibrated empirically** in the sim
(publish a pure body twist, read ``GetBodyState('base_link')``, confirm the sign,
flip the constant if it comes out backwards).

The sign is **split by column** -- the two columns are calibrated separately,
and after #125 they *happen* to agree at ``-1.0`` (so the net effect is a single
global negation, but the two constants stay separate and are applied
independently):

* the **translational** columns (vx/vy) carry :data:`WHEEL_SIGN = -1.0` --
  the LeRobot matrix result is negated for the MJCF joints, and pure ``+vx``
  must drive the base ``+x`` (probe-verified);
* the **rotational** column (wz) carries :data:`WZ_SIGN = -1.0` -- the same
  joint-axis convention that negates the translational columns also negates the
  rotational one, so pure ``+wz`` must rotate the base ``+yaw`` (probe-verified
  in isolated sim and through the ROS path).  Note this is a *clean* fact, not
  an "inversion relative to PR2": the #125 red-team initially read the ROS path
  as rotating ``-yaw`` because the probe wrapped ``end - start`` yaw once over
  an 8 s window that turns more than pi (a measurement aliasing artifact, see
  below), which is what briefly made ``WZ_SIGN`` look like the thing to flip.

A **pitfall for anyone re-calibrating this**: measure yaw *incrementally*, not
by wrapping ``end_yaw - start_yaw`` once.  A pure ``+wz`` drive of a few seconds
turns more than half a turn, and a single ``atan2`` wrap then reports the wrong
sign (``+279 deg`` aliases to ``-81 deg``).  The #125 probe did exactly that and
produced the phantom "the ROS path inverts wz" symptom; the fix was in the
*measurement*, not the plant.

See ``docs/features/pr2-nav2-base-drive/implementation.md`` (the original
PR2 calibration) and ``docs/features/rim-roller-omniwheel/implementation.md``
(the #125 re-calibration) for the observed evidence and the final values.

Timeout guard
-------------
A controller that keeps the last Twist forever is a runaway.  If no ``cmd_vel``
arrives within ``cmd_vel_timeout`` (default 0.5 s) the node publishes zeros, so
a stale or dropped Nav2 command cannot keep the base driving.  (The full safety
layer, D17, is out of scope here; this is a minimal local guard.)

A keep-alive re-publish at ``publish_rate`` also re-sends the last command, so
the group controller (which expects a fresh command each cycle) does not time
out between ``cmd_vel`` messages.

Acceleration limit (issue #141)
-------------------------------
A ``cmd_vel`` Twist is a *velocity* command, and an unramped one steps the
wheels 0 -> full rate in a single tick.  In the shipped ROS loop (MuJoCo
3.12.0, ``_ROLLER_MASS=0.01``) that sharp step tips the tall, top-heavy chassis
forward onto its nose: the free-jointed base pitches 0 -> +1.21 rad (~69 deg)
over ~1.2 s, then locks nose-down with the wheels free-spinning airborne, so
the base delivers only ~0.5x of the commanded speed (issue #141; the earlier
#138 "roller-contact slip" reading was refuted -- the model is byte-identical
and the direct path never tips).

The fix is a **trapezoidal ramp on the commanded body velocity** at this seam:
the commanded ``(vx, vy, wz)`` is slewed toward the target Twist at
``max_accel`` (linear, m/s^2) and ``max_angular_accel`` (rad/s^2) rather than
jumping there.  This is a *physical* parameter -- a real base cannot teleport
to speed -- and it is **not** a magnitude cap: the steady-state speed is
unchanged (only the transient is shaped).  A ramp time of R >= ~0.03-0.05 s
(``max_accel`` <= ~4-6.7 m/s^2 at 0.20 m/s) prevents the tip and restores
~1.0x delivery; the default ``max_accel=4.0`` sits at the conservative end of
that verified band.

The timeout behaves symmetrically: on timeout the *target* becomes (0, 0, 0)
and the ramp decelerates the wheels to rest over the same accel limit (jumping
the wheels to zero instantly would be the same sharp step in reverse).
"""

from __future__ import annotations

import math

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray

__all__ = ['COMMAND_TOPIC', 'OmniBaseController', 'WHEEL_SIGN',
           'WZ_SIGN', 'body_to_wheel', 'ramp_velocity']

#: The velocity group controller's command topic (PR8b ``controllers.yaml``).
COMMAND_TOPIC = '/base_velocity_controller/commands'
#: The Twist topic Nav2's controller server publishes on.
CMD_VEL_TOPIC = 'cmd_vel'

#: Signed **translational** convention for the vx/vy columns: ``+1.0`` uses the
#: LeRobot matrix as-is, ``-1.0`` negates it (the URDF/MJCF joint-axis
#: convention is opposite to the driver's -- see the module docstring).
#: **Calibrated in sim**, not assumed; the value below is the one the sim probe
#: confirmed (``+vx`` drives the base ``+x``).  The **rotational** (wz) column
#: is split out and carries its own sign, :data:`WZ_SIGN` (see there); after
#: #125 both are ``-1.0``.
WHEEL_SIGN = -1.0

#: Signed **rotational** convention for the ``wz`` column, applied on top of
#: :data:`WHEEL_SIGN`'s translational sign.  **Calibrated in sim**: the same
#: joint-axis convention that negates the translational columns negates the
#: rotational one, so pure ``+wz`` rotates the base ``+yaw`` (probe-verified in
#: isolated sim and through the ROS path).  (The #125 red-team first read the
#: ROS path as ``-yaw`` only because the probe wrapped ``end - start`` once over
#: a >pi rotation -- a measurement artifact, not a plant inversion; see the
#: module docstring's re-calibration pitfall.)
WZ_SIGN = -1.0

#: Wheel radius, m (``base.xacro`` sources ``wheel_radius = 0.05`` from LeRobot).
WHEEL_RADIUS = 0.05
#: Centre-to-axle radius, m (``base_radius = 0.125``).
BASE_RADIUS = 0.125

#: The LeRobot ``K`` matrix (mount angles left/back/right = 60/180/300 deg),
#: rows in the controller's joint order ``[left, back, right]``.
_IK_MATRIX = (
    (-0.8660254037844386, 0.5, BASE_RADIUS),   # left,  phi = 60 deg
    (0.0, -1.0, BASE_RADIUS),                  # back,  phi = 180 deg
    (0.8660254037844386, 0.5, BASE_RADIUS),    # right, phi = 300 deg
)


def body_to_wheel(vx: float, vy: float, wz: float,
                  wheel_sign: float = WHEEL_SIGN) -> tuple[float, float, float]:
    """Convert a body Twist to the 3 wheel angular velocities (rad/s).

    Pure function (unit-testable without a graph).  The sign is **split by
    column**: ``wheel_sign`` is the empirically calibrated sign for the
    **translational** (vx/vy) terms (default :data:`WHEEL_SIGN`), and the
    **rotational** (wz) term carries its own calibrated sign :data:`WZ_SIGN`.
    After #125 both are ``-1.0``, so the net effect is a single global ``-1.0``,
    but the two constants stay separate so the two sim calibrations remain
    independently adjustable.  Pass ``+1.0`` for ``wheel_sign`` to get the raw
    LeRobot translation (the wz column still uses :data:`WZ_SIGN`).
    """
    return tuple(
        (wheel_sign * (row[0] * vx + row[1] * vy) + WZ_SIGN * row[2] * wz)
        / WHEEL_RADIUS
        for row in _IK_MATRIX
    )

#: Default linear acceleration limit, m/s^2 (issue #141).  The verified no-tip
#: band for the shipped ROS loop is R >= ~0.03-0.05 s, i.e. <= ~4.0-6.7 m/s^2
#: at the 0.20 m/s acceptance speed; 4.0 m/s^2 is the conservative end of that
#: band (a 0.05 s ramp to 0.20 m/s).
DEFAULT_MAX_ACCEL = 4.0
#: Default angular acceleration limit, rad/s^2 (issue #141).  There is no
#: measured no-tip bound for yaw (the fidelity regression drives pure ``+vx``,
#: so wz is zero throughout and this value does not affect it); it is set to
#: ``DEFAULT_MAX_ACCEL / BASE_RADIUS`` -- the linear limit expressed at the
#: wheel-contact radius -- as a simple, defensible, *documented* choice rather
#: than an arbitrary number.
DEFAULT_MAX_ANGULAR_ACCEL = DEFAULT_MAX_ACCEL / BASE_RADIUS


def ramp_velocity(current: tuple[float, float, float],
                  target: tuple[float, float, float],
                  max_accel: float,
                  max_angular_accel: float,
                  dt: float) -> tuple[float, float, float]:
    """Slew ``current`` toward ``target`` respecting per-axis accel limits.

    Pure function (unit-testable without a graph).  Each linear component
    (``vx``, ``vy``) may change by at most ``max_accel * dt`` this tick, and the
    angular component (``wz``) by at most ``max_angular_accel * dt``; a
    component already within one step of its target snaps exactly onto it (no
    overshoot, no residual).  A non-positive ``dt`` returns ``current``
    unchanged (so a degenerate timer period cannot produce NaN or a jump).

    This is the issue #141 ramp: applied every controller tick between the
    commanded Twist and the holonomic IK, it turns a velocity *step* into a
    trapezoidal command, which is what stops the base from tipping.
    """
    if dt <= 0.0:
        return current
    limits = (max_accel, max_accel, max_angular_accel)
    out = []
    for cur, tgt, limit in zip(current, target, limits):
        step = limit * dt
        delta = tgt - cur
        if abs(delta) <= step:
            out.append(tgt)
        else:
            out.append(cur + math.copysign(step, delta))
    return (out[0], out[1], out[2])


class OmniBaseController(Node):
    """Convert ``/cmd_vel`` Twists into ``/base_velocity_controller/commands``.

    Parameters (declared with defaults):

    * ``publish_rate`` (double, default 50.0) -- the keep-alive re-publish rate
      in Hz (the group controller expects a fresh command every cycle).
    * ``cmd_vel_timeout`` (double, default 0.5) -- seconds without a
      ``/cmd_vel`` message after which the wheels are commanded zeros.
    * ``max_accel`` (double, default 4.0) -- linear acceleration limit, m/s^2,
      for the commanded body velocity (issue #141; the trapezoidal ramp that
      stops the base tipping on a step command).
    * ``max_angular_accel`` (double, default ``max_accel / BASE_RADIUS``,
      ~32.0 rad/s^2) -- angular acceleration limit, rad/s^2, for the commanded
      yaw rate.
    * ``cmd_vel_topic`` (string, default ``cmd_vel``) -- the Twist topic.
    * ``command_topic`` (string, default
      ``/base_velocity_controller/commands``) -- the wheel command topic.
    """

    def __init__(self) -> None:
        """Create the subscription, the command publisher and the keep-alive."""
        super().__init__('omni_base_controller')
        self.declare_parameter('publish_rate', 50.0)
        self.declare_parameter('cmd_vel_timeout', 0.5)
        self.declare_parameter('max_accel', DEFAULT_MAX_ACCEL)
        self.declare_parameter('max_angular_accel', DEFAULT_MAX_ANGULAR_ACCEL)
        self.declare_parameter('cmd_vel_topic', CMD_VEL_TOPIC)
        self.declare_parameter('command_topic', COMMAND_TOPIC)

        rate = self.get_parameter('publish_rate').get_parameter_value().double_value
        if rate <= 0.0:
            raise ValueError(f'publish_rate must be positive, got {rate}')
        self._timeout = (
            self.get_parameter('cmd_vel_timeout').get_parameter_value().double_value)
        if self._timeout <= 0.0:
            raise ValueError(
                f'cmd_vel_timeout must be positive, got {self._timeout}')
        self._max_accel = (
            self.get_parameter('max_accel').get_parameter_value().double_value)
        if self._max_accel <= 0.0:
            raise ValueError(
                f'max_accel must be positive, got {self._max_accel}')
        self._max_angular_accel = (
            self.get_parameter('max_angular_accel')
            .get_parameter_value().double_value)
        if self._max_angular_accel <= 0.0:
            raise ValueError(
                f'max_angular_accel must be positive, got '
                f'{self._max_angular_accel}')
        cmd_topic = (
            self.get_parameter('cmd_vel_topic').get_parameter_value().string_value
            or CMD_VEL_TOPIC)
        command_topic = (
            self.get_parameter('command_topic').get_parameter_value().string_value
            or COMMAND_TOPIC)

        #: Nominal period of the keep-alive timer; the ramp steps per tick.
        self._dt = 1.0 / rate
        #: The commanded body Twist most recently received (the ramp target).
        self._target = (0.0, 0.0, 0.0)
        #: The ramped body Twist actually sent through the IK (the ramp state).
        self._cur = (0.0, 0.0, 0.0)
        self._last_cmd_time = None
        self._zeroed = True

        self._publisher = self.create_publisher(
            Float64MultiArray, command_topic, 10)
        self.create_subscription(Twist, cmd_topic, self._on_cmd_vel, 10)
        self._timer = self.create_timer(1.0 / rate, self._publish_wheels)
        self.get_logger().info(
            f'omni_base_controller up: {cmd_topic} -> {command_topic} '
            f'(WHEEL_SIGN={WHEEL_SIGN:+.1f}, WZ_SIGN={WZ_SIGN:+.1f}, '
            f'timeout={self._timeout:.2f}s, max_accel={self._max_accel:.2f} '
            f'm/s^2, max_angular_accel={self._max_angular_accel:.2f} rad/s^2)')

    def _on_cmd_vel(self, msg: Twist) -> None:
        """Record the incoming Twist as the ramp *target* and remember the time."""
        self._target = (msg.linear.x, msg.linear.y, msg.angular.z)
        self._last_cmd_time = self.get_clock().now()
        if self._zeroed:
            self._zeroed = False
            self.get_logger().info('cmd_vel received; base control engaged')

    def _publish_wheels(self) -> None:
        """Ramp toward the target, publish the wheel speeds, keep the controller fed.

        The keep-alive runs at ``publish_rate`` so the group controller always
        has a fresh command, and it substitutes zeros once
        ``cmd_vel_timeout`` has elapsed since the last Twist -- but as a
        *target* that the ``max_accel``/``max_angular_accel`` ramp decelerates
        toward, not an instant jump (issue #141).
        """
        if (self._last_cmd_time is not None
                and (self.get_clock().now() - self._last_cmd_time).nanoseconds
                * 1e-9 <= self._timeout):
            target = self._target
        else:
            if self._last_cmd_time is not None and not self._zeroed:
                self._zeroed = True
                age = ((self.get_clock().now() - self._last_cmd_time).nanoseconds
                       * 1e-9)
                self.get_logger().warn(
                    f'no cmd_vel for {age:.2f}s; zeroing the wheels')
            target = (0.0, 0.0, 0.0)

        self._cur = ramp_velocity(
            self._cur, target, self._max_accel, self._max_angular_accel,
            self._dt)
        wheels = body_to_wheel(*self._cur)

        msg = Float64MultiArray()
        msg.data = [float(w) for w in wheels]
        self._publisher.publish(msg)


def main(args=None) -> None:
    """Run the omni base controller until interrupted."""
    rclpy.init(args=args)
    node = OmniBaseController()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
