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


# -- Issue #127: roller-plant dynamics (isolated pure sim) --------------------
#
# These run the derived drivable MJCF (the same file the ROS sim loads) in
# ``mujoco`` directly: settle the base, then drive the three wheel joints at
# the body-to-wheel speeds for the command under test.  They pin the *coupled*
# plant behaviour #127 is about.
#
# Two measurement facts the assertions depend on, both established by probing
# on the laptop node (see docs/features/vx-wz-composition/implementation.md):
#
# 1. The signal is read in the **body frame**.  A holonomic base following a
#    ``vx + wz`` arc translates along a curve; measuring ``dx`` in the world
#    frame makes a correct plant look like it undershoots (the first ~1 s is a
#    velocity ramp and the rest is curved).  Body-frame forward speed is the
#    quantity the command names, so that is what is asserted.
# 2. The roller hinge |qvel| is **not** a grip signal: a roller that is rolling
#    spins fast on purpose.  Grip means the *contact patch* does not slide, so
#    the assertion reads the tangential relative velocity at the roller-floor
#    contact instead.

#: The three wheel joints, in the controller's command order (left/back/right).
_IK_MATRIX = (
    (-0.8660254037844386, 0.5, 0.125),
    (0.0, -1.0, 0.125),
    (0.8660254037844386, 0.5, 0.125),
)
#: The calibrated bridge signs (``robot_nav.omni_base_controller``); pinned so
#: this file fails loudly if the bridge convention and the plant drift apart.
WHEEL_SIGN = -1.0
WZ_SIGN = -1.0


def _body_to_wheel(vx, vy, wz):
    """Body twist -> the 3 wheel angular rates (the shipped bridge's IK)."""
    return tuple(
        (WHEEL_SIGN * (row[0] * vx + row[1] * vy) + WZ_SIGN * row[2] * wz) / WHEEL_RADIUS
        for row in _IK_MATRIX)


#: How long the command is held before measuring (the base reaches its steady
#: speed in ~1 s; ``docs/features/vx-wz-composition/implementation.md`` measures
#: the ramp's 90% point at ~1.0 s, so 1.5 s clears it with margin).
_RAMP_S = 1.5
#: Seed settle time before the command is applied (the base drops onto the
#: floor and stops bouncing).
_SETTLE_S = 1.0


def _drive(model, vx, vy, wz, *, settle=_SETTLE_S, ramp=_RAMP_S, duration=1.0):
    """Settle, ramp onto ``(vx, vy, wz)``, return the mean steady body twist.

    Returns ``(lin_x, lin_y, ang_z)`` averaged over a 1 s window taken **after**
    the ramp, in the **body frame** (``mj_objectVelocity`` with the local flag),
    so a curved path is not mistaken for a velocity error.  Deterministic: fixed
    model, fixed command, implicit integrator.
    """
    data = mujoco.MjData(model)
    for _ in range(int(settle / model.opt.timestep)):
        mujoco.mj_step(model, data)
    actuators = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
                 for name in (b'base_left_wheel', b'base_back_wheel',
                              b'base_right_wheel')]
    for actuator, rate in zip(actuators, _body_to_wheel(vx, vy, wz)):
        data.ctrl[actuator] = rate
    for _ in range(int(ramp / model.opt.timestep)):
        mujoco.mj_step(model, data)
    base = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'base_link')
    samples = []
    for _ in range(int(duration / model.opt.timestep)):
        mujoco.mj_step(model, data)
        velocity = np.zeros(6)
        mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_BODY, base,
                                 velocity, 1)
        samples.append(np.concatenate([velocity[3:6], velocity[:3]]))
    return np.mean(samples, axis=0)


def _roller_contact_slip(model, data):
    """Return the max tangential slip speed at any roller-floor contact (m/s).

    Slip is the velocity of the roller material **at the contact point**,
    relative to the (static) floor, projected into the contact tangent plane
    (``data.contact[i].frame`` is the contact basis: row 0 = normal, rows 1-2 =
    tangents).  The contact point's velocity is
    ``v_body + omega x (contact_pos - body_xpos)`` -- NOT the body-origin
    velocity, which for a spinning roller is unrelated to the patch and would
    report the roller's spin as "slip" (probed: the body-origin metric reads
    ~0.27 m/s where the true patch slip is ~0.02 m/s).

    A *gripping* roller has a small tangent component; a *scrubbing* one does
    not.
    """
    floor = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, b'floor_geom')
    greatest = 0.0
    for i in range(data.ncon):
        contact = data.contact[i]
        if floor not in (contact.geom1, contact.geom2):
            continue
        roller = contact.geom2 if contact.geom1 == floor else contact.geom1
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, roller) or ''
        if '_roller_' not in name:
            continue
        body = model.geom_bodyid[roller]
        velocity = np.zeros(6)
        mujoco.mj_objectVelocity(model, data, mujoco.mjtObj.mjOBJ_BODY, body,
                                 velocity, 0)
        omega, linear = velocity[:3], velocity[3:6]
        offset = np.asarray(contact.pos) - data.xpos[body]
        patch_velocity = linear + np.cross(omega, offset)
        frame = np.array(contact.frame).reshape(3, 3)
        tangent = frame @ patch_velocity
        greatest = max(greatest, float(np.hypot(tangent[1], tangent[2])))
    return greatest


def _settled_slip(model, vx, vy, wz, *, settle=_SETTLE_S, ramp=_RAMP_S,
                  duration=1.0):
    """Drive the command and return the max roller contact slip over the window.

    Measured after the ramp, like :func:`_drive`: the contact is only the
    steady rolling contact once the base has reached its commanded speed.
    """
    data = mujoco.MjData(model)
    for _ in range(int(settle / model.opt.timestep)):
        mujoco.mj_step(model, data)
    actuators = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
                 for name in (b'base_left_wheel', b'base_back_wheel',
                              b'base_right_wheel')]
    for actuator, rate in zip(actuators, _body_to_wheel(vx, vy, wz)):
        data.ctrl[actuator] = rate
    for _ in range(int(ramp / model.opt.timestep)):
        mujoco.mj_step(model, data)
    greatest = 0.0
    for _ in range(int(duration / model.opt.timestep)):
        mujoco.mj_step(model, data)
        greatest = max(greatest, _roller_contact_slip(model, data))
    return greatest


def test_write_mjcf_model_combined_vx_wz_composes_in_the_body_frame():
    """A combined ``vx + wz`` command delivers both channels (~#127).

    The #125-era note claimed a combined command "does not compose" (rollers
    slip/whirl).  Re-measured in the body frame, the shipped plant composes:
    the base holds the commanded forward speed AND the commanded yaw rate at
    the same time, with no off-axis slide.  This pins that so a future contact
    change cannot silently re-break the coupled channel.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    vx, wz = 0.3, 0.6
    twist = _drive(model, vx, 0.0, wz)
    lin_x, lin_y, ang_z = twist[0], twist[1], twist[5]
    assert lin_x >= 0.7 * vx, (
        'combined vx=%.2f wz=%.2f: body forward speed %.3f is below 0.7x '
        'commanded (%.3f) -- the coupled command does not compose'
        % (vx, wz, lin_x, 0.7 * vx))
    assert ang_z >= 0.7 * wz, (
        'combined vx=%.2f wz=%.2f: yaw rate %.3f is below 0.7x commanded '
        '(%.3f) -- rotation is scrubbed' % (vx, wz, ang_z, 0.7 * wz))
    assert abs(lin_y) <= 0.35 * vx, (
        'combined vx=%.2f wz=%.2f: body lateral speed %.3f is excessive -- the '
        'base is sliding sideways rather than following the arc'
        % (vx, wz, lin_y))


def test_write_mjcf_model_pure_channels_deliver_command():
    """Each pure channel delivers its command in the body frame (regression).

    ``vx`` -> forward, ``vy`` -> lateral, ``wz`` -> yaw, each at the commanded
    magnitude to within a generous tolerance, and each leaves the other
    channels near zero.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))

    for (vx, vy, wz), label in (
            ((0.3, 0.0, 0.0), '+vx'), ((0.0, 0.2, 0.0), '+vy'),
            ((0.0, 0.0, 0.6), '+wz')):
        twist = _drive(model, vx, vy, wz)
        lin_x, lin_y, ang_z = twist[0], twist[1], twist[5]
        # The commanded channel must be delivered at >= 0.8x, and the two
        # uncommanded channels must stay near zero.
        if vx:
            assert lin_x >= 0.8 * vx, ('pure %s: forward %.3f' % (label, lin_x))
        else:
            assert abs(lin_x) <= 0.15, ('pure %s: forward %.3f' % (label, lin_x))
        if vy:
            assert lin_y >= 0.8 * vy, ('pure %s: lateral %.3f' % (label, lin_y))
        else:
            assert abs(lin_y) <= 0.15, ('pure %s: lateral %.3f' % (label, lin_y))
        if wz:
            assert ang_z >= 0.8 * wz, ('pure %s: yaw %.3f' % (label, ang_z))
        else:
            assert abs(ang_z) <= 0.15, ('pure %s: yaw %.3f' % (label, ang_z))


def test_write_mjcf_model_roller_hinges_roll_rather_than_stall():
    """The rollers ROLL under a drive command (the #125 model is still live).

    A freely hinged rim roller must spin while the wheel rolls; a roller that
    stalls is a plain cylinder again and the plant would scrub.  (This is the
    *opposite* of the #127 note's "grip means ~0", which conflated rolling with
    slip: a rolling roller necessarily spins fast.  Grip is measured by the
    base actually delivering its commanded speed, which the composition and
    pure-channel tests assert.)
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    for _ in range(int(_SETTLE_S / model.opt.timestep)):
        mujoco.mj_step(model, data)
    actuators = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
                 for name in (b'base_left_wheel', b'base_back_wheel',
                              b'base_right_wheel')]
    for actuator, rate in zip(actuators, _body_to_wheel(0.3, 0.0, 0.0)):
        data.ctrl[actuator] = rate
    for _ in range(int(_RAMP_S / model.opt.timestep)):
        mujoco.mj_step(model, data)
    roller_dofs = [
        model.jnt_dofadr[joint] for joint in range(model.njnt)
        if '_roller_' in (mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT,
                                            joint) or '')]
    assert roller_dofs, 'the drivable model has no roller hinges'
    peak = 0.0
    for _ in range(200):
        mujoco.mj_step(model, data)
        peak = max(peak, max(abs(data.qvel[dof]) for dof in roller_dofs))
    assert peak >= 1.0, (
        'roller hinges barely moved (peak |qvel| %.3f rad/s) while driving '
        '+vx: they are not rolling, so the rim-roller model is not engaged'
        % peak)


def test_write_mjcf_model_contact_slip_stays_below_the_command():
    """The roller-floor contact slip never exceeds the commanded speed (#127).

    The instantaneous contact patch slides during roller-passing (the wheel
    hands off from one roller to the next), so the slip is not zero; what must
    hold is that it stays a fraction of the commanded speed -- a contact that
    slipped *at or beyond* the command would mean the base is being driven by
    friction alone, not by rolling.
    """
    path, _ = _write_to_tmp()
    model = mujoco.MjModel.from_xml_path(str(path))
    slip = _settled_slip(model, 0.3, 0.0, 0.0)
    assert slip <= 0.15, (
        'pure +vx=0.3: roller contact slip %.3f m/s is >= 0.15 (half the '
        'commanded speed) -- the rollers are scrubbing, not gripping' % slip)


def test_write_mjcf_model_rollers_are_passive_and_unactuated_on_every_path():
    """The welded escape hatch carries no rollers and stays the PR8b model.

    (#125 R5 restated for #127: the roller work never leaks into the welded
    path, so the plant-only change cannot alter the escape hatch.)
    """
    model_path, _ = _write_to_tmp(base_free_joint=False, floor=False)
    model = mujoco.MjModel.from_xml_path(str(model_path))
    assert model.nq == ACTUATED_DOF, model.nq
    assert model.nv == ACTUATED_DOF, model.nv
    assert model.nbody == WELDED_NBODY, model.nbody
    for joint in range(model.njnt):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint) or ''
        assert '_roller_' not in name
