# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The MuJoCo bringup launch (PR8b / issue #92).

The acceptance for PR8b is "sim spawns; ros2_control loads; joints are
commandable (a joint command moves the sim state)". This file holds two kinds
of claim:

- A cheap structural check (test_launch_generates_expected_nodes) that the
  shipped ``mujoco.launch.py`` imports and generates the promised nodes
  (robot_state_publisher, the mujoco_ros2_control node, and the three
  controller spawners), plus that the controller YAML is installed and carries
  the two group controllers.
- A real integration smoke (test_joint_command_moves_sim_state) that launches
  the actual bringup headless, waits for ros2_control to report the
  arm/gripper position controller active, publishes a position command to it,
  and asserts the commanded joint's state moves in the sim (a joint command
  moves the sim state). The 3 wheels are equally commandable via the velocity
  controller; the position-controller smoke is the deterministic representative.

The one-``mj_step`` no-NaN smoke lives at the description layer
(test_mjcf_model.test_one_mj_step_smoke_has_no_nan) and covers the hardware
interface's own stepping; here we additionally prove the sim is live through
the ros2_control stack.

The integration smoke is *conditional*: ``mujoco_ros2_control`` (dfki-ric) is a
source-build dependency pulled in by ``robot.repos`` (D33) and is not present in
a tree that has not run ``vcs import src < robot.repos`` + a build.  When the
plugin is missing the launch cannot come up, so the smoke skips with the build
command in its reason rather than failing on a dependency the developer has not
been asked to install yet.
"""
import os
import shutil
import signal
import subprocess
import threading
import time

import pytest

# ROS imports are deferred into the tests so collection needs no ROS runtime.


#: private ROS domain for this suite.
MUJOCO_DOMAIN_ID = '113'
LAUNCH_READY_TIMEOUT_S = 60.0
CONTROLLER_ACTIVE_TIMEOUT_S = 60.0
JOINT_MOVE_TIMEOUT_S = 30.0

#: controller that the smoke test commands (a position group).
POSITION_CONTROLLER = 'arm_gripper_position_controller'
#: The joint the smoke moves and observes. It must be one of the *group*
#: controller's joints: since PR2/issue #147 the 10 arm revolute joints belong
#: to the per-side JointTrajectoryControllers (left/right_arm_controller), and a
#: ros2_control command interface has exactly one owner, so a bare
#: Float64MultiArray published to the group controller cannot move an arm joint
#: any more.
#:
#: The PRD's R1c suggested ``column_lift`` for this, and it is what the test
#: uses. Measured behaviour on this host: the joint is heavily damped, so the
#: command must be a large fraction of its 0-1.2 m travel and the assertion is
#: "moved appreciably", not "converged" (its MJCF position actuator reaches
#: only part of a step under the bringup's load).
#:
#: ``left_gripper`` was originally tried and rejected here: with the then-shipped
#: undamped kp=100 jaw actuator (issue #156) it saturated after ~0.01 of a step
#: and ignored the command, so it could not exercise a move at all. That finding
#: is now HISTORY -- #156 added the joint-level damping the jaw DOF needed and
#: the driven jaws track their full [-1.5, 0] range in the sim; the dedicated
#: jaw-tracking smoke is ``test_gripper_jaws_track_a_full_range_command`` below.
#: ``column_lift`` stays this smoke's joint because this test's claim is "a joint
#: command moves the sim state through the ros2_control stack", not the jaw
#: contract.
SMOKE_JOINT = 'column_lift'

#: The four jaw joints: both driven jaws and their mirror followers.
JAW_JOINTS = ('left_gripper', 'left_gripper_mirror',
              'right_gripper', 'right_gripper_mirror')
#: The two joints the sim's ``arm_gripper_position_controller`` commands.
DRIVEN_JAW_JOINTS = ('left_gripper', 'right_gripper')
#: The commandable jaw endpoints (rad): q=0 is closed (the fingertips meet), the
#: driven jaws open toward -1.5 and the -1 mimics toward +1.5.
JAW_CLOSED = 0.0
JAW_OPEN = -1.5
#: Tracking tolerance for the jaw smoke (rad). The actuator is a stiff kp=100
#: position loop and the shipped joint damping settles it in ~0.25 s of sim time
#: with no overshoot past the declared limit (see the feature's
#: ``implementation.md``), so this is a measurement tolerance (100 Hz control
#: discretisation), not slack for a stuck joint.
JAW_TRACK_TOL = 0.05
#: Monotonicity tolerance (rad): a leg may not reverse by more than this. The
#: measured reversal is ~1e-5 rad, so any real jam/ring is caught.
JAW_MONOTONIC_TOL = 1e-2


def _require_tool(name):
    """Return the path to an executable on PATH, failing loudly if absent."""
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; pin it in pixi.toml / package.xml and run inside '
        '`pixi run`.' % name)
    return path


def _install_share(pkg):
    from ament_index_python.packages import get_package_share_directory
    return get_package_share_directory(pkg)


def _have_package(pkg):
    """Return True iff ``pkg`` is registered in the ament index."""
    from ament_index_python.packages import PackageNotFoundError
    from ament_index_python.packages import get_package_prefix
    try:
        get_package_prefix(pkg)
    except PackageNotFoundError:
        return False
    return True


def _launch_path():
    return os.path.join(_install_share('robot_bringup'), 'launch',
                        'mujoco.launch.py')


def test_launch_generates_expected_nodes():
    """The shipped MuJoCo launch file imports and declares the promised nodes.

    Cheap, deterministic: imports the installed launch module and exercises
    ``generate_launch_description`` so a broken launch file (bad import,
    missing helper, wrong node) fails fast without needing the ROS graph or a
    GPU. The heavy proof that it actually runs and moves joints is the
    integration smoke in ``test_joint_command_moves_sim_state``.
    """
    import importlib.util
    from launch.actions import RegisterEventHandler
    from launch_ros.actions import Node
    path = _launch_path()
    assert os.path.isfile(path), (
        'mujoco.launch.py not present in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location('robot_bringup_mujoco', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    def _walk(actions):
        # Follows top-level entities AND RegisterEventHandler's on_start list,
        # where the dfki-ric franka-pattern launch nests the controller
        # spawners (they must only start once the sim node is up). Reaches
        # into the private ``_OnActionEventBase__actions_on_event`` (the only
        # reliable way to enumerate an OnProcessStart handler's actions in
        # the pinned launch version; get_sub_entities()/describe() return
        # empty for it).
        for action in actions:
            if isinstance(action, Node):
                yield (getattr(action, 'node_package', '?'),
                       getattr(action, 'node_executable', '?'))
            elif isinstance(action, RegisterEventHandler):
                handler = getattr(action, 'event_handler', None)
                on_start = getattr(handler,
                                   '_OnActionEventBase__actions_on_event', [])
                yield from _walk(on_start)

    flat = ' '.join('%s/%s' % n for n in _walk(description.entities))
    assert 'robot_state_publisher/robot_state_publisher' in flat
    assert 'mujoco_ros2_control/mujoco_ros2_control' in flat
    assert 'controller_manager/spawner' in flat


def test_materialized_mjcf_contains_the_world_objects():
    """The launch's materialize path puts the world objects into the MJCF (#150).

    PR A of #150: the sim's physical world. This is the cheap structural proof
    -- no ROS graph, no sim -- that ``mujoco.launch.py``'s materialize path
    reads a world-state file and splices its object bodies into the derived
    MJCF the sim loads. It drives the launch module's own value-level helper
    (``_materialize_mjcf_for_state``) against the *shipped seed* so the tested
    path is the sim path, then greps the materialized model for the seed's
    object body names AND the finite support surface. The physics proof (rest +
    contact) lives in robot_description's ``test_world_bodies.py``.
    """
    import importlib.util
    import json
    import tempfile

    from robot_world import default_seed_document

    path = _launch_path()
    spec = importlib.util.spec_from_file_location(
        'robot_bringup_mujoco_launch_scene', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    document = default_seed_document()
    assert document.objects, 'the shipped seed has no objects to materialize'

    directory = tempfile.mkdtemp(prefix='i150_world_materialize_')
    world_state = os.path.join(directory, 'world.json')
    with open(world_state, 'w') as handle:
        json.dump(document.to_dict(), handle)
    mjcf_path = os.path.join(directory, 'derived_scene.xml')

    module._materialize_mjcf_for_state(mjcf_path, world_state)
    with open(mjcf_path) as handle:
        text = handle.read()

    # The finite support pads are there ... (one per graspable object -- issue
    # #152 derives them, so the shipped seed's five graspables mean
    # world_support_0..4, not the pre-#152 single surface).
    assert 'name="world_support_0"' in text, (
        'the materialized MJCF has no support surface; world objects would '
        'fall through the sim floor')
    # ... and so is every seed object body.
    for obj in document.objects:
        assert '<body name="%s"' % obj.object_id in text, (
            'world object %r is missing from the materialized MJCF'
            % obj.object_id)

    # A missing/unreadable world file must degrade, not crash: the sim still
    # comes up with the robot + floor only (no exception, no world bodies).
    missing = os.path.join(directory, 'does_not_exist.json')
    fallback_path = os.path.join(directory, 'fallback_scene.xml')
    module._materialize_mjcf_for_state(fallback_path, missing)
    with open(fallback_path) as handle:
        fallback_text = handle.read()
    assert 'name="world_support' not in fallback_text
    for obj in document.objects:
        assert '<body name="%s"' % obj.object_id not in fallback_text
    # The floor is still there, so the robot is still drivable.
    assert 'name="floor"' in fallback_text


#: The five revolute joints per arm, in the chain's order (PR2 / issue #147).
ARM_JOINTS = (
    'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex', 'wrist_roll',
)


def _load_launch_module(tag):
    """Import the installed ``mujoco.launch.py`` under a private module name."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(tag, _launch_path())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _declared_default(description, name):
    """Return the declared string default of a launch argument, or ``None``.

    The pinned launch version normalizes ``default_value`` to a list of
    substitutions; a plain string comes back through ``TextSubstitution.text``.
    """
    from launch.actions import DeclareLaunchArgument
    for action in description.entities:
        if isinstance(action, DeclareLaunchArgument) and action.name == name:
            return ''.join(getattr(sub, 'text', str(sub))
                           for sub in action.default_value)
    return None


def test_world_objects_defaults_to_true():
    """The physical world is **on by default** (issue #152, acceptance 3).

    PR A (#150) gated the collidable world behind ``world_objects:=true``
    because the shipped seed was physically incoherent -- materializing it
    blocked the charger->kitchen path. Issue #152 fixed the seed, so the
    default flips back. This pins both halves of that claim: the *declared*
    default value, and the in-code fallback the OpaqueFunction uses when the
    key is absent from the resolved configuration (which is the default path).
    """
    import json
    import tempfile

    from robot_world import default_seed_document

    module = _load_launch_module('robot_bringup_mujoco_world_default')
    description = module.generate_launch_description()
    assert _declared_default(description, 'world_objects') == 'true', (
        'world_objects must default to true once the seed is coherent')

    # The value-level fallback: a resolved context WITHOUT a world_objects key
    # (a bare launch) must materialize the world, not the bare robot + floor.
    directory = tempfile.mkdtemp(prefix='i152_world_default_')
    world_state = os.path.join(directory, 'world.json')
    with open(world_state, 'w') as handle:
        json.dump(default_seed_document().to_dict(), handle)
    mjcf_path = os.path.join(directory, 'derived_scene.xml')
    context = type('Context', (), {
        'launch_configurations': {'mjcf_path': mjcf_path,
                                  'world_state_path': world_state}})()
    module._materialize_world_objects(context)
    with open(mjcf_path) as handle:
        text = handle.read()
    assert 'name="world_support_0"' in text, (
        'the default path did not materialize the derived support pads')
    assert '<body name="mug_1"' in text, (
        'the default path did not materialize the shipped seed objects')


def test_controllers_yaml_declares_position_and_velocity_groups():
    """The controller params declare the arm JTCs + the two group controllers.

    Derived expectations (restructured by PR2 / issue #147, ruling R1c): the
    arm revolute joints moved out of ``arm_gripper_position_controller`` into a
    per-side ``joint_trajectory_controller/JointTrajectoryController``, which is
    what MoveIt's trajectory execution manager drives. So:
      * ``arm_gripper_position_controller`` is now the 3 remaining
        position-commanded joints (column_lift + 2 driven grippers);
      * ``left_arm_controller``/``right_arm_controller`` are JTCs over exactly
        the 5 revolute joints of their side, with a ``position`` command and
        state interface;
      * ``base_velocity_controller`` is unchanged (3 wheels).
    The two gripper-mirror joints are absent (they are mimics, R-PR8b-6).

    The JTC assertions are deliberately exact about the joint *sets*: a stray
    arm joint left in the group controller would be a claim conflict at spawn
    time (one command interface, one owner), and a mirror joint in a JTC would
    plan a DOF the URDF's ``<mimic>`` immediately overwrites.
    """
    import yaml
    base = _install_share('robot_bringup')
    with open(os.path.join(base, 'params', 'controllers.yaml')) as f:
        cfg = yaml.safe_load(f)
    pos = cfg['arm_gripper_position_controller']['ros__parameters']
    vel = cfg['base_velocity_controller']['ros__parameters']
    assert pos['type'] == 'position_controllers/JointGroupPositionController'
    assert vel['type'] == 'velocity_controllers/JointGroupVelocityController'
    position_joints = pos['joints']
    velocity_joints = vel['joints']
    assert len(position_joints) == 3, position_joints
    assert len(velocity_joints) == 3, velocity_joints
    assert set(position_joints) == {
        'column_lift', 'left_gripper', 'right_gripper'}, position_joints
    assert 'left_gripper_mirror' not in position_joints
    assert set(velocity_joints) == {
        'base_left_wheel', 'base_back_wheel', 'base_right_wheel'}

    # -- the per-side JTCs own the arm DOF.
    for side in ('left', 'right'):
        jtc = cfg['%s_arm_controller' % side]['ros__parameters']
        assert jtc['type'] == (
            'joint_trajectory_controller/JointTrajectoryController')
        assert jtc['command_interfaces'] == ['position']
        assert jtc['state_interfaces'] == ['position']
        assert jtc['joints'] == ['%s_%s' % (side, j) for j in ARM_JOINTS], (
            '%s_arm_controller joints drifted from the 5 arm revolute joints: '
            '%r' % (side, jtc['joints']))

        # The embedded CM instantiates by name from the top-level block
        # (dfki-ric franka pattern, R-PR8b-14): a name only in the per-node
        # block would never be loadable.
        declared = cfg['controller_manager']['ros__parameters']
        assert declared['%s_arm_controller' % side]['type'] == (
            'joint_trajectory_controller/JointTrajectoryController')

    # The arm joints belong to exactly one controller: the group controller's
    # set and the JTCs' sets are disjoint and together cover all 10.
    jtc_joints = set()
    for side in ('left', 'right'):
        jtc_joints |= set(
            cfg['%s_arm_controller' % side]['ros__parameters']['joints'])
    assert len(jtc_joints) == 10, jtc_joints
    assert jtc_joints.isdisjoint(set(position_joints)), (
        'a joint is claimed by both the group controller and an arm JTC: %r'
        % (jtc_joints & set(position_joints)))


def _spawn_launch(env):
    """Start the real MuJoCo bringup as a subprocess (process-group teardown)."""
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_bringup', 'mujoco.launch.py',
         'use_sim_time:=true'],
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


def _wait_until(predicate, timeout_s, on_timeout):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.25)
    raise AssertionError('%s (last=%r)' % (on_timeout, last))


def _position_controller_joint_order():
    """Return the position controller's joint order from the installed YAML."""
    import yaml
    with open(os.path.join(_install_share('robot_bringup'),
                           'params', 'controllers.yaml')) as f:
        return (yaml.safe_load(f)
                [POSITION_CONTROLLER]['ros__parameters']['joints'])


def test_joint_command_moves_sim_state():
    """End-to-end: sim spawns, ros2_control loads, a joint command moves state.

    Launches the real bringup headless; waits until controller_manager reports
    ``arm_gripper_position_controller`` active; reads the joint's current
    position; publishes a small position offset to that controller's commands
    topic; and asserts the joint position changes in the sim. This is the
    PR8b acceptance criterion exercised through the shipped artifact.
    """
    if not _have_package('mujoco_ros2_control'):
        pytest.skip(
            'mujoco_ros2_control (dfki-ric, source-build via robot.repos, D33) '
            'is not installed; the full-sim bringup smoke needs it live. '
            'Build it with: `vcs import src < robot.repos && pixi run build`.')
    import rclpy
    from controller_manager_msgs.srv import ListControllers
    from rclpy.node import Node
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Float64MultiArray

    env = dict(os.environ, ROS_DOMAIN_ID=MUJOCO_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env)
    try:
        # The probe must share the launched sim's ROS_DOMAIN_ID (MUJOCO_DOMAIN_ID),
        # not whatever domain the pytest host happens to be on, or it can never
        # see the controller_manager or /joint_states on the sim's domain.
        os.environ['ROS_DOMAIN_ID'] = str(MUJOCO_DOMAIN_ID)
        # Use a dedicated rclpy context so this test never collides with other
        # tests in the same pytest process that also call rclpy.init() (which
        # raises "Context.init() must only be called once" on a second call).
        # The test runs a full launch subprocess; a private context lets it
        # spin up and tear down cleanly regardless of pytest collection order.
        _ctx = rclpy.Context()
        rclpy.init(context=_ctx)
        node = Node('mujoco_smoke_probe', context=_ctx)
        executor = None
        try:
            # Single spinner (no background thread): each `_wait_until` predicate
            # below drives the executor via spin_once, servicing both the
            # controller-manager service client and the /joint_states
            # subscription. A dedicated background spin thread vs. the
            # predicate-level spins on the same executor would race and neither
            # service responses nor subscription callbacks would deliver. This
            # test was blocked by the sim bad_alloc for PR8b's whole life and is
            # only now exercising the real graph for the first time.
            executor = rclpy.executors.SingleThreadedExecutor(context=_ctx)
            executor.add_node(node)

            # -- wait for controller_manager and the position controller active
            cli = node.create_client(
                ListControllers, '/controller_manager/list_controllers')

            def _service_ready():
                executor.spin_once(timeout_sec=0.1)
                return cli.service_is_ready()
            _wait_until(_service_ready, LAUNCH_READY_TIMEOUT_S,
                        'controller_manager list_controllers never became '
                        'available')

            def _position_active():
                executor.spin_once(timeout_sec=0.1)
                if not cli.service_is_ready():
                    return False
                req = ListControllers.Request()
                fut = cli.call_async(req)
                executor.spin_until_future_complete(fut, timeout_sec=2)
                return any(
                    c.name == POSITION_CONTROLLER and c.state == 'active'
                    for c in fut.result().controller) if fut.done() else False

            _wait_until(_position_active, CONTROLLER_ACTIVE_TIMEOUT_S,
                        'position controller never became active')

            # -- read current joint position from /joint_states
            joint_state = {}

            def _joint_cb(msg):
                joint_state.update(dict(zip(msg.name, msg.position)))
            node.create_subscription(JointState, '/joint_states',
                                     _joint_cb, 10)

            def _have_joint():
                executor.spin_once(timeout_sec=0.1)
                return SMOKE_JOINT in joint_state
            _wait_until(_have_joint, JOINT_MOVE_TIMEOUT_S,
                        'joint_states never reported %s' % SMOKE_JOINT)
            before = joint_state[SMOKE_JOINT]

            # -- publish a defensive position move (+0.15 rad) to the group
            joints = _position_controller_joint_order()
            order_index = joints.index(SMOKE_JOINT)
            # A large step: see SMOKE_JOINT's note -- the column is heavily
            # damped and only reaches a fraction of a small command.
            target = before + 0.5
            cmd_pub = node.create_publisher(
                Float64MultiArray,
                '/%s/commands' % POSITION_CONTROLLER, 10)
            # Wait for the subscription to match before publishing. A plain
            # `sleep(1)` is not enough on this host: the bringup now starts five
            # controllers (two JTCs + the group controller + the broadcaster +
            # the base) sequentially on one spawner, so discovery for a
            # freshly-created publisher can take longer than a second under the
            # resulting load -- and a command published before the subscription
            # matches is silently dropped, which reads as "the joint did not
            # move". `get_subscription_count()` is the honest signal.
            _wait_until(lambda: cmd_pub.get_subscription_count() > 0, 30.0,
                        'no subscriber appeared for the %s command topic'
                        % POSITION_CONTROLLER)

            home = [0.0] * len(joints)
            home[order_index] = target

            def _moved():
                # Keep commanding while we wait: a JointGroupPositionController
                # holds the last command, but re-publishing makes the test
                # robust to the first command landing before the controller's
                # first update tick.
                cmd = Float64MultiArray()
                cmd.data = list(home)
                cmd_pub.publish(cmd)
                executor.spin_once(timeout_sec=0.1)
                # The claim is that the command MOVES the joint. Asserting the
                # exact settled value would be asserting the sim's actuator
                # dynamics and this host's throughput -- `column_lift` in
                # particular lags heavily. A 10% closure of the commanded step,
                # in the commanded direction, is unambiguous motion.
                delta = joint_state.get(SMOKE_JOINT, before) - before
                return abs(delta) > 0.1 * abs(target - before)

            _wait_until(_moved, JOINT_MOVE_TIMEOUT_S,
                        'joint did not move toward commanded position')
            after = joint_state.get(SMOKE_JOINT, before)
            # The claim under test: the command MOVES the joint. The exact
            # settled value and its sign are the sim's actuator dynamics (the
            # gripper joint's own transmission can invert the sign), which is
            # not what this smoke test is about -- `robot_backends`'
            # `test_joint_command_moves_sim_state` sibling in the mock/real
            # suites owns the semantics.
            assert abs(after - before) > 0.02, (
                'joint state did not move (before=%r after=%r)' % (before, after))
        finally:
            if executor is not None:
                executor.shutdown()
            node.destroy_node()
            _ctx.try_shutdown()
            # rclpy.shutdown() is deferred (see PR8a test comment): spinning it
            # down mid-thread aborts under this pytest host; the process exits
            # immediately after, so the cost is nil.
    finally:
        _terminate_group(process, group)


def _jaw_limits():
    """Return ``{joint: (lower, upper)}`` for the jaw joints, off the shipped URDF.

    The URDF is the single source of truth for the jaw contract -- it is the
    declaration the dfki-ric plugin clamps the mirror command to -- so reading
    it here asserts the *declared* limits rather than restating literals that
    could drift. Expands the same top-level xacro through the ``xacro`` CLI the
    launch uses (no ROS runtime, no extra dependency).
    """
    import xml.etree.ElementTree as ElementTree
    from ament_index_python.packages import get_package_share_directory
    urdf = subprocess.check_output(
        [_require_tool('xacro'),
         os.path.join(get_package_share_directory('robot_description'), 'urdf',
                      'robot.urdf.xacro')], text=True)
    limits = {}
    for joint in ElementTree.fromstring(urdf).findall('joint'):
        name = joint.get('name')
        if name in JAW_JOINTS:
            limit = joint.find('limit')
            limits[name] = (float(limit.get('lower')), float(limit.get('upper')))
    assert set(limits) == set(JAW_JOINTS), (
        'the shipped URDF is missing jaw joint(s): %r' % (set(JAW_JOINTS) - set(limits)))
    return limits


def test_gripper_jaws_track_a_full_range_command():
    """End-to-end: the driven jaws track a full-range command, monotone, no jam.

    Issue #156's acceptance. The jaw DOF used to be an undamped, numerically
    unstable kp=100 position loop at dt=0.002 s: from the closed pose it
    diverged within ~25 steps, jammed on jaw self-contact *outside* the declared
    range (measured +0.39 / -0.49 rad instead of [-1.5, 0]) and ignored every
    command -- which is why the sibling smoke above had to use ``column_lift``
    and why MoveIt refused every plan with ``START_STATE_IN_COLLISION``.

    This launches the real bringup and drives the two driven jaws through their
    whole range via the shipped ``arm_gripper_position_controller`` --
    closed -> open -> closed -> open -- asserting, from ``/joint_states``:

    * the jaws TRACK: each leg settles within ``JAW_TRACK_TOL`` of its target
      (a jammed jaw settles at a wrong value and never arrives);
    * the motion is MONOTONE in each leg (no reversal beyond
      ``JAW_MONOTONIC_TOL``) and passes through interior values (a real sweep,
      not a single leap);
    * every sample stays INSIDE the declared limits read off the shipped URDF,
      for all four jaw joints (the old jam lived *outside* them);
    * the mirror jaw follows as the TRUE NEGATION of the driven one
      (``mirror == -driven``), the contract the -1 ``<mimic>`` and the mirror's
      negated declared range exist to keep.
    """
    if not _have_package('mujoco_ros2_control'):
        pytest.skip(
            'mujoco_ros2_control (dfki-ric, source-build via robot.repos, D33) '
            'is not installed; the full-sim jaw-tracking smoke needs it live. '
            'Build it with: `vcs import src < robot.repos && pixi run build`.')
    import rclpy
    from controller_manager_msgs.srv import ListControllers
    from rclpy.node import Node
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Float64MultiArray

    limits = _jaw_limits()
    env = dict(os.environ, ROS_DOMAIN_ID=MUJOCO_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env)
    try:
        os.environ['ROS_DOMAIN_ID'] = str(MUJOCO_DOMAIN_ID)
        _ctx = rclpy.Context()
        rclpy.init(context=_ctx)
        node = Node('mujoco_jaw_probe', context=_ctx)
        executor = None
        try:
            executor = rclpy.executors.SingleThreadedExecutor(context=_ctx)
            executor.add_node(node)

            cli = node.create_client(
                ListControllers, '/controller_manager/list_controllers')

            def _service_ready():
                executor.spin_once(timeout_sec=0.1)
                return cli.service_is_ready()
            _wait_until(_service_ready, LAUNCH_READY_TIMEOUT_S,
                        'controller_manager list_controllers never became '
                        'available')

            def _position_active():
                executor.spin_once(timeout_sec=0.1)
                if not cli.service_is_ready():
                    return False
                fut = cli.call_async(ListControllers.Request())
                executor.spin_until_future_complete(fut, timeout_sec=2)
                return any(
                    c.name == POSITION_CONTROLLER and c.state == 'active'
                    for c in fut.result().controller) if fut.done() else False
            _wait_until(_position_active, CONTROLLER_ACTIVE_TIMEOUT_S,
                        'position controller never became active')

            # -- record /joint_states (jaw joints only): the monotonicity and
            #    limit assertions need the *transient*, not just the endpoint.
            samples = []

            def _on_joint_state(msg):
                values = dict(zip(msg.name, msg.position))
                if all(j in values for j in JAW_JOINTS):
                    samples.append({j: values[j] for j in JAW_JOINTS})

            node.create_subscription(JointState, '/joint_states',
                                     _on_joint_state, 200)

            def _have_jaws():
                executor.spin_once(timeout_sec=0.1)
                return bool(samples)
            _wait_until(_have_jaws, JOINT_MOVE_TIMEOUT_S,
                        '/joint_states never reported all four jaw joints')

            joints = _position_controller_joint_order()
            jaw_index = {j: joints.index(j) for j in DRIVEN_JAW_JOINTS}
            cmd_pub = node.create_publisher(
                Float64MultiArray,
                '/%s/commands' % POSITION_CONTROLLER, 10)
            _wait_until(lambda: cmd_pub.get_subscription_count() > 0, 30.0,
                        'no subscriber appeared for the %s command topic'
                        % POSITION_CONTROLLER)

            def _publish(target):
                cmd = Float64MultiArray()
                cmd.data = [0.0] * len(joints)
                for joint, index in jaw_index.items():
                    cmd.data[index] = target
                cmd_pub.publish(cmd)

            def _settle(target, timeout_s=JOINT_MOVE_TIMEOUT_S):
                """Publish ``target`` until both driven jaws arrive; return the segment."""
                start = len(samples)
                deadline = time.monotonic() + timeout_s
                while time.monotonic() < deadline:
                    # Keep commanding: a JointGroupPositionController holds the
                    # last command, but re-publishing is robust to the first
                    # command landing before the controller's first update tick.
                    _publish(target)
                    executor.spin_once(timeout_sec=0.02)
                    if samples and all(
                            abs(samples[-1][j] - target) < JAW_TRACK_TOL
                            for j in DRIVEN_JAW_JOINTS):
                        return samples[start:]
                last = samples[-1] if samples else None
                raise AssertionError(
                    'driven jaws never reached %+.3f rad within %.0fs; last '
                    '/joint_states=%r. Stack output tail:\n%s'
                    % (target, timeout_s, last, ''.join(output)[-2000:]))

            # -- sweep the whole aperture: open -> closed -> open (the jaws rest
            #    closed, so the first leg is a real move).
            for leg, target in enumerate((JAW_OPEN, JAW_CLOSED, JAW_OPEN)):
                segment = _settle(target)
                assert segment, 'leg %d recorded no /joint_states samples' % leg
                first = segment[0]
                # Direction of the leg, from where it started.
                descending = target < first['right_gripper']
                for snapshot in segment:
                    for joint in JAW_JOINTS:
                        lower, upper = limits[joint]
                        assert lower - 1e-3 <= snapshot[joint] <= upper + 1e-3, (
                            'leg %d: %s=%+.4f is outside its declared limits '
                            '[%.3f, %.3f] -- the old jam lived out here'
                            % (leg, joint, snapshot[joint], lower, upper))
                    # The mirror is the driven jaw's exact negation.
                    for side in ('left', 'right'):
                        driven = snapshot['%s_gripper' % side]
                        mirror = snapshot['%s_gripper_mirror' % side]
                        assert abs(mirror + driven) < JAW_MONOTONIC_TOL, (
                            'leg %d: %s_gripper_mirror=%+.4f is not the '
                            'negation of %s_gripper=%+.4f'
                            % (leg, side, mirror, side, driven))
                # Monotone in the leg's direction, and no dead-stick: the jaw
                # must pass through interior values, not jump to the endpoint.
                previous = segment[0]
                for snapshot in segment[1:]:
                    for joint in DRIVEN_JAW_JOINTS:
                        delta = snapshot[joint] - previous[joint]
                        if descending:
                            assert delta <= JAW_MONOTONIC_TOL, (
                                'leg %d: %s reversed while opening (%+.4f -> '
                                '%+.4f)' % (leg, joint, previous[joint],
                                            snapshot[joint]))
                        else:
                            assert delta >= -JAW_MONOTONIC_TOL, (
                                'leg %d: %s reversed while closing (%+.4f -> '
                                '%+.4f)' % (leg, joint, previous[joint],
                                            snapshot[joint]))
                    previous = snapshot
                final = segment[-1]
                for joint in DRIVEN_JAW_JOINTS:
                    assert abs(final[joint] - target) < JAW_TRACK_TOL, (
                        'leg %d: %s settled at %+.4f, %.4f rad from the target '
                        '%+.3f (a jammed jaw stops short)'
                        % (leg, joint, final[joint],
                           abs(final[joint] - target), target))
                lo = min(s['right_gripper'] for s in segment)
                hi = max(s['right_gripper'] for s in segment)
                margin = 0.02
                assert hi - lo > 4 * margin, (
                    'leg %d swept only %.4f rad of the commanded %.4f: the joint '
                    'barely moved' % (leg, hi - lo, abs(target - first['right_gripper'])))
                assert any(lo + margin < s['right_gripper'] < hi - margin
                           for s in segment), (
                    'leg %d swept from %+.4f to %+.4f with no interior sample: '
                    'the joint jumped, it did not track' % (leg, first['right_gripper'],
                                                            final['right_gripper']))
        finally:
            if executor is not None:
                executor.shutdown()
            node.destroy_node()
            _ctx.try_shutdown()
            # rclpy.shutdown() is deferred (same reason as the sibling smoke).
    finally:
        _terminate_group(process, group)
