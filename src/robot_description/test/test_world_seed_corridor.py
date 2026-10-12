# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The shipped seed's physical scene must stay clear of the nav corridor (#152).

The charger->kitchen drive runs along ``y = 0`` for ``x in [0, 2]``. The robot's
own body at the seed's column height (0.30) occupies three envelopes there
(VERIFIED from the compiled model, issue #152 context):

* the **base** -- a 0.15 m radius chassis disc plus its wheels,
  ``|y| <= 0.15``, ``z 0 .. 0.115``;
* the **mast** -- a 0.06 m square column fused into ``base_link``,
  ``|y| <= 0.03``, ``z 0.115 .. 1.445``;
* the **arms** -- shoulders at ``y = +/-0.18`` with links and jaws reaching
  ``|y| = 0.2935``, ``z 0.94 .. 1.106`` at the seed column height.

A physical body whose footprint enters any of those envelopes over that stretch
fights the robot and breaks navigation -- the regression that forced PR A's
``world_objects`` opt-out. The seed therefore has to keep every object and every
derived support pad outside all of them.

This file is the **fast, geometric** guard for that invariant (issue #152
R2/R8): it needs no MuJoCo and no sim, so it always runs, and it holds the
*shipped* seed plus the pads the converter derives from it to the clearance the
acceptance criterion demands. The heavyweight end-to-end proof is
``robot_bringup``'s ``test_pr3_navigate``, which drives the materialized world
for real; this file is the guard that fails in a second when a seed edit
re-introduces a blocker.
"""

from robot_description.world_bodies import (
    derived_support_surfaces,
    physical_geom_for_label,
    SUPPORT_HALF_THICKNESS,
)
from robot_skills import Point, Pose
from robot_world import default_seed_document, WorldDocument, WorldObject

#: The x-range over which the corridor envelopes matter (R2): it covers the
#: drive from the charger ``(0, 0)`` to the kitchen ``(2, 0)``.  0.5 m of slack
#: either side is more than the robot's own ``max |x|`` (0.4914 m), so a body
#: outside it cannot be reached even with the base at either end.
CORRIDOR_X = (-0.5, 2.5)

#: The envelopes the robot's body occupies along the corridor, as
#: ``(half_width_y, z_low, z_high)`` boxes centred on ``y = 0``: the base disc +
#: wheels, the column mast (fused into ``base_link``) and the arms (issue #152
#: context §2, re-measured on the compiled model at the seed column height
#: 0.30).  A body on the corridor must miss **every** one.
ROBOT_ENVELOPES = (
    (0.15, 0.0, 0.115),      # base disc (r 0.15) + wheels, on the floor
    (0.03, 0.115, 1.445),    # column mast (fused into base_link)
    (0.294, 0.94, 1.106),    # arm links + jaws at column height 0.30
)


def _boxes(document):
    """Yield ``(name, x_lo, x_hi, y_lo, y_hi, z_lo, z_hi)`` per physical body.

    Objects use the converter's own geometry table (:func:`physical_geom_for_label`),
    so the guard measures the shapes the sim actually builds rather than a
    parallel transcription, and the pads are exactly what
    :func:`derived_support_surfaces` hands the converter -- a box of half
    thickness :data:`SUPPORT_HALF_THICKNESS` whose *top* is the derived ``top_z``.
    """
    for obj in document.objects:
        geom = physical_geom_for_label(obj.label)
        pos = obj.pose.position
        if geom.kind == 'box':
            hx, hy, hz = geom.dims
        else:
            hx = hy = geom.dims[0]
            hz = geom.dims[1]
        yield (obj.object_id, pos.x - hx, pos.x + hx, pos.y - hy, pos.y + hy,
               pos.z - hz, pos.z + hz)
    for index, (top_z, cx, cy, half_extent) in enumerate(
            derived_support_surfaces(document)):
        yield ('world_support_%d' % index, cx - half_extent, cx + half_extent,
               cy - half_extent, cy + half_extent,
               top_z - 2.0 * SUPPORT_HALF_THICKNESS, top_z)


def _overlaps(first, second):
    """Return whether two ``(lo, hi)`` spans overlap."""
    return first[0] < second[1] and second[0] < first[1]


def _blockers(document):
    """Return the bodies that intrude into the corridor envelopes.

    A body matters only if it overlaps the corridor's x-range; within that it
    must miss every box in :data:`ROBOT_ENVELOPES`. Anything intersecting one of
    them -- ``|y| <= half_width`` while its z-span overlaps the envelope's --
    is a blocker.
    """
    blocking = []
    for (name, x_lo, x_hi, y_lo, y_hi, z_lo, z_hi) in _boxes(document):
        if not _overlaps((x_lo, x_hi), CORRIDOR_X):
            continue  # outside the corridor's x-range: it is not on the path
        for half_width, env_z_lo, env_z_hi in ROBOT_ENVELOPES:
            if (y_lo <= half_width and y_hi >= -half_width
                    and _overlaps((z_lo, z_hi), (env_z_lo, env_z_hi))):
                blocking.append((name, (y_lo, y_hi), (z_lo, z_hi)))
                break
    return blocking


def test_the_shipped_seed_has_no_corridor_blocker():
    """No shipped object or derived pad intrudes on the charger->kitchen path.

    Issue #152 acceptance: with the world materialized, navigation stays green,
    and this is the geometry that makes it so -- every physical body that
    overlaps the corridor's x-range misses both the mast and the arm envelopes.
    """
    blocking = _blockers(default_seed_document())
    assert not blocking, (
        'these bodies intrude into the navigation corridor envelopes %r over '
        'x in %r: %r' % (ROBOT_ENVELOPES, CORRIDOR_X, blocking))


def test_the_corridor_guard_measures_a_real_scene():
    """Guard against a vacuous pass: the seed has objects *and* derived pads.

    A seed that lost its objects (or a derivation that returned nothing) would
    make the test above pass without measuring anything.
    """
    document = default_seed_document()
    boxes = list(_boxes(document))
    surfaces = derived_support_surfaces(document)
    assert len(boxes) == len(document.objects) + len(surfaces)
    assert len(surfaces) >= 2, surfaces
    assert len(document.objects) >= 2


def test_the_corridor_guard_catches_a_body_on_the_path():
    """The guard is not a tautology: a body planted on the path is caught.

    A deliberate counter-example -- one graspable moved onto the corridor centre
    -- must be reported, or the test above proves nothing.
    """
    document = WorldDocument(
        locations={'charger': Pose.from_xyz(0.0, 0.0, 0.0)},
        start_location='charger',
        objects=(
            WorldObject(object_id='blocker_1', label='mug',
                        pose=Pose(position=Point(1.0, 0.0, 1.15))),
        ),
    )
    blocking = _blockers(document)
    assert [name for name, _, _ in blocking] == ['blocker_1', 'world_support_0'], (
        blocking)
