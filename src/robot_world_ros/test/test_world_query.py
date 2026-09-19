# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world query service returns the store's document, byte-for-byte.

The essence of D35's split: ``robot_world`` owns the world and its one
canonical JSON, and this node is only the ROS-facing shell that hands that
JSON out. The test therefore checks the *strongest* claim the service makes --
the response is exactly ``document_text(store.document())``, the bytes the
store would write to disk -- rather than a shape that merely looks right.
Anything that re-encoded the document (a typed message, a re-``json.dumps``, a
copy of the scene) would be a second representation (D23) and this test would
catch it.

Both tests build the node through its **real** constructor: parameters and the
``$ROBOT_WORLD_STATE`` fallback are part of the contract (R3), so a test that
reached past ``__init__`` would leave that half of the node unexercised. Each
test points the node at its own temp live file via the environment, so the
construction path -- params declared, path resolved, ``FileWorldStore`` opened
once -- runs for real.

``rclpy`` is imported lazily, inside the tests (robot_bringup's idiom), so
collecting this module needs no ROS runtime and a gap in the test env shows up
as the failing test, not a collection abort.
"""
import json
import os
import tempfile
import threading
import time

from robot_world import document_text, FileWorldStore, WorldDocument

#: A ROS domain of this suite's own, so the node under test does not collide
#: with anything else on the machine (robot_bringup's idiom).
WORLD_QUERY_DOMAIN_ID = '113'
SERVICE_READ_TIMEOUT_S = 20.0

#: The fully-qualified service the node offers (R3).
WORLD_QUERY_SERVICE = '/world_query/get_world'

#: The seed scene's 4 locations and 7 objects, as acceptance names them.
EXPECTED_LOCATIONS = ('charger', 'kitchen', 'table', 'living_room')
EXPECTED_OBJECTS = ('mug_1', 'plate_1', 'bowl_1', 'counter_1', 'book_1',
                    'cup_1', 'sofa_1')


def _init_rclpy():
    """Init the default rclpy context only if none is up yet; return who owns it."""
    import rclpy
    from rclpy.utilities import get_default_context
    owned = not get_default_context().ok()
    if owned:
        rclpy.init()
    return owned


def _temp_live_path():
    """Return a fresh temp live-state path (seeded from the shipped seed)."""
    directory = tempfile.mkdtemp(prefix='world_query_test_')
    return os.path.join(directory, 'world.json')


def test_get_world_returns_seed_document_exactly():
    """The handler's ``world_json`` is the store's canonical text, and parses back.

    Checks the response against ``document_text(store.document())`` directly --
    byte-for-byte, the D23 claim -- and then that the text round-trips through
    the document's own parser to the same ``WorldDocument``, carrying the
    seed's named locations and objects.
    """
    import rclpy
    from robot_world_ros.world_query_node import WorldQueryNode
    from robot_world_ros_interfaces.srv import GetWorld

    live_path = _temp_live_path()
    expected = document_text(FileWorldStore(live_path).document())

    # Point the node's env fallback at the temp live file and build it for
    # real, so start-up path resolution and store construction are exercised.
    os.environ['ROS_DOMAIN_ID'] = WORLD_QUERY_DOMAIN_ID
    os.environ['ROBOT_WORLD_STATE'] = live_path
    owned = _init_rclpy()
    node = WorldQueryNode()
    try:
        response = node._handle_get_world(GetWorld.Request(), GetWorld.Response())
        # The node is in the `/world_query` namespace and offers the relative
        # service `get_world`, so the graph sees R3's
        # `/world_query/get_world` (asserted end-to-end by the client in
        # test_node_builds_and_responds_headless).
        assert node.get_namespace() == '/world_query'
        assert node._service.srv_name == 'get_world'
    finally:
        node.destroy_node()
        if owned:
            rclpy.shutdown()

    assert response.world_json == expected

    parsed = json.loads(response.world_json)
    document = WorldDocument.from_dict(parsed)
    assert document == FileWorldStore(live_path).document()

    assert tuple(sorted(document.locations)) == tuple(sorted(EXPECTED_LOCATIONS))
    assert tuple(sorted(o.object_id for o in document.objects)) == (
        tuple(sorted(EXPECTED_OBJECTS)))


def test_node_builds_and_responds_headless():
    """The node comes up on a temp live file and answers the real service.

    The heavy check: ``rclpy.init()``, build :class:`WorldQueryNode` through its
    real constructor on a temp live path, create a client, and call
    ``/world_query/get_world`` for real (spinning a ``SingleThreadedExecutor``
    in a thread). Asserts the response parses to the seed document, which is
    what "returns the seed world exactly as the store reads it" means
    end-to-end.
    """
    from rclpy.executors import SingleThreadedExecutor
    from robot_world_ros.world_query_node import WorldQueryNode
    from robot_world_ros_interfaces.srv import GetWorld

    live_path = _temp_live_path()
    expected = document_text(FileWorldStore(live_path).document())

    os.environ['ROS_DOMAIN_ID'] = WORLD_QUERY_DOMAIN_ID
    os.environ['ROBOT_WORLD_STATE'] = live_path
    _init_rclpy()
    node = WorldQueryNode()

    executor = SingleThreadedExecutor()
    executor.add_node(node)
    spin = threading.Thread(target=executor.spin, daemon=True)
    spin.start()

    try:
        client = node.create_client(GetWorld, WORLD_QUERY_SERVICE)
        assert client.wait_for_service(timeout_sec=SERVICE_READ_TIMEOUT_S), (
            'the world query service never came up at %s' % WORLD_QUERY_SERVICE)
        future = client.call_async(GetWorld.Request())
        deadline = time.monotonic() + SERVICE_READ_TIMEOUT_S
        while not future.done() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert future.done(), 'no response from %s' % WORLD_QUERY_SERVICE
        result = future.result()
        assert result is not None
        assert result.world_json == expected
        assert WorldDocument.from_dict(json.loads(result.world_json)) == (
            FileWorldStore(live_path).document())
    finally:
        executor.shutdown()
        node.destroy_node()
        # rclpy.shutdown() is deliberately skipped here, matching
        # robot_bringup's headless idiom: tearing the context down while an
        # executor thread winds down aborts under this pytest host, and the
        # process exits right after the suite.
