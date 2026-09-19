# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world-state service: expose the store's document over ROS 2 (read+write).

This is the ROS half of D35's split. ``robot_world`` is -- and stays -- a pure
Python store with no ROS at runtime (D30): it is what the MuJoCo swap
inherits, and it must keep working from a plain pytest run with no graph. This
node is the small ROS-facing shell around it: it owns a single
:class:`~robot_world.FileWorldStore` and answers two questions --

* *what does the world look like right now?* -- ``/world_query/get_world``
  returns the store's own canonical JSON;
* *change the world* -- ``/world_query/update_object_pose``,
  ``/world_query/add_object`` and ``/world_query/remove_object`` mutate one
  object at a time on that same store.

Read path: why a JSON string and not typed fields (R2/D23)
---------------------------------------------------------
The query response is ``robot_world.document_text(store.document())``: the
exact bytes the store would write to disk. A typed message
(``WorldLocation[]`` plus ``geometry_msgs/Pose``, an enum for ``held_by``, a
separate schema field) would be a *second representation* of the same scene,
and one that drifts: float64 rounds the store's floats, ``held_by`` needs a
mapping that can rot, and the message schema forks from the document schema as
it evolves. Carrying the canonical JSON is what makes the query return the
seed world "exactly as the store reads it" -- byte-for-byte -- and makes the
service a thin adapter rather than a second source of truth (D23).

Write path: three per-object ops, typed poses (R1/R2)
-----------------------------------------------------
The write surface is three services, one per object-store mutator -- never a
single full-document write. The store's mutators already encode the correct
boundary: perception (invariant 4) observes *objects with poses*; it does not
observe the human-curated locations map, ``start_location`` or
``start_column_height``. A full-document ``UpdateWorld`` would force perception
to echo back scene infrastructure it does not know (or force a merge step
here) -- a second representation again (D23/D35). Each per-object op is also
naturally atomic: one mutation is one temp-file + ``os.replace`` commit (D23's
"one batch = one write").

Unlike the read path, the write ops *are* field-shaped, so they use typed
fields and ``geometry_msgs/Pose`` -- which is byte-structurally identical to
the store's :class:`robot_skills.Pose` (three float64 position components plus
four float64 quaternion components). Building the store pose from the message
is a lossless copy, not a re-encoding that could round floats or re-map enums,
and the store's own strictness (non-finite floats, an all-zero quaternion) runs
anyway inside ``Pose``/``Quaternion``'s constructors.

Every write handler mutates ``self._store`` -- the *same*
:class:`~robot_world.FileWorldStore` the read handler snapshots -- so the node
is the **sole writer**, and read-after-write is fresh *by construction*: there
is no separate in-memory snapshot that can go stale, because read and write
share one store instance, and each store mutation commits to disk atomically
in the same call. Handlers catch ``(ValueError, TypeError)`` -- the two types
every store/serialization refusal raises (``WorldStoreError`` and
``SerializationError`` are both ``ValueError`` subclasses) -- and map them to
``success=False`` with the reason in ``error``; they never swallow anything
else.

The store is constructed **once**, at node start, not per request: opening the
live-state file is startup work (it may seed a missing file or refuse a corrupt
one -- D23), and a query is a snapshot of in-memory state.
"""

import os

from geometry_msgs.msg import Pose as RosPose
import rclpy
from rclpy.node import Node
from robot_skills import Point, Pose, Quaternion
from robot_world import document_text, FileWorldStore, WorldObject
from robot_world_ros_interfaces.srv import (
    AddObject,
    GetWorld,
    RemoveObject,
    UpdateObjectPose,
)

__all__ = ['WorldQueryNode', 'main']

#: Environment variable naming the live-state file, used when the
#: ``world_state_path`` parameter is left empty (D23/R3).
WORLD_STATE_ENV = 'ROBOT_WORLD_STATE'

#: Environment variable naming an optional operator-supplied seed file.
WORLD_SEED_ENV = 'ROBOT_WORLD_SEED'


def _to_store_pose(message: RosPose) -> Pose:
    """Return the store pose for a ``geometry_msgs/Pose`` message.

    A field-by-field copy: the message and :class:`robot_skills.Pose` are
    structurally identical (float64 ``position``/``orientation``), so this is
    lossless. ``Pose``/``Point``/``Quaternion`` validate on construction --
    non-finite floats and an all-zero quaternion raise there -- which is why
    the handlers below can treat a bad pose as a ``(ValueError, TypeError)``
    refusal like any other.
    """
    return Pose(
        position=Point(
            x=message.position.x,
            y=message.position.y,
            z=message.position.z,
        ),
        orientation=Quaternion(
            x=message.orientation.x,
            y=message.orientation.y,
            z=message.orientation.z,
            w=message.orientation.w,
        ),
    )


class WorldQueryNode(Node):
    """Offer the read query and the three object-write services.

    Parameters (declared with defaults, so a bare ``ros2 run`` can still be
    told where the world lives):

    * ``world_state_path`` (string, default ``''``) -- the live-state file.
      Empty falls back to ``$ROBOT_WORLD_STATE``; still empty is a **startup
      failure**, not a guess: a node must be told where the live file is, and
      inventing a path would silently create a second world.
    * ``world_seed_path`` (string, default ``''``) -- an optional seed file.
      Empty falls back to ``$ROBOT_WORLD_SEED``; still empty means ``None``,
      i.e. the seed shipped inside ``robot_world``.
    """

    def __init__(self) -> None:
        """Open the store and offer the read and write services."""
        # Node name `world_query`, in the `/world_query` namespace, so the
        # services resolve to `/world_query/get_world` and the three write
        # services under the same prefix (R3).
        super().__init__('world_query', namespace='world_query')

        self.declare_parameter('world_state_path', '')
        self.declare_parameter('world_seed_path', '')

        live_path = (
            self.get_parameter('world_state_path').get_parameter_value().string_value
            or os.environ.get(WORLD_STATE_ENV, '')
        )
        if not live_path:
            raise RuntimeError(
                'no world live-state file configured: set the world_state_path '
                f'parameter or the ${WORLD_STATE_ENV} environment variable '
                '(the world query service never invents a path)')

        seed_path = (
            self.get_parameter('world_seed_path').get_parameter_value().string_value
            or os.environ.get(WORLD_SEED_ENV, '')
        ) or None

        # Opened once at startup. A missing live file is seeded from the seed
        # and a corrupt one raises (D23) -- a loud startup failure, not a
        # per-request error. The write handlers below mutate this same
        # instance, so the read path is fresh by construction (R4).
        self._store = FileWorldStore(live_path, seed_path)
        self._service = self.create_service(
            GetWorld, 'get_world', self._handle_get_world)
        self._update_pose_service = self.create_service(
            UpdateObjectPose, 'update_object_pose', self._handle_update_object_pose)
        self._add_object_service = self.create_service(
            AddObject, 'add_object', self._handle_add_object)
        self._remove_object_service = self.create_service(
            RemoveObject, 'remove_object', self._handle_remove_object)
        seed_note = repr(seed_path) if seed_path else 'shipped seed'
        self.get_logger().info(
            f'world query service up: live file {live_path!r}, seed {seed_note}')

    def _handle_get_world(
        self, request: GetWorld.Request, response: GetWorld.Response,
    ) -> GetWorld.Response:
        """Fill ``world_json`` with a fresh snapshot of the store's document."""
        response.world_json = document_text(self._store.document())
        return response

    def _handle_update_object_pose(
        self,
        request: UpdateObjectPose.Request,
        response: UpdateObjectPose.Response,
    ) -> UpdateObjectPose.Response:
        """Move a registered object; report a store refusal instead of raising."""
        try:
            self._store.update_object_pose(request.object_id, _to_store_pose(request.pose))
        except (ValueError, TypeError) as exc:
            response.success = False
            response.error = str(exc)
            return response
        response.success = True
        response.error = ''
        return response

    def _handle_add_object(
        self, request: AddObject.Request, response: AddObject.Response,
    ) -> AddObject.Response:
        """Register a new object; report a store refusal instead of raising."""
        try:
            self._store.add_object(WorldObject(
                object_id=request.object_id,
                label=request.label,
                pose=_to_store_pose(request.pose),
                graspable=request.graspable,
                # held_by is robot proprioception (set_held_by), never something
                # perception reports; a perceived object is held by nobody (R2).
                held_by=None,
            ))
        except (ValueError, TypeError) as exc:
            response.success = False
            response.error = str(exc)
            return response
        response.success = True
        response.error = ''
        return response

    def _handle_remove_object(
        self, request: RemoveObject.Request, response: RemoveObject.Response,
    ) -> RemoveObject.Response:
        """Drop an object; report a store refusal instead of raising."""
        try:
            self._store.remove_object(request.object_id)
        except (ValueError, TypeError) as exc:
            response.success = False
            response.error = str(exc)
            return response
        response.success = True
        response.error = ''
        return response


def main(args=None) -> None:
    """Run the world query node until interrupted."""
    rclpy.init(args=args)
    node = WorldQueryNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
