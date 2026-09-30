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
