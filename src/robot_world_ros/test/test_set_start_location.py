# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The ``set_start_location`` service records an arrival (PR3/issue #135, R3).

This is the sole-writer end of the nav bridge: the ``semantic_nav`` node asks
``/world_query/set_start_location`` when the base reaches a named location, so
the world's ``start_location`` keeps agreeing with where the robot is.  The
node stays the sole writer (D35), so the test drives its handler and its live
service exactly like ``test_world_write.py`` drives the object writes:

* a known location commits and survives a re-open from disk;
* an unknown location is refused (``success=False`` + the location list) and
  leaves the file byte-identical.

``rclpy`` is imported lazily, inside the tests, so collecting this module needs
no ROS runtime.
"""
import json
import os
import tempfile
import threading
import time

from robot_world import FileWorldStore, WorldDocument

#: A ROS domain of this suite's own (test_world_query.py uses 113,
#: test_world_write.py 115).
START_LOCATION_DOMAIN_ID = '116'
SERVICE_READ_TIMEOUT_S = 20.0

#: The fully-qualified service the node offers (R3).
SET_START_LOCATION_SERVICE = '/world_query/set_start_location'
GET_WORLD_SERVICE = '/world_query/get_world'


def _temp_live_path():
    """Return a fresh temp live-state path (seeded from the shipped seed)."""
    directory = tempfile.mkdtemp(prefix='world_start_location_test_')
    return os.path.join(directory, 'world.json')


def _build_node(live_path):
    """Init rclpy (if idle) and build a real :class:`WorldQueryNode` on ``live_path``."""
    import rclpy
    from rclpy.utilities import get_default_context
    from robot_world_ros.world_query_node import WorldQueryNode

    os.environ['ROS_DOMAIN_ID'] = START_LOCATION_DOMAIN_ID
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


def _re_read(live_path):
    """Return the scene as a *fresh* store opened from disk reads it."""
    return FileWorldStore(live_path).document()


def test_set_start_location_commits_and_survives_re_read():
    """A known location updates the store, commits, and shows up in get_world."""
    from robot_world_ros_interfaces.srv import GetWorld, SetStartLocation

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        request = SetStartLocation.Request()
        request.location = 'kitchen'
        response = node._handle_set_start_location(
            request, SetStartLocation.Response())
        assert response.success is True, response.error
        assert response.error == ''

        world = node._handle_get_world(GetWorld.Request(), GetWorld.Response())
        assert '"start_location": "kitchen"' in world.world_json
    finally:
        _teardown(node, owned)

    document = _re_read(live_path)
    assert document.start_location == 'kitchen'
    # The rest of the scene did not move: only the start location changed.
    assert document.find_object('mug_1') is not None
    assert WorldDocument.from_dict(json.loads(world.world_json)) == document


def test_set_start_location_refusal_leaves_the_scene_untouched():
    """An unknown location returns success=False and changes nothing on disk."""
    from robot_world_ros_interfaces.srv import SetStartLocation

    live_path = _temp_live_path()
    node, owned = _build_node(live_path)
    try:
        with open(live_path, encoding='utf-8') as stream:
            before = stream.read()
        assert '"start_location": "charger"' in before

        request = SetStartLocation.Request()
        request.location = 'attic'
        response = node._handle_set_start_location(
            request, SetStartLocation.Response())

        assert response.success is False
        assert "unknown location 'attic'" in response.error
        assert 'known locations: charger, kitchen, living_room, table' in response.error

        with open(live_path, encoding='utf-8') as stream:
            after = stream.read()
        assert after == before
    finally:
        _teardown(node, owned)


def test_set_start_location_service_over_the_wire():
    """End-to-end: the node serves ``/world_query/set_start_location`` for real.

    Spins the node with a ``SingleThreadedExecutor`` in a thread and calls the
    real service (R3's promise that the nav bridge can reach it), then reads
    back ``/world_query/get_world`` -- the exact query -> write -> query round
    trip the integration test asserts against the live bringup.
    """
    from rclpy.executors import SingleThreadedExecutor
    from robot_world_ros_interfaces.srv import GetWorld, SetStartLocation

    live_path = _temp_live_path()
    node, _owned = _build_node(live_path)

    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()
    try:
        writer = node.create_client(SetStartLocation, SET_START_LOCATION_SERVICE)
        reader = node.create_client(GetWorld, GET_WORLD_SERVICE)
        assert writer.wait_for_service(timeout_sec=SERVICE_READ_TIMEOUT_S), (
            'the write service never came up at %s' % SET_START_LOCATION_SERVICE)
        assert reader.wait_for_service(timeout_sec=SERVICE_READ_TIMEOUT_S), (
            'the query service never came up at %s' % GET_WORLD_SERVICE)

        write_request = SetStartLocation.Request()
        write_request.location = 'table'
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
        assert document.start_location == 'table'
    finally:
        executor.shutdown()
        node.destroy_node()
        # rclpy.shutdown() is deliberately skipped (the shared idiom): tearing
        # the context down while an executor thread winds down aborts here.
