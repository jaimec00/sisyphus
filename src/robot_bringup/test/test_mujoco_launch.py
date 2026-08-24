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
"""
import os
import shutil
import signal
import subprocess
import threading
import time

# ROS imports are deferred into the tests so collection needs no ROS runtime.


#: private ROS domain for this suite.
MUJOCO_DOMAIN_ID = '113'
LAUNCH_READY_TIMEOUT_S = 60.0
CONTROLLER_ACTIVE_TIMEOUT_S = 60.0
JOINT_MOVE_TIMEOUT_S = 20.0

#: controller that the smoke test commands (a position group).
POSITION_CONTROLLER = 'arm_gripper_position_controller'
#: the joint the smoke moves and observes.
SMOKE_JOINT = 'left_shoulder_pan'


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


def test_controllers_yaml_declares_position_and_velocity_groups():
    """The controller params declare the two group controllers over the joints.

    Derived expectations: 13 position-commanded joints (column_lift + 10 arm
    revolute + 2 driven grippers) and 3 velocity-commanded wheels. The two
    gripper-mirror joints are absent (they are mimics, R-PR8b-6).
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
    assert len(position_joints) == 13, position_joints
    assert len(velocity_joints) == 3, velocity_joints
    assert 'column_lift' in position_joints
    assert 'left_shoulder_pan' in position_joints
    assert 'left_gripper' in position_joints
    assert 'left_gripper_mirror' not in position_joints
    assert set(velocity_joints) == {
        'base_left_wheel', 'base_back_wheel', 'base_right_wheel'}


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
            target = before + 0.15
            cmd_pub = node.create_publisher(
                Float64MultiArray,
                '/%s/commands' % POSITION_CONTROLLER, 10)
            time.sleep(1.0)  # let the publisher connect
            home = [0.0] * len(joints)
            home[order_index] = target
            for _ in range(5):
                cmd = Float64MultiArray()
                cmd.data = list(home)
                cmd_pub.publish(cmd)
                executor.spin_once(timeout_sec=0.1)
                time.sleep(0.2)

            # -- give the position actuator time to move, then confirm
            def _moved():
                executor.spin_once(timeout_sec=0.1)
                return abs(joint_state.get(SMOKE_JOINT, before) - target) < 0.05
            _wait_until(_moved, JOINT_MOVE_TIMEOUT_S,
                        'joint did not move toward commanded position')
            after = joint_state.get(SMOKE_JOINT, before)
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
