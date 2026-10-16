# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Acceptance criterion: the shipped seed *is* the demo apartment, exactly.

The expectation is written out longhand here rather than compared against
``robot_backends.default_world()``: that function now *loads this file*, so
comparing the two would assert nothing.  These numbers are the pin -- changing
the scene means changing them deliberately, in the same commit.
"""

import json

from robot_skills import Pose
from robot_world import (
    default_seed_document,
    DEFAULT_SEED_RESOURCE,
    document_text,
    FileWorldStore,
    storage,
    WorldDocument,
)

#: The scene ``robot_backends.default_world()`` shipped before it was a file.
EXPECTED_LOCATIONS = {
    'charger': (0.0, 0.0, 0.0),
    'kitchen': (2.0, 0.0, 0.0),
    'table': (0.0, 2.0, 0.0),
    'living_room': (-2.0, 1.0, 0.0),
}

#: object_id -> (label, x, y, z, graspable), in the order the seed lists them.
#:
#: These are the *physically coherent* re-place (issue #152, manager ruling #2):
#: every graspable object's underside sits exactly on its own support pad the
#: sim derives from its pose (the kitchen trio on pads of top z=1.10, the table
#: pair on pad tops 0.885 / 0.87), and every object and pad is clear of both the
#: mast (``|y| > 0.03``) and the arm band (``|y| > 0.294`` at z 0.94..1.106)
#: along the charger->kitchen corridor, so materializing the world does not
#: block navigation. Issue #158 tightened that for the **base/mast** bands and
#: the kitchen goal ``(2.0, 0)``: ``counter_1`` now clears them by
#: ``CORRIDOR_MARGIN``, so the plant's lateral drift cannot drive the base into
#: it. The arm band keeps its exact envelope -- the graspables sit at the edge
#: of the 5-DOF arm's reach (left arm reaches the ``+y`` objects, right the
#: ``-y`` ones) so no outward margin is reachable without putting them out of
#: reach (issue #158 M10/M13) -- and inside the Mock's 1.0 m table window
#: (PR4 / #149 R6).
EXPECTED_OBJECTS = (
    ('mug_1', 'mug', 2.15, 0.45, 1.150, True),
    ('plate_1', 'plate', 2.00, -0.48, 1.105, True),
    ('bowl_1', 'bowl', 2.25, -0.44, 1.125, True),
    ('counter_1', 'counter', 2.15, 0.62, 0.400, False),
    ('book_1', 'book', 0.10, 2.00, 0.900, True),
    ('cup_1', 'cup', 0.20, 2.20, 0.915, True),
    ('sofa_1', 'sofa', -2.00, 1.60, 0.40, False),
)


def test_the_shipped_seed_is_the_demo_apartment():
    """Four named locations, seven objects, the mug on the kitchen counter."""
    seed = default_seed_document()

    assert seed.start_location == 'charger'
    assert seed.start_column_height == 0.3
    assert {
        name: (pose.position.x, pose.position.y, pose.position.z)
        for name, pose in seed.locations.items()
    } == EXPECTED_LOCATIONS

    assert tuple(
        (item.object_id, item.label, item.pose.position.x, item.pose.position.y,
         item.pose.position.z, item.graspable)
        for item in seed.objects
    ) == EXPECTED_OBJECTS


def test_the_seed_scene_starts_with_nothing_held_and_no_rotations():
    """A seed describes a room at rest: no held objects, identity orientations."""
    seed = default_seed_document()

    for item in seed.objects:
        assert item.held_by is None, item.object_id
        assert item.pose.orientation == Pose().orientation
    for pose in seed.locations.values():
        assert pose.orientation == Pose().orientation
    assert seed.start_pose == seed.locations['charger']


def test_the_seed_file_is_exactly_what_a_write_would_emit():
    """The checked-in file is regenerable, so hand edits cannot drift the format."""
    resource = storage.resources.files('robot_world') / DEFAULT_SEED_RESOURCE
    text = resource.read_text(encoding='utf-8')
    seed = default_seed_document()

    assert json.loads(text) == seed.to_dict()
    assert text == document_text(seed)
    assert WorldDocument.from_dict(json.loads(text)) == seed


def test_running_a_persisted_world_never_writes_to_the_shipped_seed(tmp_path):
    """The seed is read-only in practice, not just by intention."""
    resource = storage.resources.files('robot_world') / DEFAULT_SEED_RESOURCE
    before = resource.read_text(encoding='utf-8')

    store = FileWorldStore(tmp_path / 'world.json')
    store.update_object_pose('mug_1', Pose.from_xyz(0.3, 2.0, 0.75))
    store.remove_object('sofa_1')
    store.reset()

    assert resource.read_text(encoding='utf-8') == before
    assert default_seed_document().find_object('sofa_1') is not None
