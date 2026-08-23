# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Launch the robot in MuJoCo under ``mujoco_ros2_control`` (PR8b / issue #92).

This is the first launch that makes the robot *move* in sim through the
standard ROS 2 control stack — the seam roadmap #4's MuJoCo ``RobotBackend``
will drive. It:

1. Materializes the derived MJCF (URDF import + overlay splice, via
   ``robot_description.mjcf_model.write_mjcf_model``) to a runtime file. The
   derived MJCF is never checked in (PR7/issue #89), so the sim plugin needs a
   concrete file to load; this writes one deterministically under
   ``~/.ros/sisyphus_derived_scene.xml`` (override with the ``mjcf_path``
   launch arg).
2. Expands the *installed* ``robot.urdf.xacro`` with ``mujoco_model_path``
   pointing at that file, so the ``<ros2_control>`` block's ``mujoco_model``
   param resolves (D27/D24: ament-index installed copy, no source-tree
   fallback).
3. Starts the MuJoCo simulator system interface node
   (``mujoco_ros2_control``'s ``ros2_control_node``) with the controller
   params, plus ``robot_state_publisher`` for TF.
4. Spawns the controllers (joint_state_broadcaster + arm/gripper position +
   base velocity) via ``controller_manager``'s spawner.

Joints become commandable through the two group controllers: publish
``Float64MultiArray`` to ``/arm_gripper_position_controller/commands`` (13
values: column_lift, 10 arm revolute, left/right_gripper) and to
``/base_velocity_controller/commands`` (3 wheel speeds). A joint command moves
the sim state (a velocity command spins a wheel joint, a position command
moves an arm/column/gripper joint).
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, Shutdown
from launch.substitutions import (Command, LaunchConfiguration,
                                  PathJoinSubstitution)
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

#: Runtime file the sim plugin loads (never checked in; derived at launch).
DEFAULT_MJCF = os.path.join(os.path.expanduser('~'), '.ros',
                            'sisyphus_derived_scene.xml')


def _robot_description_xacro():
    """Return the installed robot_description xacro path."""
    return os.path.join(
        get_package_share_directory('robot_description'),
        'urdf', 'robot.urdf.xacro')


def _materialize_mjcf(mjcf_path):
    """Write the derived MJCF to ``mjcf_path`` using the installed loader."""
    from robot_description.mjcf_model import write_mjcf_model
    return write_mjcf_model(mjcf_path)


def generate_launch_description():
    """Build the LaunchDescription for the MuJoCo sim bringup."""
    use_sim_time = LaunchConfiguration('use_sim_time')
    mjcf_path = LaunchConfiguration('mjcf_path')
    controllers_file = PathJoinSubstitution(
        [FindPackageShare('robot_bringup'),
         'params', 'controllers.yaml'])

    # Materialize the derived MJCF now, at description build time (guaranteed
    # to run before any node starts), so the file exists when the sim plugin
    # loads its ``mujoco_model`` param. Default path; an explicit ``mjcf_path``
    # argument still takes precedence at substitution time below.
    _materialize_mjcf(DEFAULT_MJCF)
    robot_description = ParameterValue(
        Command([
            'xacro ', _robot_description_xacro(),
            ' mujoco_model_path:=', mjcf_path,
        ]), value_type=str)

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='true',
            description='Run the sim and controllers against /clock.'),
        DeclareLaunchArgument(
            'mjcf_path', default_value=DEFAULT_MJCF,
            description='Runtime path for the derived MJCF the sim loads.'),
        # Eagerly materialize the derived MJCF (side effect before launching).
        # A zero-arg OpaqueFunction-free approach: write it right here.
        # Done via a Python expression at description-build time below.
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[
                {'robot_description': robot_description},
                {'use_sim_time': use_sim_time},
            ],
        ),
        Node(
            package='mujoco_ros2_control',
            executable='ros2_control_node',
            name='mujoco_ros2_control_node',
            output='both',
            emulate_tty=True,
            parameters=[
                {'use_sim_time': use_sim_time},
                controllers_file,
            ],
            on_exit=Shutdown(),
        ),
        Node(
            package='controller_manager',
            executable='spawner',
            name='spawner_joint_state_broadcaster',
            arguments=['joint_state_broadcaster', '--param-file',
                       controllers_file],
            output='both',
        ),
        Node(
            package='controller_manager',
            executable='spawner',
            name='spawner_arm_gripper_position_controller',
            arguments=['arm_gripper_position_controller',
                       '--param-file', controllers_file],
            output='both',
        ),
        Node(
            package='controller_manager',
            executable='spawner',
            name='spawner_base_velocity_controller',
            arguments=['base_velocity_controller',
                       '--param-file', controllers_file],
            output='both',
        ),
    ])
