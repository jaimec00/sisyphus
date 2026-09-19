# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world-state query service is wired into the bringup, end to end (PR3).

PR1/PR2 stood up ``robot_world_ros``'s ``world_query_node`` on its own
(``/world_query/get_world`` and the three write services, all on one node). This
file holds the PR3 claim: that node is part of the *shipped* bringup, and a
sense -> write -> sense round-trip through the real launch lands on disk.

Three claims, cheapest first:

* ``test_world_launch_generates_expected_nodes`` -- the shipped
  ``world.launch.py`` is installed and declares the ``robot_world_ros``
  ``world_query_node`` (no sim, no graph).
* ``test_mujoco_launch_includes_world_service`` -- ``mujoco.launch.py``'s
  LaunchDescription *includes* ``world.launch.py``, so the full sim bringup
  stands the service up alongside the control stack (again no sim needed).
* ``test_query_write_query_round_trip_under_launch`` -- the end-to-end proof:
  launch the real ``world.launch.py`` headless against a temp live file, query
  the seed, write a new pose (the structured-coordinates shape invariant 4's
  perception emits), query again and assert the round-trip matches, then re-open
  the file from disk and assert it equals the query -- D23's single source of
  truth (the ROS node and the brain's read path share one file).

Everything is non-skipping: the claims need only ``robot_world_ros`` + ``rclpy``
(both always present in this repo), never ``mujoco_ros2_control``. ROS imports
are lazy, inside the test functions, so collecting this module needs no ROS
runtime (the repo's idiom) and a gap in the test env fails the test, not
collection.
"""
import math
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

#: A ROS domain of this suite's own, so the launched node does not collide with
#: anything else on the machine (112 tf_tree, 113 mujoco, 115 world_write).
WORLD_LAUNCH_DOMAIN_ID = '117'
SERVICE_READY_TIMEOUT_S = 30.0
SERVICE_CALL_TIMEOUT_S = 20.0

#: The fully-qualified services the launched node (namespace ``/world_query``,
#: hardcoded in its ``__init__``) must offer.
GET_WORLD_SERVICE = '/world_query/get_world'
UPDATE_POSE_SERVICE = '/world_query/update_object_pose'

#: The seed object this round-trip moves (graspable, so perception-shaped).
SEED_OBJECT_ID = 'mug_1'


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


def _launch_path(name):
    """Return the path to an installed bringup launch file."""
    return os.path.join(_install_share('robot_bringup'), 'launch', name)


def test_world_launch_generates_expected_nodes():
    """The shipped world launch exists and declares the world query node.

    Cheap and deterministic: import the *installed* launch module and exercise
    ``generate_launch_description``, which fails fast on a broken launch (bad
    import, wrong package/executable) without the ROS graph. The heavy proof
    that the node actually runs and round-trips is the e2e test below.
    """
    import importlib.util
    from launch.actions import DeclareLaunchArgument
    from launch_ros.actions import Node
    path = _launch_path('world.launch.py')
    assert os.path.isfile(path), (
        'world.launch.py not present in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location(
        'robot_bringup_world_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    nodes = [a for a in description.entities if isinstance(a, Node)]
    seen = [(getattr(a, 'node_package', '?'),
             getattr(a, 'node_executable', '?')) for a in nodes]
    assert ('robot_world_ros', 'world_query_node') in seen, (
        'world.launch.py does not declare robot_world_ros/world_query_node: '
        '%r' % (seen,))

    # The two params the node declares must be launch arguments with sensible
    # defaults: a bare `ros2 launch robot_bringup world.launch.py` must bring
    # the node up against a *live* file, not an empty path (which the node
    # refuses at startup).
    declared = {getattr(a, 'name', None) for a in description.entities
                if isinstance(a, DeclareLaunchArgument)}
    assert 'world_state_path' in declared, declared
    assert 'world_seed_path' in declared, declared

    # No namespace= on the node action: the node hardcodes /world_query in its
    # __init__; a launch-side namespace would double-namespace it. The pinned
    # launch version exposes no public accessor for either, so the private
    # members are read -- the same necessity test_mujoco_launch.py documents
    # for the handler's private actions list.
    node = nodes[0]
    assert getattr(node, '_Node__node_namespace', None) in (None, ''), (
        'world.launch.py must not set a namespace on world_query_node (the '
        'node sets /world_query itself); got %r'
        % (getattr(node, '_Node__node_namespace', None),))

    # Both node params must be wired to their launch arguments, so an override
    # of ``world_state_path``/``world_seed_path`` reaches the node. The param
    # dict keys are substitution sequences (one TextSubstitution per name);
    # render them against an empty context to compare on the parameter name.
    from launch import LaunchContext
    from launch.utilities import perform_substitutions
    context = LaunchContext()
    param_dicts = getattr(node, '_Node__parameters', ())
    wired = set()
    for entry in param_dicts:
        if not hasattr(entry, 'keys'):
            continue
        for key in entry.keys():
            try:
                wired.add(perform_substitutions(context, key))
            except Exception:
                wired.add(str(key))
    assert {'world_state_path', 'world_seed_path'} <= wired, (
        'world_query_node params are not wired to the launch arguments: %r'
        % (sorted(wired),))


def test_mujoco_launch_includes_world_service():
    """mujoco.launch.py includes world.launch.py, by resolved source path.

    The world service must come up as part of the sim bringup. The proof is
    structural -- the sim launch's LaunchDescription carries an
    IncludeLaunchDescription whose source resolves to the installed
    ``world.launch.py`` -- so it costs nothing and needs no sim. The included
    node definition itself is exercised by the other two tests.
    """
    import importlib.util
    from launch import LaunchContext
    from launch.actions import IncludeLaunchDescription
    from launch.utilities import perform_substitutions
    path = _launch_path('mujoco.launch.py')
    assert os.path.isfile(path), (
        'mujoco.launch.py not present in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location(
        'robot_bringup_mujoco_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    includes = [a for a in description.entities
                if isinstance(a, IncludeLaunchDescription)]
    assert includes, (
        'mujoco.launch.py declares no IncludeLaunchDescription; the world '
        'service is not wired into the sim bringup')

    # Perform each included source's location against an empty context: the
    # source is a PythonLaunchDescriptionSource over a PathJoinSubstitution,
    # which renders to the concrete installed ``launch/world.launch.py`` path.
    # The location substitution list is reached through the private
    # ``__location`` member -- the same necessity the repo's existing
    # ``test_mujoco_launch.py`` documents for the handler's private actions
    # list (the pinned launch version exposes no public accessor).
    expected = os.path.join('launch', 'world.launch.py')
    context = LaunchContext()
    rendered = []
    for include in includes:
        source = include.launch_description_source
        location = getattr(
            source, '_LaunchDescriptionSource__location', None)
        try:
            resolved = perform_substitutions(context, location)
        except Exception:
            resolved = repr(source)
        rendered.append(str(resolved))
    assert any(r.endswith(expected) for r in rendered), (
        'mujoco.launch.py does not include world.launch.py; includes=%r'
        % (rendered,))


def _spawn_launch(env, live_path):
    """Start the real world launch headless (process-group teardown)."""
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_bringup', 'world.launch.py',
         'world_state_path:=%s' % live_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []
    reader = threading.Thread(
        target=lambda: output.append(process.stdout.read()), daemon=True)
    reader.start()
    return process, group, output, reader


def _terminate_group(process, group):
    """Take down the launch and everything it spawned."""
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        pass
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


def _ros_pose(x, y, z, qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    """Build a ``geometry_msgs/Pose`` message (the perception shape, inv. 4)."""
    from geometry_msgs.msg import Pose
    message = Pose()
    message.position.x = x
    message.position.y = y
    message.position.z = z
    message.orientation.x = qx
    message.orientation.y = qy
    message.orientation.z = qz
    message.orientation.w = qw
    return message


def test_query_write_query_round_trip_under_launch():
    """E2E: query seed -> write a pose -> query -> disk; all must agree.

    Launches the shipped ``world.launch.py`` headless against a temp live file
    on its own ROS domain, then probes with a **dedicated** ``rclpy.Context``
    (so it never collides with another test's default context) and a
    ``SingleThreadedExecutor``. The sequence is the PR3 acceptance criterion:

    1. query the seed and assert ``mug_1`` is present, capturing its *runtime*
       seed pose (never a hardcoded one -- the module seed owns it);
    2. write a new position AND a non-trivial unit quaternion (a 90-degree Z
       rotation) through ``/world_query/update_object_pose``;
    3. query again and assert ``mug_1`` is now *exactly* at the written pose,
       position and orientation alike (the round-trip matches);
    4. re-open the file from disk with ``FileWorldStore`` and assert its
       document equals the query's -- the store the ROS node wrote is the same
       store the brain reads (D23 single source of truth).
    """
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from robot_world import FileWorldStore, WorldDocument
    from robot_world_ros_interfaces.srv import GetWorld, UpdateObjectPose
    import json

    directory = tempfile.mkdtemp(prefix='world_launch_e2e_')
    live_path = os.path.join(directory, 'world.json')

    env = dict(os.environ, ROS_DOMAIN_ID=WORLD_LAUNCH_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env, live_path)
    # The probe must share the launched node's domain, not the pytest host's.
    os.environ['ROS_DOMAIN_ID'] = WORLD_LAUNCH_DOMAIN_ID
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('world_launch_e2e_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)

        reader_cli = node.create_client(GetWorld, GET_WORLD_SERVICE)
        writer_cli = node.create_client(UpdateObjectPose, UPDATE_POSE_SERVICE)

        def _wait_for(client, service, timeout_s):
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.1)
                if client.service_is_ready():
                    return
            logs = ''.join(output)
            raise AssertionError(
                '%s never became available within %.0fs. Launch log:\n%s'
                % (service, timeout_s, logs or '<no output>'))

        _wait_for(reader_cli, GET_WORLD_SERVICE, SERVICE_READY_TIMEOUT_S)
        _wait_for(writer_cli, UPDATE_POSE_SERVICE, SERVICE_READY_TIMEOUT_S)

        def _call(client, request):
            future = client.call_async(request)
            executor.spin_until_future_complete(
                future, timeout_sec=SERVICE_CALL_TIMEOUT_S)
            assert future.done(), 'service call did not complete'
            result = future.result()
            assert result is not None, 'service call returned no response'
            return result

        def _query_document():
            response = _call(reader_cli, GetWorld.Request())
            return WorldDocument.from_dict(json.loads(response.world_json))

        # -- 1. query the seed; mug_1 is present at its (runtime) seed pose.
        seed_document = _query_document()
        seed_object = seed_document.find_object(SEED_OBJECT_ID)
        assert seed_object is not None, (
            'seed world has no object %r' % SEED_OBJECT_ID)
        seed_position = seed_object.pose.position

        # -- 2. write a perception-shaped update: new position + 90 deg Z turn.
        write_x, write_y, write_z = 0.42, 2.13, 0.78
        half = math.sqrt(2.0) / 2.0
        write_pose = _ros_pose(write_x, write_y, write_z,
                               qx=0.0, qy=0.0, qz=half, qw=half)

        def _moved_from_seed():
            return not (
                write_x == seed_position.x
                and write_y == seed_position.y
                and write_z == seed_position.z)

        assert _moved_from_seed(), (
            'the write pose equals the seed pose; the round-trip would not '
            'prove anything')

        write_request = UpdateObjectPose.Request()
        write_request.object_id = SEED_OBJECT_ID
        write_request.pose = write_pose
        write_response = _call(writer_cli, write_request)
        assert write_response.success is True, write_response.error
        assert write_response.error == ''

        # -- 3. query again: mug_1 is exactly at the written pose.
        after_document = _query_document()
        after_object = after_document.find_object(SEED_OBJECT_ID)
        assert after_object is not None, (
            '%r vanished after the write' % SEED_OBJECT_ID)
        pose = after_object.pose
        assert pose.position.x == write_x, pose.position
        assert pose.position.y == write_y, pose.position
        assert pose.position.z == write_z, pose.position
        assert pose.orientation.x == 0.0, pose.orientation
        assert pose.orientation.y == 0.0, pose.orientation
        assert pose.orientation.z == half, pose.orientation
        assert pose.orientation.w == half, pose.orientation

        # -- 4. the file on disk (the brain's read path) matches the query.
        disk_document = FileWorldStore(live_path).document()
        assert disk_document == after_document, (
            'the live-state file and the query response disagree (D23: one '
            'source of truth)')
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        # rclpy.shutdown() is deferred (the repo's headless idiom): spinning
        # the default context down mid-thread aborts under this pytest host and
        # the process exits right after the suite.
        _terminate_group(process, group)
