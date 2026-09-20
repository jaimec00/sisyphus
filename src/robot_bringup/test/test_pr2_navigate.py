# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 — the base DRIVES under wheel commands (issue #124).

This is PR2's integration claim (RULING 6), **re-scoped to the open-loop
claim**.  It composes the full bringup -- the sim/control stack, the world query
service, and the whole Nav2 layer (localization + planning + the omni
``/cmd_vel`` bridge) -- via the *shipped* ``mujoco.launch.py`` on a ROS domain
of its own, and then:

1. waits for the navigation stack to come up (``bt_navigator`` ACTIVE, the
   ``/odom`` + ``/map`` topics live, ``controller_server`` ACTIVE, and the
   ``base_velocity_controller`` accepting commands) -- proving the full stack
   composes cleanly;
2. publishes a body ``geometry_msgs/Twist`` **directly** on ``/cmd_vel`` and
   asserts the ground-truth base pose (``GetBodyState('base_link')``) moves in
   the **commanded direction**: pure ``+vx`` translates the base ``+x``
   (and holds ``|yaw|`` small), pure ``+wz`` rotates the base ``+yaw`` (the
   sign-split fix).

The claim is **open-loop direction only** — NOT speed, and NOT closed-loop
``NavigateToPose`` convergence.  The sim models the omniwheels as plain
cylinders (no rim rollers), so the wheel/floor contact **scrubs**: the base
under-delivers speed (~⅓ of commanded) and combined ``vx+wz`` degrades.  The
closed-loop ``NavigateToPose`` acceptance is therefore **deferred to post-#125**
(the rim-roller omniwheel model, promoted to the prerequisite for closed-loop
nav).  Nav2 is still brought fully up here — no goal is sent, so the controller
/ smoother stay quiet and the direct ``/cmd_vel`` publication is
uncontested — because keeping it up proves the stack composes.

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
#: Pure +vx drive check: commanded 0.3 m/s, held for this long.
VX_DRIVE_S = 5.0
VX_COMMAND = 0.3
#: Pure +wz rotation check: commanded 0.6 rad/s, held for this long.
WZ_DRIVE_S = 8.0
WZ_COMMAND = 0.6
#: Open-loop DIRECTION thresholds (not speed).  The plain-cylinder wheel/floor
#: contact scrubs, so the base under-delivers speed (~⅓ commanded), and the
#: response is only reproducible from a **fresh** sim (a second command in the
#: same session slips): measured per fresh session, +vx=0.3 -> dx≈+0.31…+0.49 m,
#: +wz=0.6 -> dyaw≈+1.77 rad over 8 s.  Thresholds keep comfortable margin
#: against speed, asserting direction only.  No teleport: require an
#: intermediate pose en route.
MIN_VX_DX = 0.15
MIN_WZ_DYAWM = 0.5
#: When driving +x the plain-cylinder contact yaws the base somewhat (scrub,
#: measured up to ~0.9 rad and variable); assert only that it stays under a
#: quarter turn, i.e. the base is clearly translating rather than spinning in
#: place.  This is a direction check, not a heading-hold check (the scrub is
#: #125's to fix).
MAX_VX_YAWR = 1.4
MIN_INTERMEDIATE_DELTA = 0.10


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


def _drive_probe(vx, wz, duration):
    """Launch the full bringup once and return the base's motion for one Twist.

    Spawns the *shipped* ``mujoco.launch.py`` (sim + controllers + world + the
    whole Nav2 layer) headless on an isolated ROS domain, waits for the stack to
    be ACTIVE and ``GetBodyState`` to answer (proving the stack composes), then
    publishes the given body Twist **directly** on ``/cmd_vel`` for ``duration``
    seconds and returns ``(dx, dy, dyaw, max_travel)`` of the ground-truth base
    pose over that window.

    Each direction gets its **own sim session**: the plain-cylinder wheel/floor
    contact (no rim rollers) slips once the base has been driven, so a second
    command in the same session is unreliable.  A fresh start per direction is
    the reproducible configuration (see ``implementation.md`` and #125).
    """
    import rclpy
    from controller_manager_msgs.srv import ListControllers
    from geometry_msgs.msg import Twist
    from lifecycle_msgs.msg import State
    from lifecycle_msgs.srv import GetState
    from mujoco_ros2_control.srv import GetBodyState
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node

    directory = tempfile.mkdtemp(prefix='pr2_drive_e2e_')
    world_path = os.path.join(directory, 'world.json')
    _write_world_file(world_path)

    env = dict(os.environ, ROS_DOMAIN_ID=NAV2_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env, world_path)
    os.environ['ROS_DOMAIN_ID'] = NAV2_DOMAIN_ID
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('pr2_drive_e2e_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)

        def _logs():
            return ''.join(output) or '<no output>'

        # -- the nav stack is up: bt_navigator + controller_server ACTIVE.
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

        cmd_pub = node.create_publisher(Twist, 'cmd_vel', 10)

        def _wrap(angle):
            return math.atan2(math.sin(angle), math.cos(angle))

        twist = Twist()
        twist.linear.x = float(vx)
        twist.angular.z = float(wz)

        start = _base_state()
        assert start is not None, (
            'GetBodyState(base_link) failed; is the base free?\n%s' % _logs())
        start_xy = (start.position.x, start.position.y)
        start_yaw = _wrap(_yaw_from_quaternion(start.orientation))
        deadline = time.monotonic() + duration
        final = start
        max_travel = 0.0
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.0)
            cmd_pub.publish(twist)
            pose = _base_state()
            if pose is not None:
                final = pose
                max_travel = max(max_travel, math.hypot(
                    pose.position.x - start_xy[0],
                    pose.position.y - start_xy[1]))
            time.sleep(0.02)
        end_yaw = _wrap(_yaw_from_quaternion(final.orientation))
        return (final.position.x - start.position.x,
                final.position.y - start.position.y,
                _wrap(end_yaw - start_yaw), max_travel)
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        _terminate_group(process, group)


def test_base_drives_under_wheel_commands():
    """The base DRIVES under ``/cmd_vel`` wheel commands (open-loop, direction).

    Composes the full bringup (sim + controllers + world + the whole Nav2 layer)
    via the *shipped* ``mujoco.launch.py`` on an isolated ROS domain, twice --
    once per direction, each from a fresh sim session -- and asserts the
    ground-truth base pose moves in the **commanded direction**:

    * pure ``+vx`` -> the base translates ``+x`` (dx clearly positive) and does
      not spin in place (``|dyaw|`` under a quarter turn);
    * pure ``+wz`` -> the base rotates ``+yaw`` (dyaw clearly positive) -- the
      sign-split fix (a global ``-1.0`` rotated it ``-yaw``).

    Each probe also proves the stack composes: it waits for ``bt_navigator`` and
    ``controller_server`` ACTIVE, ``base_velocity_controller`` active, and
    ``GetBodyState`` live before driving.

    **Direction only, NOT speed** -- the plain-cylinder sim wheels scrub, so the
    base under-delivers speed (~⅓ commanded).  The closed-loop ``NavigateToPose``
    convergence acceptance is therefore **deferred to post-#125** (rim-roller
    omniwheel model, the prerequisite for closed-loop nav).  No ``NavigateToPose``
    goal is sent: the controller/smoother stay quiet, so the direct ``/cmd_vel``
    publication is uncontested.
    """
    if not _have_package('mujoco_ros2_control'):
        pytest.skip(
            'mujoco_ros2_control (dfki-ric, source-build via robot.repos, D33) '
            'is not installed; the drive acceptance needs the live sim. '
            'Build it with: `vcs import src < robot.repos && pixi run build`.')

    # -- 2a. pure +wz: the base rotates +yaw (the sign-split fix).
    _, _, dyaw_wz, _ = _drive_probe(0.0, WZ_COMMAND, WZ_DRIVE_S)
    assert dyaw_wz >= MIN_WZ_DYAWM, (
        'pure +wz=%.2f rotated dyaw=%.3f rad (expected >= %.2f) -- the base did '
        'not rotate +yaw (the wz sign split is the fix for this)'
        % (WZ_COMMAND, dyaw_wz, MIN_WZ_DYAWM))

    # -- 2b. pure +vx: the base translates +x, not a spin or a teleport.
    dx_vx, _, dyaw_vx, max_travel = _drive_probe(
        VX_COMMAND, 0.0, VX_DRIVE_S)
    assert dx_vx >= MIN_VX_DX, (
        'pure +vx=%.2f drove dx=%.3f m (expected >= %.2f) -- base did not '
        'translate +x' % (VX_COMMAND, dx_vx, MIN_VX_DX))
    assert abs(dyaw_vx) <= MAX_VX_YAWR, (
        'pure +vx=%.2f turned dyaw=%.3f rad (expected |dyaw| <= %.2f) -- the '
        'base spun rather than translating' % (VX_COMMAND, dyaw_vx, MAX_VX_YAWR))
    assert max_travel >= MIN_INTERMEDIATE_DELTA, (
        'pure +vx=%.2f: base never displaced (max travel %.3f m) -- not a drive'
        % (VX_COMMAND, max_travel))
