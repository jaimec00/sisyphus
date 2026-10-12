# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""The shipped seed's physical scene must stay clear of the nav corridor (#152).

The charger->kitchen drive runs along ``y = 0`` for ``x in [0, 2]``. The robot's
whole body -- chassis, column mast and both arms -- reaches ``|y| = 0.2935 m``
(VERIFIED, issue #152 context), so a physical body whose footprint enters the
band ``|y| <= 0.30`` over that stretch fights the base and breaks navigation --
the regression that forced PR A's ``world_objects`` opt-out.

This file is the **fast, geometric** guard for that invariant (issue #152 R2/R8):
it needs no MuJoCo and no sim, so it always runs, and it holds the *shipped*
seed plus the surfaces the converter derives from it to the clearance R2
demands. The heavyweight end-to-end proof is ``robot_bringup``'s
``test_pr3_navigate``, which drives the materialized world for real; this file
is the guard that fails in a second when a seed edit re-introduces a blocker.
"""

from robot_description.world_bodies import (
    derived_support_surfaces,
    physical_geom_for_label,
    SUPPORT_HALF_THICKNESS,
)
from robot_skills import Point, Pose
from robot_world import default_seed_document, WorldDocument, WorldObject

#: Half-width of the corridor band the robot body needs, metres (issue #152 R2):
#: the robot's whole-body max ``|y|`` is 0.2935, so 0.30 is that plus a margin.
CORRIDOR_HALF_WIDTH = 0.30
#: The x-range over which the corridor band matters (R2): it covers the drive
#: from the charger ``(0, 0)`` to the kitchen ``(2, 0)`` with slack either side.
CORRIDOR_X = (-0.5, 2.5)
#: The z-band the robot occupies at rest (issue #152 context §2): the mast
#: (z 0.115..1.445) and the arms (z 0.93..1.20) live inside it, so a body wholly
#: below or above it cannot touch the robot.
ROBOT_Z = (0.115, 1.445)


def _boxes(document):
    """Yield ``(name, x_lo, x_hi, y_lo, y_hi, z_lo, z_hi)`` per physical body.

    Objects use the converter's own geometry table (:func:`physical_geom_for_label`),
    so the guard measures the shapes the sim actually builds rather than a
    parallel transcription, and the surfaces are exactly what
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


def _blockers(document):
    """Return the bodies that intrude into the corridor band, as ``(name, y)``.

    A body matters only if it overlaps the robot's z-band *and* the corridor's
    x-range; within that it must lie entirely on one side of the band
    (``y_lo >= +half`` or ``y_hi <= -half``). Anything whose span straddles the
    band is a blocker.
    """
    blocking = []
    for (name, x_lo, x_hi, y_lo, y_hi, z_lo, z_hi) in _boxes(document):
        if z_hi < ROBOT_Z[0] or z_lo > ROBOT_Z[1]:
            continue  # entirely below or above the robot: it cannot touch it
        if x_hi < CORRIDOR_X[0] or x_lo > CORRIDOR_X[1]:
            continue  # outside the corridor's x-range: it is not on the path
        if y_lo >= CORRIDOR_HALF_WIDTH or y_hi <= -CORRIDOR_HALF_WIDTH:
            continue  # clear of the band on one side
        blocking.append((name, (y_lo, y_hi)))
    return blocking


def test_the_shipped_seed_has_no_corridor_blocker():
    """No shipped object or derived surface intrudes on the charger->kitchen path.

    Issue #152 acceptance: with the world materialized, navigation stays green,
    and this is the geometry that makes it so -- every physical body that
    overlaps the robot's z-band and the corridor's x-range stays at least
    :data:`CORRIDOR_HALF_WIDTH` off the corridor centre line.
    """
    blocking = _blockers(default_seed_document())
    assert not blocking, (
        'these bodies intrude into the navigation corridor '
        '(|y| <= %r over x in %r): %r'
        % (CORRIDOR_HALF_WIDTH, CORRIDOR_X, blocking))


def test_the_corridor_guard_measures_a_real_scene():
    """Guard against a vacuous pass: the seed has objects *and* derived surfaces.

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
    assert [name for name, _ in blocking] == ['blocker_1', 'world_support_0'], (
        blocking)
