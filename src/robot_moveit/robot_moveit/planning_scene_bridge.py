# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

r"""Feed the `robot_world` scene into the MoveIt planning scene (PR1 / R6).

This node is the runtime half of the world -> MoveIt bridge. Once per
``publish_period`` it:

1. calls ``/world_query/get_world`` (``robot_world_ros_interfaces/srv/GetWorld``)
   -- the world is read through the service, never by importing the store or
   reading its file (D23/D35: one source of truth, and this node is a *reader*
   of it, not a second one);
2. parses the canonical JSON with ``robot_world``'s own ``WorldDocument`` (the
   schema has one owner, and it is not this package);
3. turns each object into a ``moveit_msgs/CollisionObject`` via the pure
   transform in :mod:`robot_moveit.world_to_scene` and the estimated geometry
   table in :mod:`robot_moveit.scene_geometry` (R5);
4. applies the whole set to ``move_group`` with a single diff
   ``PlanningScene`` through ``/apply_planning_scene``
   (``moveit_msgs/srv/ApplyPlanningScene``).

Why the ``/apply_planning_scene`` service and not ``moveit-py`` (deviation from
R6, flagged in implementation.md)
----------------------------------------------------------------------------
R6 says "via moveit-py's ``PlanningSceneInterface``". The installed moveit-py
(``ros-jazzy-moveit-py`` 2.12.4) does **not** expose a python
``PlanningSceneInterface``: ``moveit.planning`` offers ``MoveItPy``,
``PlanningComponent``, ``PlanningSceneMonitor`` and the locked-scene context
managers, and the C++ ``planning_scene_interface`` headers ship only as C++.
Using it would mean constructing a ``MoveItPy`` (a full moveit_cpp stack with
its own robot model and planning scene -- a *second* planning scene, not the
``move_group``'s), which is heavier and couples this node to a second model
load. The service is the same operation MoveIt's own interface wraps
(``apply_collision_object`` -> ``/apply_planning_scene``), speaks to the
``move_group`` this PR actually stands up, and needs no local robot model. The
*effect* R6 asks for -- world objects appear as collision objects in the
planning scene -- is identical; the mechanism is the service rather than the
unavailable python class. This is recorded as a deviation, not silently
substituted.

Idempotence and removals
------------------------
The diff carries ``ADD`` for every current world object, so re-running replaces
each object's geometry in place (``CollisionObject`` ADD semantics: "If the
object previously existed, it is replaced"). Objects that leave the world are
``REMOVE``\\d: the node remembers the ids it applied last time and emits a
remove for each id no longer present, so the planning scene tracks the world
rather than accumulating stale objects. The very first pass removes nothing
(there is no previous set).

A world read that fails (service absent, timeout, bad JSON) is logged and the
node keeps its last-applied state -- it never clears the scene on a transient
error, because "the world said nothing" is not "the world is empty".
"""

import threading

from geometry_msgs.msg import Pose
from moveit_msgs.msg import CollisionObject, PlanningScene
from moveit_msgs.srv import ApplyPlanningScene
import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from robot_moveit.scene_geometry import BOX, CYLINDER
from robot_moveit.world_to_scene import (
    scene_objects_from_world_json,
    SceneObject,
)
from robot_world_ros_interfaces.srv import GetWorld
from shape_msgs.msg import SolidPrimitive

#: Map this module's shape kind -> shape_msgs/SolidPrimitive type constant and
#: the dimension ordering that type expects.
_PRIMITIVE_TYPE = {BOX: SolidPrimitive.BOX, CYLINDER: SolidPrimitive.CYLINDER}
_PRIMITIVE_DIMENSIONS = {
    # Box: (x, y, z) extents in the same order SolidPrimitive wants them.
    BOX: lambda dims: [float(dims[0]), float(dims[1]), float(dims[2])],
    # Cylinder: SolidPrimitive orders [HEIGHT, RADIUS] (mesh/height first),
    # while the table stores (radius, height) -- the flip is deliberate and
    # the only place it happens.
    CYLINDER: lambda dims: [float(dims[1]), float(dims[0])],
}


def collision_object_for(scene_object: SceneObject) -> CollisionObject:
    """Build a ``CollisionObject`` (operation ADD) for one descriptor.

    Pure apart from constructing ROS messages (no graph), so the unit test can
    call it directly. The object's frame, id and pose all come straight from the
    descriptor; the primitive comes from the R5 table.
    """
    primitive = SolidPrimitive()
    primitive.type = _PRIMITIVE_TYPE[scene_object.shape.kind]
    primitive.dimensions = _PRIMITIVE_DIMENSIONS[scene_object.shape.kind](
        scene_object.shape.dimensions)

    pose = Pose()
    pose.position.x, pose.position.y, pose.position.z = scene_object.position
    (pose.orientation.x, pose.orientation.y,
     pose.orientation.z, pose.orientation.w) = scene_object.orientation

    collision = CollisionObject()
    collision.header.frame_id = scene_object.frame_id
    collision.id = scene_object.object_id
    collision.primitives = [primitive]
    # One primitive at the object's own pose: the primitive's pose is identity
    # in the object frame (a later PR that places geometry off the origin, e.g.
    # a plate lying on its side, sets primitive_poses instead of moving the id).
    collision.primitive_poses = [Pose()]
    collision.operation = CollisionObject.ADD
    return collision


def diff_scene(descriptors, previously_applied_ids):
    """Return the ``CollisionObject`` list that makes the scene match the world.

    Every current object is ADDed (replace-in-place), and every id that was
    applied last time but is gone now is REMOVEd. ``previously_applied_ids`` is
    the id set the caller applied on its previous pass (empty on the first).
    Pure apart from message construction.
    """
    current_ids = {d.object_id for d in descriptors}
    objects = [collision_object_for(d) for d in descriptors]
    for stale_id in sorted(set(previously_applied_ids) - current_ids):
        remove = CollisionObject()
        remove.id = stale_id
        remove.operation = CollisionObject.REMOVE
        objects.append(remove)
    return objects


class PlanningSceneBridge(Node):
    """Polls the world service and mirrors its objects into the planning scene."""

    def __init__(self):
        """Configure params, clients and the poll timer (see the class doc)."""
        super().__init__('planning_scene_bridge')

        self.declare_parameter('world_service', '/world_query/get_world')
        self.declare_parameter('apply_service', '/apply_planning_scene')
        # 2 s: the world changes on human/skill actions, not at control rate,
        # and the apply service is a full-scene diff -- faster would be chatter.
        self.declare_parameter('poll_period_s', 2.0)
        self.declare_parameter('service_timeout_s', 10.0)

        world_service = self.get_parameter('world_service').value
        apply_service = self.get_parameter('apply_service').value
        self._poll_period = float(self.get_parameter('poll_period_s').value)
        self._timeout = float(self.get_parameter('service_timeout_s').value)

        # Reentrant group + a multi-threaded executor: the poll timer blocks on
        # the world call and then on the apply call, and both clients live on
        # one node -- a single-threaded executor would deadlock the second call
        # behind the first. Reentrant (not mutually exclusive) is required for
        # the same nested-call reason.
        self._group = ReentrantCallbackGroup()
        self._world_client = self.create_client(
            GetWorld, world_service, callback_group=self._group)
        self._apply_client = self.create_client(
            ApplyPlanningScene, apply_service, callback_group=self._group)

        self._applied_ids = set()
        self._warned_labels = set()
        self._lock = threading.Lock()

        self._timer = self.create_timer(
            self._poll_period, self._tick, callback_group=self._group)
        self.get_logger().info(
            'planning_scene_bridge up: %s -> %s every %.1fs'
            % (world_service, apply_service, self._poll_period))

    # -- world read ---------------------------------------------------------

    def _read_world_json(self):
        """Return the world JSON string, or ``None`` if the call did not land."""
        if not self._world_client.service_is_ready():
            if not self._world_client.wait_for_service(timeout_sec=self._timeout):
                self.get_logger().warn(
                    'world service not available; keeping the last scene')
                return None
        future = self._world_client.call_async(GetWorld.Request())
        event = threading.Event()
        future.add_done_callback(lambda _f: event.set())
        if not event.wait(self._timeout):
            self.get_logger().warn('world call timed out; keeping the last scene')
            return None
        response = future.result()
        if response is None:
            self.get_logger().warn('world call returned no response')
            return None
        return response.world_json

    # -- planning scene write ----------------------------------------------

    def _apply(self, objects):
        """Apply a list of CollisionObjects as one diff; True on success."""
        if not self._apply_client.service_is_ready():
            if not self._apply_client.wait_for_service(timeout_sec=self._timeout):
                self.get_logger().warn(
                    'apply_planning_scene not available; is move_group up?')
                return False
        scene = PlanningScene()
        # A diff scene: only the objects named below are touched, so the robot
        # state and any other monitor's objects survive the update.
        scene.is_diff = True
        scene.world.collision_objects = list(objects)

        request = ApplyPlanningScene.Request()
        request.scene = scene
        future = self._apply_client.call_async(request)
        event = threading.Event()
        future.add_done_callback(lambda _f: event.set())
        if not event.wait(self._timeout):
            self.get_logger().warn('apply_planning_scene call timed out')
            return False
        response = future.result()
        if response is None or not response.success:
            self.get_logger().warn('move_group refused the planning-scene update')
            return False
        return True

    # -- loop ---------------------------------------------------------------

    def _tick(self):
        world_json = self._read_world_json()
        if world_json is None:
            return
        try:
            descriptors = scene_objects_from_world_json(world_json)
        except (ValueError, TypeError) as error:
            # The world owns its schema; a payload this build cannot parse is
            # reported, never guessed at, and never allowed to clear the scene.
            self.get_logger().error('could not parse world document: %s' % error)
            return

        self._log_estimated_labels(descriptors)

        with self._lock:
            objects = diff_scene(descriptors, self._applied_ids)
        if not self._apply(objects):
            return
        with self._lock:
            self._applied_ids = {d.object_id for d in descriptors}

    def _log_estimated_labels(self, descriptors):
        """Warn once per unknown label (R5: unknown -> default box + WARN)."""
        from robot_moveit.scene_geometry import known_labels
        known = known_labels()
        for descriptor in descriptors:
            label = descriptor.label
            if label not in known and label not in self._warned_labels:
                self._warned_labels.add(label)
                self.get_logger().warn(
                    'label %r has no geometry in the R5 table; using the '
                    'default 5 cm box (object %r)'
                    % (label, descriptor.object_id))


def main(args=None):
    """Entry point: spin the bridge on a multi-threaded executor."""
    rclpy.init(args=args)
    node = PlanningSceneBridge()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
