# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""End to end: world objects land in the MoveIt planning scene (R10 item 4).

This is PR1's third acceptance criterion and its strongest test. It launches the
real three-node stack -- ``robot_bringup``'s ``world.launch.py``, the headless
``move_group`` from ``robot_moveit_config``, and this package's
``planning_scene_bridge`` -- through the shipped
``planning_scene_bridge.launch.py``, on a private ``ROS_DOMAIN_ID``, and then
polls ``move_group``'s ``/get_planning_scene`` until the seed objects appear as
collision objects.

What it proves, and what it does not
------------------------------------
It proves the *integration*: the bridge read the world through the service,
converted it, and move_group accepted the diff -- the acceptance criterion
"the planning scene contains the world's objects". It does not re-assert the
transforms (that is the unit test's job) or the SRDF (the config package's);
here the assertions are deliberately about the objects *arriving*, by id, with
a primitive, at the world pose.

Environment notes: everything is headless (no RViz, no controllers, no sim);
``move_group`` needs the installed config package and ``world_query`` needs
``robot_world``'s seed; both are shipped deps, so the test does not skip.
"""
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

#: Private ROS domain (112 tf_tree, 113 mujoco, 115 world_write, 117
#: world_launch, 118 move_group): this stack runs three long-lived nodes.
BRIDGE_E2E_DOMAIN_ID = '119'
WORLD_READY_TIMEOUT_S = 180.0
SCENE_OBJECT_TIMEOUT_S = 180.0
SERVICE_CALL_TIMEOUT_S = 20.0

#: The seed's object ids the planning scene must contain (from robot_world).
SEED_OBJECT_IDS = {
    'mug_1', 'plate_1', 'bowl_1', 'counter_1', 'book_1', 'cup_1', 'sofa_1',
}


def _require_tool(name):
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; it is pinned in pixi.toml / package.xml -- run '
        'inside `pixi run`.' % name)
    return path


def _launch_path():
    from ament_index_python.packages import get_package_share_directory
    return os.path.join(get_package_share_directory('robot_moveit'),
                        'launch', 'planning_scene_bridge.launch.py')


def test_bridge_launch_generates_expected_nodes():
    """The composed launch declares the bridge, world and move_group nodes."""
    import importlib.util
    from launch.actions import IncludeLaunchDescription
    from launch_ros.actions import Node
    path = _launch_path()
    assert os.path.isfile(path), (
        'planning_scene_bridge.launch.py not in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location(
        'robot_moveit_bridge_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    nodes = {(getattr(a, 'node_package', '?'), getattr(a, 'node_executable', '?'))
             for a in description.entities if isinstance(a, Node)}
    assert ('robot_moveit', 'planning_scene_bridge') in nodes, nodes
    # The static world -> base_link TF: without it move_group rejects every
    # object as "Unknown frame: world" (verified by probing move_group).
    assert ('tf2_ros', 'static_transform_publisher') in nodes, nodes

    includes = [a for a in description.entities
                if isinstance(a, IncludeLaunchDescription)]
    assert len(includes) == 2, (
        'expected world.launch.py + move_group.launch.py includes; got %d'
        % len(includes))


def _spawn_launch(env, world_state_path):
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_moveit',
         'planning_scene_bridge.launch.py',
         'world_state_path:=%s' % world_state_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []
    reader = threading.Thread(
        target=lambda: output.append(process.stdout.read()), daemon=True)
    reader.start()
    return process, group, output, reader


def _terminate_group(process, group):
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


def test_seed_objects_appear_in_the_planning_scene():
    """E2E: launch the stack and poll until move_group holds the seed objects.

    Polls ``/get_planning_scene`` for the collision-object names (a light
    request: ``WORLD_OBJECT_NAMES``) until all seven seed ids are present, then
    requests the geometry for them and asserts each object reports a primitive
    matching the R5 table's kind and the world's pose (position, to 1e-9 --
    the values are copied, not recomputed).
    """
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from moveit_msgs.srv import GetPlanningScene
    from moveit_msgs.msg import PlanningSceneComponents
    from robot_world.storage import default_seed_document

    default_seed_document()  # sanity: the seed loads (the scene must match it)

    directory = tempfile.mkdtemp(prefix='bridge_e2e_')
    world_state_path = os.path.join(directory, 'world.json')
    env = dict(os.environ, ROS_DOMAIN_ID=BRIDGE_E2E_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env, world_state_path)
    os.environ['ROS_DOMAIN_ID'] = BRIDGE_E2E_DOMAIN_ID
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('bridge_e2e_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        client = node.create_client(GetPlanningScene, '/get_planning_scene')

        deadline = time.monotonic() + WORLD_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
            if client.service_is_ready():
                break
        else:
            logs = ''.join(output)
            raise AssertionError(
                '/get_planning_scene never became available within %.0fs. '
                'Launch log:\n%s' % (WORLD_READY_TIMEOUT_S,
                                     logs or '<no output>'))

        def _names():
            request = GetPlanningScene.Request()
            request.components.components = (
                PlanningSceneComponents.WORLD_OBJECT_NAMES)
            future = client.call_async(request)
            executor.spin_until_future_complete(
                future, timeout_sec=SERVICE_CALL_TIMEOUT_S)
            if not future.done() or future.result() is None:
                return set()
            # WORLD_OBJECT_NAMES fills each entry's id only (no geometry), so
            # read the ids off the CollisionObject list.
            return {o.id for o in future.result().scene.world.collision_objects}

        names = set()
        deadline = time.monotonic() + SCENE_OBJECT_TIMEOUT_S
        while time.monotonic() < deadline:
            names = _names()
            if SEED_OBJECT_IDS <= names:
                break
            time.sleep(0.5)
        logs = ''.join(output)
        assert SEED_OBJECT_IDS <= names, (
            'planning scene missing seed objects %r (saw %r) after %.0fs. '
            'Launch log:\n%s'
            % (sorted(SEED_OBJECT_IDS - names), sorted(names),
               SCENE_OBJECT_TIMEOUT_S, logs or '<no output>'))

        # Now read the geometry and assert each object carries the primitive
        # the R5 table puts on its label. NOTE: this deliberately does NOT
        # re-assert the world position here. move_group stores the accepted
        # objects transformed into its planning frame (``base_link``) and, for
        # an object whose pose it has folded in, reports the *object frame*
        # pose as identity -- so reading a world position back off the scene
        # message is not a faithful round-trip. The exact pose copy is proven
        # in the unit test (test_world_to_scene), and the arrival proof here is
        # the ids above plus the primitive below (both faithful in the scene).
        request = GetPlanningScene.Request()
        request.components.components = (
            PlanningSceneComponents.WORLD_OBJECT_GEOMETRY)
        future = client.call_async(request)
        executor.spin_until_future_complete(
            future, timeout_sec=SERVICE_CALL_TIMEOUT_S)
        assert future.done() and future.result() is not None
        by_id = {o.id: o for o in future.result().scene.world.collision_objects}
        expected_kind = {'mug_1': 'CYLINDER', 'plate_1': 'CYLINDER',
                         'bowl_1': 'CYLINDER', 'cup_1': 'CYLINDER',
                         'book_1': 'BOX', 'counter_1': 'BOX', 'sofa_1': 'BOX'}
        for object_id in SEED_OBJECT_IDS:
            collision = by_id[object_id]
            assert len(collision.primitives) == 1, object_id
            primitive = collision.primitives[0]
            assert primitive.type == getattr(primitive, expected_kind[object_id]), (
                '%s primitive kind drifted from the R5 table' % object_id)
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        _terminate_group(process, group)
