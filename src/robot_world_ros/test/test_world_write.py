# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world write services mutate the store, atomically and only when valid.

The write half of D35's split (R1/R2/R4): ``/world_query/update_object_pose``,
``/world_query/add_object`` and ``/world_query/remove_object`` change one object
at a time on the node's single ``FileWorldStore``. Two properties are load
bearing and both are checked here against the *disk*, not just the in-memory
scene:

* **Persistence.** A committed write survives a re-read: a fresh
  ``FileWorldStore(live_path)`` opened from disk sees the new scene, because the
  store's mutators commit atomically (temp file + ``os.replace``, D23).
* **Refusal leaves the file byte-identical.** An unknown/duplicate id, a bad
  label, or a pose the store rejects (non-finite float, all-zero quaternion)
  returns ``success=False`` with the scene unchanged on disk -- the store raises
  *before* touching the registry, and the handler maps that to a status instead
  of a crash.

The handlers are called directly (``node._handle_...``) for the per-op tests,
which exercises the real handler bodies without a ROS round trip; the final
test drives the live services end to end to prove the R4 claim (write then query
is fresh, no re-open, one shared store instance).

``rclpy`` is imported lazily, inside the tests (robot_bringup's idiom), so
collecting this module needs no ROS runtime and a gap in the test env shows up
as the failing test, not a collection abort. ``rclpy.init()`` is guarded on the
default context so this module coexists with ``test_world_query.py`` (which
leaves a context up): a second ``init`` on a live context is a ``RuntimeError``.
"""
import json
import os
import tempfile
import threading
import time

from robot_world import FileWorldStore, WorldDocument

#: A ROS domain of this suite's own, so the node under test does not collide
#: with anything else on the machine (test_world_query.py's idiom).
WORLD_WRITE_DOMAIN_ID = '115'
SERVICE_READ_TIMEOUT_S = 20.0

#: The fully-qualified services the node offers (R3).
UPDATE_POSE_SERVICE = '/world_query/update_object_pose'
GET_WORLD_SERVICE = '/world_query/get_world'


def _temp_live_path():
    """Return a fresh temp live-state path (seeded from the shipped seed)."""
    directory = tempfile.mkdtemp(prefix='world_write_test_')
    return os.path.join(directory, 'world.json')


def _build_node(live_path):
    """Init rclpy (if idle) and build a real :class:`WorldQueryNode` on ``live_path``.

    Returns ``(node, owned_init)``: ``owned_init`` is True when this call
    initialized the rclpy context and must therefore shut it down, and False
    when a context was already up (left by another test module) and must be
    left alone -- calling ``init`` on a live context raises.

    Points the node's env fallback at the temp live file so the construction
    path (params declared, path resolved, store opened once) runs for real.
    """
    import rclpy
    from rclpy.utilities import get_default_context
    from robot_world_ros.world_query_node import WorldQueryNode

    os.environ['ROS_DOMAIN_ID'] = WORLD_WRITE_DOMAIN_ID
    os.environ['ROBOT_WORLD_STATE'] = live_path
    owned_init = not get_default_context().ok()
    if owned_init:
        rclpy.init()
    return WorldQueryNode(), owned_init


def _teardown(node, owned_init):
    """Destroy ``node`` and shut rclpy down only if this call initialized it."""
    import rclpy
    node.destroy_node()
    if owned_init:
        rclpy.shutdown()


def _ros_pose(x, y, z, qx=0.0, qy=0.0, qz=0.0, qw=1.0):
    """Build a ``geometry_msgs/Pose`` message."""
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


def _re_read(live_path):
    """Return the scene as a *fresh* store opened from disk reads it."""
    return FileWorldStore(live_path).document()


def test_update_object_pose_persists_and_survives_re_read():
    """An update through the handler commits and shows up on disk and in get_world."""
    from robot_world_ros_interfaces.srv import GetWorld, UpdateObjectPose

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        request = UpdateObjectPose.Request()
        request.object_id = 'mug_1'
        request.pose = _ros_pose(0.3, 2.0, 0.75)
        response = node._handle_update_object_pose(
            request, UpdateObjectPose.Response())
        assert response.success is True, response.error
        assert response.error == ''

        # The node's own read handler sees the write (shared store instance)...
        world = node._handle_get_world(GetWorld.Request(), GetWorld.Response())
        assert '"x": 0.3' in world.world_json
    finally:
        _teardown(node, owned)

    # ...and a store re-opened from disk sees it too (atomic persistence).
    document = _re_read(live_path)
    item = document.find_object('mug_1')
    assert item is not None
    assert item.pose.position.x == 0.3
    assert item.pose.position.y == 2.0
    assert item.pose.position.z == 0.75
    assert WorldDocument.from_dict(json.loads(world.world_json)) == document


def test_add_object_persists_and_survives_re_read():
    """An add through the handler registers the object on disk and in get_world."""
    from robot_world_ros_interfaces.srv import AddObject, GetWorld

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        request = AddObject.Request()
        request.object_id = 'spoon_1'
        request.label = 'spoon'
        request.pose = _ros_pose(1.0, 1.0, 0.8)
        request.graspable = True
        response = node._handle_add_object(request, AddObject.Response())
        assert response.success is True, response.error
        assert response.error == ''

        world = node._handle_get_world(GetWorld.Request(), GetWorld.Response())
    finally:
        _teardown(node, owned)

    document = _re_read(live_path)
    item = document.find_object('spoon_1')
    assert item is not None
    assert item.label == 'spoon'
    assert item.graspable is True
    assert item.held_by is None
    assert item.pose.position.x == 1.0
    assert WorldDocument.from_dict(json.loads(world.world_json)) == document


def test_remove_object_persists_and_survives_re_read():
    """A remove through the handler drops the object on disk and in get_world."""
    from robot_world_ros_interfaces.srv import GetWorld, RemoveObject

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        request = RemoveObject.Request()
        request.object_id = 'bowl_1'
        response = node._handle_remove_object(request, RemoveObject.Response())
        assert response.success is True, response.error
        assert response.error == ''

        world = node._handle_get_world(GetWorld.Request(), GetWorld.Response())
    finally:
        _teardown(node, owned)

    document = _re_read(live_path)
    assert document.find_object('bowl_1') is None
    assert WorldDocument.from_dict(json.loads(world.world_json)) == document


def test_write_is_atomic():
    """After a write the live file parses back whole, with no ``.tmp`` litter."""
    from robot_world_ros_interfaces.srv import UpdateObjectPose

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        request = UpdateObjectPose.Request()
        request.object_id = 'mug_1'
        request.pose = _ros_pose(0.5, 2.5, 0.9)
        response = node._handle_update_object_pose(
            request, UpdateObjectPose.Response())
        assert response.success is True, response.error
    finally:
        _teardown(node, owned)

    # The file is a complete, schema-valid document (a torn write would fail
    # the parser): parse it directly and assert the schema stamp survives.
    with open(live_path, encoding='utf-8') as stream:
        parsed = json.loads(stream.read())
    assert parsed['world_schema_version'] == 1
    assert WorldDocument.from_dict(parsed) == _re_read(live_path)

    # The atomic write's temp file is gone: the directory holds only the live
    # file (the write removes its temp on success and on failure alike).
    litter = [name for name in os.listdir(os.path.dirname(live_path))
              if name.endswith('.tmp')]
    assert litter == [], f'temp file left behind: {litter}'


def test_foreign_or_corrupt_input_rejected():
    """Every refusal returns success=False and leaves the scene byte-identical."""
    from robot_world_ros_interfaces.srv import AddObject, RemoveObject, UpdateObjectPose

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        with open(live_path, encoding='utf-8') as stream:
            before = stream.read()

        def update_pose(object_id, pose):
            request = UpdateObjectPose.Request()
            request.object_id = object_id
            request.pose = pose
            return node._handle_update_object_pose(
                request, UpdateObjectPose.Response())

        def add(object_id, label, pose, graspable=True):
            request = AddObject.Request()
            request.object_id = object_id
            request.label = label
            request.pose = pose
            request.graspable = graspable
            return node._handle_add_object(request, AddObject.Response())

        def remove(object_id):
            request = RemoveObject.Request()
            request.object_id = object_id
            return node._handle_remove_object(request, RemoveObject.Response())

        good = _ros_pose(1.0, 1.0, 1.0)
        refusals = {
            'update unknown object_id': update_pose('ghost_1', good),
            'remove unknown object_id': remove('ghost_1'),
            'add duplicate object_id': add('mug_1', 'mug', good),
            'add blank object_id': add('   ', 'spoon', good),
            'add blank label': add('spoon_1', '', good),
            'update NaN pose': update_pose(
                'mug_1', _ros_pose(float('nan'), 0.0, 0.0)),
            'add inf pose': add(
                'spoon_1', 'spoon', _ros_pose(0.0, float('inf'), 0.0)),
            'update all-zero quaternion': update_pose(
                'mug_1', _ros_pose(0.0, 0.0, 0.0, qw=0.0)),
        }
        for label, response in refusals.items():
            assert response.success is False, label
            assert response.error != '', label

        # Every refusal left the live file exactly as it was: the store raises
        # before mutating the registry, and the handler never commits a partial.
        with open(live_path, encoding='utf-8') as stream:
            after = stream.read()
        assert after == before
    finally:
        _teardown(node, owned)


def test_write_then_query_is_fresh():
    """End-to-end R4 proof: a write over the service is visible to a later query.

    Spins the node with a ``SingleThreadedExecutor`` in a thread and calls the
    real ``/world_query/update_object_pose`` and ``/world_query/get_world``
    clients. The query must reflect the write with no file re-open in between --
    read and write share one store instance, so there is no snapshot to go
    stale.
    """
    from rclpy.executors import SingleThreadedExecutor
    from robot_world_ros_interfaces.srv import GetWorld, UpdateObjectPose

    live_path = _temp_live_path()
    node, _owned = _build_node(live_path)

    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()
    try:
        writer = node.create_client(UpdateObjectPose, UPDATE_POSE_SERVICE)
        reader = node.create_client(GetWorld, GET_WORLD_SERVICE)
        assert writer.wait_for_service(timeout_sec=SERVICE_READ_TIMEOUT_S), (
            'the write service never came up at %s' % UPDATE_POSE_SERVICE)
        assert reader.wait_for_service(timeout_sec=SERVICE_READ_TIMEOUT_S), (
            'the query service never came up at %s' % GET_WORLD_SERVICE)

        write_request = UpdateObjectPose.Request()
        write_request.object_id = 'mug_1'
        write_request.pose = _ros_pose(0.7, 2.2, 0.66)

        # Write, then read back over the wire; both go through the same node.
        write_future = writer.call_async(write_request)
        deadline = time.monotonic() + SERVICE_READ_TIMEOUT_S
        while not write_future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert write_future.done()
        write_result = write_future.result()
        assert write_result is not None
        assert write_result.success is True, write_result.error

        read_future = reader.call_async(GetWorld.Request())
        deadline = time.monotonic() + SERVICE_READ_TIMEOUT_S
        while not read_future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert read_future.done()
        read_result = read_future.result()
        assert read_result is not None

        document = WorldDocument.from_dict(json.loads(read_result.world_json))
        item = document.find_object('mug_1')
        assert item is not None
        assert item.pose.position.x == 0.7
        assert item.pose.position.y == 2.2
        assert item.pose.position.z == 0.66
    finally:
        executor.shutdown()
        node.destroy_node()
        # rclpy.shutdown() is deliberately skipped here, matching
        # robot_bringup's headless idiom: tearing the context down while an
        # executor thread winds down aborts under this pytest host, and the
        # process exits right after the suite.
