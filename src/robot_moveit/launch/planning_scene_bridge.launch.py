# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Bring up the world -> planning-scene mirror (PR1 / R6).

Composes the three nodes the bridge needs and nothing else:

* ``robot_bringup``'s ``world.launch.py`` -- the ``/world_query/get_world``
  service (included, not re-declared: the world node definition has one home);
* the headless ``move_group`` from ``robot_moveit_config`` (included);
* this package's ``planning_scene_bridge`` node, pointed at both services.

This is the LaunchDescription R10 item 4 exercises end to end, and the thing a
later PR (PR2/PR3) will fold into the full bringup once planning and execution
join. It is headless: no RViz, no controllers, no sim.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

#: Live world-state file this bringup seeds/updates (the repo's ``~/.ros/``
#: runtime-file convention, matching robot_bringup's world.launch.py).
DEFAULT_WORLD_STATE = os.path.join(
    os.path.expanduser('~'), '.ros', 'sisyphus_world.json')


def generate_launch_description():
    """Build the LaunchDescription for the world + move_group + bridge stack."""
    world_launch = os.path.join(
        get_package_share_directory('robot_bringup'), 'launch',
        'world.launch.py')
    move_group_launch = os.path.join(
        get_package_share_directory('robot_moveit_config'), 'launch',
        'move_group.launch.py')

    return LaunchDescription([
        DeclareLaunchArgument(
            'world_state_path', default_value=DEFAULT_WORLD_STATE,
            description=('Live world-state JSON file the world service owns. '
                         'Defaults to the same ~/.ros path the included '
                         'world.launch.py uses, so a bare launch brings the '
                         'service up against a live file rather than an empty '
                         'path the node refuses.')),
        DeclareLaunchArgument(
            'poll_period_s', default_value='2.0',
            description='How often the bridge re-reads the world.'),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(world_launch),
            launch_arguments={
                'world_state_path': LaunchConfiguration('world_state_path'),
            }.items()),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(move_group_launch)),
        # Static identity TF world -> base_link. The world objects carry the
        # ``world`` frame, and move_group will not accept an object in a frame
        # it cannot resolve -- so ``world`` must exist in the TF tree. At the
        # shipped start pose the robot sits at the world origin (the seed's
        # ``start_location: charger`` is the (0,0,0) location), so the identity
        # is exactly right for this foundation PR. A later PR replaces this with
        # the nav stack's dynamic ``map`` -> ``base_link`` once the base moves;
        # until then this is the honest "robot at origin" statement, not a fudge.
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='world_to_base_link',
            output='log',
            arguments=['--frame-id', 'world', '--child-frame-id', 'base_link'],
        ),
        Node(
            package='robot_moveit',
            executable='planning_scene_bridge',
            name='planning_scene_bridge',
            output='screen',
            parameters=[{
                'poll_period_s': LaunchConfiguration('poll_period_s'),
            }],
        ),
    ])
