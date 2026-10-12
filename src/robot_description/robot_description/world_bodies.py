# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Materialize the world objects as *physical* MuJoCo bodies (issue #150, PR A).

The sim bringup (``mujoco.launch.py``) previously materialized the robot plus a
floor and nothing else: world objects lived only in the MoveIt planning scene,
so a pick-and-place plan manoeuvred an empty room. This module turns a
:class:`robot_world.WorldDocument` -- the D35/D23 world-state seam, pose +
label per object -- into the ``<body>`` blocks that
:func:`robot_description.mjcf_model.write_mjcf_model` splices into the
worldbody, giving the sim objects with mass, inertia and *real* contacts.

Relationship to the in-process backend's world bodies
-----------------------------------------------------
``robot_backends.mujoco_backend._world_bodies_xml`` renders the same objects,
but **deliberately non-physically** (``contype="0" conaffinity="0"`` +
``gravcomp="1"``): there they are attach-on-close bookkeeping, not obstacles
(D34). This converter is the opposite concern -- a collidable scene -- and does
not reuse that path (issue #150 R1). The two coexist: the backend's ghost
bodies for its own bookkeeping, these real bodies in the ROS sim.

An object also has to rest on *something*. The seed's objects sit at counter
height (``mug_1`` at z=1.15), but the sim has only the drive floor at z=-0.05,
so a free-jointed object would simply fall. The converter therefore also emits
a **finite** support surface ("table") whose top is at :data:`SUPPORT_TOP_Z` --
*finite*, not an infinite plane, because a plane at counter height would
intersect the robot's column and arms (issue #150 R5).

Geometry single source of truth (R3)
------------------------------------
All object geometry mirrors ``robot_moveit.scene_geometry.GEOMETRY_BY_LABEL``
-- the *planning-scene* geometry -- so the sim and the planner agree on every
object's shape and size:

* ``BOX`` dimensions are full extents; MuJoCo ``geom size`` is **half**-extents.
* ``CYLINDER`` dimensions are ``(radius, height)``; MuJoCo is
  ``(radius, half-height)``.

The table lives in :data:`PHYSICAL_GEOMS_BY_LABEL` as shape + MuJoCo dimensions
+ an ESTIMATED mass + a friction, and every number is called out ESTIMATED in
the same spirit as ``scene_geometry`` (D29/R5): an eyeball of a household
object, not a datasheet. ``robot_description`` must **not** import
``robot_moveit`` (``robot_moveit`` -> ``robot_bringup`` -> ``robot_description``
would close a dependency cycle), so the table is mirrored here; deduping the
three copies (``scene_geometry``, ``robot_backends``, this one) is a follow-up.

Dependency discipline
---------------------
Like :mod:`robot_description.mjcf_model` and :mod:`robot_description.robot_model`,
this module imports **no ROS runtime** (no ``rclpy``, no ``ament_index``). It
imports :mod:`robot_world`, which is ROS-free (VERIFIED), so the
``robot_description`` -> ``robot_world`` ``exec_depend`` keeps the "no ROS import
at runtime" invariant (D30) intact.
"""

from __future__ import annotations

from typing import Mapping

from robot_world import WorldDocument, WorldObject

__all__ = [
    'BOX',
    'CYLINDER',
    'collidable_world_bodies_xml',
    'DEFAULT_BOX_SIZE',
    'DEFAULT_FRICTION',
    'DEFAULT_MASS',
    'PHYSICAL_GEOMS_BY_LABEL',
    'PhysicalGeom',
    'SUPPORT_BODY_NAME',
    'SUPPORT_CENTER',
    'SUPPORT_HALF_EXTENT',
    'SUPPORT_HALF_THICKNESS',
    'SUPPORT_TOP_Z',
    'physical_geom_for_label',
]

#: Shape kinds, matching ``robot_moveit.scene_geometry`` (plain strings, no
#: enum) so the two tables read alike.
BOX = 'box'
CYLINDER = 'cylinder'

#: The fallback for a label the table does not know: the same 5 cm cube
#: ``scene_geometry.DEFAULT_BOX_SIZE`` uses (R3), so an unknown label gets the
#: same footprint in the sim as in the planning scene.
DEFAULT_BOX_SIZE = (0.05, 0.05, 0.05)

#: Fallback mass, kg, for a label with no estimate (ESTIMATED -- a light
#: household object). Kept well above ``mjMINVAL`` so a free-jointed body
#: always compiles (a free body below ``mjMINVAL`` is rejected).
DEFAULT_MASS = 0.3

#: Fallback friction (sliding, torsional, rolling), matching MuJoCo's own
#: default. ESTIMATED: a general household surface. Objects get it explicitly
#: so the model text records the intent rather than relying on an implicit
#: default.
DEFAULT_FRICTION = (1.0, 0.005, 0.0001)

#: Top of the support surface, metres, in the world frame (R5). Chosen so the
#: shipped seed's ``mug_1`` (center z=1.15, mug half-height 0.05) rests exactly
#: on it: ``1.15 - 0.05 = 1.10``.
SUPPORT_TOP_Z = 1.10

#: Half the support surface's horizontal extent (so 2 m x 2 m of table, R5):
#: large enough that the shipped seed's graspable objects sit on it, small
#: enough to stay a finite box a plane's infinite extent cannot be.
SUPPORT_HALF_EXTENT = 1.0

#: Half the support surface's thickness, metres (so a 10 cm slab, R5): thin,
#: but thick enough that the surface never intersects the robot's column or
#: arms, which is exactly why a finite box is used instead of a plane.
SUPPORT_HALF_THICKNESS = 0.05

#: Horizontal centre of the support surface ``(x, y)``, metres (R5: centre is a
#: converter constant/param).
#:
#: **Why it is not the world origin.** The robot's column mast is a 0.06 m sq.
#: capsule centred on ``base_link`` (the world origin at home) spanning world
#: z = 0.115 .. 1.445 -- i.e. right through ``SUPPORT_TOP_Z = 1.10``. A surface
#: whose footprint covers the origin therefore *intersects the mast*, and a
#: finite box does **not** avoid that (finiteness only bounds the surface's
#: edge; a box centred on the origin still covers it). VERIFIED: a free object
#: placed on an origin-centred surface in the full model (robot + rollers +
#: implicit integrator) is ejected at ~120 m/s by the mast contact. So the
#: surface is offset clear of the mast, under the shipped seed's graspable
#: kitchen objects (``mug_1`` at (2.1, 0.1, 1.15)) -- which is also the cluster
#: :data:`SUPPORT_TOP_Z` was chosen for (R5).
SUPPORT_CENTER = (2.15, 0.0)

#: Name of the support-surface body in the derived MJCF (tests find it by name).
SUPPORT_BODY_NAME = 'world_support'


class PhysicalGeom:
    """One label's physical geometry: shape + MuJoCo dims + mass + friction.

    ``kind`` is :data:`BOX` or :data:`CYLINDER`; ``dims`` is the tuple MuJoCo's
    ``geom size`` expects for that kind -- box **half**-extents ``(hx, hy, hz)``,
    cylinder ``(radius, half_height)``. ``mass`` is kg and ``friction`` is the
    ``(slide, spin, roll)`` triple MuJoCo's ``geom friction`` expects.
    ``estimated`` marks a guessed value (all of them are, per D29/R5).
    """

    __slots__ = ('kind', 'dims', 'mass', 'friction', 'estimated')

    def __init__(self, kind: str, dims: tuple, mass: float,
                 friction: tuple = DEFAULT_FRICTION, estimated: bool = True):
        self.kind = kind
        self.dims = tuple(dims)
        self.mass = float(mass)
        self.friction = tuple(friction)
        self.estimated = estimated


#: Label -> :class:`PhysicalGeom`, the *physics* mirror of
#: ``robot_moveit.scene_geometry.GEOMETRY_BY_LABEL`` (R3).
#:
#: Shape + dims MUST mirror ``scene_geometry`` exactly (full -> half extents /
#: height), so the sim and the planning scene agree; mass + friction are added
#: here (ESTIMATED, not mirrored -- the planning scene carries no mass). Mirror
#: ``scene_geometry``, **not** ``robot_backends`` (which already diverges, e.g.
#: mug radius 0.035 vs 0.04).
PHYSICAL_GEOMS_BY_LABEL: Mapping[str, PhysicalGeom] = {
    # ESTIMATED: a 40 mm-radius, 100 mm-tall ceramic mug, ~0.3 kg.
    'mug': PhysicalGeom(CYLINDER, (0.04, 0.05), 0.3),
    # ESTIMATED: a 35 mm-radius, 90 mm-tall cup, ~0.25 kg.
    'cup': PhysicalGeom(CYLINDER, (0.035, 0.045), 0.25),
    # ESTIMATED: a 120 mm-radius, 10 mm-thick plate, ~0.4 kg.
    'plate': PhysicalGeom(CYLINDER, (0.12, 0.005), 0.4),
    # ESTIMATED: an 80 mm-radius, 50 mm-tall bowl, ~0.3 kg.
    'bowl': PhysicalGeom(CYLINDER, (0.08, 0.025), 0.3),
    # ESTIMATED: a closed 200 x 140 x 30 mm book, ~0.4 kg.
    'book': PhysicalGeom(BOX, (0.10, 0.07, 0.015), 0.4),
    # ESTIMATED: a 180 x 50 x 20 mm handheld remote, ~0.15 kg.
    'remote': PhysicalGeom(BOX, (0.09, 0.025, 0.01), 0.15),
    # ESTIMATED: a counter section, 600 x 600 x 900 mm, ~30 kg.
    'counter': PhysicalGeom(BOX, (0.30, 0.30, 0.45), 30.0),
    # ESTIMATED: a sofa, 1800 x 800 x 400 mm, ~40 kg.
    'sofa': PhysicalGeom(BOX, (0.90, 0.40, 0.20), 40.0),
    # ESTIMATED: a small side/coffee table, 800 x 800 x 400 mm, ~12 kg.
    'table': PhysicalGeom(BOX, (0.40, 0.40, 0.20), 12.0),
    # ESTIMATED: a dining chair, 450 x 450 x 900 mm, ~7 kg.
    'chair': PhysicalGeom(BOX, (0.225, 0.225, 0.45), 7.0),
    # ESTIMATED: a 1200 x 400 x 750 mm shelf unit, ~20 kg.
    'shelf': PhysicalGeom(BOX, (0.60, 0.20, 0.375), 20.0),
    # ESTIMATED: a tall 350 x 350 x 1700 mm cabinet, ~45 kg.
    'cabinet': PhysicalGeom(BOX, (0.175, 0.175, 0.85), 45.0),
    # ESTIMATED: a 250 x 350 x 300 mm trash bin, ~2.5 kg.
    'bin': PhysicalGeom(BOX, (0.125, 0.175, 0.15), 2.5),
    # ESTIMATED: a 400 mm-diameter, 300 mm-tall storage drum, ~5 kg.
    'drum': PhysicalGeom(CYLINDER, (0.20, 0.15), 5.0),
}


def physical_geom_for_label(label: str) -> PhysicalGeom:
    """Return the physical geometry for a world-object label.

    Unknown (or empty) labels fall back to the same 5 cm box the planning scene
    uses (:data:`DEFAULT_BOX_SIZE`) with :data:`DEFAULT_MASS`, so a label beyond
    the table still materializes physically rather than crashing the sim build.
    """
    entry = PHYSICAL_GEOMS_BY_LABEL.get(label)
    if entry is not None:
        return entry
    return PhysicalGeom(BOX, tuple(d / 2.0 for d in DEFAULT_BOX_SIZE),
                        DEFAULT_MASS)


def _box_diaginertia(mass: float, dims: tuple) -> tuple:
    """Return the solid-box inertia ``(Ixx, Iyy, Izz)`` for full box dims.

    ``dims`` are MuJoCo half-extents; the solid-box principal moments about the
    center are ``m/12 * (dy^2 + dz^2)`` and cyclic, using the *full* extents.
    Derived here (not hard-coded) so a mass/dimension retune cannot leave a
    stale tensor behind (R4; mirrors the ``_roller_body_xml`` pattern in
    ``mjcf_model``).
    """
    hx, hy, hz = dims
    x, y, z = 2.0 * hx, 2.0 * hy, 2.0 * hz
    ixx = mass * (y * y + z * z) / 12.0
    iyy = mass * (x * x + z * z) / 12.0
    izz = mass * (x * x + y * y) / 12.0
    return (ixx, iyy, izz)


def _cylinder_diaginertia(mass: float, dims: tuple) -> tuple:
    """Return the solid-cylinder inertia ``(Ixx, Iyy, Izz)`` for ``(r, half_h)``.

    MuJoCo's cylinder is z-aligned, so ``Ixx == Iyy`` (transverse) and ``Izz``
    is the barrel axis; both are the standard solid-cylinder results. Derived
    from mass + dims (R4), never hard-coded.
    """
    radius, half_h = dims
    height = 2.0 * half_h
    i_trans = mass * (3.0 * radius ** 2 + height ** 2) / 12.0
    i_axis = mass * radius ** 2 / 2.0
    return (i_trans, i_trans, i_axis)


def _diaginertia(geom: PhysicalGeom) -> tuple:
    """Return the inertia triple for a physical geom (shape dispatch)."""
    if geom.kind == CYLINDER:
        return _cylinder_diaginertia(geom.mass, geom.dims)
    return _box_diaginertia(geom.mass, geom.dims)


def _fmt(values) -> str:
    """Render a sequence of floats as a space-separated MJCF attribute value."""
    return ' '.join(f'{v:.8g}' for v in values)


def _quat_attr(object_pose) -> str:
    """Return the MJCF ``quat`` attribute value ``w x y z`` for a pose.

    MuJoCo orders a quaternion ``(w, x, y, z)``; :class:`robot_skills.Quaternion`
    orders it ``(x, y, z, w)``, so the components are reordered -- a silent
    transpose here would aim every object wrongly. An identity quaternion is
    emitted for the common case so the model text stays readable.
    """
    q = object_pose.orientation
    if (q.x, q.y, q.z, q.w) == (0.0, 0.0, 0.0, 1.0):
        return '1 0 0 0'
    return _fmt((q.w, q.x, q.y, q.z))


def _support_body_xml(support_top_z: float, support_center: tuple) -> str:
    """Return the finite support-surface ``<body>`` block (R5).

    A *static* body (no joint, no inertial) whose single collidable box has its
    top face at ``support_top_z``: the box center sits half a thickness below
    that, so an object placed with its bottom at ``support_top_z`` rests on the
    top face. It is a finite box, not a plane, so it cannot extend to infinity
    and intersect the robot's column or arms -- and it is offset horizontally
    to :data:`SUPPORT_CENTER` (see there) so its *footprint* is clear of the
    mast too.
    """
    cx, cy = support_center
    center_z = support_top_z - SUPPORT_HALF_THICKNESS
    return '\n'.join([
        f'<body name="{SUPPORT_BODY_NAME}" pos="{_fmt((cx, cy, center_z))}">',
        (f'  <geom name="{SUPPORT_BODY_NAME}_geom" type="box" '
         f'size="{_fmt((SUPPORT_HALF_EXTENT, SUPPORT_HALF_EXTENT, SUPPORT_HALF_THICKNESS))}" '
         f'friction="{_fmt(DEFAULT_FRICTION)}"/>'),
        '</body>',
    ])


def _object_body_xml(obj: WorldObject) -> str:
    """Return one object's ``<body>`` block (R4).

    A **graspable** object is a free-jointed body -- ``<freejoint>`` + a
    shape-derived ``<inertial>`` + a collidable ``<geom>`` with *default*
    contype/conaffinity (so it participates in contact) and no ``gravcomp``, so
    it falls under gravity and rests on the support surface. A **non-graspable**
    object is a static body: no joint, no inertial, just the collidable geom.
    """
    geom = physical_geom_for_label(obj.label)
    pos = obj.pose.position
    lines = [
        f'<body name="{obj.object_id}" pos="{_fmt((pos.x, pos.y, pos.z))}" '
        f'quat="{_quat_attr(obj.pose)}">',
    ]
    if obj.graspable:
        lines.append(f'  <freejoint name="{obj.object_id}_free"/>')
        lines.append(
            f'  <inertial pos="0 0 0" mass="{geom.mass:g}" '
            f'diaginertia="{_fmt(_diaginertia(geom))}"/>')
    lines.append(
        f'  <geom name="{obj.object_id}_geom" type="{geom.kind}" '
        f'size="{_fmt(geom.dims)}" friction="{_fmt(geom.friction)}"/>')
    lines.append('</body>')
    return '\n'.join(lines)


def collidable_world_bodies_xml(
        document: WorldDocument, *, support_top_z: float = SUPPORT_TOP_Z,
        support_center: tuple = SUPPORT_CENTER) -> str:
    """Return the ``<body>`` fragment for a world document's collidable scene.

    The fragment is passed to
    :func:`robot_description.mjcf_model.write_mjcf_model`'s ``world_bodies``
    keyword, which splices it into the worldbody *after* the floor, so the sim
    gets the drive floor **and** this support surface + these objects.

    Order is deterministic: the support surface first, then every object in
    ``document.objects`` order. ``support_top_z`` and ``support_center`` are
    keywords (R5) so a test can place the surface to match its own seeded
    object without touching a module constant -- and so a different scene can
    put the surface where its objects are. ``support_center`` must clear the
    robot's column mast (see :data:`SUPPORT_CENTER`); the default does.

    The fragment is *not* indented here -- ``mjcf_model._insert_world_bodies``
    re-indents it one level when it splices it in (the same contract
    ``FLOOR_BODIES`` relies on). An empty document still emits the support
    surface: the surface is part of "the physical world", and a scene with
    no objects still needs a place for one to be put.
    """
    blocks = [_support_body_xml(support_top_z, support_center)]
    blocks.extend(_object_body_xml(obj) for obj in document.objects)
    return '\n'.join(blocks)
