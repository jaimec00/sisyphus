# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Derive a loadable, correct MJCF from the shipped URDF plus the PR7 overlay.

This is the seam PR7 lands (issue #89, roadmap §PR7). It produces a MuJoCo
model whose joint structure agrees with the URDF-derived counts, exercises the
tree with one ``mj_step`` and carries the head camera sensor site at the
REP-103 optical frame. The URDF stays the single source of truth; the MJCF is
*derived* from it at call time and the hand-authored overlay is the only MJCF
text in the repo (``mjcf/overlay.xml``). Nothing generated is ever checked in.

The pipeline (all of it executed/probed against ``mujoco`` 3.12.0 on the
laptop node — see ``docs/features/pr7-mjcf-derivation/status.md`` rulings):

1. Expand ``robot.urdf.xacro`` (same ``xacro.process_file`` the
   ``robot_model`` loader uses).
2. ``mujoco.MjModel.from_xml_path`` on the expanded URDF, with the STL meshes
   handed in as ``assets`` (MuJoCo keys URDF mesh assets by *lowercase
   basename*), then ``mujoco.mj_saveLastXML`` to get the derived base MJCF.
   ``fusestatic`` is ON by default here: fixed-jointed static bodies
   (``column_rail_link`` and the whole static base trunk) are folded into the
   world body (R-PR7-5).
3. The derived MJCF writes ``<mesh file="package://robot_description/...">``;
   ``MjSpec.compile()`` cannot resolve ``package://``, so those URIs are
   rewritten to the absolute ``meshes/`` path first (R-PR7-2).
4. The hand-authored ``mjcf/overlay.xml`` is spliced in: its root-level blocks
   (contact defaults, actuators, sensor) are appended, and the delimited
   head-camera block is inserted as the first child of the ``column_top`` body
   (that body is where the camera lives once fusestatic folds the massless
   camera frames away). ``MjSpec.from_string(merged)`` + ``spec.compile()``.

Dependency note (matches ``robot_model.py``): this module imports **no ROS
runtime** (no ``rclpy``, no ``ament_index_python``). It resolves ``urdf/``,
``meshes/`` and ``mjcf/`` relative to this package's own ``__file__`` and
expands with the ``xacro`` Python API. ``robot_backends`` (and later the
MuJoCo ``RobotBackend``) must keep its "no ROS import at runtime" invariant
(D30), so the same discipline applies here. The ``__file__``-relative lookup
holds for a source checkout and for the ``--symlink-install`` colcon build
this repo uses.

The MuJoCo dependency is deliberate and heavy (a compiled simulator pulls in
the whole mujoco runtime); it is pinned in ``pixi.toml`` (``>=3.12,<4``) and
is a runtime need of ``load_mjcf_model``.
"""

from __future__ import annotations

import math
from pathlib import Path
import tempfile
from typing import Dict

import mujoco
import xacro

__all__ = ['FLOOR_BODIES', 'load_mjcf_model', 'load_mjcf_model_with_scene',
           'write_mjcf_model']

#: Top-level entry point the derivation expands (same as robot_model.py).
_TOP_LEVEL = 'robot.urdf.xacro'

#: Sentinel comment markers delimiting the head-camera block in overlay.xml.
#: The loader extracts the block and inserts it as the first child of the
#: ``column_top`` body, because that block does not live at the <mujoco> root:
#: once ``fusestatic`` folds the massless head_camera_link/optical_frame away
#: (R-PR7-5/6), the camera + sensor site must be re-attached to ``column_top``.
_HEAD_CAMERA_BEGIN = 'PR7-HEAD-CAMERA-BEGIN'
_HEAD_CAMERA_END = 'PR7-HEAD-CAMERA-END'

#: The body the head camera is mounted on in the *derived* MJCF. The URDF puts
#: the camera on ``column_top`` (via ``head_camera_mount``); ``fusestatic``
#: keeps it on the ``column_top`` body (the carriage is a movable body, not a
#: fused static frame).
_HEAD_CAMERA_PARENT_BODY = 'column_top'

#: Names for the free-jointed base the scene backend wraps the robot trunk in.
#: The base trunk is fused into the world body by ``fusestatic`` (PR1/PR7) and
#: so has no own joint; ``navigate_to`` (PR2) needs a way to *teleport* it, so
#: :func:`_wrap_base_freejoint` closes the trunk in a 6-DOF free-jointed body.
#: MuJoCo's free joint qpos is 7 floats ``[x, y, z, qw, qx, qy, qz]`` (6 DOF):
#: it adds 7 to ``nq`` and 6 to ``nv`` (probed against mujoco 3.12).
_BASE_FREEJOINT_BODY = 'base_link'
_BASE_FREEJOINT_NAME = 'base_free'

#: Mass (kg) of the wrapped ``base_link`` body, from ``base.xacro``'s
#: ``chassis_mass``= 6.0.  A free-jointed body MuJoCo will *move* must carry a
#: mass/inertia above ``mjMINVAL`` or the compile is rejected; this is the only
#: reason the inertial exists (navigate_to is teleport-only in PR2, R4/R6), so
#: the exact value only needs to be positive-definite and > ``mjMINVAL``.  The
#: stand-in diaginertia below is the ruling's suggested stable value.
_BASE_MASS = 6.0
_BASE_DIAGINERTIA = '0.1 0.1 0.1'

#: The wheel radius (``base.xacro``: ``wheel_radius = 0.05``), sourced rather
#: than hard-coded here: the floor's top surface must sit exactly one wheel
#: radius below ``base_link``'s origin for the wheels to rest on it.  The
#: URDF stays the single source of truth for geometry; this is the one number
#: the floor derivation needs from it (kept in sync by
#: ``test_write_mjcf_model_floor_top_is_one_wheel_radius_below_the_base``).
_WHEEL_RADIUS = 0.05

#: The three drivable wheel-link bodies the rim rollers hang off, in the
#: derived MJCF (URDF wheel names, ``base.xacro`` ``omni_wheel`` macro).
_WHEEL_LINK_BODIES = ('base_left_wheel_link', 'base_back_wheel_link',
                      'base_right_wheel_link')

#: -- Rim-roller model (issue #125) ------------------------------------------
#: The URDF models each omniwheel as a plain cylinder, so the sim's wheel/floor
#: contact scrubs: lateral (vy) transport and yaw do not deliver commanded
#: speed.  The *sim* path fixes that by giving each wheel a ring of freely
#: spinning barrel rollers around its rim (a real omniwheel), so the contact
#: can slide along the wheel's axle without slipping.
#:
#: These constants are sim-contact fidelity only -- the URDF wheel stays a
#: plain cylinder (D29) and the welded/in-process models are untouched (R1).
_ROLLER_COUNT = 8
#: Roller barrel cross-section radius, m (~7 mm: real omniwheel rollers).
_ROLLER_RADIUS = 0.007
#: Roller barrel half-length, m (full length 0.02 < ``wheel_width`` 0.03).
_ROLLER_HALF_LENGTH = 0.01
#: Roller-centre radius, m: the roller *surfaces* are this plus
#: :data:`_ROLLER_RADIUS` from the axle, i.e. back at ``_WHEEL_RADIUS`` = 0.05
#: -- the contact radius the floor height (``FLOOR_BODIES``) and
#: ``base_footprint`` already assume.
_ROLLER_CENTER_RADIUS = _WHEEL_RADIUS - _ROLLER_RADIUS
#: Wheel-hub collision radius, m.  Shrunk below :data:`_ROLLER_CENTER_RADIUS`
#: so the hub cylinder can never reach the floor: only the rollers contact.
#: MJCF-only -- the URDF hub keeps radius ``wheel_radius``.
_HUB_RADIUS = 0.040
#: Contact bitfields for the shrunk hub.  MuJoCo collides two geoms iff
#: ``(contype1 & conaffinity2) | (contype2 & conaffinity1)``; setting BOTH of the
#: hub's bits to 0 makes the hub never participate in any contact.  That is
#: required, not cosmetic: the hub (radius :data:`_HUB_RADIUS`) radially
#: overlaps the rollers (inner edge at ``_ROLLER_CENTER_RADIUS -
#: _ROLLER_RADIUS``), and the hub geom and the roller geoms sit on *different*
#: bodies (the wheel link and the roller bodies), so MuJoCo would otherwise
#: generate hub<->roller contacts and jam the rollers -- probed: with the hub
#: colliding, roller joint velocities stay near zero and the base barely
#: rotates.  The rollers carry the floor contact; the hub is then structural
#: (and visual) only.
_HUB_CONTYPE = 0
_HUB_CONAFFINITY = 0
#: Roller barrel mass, kg (small but well above ``mjMINVAL``).
_ROLLER_MASS = 0.01
#: Roller barrel inertia: solid cylinder about its barrel axis (I_AXIS) and
#: transverse to it (I_TRANS), from the mass/dimensions above so a retune
#: cannot leave a stale tensor behind.
_ROLLER_I_AXIS = _ROLLER_MASS * _ROLLER_RADIUS ** 2 / 2.0
_ROLLER_I_TRANS = _ROLLER_MASS * (
    3.0 * _ROLLER_RADIUS ** 2 + (2.0 * _ROLLER_HALF_LENGTH) ** 2) / 12.0

#: Newline used to assemble the multi-line :data:`FLOOR_BODIES` fragment
#: (``_insert_world_bodies`` re-indents it line by line).
_NL = '\n'

#: The static floor :func:`write_mjcf_model` splices in so the now-movable
#: base has something to drive on (issue #124, RULING 2).
#:
#: The wheels are velocity actuators, and a velocity-commanded wheel only
#: *moves the base* if it contacts ground with friction.  So the ROS-sim path
#: needs a floor.  It is spliced through :func:`_build_merged_mjcf`'s
#: ``world_bodies`` seam -- the same seam the in-process backend uses for scene
#: objects -- and because that seam runs *after* the free-joint wrap, the floor
#: stays welded to the world (a sibling of the wrapped ``base_link``), never
#: riding on the base.
#:
#: Geometry: after the wrap the free joint starts at ``pos="0 0 0"`` (i.e.
#: ``base_link`` at the world origin, axle height), and the wheels sit at
#: ``base_link`` z = 0 with ``wheel_radius = 0.05``, so the wheel bottoms are at
#: world z = -0.05.  The floor's top surface is therefore at world z = -0.05
#: (a plane's surface *is* its z), leaving the chassis underside
#: (z = 0.085 - 0.06/2 = 0.055) well clear: only the 3 wheels touch.  A plane
#: is preferred over a finite box (infinite support, no fall-off edge, inherits
#: the overlay's ``<default>`` friction the wheels need).  It is a *static*
#: world body: no joint, no inertial, so it never moves and never falls.
FLOOR_BODIES = (
    '<body name="floor">' + _NL
    + '  <geom name="floor_geom" type="plane" '
    + f'pos="0 0 {-_WHEEL_RADIUS:g}" size="0 0 1" condim="3" '
    + 'friction="1.0 0.4 0.02" solref="0.02 1.0" priority="1"/>' + _NL
    + '</body>'
)


def _package_dir() -> Path:
    """Return the directory holding ``urdf/``, ``meshes/`` and ``mjcf/``."""
    return Path(__file__).resolve().parent.parent


def _collect_assets(meshes_dir: Path) -> Dict[str, bytes]:
    """Map every STL under ``meshes/`` to its bytes, keyed by lowercase basename.

    MuJoCo's URDF loader keys mesh assets by *lowercase filename*: registering
    full ``package://...`` URIs fails with "Repeated file name in assets dict"
    (probed). Our mesh basenames are unique across ``arm/`` and ``gripper/``,
    so the bare-basename key is unambiguous.
    """
    assets: Dict[str, bytes] = {}
    for path in sorted(meshes_dir.rglob('*.stl')):
        assets[path.name.lower()] = path.read_bytes()
    return assets


def _expand_urdf(urdf_dir: Path) -> str:
    """Expand ``robot.urdf.xacro`` to a URDF XML string."""
    doc = xacro.process_file(str(urdf_dir / _TOP_LEVEL))
    return doc.toxml()


def _derive_base_mjcf(urdf_xml: str, assets: Dict[str, bytes], out_path: Path) -> None:
    """Import the URDF into MuJoCo and write the derived base MJCF to ``out_path``."""
    # MjModel.from_xml_path needs a file path; stage the expanded URDF in a temp dir.
    with tempfile.TemporaryDirectory() as td:
        urdf_path = Path(td) / 'robot.urdf'
        urdf_path.write_text(urdf_xml)
        model = mujoco.MjModel.from_xml_path(str(urdf_path), assets=assets)
    mujoco.mj_saveLastXML(str(out_path), model)


def _redirect_meshes(mjcf: str, meshes_dir: Path) -> str:
    """Rewrite ``package://robot_description/meshes/...`` to absolute filesystem paths.

    The derived MJCF keeps the URDF's ``package://`` mesh URIs. ``MjSpec``
    cannot resolve those at compile time (R-PR7-2), so rewrite to the absolute
    ``meshes/`` directory the loader already resolves via ``__file__``.
    """
    return mjcf.replace('package://robot_description/meshes/',
                        f'{meshes_dir.resolve()}/')


def _read_overlay(overlay_path: Path) -> tuple[str, str]:
    """Read ``overlay.xml`` into ``(root_blocks, head_camera_block)``.

    ``overlay.xml`` wraps its content in a top-level ``<mujoco>`` element for
    readability; the loader splices the *content*, dropping the wrapper. The
    head-camera block (the part that must move to the ``column_top`` body) is
    delimited by the ``PR7-HEAD-CAMERA-BEGIN``/``END`` marker comments.
    """
    text = overlay_path.read_text()
    # Strip the outer <mujoco> wrapper; the content is everything between them.
    inner = text.split('<mujoco>', 1)[1].rsplit('</mujoco>', 1)[0]
    begin = inner.index(f'<!-- {_HEAD_CAMERA_BEGIN}')
    end = inner.index(f'<!-- {_HEAD_CAMERA_END}') + len(f'<!-- {_HEAD_CAMERA_END} -->') + 1
    head_camera_block = inner[begin:end].strip()
    root_blocks = (inner[:begin] + inner[end:]).strip()
    return root_blocks, head_camera_block


def _splice_overlay(base_mjcf: str, root_blocks: str, head_camera_block: str,
                    parent_body: str) -> str:
    """Insert the hand-authored overlay into the derived MJCF and return the merged XML.

    ``root_blocks`` is appended before the closing ``</mujoco>``; the head
    camera block is inserted as the first child of the ``<body name="...">``
    whose name is ``parent_body``. ``head_camera_block`` gains one level of
    indentation so the merge stays readable.
    """
    assert base_mjcf.rstrip().endswith('</mujoco>')
    merged = base_mjcf.rstrip()[: -len('</mujoco>')]
    merged += '\n' + root_blocks + '\n</mujoco>'

    marker = f'<body name="{parent_body}"'
    idx = merged.index(marker)
    insert_pos = merged.index('\n', idx) + 1
    indented = '\n'.join('  ' + line if line.strip() else line
                         for line in head_camera_block.split('\n'))
    merged = merged[:insert_pos] + indented + '\n' + merged[insert_pos:]
    return merged


def _insert_world_bodies(merged: str, world_bodies: str) -> str:
    """Splice ``world_bodies`` into ``merged`` as new siblings of the robot trunk.

    The robot's tree (``fusestatic`` folds the whole static trunk into the
    world body) occupies the single ``<worldbody>...</worldbody>``; the overlay
    blocks (contact defaults, actuators, sensor) live *after* that element.  A
    compiled ``MjModel`` cannot gain bodies post-compile (R-1), so PR1 scene
    objects are added here -- as static, joint-less bodies welded to the world
    -- by inserting them as the last sibling inside ``</worldbody>``, before it
    closes.  That keeps them inside the world body (so they are immovable and
    never simulated) and after every robot body (so they cannot disturb the
    robot geometry).

    ``world_bodies`` is a fragment of ``<body>...</body>`` elements at the
    worldbody's one level of nesting; it gains one level of indentation here
    so the merged text stays readable.  An empty fragment returns ``merged``
    unchanged, so plain :func:`load_mjcf_model` is byte-identical whether it
    routes through this or not.
    """
    if not world_bodies or not world_bodies.strip():
        return merged
    marker = '</worldbody>'
    # Exactly one worldbody (the implicit one every MJCF has).
    assert merged.count(marker) == 1, merged.count(marker)
    insert_at = merged.rindex(marker)
    indented = '\n'.join(
        '  ' + line if line.strip() else line
        for line in world_bodies.split('\n'))
    return merged[:insert_at] + indented + '\n' + merged[insert_at:]


def _wrap_base_freejoint(merged: str) -> str:
    """Close the robot trunk in a free-jointed ``base_link`` body (navigate seam).

    The robot's derived MJCF has no base joint: ``fusestatic`` folds the static
    trunk (chassis ``<geom>`` pair + the movable wheels/column arms) into the
    world body, so the base is welded to the origin and cannot move -- which is
    fine for PR1 (scene loader, no motion) but leaves the MuJoCo backend unable
    to execute ``navigate_to``.

    ``navigate_to`` needs to *teleport* the base (PR2, issue #101).  MuJoCo has
    no joint-independent body move, so this wraps the ENTIRE worldbody content
    (everything between ``<worldbody>`` and ``</worldbody>`` -- the robot's
    fused trunk, i.e. the top-level ``<geom>`` pair + the wheel/column bodies)
    in a single 6-DOF free-jointed ``<body name="base_link">``.  Driving that
    free joint's qpos then moves the whole kinematic subtree as one unit, which
    is exactly a mobile base teleport.  ``world_bodies`` scene objects are
    spliced *after* this wrap (see :func:`_build_merged_mjcf`), so they stay
    welded to the world rather than riding on the base (R-1).

    The wrapped body carries an :class:`inertial` (mass ``_BASE_MASS``,
    ``_BASE_DIAGINERTIA``) because MuJoCo rejects a free-jointed body whose mass
    is below ``mjMINVAL``.  PR2 only ever *teleports* it (R3), so the values are
    a stable stand-in, not a measured chassis inertia (R4/R6).  Exactly one
    ``<worldbody>`` is expected (the implicit one every MJCF has).
    """
    open_tag = '<worldbody>'
    close_tag = '</worldbody>'
    # Exactly one worldbody (the implicit one every MJCF has).
    assert merged.count(close_tag) == 1, merged.count(close_tag)
    oi = merged.index(open_tag)
    ci = merged.index(close_tag, oi)
    inner_block = merged[oi + len(open_tag):ci]
    inner_block = ''.join(
        ('  ' + line if line.strip() else line) + '\n'
        for line in inner_block.split('\n'))
    wrapped = (
        f'<body name="{_BASE_FREEJOINT_BODY}" pos="0 0 0">\n'
        f'  <freejoint name="{_BASE_FREEJOINT_NAME}"/>\n'
        f'  <inertial pos="0 0 0" mass="{_BASE_MASS:g}" '
        f'diaginertia="{_BASE_DIAGINERTIA}"/>\n'
        + inner_block
        + '</body>\n'
    )
    return merged[:oi + len(open_tag)] + '\n' + wrapped + merged[ci:]


def _roller_body_xml(body_name: str, k: int) -> str:
    """Return one rim-roller ``<body>`` block (child of a wheel-link body).

    ``k`` is the roller index; ``theta = 2*pi*k/_ROLLER_COUNT`` is its angle
    around the wheel's rim in the **wheel-link frame**, where (per
    ``base.xacro``'s ``omni_wheel`` macro) +z is the wheel's spin axis pointing
    radially outward and the rim circle lies in the local xy-plane.  The roller
    centre therefore sits at
    ``(_ROLLER_CENTER_RADIUS*cos theta, _ROLLER_CENTER_RADIUS*sin theta, 0)``.

    **Roller axis = the rolling-tangent direction.**  In the wheel-link frame a
    wheel rolling "forward" turns about its rim, so the rolling direction at rim
    angle ``theta`` is the *circumferential tangent*
    ``d = (-sin theta, cos theta, 0)``.  A real omniwheel's job is to let the
    contact slide freely **along the wheel's axle** (the spin axis, local +z)
    while gripping in the rolling direction -- which means each roller's own
    spin axis must be the rolling direction ``d``, *not* the axle.  (Setting it
    to the axle instead would let the wheel skate forward and grip sideways --
    exactly inverted, and probed: the base then barely rotates under a yaw
    command, since the rollers absorb the rolling motion.)  So the hinge axis is
    ``d = (-sin theta, cos theta, 0)``: parallel to the wheel plane, orthogonal
    to both the axle (local +z) and the radial spoke.

    The body is placed at the rim with an *identity* quat; both the hinge
    ``axis`` and the capsule geom orientation are written in that parent frame
    directly (the geom via ``fromto``, MuJoCo's endpoint form -- a capsule's
    default axis is +z, and ``fromto`` re-aims it along ``d``).  Writing the
    axis explicitly (rather than baking theta into a body euler) keeps the
    roller's own frame axis-aligned with the wheel link, so the axis claim is
    checkable: ``axis . spin_axis == 0`` (parallel to the wheel plane).

    The rollers are passive: an unactuated hinge joint (no ``range``, no
    actuator) shows up as ``+1`` nbody / ``+1`` nq / ``+1`` nv while ``nu`` is
    unchanged (R4).
    """
    theta = 2.0 * math.pi * k / _ROLLER_COUNT
    cx = _ROLLER_CENTER_RADIUS * math.cos(theta)
    cy = _ROLLER_CENTER_RADIUS * math.sin(theta)
    dx = -math.sin(theta)
    dy = math.cos(theta)
    h = _ROLLER_HALF_LENGTH
    name = f'{body_name}_roller_{k}'
    return (
        f'    <body name="{name}" pos="{cx:.8g} {cy:.8g} 0">' + _NL
        + f'      <inertial pos="0 0 0" mass="{_ROLLER_MASS:g}" '
        + f'diaginertia="{_ROLLER_I_AXIS:g} {_ROLLER_I_TRANS:g} '
        + f'{_ROLLER_I_TRANS:g}"/>' + _NL
        + f'      <joint name="{name}" type="hinge" '
        + f'axis="{dx:.8g} {dy:.8g} 0"/>' + _NL
        + f'      <geom name="{name}_geom" type="capsule" '
        + f'size="{_ROLLER_RADIUS:g}" '
        + f'fromto="{-h * dx:.8g} {-h * dy:.8g} 0 {h * dx:.8g} {h * dy:.8g} 0"/>'
        + _NL
        + '    </body>'
    )


def _shrink_hub_radius(merged: str, body_name: str) -> str:
    """Shrink the wheel hub's cylinder geoms to :data:`_HUB_RADIUS` (MJCF-only).

    The URDF wheel is a cylinder of radius ``_WHEEL_RADIUS``; if it stayed that
    size it would sit *below* the rollers and keep touching the floor, so the
    rollers would never bear the load.  Rewriting its derived ``<geom
    size="0.05 0.015" type="cylinder"/>`` to radius :data:`_HUB_RADIUS` (both
    the collision and the visual copy -- physics only reads the collision one,
    but the two should not disagree) leaves **only** the rollers contacting.
    Done on the derived MJCF text, never the URDF: ``test_description.py`` (D29)
    parses the URDF and must keep seeing a plain ``wheel_radius`` cylinder.

    Scoped to ``body_name``'s slice of the text so only that wheel's hub shrinks.
    """
    start = merged.index(f'<body name="{body_name}"')
    end = merged.index('</body>', start)
    segment = merged[start:end]
    old = f'size="{_WHEEL_RADIUS:g} 0.015" type="cylinder"'
    new = (f'size="{_HUB_RADIUS:g} 0.015" type="cylinder" '
           + f'contype="{_HUB_CONTYPE}" conaffinity="{_HUB_CONAFFINITY}"')
    assert old in segment, (body_name, old)
    segment = segment.replace(old, new)
    return merged[:start] + segment + merged[end:]


def _add_rim_rollers(merged: str) -> str:
    """Give each drivable wheel a ring of passive rim rollers (issue #125, R2).

    Called by :func:`write_mjcf_model` only for the drivable model
    (``base_free_joint=True``).  For each of the three wheel-link bodies it:

    1. appends ``_ROLLER_COUNT`` roller ``<body>`` blocks as the body's last
       children (see :func:`_roller_body_xml`); and
    2. shrinks that wheel's hub cylinder geoms to :data:`_HUB_RADIUS` (see
       :func:`_shrink_hub_radius`) so the hub never reaches the floor.

    The welded escape hatch (``base_free_joint=False``) never calls this, so it
    stays the PR8b model verbatim (no rollers, nq = nv = 18, nbody = 19, R5);
    likewise ``load_mjcf_model`` / ``load_mjcf_model_with_scene`` never route
    here (R1).  The wheel-link bodies are found by name because they are
    movable bodies, so they survive ``fusestatic`` in the derived MJCF.
    """
    for body_name in _WHEEL_LINK_BODIES:
        merged = _shrink_hub_radius(merged, body_name)

    for body_name in _WHEEL_LINK_BODIES:
        start = merged.index(f'<body name="{body_name}"')
        # The wheel body's own closing tag is the *next* ``</body>``; after the
        # shrink above it still holds only inertial/joint/geom, so the first close
        # tag after the open tag is the body's, not a child's.
        close = merged.index('</body>', start)
        rollers = _NL.join(_roller_body_xml(body_name, k)
                           for k in range(_ROLLER_COUNT))
        merged = merged[:close] + rollers + _NL + merged[close:]
    return merged


def _build_merged_mjcf(pkg: Path, world_bodies: str = '',
                       *, base_free_joint: bool = False) -> str:
    """Derive and return the merged MJCF text (URDF import + overlay splice).

    Shared by :func:`load_mjcf_model` (which compiles it) and
    :func:`write_mjcf_model` (which materializes it to a file for the
    ``mujoco_ros2_control`` sim - PR8b / issue #92). Returns the single,
    hand-authored-overlay-spliced MJCF string.

    ``world_bodies`` optionally carries extra static ``<body>`` blocks that are
    spliced into the worldbody before compilation -- the seam the scene-aware
    MuJoCo backend (PR1, issue #99) uses to place immovable world objects (see
    ``_insert_world_bodies``).  The default '' means robot-only, so callers of
    the bare merge (the ``write_mjcf_model`` PR8b path) are unaffected.

    ``base_free_joint=True`` additionally closes the robot trunk in a
    free-jointed ``base_link`` body (see :func:`_wrap_base_freejoint`) -- the
    seam the MuJoCo backend's ``navigate_to`` uses (PR2, issue #101).  The wrap
    happens *after* the robot-only merge but *before* ``world_bodies`` are
    spliced, so scene objects stay welded to the world (siblings of the wrapped
    base) rather than riding on it.  The default stays False: the bare
    :func:`load_mjcf_model` and :func:`write_mjcf_model` paths are unchanged
    (the robot trunk is fused to the origin, nq = nv = 18).
    """
    assets = _collect_assets(pkg / 'meshes')
    urdf_xml = _expand_urdf(pkg / 'urdf')
    root_blocks, head_camera_block = _read_overlay(pkg / 'mjcf' / 'overlay.xml')

    with tempfile.TemporaryDirectory() as td:
        base_mjcf_path = Path(td) / 'derived.mjcf'
        _derive_base_mjcf(urdf_xml, assets, base_mjcf_path)
        base_mjcf = base_mjcf_path.read_text()

    base_mjcf = _redirect_meshes(base_mjcf, pkg / 'meshes')
    merged = _splice_overlay(base_mjcf, root_blocks, head_camera_block,
                             _HEAD_CAMERA_PARENT_BODY)
    if base_free_joint:
        merged = _wrap_base_freejoint(merged)
    return _insert_world_bodies(merged, world_bodies)


def load_mjcf_model() -> mujoco.MjModel:
    """Derive and compile the bare robot's MJCF (no base free joint, nq=nv=18).

    Returns a compiled :class:`mujoco.MjModel` whose body tree is the fused
    robot trunk welded to the world origin (``fusestatic`` folds the base into
    the world, so there is **no** base joint here -- nq = nv = 18, matching
    ``test_mjcf_model.py`` and the ``mujoco_ros2_control``/PR8b sim seam, both
    of which load the fused robot as it is).  Throwaway files (staged URDF,
    derived base MJCF) live in a temp dir and are not committed.

    This is deliberately **not** the scene backend's model: the backend
    teleports the base, so it loads through :func:`load_mjcf_model_with_scene`,
    which wraps the trunk in a free joint (PR2, issue #101).
    """
    merged = _build_merged_mjcf(_package_dir())
    spec = mujoco.MjSpec.from_string(merged)
    return spec.compile()


def load_mjcf_model_with_scene(world_bodies: str) -> mujoco.MjModel:
    """Compile the scene MJCF (free-jointed base + ``world_bodies``) (PR2).

    The scene-aware model the MuJoCo backend drives (PR1/P2, issues #99/#101):
    the robot trunk is closed in a free-jointed ``base_link`` body (see
    :func:`_wrap_base_freejoint`) -- the 6-DOF free joint ``navigate_to``
    teleports, adding 6 DOF so ``nv = 18 + 6`` and ``nq = 18 + 7`` (a free
    joint's qpos is 7 floats) -- and the static ``<body>`` blocks in
    ``world_bodies`` are spliced into the worldbody *after* the wrap (see
    :func:`_insert_world_bodies`), keeping the scene objects immovable and
    welded to the world (siblings of the wrapped base).

    Unlike the bare :func:`load_mjcf_model`, this model has the base free joint
    and so is *not* the PR8b ROS-sim model: it exists for the in-process MuJoCo
    backend, whose ``navigate_to`` must be able to move the base.  ``world_bodies``
    may be '' (robot + free joint, no scene objects); robot derivation itself
    is never duplicated because all paths compile ``_build_merged_mjcf``
    output.
    """
    merged = _build_merged_mjcf(
        _package_dir(), world_bodies=world_bodies, base_free_joint=True)
    spec = mujoco.MjSpec.from_string(merged)
    return spec.compile()


#: Integrator the drivable model is compiled with.  MuJoCo's default Euler
#: integrator is numerically unstable for the stiff wheel/floor contacts the
#: free base introduces (probed: "Nan, Inf or huge value in QACC at DOF 6"
#: within 0.06 s, and the wheel joints *lock* against the contact -- a wheel
#: commanded 5.2 rad/s does not turn).  The implicit integrator resolves the
#: stiff contact and the commanded wheel speeds are followed exactly.  Set only
#: on the free-jointed sim path, so the welded :func:`load_mjcf_model` model
#: (and the in-process backend) are unchanged.
_DRIVE_INTEGRATOR_OPTION = '<option integrator="implicit"/>'


def _inject_integrator(merged: str) -> str:
    """Insert the drivable-model ``<option integrator="implicit"/>`` first.

    MuJoCo allows exactly one ``<option>``; the overlay does not author one, so
    the element is spliced as the first child of ``<mujoco>``.  Only
    :func:`write_mjcf_model` calls this.
    """
    open_idx = merged.index('<mujoco')
    close_idx = merged.index('>', open_idx) + 1
    return (merged[:close_idx] + '\n  ' + _DRIVE_INTEGRATOR_OPTION
            + merged[close_idx:])


def write_mjcf_model(path: str, *, base_free_joint: bool = True,
                     floor: bool = True) -> str:
    """Materialize the derived MJCF to ``path`` for the sim to load.

    The ``mujoco_ros2_control`` system interface loads the sim model from a
    *file* (its ``mujoco_model`` hardware param). The derived MJCF is never
    checked in (PR7, issue #89), so a bringup launch materializes it at
    runtime by calling this, then passes ``path`` to the xacro's
    ``mujoco_model_path`` arg. Returns the MJCF text that was written, for
    callers that want to inspect it. The output path is made absolute.

    Since PR2 (issue #124, RULING 1) the **default** is the *drivable* model:
    the base is closed in a free joint (``base_free_joint=True``, via
    :func:`_wrap_base_freejoint`) and a static floor is spliced in
    (``floor=True``, :data:`FLOOR_BODIES`) so the velocity-commanded wheels have
    ground to push against. Before PR2 this path emitted the *welded* model --
    ``fusestatic`` folds the static trunk into the world body, so there was no
    ``base_link`` body and no free joint, and the base could not move.

    The keyword arguments keep the bare welded model reachable for callers and
    tests that need it (``base_free_joint=False, floor=False``); the launch (and
    so the sim) uses the default.  The in-process backend
    (:func:`load_mjcf_model_with_scene`) is unaffected: it passes its own
    ``base_free_joint``/``world_bodies`` explicitly and never routes through
    here.
    """
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    merged = _build_merged_mjcf(
        _package_dir(),
        world_bodies=FLOOR_BODIES if floor else '',
        base_free_joint=base_free_joint,
    )
    if base_free_joint:
        merged = _inject_integrator(merged)
        # Issue #125: the drivable sim model carries real omniwheel rim rollers
        # (see _add_rim_rollers).  The welded escape hatch (base_free_joint=False)
        # never reaches here, so it stays the PR8b model verbatim (R5).
        merged = _add_rim_rollers(merged)
    out.write_text(merged)
    return merged
