# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR2 — the *write* (ROS-sim) path materializes a drivable, free-jointed base.

``test_mjcf_model.py`` pins the *bare* loader (:func:`load_mjcf_model`, the
welded model) and the in-process scene loader.  This file pins the third path,
:func:`write_mjcf_model` — the one the ``mujoco_ros2_control`` sim actually
loads — which since PR2 (issue #124, RULING 1/2) defaults to:

* a **free-jointed** ``base_link`` body (so the base can move) — nq = 18 + 7,
  nv = 18 + 6, nbody one more than the welded model; and
* a static **floor** world body one wheel radius below the base, so the
  velocity-commanded wheels have ground to push against.

Issue #125 (RULING 3/4/5) additionally gives each of the three drivable wheels
a ring of **rim rollers** — ``ROLLER_COUNT`` passive barrel bodies on tangent
hinges around the rim, and the hub cylinder shrunk to ``HUB_RADIUS`` so only the
rollers touch the floor.  That is a *sim-path-only* change (R1): it adds
``3 * ROLLER_COUNT`` bodies / hinge joints (so nq/nv/nbody grow by the same
amount), leaves ``nu`` at 18 (the rollers are unactuated, R4), and never touches
the welded escape hatch, the bare loader, or the in-process backend.

The bare-weld escape hatch (``base_free_joint=False, floor=False``) is asserted
to still reproduce the PR8b welded model (nq=nv=18, no rollers), so the default
changed without making the old model unreachable.
"""

import pathlib
import tempfile

import mujoco
import numpy as np

from robot_description.mjcf_model import (
    FLOOR_BODIES,
    load_mjcf_model,
    write_mjcf_model,
)

#: The URDF's non-fixed DOF (matches test_mjcf_model.ACTUATED_DOF).
ACTUATED_DOF = 18
#: Rollers per wheel (issue #125, R3): must match mjcf_model._ROLLER_COUNT.
ROLLER_COUNT = 8
#: Hub cylinder radius in the derived MJCF (shrunk below wheel_radius, R3).
HUB_RADIUS = 0.040
#: The three drivable wheel-link bodies the rollers hang off.
WHEEL_LINKS = (b'base_left_wheel_link', b'base_back_wheel_link',
               b'base_right_wheel_link')
#: A free joint adds 7 to nq and 6 to nv (mujoco 3.12; probed in PR2).
FREEJOINT_NQ = ACTUATED_DOF + 7
FREEJOINT_NV = ACTUATED_DOF + 6
#: The welded model's nbody (fusestatic number; test_mjcf_model).
WELDED_NBODY = 19
#: Roller contributions to the drivable model (issue #125, R5): each roller is
#: one body + one hinge joint (+1 nq, +1 nv, +1 nbody) -- never an actuator.
ROLLER_DOF = 3 * ROLLER_COUNT
#: The drivable write-path counts *with* the rim rollers (R5).
ROLLER_NQ = FREEJOINT_NQ + ROLLER_DOF
ROLLER_NV = FREEJOINT_NV + ROLLER_DOF
#: 19 welded bodies + the wrapped base_link + the floor + one per roller.
ROLLER_NBODY = WELDED_NBODY + 1 + 1 + ROLLER_DOF
#: wheel_radius from base.xacro: the floor top sits this far below base_link.
WHEEL_RADIUS = 0.05


def _write_to_tmp(**kwargs):
    """Write the derived MJCF to a temp file and return (path, text)."""
    path = pathlib.Path(tempfile.mkdtemp(prefix='pr2_mjcf_')) / 'scene.xml'
    text = write_mjcf_model(str(path), **kwargs)
    return path, text


def test_write_mjcf_model_default_is_free_jointed():
    """The default write path frees the base: nq = 18+7+3N, nv = 18+6+3N.

    Since #125 the default model also carries the rim rollers, so the counts
    are the PR2 free-joint counts plus one DOF per roller.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    assert model.nq == ROLLER_NQ, model.nq
    assert model.nv == ROLLER_NV, model.nv
    assert model.nbody == ROLLER_NBODY, model.nbody
    # The rollers are passive: nu stays the URDF's 18 actuators (R4).
    assert model.nu == ACTUATED_DOF, model.nu


def test_write_mjcf_model_rim_rollers_are_present_and_passive():
    """Each wheel carries ``ROLLER_COUNT`` passive hinge rollers (#125, R2/R3/R4).

    Asserts the roller *model* is what the brief claims: each wheel-link body
    has exactly ``ROLLER_COUNT`` child bodies, each child carries a free (no
    range) hinge joint with no actuator, and every roller's hinge axis is
    tangent to the rim -- orthogonal to the wheel's spin axis, which is the
    wheel-link's local +z.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))

    # Every actuator drives one of the 18 URDF joints, never a roller.
    actuated_joints = {int(model.actuator_trnid[a][0]) for a in range(model.nu)}

    for link in WHEEL_LINKS:
        link_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link)
        assert link_id >= 0, link
        child_bodies = [b for b in range(model.nbody)
                        if int(model.body_parentid[b]) == link_id]
        assert len(child_bodies) == ROLLER_COUNT, (link, len(child_bodies))

        # The wheel's spin axis is its local +z (base.xacro omni_wheel macro);
        # at the home pose that is the body's frame z, and since the free joint
        # starts at pos 0 with identity orientation, world z of each roller
        # equals its height above/below the axle plane.
        for child in child_bodies:
            name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, child)
            joints = [j for j in range(model.njnt)
                      if int(model.jnt_bodyid[j]) == child]
            assert len(joints) == 1, (name, joints)
            jid = joints[0]
            assert int(model.jnt_type[jid]) == int(mujoco.mjtJoint.mjJNT_HINGE), (
                name, int(model.jnt_type[jid]))
            # Free spin: no joint range (no limit) ...
            assert int(model.jnt_limited[jid]) == 0, name
            # ... and no actuator (passive).
            assert jid not in actuated_joints, name
            # The hinge axis lies in the wheel-link frame: tangent to the rim,
            # i.e. orthogonal to the spin axis (the link's local +z).
            axis = model.jnt_axis[jid]
            assert abs(float(axis[2])) < 1e-9, (name, axis)
            assert np.isclose(np.linalg.norm(axis), 1.0, atol=1e-6), (name, axis)

    # The roller bodies sit on the rim: their distance from the wheel centre in
    # the wheel-link frame is ROLLER_CENTER_RADIUS, all in the local xy-plane.
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    r_center = WHEEL_RADIUS - 0.007
    for link in WHEEL_LINKS:
        link_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link)
        origin = data.xpos[link_id]
        for b in range(model.nbody):
            if int(model.body_parentid[b]) != link_id:
                continue
            offset = data.xpos[b] - origin
            assert np.isclose(np.linalg.norm(offset), r_center, atol=1e-6), (
                mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b), offset)


def test_write_mjcf_model_hub_is_shrunk_below_the_roller_radius():
    """The wheel hub's cylinder radius drops below the rollers (#125, R3).

    Only the rollers may contact the floor, so the hub's derived cylinder geoms
    (collision + visual) must be smaller than ``wheel_radius``; the URDF keeps
    the full cylinder (D29), this is an MJCF-text change only.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    for link in WHEEL_LINKS:
        link_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link)
        hub_geoms = [
            g for g in range(model.ngeom)
            if int(model.geom_bodyid[g]) == link_id
            and int(model.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_CYLINDER)]
        assert hub_geoms, link
        for g in hub_geoms:
            assert np.isclose(float(model.geom_size[g][0]), HUB_RADIUS, atol=1e-9), (
                link, float(model.geom_size[g][0]))


def test_write_mjcf_model_rollers_are_what_touches_the_floor():
    """At home the roller outer surfaces (not the hub) sit on the floor.

    The roller centre radius plus the roller barrel radius must equal
    ``wheel_radius``: that is the contact radius the floor height assumes.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    lowest = None
    for link in WHEEL_LINKS:
        link_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link)
        for b in range(model.nbody):
            if int(model.body_parentid[b]) != link_id:
                continue
            roller_geoms = [g for g in range(model.ngeom)
                            if int(model.geom_bodyid[g]) == b]
            for g in roller_geoms:
                surface_z = (float(data.geom_xpos[g][2])
                             - float(model.geom_size[g][0]))
                lowest = surface_z if lowest is None else min(lowest, surface_z)
    assert lowest is not None, 'no roller geoms found'
    assert abs(lowest - (-WHEEL_RADIUS)) < 1e-6, lowest


def test_write_mjcf_model_default_has_base_link_body_with_free_joint():
    """The write path carries a ``base_link`` body and its free joint.

    On the welded model there is no ``base_link`` body at all (fusestatic folds
    the static trunk into the world).  The drivable model must have it, with a
    free joint, so ``GetBodyState('base_link')`` and the sim's mobile base work.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'base_link')
    assert body_id >= 0, 'base_link body missing from the write path'
    # The body carries exactly one free joint.
    joint_ids = [j for j in range(model.njnt)
                 if model.jnt_bodyid[j] == body_id]
    assert len(joint_ids) == 1, joint_ids
    joint_type = int(model.jnt_type[joint_ids[0]])
    assert joint_type == int(mujoco.mjtJoint.mjJNT_FREE), joint_type


def test_write_mjcf_model_default_splices_the_floor_world_body():
    """The default write path has a static floor body, welded to the world.

    The floor is what the velocity-commanded wheels push against.  It must be a
    sibling of ``base_link`` (a world body), not a child -- otherwise it would
    ride on the base.  Its geom is a plane.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    floor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'floor')
    assert floor_id >= 0, 'floor body missing from the write path'
    # The floor is a direct child of the world body (id 0).
    assert model.body_parentid[floor_id] == 0, model.body_parentid[floor_id]
    # ... and it has no joint (static / welded).
    assert not any(model.jnt_bodyid[j] == floor_id for j in range(model.njnt))
    # ... and at least one of its geoms is a plane.
    geoms = [g for g in range(model.ngeom) if model.geom_bodyid[g] == floor_id]
    assert geoms, 'floor body has no geoms'
    assert any(int(model.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_PLANE)
               for g in geoms)


def test_write_mjcf_model_floor_top_is_one_wheel_radius_below_the_base():
    """The floor plane's surface sits at z = -wheel_radius (the wheel bottoms).

    After the wrap the free joint starts at ``pos="0 0 0"`` (base_link at the
    world origin, axle height) and the wheels sit at base_link z = 0 with radius
    0.05, so the wheel bottoms are at world z = -0.05.  A plane's surface *is*
    its offset, so the geom pos z must be exactly -0.05 -- that is what makes
    the wheels rest on the floor rather than float or penetrate.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    floor_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'floor')
    geoms = [g for g in range(model.ngeom) if model.geom_bodyid[g] == floor_id]
    # geom_pos is in the body frame; the floor body is at the world origin, so
    # the geom's z offset *is* its world z.
    plane_zs = [float(model.geom_pos[g][2]) for g in geoms
                if int(model.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_PLANE)]
    assert plane_zs, 'no plane geom on the floor body'
    for z in plane_zs:
        assert abs(z - (-WHEEL_RADIUS)) < 1e-9, z


def test_write_mjcf_model_keeps_the_welded_escape_hatch():
    """``base_free_joint=False, floor=False`` reproduces the PR8b welded model.

    The default is the drivable model, but the old welded model (nq=nv=18,
    nbody=19, no floor) must stay reachable -- callers/tests that want the bare
    sim model can ask for it explicitly.
    """
    path, _ = _write_to_tmp(base_free_joint=False, floor=False)
    model = mujoco.MjModel.from_xml_path(str(path))
    assert model.nq == ACTUATED_DOF, model.nq
    assert model.nv == ACTUATED_DOF, model.nv
    assert model.nbody == WELDED_NBODY, model.nbody
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'floor') == -1
    # No rim rollers on the welded escape hatch (#125, R5).
    for link in WHEEL_LINKS:
        assert mujoco.mj_name2id(
            model, mujoco.mjtObj.mjOBJ_BODY,
            (link + b'_roller_0')) == -1


def test_write_mjcf_model_free_joint_but_no_floor():
    """``floor=False`` keeps the free base without the floor (the knobs are independent).

    The rim rollers are tied to ``base_free_joint`` (R2), not to ``floor``, so
    they are still present here -- only the floor body is missing.
    """
    path, _ = _write_to_tmp(floor=False)
    model = mujoco.MjModel.from_xml_path(str(path))
    assert model.nq == ROLLER_NQ, model.nq
    assert model.nv == ROLLER_NV, model.nv
    assert mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'floor') == -1
    for link in WHEEL_LINKS:
        link_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, link)
        children = [b for b in range(model.nbody)
                    if int(model.body_parentid[b]) == link_id]
        assert len(children) == ROLLER_COUNT, (link, len(children))


def test_write_mjcf_model_returns_the_text_it_wrote():
    """The return value is exactly the file contents (the caller-inspection seam)."""
    path, text = _write_to_tmp()
    assert path.read_text() == text


def test_write_mjcf_model_free_base_starts_at_the_origin():
    """The free joint's home qpos places base_link at the world origin.

    The acceptance drives from this start; a nonzero home would mean the base
    does not begin at ``start_location`` (charger at (0,0,0)).
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'base_link')
    np.testing.assert_allclose(data.xpos[body_id], [0.0, 0.0, 0.0], atol=1e-9)


def test_write_mjcf_model_wheels_rest_on_the_floor_at_home():
    """At the home pose the wheel bottoms touch the floor (no float, no penetration).

    This is the physics claim behind the floor height: with the free joint at
    the origin and the plane at z = -0.05, each wheel's lowest point is within a
    tolerance of the floor.  Read the wheel geom's lowest extent in the world
    frame via a forward pass.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    for wheel in (b'base_left_wheel_link', b'base_back_wheel_link',
                  b'base_right_wheel_link'):
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, wheel)
        assert body_id >= 0, wheel
        # The wheel link origin sits at base_link + mount offset; with the free
        # joint at the origin and the wheel axle at base_link z = 0, its world z
        # is (approximately) 0, and the wheel bottom is wheel_radius below it.
        wheel_z = float(data.xpos[body_id][2])
        bottom = wheel_z - WHEEL_RADIUS
        # The plane surface is at -0.05; the bottom must not be below it by more
        # than a millimetre (penetration) nor float above it (no contact).
        assert -WHEEL_RADIUS - 1e-3 <= bottom <= -WHEEL_RADIUS + 5e-3, (
            '%s bottom at %.4f, floor at %.4f' % (
                wheel.decode(), bottom, -WHEEL_RADIUS))


def test_load_mjcf_model_is_unchanged_by_the_write_path_default():
    """The bare loader still yields the welded model (PR2 did not touch it).

    Guards the RULING 1 boundary: only ``write_mjcf_model``'s default changed;
    ``load_mjcf_model`` stays the welded model the PR8b/MJCF tests pin.
    """
    model = load_mjcf_model()
    assert model.nq == ACTUATED_DOF
    assert model.nv == ACTUATED_DOF
    assert model.nbody == WELDED_NBODY


def test_floor_bodies_fragment_is_the_single_floor_source():
    """FLOOR_BODIES is exported and names the floor body (one source of truth)."""
    assert 'name="floor"' in FLOOR_BODIES
    assert 'type="plane"' in FLOOR_BODIES
