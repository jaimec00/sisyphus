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

The sign is **split by column**, because the sim does not agree with a single
global sign:

* the **translational** columns (vx/vy) carry :data:`WHEEL_SIGN = -1.0` --
  the LeRobot matrix result is negated for the MJCF joints, and pure ``+vx``
  must drive the base ``+x`` (probe-verified);
* the **rotational** column (wz) carries its own :data:`WZ_SIGN = +1.0` -- a
  global ``-1.0`` gave *inverted* yaw (pure ``+wz`` rotated the base ``-yaw``),
  so the wz column is **not** negated.

See ``docs/features/pr2-nav2-base-drive/implementation.md`` for the observed
evidence and the final values.

Timeout guard
-------------
A controller that keeps the last Twist forever is a runaway.  If no ``cmd_vel``
arrives within ``cmd_vel_timeout`` (default 0.5 s) the node publishes zeros, so
a stale or dropped Nav2 command cannot keep the base driving.  (The full safety
layer, D17, is out of scope here; this is a minimal local guard.)

A keep-alive re-publish at ``publish_rate`` also re-sends the last command, so
the group controller (which expects a fresh command each cycle) does not time
out between ``cmd_vel`` messages.
"""

from __future__ import annotations

from geometry_msgs.msg import Twist
import rclpy
from rclpy.node import Node
from std_msgs.msg import Float64MultiArray

__all__ = ['COMMAND_TOPIC', 'OmniBaseController', 'WHEEL_SIGN',
           'WZ_SIGN', 'body_to_wheel']

#: The velocity group controller's command topic (PR8b ``controllers.yaml``).
COMMAND_TOPIC = '/base_velocity_controller/commands'
#: The Twist topic Nav2's controller server publishes on.
CMD_VEL_TOPIC = 'cmd_vel'

#: Signed **translational** convention for the vx/vy columns: ``+1.0`` uses the
#: LeRobot matrix as-is, ``-1.0`` negates it (the URDF/MJCF joint-axis
#: convention is opposite to the driver's -- see the module docstring).
#: **Calibrated in sim**, not assumed; the value below is the one the sim probe
#: confirmed (``+vx`` drives the base ``+x``).  The **rotational** (wz) column
#: is split out and carries its own sign, :data:`WZ_SIGN` -- a single global
#: sign does **not** fit both (see the module docstring).
WHEEL_SIGN = -1.0

#: Signed **rotational** convention for the ``wz`` column, applied on top of
#: :data:`WHEEL_SIGN`'s translational sign.  **Calibrated in sim**: a global
#: ``-1.0`` gave inverted yaw (pure ``+wz`` rotated the base ``-yaw``), so the
#: wz column is not negated -- ``+1.0`` makes pure ``+wz`` rotate the base
#: ``+yaw``.
WZ_SIGN = +1.0

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
    **translational** (vx/vy) terms (see :data:`WHEEL_SIGN`); pass ``+1.0`` for
    the raw LeRobot convention, ``-1.0`` for the MJCF joint convention.  The
    **rotational** (wz) term carries its own calibrated sign,
    :data:`WZ_SIGN` (see there), which a global ``-1.0`` would get wrong.
    """
    return tuple(
        (wheel_sign * (row[0] * vx + row[1] * vy) + WZ_SIGN * row[2] * wz)
        / WHEEL_RADIUS
        for row in _IK_MATRIX
    )


class OmniBaseController(Node):
    """Convert ``/cmd_vel`` Twists into ``/base_velocity_controller/commands``.

    Parameters (declared with defaults):

    * ``publish_rate`` (double, default 50.0) -- the keep-alive re-publish rate
      in Hz (the group controller expects a fresh command every cycle).
    * ``cmd_vel_timeout`` (double, default 0.5) -- seconds without a
      ``/cmd_vel`` message after which the wheels are commanded zeros.
    * ``cmd_vel_topic`` (string, default ``cmd_vel``) -- the Twist topic.
    * ``command_topic`` (string, default
      ``/base_velocity_controller/commands``) -- the wheel command topic.
    """

    def __init__(self) -> None:
        """Create the subscription, the command publisher and the keep-alive."""
        super().__init__('omni_base_controller')
        self.declare_parameter('publish_rate', 50.0)
        self.declare_parameter('cmd_vel_timeout', 0.5)
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
        cmd_topic = (
            self.get_parameter('cmd_vel_topic').get_parameter_value().string_value
            or CMD_VEL_TOPIC)
        command_topic = (
            self.get_parameter('command_topic').get_parameter_value().string_value
            or COMMAND_TOPIC)

        self._wheels = (0.0, 0.0, 0.0)
        self._last_cmd_time = None
        self._zeroed = True

        self._publisher = self.create_publisher(
            Float64MultiArray, command_topic, 10)
        self.create_subscription(Twist, cmd_topic, self._on_cmd_vel, 10)
        self._timer = self.create_timer(1.0 / rate, self._publish_wheels)
        self.get_logger().info(
            f'omni_base_controller up: {cmd_topic} -> {command_topic} '
            f'(WHEEL_SIGN={WHEEL_SIGN:+.1f}, WZ_SIGN={WZ_SIGN:+.1f}, '
            f'timeout={self._timeout:.2f}s)')

    def _on_cmd_vel(self, msg: Twist) -> None:
        """Convert an incoming Twist to wheel speeds and remember the time."""
        self._wheels = body_to_wheel(
            msg.linear.x, msg.linear.y, msg.angular.z)
        self._last_cmd_time = self.get_clock().now()
        if self._zeroed:
            self._zeroed = False
            self.get_logger().info('cmd_vel received; base control engaged')

    def _publish_wheels(self) -> None:
        """Publish the (possibly zeroed) wheel speeds; keep the controller fed.

        The keep-alive runs at ``publish_rate`` so the group controller always
        has a fresh command, and it substitutes zeros once
        ``cmd_vel_timeout`` has elapsed since the last Twist.
        """
        if self._last_cmd_time is not None:
            age = (self.get_clock().now() - self._last_cmd_time).nanoseconds * 1e-9
            if age > self._timeout:
                if not self._zeroed:
                    self._zeroed = True
                    self.get_logger().warn(
                        f'no cmd_vel for {age:.2f}s; zeroing the wheels')
                wheels = (0.0, 0.0, 0.0)
            else:
                wheels = self._wheels
        else:
            wheels = (0.0, 0.0, 0.0)

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
