# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

r"""Ground-truth ``odom -> base_link`` (RULING 1/RULING 2, D29).

Nav2 needs an odometric edge.  On the classical/real track (D36) that edge
normally comes from wheel odometry; in today's PR8b ROS-sim it comes from
ground truth, because -- verified by materializing the sim MJCF and grep'ing it
(RULING 1) -- **there is no ``base_link`` body and no free joint in the sim**:
MuJoCo's ``fusestatic`` folds the whole static base trunk into the world body,
so the base is welded at the world origin and cannot move.  The base pose is
therefore the **constant** world-origin identity, and the world origin *is*
``start_location`` (``charger`` at ``(0, 0, 0)``).

The pose source is behind a **seam**
--------------------------------------------------
:meth:`GroundTruthOdomNode.base_pose` is the one function a caller (or PR2) has
to replace to make this node live.  PR2 makes the base a free body and drives
it; the ground-truth base pose then comes from the dfki-ric ``GetBodyState``
service (``/mujoco_get_body_state``, which returns any named body's world pose
-- RULING 1).  Swapping that in is a change to :meth:`base_pose` alone: the
node, its rate, its frame ids and its TF plumbing do not move.  This PR ships
the constant seam, not the live read, because the free joint is PR2's problem.

TF direction is ``odom -> base_link``, never ``odom -> base_footprint``
------------------------------------------------------------------------
This repo **inverted** REP-105's standard tree in D29: ``base_footprint`` is a
*fixed child* of ``base_link`` (a TF link has exactly one parent), and
robot_state_publisher already publishes it that way.  Publishing
``odom -> base_footprint`` here would give ``base_footprint`` two parents and
break the tree at runtime -- D29 says so verbatim.  So this node publishes
``odom -> base_link``; the resulting tree is

    map -> odom -> base_link -> base_footprint
                      \\-> wheels / column / arms / grippers
"""

from geometry_msgs.msg import TransformStamped
import rclpy
from rclpy.node import Node

from robot_skills import Pose
from tf2_ros import TransformBroadcaster

__all__ = ['GroundTruthOdomNode', 'base_pose', 'main']

#: Parent frame of the odometric edge (RULING 2/D29): the odom frame.
ODOM_FRAME = 'odom'
#: Child frame of the odometric edge -- ``base_link``, NOT ``base_footprint``.
BASE_FRAME = 'base_link'
#: Ground-truth base pose: the base is welded at the world origin (RULING 1).
DEFAULT_BASE_POSE = Pose.from_xyz(0.0, 0.0, 0.0)


def base_pose() -> Pose:
    """Return the robot base's pose in the ``odom`` frame (the seam).

    Today: the constant world-origin identity, because the PR8b sim welds the
    base to the world origin (RULING 1).  PR2 replaces this body with a live
    read of the dfki-ric ``GetBodyState`` service once the base is a free body;
    nothing else in this module has to change.
    """
    return DEFAULT_BASE_POSE


class GroundTruthOdomNode(Node):
    """Publish the ground-truth ``odom -> base_link`` transform at a fixed rate.

    Parameters (declared with defaults):

    * ``publish_rate`` (double, default 50.0) -- TF broadcast rate in Hz.
    * ``odom_frame`` (string, default ``odom``) -- parent frame.
    * ``base_frame`` (string, default ``base_link``) -- child frame.
    """

    def __init__(self) -> None:
        """Create the broadcaster and the fixed-rate timer."""
        super().__init__('ground_truth_odom')
        self.declare_parameter('publish_rate', 50.0)
        self.declare_parameter('odom_frame', ODOM_FRAME)
        self.declare_parameter('base_frame', BASE_FRAME)

        rate = self.get_parameter('publish_rate').get_parameter_value().double_value
        if rate <= 0.0:
            raise ValueError(f'publish_rate must be positive, got {rate}')
        self._odom_frame = (
            self.get_parameter('odom_frame').get_parameter_value().string_value
            or ODOM_FRAME)
        self._base_frame = (
            self.get_parameter('base_frame').get_parameter_value().string_value
            or BASE_FRAME)

        self._broadcaster = TransformBroadcaster(self)
        self._timer = self.create_timer(1.0 / rate, self._broadcast)
        self.get_logger().info(
            f'ground-truth odom up: {self._odom_frame} -> {self._base_frame} '
            f'at {rate:.1f} Hz')

    def _broadcast(self) -> None:
        """Broadcast the current ground-truth base pose as TF."""
        pose = base_pose()
        transform = TransformStamped()
        transform.header.stamp = self.get_clock().now().to_msg()
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


def main(args=None) -> None:
    """Run the ground-truth odometry node until interrupted."""
    rclpy.init(args=args)
    node = GroundTruthOdomNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
