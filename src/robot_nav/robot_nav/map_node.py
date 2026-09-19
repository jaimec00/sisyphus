# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The static map: query the world, publish ``/map`` + ``map -> odom`` (PR1).

This node is the ROS shell around :mod:`robot_nav.static_map`'s pure
derivation.  It is a **lifecycle node** (RULING 5): Nav2's localization layer
is a set of managed nodes a lifecycle manager brings up in order, and PR1's job
is to prove that machinery configures and activates headless.  The node's real
work is split across the two transitions Nav2 uses:

* **on_configure** -- query ``/world_query/get_world`` (retrying until the
  service appears, since the world node may come up alongside this one), parse
  the canonical JSON with ``robot_world.WorldDocument`` (the strict parser --
  never hand-rolled, RULING 3), project it to an ``OccupancyGrid`` once, and
  build the publisher.  Configuration is the *slow, retrying* half, which is
  exactly why it lives in ``on_configure`` and not in the constructor: a
  lifecycle node must come up UNCONFIGURED and be configurable on demand.
* **on_activate** -- latch the grid on ``/map`` (transient-local QoS), broadcast
  the identity ``map -> odom`` static transform, and start a republish timer so
  a late subscriber that misses the latch still gets the grid.

Why a lifecycle node and not ``nav2_map_server``
------------------------------------------------
RULING 5 prefers standing up a **real Nav2 lifecycle node**.  The intended one
is Nav2's ``map_server``, but it loads its grid from a PGM/YAML pair on disk --
we have no PGM because our map is *derived* at runtime, and RULING 3 keeps
``robot_world`` the single source of truth, so writing a PGM file would be a
second home for the map.  Subclassing ``nav2_util.lifecycle_node.LifecycleNode``
(the Nav2-way managed node) is not available either: on RoboStack the
``nav2_util`` package ships only its C++/CMake artifacts, with **no** Python
``lifecycle_node`` module (verified -- see
``docs/features/pr1-nav2-localization/implementation.md``).  So ``map_node`` is
a managed node on **``rclpy.lifecycle.LifecycleNode``**, the standard ROS 2
lifecycle base, which carries the same ``configure``/``activate`` transitions
and the same ``/map_node/change_state`` service a Nav2 lifecycle manager
drives.  ``nav.launch.py`` additionally stands up the real ``nav2_map_server``
lifecycle node (with a generated PGM) so a *genuine Nav2* lifecycle node is
observed reaching ACTIVE too.

The ``map -> odom`` transform is **identity** on purpose (RULING 3): ground
truth means odom does not drift, and ``map`` is the world frame, so ``map`` and
``odom`` coincide.  It is published with a ``StaticTransformBroadcaster`` --
static, so it costs nothing and is latched for late subscribers.
"""

import json
import time

from nav_msgs.msg import OccupancyGrid
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from robot_nav.static_map import (
    DEFAULT_FOOTPRINT_RADIUS,
    DEFAULT_MARGIN,
    DEFAULT_RESOLUTION,
    occupancy_grid_from_document,
)
from robot_world import WorldDocument
from robot_world_ros_interfaces.srv import GetWorld
from tf2_ros import StaticTransformBroadcaster

__all__ = ['MapNode', 'main']

#: The world query service (RULING 3; the D35 ROS seam -- a node consumes the
#: service, it does not import robot_world internals).
GET_WORLD_SERVICE = '/world_query/get_world'

#: Map frame and odom frame; RULING 3 makes map == world and map -> odom
#: identity.
MAP_FRAME = 'map'
ODOM_FRAME = 'odom'


def _translucent_map_qos() -> QoSProfile:
    """Return the transient-local QoS ``/map`` is published with.

    Standard for a latched map: ``transient_local`` durability means a
    subscriber that appears *after* publication still receives the last grid,
    and ``reliable`` keeps a large grid from being dropped.
    """
    return QoSProfile(
        depth=1,
        history=HistoryPolicy.KEEP_LAST,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )


class MapNode(LifecycleNode):
    """Query the world and publish the derived map + ``map -> odom`` (RULING 5).

    Parameters (declared with defaults):

    * ``world_service`` (string, default ``/world_query/get_world``) -- the
      service the grid is derived from.
    * ``resolution`` (double, default 0.05) -- metres per cell.
    * ``margin`` (double, default 1.0) -- padding around the scene extent.
    * ``footprint_radius`` (double, default 0.05) -- object footprint radius.
    * ``service_wait_timeout`` (double, default 30.0) -- seconds to wait for the
      world service during ``on_configure`` before giving up (a lifecycle
      ``configure`` failure, not a crash).
    * ``republish_period`` (double, default 5.0) -- seconds between periodic
      grid republishes while ACTIVE.
    """

    def __init__(self) -> None:
        """Declare parameters; do no graph or service work yet."""
        super().__init__('map_node')
        self.declare_parameter('world_service', GET_WORLD_SERVICE)
        self.declare_parameter('resolution', DEFAULT_RESOLUTION)
        self.declare_parameter('margin', DEFAULT_MARGIN)
        self.declare_parameter('footprint_radius', DEFAULT_FOOTPRINT_RADIUS)
        self.declare_parameter('service_wait_timeout', 30.0)
        self.declare_parameter('republish_period', 5.0)

        self._grid = None
        self._map_publisher = None
        self._static_broadcaster = None
        self._republish_timer = None

    # -- lifecycle transitions ------------------------------------------------

    def on_configure(self, state) -> TransitionCallbackReturn:
        """Query the world, derive the grid, and build the publisher/transform.

        The world service may not be up yet (the world node and this node come
        up together in the bringup), so the query retries until the service
        appears or ``service_wait_timeout`` elapses.  A timeout is a lifecycle
        ``FAILURE`` -- the node stays UNCONFIGURED and a manager can retry --
        never a raise that takes the process down.

        ``super().on_configure`` runs first: the base implementation drives the
        managed entities' own configure callbacks, which is what wires up the
        lifecycle publisher this node creates below.
        """
        result = super().on_configure(state)
        if result != TransitionCallbackReturn.SUCCESS:
            return result

        service = self.get_parameter('world_service').get_parameter_value().string_value
        resolution = self.get_parameter('resolution').get_parameter_value().double_value
        margin = self.get_parameter('margin').get_parameter_value().double_value
        footprint = (
            self.get_parameter('footprint_radius').get_parameter_value().double_value)
        timeout = (
            self.get_parameter('service_wait_timeout').get_parameter_value().double_value)

        self.get_logger().info(f'configuring: querying {service} for the world')
        document = self._query_world(service, timeout)
        if document is None:
            return TransitionCallbackReturn.FAILURE

        self._grid = occupancy_grid_from_document(
            document,
            resolution=resolution,
            margin=margin,
            footprint_radius=footprint,
        )
        self.get_logger().info(
            'derived map: %dx%d cells @ %.3f m/cell, origin (%.2f, %.2f)'
            % (self._grid.info.width, self._grid.info.height,
               self._grid.info.resolution,
               self._grid.info.origin.position.x,
               self._grid.info.origin.position.y))

        self._map_publisher = self.create_lifecycle_publisher(
            OccupancyGrid, 'map', _translucent_map_qos())
        self._static_broadcaster = StaticTransformBroadcaster(self)
        self._grid.header.frame_id = MAP_FRAME
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state) -> TransitionCallbackReturn:
        """Publish the latched grid, the identity ``map -> odom``, and a timer.

        The republish timer is *in addition* to the latch: a subscriber whose
        QoS does not enable transient-local, or one that subscribes while the
        publisher is briefly reconfigured, still gets a grid without waiting
        for a scene change (which, on a static map, never comes).

        ``super().on_activate`` runs first: it invokes the managed entities'
        activate callbacks, which is what actually *enables* the lifecycle
        publisher -- a publish before that is a silent no-op.
        """
        result = super().on_activate(state)
        if result != TransitionCallbackReturn.SUCCESS:
            return result

        if self._grid is None:
            self.get_logger().error('activating before a grid was derived')
            return TransitionCallbackReturn.FAILURE

        self._publish_map()
        self._broadcast_map_to_odom()

        period = self.get_parameter('republish_period').get_parameter_value().double_value
        if period > 0.0:
            self._republish_timer = self.create_timer(period, self._publish_map)
        self.get_logger().info('map active: /map latched, map -> odom identity published')
        return TransitionCallbackReturn.SUCCESS

    def on_deactivate(self, state) -> TransitionCallbackReturn:
        """Stop the republish timer, then deactivate the managed publisher."""
        if self._republish_timer is not None:
            self.destroy_timer(self._republish_timer)
            self._republish_timer = None
        self.get_logger().info('map deactivated')
        return super().on_deactivate(state)

    def on_cleanup(self, state) -> TransitionCallbackReturn:
        """Drop the derived grid and the publisher; return to UNCONFIGURED."""
        if self._republish_timer is not None:
            self.destroy_timer(self._republish_timer)
            self._republish_timer = None
        if self._map_publisher is not None:
            self.destroy_publisher(self._map_publisher)
            self._map_publisher = None
        self._grid = None
        self.get_logger().info('map cleaned up')
        return super().on_cleanup(state)

    # -- helpers --------------------------------------------------------------

    def _query_world(self, service: str, timeout: float):
        """Return the world document from ``service``, or ``None`` on timeout.

        Retries until the service appears (the graph is still forming during a
        launch) and then until the call completes.  ``timeout`` bounds the
        whole wait, so a configure that cannot reach the world fails in a
        bounded time instead of hanging the lifecycle manager.

        This callback runs *inside* the lifecycle node's own spin (the launch
        emits the ``configure`` transition and ``main`` is already spinning the
        node), so the call cannot be serviced by the node's main executor while
        the callback blocks it -- and a second executor over the same node does
        not help (a node belongs to one executor; the request then never
        completes).  The wait therefore uses a **dedicated throwaway node** of
        its own with its own executor: the blocking call is serviced by that
        executor, and the lifecycle node is left to its main spin.
        """
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node as _Node

        client_node = _Node('map_node_world_client')
        executor = SingleThreadedExecutor(context=self.context)
        executor.add_node(client_node)
        try:
            client = client_node.create_client(GetWorld, service)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not client.service_is_ready():
                executor.spin_once(timeout_sec=0.1)
            if not client.service_is_ready():
                self.get_logger().error(
                    f'world service {service} not available within {timeout:.0f}s')
                return None

            future = client.call_async(GetWorld.Request())
            while time.monotonic() < deadline and not future.done():
                executor.spin_once(timeout_sec=0.05)
        finally:
            executor.remove_node(client_node)
            executor.shutdown()
            client_node.destroy_node()
        if not future.done():
            self.get_logger().error(
                f'world service {service} did not answer within {timeout:.0f}s')
            return None
        response = future.result()
        if response is None:
            self.get_logger().error(f'world service {service} returned no response')
            return None
        try:
            return WorldDocument.from_dict(json.loads(response.world_json))
        except (ValueError, TypeError) as exc:
            # The store's refusals (SerializationError, WorldStoreError) are
            # ValueError subclasses; a malformed document is a configure
            # failure, not a crash.
            self.get_logger().error(f'world document refused: {exc}')
            return None

    def _publish_map(self) -> None:
        """Publish a stamped copy of the derived grid on ``/map``."""
        if self._grid is None or self._map_publisher is None:
            return
        grid = self._grid
        grid.header.stamp = self.get_clock().now().to_msg()
        grid.header.frame_id = MAP_FRAME
        self._map_publisher.publish(grid)

    def _broadcast_map_to_odom(self) -> None:
        """Broadcast the identity ``map -> odom`` static transform (RULING 3).

        Identity because ground truth means odom does not drift and ``map`` is
        the world frame, so ``map`` and ``odom`` coincide.
        """
        from geometry_msgs.msg import TransformStamped
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
        transform.header.frame_id = MAP_FRAME
        transform.child_frame_id = ODOM_FRAME
        transform.transform.translation.x = 0.0
        transform.transform.translation.y = 0.0
        transform.transform.translation.z = 0.0
        transform.transform.rotation.w = 1.0
        self._static_broadcaster.sendTransform(transform)


def main(args=None) -> None:
    """Run the map node until interrupted (starts UNCONFIGURED).

    A bare ``ros2 run robot_nav map_node`` comes up UNCONFIGURED, exactly like
    a Nav2 node: the grid is derived in ``on_configure``, which a lifecycle
    manager (or ``ros2 lifecycle set /map_node configure``) triggers.
    """
    import rclpy
    rclpy.init(args=args)
    node = MapNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
