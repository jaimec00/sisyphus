# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The semantic navigation bridge: ``NavigateToLocation`` -> Nav2 -> world.

This is PR3 of the Nav2 work (issue #135, RULING 1/D30/D36): the seam that lets
the *brain's* semantic ``navigate_to(location)`` actually drive the base on the
classical/real track.  It lives here, on the ROS 2 side, and never in
``robot_backends`` / ``robot_mcp`` / ``robot_world`` -- those stay ROS-free at
runtime (D30).  The ROS-free ``navigate_to`` (Mock / MuJoCoBackend teleport) is
unchanged; it remains the in-process sim's fast path.

The seam is deliberately **semantic**, exactly like the skill:

* a goal names a *location the world model already knows*, never a pose and
  never a velocity.  The brain has no business with ``/cmd_vel`` (invariant 1),
  and the pose resolution is the bridge's job, not the brain's;
* the result is success/error, mirroring the store/backend refusal wording.

What a goal does
----------------
1. **Resolve** the name against the world: query ``/world_query/get_world``
   (the D35 read path -- the bridge consumes the *service*, it never reaches
   into ``robot_world`` internals or opens the store itself), parse the
   canonical JSON with ``robot_world.WorldDocument.from_dict`` (the strict
   parser, RULING 3 -- never hand-rolled), and read ``document.locations[name]``.
   An unknown name is refused with the location list, mirroring the store's own
   wording (R2).
2. **Drive** there: build a ``nav2_msgs/action/NavigateToPose`` goal in the
   ``map`` frame -- the pose copied field-by-field by
   :func:`pose_from_robot_pose`, the pure helper -- and send it through an
   ``rclpy.action.ActionClient`` on ``/navigate_to_pose``.  Nav2 owns
   planning/control from there (RULING 4: MPPI-Omni, NavFn, ground-truth odom).
3. **Record** the arrival: on ``STATUS_SUCCEEDED`` call
   ``/world_query/set_start_location`` with the same name (R3), so
   ``robot_world.start_location`` tracks where the base actually is.  The world
   query node stays the *sole writer* (D35) -- the bridge asks the service, it
   does not mutate a store of its own.

Anything other than success (a rejected/aborted/timed-out goal, no Nav2 stack)
returns ``success=false`` with a descriptive reason; the world is left alone.

Pure half vs. ROS half
----------------------
:func:`pose_from_robot_pose` is a pure, graph-free function -- the same split
``omni_base_controller.body_to_wheel`` and
``static_map.occupancy_grid_from_document`` use -- so the field-by-field pose
copy is unit-testable with no ROS runtime.  Everything else is the thin rclpy
shell around it.

Why the node needs *two* threads (``main``)
-------------------------------------------
A goal handler runs the whole resolve -> drive -> record chain synchronously in
its ``execute_callback``, and each step blocks on a nested service/action
client (``GetWorld``, ``NavigateToPose``, ``SetStartLocation``) while it polls
its future.  A single-threaded spin would therefore deadlock: the very thread
blocked in the poll is the one that must deliver the client's response.  So
``main`` spins a :class:`rclpy.executors.MultiThreadedExecutor`, and the action
server gets a :class:`rclpy.callback_groups.ReentrantCallbackGroup` -- without
the reentrant group the executor's threads are still serialised behind the
default (mutually-exclusive) group's lock, and the nested callbacks starve the
same way.  The nested clients stay in the default group: exactly one is active
at a time, so serialising *them* is correct.
"""

import json
import time

from geometry_msgs.msg import Pose as RosPose
from nav2_msgs.action import NavigateToPose
import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from robot_nav_interfaces.action import NavigateToLocation
from robot_skills import Pose
from robot_world import WorldDocument
from robot_world_ros_interfaces.srv import GetWorld, SetStartLocation

__all__ = ['SemanticNavNode', 'main', 'pose_from_robot_pose', 'resolve_location']

#: The world query service the location is resolved from (D35/R2).
GET_WORLD_SERVICE = '/world_query/get_world'

#: The world write service an arrival is recorded through (R3).
SET_START_LOCATION_SERVICE = '/world_query/set_start_location'

#: Nav2's plan-and-drive action (PR2/#124 wired the stack that serves it).
NAVIGATE_TO_POSE_ACTION = '/navigate_to_pose'

#: Frame every goal is expressed in: the world frame, which is ``map`` on the
#: classical track (RULING 3 -- ground-truth odom makes map == world).
MAP_FRAME = 'map'

#: ``action_msgs/msg/GoalStatus.STATUS_SUCCEEDED`` -- the goal reached its
#: target.  Hard-coded, like the sim acceptance test does, so this module
#: imports no action_msgs just for one constant.
STATUS_SUCCEEDED = 4

#: How long to wait for each counterpart to appear before giving up on a goal.
SERVER_WAIT_S = 30.0

#: How long a NavigateToPose goal gets to complete before the bridge gives up.
NAVIGATION_TIMEOUT_S = 120.0


def pose_from_robot_pose(pose: Pose) -> RosPose:
    """Return a ``geometry_msgs/Pose`` message from a ``robot_skills.Pose``.

    A pure, field-by-field copy -- the two are structurally identical (three
    float64 position components plus four float64 quaternion components, x y z
    w in both), so this is lossless and needs no graph.  Kept module-level and
    exported so it is unit-testable exactly like
    :func:`robot_nav.omni_base_controller.body_to_wheel` (R4).

    The quadrants of the quaternion are the *same convention* in both types
    (``x``, ``y``, ``z``, ``w``), so no reordering happens here; a copy that
    silently swapped ``z`` and ``w`` would turn a yaw into a pitch and the base
    would drive to a mirrored pose, which is why the copy is written out rather
    than a ``**asdict``.
    """
    message = RosPose()
    message.position.x = float(pose.position.x)
    message.position.y = float(pose.position.y)
    message.position.z = float(pose.position.z)
    message.orientation.x = float(pose.orientation.x)
    message.orientation.y = float(pose.orientation.y)
    message.orientation.z = float(pose.orientation.z)
    message.orientation.w = float(pose.orientation.w)
    return message


def resolve_location(document: WorldDocument, name: str):
    """Return the reference pose for ``name``, or ``None`` if it is not a location.

    The pure half of R2's resolution, split out so a test can exercise the
    *lookup and its refusal* with no graph and no service: the store's
    ``locations`` map is the single source of truth, and this is the one place
    the bridge reads it.  An unknown name yields ``None`` so the caller can
    build the refusal (which needs the location *list*, hence the responsibility
    stays with the caller -- this function answers "where is it", not "what do I
    say when it is not there").
    """
    return document.locations.get(name)


def unknown_location_error(name: str, document: WorldDocument) -> str:
    """Return the refusal for an unknown location, mirroring the store's wording.

    ``robot_world``'s own refusals read
    ``unknown location 'X'; known locations: a, b`` (the store mutator and the
    backend's navigate refusal use exactly that shape), so the bridge reports
    the same way rather than inventing a second phrasing for the same fact.
    """
    known = ', '.join(sorted(document.locations))
    return f'unknown location {name!r}; known locations: {known}'


class SemanticNavNode(Node):
    """Serve ``NavigateToLocation`` by driving Nav2 and updating the world.

    Parameters (declared with defaults, so a bare ``ros2 run`` works):

    * ``world_service`` (string, default ``/world_query/get_world``) -- where
      the location is resolved from.
    * ``set_start_location_service`` (string, default
      ``/world_query/set_start_location``) -- where the arrival is recorded.
    * ``navigate_action`` (string, default ``/navigate_to_pose``) -- Nav2's
      NavigateToPose action.
    * ``server_wait_s`` (double, default 30) -- how long a goal waits for each
      counterpart server to appear (they come up alongside this node in the
      bringup).
    * ``navigation_timeout_s`` (double, default 120) -- how long a goal waits
      for Nav2 to finish.
    """

    def __init__(self) -> None:
        """Declare parameters and offer the ``navigate_to_location`` action."""
        super().__init__('semantic_nav')

        self.declare_parameter('world_service', GET_WORLD_SERVICE)
        self.declare_parameter(
            'set_start_location_service', SET_START_LOCATION_SERVICE)
        self.declare_parameter('navigate_action', NAVIGATE_TO_POSE_ACTION)
        self.declare_parameter('server_wait_s', SERVER_WAIT_S)
        self.declare_parameter('navigation_timeout_s', NAVIGATION_TIMEOUT_S)

        self._world_service = self._string_param('world_service')
        self._set_start_service = self._string_param('set_start_location_service')
        self._navigate_action = self._string_param('navigate_action')

        # The action clients are created last and left disconnected until a
        # goal arrives: the servers come up alongside this node in the bringup,
        # so waiting at construction would stall the launch.  ``_wait_for``
        # bounds each wait per goal instead.
        self._navigate_client = ActionClient(
            self, NavigateToPose, self._navigate_action)
        self._set_start_client = self.create_client(
            SetStartLocation, self._set_start_service)
        # The action server is the one callback that *blocks* (its handler calls
        # the nested clients and waits on their futures), so it gets a reentrant
        # callback group of its own: paired with the MultiThreadedExecutor in
        # ``main``, that lets a nested client's response callback be serviced
        # while the handler is still running.  Under the default
        # mutually-exclusive group the handler would hold the group's lock for
        # the whole goal and starve every nested callback, however many threads
        # spin the executor.
        self._action_server = rclpy.action.ActionServer(
            self, NavigateToLocation, 'navigate_to_location',
            execute_callback=self._execute,
            callback_group=ReentrantCallbackGroup(),
        )
        self.get_logger().info(
            'semantic nav bridge up: navigate_to_location -> %s, world %s, '
            'arrival recorded via %s'
            % (self._navigate_action, self._world_service, self._set_start_service))

    # -- helpers -----------------------------------------------------------

    def _string_param(self, name: str) -> str:
        """Return a declared string parameter's value."""
        return self.get_parameter(name).get_parameter_value().string_value

    def _wait_for(self, predicate, what: str, timeout: float) -> bool:
        """Poll (bounded) until ``predicate`` holds; log and return False on timeout.

        A thin, testable shim over the bounded retry each client does: the
        servers appear asynchronously, so every use is a retry rather than a
        fixed sleep (R4's "wait/retry for the two servers").  The loop does not
        spin the executor itself -- it runs on the action server's own thread,
        and the *other* executor threads deliver whatever this poll is waiting
        for (the module docstring's two-thread note); that split is precisely
        what makes the multi-threaded spin necessary.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        self.get_logger().error(f'timed out after {timeout:.0f}s waiting for {what}')
        return False

    def _read_world(self, timeout: float):
        """Return the world document from the query service, or ``None`` on failure."""
        client = self.create_client(GetWorld, self._world_service)
        try:
            if not self._wait_for(
                    client.service_is_ready, f'{self._world_service}', timeout):
                return None
            future = client.call_async(GetWorld.Request())
            if not self._wait_for(lambda: future.done(), 'the world query', timeout):
                return None
            response = future.result()
            if response is None:
                self.get_logger().error('world query returned no response')
                return None
            try:
                return WorldDocument.from_dict(json.loads(response.world_json))
            except (ValueError, TypeError) as exc:
                self.get_logger().error(f'world document refused: {exc}')
                return None
        finally:
            self.destroy_client(client)

    def _drive_to(self, goal_pose: RosPose, timeout: float):
        """Send a NavigateToPose goal; return ``(succeeded, status_or_error)``.

        Waits (bounded) for the Nav2 action server, sends the goal in the
        ``map`` frame, and waits for the result.  Returns ``(True, status)``
        when Nav2 reports ``STATUS_SUCCEEDED`` and ``(False, message)``
        otherwise -- any other status, a rejection, or a timeout.
        """
        if not self._wait_for(
                self._navigate_client.server_is_ready,
                f'{self._navigate_action}', timeout):
            return False, f'Nav2 action {self._navigate_action} is not available'
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = MAP_FRAME
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose = goal_pose
        send_future = self._navigate_client.send_goal_async(goal)
        if not self._wait_for(lambda: send_future.done(), 'the goal to be accepted', timeout):
            return False, 'Nav2 did not accept the goal in time'
        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            return False, 'Nav2 rejected the navigation goal'
        result_future = goal_handle.get_result_async()
        if not self._wait_for(
                lambda: result_future.done(), 'Nav2 to finish', timeout):
            return False, f'Nav2 did not reach the goal within {timeout:.0f}s'
        status = int(result_future.result().status)
        if status != STATUS_SUCCEEDED:
            return False, f'Nav2 navigation failed (goal status {status})'
        return True, status

    def _record_arrival(self, location: str, timeout: float) -> str:
        """Call ``set_start_location``; return ``''`` on success or the error text.

        The world query node is the sole writer (D35), so the bridge asks the
        service rather than mutating anything itself; a refusal (or a missing
        service) is reported back as the goal's error, because a navigation
        that drove the base but could not record where it is has *not* fully
        honoured the semantic contract.
        """
        if not self._wait_for(
                self._set_start_client.service_is_ready,
                f'{self._set_start_service}', timeout):
            return f'world write service {self._set_start_service} is not available'
        request = SetStartLocation.Request()
        request.location = location
        future = self._set_start_client.call_async(request)
        if not self._wait_for(lambda: future.done(), 'the world write', timeout):
            return 'world write service did not answer in time'
        response = future.result()
        if response is None:
            return 'world write service returned no response'
        if not response.success:
            return response.error or 'world write was refused'
        return ''

    # -- the action --------------------------------------------------------

    def _execute(self, goal_handle):
        """Resolve -> drive -> record, and fill the result (R2/R3/R4)."""
        result = NavigateToLocation.Result()
        location = goal_handle.request.location
        server_wait = float(self.get_parameter(
            'server_wait_s').get_parameter_value().double_value)
        nav_timeout = float(self.get_parameter(
            'navigation_timeout_s').get_parameter_value().double_value)

        document = self._read_world(server_wait)
        if document is None:
            result.success = False
            result.error = (
                f'could not read the world from {self._world_service}')
            goal_handle.abort()
            return result

        pose = resolve_location(document, location)
        if pose is None:
            result.success = False
            result.error = unknown_location_error(location, document)
            goal_handle.abort()
            return result

        succeeded, detail = self._drive_to(pose_from_robot_pose(pose), nav_timeout)
        if not succeeded:
            result.success = False
            result.error = str(detail)
            goal_handle.abort()
            return result

        write_error = self._record_arrival(location, server_wait)
        if write_error:
            # The base arrived but the world could not be told; the semantic
            # contract (the world and the robot agree where the robot is) is
            # not met, so this is a failure with the reason, not a pass.
            result.success = False
            result.error = write_error
            goal_handle.abort()
            return result

        result.success = True
        result.error = ''
        goal_handle.succeed()
        return result


def main(args=None) -> None:
    """Run the semantic navigation bridge until interrupted.

    Spins a :class:`~rclpy.executors.MultiThreadedExecutor`, not
    ``rclpy.spin``'s single thread: a goal handler blocks while it waits on its
    nested service/action clients, so it needs sibling threads to service their
    response callbacks (together with the action server's reentrant callback
    group -- see the module docstring).
    """
    rclpy.init(args=args)
    node = SemanticNavNode()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
