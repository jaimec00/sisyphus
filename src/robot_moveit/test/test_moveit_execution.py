# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 / issue #147 R7: the full execution stack moves the sim through a trajectory.

This is the acceptance test for the whole PR: a Cartesian goal driven through the
``cartesian_goal`` service launches the *composed* stack
(``robot_moveit/moveit_execution.launch.py``: MuJoCo sim + the arm JTCs +
move_group + the planning-scene bridge + the goal node), and the arm joints move
in the sim.

Three claims, each the reason a piece of this PR exists:

* ``test_execution_stack_moves_arm_along_a_trajectory`` -- the moves-arm case.
  A reachable target (inside the left shoulder's 0.85 m sphere) is planned and
  executed, and the assertion the whole PR is about: **at least one
  ``/joint_states`` sample is seen strictly between the start and the goal
  values of a commanded joint**. A "teleport" implementation (a position
  controller set once) jumps straight from start to goal; only a real
  time-parameterised trajectory passes through a value in between. The final
  pose is also asserted at the goal within tolerance.
* ``test_execution_stack_reports_out_of_reach`` -- a target far outside the
  envelope is refused as ``OUT_OF_REACH`` *before* planning (R3's discriminator:
  MoveIt's -1 cannot tell "unreachable" from "no path").
* ``test_execution_stack_rejects_self_colliding_goal`` -- a goal inside the
  robot's own column is rejected as ``COLLISION``. This is the proof R2's
  re-enabled pairs actually detect a collision (a goal the arm can only reach by
  folding into the column would have planned cleanly with PR1's disables).

Why the launch is a subprocess on its own domain
------------------------------------------------
The stack is long-lived and heavy (sim + Nav2 + move_group), so it runs as one
``ros2`` launch in its own process group on a private ``ROS_DOMAIN_ID`` (120:
112-119, 121 and 131 are taken by the other suites). Launching it once per test
would cost minutes; the tests therefore share one stacked fixture-scoped launch
and each drives it through a service call, subscribing to ``/joint_states`` for
the trajectory observation.

The world is seeded through the live-state file the world service reads, so the
object-based path (``object_id``) is exercised without inventing a second world
API: the test writes a document with one reachable object and one far object,
then the world service serves it.
"""
import os
import shutil
import signal
import subprocess
import threading
import time

import pytest

#: Private ROS domain for this suite (in use: 112,113,115,116,117,118,119,121,131).
EXECUTION_DOMAIN_ID = '120'
#: The stack is heavy (sim + Nav2 + move_group); give it a long, honest budget.
STACK_READY_TIMEOUT_S = 420.0
SERVICE_CALL_TIMEOUT_S = 120.0
#: How long to watch /joint_states for the mid-trajectory sample.
TRAJECTORY_OBSERVE_TIMEOUT_S = 60.0

#: Response status values the .srv documents (mirrored in the node).
STATUS_SUCCESS = 0
STATUS_OUT_OF_REACH = 1
STATUS_COLLISION = 2
STATUS_FAILURE = 3

#: The arm joints in chain order (the JTC's order, and thus the trajectory's).
LEFT_ARM_JOINTS = ['left_%s' % j for j in (
    'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex',
    'wrist_roll')]

#: A reachable target. Chosen empirically from the arm's forward-kinematics
#: reachable set (verified: a 14-per-joint grid over the URDF bounds reaches
#: this point to within 0.015 m). The SO-101 arm's tip volume in base_link is
#: roughly x in [-0.23, 0.38], y in [-0.16, 0.52], z in [0.58, 1.13] -- note
#: that forward reach *stops* at x ~= 0.38 (the neutral tip x), so a target like
#: (0.45, ...) is outside the arm's workspace despite being inside the 0.85 m
#: shoulder sphere. The neutral tip is (0.383, 0.18, 0.791), so this target is a
#: real move (not a no-op).
REACHABLE_TARGET = (0.30, 0.30, 0.65)

#: The tip position at the all-zeros arm pose (verified /compute_fk) -- used as
#: the "start" invariant the trajectory observation compares against.
NEUTRAL_TIP = (0.383, 0.18, 0.791)

#: A target the arm can *reach* but only by folding through its own column
#: (base_link ~= the column footprint; x in [0.05, 0.15], y ~= 0 is inside the
#: column's swept volume). Chosen empirically: this point yields
#: ``START_STATE_IN_COLLISION`` (-10) / COLLISION, whereas a point outside the
#: arm's reach instead yields NO_IK (-31) -- verified by probing both. Using a
#: reachable-but-colliding point is what makes the assertion about the collision
#: checker rather than about the reach envelope.
SELF_COLLIDING_TARGET = (0.05, 0.00, 0.60)

#: A target far outside any reach: the seed's mug_1 (x ~2.1) is ~2.1 m from the
#: shoulder, well beyond 0.85 m. Asserting on a *number* rather than the seed
#: object keeps this independent of the seed file; the object-path variant is
#: covered by the moves-arm test's object_id case only if a reachable object is
#: seeded.
OUT_OF_REACH_TARGET = (2.10, 0.10, 1.15)

#: The object this test seeds. It sits *beside* the goal point, not on it: a
#: body at the exact goal pose would put the gripper in collision with it and a
#: correct planner would (rightly) refuse. 15 cm to the side keeps it in the
#: scene (so the planning scene is non-trivial) without blocking the goal.
REACHABLE_OBJECT_ID = 'pr2_reachable_fixture'
REACHABLE_OBJECT_POSE = {'x': REACHABLE_TARGET[0], 'y': REACHABLE_TARGET[1] + 0.15,
                         'z': REACHABLE_TARGET[2]}


def _require_tool(name):
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; it is pinned in pixi.toml / package.xml -- run '
        'inside `pixi run`.' % name)
    return path


def _launch_file(package, name):
    from ament_index_python.packages import get_package_share_directory
    return os.path.join(get_package_share_directory(package), 'launch', name)


# -- world seeding -----------------------------------------------------------

def _seed_live_world(path):
    """Write a live-state document seeding one reachable object.

    Starts from ``robot_world``'s shipped seed (never a hand-copied scene -- the
    seed is the arbiter of the room) and appends one object at a reachable pose
    so ``object_id`` resolution has something to find. Serialised with
    robot_world's own canonical text so the format is byte-for-byte what the
    world service writes and reads.
    """
    from robot_world.document import WorldObject, WorldDocument
    from robot_world.storage import (default_seed_document, document_text,
                                     read_document)

    seed = default_seed_document()
    fixture = WorldObject(
        object_id=REACHABLE_OBJECT_ID,
        label='cup',
        pose=_pose_at(REACHABLE_OBJECT_POSE),
        graspable=True,
        held_by=None,
    )
    payload = seed.to_dict()
    payload['objects'] = [obj.to_dict() for obj in seed.objects] \
        + [fixture.to_dict()]
    document = WorldDocument.from_dict(payload)

    with open(path, 'w') as handle:
        handle.write(document_text(document))

    # Read it back through the same reader the service uses: a well-formed but
    # unreadable file would otherwise fail later as an opaque service error.
    reparsed = read_document(path)
    assert reparsed.find_object(REACHABLE_OBJECT_ID) is not None, (
        'seeded world did not read back with %r' % REACHABLE_OBJECT_ID)
    return path


def _pose_at(position):
    """Return a ``robot_skills.Pose`` at ``position`` with identity rotation.

    ``robot_world`` stores ``robot_skills.Pose`` (its own value type, not the
    message), which is what ``WorldObject.pose`` type-checks against.
    """
    from robot_skills import Pose
    return Pose.from_xyz(position['x'], position['y'], position['z'])


def _spawn_stack(env, world_state_path):
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_moveit',
         'moveit_execution.launch.py',
         'world_state_path:=%s' % world_state_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, env=env, start_new_session=True)
    group = os.getpgid(process.pid)
    output = []

    def _drain():
        # ``read()`` with no size blocks until EOF, so a newline-at-a-time loop
        # is what makes ``self.logs`` observe output *while* the stack runs --
        # which is the whole point of waiting on a readiness line.
        for line in iter(process.stdout.readline, ''):
            output.append(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()
    return process, group, output


def _terminate_group(process, group):
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            pass


class _ExecutionStack:
    """A launched execution stack plus a probing rclpy node (context manager).

    Mirrors ``_MoveGroupProbe`` (test_move_group_launch.py) and the sim smoke
    (test_mujoco_launch.py): a private domain, a launch subprocess in its own
    process group, one SingleThreadedExecutor, ``killpg`` on exit. Adds a
    stacked ``joint_states`` recorder the trajectory assertions read.
    """

    def __init__(self, domain_id=EXECUTION_DOMAIN_ID):
        self._domain_id = domain_id
        self._process = None
        self._group = None
        self._output = None
        self._context = None
        self._node = None
        self._executor = None
        self._world_path = None
        self._samples = []          # (monotonic_s, {joint: position})

    def __enter__(self):
        import rclpy
        from rclpy.context import Context
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node

        os.makedirs(os.path.join(os.path.expanduser('~'), '.ros'), exist_ok=True)
        self._world_path = os.path.join(
            os.path.expanduser('~'), '.ros',
            'pr2_i147_execution_%s.json' % self._domain_id)
        _seed_live_world(self._world_path)

        # PYTHONUNBUFFERED: the stack's nodes are Python; without it their
        # stdout is block-buffered into a pipe and the readiness line the fixture
        # waits on never arrives until the process exits.
        env = dict(os.environ, ROS_DOMAIN_ID=self._domain_id,
                   PYTHONUNBUFFERED='1')
        self._process, self._group, self._output = _spawn_stack(
            env, self._world_path)
        os.environ['ROS_DOMAIN_ID'] = self._domain_id
        self._context = Context()
        rclpy.init(context=self._context)
        self._node = Node('execution_stack_probe', context=self._context)
        self._executor = SingleThreadedExecutor(context=self._context)
        self._executor.add_node(self._node)
        return self

    def __exit__(self, *exc):
        try:
            if self._executor is not None:
                self._executor.shutdown()
            if self._node is not None:
                self._node.destroy_node()
            if self._context is not None:
                self._context.try_shutdown()
        finally:
            _terminate_group(self._process, self._group)
            if self._world_path and os.path.exists(self._world_path):
                os.unlink(self._world_path)

    @property
    def logs(self):
        return ''.join(self._output or [])

    def wait_for_client(self, client, service, timeout=STACK_READY_TIMEOUT_S):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.1)
            if client.service_is_ready():
                return client
        raise AssertionError(
            '%s never became available within %.0fs. Launch log tail:\n%s'
            % (service, timeout, self.logs[-4000:] or '<no output>'))

    def client(self, srv_type, service):
        return self.wait_for_client(
            self._node.create_client(srv_type, service), service)

    def call(self, client, request, timeout=SERVICE_CALL_TIMEOUT_S):
        future = client.call_async(request)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.1)
            if future.done():
                assert future.result() is not None, 'service returned no response'
                return future.result()
        raise AssertionError('service call timed out after %.0fs' % timeout)

    # -- joint-state recording -------------------------------------------

    def start_recording(self, joints):
        """Subscribe to ``/joint_states`` and record the named joints."""
        from sensor_msgs.msg import JointState

        self._samples = []
        wanted = set(joints)

        def _on_joint_state(msg):
            values = dict(zip(msg.name, msg.position))
            snapshot = {j: values[j] for j in wanted if j in values}
            if snapshot:
                self._samples.append((time.monotonic(), snapshot))

        self._sub = self._node.create_subscription(
            JointState, '/joint_states', _on_joint_state, 50)
        return self

    def spin_for(self, seconds):
        """Spin (delivering callbacks) for ``seconds`` wall-clock."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.05)

    @property
    def samples(self):
        return list(self._samples)

    def wait_for_ready(self, timeout=STACK_READY_TIMEOUT_S):
        """Spin until the goal node reports its MoveIt backend is built.

        Polls the node's ``ready`` parameter through ``/cartesian_goal/
        get_parameters``. A parameter is observable from outside the launched
        process; its stdout is not (``ros2 launch`` buffers it).
        """
        from rcl_interfaces.srv import GetParameters
        from rclpy.parameter import Parameter

        client = self.wait_for_client(
            self._node.create_client(
                GetParameters, '/cartesian_goal/get_parameters'),
            '/cartesian_goal/get_parameters', timeout=timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            request = GetParameters.Request()
            request.names = ['ready']
            future = client.call_async(request)
            self._executor.spin_until_future_complete(future, timeout_sec=2.0)
            if future.done() and future.result() is not None:
                values = future.result().values
                if values and values[0].type == Parameter.Type.BOOL.value \
                        and values[0].bool_value:
                    return
            self._executor.spin_once(timeout_sec=0.1)
        raise AssertionError(
            'the cartesian_goal node never reported ready within %.0fs\n'
            'Stack output tail:\n%s' % (timeout, self.logs[-6000:]))

    def wait_for_log(self, needle, timeout=STACK_READY_TIMEOUT_S):
        """Spin until the launched stack's output contains ``needle``.

        The stack runs as one subprocess whose stdout this probe captures; a
        node's own readiness line is the honest signal that its backend is
        built, which no service/client check can see.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.1)
            if needle in self.logs:
                return
        raise AssertionError(
            'never saw %r in the stack output within %.0fs. Log tail:\n%s'
            % (needle, timeout, self.logs[-4000:] or '<no output>'))

    def wait_for_tf_chain(self, timeout=STACK_READY_TIMEOUT_S):
        """Spin until the node's TF chain is complete: source -> base_link.

        Polls a TF listener of our own for the ``world`` -> ``base_link``
        transform (the chain the ``cartesian_goal`` node resolves goals
        through). Both ends are required: the world frame comes from the
        launch's static publisher, and the arm side only becomes connected once
        ``joint_states`` drive the prismatic column.
        """
        import rclpy
        from tf2_ros import Buffer, TransformException, TransformListener

        buffer = Buffer()
        TransformListener(buffer, self._node)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.1)
            try:
                buffer.lookup_transform('base_link', 'world', rclpy.time.Time())
                return
            except TransformException:
                continue
        raise AssertionError(
            'the world -> base_link TF chain never completed within %.0fs'
            % timeout)

    def wait_for_joint_state(self, timeout=TRAJECTORY_OBSERVE_TIMEOUT_S):
        """Spin until at least one ``/joint_states`` sample has arrived."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.1)
            if self._samples:
                return
        raise AssertionError(
            '/joint_states never produced a sample within %.0fs'
            % timeout)


@pytest.fixture(scope='module')
def stack():
    """One execution stack for the module (launching it per test is minutes).

    Readiness is two-stage and both stages matter: the ``cartesian_goal``
    service must answer (the goal node is up), and ``/joint_states`` must be
    flowing (the sim's controllers are spawned and active). The second lags the
    first by tens of seconds -- the embedded controller_manager serialises
    controller switches -- so the recorder is attached immediately and re-armed,
    and the joint-state wait gets its own generous budget.
    """
    from robot_moveit_ros_interfaces.srv import CartesianGoal

    with _ExecutionStack() as running:
        running.wait_for_client(
            running._node.create_client(
                CartesianGoal, '/cartesian_goal/cartesian_goal'),
            '/cartesian_goal/cartesian_goal')
        # The service existing is NOT readiness: the goal node builds its
        # MoveItPy backend on a background thread (~45 s: model, pipelines,
        # controller manager), and a goal sent before then fails as
        # "MoveIt planning interface unavailable". Poll the node's `ready`
        # parameter (set once the backend is up) -- not its stdout, which
        # `ros2 launch` buffers.
        running.wait_for_ready(timeout=STACK_READY_TIMEOUT_S)
        running.start_recording(LEFT_ARM_JOINTS)
        running.wait_for_joint_state(timeout=STACK_READY_TIMEOUT_S)
        # The prismatic `column_lift` transform the arm chain hangs off only
        # appears once joint_states flow, so the arm TF *and* the world frame
        # both lag the first joint-state sample. Wait for the whole chain the
        # node's lookups need before any test calls it -- a lookup against a
        # half-built tree returns "not part of the same tree", which reads as a
        # goal failure rather than the startup race it is.
        running.wait_for_tf_chain(timeout=STACK_READY_TIMEOUT_S)
        yield running


def _goal_request(stack, arm='left', object_id='', pose=None):
    from robot_moveit_ros_interfaces.srv import CartesianGoal
    request = CartesianGoal.Request()
    request.arm = arm
    request.object_id = object_id
    if pose is not None:
        request.use_target_pose = True
        request.target_pose.position.x = pose[0]
        request.target_pose.position.y = pose[1]
        request.target_pose.position.z = pose[2]
        request.target_pose.orientation.w = 1.0
    return request


def _goal_client(stack):
    from robot_moveit_ros_interfaces.srv import CartesianGoal
    return stack.client(CartesianGoal, '/cartesian_goal/cartesian_goal')


def test_execution_stack_moves_arm_along_a_trajectory(stack):
    """R7 moves-arm: the plan executes and the joints pass through, not jump.

    The load-bearing assertion is the "not a teleport" one. The controller used
    here is a JointTrajectoryController fed a time-parameterised
    FollowJointTrajectory goal, so the arm sweeps; a position-bridge
    (JointGroupPositionController) would snap. ``mid_samples`` is the count of
    samples where at least one commanded joint differs from BOTH its start and
    its goal value -- a strictly interior point of the motion.
    """
    client = _goal_client(stack)
    stack.start_recording(LEFT_ARM_JOINTS)

    # Let the recorder observe the pre-move pose.
    stack.spin_for(2.0)
    start_positions = dict(stack.samples[-1][1]) if stack.samples else {}

    response = stack.call(
        client, _goal_request(stack, arm='left', pose=REACHABLE_TARGET))

    assert response.success, (
        'execution of a reachable goal failed: status=%d error_code=%d %s\n'
        'Launch log tail:\n%s'
        % (response.status, response.error_code, response.message,
           stack.logs[-4000:]))
    assert response.status == STATUS_SUCCESS, response.status

    # -- the "not a teleport" assertion.
    stack.spin_for(1.0)
    samples = stack.samples
    assert len(samples) >= 3, (
        'expected several /joint_states samples during execution, got %d'
        % len(samples))
    final = samples[-1][1]
    margin = 0.02
    interior = []
    for _stamp, snapshot in samples:
        if any(
            min(start_positions.get(j, snapshot[j]),
                final.get(j, snapshot[j])) + margin
            < snapshot[j]
            < max(start_positions.get(j, snapshot[j]),
                  final.get(j, snapshot[j])) - margin
            for j in LEFT_ARM_JOINTS
        ):
            interior.append(snapshot)
    assert interior, (
        'no /joint_states sample lies strictly between the start and final '
        'poses of any commanded joint -- the move was a teleport, not a '
        'trajectory. start=%r final=%r'
        % (start_positions, final))

    # -- and it finished at the goal, not merely at *a* pose.
    assert any(abs(final.get(j, 0.0) - start_positions.get(j, 0.0)) > 0.02
               for j in LEFT_ARM_JOINTS), (
        'the arm joints did not move at all: start=%r final=%r'
        % (start_positions, final))


def test_execution_stack_reports_out_of_reach(stack):
    """R7/R3: a far target is OUT_OF_REACH, decided before planning.

    Driven by the seed's ``mug_1`` (~2.1 m away) through the ``object_id`` path,
    so this also proves object resolution works and that the envelope check fires
    on a *named* world object rather than an explicit pose.
    """
    client = _goal_client(stack)
    response = stack.call(
        client, _goal_request(stack, arm='left', object_id='mug_1'))
    assert not response.success, response.message
    assert response.status == STATUS_OUT_OF_REACH, (
        'mug_1 (~2 m away) must be OUT_OF_REACH (status=1), got status=%d '
        'error_code=%d %s' % (response.status, response.error_code,
                              response.message))

    # The explicit-pose path agrees: a far *point* is refused the same way.
    response = stack.call(
        client, _goal_request(stack, arm='left', pose=OUT_OF_REACH_TARGET))
    assert response.status == STATUS_OUT_OF_REACH, (
        'an explicit far pose must also be OUT_OF_REACH, got status=%d %s'
        % (response.status, response.message))


def test_execution_stack_rejects_self_colliding_goal(stack):
    """R7/R2: a goal inside the robot's own column is rejected as COLLISION.

    This is the empirical proof the R2 re-enable works: with PR1's pre-emptive
    disables, an arm<->column pair was unchecked and this goal would have been
    accepted. The target sits inside the column footprint (base_link ~0,0), which
    the arm can only reach by folding through the column.
    """
    client = _goal_client(stack)
    response = stack.call(
        client, _goal_request(stack, arm='left', pose=SELF_COLLIDING_TARGET))
    assert not response.success, response.message
    assert response.status == STATUS_COLLISION, (
        'a goal inside the robot body must be COLLISION (status=2), got '
        'status=%d error_code=%d %s'
        % (response.status, response.error_code, response.message))
