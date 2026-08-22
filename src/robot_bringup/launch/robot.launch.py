# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Launch the assembled robot's TF tree against the installed description.

This is the first launch layer in the repo: start robot_state_publisher (and
its standard companion joint_state_publisher) against the *installed*
``robot_description``, resolved through the ament index
(``get_package_share_directory``) with no source-tree fallback (D27/D24), and
expanded at launch time with the xacro CLI.

``robot_description`` is injected as a launch-time Command substitution wrapped
in a typed ParameterValue so the multi-line xacro output is not re-parsed as
YAML; the params file carries only the node's own tuning knobs
(``publish_frequency``, ``use_tf_static``). Joint-state publisher is what makes
the FULL tree observable: without a ``/joint_states`` source RSP publishes only
the static tree and leaves the movable-joint frames (wheels, ``column_lift``,
arm joints) unpublished. JSP with an empty ``source_list`` reads the URDF's
initial joint values, so the whole tree is published at the home config.
(Controllers / ``mujoco_ros2_control`` are PR8b, explicitly out of scope here.)
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import (Command, LaunchConfiguration,
                                  PathJoinSubstitution)
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def _robot_description_xacro():
    """Return the installed robot_description xacro path."""
    return os.path.join(
        get_package_share_directory('robot_description'),
        'urdf', 'robot.urdf.xacro')


def generate_launch_description():
    """Build the LaunchDescription the bringup runs."""
    robot_description = ParameterValue(
        Command(['xacro ', _robot_description_xacro()]), value_type=str)
    use_sim_time = LaunchConfiguration('use_sim_time')
    rsp_params = PathJoinSubstitution(
        [FindPackageShare('robot_bringup'),
         'params', 'robot_state_publisher.yaml'])

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='false',
            description='Run (and, for RSP/JSP, read) against /clock when true.'),
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[
                rsp_params,
                {'robot_description': robot_description},
                {'use_sim_time': use_sim_time},
            ],
        ),
        Node(
            package='joint_state_publisher',
            executable='joint_state_publisher',
            name='joint_state_publisher',
            output='screen',
            parameters=[
                {'robot_description': robot_description},
                {'use_sim_time': use_sim_time},
            ],
        ),
    ])
