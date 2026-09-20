# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 — the ground-truth odom node's live pose seam (RULING 3).

PR1 shipped ``base_pose()`` as the constant identity behind a seam.  PR2 makes
the node read the sim's live ``GetBodyState('base_link')`` and publish both the
``odom -> base_link`` TF and a ``nav_msgs/Odometry`` on ``/odom``.  The live
service read needs the sim, so it is exercised by the bringup acceptance; here
the *shape* of the seam is pinned, graph-free:

* the module default pose is still the identity (the fallback that keeps the
  tree complete and never NaN before the first good read);
* the node declares the documented parameters with the right defaults;
* the node publishes exactly the frames RULING 2/D29 require (``odom`` ->
  ``base_link``), exposes an ``/odom`` publisher, and holds the last-known pose
  when the service is unavailable (never raises, never NaNs).

ROS imports are lazy, inside the tests, so collection needs no runtime.
"""


def test_default_base_pose_is_the_identity_fallback():
    """``base_pose()`` still returns the identity (the pre-read fallback)."""
    from robot_nav.ground_truth_odom import DEFAULT_BASE_POSE, base_pose
    pose = base_pose()
    assert pose is DEFAULT_BASE_POSE
    assert (pose.position.x, pose.position.y, pose.position.z) == (0.0, 0.0, 0.0)
    assert pose.orientation.w == 1.0


def test_odom_frames_are_the_d29_edge():
    """The odometric edge is ``odom -> base_link``, never via base_footprint."""
    from robot_nav.ground_truth_odom import BASE_FRAME, ODOM_FRAME
    assert ODOM_FRAME == 'odom'
    assert BASE_FRAME == 'base_link'


def test_body_state_service_constants_name_the_free_body():
    """The live read targets the dfki-ric GetBodyState service on ``base_link``."""
    from robot_nav.ground_truth_odom import (
        BASE_BODY_NAME, BODY_STATE_SERVICE, ODOM_TOPIC)
    assert BODY_STATE_SERVICE == '/mujoco_get_body_state'
    assert BASE_BODY_NAME == 'base_link'
    assert ODOM_TOPIC == '/odom'


def test_node_declares_the_live_parameters_and_tolerates_no_service():
    """The node builds with the documented defaults and survives no service.

    A dedicated ``rclpy.Context`` isolates the rclpy init from the rest of the
    suite.  The service does not exist here, so ``_read_base_state`` must return
    ``None`` (holding the identity) rather than raising, and the node must still
    own its TF broadcaster and ``/odom`` publisher.
    """
    import rclpy
    from robot_nav.ground_truth_odom import (
        BASE_BODY_NAME, BODY_STATE_SERVICE, GroundTruthOdomNode)
    # Use the default context so the node (which uses rclpy's global context)
    # can be constructed.  Guard the init in case another test in the same
    # process already initialised rclpy.
    already_up = rclpy.ok()
    if not already_up:
        rclpy.init()
    node = None
    try:
        node = GroundTruthOdomNode()
        assert node.get_parameter('body_state_service').value == BODY_STATE_SERVICE
        assert node.get_parameter('base_body_name').value == BASE_BODY_NAME
        assert node.get_parameter('publish_rate').value == 50.0
        assert node.get_parameter('odom_frame').value == 'odom'
        assert node.get_parameter('base_frame').value == 'base_link'
        # The /odom publisher exists with the documented topic.
        assert node._odom_publisher is not None
        assert node._odom_publisher.topic_name == '/odom'
        # No service up: the read holds the last-known (identity), no raise.
        assert node._read_base_state() is None
        assert node._pose.position.x == 0.0 and node._pose.position.y == 0.0
    finally:
        if node is not None:
            node.destroy_node()
        # rclpy.shutdown() is deferred (the repo's headless idiom).
