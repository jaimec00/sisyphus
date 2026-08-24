# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Launch the robot in MuJoCo under dfki-ric's ``mujoco_ros2_control`` (PR8b / issue #92).

This is the first launch that makes the robot *move* in sim through the
standard ROS 2 control stack — the seam roadmap #4's MuJoCo ``RobotBackend``
will drive.

dfki-ric's ``mujoco_ros2_control`` (a DISTINCT implementation from the
ros-controls org) embeds its own ``controller_manager`` and hardware resource
manager inside a single ``mujoco_ros2_control`` executable. It loads the MJCF
from the ``robot_model_path`` param synchronously in configure (sidestepping
the ros-controls ``std::bad_alloc``) and parses the ``robot_description`` URDF
string for the ros2_control joint/interface block plus native <mimic> tags.

This launch differs from the ros-controls-era bringup in that we do NOT run a
separate ``ros2_control_node`` nor ``xacro2mjcf.py``: we point
``robot_model_path`` straight at our hand-authored derived MJCF (materialized
at launch by ``robot_description.mjcf_model.write_mjcf_model`` — PR7/issue #89
never checks in the generated MJCF) and skip auto-conversion.

Joints become commandable through the two group controllers: publish
``Float64MultiArray`` to ``/arm_gripper_position_controller/commands`` (13
values: column_lift, 10 arm revolute, left/right_gripper) and to
``/base_velocity_controller/commands`` (3 wheel speeds). A joint command moves
the sim state (a velocity command spins a wheel joint, a position command
moves an arm/column/gripper joint).

This launch follows dfki-ric's own franka example
(mujoco_ros2_control_examples/launch/franka.launch.py) for the control-stack
plumbing: the embedded controller_manager registers at ``/controller_manager``
(its node name with an empty namespace — see init_controller_manager in
mujoco_ros2_control_plugin.cpp), so the spawners target ``/controller_manager``;
and the whole ``controllers.yaml`` is passed as a simulator NODE parameter so
the controller `type` declarations + CM ``update_rate`` land on the embedded
CM (R-PR8b-11/14).
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, LogInfo,
                            RegisterEventHandler, Shutdown)
from launch.event_handlers import OnProcessStart
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


def _spawner(name, params_file=None):
    """Spawn a controller on dfki-ric's embedded controller_manager.

    dfki-ric embeds its controller_manager at ``/controller_manager`` (empty
    namespace + node name "controller_manager"); the spawner must name that
    full path explicitly (R-PR8b-11).
    """
    arguments = [name, '--controller-manager', '/controller_manager']
    if params_file is not None:
        arguments += ['--param-file', params_file]
    return Node(
        package='controller_manager',
        executable='spawner',
        name=f'spawner_{name}',
        arguments=arguments,
        output='screen',
    )


def generate_launch_description():
    """Build the LaunchDescription for the MuJoCo sim bringup (dfki-ric)."""
    use_sim_time = LaunchConfiguration('use_sim_time')
    mjcf_path = LaunchConfiguration('mjcf_path')
    controllers_file = PathJoinSubstitution(
        [FindPackageShare('robot_bringup'),
         'params', 'controllers.yaml'])

    # Materialize the derived MJCF now, at description build time (guaranteed
    # to run before any node starts), so the file exists when the sim plugin
    # loads its ``robot_model_path`` param. Default path; an explicit
    # ``mjcf_path`` argument still takes precedence at substitution time below.
    _materialize_mjcf(DEFAULT_MJCF)
    robot_description = ParameterValue(
        Command(['xacro ', _robot_description_xacro()]),
        value_type=str)

    simulator = Node(
        package='mujoco_ros2_control',
        executable='mujoco_ros2_control',
        # No explicit name= : the plugin node keeps its code-default name
        # "mujoco_ros2_control" and the embedded controller_manager keeps
        # "controller_manager" (dfki-ric franka-example pattern). A forced
        # name here remaps BOTH nodes to "mujoco_ros2_control", starving the
        # CM of its controllers.yaml params (controller load fails: "Could
        # not set controller param type").
        output='screen',
        emulate_tty=True,
        parameters=[
            {'robot_description': robot_description},
            {'use_sim_time': use_sim_time},
            # The whole controllers.yaml is a node parameter so the embedded
            # controller_manager picks up the controller type declarations and
            # update_rate (franka-example pattern; R-PR8b-14).
            controllers_file,
            {
                'simulation_frequency': 500.0,
                'real_time_factor': 1.0,
                'show_gui': False,
                'clock_publisher_frequency': 500.0,
                'synchronous_mode': False,
            },
            {'robot_model_path': mjcf_path},
        ],
        # dfki-ric's embedded controller_manager reads the robot description
        # from the /controller_manager/robot_description topic; forward it to
        # robot_state_publisher's output (franka-example remap).
        remappings=[('/controller_manager/robot_description', '/robot_description')],
        on_exit=Shutdown(),
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='true',
            description='Run the sim and controllers against /clock.'),
        DeclareLaunchArgument(
            'mjcf_path', default_value=DEFAULT_MJCF,
            description='Runtime path for the derived MJCF the sim loads.'),
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
        simulator,
        # Controllers only come up once the sim node is running (dfki-ric
        # embeds the controller_manager; it must be up for the spawners).
        RegisterEventHandler(OnProcessStart(
            target_action=simulator,
            on_start=[
                LogInfo(msg='MuJoCo sim up; spawning controllers'),
                _spawner('joint_state_broadcaster', controllers_file),
                _spawner('arm_gripper_position_controller', controllers_file),
                _spawner('base_velocity_controller', controllers_file),
            ],
        )),
    ])
