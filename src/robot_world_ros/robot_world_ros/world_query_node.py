# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The world-state query service: expose the store's document over ROS 2.

This is the ROS half of D35's split. ``robot_world`` is -- and stays -- a pure
Python store with no ROS at runtime (D30): it is what the MuJoCo swap
inherits, and it must keep working from a plain pytest run with no graph. This
node is the small ROS-facing shell around it: it owns a single
:class:`~robot_world.FileWorldStore` and answers one question -- "what does the
world look like right now?" -- with the store's own canonical JSON.

Why a JSON string and not typed fields (R2/D23)
-----------------------------------------------
The response is ``robot_world.document_text(store.document())``: the exact
bytes the store would write to disk. A typed message (``WorldLocation[]`` plus
``geometry_msgs/Pose``, an enum for ``held_by``, a separate schema field) would
be a *second representation* of the same scene, and one that drifts: float64
rounds the store's floats, ``held_by`` needs a mapping that can rot, and the
message schema forks from the document schema as it evolves. Carrying the
canonical JSON is what makes the query return the seed world "exactly as the
store reads it" -- byte-for-byte -- and makes the service a thin adapter
rather than a second source of truth (D23).

Read path only
--------------
This PR is read-only: the handler snapshots the store and hands the JSON back.
Nothing here mutates ``robot_world``, so the store's own invariants are
untouched. The write service lands in a later PR.

The store is constructed **once**, at node start, not per request: opening the
live-state file is startup work (it may seed a missing file or refuse a corrupt
one -- D23), and a query is a snapshot of in-memory state.
"""

import os

import rclpy
from rclpy.node import Node
from robot_world import document_text, FileWorldStore
from robot_world_ros_interfaces.srv import GetWorld

__all__ = ['WorldQueryNode', 'main']

#: Environment variable naming the live-state file, used when the
#: ``world_state_path`` parameter is left empty (D23/R3).
WORLD_STATE_ENV = 'ROBOT_WORLD_STATE'

#: Environment variable naming an optional operator-supplied seed file.
WORLD_SEED_ENV = 'ROBOT_WORLD_SEED'


class WorldQueryNode(Node):
    """Answer ``/world_query/get_world`` with the store's current document.

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
        """Open the store and offer the query service."""
        # Node name `world_query`, in the `/world_query` namespace, so the
        # `get_world` service resolves to `/world_query/get_world` (R3).
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
        # per-request error.
        self._store = FileWorldStore(live_path, seed_path)
        self._service = self.create_service(
            GetWorld, 'get_world', self._handle_get_world)
        seed_note = repr(seed_path) if seed_path else 'shipped seed'
        self.get_logger().info(
            f'world query service up: live file {live_path!r}, seed {seed_note}')

    def _handle_get_world(
        self, request: GetWorld.Request, response: GetWorld.Response,
    ) -> GetWorld.Response:
        """Fill ``world_json`` with a fresh snapshot of the store's document."""
        response.world_json = document_text(self._store.document())
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
