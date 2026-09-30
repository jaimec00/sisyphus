# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""World -> planning-scene objects is exactly R5's table and the seed's poses.

R10 item 3: the pure transform, tested with no graph at all. These tests import
``robot_moveit.world_to_scene`` and ``robot_moveit.scene_geometry`` directly
(they are ROS-free by construction), feed them ``robot_world``'s shipped seed,
and assert:

* every seed object becomes exactly one ``CollisionObject`` whose id, frame,
  primitive kind, dimensions and pose match the world (the seed is the arbiter
  of the poses -- never a hardcoded copy, per the repo's e2e idiom);
* the R5 table's shapes are used for the known labels and the default box for
  an unknown one (with the fallback flagged estimated);
* ``diff_scene`` adds every current object and removes an id that vanished.

The message assertions are on ``moveit_msgs`` messages, which need no runtime
beyond the message import, so this stays a unit test.
"""
import json

#: The seed world's object ids (the shipped scene, read through robot_world).
SEED_OBJECT_IDS = {
    'mug_1', 'plate_1', 'bowl_1', 'counter_1', 'book_1', 'cup_1', 'sofa_1',
}


def _seed_document():
    """Return robot_world's shipped seed document (never a local copy)."""
    from robot_world.storage import default_seed_document
    return default_seed_document()


def _seed_world_json():
    """Return the seed as robot_world's canonical JSON text."""
    from robot_world import document_text
    return document_text(_seed_document())


def test_seed_transform_matches_the_world():
    """Every seed object -> one CollisionObject with the world's id and pose."""
    from robot_moveit.planning_scene_bridge import diff_scene
    from robot_moveit.world_to_scene import scene_objects_from_world_json

    document = _seed_document()
    descriptors = scene_objects_from_world_json(_seed_world_json())

    assert [d.object_id for d in descriptors] == [
        o.object_id for o in document.objects]
    assert {d.object_id for d in descriptors} == SEED_OBJECT_IDS

    objects = diff_scene(descriptors, set())
    by_id = {o.id: o for o in objects}
    assert set(by_id) == SEED_OBJECT_IDS

    for world_object in document.objects:
        collision = by_id[world_object.object_id]
        assert collision.operation == collision.ADD
        assert collision.header.frame_id == 'world'
        assert len(collision.primitives) == 1
        assert len(collision.primitive_poses) == 1
        # The primitive sits at the object origin; the object's own pose is the
        # world pose (asserted through the descriptor that produced it).
        position = collision.primitive_poses[0].position
        assert (position.x, position.y, position.z) == (0.0, 0.0, 0.0)

        descriptor = next(d for d in descriptors
                          if d.object_id == world_object.object_id)
        assert descriptor.frame_id == collision.header.frame_id
        assert descriptor.position == (
            world_object.pose.position.x,
            world_object.pose.position.y,
            world_object.pose.position.z)
        assert descriptor.orientation == (
            world_object.pose.orientation.x,
            world_object.pose.orientation.y,
            world_object.pose.orientation.z,
            world_object.pose.orientation.w)

    # Spot-check two known labels' primitives against the R5 table exactly.
    mug = by_id['mug_1'].primitives[0]
    assert mug.type == mug.CYLINDER
    # SolidPrimitive orders cylinder dims [HEIGHT, RADIUS]; the table stores
    # (radius, height) -- assert both the order and the values.
    assert list(mug.dimensions) == [0.10, 0.04]
    book = by_id['book_1'].primitives[0]
    assert book.type == book.BOX
    assert list(book.dimensions) == [0.20, 0.14, 0.03]


def test_unknown_label_falls_back_to_default_box():
    """An unknown label gets the 5 cm default box, flagged estimated."""
    from robot_moveit.scene_geometry import (
        BOX, DEFAULT_BOX_SIZE, known_labels, spec_for_label)

    assert 'lampshade' not in known_labels()
    spec = spec_for_label('lampshade')
    assert spec.kind == BOX
    assert spec.dimensions == DEFAULT_BOX_SIZE
    assert spec.estimated is True

    # Every table entry is also flagged estimated (R5: all guesses).
    for label in known_labels():
        assert spec_for_label(label).estimated is True


def test_every_known_label_builds_a_valid_primitive():
    """Each R5 table entry maps to a SolidPrimitive with the right dim count."""
    from robot_moveit.planning_scene_bridge import collision_object_for
    from robot_moveit.scene_geometry import CYLINDER, known_labels, spec_for_label
    from robot_moveit.world_to_scene import SceneObject

    for label in sorted(known_labels()):
        shape = spec_for_label(label)
        descriptor = SceneObject(
            object_id='x', label=label, shape=shape, frame_id='world',
            position=(0.0, 0.0, 0.0), orientation=(0.0, 0.0, 0.0, 1.0))
        primitive = collision_object_for(descriptor).primitives[0]
        expected = 2 if shape.kind == CYLINDER else 3
        assert len(primitive.dimensions) == expected, label


def test_diff_scene_removes_departed_ids():
    """A world that lost an object yields a REMOVE for it, ADDs for the rest."""
    from moveit_msgs.msg import CollisionObject
    from robot_moveit.planning_scene_bridge import diff_scene
    from robot_moveit.world_to_scene import scene_objects_from_world_json

    descriptors = scene_objects_from_world_json(_seed_world_json())
    # Pretend the previous pass had an extra object that is now gone.
    objects = diff_scene(descriptors, {'ghost_object'})
    by_id = {o.id: o for o in objects}
    assert by_id['ghost_object'].operation == CollisionObject.REMOVE
    assert by_id['mug_1'].operation == CollisionObject.ADD
    # The remove carries no geometry (id-only removal).
    assert by_id['ghost_object'].primitives == []
    assert by_id['mug_1'].primitives != []


def test_world_json_round_trip_is_the_seed():
    """The JSON this test feeds is robot_world's canonical document text."""
    document = _seed_document()
    parsed = json.loads(_seed_world_json())
    assert parsed['objects'][0]['object_id'] == document.objects[0].object_id
    assert len(parsed['objects']) == len(document.objects)
