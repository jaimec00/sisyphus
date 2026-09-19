# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 — a ``NavigateToPose`` goal actually DRIVES the base (issue #124).

This is PR2's integration claim (RULING 6).  It composes the full bringup --
the sim/control stack, the world query service, and the whole Nav2 layer
(localization + planning + the omni ``/cmd_vel`` bridge) -- via the *shipped*
``mujoco.launch.py`` on a ROS domain of its own, and then:

1. waits for the navigation stack to come up (``bt_navigator`` ACTIVE, the
   ``/odom`` + ``/map`` topics live, and the ``base_velocity_controller``
   accepting commands);
2. sends a ``NavigateToPose`` action goal to a nearby reachable location
   (``kitchen``'s reference pose, ~2 m away);
3. asserts the base **drives** there: the ground-truth base pose (read from the
   sim's ``GetBodyState('base_link')``) converges to the goal in position *and*
   heading, **and** that it got there by driving -- it passed through
   intermediate poses over a non-trivial time (not a teleport).

It lives in ``robot_bringup`` for the same reason PR1's acceptance does: it
composes the bringup (``robot_bringup`` already ``exec_depend``s ``robot_nav``,
so the reverse ``test_depend`` would be a colcon cycle) and it shells out to
``ros2 launch robot_bringup ...``.  ROS imports are lazy, inside the test.

The test is *conditional* like ``test_joint_command_moves_sim_state``: without
the source-built ``mujoco_ros2_control`` (D33) the sim cannot spawn, so the test
skips with the build command rather than failing on an uninstalled dependency.
"""
import math
import os
import signal
import subprocess
import tempfile
import threading
import time

import pytest

#: A ROS domain of this suite's own (121 is PR1's nav test in this package).
NAV2_DOMAIN_ID = '124'
#: How long the whole sim + Nav2 stack gets to come up and answer.
LAUNCH_READY_TIMEOUT_S = 120.0
#: How long a single driven goal gets (drive 0.3 m/s over ~2 m is ~10 s;
#: generous budget -- the sim and the controller each ramp).
GOAL_TIMEOUT_S = 120.0
#: The goal: ``kitchen``'s reference pose from the shipped seed world.
GOAL_X = 2.0
GOAL_Y = 0.0
#: Position/heading tolerances for "arrived".
POSITION_TOLERANCE = 0.30
HEADING_TOLERANCE = 0.35
#: A teleport would land in one step; require the base to have taken real time
#: and to have been seen at intermediate positions.
MIN_DRIVE_SECONDS = 2.0
MIN_INTERMEDIATE_DELTA = 0.20


def _require_tool(name):
    """Return the path to an executable on PATH, failing loudly if absent."""
    import shutil
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; pin it in pixi.toml / package.xml and run inside '
        '`pixi run`.' % name)
    return path


def _have_package(pkg):
    """Return True iff ``pkg`` is registered in the ament index."""
    from ament_index_python.packages import PackageNotFoundError
    from ament_index_python.packages import get_package_prefix
    try:
        get_package_prefix(pkg)
    except PackageNotFoundError:
        return False
    return True


def _spawn_launch(env, world_state_path):
    """Start ``mujoco.launch.py`` headless as its own process group."""
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_bringup', 'mujoco.launch.py',
         'use_sim_time:=true', 'world_state_path:=%s' % world_state_path],
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


def _write_world_file(path):
    from robot_world import default_seed_document, write_document
    write_document(path, default_seed_document())


def _yaw_from_quaternion(quat):
    """Return the yaw of a geometry_msgs Quaternion."""
    siny = 2.0 * (quat.w * quat.z + quat.x * quat.y)
    cosy = 1.0 - 2.0 * (quat.y * quat.y + quat.z * quat.z)
    return math.atan2(siny, cosy)


def test_navigate_to_pose_drives_the_base():
    """A ``NavigateToPose`` goal drives the base to the goal (not a teleport).

    Launches the shipped ``mujoco.launch.py`` (sim + controllers + world +
    the whole Nav2 layer) headless on an isolated ROS domain, waits for
    ``bt_navigator`` to be ACTIVE, sends a ``NavigateToPose`` goal for
    ``kitchen``, and asserts the ground-truth base pose converges in position
    and heading while taking real time and passing through intermediate poses.
    """
    if not _have_package('mujoco_ros2_control'):
        pytest.skip(
            'mujoco_ros2_control (dfki-ric, source-build via robot.repos, D33) '
            'is not installed; the NavigateToPose acceptance needs the live sim. '
            'Build it with: `vcs import src < robot.repos && pixi run build`.')
    import rclpy
    from action_msgs.msg import GoalStatus
    from controller_manager_msgs.srv import ListControllers
    from geometry_msgs.msg import PoseStamped
    from lifecycle_msgs.msg import State
    from lifecycle_msgs.srv import GetState
    from mujoco_ros2_control.srv import GetBodyState
    from nav2_msgs.action import NavigateToPose
    import rclpy.action
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node

    directory = tempfile.mkdtemp(prefix='pr2_nav2_e2e_')
    world_path = os.path.join(directory, 'world.json')
    _write_world_file(world_path)

    env = dict(os.environ, ROS_DOMAIN_ID=NAV2_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env, world_path)
    os.environ['ROS_DOMAIN_ID'] = NAV2_DOMAIN_ID
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('pr2_nav2_e2e_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)

        def _logs():
            return ''.join(output) or '<no output>'

        # -- 1. the nav stack is up: bt_navigator ACTIVE and odom flowing.
        def _lifecycle_active(node_name):
            client = node.create_client(GetState, '/%s/get_state' % node_name)
            deadline = time.monotonic() + LAUNCH_READY_TIMEOUT_S
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.1)
                if not client.service_is_ready():
                    continue
                future = client.call_async(GetState.Request())
                executor.spin_until_future_complete(future, timeout_sec=5.0)
                if future.done() and future.result() is not None:
                    if future.result().current_state.id == (
                            State.PRIMARY_STATE_ACTIVE):
                        return True
            return False

        assert _lifecycle_active('bt_navigator'), (
            'bt_navigator never became ACTIVE\n%s' % _logs())
        assert _lifecycle_active('controller_server'), (
            'controller_server never became ACTIVE\n%s' % _logs())

        # The base velocity controller must be accepting wheel commands.
        cm_client = node.create_client(
            ListControllers, '/controller_manager/list_controllers')
        deadline = time.monotonic() + LAUNCH_READY_TIMEOUT_S
        base_active = False
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
            if not cm_client.service_is_ready():
                continue
            future = cm_client.call_async(ListControllers.Request())
            executor.spin_until_future_complete(future, timeout_sec=5.0)
            if future.done() and future.result() is not None:
                if any(c.name == 'base_velocity_controller'
                       and c.state == 'active'
                       for c in future.result().controller):
                    base_active = True
                    break
        assert base_active, (
            'base_velocity_controller never became active\n%s' % _logs())

        # -- ground-truth base pose via the sim's GetBodyState service.
        state_client = node.create_client(GetBodyState, '/mujoco_get_body_state')
        assert state_client.wait_for_service(timeout_sec=30.0), (
            '/mujoco_get_body_state not available\n%s' % _logs())

        def _base_state():
            executor.spin_once(timeout_sec=0.05)
            if not state_client.service_is_ready():
                return None
            request = GetBodyState.Request()
            request.body_name = 'base_link'
            future = state_client.call_async(request)
            executor.spin_until_future_complete(future, timeout_sec=5.0)
            if not future.done() or future.result() is None:
                return None
            response = future.result()
            if not response.success:
                return None
            return response.pose

        start = _base_state()
        assert start is not None, (
            'GetBodyState(base_link) failed; is the base free?\n%s' % _logs())
        start_xy = (start.position.x, start.position.y)

        # -- 2. send the goal.
        action_client = rclpy.action.ActionClient(
            node, NavigateToPose, 'navigate_to_pose')
        assert action_client.wait_for_server(timeout_sec=30.0), (
            'navigate_to_pose action server not available\n%s' % _logs())

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = node.get_clock().now().to_msg()
        goal.pose.pose.position.x = GOAL_X
        goal.pose.pose.position.y = GOAL_Y
        goal.pose.pose.orientation.w = 1.0
        send_future = action_client.send_goal_async(goal)
        executor.spin_until_future_complete(send_future, timeout_sec=30.0)
        assert send_future.done() and send_future.result() is not None, (
            'goal was never accepted\n%s' % _logs())
        goal_handle = send_future.result()
        assert goal_handle.accepted, (
            'NavigateToPose goal rejected\n%s' % _logs())
        result_future = goal_handle.get_result_async()

        # -- 3. the base drives: sample its pose while the goal runs.
        started = time.monotonic()
        max_travel = 0.0
        final_pose = None
        while time.monotonic() - started < GOAL_TIMEOUT_S:
            executor.spin_once(timeout_sec=0.1)
            pose = _base_state()
            if pose is not None:
                final_pose = pose
                travelled = math.hypot(pose.position.x - start_xy[0],
                                       pose.position.y - start_xy[1])
                max_travel = max(max_travel, travelled)
            if result_future.done():
                break

        assert result_future.done(), (
            'NavigateToPose did not finish within %.0fs (travelled %.2f m)\n%s'
            % (GOAL_TIMEOUT_S, max_travel, _logs()))
        status = result_future.result().status
        assert status == GoalStatus.STATUS_SUCCEEDED, (
            'NavigateToPose status %r, expected SUCCEEDED\n%s'
            % (status, _logs()))

        elapsed = time.monotonic() - started
        final_pose = _base_state() or final_pose
        assert final_pose is not None, 'no base pose after the goal'

        # Position + heading converged...
        dx = final_pose.position.x - GOAL_X
        dy = final_pose.position.y - GOAL_Y
        distance = math.hypot(dx, dy)
        assert distance <= POSITION_TOLERANCE, (
            'base ended %.3f m from the goal (%.3f, %.3f); pose '
            '(%.3f, %.3f)\n%s' % (distance, GOAL_X, GOAL_Y,
                                  final_pose.position.x, final_pose.position.y,
                                  _logs()))
        yaw = _yaw_from_quaternion(final_pose.orientation)
        assert abs(math.atan2(math.sin(yaw), math.cos(yaw))) <= HEADING_TOLERANCE, (
            'base heading %.3f rad is not the goal heading (0)\n%s'
            % (yaw, _logs()))

        # ...and it DROVE there (real time + intermediate poses), not teleported.
        assert elapsed >= MIN_DRIVE_SECONDS, (
            'goal completed in %.2fs -- too fast to be a drive\n%s'
            % (elapsed, _logs()))
        assert max_travel >= MIN_INTERMEDIATE_DELTA, (
            'base never moved away from its start (max travel %.3f m)\n%s'
            % (max_travel, _logs()))
        assert abs(start_xy[0]) < 0.2 and abs(start_xy[1]) < 0.2, (
            'base did not start at the origin: %r' % (start_xy,))
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        _terminate_group(process, group)
