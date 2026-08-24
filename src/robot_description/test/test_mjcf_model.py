# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR7/PR8b — the MuJoCo MJCF derivation gate (issues #89 / #92).

The URDF stays the single source of truth; the MJCF is *derived* from it at
call time by ``robot_description.mjcf_model.load_mjcf_model`` and the
hand-authored overlay in ``mjcf/overlay.xml`` is the only MJCF text in the
repo. This file is the matching claim *at the source* (same home as
``test_robot_model.py``): the description package owns the loader, so it owns
the loader's gate here, independent of any consumer.

The assertions are the issues' acceptance criteria, made exact against the
probed model (see ``docs/features/i92-pr8b-mujoco-sim-bringup/status.md``
rulings R-PR8b-6/11/12):

1. MuJoCo loads the derived model (no exception) — the load itself is the
   strongest single claim, covering the expand footprint, mesh resolution and
   the overlay splice.
2. ``nq == nv == 18`` — the URDF's 18 non-fixed DOF (3 continuous wheels +
   1 prismatic column + 14 revolute arms/grippers) all survive derivation.
   ``nu == 18`` — dfki-ric/mujoco_ros2_control's franka-hand pattern (PR8b
   probe; R-PR8b-12): every gripper joint, including the 2 mirror jaws, gets
   its own position actuator, and dfki-ric derives the mirror command from the
   driven joint's command via the URDF <mimic> (multiplier -1). So all 18 DOF
   are actuated (3 <velocity> wheels + 15 <position>). ``neq == 0`` — no
   <equality> constraints; dfki-ric reads <mimic> natively (it would fight a
   MuJoCo equality constraint).
3. ``nbody == 19`` — this is the **fusestatic** number, NOT the URDF's 32
   links (R-PR7-5). The static trunk (``base_link``/``base_chassis_link``/
   ``column_rail_link`` and the massless frames) is folded into the world body
   by MuJoCo's default ``fusestatic``, which is exactly the D31 check: the
   fixed-jointed ``column_rail_link`` IS folded in. Asserting 19 pins the
   *derived* reality rather than inheriting the 32-link assumption.
4. One ``mj_step`` smoke without NaN — the tree is physically steppable
   (contacts engage) and nothing explodes.
5. The head camera sits at the REP-103 optical frame — a ``head_camera``
   camera + ``head_camera_site`` at pos ``(0,0,0.05)`` with euler
   ``(0, -pi/2, +pi/2)`` relative to the ``column_top`` body (R-PR7-6). That
   frame is where the URDF's ``head_camera_optical_frame`` lives once
   ``fusestatic`` folds the massless camera frames away.
6. PR8b (dfki-ric): the overlay names every commandable actuator (18: 3
   <velocity> wheels + 15 <position>) and drives the gripper mirrors from the
   driven command via the URDF <mimic> tag.
"""

import mujoco
import numpy as np

from robot_description.mjcf_model import load_mjcf_model

#: URDF-derived DOF count: 3 continuous + 1 prismatic + 14 revolute = 18.
#: Every non-fixed joint survives derivation, so nq == nv == 18.
ACTUATED_DOF = 18

#: Commandable MJCF actuators after the dfki-ric swap (R-PR8b-12): 18 — 3
#: <velocity> wheels + 13 <position> (column, 10 arm, 2 driven grippers) + 2
#: <position> mirror jaws. dfki-ric gives every gripper joint its own actuator
#: (franka-hand pattern) and derives the mirror command from the driven
#: command via the URDF <mimic>, so nu == nq == nv == 18.
COMMANDABLE_ACTUATORS = 18

#: MuJoCo equality constraints. dfki-ric reads URDF <mimic> natively (R-PR8b-6
#: probe), so no <equality> block is spliced; neq stays 0 (a MuJoCo equality
#: would fight the plugin's own mirror-command handling).
GRIPPER_MIMIC_EQUALITIES = 0

#: ``nbody`` after MuJoCo's default ``fusestatic`` folds the static trunk
#: (base + column_rail_link + massless frames) into the world body. Distinct
#: from the URDF's 32 links by design — see module docstring / R-PR7-5.
FUSESTATIC_NBODY = 19

#: Head-camera optical-frame pose relative to the ``column_top`` body: the
#: URDF's ``head_camera_mount`` xyz (0,0,0.05) composed with the
#: ``head_camera_optical_joint`` rpy (0, -pi/2, +pi/2) (R-PR7-6).
CAM_REL_POS = (0.0, 0.0, 0.05)


def test_derived_mjcf_compiles():
    """Build the derived model from the URDF plus overlay without exception."""
    model = load_mjcf_model()
    assert model is not None


def test_nq_nv_match_urdf_derived_dof_counts():
    """Joints agree with the URDF's 18 non-fixed DOF (all survive derivation)."""
    model = load_mjcf_model()
    assert model.nq == ACTUATED_DOF
    assert model.nv == ACTUATED_DOF


def test_nu_reflects_dfki_ric_all_dof_actuated():
    """Commandable actuators = 18 (every DOF, incl. both gripper mirrors)."""
    model = load_mjcf_model()
    assert model.nu == COMMANDABLE_ACTUATORS


def test_neq_no_equality_constraints():
    """dfki-ric reads URDF <mimic> natively; no <equality> is spliced."""
    model = load_mjcf_model()
    assert model.neq == GRIPPER_MIMIC_EQUALITIES


def test_nbody_reflects_fusestatic_not_urdf_link_count():
    """Fusestatic folds the static trunk into the world body (D31 check)."""
    model = load_mjcf_model()
    assert model.nbody == FUSESTATIC_NBODY


def test_all_commandable_actuators_are_named_and_target_the_joint():
    """Every commandable actuator is named == the joint it drives (ros2_control seam).

    dfki-ric's mujoco_ros2_control maps URDF ros2_control joints to MJCF
    actuators by name / target joint (R-PR8b-6; probe), so every one of the 18
    commandable actuators must carry the joint's name and target that joint —
    including the 2 gripper-mirror jaws (each its own <position> actuator).
    """
    model = load_mjcf_model()
    assert model.nu == COMMANDABLE_ACTUATORS
    for i in range(model.nu):
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, i)
        assert name is not None, f'actuator {i} is unnamed'
        trnt = int(model.actuator_trntype[i])
        trid = int(model.actuator_trnid[i, 0])
        assert trnt == mujoco.mjtTrn.mjTRN_JOINT, f'actuator {i} not joint-type'
        jn = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, trid)
        assert jn == name, f'actuator {name!r} targets {jn!r}, not itself'


def test_one_mj_step_smoke_has_no_nan():
    """A single integration step stays finite (no exploding tree)."""
    model = load_mjcf_model()
    data = mujoco.MjData(model)
    mujoco.mj_step(model, data)
    assert not np.isnan(data.qpos).any()
    assert not np.isnan(data.xpos).any()


def _camera_site_frame(model):
    """Return (object-name, world_pos, world_R) of the head camera site."""
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE,
                                b'head_camera_site')
    assert site_id >= 0, 'head_camera_site site missing from derived model'
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    pos = data.site_xpos[site_id].copy()
    rot = data.site_xmat[site_id].reshape(3, 3).copy()
    return site_id, pos, rot


def test_head_camera_site_at_rep103_optical_pose():
    """The camera site lives at the URDF's optical frame relative to column_top."""
    model = load_mjcf_model()
    _, pos, _ = _camera_site_frame(model)

    # Verify the site exists (name resolved) — implied by no exception.
    # Relative frame check: cross-check the world position tracks column_top's
    # world position + the mount offset. At the built-in zero state the base is
    # static at the origin; the column_top datum is at (0, 0, column_datum).
    # Read column_top's world position and confirm pos == col_top + CAM_REL_POS.
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    col_top_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, b'column_top')
    col_top_pos = data.xpos[col_top_id]
    expected = col_top_pos + np.array(CAM_REL_POS)
    np.testing.assert_allclose(pos, expected, atol=1e-4)


def test_head_camera_orientation_is_rep103_optical():
    """The camera site's world frame is the REP-103 optical frame.

    At the (static, zero) home state the ``column_top`` body's own world
    orientation is the identity (only fixed joints and a prismatic slide lie
    before it), so the sensor site's world orientation equals the overlay's
    declared ``euler="0 -pi/2 +pi/2"`` applied by MuJoCo. The resulting frame
    is the REP-103 optical frame: z forward (the head's +x, the depth axis),
    x right, y down. That is the frame road-map #4's RGB-D pipeline consumes,
    and is what the issue means by "camera present at the correct pose".
    """
    model = load_mjcf_model()
    _, _, rot = _camera_site_frame(model)
    # Probe-verified REP-103 optical frame at home (see status.md R-PR7-6):
    #   z forward/+world-x, x right/+world-y, y down/-world-z.
    expected = np.array([[0.0, 0.0, -1.0],
                         [1.0, 0.0, 0.0],
                         [0.0, -1.0, 0.0]])
    np.testing.assert_allclose(rot, expected, atol=1e-3)


def test_head_camera_declared():
    """The head-camera sensor site + framepos sensor survive; ncam stays 0.

    dfki-ric creates a MujocoDepthCamera per MJCF <camera>, and that node
    aborts on eglInitialize failure in the headless bringup env (no GPU/EGL),
    so the overlay deliberately omits the <camera> ELEMENT (R-PR8b overlay
    header, point 3). The <site> + framepos sensor (roadmap #4's RGB-D seam)
    must be present; ncam must be 0 so the sim spawns headless cleanly.
    """
    model = load_mjcf_model()
    site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE,
                                b'head_camera_site')
    assert site_id >= 0, 'head_camera_site site missing'
    # At least one sensor exists (the framepos on the camera site).
    assert model.nsensor >= 1
    # No camera element: ncam == 0 keeps the headless sim EGL-free (R-PR8b).
    assert model.ncam == 0
