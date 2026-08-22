# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""PR7 — the MuJoCo MJCF derivation gate (issue #89, roadmap §PR7).

The URDF stays the single source of truth; the MJCF is *derived* from it at
call time by ``robot_description.mjcf_model.load_mjcf_model`` and the
hand-authored overlay in ``mjcf/overlay.xml`` is the only MJCF text in the
repo. This file is the matching claim *at the source* (same home as
``test_robot_model.py``): the description package owns the loader, so it owns
the loader's gate here, independent of any consumer.

The assertions are the issue's acceptance criteria, made exact against the
probed model (see ``docs/features/pr7-mjcf-derivation/status.md`` rulings):

1. MuJoCo loads the derived model (no exception) — the load itself is the
   strongest single claim, covering the expand footprint, mesh resolution and
   the overlay splice.
2. ``nq == nv == nu == 18`` — the URDF's 18 non-fixed DOF (3 continuous wheels
   + 1 prismatic column + 14 revolute arms/grippers) all survive derivation,
   and every one of them has a placeholder actuator (R-PR7-4), so ``nu``
   equals the actuated-joint count.
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
"""

import mujoco
import numpy as np

from robot_description.mjcf_model import load_mjcf_model

#: URDF-derived DOF count: 3 continuous + 1 prismatic + 14 revolute = 18.
#: Every non-fixed joint is actuated in the overlay, so ``nu`` equals this too.
ACTUATED_DOF = 18

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


def test_nq_nv_nu_match_urdf_derived_counts():
    """Joints and actuators agree with the URDF's 18 non-fixed DOF."""
    model = load_mjcf_model()
    assert model.nq == ACTUATED_DOF
    assert model.nv == ACTUATED_DOF
    assert model.nu == ACTUATED_DOF


def test_nbody_reflects_fusestatic_not_urdf_link_count():
    """Fusestatic folds the static trunk into the world body (D31 check)."""
    model = load_mjcf_model()
    assert model.nbody == FUSESTATIC_NBODY


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
    """The overlay declares a head_camera camera + a framepos sensor."""
    model = load_mjcf_model()
    cam_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_CAMERA, b'head_camera')
    assert cam_id >= 0, 'head_camera camera missing'
    # At least one sensor exists (the framepos on the camera site).
    assert model.nsensor >= 1
