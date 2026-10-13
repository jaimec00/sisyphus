# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Issue #150 (PR A) — the physical world is real physics, not ghost bodies.

``robot_description.world_bodies`` turns a ``robot_world.WorldDocument`` into the
collidable ``<body>`` blocks the sim splices into its worldbody. This file is the
matching gate at the source (same home as ``test_mjcf_model.py`` /
``test_mjcf_drive.py``): the description package owns the converter, so it owns
the converter's physics here.

The claims are the issue's acceptance criteria, made exact against the probed
model (mujoco 3.12, implicit integrator — the sim's own):

1. **The XML is physical** — a graspable object's block carries a ``<freejoint>``
   + an ``<inertial>`` + a ``<geom>`` with **no** ``contype="0"``/``conaffinity="0"``
   and **no** ``gravcomp`` (those are the in-process backend's deliberate
   non-physical markers, R1/R4).
2. **The inertia is derived, not stale** — the ``diaginertia`` matches the
   solid-box / solid-cylinder formula for the declared mass + dims, recomputed
   here by hand (R4; a stale/zero tensor is the failure this catches).
3. **A seeded object rests at its pose** — compiled through the *same* path the
   sim uses (``write_mjcf_model`` with ``world_bodies``), a graspable object
   seeded with its bottom on the support surface settles at its seed pose (center
   z ≈ seed z, x/y unchanged) — no fall-through, no drift, no NaN.
4. **It really collides** — an applied ``xfrc_applied`` perturbation moves it,
   which only happens if contype/conaffinity are actually enabled (R4).
5. **The support surface is finite** — a box, not an infinite plane, whose top is
   at ``SUPPORT_TOP_Z`` (R5): a plane at counter height would cut the robot's
   column and arms.

The geometry mirror of ``robot_moveit.scene_geometry`` is asserted too, because
the sim and the planning scene must agree on every object or the plan collides
against a different world than the one it executes in.

Issue #152 adds the derived-pad path: ``derived_support_surfaces`` returns
**one pad per graspable object** (the shipped seed's objects rest at several
different tops, and the kitchen trio straddles the corridor, so one surface
cannot serve them), and the converter emits one ``world_support_N`` per pad.
The claims here extend to that path -- the *shipped seed* (not a synthetic
one-object scene) settles with every graspable at its seed pose, in resting
contact with one of the derived pads.
"""

import math
import pathlib
import tempfile

import mujoco
import numpy as np

from robot_description.mjcf_model import write_mjcf_model
from robot_description.world_bodies import (
    collidable_world_bodies_xml,
    DEFAULT_BOX_SIZE,
    DERIVED_HALF_EXTENT_MARGIN,
    DERIVED_MIN_HALF_EXTENT,
    derived_support_surfaces,
    physical_geom_for_label,
    PHYSICAL_GEOMS_BY_LABEL,
    SUPPORT_BODY_NAME,
    SUPPORT_CENTER,
    SUPPORT_HALF_EXTENT,
    SUPPORT_HALF_THICKNESS,
    SUPPORT_TOP_Z,
)
from robot_skills import Point, Pose
from robot_world import default_seed_document, WorldDocument, WorldObject


def _seed_object(label, *, x=None, y=None, z=None, graspable=True,
                 half_extent=None):
    """Build a standalone ``WorldObject`` whose bottom rests on the surface.

    ``x``/``y`` default to the surface's default centre (:data:`SUPPORT_CENTER`,
    which the converter must keep clear of the robot's mast — see there), and
    ``z`` to ``SUPPORT_TOP_Z + half_extent`` — the pose that puts the object's
    bottom exactly on the support surface — so a passing rest test is the
    meaningful claim (the object was placed, not dropped). ``half_extent``
    defaults to the label's vertical half-height.
    """
    geom = physical_geom_for_label(label)
    if half_extent is None:
        half_extent = (geom.dims[2] if geom.kind == 'box' else geom.dims[1])
    if x is None:
        x = SUPPORT_CENTER[0]
    if y is None:
        y = SUPPORT_CENTER[1]
    if z is None:
        z = SUPPORT_TOP_Z + half_extent
    return WorldObject(object_id='obj_1', label=label,
                       pose=Pose(position=Point(x, y, z)), graspable=graspable)


def _seed_document(obj, *, support_top_z=SUPPORT_TOP_Z):
    """Wrap one object in a minimal valid ``WorldDocument``."""
    return WorldDocument(
        locations={'charger': Pose.from_xyz(0.0, 0.0, 0.0)},
        start_location='charger',
        objects=(obj,),
    )


def _materialize(document):
    """Write the derived MJCF through the sim's own path; return (path, text)."""
    path = pathlib.Path(tempfile.mkdtemp(prefix='i150_world_')) / 'scene.xml'
    text = write_mjcf_model(str(path),
                            world_bodies=collidable_world_bodies_xml(document))
    return path, text


#: Integration time for "the object has settled": 20 000 steps of 2 ms sim time
#: is 40 s — far longer than the ~ms a resting cylinder takes to come to rest,
#: and the same budget the context map's probe used (VERIFIED).
SETTLE_STEPS = 20000

#: The resting heights the shipped seed derives (issue #152 R1'), in ascending
#: order, one pad per graspable: ``cup_1`` (top 0.87), ``book_1`` (0.885), then
#: the kitchen trio (``mug_1``/``plate_1``/``bowl_1``, all 1.10). Spelled out so
#: a seed move that changes a resting height fails here rather than inside a
#: 40 s settle run.
SEED_SUPPORT_TOPS = (0.87, 0.885, 1.10, 1.10, 1.10)


def _resting_half(obj):
    """Return the distance from an object's centre down to its resting face."""
    geom = physical_geom_for_label(obj.label)
    return geom.dims[2] if geom.kind == 'box' else geom.dims[1]


def test_graspable_object_xml_is_physically_real():
    """A graspable block is free-jointed + inertial + a *default* collidable geom.

    R4: the in-process backend's ghost markers (``contype="0"``,
    ``conaffinity="0"``, ``gravcomp``) must NOT appear — the sim object has to
    fall under gravity and collide. The inertia is present so the free body
    compiles and moves with a real tensor.
    """
    document = _seed_document(_seed_object('mug'))
    _, text = _materialize(document)
    # The object's own block.
    start = text.index('<body name="obj_1"')
    block = text[start:text.index('</body>', start)]
    assert '<freejoint name="obj_1_free"/>' in block, block
    assert '<inertial' in block, block
    assert '<geom name="obj_1_geom"' in block, block
    # The non-physical markers are forbidden (R1/R4).
    assert 'contype="0"' not in block, block
    assert 'conaffinity="0"' not in block, block
    assert 'gravcomp' not in block, block


def test_non_graspable_object_is_static():
    """A non-graspable object has no joint and no inertial (R4)."""
    document = _seed_document(_seed_object('counter', graspable=False))
    _, text = _materialize(document)
    start = text.index('<body name="obj_1"')
    block = text[start:text.index('</body>', start)]
    assert 'freejoint' not in block, block
    assert 'inertial' not in block, block
    assert '<geom name="obj_1_geom"' in block, block


def test_inertia_matches_solid_shape_formula_by_hand():
    """``diaginertia`` equals the solid-box / solid-cylinder formula (R4).

    Recompute from the declared mass + dims here; a stale/zero tensor (e.g. a
    retune that left the numbers behind) is exactly what this catches.
    """
    # Box: full extents 2*dims.
    hx, hy, hz = PHYSICAL_GEOMS_BY_LABEL['book'].dims
    mass = PHYSICAL_GEOMS_BY_LABEL['book'].mass
    x, y, z = 2 * hx, 2 * hy, 2 * hz
    expected = (mass * (y * y + z * z) / 12.0,
                mass * (x * x + z * z) / 12.0,
                mass * (x * x + y * y) / 12.0)
    model = mujoco.MjModel.from_xml_string(
        '<mujoco><worldbody>'
        + collidable_world_bodies_xml(_seed_document(_seed_object('book')))
        + '</worldbody></mujoco>')
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'obj_1')
    np.testing.assert_allclose(model.body_inertia[gid], expected, rtol=1e-6)

    # Cylinder: (radius, half-height), z-aligned.
    radius, half_h = PHYSICAL_GEOMS_BY_LABEL['mug'].dims
    mass = PHYSICAL_GEOMS_BY_LABEL['mug'].mass
    height = 2 * half_h
    i_trans = mass * (3 * radius ** 2 + height ** 2) / 12.0
    i_axis = mass * radius ** 2 / 2.0
    model = mujoco.MjModel.from_xml_string(
        '<mujoco><worldbody>'
        + collidable_world_bodies_xml(_seed_document(_seed_object('mug')))
        + '</worldbody></mujoco>')
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'obj_1')
    np.testing.assert_allclose(model.body_inertia[gid],
                               (i_trans, i_trans, i_axis), rtol=1e-6)


def test_seeded_object_rests_at_its_seed_pose():
    """A seeded graspable object settles at its pose — no fall-through, no drift.

    Compiled through ``write_mjcf_model`` (the sim's path: free base + implicit
    integrator + rim rollers), the object placed with its bottom on the support
    surface must stay there: center z ≈ seed z, x/y unchanged, no NaN.
    """
    obj = _seed_object('mug')
    document = _seed_document(obj)
    path, _ = _materialize(document)
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    for _ in range(SETTLE_STEPS):
        mujoco.mj_step(model, data)

    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'obj_1')
    assert body_id >= 0
    settled = data.xpos[body_id].copy()
    seed = np.array([obj.pose.position.x, obj.pose.position.y, obj.pose.position.z])
    assert not np.isnan(settled).any(), settled
    # x/y unchanged (the object did not slide off the surface) ...
    np.testing.assert_allclose(settled[:2], seed[:2], atol=1e-3)
    # ... and the center stayed at the seed height (bottom on the surface).
    # 1e-3 leaves ~50x margin over the VERIFIED ~2e-5 m real settle error,
    # while catching a ~5 mm fall-through the old 1e-2 bound let slip.
    assert abs(settled[2] - seed[2]) < 1e-3, (settled[2], seed[2])


def test_applied_perturbation_moves_the_object():
    """Proves the object forms a real resting contact, and is a movable body.

    Two independent checks:

    * **Contact pair (the real proof, R4/R5).** After settling, some contact in
      ``data.contact[:data.ncon]`` must pair the object's geom with the support
      surface's geom. ``xfrc_applied`` is a direct DOF force applied regardless
      of contype/conaffinity, so the push below alone would pass on a
      collision-inert ghost body (contype=0) that free-falls through the
      surface. The contact-pair assertion is what proves the object's geom is
      genuinely collidable and resting on the surface.
    * **Applied perturbation moves it (movability only).** A push moves the free
      body, proving it is not welded/static — not that it collides.
    """
    obj = _seed_object('mug')
    path, _ = _materialize(_seed_document(obj))
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    for _ in range(2000):
        mujoco.mj_step(model, data)
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'obj_1')

    # The object's geom and the support surface's geom must form a resting
    # contact pair (a ghost body would form none).
    obj_gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, b'obj_1_geom')
    support_gid = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_GEOM, b'world_support_geom')
    assert obj_gid >= 0 and support_gid >= 0
    pairs = [frozenset((int(c.geom1), int(c.geom2)))
             for c in data.contact[:data.ncon]]
    assert frozenset((obj_gid, support_gid)) in pairs, (
        'object is not in resting contact with the support surface; the geom '
        'has contype/conaffinity disabled (or it fell through): %r' % (pairs,))

    before = data.xpos[body_id].copy()
    data.xfrc_applied[body_id][0] = 20.0
    for _ in range(200):
        mujoco.mj_step(model, data)
    after = data.xpos[body_id].copy()
    assert not np.isnan(after).any(), after
    assert abs(after[0] - before[0]) > 1e-3, (before, after)


def test_support_surface_is_a_finite_box_with_its_top_at_support_top_z():
    """The support surface is a *finite* box, top face at ``SUPPORT_TOP_Z`` (R5).

    A plane at counter height would be infinite and intersect the robot's column
    and arms; the surface must be a bounded box. Its top must sit exactly at
    ``SUPPORT_TOP_Z`` so a seeded object rests there.
    """
    document = _seed_document(_seed_object('mug'))
    _, text = _materialize(document)
    assert 'name="world_support"' in text
    model = mujoco.MjModel.from_xml_path(
        str(_materialize(document)[0]))
    sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'world_support')
    assert sid >= 0, 'support body missing'
    # Static: no joint.
    assert not any(model.jnt_bodyid[j] == sid for j in range(model.njnt))
    geoms = [g for g in range(model.ngeom) if model.geom_bodyid[g] == sid]
    assert len(geoms) == 1, geoms
    g = geoms[0]
    assert int(model.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_BOX), (
        'support surface must be a finite box, not a plane')
    # size is half-extents; the top face is center_z + half-thickness.
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    top = float(data.geom_xpos[g][2]) + float(model.geom_size[g][2])
    assert abs(top - SUPPORT_TOP_Z) < 1e-9, (top, SUPPORT_TOP_Z)
    # Horizontal half-extents bound the surface (finite, R5).
    assert float(model.geom_size[g][0]) == SUPPORT_HALF_EXTENT
    assert float(model.geom_size[g][1]) == SUPPORT_HALF_EXTENT
    assert float(model.geom_size[g][2]) == SUPPORT_HALF_THICKNESS
    # The surface is offset clear of the robot's mast (which stands at the
    # origin and spans the surface's height): its footprint must NOT cover the
    # origin, or the mast would pierce it and eject any object placed on it
    # (VERIFIED in the full model -- see SUPPORT_CENTER).
    center = data.geom_xpos[g]
    x_lo = float(center[0]) - SUPPORT_HALF_EXTENT
    x_hi = float(center[0]) + SUPPORT_HALF_EXTENT
    y_lo = float(center[1]) - SUPPORT_HALF_EXTENT
    y_hi = float(center[1]) + SUPPORT_HALF_EXTENT
    assert not (x_lo <= 0.0 <= x_hi and y_lo <= 0.0 <= y_hi), (
        'the support surface footprint covers the robot origin, where the '
        'column mast stands (spanning SUPPORT_TOP_Z); it would pierce the '
        'surface. Got centre %r.' % (tuple(center),))


def test_default_support_surface_does_not_touch_the_robot_trunk():
    """At home the default support surface has no contact with the robot (R5).

    The mast spans world z 0.115..1.445 at the origin, right through
    ``SUPPORT_TOP_Z``; the surface must be offset clear of it. This compiles the
    full sim model (robot + rollers + surface + an object) and asserts no
    contact involves the support geom and the robot at the home state -- a
    surface that touched the robot would fight it every step.
    """
    path, _ = _materialize(_seed_document(_seed_object('mug')))
    model = mujoco.MjModel.from_xml_path(str(path))
    support_gid = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_GEOM, b'world_support_geom')
    assert support_gid >= 0
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    for c in data.contact[:data.ncon]:
        assert support_gid not in (c.geom1, c.geom2), (
            'the support surface contacts the robot at home: %s <-> %s'
            % (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, c.geom1),
               mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, c.geom2)))


def test_geometry_mirrors_the_planning_scene_table():
    """Every mirrored label keeps the planning scene's shape and extents (R3).

    ``robot_description`` cannot import ``robot_moveit`` (dependency cycle), so
    the mirror is checked against the values ``scene_geometry`` documents: BOX
    full extents -> MuJoCo half-extents; CYLINDER (r, height) -> (r, half-height).
    A drift here means the planner and the sim disagree about the world.
    """
    # The scene_geometry values for the labels both tables know (full extents /
    # full height), transcribed here so the mirror has a fixed reference.
    scene_geometry = {
        'mug': ('cylinder', (0.04, 0.10)),
        'cup': ('cylinder', (0.035, 0.09)),
        'plate': ('cylinder', (0.12, 0.01)),
        'bowl': ('cylinder', (0.08, 0.05)),
        'book': ('box', (0.20, 0.14, 0.03)),
        'remote': ('box', (0.18, 0.05, 0.02)),
        'counter': ('box', (0.60, 0.60, 0.90)),
        'sofa': ('box', (1.80, 0.80, 0.40)),
        'table': ('box', (0.80, 0.80, 0.40)),
        'chair': ('box', (0.45, 0.45, 0.90)),
        'shelf': ('box', (1.20, 0.40, 0.75)),
        'cabinet': ('box', (0.35, 0.35, 1.70)),
        'bin': ('box', (0.25, 0.35, 0.30)),
        'drum': ('cylinder', (0.20, 0.30)),
    }
    assert set(scene_geometry) <= set(PHYSICAL_GEOMS_BY_LABEL), (
        'a mirror label is missing: %r'
        % (set(scene_geometry) - set(PHYSICAL_GEOMS_BY_LABEL)))
    for label, (kind, dims) in scene_geometry.items():
        geom = PHYSICAL_GEOMS_BY_LABEL[label]
        assert geom.kind == kind, (label, geom.kind, kind)
        if kind == 'box':
            expected = tuple(d / 2.0 for d in dims)
        else:
            expected = (dims[0], dims[1] / 2.0)
        np.testing.assert_allclose(geom.dims, expected, rtol=1e-12, err_msg=label)


def test_unknown_label_falls_back_to_the_planning_scene_default_box():
    """An unknown label gets the same 5 cm box the planning scene uses (R3)."""
    geom = physical_geom_for_label('total_nonsense')
    assert geom.kind == 'box'
    np.testing.assert_allclose(geom.dims,
                               tuple(d / 2.0 for d in DEFAULT_BOX_SIZE))
    assert geom.mass > 0.0


def test_quaternion_is_reordered_to_mujoco_wxyz():
    """A non-identity pose quaternion is emitted MuJoCo-order (w x y z).

    ``robot_skills.Quaternion`` is (x, y, z, w); MJCF is (w, x, y, z). A 90 deg
    Z rotation (x=0, y=0, z=0.7071, w=0.7071) must land on the body as
    ``0.7071 0 0 0.7071`` — not transposed.
    """
    half = math.sqrt(2.0) / 2.0
    obj = WorldObject(
        object_id='obj_1', label='mug',
        pose=Pose(position=Point(0.0, 0.0, SUPPORT_TOP_Z + 0.05),
                  orientation=type(_seed_object('mug').pose.orientation)(
                      x=0.0, y=0.0, z=half, w=half)))
    model = mujoco.MjModel.from_xml_string(
        '<mujoco><worldbody>'
        + collidable_world_bodies_xml(_seed_document(obj))
        + '</worldbody></mujoco>')
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'obj_1')
    # body_quat is MuJoCo's (w, x, y, z).
    np.testing.assert_allclose(model.body_quat[bid],
                               (half, 0.0, 0.0, half), atol=1e-9)


def test_derived_support_surfaces_are_one_pad_per_graspable_object():
    """The seed derives one pad per graspable, under that object (issue #152 R1').

    Five graspables, five pads, emitted in ascending top order: the table pair's
    (``cup_1`` top 0.87, ``book_1`` 0.885) first, then the kitchen trio's
    (``mug_1``/``plate_1``/``bowl_1``, all 1.10 -- three pads, not one).  Each
    pad must be centred on its own object, top at that object's underside, never
    narrower than the derived minimum, and wide enough to hold the object.
    """
    document = default_seed_document()
    surfaces = derived_support_surfaces(document)
    graspables = [obj for obj in document.objects if obj.graspable]

    assert [round(top_z, 6) for top_z, _, _, _ in surfaces] == \
        list(SEED_SUPPORT_TOPS)
    assert len(surfaces) == len(graspables), (surfaces, graspables)
    for obj in graspables:
        geom = physical_geom_for_label(obj.label)
        position = obj.pose.position
        matching = [s for s in surfaces if s[1] == position.x and s[2] == position.y]
        assert matching, 'no pad centred under %s' % (obj.object_id,)
        (top_z, _, _, half_extent), = matching
        assert math.isclose(top_z, position.z - _resting_half(obj)), obj.object_id
        assert half_extent >= DERIVED_MIN_HALF_EXTENT, (obj.object_id, half_extent)
        assert half_extent > geom.dims[0], obj.object_id


def test_derived_support_surfaces_ignore_non_graspable_furniture():
    """Only graspables rest on surfaces: furniture is an obstacle, not a place.

    A document whose only object is the non-graspable sofa derives no surface
    at all (issue #152 R1) -- a surface under a sofa would be a place the sim
    invents out of thin air.
    """
    document = _seed_document(_seed_object('sofa', graspable=False))
    assert derived_support_surfaces(document) == []


def test_derived_surface_is_the_footprint_plus_a_margin_with_a_floor():
    """A pad is its object's footprint plus the margin, floored at the minimum.

    The half-extent formula is R1's: ``max(MIN, footprint_half + MARGIN)``,
    where the footprint half is a BOX's larger x/y half-size and a CYLINDER's
    radius.  A mug (radius 0.04) is under the floor, so it comes out at the
    floor; a plate (radius 0.12) and a book (half 0.10) are over it, so they
    come out at footprint + margin.  The pad is centred on the object.
    """
    for label in ('mug', 'cup', 'bowl'):
        lone = _seed_document(_seed_object(label))
        (top_z, cx, cy, half_extent), = derived_support_surfaces(lone)
        assert math.isclose(half_extent, DERIVED_MIN_HALF_EXTENT), label
        # The lone object's own centre is the pad centre.
        assert math.isclose(cx, SUPPORT_CENTER[0]) and math.isclose(cy, SUPPORT_CENTER[1])

    for label, footprint_half in (('plate', 0.12), ('book', 0.10)):
        lone = _seed_document(_seed_object(label))
        (_, cx, cy, half_extent), = derived_support_surfaces(lone)
        assert math.isclose(half_extent, footprint_half + DERIVED_HALF_EXTENT_MARGIN), label
        assert math.isclose(cx, SUPPORT_CENTER[0]) and math.isclose(cy, SUPPORT_CENTER[1])


def test_two_objects_at_one_height_get_two_pads_not_one_span():
    """Per-object pads: two objects at one height derive two pads (R1').

    This is the shape the shipped seed needs: two objects at the same resting
    height on opposite sides of ``y = 0`` must each get their own pad, rather
    than one grouped surface spanning the gap (which would cross the corridor).
    """
    spread = WorldDocument(
        locations={'charger': Pose.from_xyz(0.0, 0.0, 0.0)},
        start_location='charger',
        objects=tuple(
            WorldObject(object_id='mug_%d' % i, label='mug',
                        pose=Pose(position=Point(2.0 + 0.3 * i, 3.0, 1.15)))
            for i in range(3)
        ),
    )
    surfaces = derived_support_surfaces(spread)
    assert len(surfaces) == 3, surfaces
    assert [round(top_z, 6) for top_z, _, _, _ in surfaces] == [1.10, 1.10, 1.10]
    assert [cx for _, cx, _, _ in surfaces] == [2.0, 2.3, 2.6]
    for _, _, cy, half_extent in surfaces:
        assert math.isclose(cy, 3.0)
        assert math.isclose(half_extent, DERIVED_MIN_HALF_EXTENT)


def test_default_converter_path_still_emits_the_single_named_surface():
    """``support_surfaces=None`` keeps the historical ``world_support`` (R1).

    Backward compatibility: a caller that does not ask for derived surfaces
    gets exactly one surface, under the name every pre-#152 test greps for.
    """
    xml = collidable_world_bodies_xml(default_seed_document())
    assert 'name="%s"' % SUPPORT_BODY_NAME in xml
    assert 'name="%s_0"' % SUPPORT_BODY_NAME not in xml


def test_the_shipped_seed_rests_at_its_seed_pose_on_derived_surfaces():
    """Every shipped graspable settles at its seed pose on a derived pad.

    The acceptance-1 gate for issue #152: the *shipped* seed -- not a synthetic
    one-object scene -- materialized through the sim's own path
    (``write_mjcf_model`` + derived pads) and settled for
    :data:`SETTLE_STEPS`. Every graspable must stay within 1 mm of its seed pose
    (no ejection, no fall-through, no drift, no NaN) **and** its geom must form
    a resting contact with one of the derived ``world_support_*`` geoms, which
    is what proves it is resting rather than hovering.
    """
    document = default_seed_document()
    surfaces = derived_support_surfaces(document)
    path = pathlib.Path(tempfile.mkdtemp(prefix='i152_seed_')) / 'scene.xml'
    write_mjcf_model(str(path), world_bodies=collidable_world_bodies_xml(
        document, support_surfaces=surfaces))
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    for _ in range(SETTLE_STEPS):
        mujoco.mj_step(model, data)

    support_geoms = {
        g for g in range(model.ngeom)
        if (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g) or '')
        .startswith(SUPPORT_BODY_NAME)
    }
    assert len(support_geoms) == len(surfaces), (
        'expected one collidable geom per derived surface: %r' % (support_geoms,))
    pairs = [frozenset((int(c.geom1), int(c.geom2)))
             for c in data.contact[:data.ncon]]

    graspables = [obj for obj in document.objects if obj.graspable]
    assert graspables, 'the shipped seed should hold graspable objects'
    for obj in graspables:
        body_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY, obj.object_id.encode())
        assert body_id >= 0, obj.object_id
        settled = data.xpos[body_id].copy()
        seed = np.array([obj.pose.position.x, obj.pose.position.y,
                         obj.pose.position.z])
        assert not np.isnan(settled).any(), (obj.object_id, settled)
        # 1e-3 leaves ~50x margin over the ~2e-5 m real settle error while
        # still catching a millimetre-scale fall-through or slide.
        np.testing.assert_allclose(settled, seed, atol=1e-3,
                                   err_msg=obj.object_id)
        geom_id = mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_GEOM, (obj.object_id + '_geom').encode())
        assert geom_id >= 0, obj.object_id
        assert any(geom_id in pair and (pair - {geom_id}) & support_geoms
                   for pair in pairs), (
            '%s is not in resting contact with a derived support surface: %r'
            % (obj.object_id, pairs))
