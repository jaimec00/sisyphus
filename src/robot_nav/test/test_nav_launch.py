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
