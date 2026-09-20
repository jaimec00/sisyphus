# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

r"""Ground-truth ``odom -> base_link`` from the sim's live base pose (RULING 1/3, D29).

Nav2 needs an odometric edge. On the classical/real track (D36) that edge
normally comes from wheel odometry; in today's PR8b ROS-sim it comes from ground
truth -- the dfki-ric ``GetBodyState`` service (``/mujoco_get_body_state``),
which returns any named body's world-frame ``pose`` + ``twist``.

PR1 shipped this node with the base pose behind a **seam** (a one-function
:func:`base_pose`) returning the constant world-origin identity, because the
sim's base was *welded* (``fusestatic`` folds the static trunk into the world
body -- no ``base_link`` body, no free joint). PR2 (issue #124, RULING 1) frees
the base in the ROS-sim path and drives it, so the seam is now **live**:
:meth:`GroundTruthOdomNode._read_base_state` reads the free ``base_link`` body's
pose + twist from the service each cycle.

Three things this node publishes, and why
------------------------------------------
1. **TF ``odom -> base_link``** at ``publish_rate`` -- the odometric edge Nav2
   consumes for its ``odom`` frame (RULING 3). It is ``odom -> base_link``, never
   ``odom -> base_footprint``: this repo **inverted** REP-105's tree in D29, so
   ``base_footprint`` is a *fixed child* of ``base_link`` and already published
   that way by robot_state_publisher. Giving ``base_footprint`` a second parent
   would break the tree at runtime.
2. **``nav_msgs/Odometry`` on ``/odom``** (RULING 3) -- Nav2's controller and
   velocity smoother consume ``/odom`` for velocity feedback; TF alone leaves
   them velocity-blind. The twist comes from the *same* service response.
3. The ``map -> odom`` identity static TF is **unchanged** (published by
   ``map_node``): ground truth means odom does not drift, so map and odom
   coincide.

Robustness: the service may be unavailable, slow, or (on the *welded* model, or
before the sim is up) return ``success=false``. Rather than publishing NaN, the
node falls back to the last-known pose -- the identity until the first good read
-- logs the failure **once** per transition, and keeps the TF tree flowing.

The resulting tree is

    map -> odom -> base_link -> base_footprint
                      \\-> wheels / column / arms / grippers
"""

from geometry_msgs.msg import TransformStamped
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile

from robot_skills import Pose
from tf2_ros import TransformBroadcaster

__all__ = ['GroundTruthOdomNode', 'base_pose', 'main']

#: Parent frame of the odometric edge (RULING 2/D29): the odom frame.
ODOM_FRAME = 'odom'
#: Child frame of the odometric edge -- ``base_link``, NOT ``base_footprint``.
BASE_FRAME = 'base_link'
#: Ground-truth base pose when nothing better is known yet (the world origin,
#: which *is* ``start_location`` -- ``charger`` at (0, 0, 0)).
DEFAULT_BASE_POSE = Pose.from_xyz(0.0, 0.0, 0.0)

#: The dfki-ric body-state service and the body whose state is the base pose.
#: The free-jointed body ``_wrap_base_freejoint`` creates is named ``base_link``
#: (it only exists once the base is freed -- RULING 1), so that is the name to
#: read. On the welded model there is no such body and the read fails.
BODY_STATE_SERVICE = '/mujoco_get_body_state'
BASE_BODY_NAME = 'base_link'

#: Odometry topic (RULING 3: Nav2's controller/velocity smoother read it).
ODOM_TOPIC = '/odom'


def base_pose() -> Pose:
    """Return the robot base's pose in the ``odom`` frame (the seam).

    PR1 shipped this as the constant world-origin identity; PR2 makes it live by
    reading the dfki-ric ``GetBodyState`` service in
    :meth:`GroundTruthOdomNode._read_base_state`.  The module-level function
    remains as the documented default/fallback (identity), so the seam a caller
    replaces is still a single, obvious thing: the node's pose source.
    """
    return DEFAULT_BASE_POSE


def _odom_qos() -> QoSProfile:
    """Return the QoS for the ``/odom`` publisher (reliable, volatile, depth 10).

    Nav2 subscribes to ``/odom`` with a standard sensor-ish QoS; reliable with a
    small depth matches the default subscription and drops no pose on a slow
    spin.
    """
    return QoSProfile(depth=10, durability=DurabilityPolicy.VOLATILE)


class GroundTruthOdomNode(Node):
    """Publish the ground-truth ``odom -> base_link`` TF + ``/odom`` (RULING 3).

    Parameters (declared with defaults):

    * ``publish_rate`` (double, default 50.0) -- TF broadcast / odom publish rate.
    * ``odom_frame`` (string, default ``odom``) -- parent frame.
    * ``base_frame`` (string, default ``base_link``) -- child frame.
    * ``body_state_service`` (string, default ``/mujoco_get_body_state``) -- the
      GetBodyState service the live pose is read from.
    * ``base_body_name`` (string, default ``base_link``) -- the body to read.
    """

    def __init__(self) -> None:
        """Create the broadcaster, the odom publisher, and the service client."""
        super().__init__('ground_truth_odom')
        self.declare_parameter('publish_rate', 50.0)
        self.declare_parameter('odom_frame', ODOM_FRAME)
        self.declare_parameter('base_frame', BASE_FRAME)
        self.declare_parameter('body_state_service', BODY_STATE_SERVICE)
        self.declare_parameter('base_body_name', BASE_BODY_NAME)

        rate = self.get_parameter('publish_rate').get_parameter_value().double_value
        if rate <= 0.0:
            raise ValueError(f'publish_rate must be positive, got {rate}')
        self._odom_frame = (
            self.get_parameter('odom_frame').get_parameter_value().string_value
            or ODOM_FRAME)
        self._base_frame = (
            self.get_parameter('base_frame').get_parameter_value().string_value
            or BASE_FRAME)
        self._service_name = (
            self.get_parameter('body_state_service')
            .get_parameter_value().string_value or BODY_STATE_SERVICE)
        self._body_name = (
            self.get_parameter('base_body_name').get_parameter_value().string_value
            or BASE_BODY_NAME)

        # Last-known state; starts at the identity so the tree is complete
        # before the first successful read (never NaN).
        self._pose, self._twist = DEFAULT_BASE_POSE, None
        self._have_state = False
        self._client = None
        self._pending = None
        self._logged_unavailable = False
        self._logged_failure = False

        self._broadcaster = TransformBroadcaster(self)
        from nav_msgs.msg import Odometry  # local import: keeps module ROS-lazy
        self._odom_publisher = self.create_publisher(Odometry, ODOM_TOPIC,
                                                     _odom_qos())
        self._timer = self.create_timer(1.0 / rate, self._broadcast)
        self.get_logger().info(
            f'ground-truth odom up: {self._odom_frame} -> {self._base_frame} '
            f'at {rate:.1f} Hz, live from {self._service_name} '
            f'({self._body_name}); publishing {ODOM_TOPIC}')

    # -- pose source (the seam, now live) ------------------------------------

    def _ensure_client(self):
        """Lazily create the GetBodyState client (the srv import is ROS-runtime)."""
        if self._client is None:
            from mujoco_ros2_control.srv import GetBodyState
            self._client = self.create_client(GetBodyState, self._service_name)
        return self._client

    def _read_base_state(self):
        """Return the live ``(Pose, Twist)`` for the base body, or ``None``.

        Non-blocking: the request is dispatched and its response collected on a
        later cycle.  ``None`` means "no fresh state this cycle" -- the caller
        keeps the last-known pose (never NaN).  A service that is unavailable or
        a response with ``success=false`` is logged **once** (per transition) so
        a persistent failure is visible without flooding the log.
        """
        client = self._ensure_client()
        if not client.service_is_ready():
            if not self._logged_unavailable:
                self._logged_unavailable = True
                self.get_logger().warn(
                    f'{self._service_name} not available yet; holding the '
                    f'last-known base pose')
            return None
        self._logged_unavailable = False

        # Collect a completed request from a previous cycle, if any.
        if self._pending is not None:
            if not self._pending.done():
                return None
            response = self._pending.result()
            self._pending = None
            if response is None or not response.success:
                message = getattr(response, 'message', 'no response')
                if not self._logged_failure:
                    self._logged_failure = True
                    self.get_logger().warn(
                        f'GetBodyState({self._body_name!r}) failed: {message}; '
                        f'holding the last-known base pose')
                return None
            self._logged_failure = False
            self._have_state = True
            return response.pose, response.twist

        # Nothing pending: fire the next request.
        self._pending = client.call_async(
            _body_state_request(self._body_name))
        return None

    # -- publication ----------------------------------------------------------

    def _broadcast(self) -> None:
        """Broadcast TF ``odom -> base_link`` and publish ``/odom``."""
        fresh = self._read_base_state()
        if fresh is not None:
            self._pose, self._twist = fresh

        stamp = self.get_clock().now().to_msg()
        pose = self._pose

        transform = TransformStamped()
        transform.header.stamp = stamp
        transform.header.frame_id = self._odom_frame
        transform.child_frame_id = self._base_frame
        transform.transform.translation.x = pose.position.x
        transform.transform.translation.y = pose.position.y
        transform.transform.translation.z = pose.position.z
        transform.transform.rotation.x = pose.orientation.x
        transform.transform.rotation.y = pose.orientation.y
        transform.transform.rotation.z = pose.orientation.z
        transform.transform.rotation.w = pose.orientation.w
        self._broadcaster.sendTransform(transform)

        self._publish_odom(stamp, pose)

    def _publish_odom(self, stamp, pose) -> None:
        """Publish the same pose + twist as ``nav_msgs/Odometry`` on ``/odom``.

        Twist is taken from the same GetBodyState response (RULING 3); until a
        first good read it is zero, never NaN.
        """
        from nav_msgs.msg import Odometry
        message = Odometry()
        message.header.stamp = stamp
        message.header.frame_id = self._odom_frame
        message.child_frame_id = self._base_frame
        message.pose.pose.position.x = pose.position.x
        message.pose.pose.position.y = pose.position.y
        message.pose.pose.position.z = pose.position.z
        message.pose.pose.orientation.x = pose.orientation.x
        message.pose.pose.orientation.y = pose.orientation.y
        message.pose.pose.orientation.z = pose.orientation.z
        message.pose.pose.orientation.w = pose.orientation.w
        if self._twist is not None:
            message.twist.twist.linear.x = self._twist.linear.x
            message.twist.twist.linear.y = self._twist.linear.y
            message.twist.twist.linear.z = self._twist.linear.z
            message.twist.twist.angular.x = self._twist.angular.x
            message.twist.twist.angular.y = self._twist.angular.y
            message.twist.twist.angular.z = self._twist.angular.z
        self._odom_publisher.publish(message)


def _body_state_request(body_name: str):
    """Build a ``GetBodyState`` request for ``body_name``.

    Isolated so the ROS-runtime srv import stays out of the module import path
    (D30 discipline: this module is only imported by ROS nodes, but keeping the
    import local is the repo idiom and keeps a no-ROS import cheap).
    """
    from mujoco_ros2_control.srv import GetBodyState
    request = GetBodyState.Request()
    request.body_name = body_name
    return request


def main(args=None) -> None:
    """Run the ground-truth odometry node until interrupted."""
    rclpy.init(args=args)
    node = GroundTruthOdomNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
