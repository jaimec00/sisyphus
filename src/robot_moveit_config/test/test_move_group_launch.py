# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Headless ``move_group`` comes up and loads the arm groups (R10 item 2).

This is PR1's second acceptance criterion, tested the way the repo tests every
launch: a cheap structural check of the launch description, then a real
``ros2 launch`` subprocess on a private ``ROS_DOMAIN_ID``, probed with a
dedicated ``rclpy.Context`` + ``SingleThreadedExecutor``, torn down with
``os.killpg``. Non-skipping: ``move_group`` is pinned in ``pixi.toml`` and is
part of the shipped tree, so a gap in the environment is a failure, not a skip.

Claims, cheapest first:

* ``test_move_group_launch_generates_expected_node`` -- import the installed
  launch module and assert it declares ``moveit_ros_move_group/move_group``.
* ``test_move_group_starts_and_loads_arm_groups`` -- launch it headless, wait
  for ``/get_planning_scene`` to answer and assert the loaded model is named
  ``sisyphus`` (the name comes from the SRDF's ``<robot name>``); then prove
  each R2 group is *loaded* by asking move_group a group-scoped IK question
  (``/compute_ik``) and asserting the answer is ``NO_IK_SOLUTION`` rather than
  ``INVALID_GROUP_NAME``.
* ``test_move_group_planner_config_resolves`` (R-fix3, red-team BLOCK 1) --
  assert the ``default_planner_config`` name each arm group selects is a real
  key under the shipped ``ompl.planner_configs`` on the param server, and that
  a *bogus* name is absent. Before this test, a typo'd planner name
  (``RRTConnectkConfigDefault``) shipped green while ``move_group`` logged
  "Could not find the planner configuration ... on the param server" on every
  launch, ``/get_planner_params`` came back empty and ``/plan_kinematic_path``
  returned code 99999 with zero points.
* ``test_move_group_neutral_pose_is_collision_free`` (R-fix2, red-team BLOCK 2)
  -- assert ``/check_state_validity`` reports the neutral (all-zeros) arm pose
  as valid. Before this test, the SRDF carried no ``<disable_collisions>``, so
  the neutral pose self-collided (adjacent links + one body pair) and
  ``CheckStartStateCollision`` rejected every plan.
* ``test_move_group_can_plan_for_an_arm`` (red-team BLOCK 1 end-to-end) -- ask
  ``/plan_kinematic_path`` for a short joint-space move and assert the error
  code is SUCCESS (1) with a non-empty trajectory. This is the claim the two
  config fixes above exist to make true.

Why the IK error code is the discriminator
------------------------------------------
An earlier draft asked ``/check_state_validity`` for each group and asserted the
call was answered -- but a *nonexistent* group is answered too (``valid=False``),
so that proved nothing. ``/compute_ik`` discriminates empirically: a group that
is not loaded yields ``moveit_msgs/MoveItErrorCodes.INVALID_GROUP_NAME`` (-15),
while a loaded group with no reachable solution yields ``NO_IK_SOLUTION`` (-31)
-- confirmed by probing move_group directly for both a real group and a bogus
one. Since the URDF names no groups at all, a group that *is* recognised can
only have come through ``robot_description_semantic``: this is the proof the
arm planning group loads from the SRDF.
"""
import os
import shutil
import signal
import subprocess
import threading
import time

#: A ROS domain of this suite's own (112 tf_tree, 113 mujoco, 115 world_write,
#: 117 world_launch, 119 bridge_e2e): move_group is long-lived, so it must not
#: collide with anything else on the machine.
MOVE_GROUP_DOMAIN_ID = '118'
SERVICE_READY_TIMEOUT_S = 180.0
SERVICE_CALL_TIMEOUT_S = 30.0

#: The planning groups the SRDF declares (R2).
ARM_GROUPS = ('left_arm', 'right_arm')
GRIPPER_GROUPS = ('left_gripper', 'right_gripper')

#: The pose link an arm group's IK is asked about (a link inside the group).
ARM_IK_LINK = {
    'left_arm': 'left_gripper_base_link',
    'right_arm': 'right_gripper_base_link',
}

ROBOT_MODEL_NAME = 'sisyphus'

#: moveit_msgs/MoveItErrorCodes values this test discriminates on.
NO_IK_SOLUTION = -31
INVALID_GROUP_NAME = -15
SUCCESS = 1

#: The param-server namespace the OMPL pipeline lands in (R-fix3). The builder
#: flattens each pipeline to a top-level parameter namespace, so the planner
#: registry is ``ompl.planner_configs.<name>`` and each group's selection is
#: ``ompl.<group>.default_planner_config``.
OMPL_PLANNER_CONFIGS_PARAM = '/move_group'

#: A name that must NOT resolve -- the pre-fix typo, kept as the negative
#: control so the resolution check is not vacuous.
BOGUS_PLANNER_CONFIG = 'RRTConnectkConfigDefault'


def _require_tool(name):
    path = shutil.which(name)
    assert path is not None, (
        '%s is not on PATH; it is pinned in pixi.toml / package.xml -- run '
        'inside `pixi run`.' % name)
    return path


def _launch_path():
    from ament_index_python.packages import get_package_share_directory
    return os.path.join(get_package_share_directory('robot_moveit_config'),
                        'launch', 'move_group.launch.py')


def test_move_group_launch_generates_expected_node():
    """The installed launch imports and declares the move_group node.

    Cheap and deterministic: importing the module exercises
    ``MoveItConfigsBuilder`` against the installed config package (so a missing
    SRDF/yaml, a bad ``.setup_assistant`` pointer or an unexpanded xacro fails
    here, without a graph), and ``generate_launch_description`` proves the node
    action is the one the acceptance criterion names.
    """
    import importlib.util
    from launch.actions import DeclareLaunchArgument
    from launch_ros.actions import Node
    path = _launch_path()
    assert os.path.isfile(path), (
        'move_group.launch.py not present in installed tree: %s' % path)
    spec = importlib.util.spec_from_file_location(
        'robot_moveit_config_move_group_launch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    description = module.generate_launch_description()

    nodes = [a for a in description.entities if isinstance(a, Node)]
    seen = [(getattr(a, 'node_package', '?'),
             getattr(a, 'node_executable', '?')) for a in nodes]
    assert ('moveit_ros_move_group', 'move_group') in seen, (
        'move_group.launch.py does not declare moveit_ros_move_group/'
        'move_group: %r' % (seen,))

    declared = {getattr(a, 'name', None) for a in description.entities
                if isinstance(a, DeclareLaunchArgument)}
    assert {'allow_trajectory_execution',
            'publish_monitored_planning_scene'} <= declared, declared


def _spawn_launch(env):
    process = subprocess.Popen(
        [_require_tool('ros2'), 'launch', 'robot_moveit_config',
         'move_group.launch.py'],
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


def test_move_group_starts_and_loads_arm_groups():
    """E2E: launch move_group headless and prove the R2 groups are loaded.

    Launches the shipped ``move_group.launch.py`` on a private ROS domain, then:

    1. waits for ``/get_planning_scene`` and ``/compute_ik`` to answer;
    2. asserts the loaded model is named ``sisyphus`` (the name comes from the
       SRDF, so a wrong or missing SRDF is visible here);
    3. asks ``/compute_ik`` for each R2 group and asserts the error code is
       ``NO_IK_SOLUTION`` -- not ``INVALID_GROUP_NAME`` -- which is the
       empirical proof the group is loaded, and additionally asserts a made-up
       group name *does* return ``INVALID_GROUP_NAME`` (so the discriminator is
       meaningful, not vacuous).
    """
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from moveit_msgs.srv import GetPlanningScene, GetPositionIK
    from moveit_msgs.msg import (
        PlanningSceneComponents, PositionIKRequest)
    from geometry_msgs.msg import PoseStamped

    env = dict(os.environ, ROS_DOMAIN_ID=MOVE_GROUP_DOMAIN_ID)
    process, group, output, reader = _spawn_launch(env)
    os.environ['ROS_DOMAIN_ID'] = MOVE_GROUP_DOMAIN_ID
    context = rclpy.Context()
    rclpy.init(context=context)
    node = Node('move_group_launch_probe', context=context)
    executor = None
    try:
        executor = SingleThreadedExecutor(context=context)
        executor.add_node(node)
        scene_client = node.create_client(GetPlanningScene,
                                          '/get_planning_scene')
        ik_client = node.create_client(GetPositionIK, '/compute_ik')

        def _wait_for(client, service):
            deadline = time.monotonic() + SERVICE_READY_TIMEOUT_S
            while time.monotonic() < deadline:
                executor.spin_once(timeout_sec=0.1)
                if client.service_is_ready():
                    return
            logs = ''.join(output)
            raise AssertionError(
                '%s never became available within %.0fs. Launch log:\n%s'
                % (service, SERVICE_READY_TIMEOUT_S, logs or '<no output>'))

        _wait_for(scene_client, '/get_planning_scene')
        _wait_for(ik_client, '/compute_ik')

        # -- 1. the loaded model is the SRDF's.
        request = GetPlanningScene.Request()
        request.components.components = (
            PlanningSceneComponents.ROBOT_STATE
            | PlanningSceneComponents.SCENE_SETTINGS)
        future = scene_client.call_async(request)
        executor.spin_until_future_complete(
            future, timeout_sec=SERVICE_CALL_TIMEOUT_S)
        assert future.done(), 'GetPlanningScene did not complete'
        scene = future.result().scene
        assert scene.robot_model_name == ROBOT_MODEL_NAME, (
            'move_group loaded model %r, expected %r (from the SRDF)'
            % (scene.robot_model_name, ROBOT_MODEL_NAME))

        # -- 2. each R2 group is loaded: IK recognises it.
        def _ik_error_code(group_name, link_name):
            ik_request = PositionIKRequest()
            ik_request.group_name = group_name
            ik_request.ik_link_name = link_name
            ik_request.pose_stamped = PoseStamped()
            ik_request.pose_stamped.header.frame_id = 'base_link'
            ik_request.pose_stamped.pose.orientation.w = 1.0
            ik_request.timeout.sec = 1
            request = GetPositionIK.Request()
            request.ik_request = ik_request
            future = ik_client.call_async(request)
            executor.spin_until_future_complete(
                future, timeout_sec=SERVICE_CALL_TIMEOUT_S)
            assert future.done() and future.result() is not None, group_name
            return future.result().error_code.val

        for group_name in ARM_GROUPS:
            code = _ik_error_code(group_name, ARM_IK_LINK[group_name])
            assert code != INVALID_GROUP_NAME, (
                'move_group does not recognise group %r -- the SRDF group did '
                'not load (error_code=%d)' % (group_name, code))
            assert code == NO_IK_SOLUTION, (
                'unexpected IK error for %r: %d' % (group_name, code))

        # Gripper groups are loaded too (single-DOF, no IK plugin -- the point
        # is that move_group recognises the name at all).
        for group_name in GRIPPER_GROUPS:
            code = _ik_error_code(group_name, '%s_gripper_upper_jaw_link'
                                  % group_name.split('_')[0])
            assert code != INVALID_GROUP_NAME, (
                'move_group does not recognise group %r (error_code=%d)'
                % (group_name, code))

        # -- 3. the discriminator is meaningful: a bogus group is rejected.
        bogus = _ik_error_code('definitely_not_a_group', 'left_gripper_base_link')
        assert bogus == INVALID_GROUP_NAME, (
            'a nonexistent group must yield INVALID_GROUP_NAME (%d); got %d -- '
            'the group-existence check above would be vacuous'
            % (INVALID_GROUP_NAME, bogus))
    finally:
        if executor is not None:
            executor.shutdown()
        node.destroy_node()
        context.try_shutdown()
        _terminate_group(process, group)


class _MoveGroupProbe:
    """A launched ``move_group`` plus a probing rclpy node, as a context manager.

    Encapsulates the launch/spin/teardown dance the three service-level tests
    share: a private ROS domain, a ``ros2 launch`` subprocess in its own
    process group, one ``SingleThreadedExecutor`` for calls, and ``killpg`` on
    exit. ``call(service, request)`` waits for the service, dispatches, and
    returns the response; ``values``/``describe`` drive ``/get_parameters``.
    """

    def __init__(self, domain_id=MOVE_GROUP_DOMAIN_ID):
        self._domain_id = domain_id
        self._process = None
        self._group = None
        self._output = None
        self._context = None
        self._node = None
        self._executor = None

    def __enter__(self):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node

        env = dict(os.environ, ROS_DOMAIN_ID=self._domain_id)
        self._process, self._group, self._output, _ = _spawn_launch(env)
        os.environ['ROS_DOMAIN_ID'] = self._domain_id
        self._context = rclpy.Context()
        rclpy.init(context=self._context)
        self._node = Node('move_group_launch_probe', context=self._context)
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

    @property
    def logs(self):
        return ''.join(self._output or [])

    def wait_for_client(self, client, service):
        deadline = time.monotonic() + SERVICE_READY_TIMEOUT_S
        while time.monotonic() < deadline:
            self._executor.spin_once(timeout_sec=0.1)
            if client.service_is_ready():
                return client
        raise AssertionError(
            '%s never became available within %.0fs. Launch log:\n%s'
            % (service, SERVICE_READY_TIMEOUT_S, self.logs or '<no output>'))

    def client(self, srv_type, service, wait=True):
        client = self._node.create_client(srv_type, service)
        return self.wait_for_client(client, service) if wait else client

    def call(self, client, request, timeout=SERVICE_CALL_TIMEOUT_S):
        future = client.call_async(request)
        self._executor.spin_until_future_complete(future, timeout_sec=timeout)
        assert future.done(), 'service call timed out after %ss' % timeout
        return future.result()


def test_move_group_planner_config_resolves():
    """R-fix3: the default_planner_config names resolve on the param server.

    The red-team's BLOCK 1 was a silent misconfiguration: ``ompl_planning.yaml``
    selected ``RRTConnectkConfigDefault``, a name the shipped
    ``ompl_defaults.yaml`` does not define (it defines ``RRTConnect``), so
    ``move_group`` spun with a dead planner and every plan failed -- yet no test
    noticed. This asserts, against the *live* param server:

    * ``ompl.<group>.default_planner_config`` is set and non-empty;
    * that name exists as ``ompl.planner_configs.<name>`` with a ``type``;
    * every name in ``ompl.<group>.planner_configs`` resolves the same way;
    * the old typo (``RRTConnectkConfigDefault``) does NOT resolve -- the
      negative control that makes the check meaningful.
    """
    from rclpy.parameter import Parameter
    from rcl_interfaces.srv import GetParameters

    with _MoveGroupProbe() as probe:
        client = probe.client(GetParameters, '/move_group/get_parameters')
        names = []
        for group in ARM_GROUPS:
            names.append('ompl.%s.default_planner_config' % group)
            names.append('ompl.%s.planner_configs' % group)
        request = GetParameters.Request()
        request.names = names
        response = probe.call(client, request)
        got = dict(zip(names, response.values))

        for group in ARM_GROUPS:
            default_name = got['ompl.%s.default_planner_config' % group
                               ].string_value
            assert default_name, (
                'move_group did not publish ompl.%s.default_planner_config -- '
                'the group block is missing or not loaded' % group)
            listed = list(got['ompl.%s.planner_configs' % group
                              ].string_array_value)
            assert listed, (
                'ompl.%s.planner_configs is empty' % group)
            assert default_name in listed, (
                '%s.default_planner_config=%r is not in its own '
                'planner_configs=%r' % (group, default_name, listed))

        # Every selected name + every listed name must exist in the registry,
        # and the registry entries must carry a non-empty OMPL type.
        selected = sorted({got['ompl.%s.default_planner_config' % g
                               ].string_value for g in ARM_GROUPS} |
                          {n for g in ARM_GROUPS
                           for n in got['ompl.%s.planner_configs' % g
                                        ].string_array_value})
        registry_names = ['ompl.planner_configs.%s.type' % n for n in selected]
        registry_names.append('ompl.planner_configs.%s.type'
                              % BOGUS_PLANNER_CONFIG)
        request = GetParameters.Request()
        request.names = registry_names
        response = probe.call(client, request)
        for name, value in zip(registry_names, response.values):
            # An undefined parameter comes back as ``NOT_SET`` with an empty
            # string -- which is exactly the shape a *missing* planner_config
            # has. A defined one is a STRING whose value names the OMPL planner
            # type (e.g. ``geometric::RRTConnect``).
            # NB: ``ParameterValue.type`` is a plain int; ``Parameter.Type``
            # is a non-IntEnum, so compare against its ``.value``.
            resolved = (value.type == Parameter.Type.STRING.value
                        and bool(value.string_value))
            if name.endswith('%s.type' % BOGUS_PLANNER_CONFIG):
                assert not resolved, (
                    'the pre-fix typo %r resolves on the param server (%r); '
                    'the negative control is broken'
                    % (BOGUS_PLANNER_CONFIG, value.string_value))
                continue
            assert resolved, (
                '%s resolves to %r (type=%d) -- move_group cannot find planner '
                'config %r' % (name, value.string_value, value.type, name))


def test_move_group_neutral_pose_is_collision_free():
    """R-fix2: the neutral (all-zeros) arm pose is collision free.

    The red-team's BLOCK 2: the SRDF shipped no ``<disable_collisions>``, so
    MoveIt checked every link pair and found the adjacent links (and one body
    pair) in contact at the neutral pose -- ``CheckStartStateCollision`` then
    rejected every plan. The fix adds the disable_collisions entries; this
    asserts ``/check_state_validity`` now reports ``valid=True`` for the
    all-zeros pose of each arm, and lists the contacts otherwise so a
    regression is diagnosable (not just "valid == False").
    """
    from moveit_msgs.srv import GetStateValidity
    from moveit_msgs.msg import RobotState
    from sensor_msgs.msg import JointState

    with _MoveGroupProbe() as probe:
        client = probe.client(GetStateValidity, '/check_state_validity', wait=True)
        for group in ARM_GROUPS:
            side = group.split('_')[0]
            request = GetStateValidity.Request()
            request.group_name = group
            request.robot_state = RobotState()
            joint_state = JointState()
            joint_state.name = ['%s_%s' % (side, j) for j in (
                'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex',
                'wrist_roll')]
            joint_state.position = [0.0] * len(joint_state.name)
            request.robot_state.joint_state = joint_state
            response = probe.call(client, request)
            contacts = ['%s<->%s' % (c.contact_body_1, c.contact_body_2)
                        for c in response.contacts]
            assert response.valid, (
                'neutral pose of %s is not collision free; contacts: %r'
                % (group, contacts))


def test_move_group_can_plan_for_an_arm():
    """BLOCK 1 end-to-end: a plan request succeeds and returns a trajectory.

    The strongest form of "make a plan requestable": ask
    ``/plan_kinematic_path`` for a short joint-space move from the neutral pose
    and assert ``error_code == SUCCESS`` with a non-empty trajectory. Before
    the OMPL-name fix this returned code 99999 with zero points (no planner
    configuration), and it is exactly what BLOCK 1 said no test covered.
    """
    from moveit_msgs.srv import GetMotionPlan
    from moveit_msgs.msg import (
        Constraints, JointConstraint, RobotState)
    from sensor_msgs.msg import JointState

    targets = [0.3, -0.4, 0.5, -0.3, 0.2]
    with _MoveGroupProbe() as probe:
        client = probe.client(GetMotionPlan, '/plan_kinematic_path', wait=True)
        request = GetMotionPlan.Request()
        motion = request.motion_plan_request
        motion.group_name = 'left_arm'
        motion.num_planning_attempts = 3
        motion.allowed_planning_time = 5.0
        motion.max_velocity_scaling_factor = 0.5
        motion.max_acceleration_scaling_factor = 0.5

        joint_names = ['left_%s' % j for j in (
            'shoulder_pan', 'shoulder_lift', 'elbow_flex', 'wrist_flex',
            'wrist_roll')]
        start = JointState()
        start.name = joint_names
        start.position = [0.0] * len(joint_names)
        motion.start_state = RobotState()
        motion.start_state.joint_state = start

        goal = Constraints()
        for name, value in zip(joint_names, targets):
            constraint = JointConstraint()
            constraint.joint_name = name
            constraint.position = value
            constraint.tolerance_above = 0.01
            constraint.tolerance_below = 0.01
            constraint.weight = 1.0
            goal.joint_constraints.append(constraint)
        motion.goal_constraints.append(goal)

        response = probe.call(client, request, timeout=60.0)
        result = response.motion_plan_response
        assert result.error_code.val == SUCCESS, (
            'plan request for left_arm failed with error_code=%d (no planner '
            'configuration yields 99999 / -1; collision yields -1 with a '
            'contact list)' % result.error_code.val)
        points = list(result.trajectory.joint_trajectory.points)
        assert points, 'plan succeeded (code 1) but returned an empty trajectory'
