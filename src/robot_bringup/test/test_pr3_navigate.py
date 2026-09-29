# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR3 — the semantic ``NavigateToLocation`` bridge drives the base (issue #135).

This is PR3's integration claim (RULING 5/R1): the *skill-level* semantic seam
the brain speaks -- "go to the kitchen" -- actually moves the robot on the
classical/real track, and the world agrees afterwards.  It composes the full
bringup (the sim/control stack, the world query service, and the whole Nav2
layer including the ``semantic_nav`` bridge) via the *shipped*
``mujoco.launch.py`` on a ROS domain of its own, then:

1. waits for the stack to come up (``bt_navigator`` ACTIVE, the
   ``base_velocity_controller`` active, ``GetBodyState`` answering);
2. calls ``NavigateToLocation('kitchen')`` on ``/navigate_to_location`` -- the
   ROS action of RULING 1's new ``robot_nav_interfaces`` package -- and asserts
   the action reports ``success=True``;
3. asserts the **ground-truth** base pose (``mujoco_ros2_control``
   ``GetBodyState('base_link')``) shows the base *substantially drove toward*
   kitchen's *reference* pose ``(x ~= 2.0, y ~= 0.0, heading ~= 0)``: forward
   progress ``dx > MIN_ARRIVAL_DX``, small lateral drift ``abs(dy) <
   MAX_ARRIVAL_ABS_DY`` and heading held ``abs(dyaw) < MAX_ARRIVAL_ABS_DYAW``.
   Kitchen is straight ahead of the charger start (which is the origin, heading
   +x), so the goal exercises the forward channel; the yaw is accumulated
   incrementally across the run (the #125 pitfall) and the base is allowed to
   settle before measuring;
4. asserts ``/world_query/get_world`` now reports ``start_location='kitchen'``
   -- the **query -> nav -> query** round trip that is the whole point of R3:
   the semantic bridge not only drives the base but keeps the world's
   ``start_location`` in step with where the base actually is.

.. note::
   Strict xy-convergence (``error_xy < GOAL_XY_TOLERANCE``, 0.10 m) is
   *deferred* to the MPPI / rim-roller-plant convergence follow-up -- the same
   deferral ``test_pr2_navigate.py`` records (that plant systematically
   undershoots ~8%, so PR2 asserts only direction too).  PR3's acceptance is
   the *semantic* contract plus "the base drove substantially toward the
   location" -- not a converged pose;

It lives in ``robot_bringup`` for the same reason PR1's and PR2's acceptance
tests do: it composes the bringup (``robot_bringup`` already ``exec_depend``s
``robot_nav``) and shells out to ``ros2 launch robot_bringup ...``.  ROS imports
are lazy, inside the test.

The test is *conditional* like ``test_pr2_navigate.py``: without the
source-built ``mujoco_ros2_control`` (D33) the sim cannot spawn, so it skips
with the build command rather than failing on an uninstalled dependency.
"""
import math
import os
import re
import signal
import subprocess
import tempfile
import threading
import time

import pytest

#: A base ROS domain of this suite's own.  ``test_pr2_navigate.py`` uses
#: 124 (open-loop) through 127 (its convergence probe); PR3 starts at 128 and
#: each probe gets its own domain so the two launches in one process never
#: share a FastDDS shared-memory port namespace (the same reason the PR2 file
#: spaces its domains out).
NAV2_DOMAIN_ID = 128
#: How long the whole sim + Nav2 stack gets to come up and answer.
LAUNCH_READY_TIMEOUT_S = 120.0
#: Bringup attempts per probe.  The Nav2 lifecycle manager can hit a transient
#: DDS-discovery race on a loaded host ("bt_navigator/get_state ...
#: async_send_request failed" -> "Aborting bringup"); it does not retry, so the
#: probe relaunches on a fresh domain.  A real defect still fails every attempt.
_BRINGUP_ATTEMPTS = 3
#: The location the semantic goal names (the shipped seed scene's kitchen).
TARGET_LOCATION = 'kitchen'
#: Kitchen's reference pose in the seed world (default_world.json).
KITCHEN_X = 2.0
KITCHEN_Y = 0.0
#: Minimum forward progress toward kitchen the base must make.  Kitchen is
#: 2.0 m ahead of the origin start, so closing >half the gap proves a real
#: drive toward the location (not a teleport, a backwards move, or a no-op).
MIN_ARRIVAL_DX = 1.0
#: Maximum lateral drift allowed, keeping the base on the charger->kitchen
#: line.  Strict xy-convergence (``error_xy < 0.10``) is deferred to the
#: MPPI / rim-roller-plant follow-up (PR2 records the same ~8% undershoot), so
#: PR3 accepts the *neighbourhood*, not a converged pose.
MAX_ARRIVAL_ABS_DY = 0.3
#: Maximum heading change allowed, keeping the base pointed near +x (kitchen's
#: reference yaw is 0).
MAX_ARRIVAL_ABS_DYAW = 0.5
#: How long the whole NavigateToLocation goal gets to converge.
GOAL_TIMEOUT_S = 120.0
#: The base must settle (stop moving) after the goal before the pose assertion,
#: so a still-transiting base is not measured.
SETTLE_PERIOD_S = 1.0


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


#: FastDDS leaves its POSIX shared-memory segments in ``/dev/shm`` named
#: ``fastrtps_<...>`` plus a ``sem.fastrtps_<...>_mutex`` lock per port.  It
#: does not always unlink them on teardown, and when they accumulate a *later*
#: launch dies at startup with ``Failed init_port fastrtps_port7003:
#: open_and_lock_file failed`` -- a false negative unrelated to the code under
#: test.  These helpers mirror ``test_pr2_navigate.py`` exactly.
_SHm_NAME_RE = re.compile(r'^(?:fastrtps_|sem\.fastrtps_)')


def _shm_inventory():
    """Return the set of names currently in ``/dev/shm`` (missing dir -> empty)."""
    try:
        return set(os.listdir('/dev/shm'))
    except OSError:
        return set()


def _held_by_a_live_process(path):
    """Return True iff some live process still holds ``path`` open or mapped."""
    try:
        result = subprocess.run(
            ['fuser', path], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return result.returncode == 0


def _worktree_marker():
    """Return the root path identifying THIS worktree."""
    prefix = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return prefix.rsplit('/src/', 1)[0]


def _node_path_markers():
    """Path markers that appear only in THIS worktree's launched nodes."""
    root = _worktree_marker()
    return (os.path.join(root, 'install'),
            os.path.join(root, '.pixi', 'envs', 'default', 'lib'))


def _is_our_process(pid):
    """Return True iff ``pid`` is one of THIS worktree's ROS processes."""
    if pid == os.getpid():
        return False
    try:
        with open('/proc/%d/cmdline' % pid, 'rb') as handle:
            cmdline = handle.read().decode('utf-8', 'replace')
    except OSError:
        return False
    return any(marker in cmdline for marker in _node_path_markers())


def _stray_our_processes():
    """Return the PIDs of every live process of THIS worktree (minus us)."""
    strays = set()
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if _is_our_process(pid):
            strays.add(pid)
    return strays


def _reap_orphans():
    """Kill this worktree's launch children that escaped the process group.

    Safe on a node shared with other ROS users: a PID is reaped only if its
    cmdline names THIS worktree's own paths.  Returns the count reaped.
    """
    culprits = _stray_our_processes()
    for pid in culprits:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if culprits:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and _stray_our_processes():
            time.sleep(0.2)
    return len(culprits)


def _sweep_stale_shm():
    """Remove *all* unheld FastDDS SHM artifacts before a launch.

    Safe on a shared node: any file held by a live process is skipped
    (``fuser``), so a concurrent ROS user's segments are never touched.
    """
    removed = 0
    for name in sorted(_shm_inventory()):
        if not _SHm_NAME_RE.match(name):
            continue
        path = os.path.join('/dev/shm', name)
        if _held_by_a_live_process(path):
            continue
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


def _cleanup_shm(before):
    """Remove only the fastrtps SHM artifacts *this* session created."""
    removed = 0
    for _ in range(10):
        pending = 0
        for name in _shm_inventory() - before:
            if not _SHm_NAME_RE.match(name):
                continue
            path = os.path.join('/dev/shm', name)
            if _held_by_a_live_process(path):
                pending += 1
                continue
            try:
                os.unlink(path)
                removed += 1
            except OSError:
                pass
        if pending == 0:
            break
        time.sleep(0.5)
    return removed


def _spawn_launch(env, world_state_path):
    """Start ``mujoco.launch.py`` headless as its own process group."""
    log_path = os.path.join(os.path.dirname(world_state_path), 'launch.log')
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_bringup', 'mujoco.launch.py',
         'use_sim_time:=true', 'world_state_path:=%s' % world_state_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=env, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []

    def _pump():
        with open(log_path, 'w') as handle:
            for line in process.stdout:
                output.append(line)
                handle.write(line)
                handle.flush()

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    return process, group, output, reader


def _group_alive(group):
    """Return True iff any process is still in process group ``group``."""
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


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
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and _group_alive(group):
        time.sleep(0.2)


def _write_world_file(path):
    from robot_world import default_seed_document, write_document
    write_document(path, default_seed_document())


def _yaw_from_quaternion(quat):
    """Return the yaw of a geometry_msgs Quaternion."""
    siny = 2.0 * (quat.w * quat.z + quat.x * quat.y)
    cosy = 1.0 - 2.0 * (quat.y * quat.y + quat.z * quat.z)
    return math.atan2(siny, cosy)


def _run_probe_in_subprocess(queue, domain_id):
    """Child-process entry point: run the semantic probe onto ``queue``."""
    import traceback

    try:
        result = _semantic_probe_worker(domain_id)
        queue.put(('ok', result))
    except BaseException:  # noqa: BLE001 - relay everything to the parent
        queue.put(('error', traceback.format_exc()))


def _semantic_probe(domain_id):
    """Run the semantic navigate probe in its **own process** and return it.

    Same isolation contract as ``test_pr2_navigate.py`` (own process + own
    ``ROS_DOMAIN_ID``, bounded bringup retry on a fresh domain): FastDDS' SHM
    transport is a process-global singleton, so a second rclpy session in one
    interpreter cannot publish.
    """
    import multiprocessing

    last_error = None
    for attempt in range(_BRINGUP_ATTEMPTS):
        context = multiprocessing.get_context('spawn')
        queue = context.Queue()
        child = context.Process(
            target=_run_probe_in_subprocess,
            args=(queue, domain_id + attempt))
        child.start()
        try:
            kind, payload = queue.get(
                timeout=GOAL_TIMEOUT_S * 2 + LAUNCH_READY_TIMEOUT_S + 120)
        finally:
            child.join(timeout=30)
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)
        if kind == 'ok':
            return payload
        last_error = payload
        if not _is_transient_bringup_failure(payload):
            break
        if attempt + 1 < _BRINGUP_ATTEMPTS:
            print('[pr3-nav] bringup attempt %d/%d failed transiently; '
                  'relaunching on a fresh domain' % (attempt + 1,
                                                     _BRINGUP_ATTEMPTS))
    raise AssertionError(last_error)


def _is_transient_bringup_failure(text):
    """Return True iff ``text`` shows a retryable bringup/readiness failure.

    Mirrors ``test_pr2_navigate.py``'s list: the Nav2 lifecycle DDS-discovery
    abort, FastDDS SHM port contention, and readiness timeouts.  The *semantic*
    assertions (a refused goal, a converged-but-wrong pose, a stale world) are
    deliberately NOT matched, so a real defect is not retried away.
    """
    if text is None:
        return False
    markers = (
        'bt_navigator/get_state service client: async_send_request failed',
        'Failed to bring up all requested nodes. Aborting bringup',
        'Failed init_port fastrtps_port',
        'open_and_lock_file failed',
        'bt_navigator never became ACTIVE',
        'base_velocity_controller never became active',
        '/mujoco_get_body_state not available',
        'GetBodyState(base_link) failed',
        'the semantic bridge never came up',
        'no output',
    )
    return any(marker in text for marker in markers)


def _semantic_probe_worker(domain_id):
    """Launch the bringup, call ``NavigateToLocation('kitchen')``, return the outcome.

    Spawns the *shipped* ``mujoco.launch.py`` headless on an isolated domain
    (the same DDS / ``/dev/shm`` hardening and readiness gates as PR2's probe),
    sends a real ``robot_nav_interfaces/action/NavigateToLocation`` goal naming
    ``kitchen`` on ``/navigate_to_location``, waits for it to finish, then reads
    both ground truths:

    * the base pose via ``GetBodyState('base_link')`` (accumulated yaw across
      the run, settle after), and
    * ``start_location`` via ``/world_query/get_world``.

    Returns ``(dx, dy, dyaw, action_success, action_error, start_location)`` of
    the ground-truth base pose relative to the start.
    """
    import rclpy
    from controller_manager_msgs.srv import ListControllers
    from lifecycle_msgs.msg import State
    from lifecycle_msgs.srv import GetState
    from mujoco_ros2_control.srv import GetBodyState
    from rclpy.action import ActionClient
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from robot_nav_interfaces.action import NavigateToLocation
    from robot_world import WorldDocument
    from robot_world_ros_interfaces.srv import GetWorld
    import json

    directory = tempfile.mkdtemp(prefix='pr3_nav_e2e_')
    world_path = os.path.join(directory, 'world.json')
    _write_world_file(world_path)

    domain = str(domain_id)
    env = dict(os.environ, ROS_DOMAIN_ID=domain)
    _reap_orphans()
    _sweep_stale_shm()
    shm_before = _shm_inventory()
    process, group, output, reader = _spawn_launch(env, world_path)
    os.environ['ROS_DOMAIN_ID'] = domain
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('pr3_semantic_e2e_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)

        def _logs():
            text = ''.join(output)
            if not text:
                return '<no output>'
            return text[-8000:]

        _ABORT_MARKERS = (
            'Failed to bring up all requested nodes. Aborting bringup',
            'process has died',
        )

        def _bringup_aborted():
            text = ''.join(output)
            return any(marker in text for marker in _ABORT_MARKERS)

        def _lifecycle_active(node_name):
            client = node.create_client(GetState, '/%s/get_state' % node_name)
            deadline = time.monotonic() + LAUNCH_READY_TIMEOUT_S
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.1)
                if _bringup_aborted():
                    return False
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

        # The base velocity controller must be accepting wheel commands.
        cm_client = node.create_client(
            ListControllers, '/controller_manager/list_controllers')
        deadline = time.monotonic() + LAUNCH_READY_TIMEOUT_S
        base_active = False
        while time.monotonic() < deadline:
            executor.spin_once(timeout_sec=0.1)
            if _bringup_aborted():
                break
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

        # The semantic bridge's action server (PR3's own seam).
        action_client = ActionClient(
            node, NavigateToLocation, 'navigate_to_location')
        assert action_client.wait_for_server(timeout_sec=60.0), (
            'the semantic bridge never came up at /navigate_to_location\n%s'
            % _logs())

        goal = NavigateToLocation.Goal()
        goal.location = TARGET_LOCATION
        send_future = action_client.send_goal_async(goal)
        executor.spin_until_future_complete(send_future, timeout_sec=30.0)
        assert send_future.done() and send_future.result() is not None, (
            'NavigateToLocation goal was not accepted\n%s' % _logs())
        goal_handle = send_future.result()
        assert goal_handle.accepted, (
            'NavigateToLocation goal rejected\n%s' % _logs())

        def _wrap(angle):
            return math.atan2(math.sin(angle), math.cos(angle))

        # Accumulate the yaw **incrementally** across the whole run, exactly
        # like test_pr2_navigate.py's _goal_probe_worker: each per-sample delta
        # is far below pi, so it is unambiguous (the #125 pitfall).
        tracked_yaw = _yaw_from_quaternion(start.orientation)
        dyaw = 0.0

        def _accumulate(pose):
            nonlocal dyaw, tracked_yaw
            if pose is None:
                return
            nxt = _yaw_from_quaternion(pose.orientation)
            dyaw += _wrap(nxt - tracked_yaw)
            tracked_yaw = nxt

        result_future = goal_handle.get_result_async()
        deadline = time.monotonic() + GOAL_TIMEOUT_S
        while time.monotonic() < deadline and not result_future.done():
            executor.spin_once(timeout_sec=0.1)
            _accumulate(_base_state())
        action_success = False
        action_error = 'the semantic goal did not finish in time'
        if result_future.done():
            result = result_future.result().result
            action_success = bool(result.success)
            action_error = result.error

        # Let the base settle before measuring the pose.
        final = _base_state()
        _accumulate(final)
        stable_since = time.monotonic()
        settle_deadline = time.monotonic() + SETTLE_PERIOD_S * 5.0
        previous = None
        while time.monotonic() < settle_deadline:
            executor.spin_once(timeout_sec=0.1)
            pose = _base_state()
            if pose is None:
                continue
            _accumulate(pose)
            if previous is not None:
                moved = math.hypot(
                    pose.position.x - previous.position.x,
                    pose.position.y - previous.position.y)
                if moved < 1e-3:
                    if time.monotonic() - stable_since >= SETTLE_PERIOD_S:
                        final = pose
                        break
                else:
                    stable_since = time.monotonic()
            previous = pose
            final = pose

        # -- the query -> nav -> query round trip: has start_location moved?
        world_client = node.create_client(GetWorld, '/world_query/get_world')
        start_location = None
        if world_client.wait_for_service(timeout_sec=30.0):
            future = world_client.call_async(GetWorld.Request())
            executor.spin_until_future_complete(future, timeout_sec=10.0)
            if future.done() and future.result() is not None:
                try:
                    document = WorldDocument.from_dict(
                        json.loads(future.result().world_json))
                    start_location = document.start_location
                except (ValueError, TypeError):
                    start_location = None

        return (final.position.x - start.position.x,
                final.position.y - start.position.y,
                dyaw,
                action_success,
                action_error,
                start_location)
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        _terminate_group(process, group)
        _reap_orphans()
        removed = _cleanup_shm(shm_before)
        if removed:
            print('[pr3-shm] reclaimed %d FastDDS /dev/shm segment(s)' % removed)


def test_navigate_to_location_drives_the_base_and_updates_the_world():
    """The semantic bridge drives to kitchen AND records the arrival (R1/R3).

    The whole point of PR3: the brain's *semantic* goal -- a location name --
    drives the base substantially toward that location on the classical track,
    and the world's ``start_location`` follows.  Asserts, against ground truth:

    * ``NavigateToLocation('kitchen')`` reports ``success=True``;
    * the base *substantially drove toward* kitchen's reference pose
      ``(2.0, 0.0)`` -- forward progress ``dx > MIN_ARRIVAL_DX``, lateral drift
      ``abs(dy) < MAX_ARRIVAL_ABS_DY`` and heading held ``abs(dyaw) <
      MAX_ARRIVAL_ABS_DYAW`` (kitchen is straight ahead of the origin start, so
      this is the forward channel);
    * ``/world_query/get_world`` now reports ``start_location='kitchen'``.

    NOTE: strict xy-convergence (``error_xy < 0.10``) is *deferred* to the
    MPPI / rim-roller-plant convergence follow-up -- the same deferral
    ``test_pr2_navigate.py`` records (that plant systematically undershoots
    ~8%, so PR2 asserts only direction).  This test's acceptance is the
    semantic contract + "drove substantially toward the location", NOT a
    converged pose; it deliberately does not assert convergence.

    Own ROS domain (base 128), so it never shares a FastDDS SHM namespace with
    ``test_pr2_navigate.py``'s 124..127.
    """
    if not _have_package('mujoco_ros2_control'):
        pytest.skip(
            'mujoco_ros2_control (dfki-ric, source-build via robot.repos, D33) '
            'is not installed; the semantic nav acceptance needs the live sim. '
            'Build it with: `vcs import src < robot.repos && pixi run build`.')

    dx, dy, dyaw, succeeded, error, start_location = _semantic_probe(NAV2_DOMAIN_ID)
    error_xy = math.hypot(dx - KITCHEN_X, dy - KITCHEN_Y)
    print('[pr3-nav] semantic probe: dx=%.3f dy=%.3f dyaw=%.3f succeeded=%s '
          'xy_err=%.3f (diagnostic only; strict convergence deferred) '
          'start_location=%r error=%r'
          % (dx, dy, dyaw, succeeded, error_xy, start_location, error))

    assert succeeded, (
        "NavigateToLocation('%s') reported failure: %r (dx=%.3f dy=%.3f "
        'xy_err=%.3f)' % (TARGET_LOCATION, error, dx, dy, error_xy))
    assert (dx > MIN_ARRIVAL_DX
            and abs(dy) < MAX_ARRIVAL_ABS_DY
            and abs(dyaw) < MAX_ARRIVAL_ABS_DYAW), (
        "NavigateToLocation('%s') reported success but the base did not reach "
        "kitchen's neighbourhood (%.2f, %.2f): dx=%.3f (needs > %.2f) "
        'dy=%.3f dyaw=%.3f (needs |dy| < %.2f and |dyaw| < %.2f)'
        % (TARGET_LOCATION, KITCHEN_X, KITCHEN_Y, dx, MIN_ARRIVAL_DX, dy, dyaw,
           MAX_ARRIVAL_ABS_DY, MAX_ARRIVAL_ABS_DYAW))
    assert start_location == TARGET_LOCATION, (
        'the query -> nav -> query round trip failed: /world_query/get_world '
        'reports start_location=%r after navigating to %r'
        % (start_location, TARGET_LOCATION))
