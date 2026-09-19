# robot_nav

The Nav2 localization layer for the sisyphus robot (PR1 of the Nav2 base-nav
track, issue #121 / D36). Three pieces:

- **`robot_nav/static_map.py`** — a *pure* derivation from a parsed
  `robot_world` document to a `nav_msgs/OccupancyGrid`. No rclpy, no graph; the
  one piece with real rules in it is unit-tested without ROS. `locations`
  become free waypoints, objects become occupied cells with a small footprint,
  bounds are the scene extent plus a margin.
- **`robot_nav/ground_truth_odom.py`** — publishes `odom -> base_link` (D29
  direction) as ground truth. The pose source sits behind the `base_pose()`
  seam so PR2 can swap in a live `GetBodyState` read once the base is free.
- **`robot_nav/map_node.py`** — a `rclpy.lifecycle.LifecycleNode` that queries
  `/world_query/get_world` (retrying), derives the grid, and publishes `/map`
  (transient-local) plus the identity `map -> odom` static transform.

`launch/nav.launch.py` brings these up headless, together with a real
`nav2_map_server` lifecycle node driven by `nav2_lifecycle_manager` (the PR1
proof that the Nav2 lifecycle machinery configures/activates headless).
`robot_bringup`'s `mujoco.launch.py` includes it.

Nothing here is imported by `robot_backends`/`robot_mcp`/`robot_world`: those
stay ROS-free at runtime (D30).
