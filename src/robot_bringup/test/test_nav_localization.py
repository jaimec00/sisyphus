# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The Nav2 localization layer comes up headless and publishes a live map + TF.

This is PR1's integration claim (issue #121 / RULING 3, RULING 5), and it
lives here in ``robot_bringup`` because it composes the full bringup: the TF
stack (``robot.launch.py``, which publishes ``base_link``'s children), the
world query service (``world.launch.py``, which answers ``/world_query/get_world``)
and the localization layer (``robot_nav``'s ``nav.launch.py``).  This package
already ``exec_depend``s ``robot_nav``, so the composed bringup is reachable at
test time; putting this test in ``robot_nav`` would require a reverse
``test_depend`` on ``robot_bringup``, a colcon build-order cycle.

Everything is driven through the *shipped* launch files on a ROS domain of its
own (like ``test_world_launch.py``/``test_tf_tree.py``), so the thing under
test is the artifact that ships, not an inline re-play:

* ``test_mujoco_launch_includes_nav_launch`` -- structural: the sim bringup
  includes ``nav.launch.py`` (like it already includes ``world.launch.py``).
* ``test_nav_localization_under_launch`` -- the heavyweight acceptance: launch
  headless, bring ``map_node`` ACTIVE, and assert the complete connected TF
  tree (``map -> odom -> base_link -> base_footprint``, with ``base_link``'s
  parent ``odom`` per D29 and ``base_footprint``'s parent ``base_link``), a
  ``/map`` grid with the seed scene's expected free and occupied cells, and
  that the real ``nav2_map_server`` lifecycle node also reaches ACTIVE.

ROS imports are lazy, inside the test functions (the repo idiom): collecting
this module needs no ROS runtime, and a gap in the test env fails the test, not
collection.
"""
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

#: A ROS domain of this suite's own (112 tf_tree, 113 mujoco, 115 world_write,
#: 117 world_launch in robot_bringup).
NAV_DOMAIN_ID = '121'
#: How long to wait for the launched nodes to come up and answer.
LAUNCH_READY_TIMEOUT_S = 60.0
#: How long the probe waits for the TF tree and the grid.
READ_TIMEOUT_S = 45.0

#: The frames the acceptance requires, and their parents (D29/RULING 2).
EXPECTED_PARENTS = {
    'odom': 'map',
    'base_link': 'odom',
    'base_footprint': 'base_link',
}
#: D29's inverted edge: the wheels hang off base_link, not base_footprint.
EXPECTED_WHEELS = {
    'base_left_wheel_link',
    'base_back_wheel_link',
    'base_right_wheel_link',
}


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


def test_mujoco_launch_includes_nav_launch():
    """mujoco.launch.py includes nav.launch.py, by resolved source path.

    Localization must come up as part of the sim bringup.  The proof is
    structural -- the sim launch's LaunchDescription carries an
    IncludeLaunchDescription whose source resolves to the installed
    ``nav.launch.py`` -- so it costs nothing and needs no sim.
    """
    import importlib.util
    from launch import LaunchContext
    from launch.actions import IncludeLaunchDescription
    from launch.utilities import perform_substitutions
    path = _launch_path('robot_bringup', 'mujoco.launch.py')
    assert os.path.isfile(path), (
        'mujoco.launch.py not present in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location('robot_bringup_mujoco_nav', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    includes = [a for a in description.entities
                if isinstance(a, IncludeLaunchDescription)]
    assert includes, 'mujoco.launch.py declares no IncludeLaunchDescription'

    expected = os.path.join('launch', 'nav.launch.py')
    context = LaunchContext()
    rendered = []
    for include in includes:
        source = include.launch_description_source
        location = getattr(source, '_LaunchDescriptionSource__location', None)
        try:
            resolved = perform_substitutions(context, location)
        except Exception:
            resolved = repr(source)
        rendered.append(str(resolved))
    assert any(r.endswith(expected) for r in rendered), (
        'mujoco.launch.py does not include nav.launch.py; includes=%r' % (rendered,))


def _spawn_one(args, env):
    """Start one launch as its own process group; collect its output async."""
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch'] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []
    reader = threading.Thread(
        target=lambda: output.append(process.stdout.read()), daemon=True)
    reader.start()
    return process, group, output, reader


def _spawn_launch(env, world_state_path):
    """Start the composed bringup the localization layer ships inside.

    ``nav.launch.py`` is the localization concern; it is *included* by the sim
    bringup and composes with two sibling concerns that own their own frames
    and data (exactly as ``mujoco.launch.py`` composes them): the robot TF
    stack (``robot_bringup robot.launch.py`` publishes ``base_link``'s
    children -- ``base_footprint`` and the wheels) and the world query service
    (``world.launch.py`` answers ``/world_query/get_world``, which the map node
    derives its grid from).  Launching the three together is the bringup shape
    under test; the artifact under test is still the shipped
    ``nav.launch.py``, not an inline re-play of its nodes.

    Each launch gets its own process group so teardown cannot leak a node.
    """
    launched = [
        _spawn_one(['robot_bringup', 'robot.launch.py'], env),
        _spawn_one(['robot_bringup', 'world.launch.py',
                    'world_state_path:=%s' % world_state_path], env),
    ]
    time.sleep(3.0)  # let RSP and the world service register before nav joins
    launched.append(_spawn_one(
        ['robot_nav', 'nav.launch.py',
         'world_state_path:=%s' % world_state_path], env))
    output = []
    for _, _, piece, _ in launched:
        output.append(piece)
    return launched, output


def _terminate_group(launched):
    """Take down every launch and everything it spawned (D29 lesson)."""
    for process, group, _, _ in launched:
        try:
            os.killpg(group, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for process, group, _, _ in launched:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass


def _output_text(output):
    """Flatten the per-launch output buffers into one string for diagnostics."""
    return ''.join(''.join(piece) for piece in output)


def _parse_frame_string(text):
    """Parse tf2's all_frames_as_string into ``{child: parent}``."""
    parents = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('Frame '):
            continue
        head = line[len('Frame '):]
        child, _, rest = head.partition(' exists with parent ')
        parents[child] = rest.rstrip('.')
    return parents


def _write_world_file(path):
    """Write the shipped seed world to ``path`` (the launch renders it to PGM)."""
    from robot_world import default_seed_document, write_document
    write_document(path, default_seed_document())


def test_nav_localization_under_launch():
    """Acceptance: launch headless and assert the TF tree, /map, and ACTIVE.

    Launches the shipped ``nav.launch.py`` on its own ROS domain with a temp
    world-state file (so the demo PGM and the ``/map`` grid both come from the
    seed scene), then probes with a dedicated ``rclpy.Context``.  Three
    claims, one launch:

    1. the TF tree is connected and correctly rooted:
       ``map -> odom -> base_link -> base_footprint`` with ``base_link``'s
       parent ``odom`` (D29) and ``base_footprint``'s parent ``base_link``;
    2. ``/map`` carries a grid whose cell at each seed location is free and at
       each seed furniture object is occupied;
    3. ``map_node`` reaches ACTIVE -- and the real ``nav2_map_server``
       lifecycle node does too (RULING 5).
    """
    import rclpy
    from lifecycle_msgs.msg import State
    from lifecycle_msgs.srv import GetState
    from nav_msgs.msg import OccupancyGrid
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from robot_world import default_seed_document
    from tf2_ros import Buffer, TransformListener

    directory = tempfile.mkdtemp(prefix='nav_launch_e2e_')
    world_path = os.path.join(directory, 'world.json')
    _write_world_file(world_path)

    env = dict(os.environ, ROS_DOMAIN_ID=NAV_DOMAIN_ID)
    launched, output = _spawn_launch(env, world_path)
    os.environ['ROS_DOMAIN_ID'] = NAV_DOMAIN_ID
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('nav_launch_e2e_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)

        # -- wait for the TF tree to connect.
        buffer = Buffer()
        node._tf_listener = TransformListener(buffer, node)
        deadline = time.monotonic() + LAUNCH_READY_TIMEOUT_S
        parents = {}
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
            try:
                parents = _parse_frame_string(buffer.all_frames_as_string())
            except Exception:
                parents = {}
            expected_present = all(
                parents.get(child) == parent
                for child, parent in EXPECTED_PARENTS.items())
            if expected_present and EXPECTED_WHEELS <= set(parents):
                break
        else:
            logs = _output_text(output)
            raise AssertionError(
                'TF tree did not connect within %.0fs; frames=%r\nLaunch log:\n%s'
                % (LAUNCH_READY_TIMEOUT_S, parents, logs or '<no output>'))

        # -- 1. the TF tree D29/RULING 2 requires.
        for child, parent in EXPECTED_PARENTS.items():
            assert parents.get(child) == parent, (
                'TF: %s should have parent %s, got %r (full tree: %r)'
                % (child, parent, parents.get(child), parents))
        for wheel in EXPECTED_WHEELS:
            assert wheel in parents, (
                'wheel frame %r missing from the TF tree' % wheel)
            assert parents[wheel] == 'base_link', (
                'D29: %s should hang off base_link, got %r'
                % (wheel, parents[wheel]))
        # The tree is connected: map is the only root in this chain.
        assert parents['odom'] == 'map', parents

        # -- 2. /map carries the derived grid.
        received = []
        node.create_subscription(
            OccupancyGrid, '/map', lambda msg: received.append(msg), 10)
        deadline = time.monotonic() + READ_TIMEOUT_S
        while time.monotonic() < deadline and not received:
            executor.spin_once(timeout_sec=0.1)
        assert received, (
            'no grid received on /map within %.0fs\nLaunch log:\n%s'
            % (READ_TIMEOUT_S, _output_text(output) or '<no output>'))
        grid = received[-1]
        assert grid.header.frame_id == 'map', grid.header
        assert grid.info.width > 0 and grid.info.height > 0, grid.info
        assert len(grid.data) == grid.info.width * grid.info.height

        def _cell(x, y):
            col = int((x - grid.info.origin.position.x) // grid.info.resolution)
            row = int((y - grid.info.origin.position.y) // grid.info.resolution)
            assert 0 <= row < grid.info.height and 0 <= col < grid.info.width, (
                '(%r, %r) falls outside the published grid' % (x, y))
            return grid.data[row * grid.info.width + col]

        document = default_seed_document()
        occupied = {obj.object_id for obj in document.objects if not obj.graspable}
        assert occupied, 'the seed scene should hold non-graspable furniture'
        for name, pose in document.locations.items():
            value = _cell(pose.position.x, pose.position.y)
            assert value == 0, (
                'location %r cell is %d, expected free (0)' % (name, value))
        for obj in document.objects:
            if obj.object_id not in occupied:
                continue
            value = _cell(obj.pose.position.x, obj.pose.position.y)
            assert value == 100, (
                'furniture %r cell is %d, expected occupied (100)'
                % (obj.object_id, value))

        # -- 3. lifecycle: map_node ACTIVE, and the real Nav2 map_server too.
        def _lifecycle_state(service_name):
            client = node.create_client(GetState, service_name)
            deadline = time.monotonic() + READ_TIMEOUT_S
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.1)
                if client.service_is_ready():
                    break
            else:
                return None
            future = client.call_async(GetState.Request())
            executor.spin_until_future_complete(future, timeout_sec=READ_TIMEOUT_S)
            if not future.done() or future.result() is None:
                return None
            return future.result().current_state.id

        map_node_state = _lifecycle_state('/map_node/get_state')
        assert map_node_state == State.PRIMARY_STATE_ACTIVE, (
            'map_node should be ACTIVE (%d), got %r\nLaunch log:\n%s'
            % (State.PRIMARY_STATE_ACTIVE, map_node_state,
               _output_text(output) or '<no output>'))
        server_state = _lifecycle_state('/map_server/get_state')
        assert server_state == State.PRIMARY_STATE_ACTIVE, (
            'the real nav2_map_server should be ACTIVE (%d), got %r\n'
            'Launch log:\n%s'
            % (State.PRIMARY_STATE_ACTIVE, server_state,
               _output_text(output) or '<no output>'))
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        # rclpy.shutdown() is deferred (the repo's headless idiom).
        _terminate_group(launched)
