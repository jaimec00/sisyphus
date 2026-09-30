# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Bring up ``move_group`` headless (PR1 / issue #145, R8).

This is the foundation launch: it stands the MoveIt 2 planning node up against
the shipped URDF/SRDF, with **no** motion, **no** controllers and **no** RViz.
The point is the acceptance criterion "move_group starts headless; the arm
planning group loads from the SRDF" -- a downstream PR (PR2) adds planning;
PR3 adds execution.

What it starts
--------------
Exactly one node: ``moveit_ros_move_group/move_group``. It is configured from
``MoveItConfigsBuilder("sisyphus", package_name="robot_moveit_config")``, which
loads -- via the ``.setup_assistant`` pointers -- the URDF from
``robot_description`` (expanded from ``urdf/robot.urdf.xacro``), the SRDF from
``config/sisyphus.srdf``, plus ``kinematics.yaml`` (KDL, R7),
``joint_limits.yaml`` and the OMPL planning pipeline from ``ompl_planning.yaml``.

Why no robot_state_publisher here
---------------------------------
``move_group`` loads the robot model from the URDF *parameter* (the builder
expands it) -- it does not need TF for model loading, and the acceptance
criterion is about the *model and groups*, not about a live state stream. A
later PR that adds planning/execution composes this launch with the
description/MuJoCo bringup, which is already where ``robot_state_publisher``
and ``/joint_states`` come from. Keeping the two apart is what makes this
launch testable with nothing but ``move_group`` installed.

Params are the builder's ``to_dict()`` plus the handful of runtime switches the
upstream ``generate_move_group_launch`` sets. Trajectory execution is
deliberately left ON (upstream default) even though nothing here moves: the
switch only decides whether move_group *would* hand a trajectory to a
controller manager, and turning it off would hide a later PR's wiring mistake.
Those are all exposed as launch arguments, so a caller can override them.
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from moveit_configs_utils import MoveItConfigsBuilder


def generate_launch_description():
    """Build the LaunchDescription for a headless move_group."""
    # Pipelines are pinned to OMPL: the builder's default is to merge EVERY
    # pipeline template it finds on the channel (OMPL, CHOMP, STOMP, Pilz), and
    # the Pilz template then demands a ``pilz_cartesian_limits.yaml`` this
    # package does not ship -- which fails the launch outright. This PR needs
    # one working pipeline, and ``ompl_planning.yaml`` is it (R7/R8).
    moveit_config = (
        MoveItConfigsBuilder('sisyphus', package_name='robot_moveit_config')
        .planning_pipelines(pipelines=['ompl'])
        .to_moveit_configs()
    )

    declare_allow_trajectory_execution = DeclareLaunchArgument(
        'allow_trajectory_execution', default_value='true',
        description=('Whether move_group may hand trajectories to a controller '
                     'manager. Unused this PR (nothing plans); left on so PR2/PR3 '
                     'inherit the upstream default rather than a masked-off one.'))
    declare_publish_monitored_planning_scene = DeclareLaunchArgument(
        'publish_monitored_planning_scene', default_value='true',
        description=('Publish the monitored planning scene. ON: the '
                     'planning_scene_bridge and any external monitor read the '
                     'scene through this topic/service.'))

    should_publish = LaunchConfiguration('publish_monitored_planning_scene')

    # The runtime switches upstream's generate_move_group_launch sets, kept in
    # sync with it by hand (this repo reads the builder directly rather than
    # importing the helper, so the param dict is inspectable in one place).
    move_group_configuration = {
        'publish_robot_description_semantic': True,
        'allow_trajectory_execution': LaunchConfiguration(
            'allow_trajectory_execution'),
        # Wrapped so the value may be the empty string (upstream's own note):
        # move_group accepts an empty capability list, just not a non-string.
        'capabilities': ParameterValue('', value_type=str),
        'disable_capabilities': ParameterValue('', value_type=str),
        'publish_planning_scene': should_publish,
        'publish_geometry_updates': should_publish,
        'publish_state_updates': should_publish,
        'publish_transforms_updates': should_publish,
        # Dynamics on /joint_states are not used by anything move_group does
        # (upstream defaults this false for the same reason).
        'monitor_dynamics': False,
    }

    move_group_node = Node(
        package='moveit_ros_move_group',
        executable='move_group',
        name='move_group',
        output='screen',
        parameters=[
            moveit_config.to_dict(),
            move_group_configuration,
        ],
        # move_group touches OpenGL only if a plugin asks for it; an empty
        # DISPLAY is enough to keep it headless (upstream sets the same).
        additional_env={'DISPLAY': os.environ.get('DISPLAY', '')},
    )

    return LaunchDescription([
        declare_allow_trajectory_execution,
        declare_publish_monitored_planning_scene,
        move_group_node,
    ])
