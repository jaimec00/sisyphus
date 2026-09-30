# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The pure transform: ``WorldDocument`` -> planning-scene object descriptors.

R6 splits the bridge in two: this module turns a world document into a list of
:class:`SceneObject` descriptors (id + primitive + pose), and the ROS node
turns those into ``moveit_msgs/CollisionObject`` messages and pushes them at
``move_group``. Keeping the transform ROS-free is what makes R10 item 3 a plain
unit test -- no graph, no ``move_group``, just world in, descriptors out -- and
it keeps the mapping in one place so the ROS half has no geometry logic.

The pose is copied verbatim, position and orientation alike: ``robot_world``'s
``Pose`` is the same seven float64s as ``geometry_msgs/Pose`` (the world node's
own docstring makes the same point for its write services), so this is a copy,
not a re-encoding that could round floats or re-order a quaternion. The frame
is the world/map frame -- the same one the column and base live in -- and is
carried on the descriptor as :attr:`SceneObject.frame_id` rather than baked in
here, so a later PR can put objects in a declared frame without touching the
transform.
"""
from dataclasses import dataclass
from typing import Iterable

from robot_moveit.scene_geometry import ShapeSpec, spec_for_label
from robot_world import WorldDocument, WorldObject

#: The frame every world object's pose is expressed in. ``robot_world`` is
#: frame-agnostic by design (it stores coordinates, not TF), and the shipped
#: world coincides with the robot's map/world frame (the seed's locations are
#: the same coordinates the base navigates in, D23/D35).
WORLD_FRAME = 'world'


@dataclass(frozen=True)
class SceneObject:
    """One world object as planning-scene geometry (ROS-free).

    ``object_id`` becomes the collision object id -- ``robot_world``'s own id,
    so the planning scene names an object exactly as the world does and a
    follow-up lookup is by the same key (D23/D35: no second naming scheme).
    """

    object_id: str
    label: str
    shape: ShapeSpec
    frame_id: str
    position: tuple  # (x, y, z)
    orientation: tuple  # (x, y, z, w)


def _object_to_scene_object(obj: WorldObject) -> SceneObject:
    pose = obj.pose
    return SceneObject(
        object_id=obj.object_id,
        label=obj.label,
        shape=spec_for_label(obj.label),
        frame_id=WORLD_FRAME,
        position=(pose.position.x, pose.position.y, pose.position.z),
        orientation=(pose.orientation.x, pose.orientation.y,
                     pose.orientation.z, pose.orientation.w),
    )


def scene_objects(document: WorldDocument) -> list:
    """Return the planning-scene descriptors for every object in ``document``.

    Every world object becomes exactly one descriptor, in document order. The
    world holds no notion of "collidable": an object is a thing in the scene,
    and whether it *should* stop the arm is a planning concern, not a world one
    -- so the bridge does not filter on ``graspable``.
    """
    return [_object_to_scene_object(obj) for obj in document.objects]


def scene_objects_from_world_json(world_json: str) -> list:
    """Parse canonical world JSON and return its scene descriptors.

    A convenience for callers that already hold the service payload (the ROS
    node does): ``robot_world`` owns the schema, so the parse is its own
    ``WorldDocument.from_dict`` -- the bridge never re-implements the schema.
    """
    import json
    return scene_objects(WorldDocument.from_dict(json.loads(world_json)))


def descriptor_ids(descriptors: Iterable[SceneObject]) -> list:
    """Return the object ids of ``descriptors`` in order (test/diagnostics aid)."""
    return [d.object_id for d in descriptors]
