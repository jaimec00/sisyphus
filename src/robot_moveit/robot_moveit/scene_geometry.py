# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Label -> primitive collision geometry, and the world -> planning-scene transform.

This module is the pure half of the planning-scene bridge: it imports no ROS
and touches no graph, so it is unit-testable with a plain ``WorldDocument`` in,
a list of shape/pose descriptors out, and no runtime at all. The ROS half
(``planning_scene_bridge``) calls the two functions here and then wraps the
result in ``moveit_msgs`` messages.

Why a table at all (R5/D29)
---------------------------
``robot_world`` carries an object's ``label`` and ``pose`` and **no geometry**
-- deliberately (D23: the world is a scene registry, not a CAD catalog). MoveIt,
however, will not collide-check a point: a ``CollisionObject`` needs a
primitive. So the bridge must bridge that gap, and the honest way to do it is a
small, *explicitly estimated* table rather than a lookup that pretends to be
authoritative. Every number below is a guess about a household object's size,
marked ESTIMATED, and owed a real source (a mesh, a datasheet, or perception)
when one exists. The rule is R5's and D29's: don't dress a guess up as a
datasheet number.

An unknown label is not an error. The world is allowed to grow a label this
table has not seen (perception observes "a thing", it does not consult this
module), so an unknown label falls back to a small box and logs a warning. A
crash here would make adding a new object to the world a code change -- the
opposite of what the world store is for.

The table sits at the *object's* origin, not the world origin: each descriptor
carries its own local pose, so a later change (a handled mug's offset, a plate
lying on its side) can move geometry without touching the table.
"""
from dataclasses import dataclass
from typing import Mapping

#: Shape kinds this module emits. Kept as plain strings (not an enum) so the
#: ROS half can map them to ``shape_msgs/SolidPrimitive`` constants directly;
#: ``BOX`` and ``CYLINDER`` cover every seed object.
BOX = 'box'
CYLINDER = 'cylinder'

#: The fallback for a label the table does not know: a 5 cm cube (R5).
#: ESTIMATED -- small enough to be unobtrusive, big enough to collide.
DEFAULT_BOX_SIZE = (0.05, 0.05, 0.05)

#: Label -> (shape, dimensions), dimensions in METRES: for BOX
#: ``(x, y, z)`` full extents; for CYLINDER ``(radius, height)`` (third entry
#: unused/absent). EVERY number here is ESTIMATED (R5, D29): an eyeball of a
#: typical household object, not a measured or datasheet value. Covers the
#: shipped seed's labels plus the common couplet (cup/mug, plate/bowl).
#:
#: The list is intentionally short. A label beyond it falls back to
#: DEFAULT_BOX_SIZE with a warning; growing the table is cheap and local.
GEOMETRY_BY_LABEL: Mapping[str, tuple] = {
    # ESTIMATED: a 40 mm-radius, 100 mm-tall mug -- a typical ceramic mug.
    'mug': (CYLINDER, (0.04, 0.10)),
    # ESTIMATED: a 35 mm-radius, 90 mm-tall cup.
    'cup': (CYLINDER, (0.035, 0.09)),
    # ESTIMATED: a 120 mm-radius, 10 mm-thick plate.
    'plate': (CYLINDER, (0.12, 0.01)),
    # ESTIMATED: an 80 mm-radius, 50 mm-tall bowl.
    'bowl': (CYLINDER, (0.08, 0.05)),
    # ESTIMATED: a closed 200 x 140 x 30 mm book.
    'book': (BOX, (0.20, 0.14, 0.03)),
    # ESTIMATED: a 180 x 50 x 20 mm handheld remote.
    'remote': (BOX, (0.18, 0.05, 0.02)),
    # ESTIMATED: a counter section, 600 x 600 x 900 mm (top at +450 mm from
    # the object origin at the counter's own centre height in the seed).
    'counter': (BOX, (0.60, 0.60, 0.90)),
    # ESTIMATED: a sofa, 1800 x 800 x 400 mm.
    'sofa': (BOX, (1.80, 0.80, 0.40)),
    # ESTIMATED: a small side/coffee table, 800 x 800 x 400 mm.
    'table': (BOX, (0.80, 0.80, 0.40)),
    # ESTIMATED: a dining chair, 450 x 450 x 900 mm.
    'chair': (BOX, (0.45, 0.45, 0.90)),
    # ESTIMATED: a 1200 x 400 x 750 mm shelf unit.
    'shelf': (BOX, (1.20, 0.40, 0.75)),
    # ESTIMATED: a tall 350 x 350 x 1700 mm cabinet.
    'cabinet': (BOX, (0.35, 0.35, 1.70)),
    # ESTIMATED: a 250 x 350 x 300 mm trash bin.
    'bin': (BOX, (0.25, 0.35, 0.30)),
    # ESTIMATED: a 400 mm-diameter, 300 mm-tall storage drum.
    'drum': (CYLINDER, (0.20, 0.30)),
}


@dataclass(frozen=True)
class ShapeSpec:
    """One object's collision geometry, in the object's own frame.

    ``kind`` is :data:`BOX` or :data:`CYLINDER`; ``dimensions`` is the tuple
    from :data:`GEOMETRY_BY_LABEL` for that kind (box: x/y/z extents; cylinder:
    radius/height). ``estimated`` is carried through so the runtime log can say
    which objects got a guessed shape -- a reviewer can see at a glance whether
    a scene is built from the table or from the fallback.
    """

    kind: str
    dimensions: tuple
    estimated: bool = True


def spec_for_label(label: str) -> ShapeSpec:
    """Return the estimated :class:`ShapeSpec` for a world object label.

    An unknown (or empty) label gets the default 5 cm box, still flagged
    estimated; the caller is expected to log the fallback (this function is
    pure and does not log).
    """
    entry = GEOMETRY_BY_LABEL.get(label)
    if entry is None:
        return ShapeSpec(BOX, DEFAULT_BOX_SIZE, estimated=True)
    kind, dimensions = entry
    return ShapeSpec(kind, tuple(dimensions), estimated=True)


def known_labels() -> frozenset:
    """Return the labels the table covers (used by tests and diagnostics)."""
    return frozenset(GEOMETRY_BY_LABEL)
