# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Bring up the full MoveIt 2 *execution* stack (PR2 / issue #147, ruling R6).

Why this file lives in ``robot_moveit``, not ``robot_moveit_config``
-------------------------------------------------------------------
R6 asked for a new composed launch "in robot_moveit_config". That is
impossible as written: ``robot_moveit`` *already* ``exec_depend``s on
``robot_moveit_config`` (its ``planning_scene_bridge.launch.py`` includes
``move_group.launch.py``), and a composed launch must also start this
package's ``planning_scene_bridge`` and ``cartesian_goal`` nodes -- so putting
it in ``robot_moveit_config`` creates a package dependency cycle that colcon
refuses to order (verified: "Unable to order packages topologically:
robot_moveit: ['robot_moveit_config'] / robot_moveit_config:
['robot_moveit']"). The substance of R6 is preserved: PR1's
``move_group.launch.py`` is untouched and controller-free, and this is a NEW
composed launch that starts it with the controller-manager config. It lives
where its dependencies already point. (Recorded in the feature's
implementation.md.)

This is the first launch in which a MoveIt plan can actually move the robot:
PR1's ``move_group.launch.py`` was deliberately controller-free and nameless
("PR3 adds execution" in its own words), and this file composes the three
pieces that turn a plan into motion:

1. the **MuJoCo sim + controllers** -- ``robot_bringup/mujoco.launch.py``, which
   also brings up ``robot_state_publisher``, the world service, Nav2, and (PR2)
   the per-side ``JointTrajectoryController`` pairs;
2. **``move_group``** with the controller-manager configuration
   (``config/moveit_controllers.yaml``), so its
   ``TrajectoryExecutionManager`` has somewhere to send a plan -- through msscm
   to each arm's ``FollowJointTrajectory`` action;
3. the **planning-scene bridge** (world objects -> planning scene) and the
   **``cartesian_goal`` node** (the service the acceptance tests drive).

Why compose rather than extend ``move_group.launch.py``
-------------------------------------------------------
``move_group.launch.py`` stays as PR1 shipped it: one node, no controllers, no
sim, importable and testable in isolation. Execution wiring is additive, so it
lives here -- a launch that owns *no* node definitions of its own and simply
includes / parametrises the ones that already exist. That keeps the single
source of truth for controllers in robot_bringup (R1b) and the single source
of truth for MoveIt config in robot_moveit_config.

Why the launch args are forwarded
---------------------------------
``use_sim_time`` and ``world_state_path`` must reach *both* the sim bringup and
move_group (move_group reads ``/clock`` for its trajectory timing and the
bridge polls the same world file the sim's service serves). ``ros2 launch`` does
not propagate a parent launch argument into an included description implicitly,
so they are declared here and forwarded explicitly.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription,
                            OpaqueFunction)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from moveit_configs_utils import MoveItConfigsBuilder

#: The MoveIt controllers file this launch adds (see its own header for the
#: msscm key shape and the empirically-confirmed action name).
MOVEIT_CONTROLLERS_RELATIVE = os.path.join('config', 'moveit_controllers.yaml')

#: Live world-state file the sim's world service serves. Defaulted to the same
#: ~/.ros path mujoco.launch.py uses: an empty value would be forwarded to
#: world.launch.py, whose node *refuses* an empty path ("the world query service
#: never invents a path"), so forwarding "empty" would kill the world service --
#: and with it the planning-scene bridge and every object_id goal.
DEFAULT_WORLD_STATE = os.path.join(os.path.expanduser('~'), '.ros',
                                   'sisyphus_world.json')


def _moveit_config():
    """Return the MoveIt configs, pinned to the OMPL pipeline (see PR1).

    Same override as ``move_group.launch.py``: the builder would otherwise merge
    every pipeline template on the channel and the Pilz one demands a
    ``pilz_cartesian_limits.yaml`` this package does not ship.
    """
    return (
        MoveItConfigsBuilder('sisyphus', package_name='robot_moveit_config')
        .planning_pipelines(pipelines=['ompl'])
        .to_moveit_configs()
    )


def _launch_setup(context, *args, **kwargs):
    """Build the execution stack now that the launch args are resolved.

    An ``OpaqueFunction`` (rather than a plain list) is needed because the
    controllers file path is a *share* path and the MoveIt param dict must be
    assembled at launch time; evaluating them at description-build time would
    make the launch unimportable in a test that has no installed share tree.
    """
    moveit_config = _moveit_config()
    controllers_file = os.path.join(
        get_package_share_directory('robot_moveit_config'),
        MOVEIT_CONTROLLERS_RELATIVE)

    use_sim_time = LaunchConfiguration('use_sim_time')
    world_state_path = LaunchConfiguration('world_state_path')
    nav = LaunchConfiguration('nav')

    move_group_params = moveit_config.to_dict()
    # move_group's own runtime switches (mirrors move_group.launch.py's dict;
    # kept in sync by hand for the same reason -- this repo reads the builder
    # directly rather than importing upstream's generate_move_group_launch).
    move_group_params.update({
        'publish_robot_description_semantic': True,
        'allow_trajectory_execution': True,
        'capabilities': '',
        'disable_capabilities': '',
        'publish_planning_scene': True,
        'publish_geometry_updates': True,
        'publish_state_updates': True,
        'publish_transforms_updates': True,
        'monitor_dynamics': False,
        # -- R6: the controller-manager config. Without this parameter
        # move_group has no MoveItControllerManager and `allow_trajectory_
        # execution` does nothing.
        'moveit_controller_manager':
            'moveit_simple_controller_manager/MoveItSimpleControllerManager',
    })
    # msscm reads its controller list from the ``moveit_simple_controller_
    # manager`` sub-namespace of its own node's parameters.
    move_group_params.update(_load_controllers_params(controllers_file))

    move_group_node = Node(
        package='moveit_ros_move_group',
        executable='move_group',
        name='move_group',
        output='screen',
        parameters=[
            move_group_params,
            {'use_sim_time': use_sim_time},
        ],
        additional_env={'DISPLAY': os.environ.get('DISPLAY', '')},
    )

    # Static identity world -> base_link. World object poses carry the ``world``
    # frame (robot_world's frame name) and nothing publishes it, so a goal
    # expressed in ``world`` cannot resolve into the planner's ``base_link``
    # without this -- the same statement PR1's planning_scene_bridge.launch.py
    # makes, for the same reason: the robot starts at the world origin (the
    # seed's ``start_location: charger`` is the (0,0,0) location).
    #
    # It is published onto ``base_link`` and NOT onto ``map`` because Nav2 is
    # off by default here (see the `nav` argument): with no ``map -> odom ->
    # base_link`` chain, hanging ``world`` off ``map`` would leave ``world`` and
    # the arm in two unconnected trees and TF would refuse the lookup. A caller
    # that turns Nav2 on also gets the live ``map -> odom -> base_link`` chain
    # and should move this transform to `map` -- until the base actually moves,
    # `base_link` and `map` coincide (ground-truth odom), so the two are the
    # same statement today.
    world_to_base_link = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='world_to_base_link',
        output='log',
        arguments=['--frame-id', 'world', '--child-frame-id', 'base_link'],
    )

    # The world -> planning-scene bridge (PR1). It polls the world service and
    # mirrors objects in, so a goal aimed at a world object is checked against
    # the same geometry the rest of the stack sees.
    planning_scene_bridge = Node(
        package='robot_moveit',
        executable='planning_scene_bridge',
        name='planning_scene_bridge',
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}],
    )

    # The Cartesian-goal service this PR adds: the seam the brain/tests drive.
    # It needs the *whole* MoveIt config on its own parameter server, because
    # moveit_py's MoveItPy spins a fresh node and cannot inherit move_group's
    # params -- a MoveItPy without a robot_description/robot_description_
    # semantic cannot build a model, and without the controller-manager config
    # cannot execute. So it is passed the same dict move_group gets, plus
    # use_sim_time (it reads /clock for goal timestamps and TF).
    cartesian_goal = Node(
        package='robot_moveit',
        executable='cartesian_goal',
        name='cartesian_goal',
        output='screen',
        parameters=[
            moveit_config.to_dict(),
            {'moveit_controller_manager':
                'moveit_simple_controller_manager/MoveItSimpleControllerManager'},
            _load_controllers_params(controllers_file),
            {'use_sim_time': use_sim_time},
        ],
    )

    sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution(
            [FindPackageShare('robot_bringup'), 'launch',
             'mujoco.launch.py'])),
        launch_arguments={
            'use_sim_time': use_sim_time,
            'world_state_path': world_state_path,
            # Nav2 is deliberately OFF by default here. This stack moves the
            # *arm*, not the base: no acceptance criterion needs Nav2, and
            # bringing it up costs another ~15 lifecycle nodes on a
            # resource-tight host -- where a Nav2 map_node lifecycle timeout
            # then kills the whole bringup and, with it, the TF chain the goal
            # node resolves through. A caller that does want the base live can
            # pass nav:=true.
            'nav': nav,
        }.items())

    return [
        sim,
        world_to_base_link,
        move_group_node,
        planning_scene_bridge,
        cartesian_goal,
    ]


def _load_controllers_params(controllers_file):
    """Return the ``moveit_simple_controller_manager`` param namespace.

    Reads the shipped YAML and re-keys it under move_group's
    ``moveit_simple_controller_manager`` parameter namespace, which is where
    msscm looks for ``controller_names`` and each controller's
    ``type``/``action_ns``/``joints``/``default``. Loading the file with ``yaml``
    and passing the dict (rather than passing the path) keeps the shape explicit
    and lets the test assert the same file moves the same keys.
    """
    import yaml
    with open(controllers_file) as handle:
        document = yaml.safe_load(handle)
    assert document['moveit_controller_manager'] == (
        'moveit_simple_controller_manager/MoveItSimpleControllerManager'), (
        'moveit_controllers.yaml must select the simple controller manager')
    return {'moveit_simple_controller_manager':
            document['moveit_simple_controller_manager']}


def generate_launch_description():
    """Build the LaunchDescription for the full execution stack."""
    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='true',
            description='Run every node against the sim /clock.'),
        DeclareLaunchArgument(
            'nav', default_value='false',
            description=('Bring up the Nav2 localization layer as well. Off by '
                         'default: this stack drives the arm, and the base does '
                         'not move in this PR.')),
        DeclareLaunchArgument(
            'world_state_path', default_value=DEFAULT_WORLD_STATE,
            description=('Live world-state file the sim world service serves. '
                         'Must be a real file: the world node refuses an empty '
                         'path rather than inventing one.')),
        OpaqueFunction(function=_launch_setup),
    ])
