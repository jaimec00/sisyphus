# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""``nav.launch.py`` declares the localization nodes and a real Nav2 node.

This is the graph-free half of PR1's launch claim (issue #121 / RULING 5): the
installed launch file loads and declares ``robot_nav``'s ``map_node`` and
``ground_truth_odom`` plus a real Nav2 ``map_server`` lifecycle node.

The heavyweight acceptance -- launching the full bringup headless and asserting
the connected TF tree, the live ``/map`` grid and both lifecycle nodes ACTIVE --
lives in ``robot_bringup``'s ``test_nav_localization.py``.  It composes the
bringup (``robot_bringup``'s ``robot.launch.py`` + ``world.launch.py`` + this
package's ``nav.launch.py``), so it belongs to ``robot_bringup``: that package
already ``exec_depend``s ``robot_nav``, whereas the reverse edge (a
``test_depend`` here on ``robot_bringup``) would be a colcon build-order cycle.
"""
import os
import shutil


def _require_tool(name):
    """Return the path to an executable on PATH, failing loudly if absent."""
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; it is pinned in pixi.toml and declared in '
        'package.xml -- run inside `pixi run`.' % name)
    return path


def _install_share(pkg):
    from ament_index_python.packages import get_package_share_directory
    return get_package_share_directory(pkg)


def _launch_path(pkg, name):
    """Return the path to an installed launch file."""
    return os.path.join(_install_share(pkg), 'launch', name)


def test_nav_launch_generates_expected_nodes():
    """The installed launch declares the localization nodes and a Nav2 node.

    Graph-free: import the installed launch module (through the ament index, no
    source-tree fallback -- D27) and exercise ``generate_launch_description``,
    which fails fast on a broken launch without any node running.  It must
    declare ``robot_nav``'s ``map_node`` and ``ground_truth_odom`` plus a
    ``nav2_map_server`` node (RULING 5's real Nav2 lifecycle node).
    """
    import importlib.util
    from launch.actions import OpaqueFunction
    from launch.launch_context import LaunchContext
    from launch_ros.actions import Node
    path = _launch_path('robot_nav', 'nav.launch.py')
    assert os.path.isfile(path), (
        'nav.launch.py not present in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location('robot_nav_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    seen = []
    for action in description.entities:
        if isinstance(action, Node):
            seen.append((getattr(action, 'node_package', '?'),
                         getattr(action, 'node_executable', '?')))
    flat = ' '.join('%s/%s' % item for item in seen)
    assert 'robot_nav/map_node' in flat, flat
    assert 'robot_nav/ground_truth_odom' in flat, flat

    # The demo nav2_map_server is built inside an OpaqueFunction (its PGM is
    # rendered from the resolved ``world_state_path``), so resolve it here.
    context = LaunchContext()
    context.launch_configurations['world_state_path'] = (
        '/tmp/__nav_launch_probe_world__.json')
    for action in description.entities:
        if isinstance(action, OpaqueFunction):
            for produced in action.execute(context):
                if isinstance(produced, Node):
                    seen.append((getattr(produced, 'node_package', '?'),
                                 getattr(produced, 'node_executable', '?')))
    flat = ' '.join('%s/%s' % item for item in seen)
    assert 'nav2_map_server/map_server' in flat, flat


def test_nav_launch_declares_the_pr2_planning_stack():
    """PR2 (issue #124): the launch declares the omni bridge + nav2_bringup.

    Two additions over PR1: the ``omni_base_controller`` node (the
    ``/cmd_vel`` -> wheels bridge, RULING 5) and an ``IncludeLaunchDescription``
    of nav2_bringup's ``navigation.launch.py`` (the costmaps / NavFn / MPPI
    planner-controller stack, RULING 4).  The launch must NOT include
    nav2_bringup's localization/bringup files (those assume map_server + AMCL;
    our localization is ground-truth, PR1) -- it includes ``navigation.launch.py``
    only.
    """
    import importlib.util
    from launch.actions import IncludeLaunchDescription
    from launch.utilities import perform_substitutions
    from launch.launch_context import LaunchContext
    from launch_ros.actions import Node
    path = _launch_path('robot_nav', 'nav.launch.py')
    spec = importlib.util.spec_from_file_location('robot_nav_launch_pr2', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    nodes = set()
    includes = []
    for action in description.entities:
        if isinstance(action, Node):
            nodes.add((getattr(action, 'node_package', '?'),
                       getattr(action, 'node_executable', '?')))
        elif isinstance(action, IncludeLaunchDescription):
            source = action.launch_description_source
            location = getattr(
                source, '_LaunchDescriptionSource__location', None)
            context = LaunchContext()
            try:
                resolved = perform_substitutions(context, location)
            except Exception:
                resolved = repr(source)
            includes.append(str(resolved))

    assert ('robot_nav', 'omni_base_controller') in nodes, nodes
    assert any(r.endswith(os.path.join('launch', 'navigation.launch.py'))
               for r in includes), includes
    # Localization must not be re-included (map_server + AMCL assume a scan).
    assert not any(r.endswith('localization.launch.py') for r in includes), includes
    assert not any(r.endswith('bringup_launch.py') for r in includes), includes


def test_nav2_params_file_targets_the_holonomic_base():
    """The shipped ``nav2.yaml`` matches the base: omni MPPI, NavFn, D29 frames.

    RULING 4: the controller is MPPI with ``motion_model: Omni`` (the base is
    holonomic), the planner is NavFn, the frames are map/odom/base_link, and the
    costmaps carry static + inflation only (no scan-less obstacle layer).
    """
    import yaml
    path = os.path.join(_install_share('robot_nav'), 'params', 'nav2.yaml')
    assert os.path.isfile(path), path
    with open(path) as handle:
        cfg = yaml.safe_load(handle)
    follow = cfg['controller_server']['ros__parameters']['FollowPath']
    assert follow['plugin'] == 'nav2_mppi_controller::MPPIController'
    assert follow['motion_model'] == 'Omni'
    planner = (cfg['planner_server']['ros__parameters']
               ['GridBased']['plugin'])
    assert planner == 'nav2_navfn_planner::NavfnPlanner'
    for costmap in ('local_costmap', 'global_costmap'):
        params = cfg[costmap][costmap]['ros__parameters']
        assert params['robot_base_frame'] == 'base_link'
        assert params['plugins'] == ['static_layer', 'inflation_layer']
    assert (cfg['global_costmap']['global_costmap']['ros__parameters']
            ['global_frame']) == 'map'
    assert (cfg['local_costmap']['local_costmap']['ros__parameters']
            ['global_frame']) == 'odom'
