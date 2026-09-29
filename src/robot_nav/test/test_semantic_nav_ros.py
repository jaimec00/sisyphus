# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""ROS-side unit test for the semantic bridge's resolve->drive->record path.

``test_semantic_nav.py`` pins the *pure* half (the pose copy and the lookup)
with no graph.  This file pins the other half -- the rclpy shell the bridge
actually runs -- without the sim: the bridge's three nested counterparts are
stubbed (a ``world_query`` service pair and a ``NavigateToPose`` server), and
the bridge is driven through its *real* seam, the ``NavigateToLocation``
action.

Why it exists (beyond closing the "_read_world/_drive_to/_record_arrival have
no unit test" note): a goal handler blocks while it polls its nested clients'
futures, and the response callbacks that complete those futures can only run on
*another* thread.  So the bridge must be spun by a
:class:`~rclpy.executors.MultiThreadedExecutor`; on the single-threaded
``rclpy.spin`` that ``main`` used to call, every nested callback is starved and
the goal comes back ``success=False`` with ``could not read the world from
/world_query/get_world``.  That is the regression this test pins: the child
launches the bridge exactly as shipped -- ``ros2 run robot_nav semantic_nav``,
i.e. the ``main()`` entry point, so ``main``'s own spin configuration is what
runs -- and the goal must succeed.

The whole probe runs in a **subprocess** so its rclpy session, its FastDDS
shared-memory transport and its ``ROS_DOMAIN_ID`` (131, clear of the PR2/PR3
probe domains 124..130) are isolated from the rest of the suite.  Inside that
probe there are two more processes: the bridge under test, and -- in the probe
itself -- the stubs and the action client, spun on their own
``MultiThreadedExecutor``.  The probe prints one ``I135-RESULT`` line that the
parent unpacks and asserts on, so a crash surfaces as a readable failure rather
than a hang.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

#: Domain of this test's own, clear of test_pr2_navigate.py (124..127) and
#: test_pr3_navigate.py (128..130).
ROS_DOMAIN_ID = '131'

#: How long the whole child probe may take: two interpreter starts, DDS
#: discovery, and the goal.
PROBE_TIMEOUT_S = 120.0

#: Location the goal names.  It is a real key of the stub world below.
TARGET_LOCATION = 'kitchen'
#: Kitchen's reference x/y in the stub world -- what NavigateToPose receives.
KITCHEN_X = 2.0
KITCHEN_Y = 0.0

_CHILD = r'''
import json
import os
import signal
import subprocess
import sys
import threading
import time

import rclpy
from rclpy.action import ActionClient, ActionServer
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from nav2_msgs.action import NavigateToPose
from robot_nav_interfaces.action import NavigateToLocation
from robot_skills import Pose as SkillPose
from robot_world import WorldDocument
from robot_world_ros_interfaces.srv import GetWorld, SetStartLocation

TARGET = 'kitchen'
KITCHEN_X = 2.0
KITCHEN_Y = 0.0

result = {'ok': False, 'error': 'child did not run', 'navigate_pose': None,
          'frame_id': None, 'start_location_seen': None, 'bridge_log': ''}


class StubWorld(Node):
    """The three counterparts the bridge talks to, stood up as stubs."""

    def __init__(self):
        super().__init__('i135_world_stub')
        self.see = {}

        document = WorldDocument(
            locations={
                'kitchen': SkillPose.from_xyz(KITCHEN_X, KITCHEN_Y, 0.0),
                'charger': SkillPose.from_xyz(0.0, 0.0, 0.0),
            },
            start_location='charger',
        )
        self._world_json = json.dumps(document.to_dict())

        self.create_service(GetWorld, '/world_query/get_world',
                            self._get_world)
        self.create_service(SetStartLocation,
                            '/world_query/set_start_location',
                            self._set_start_location)
        # The Nav2 counterpart: accept, record the requested pose, succeed
        # immediately (the base is not simulated here -- the pose it was asked
        # to drive to, and the location recorded back, are the claims).
        self._nav_server = ActionServer(
            self, NavigateToPose, '/navigate_to_pose',
            execute_callback=self._navigate,
            callback_group=ReentrantCallbackGroup(),
        )

    def _get_world(self, request, response):
        response.world_json = self._world_json
        return response

    def _set_start_location(self, request, response):
        self.see['start_location'] = request.location
        response.success = True
        response.error = ''
        return response

    def _navigate(self, goal_handle):
        pose = goal_handle.request.pose
        self.see['navigate_x'] = pose.pose.position.x
        self.see['navigate_y'] = pose.pose.position.y
        self.see['frame_id'] = pose.header.frame_id
        goal_handle.succeed()
        return NavigateToPose.Result()


def _spawn_bridge():
    """Start the shipped entry point so ``main``'s spin choice is tested."""
    return subprocess.Popen(
        [sys.executable, '-c',
         'from robot_nav.semantic_nav import main; main()'],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def main():
    rclpy.init()
    stub = StubWorld()
    executor = MultiThreadedExecutor()
    executor.add_node(stub)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()

    bridge = _spawn_bridge()
    log_lines = []

    def _pump():
        for line in bridge.stdout:
            log_lines.append(line)
    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()

    try:
        client = ActionClient(stub, NavigateToLocation, 'navigate_to_location')
        if not client.wait_for_server(timeout_sec=30.0):
            result['error'] = 'NavigateToLocation server never came up'
            return
        goal = NavigateToLocation.Goal()
        goal.location = TARGET
        send_future = client.send_goal_async(goal)
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and not send_future.done():
            time.sleep(0.02)
        if not send_future.done() or send_future.result() is None:
            result['error'] = 'goal was not accepted in time'
            return
        goal_handle = send_future.result()
        if not goal_handle.accepted:
            result['error'] = 'goal was rejected'
            return
        result_future = goal_handle.get_result_async()
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline and not result_future.done():
            time.sleep(0.02)
        if not result_future.done():
            result['error'] = (
                'goal produced no result in time (the bridge likely '
                'deadlocked on its nested clients)')
            return
        outcome = result_future.result().result
        result['ok'] = bool(outcome.success)
        result['error'] = outcome.error
        result['navigate_pose'] = (stub.see.get('navigate_x'),
                                   stub.see.get('navigate_y'))
        result['frame_id'] = stub.see.get('frame_id')
        result['start_location_seen'] = stub.see.get('start_location')
    except BaseException as exc:  # noqa: BLE001 - relay everything
        import traceback
        result['error'] = traceback.format_exc()
    finally:
        result['bridge_log'] = ''.join(log_lines)[-4000:]
        if bridge.poll() is None:
            bridge.send_signal(signal.SIGINT)
            try:
                bridge.wait(timeout=10)
            except subprocess.TimeoutExpired:
                bridge.kill()
        executor.shutdown()
        stub.destroy_node()
        rclpy.shutdown()


main()
sys.stdout.write('I135-RESULT ' + json.dumps(result) + '\n')
sys.stdout.flush()
'''


def _run_probe():
    """Run the ROS-side probe in a subprocess and return its result dict."""
    env = dict(os.environ, ROS_DOMAIN_ID=ROS_DOMAIN_ID)
    completed = subprocess.run(
        [sys.executable, '-c', _CHILD],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, timeout=PROBE_TIMEOUT_S, cwd=os.getcwd())
    payload = None
    for line in completed.stdout.splitlines():
        if line.startswith('I135-RESULT '):
            payload = json.loads(line[len('I135-RESULT '):])
    if payload is None:
        pytest.fail(
            'the ROS-side probe produced no result line (rc=%s):\n%s'
            % (completed.returncode,
               textwrap.indent(completed.stdout, '    ')))
    if not payload['ok']:
        pytest.fail(
            'the semantic bridge failed the stub success path: %r\n'
            '--- bridge log ---\n%s\n--- probe output ---\n%s'
            % (payload['error'],
               textwrap.indent(payload['bridge_log'], '    '),
               textwrap.indent(completed.stdout, '    ')))
    return payload


def test_semantic_bridge_drives_and_records_the_arrival():
    """The full resolve -> drive -> record chain succeeds against stub servers.

    The shipped entry point is launched as its own process, so ``main``'s spin
    configuration is exactly what runs; drives
    ``NavigateToLocation('kitchen')`` through the real action client and
    asserts (a) the result is
    ``success=True`` with an empty error -- impossible when the node is spun on
    a single thread, whose nested futures never complete -- and (b) the stubs
    saw the right traffic: Nav2 was asked for kitchen's reference pose in the
    ``map`` frame, and the world write carried the location name back.
    """
    payload = _run_probe()

    # JSON has no tuples: the child's ``(x, y)`` round-trips as a list.
    assert payload['navigate_pose'] == [KITCHEN_X, KITCHEN_Y], (
        "NavigateToPose did not receive kitchen's reference pose: %r"
        % (payload['navigate_pose'],))
    assert payload['frame_id'] == 'map', (
        'NavigateToPose was not sent in the map frame: %r'
        % (payload['frame_id'],))
    assert payload['start_location_seen'] == TARGET_LOCATION, (
        'set_start_location did not receive the arrived location: %r'
        % (payload['start_location_seen'],))
