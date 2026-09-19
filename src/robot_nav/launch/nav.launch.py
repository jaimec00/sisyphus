# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Launch the Nav2 localization layer headless (PR1 / issue #121).

Three concerns come up here, and nothing else -- the planner, controller,
costmaps, BT navigator and AMCL are PR2+ (RULING 5):

* ``ground_truth_odom`` -- publishes ``odom -> base_link`` (RULING 1/2, D29);
* ``map_node`` -- the lifecycle node that queries ``/world_query/get_world``,
  derives the grid, and publishes ``/map`` + the identity ``map -> odom``
  (RULING 3);
* a **real Nav2 lifecycle node**, ``nav2_map_server``, brought ACTIVE by
  ``nav2_lifecycle_manager`` -- the PR1 proof that the Nav2 lifecycle
  machinery configures and activates headless (RULING 5).

``map_node`` is a lifecycle node but is *not* driven by the manager: it carries
its own transitions here (``configure`` then ``activate``) via ``EmitEvent``
handlers, so its startup is explicit and observable.  ``nav2_map_server`` is
managed the Nav2 way, by a ``nav2_lifecycle_manager`` naming it, because that
is the machinery PR1 exists to prove.

The ``nav2_map_server`` needs a PGM on disk, and our operational map is
*derived* (RULING 3).  So the launch renders the derived grid to a throwaway
PGM+YAML under ``~/.ros`` from the *resolved* ``world_state_path`` (deferred
through an ``OpaqueFunction`` so the render sees the argument the bringup and
the tests pass, not the default) and points ``map_server`` at it.  That PGM is
a *rendering* of the world, never a second home for map data -- the source of
truth stays ``robot_world``, and the operational map stays the ``/map`` topic
from ``map_node``.

``mujoco.launch.py`` includes this file, exactly as it already includes
``world.launch.py``, so the full sim bringup stands localization up alongside
the sim/control stack.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, OpaqueFunction,
                            RegisterEventHandler)
from launch.event_handlers import OnProcessStart
from launch.events import matches_action
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import LifecycleNode as RosLifecycleNode
from launch_ros.actions import Node
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState
from lifecycle_msgs.msg import Transition

#: Runtime directory for artifacts this launch writes (the repo's convention,
#: cf. ``mujoco.launch.py``'s derived MJCF and ``world.launch.py``'s live file).
_RUNTIME_DIR = os.path.join(os.path.expanduser('~'), '.ros')
#: The throwaway PGM the demonstration ``nav2_map_server`` loads.
_DEMO_MAP_BASE = os.path.join(_RUNTIME_DIR, 'sisyphus_nav2_demo_map')
#: The derived world document the PGM is rendered from.
DEFAULT_WORLD_STATE = os.path.join(_RUNTIME_DIR, 'sisyphus_world.json')
#: The ``nav2_map_server`` node name (a Nav2 node keeps its own name).
MAP_SERVER_NAME = 'map_server'


def _materialize_demo_pgm(world_state_path: str) -> str:
    """Render the world's derived grid to a PGM+YAML; return the YAML path.

    Run at launch-description build time so the files exist before the
    ``map_server`` node configures.  The world is read with the shipped
    ``robot_world`` seed as a fallback when the live file is not there yet (a
    bare ``ros2 launch robot_nav nav.launch.py`` should still bring the demo
    map up), and the grid comes from ``static_map`` -- the one derivation --
    so the PGM and the ``/map`` topic agree.
    """
    from robot_nav.pgm_export import export_grid
    from robot_nav.static_map import occupancy_grid_from_document
    from robot_world import default_seed_document, read_document

    document = None
    if os.path.isfile(world_state_path):
        try:
            document = read_document(world_state_path)
        except (ValueError, TypeError):
            document = None
    if document is None:
        document = default_seed_document()
    grid = occupancy_grid_from_document(document)
    os.makedirs(_RUNTIME_DIR, exist_ok=True)
    return export_grid(grid, _DEMO_MAP_BASE)


def _nav_params_path() -> str:
    """Return the installed ``params/nav.yaml`` path.

    This is the single home for the two nodes' tunable parameters; the inline
    parameter dicts below carry only the launch-specific overrides the YAML
    cannot (``world_service``, ``use_sim_time``).
    """
    return os.path.join(
        get_package_share_directory('robot_nav'), 'params', 'nav.yaml')


def _demo_map_server(context, *args, **kwargs):
    """Render the demo PGM from the resolved world state and return map_server.

    The demonstration ``nav2_map_server`` loads its grid from a PGM on disk,
    and that PGM must be a rendering of the *same* world the operational
    ``/map`` derives from, so the two agree.  The render therefore waits for
    the ``world_state_path`` launch configuration to resolve (the bringup and
    the acceptance test both point it at the live world file), which is why
    this is an OpaqueFunction rather than a node built at description time.
    """
    resolved = context.perform_substitution(
        LaunchConfiguration('world_state_path'))
    demo_yaml = _materialize_demo_pgm(resolved)
    return [RosLifecycleNode(
        package='nav2_map_server',
        executable='map_server',
        name=MAP_SERVER_NAME,
        namespace='',
        output='screen',
        parameters=[{
            'yaml_filename': demo_yaml,
            'topic_name': 'nav2_map_server_derived_map',
            'frame_id': 'map',
            'use_sim_time': LaunchConfiguration('use_sim_time'),
        }],
    )]


def _emit_transition(node, transition_id):
    """Return an action that asks ``node`` to perform a lifecycle transition."""
    return EmitEvent(event=ChangeState(
        lifecycle_node_matcher=matches_action(node),
        transition_id=transition_id,
    ))


def generate_launch_description():
    """Build the LaunchDescription for the Nav2 localization layer."""
    use_sim_time = LaunchConfiguration('use_sim_time')
    world_service = LaunchConfiguration('world_service')
    nav_params = _nav_params_path()

    odom_node = Node(
        package='robot_nav',
        executable='ground_truth_odom',
        name='ground_truth_odom',
        output='screen',
        parameters=[nav_params, {'use_sim_time': use_sim_time}],
    )

    map_node = RosLifecycleNode(
        package='robot_nav',
        executable='map_node',
        name='map_node',
        namespace='',
        output='screen',
        parameters=[nav_params, {
            'world_service': world_service,
            'use_sim_time': use_sim_time,
        }],
    )

    # The real Nav2 lifecycle node: a stock ``nav2_map_server`` serving the
    # rendered map, brought up the Nav2 way by a lifecycle manager.  It is
    # built inside an OpaqueFunction so its ``yaml_filename`` is the PGM pair
    # rendered from the *resolved* ``world_state_path`` (see
    # ``_demo_map_server``), matching the operational ``/map``.
    nav2_map_server = OpaqueFunction(function=_demo_map_server)
    nav2_manager = Node(
        package='nav2_lifecycle_manager',
        executable='lifecycle_manager',
        name='lifecycle_manager_localization',
        output='screen',
        parameters=[{
            'autostart': True,
            'node_names': [MAP_SERVER_NAME],
            'use_sim_time': use_sim_time,
        }],
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            'use_sim_time', default_value='false',
            description='Run against /clock (the sim bringup sets true).'),
        DeclareLaunchArgument(
            'world_service', default_value='/world_query/get_world',
            description='World query service the map is derived from.'),
        DeclareLaunchArgument(
            'world_state_path', default_value=DEFAULT_WORLD_STATE,
            description='Live world-state file the demo PGM is rendered from.'),
        odom_node,
        map_node,
        nav2_map_server,
        nav2_manager,
        # map_node is a lifecycle node; drive it configure -> activate as soon
        # as it is up, so a bare launch leaves localization ACTIVE.  The
        # configure is emitted on process start; the activate is emitted when
        # the node reports it has finished configuring (its state transition
        # to INACTIVE, the goal state of ``configure``).
        RegisterEventHandler(OnProcessStart(
            target_action=map_node,
            on_start=[
                _emit_transition(map_node, Transition.TRANSITION_CONFIGURE),
            ],
        )),
        RegisterEventHandler(OnStateTransition(
            target_lifecycle_node=map_node,
            goal_state='inactive',
            entities=[
                _emit_transition(map_node, Transition.TRANSITION_ACTIVATE),
            ],
        )),
    ])
