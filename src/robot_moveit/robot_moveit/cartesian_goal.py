# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Drive a Cartesian goal for one arm through MoveIt (PR2 / issue #147, R5).

This is the service the brain (and the acceptance tests) speak to when it wants
"put the gripper at this pose". It is a thin, honest wrapper around MoveIt's
planning interface plus one thing MoveIt cannot tell us: *why* a plan failed.

Why the envelope pre-check exists (R3)
--------------------------------------
``moveit_msgs/MoveItErrorCodes.PLANNING_FAILED`` (-1) is ambiguous: OMPL returns
it both for "no collision-free path" and for "the goal is out of reach" (the IK
sampler simply never succeeds, and the group has no Cartesian path to fall back
on). A caller that wants ``out_of_reach`` to be a distinct outcome -- the Mock
backend and the skill layer both treat it as one, and acceptance criterion 2
asks for it -- cannot recover that from the code alone. So this node reproduces
the backend's reach rule *before* planning: the target must lie within
``reach_radius`` (0.85 m) of the arm's own shoulder origin, both measured in
``base_link``.

The ``world`` frame is published by the launch (a static identity
``world -> base_link``, PR1's ``planning_scene_bridge.launch.py`` convention:
the robot starts at the world origin), so a goal's ``world`` pose resolves
through TF into ``base_link``. Nothing in this node invents a frame.

The shoulder origin comes from TF2, not a constant. That is deliberate: the
shoulder rides the prismatic column, so its height depends on the current
``column_lift`` position, and a hard-coded z would be wrong the moment the lift
moves. TF2 gives the same growing/spinning frame the planner uses.

Where the numbers come from
---------------------------
``reach_radius`` is 0.85 m, the sphere around each shoulder that
``robot_backends.mock_backend._require_reachable`` enforces and that
``robot_description/urdf/arm.xacro`` declares (``xacro:property
reach_radius``). The "~0.4 m" figure in the D39 prose is the *task* envelope,
not arm reach. It is a parameter with the URDF's value as the default, so an arm
swap changes one config value rather than a buried literal.

Why the goal is a joint-space state, not a 6-DOF pose (empirical)
------------------------------------------------------------------
The SO-101 arm is **5-DOF** (shoulder pan/lift, elbow flex, wrist flex/roll --
there is no wrist yaw), so its *reachable set is a position volume with a
position-dependent orientation*, not a 6-DOF pose set. Measured on this stack
(``/compute_ik``, with the neutral tip orientation (-0.5, 0.5, 0.5, 0.5)):

* the neutral tip position (0.383, 0.18, 0.791) -> ``val=1`` (solvable);
* (0.30, 0.18, 0.791) -> ``val=1``; but
* (0.45, 0.18, 0.791) -> ``val=-31`` and (0.45, 0.34, 0.70) -> ``val=-31``.

Forward reach needs the tool to *rotate*, and a fixed-orientation pose goal
(what ``set_goal_state(pose_stamped_msg=...)`` builds, with its tight default
orientation tolerance) is unsatisfiable for most of the workspace -- OMPL then
reports "Unable to sample any valid states for goal tree" and the plan fails
with ``FAILURE``. A ``motion_plan_constraints`` **position-only** goal fails the
same way: MoveIt's constraint sampler still needs an IK solve, and KDL's IK
targets the full pose.

The approach that works, and the one this node uses: **search the arm's own
forward kinematics for a joint state whose tip lands within tolerance of the
requested point**, then hand MoveIt that state as a joint-space goal
(``set_goal_state(robot_state=...)``). This is exactly "a Cartesian goal" on an
under-actuated arm -- the position is honoured, the orientation is whatever the
arm must adopt to get there -- and it is what the Mock backend's reach test (a
*sphere* around the shoulder) already models. Recorded in implementation.md
along with the reason R5's literal ``pose_stamped_msg`` form cannot work here.

Plan -> execute
---------------
``moveit_py``'s ``PlanningComponent`` plans with the full collision checker (the
same disabled-collision set the SRDF declares), and ``MoveItPy``'s
``TrajectoryExecutionManager`` -- wired to the arm JTCs through
``moveit_controllers.yaml`` -- turns the plan into a
``FollowJointTrajectory`` goal. That is the "not a teleport" property the tests
assert: a real time-parameterised joint trajectory executed by the controller,
not a jump in the sim state.

Error mapping
-------------
* plan ``SUCCESS`` + executed + execution ``SUCCEEDED`` -> ``SUCCESS`` (0)
* envelope pre-check fails                              -> ``OUT_OF_REACH`` (1)
* ``START_STATE_IN_COLLISION`` (-10) / ``GOAL_IN_COLLISION`` (-12) /
  ``PLANNING_FAILED`` (-1) *with contacts*             -> ``COLLISION`` (2)
* any other plan/execute failure                        -> ``FAILURE`` (3)

The ``-1``-with-contacts test is what separates "the arm would have to pass
through itself or the column" from "the planner gave up": MoveIt attaches a
contact list to the response when it found a collision during planning and
attaches none when it simply timed out. Both are reported honestly; neither is
dressed up as success.

Empirically-pinned moveit_py API (2.12.4)
----------------------------------------
Read from the installed bindings and the upstream binding sources, not from
memory (ruling R5's probe):

* ``MoveItPy(node_name=..., name_space='', launch_params_filepaths=[...],
  config_dict=None, provide_planning_service=True, remappings=None)`` -- the
  ctor spins its *own* node on its own executor thread, so it must be given the
  MoveIt parameters (we pass them via ``config_dict``). NB the config dict needs
  ``planning_pipelines: {pipeline_names: [...], namespace: ''}`` -- the flat
  ``planning_pipelines: [...]`` move_group takes is rejected by MoveItCpp with
  "Failed to load any planning pipelines" (verified).
* ``PlanningComponent.set_goal_state(pose_stamped_msg=..., pose_link=...)``.
* ``PlanningComponent.plan(single_plan_parameters=..., multi_plan_parameters=None,
  planning_scene=None, solution_selection_function=None,
  stopping_criterion_callback=None)`` -- the kwargs are ``single_plan_parameters``
  / ``multi_plan_parameters`` (NOT ``plan_parameters``, which is what the
  docstring text says; the binding is authoritative).
* ``PlanRequestParameters(moveit_cpp, ns)`` -- constructed *from the MoveItPy
  instance* plus a parameter namespace, then fields
  ``planning_pipeline``/``planning_time``/``planning_attempts``/
  ``max_velocity_scaling_factor``/``max_acceleration_scaling_factor`` are
  read-write attributes.
* ``MoveItPy.execute(robot_trajectory, controllers=[])`` -- the ``controllers``
  argument is REQUIRED (no default in the binding), so it must be passed
  explicitly. Returns an ``ExecutionStatus`` whose ``__bool__`` is
  ``status == SUCCEEDED``.
* ``PlanningComponent.set_goal_state(robot_state=...)`` takes
  ``moveit.core.RobotState`` (the C++ wrapper), NOT ``moveit_msgs/RobotState``;
  passing the message raises "incompatible function arguments".
* ``RobotState.get_global_link_transform(link)`` returns a **4x4 numpy matrix**
  (translation in the last column), not a pose object -- reading ``.translation``
  raises ``AttributeError``.
* ``JointModelGroup.active_joint_model_bounds`` returns a list of single-element
  lists of ``VariableBounds`` objects (a pybind quirk), read via
  ``entry[0].min_position`` / ``.max_position``.
"""
import threading

from geometry_msgs.msg import PoseStamped
from moveit_msgs.msg import MoveItErrorCodes
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from robot_moveit.world_to_scene import scene_objects_from_world_json
from robot_moveit_ros_interfaces.srv import CartesianGoal
from robot_world_ros_interfaces.srv import GetWorld
from tf2_ros import Buffer, TransformException, TransformListener

#: Outcome classes on the response (the enum the .srv documents).
STATUS_SUCCESS = 0
STATUS_OUT_OF_REACH = 1
STATUS_COLLISION = 2
STATUS_FAILURE = 3

#: The frame every goal is reasoned about in: the same base the SRDF groups and
#: the planning scene use, and the frame TF2 always has by the time a plan runs.
BASE_FRAME = 'base_link'

#: The frame the *world service* reports object poses in (robot_world's frame
#: name). The launch publishes the identity ``world -> map`` so this frame is
#: resolvable in TF (see moveit_execution.launch.py for why it hangs off `map`
#: and not off `base_link`). Configurable via the `world_frame` parameter.
WORLD_FRAME = 'world'


#: MoveIt's per-arm goal link: the group's tip, the frame the SRDF declares as
#: the arm chain's tip and as the EEF's parent.
GOAL_LINK = '%s_gripper_base_link'

#: How close the gripper must get to the requested point for the FK search to
#: accept a joint state. 2 cm: finer than the objects the robot handles at this
#: stage, coarse enough that a 5-DOF arm's discrete samples land inside it.
DEFAULT_GOAL_TOLERANCE_M = 0.02

#: Grid resolution of the forward-kinematics search, per joint. 14 per joint is
#: 14^5 = 537824 candidates, which at ~0.02 ms per MoveIt FK call is ~10 s for a
#: full sweep -- the search breaks out early when a candidate is in tolerance.
#: Coarser (12) was tried and *missed reachable targets*: the lexicographic grid
#: walks the joint nearest the tool last, so truncating the sweep finds nothing
#: for a target the arm can plainly reach.
DEFAULT_IK_SAMPLES = 14

#: Hard ceiling on forward-kinematics candidates evaluated per goal. A 5-DOF
#: grid at 14 samples/joint is 537824 points; the cap is set just above it so a
#: full sweep always completes (a truncated sweep is what made reachable targets
#: look unreachable) while a larger configured grid still stays bounded.
DEFAULT_IK_MAX_CANDIDATES = 550000

#: How long to wait for a TF chain that is still being published (see
#: ``_lookup_with_wait``). 30 s: enough for the bringup's static transform to
#: reach a late-joining listener, short enough to answer a broken launch.
DEFAULT_TF_WAIT_TIMEOUT_S = 30.0

#: Planning budget per request. Generous relative to a 5-DOF arm, small enough
#: that a genuinely unreachable goal still answers within one service call.
DEFAULT_PLANNING_TIME_S = 5.0

#: The planning pipeline the config package ships (see move_group.launch.py:
#: the builder is pinned to OMPL because the Pilz template demands a file this
#: package does not ship).
PLANNING_PIPELINE = 'ompl'

#: Service-call / TF wait budget inside a request handler.
_SERVICE_TIMEOUT_S = 5.0


def classify_plan_failure(error_code, contacts):
    """Map a failed plan to ``(status, human-readable reason)``.

    Pure, so the mapping is unit-testable without a graph. The distinction that
    matters: a collision is actionable (the caller chose a bad goal, or the
    scene moved) while a bare ``-1`` is "the planner did not find a path" -- do
    not report the second as the first.
    """
    if error_code in (MoveItErrorCodes.START_STATE_IN_COLLISION,
                      MoveItErrorCodes.GOAL_IN_COLLISION):
        which = ('start state in collision'
                 if error_code == MoveItErrorCodes.START_STATE_IN_COLLISION
                 else 'goal in collision')
        return STATUS_COLLISION, (
            'plan rejected: %s (error_code=%d, contacts=%d)'
            % (which, error_code, len(contacts)))
    if error_code == MoveItErrorCodes.PLANNING_FAILED and contacts:
        return STATUS_COLLISION, (
            'plan rejected: collision during planning (error_code=-1, '
            'contacts=%d)' % len(contacts))
    return STATUS_FAILURE, (
        'plan failed: error_code=%d, contacts=%d' % (error_code, len(contacts)))


def is_out_of_reach(target_xyz, shoulder_xyz, reach_radius):
    """Return True when ``target_xyz`` lies outside the reach sphere.

    The same rule as ``mock_backend._require_reachable``: Euclidean distance in
    the planning frame, inclusive at the boundary (a target exactly on the
    sphere is reachable). Pure.
    """
    distance = _distance(target_xyz, shoulder_xyz)
    return distance > reach_radius


def _product_of_ranges(dimensions, samples):
    """Yield every index tuple in ``range(samples) ** dimensions`` (a grid).

    A generator rather than ``itertools.product(range(samples), repeat=n)`` only
    for the readable name at the call site; the cost is identical.
    """
    import itertools
    return itertools.product(range(samples), repeat=dimensions)


def _orientation_is_unset(quaternion):
    """Return True when a quaternion is the all-zero (invalid) default.

    A default-constructed ``geometry_msgs/Quaternion`` is (0, 0, 0, 0) -- not a
    rotation at all. A caller that left the orientation field alone produces
    this; a caller that means identity sets ``w = 1``.
    """
    return (quaternion.x == 0.0 and quaternion.y == 0.0
            and quaternion.z == 0.0 and quaternion.w == 0.0)


def _distance(a, b):
    """Euclidean distance between two 3-vectors."""
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


#: A goal is *identified* either by a world object id or by an explicit pose.
#: ROS services have no field presence (no proto3-style "optional"), and a
#: default-constructed ``geometry_msgs/Pose`` is a *valid* pose (position zero,
#: unit quaternion) -- so there is no pose value that can mean "not given".
#: The discriminator is therefore an explicit boolean on the request; a caller
#: that only wants a pose sets ``use_target_pose`` and leaves ``object_id``
#: empty. See the .srv header.
def _target_is_identifiable(request):
    """Return True when the request names a target one way or the other."""
    if request.object_id:
        return True
    return request.use_target_pose


def _quaternion_rotate(vector, quaternion):
    """Rotate ``vector`` by the unit ``quaternion``; returns a 3-tuple.

    Expanded ``q * v * q^-1`` with no external dependency, so the transform math
    is testable and obviously-correct in one place.
    """
    x, y, z = vector
    qx, qy, qz, qw = quaternion
    tx = 2.0 * (qy * z - qz * y)
    ty = 2.0 * (qz * x - qx * z)
    tz = 2.0 * (qx * y - qy * x)
    return (
        x + qw * tx + (qy * tz - qz * ty),
        y + qw * ty + (qz * tx - qx * tz),
        z + qw * tz + (qx * ty - qy * tx),
    )


def apply_transform(stamped, transform):
    """Return a new PoseStamped: ``stamped`` expressed in the transform's target.

    ``transform`` must be the TF2 transform *into* the desired frame
    (``lookup_transform(target, source)``). Pure -- the unit test drives it with
    a hand-built transform.
    """
    translation = transform.transform.translation
    rotation = transform.transform.rotation
    rotated = _quaternion_rotate(
        (stamped.pose.position.x, stamped.pose.position.y,
         stamped.pose.position.z),
        (rotation.x, rotation.y, rotation.z, rotation.w))

    result = PoseStamped()
    result.header.frame_id = transform.header.frame_id or BASE_FRAME
    result.pose.position.x = rotated[0] + translation.x
    result.pose.position.y = rotated[1] + translation.y
    result.pose.position.z = rotated[2] + translation.z

    # q_result = q_tf * q_pose.
    px, py, pz, pw = (stamped.pose.orientation.x, stamped.pose.orientation.y,
                      stamped.pose.orientation.z, stamped.pose.orientation.w)
    qx, qy, qz, qw = rotation.x, rotation.y, rotation.z, rotation.w
    result.pose.orientation.x = qw * px + qx * pw + qy * pz - qz * py
    result.pose.orientation.y = qw * py - qx * pz + qy * pw + qz * px
    result.pose.orientation.z = qw * pz + qx * py - qy * px + qz * pw
    result.pose.orientation.w = qw * pw - qx * px - qy * py - qz * pz
    return result


class CartesianGoalNode(Node):
    """Serves ``~/cartesian_goal``: resolve a target, check it, plan it, run it."""

    def __init__(self):
        """Declare parameters, wire the clients/TF, advertise the service."""
        super().__init__('cartesian_goal')
        self.declare_parameter('reach_radius', 0.85)
        self.declare_parameter('planning_time_s', DEFAULT_PLANNING_TIME_S)
        self.declare_parameter('world_service', '/world_query/get_world')
        self.declare_parameter('world_frame', WORLD_FRAME)
        self.declare_parameter('tf_wait_timeout_s', DEFAULT_TF_WAIT_TIMEOUT_S)
        self.declare_parameter('moveit_node_name', 'cartesian_goal_moveit')
        self.declare_parameter('goal_tolerance_m', DEFAULT_GOAL_TOLERANCE_M)
        self.declare_parameter('ik_samples_per_joint', DEFAULT_IK_SAMPLES)
        self.declare_parameter('ik_max_candidates', DEFAULT_IK_MAX_CANDIDATES)

        self._reach_radius = float(self.get_parameter('reach_radius').value)
        self._planning_time = float(
            self.get_parameter('planning_time_s').value)
        self._world_service = str(self.get_parameter('world_service').value)
        self._world_frame = str(self.get_parameter('world_frame').value)
        self._tf_wait_timeout_s = float(
            self.get_parameter('tf_wait_timeout_s').value)
        self._moveit_node_name = str(
            self.get_parameter('moveit_node_name').value)
        self._goal_tolerance_m = float(
            self.get_parameter('goal_tolerance_m').value)
        self._ik_samples_per_joint = int(
            self.get_parameter('ik_samples_per_joint').value)
        self._ik_max_candidates = int(
            self.get_parameter('ik_max_candidates').value)

        # The handler blocks on planning/execution (seconds) while it still needs
        # the world client to answer and TF to buffer, so the group must be
        # reentrant and the executor multi-threaded -- otherwise the handler's
        # own future would never be serviced.
        self._group = ReentrantCallbackGroup()
        self._srv = self.create_service(
            CartesianGoal, '~/cartesian_goal', self._handle,
            callback_group=self._group)
        self._world_client = self.create_client(
            GetWorld, self._world_service, callback_group=self._group)
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # MoveIt params captured from *this* node so the lazy MoveItPy can be
        # handed exactly the config the launch put on the parameter server.
        # Built lazily (not here) so the node starts and advertises the service
        # even before move_group's params land, and so a MoveItPy init failure
        # is a *service error* rather than a launch-time crash.
        self._moveit = None
        self._moveit_lock = threading.Lock()
        self._moveit_params = None
        # Build the MoveIt backend eagerly, on a background thread, so the
        # ~45 s model/pipeline/controller load happens *while* the rest of the
        # bringup comes up rather than on the first goal. `ready` is what the
        # e2e test waits on -- the service existing is not readiness.
        # `ready` is exposed as a settable parameter too, so a client (the e2e
        # test) can poll for backend readiness instead of scraping logs --
        # `ros2 launch` buffers its children's stdout, so a readiness *line* is
        # not reliably observable from outside.
        self.declare_parameter('ready', False)
        self._ready = False
        self._prewarm = threading.Thread(target=self._prewarm_moveit, daemon=True)
        self._prewarm.start()

    def _prewarm_moveit(self):
        """Build the MoveItPy backend in the background (see ``ready``)."""
        try:
            if self._moveit_py() is not None:
                self._ready = True
                try:
                    self.set_parameters([
                        rclpy.parameter.Parameter(
                            'ready', value=True)])
                except Exception:
                    pass
                self.get_logger().info('MoveIt backend ready')
            else:
                self.get_logger().error('MoveIt backend failed to initialise')
        except Exception as exc:  # pragma: no cover - defensive
            self.get_logger().error('MoveIt backend prewarm raised: %s' % exc)

        self.get_logger().info(
            'cartesian_goal ready (reach_radius=%.2f m, frame=%s, world=%s)'
            % (self._reach_radius, BASE_FRAME, self._world_service))

    # -- MoveIt backend ----------------------------------------------------

    def _moveit_py(self):
        """Return the lazily-built ``MoveItPy``, or ``None`` on failure."""
        with self._moveit_lock:
            if self._moveit is not None:
                return self._moveit
        try:
            from moveit.planning import MoveItPy
        except Exception as exc:  # pragma: no cover - import error path
            self.get_logger().error('moveit_py unavailable: %s' % exc)
            return None
        try:
            instance = MoveItPy(
                node_name=self._moveit_node_name,
                # Explicit empty list: MoveItPy otherwise reads
                # `--params-file` entries out of *this* process's sys.argv
                # (moveit.utils.get_launch_params_filepaths) and re-applies the
                # launch's parameter files to its own node -- which carries a
                # `qos_overrides` mapping whose string value does not match the
                # clock subscription's declared type, so the node aborts
                # ("InvalidParameterValueException: parameter
                # 'qos_overrides./clock.subscription.durability' could not be
                # set"). Passing [] makes it use only `config_dict`.
                launch_params_filepaths=[],
                config_dict=self._collect_moveit_params())
        except Exception as exc:
            self.get_logger().error('MoveItPy init failed: %s' % exc)
            return None
        with self._moveit_lock:
            self._moveit = instance
        return instance

    def _collect_moveit_params(self):
        """Return the MoveIt parameter dict MoveItPy's own node needs.

        ``MoveItPy`` spins a *separate* node, so it cannot inherit this node's
        parameters -- the MoveIt config must be handed to it explicitly. The
        launch passes that config as the ``cartesian_goal`` node's own
        parameters (the standard moveit_py pattern); this reads them back off the
        node's parameter overrides.

        Why the node's private ``_parameter_overrides`` and not
        ``has_parameter``: this node does not declare the MoveIt parameters (it
        does not want them in its own namespaces) and does not run with
        ``automatically_declare_parameters_from_overrides``, so
        ``has_parameter('robot_description')`` is *False* even though the value
        arrived on the command line. rclpy keeps the raw overrides on the node
        as ``_parameter_overrides`` (a name -> Parameter mapping); it is private,
        but it is the only honest view of what the launch actually supplied, and
        reading it is confined to this one pass-through. ``getattr`` guards
        against its removal so a future rclpy that renames it degrades to the
        logged warning rather than an AttributeError in the service handler.
        """
        if self._moveit_params is not None:
            return self._moveit_params
        overrides = getattr(self, '_parameter_overrides', None) or {}
        overrides = {name: getattr(param, 'value', param)
                     for name, param in overrides.items()}
        # Everything the launch supplied *except* this node's own switches; the
        # MoveIt config is exactly the rest (robot description + semantic +
        # kinematics + planning + controller manager).
        own_switches = {
            'reach_radius', 'planning_time_s', 'world_service',
            'world_frame', 'tf_wait_timeout_s',
            'moveit_node_name', 'goal_tolerance_m',
            'ik_samples_per_joint', 'ik_max_candidates',
            'world_frame', 'tf_wait_timeout_s',
        }
        params = {name: value for name, value in overrides.items()
                  if name not in own_switches}

        # MoveItCpp (which MoveItPy wraps) does NOT read the flat shape
        # move_group uses. It reads `planning_pipelines.pipeline_names` and
        # optionally `planning_pipelines.namespace`
        # (moveit_cpp.hpp PlanningPipelineOptions::load). The builder's
        # `to_dict()` emits `planning_pipelines: ['ompl']` for move_group, and
        # MoveItPy given that flat list fails with "Failed to load any planning
        # pipelines" -- empirically observed. Translate it here so one builder
        # dict can serve both consumers.
        pipeline_names = params.pop('planning_pipelines', None)
        if pipeline_names:
            params['planning_pipelines'] = {
                'pipeline_names': list(pipeline_names),
                'namespace': '',
            }
        # The pipeline's own parameters (e.g. `ompl.*`) live under the pipeline
        # name at the root, which is where createPlanningPipelineMap looks when
        # the namespace is empty.

        # MoveItPy spins its own node; without `use_sim_time` it subscribes to
        # /joint_states against the wall clock while the sim publishes against
        # /clock, and its trajectory validator then times out ("couldn't receive
        # full current joint state within 1s") and every execution ABORTS.
        # Passing the bool here is safe: the earlier crash came from a *nested*
        # `qos_overrides.*` dict arriving via the launch param file, not from
        # this scalar.
        # Force a real bool: the launch supplies `use_sim_time` as a
        # substitution, so the override arrives as the STRING 'true'/'false'.
        # MoveItPy writes the config dict to a params file where a string is a
        # different parameter type than a bool, and rclpy's node then refuses
        # the clock's QoS override
        # ("InvalidParameterValueException: qos_overrides./clock.subscription.
        # durability could not be set") and the process aborts. A bool is what
        # the standalone probe used, and what works.
        raw = params.get('use_sim_time')
        if isinstance(raw, str):
            use_sim_time = raw.strip().lower() in ('true', '1', 'yes')
        elif raw is None:
            use_sim_time = bool(self.get_parameter('use_sim_time').value) \
                if self.has_parameter('use_sim_time') else False
        else:
            use_sim_time = bool(raw)
        params['use_sim_time'] = use_sim_time
        # rclpy auto-declares `qos_overrides./clock.subscription.durability`
        # for a sim-time node, and MoveItPy's node (created with
        # `automatically_declare_parameters_from_overrides(true)` on top of a
        # generated params file) intermittently aborts while setting it
        # ("InvalidParameterValueException: parameter
        # 'qos_overrides./clock.subscription.durability' could not be set",
        # SIGABRT). Declaring the override up front, with the type the clock
        # subscription actually wants, makes the value already present and
        # correctly typed when rclpy looks for it.
        params.setdefault('qos_overrides', {}).setdefault(
            '/clock/subscription', {}).setdefault(
                'durability', 'volatile')
        self._moveit_params = params
        if not params.get('robot_description'):
            self.get_logger().warn(
                'no robot_description parameter on cartesian_goal; MoveItPy '
                'will fail to build its model (the launch passes it)')
        return params

    # -- target resolution --------------------------------------------------

    def _resolve_target(self, request):
        """Return ``(target_pose_stamped_in_base, None)`` or ``(None, message)``."""
        pose_msg = request.target_pose
        if request.object_id:
            resolved = self._object_pose(request.object_id)
            if resolved is None:
                return None, ('object %r not found via the world service'
                              % request.object_id)
            pose_msg = resolved

        stamped = PoseStamped()
        stamped.header.frame_id = self._world_frame
        stamped.pose = pose_msg
        transform = self._lookup_with_wait(
            BASE_FRAME, self._world_frame, timeout_s=self._tf_wait_timeout_s)
        if transform is None:
            return None, ('no %s -> %s transform after %.0fs (is the TF chain '
                          'up? the launch publishes a static world -> base_link)'
                          % (BASE_FRAME, self._world_frame,
                             self._tf_wait_timeout_s))
        return apply_transform(stamped, transform), None

    def _lookup_with_wait(self, target, source, timeout_s):
        """Look up a transform, retrying until ``timeout_s`` elapses.

        A static transform is latched, but a *late-joining* listener only
        receives it on the next delivery -- and the goal node may be asked
        before its buffer has seen the frames. Rather than fail a goal on a
        startup race, wait for the chain (bounded), so the first request after
        bringup succeeds instead of needing a retry from the caller.
        """
        import time as _time

        deadline = _time.monotonic() + timeout_s
        while True:
            try:
                return self._tf_buffer.lookup_transform(
                    target, source, rclpy.time.Time())
            except TransformException as exc:
                last = exc
                if _time.monotonic() >= deadline:
                    self.get_logger().warn(
                        'TF lookup %s -> %s gave up after %.0fs: %s'
                        % (target, source, timeout_s, last))
                    return None
                _time.sleep(0.1)

    def _object_pose(self, object_id):
        """Return the world-frame pose of ``object_id``, or ``None``.

        Reuses PR1's world plumbing: the service returns the world JSON and
        ``robot_moveit.world_to_scene`` turns it into descriptors with frame and
        pose. The reference point is the object's own pose -- the world document
        stores the pose the scene builder uses -- *not* the origin plus a
        hand-tuned grasp offset: the skill layer owns grasp offsets.
        """
        if not self._world_client.service_is_ready():
            if not self._world_client.wait_for_service(
                    timeout_sec=_SERVICE_TIMEOUT_S):
                self.get_logger().warn('world service unavailable')
                return None
        future = self._world_client.call_async(GetWorld.Request())
        event = threading.Event()
        future.add_done_callback(lambda _f: event.set())
        if not event.wait(_SERVICE_TIMEOUT_S):
            self.get_logger().warn('world service call timed out')
            return None
        response = future.result()
        if response is None:
            return None
        descriptors = scene_objects_from_world_json(response.world_json)
        known = [d.object_id for d in descriptors]
        for descriptor in descriptors:
            if descriptor.object_id != object_id:
                continue
            from geometry_msgs.msg import Pose
            pose = Pose()
            pose.position.x, pose.position.y, pose.position.z = (
                descriptor.position)
            (pose.orientation.x, pose.orientation.y,
             pose.orientation.z, pose.orientation.w) = descriptor.orientation
            return pose
        self.get_logger().warn(
            'object %r not in the world service scene; known ids: %r'
            % (object_id, known))
        return None

    # -- Cartesian -> joint-space goal (see the module docstring) ---------

    def _joint_goal_for_position(self, moveit, side, target_xyz):
        """Return a joint-space goal state reaching ``target_xyz``, or ``None``.

        A bounded forward-kinematics search: discretise the arm's five active
        joints over their URDF bounds, compute the tip pose for each candidate
        via MoveIt's ``RobotState.get_global_link_transform``, and keep the
        candidate whose tip is closest to the target. Coarse by design -- it
        only has to land inside ``goal_tolerance_m``; the planner smooths the
        path from there.

        Why not IK: see the module docstring. The arm is 5-DOF, KDL's IK targets
        a full pose, and no single orientation covers the workspace; the FK
        search is the honest way to answer "which joint vector puts the tip
        here" for an under-actuated arm.

        The returned value is a ``moveit.core.RobotState`` (what
        ``set_goal_state(robot_state=...)`` takes -- *not* the message type),
        with the arm's joints set to the winning vector.
        """
        from moveit.core.robot_state import RobotState as MoveItRobotState

        group_name = '%s_arm' % side
        robot_model = moveit.get_robot_model()
        if not robot_model.has_joint_model_group(group_name):
            return None
        group = robot_model.get_joint_model_group(group_name)
        names = list(group.active_joint_model_names)
        # `active_joint_model_bounds` comes back as a list of SINGLE-ELEMENT
        # lists of ``VariableBounds`` (a pybind quirk: 5 joints -> 5 one-item
        # lists), NOT as flat doubles and NOT as (min, max) pairs. Both wrong
        # guesses raise at the unpack, which is how this was pinned down.
        bounds = []
        for entry in list(group.active_joint_model_bounds):
            variable = entry[0] if isinstance(entry, list) and entry else entry
            bounds.append((float(variable.min_position),
                           float(variable.max_position)))

        link_name = GOAL_LINK % side
        samples = self._ik_samples_per_joint
        state = MoveItRobotState(robot_model)
        # Zero every joint before searching: a fresh RobotState leaves the
        # non-arm joints (notably the prismatic column) at their uninitialized
        # defaults, and FK off a garbage column height puts the tip hundreds of
        # mm away from where it actually is -- which reads as "unreachable" for
        # a plainly reachable target (observed: residual 0.27 m in the node vs
        # 0.015 m for the same target in a standalone probe that zeroed first).
        state.set_to_default_values()
        state.update()

        def _distance_for(positions):
            state.set_joint_group_positions(group_name, positions)
            state.update()
            # `get_global_link_transform` returns a 4x4 numpy matrix (verified),
            # not a pose object -- the translation is its last column.
            transform = state.get_global_link_transform(link_name)
            return _distance(
                (float(transform[0][3]), float(transform[1][3]),
                 float(transform[2][3])), target_xyz)

        # Global grid over the full URDF range. A 5-DOF grid is expensive, so
        # the loop exits the moment a candidate lands inside tolerance -- a
        # reachable target is usually found well before the grid is exhausted.
        best = None
        best_distance = None
        budget = self._ik_max_candidates
        for count, index in enumerate(_product_of_ranges(len(names), samples)):
            if count >= budget:
                break
            # Sample strictly inside the bounds: the endpoints are the URDF's
            # exact min/max, and a value *at* the limit is rejected by MoveIt's
            # start-state bounds check (START_STATE_INVALID, -26) -- which would
            # make a genuinely in-collision goal look like a bad-state goal.
            positions = [
                low + (high - low) * ((index[i] + 0.5) / samples)
                for i, (low, high) in enumerate(bounds)
            ]
            distance = _distance_for(positions)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best = positions
                if best_distance <= self._goal_tolerance_m:
                    break

        if best is None or best_distance > self._goal_tolerance_m:
            self.get_logger().warn(
                '%s: FK search best residual %s for (%.2f, %.2f, %.2f) '
                '(tolerance %.2f)'
                % (side, ('none' if best_distance is None
                          else '%.4f m' % best_distance),
                   target_xyz[0], target_xyz[1], target_xyz[2],
                   self._goal_tolerance_m))
            return None

        # `set_goal_state(robot_state=...)` takes moveit_py's OWN
        # `moveit.core.RobotState` (C++), not a `moveit_msgs/RobotState`
        # (verified against the pybind: passing the message raises
        # "incompatible function arguments"). `state` already holds the winning
        # joint vector, so hand it straight over.
        self.get_logger().info(
            '%s Cartesian goal -> joint state (residual %.4f m)'
            % (side, best_distance))
        return state

    # -- R3: the shoulder the reach sphere is centred on --------------------

    def _shoulder_xyz(self, side):
        """Return the ``side`` shoulder origin in ``BASE_FRAME``, or ``None``.

        ``<side>_shoulder_link`` is the arm chain's base, so its TF origin *is*
        the shoulder point the reach sphere is centred on -- and because TF
        composes through the prismatic column it is already at the current lift
        height.
        """
        link = '%s_shoulder_link' % side
        transform = self._lookup_with_wait(
            BASE_FRAME, link, timeout_s=self._tf_wait_timeout_s)
        if transform is None:
            return None
        translation = transform.transform.translation
        return (translation.x, translation.y, translation.z)

    # -- request handling ---------------------------------------------------

    def _handle(self, request, response):
        """Answer one CartesianGoal request; never raise out of the handler.

        The service handler runs on a multi-threaded executor, so an uncaught
        exception aborts the *process* (observed: a ``ValueError`` from a bad
        MoveIt attribute killed the node, and every subsequent call then failed
        as "planning interface unavailable" -- a far worse failure mode than a
        clean error response). Everything below the guard is expected to raise
        only its own ``_fail``; this catches the unexpected.
        """
        try:
            return self._handle_request(request, response)
        except Exception as exc:  # pragma: no cover - defensive
            self.get_logger().error('cartesian_goal handler raised: %s' % exc)
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE,
                'internal error: %s' % exc)

    def _handle_request(self, request, response):
        """Answer one CartesianGoal request (see the module docstring)."""
        side = request.arm
        if side not in ('left', 'right'):
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.INVALID_GROUP_NAME,
                "arm must be 'left' or 'right', got %r" % side)

        if not _target_is_identifiable(request):
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE,
                'either object_id or use_target_pose is required')

        target, error = self._resolve_target(request)
        if target is None:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE, error)

        # -- R3: envelope pre-check, BEFORE planning (see module docstring).
        shoulder = self._shoulder_xyz(side)
        if shoulder is None:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE,
                'no %s_shoulder_link transform; cannot apply the reach '
                'envelope check' % side)
        target_xyz = (target.pose.position.x, target.pose.position.y,
                      target.pose.position.z)
        if is_out_of_reach(target_xyz, shoulder, self._reach_radius):
            self.get_logger().info(
                'out_of_reach: %r %.2f m from the %s shoulder'
                % (request.object_id or 'explicit pose',
                   _distance(target_xyz, shoulder), side))
            response.success = False
            response.status = STATUS_OUT_OF_REACH
            # MoveIt's own code for "IK could not reach it": the pre-check is
            # what makes it unambiguous, and the code keeps the wire format a
            # real MoveItErrorCodes value.
            response.error_code = MoveItErrorCodes.NO_IK_SOLUTION
            response.message = (
                'target (%.2f, %.2f, %.2f) is %.2f m from the %s shoulder, '
                'beyond the %.2f m reach'
                % (target_xyz[0], target_xyz[1], target_xyz[2],
                   _distance(target_xyz, shoulder), side, self._reach_radius))
            return response

        moveit = self._moveit_py()
        if moveit is None:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE,
                'MoveIt planning interface unavailable')

        return self._plan_and_execute(
            moveit, side, target, request.object_id, response)

    def _plan_and_execute(self, moveit, side, target, object_id, response):
        """Plan the pose goal and execute it; fill ``response``."""
        group_name = '%s_arm' % side
        try:
            component = moveit.get_planning_component(group_name)
        except Exception as exc:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.INVALID_GROUP_NAME,
                'no planning component %r: %s' % (group_name, exc))

        target_xyz = (target.pose.position.x, target.pose.position.y,
                      target.pose.position.z)
        goal_state = self._joint_goal_for_position(moveit, side, target_xyz)
        if goal_state is None:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.NO_IK_SOLUTION,
                'no arm configuration puts the gripper within %.2f m of '
                '(%.2f, %.2f, %.2f)'
                % (self._goal_tolerance_m, target_xyz[0], target_xyz[1],
                   target_xyz[2]))
        component.set_goal_state(robot_state=goal_state)

        try:
            params = self._plan_request_parameters(moveit)
            # NB the kwarg is `single_plan_parameters` (the pybind name), not
            # `plan_parameters` (the docstring's prose) -- see the module
            # docstring's "Empirically-pinned moveit_py API" section.
            plan_result = component.plan(single_plan_parameters=params)
        except Exception as exc:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE,
                'planning raised: %s' % exc)

        error_code = int(plan_result.error_code.val)
        contacts = list(getattr(plan_result, 'contacts', []) or [])
        if error_code != MoveItErrorCodes.SUCCESS:
            status, reason = classify_plan_failure(error_code, contacts)
            response.success = False
            response.status = status
            response.error_code = error_code
            response.message = '%s (object_id=%r, arm=%s)' % (
                reason, object_id, side)
            return response

        try:
            # `execute` takes the trajectory AND a controllers list; the
            # binding has no default for the second argument (verified: omitting
            # it raises "incompatible function arguments"). An empty list means
            # "let the controller manager pick", which is what msscm does.
            execution_status = moveit.execute(plan_result.trajectory, [])
        except Exception as exc:
            return self._fail(
                response, STATUS_FAILURE, MoveItErrorCodes.FAILURE,
                'execution raised: %s' % exc)

        # ExecutionStatus.__bool__ is ``status == SUCCEEDED`` (MoveIt core's own
        # definition), so the bool is the authoritative success test.
        succeeded = bool(execution_status)
        response.success = succeeded
        response.error_code = (MoveItErrorCodes.SUCCESS if succeeded
                               else MoveItErrorCodes.FAILURE)
        response.status = STATUS_SUCCESS if succeeded else STATUS_FAILURE
        response.message = (
            'executed %s arm goal for %r (execution=%s)'
            % (side, object_id or 'explicit pose',
               _execution_status_text(execution_status)))
        return response

    def _plan_request_parameters(self, moveit):
        """Build PlanRequestParameters from the MoveItPy instance.

        ``PlanRequestParameters(moveit_cpp, ns)`` reads the defaults off the
        parameter server namespace; the caller then overrides the fields it
        cares about. The ns is left to ``load``'s default (the root), which is
        where the launch puts ``ompl``/``planning_pipelines``.
        """
        from moveit.planning import PlanRequestParameters

        params = PlanRequestParameters(moveit, '')
        params.planning_pipeline = PLANNING_PIPELINE
        params.planning_time = self._planning_time
        params.planning_attempts = 5
        params.max_velocity_scaling_factor = 0.5
        params.max_acceleration_scaling_factor = 0.5
        return params

    @staticmethod
    def _fail(response, status, error_code, message):
        """Fill ``response`` with a failure and return it."""
        response.success = False
        response.status = status
        response.error_code = error_code
        response.message = message
        return response


def _execution_status_text(execution_status):
    """Return the ExecutionStatus string, or a repr when it has no asString."""
    status = getattr(execution_status, 'status', execution_status)
    as_string = getattr(status, 'asString', None)
    if callable(as_string):
        return str(as_string())
    return repr(status)


def main(args=None):
    """Spin the cartesian_goal node."""
    rclpy.init(args=args)
    node = CartesianGoalNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        moveit = node._moveit
        if moveit is not None:
            try:
                moveit.shutdown()
            except Exception:
                pass
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
