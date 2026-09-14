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

from pathlib import Path
import tempfile
from typing import Dict

import mujoco
import xacro

__all__ = ['load_mjcf_model', 'load_mjcf_model_with_scene', 'write_mjcf_model']

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


def write_mjcf_model(path: str) -> str:
    """Materialize the derived MJCF to ``path`` for the sim to load.

    The ``mujoco_ros2_control`` system interface loads the sim model from a
    *file* (its ``mujoco_model`` hardware param). The derived MJCF is never
    checked in (PR7, issue #89), so a bringup launch materializes it at
    runtime by calling this, then passes ``path`` to the xacro's
    ``mujoco_model_path`` arg. Returns the MJCF text that was written, for
    callers that want to inspect it. The output path is made absolute.
    """
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    merged = _build_merged_mjcf(_package_dir())
    out.write_text(merged)
    return merged
