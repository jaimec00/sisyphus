# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Launch the world-state query service (PR3 / issue #117).

``robot_world_ros``'s ``world_query_node`` is the ROS-facing shell around the
pure-Python ``robot_world`` store (D35): it answers ``/world_query/get_world``
and mutates one object at a time via ``/world_query/{update_object_pose,
add_object,remove_object}``. This is a third bringup concern -- the world state
service -- with **no** sim or description dependency: a running robot needs a
world whether or not MuJoCo is up, so the world node gets its own launch file
rather than being inlined into the sim bringup.

``mujoco.launch.py`` *includes* this file, so the full sim bringup stands the
world service up "alongside the existing sim/control stack"; the node
definition lives here once.

``world_state_path`` defaults under ``~/.ros/`` -- the runtime-file convention
``mujoco.launch.py``'s derived MJCF already uses -- so a bare ``ros2 launch
robot_bringup mujoco.launch.py`` brings the node up against a live file rather
than a node that crashes at startup on an empty path. The node itself hardcodes
its ``/world_query`` namespace (in its ``__init__``), so this launch declares
**no** ``namespace``; a launch-side one would double-namespace the node. The
file the node opens and the file the brain's read path (``robot_mcp``,
``--world-state`` / ``$ROBOT_WORLD_STATE``) opens are one and the same, which
is D23's single source of truth in the shipped bringup.
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

#: Live world-state file this bringup seeds/updates (never checked in; the
#: repo's ``~/.ros/`` runtime-file convention, cf. ``mujoco.launch.py``).
DEFAULT_WORLD_STATE = os.path.join(
    os.path.expanduser('~'), '.ros', 'sisyphus_world.json')


def generate_launch_description():
    """Build the LaunchDescription for the world query service."""
    return LaunchDescription([
        DeclareLaunchArgument(
            'world_state_path', default_value=DEFAULT_WORLD_STATE,
            description='Live world-state JSON file the query service owns.'),
        DeclareLaunchArgument(
            'world_seed_path', default_value='',
            description=('Optional world seed file; empty means the seed '
                         'shipped inside robot_world.')),
        Node(
            package='robot_world_ros',
            executable='world_query_node',
            name='world_query',
            output='screen',
            parameters=[{
                'world_state_path': LaunchConfiguration('world_state_path'),
                'world_seed_path': LaunchConfiguration('world_seed_path'),
            }],
        ),
    ])
