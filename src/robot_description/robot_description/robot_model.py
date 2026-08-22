# Copyright (c) 2026 Jaime C.
#
# Use of this source code is governed by an MIT-style
# license that can be found in the LICENSE file or at
# https://opensource.org/licenses/MIT.

"""Read the robot's kinematic constants out of the shipped URDF (D23 payoff).

This is the seam PR6 lands: instead of holding the robot's body as literals in
``robot_backends.RobotModel`` and *transcribing* a copy into the description
gate (the ``ROBOT_MODEL_*`` constants in ``test/test_description.py``), the
description now owns one loader that every consumer reads.  Mock today, MuJoCo
tomorrow -- both take their constants from here, so they cannot disagree.

The loader returns plain data (:class:`RobotModelConstants`): floats plus a
3-tuple for the home gripper offset.  It deliberately does **not** depend on
``robot_skills`` -- the wrapping into a ``Point``/``RobotModel`` is the
consumer's job, keeping this package a pure "XML + one loader" description
package.

Two mechanisms, one each for the two kinds of constant:

* the five **declared properties** (shoulder offsets, reach radius, column
  travel) are read literally as ``<xacro:property>`` elements from the
  shipped ``arm.xacro`` / ``column.xacro``.  This is load-bearing for
  ``reach_radius``: it is a workspace/safety *constraint* that no geometry in
  the expanded URDF references, so it is present in the expansion only as a
  comment -- reading the property (rather than deriving the arm's actual reach
  from link lengths) is the only way the two cannot silently diverge.
* the **derived** ``home_gripper_offset`` is computed from the expanded,
  parsed ``robot.urdf.xacro`` the same way the gate's
  ``test_so101_gripper_grasp_reference_matches_home_gripper_offset`` does: at
  the zero pose the grasp midpoint (mean of the two fingertip link origins)
  minus the shoulder link origin, in ``base_link`` coordinates, composed by
  walking each joint's ``<origin>`` (xyz + rpy) from the root down.  No joint
  *value* enters -- at the homing pose every revolute joint is at zero, so the
  rotation at each joint is the identity.

Dependency note: this module imports **no ROS runtime** (no ``rclpy``, no
``ament_index_python``).  It resolves the URDF files relative to this package's
own ``__file__`` and expands with the ``xacro`` Python API, both of which are
ROS-free on purpose -- ``robot_backends`` imports this module and must keep its
"no ROS import at runtime" invariant (D30, ``test_no_ros_runtime``).  The
``__file__``-relative lookup holds for a source checkout and for the
``--symlink-install`` colcon build this repo uses; a future real-wheel
deployment that relocates ``urdf/`` away from the package would switch this to
``importlib.resources`` package data instead.
"""

from __future__ import annotations

import math
import os
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import xacro
from urdf_parser_py.urdf import URDF

__all__ = ['RobotModelConstants', 'load_robot_model']

#: The ``<xacro:property>`` tags that hold the five declared scalar constants,
#: mapped ``(xacro_subassembly_file, element_name) -> RobotModelConstants field``.
#: All five are declared with ``RobotModel``'s own un-prefixed field names so a
#: grep for the identifier finds both ends (see arm.xacro / column.xacro).
_PROPERTY_SOURCES = {
    'shoulder_offset_y': ('arm.xacro', 'shoulder_offset_y'),
    'shoulder_offset_z': ('arm.xacro', 'shoulder_offset_z'),
    'reach_radius': ('arm.xacro', 'reach_radius'),
    'min_column_height': ('column.xacro', 'min_column_height'),
    'max_column_height': ('column.xacro', 'max_column_height'),
}

#: xacro's namespace, needed to find ``<xacro:property>`` in the source files.
_XACRO_NS = 'http://www.ros.org/wiki/xacro'

#: The top-level entry point every consumer expands (see robot.urdf.xacro).
_TOP_LEVEL = 'robot.urdf.xacro'

#: The arm whose geometry backs the home-gripper-offset FK.  Both arms are
#: identical in internal kinematics (arm.xacro), so either would do; LEFT is
#: the same side the gate asserts against.
_SIDE = 'left'

#: Rounding precision for the derived numbers, matching the gate's
#: ``PLACEMENT_TOL_M`` (1e-9).  The FK composition accumulates one-ULP float
#: noise (~5e-17); rounding to 9 decimals folds that back to the clean
#: literals (0.35, 0, -0.05) the gate pins, so ``RobotModel()`` is exactly
#: today's numbers rather than today's numbers plus float dust.
_ROUND_NDIGITS = 9


def _urdf_dir() -> Path:
    """Return the directory holding the shipped ``.xacro`` sources.

    Resolved relative to this package's ``__file__`` so it needs no ament
    index: with ``--symlink-install`` the package lives in ``build/../`` next
    to a symlinked ``urdf/``, and in a source checkout it is ``src/../urdf/``.
    ``resolve()`` follows the symlinks, so either layout lands on the real
    files.
    """
    return Path(__file__).resolve().parent.parent / 'urdf'


def _read_property(urdf_dir: Path, filename: str, name: str) -> float:
    """Return ``name``'s declared ``<xacro:property>`` value in ``filename``.

    Reads the *source* ``.xacro``, not the expansion: a property no geometry
    references (``reach_radius``) never reaches the expanded URDF, so the
    source is the only place it lives.  Fails loudly if the property is
    missing, renamed, or not a number -- the loader is the single source of
    truth, so a silent default would be exactly the drift D23 retires.
    """
    path = urdf_dir / filename
    tree = ElementTree.parse(path)
    for element in tree.iter():
        if element.tag == f'{{{_XACRO_NS}}}property' and element.get('name') == name:
            try:
                return float(element.get('value'))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f'{filename}: <xacro:property name="{name}"> has no numeric '
                    f'value={element.get("value")!r}') from exc
    raise ValueError(
        f'{filename}: no <xacro:property name="{name}"> found (shipped copy at '
        f'{path}); the loader reads the property, not a derived value')


def _parse_model(urdf_dir: Path) -> URDF:
    """Expand and parse the shipped ``robot.urdf.xacro`` into a model tree."""
    doc = xacro.process_file(str(urdf_dir / _TOP_LEVEL))
    return URDF.from_xml_string(doc.toxml())


def _rotation_from_rpy(rpy):
    """3x3 matrix for a URDF rpy triple (Rz * Ry * Rx)."""
    roll, pitch, yaw = rpy
    cos_r, sin_r = math.cos(roll), math.sin(roll)
    cos_p, sin_p = math.cos(pitch), math.sin(pitch)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    return (
        (cos_y * cos_p,
         cos_y * sin_p * sin_r - sin_y * cos_r,
         cos_y * sin_p * cos_r + sin_y * sin_r),
        (sin_y * cos_p,
         sin_y * sin_p * sin_r + cos_y * cos_r,
         sin_y * sin_p * cos_r - cos_y * sin_r),
        (-sin_p, cos_p * sin_r, cos_p * cos_r),
    )


def _rotate(matrix, vector):
    """Apply a 3x3 rotation matrix to a 3-vector."""
    return tuple(sum(matrix[i][k] * vector[k] for k in range(3))
                 for i in range(3))


def _multiply(left, right):
    """3x3 matrix product ``left @ right``."""
    return tuple(tuple(sum(left[i][k] * right[k][j] for k in range(3))
                       for j in range(3)) for i in range(3))


def _joint_xyz(joint):
    """A joint origin's xyz, treating a missing origin as no offset."""
    if joint.origin is None or joint.origin.xyz is None:
        return (0.0, 0.0, 0.0)
    return tuple(float(value) for value in joint.origin.xyz)


def _joint_rpy(joint):
    """A joint origin's rpy, treating a missing origin as no rotation."""
    if joint.origin is None or joint.origin.rpy is None:
        return (0.0, 0.0, 0.0)
    return tuple(float(value) for value in joint.origin.rpy)


def _link_origin(model, link_name):
    """The position of ``link_name``'s frame in ``base_link`` coordinates.

    Composes the joint ``<origin>`` (xyz + rpy) along the parent chain from the
    root down, at the zero pose -- no joint *value*, so each joint contributes
    its static origin and rotation only.  Mirror of the gate's
    ``_link_origin_in_base_link``.
    """
    identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    chain = []
    link = link_name
    while link != 'base_link':
        if link not in model.parent_map:
            raise ValueError(
                f'{link!r} is not base_link and has no parent joint; the model '
                f'tree is malformed')
        joint_name, parent = model.parent_map[link]
        chain.append((joint_name, link))
        link = parent
    rotation = identity
    origin = (0.0, 0.0, 0.0)
    for joint_name, _child in reversed(chain):
        joint = model.joint_map[joint_name]
        origin = tuple(origin[i] + _rotate(rotation, _joint_xyz(joint))[i]
                       for i in range(3))
        rotation = _multiply(rotation, _rotation_from_rpy(_joint_rpy(joint)))
    return origin


def _home_gripper_offset(model) -> Tuple[float, float, float]:
    """Derive the home grasp reference: grasp midpoint minus shoulder, at q=0."""
    upper = _link_origin(model, f'{_SIDE}_gripper_upper_tip_link')
    lower = _link_origin(model, f'{_SIDE}_gripper_lower_tip_link')
    shoulder = _link_origin(model, f'{_SIDE}_shoulder_link')
    grasp = tuple((upper[i] + lower[i]) / 2.0 for i in range(3))
    offset = tuple(grasp[i] - shoulder[i] for i in range(3))
    return tuple(round(component, _ROUND_NDIGITS) for component in offset)


@dataclass(frozen=True)
class RobotModelConstants:
    """The kinematic constants ``RobotModel`` is seeded from, as plain data.

    Distances are metres; ``home_gripper_offset`` is a ``(x, y, z)`` tuple
    (forward, left, up) relative to the shoulder, ready for the consumer to
    wrap into whatever point type it uses.
    """

    shoulder_offset_y: float
    shoulder_offset_z: float
    reach_radius: float
    min_column_height: float
    max_column_height: float
    home_gripper_offset: Tuple[float, float, float]


def load_robot_model() -> RobotModelConstants:
    """Load the robot's kinematic constants from the shipped URDF.

    The five scalars come from the declared ``<xacro:property>`` elements; the
    home gripper offset is derived from the expanded, parsed model.  The result
    is the single source of truth for ``RobotModel`` (and, later, MuJoCo).
    """
    urdf_dir = _urdf_dir()
    fields = {}
    for field_name, (filename, prop_name) in _PROPERTY_SOURCES.items():
        fields[field_name] = _read_property(urdf_dir, filename, prop_name)
    model = _parse_model(urdf_dir)
    fields['home_gripper_offset'] = _home_gripper_offset(model)
    return RobotModelConstants(**fields)
