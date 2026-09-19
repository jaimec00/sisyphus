# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The Nav2 localization layer (D36).

Three pieces, in order of how pure they are:

* :mod:`robot_nav.static_map` -- a *pure* derivation from a parsed world
  document to a ``nav_msgs/OccupancyGrid``.  No rclpy, no graph: it is the one
  piece unit-tested without ROS.
* :mod:`robot_nav.ground_truth_odom` -- the ``odom -> base_link`` publisher.
* :mod:`robot_nav.map_node` -- the lifecycle node that queries the world
  service, builds the grid, and publishes ``/map`` plus ``map -> odom``.

This package may import ``robot_world`` (a pure-Python library, exactly as
``robot_world_ros`` does).  It must never be imported *by* ``robot_backends``,
``robot_mcp`` or ``robot_world``: those stay ROS-free at runtime (D30).
"""
